import os
import io
import base64
import requests
import yfinance as yf
from datetime import datetime
import anthropic
import pytz

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")
IMGUR_CLIENT_ID = os.environ.get("IMGUR_CLIENT_ID")


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
    return "🔺" if value >= 0 else "🔻"


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
        "1. 開頭市場重點：用一段話（不超過2句）描述昨日整體行情走勢，語氣像晨會口頭摘要，必須點出最重要的交易主線，不要條列、不要分點，用流暢連貫的中文寫成一段。\n"
        "2. 撰寫【總經總覽】：2-3句，必須提到具體事件或消息，例如聯準會官員談話、重要經濟數據、政策、關稅、地緣政治、油價變化等。\n"
        "3. 撰寫【美國市場】：1-2句，必須點出美股漲跌主因，例如科技股、AI、銀行股、能源股、財報或特定新聞。\n"
        "4. 撰寫【債券市場】：1-2句，必須說明美債殖利率變動背後的原因，並區分中短天期與長天期債券表現不一定一致；若只有10年期殖利率資訊，不可直接泛化成整體公債價格全面上揚或下跌。最後補一句固定收益商品的觀察重點。\n\n"
        "要求：\n"
        "- 一定要具體，不要寫成空泛模板。\n"
        "- 優先使用最近24小時內最重要的財經新聞脈絡。\n"
        "- 不要亂編新聞，如果沒有明確事件，就誠實寫市場主要關注焦點。\n"
        "- 語氣專業但口語化，像分行晨會摘要。\n"
        "- 開頭必須是一段連貫文字，不可分點或條列。\n"
        "- 每段不要過長。\n"
        "- 總長度控制精簡。\n\n"
        "輸出格式必須完全如下：\n\n"
        "【前言】\n"
        "(一段話，不超過2句，用連貫文字描述昨日行情走勢)\n\n"
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


def generate_news_image(report_text: str, market_data: dict) -> bytes:
    """
    根據財經日報內容，生成一張深色風格的市場重點摘要圖片。
    回傳 PNG bytes。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib import font_manager
    import textwrap

    # ── 嘗試載入中文字體 ──
    font_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/opt/render/project/src/fonts/NotoSansTC-Regular.ttf",
        "/home/user/fonts/NotoSansTC-Regular.ttf",
    ]
    cjk_font = None
    for fp in font_candidates:
        if os.path.exists(fp):
            cjk_font = font_manager.FontProperties(fname=fp)
            break

    tw_tz = pytz.timezone("Asia/Taipei")
    today = datetime.now(tw_tz)
    weekday_map = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    date_str = today.strftime("%Y.%m.%d")
    weekday_str = weekday_map[today.weekday()]

    # ── 擷取各段摘要 ──
    macro   = extract_section(report_text, "總經總覽")[:80]
    us_mkt  = extract_section(report_text, "美國市場")[:80]
    bonds   = extract_section(report_text, "債券市場")[:80]

    # ── 市場數據格式化 ──
    def fmt_row(name, d, suffix=""):
        if not d:
            return name, "N/A", "—", "#888888"
        chg = d["change"]
        color = "#FF5C5C" if chg >= 0 else "#4AE8A0"
        arrow = "▲" if chg >= 0 else "▼"
        return (
            name,
            f"{d['price']:,.2f}{suffix}",
            f"{arrow} {abs(chg):,.2f} ({d['pct']:+.2f}%)",
            color,
        )

    rows = [
        fmt_row("Dow Jones", market_data.get("Dow Jones")),
        fmt_row("S&P 500",   market_data.get("S&P 500")),
        fmt_row("NASDAQ",    market_data.get("NASDAQ")),
        fmt_row("SOX",       market_data.get("SOX")),
        fmt_row("US10Y",     market_data.get("US10Y"), "%"),
        fmt_row("DXY",       market_data.get("DXY")),
        fmt_row("Gold",      market_data.get("Gold")),
        fmt_row("WTI",       market_data.get("WTI")),
    ]

    # ── 色彩配置 ──
    BG    = "#0D1117"
    CARD  = "#161B22"
    BORDER = "#30363D"
    GOLD  = "#D4A843"
    WHITE = "#E6EDF3"
    GREY  = "#8B949E"

    kw = {"fontproperties": cjk_font} if cjk_font else {}

    fig = plt.figure(figsize=(10, 13), facecolor=BG)
    fig.patch.set_facecolor(BG)

    # ── 標題區 ──
    ax_title = fig.add_axes([0.0, 0.91, 1.0, 0.09])
    ax_title.set_facecolor(BG)
    ax_title.axis("off")
    ax_title.text(0.04, 0.75, "財經日報", fontsize=22, color=GOLD,
                  fontweight="bold", va="center", **kw)
    ax_title.text(0.04, 0.25,
                  f"Market Morning Brief  |  {date_str}  {weekday_str}",
                  fontsize=10, color=GREY, va="center")
    ax_title.axhline(y=0.02, xmin=0.03, xmax=0.97, color=BORDER, linewidth=0.8)

    # ── 市場數據表格 ──
    ax_tbl = fig.add_axes([0.03, 0.56, 0.94, 0.34])
    ax_tbl.set_facecolor(BG)
    ax_tbl.axis("off")
    ax_tbl.text(0, 1.02, "市場數據", fontsize=12, color=GOLD,
                fontweight="bold", transform=ax_tbl.transAxes, **kw)

    col_x = [0.01, 0.30, 0.62]
    row_h = 1.0 / (len(rows) + 1)

    for xi, header in zip(col_x, ["指數 / 資產", "最新價格", "漲跌"]):
        ax_tbl.text(xi, 1.0 - row_h * 0.5, header,
                    fontsize=8.5, color=GREY, va="center",
                    transform=ax_tbl.transAxes, **kw)

    ax_tbl.plot([0.01, 0.99], [1.0 - row_h, 1.0 - row_h],
                color=BORDER, linewidth=0.6, transform=ax_tbl.transAxes)

    for i, (name, price, chg, color) in enumerate(rows):
        y = 1.0 - row_h * (i + 1.5)
        ax_tbl.text(col_x[0], y, name,  fontsize=9.5, color=WHITE,
                    va="center", transform=ax_tbl.transAxes)
        ax_tbl.text(col_x[1], y, price, fontsize=9.5, color=WHITE,
                    va="center", transform=ax_tbl.transAxes)
        ax_tbl.text(col_x[2], y, chg,   fontsize=9.5, color=color,
                    va="center", transform=ax_tbl.transAxes)
        if i < len(rows) - 1:
            sep_y = y - row_h * 0.45
            ax_tbl.plot([0.01, 0.99], [sep_y, sep_y],
                        color=BORDER, linewidth=0.4, transform=ax_tbl.transAxes)

    # ── 新聞摘要三欄卡片 ──
    sections = [
        ("📊 總經總覽", macro),
        ("🇺🇸 美國市場", us_mkt),
        ("📈 債券市場", bonds),
    ]

    card_top = 0.52
    card_h   = 0.18
    card_gap = 0.03
    card_w   = (1.0 - 0.06 - card_gap * 2) / 3

    for i, (sec_title, sec_text) in enumerate(sections):
        left = 0.03 + i * (card_w + card_gap)

        rect = mpatches.FancyBboxPatch(
            (left, card_top - card_h), card_w, card_h,
            boxstyle="round,pad=0.01",
            linewidth=0.8, edgecolor=BORDER,
            facecolor=CARD, transform=fig.transFigure,
            figure=fig, clip_on=False
        )
        fig.add_artist(rect)

        ax_c = fig.add_axes([left + 0.01, card_top - card_h + 0.01,
                             card_w - 0.02, card_h - 0.02])
        ax_c.set_facecolor(CARD)
        ax_c.axis("off")
        ax_c.text(0, 0.92, sec_title, fontsize=9, color=GOLD,
                  fontweight="bold", va="top",
                  transform=ax_c.transAxes, **kw)
        wrapped = textwrap.fill(sec_text, width=28)
        ax_c.text(0, 0.72, wrapped, fontsize=7.8, color=WHITE,
                  va="top", linespacing=1.5,
                  transform=ax_c.transAxes, **kw)

    # ── 底部版權 ──
    ax_foot = fig.add_axes([0.0, 0.0, 1.0, 0.03])
    ax_foot.set_facecolor(BG)
    ax_foot.axis("off")
    ax_foot.text(0.5, 0.5,
                 "本圖表僅供參考，不構成投資建議  |  Albert Claw Bot",
                 fontsize=7.5, color=GREY, ha="center", va="center")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def upload_to_imgur(image_bytes: bytes) -> str:
    """上傳圖片到 Imgur，回傳圖片直連 URL。"""
    if not IMGUR_CLIENT_ID:
        print("Missing IMGUR_CLIENT_ID, skipping image upload")
        return ""
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        resp = requests.post(
            "https://api.imgur.com/3/image",
            headers={"Authorization": f"Client-ID {IMGUR_CLIENT_ID}"},
            data={"image": b64, "type": "base64"},
            timeout=30,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("success"):
            return data["data"]["link"]
        else:
            print(f"Imgur upload failed: {data}")
            return ""
    except Exception as e:
        print(f"Imgur upload error: {e}")
        return ""


def build_final_report(data: dict) -> tuple:
    snapshot = build_market_snapshot(data)
    commentary = generate_commentary_with_claude(snapshot)

    intro     = extract_section(commentary, "前言")
    macro     = extract_section(commentary, "總經總覽")
    us_market = extract_section(commentary, "美國市場")
    bonds     = extract_section(commentary, "債券市場")

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

    return final_text.strip(), data


def generate_weekly_economic_calendar() -> str:
    """
    用 Claude web search 查詢本週重要經濟數據（含預期值、優劣方向、央行利率預期）。
    回傳格式化週曆文字。僅在週一（台北時間）呼叫。
    """
    from datetime import timedelta
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    tw_tz = pytz.timezone("Asia/Taipei")
    today = datetime.now(tw_tz)
    weekday_map_zh = ["週一", "週二", "週三", "週四", "週五"]

    mon = today - timedelta(days=today.weekday())
    week_dates = []
    for i in range(5):
        d = mon + timedelta(days=i)
        week_dates.append(f"{weekday_map_zh[i]}（{d.strftime('%m/%d')}）")

    fri = mon + timedelta(days=4)
    header_range = f"{mon.strftime('%m/%d')}－{fri.strftime('%m/%d')}"

    prompt = (
        f"今天是台北時間 {today.strftime('%Y年%m月%d日')}（週一）。\n\n"
        "請用 web search 查詢本週（週一到週五）全球重要經濟數據與央行會議時程。\n\n"
        "【篩選原則】\n"
        "只列出市場真正在意的重要數據與事件，例如：\n"
        "• 美國：CPI、PPI、PCE、非農就業、初領失業金、零售銷售、ISM PMI、FOMC利率決策\n"
        "• 歐元區：CPI、ECB利率決策、PMI\n"
        "• 中國：CPI、PPI、PMI、工業生產\n"
        "• 日本：BOJ利率決策、CPI\n"
        "• 英國：CPI、BOE利率決策\n"
        "不重要的數據請勿列出。若某天真的沒有重要數據，寫「• 無重要數據」。\n\n"
        "【每筆數據請附上以下資訊】\n"
        "一般經濟數據格式：\n"
        "• 🇺🇸 數據名稱（月份）｜預期：X% ／ 前值：X%\n"
        "  → 若高於預期：[對市場的含義，例如「通膨偏強，不利降息」]\n"
        "  → 若低於預期：[對市場的含義]\n\n"
        "央行利率決策格式：\n"
        "• 🇺🇸 FOMC 利率決策｜目前利率：X.XX%\n"
        "  市場預期：[維持不變／升息／降息]（市場隱含機率約X%）\n"
        "  → 若意外[升息/降息]：[對債市/股市/匯率的影響]\n\n"
        f"本週日期：{' / '.join(week_dates)}\n\n"
        "【輸出格式】必須完全如下，不得省略任何區塊：\n\n"
        f"📅 本週重要經濟數據｜{header_range}\n\n"
        f"{week_dates[0]}\n"
        "• [數據或「無重要數據」]\n\n"
        f"{week_dates[1]}\n"
        "• [數據]\n\n"
        f"{week_dates[2]}\n"
        "• [數據]\n\n"
        f"{week_dates[3]}\n"
        "• [數據]\n\n"
        f"{week_dates[4]}\n"
        "• [數據]\n\n"
        "💡 本週市場關注重點：[一句話總結本週最重磅事件與潛在市場影響]"
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1800,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
    except Exception:
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


def generate_report() -> tuple:
    """
    給 /daily 指令呼叫。
    回傳 (report_text, image_url, weekly_calendar)
    weekly_calendar 只有週一才有內容，其他天為空字串。
    image_url 若生成失敗則為空字串。
    """
    tw_tz = pytz.timezone("Asia/Taipei")
    is_monday = datetime.now(tw_tz).weekday() == 0

    market_data = get_market_data()
    report_text, mdata = build_final_report(market_data)

    image_url = ""
    try:
        img_bytes = generate_news_image(report_text, mdata)
        image_url = upload_to_imgur(img_bytes)
    except Exception as e:
        print(f"Image generation error: {e}")

    weekly_calendar = ""
    if is_monday:
        try:
            print("Generating weekly economic calendar...")
            weekly_calendar = generate_weekly_economic_calendar()
            print("Weekly calendar done.")
        except Exception as e:
            print(f"Weekly calendar error: {e}")

    return report_text, image_url, weekly_calendar


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


def main():
    """排程自動執行用。"""
    report, image_url, weekly_calendar = generate_report()
    save_report_to_db(report)

    # 週一：先發本週經濟數據，再發日報
    if weekly_calendar:
        print("Sending weekly economic calendar...")
        send_line_message(weekly_calendar)

    print("Sending daily report to LINE...")
    send_line_message(report)

    if image_url:
        send_line_message(f"📊 今日市場摘要圖\n{image_url}")
        print(f"Market image sent: {image_url}")

    print("Daily report done!")


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
