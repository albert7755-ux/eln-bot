"""
mail_monitor.py
每15分鐘由 Render Cron Job 執行
檢查最新未讀郵件，高優先立即推播到 LINE
"""
import os
from datetime import datetime, timezone, timedelta
from linebot import LineBotApi
from linebot.models import TextSendMessage

TZ_TAIPEI = timezone(timedelta(hours=8))

def main():
    print(f"[{datetime.now(TZ_TAIPEI).strftime('%Y/%m/%d %H:%M')}] Mail monitor starting...")

    line_bot_api = LineBotApi(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""))
    user_id = os.environ.get("LINE_USER_ID", "")

    if not user_id:
        print("Missing LINE_USER_ID")
        return

    try:
        from gmail_manager import check_new_emails
        msg = check_new_emails()
        if msg:
            line_bot_api.push_message(user_id, TextSendMessage(text=msg[:4900]))
            print("Important email notification sent!")
        else:
            print("No important new emails.")
    except Exception as e:
        print(f"Error: {e}")

    print("Done!")

if __name__ == "__main__":
    main()
