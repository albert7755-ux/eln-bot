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
            "date": values[-1][0],
        }
    except Exception as e:
        print(f"[BondDaily] FRED {series_id} 抓取失敗: {e}")
        return None


JGB_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _parse_jgb_10y_csv(text: str):
    """從 MOF CSV 內容解析出 10 年期欄位的所有數值(自動偵測 10Y/10年 在第幾欄)"""
    rows = [r for r in csv.reader(io.StringIO(text)) if len(r) >= 11]
    col = 10  # 預設:日期(0), 1年(1)...10年(10)
    for r in rows:
        cells = [c.strip() for c in r]
        if "10Y" in cells:
            col = cells.index("10Y")
            break
        if "10年" in cells:
            col = cells.index("10年")
            break
    values = []
    for r in rows:
        if len(r) <= col:
            continue
        try:
            values.append(float(r[col].strip()))
        except ValueError:
            continue  # 跳過表頭、"-"、註解列
    return values


def get_jgb_10y_month():
    """
    近一個月日本10年期殖利率走勢:抓 MOF 當月 CSV 的全部日資料,
    不足 15 筆時(月初)再併上完整歷史檔補足。回傳最近約 22 筆 float。
    """
    urls = [
        ("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv", "utf-8"),
        ("https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv", "shift_jis"),
        ("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv", "utf-8"),
    ]
    series = []
    for url, enc in urls:
        try:
            resp = requests.get(url, headers=JGB_HEADERS, timeout=20)
            if resp.status_code != 200:
                continue
            vals = _parse_jgb_10y_csv(resp.content.decode(enc, errors="ignore"))
            if not vals:
                continue
            if not series:
                series = vals
            else:
                series = vals[-(30):] + series  # 歷史檔在前
            if len(series) >= 15:
                break
        except Exception as e:
            print(f"[BondDaily] JGB month {url} 失敗: {e}")
    return series[-22:] if series else []

def jgb_month_line(series):
    """把近一月序列壓成一行:月初/兩週前/一週前/最新 四個點＋總變化"""
    if len(series) < 6:
        return ""
    pts = [series[0], series[max(0, len(series)//2 - 1)], series[-6], series[-1]]
    chg_bp = (series[-1] - series[0]) * 100
    trail = " → ".join(f"{v:.2f}" for v in pts)
    return f"近一月走勢:{trail}%(約{chg_bp:+.0f}bp)"

def get_jgb_10y():
    """
    日本10年期公債殖利率(財務省官方資料),依序嘗試三個來源:
    1. 英文版當月 CSV(jgbcme.csv,注意檔名有個 e)
    2. 日文版當月 CSV(jgbcm.csv,Shift-JIS 編碼)
    3. 英文版完整歷史 CSV(每月1號當月檔只有一筆資料時的備援)
    """
    sources = [
        ("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv", "utf-8"),
        ("https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv", "shift_jis"),
        ("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv", "utf-8"),
    ]
    for url, enc in sources:
        fname = url.rsplit("/", 1)[-1]
        try:
            resp = requests.get(url, headers=JGB_HEADERS, timeout=20)
            if resp.status_code != 200:
                print(f"[BondDaily] MOF {fname} HTTP {resp.status_code},換下一個來源")
                continue
            values = _parse_jgb_10y_csv(resp.content.decode(enc, errors="ignore"))
            if len(values) >= 2:
                prev, last = values[-2], values[-1]
                return {
                    "price": round(last, 3),
                    "change": round(last - prev, 3),
                    "pct": 0.0,
                }
            print(f"[BondDaily] MOF {fname} 有效數值不足({len(values)}筆),換下一個來源")
        except Exception as e:
            print(f"[BondDaily] MOF {fname} 抓取失敗: {e}")
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
    # 利差專用:2/10/20/30 全部用 FRED 同一天的官方收盤,避免與 yfinance 混用造成日期錯位
    results["FRED10Y"] = get_fred_yield("DGS10")
    results["FRED30Y"] = get_fred_yield("DGS30")
    results["JGB10Y"] = get_jgb_10y()
    try:
        results["JGB10Y_MONTH"] = get_jgb_10y_month()
    except Exception as e:
        print(f"[BondDaily] JGB month fail: {e}")
        results["JGB10Y_MONTH"] = []
    return results


# ==============================
# 二、組版型
# ==============================

def updown_mark(value: float):
    return "🔺" if value >= 0 else "▼"


def _yield_line(label: str, d: dict) -> str:
    """殖利率專用格式:變化用 bp(基點)表示,同仁們比較好講"""
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

    # 利差計算:全部用 FRED(美國財政部官方收盤)同一天的數字,
    # 不與 yfinance 混算——之前 20Y(FRED,T-1) 配 30Y(yfinance,T) 日期錯位,利差會跟行情軟體對不起來
    d2, f10, d20, f30 = data.get("US2Y"), data.get("FRED10Y"), data.get("US20Y"), data.get("FRED30Y")
    spread_date = ""
    if d2 and f10 and d2.get("date") == f10.get("date"):
        spread_2s10s = (f10["price"] - d2["price"]) * 100
        lines.append(f"2年/10年利差:{spread_2s10s:+.0f}bp")
        spread_date = d2.get("date", "")
    if d20 and f30 and d20.get("date") == f30.get("date"):
        spread_20s30s = (f30["price"] - d20["price"]) * 100
        shape = "正斜率" if spread_20s30s > 0 else "倒掛(20Y高於30Y)"
        lines.append(f"20年/30年利差:{spread_20s30s:+.0f}bp({shape})")
        spread_date = d20.get("date", spread_date)
    note = "(*2年期與20年期為FRED資料,更新較慢一日"
    if spread_date:
        note += f";利差以FRED {spread_date} 同日收盤計算"
    lines.append(note + ")")

    lines.append("")
    lines.append("二、日債與匯率")
    lines.append(_yield_line("日本10年期公債", data.get("JGB10Y")))
    _jm = jgb_month_line(data.get("JGB10Y_MONTH") or [])
    if _jm:
        lines.append(_jm)
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
# 四、Claude 評論 + 每日輪替專題
# ==============================

def get_weekday_topic() -> str:
    """星期幾決定專題主題,一週輪一圈"""
    tw_tz = pytz.timezone("Asia/Taipei")
    weekday = datetime.now(tw_tz).weekday()
    topics = {
        0: "本週債市展望:本週有哪些重要經濟數據、央行事件、國債標售,對殖利率可能有什麼影響",
        1: "美債專題:美債供需、財政部發債、Fed 縮表或官員談話等結構性議題",
        2: "通膨專題:最新 CPI/PCE/薪資數據與通膨預期,對利率路徑的意義",
        3: "投資等級公司債專題:投資等級(IG)利差變化、指標性大型企業新發行與需求狀況、重要評等動態;我們的客戶持有的是投資等級債,非投資等級(高收益)市場只在影響IG時順帶一提即可",
        4: "各國央行貨幣政策專題:本週 Fed、ECB、日銀、英國央行等主要央行的決策、官員談話與市場定價變化,挑當週最有戲的央行來談",
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
        "你是銀行固定收益科的債券晨報編輯,讀者是分行的理財同仁,"
        "他們服務的高資產客戶持有海外債券(以投資等級債為主)、債券基金與結構型商品。\n\n"
        f"今天台北時間是 {today_str}。以下是昨晚(美國時間)收盤的債券市場數據:\n\n"
        f"{snapshot_text}\n\n"
        f"請上網搜尋 {today_str} 前後最新的債券與利率相關新聞(優先最近24小時),"
        "重點關注:美債殖利率變動原因、Fed 官員談話、通膨與就業數據、"
        "各國央行動向、公司債利差與新發行、重要國債標售結果。\n\n"
        "請完成以下段落:\n"
        "1.【前言】1-2句,點出昨晚債市最重要的主線。\n"
        "2.【殖利率動向解讀】2-3句,解釋美債各天期為什麼這樣動,"
        "務必區分短天期(反映Fed政策預期)與長天期(反映通膨與期限溢酬)的不同邏輯,"
        "不可把單一天期的變化泛化成整條曲線。"
        "另外請留意20年/30年利差:過去幾年20年期因供需因素長期高於30年期(曲線扭曲),"
        "若數據顯示20年已低於30年(正斜率),代表扭曲修復,值得一提;若利差有明顯變化也請說明。"
        "【極重要】描述漲跌與比較時,必須逐項核對上方表格的實際數字與箭頭(🔺=升、▼=降),"
        "先確認方向再下筆;與其寫「長端比短端如何」這種容易寫反的比較句,"
        "寧可直接引用數字,例如「10年升6bp、2年降5bp」。寫錯方向是嚴重錯誤。\n"
        f"3.【今日專題】用100-150字寫一則小專題,今天的主題是:{topic}。"
        "只挑1-2個最重要的事件講,寧短勿長,不要條列式流水帳。\n"
        "4.【今日操作思維】2-3句,寫給「我們」的觀察與提醒,不是判斷與指令。"
        "基調要正面、有建設性:同樣的市況,優先從「機會與可著力之處」的角度切入,"
        "例如殖利率處於高位代表新資金的進場收益率具吸引力、波動代表客戶更需要專業陪伴、"
        "事件前的觀望期正是盤點客戶配置與需求的好時機——把市況轉譯成我們今天「可以做什麼」,"
        "而不是渲染風險或潑冷水;若市場確實偏空,誠實陳述之餘仍要給一個正面的行動視角。"
        "每天換不同角度:具體數字鉤子、模擬客戶提問並給一個回答方向、歷史對比、或即將發生的事件,"
        "挑最適合今天新聞的一種。"
        "正面不等於樂觀喊多:對市場方向仍要保留不確定性,禁止「正是時機」「趨勢已確立」「必然」"
        "這類果決斷言,行情永遠可能反向,語氣要留餘地;避免固定句型,"
        "不要每天都用「值得留意」「建議關注」這類結尾;"
        "只能是市場觀察,不可以是投資建議或報酬保證,不要提及任何具體債券商品。\n\n"
        "要求:\n"
        "- 一定要具體,引用真實新聞事件,沒有事件就誠實說市場在等什麼。\n"
        "- 不要亂編新聞或數字。\n"
        "- 語氣專業但口語化,像晨會上自己人之間的分享。\n"
        "- 稱呼一律用「我們」(第一人稱複數,把作者和讀者放在同一邊),"
        "絕對不要出現「理專」「同仁們」「各位」這類把讀者隔開的稱呼。\n"
        "- 禁止空泛的呼籲句和集體喊話,例如「大家來想想」「不妨思考」「讓我們一起」「值得我們深思」;"
        "要嘛給具體的觀察或做法,要嘛不寫。\n"
        "- 純文字輸出,禁用任何markdown符號(**粗體**、#標題、-條列),LINE不支援會變亂碼。\n"
        "- 總長度精簡,適合手機閱讀。\n\n"
        "輸出格式必須完全如下:\n\n"
        "【前言】\n(內容)\n\n"
        "【殖利率動向解讀】\n(內容)\n\n"
        "【今日專題】\n(內容)\n\n"
        "【今日操作思維】\n(內容)\n"
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1600,
            temperature=0.3,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
    except Exception:
        # 萬一 web search 出問題,退回純文字模式,至少報告不會開天窗
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1600,
            temperature=0.3,
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
    topic = extract_section(commentary, "今日專題")
    action = extract_section(commentary, "今日操作思維")

    tw_tz = pytz.timezone("Asia/Taipei")
    weekday = datetime.now(tw_tz).weekday()
    topic_titles = {
        0: "本週債市展望", 1: "美債專題", 2: "通膨專題",
        3: "投資等級債專題", 4: "央行政策專題", 5: "本週債市回顧", 6: "本週債市回顧",
    }

    final_text = snapshot.replace(
        "__INTRO__",
        intro if intro else "昨晚債市持續消化利率與通膨訊號,殖利率變化詳見下表。"
    )

    final_text += "\n\n四、殖利率動向解讀\n"
    final_text += yields if yields else "美債殖利率變化反映市場對利率路徑的最新定價,建議留意後續數據。"

    final_text += f"\n\n五、{topic_titles[weekday]}\n"
    final_text += topic if topic else "(今日專題生成失敗,明日再會)"

    if action:
        final_text += "\n\n🧭 今日操作思維\n"
        final_text += action

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
