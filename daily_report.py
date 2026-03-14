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


def _safe_close_pair(symbol: str):
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period="5d", auto_adjust=False)

    if hist is None or hist.empty:
        return None

    close = hist["Close"].dropna()
    if len(close) < 2:
        return None

    prev_close = float(close.iloc[-2])
    last_close = float(close.iloc[-1])

    if symbol == "^TNX":
        prev_close = prev_close / 10.0
        last_close = last_close / 10.0

    change = last_close - prev_close
    pct = (change / prev_close) * 100 if prev_close else 0.0

    return {
        "price": round(last_close, 2),
        "change": round(change, 2),
        "pct": round(pct, 2),
    }


def get_market_data():
    tickers = {
        "Dow Jones": "^DJI",
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "SOX": "^SOX",
        "US10Y": "^TNX",
        "DXY": "DX-Y.NYB",
        "Gold": "GC=F",
        "WTI": "CL=F",
    }

    results = {}
    for name, symbol in tickers.items():
        try:
            results[name] = _safe_close_pair(symbol)
        except Exception as e:
            results[name] = None
            print(f"Error fetching {name} ({symbol}): {e}")
    return results


def _line_for_index(label: str, d: dict, suffix: str = "點") -> str:
    if not d:
        return f"{label}：數據抓取失敗"
    arrow = "▲" if d["change"] >= 0 else "▼"
    return f"{label}：{d['price']:,.2f} {suffix} {arrow}{abs(d['change']):,.2f} ({d['pct']:+.2f}%)"


def build_market_snapshot(data):
    tw_tz = pytz.timezone("Asia/Taipei")
    today = datetime.now(tw_tz)
    weekday_map = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

    lines = []
    lines.append(f"【{today.strftime('%Y年%m月%d日')}（{weekday_map[today.weekday()]}）財經日報】")
    lines.append("")
    lines.append("__INTRO__")
    lines.append("")
    lines.append("一、全球市場概覽")
    lines.append(_line_for_index("道瓊工業指數", data.get("Dow Jones")))
    lines.append(_line_for_index("標普500指數", data.get("S&P 500")))
    lines.append(_line_for_index("那斯達克指數", data.get("NASDAQ")))
    lines.append(_line_for_index("費城半導體指數", data.get("SOX")))
    lines.append("")
    lines.append("二、利率與大宗商品")

    d_us10y = data.get("US10Y")
    if d_us10y:
        arrow = "▲" if d_us10y["change"] >= 0 else "▼"
        lines.append(f"美國10年期公債：{d_us10y['price']:.2f}% {arrow}{abs(d_us10y['change']):.2f}")
    else:
        lines.append("美國10年期公債：數據抓取失敗")

    d_dxy = data.get("DXY")
    if d_dxy:
        arrow = "▲" if d_dxy["change"] >= 0 else "▼"
        lines.append(f"美元指數 (DXY)：{d_dxy['price']:.2f} {arrow}{abs(d_dxy['change']):.2f}")
    else:
        lines.append("美元指數 (DXY)：數據抓取失敗")

    d_wti = data.get("WTI")
    if d_wti:
        arrow = "▲" if d_wti["change"] >= 0 else "▼"
        lines.append(f"WTI 原油：{d_wti['price']:.2f} {arrow}{abs(d_wti['change']):.2f}")
    else:
        lines.append("WTI 原油：數據抓取失敗")

    d_gold = data.get("Gold")
    if d_gold:
        arrow = "▲" if d_gold["change"] >= 0 else "▼"
        lines.append(f"黃金：{d_gold['price']:.2f} {arrow}{abs(d_gold['change']):.2f}")
    else:
        lines.append("黃金：數據抓取失敗")

    return "\n".join(lines)


def generate_commentary_with_claude(snapshot_text: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = (
        "你是一位專業的財經日報撰寫助理，服務對象是銀行分行的理財專員。\n\n"
        "以下是今日固定版型的市場數據：\n\n"
        f"{snapshot_text}\n\n"
        "請根據這些數據與最新市場情況，撰寫以下內容，內容需簡潔、專業、口語易讀，方便理財專員晨會閱讀與對客戶說明。\n\n"
        "請完成：\n"
        "1. 開頭前言：2句，直接放在標題下方，不要加任何小標題。\n"
        "2. 撰寫【總經總覽】：2-3句，涵蓋全球總體經濟關鍵動態（例如利率、通膨、政策或地緣政治）。\n"
        "3. 撰寫【美國市場】：1-2句，重點說明美股走勢與主要驅動因素（例如科技股、利率、經濟數據）。\n"
        "4. 撰寫【債券市場】：1-2句，說明美國利率走向與固定收益商品觀察重點。\n\n"
        "風格要求：\n"
        "- 整體語氣：專業但口語化，像分行晨會摘要\n"
        "- 讓理財專員可以快速看懂重點\n"
        "- 避免過度學術化或過度冗長\n"
        "- 避免過度誇張或恐慌字眼\n"
        "- 每段不要過長\n"
        "- 總長度控制精簡\n\n"
        "輸出格式必須完全如下：\n\n"
        "【前言】\n"
        "(2句內容)\n\n"
        "【總經總覽】\n"
        "(內容)\n\n"
        "【美國市場】\n"
        "(內容)\n\n"
        "【債券市場】\n"
        "(內容)\n"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )

    full_text = ""
    for block in message.content:
        if hasattr(block, "text"):
            full_text += block.text
    return full_text.strip()


def extract_section(text: str, title: str) -> str:
    import re
    pattern = rf"【{re.escape(title)}】\s*(.*?)(?=\n【|$)"
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else ""


def build_final_report(data: dict) -> str:
    snapshot = build_market_snapshot(data)
    commentary = generate_commentary_with_claude(snapshot)

    intro = extract_section(commentary, "前言")
    macro = extract_section(commentary, "總經總覽")
    us_market = extract_section(commentary, "美國市場")
    bonds = extract_section(commentary, "債券市場")

    final_text = snapshot.replace("__INTRO__", intro if intro else "昨晚美股整體表現分化，市場持續關注利率與通膨動態。")
    final_text += "\n\n三、總經總覽\n"
    final_text += macro if macro else "全球市場持續關注通膨、利率與政策訊號，風險偏好維持審慎。"

    final_text += "\n\n四、美國市場\n"
    final_text += us_market if us_market else "美股走勢仍由大型科技股與利率預期主導，市場情緒偏中性。"

    final_text += "\n\n五、債券市場\n"
    final_text += bonds if bonds else "美債殖利率變化仍是固定收益商品的重要觀察指標，建議留意利率路徑。"

    return final_text.strip()


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
    print("Building final report...")
    report = build_final_report(market_data)
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
