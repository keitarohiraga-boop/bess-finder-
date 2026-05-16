"""
Slack通知ルーター
#bess-site-finder チャンネルへの投稿を一元管理する。
Claude Agent の tool としても呼び出せるよう汎用設計。
"""
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any

router = APIRouter(prefix="/slack", tags=["slack"])

WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")


# ===== Block Kit テンプレート =====

def _block_approval_request(p: dict) -> list:
    score_bar = "🟢" if p.get("score", 0) >= 70 else "🟡" if p.get("score", 0) >= 50 else "🔴"
    irr = p.get("irr")
    irr_text = f"{irr:.1f}%" if irr else "未算出"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔋 BESS候補地 承認依頼", "emoji": True}
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*候補地*\n{p.get('site_name', '—')}"},
                {"type": "mrkdwn", "text": f"*担当*\n{p.get('assignee', '—')}"},
                {"type": "mrkdwn", "text": f"*総合スコア*\n{score_bar} {p.get('score', '—')}点"},
                {"type": "mrkdwn", "text": f"*IRR概算*\n{irr_text}"},
                {"type": "mrkdwn", "text": f"*案件タイプ*\n{p.get('case_type', '—')}"},
                {"type": "mrkdwn", "text": f"*ステータス*\n{p.get('status', '—')}"},
            ]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*メモ・所見*\n{p.get('pass_reason') or p.get('note') or '（なし）'}"}
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"案件ID: #{p.get('case_id', '—')} | 候補地ID: #{p.get('site_id', '—')} | {_now_jst()}"}
            ]
        }
    ]


def _block_task_complete(p: dict) -> list:
    steps = p.get("steps", [])
    steps_text = "\n".join(f"• {s}" for s in steps) if steps else "（詳細なし）"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🤖 Agentタスク完了", "emoji": True}
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*タスク*\n{p.get('task', '—')}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*実行内容*\n{steps_text}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*結果サマリー*\n{p.get('summary', '—')}"}
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"実行時刻: {_now_jst()}"}
            ]
        }
    ]


def _block_site_summary(p: dict) -> list:
    sites = p.get("sites", [])
    sites_text = "\n".join(
        f"• *{s.get('name', '—')}* — スコア {s.get('score', '—')}点 / IRR {s.get('irr', '未算出')}"
        for s in sites
    ) if sites else "（候補地なし）"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📍 候補地サマリー", "emoji": True}
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{p.get('title', '候補地一覧')}*\n{sites_text}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Agent所見*\n{p.get('comment', '—')}"}
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"{_now_jst()}"}
            ]
        }
    ]


def _block_status_changed(p: dict) -> list:
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📋 案件ステータス更新", "emoji": True}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*候補地*\n{p.get('site_name', '—')}"},
                {"type": "mrkdwn", "text": f"*担当*\n{p.get('assignee', '—')}"},
                {"type": "mrkdwn", "text": f"*変更前*\n{p.get('from_status', '—')}"},
                {"type": "mrkdwn", "text": f"*変更後*\n{p.get('to_status', '—')}"},
            ]
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"案件ID: #{p.get('case_id', '—')} | {_now_jst()}"}
            ]
        }
    ]


TEMPLATES = {
    "approval_request": _block_approval_request,
    "task_complete":    _block_task_complete,
    "site_summary":     _block_site_summary,
    "status_changed":   _block_status_changed,
}


def _now_jst() -> str:
    from datetime import timezone, timedelta
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime("%Y-%m-%d %H:%M JST")


def _send_to_slack(blocks: list, text: str = "BESS Site Finder 通知") -> dict:
    if not WEBHOOK_URL:
        raise HTTPException(status_code=503, detail="SLACK_WEBHOOK_URL が未設定です")
    body = json.dumps({"text": text, "blocks": blocks}).encode()
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"ok": True, "status": resp.status}
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Slack API エラー: {e.code} {e.reason}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"送信失敗: {str(e)}")


# ===== エンドポイント =====

class NotifyRequest(BaseModel):
    message_type: str
    payload: dict[str, Any]


@router.post("/notify", summary="Slackに通知を送信")
def notify(body: NotifyRequest):
    builder = TEMPLATES.get(body.message_type)
    if not builder:
        raise HTTPException(
            status_code=400,
            detail=f"未対応のmessage_type: {body.message_type}. 使用可能: {list(TEMPLATES.keys())}"
        )
    blocks = builder(body.payload)
    return _send_to_slack(blocks, text=f"[BESS] {body.message_type}")


@router.get("/status", summary="Slack連携の設定状態を確認")
def status():
    return {
        "configured": bool(WEBHOOK_URL),
        "supported_types": list(TEMPLATES.keys()),
    }
