import os
import requests
import yfinance as yf
from datetime import datetime, timedelta
import anthropic
import pytz

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_market_data():
    tickers = {
        "Dow Jones": "^DJI",
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "SOX": "^SOX",
        "US10Y": "^TNX",
        "Gold": "GC=F",
        "Silver": "SI=F",
        "WTI": "CL=F",
    }
    results = {}
    for name, symbol in tickers.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="2d")
            if len(hist) >= 2:
                prev_close = hist["Close"].iloc[-2]
                last_close = hist["Close"].iloc[-1]
                change = last_close - prev_close
                pct = (change / prev_close) * 100
                results[name] = {
                    "price": round(last_close, 2),
                    "change": round(change, 2),
                    "pct": round(pct, 2),
                }
            else:
                results[name] = None
        except Exception as e:
            results[name] = None
            print(f"Error fetching {name}: {e}")
    return results

def format_market_data(data):
    tw_tz = pytz.timezone("Asia/Taipei")
    today = datetime.now(tw_tz)
    yesterday = today - timedelta(days=1)
    weekday_map = ["週一","週二","週三","週四","週五","週六","週日"]

    lines = []
    lines.append(f"【{today.strftime('%Y年%m月%d日')}（{weekday_map[today.weekday()]}）財經日報】")
    lines.append(f"反映 {yesterday.strftime('%m/%d')}（{weekday_map[yesterday.weekday()]}）美股收盤\n")

    lines.append("一、美股四大指數")
    mapping = [
        ("Dow Jones", "道瓊 (DJI)"),
        ("S&P 500", "標普500 (S&P)"),
        ("NASDAQ", "那斯達克 (IXIC)"),
        ("SOX", "費半 (SOX)"),
    ]
    for key, label in mapping:
        d = data.get(key)
        if d:
            arrow = "▲" if d["change"] >= 0 else "▼"
            lines.append(f"- {label}: {d['price']:,.2f} 點  {arrow}{abs(d['change']):,.2f}({d['pct']:+.2f}%)")
        else:
            lines.append(f"- {label}: 數據抓取失敗")

    lines.append("\n二、美國10年期公債殖利率")
    d = data.get("US10Y")
    if d:
        arrow = "▲" if d["change"] >= 0 else "▼"
        lines.append(f"- 10年期: {d['price']:.3f}%  {arrow}{abs(d['change']):.3f}%")
    else:
        lines.append("- 10年期: 數據抓取失敗")

    lines.append("\n三、原物料")
    commodities = [
        ("Gold", "黃金", "盎司"),
        ("Silver", "白銀", "盎司"),
        ("WTI", "原油(WTI)", "桶"),
    ]
    for key, label, unit in commodities:
        d = data.get(key)
        if d:
            arrow = "▲" if d["change"] >= 0 else "▼"
            lines.append(f"- {label}: ${d['price']:,.2f}/{unit}  {arrow}{abs(d['change']):,.2f}({d['pct']:+.2f}%)")
        else:
            lines.append(f"- {label}: 數據抓取失敗")

    return "\n".join(lines)

def generate_report_with_claude(market_text):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = (
        "你是一位專業的財經日報撰寫助理，服務對象是投資輔銷人員。\n\n"
        "以下是今日的市場原始數據:\n\n"
        + market_text
        + "\n\n請完成以下任務:\n"
        "1. 根據數據寫一段簡短的市場總覽(2-3句，語氣專業穩健)\n"
        "2. 搜尋今日最重要的總體經濟新聞2則(一句話摘要，客觀陳述)\n"
        "3. 搜尋今日最重要的個股新聞2則(一句話摘要，客觀陳述)\n"
        "4. 給出股市、債市、原物料三個方向的操作策略(各一句話)\n\n"
        "策略語氣要求:\n"
        "- 正向引導為主，點出布局機會\n"
        "- 避免使用恐慌、崩跌、危機等負面字眼\n"
        "- 適合投資輔銷人員傳閱的專業語氣\n\n"
        "輸出格式如下，純文字條列:\n\n"
        + market_text
        + "\n\n市場總覽\n(在這裡寫)\n\n"
        "四、焦點新聞\n\n"
        "【總經】\n- (新聞1)\n- (新聞2)\n\n"
        "【個股】\n- (新聞1)\n- (新聞2)\n\n"
        "五、操作策略\n"
        "- 股市:\n- 債市:\n- 原物料:\n\n"
        "以上為根據最新收盤資訊校對之報表。如需特定板塊深入分析，請隨時告知。"
    )
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )
    full_text = ""
    for block in message.content:
        if hasattr(block, "text"):
            full_text += block.text
    return full_text

def save_report_to_db(report_text):
    if not DATABASE_URL:
        return
    try:
        from sqlalchemy import create_engine, text
        db_url = DATABASE_URL
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.begin() as conn:
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS daily_report_cache (
                id SERIAL PRIMARY KEY,
                report_text TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """))
            conn.execute(text("""
            INSERT INTO daily_report_cache (report_text, created_at)
            VALUES (:r, NOW())
            """), {"r": report_text})
        print("Report saved to DB")
    except Exception as e:
        print(f"DB save failed: {e}")

def send_line_message(text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}]
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        print("LINE push success")
    else:
        print(f"LINE push failed: {response.status_code} {response.text}")

def generate_report() -> str:
    print("Fetching market data...")
    market_data = get_market_data()
    print("Formatting data...")
    market_text = format_market_data(market_data)
    print("Generating report with Claude...")
    report = generate_report_with_claude(market_text)
    return report

def main():
    report = generate_report()
    save_report_to_db(report)
    print("Sending daily report to LINE...")
    send_line_message(report)
    print("Daily report done!")

    # 自動產生新聞摘要PDF
    try:
        print("Fetching news and generating PDF...")
        from news_fetcher import generate_news_report
        from pdf_generator import create_and_upload_pdf
        from linebot import LineBotApi
        from linebot.models import TextSendMessage
        news_report = generate_news_report()
        link = create_and_upload_pdf("news", news_report)
        _api = LineBotApi(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""))
        _user_id = os.environ.get("LINE_USER_ID", "")
        _api.push_message(_user_id, TextSendMessage(text=f"📰 今日財經新聞摘要 PDF\n\n{link}"))
        print("News PDF sent!")
    except Exception as e:
        print(f"News PDF error: {e}")

def send_email_summary():
    """早上推播郵件摘要"""
    from linebot import LineBotApi
    from linebot.models import TextSendMessage
    line_bot_api = LineBotApi(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN",""))
    user_id = os.environ.get("LINE_USER_ID","")
    if not user_id:
        print("Missing LINE_USER_ID")
        return
    try:
        from gmail_manager import daily_email_summary
        summary = daily_email_summary()
        line_bot_api.push_message(user_id, TextSendMessage(text=summary[:4900]))
        print("Email summary sent!")
    except Exception as e:
        print(f"Email summary error: {e}")

if __name__ == "__main__":
    main()
    send_email_summary()
