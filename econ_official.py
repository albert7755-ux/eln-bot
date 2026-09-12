# -*- coding: utf-8 -*-
"""
econ_official.py — 直接從官方統計數列抓數據(不靠新聞搜尋)
==========================================================
來源:FRED CSV(免API金鑰,與 bond_daily_report 抓 DGS2/DGS20 同一個管道)
涵蓋:CPI、核心CPI、非農就業、PCE、核心PCE

判斷「已公布」的方式:
  數列出現了「比資料庫記錄更新的參考月份」→ 代表新一期數據已發布。
  數字全部由官方數列計算(月增/年增),AI 不參與,不會有臆測。
"""
import csv
import io
from datetime import datetime, date

import requests

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BondBot/1.0)"}

# key 對應 econ_watch.ECON_ITEMS 的 key
SERIES = {
    "us_cpi": {
        "label": "美國CPI",
        "main": ("CPIAUCSL", "整體CPI"),      # 季調,算月增
        "yoy_src": ("CPIAUCSL", None),        # 年增用同一數列
        "core": ("CPILFESL", "核心CPI"),
        "kind": "index",
    },
    "us_pce": {
        "label": "美國PCE",
        "main": ("PCEPI", "整體PCE"),
        "yoy_src": ("PCEPI", None),
        "core": ("PCEPILFE", "核心PCE"),
        "kind": "index",
    },
    "us_nfp": {
        "label": "美國非農就業",
        "main": ("PAYEMS", "非農就業人數"),    # 千人,算月變化
        "yoy_src": None,
        "core": ("UNRATE", "失業率"),
        "kind": "jobs",
    },
}


def fetch_series(series_id, timeout=20):
    """回傳 [(date, value), ...] 依日期排序;失敗回 []"""
    try:
        r = requests.get(FRED_CSV.format(sid=series_id), headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        rows = list(csv.reader(io.StringIO(r.text)))
        out = []
        for row in rows[1:]:
            if len(row) < 2:
                continue
            try:
                d = datetime.strptime(row[0].strip(), "%Y-%m-%d").date()
                v = float(row[1].strip())
            except ValueError:
                continue          # 跳過表頭與 "." 缺值
            out.append((d, v))
        out.sort(key=lambda x: x[0])
        return out
    except Exception as e:
        print(f"[EconOfficial] {series_id} 抓取失敗: {e}")
        return []


def _mom_yoy(series):
    """回傳 (參考月, 最新值, 月增%, 年增%)"""
    if len(series) < 13:
        return None
    d, v = series[-1]
    _, v_prev = series[-2]
    mom = (v / v_prev - 1) * 100 if v_prev else None
    yoy = None
    for dd, vv in series:
        if dd.year == d.year - 1 and dd.month == d.month and vv:
            yoy = (v / vv - 1) * 100
            break
    return d, v, mom, yoy


def get_official(key):
    """
    抓取該項目的官方數據,回傳 dict:
      {ref_month(date), lines(list[str]), summary(str)} 或 None
    """
    cfg = SERIES.get(key)
    if not cfg:
        return None
    main = fetch_series(cfg["main"][0])
    if not main:
        return None

    if cfg["kind"] == "jobs":
        if len(main) < 2:
            return None
        d, v = main[-1]
        _, v_prev = main[-2]
        chg = (v - v_prev) * 1000        # PAYEMS 單位為千人
        lines = [f"非農就業人數月增 {chg/1000:,.1f} 萬人（{v:,.0f} 千人）"]
        une = fetch_series(cfg["core"][0])
        if une and une[-1][0] == d:
            lines.append(f"失業率 {une[-1][1]:.1f}%")
        return {"ref_month": d, "lines": lines,
                "summary": f"{d:%Y年%-m月}非農就業月增約 {chg/1000:,.1f} 萬人"}

    got = _mom_yoy(main)
    if not got:
        return None
    d, v, mom, yoy = got
    lines = []
    if mom is not None and yoy is not None:
        lines.append(f"{cfg['main'][1]}：月增 {mom:+.1f}%、年增 {yoy:+.1f}%")
    core = fetch_series(cfg["core"][0])
    cgot = _mom_yoy(core) if core else None
    if cgot and cgot[0] == d:
        _, _, cmom, cyoy = cgot
        if cmom is not None and cyoy is not None:
            lines.append(f"{cfg['core'][1]}：月增 {cmom:+.1f}%、年增 {cyoy:+.1f}%")
    if not lines:
        return None
    return {"ref_month": d, "lines": lines,
            "summary": f"{d:%Y年%-m月}{cfg['label']}年增 {yoy:+.1f}%"}


# ---------- 已公布偵測(記錄最新參考月,出現更新的月份就是新數據) ----------
def ensure_table(engine, text):
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE IF NOT EXISTS econ_official_seen(
            event_key TEXT PRIMARY KEY, ref_month DATE NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW());"""))


def last_seen_month(engine, text, key):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT ref_month FROM econ_official_seen WHERE event_key=:k"),
                           {"k": key}).fetchone()
    return row[0] if row else None


def mark_seen(engine, text, key, ref_month):
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO econ_official_seen(event_key, ref_month) VALUES (:k,:d)
                             ON CONFLICT (event_key) DO UPDATE
                             SET ref_month=EXCLUDED.ref_month, updated_at=NOW()"""),
                     {"k": key, "d": ref_month})


def check_official(engine, text, keys=None):
    """
    檢查官方數列是否出現新的參考月份。
    回傳 [(key, label, ref_month, lines)] — 只含「這次新出現」的項目。
    首次執行時只記錄基準、不推播,避免上線當下把舊數據當新數據推出去。
    """
    ensure_table(engine, text)
    out = []
    for key in (keys or SERIES.keys()):
        data = get_official(key)
        if not data:
            continue
        prev = last_seen_month(engine, text, key)
        ref = data["ref_month"]
        if prev is None:
            mark_seen(engine, text, key, ref)      # 建立基準,不推播
            print(f"[EconOfficial] {key} 建立基準:{ref}")
            continue
        if ref > prev:
            out.append((key, SERIES[key]["label"], ref, data["lines"]))
            mark_seen(engine, text, key, ref)
            print(f"[EconOfficial] {key} 偵測到新數據:{prev} → {ref}")
    return out


def format_official(label, ref_month, lines):
    body = [f"📊 {label} 公布（{ref_month:%Y年%m月}數據）", ""]
    body += lines
    body += ["", "（資料來源：官方統計數列／FRED，數字為系統計算，僅供參考，非投資建議）"]
    return "\n".join(body)
