# bond_daily_report.py
# 債券市場日報 —— 獨立於 daily_report.py,專注固定收益
# 架構跟 daily_report.py 一樣:抓數據 → 組版型 → Claude 搜新聞寫評論 → 推播
# 使用的環境變數與 daily_report.py 完全相同,不需要新增任何設定

import os
import csv
import io
import re
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


TREASURY_COLS = {
    "US3M": ["3 Mo"], "US2Y": ["2 Yr"], "US5Y": ["5 Yr"],
    "US10Y": ["10 Yr"], "US20Y": ["20 Yr"], "US30Y": ["30 Yr"],
}

def get_treasury_curve():
    """
    美國財政部官方每日公債殖利率曲線(Par Yield Curve)。
    一次取得所有天期、同一個交易日,徹底解決混用 yfinance/FRED 造成的日期錯位。
    回傳 {"date":..., "prev_date":..., "US3M":{price,change,...}, ...} 或 None。
    """
    from datetime import datetime as _dt
    year = _dt.now().year
    urls = [
        ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
         f"daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&"
         f"field_tdr_date_value={year}&page&_format=csv"),
        ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
         f"daily-treasury-rates.csv/{year-1}/all?type=daily_treasury_yield_curve&"
         f"field_tdr_date_value={year-1}&page&_format=csv"),
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=JGB_HEADERS, timeout=20)
            resp.raise_for_status()
            rows = list(csv.reader(io.StringIO(resp.text)))
            if len(rows) < 3:
                continue
            header = [c.strip() for c in rows[0]]
            data_rows = [r for r in rows[1:] if r and r[0].strip()]
            if len(data_rows) < 2:
                continue
            # 財政部 CSV 為新到舊排列;第一列是最新交易日
            latest, prev = data_rows[0], data_rows[1]
            def pick(row, names):
                for nm in names:
                    if nm in header:
                        idx = header.index(nm)
                        try:
                            v = row[idx].strip()
                            if v not in ("", "N/A"):
                                return float(v)
                        except Exception:
                            pass
                return None
            out = {"date": latest[0].strip(), "prev_date": prev[0].strip()}
            got = 0
            for key, names in TREASURY_COLS.items():
                cur, pre = pick(latest, names), pick(prev, names)
                if cur is None:
                    out[key] = None
                    continue
                out[key] = {"price": round(cur, 3),
                            "change": round(cur - pre, 3) if pre is not None else 0.0,
                            "pct": 0.0, "date": out["date"]}
                got += 1
            if got >= 5:
                print(f"[BondDaily] Treasury curve {out['date']} (vs {out['prev_date']}) 取得 {got} 個天期")
                return out
        except Exception as e:
            print(f"[BondDaily] Treasury curve 抓取失敗: {e}")
    return None


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


def _parse_jgb_date(raw: str):
    """MOF 日期格式:2026/9/8、2026-09-08、R8.9.8(令和)"""
    from datetime import datetime as _dt, date as _date
    t = raw.strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return _dt.strptime(t, fmt).date()
        except ValueError:
            pass
    m = re.match(r"^[RＲ](\d+)[.．/](\d+)[.．/](\d+)$", t)
    if m:
        try:
            return _date(2018 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _parse_jgb_10y_series(text: str):
    """從 MOF CSV 解析 (日期, 10年期殖利率) 序列,依日期排序;日期解析失敗的列跳過"""
    rows = [r for r in csv.reader(io.StringIO(text)) if len(r) >= 11]
    col = 10
    for r in rows:
        cells = [c.strip() for c in r]
        if "10Y" in cells:
            col = cells.index("10Y"); break
        if "10年" in cells:
            col = cells.index("10年"); break
    series = []
    for r in rows:
        if len(r) <= col:
            continue
        d = _parse_jgb_date(r[0])
        if d is None:
            continue
        try:
            series.append((d, float(r[col].strip())))
        except ValueError:
            continue
    series.sort(key=lambda x: x[0])
    return series


def _parse_jgb_10y_csv(text: str):
    """相容舊呼叫:只回傳數值序列(已依日期排序)"""
    return [v for _, v in _parse_jgb_10y_series(text)]


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
            series = _parse_jgb_10y_series(resp.content.decode(enc, errors="ignore"))
            if len(series) >= 2:
                (d_prev, prev), (d_last, last) = series[-2], series[-1]
                print(f"[BondDaily] JGB 來源 {fname}: {d_prev} {prev} → {d_last} {last}")
                return {"price": round(last, 3), "change": round(last - prev, 3), "pct": 0.0,
                        "date": d_last, "source": "MOF"}
            print(f"[BondDaily] MOF {fname} 有效數值不足({len(series)}筆),換下一個來源")
        except Exception as e:
            print(f"[BondDaily] MOF {fname} 抓取失敗: {e}")
    return None


def get_jgb_10y_te():
    """備援/交叉比對:Trading Economics 頁面上的最新值與日變化"""
    try:
        resp = requests.get("https://tradingeconomics.com/japan/government-bond-yield",
                            headers=JGB_HEADERS, timeout=20)
        if resp.status_code != 200:
            return None
        html = resp.text
        m = re.search(r"(?:eased|fell|rose|climbed|increased|decreased|edged (?:up|down)) to ([\d.]+)% on ([A-Z][a-z]+ \d{1,2}, \d{4})", html)
        m2 = re.search(r"marking a ([\d.]+) percentage points (increase|decrease)", html)
        if not m:
            return None
        val = float(m.group(1))
        d = datetime.strptime(m.group(2), "%B %d, %Y").date()
        chg = float(m2.group(1)) * (1 if m2.group(2) == "increase" else -1) if m2 else 0.0
        print(f"[BondDaily] JGB TE: {d} {val} ({chg:+.3f})")
        return {"price": round(val, 3), "change": round(chg, 3), "pct": 0.0,
                "date": d, "source": "TradingEconomics"}
    except Exception as e:
        print(f"[BondDaily] JGB TE 失敗: {e}")
    return None


def get_jgb_10y_checked():
    """
    MOF 為主;若 MOF 日期落後預期交易日、或與 TE 同日差距 > 4bp,改用 TE 並註記。
    預期交易日 = 台北今天的前一個日本營業日(週末往前推)。
    """
    from datetime import timedelta as _td
    tw = pytz.timezone("Asia/Taipei")
    exp = datetime.now(tw).date() - _td(days=1)
    while exp.weekday() >= 5:
        exp -= _td(days=1)
    mof = get_jgb_10y()
    te = get_jgb_10y_te()
    if mof and mof.get("date") == exp:
        if te and te.get("date") == exp and abs(te["price"] - mof["price"]) > 0.04:
            print(f"[BondDaily] JGB MOF({mof['price']}) 與 TE({te['price']}) 差距逾4bp,改用 TE")
            return te
        return mof
    if te and te.get("date") == exp:
        print(f"[BondDaily] JGB MOF 日期 {mof.get('date') if mof else None} ≠ 預期 {exp},改用 TE")
        return te
    pick = mof or te
    return dict(pick, stale=True) if pick else None


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

    # ── 殖利率曲線:優先用美國財政部官方每日曲線(所有天期同一交易日)──
    curve = get_treasury_curve()
    results["CURVE_SOURCE"] = None
    if curve:
        for key in TREASURY_COLS:
            if curve.get(key):
                results[key] = curve[key]          # 覆蓋 yfinance 的同名天期
        results["CURVE_SOURCE"] = {"name": "美國財政部", "date": curve["date"]}
    else:
        # 備援:維持原本混用來源(2Y/20Y 走 FRED),並標示資料來源不一致
        print("[BondDaily] 財政部曲線不可用,改用 yfinance + FRED 備援")
        results["US2Y"] = get_fred_yield("DGS2")
        results["US20Y"] = get_fred_yield("DGS20")
        results["FRED10Y"] = get_fred_yield("DGS10")
        results["FRED30Y"] = get_fred_yield("DGS30")
    results["JGB10Y"] = get_jgb_10y_checked()
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
    """
    殖利率格式:絕對變化用 bp,另附相對變化率(%),
    讓「降6bp」有比較基準——同樣 6bp 對 3個月期與 30年期的意義差很多。
    """
    if not d:
        return f"{label}:數據抓取失敗"
    arrow = updown_mark(d["change"])
    bp = abs(d["change"]) * 100  # 0.05% = 5 bp
    prev = d["price"] - d["change"]
    pct = (d["change"] / prev * 100) if prev else 0.0
    return f"{label}:{d['price']:.2f}% {arrow}{bp:.0f}bp ({pct:+.2f}%)"


def _etf_line(label: str, d: dict) -> str:
    if not d:
        return f"{label}:數據抓取失敗"
    arrow = updown_mark(d["change"])
    return f"{label}:{d['price']:.2f} {arrow}{abs(d['change']):.2f} ({d['pct']:+.2f}%)"


def _last_us_trading_day(today_tw):
    """台北的今天,對應『昨晚應該有的美國交易日』= 台北日期減一天;週日/週一往前回推到週五"""
    from datetime import timedelta as _td
    d = today_tw.date() - _td(days=1)
    while d.weekday() >= 5:        # 週六/週日
        d -= _td(days=1)
    return d


def detect_us_closed(data, today_tw):
    """
    比對財政部曲線日期與『昨晚應有的交易日』,不一致代表美債昨晚休市(假日)。
    回傳 (is_closed, expected_date, actual_date) ; actual_date 可能為 None
    """
    from datetime import datetime as _dt
    src = data.get("CURVE_SOURCE") or {}
    raw = src.get("date")
    actual = None
    if raw:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
            try:
                actual = _dt.strptime(str(raw).strip(), fmt).date(); break
            except Exception:
                continue
    expected = _last_us_trading_day(today_tw)
    if actual is None:
        return False, expected, None
    return actual < expected, expected, actual


def build_bond_snapshot(data):
    tw_tz = pytz.timezone("Asia/Taipei")
    today = datetime.now(tw_tz)
    weekday_map = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

    lines = []
    lines.append(f"【{today.strftime('%Y年%m月%d日')}({weekday_map[today.weekday()]})債券市場日報】")
    lines.append("")
    lines.append("__INTRO__")
    lines.append("")
    closed, exp_d, act_d = detect_us_closed(data, today)
    if closed and act_d:
        wdm = ["一", "二", "三", "四", "五", "六", "日"]
        lines.append(f"⚠️ 美債昨日（{exp_d:%m/%d} 週{wdm[exp_d.weekday()]}）休市，"
                     f"以下為 {act_d:%m/%d}（週{wdm[act_d.weekday()]}）收盤數據，變動為該日對前一交易日。")
        lines.append("")
    src = data.get("CURVE_SOURCE")
    star = "" if src else "*"          # 資料源統一時不需要星號註記
    lines.append("一、美債殖利率曲線")
    lines.append(_yield_line("3個月期", data.get("US3M")))
    lines.append(_yield_line(f"2年期{star}", data.get("US2Y")))
    lines.append(_yield_line("5年期", data.get("US5Y")))
    lines.append(_yield_line("10年期", data.get("US10Y")))
    lines.append(_yield_line(f"20年期{star}", data.get("US20Y")))
    lines.append(_yield_line("30年期", data.get("US30Y")))

    # 利差計算:直接用上方顯示的同一組數字,確保與表格一致
    d2, d10, d20, d30 = (data.get("US2Y"), data.get("US10Y"),
                         data.get("US20Y"), data.get("US30Y"))
    if src:
        # 資料源統一(財政部官方曲線,所有天期同一交易日)
        if d2 and d10:
            lines.append(f"2年/10年利差:{(d10['price'] - d2['price']) * 100:+.0f}bp")
        if d20 and d30:
            sp = (d30["price"] - d20["price"]) * 100
            shape = "正斜率" if sp > 0 else "倒掛(20Y高於30Y)"
            lines.append(f"20年/30年利差:{sp:+.0f}bp({shape})")
        lines.append(f"(全部天期同為{src['name']} {src['date']} 收盤)")
    else:
        # 備援模式:2Y/20Y 走 FRED(較慢一日),僅在日期相同時才計算利差
        f10, f30 = data.get("FRED10Y"), data.get("FRED30Y")
        spread_date = ""
        if d2 and f10 and d2.get("date") == f10.get("date"):
            lines.append(f"2年/10年利差:{(f10['price'] - d2['price']) * 100:+.0f}bp")
            spread_date = d2.get("date", "")
        if d20 and f30 and d20.get("date") == f30.get("date"):
            sp = (f30["price"] - d20["price"]) * 100
            shape = "正斜率" if sp > 0 else "倒掛(20Y高於30Y)"
            lines.append(f"20年/30年利差:{sp:+.0f}bp({shape})")
            spread_date = d20.get("date", spread_date)
        note = "(*2年期與20年期為FRED資料,更新較慢一日"
        if spread_date:
            note += f";利差以FRED {spread_date} 同日收盤計算"
        lines.append(note + ")")

    lines.append("")
    lines.append("二、日債與匯率")
    _j = data.get("JGB10Y")
    _jl = _yield_line("日本10年期公債", _j)
    if _j and _j.get("date"):
        _jl += f"（{_j['date']:%m/%d}"
        if _j.get("source") == "TradingEconomics":
            _jl += "·TE"
        if _j.get("stale"):
            _jl += "·資料未更新"
        _jl += "）"
    lines.append(_jl)
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
    focus, focus_risk = get_daily_focus_full()
    if focus:
        print(f"[BondDaily] 當期主打方向: {focus}｜必講風險: {focus_risk or '(未設定)'}")

    prompt = (
        "你是銀行固定收益科的債券晨報編輯,讀者是分行的理財同仁,"
        "他們服務的高資產客戶持有海外債券(以投資等級債為主)、債券基金與結構型商品。\n\n"
        f"今天台北時間是 {today_str}。以下是最新一個美國交易日收盤的債券市場數據:\n\n"
        f"{snapshot_text}\n\n"
        + ("【極重要-休市】上方數據標示美債昨日休市(美國假日)。昨晚『沒有交易』,"
           "所以不可以寫『昨晚殖利率走升/回落』『假期後首個交易日』這類描述,"
           "也不要把數據的變動說成昨晚發生的事。前言與殖利率解讀請改寫成:"
           "說明昨日休市、上一交易日的收盤水位、以及今晚開盤市場將面對的事件(數據/會議)。\n\n"
           if "美債昨日" in snapshot_text and "休市" in snapshot_text else "") +
        "【極重要-利差方向】上方數據中的『2年/10年利差』與『20年/30年利差』已由系統計算完成,"
        "括號內若標示『正斜率』代表 30年殖利率高於 20年(曲線扭曲已修復);"
        "若標示『倒掛(20Y高於30Y)』代表 20年高於 30年(扭曲尚未修復)。"
        "你在文字中描述這兩組利差時,必須完全依照上方括號內的標示,"
        "嚴禁自行推算或寫出與其相反的方向,也不要重新計算數值。\n\n"
        f"請上網搜尋 {today_str} 前後最新的債券與利率相關新聞。\n"
        "【搜尋重點順序】\n"
        "1. 最優先:昨晚(美股交易時段)殖利率變動的『直接觸發事件』——例如油價大幅變動、地緣政治進展、"
        "當日公布的經濟數據、Fed 官員突發談話、國債標售結果。這類事件通常只在 24 小時內的新聞裡,"
        "請務必找出來,不要用『財政部回購計畫』這類持續數日的結構性題材充當當日觸發原因。\n"
        "2. 其次:結構性與政策面因素(財政赤字、回購操作、供需、通膨趨勢),作為背景補充。\n"
        "3. 也留意:各國央行動向、投資等級公司債利差與新發行。\n\n"
        "請完成以下段落:\n"
        "1.【前言】1句,點出昨晚債市最重要的主線,不要鋪陳。\n"
        "2.【殖利率動向解讀】2-3句(上限120字)。第一句必須回答『昨晚殖利率變動最直接的觸發事件是什麼』"
        "(引用具體新聞與數字,例如油價跌幅、數據結果、官員談話內容);若確實找不到明確觸發事件,"
        "就誠實說明市場在等待什麼,不要拿結構性題材硬湊。接著解釋美債各天期為什麼這樣動,"
        "務必區分短天期(反映Fed政策預期)與長天期(反映通膨與期限溢酬)的不同邏輯,"
        "不可把單一天期的變化泛化成整條曲線。"
        "關於20年/30年利差:只有在『當日利差方向發生翻轉』(由倒掛轉正斜率或反之)、"
        "或『單日變動達3bp以上』時才需要提及並說明意義;"
        "若只是延續前一日的既有狀態(例如已維持正斜率多日),一律不要提,"
        "更不要重複「曲線扭曲已修復、長端結構趨於正常」這類每天都成立的敘述——"
        "日報只寫當天的新資訊,不寫已成為背景的舊結論。"
        "【極重要】描述漲跌與比較時,必須逐項核對上方表格的實際數字與箭頭(🔺=升、▼=降),"
        "先確認方向再下筆;與其寫「長端比短端如何」這種容易寫反的比較句,"
        "寧可直接引用數字,例如「10年升6bp、2年降5bp」。寫錯方向是嚴重錯誤。\n"
        f"3.【今日專題】用70-100字寫一則小專題,今天的主題是:{topic}。"
        "只挑1個最重要的事件講,寧短勿長,不要重複前面已寫過的內容。\n"
        "4.【今日操作思維】2-3句(上限150字),寫給「我們」的觀察與提醒,不是判斷與指令。\n"
        "  語氣要求:口語、短句、像在群組裡跟同事講話,不要教科書腔。"
        "【嚴格禁止】不要用問句開場、不要代替客戶提問、不要出現"
        "「客戶最近常問」「客戶可能會問」「如果客戶問…」「有人會問」這類寫法,"
        "也不要在文中拋出反問句。直接把觀點寫出來就好,"
        "例如「殖利率逼近5%,新資金的票息保護比過去幾年厚實得多」這種直述句。"
        "不要浮誇、不要emoji、不要驚嘆號。\n"
        "基調要正面、有建設性:同樣的市況,優先從「機會與可著力之處」的角度切入,"
        "例如殖利率處於高位代表新資金的進場收益率具吸引力、波動代表客戶更需要專業陪伴、"
        "事件前的觀望期正是盤點客戶配置與需求的好時機——把市況轉譯成我們今天「可以做什麼」,"
        "而不是渲染風險或潑冷水;若市場確實偏空,誠實陳述之餘仍要給一個正面的行動視角。"
        "每天換不同角度,避免固定套路:具體數字的意義、歷史水準對比、"
        "即將發生的事件與其影響、市場定價與基本面的落差、配置面的可著力之處,"
        "挑最適合今天新聞的一種,用直述句寫出來。"
        "正面不等於樂觀喊多:對市場方向仍要保留不確定性,禁止「正是時機」「趨勢已確立」「必然」"
        "這類果決斷言,行情永遠可能反向,語氣要留餘地;避免固定句型,"
        "不要每天都用「值得留意」「建議關注」這類結尾;"
        "只能是市場觀察,不可以是投資建議或報酬保證。\n"
        + ("【當期主打方向】總行目前主推的產品方向是:" + focus + "。"
           "請在【今日操作思維】最後,用1句話把當天市場狀況自然連結到這個方向,"
           "說明它在目前環境下的意義。"
           "寫法:直接陳述這個結構在當下市況的邏輯(例如票息怎麼變、時間怎麼發揮作用),"
           "不要用「客戶會問…」「對於擔心…的客戶」這種假設客戶提問的句式。"
           "務必遵守:只講產品『類型與結構』的邏輯,絕對不要提到具體債券名稱、代碼、票息數字或價格;"
           "不要用『推薦』『建議買進』『最佳時機』等勸誘字眼,語氣是提供一個討論角度。\n"
           + ("【必講風險】提到上述產品方向時,必須在同一段內一併點出下列風險,不可省略、不可淡化,"
              "用自然的句子帶出而非條列:" + focus_risk + "\n\n" if focus_risk else "\n")
           if focus else "不要提及任何具體債券商品。\n\n") +
        "要求:\n"
        "- 一定要具體,引用真實新聞事件,沒有事件就誠實說市場在等什麼。\n"
        "- 不要亂編新聞或數字。\n"
        "- 禁止套語:不要每天重複同樣的結論句。若某個結構性現象前幾天已經說明過且今天沒有新變化,"
        "就略過不寫,把篇幅留給當天真正的新資訊。\n"
        "- 語氣專業但口語化,像晨會上自己人之間的分享;句子短一點,少用文言與冗長的形容。\n"
        "- 稱呼一律用「我們」(第一人稱複數,把作者和讀者放在同一邊),"
        "絕對不要出現「理專」「同仁們」「各位」這類把讀者隔開的稱呼。\n"
        "- 禁止空泛的呼籲句和集體喊話,例如「大家來想想」「不妨思考」「讓我們一起」「值得我們深思」;"
        "要嘛給具體的觀察或做法,要嘛不寫。\n"
        "- 純文字輸出,禁用任何markdown符號(**粗體**、#標題、-條列),LINE不支援會變亂碼。\n"
        "- 總長度精簡,適合手機閱讀:四段文字合計約350~480字,寧可少寫也不要湊字數,但每段不可空白。\n\n"
        "輸出格式必須完全如下:\n\n"
        "【前言】\n(內容)\n\n"
        "【殖利率動向解讀】\n(內容)\n\n"
        "【今日專題】\n(內容)\n\n"
        "【今日操作思維】\n(內容)\n"
        "格式硬性規定:四個標籤各自獨立成一行、標籤後換行再寫內容、每段都要有內容;"
        "不要粗體、不要 markdown、不要分隔線、不要把兩個標籤寫在同一行、不要改標籤名稱。\n"
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


SECTION_ALIASES = {
    "前言": ["前言", "開場", "摘要"],
    "殖利率動向解讀": ["殖利率動向解讀", "殖利率解讀", "動向解讀", "殖利率動向"],
    "今日專題": ["今日專題", "專題", "主題"],
    "今日操作思維": ["今日操作思維", "操作思維", "操作建議", "今日思維"],
}


def _normalize_commentary(text: str) -> str:
    """去掉 markdown 裝飾與分隔線,讓標籤解析不受格式漂移影響"""
    import re
    t = text.replace("\r", "")
    t = re.sub(r"\*\*|__|`", "", t)                       # 粗體/底線/程式碼記號
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.M)           # markdown 標題
    t = re.sub(r"^\s*[-=─—]{3,}\s*$", "", t, flags=re.M)  # 分隔線
    t = re.sub(r"[［\[]", "【", t); t = re.sub(r"[］\]]", "】", t)  # 全形/半形方括號視為同一種
    return t


def parse_sections(text: str) -> dict:
    """
    容錯解析:標籤不必在行首、可帶冒號、可用別名。
    回傳 {canonical_title: content}
    """
    import re
    t = _normalize_commentary(text or "")
    alias_to_canon = {a: c for c, al in SECTION_ALIASES.items() for a in al}
    pat = re.compile(r"【\s*(" + "|".join(map(re.escape, alias_to_canon.keys())) + r")\s*】\s*[:：]?\s*")
    hits = list(pat.finditer(t))
    out = {}
    for i, m in enumerate(hits):
        canon = alias_to_canon[m.group(1)]
        end = hits[i + 1].start() if i + 1 < len(hits) else len(t)
        body = t[m.end():end].strip()
        if body and canon not in out:            # 同名標籤只取第一個有內容的
            out[canon] = body
    return out


def extract_section(text: str, title: str) -> str:
    return parse_sections(text).get(title, "")


def build_final_bond_report(data: dict) -> str:
    snapshot = build_bond_snapshot(data)
    commentary = generate_bond_commentary(snapshot)

    secs = parse_sections(commentary)
    need = ("前言", "殖利率動向解讀", "今日專題", "今日操作思維")
    missing = [k for k in need if not secs.get(k)]
    if missing:
        print(f"[BondDaily] 段落解析缺少 {missing},重試一次。原始輸出前400字:\n{(commentary or '')[:400]}")
        try:
            commentary2 = generate_bond_commentary(
                snapshot + "\n\n【格式再次提醒】四個段落標題必須各自獨立成一行、只用這四個標籤:"
                           "【前言】【殖利率動向解讀】【今日專題】【今日操作思維】,"
                           "標籤後換行再寫內容,不要粗體、不要分隔線、不要把兩個標籤寫在同一行。")
            secs2 = parse_sections(commentary2)
            if sum(1 for k in need if secs2.get(k)) > sum(1 for k in need if secs.get(k)):
                secs = secs2
        except Exception as e:
            print(f"[BondDaily] 重試失敗: {e}")
    intro = secs.get("前言", "")
    yields = secs.get("殖利率動向解讀", "")
    topic = secs.get("今日專題", "")
    action = secs.get("今日操作思維", "")

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


FOCUS_PATHS = ["/data/bond_focus.json", "/tmp/bond_focus.json"]

def get_daily_focus():
    """讀取當期主打方向(由 /bonddaily focus 設定);沒設定回空字串"""
    return get_daily_focus_full()[0]


def get_daily_focus_full():
    """回傳 (主打方向, 必講風險);皆為字串,未設定回 ('', '')"""
    import json as _json
    for path in FOCUS_PATHS:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    d = _json.load(f)
                return (str(d.get("focus") or "").strip(),
                        str(d.get("risk") or "").strip())
        except Exception as e:
            print(f"[BondDaily] read focus {path}: {e}")
    return "", ""


def get_push_targets():
    """
    推播對象:Albert 個人 + 海外債主群(/coupon settarget 設定的那個群)。
    群組設定存在 targets.json,與配息雷達共用同一份名單。
    """
    targets = []
    if LINE_USER_ID:
        targets.append(LINE_USER_ID)
    # 找 targets.json(優先持久磁碟 /data,再退回 /tmp)
    import json as _json
    for path in ("/data/targets.json", "/tmp/targets.json"):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                gid = data.get("bond", "")
                if gid and gid not in targets:
                    targets.append(gid)
                break
        except Exception as e:
            print(f"[BondDaily] read targets {path}: {e}")
    return targets


def send_line_message(text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    safe_text = clean_line_text(text[:4900])
    targets = get_push_targets()
    if not targets:
        print("[BondDaily] 無推播對象")
        return
    for to in targets:
        payload = {"to": to, "messages": [{"type": "text", "text": safe_text}]}
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            print(f"[BondDaily] LINE push success -> {to[:8]}...")
        else:
            print(f"[BondDaily] LINE push failed -> {to[:8]}...: {response.status_code} {response.text}")


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
