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
            hist = ticker.history(period="5d", auto_adjust=False)

            if hist is None or hist.empty:
                results[name] = None
                continue

            close = hist["Close"].dropna()

            if len(close) < 2:
                results[name] = None
                continue

            prev_close = float(close.iloc[-2])
            last_close = float(close.iloc[-1])

            if symbol == "^TNX":
                prev_close = prev_close / 10.0
                last_close = last_close / 10.0

            change = last_close - prev_close
            pct = (change / prev_close) * 100 if prev_close else 0.0

            results[name] = {
                "price": round(last_close, 2),
                "change": round(change, 2),
                "pct": round(pct, 2),
            }
        except Exception as e:
            results[name] = None
            print(f"Error fetching {name}: {e}")

    return results


def format_market_data(data):
    tw_tz = pytz.timezone("Asia/Taipei")
    today = datetime.now(tw_tz)
    yesterday = today - timedelta(days=1)
    weekday_map = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

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
            lines.append(f"- {label}: {d['price']:,.2f} 點  {arrow}{abs(d['change']):,.2f} ({d['pct']:+.2f}%)")
        else:
            lines.append(f"- {label}: 數據抓取失敗")

    lines.append("\n二、美國10年期公債殖利率")
    d = data.get("US10Y")
    if d:
        arrow = "▲" if d["change"] >= 0 else "▼"
        lines.append(f"- 10年期: {d['price']:.2f}%  {arrow}{abs(d['change']):.2f}%")
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
            lines.append(f"- {label}: ${d['price']:,.2f}/{unit}  {arrow}{abs(d['change']):,.2f} ({d['pct']:+.2f}%)")
        else:
            lines.append(f"- {label}: 數據抓取失敗")

    return "\n".join(lines)


def generate_report_with_claude(market_text):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = (
        "你是一位專業的財經日報撰寫助理，服務對象是銀行分行的理財專員。\n\n"
        "以下是今日的市場原始數據:\n\n"
        + market_text +
        "\n\n請完成以下任務，內容需簡潔、專業、口語易讀，方便理財專員晨會閱讀與對客戶說明：\n"
        "1. 撰寫【總經總覽】：2-3句，涵蓋全球總體經濟關鍵動態（例如利率、通膨、政策或地緣政治）。\n"
        "2. 撰寫【美國市場】：1-2句，重點說明美股走勢與主要驅動因素（例如科技股、利率、經濟數據）。\n"
        "3. 撰寫【債券市場】：1-2句，說明美國利率走向與固定收益商品觀察重點。\n"
        "4. 撰寫【投資建議】：條列2-3點，涵蓋固定收益、匯率、市場觀察重點，內容需實用、可作為理專與客戶溝通的方向。\n\n"
        "風格要求：\n"
        "- 整體語氣：專業但口語化，像分行晨會摘要\n"
        "- 讓理財專員可以快速看懂重點\n"
        "- 避免過度學術化或過度冗長\n"
        "- 避免過度誇張或恐慌字眼\n"
        "- 每段不要過長，投資建議要具體可用\n"
        "- 整體字數控制在 180~220 字左右\n\n"
        "輸出格式：\n\n"
        "【總經總覽】\n"
        "(內容)\n\n"
        "【美國市場】\n"
        "(內容)\n\n"
        "【債券市場】\n"
        "(內容)\n\n"
        "【投資建議】\n"
        "- (建議1)\n"
        "- (建議2)\n"
        "- (建議3 可選)"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1800,
        messages=[{"role": "user", "content": prompt}]
    )

    full_text = ""
    for block in message.content:
        if hasattr(block, "text"):
            full_text += block.text
    return full_text.strip()


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
        "messages": [{"type": "text", "text": text[:4900]}]
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
    from linebot import LineBotApi
    from linebot.models import TextSendMessage

    line_bot_api = LineBotApi(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""))
    user_id = os.environ.get("LINE_USER_ID", "")
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
