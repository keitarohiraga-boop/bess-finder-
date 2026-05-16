"""
Slack通知ルーター（Bot Token方式）
#bess-site-finder チャンネルへの投稿・スレッド返信を一元管理する。
Claude Agent の tool としても呼び出せるよう汎用設計。
"""
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Any, Optional

from app.database import get_db
from app import models

router = APIRouter(prefix="/slack", tags=["slack"])

BOT_TOKEN  = os.getenv("SLACK_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")
# 後方互換：Incoming Webhook URLが設定されている場合はフォールバックとして使用
WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")


def _now_jst() -> str:
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime("%Y-%m-%d %H:%M JST")


# ===== Slack API 呼び出し =====

def _call_slack_api(method: str, payload: dict) -> dict:
    """Slack Web API を呼び出して結果を返す"""
    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="SLACK_BOT_TOKEN が未設定です")
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {BOT_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Slack API HTTP エラー: {e.code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Slack API 接続エラー: {str(e)}")

    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=f"Slack API エラー: {result.get('error', 'unknown')}")
    return result


# ===== Block Kit テンプレート =====

def _block_approval_request(p: dict) -> list:
    score = p.get("score", 0)
    score_icon = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
    irr = p.get("irr")
    irr_text = f"{irr:.1f}%" if irr else "未算出（シミュレーター未実行）"
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
                {"type": "mrkdwn", "text": f"*総合スコア*\n{score_icon} {score}点"},
                {"type": "mrkdwn", "text": f"*IRR概算*\n{irr_text}"},
                {"type": "mrkdwn", "text": f"*案件タイプ*\n{p.get('case_type', '—')}"},
                {"type": "mrkdwn", "text": f"*ステータス*\n{p.get('status', '—')}"},
            ]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*メモ・所見*\n{p.get('note') or '（なし）'}"}
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"案件ID: #{p.get('case_id', '—')} | 候補地ID: #{p.get('site_id', '—')} | {_now_jst()}"}
            ]
        }
    ]


def _block_approval_result(p: dict) -> list:
    approved = p.get("approved", True)
    icon  = "✅" if approved else "❌"
    label = "承認されました" if approved else "見送りとなりました"
    color_word = "承認" if approved else "見送り"
    fields = [
        {"type": "mrkdwn", "text": f"*判断*\n{icon} {color_word}"},
        {"type": "mrkdwn", "text": f"*担当*\n{p.get('assignee', '—')}"},
    ]
    if not approved and p.get("reason"):
        fields.append({"type": "mrkdwn", "text": f"*見送り理由*\n{p.get('reason')}"})
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{icon} *{p.get('site_name', '候補地')}* — {label}"}
        },
        {"type": "section", "fields": fields},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _now_jst()}]
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
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*タスク*\n{p.get('task', '—')}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*実行内容*\n{steps_text}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*結果サマリー*\n{p.get('summary', '—')}"}},
        {"type": "divider"},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _now_jst()}]}
    ]


def _block_site_summary(p: dict) -> list:
    sites = p.get("sites", [])
    sites_text = "\n".join(
        f"• *{s.get('name', '—')}* — スコア {s.get('score', '—')}点 / IRR {s.get('irr', '未算出')}"
        for s in sites
    ) if sites else "（候補地なし）"
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "📍 候補地サマリー", "emoji": True}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{p.get('title', '候補地一覧')}*\n{sites_text}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Agent所見*\n{p.get('comment', '—')}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _now_jst()}]}
    ]


def _block_status_changed(p: dict) -> list:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "📋 案件ステータス更新", "emoji": True}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*候補地*\n{p.get('site_name', '—')}"},
                {"type": "mrkdwn", "text": f"*担当*\n{p.get('assignee', '—')}"},
                {"type": "mrkdwn", "text": f"*変更前*\n{p.get('from_status', '—')}"},
                {"type": "mrkdwn", "text": f"*変更後*\n{p.get('to_status', '—')}"},
            ]
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"案件ID: #{p.get('case_id', '—')} | {_now_jst()}"}]}
    ]


TEMPLATES = {
    "approval_request": _block_approval_request,
    "approval_result":  _block_approval_result,
    "task_complete":    _block_task_complete,
    "site_summary":     _block_site_summary,
    "status_changed":   _block_status_changed,
}


# ===== エンドポイント =====

class NotifyRequest(BaseModel):
    message_type: str
    payload: dict[str, Any]
    thread_ts: Optional[str] = None  # スレッド返信時に指定


@router.post("/notify", summary="Slackに通知を送信（tsを返す）")
def notify(body: NotifyRequest, db: Session = Depends(get_db)):
    builder = TEMPLATES.get(body.message_type)
    if not builder:
        raise HTTPException(
            status_code=400,
            detail=f"未対応のmessage_type: {body.message_type}. 使用可能: {list(TEMPLATES.keys())}"
        )

    if not CHANNEL_ID:
        raise HTTPException(status_code=503, detail="SLACK_CHANNEL_ID が未設定です")

    blocks = builder(body.payload)
    slack_payload = {
        "channel": CHANNEL_ID,
        "text": f"[BESS] {body.message_type}",
        "blocks": blocks,
    }
    if body.thread_ts:
        slack_payload["thread_ts"] = body.thread_ts

    result = _call_slack_api("chat.postMessage", slack_payload)
    ts = result.get("ts")

    # 承認依頼送信時：tsをDBのslack_thread_urlに保存
    if body.message_type == "approval_request" and ts:
        case_id = body.payload.get("case_id")
        if case_id:
            case = db.get(models.SiteCase, int(case_id))
            if case:
                case.slack_thread_url = ts
                db.commit()

    return {"ok": True, "ts": ts}


@router.post("/thread-reply", summary="既存メッセージのスレッドに返信")
def thread_reply(body: NotifyRequest, db: Session = Depends(get_db)):
    """承認・見送り時にスレッドへ返信する"""
    body.thread_ts = body.thread_ts or body.payload.get("thread_ts")
    if not body.thread_ts:
        raise HTTPException(status_code=400, detail="thread_ts が必要です")
    return notify(body, db)


@router.get("/status", summary="Slack連携の設定状態を確認")
def status():
    return {
        "bot_token_configured": bool(BOT_TOKEN),
        "channel_configured": bool(CHANNEL_ID),
        "supported_types": list(TEMPLATES.keys()),
    }
