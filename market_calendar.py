# -*- coding: utf-8 -*-
"""
market_calendar.py — 台灣＋各計價幣別國家的「休市日」行事曆
============================================================
為什麼需要這支：
    原本 bond_coupon_alert.py 算營業日只跳過週六日，
    遇到像 2026/9/25 中秋節、9/28 教師節這種連假就會算錯申購截止日。

投資服務部 2026/9 來函的規則（本模組即照此實作）：
    1. 交割日 = 交易日往後數 N 個「台灣營業日」
    2. 「調整後交割日若逢幣別國家休市，將再順延交割日」
       → 實務上等同：只數「台灣開市 AND 計價幣別國家開市」的日子

    例：2026/9/24(四) 下單、USD、T+1
        9/25(五) 中秋  → 台灣休 → 不算
        9/26(六) 9/27(日)        → 不算
        9/28(一) 教師節 → 台灣休 → 不算
        9/29(二) 台美皆開市      → 第 1 個營業日 → 交割日 = 9/29  ✔

對外主要函式：
    is_biz_day(d, ccy)          這天是不是營業日
    biz_days_after(d, n, ccy)   往後推 n 個營業日
    biz_days_before(d, n, ccy)  往前推 n 個營業日
    settlement_date(trade, lag, ccy)      交易日 → 交割日
    last_trade_date(deadline, lag, ccy)   交割截止日 → 最晚下單日
    holidays_in_range(a, b, ccy)          區間內的休市日（給提醒用）
    coverage_ok(d, ccy)                   這一年有沒有假日資料（沒有就只排除週末）

維護方式（兩種，擇一）：
    A. 直接改本檔 _TW / _MKT 的字典（要重新部署）
    B. 在持久磁碟放 market_holidays.json（不用部署，/holiday 指令可線上加）
       格式：{"TW": {"2027-01-01": "元旦", ...}, "USD": {...}}
"""
from datetime import date, datetime, timedelta
from pathlib import Path
import json

# ============================================================
# 內建假日表
#   key = 年份；value = {日期字串: 名稱}
#   只放「有資料、已查證」的年份；沒放的年份 coverage_ok() 會回 False，
#   訊息會標註「未含假日校正」，不會安靜地算錯。
# ============================================================

# ---------- 台灣（行政院人事行政總處 115 年辦公日曆表）----------
_TW = {
    2026: {
        "2026-01-01": "元旦",
        "2026-02-16": "除夕",
        "2026-02-17": "春節初一",
        "2026-02-18": "春節初二",
        "2026-02-19": "春節初三",
        "2026-02-20": "小年夜補假",
        "2026-02-27": "和平紀念日調整放假",
        "2026-04-03": "兒童節補假",
        "2026-04-06": "民族掃墓節補假",
        "2026-05-01": "勞動節",
        "2026-06-19": "端午節",
        "2026-09-25": "中秋節",
        "2026-09-28": "教師節",
        "2026-10-09": "國慶日補假",
        "2026-10-26": "臺灣光復節補假",
        "2026-12-25": "行憲紀念日",
    },
    2027: {
        # 2027（116 年）辦公日曆表尚未查證，先留空。
        # 留空 = coverage_ok() 回 False = 訊息會標註未校正，不會假裝算對。
    },
}

# ---------- 美元：SIFMA 美國債市建議全日休市 ----------
_US = {
    2026: {
        "2026-01-01": "New Year's Day",
        "2026-01-19": "Martin Luther King Jr. Day",
        "2026-02-16": "Presidents Day",
        "2026-04-03": "Good Friday",
        "2026-05-25": "Memorial Day",
        "2026-06-19": "Juneteenth",
        "2026-07-03": "Independence Day (observed)",
        "2026-09-07": "Labor Day",
        "2026-10-12": "Columbus Day",
        "2026-11-11": "Veterans Day",
        "2026-11-26": "Thanksgiving Day",
        "2026-12-25": "Christmas Day",
    },
    2027: {
        "2027-01-01": "New Year's Day",
        "2027-01-18": "Martin Luther King Jr. Day",
        "2027-02-15": "Presidents Day",
        "2027-03-26": "Good Friday",
        "2027-05-31": "Memorial Day",
        "2027-06-18": "Juneteenth (observed)",
        "2027-07-05": "Independence Day (observed)",
        "2027-09-06": "Labor Day",
        "2027-10-11": "Columbus Day",
        "2027-11-11": "Veterans Day",
        "2027-11-25": "Thanksgiving Day",
        "2027-12-24": "Christmas Day (observed)",
    },
}

# ---------- 英鎊：SIFMA 英國債市建議全日休市 ----------
_GB = {
    2026: {
        "2026-01-01": "New Year's Day",
        "2026-04-03": "Good Friday",
        "2026-04-06": "Easter Monday",
        "2026-05-04": "Early May Bank Holiday",
        "2026-05-25": "Spring Bank Holiday",
        "2026-08-31": "Summer Bank Holiday",
        "2026-12-25": "Christmas Day",
        "2026-12-28": "Boxing Day (substitute)",
    },
}

# ---------- 日圓：SIFMA 日本債市建議全日休市 ----------
_JP = {
    2026: {
        "2026-01-01": "元日",
        "2026-01-02": "銀行休業日",
        "2026-01-12": "成人の日",
        "2026-02-11": "建国記念の日",
        "2026-02-23": "天皇誕生日",
        "2026-03-20": "春分の日",
        "2026-04-29": "昭和の日",
        "2026-05-05": "こどもの日",
        "2026-05-06": "憲法記念日(振替)",
        "2026-07-20": "海の日",
        "2026-08-11": "山の日",
        "2026-09-21": "敬老の日",
        "2026-09-22": "国民の休日",
        "2026-09-23": "秋分の日",
        "2026-11-03": "文化の日",
        "2026-11-23": "勤労感謝の日",
        "2026-12-31": "銀行休業日",
    },
}

# ---------- 歐元：TARGET2 ----------
_EU = {
    2026: {
        "2026-01-01": "New Year's Day",
        "2026-04-03": "Good Friday",
        "2026-04-06": "Easter Monday",
        "2026-05-01": "Labour Day",
        "2026-12-25": "Christmas Day",
        "2026-12-26": "St Stephen's Day",
    },
}

# ---------- 澳幣：雪梨 ----------
_AU = {
    2026: {
        "2026-01-01": "New Year's Day",
        "2026-01-26": "Australia Day",
        "2026-04-03": "Good Friday",
        "2026-04-06": "Easter Monday",
        "2026-04-27": "Anzac Day (observed)",
        "2026-06-08": "King's Birthday",
        "2026-12-25": "Christmas Day",
        "2026-12-28": "Boxing Day (observed)",
    },
}

# ---------- 加幣：多倫多 ----------
_CA = {
    2026: {
        "2026-01-01": "New Year's Day",
        "2026-02-16": "Family Day",
        "2026-04-03": "Good Friday",
        "2026-05-18": "Victoria Day",
        "2026-07-01": "Canada Day",
        "2026-08-03": "Civic Holiday",
        "2026-09-07": "Labour Day",
        "2026-10-12": "Thanksgiving",
        "2026-11-11": "Remembrance Day",
        "2026-12-25": "Christmas Day",
        "2026-12-28": "Boxing Day (observed)",
    },
}

# 幣別 → 該國市場假日表
_MKT = {
    "USD": _US,
    "GBP": _GB,
    "JPY": _JP,
    "EUR": _EU,
    "AUD": _AU,
    "CAD": _CA,
}

# 還沒建表的幣別（ZAR / CNY / CNH / NZD / HKD / SGD…）→ 只排除週末，
# 並由 coverage_ok() 標註未校正。
UNCOVERED_HINT = "（此幣別尚未建當地假日表，僅排除台灣假日與週末）"


# ============================================================
# 外部覆寫檔（持久磁碟）
# ============================================================
def _override_path():
    for d in (Path("/data"), Path("/tmp")):
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d / "market_holidays.json"
        except Exception:
            continue
    return Path("/tmp/market_holidays.json")


_OVERRIDE_CACHE = {"mtime": None, "data": {}}


def _load_override():
    """讀持久磁碟上的補充假日；檔案沒變就用快取。"""
    p = _override_path()
    try:
        m = p.stat().st_mtime
    except Exception:
        _OVERRIDE_CACHE["mtime"], _OVERRIDE_CACHE["data"] = None, {}
        return {}
    if _OVERRIDE_CACHE["mtime"] == m:
        return _OVERRIDE_CACHE["data"]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _OVERRIDE_CACHE["mtime"], _OVERRIDE_CACHE["data"] = m, data
    return data


def add_holiday(market, d, name=""):
    """線上新增一個休市日（寫進持久磁碟，重開機不會掉）。market: 'TW' 或幣別。"""
    p = _override_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    mk = str(market).upper()
    data.setdefault(mk, {})[d.isoformat()] = name or "自訂休市日"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _OVERRIDE_CACHE["mtime"] = None      # 讓下次重讀
    return p


def remove_holiday(market, d):
    """移除線上新增的休市日（只能刪 json 裡的，刪不掉內建表）。"""
    p = _override_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    mk = str(market).upper()
    if data.get(mk, {}).pop(d.isoformat(), None) is None:
        return False
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _OVERRIDE_CACHE["mtime"] = None
    return True


# ============================================================
# 查詢
# ============================================================
def _norm_ccy(ccy):
    c = str(ccy or "").strip().upper()
    if c in ("CNH", "RMB"):
        c = "CNY"
    return c


def _table(market):
    """回傳 {日期字串: 名稱}（內建表 + 覆寫檔合併）。market: 'TW' 或幣別。"""
    mk = str(market).upper()
    built = _TW if mk == "TW" else _MKT.get(mk, {})
    out = {}
    for _yr, days in built.items():
        out.update(days)
    out.update(_load_override().get(mk, {}))
    return out


def _covered_years(market):
    mk = str(market).upper()
    built = _TW if mk == "TW" else _MKT.get(mk, {})
    yrs = {y for y, days in built.items() if days}
    for k in _load_override().get(mk, {}):
        try:
            yrs.add(int(k[:4]))
        except Exception:
            pass
    return yrs


def holiday_name(d, market):
    """這天在該市場是不是休市日；是就回名稱，否則 None。"""
    return _table(market).get(d.isoformat())


def coverage_ok(d, ccy=None):
    """
    這個日期的年份有沒有假日資料。
    回傳 (ok, 缺的市場清單)。ok=False 代表結果只排除了週末，要在訊息上標註。
    """
    missing = []
    if d.year not in _covered_years("TW"):
        missing.append("台灣")
    c = _norm_ccy(ccy)
    if c:
        if c not in _MKT and c not in _load_override():
            missing.append(c)
        elif d.year not in _covered_years(c):
            missing.append(c)
    return (not missing), missing


def is_biz_day(d, ccy=None):
    """
    營業日 = 非週末 AND 台灣沒休市 AND（有給幣別時）該幣別國家沒休市。
    這就是來函「交割日若逢幣別國家休市將再順延」的等價寫法。
    """
    if d.weekday() >= 5:
        return False
    if holiday_name(d, "TW"):
        return False
    c = _norm_ccy(ccy)
    if c and holiday_name(d, c):
        return False
    return True


def roll_forward(d, ccy=None):
    """d 若不是營業日就往後順延到第一個營業日。"""
    cur = d
    for _ in range(40):
        if is_biz_day(cur, ccy):
            return cur
        cur += timedelta(days=1)
    return cur


def roll_backward(d, ccy=None):
    cur = d
    for _ in range(40):
        if is_biz_day(cur, ccy):
            return cur
        cur -= timedelta(days=1)
    return cur


def biz_days_after(d, n, ccy=None):
    """從 d 往後數 n 個營業日（d 當天不算）。n=0 時回 d。"""
    cur = d
    steps = 0
    while steps < n:
        cur += timedelta(days=1)
        if is_biz_day(cur, ccy):
            steps += 1
    return cur


def biz_days_before(d, n, ccy=None):
    """從 d 往前數 n 個營業日（d 當天不算）。"""
    cur = d
    steps = 0
    while steps < n:
        cur -= timedelta(days=1)
        if is_biz_day(cur, ccy):
            steps += 1
    return cur


def settlement_date(trade_date, lag, ccy=None):
    """交易日 → 交割日（T+lag）。"""
    return biz_days_after(roll_forward(trade_date, ccy), lag, ccy)


def last_trade_date(settle_deadline, lag, ccy=None):
    """
    交割日最晚只能到 settle_deadline 時，最晚下單日是哪天。
    （settlement_date 的反函數，保證 settlement_date(結果, lag) <= deadline）
    """
    return biz_days_before(roll_backward(settle_deadline, ccy), lag, ccy)


def holidays_in_range(start, end, ccy=None):
    """
    區間內（含端點）的休市日，排除本來就休的週六日。
    回傳 [(date, 市場, 名稱), ...]，用來在訊息裡提醒連假。
    """
    out = []
    c = _norm_ccy(ccy)
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            n_tw = holiday_name(cur, "TW")
            if n_tw:
                out.append((cur, "台灣", n_tw))
            if c:
                n_c = holiday_name(cur, c)
                if n_c:
                    out.append((cur, c, n_c))
        cur += timedelta(days=1)
    return out


def holiday_notice(today, days=14, ccys=("USD",)):
    """
    給 LINE 訊息用的一行連假提醒。未來 days 天內沒休市日就回空字串。
    """
    end = today + timedelta(days=days)
    seen, items = set(), []
    for c in (None,) + tuple(ccys):
        for d, mk, nm in holidays_in_range(today, end, c):
            key = (d, mk)
            if key in seen:
                continue
            seen.add(key)
            items.append((d, mk, nm))
    if not items:
        return ""
    wd = "一二三四五六日"
    items.sort()
    body = "、".join(
        f"{d:%m/%d}({wd[d.weekday()]}){nm}" + ("" if mk == "台灣" else f"[{mk}]")
        for d, mk, nm in items
    )
    return f"\n🏖 未來{days}天休市：{body}\n　（交割日順延，申購截止日已一併提前計算）"


def explain(trade_date, lag, ccy="USD"):
    """
    除錯／回覆信件用：把 T+N 的推算過程攤開來講。
    """
    wd = "一二三四五六日"
    naive = trade_date
    n = lag
    while n > 0:                      # 舊算法：只跳週末
        naive += timedelta(days=1)
        if naive.weekday() < 5:
            n -= 1
    real = settlement_date(trade_date, lag, ccy)
    lines = [f"交易日 {trade_date:%Y/%m/%d}({wd[trade_date.weekday()]})｜{ccy}｜T+{lag}",
             f"  原始交割日（只排除週末）：{naive:%Y/%m/%d}({wd[naive.weekday()]})",
             f"  調整後交割日：　　　　　　{real:%Y/%m/%d}({wd[real.weekday()]})"]
    skipped = holidays_in_range(trade_date + timedelta(days=1), real, ccy)
    for d, mk, nm in skipped:
        lines.append(f"    ↳ {d:%m/%d} {mk} {nm} 休市，順延")
    ok, missing = coverage_ok(real, ccy)
    if not ok:
        lines.append(f"  ⚠️ {'、'.join(missing)} {real.year} 年假日表尚未建置，此結果僅排除週末")
    return "\n".join(lines)


def format_holiday_list(year=None, market="TW"):
    """/holiday 指令用：列出某年某市場的休市日。"""
    wd = "一二三四五六日"
    year = year or date.today().year
    tbl = _table(market)
    rows = sorted((k, v) for k, v in tbl.items() if k.startswith(str(year)))
    if not rows:
        return f"{market} {year} 年尚無假日資料（計算時只會排除週六日）。"
    ov = _load_override().get(str(market).upper(), {})
    lines = [f"📅 {market} {year} 年休市日（{len(rows)} 天）"]
    for k, v in rows:
        d = datetime.strptime(k, "%Y-%m-%d").date()
        mark = " *" if k in ov else ""
        lines.append(f"  {d:%m/%d}({wd[d.weekday()]}) {v}{mark}")
    if ov:
        lines.append("  （* 為線上新增）")
    return "\n".join(lines)


if __name__ == "__main__":
    # 對照投資服務部 2026/9 來函
    t = date(2026, 9, 24)
    for lag in (1, 2, 3):
        print(explain(t, lag, "USD"))
        print()
