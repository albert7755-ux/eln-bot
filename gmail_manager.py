"""
gmail_manager.py
Gmail 自動整理系統
- 讀取未讀郵件
- Claude AI 分析重要性與分類
- 自動加標籤
- 推播摘要到 LINE
"""
import os
import json
import base64
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import anthropic

TZ_TAIPEI = timezone(timedelta(hours=8))

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

# ══════════════════════════════
# Gmail 授權
# ══════════════════════════════
def get_gmail_service():
    token_json = os.environ.get("GOOGLE_TOKEN_JSON", "")
    if not token_json:
        raise RuntimeError("Missing GOOGLE_TOKEN_JSON")
    token_data = json.loads(token_json)
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)

# ══════════════════════════════
# 讀取郵件
# ══════════════════════════════
def get_unread_emails(service, max_results: int = 20) -> list[dict]:
    """讀取未讀郵件"""
    result = service.users().messages().list(
        userId="me",
        labelIds=["UNREAD"],
        maxResults=max_results
    ).execute()

    messages = result.get("messages", [])
    emails = []

    for msg in messages:
        try:
            detail = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="full"
            ).execute()

            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            subject = headers.get("Subject", "（無主旨）")
            sender = headers.get("From", "（未知寄件人）")
            date_str = headers.get("Date", "")

            # 解析日期
            try:
                date = parsedate_to_datetime(date_str).astimezone(TZ_TAIPEI)
                date_fmt = date.strftime("%m/%d %H:%M")
            except Exception:
                date_fmt = date_str[:16]

            # 取得郵件內文
            body = extract_body(detail["payload"])

            emails.append({
                "id": msg["id"],
                "subject": subject,
                "sender": sender,
                "date": date_fmt,
                "body": body[:1000],  # 只取前1000字
                "labels": detail.get("labelIds", []),
            })
        except Exception as e:
            print(f"Error reading email {msg['id']}: {e}")

    return emails

def extract_body(payload) -> str:
    """從郵件 payload 提取純文字內容"""
    body = ""
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain":
                data = part["body"].get("data", "")
                if data:
                    body += base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            elif "parts" in part:
                body += extract_body(part)
    else:
        data = payload["body"].get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    # 清理 HTML 標籤
    body = re.sub(r"<[^>]+>", "", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body

# ══════════════════════════════
# Claude 分析郵件
# ══════════════════════════════
def analyze_emails(emails: list[dict]) -> dict:
    """用 Claude 分析郵件重要性與分類"""
    if not emails:
        return {"important": [], "summary": "今日無未讀郵件。"}

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # 組合郵件清單
    email_list = ""
    for i, e in enumerate(emails):
        email_list += (
            f"\n[{i+1}] 寄件人：{e['sender']}\n"
            f"主旨：{e['subject']}\n"
            f"時間：{e['date']}\n"
            f"內容摘要：{e['body'][:300]}\n"
        )

    prompt = f"""以下是未讀郵件清單，請用繁體中文分析：

{email_list}

請用 JSON 格式回覆，結構如下：
{{
  "important": [
    {{
      "index": 1,
      "sender": "寄件人",
      "subject": "主旨",
      "category": "分類（客戶來信/銀行通知/金融機構/內部信件/廣告/其他）",
      "priority": "高/中/低",
      "summary": "一句話摘要",
      "action": "建議行動（需回覆/需處理/僅供參考/可忽略）"
    }}
  ],
  "daily_summary": "整體摘要，說明今日重要郵件概況（2到3句話）"
}}

只回覆 JSON，不要其他文字。"""

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    text = resp.content[0].text
    clean = re.sub(r"```json|```", "", text).strip()
    return json.loads(clean)

# ══════════════════════════════
# 自動加標籤
# ══════════════════════════════
def ensure_label(service, label_name: str) -> str:
    """確保標籤存在，回傳 label_id"""
    labels = service.users().labels().list(userId="me").execute()
    for label in labels.get("labels", []):
        if label["name"] == label_name:
            return label["id"]
    # 建立新標籤
    new_label = service.users().labels().create(
        userId="me",
        body={"name": label_name, "labelListVisibility": "labelShow",
              "messageListVisibility": "show"}
    ).execute()
    return new_label["id"]

def apply_label(service, email_id: str, label_name: str):
    """幫郵件加上標籤"""
    try:
        label_id = ensure_label(service, label_name)
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"addLabelIds": [label_id]}
        ).execute()
    except Exception as e:
        print(f"Label error ({email_id}): {e}")

# ══════════════════════════════
# 格式化 LINE 推播訊息
# ══════════════════════════════
def format_line_message(analysis: dict, emails: list[dict]) -> str:
    now = datetime.now(TZ_TAIPEI).strftime("%m/%d %H:%M")
    msg = f"📧 郵件摘要 {now}\n"
    msg += "─────────────────\n"
    msg += f"{analysis.get('daily_summary', '')}\n"
    msg += "─────────────────\n"

    important = analysis.get("important", [])
    high = [e for e in important if e.get("priority") == "高"]
    mid  = [e for e in important if e.get("priority") == "中"]

    if high:
        msg += "🔴 高優先\n"
        for e in high:
            msg += f"• {e['subject'][:25]}\n"
            msg += f"  {e['summary']}\n"
            msg += f"  → {e['action']}\n"

    if mid:
        msg += "\n🟡 中優先\n"
        for e in mid:
            msg += f"• {e['subject'][:25]}\n"
            msg += f"  {e['summary']}\n"

    low_count = len([e for e in important if e.get("priority") == "低"])
    if low_count:
        msg += f"\n🟢 低優先：{low_count} 封（可稍後處理）\n"

    msg += f"\n共 {len(emails)} 封未讀郵件"
    return msg.strip()

# ══════════════════════════════
# 即時監控（每15分鐘）
# ══════════════════════════════
def check_new_emails() -> str | None:
    """檢查最新未讀郵件，只回傳高優先的"""
    service = get_gmail_service()

    # 只看最近15分鐘的郵件
    fifteen_min_ago = int((datetime.now(timezone.utc) - timedelta(minutes=15)).timestamp())
    result = service.users().messages().list(
        userId="me",
        labelIds=["UNREAD"],
        q=f"after:{fifteen_min_ago}",
        maxResults=10
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return None

    emails = []
    for msg in messages:
        try:
            detail = service.users().messages().get(
                userId="me", id=msg["id"], format="full"
            ).execute()
            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            body = extract_body(detail["payload"])
            emails.append({
                "id": msg["id"],
                "subject": headers.get("Subject", "（無主旨）"),
                "sender": headers.get("From", ""),
                "date": headers.get("Date", "")[:16],
                "body": body[:500],
                "labels": detail.get("labelIds", []),
            })
        except Exception as e:
            print(f"Error: {e}")

    if not emails:
        return None

    analysis = analyze_emails(emails)

    # 自動加標籤
    for item in analysis.get("important", []):
        idx = item.get("index", 1) - 1
        if 0 <= idx < len(emails):
            category = item.get("category", "其他")
            apply_label(service, emails[idx]["id"], f"龍蝦/{category}")

    # 只有高優先才即時通知
    high = [e for e in analysis.get("important", []) if e.get("priority") == "高"]
    if not high:
        return None

    msg = "📧 重要新郵件！\n─────────────────\n"
    for e in high:
        msg += f"🔴 {e['subject'][:30]}\n"
        msg += f"寄件人：{e['sender'][:30]}\n"
        msg += f"摘要：{e['summary']}\n"
        msg += f"建議：{e['action']}\n\n"
    return msg.strip()

# ══════════════════════════════
# 每日早上摘要
# ══════════════════════════════
def daily_email_summary() -> str:
    """每天早上產生郵件摘要"""
    service = get_gmail_service()
    emails = get_unread_emails(service, max_results=30)

    if not emails:
        return "📧 今日無未讀郵件 ✅"

    analysis = analyze_emails(emails)

    # 自動加標籤
    for item in analysis.get("important", []):
        idx = item.get("index", 1) - 1
        if 0 <= idx < len(emails):
            category = item.get("category", "其他")
            apply_label(service, emails[idx]["id"], f"龍蝦/{category}")

    return format_line_message(analysis, emails)
