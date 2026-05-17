"""
メール送信ルーター（Brevo / 旧Sendinblue）
外部の不動産仲介業者への照会メール送信に使用。
Claude Agent の tool としても呼び出せるよう汎用設計。
"""
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/email", tags=["email"])

BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
FROM_EMAIL    = os.getenv("GMAIL_USER", "")
FROM_NAME     = os.getenv("SENDGRID_FROM_NAME", "BESS Site Finder")


# ===== Brevo API 送信 =====

def _send_email(to_email: str, to_name: str, subject: str, body_text: str, body_html: str = "") -> dict:
    if not BREVO_API_KEY:
        raise HTTPException(status_code=503, detail="BREVO_API_KEY が未設定です")
    if not FROM_EMAIL:
        raise HTTPException(status_code=503, detail="GMAIL_USER（送信元アドレス）が未設定です")

    payload = {
        "sender":      {"name": FROM_NAME, "email": FROM_EMAIL},
        "to":          [{"email": to_email, "name": to_name or to_email}],
        "subject":     subject,
        "textContent": body_text,
    }
    if body_html:
        payload["htmlContent"] = body_html

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=body,
        headers={
            "api-key":      BREVO_API_KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return {"ok": True, "messageId": result.get("messageId")}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        raise HTTPException(status_code=502, detail=f"Brevo エラー: {e.code} {error_body}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"メール送信失敗: {str(e)}")


# ===== メールテンプレート =====

def _build_inquiry_email(payload: dict) -> tuple[str, str]:
    site_address = payload.get("site_address", "")
    area_m2      = payload.get("area_m2", "")
    score        = payload.get("score", "")
    irr          = payload.get("irr", "")
    our_company  = payload.get("our_company", "Natural Born株式会社")
    contact_name = payload.get("contact_name", "担当者")
    contact_email = payload.get("contact_email", FROM_EMAIL)
    note         = payload.get("note", "")
    agent_name   = payload.get("agent_name", "不動産会社")

    now_jst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y年%m月%d日")

    text = f"""{agent_name} 御中

突然のご連絡失礼いたします。
{our_company}の{contact_name}と申します。

弊社では蓄電池施設（BESS）の建設用地を探しており、
貴社エリアにある下記の土地について、
所有者様へのご紹介・仲介をお願いできないかと考えております。

【対象地】
・所在：{site_address}
・面積：{f"{area_m2:,.0f}㎡" if area_m2 else "要確認"}

【弊社の関心】
蓄電池施設の建設用地として取得（または長期リース）を検討しております。
{f"・BESSポテンシャルスコア：{score}点" if score else ""}
{f"・収益性試算IRR：{irr}%" if irr else ""}
{f"・備考：{note}" if note else ""}

ご対応可能でしたら、お気軽にご連絡いただけますと幸いです。

{now_jst}
{our_company}
担当：{contact_name}
E-mail：{contact_email}
"""
    html = f"""<p>{agent_name} 御中</p>
<p>{our_company}の{contact_name}と申します。</p>
<p>蓄電池施設（BESS）の建設用地について、下記の土地の仲介をお願いできないかご相談です。</p>
<ul>
  <li>所在：{site_address}</li>
  {"<li>面積：" + f"{area_m2:,.0f}㎡</li>" if area_m2 else ""}
  {"<li>スコア：" + str(score) + "点</li>" if score else ""}
  {"<li>IRR試算：" + str(irr) + "%</li>" if irr else ""}
  {"<li>備考：" + note + "</li>" if note else ""}
</ul>
<p>ご対応可能でしたらご連絡ください。</p>
<p>{now_jst}<br>{our_company} {contact_name}<br>{contact_email}</p>"""
    return text, html


# ===== エンドポイント =====

class SendEmailRequest(BaseModel):
    to_email:     str
    to_name:      str = ""
    subject:      str = ""
    message_type: str = "inquiry"
    payload:      dict = {}
    body_text:    Optional[str] = None


@router.post("/send", summary="メールを送信")
def send_email(body: SendEmailRequest):
    if body.message_type == "inquiry":
        subject = body.subject or f"【蓄電池用地 照会】{body.payload.get('site_address', '候補地')}について"
        text, html = _build_inquiry_email(body.payload)
    elif body.message_type == "custom":
        if not body.body_text:
            raise HTTPException(status_code=400, detail="body_text が必要です")
        text, html = body.body_text, ""
        subject = body.subject
    else:
        raise HTTPException(status_code=400, detail=f"未対応: {body.message_type}")
    return _send_email(body.to_email, body.to_name, subject, text, html)


@router.get("/status", summary="メール設定確認")
def status():
    return {
        "configured": bool(BREVO_API_KEY and FROM_EMAIL),
        "provider":   "Brevo",
        "from_email": FROM_EMAIL or "未設定",
    }


@router.get("/test", summary="自分宛テストメール送信")
def test_send():
    if not BREVO_API_KEY or not FROM_EMAIL:
        raise HTTPException(status_code=503, detail="設定不足")
    return _send_email(FROM_EMAIL, "", "BESS Site Finder テストメール", "Brevo経由のテストメールです。")
