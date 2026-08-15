# -*- coding: utf-8 -*-
"""
bond_coupon_alert.py — 海外債配息「最晚買入日」提醒
====================================================
用法（獨立測試）：
    python bond_coupon_alert.py 08-14-2026-Bond_Pricing_Update_excel.xlsx

在龍蝦Bot裡：
    from bond_coupon_alert import build_alert_message
    msg = build_alert_message("path/to/latest.xlsx", today=date.today())

邏輯（依 Albert 規則）：
  1. 配息日 = 從「到期日」+「配息頻率」倒推（同一天號，每半年/每季/每年/每月）
  2. 最晚交割日 = 配息日的前 1 個營業日
  3. ISIN 開頭 US / CA → T+1；其他 (XS/AU/NZ/GB/…) → T+2
     最晚下單日 = 最晚交割日 再往前 N 個營業日
  4. 只列出「配息日落在 今天 ~ 今天+14天」的債券
     ※ 營業日只排除週六日，未排除台美假日
"""
import re
import sys
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from openpyxl import load_workbook

FREQ_MONTHS = {"每月": 1, "每季": 3, "每半年": 6, "每年": 12}
LOOKAHEAD_DAYS = 14

DISCLAIMER = (
    "\n⚠️ 已知限制\n"
    "1. 配息日是從「到期日＋配息頻率」倒推的，少數債券付息日與到期日不同號，請以實際配息日為主\n"
    "2. 營業日只避開週六日，未避開台美假日，假日前後請人工再確認"
)

def is_bond_pricing_file(path, filename=""):
    """判斷上傳的 Excel 是不是總行的海外債報價檔（跟 ELN 檔區分開）"""
    if "bond" in str(filename).lower() and "pric" in str(filename).lower():
        return True
    try:
        wb = load_workbook(path, read_only=True)
        names = wb.sheetnames
        wb.close()
        return any(("海外債券資訊" in n) or ("加碼標的" in n) for n in names)
    except Exception:
        return False

# ---------- 小工具 ----------
def to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

def settle_lag(isin):
    """US / CA 開頭 T+1，其他 T+2"""
    return 1 if str(isin).upper().startswith(("US", "CA")) else 2

def biz_days_before(d, n):
    """從 d 往前推 n 個營業日（只跳過週六日）"""
    cur = d
    while n > 0:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            n -= 1
    return cur

def next_coupon_dates(maturity, freq_months, start, end):
    """
    從到期日往回每 freq_months 個月推一次，
    回傳所有落在 [start, end] 的配息日。
    """
    if maturity is None or not freq_months:
        return []
    out = []
    d = maturity
    # 先往回推到 end 之前（避免長天期債跑很久，直接用月差估算）
    months_back = ((maturity.year - end.year) * 12 + (maturity.month - end.month))
    k = max(0, months_back // freq_months - 1)
    d = maturity - relativedelta(months=freq_months * k)
    while d >= start:
        if d <= end:
            out.append(d)
        d -= relativedelta(months=freq_months)
    return sorted(out)

# ---------- 讀 Excel ----------
def read_bonds(path):
    """把所有 sheet 讀成 list[dict]，並用 ISIN 去重"""
    wb = load_workbook(path, read_only=True, data_only=True)
    bonds = {}
    for name in wb.sheetnames:
        ws = wb[name]
        rows = list(ws.iter_rows(values_only=True))
        # 找標題列（含 "ISIN"）
        hdr_idx = next((i for i, r in enumerate(rows)
                        if r and any(isinstance(c, str) and "ISIN" in c for c in r)), None)
        if hdr_idx is None:
            continue
        hdr = [str(c).replace("\n", "").replace(" ", "") if c else "" for c in rows[hdr_idx]]
        col = {}
        for i, h in enumerate(hdr):
            if "ISIN" in h: col.setdefault("isin", i)
            elif h.startswith("產品代碼"): col.setdefault("code", i)
            elif h == "債券名稱": col.setdefault("name", i)
            elif h == "幣別": col.setdefault("ccy", i)
            elif h.startswith("票面"): col.setdefault("coupon", i)
            elif h.startswith("配息"): col.setdefault("freq", i)
            elif h == "OfferPrice": col.setdefault("offer", i)
            elif h.startswith("YTM"): col.setdefault("ytm", i)
            elif h == "到期日": col.setdefault("maturity", i)
            elif h.startswith("最低申購"): col.setdefault("min_amt", i)
            elif h.startswith("本日有"): col.setdefault("avail", i)
            elif h == "交割日": col.setdefault("settle", i)
        for r in rows[hdr_idx + 2:]:          # 跳過評等副標題列
            if not r or not r[col["isin"]]:
                continue
            isin = str(r[col["isin"]]).strip()
            if not re.match(r"^[A-Z]{2}[A-Z0-9]{9}\d$", isin):
                continue
            def g(k):
                return r[col[k]] if k in col and col[k] < len(r) else None
            b = dict(
                isin=isin, code=g("code"), name=str(g("name") or "").strip(),
                ccy=g("ccy"), coupon=g("coupon"), freq=str(g("freq") or "").strip(),
                offer=g("offer"), ytm=g("ytm"), maturity=to_date(g("maturity")),
                min_amt=g("min_amt"), avail=g("avail"), sheet=name,
                sheets=set([name]),
            )
            if isin in bonds:
                bonds[isin]["sheets"].add(name)
            else:
                bonds[isin] = b
    return list(bonds.values())

# ---------- 核心 ----------
def build_alerts(path, today=None, lookahead=LOOKAHEAD_DAYS):
    today = today or date.today()
    end = today + timedelta(days=lookahead)
    alerts = []
    for b in read_bonds(path):
        fm = FREQ_MONTHS.get(b["freq"])
        for cd in next_coupon_dates(b["maturity"], fm, today, end):
            last_settle = biz_days_before(cd, 1)
            lag = settle_lag(b["isin"])
            last_trade = biz_days_before(last_settle, lag)
            status = "✅ 可買" if last_trade >= today else "⛔ 已過"
            alerts.append(dict(
                b, coupon_date=cd, last_settle=last_settle,
                last_trade=last_trade, lag=lag, status=status,
                days_left=(last_trade - today).days,
            ))
    alerts.sort(key=lambda a: (a["coupon_date"], -a["lag"], a["name"]))
    return alerts

def build_alert_message(path, today=None, lookahead=LOOKAHEAD_DAYS, max_lines=30):
    """回傳給 LINE 用的純文字訊息：只列『還來得及』的，已過的只給數字"""
    today = today or date.today()
    alerts = build_alerts(path, today, lookahead)
    ok = [a for a in alerts if a["status"].startswith("✅")]
    gone = len(alerts) - len(ok)
    wd = "一二三四五六日"
    if not ok:
        return (f"📅 {today:%m/%d} 海外債配息雷達\n"
                f"未來{lookahead}天有 {len(alerts)} 檔配息，但最晚下單日皆已過，今日無可搶配息標的。"
                + DISCLAIMER)
    ok.sort(key=lambda a: (a["last_trade"], -a["lag"], a["name"]))
    lines = [f"📅 {today:%m/%d}({wd[today.weekday()]}) 海外債配息雷達",
             f"未來{lookahead}天 {len(alerts)} 檔配息｜還來得及 {len(ok)} 檔｜已過 {gone} 檔",
             "（依最晚下單日排序，越上面越急）\n"]
    cur = None
    for i, a in enumerate(ok):
        if a["last_trade"] != cur:
            cur = a["last_trade"]
            tag = "🔥 今天最後一天" if cur == today else f"⏰ 最晚下單 {cur:%m/%d}({wd[cur.weekday()]})"
            lines.append(f"── {tag} ──")
        offer = a["offer"] if a["offer"] not in (None, "", 0, "#VALUE!") else "-"
        ytm = a["ytm"] if a["ytm"] not in (None, "", 0) else "-"
        avail = "" if str(a["avail"]) == "有" else f"｜額度:{a['avail']}"
        lines.append(
            f"{a['name']} {a['ccy']} {a['coupon']}% {a['freq']}\n"
            f"  配息{a['coupon_date']:%m/%d}｜T+{a['lag']}｜Offer {offer}｜YTM {ytm}{avail}"
        )
        if i + 1 >= max_lines and i + 1 < len(ok):
            lines.append(f"…另有 {len(ok)-i-1} 檔，見Excel")
            break
    lines.append(DISCLAIMER)
    return "\n".join(lines)

if __name__ == "__main__":
    p = sys.argv[1]
    t = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    print(build_alert_message(p, t))
