"""
spending_analyzer.py
掃描 Gmail 消費相關信件，整理月度消費明細
"""
import os
import re
import json
from datetime import datetime, timezone, timedelta
from gmail_manager import get_gmail_service, extract_body
import anthropic

TZ_TAIPEI = timezone(timedelta(hours=8))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# 消費相關關鍵字（寄件者或主旨）
SPENDING_KEYWORDS = [
    # 信用卡
    "富邦", "fubon", "玉山", "esun",
    "信用卡", "帳單", "刷卡", "消費通知", "交易通知",
    # 電商
    "momo", "蝦皮", "shopee", "pchome", "訂單確認", "付款成功", "購買成功",
    # 訂閱
    "apple", "anthropic", "openai", "google", "claude", "chatgpt", "gemini",
    "receipt", "invoice", "payment", "subscription", "invoice",
    # 通用
    "收據", "發票", "付款", "扣款", "繳費"
]

# 廣告過濾關鍵字
AD_KEYWORDS = [
    "優惠", "折扣", "限時", "活動", "促銷", "特價", "滿額", "點數",
    "newsletter", "unsubscribe", "行銷"
]


def get_spending_emails(days: int = 31) -> list:
    """抓取近 N 天的消費相關信件"""
    service = get_gmail_service()
    
    # 計算起始時間
    start_time = datetime.now(TZ_TAIPEI) - timedelta(days=days)
    after_timestamp = int(start_time.timestamp())
    
    # 搜尋消費相關信件
    query = f"after:{after_timestamp} (富邦 OR 玉山 OR momo OR 蝦皮 OR pchome OR apple OR anthropic OR openai OR 帳單 OR 刷卡 OR 訂單 OR receipt OR invoice OR payment)"
    
    result = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=50
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
            subject = headers.get("Subject", "")
            sender = headers.get("From", "")
            date_str = headers.get("Date", "")
            
            # 過濾廣告
            combined = (subject + sender).lower()
            if any(ad in combined for ad in AD_KEYWORDS):
                continue
            
            # 確認是消費相關
            if not any(kw.lower() in combined for kw in SPENDING_KEYWORDS):
                continue
            
            body = extract_body(detail["payload"])
            
            emails.append({
                "subject": subject,
                "sender": sender,
                "date": date_str[:16],
                "body": body[:800]
            })
        except Exception as e:
            print(f"Error: {e}")
    
    return emails


def analyze_spending_with_claude(emails: list, month_str: str = "") -> str:
    """用 Claude 分析消費明細"""
    if not emails:
        return "📭 本期找不到消費相關信件。"
    
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    email_text = ""
    for i, e in enumerate(emails):
        email_text += f"\n[{i+1}] 寄件者：{e['sender']}\n主旨：{e['subject']}\n日期：{e['date']}\n內容摘要：{e['body'][:300]}\n"
    
    now = datetime.now(TZ_TAIPEI)
    period = month_str or now.strftime("%Y年%m月")
    
    prompt = f"""以下是 {period} 的消費相關信件，請幫我整理成消費明細：

{email_text}

請按以下格式輸出：

💳 {period} 消費明細
─────────────────

📊 消費分類統計
• 信用卡帳單：列出富邦、玉山各自金額（若有）
• 🛍️ 電商購物：momo、蝦皮、PChome 各筆消費
• 📱 訂閱服務：Apple、Anthropic、OpenAI 等各筆金額
• 🏦 其他：其他消費

💰 本期消費總結
• 總計：約 NT$ XXXX（或 USD XXX）
• 最大支出：XXX
• 消費方向分析：2-3句總結本月消費習慣

⚠️ 注意事項：
- 若信件中有明確金額請列出，沒有則寫「金額未顯示」
- 廣告或通知類信件請忽略
- 幣別請標示清楚（台幣/美金）
- 若同一張卡有多筆交易，請分別列出"""

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return resp.content[0].text.strip()


def get_monthly_spending_report(days: int = 31) -> str:
    """主函式：抓信件 + 分析 + 回傳報告"""
    try:
        print("[Spending] 抓取消費信件...")
        emails = get_spending_emails(days=days)
        print(f"[Spending] 找到 {len(emails)} 封消費相關信件")
        
        now = datetime.now(TZ_TAIPEI)
        month_str = now.strftime("%Y年%m月")
        
        report = analyze_spending_with_claude(emails, month_str)
        return report
    except Exception as e:
        return f"❌ 消費分析失敗：{str(e)[:200]}"
