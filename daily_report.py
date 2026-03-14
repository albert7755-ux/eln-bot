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


# ==============================
# 抓市場數據
# ==============================

def get_market_data():

    tickers = {
        "Dow": "^DJI",
        "SP500": "^GSPC",
        "NASDAQ": "^IXIC",
        "SOX": "^SOX",
        "US10Y": "^TNX",
        "DXY": "DX-Y.NYB",
        "WTI": "CL=F",
        "GOLD": "GC=F",
    }

    data = {}

    for name, symbol in tickers.items():

        try:

            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")

            if len(hist) < 2:
                data[name] = None
                continue

            prev = hist["Close"].iloc[-2]
            last = hist["Close"].iloc[-1]

            if symbol == "^TNX":
                prev = prev / 10
                last = last / 10

            change = last - prev
            pct = (change / prev) * 100

            data[name] = {
                "price": round(last, 2),
                "change": round(change, 2),
                "pct": round(pct, 2)
            }

        except Exception as e:
            print("fetch error:", name, e)
            data[name] = None

    return data


# ==============================
# 上下符號
# ==============================

def arrow(v):

    if v >= 0:
        return "🔺"
    else:
        return "🔻"


# ==============================
# 市場行情區
# ==============================

def market_section(data):

    lines = []

    lines.append("一、全球市場概覽")

    def line(label, d, unit="點"):

        if not d:
            return f"{label}：數據抓取失敗"

        return f"{label}：{d['price']:,.0f} {unit} {arrow(d['change'])}{abs(d['change']):,.0f} ({d['pct']:+.2f}%)"

    lines.append(line("道瓊工業指數", data["Dow"]))
    lines.append(line("標普500指數", data["SP500"]))
    lines.append(line("那斯達克指數", data["NASDAQ"]))
    lines.append(line("費城半導體指數", data["SOX"]))

    lines.append("")
    lines.append("二、利率與大宗商品")

    if data["US10Y"]:
        d = data["US10Y"]
        lines.append(f"美國10年期公債：{d['price']:.2f}% {arrow(d['change'])}{abs(d['change']):.2f}")

    if data["DXY"]:
        d = data["DXY"]
        lines.append(f"美元指數 (DXY)：{d['price']:.2f} {arrow(d['change'])}{abs(d['change']):.2f}")

    if data["WTI"]:
        d = data["WTI"]
        lines.append(f"WTI 原油：{d['price']:.2f} {arrow(d['change'])}{abs(d['change']):.2f}")

    if data["GOLD"]:
        d = data["GOLD"]
        lines.append(f"黃金：{d['price']:.2f} {arrow(d['change'])}{abs(d['change']):.2f}")

    return "\n".join(lines)


# ==============================
# Claude 生成新聞內容
# ==============================

def generate_commentary(market_text):

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""
你是一位銀行晨會財經日報撰寫者，服務對象是分行理財專員。

以下是市場數據：

{market_text}

請上網搜尋最新財經新聞，並完成以下內容：

1. 開頭市場重點  
只寫 **1到2句**，必須非常精簡，點出昨晚市場最重要的交易主線。

2. 【總經總覽】  
2句，必須提到具體事件，例如  
- 聯準會政策  
- 通膨數據  
- 油價  
- 地緣政治  
- 政策或關稅

3. 【美國市場】  
1到2句，說明美股漲跌主因  
例如科技股、AI、能源股、財報、政策。

4. 【債券市場】  
1到2句，說明美債殖利率變動原因。  
必須注意：  
不可把單一天期殖利率變動解讀為所有公債價格同方向變動。

語氣要求：

- 專業但口語
- 有新聞感
- 不要空泛
- 長度精簡
"""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )

    text = ""

    for block in msg.content:
        if hasattr(block, "text"):
            text += block.text

    return text.strip()


# ==============================
# 組合晨報
# ==============================

def build_report():

    tw = pytz.timezone("Asia/Taipei")

    today = datetime.now(tw)

    weekday = ["週一","週二","週三","週四","週五","週六","週日"]

    title = f"【{today.strftime('%Y年%m月%d日')}（{weekday[today.weekday()]}）財經日報】"

    market_data = get_market_data()

    market_text = market_section(market_data)

    commentary = generate_commentary(market_text)

    report = f"{title}\n\n{commentary}\n\n{market_text}"

    return report


# ==============================
# 推送 LINE
# ==============================

def send_line(text):

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "to": LINE_USER_ID,
        "messages":[
            {
                "type":"text",
                "text": text[:4900]
            }
        ]
    }

    r = requests.post(url, headers=headers, json=payload)

    print("LINE:", r.status_code)


# ==============================
# 主程式
# ==============================

def main():

    report = build_report()

    print(report)

    send_line(report)


if __name__ == "__main__":
    main()
