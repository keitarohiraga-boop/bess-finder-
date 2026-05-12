"""
案件管理ルーター
候補地ごとの社内ワークフロー（ステータス・メモ・承認）を管理する
"""
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app import models

router = APIRouter(prefix="/cases", tags=["cases"])

STATUS_LIST = ["発見", "精査中", "承認待ち", "アプローチ中", "契約済", "見送り"]
CASE_TYPES  = ["自社", "パートナー依頼"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dict(case: models.SiteCase) -> dict:
    return {
        "id":               case.id,
        "site_id":          case.site_id,
        "site_name":        case.site_name,
        "status":           case.status,
        "case_type":        case.case_type,
        "assignee":         case.assignee,
        "partner_name":     case.partner_name,
        "slack_thread_url": case.slack_thread_url,
        "pass_reason":      case.pass_reason,
        "notes":            json.loads(case.notes or "[]"),
        "created_at":       case.created_at,
        "updated_at":       case.updated_at,
    }


@router.get("", summary="全案件一覧を取得")
def list_cases(
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(models.SiteCase)
    if status:
        q = q.filter(models.SiteCase.status == status)
    return [_to_dict(c) for c in q.order_by(models.SiteCase.updated_at.desc()).all()]


@router.get("/pending-count", summary="承認待ち件数を取得")
def pending_count(db: Session = Depends(get_db)):
    count = db.query(models.SiteCase).filter(
        models.SiteCase.status == "承認待ち"
    ).count()
    return {"count": count}


@router.get("/{site_id}/by-site", summary="候補地IDで案件を取得")
def get_by_site(site_id: int, db: Session = Depends(get_db)):
    case = db.query(models.SiteCase).filter(
        models.SiteCase.site_id == site_id
    ).first()
    if not case:
        return None
    return _to_dict(case)


@router.post("", summary="案件を新規作成")
def create_case(body: dict, db: Session = Depends(get_db)):
    case = models.SiteCase(
        site_id   = body["site_id"],
        site_name = body.get("site_name", ""),
        status    = body.get("status", "発見"),
        case_type = body.get("case_type", "自社"),
        assignee  = body.get("assignee", ""),
        created_at = _now(),
        updated_at = _now(),
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return _to_dict(case)


@router.patch("/{case_id}", summary="案件を更新（ステータス・メモ等）")
def update_case(case_id: int, body: dict, db: Session = Depends(get_db)):
    case = db.get(models.SiteCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if "status" in body:
        if body["status"] not in STATUS_LIST:
            raise HTTPException(status_code=400, detail=f"Invalid status. Use one of: {STATUS_LIST}")
        case.status = body["status"]

    for field in ["case_type", "assignee", "partner_name", "slack_thread_url", "pass_reason"]:
        if field in body:
            setattr(case, field, body[field])

    # メモ追加（既存ログに追記）
    if "add_note" in body and body["add_note"].strip():
        notes = json.loads(case.notes or "[]")
        notes.append({
            "text":      body["add_note"].strip(),
            "timestamp": _now(),
            "author":    body.get("author", "担当"),
        })
        case.notes = json.dumps(notes, ensure_ascii=False)

    case.updated_at = _now()
    db.commit()
    return _to_dict(case)


@router.post("/validate-url", summary="SlackスレッドURLの疎通確認")
def validate_slack_url(body: dict):
    """URLフォーマット検証 + HEADリクエストで疎通確認"""
    url = body.get("url", "").strip()

    # フォーマット検証
    import re
    pattern = r"https://[a-zA-Z0-9\-]+\.slack\.com/archives/[A-Z0-9]+/p[0-9]+"
    if not re.match(pattern, url):
        return {"valid": False, "reason": "Slack スレッドURLの形式が正しくありません（例: https://xxx.slack.com/archives/C.../p...）"}

    # 疎通確認（SlackはログインなしでもリダイレクトするためHTTP 3xxを許容）
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {"valid": True, "status_code": resp.status}
    except urllib.error.HTTPError as e:
        # 401/403はSlack認証が必要なだけでURLは有効
        if e.code in (401, 403):
            return {"valid": True, "status_code": e.code}
        return {"valid": False, "reason": f"URLアクセスエラー: HTTP {e.code}"}
    except Exception as e:
        return {"valid": False, "reason": f"接続エラー: {str(e)}"}
