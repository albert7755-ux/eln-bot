"""
alert_monitor.py
價格警示監控系統
- 每15分鐘由 Render Cron Job 執行
- 檢查股價/匯率/ETF 是否達到目標價或突破均線
- 符合條件則推播 LINE 通知
"""
import os
import sys
import yfinance as yf
import pandas as pd
from sqlalchemy import create_engine, text
from linebot import LineBotApi
from linebot.models import TextSendMessage
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))

# ══════════════════════════════
# DB 連線
# ══════════════════════════════
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL)

def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id SERIAL PRIMARY KEY,
            chat_key TEXT NOT NULL,
            symbol TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            condition TEXT NOT NULL,
            target_value FLOAT,
            ma_period INT,
            deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            triggered_at TIMESTAMPTZ
        );
        -- 相容處理
        ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS deleted BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS triggered_at TIMESTAMPTZ;
        ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS trigger_count INT NOT NULL DEFAULT 0;
        """))

# ══════════════════════════════
# 取得即時價格
# ══════════════════════════════
def get_current_price(symbol: str) -> float | None:
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1d", interval="5m")
        if data.empty:
            return None
        return float(data["Close"].iloc[-1])
    except Exception as e:
        print(f"Price fetch error ({symbol}): {e}")
        return None

def get_ma(symbol: str, period: int) -> float | None:
    try:
        ticker = yf.Ticker(symbol)
        # 多抓一些歷史資料確保能算出均線
        data = ticker.history(period=f"{period*2}d")
        if len(data) < period:
            return None
        return float(data["Close"].rolling(window=period).mean().iloc[-1])
    except Exception as e:
        print(f"MA fetch error ({symbol}, MA{period}): {e}")
        return None

# ══════════════════════════════
# 取得所有未觸發的警示
# ══════════════════════════════
def get_active_alerts() -> list[dict]:
    """撈出所有警示：未刪除、今天尚未觸發、觸發次數未超過2次"""
    with engine.begin() as conn:
        rows = conn.execute(text("""
        SELECT id, chat_key, symbol, alert_type, condition, target_value, ma_period, trigger_count
        FROM price_alerts
        WHERE deleted = FALSE
          AND trigger_count < 2
          AND (triggered_at IS NULL OR triggered_at < NOW() - INTERVAL '1 day')
        ORDER BY created_at ASC
        """)).fetchall()
    return [
        {
            "id": r[0], "chat_key": r[1], "symbol": r[2],
            "alert_type": r[3], "condition": r[4],
            "target_value": r[5], "ma_period": r[6],
            "trigger_count": r[7]
        }
        for r in rows
    ]

def mark_triggered(alert_id: int):
    """累加觸發次數，達2次自動刪除"""
    with engine.begin() as conn:
        conn.execute(text("""
        UPDATE price_alerts
        SET triggered_at = NOW(),
            trigger_count = trigger_count + 1,
            deleted = CASE WHEN trigger_count + 1 >= 2 THEN TRUE ELSE FALSE END
        WHERE id = :id
        """), {"id": alert_id})

# ══════════════════════════════
# 檢查條件
# ══════════════════════════════
def check_alert(alert: dict) -> tuple[bool, str]:
    symbol = alert["symbol"]
    alert_type = alert["alert_type"]
    condition = alert["condition"]  # above / below
    target = alert["target_value"]
    ma_period = alert["ma_period"]

    price = get_current_price(symbol)
    if price is None:
        return False, ""

    now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")

    if alert_type == "price":
        if condition == "above" and price >= target:
            msg = (f"🔔 價格警示觸發！\n"
                   f"標的：{symbol}\n"
                   f"條件：漲到 {target}\n"
                   f"現價：{price:.2f}\n"
                   f"時間：{now_str}")
            return True, msg
        elif condition == "below" and price <= target:
            msg = (f"🔔 價格警示觸發！\n"
                   f"標的：{symbol}\n"
                   f"條件：跌到 {target}\n"
                   f"現價：{price:.2f}\n"
                   f"時間：{now_str}")
            return True, msg

    elif alert_type == "ma":
        ma = get_ma(symbol, ma_period)
        if ma is None:
            return False, ""
        if condition == "below" and price < ma:
            msg = (f"📉 均線警示觸發！\n"
                   f"標的：{symbol}\n"
                   f"條件：跌破 MA{ma_period}\n"
                   f"現價：{price:.2f}\n"
                   f"MA{ma_period}：{ma:.2f}\n"
                   f"時間：{now_str}")
            return True, msg
        elif condition == "above" and price > ma:
            msg = (f"📈 均線警示觸發！\n"
                   f"標的：{symbol}\n"
                   f"條件：漲破 MA{ma_period}\n"
                   f"現價：{price:.2f}\n"
                   f"MA{ma_period}：{ma:.2f}\n"
                   f"時間：{now_str}")
            return True, msg

    return False, ""

# ══════════════════════════════
# 推播通知
# ══════════════════════════════
def send_notification(chat_key: str, message: str):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        print("Missing LINE_CHANNEL_ACCESS_TOKEN")
        return
    api = LineBotApi(token)
    user_id = chat_key.split(":", 1)[1] if ":" in chat_key else chat_key
    api.push_message(user_id, TextSendMessage(text=message))

# ══════════════════════════════
# 主流程
# ══════════════════════════════
def main():
    print(f"[{datetime.now(TZ_TAIPEI).strftime('%Y/%m/%d %H:%M')}] Alert monitor starting...")
    init_db()
    alerts = get_active_alerts()
    print(f"Active alerts: {len(alerts)}")

    for alert in alerts:
        try:
            triggered, message = check_alert(alert)
            if triggered:
                print(f"Triggered: {alert['symbol']} ({alert['alert_type']})")
                send_notification(alert["chat_key"], message)
                mark_triggered(alert["id"])
        except Exception as e:
            print(f"Error checking alert {alert['id']}: {e}")

    print("Done!")

if __name__ == "__main__":
    main()
