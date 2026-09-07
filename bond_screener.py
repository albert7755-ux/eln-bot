# -*- coding: utf-8 -*-
"""
bond_screener.py — 海外債條件篩選 / 產業利差 / 本週回顧
========================================================
/find   條件篩選(零 token):幣別、YTM、當期收益率、年期、票面、申購資格
/sector 產業利差:發行機構→產業 分類快取(AI 一次批次分類後存 DB),
        每產業:檔數、中位 YTM、中位當期收益率、中位較美債利差、近30天 Offer 變化
weekly  本週回顧(零 token):報價異動、新上架/下架、下週配息截止與到期、產業概況
"""
import re
import json
import statistics
from datetime import date, timedelta

from bond_coupon_alert import read_bonds, first_num, pi_tag, issuer_of, build_alerts, maturing_soon

CCY_ALIAS = {"usd": "USD", "美元": "USD", "美金": "USD", "aud": "AUD", "澳幣": "AUD", "澳元": "AUD",
             "nzd": "NZD", "紐幣": "NZD", "gbp": "GBP", "英鎊": "GBP", "eur": "EUR", "歐元": "EUR",
             "cad": "CAD", "加幣": "CAD", "cny": "CNY", "人民幣": "CNY", "zar": "ZAR", "南非幣": "ZAR",
             "jpy": "JPY", "日圓": "JPY", "日幣": "JPY"}

SECTORS = ["資訊科技", "通訊服務", "非核心消費", "核心消費", "醫療保健", "金融", "工業",
           "能源", "公用事業", "原物料", "不動產", "政府/主權", "超國家組織", "其他"]


# ---------- 共用:每檔的衍生欄位 ----------
def enrich(b, today):
    off = first_num(b.get("offer"))
    cpn = first_num(b.get("coupon"))
    ytm = first_num(b.get("ytm"))
    yrs = None
    if b.get("maturity"):
        yrs = (b["maturity"] - today).days / 365.25
    cy = (cpn / off * 100) if (cpn and off and off > 1) else None
    return dict(b, _offer=off, _coupon=cpn, _ytm=ytm, _years=yrs, _cy=cy)


# ---------- /find ----------
def parse_find(query: str):
    """
    解析條件字串,例:
      usd ytm>5 10年內          aud cy>4.5 5-10年
      一般 ytm>=5.5 20年以上     專投 usd 票面>4 年期<8
    回傳 dict(filters) 與 unknown tokens
    """
    q = query.strip()
    f = {"ccy": None, "ytm_min": None, "ytm_max": None, "cy_min": None, "cy_max": None,
         "yr_min": None, "yr_max": None, "cpn_min": None, "tag": None, "kw": []}
    toks = [t for t in re.split(r"\s+", q) if t]
    for t in toks:
        tl = t.lower()
        if tl in CCY_ALIAS:
            f["ccy"] = CCY_ALIAS[tl]; continue
        if t in ("一般", "專投", "高資產"):
            f["tag"] = t; continue
        m = re.fullmatch(r"(ytm|殖利率)\s*(>=|>|<=|<|=)\s*(\d+(?:\.\d+)?)", tl)
        if m:
            v = float(m.group(3)); op = m.group(2)
            if op in (">", ">="): f["ytm_min"] = v
            elif op in ("<", "<="): f["ytm_max"] = v
            else: f["ytm_min"] = f["ytm_max"] = v
            continue
        m = re.fullmatch(r"(cy|當期|當息|當期收益率)\s*(>=|>|<=|<|=)\s*(\d+(?:\.\d+)?)", tl)
        if m:
            v = float(m.group(3)); op = m.group(2)
            if op in (">", ">="): f["cy_min"] = v
            elif op in ("<", "<="): f["cy_max"] = v
            else: f["cy_min"] = f["cy_max"] = v
            continue
        m = re.fullmatch(r"(票面|coupon)\s*(>=|>)\s*(\d+(?:\.\d+)?)", tl)
        if m:
            f["cpn_min"] = float(m.group(3)); continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)\s*年", t)
        if m:
            f["yr_min"], f["yr_max"] = float(m.group(1)), float(m.group(2)); continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*年\s*(內|以內|以下)", t)
        if m:
            f["yr_max"] = float(m.group(1)); continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*年\s*(以上|外)", t)
        if m:
            f["yr_min"] = float(m.group(1)); continue
        m = re.fullmatch(r"(年期|y|years?)\s*(>=|>|<=|<)\s*(\d+(?:\.\d+)?)", tl)
        if m:
            v = float(m.group(3))
            if m.group(2) in (">", ">="): f["yr_min"] = v
            else: f["yr_max"] = v
            continue
        f["kw"].append(t)
    return f


def run_find(path, filters, today=None, limit=15):
    today = today or date.today()
    out = []
    for b in read_bonds(path):
        if not b.get("maturity") or b["maturity"] <= today:
            continue
        e = enrich(b, today)
        if e["_offer"] is None:
            continue                                  # 無報價不列
        if filters["ccy"] and str(e["ccy"]).upper() != filters["ccy"]:
            continue
        tag = pi_tag(e)
        if filters["tag"] and filters["tag"] not in tag:
            continue
        if filters["ytm_min"] is not None and (e["_ytm"] is None or e["_ytm"] < filters["ytm_min"]):
            continue
        if filters["ytm_max"] is not None and (e["_ytm"] is None or e["_ytm"] > filters["ytm_max"]):
            continue
        if filters["cy_min"] is not None and (e["_cy"] is None or e["_cy"] < filters["cy_min"]):
            continue
        if filters["cy_max"] is not None and (e["_cy"] is None or e["_cy"] > filters["cy_max"]):
            continue
        if filters["yr_min"] is not None and (e["_years"] is None or e["_years"] < filters["yr_min"]):
            continue
        if filters["yr_max"] is not None and (e["_years"] is None or e["_years"] > filters["yr_max"]):
            continue
        if filters["cpn_min"] is not None and (e["_coupon"] is None or e["_coupon"] < filters["cpn_min"]):
            continue
        if filters["kw"]:
            hay = (str(e["name"]) + " " + issuer_of(e["name"])).lower()
            if not all(k.lower() in hay for k in filters["kw"]):
                continue
        if e["_ytm"] is not None and (e["_ytm"] < 0 or e["_ytm"] > 25):
            continue                                  # 失真 YTM 過濾
        out.append(e)
    out.sort(key=lambda x: -(x["_ytm"] or 0))
    return out[:limit], len(out)


def describe_filters(f):
    parts = []
    if f["ccy"]: parts.append(f["ccy"])
    if f["tag"]: parts.append(f["tag"])
    if f["ytm_min"] is not None: parts.append(f"YTM≥{f['ytm_min']:g}%")
    if f["ytm_max"] is not None: parts.append(f"YTM≤{f['ytm_max']:g}%")
    if f["cy_min"] is not None: parts.append(f"當期≥{f['cy_min']:g}%")
    if f["cy_max"] is not None: parts.append(f"當期≤{f['cy_max']:g}%")
    if f["cpn_min"] is not None: parts.append(f"票面≥{f['cpn_min']:g}%")
    if f["yr_min"] is not None and f["yr_max"] is not None: parts.append(f"{f['yr_min']:g}–{f['yr_max']:g}年")
    elif f["yr_max"] is not None: parts.append(f"{f['yr_max']:g}年內")
    elif f["yr_min"] is not None: parts.append(f"{f['yr_min']:g}年以上")
    if f["kw"]: parts.append("關鍵字:" + " ".join(f["kw"]))
    return "、".join(parts) or "全部"


def format_find(rows, total, filters, today, file_time=""):
    if not rows:
        return (f"🔎 條件：{describe_filters(filters)}\n目前沒有符合的債券。\n\n"
                "用法範例：\n/find usd ytm>5 10年內\n/find aud cy>4.5 5-10年\n/find 一般 ytm>5.5 20年以上")
    lines = [f"🔎 條件：{describe_filters(filters)}",
             f"符合 {total} 檔，依 YTM 高→低列 {len(rows)} 檔", ""]
    for e in rows:
        y = f"{e['_ytm']:.2f}" if e["_ytm"] is not None else "-"
        cy = f"{e['_cy']:.2f}" if e["_cy"] is not None else "-"
        lines.append(f"{e.get('code') or '-'}｜{e['name']}")
        lines.append(f"  {e['ccy']} 票面{e['_coupon']:g}%｜Offer {e['_offer']:g}｜YTM {y}｜當期 {cy}"
                     f"｜{e['maturity']:%Y/%m}({e['_years']:.1f}年)｜{pi_tag(e)}")
    if total > len(rows):
        lines.append(f"…另有 {total-len(rows)} 檔，加條件縮小範圍")
    lines.append("")
    lines.append("當期收益率＝票面÷Offer，反映買進價位下的實際年息；YTM 另計入到期價差。")
    if file_time:
        lines.append(f"📎 報價檔 {file_time}")
    return "\n".join(lines)


# ---------- /sector:產業分類快取 ----------
def ensure_sector_table(engine, text):
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE IF NOT EXISTS bond_issuer_sector(
            issuer TEXT PRIMARY KEY, sector TEXT NOT NULL, updated_at TIMESTAMPTZ DEFAULT NOW());"""))


def load_sector_map(engine, text):
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT issuer, sector FROM bond_issuer_sector")).fetchall()
    return {r[0]: r[1] for r in rows}


def classify_issuers(engine, text, llm_json_fn, issuers):
    """
    批次分類尚未分類的發行機構(一次 AI 呼叫最多 60 家),結果存 DB。
    llm_json_fn(prompt, max_tokens) -> (dict|None, source, errs)
    """
    ensure_sector_table(engine, text)
    known = load_sector_map(engine, text)
    todo = [i for i in dict.fromkeys(issuers) if i not in known]
    done = 0
    for k in range(0, len(todo), 60):
        chunk = todo[k:k+60]
        prompt = ("請把下列債券發行機構分類到以下產業之一(GICS 大類,政府/主權債與國際組織另列):"
                  + "、".join(SECTORS) + "\n\n發行機構清單:\n" + "\n".join(f"- {i}" for i in chunk)
                  + '\n\n只回傳 JSON:{"map":{"發行機構名稱":"產業", ...}},名稱必須與清單完全一致,'
                    "不確定者填「其他」。")
        got, _, _ = llm_json_fn(prompt, max_tokens=2500)
        m = (got or {}).get("map") or {}
        with engine.begin() as conn:
            for name in chunk:
                sec = m.get(name) or "其他"
                if sec not in SECTORS:
                    sec = "其他"
                conn.execute(text("""INSERT INTO bond_issuer_sector(issuer, sector) VALUES (:i,:s)
                                     ON CONFLICT (issuer) DO UPDATE SET sector=EXCLUDED.sector, updated_at=NOW()"""),
                             {"i": name, "s": sec})
                done += 1
    return done


def sector_summary(path, today, sector_map, ust_curve=None, interp_fn=None, hist_change_fn=None,
                   ccy="USD", min_count=3):
    """
    回傳 list[dict] 依中位利差排序:
      sector, n, med_ytm, med_cy, med_spread(bp or None), avg_chg30(%) or None, avg_years
    hist_change_fn(isin) -> 30天 Offer 變化 % 或 None
    """
    groups = {}
    for b in read_bonds(path):
        if not b.get("maturity") or b["maturity"] <= today:
            continue
        if ccy and str(b["ccy"]).upper() != ccy:
            continue
        e = enrich(b, today)
        if e["_offer"] is None or e["_ytm"] is None or not (0 < e["_ytm"] <= 25):
            continue
        sec = sector_map.get(issuer_of(b["name"]), "其他")
        sp = None
        if ust_curve and interp_fn and e["_years"]:
            base = interp_fn(ust_curve, e["_years"])
            if base is not None:
                sp = (e["_ytm"] - base) * 100
                if abs(sp) > 400:
                    sp = None
        chg = hist_change_fn(b["isin"]) if hist_change_fn else None
        groups.setdefault(sec, []).append((e["_ytm"], e["_cy"], sp, chg, e["_years"]))
    out = []
    for sec, rows in groups.items():
        if len(rows) < min_count:
            continue
        ytms = [r[0] for r in rows]
        cys = [r[1] for r in rows if r[1] is not None]
        sps = [r[2] for r in rows if r[2] is not None]
        chgs = [r[3] for r in rows if r[3] is not None]
        yrs = [r[4] for r in rows if r[4] is not None]
        out.append({"sector": sec, "n": len(rows),
                    "med_ytm": statistics.median(ytms),
                    "med_cy": statistics.median(cys) if cys else None,
                    "med_spread": statistics.median(sps) if sps else None,
                    "avg_chg30": statistics.mean(chgs) if chgs else None,
                    "avg_years": statistics.mean(yrs) if yrs else None})
    out.sort(key=lambda x: (x["sector"] == "其他",
                            x["med_spread"] if x["med_spread"] is not None else 9e9))
    return out


def format_sector(rows, today, ccy="USD", file_time=""):
    if not rows:
        return "目前沒有足夠資料計算產業概況(需先完成發行機構產業分類)。"
    has_sp = any(r["med_spread"] is not None for r in rows)
    has_chg = any(r["avg_chg30"] is not None for r in rows)
    lines = [f"🏭 {today:%m/%d} 產業概況（{ccy}，利差窄→寬）", ""]
    for r in rows:
        seg = [f"{r['sector']}（{r['n']}檔）", f"YTM {r['med_ytm']:.2f}"]
        if r["med_cy"] is not None: seg.append(f"當期 {r['med_cy']:.2f}")
        if r["med_spread"] is not None: seg.append(f"較美債 {r['med_spread']:+.0f}bp")
        if r["avg_chg30"] is not None: seg.append(f"近30天 {r['avg_chg30']:+.1f}%")
        lines.append("▪ " + "｜".join(seg))
    lines.append("")
    note = "利差＝該產業債券 YTM 中位數減同年期美債；利差窄代表市場要求的風險補償低。"
    if has_chg:
        note += "近30天為該產業 Offer 平均變化，正值代表價格上漲(殖利率下行)。"
    lines.append(note)
    lines.append("各產業平均年期不同，利差比較僅供參考。")
    if file_time:
        lines.append(f"📎 報價檔 {file_time}")
    return "\n".join(lines)


# ---------- 本週回顧 ----------
def next_week_range(today):
    """回傳下週一~下週五"""
    mon = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    return mon, mon + timedelta(days=4)


def weekly_review(path, today, movers_txt="", new_isins=(), gone_isins=(), sector_rows=None,
                  new_names=None, gone_names=None):
    """
    組成本週回顧文字。movers_txt 由 main.price_movers 提供;
    new_isins/gone_isins 由 main 查 bond_price_history 提供。
    """
    wd = "一二三四五六日"
    mon = today - timedelta(days=today.weekday())
    lines = [f"📆 本週回顧（{mon:%m/%d}–{today:%m/%d}）", ""]

    # 1) 報價異動
    if movers_txt:
        lines.append("📊 本週報價異動（vs 上週）")
        lines.append(movers_txt.strip())
        lines.append("")
    else:
        lines.append("📊 本週報價異動：無變動達門檻的債券\n")

    # 2) 上架/下架
    if new_names or gone_names:
        if new_names:
            lines.append(f"🆕 本週新上架 {len(new_names)} 檔")
            lines += [f"・{n}" for n in list(new_names)[:8]]
            if len(new_names) > 8: lines.append(f"…另有 {len(new_names)-8} 檔")
        if gone_names:
            lines.append(f"📤 本週下架/停售 {len(gone_names)} 檔")
            lines += [f"・{n}" for n in list(gone_names)[:8]]
            if len(gone_names) > 8: lines.append(f"…另有 {len(gone_names)-8} 檔")
        lines.append("")

    # 3) 下週要注意
    nmon, nfri = next_week_range(today)
    alerts = build_alerts(path, today, 14)
    nxt = [a for a in alerts if a["status"].startswith("✅") and nmon <= a["last_trade"] <= nfri
           and a["offer"] not in (None, "", 0, "#VALUE!", "#N/A")]
    mats = [b for b in maturing_soon(path, today, 14) if nmon <= b["maturity"] <= nfri]
    lines.append(f"👀 下週（{nmon:%m/%d}–{nfri:%m/%d}）")
    lines.append(f"・配息前申購截止：{len(nxt)} 檔（每日雷達會提醒）")
    if mats:
        lines.append(f"・到期回流：{len(mats)} 檔")
        lines += [f"　▪ {b['maturity']:%m/%d}({wd[b['maturity'].weekday()]}) {b['name']} {b['ccy']} {first_num(b['coupon']):g}%" for b in mats[:5]]
    else:
        lines.append("・到期回流：無")
    lines.append("")

    # 4) 產業概況(可選)
    if sector_rows:
        lines.append("🏭 產業利差（USD，窄→寬）")
        for r in sector_rows[:8]:
            seg = f"{r['sector']} {r['med_ytm']:.2f}%"
            if r["med_spread"] is not None: seg += f"（較美債 {r['med_spread']:+.0f}bp）"
            if r["avg_chg30"] is not None: seg += f" 近30天 {r['avg_chg30']:+.1f}%"
            lines.append("・" + seg)
        lines.append("")
    lines.append("※ 依總行報價檔 Offer 計算，僅供內部參考，非投資建議。")
    return "\n".join(lines)
