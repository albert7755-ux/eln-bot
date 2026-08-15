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
            elif h == "債券評等": col.setdefault("sp", i)
            elif h == "債券順位": col.setdefault("seniority", i)
            elif h == "BidPrice": col.setdefault("bid", i)
            elif h == "剩餘年期": col.setdefault("years", i)
            elif h == "存續期間": col.setdefault("duration", i)
            elif h == "產品風險屬性": col.setdefault("risk", i)
            elif h == "備註": col.setdefault("remark", i)
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
            sp_i = col.get("sp")
            ratings = ""
            if sp_i is not None:
                trio = [str(x).strip() for x in r[sp_i:sp_i+3] if x not in (None, "")]
                ratings = " / ".join(trio) if trio else ""
            b = dict(
                isin=isin, code=g("code"), name=str(g("name") or "").strip(),
                ratings=ratings, seniority=g("seniority"), bid=g("bid"),
                years=g("years"), duration=g("duration"), risk=g("risk"),
                remark=str(g("remark") or "").strip(),
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

def issuer_of(name):
    """『美林私人有限公司債3』→『美林私人有限公司』；『美國公債1』→『美國公債』"""
    n = re.sub(r"[\s\d]+$", "", str(name)).strip()
    if n.endswith("公司債"):
        base = n[:-3].strip()   # 蘋果公司債 → 蘋果；Alphabet 公司債 → Alphabet
        return base + "公司" if base.endswith("有限") else base   # 美林私人有限公司債 → 美林私人有限公司
    if n.endswith("公債"):
        return n                # 美國公債、澳洲公債
    if n.endswith("債"):
        return n[:-1]           # 西太平洋銀行債 → 西太平洋銀行
    return n

def biz_days_after(d, n):
    cur = d
    while n > 0:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n -= 1
    return cur

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

def build_alert_message(path, today=None, lookahead=LOOKAHEAD_DAYS, days_ahead=3, max_lines=30):
    """
    回傳給 LINE 用的純文字訊息。
    lookahead  : 往前看幾天的配息日（預設 14）
    days_ahead : 只顯示『最晚下單日』落在今天起 N 個營業日內的（預設 3）；None = 全部顯示
    """
    today = today or date.today()
    alerts = build_alerts(path, today, lookahead)
    ok_all = [a for a in alerts if a["status"].startswith("✅")]
    gone = len(alerts) - len(ok_all)
    wd = "一二三四五六日"
    if days_ahead is None:
        ok, cutoff = ok_all, None
    else:
        cutoff = biz_days_after(today, days_ahead)
        ok = [a for a in ok_all if a["last_trade"] <= cutoff]
    scope = f"最晚下單日在 {days_ahead} 個營業日內（～{cutoff:%m/%d}）" if cutoff else f"未來{lookahead}天全部"
    if not ok:
        return (f"📅 {today:%m/%d}({wd[today.weekday()]}) 海外債配息雷達\n"
                f"{scope}沒有需要搶進的配息債。\n"
                f"（未來{lookahead}天共 {len(alerts)} 檔配息，還來得及 {len(ok_all)} 檔，可打 /coupon all 看全部）"
                + DISCLAIMER)
    ok.sort(key=lambda a: (a["last_trade"], -a["lag"], a["name"]))
    lines = [f"📅 {today:%m/%d}({wd[today.weekday()]}) 海外債配息雷達",
             f"{scope}：{len(ok)} 檔",
             f"（未來{lookahead}天共 {len(alerts)} 檔配息｜還來得及 {len(ok_all)}｜已過 {gone}）\n"]
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

# ---------- Excel 條件表 ----------
def build_coupon_sheet(path, out_path, today=None, lookahead=LOOKAHEAD_DAYS, intro_fn=None):
    """
    把『還來得及參與』的債券輸出成 Excel 條件表。
    intro_fn(list[str]) -> dict[str,str]：可選，傳入發行機構名單，回傳簡介（例如用 Claude 產生）
    回傳 (out_path, 檔數, 發行機構數)
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    today = today or date.today()
    alerts = [a for a in build_alerts(path, today, lookahead) if a["status"].startswith("✅")]
    alerts.sort(key=lambda a: (a["last_trade"], -a["lag"], a["name"]))
    issuers = []
    for a in alerts:
        iss = issuer_of(a["name"])
        a["issuer"] = iss
        if iss not in issuers:
            issuers.append(iss)
    intros = {}
    if intro_fn and issuers:
        try:
            intros = intro_fn(issuers) or {}
        except Exception as e:
            intros = {"_error": str(e)}

    wb = Workbook()
    navy = PatternFill("solid", fgColor="0B2A4A")
    hf = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    bf = Font(name="Arial", size=10)
    thin = Side(style="thin", color="D0D0D0"); bd = Border(top=thin, bottom=thin, left=thin, right=thin)
    wrap = Alignment(vertical="top", wrap_text=True)

    # Sheet 1 條件表
    ws = wb.active; ws.title = "配息債條件表"
    cols = ["最晚下單日", "配息日", "交割", "發行機構", "債券名稱", "ISIN", "產品代碼", "幣別",
            "票面利率%", "配息頻率", "評等(S&P/Moody's/Fitch)", "債券順位", "Bid", "Offer", "YTM/YTC",
            "到期日", "剩餘年期", "存續期間", "風險屬性", "最低申購面額", "本日額度", "備註", "來源Sheet"]
    ws["A1"] = f"海外債配息雷達 — 還來得及參與（{today:%Y/%m/%d} 起未來{lookahead}天，共 {len(alerts)} 檔）"
    ws["A1"].font = Font(name="Arial", bold=True, size=13, color="0B2A4A")
    ws["A2"] = "最晚交割日=配息日前1營業日；US/CA T+1、其他 T+2；營業日僅排除週六日；配息日由到期日+頻率倒推，請以實際為準"
    ws["A2"].font = Font(name="Arial", size=9, italic=True, color="666666")
    for j, c in enumerate(cols, 1):
        cell = ws.cell(row=4, column=j, value=c); cell.font = hf; cell.fill = navy
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); cell.border = bd
    for i, a in enumerate(alerts, 5):
        vals = [a["last_trade"], a["coupon_date"], f"T+{a['lag']}", a["issuer"], a["name"], a["isin"], a["code"], a["ccy"],
                a["coupon"], a["freq"], a["ratings"], a["seniority"],
                a["bid"] if a["bid"] not in (None, "#VALUE!") else None,
                a["offer"] if a["offer"] not in (None, "#VALUE!") else None,
                a["ytm"], a["maturity"], a["years"], a["duration"], a["risk"], a["min_amt"], a["avail"],
                a["remark"], "、".join(sorted(a["sheets"]))]
        for j, v in enumerate(vals, 1):
            cell = ws.cell(row=i, column=j, value=v); cell.font = bf; cell.border = bd; cell.alignment = wrap
            if isinstance(v, date): cell.number_format = "yyyy/mm/dd"
        if a["last_trade"] == today:
            for j in range(1, len(cols) + 1):
                ws.cell(row=i, column=j).fill = PatternFill("solid", fgColor="FFF2CC")
    widths = [11, 11, 6, 18, 24, 15, 15, 6, 9, 8, 20, 12, 8, 8, 11, 11, 8, 8, 8, 12, 8, 50, 30]
    for j, w in enumerate(widths, 1): ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "F5"; ws.auto_filter.ref = f"A4:{get_column_letter(len(cols))}{4 + len(alerts)}"

    # Sheet 2 發行機構簡介
    ws2 = wb.create_sheet("發行機構簡介")
    ws2["A1"] = "發行機構簡介（AI 產生，僅供內部參考，對客說明請以公開資訊為準）"
    ws2["A1"].font = Font(name="Arial", bold=True, size=13, color="0B2A4A")
    for j, c in enumerate(["發行機構", "本次配息檔數", "簡介"], 1):
        cell = ws2.cell(row=3, column=j, value=c); cell.font = hf; cell.fill = navy; cell.border = bd
    cnt = {}
    for a in alerts: cnt[a["issuer"]] = cnt.get(a["issuer"], 0) + 1
    for i, iss in enumerate(issuers, 4):
        ws2.cell(row=i, column=1, value=iss).font = bf
        ws2.cell(row=i, column=2, value=cnt.get(iss, 0)).font = bf
        c3 = ws2.cell(row=i, column=3, value=intros.get(iss, intros.get("_error", "")))
        c3.font = bf; c3.alignment = wrap
        for j in range(1, 4): ws2.cell(row=i, column=j).border = bd
    ws2.column_dimensions["A"].width = 24; ws2.column_dimensions["B"].width = 12; ws2.column_dimensions["C"].width = 90

    wb.save(out_path)
    return out_path, len(alerts), len(issuers)

if __name__ == "__main__":
    p = sys.argv[1]
    t = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    print(build_alert_message(p, t))
