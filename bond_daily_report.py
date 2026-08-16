# bond_daily_report.py
# 債券市場日報 —— 獨立於 daily_report.py,專注固定收益
# 架構跟 daily_report.py 一樣:抓數據 → 組版型 → Claude 搜新聞寫評論 → 推播
# 使用的環境變數與 daily_report.py 完全相同,不需要新增任何設定

import os
import csv
import io
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
# 一、數據抓取
# ==============================

def _safe_close_pair(symbol: str):
    """抓最近兩個收盤價,回傳最新值、變化、變化%(跟 daily_report.py 同款)"""
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period="5d", auto_adjust=False)

    if hist is None or hist.empty:
        return None

    close = hist["Close"].dropna()
    if len(close) < 2:
        return None

    prev_close = float(close.iloc[-2])
    last_close = float(close.iloc[-1])
    change = last_close - prev_close
    pct = (change / prev_close) * 100 if prev_close else 0.0

    return {
        "price": round(last_close, 3),
        "change": round(change, 3),
        "pct": round(pct, 2),
    }


def get_fred_yield(series_id: str):
    """
    從 FRED 公開 CSV 抓殖利率(不用 API key)。
    yfinance 沒有 2年期(DGS2)與 20年期(DGS20)的指數代號,所以走 FRED。
    注意:FRED 數據會比市場晚一個交易日左右,僅供參考。
    """
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        rows = list(csv.reader(io.StringIO(resp.text)))
        # 取最後兩個有數值的日期(FRED 假日會填 ".")
        values = [(r[0], float(r[1])) for r in rows[1:] if len(r) >= 2 and r[1] not in (".", "")]
        if len(values) < 2:
            return None
        prev, last = values[-2][1], values[-1][1]
        return {
            "price": round(last, 3),
            "change": round(last - prev, 3),
            "pct": 0.0,
        }
    except Exception as e:
        print(f"[BondDaily] FRED {series_id} 抓取失敗: {e}")
        return None


def get_jgb_10y():
    """
    日本10年期公債殖利率:日本財務省(MOF)每天公佈官方 CSV,免費且穩定。
    CSV 是 Shift-JIS 編碼,欄位順序:日期,1年,2年,...,9年,10年,...
    """
    try:
        url = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcm.csv"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        text = resp.content.decode("shift_jis", errors="ignore")
        rows = [r for r in csv.reader(io.StringIO(text)) if len(r) >= 11]

        # 10年期在第 11 欄(index 10):日期(0), 1年(1)...10年(10)
        values = []
        for r in rows:
            v = r[10].strip()
            try:
                values.append(float(v))
            except ValueError:
                continue  # 跳過表頭或 "-" 的列

        if len(values) < 2:
            return None
        prev, last = values[-2], values[-1]
        return {
            "price": round(last, 3),
            "change": round(last - prev, 3),
            "pct": 0.0,
        }
    except Exception as e:
        print(f"[BondDaily] MOF 日債抓取失敗: {e}")
        return None


def get_bond_market_data():
    tickers = {
        "US3M": "^IRX",       # 美國3個月期
        "US5Y": "^FVX",       # 美國5年期
        "US10Y": "^TNX",      # 美國10年期
        "US30Y": "^TYX",      # 美國30年期
        "USDJPY": "JPY=X",    # 美元兌日圓
        "LQD": "LQD",         # 投資等級公司債 ETF(信用市場溫度計)
        "HYG": "HYG",         # 非投資等級債 ETF
        "TLT": "TLT",         # 20年期以上美債 ETF(長債價格方向)
    }

    results = {}
    for name, symbol in tickers.items():
        try:
            results[name] = _safe_close_pair(symbol)
        except Exception as e:
            results[name] = None
            print(f"[BondDaily] Error fetching {name} ({symbol}): {e}")

    results["US2Y"] = get_fred_yield("DGS2")
    results["US20Y"] = get_fred_yield("DGS20")
    results["JGB10Y"] = get_jgb_10y()
    return results


# ==============================
# 二、組版型
# ==============================

def updown_mark(value: float):
    return "🔺" if value >= 0 else "▼"


def _yield_line(label: str, d: dict) -> str:
    """殖利率專用格式:變化用 bp(基點)表示,理專比較好講"""
    if not d:
        return f"{label}:數據抓取失敗"
    arrow = updown_mark(d["change"])
    bp = abs(d["change"]) * 100  # 0.05% = 5 bp
    return f"{label}:{d['price']:.2f}% {arrow}{bp:.0f}bp"


def _etf_line(label: str, d: dict) -> str:
    if not d:
        return f"{label}:數據抓取失敗"
    arrow = updown_mark(d["change"])
    return f"{label}:{d['price']:.2f} {arrow}{abs(d['change']):.2f} ({d['pct']:+.2f}%)"


def build_bond_snapshot(data):
    tw_tz = pytz.timezone("Asia/Taipei")
    today = datetime.now(tw_tz)
    weekday_map = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

    lines = []
    lines.append(f"【{today.strftime('%Y年%m月%d日')}({weekday_map[today.weekday()]})債券市場日報】")
    lines.append("")
    lines.append("__INTRO__")
    lines.append("")
    lines.append("一、美債殖利率曲線")
    lines.append(_yield_line("3個月期", data.get("US3M")))
    lines.append(_yield_line("2年期*", data.get("US2Y")))
    lines.append(_yield_line("5年期", data.get("US5Y")))
    lines.append(_yield_line("10年期", data.get("US10Y")))
    lines.append(_yield_line("20年期*", data.get("US20Y")))
    lines.append(_yield_line("30年期", data.get("US30Y")))

    # 利差計算:2s10s 是市場最常講的曲線指標
    # 20s30s 則觀察 20 年券的「凸包」是否修復(過去幾年 20Y 長期高於 30Y,屬曲線扭曲)
    d2, d10, d20, d30 = data.get("US2Y"), data.get("US10Y"), data.get("US20Y"), data.get("US30Y")
    if d2 and d10:
        spread_2s10s = (d10["price"] - d2["price"]) * 100
        lines.append(f"2年/10年利差:{spread_2s10s:+.0f}bp")
    if d10 and d30:
        spread_10s30s = (d30["price"] - d10["price"]) * 100
        lines.append(f"10年/30年利差:{spread_10s30s:+.0f}bp")
    if d20 and d30:
        spread_20s30s = (d30["price"] - d20["price"]) * 100
        shape = "正斜率" if spread_20s30s > 0 else "倒掛(20Y高於30Y)"
        lines.append(f"20年/30年利差:{spread_20s30s:+.0f}bp({shape})")
    lines.append("(*2年期與20年期為FRED資料,更新較慢一日)")

    lines.append("")
    lines.append("二、日債與匯率")
    lines.append(_yield_line("日本10年期公債", data.get("JGB10Y")))
    d_jpy = data.get("USDJPY")
    if d_jpy:
        arrow = updown_mark(d_jpy["change"])
        lines.append(f"美元兌日圓:{d_jpy['price']:.2f} {arrow}{abs(d_jpy['change']):.2f}")
    else:
        lines.append("美元兌日圓:數據抓取失敗")

    lines.append("")
    lines.append("三、債券ETF與信用市場")
    lines.append(_etf_line("TLT 長天期美債", data.get("TLT")))
    lines.append(_etf_line("LQD 投資等級債", data.get("LQD")))
    lines.append(_etf_line("HYG 非投資等級債", data.get("HYG")))

    return "\n".join(lines)


# ==============================
# 三、Claude 評論 + 每日輪替專題
# ==============================

def get_weekday_topic() -> str:
    """星期幾決定專題主題,一週輪一圈"""
    tw_tz = pytz.timezone("Asia/Taipei")
    weekday = datetime.now(tw_tz).weekday()
    topics = {
        0: "本週債市展望:本週有哪些重要經濟數據、央行事件、國債標售,對殖利率可能有什麼影響",
        1: "美債專題:美債供需、財政部發債、Fed 縮表或官員談話等結構性議題",
        2: "通膨專題:最新 CPI/PCE/薪資數據與通膨預期,對利率路徑的意義",
        3: "信用債專題:投資等級與非投資等級債利差變化、大型新發行、值得注意的評等事件",
        4: "日債與日銀專題:日銀政策動向、日債殖利率變化、日圓走勢,以及對全球債市的外溢影響",
        5: "本週債市回顧:這一週殖利率與債市發生了什麼,一段話總結",
        6: "本週債市回顧:這一週殖利率與債市發生了什麼,一段話總結",
    }
    return topics[weekday]


def generate_bond_commentary(snapshot_text: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    tw_tz = pytz.timezone("Asia/Taipei")
    today_str = datetime.now(tw_tz).strftime("%Y年%m月%d日")
    topic = get_weekday_topic()

    prompt = (
        "你是銀行固定收益科的債券晨報編輯,讀者是分行理財專員,"
        "他們服務的高資產客戶持有海外債券、債券基金與結構型商品。\n\n"
        f"今天台北時間是 {today_str}。以下是昨晚(美國時間)收盤的債券市場數據:\n\n"
        f"{snapshot_text}\n\n"
        f"請上網搜尋 {today_str} 前後最新的債券與利率相關新聞(優先最近24小時),"
        "重點關注:美債殖利率變動原因、Fed 官員談話、通膨與就業數據、"
        "日銀與日債動向、公司債利差與新發行、重要國債標售結果。\n\n"
        "請完成以下段落:\n"
        "1.【前言】1-2句,點出昨晚債市最重要的主線。\n"
        "2.【殖利率動向解讀】2-3句,解釋美債各天期為什麼這樣動,"
        "務必區分短天期(反映Fed政策預期)與長天期(反映通膨與期限溢酬)的不同邏輯,"
        "不可把單一天期的變化泛化成整條曲線。"
        "另外請留意20年/30年利差:過去幾年20年期因供需因素長期高於30年期(曲線扭曲),"
        "若數據顯示20年已低於30年(正斜率),代表扭曲修復,值得一提;若利差有明顯變化也請說明。\n"
        "3.【日債觀察】1-2句,說明日債與日圓的最新動態;若沒有明確新聞,誠實寫目前市場關注焦點。\n"
        f"4.【今日專題】用150-250字寫一則小專題,今天的主題是:{topic}。"
        "要有具體事件或數據,不要空泛。\n"
        "5.【給理專的一句話】1句,把今天的資訊轉成理專跟客戶對話時可以用的觀察角度,"
        "只能是市場觀察,不可以是投資建議或報酬保證。\n\n"
        "要求:\n"
        "- 一定要具體,引用真實新聞事件,沒有事件就誠實說市場在等什麼。\n"
        "- 不要亂編新聞或數字。\n"
        "- 語氣專業但口語化,像晨會上講給理專聽。\n"
        "- 總長度精簡,適合手機閱讀。\n\n"
        "輸出格式必須完全如下:\n\n"
        "【前言】\n(內容)\n\n"
        "【殖利率動向解讀】\n(內容)\n\n"
        "【日債觀察】\n(內容)\n\n"
        "【今日專題】\n(內容)\n\n"
        "【給理專的一句話】\n(內容)\n"
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1600,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
    except Exception:
        # 萬一 web search 出問題,退回純文字模式,至少報告不會開天窗
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1600,
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


def build_final_bond_report(data: dict) -> str:
    snapshot = build_bond_snapshot(data)
    commentary = generate_bond_commentary(snapshot)

    intro = extract_section(commentary, "前言")
    yields = extract_section(commentary, "殖利率動向解讀")
    jgb = extract_section(commentary, "日債觀察")
    topic = extract_section(commentary, "今日專題")
    one_liner = extract_section(commentary, "給理專的一句話")

    tw_tz = pytz.timezone("Asia/Taipei")
    weekday = datetime.now(tw_tz).weekday()
    topic_titles = {
        0: "本週債市展望", 1: "美債專題", 2: "通膨專題",
        3: "信用債專題", 4: "日債與日銀專題", 5: "本週債市回顧", 6: "本週債市回顧",
    }

    final_text = snapshot.replace(
        "__INTRO__",
        intro if intro else "昨晚債市持續消化利率與通膨訊號,殖利率變化詳見下表。"
    )

    final_text += "\n\n四、殖利率動向解讀\n"
    final_text += yields if yields else "美債殖利率變化反映市場對利率路徑的最新定價,建議留意後續數據。"

    final_text += "\n\n五、日債觀察\n"
    final_text += jgb if jgb else "日債與日圓走勢持續受日銀政策預期影響,為觀察重點。"

    final_text += f"\n\n六、{topic_titles[weekday]}\n"
    final_text += topic if topic else "(今日專題生成失敗,明日再會)"

    if one_liner:
        final_text += "\n\n💬 給理專的一句話\n"
        final_text += one_liner

    return final_text.strip()


# ==============================
# 四、存檔與推播(跟 daily_report.py 同款)
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
            CREATE TABLE IF NOT EXISTS bond_daily_report_cache (
                id SERIAL PRIMARY KEY,
                report_text TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """))
            conn.execute(text("""
            INSERT INTO bond_daily_report_cache (report_text, created_at)
            VALUES (:r, NOW())
            """), {"r": report_text})
        print("[BondDaily] Report saved to DB")
    except Exception as e:
        print(f"[BondDaily] DB save failed: {e}")


def clean_line_text(text: str) -> str:
    import unicodedata
    cleaned = ""
    for ch in text:
        if ch == "\n" or ch == "\t":
            cleaned += ch
        elif len(ch) == 1 and unicodedata.category(ch).startswith("C"):
            continue
        else:
            cleaned += ch
    return cleaned


def send_line_message(text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    safe_text = clean_line_text(text[:4900])
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": safe_text}]
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        print("[BondDaily] LINE push success")
    else:
        print(f"[BondDaily] LINE push failed: {response.status_code} {response.text}")


# ==============================
# 主流程
# ==============================

def generate_report() -> str:
    market_data = get_bond_market_data()
    return build_final_bond_report(market_data)


def main():
    report = generate_report()
    save_report_to_db(report)
    print("[BondDaily] Sending bond daily report to LINE...")
    send_line_message(report)
    print("[BondDaily] Bond daily report done!")


if __name__ == "__main__":
    main()
