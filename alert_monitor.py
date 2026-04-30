"""
alert_monitor.py
價格警示監控系統
"""

import os
from datetime import datetime, timezone, timedelta

import pandas as pd
import yfinance as yf
from sqlalchemy import create_engine, text
from linebot import LineBotApi
from linebot.models import TextSendMessage

TZ_TAIPEI = timezone(timedelta(hours=8))

ALERT_TICKER_ALIAS = {
    "dxy": "DX-Y.NYB",
    "spx": "^GSPC",
    "sp500": "^GSPC",
    "ndx": "^NDX",
    "nasdaq100": "^NDX",
    "sox": "^SOX",
    "vix": "^VIX",
    "ust10y": "^TNX",
    "gold": "GC=F",
    "silver": "SI=F",
    "oil": "CL=F",
    "wti": "CL=F",
    "copper": "HG=F",
    "usdjpy": "JPY=X",
    "jpy": "JPY=X",
    "usd/jpy": "JPY=X",
    "eurusd": "EURUSD=X",
    "eur": "EURUSD=X",
    "gbpusd": "GBPUSD=X",
    "gbp": "GBPUSD=X",
    "usdtwd": "TWD=X",
    "twd": "TWD=X",
    "usdcnh": "CNH=X",
    "cnh": "CNH=X",
    "usdkrw": "KRW=X",
    "krw": "KRW=X",
}

def normalize_symbol(symbol: str) -> str:
    if not symbol:
        return ""
    raw = str(symbol).strip()
    lowered = raw.lower()
    return ALERT_TICKER_ALIAS.get(lowered, raw).upper()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

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
            ma_short INT,
            ma_long INT,
            deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            triggered_at TIMESTAMPTZ,
            trigger_count INT NOT NULL DEFAULT 0
        );
        """))
        conn.execute(text("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS deleted BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS triggered_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS trigger_count INT NOT NULL DEFAULT 0"))
        conn.execute(text("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS ma_short INT"))
        conn.execute(text("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS ma_long INT"))

def get_history(symbol: str, days: int = 250) -> pd.DataFrame | None:
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period=f"{days}d", auto_adjust=False)
        if data is None or data.empty:
            return None
        return data
    except Exception as e:
        print(f"History fetch error ({symbol}): {e}")
        return None

def get_current_price(symbol: str) -> float | None:
    data = get_history(symbol, days=10)
    if data is None or data.empty:
        return None
    try:
        return float(data["Close"].dropna().iloc[-1])
    except Exception:
        return None

def get_ma_from_data(data: pd.DataFrame, period: int) -> float | None:
    try:
        if data is None or data.empty or len(data) < period:
            return None
        ma_series = data["Close"].rolling(window=period).mean().dropna()
        if ma_series.empty:
            return None
        return float(ma_series.iloc[-1])
    except Exception:
        return None

def get_prev_ma_from_data(data: pd.DataFrame, period: int) -> float | None:
    try:
        if data is None or data.empty or len(data) < period + 1:
            return None
        ma_series = data["Close"].rolling(window=period).mean().dropna()
        if len(ma_series) < 2:
            return None
        return float(ma_series.iloc[-2])
    except Exception:
        return None

def get_active_alerts() -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(text("""
        SELECT
            id, chat_key, symbol, alert_type, condition,
            target_value, ma_period, ma_short, ma_long, trigger_count
        FROM price_alerts
        WHERE deleted = FALSE
          AND trigger_count < 2
          AND (triggered_at IS NULL OR triggered_at < NOW() - INTERVAL '1 day')
        ORDER BY created_at ASC
        """)).fetchall()
    return [
        {
            "id": r[0],
            "chat_key": r[1],
            "symbol": normalize_symbol(r[2]),
            "alert_type": r[3],
            "condition": r[4],
            "target_value": r[5],
            "ma_period": r[6],
            "ma_short": r[7],
            "ma_long": r[8],
            "trigger_count": r[9],
        }
        for r in rows
    ]

def mark_triggered(alert_id: int):
    with engine.begin() as conn:
        conn.execute(text("""
        UPDATE price_alerts
        SET triggered_at = NOW(),
            trigger_count = trigger_count + 1,
            deleted = CASE WHEN trigger_count + 1 >= 2 THEN TRUE ELSE FALSE END
        WHERE id = :id
        """), {"id": alert_id})

def check_alert(alert: dict) -> tuple[bool, str]:
    symbol = alert["symbol"]
    alert_type = alert["alert_type"]
    condition = (alert["condition"] or "").lower()
    target = alert["target_value"]
    ma_period = alert["ma_period"]
    ma_short = alert["ma_short"]
    ma_long = alert["ma_long"]

    data = get_history(symbol, days=260)
    if data is None or data.empty:
        return False, ""

    price = get_current_price(symbol)
    if price is None:
        return False, ""

    now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")

    if alert_type == "price":
        if condition == "above" and price >= target:
            msg = (f"🔔 價格警示觸發！\n標的：{symbol}\n條件：漲到 {target}\n現價：{price:.2f}\n時間：{now_str}")
            return True, msg
        if condition == "below" and price <= target:
            msg = (f"🔔 價格警示觸發！\n標的：{symbol}\n條件：跌到 {target}\n現價：{price:.2f}\n時間：{now_str}")
            return True, msg

    elif alert_type == "ma":
        ma = get_ma_from_data(data, ma_period)
        if ma is None:
            return False, ""
        if condition == "below" and price < ma:
            msg = (f"📉 均線警示觸發！\n標的：{symbol}\n條件：跌破 MA{ma_period}\n現價：{price:.2f}\nMA{ma_period}：{ma:.2f}\n時間：{now_str}")
            return True, msg
        if condition == "above" and price > ma:
            msg = (f"📈 均線警示觸發！\n標的：{symbol}\n條件：漲破 MA{ma_period}\n現價：{price:.2f}\nMA{ma_period}：{ma:.2f}\n時間：{now_str}")
            return True, msg

    elif alert_type == "ma_cross":
        if not ma_short or not ma_long:
            return False, ""
        short_now = get_ma_from_data(data, ma_short)
        short_prev = get_prev_ma_from_data(data, ma_short)
        long_now = get_ma_from_data(data, ma_long)
        long_prev = get_prev_ma_from_data(data, ma_long)
        if None in (short_now, short_prev, long_now, long_prev):
            return False, ""
        if condition == "cross" and short_prev <= long_prev and short_now > long_now:
            msg = (f"🚀 均線黃金交叉！\n標的：{symbol}\n條件：MA{ma_short} 上穿 MA{ma_long}\n現價：{price:.2f}\nMA{ma_short}：{short_now:.2f}\nMA{ma_long}：{long_now:.2f}\n時間：{now_str}")
            return True, msg
        if condition == "under" and short_prev >= long_prev and short_now < long_now:
            msg = (f"⚠️ 均線死亡交叉！\n標的：{symbol}\n條件：MA{ma_short} 下穿 MA{ma_long}\n現價：{price:.2f}\nMA{ma_short}：{short_now:.2f}\nMA{ma_long}：{long_now:.2f}\n時間：{now_str}")
            return True, msg

    return False, ""

def send_notification(chat_key: str, message: str):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        print("Missing LINE_CHANNEL_ACCESS_TOKEN")
        return
    api = LineBotApi(token)
    target_id = chat_key.split(":", 1)[1] if ":" in chat_key else chat_key
    api.push_message(target_id, TextSendMessage(text=message))

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
