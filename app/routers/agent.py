"""
Claude Agent ルーター
自然言語タスクを受け取り、tool use ループで自律実行する。
SSE（Server-Sent Events）で進捗をフロントにストリーミング。
"""
import json
import os
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.utils import haversine
from app.area_mapping import PREFECTURE_TO_AREA
from app.routers.simulate import simulate, SimulateRequest
from app.routers.slack import _call_slack_api, CHANNEL_ID, TEMPLATES, _now_jst

router = APIRouter(prefix="/agent", tags=["agent"])

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"


# ===== ツール定義 =====

TOOLS = [
    {
        "name": "get_sites",
        "description": "候補地一覧を取得する。スコア・エリア・ステータスでフィルタリング可能。",
        "input_schema": {
            "type": "object",
            "properties": {
                "min_score": {"type": "number", "description": "最低スコア（0-100）"},
                "limit":     {"type": "integer", "description": "取得件数（デフォルト10）"},
                "case_status": {"type": "string", "description": "案件ステータスでフィルタ（例: 精査中, 承認待ち）"},
            },
        },
    },
    {
        "name": "get_site_detail",
        "description": "指定した候補地の詳細情報・スコア・案件情報をまとめて取得する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_id": {"type": "integer", "description": "候補地ID"},
            },
            "required": ["site_id"],
        },
    },
    {
        "name": "check_hazard",
        "description": "候補地の洪水・土砂災害リスクを不動産情報ライブラリAPIから取得する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_id": {"type": "integer", "description": "候補地ID"},
            },
            "required": ["site_id"],
        },
    },
    {
        "name": "check_land_price",
        "description": "候補地周辺の地価公示データを取得する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_id": {"type": "integer", "description": "候補地ID"},
            },
            "required": ["site_id"],
        },
    },
    {
        "name": "run_simulation",
        "description": "候補地のIRR・NPV・年間収益を試算する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_id":          {"type": "integer", "description": "候補地ID"},
                "capacity_mwh":     {"type": "number",  "description": "蓄電容量MWh（デフォルト20）"},
                "power_mw":         {"type": "number",  "description": "出力MW（デフォルト5）"},
                "unit_price_per_kwh": {"type": "number", "description": "設備単価 万円/kWh（デフォルト60）"},
            },
            "required": ["site_id"],
        },
    },
    {
        "name": "update_case",
        "description": "案件のステータス変更・メモ追記・担当者設定を行う。",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_id":  {"type": "integer", "description": "候補地ID"},
                "status":   {"type": "string",  "description": "新しいステータス（発見/精査中/承認待ち/アプローチ中/契約済/見送り）"},
                "add_note": {"type": "string",  "description": "追加するメモ"},
                "assignee": {"type": "string",  "description": "担当者名"},
            },
            "required": ["site_id"],
        },
    },
    {
        "name": "send_slack",
        "description": "Slackの#bess-site-finderチャンネルにメッセージを投稿する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_type": {"type": "string", "description": "メッセージ種別（task_complete / site_summary / status_changed）"},
                "payload":      {"type": "object", "description": "メッセージ内容"},
                "thread_ts":    {"type": "string", "description": "スレッド返信先ts（省略可）"},
            },
            "required": ["message_type", "payload"],
        },
    },
]


# ===== ツール実行 =====

def _run_tool(name: str, inputs: dict, db: Session) -> str:
    try:
        if name == "get_sites":
            return _tool_get_sites(inputs, db)
        elif name == "get_site_detail":
            return _tool_get_site_detail(inputs, db)
        elif name == "check_hazard":
            return _tool_check_hazard(inputs, db)
        elif name == "check_land_price":
            return _tool_check_land_price(inputs, db)
        elif name == "run_simulation":
            return _tool_run_simulation(inputs, db)
        elif name == "update_case":
            return _tool_update_case(inputs, db)
        elif name == "send_slack":
            return _tool_send_slack(inputs)
        else:
            return f"未知のツール: {name}"
    except Exception as e:
        return f"ツール実行エラー ({name}): {str(e)}"


def _tool_get_sites(inputs: dict, db: Session) -> str:
    sites = db.query(models.Site).all()
    min_score = inputs.get("min_score", 0)
    limit = inputs.get("limit", 10)
    case_status = inputs.get("case_status")

    # 案件情報を取得
    cases = {c.site_id: c for c in db.query(models.SiteCase).all()}

    result = []
    for s in sites:
        score = s.score or 0
        if score < min_score:
            continue
        case = cases.get(s.id)
        if case_status and (not case or case.status != case_status):
            continue
        result.append({
            "id": s.id, "name": s.name, "address": s.address,
            "score": score, "area": s.area,
            "case_status": case.status if case else "未登録",
            "assignee": case.assignee if case else "",
        })

    result.sort(key=lambda x: x["score"], reverse=True)
    result = result[:limit]
    return json.dumps(result, ensure_ascii=False)


def _tool_get_site_detail(inputs: dict, db: Session) -> str:
    site = db.get(models.Site, inputs["site_id"])
    if not site:
        return "候補地が見つかりません"
    case = db.query(models.SiteCase).filter(models.SiteCase.site_id == site.id).first()

    # 最寄り変電所
    substations = db.query(models.Substation).filter(
        models.Substation.lat.between(site.lat - 0.5, site.lat + 0.5),
        models.Substation.lng.between(site.lng - 0.5, site.lng + 0.5),
    ).all()
    nearest_sub = min(substations, key=lambda s: haversine(site.lat, site.lng, s.lat, s.lng)) if substations else None

    detail = {
        "id": site.id, "name": site.name, "address": site.address,
        "area_m2": site.area, "score": site.score,
        "landuse": site.landuse_label, "flood": site.flood_label,
        "substation_dist_m": site.substation_dist,
        "nearest_substation": nearest_sub.name if nearest_sub else "不明",
        "lat": site.lat, "lng": site.lng,
        "case": {
            "status": case.status, "assignee": case.assignee,
            "notes_count": len(json.loads(case.notes or "[]")),
        } if case else None,
    }
    return json.dumps(detail, ensure_ascii=False)


def _tool_check_hazard(inputs: dict, db: Session) -> str:
    import urllib.request
    site = db.get(models.Site, inputs["site_id"])
    if not site:
        return "候補地が見つかりません"
    # reinfolib APIを内部呼び出し
    api_key = os.getenv("REINFOLIB_API_KEY", "")
    if not api_key:
        return json.dumps({"error": "REINFOLIB_API_KEY未設定", "flood": site.flood_label, "note": "DBキャッシュ値を使用"}, ensure_ascii=False)
    # DBキャッシュ値を返す（APIは別途reinfolib.pyで実装済み）
    return json.dumps({
        "flood_label": site.flood_label or "データなし",
        "flood_risk": site.flood or "none",
        "source": "DBキャッシュ（不動産情報ライブラリ）",
    }, ensure_ascii=False)


def _tool_check_land_price(inputs: dict, db: Session) -> str:
    site = db.get(models.Site, inputs["site_id"])
    if not site:
        return "候補地が見つかりません"
    candidates = db.query(models.LandPricePoint).filter(
        models.LandPricePoint.lat.between(site.lat - 0.3, site.lat + 0.3),
        models.LandPricePoint.lng.between(site.lng - 0.3, site.lng + 0.3),
    ).all()
    if not candidates:
        return json.dumps({"error": "地価データなし"}, ensure_ascii=False)
    nearest = min(candidates, key=lambda p: haversine(site.lat, site.lng, p.lat, p.lng))
    dist_m = round(haversine(site.lat, site.lng, nearest.lat, nearest.lng))
    return json.dumps({
        "price_per_m2": nearest.price_per_m2,
        "address": nearest.address,
        "use_type": nearest.use_type,
        "year": nearest.data_year,
        "distance_m": dist_m,
    }, ensure_ascii=False)


def _tool_run_simulation(inputs: dict, db: Session) -> str:
    req = SimulateRequest(
        site_id=inputs["site_id"],
        capacity_mwh=inputs.get("capacity_mwh", 20.0),
        power_mw=inputs.get("power_mw", 5.0),
        unit_price_per_kwh=inputs.get("unit_price_per_kwh", 60.0),
    )
    result = simulate(req, db)
    return json.dumps(result, ensure_ascii=False)


def _tool_update_case(inputs: dict, db: Session) -> str:
    site_id = inputs["site_id"]
    case = db.query(models.SiteCase).filter(models.SiteCase.site_id == site_id).first()
    site = db.get(models.Site, site_id)

    if not case:
        # 案件が存在しない場合は新規作成
        case = models.SiteCase(
            site_id=site_id,
            site_name=site.name if site else "",
            status=inputs.get("status", "精査中"),
            assignee=inputs.get("assignee", "Agent"),
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(case)
    else:
        if "status" in inputs:
            case.status = inputs["status"]
        if "assignee" in inputs:
            case.assignee = inputs["assignee"]

    if inputs.get("add_note"):
        notes = json.loads(case.notes or "[]")
        notes.append({
            "text": inputs["add_note"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "author": "🤖 Agent",
        })
        case.notes = json.dumps(notes, ensure_ascii=False)

    case.updated_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    return json.dumps({"ok": True, "site_id": site_id, "status": case.status}, ensure_ascii=False)


def _tool_send_slack(inputs: dict) -> str:
    builder = TEMPLATES.get(inputs["message_type"])
    if not builder:
        return f"未対応のmessage_type: {inputs['message_type']}"
    if not CHANNEL_ID:
        return "SLACK_CHANNEL_ID未設定"
    blocks = builder(inputs["payload"])
    payload = {"channel": CHANNEL_ID, "text": f"[BESS Agent] {inputs['message_type']}", "blocks": blocks}
    if inputs.get("thread_ts"):
        payload["thread_ts"] = inputs["thread_ts"]
    result = _call_slack_api("chat.postMessage", payload)
    return json.dumps({"ok": True, "ts": result.get("ts")}, ensure_ascii=False)


# ===== SSEストリーミング =====

def _sse(event: str, data: dict) -> str:
    return f"data: {json.dumps({'event': event, **data}, ensure_ascii=False)}\n\n"


async def _run_agent(workflow: str, site_id: int | None, db: Session) -> AsyncGenerator[str, None]:
    if not ANTHROPIC_API_KEY:
        yield _sse("error", {"message": "ANTHROPIC_API_KEY が未設定です"})
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # ワークフロー別システムプロンプト
    system = (
        "あなたはBESS（蓄電池施設）候補地の調査エージェントです。"
        "与えられたタスクをツールを使って自律的に実行し、結果を日本語で報告してください。"
        "メモは具体的な数値を含めて簡潔に記録し、最後に総評を提示してください。"
    )

    messages = [{"role": "user", "content": workflow}]

    yield _sse("start", {"message": f"タスク開始: {workflow[:50]}..."})

    # tool use ループ
    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        # テキスト出力をストリーミング
        for block in response.content:
            if block.type == "text" and block.text:
                yield _sse("text", {"text": block.text})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason != "tool_use":
            break

        # ツール呼び出しを実行
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            yield _sse("tool_call", {"tool": block.name, "input": block.input})
            result = _run_tool(block.name, block.input, db)
            yield _sse("tool_result", {"tool": block.name, "result": result[:200]})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    yield _sse("done", {"message": "完了"})


# ===== エンドポイント =====

WORKFLOWS = {
    "approval_package": {
        "label": "📋 承認依頼パッケージを作成",
        "description": "全データ収集→IRR試算→メモ記録→ステータス更新まで一括実行",
    },
    "bulk_review": {
        "label": "🔍 複数候補を一括精査",
        "description": "スコア上位候補を全件調査して比較レポートを生成",
    },
}


class AgentRequest(BaseModel):
    workflow: str   # "approval_package" | "bulk_review"
    site_id: int | None = None
    params: dict = {}


def _build_prompt(workflow: str, site_id: int | None, params: dict, db: Session) -> str:
    if workflow == "approval_package":
        if not site_id:
            raise HTTPException(status_code=400, detail="approval_packageにはsite_idが必要です")
        site = db.get(models.Site, site_id)
        if not site:
            raise HTTPException(status_code=404, detail="候補地が見つかりません")
        return (
            f"候補地ID={site_id}（{site.name}）について承認依頼パッケージを作成してください。\n"
            f"手順：\n"
            f"1. get_site_detail で基本情報を取得\n"
            f"2. check_hazard でハザード情報を確認\n"
            f"3. check_land_price で地価を確認\n"
            f"4. run_simulation でIRR/NPVを試算（20MWh/5MW想定）\n"
            f"5. 結果をまとめてupdate_caseでメモに記録し、ステータスを「承認待ち」に変更\n"
            f"6. 最後に総評（承認推奨/要検討/見送り推奨）とその根拠を述べてください"
        )
    elif workflow == "bulk_review":
        limit = params.get("limit", 5)
        min_score = params.get("min_score", 60)
        return (
            f"スコア{min_score}点以上の候補地を上位{limit}件取得し、一括精査してください。\n"
            f"各候補地について：\n"
            f"1. get_site_detail で基本情報を取得\n"
            f"2. run_simulation でIRRを試算\n"
            f"3. check_hazard でハザードを確認\n"
            f"全件調査後、IRRとスコアを基に優先順位をつけた比較表と総評を提示してください。"
        )
    raise HTTPException(status_code=400, detail=f"未知のワークフロー: {workflow}")


@router.get("/workflows", summary="利用可能なワークフロー一覧")
def list_workflows():
    return WORKFLOWS


@router.post("/run", summary="Agentワークフローを実行（SSEストリーミング）")
def run_agent(body: AgentRequest, db: Session = Depends(get_db)):
    prompt = _build_prompt(body.workflow, body.site_id, body.params, db)

    async def generate():
        async for chunk in _run_agent(prompt, body.site_id, db):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
