import os
import requests
import yfinance as yf
from datetime import datetime
import anthropic
import pytz

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")


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


def updown_mark(value: float):
    return "🔺" if value >= 0 else "▼"


def _line_for_index(label: str, d: dict, suffix: str = "點") -> str:
    if not d:
        return f"{label}：數據抓取失敗"
    arrow = updown_mark(d["change"])
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
        arrow = updown_mark(d_us10y["change"])
        lines.append(f"美國10年期公債：{d_us10y['price']:.2f}% {arrow}{abs(d_us10y['change']):.2f}")
    else:
        lines.append("美國10年期公債：數據抓取失敗")

    d_dxy = data.get("DXY")
    if d_dxy:
        arrow = updown_mark(d_dxy["change"])
        lines.append(f"美元指數 (DXY)：{d_dxy['price']:.2f} {arrow}{abs(d_dxy['change']):.2f}")
    else:
        lines.append("美元指數 (DXY)：數據抓取失敗")

    d_wti = data.get("WTI")
    if d_wti:
        arrow = updown_mark(d_wti["change"])
        lines.append(f"WTI 原油：{d_wti['price']:.2f} {arrow}{abs(d_wti['change']):.2f}")
    else:
        lines.append("WTI 原油：數據抓取失敗")

    d_gold = data.get("Gold")
    if d_gold:
        arrow = updown_mark(d_gold["change"])
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
        "請上網搜尋最新、最相關的國際財經消息，再根據這些數據與新聞事件，撰寫以下內容。\n"
        "重點是要有新聞感與事件感，不要只寫空泛結論。\n\n"
        "請完成：\n"
        "1. 開頭市場重點：只寫1到2句，必須非常精簡，點出昨晚市場最重要的交易主線。\n"
        "2. 撰寫【總經總覽】：2-3句，必須提到具體事件或消息，例如聯準會官員談話、重要經濟數據、政策、關稅、地緣政治、油價變化等。\n"
        "3. 撰寫【美國市場】：1-2句，必須點出美股漲跌主因，例如科技股、AI、銀行股、能源股、財報或特定新聞。\n"
        "4. 撰寫【債券市場】：1-2句，必須說明美債殖利率變動背後的原因，並區分中短天期與長天期債券表現不一定一致；若只有10年期殖利率資訊，不可直接泛化成整體公債價格全面上揚或下跌。最後補一句固定收益商品的觀察重點。\n\n"
        "要求：\n"
        "- 一定要具體，不要寫成空泛模板。\n"
        "- 優先使用最近24小時內最重要的財經新聞脈絡。\n"
        "- 不要亂編新聞，如果沒有明確事件，就誠實寫市場主要關注焦點。\n"
        "- 語氣專業但口語化，像分行晨會摘要。\n"
        "- 開頭只可 1 到 2 句。\n"
        "- 每段不要過長。\n"
        "- 總長度控制精簡。\n\n"
        "輸出格式必須完全如下：\n\n"
        "【前言】\n"
        "(1到2句內容)\n\n"
        "【總經總覽】\n"
        "(內容)\n\n"
        "【美國市場】\n"
        "(內容)\n\n"
        "【債券市場】\n"
        "(內容)\n"
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1400,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
    except Exception:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1400,
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

    final_text = snapshot.replace(
        "__INTRO__",
        intro if intro else "昨晚美股整體表現分化，市場持續關注利率、通膨與政策訊號。"
    )

    final_text += "\n\n三、總經總覽\n"
    final_text += macro if macro else "全球市場持續關注通膨、利率與政策訊號，風險偏好維持審慎。"

    final_text += "\n\n四、美國市場\n"
    final_text += us_market if us_market else "美股走勢仍由大型科技股與利率預期主導，市場情緒偏中性。"

    final_text += "\n\n五、債券市場\n"
    final_text += bonds if bonds else "美債殖利率變化仍是固定收益商品的重要觀察指標，建議留意利率路徑。"

    return final_text.strip()


# ==============================
# 生圖功能
# ==============================
def generate_dalle_image(report_text: str) -> str | None:
    """用 gpt-image-1 根據日報內容生成資訊圖，回傳圖片 bytes"""
    if not OPENAI_API_KEY:
        print("[Image] 缺少 OPENAI_API_KEY，跳過生圖")
        return None
    try:
        from openai import OpenAI
        import base64
        client = OpenAI(api_key=OPENAI_API_KEY)

        tw_tz = pytz.timezone("Asia/Taipei")
        today = datetime.now(tw_tz)
        date_str = today.strftime("%Y/%m/%d")

        # 從日報萃取重點
        lines = report_text.split("\n")
        key_points = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith("【") and len(line) > 10:
                key_points.append(line)
            if len(key_points) >= 8:
                break

        points_text = "\n".join(f"• {p}" for p in key_points)

        prompt = f"""請製作一張專業的財經資訊圖，格式如下：

標題：{date_str} 財經日報

內容重點：
{points_text}

設計要求：
- 深藍色或深色背景，金色/白色文字
- 左上角放日期和標題
- 用清楚的區塊分隔各重點
- 每個重點前加適合的 emoji
- 底部加上「內部教育訓練資料，請勿外流」
- 整體風格像彭博終端機或專業財經雜誌
- 繁體中文
"""

        print(f"[Image] gpt-image-1 生圖中...")
        image_resp = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1536",
            quality="standard",
            n=1
        )
        # gpt-image-1 回傳 base64
        image_b64 = image_resp.data[0].b64_json
        if image_b64:
            print("[Image] gpt-image-1 生圖成功")
            return f"data:image/png;base64,{image_b64}"
        # 或者回傳 URL
        image_url = image_resp.data[0].url
        if image_url:
            print(f"[Image] gpt-image-1 URL: {image_url[:60]}...")
            return image_url
        return None
    except Exception as e:
        print(f"[Image] gpt-image-1 失敗，改用 DALL-E 3: {e}")
        # fallback 到 DALL-E 3
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            image_resp = client.images.generate(
                model="dall-e-3",
                prompt=f"Professional financial news infographic for {datetime.now(pytz.timezone('Asia/Taipei')).strftime('%Y/%m/%d')}, dark blue background, gold and white text, Bloomberg terminal style, showing market data charts",
                size="1792x1024",
                quality="standard",
                n=1
            )
            url = image_resp.data[0].url
            print(f"[Image] DALL-E 3 fallback: {url[:60]}...")
            return url
        except Exception as e2:
            print(f"[Image] DALL-E 3 也失敗: {e2}")
            return None


def upload_to_cloudinary(image_url: str) -> str | None:
    """把圖片 URL 上傳到 Cloudinary，回傳永久公開 URL"""
    if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
        print("[Image] 缺少 Cloudinary 環境變數，跳過上傳")
        return None
    try:
        import hashlib
        import time

        timestamp = str(int(time.time()))
        folder = "daily_report"
        # 簽名字串必須按字母順序排列參數
        params_to_sign = f"folder={folder}&timestamp={timestamp}"
        signature = hashlib.sha1(
            f"{params_to_sign}{CLOUDINARY_API_SECRET}".encode()
        ).hexdigest()

        upload_url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"
        # 支援 base64 格式
        file_data = image_url  # 可以是 URL 或 base64 data URI
        response = requests.post(upload_url, data={
            "file": file_data,
            "timestamp": timestamp,
            "api_key": CLOUDINARY_API_KEY,
            "signature": signature,
            "folder": folder,
        })
        if response.status_code == 200:
            data = response.json()
            public_url = data.get("secure_url", "")
            print(f"[Image] Cloudinary 上傳成功: {public_url[:60]}...")
            return public_url
        else:
            print(f"[Image] Cloudinary 上傳失敗: {response.status_code} {response.text[:200]}")
            return None
    except Exception as e:
        print(f"[Image] Cloudinary 上傳錯誤: {e}")
        return None


def send_line_image(image_url: str):
    """推播圖片到 LINE"""
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url
        }]
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        print("[Image] LINE 圖片推播成功")
    else:
        print(f"[Image] LINE 圖片推播失敗: {response.status_code} {response.text}")


def generate_and_send_image(report_text: str):
    """完整流程：生圖 → 上傳 → 推播"""
    print("[Image] 開始生成財經日報圖片...")
    dalle_url = generate_dalle_image(report_text)
    if not dalle_url:
        return

    cloudinary_url = upload_to_cloudinary(dalle_url)
    if not cloudinary_url:
        return

    send_line_image(cloudinary_url)
    print("[Image] 財經日報圖片推播完成！")


# ==============================
# 原有功能
# ==============================
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


def generate_report():
    """回傳 (report_text, image_url, weekly_calendar) 三個值"""
    market_data = get_market_data()
    report = build_final_report(market_data)
    return report, None, None


def main():
    report = generate_report()
    save_report_to_db(report)
    print("Sending daily report to LINE...")
    send_line_message(report)
    print("Daily report done!")

    # 新聞 PDF
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
