"""
メール送信ルーター（Gmail SMTP）
外部の不動産仲介業者への照会メール送信に使用。
Claude Agent の tool としても呼び出せるよう汎用設計。
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/email", tags=["email"])

GMAIL_USER     = os.getenv("GMAIL_USER", "")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "")
FROM_NAME      = os.getenv("SENDGRID_FROM_NAME", "BESS Site Finder")


# ===== Gmail SMTP 送信 =====

def _send_email(to_email: str, to_name: str, subject: str, body_text: str, body_html: str = "") -> dict:
    if not GMAIL_USER or not GMAIL_PASSWORD:
        raise HTTPException(status_code=503, detail="GMAIL_USER / GMAIL_PASSWORD が未設定です")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{GMAIL_USER}>"
    msg["To"]      = f"{to_name} <{to_email}>" if to_name else to_email

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(GMAIL_USER, GMAIL_PASSWORD)
            smtp.sendmail(GMAIL_USER, to_email, msg.as_bytes())
        return {"ok": True}
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(status_code=502, detail="Gmail認証失敗。アプリパスワードを確認してください")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"メール送信失敗: {str(e)}")


# ===== メールテンプレート =====

def _build_inquiry_email(payload: dict) -> tuple[str, str]:
    """不動産仲介業者への照会メール本文を生成"""
    site_name    = payload.get("site_name", "候補地")
    site_address = payload.get("site_address", "")
    area_m2      = payload.get("area_m2", "")
    score        = payload.get("score", "")
    irr          = payload.get("irr", "")
    our_company  = payload.get("our_company", "Natural Born株式会社")
    contact_name = payload.get("contact_name", "担当者")
    contact_email = payload.get("contact_email", GMAIL_USER)
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

    html = f"""
<p>{agent_name} 御中</p>
<p>突然のご連絡失礼いたします。<br>
{our_company}の{contact_name}と申します。</p>
<p>弊社では蓄電池施設（BESS）の建設用地を探しており、
貴社エリアにある下記の土地について、
所有者様へのご紹介・仲介をお願いできないかと考えております。</p>
<h3>【対象地】</h3>
<ul>
<li>所在：{site_address}</li>
{"<li>面積：" + f"{area_m2:,.0f}㎡</li>" if area_m2 else ""}
</ul>
<h3>【弊社の関心】</h3>
<p>蓄電池施設の建設用地として取得（または長期リース）を検討しております。</p>
{"<p>BESSポテンシャルスコア：<strong>" + str(score) + "点</strong></p>" if score else ""}
{"<p>収益性試算IRR：<strong>" + str(irr) + "%</strong></p>" if irr else ""}
{"<p>備考：" + note + "</p>" if note else ""}
<p>ご対応可能でしたら、お気軽にご連絡いただけますと幸いです。</p>
<p>{now_jst}<br>
{our_company}<br>
担当：{contact_name}<br>
E-mail：{contact_email}</p>
"""
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
            raise HTTPException(status_code=400, detail="custom の場合は body_text が必要です")
        text, html = body.body_text, ""
        subject = body.subject
    else:
        raise HTTPException(status_code=400, detail=f"未対応の message_type: {body.message_type}")

    return _send_email(body.to_email, body.to_name, subject, text, html)


@router.get("/status", summary="メール送信設定の確認")
def status():
    return {
        "configured": bool(GMAIL_USER and GMAIL_PASSWORD),
        "provider":   "Gmail SMTP",
        "from_email": GMAIL_USER or "未設定",
    }


@router.get("/test", summary="自分宛にテストメールを送信")
def test_send():
    if not GMAIL_USER or not GMAIL_PASSWORD:
        raise HTTPException(status_code=503, detail="GMAIL_USER / GMAIL_PASSWORD が未設定です")
    return _send_email(GMAIL_USER, "", "BESS Site Finder テストメール", "このメールはGmail SMTP設定のテストです。")
