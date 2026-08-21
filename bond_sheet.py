# -*- coding: utf-8 -*-
"""
bond_sheet.py — 發行機構銷售資訊一頁通（/sheet 指令用）
=======================================================
輸出兩種：LINE 文字版 build_sheet_text()、PDF 版 build_sheet_pdf()
資料由 main.py 蒐集後傳入：簡介(AI快取)、信評(報價檔)、財務(yfinance)、架上標的(報價檔)、近期價格(歷史庫)
"""
from datetime import date

# ---------- 財務重點（yfinance） ----------
def get_financials(ticker):
    """
    財務重點五指標:市值、EPS、ROE、負債比(總負債/總資產)、淨負債/EBITDA。
    回傳 dict 或 None。
    """
    if not ticker:
        return None
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        def g(k):
            v = info.get(k)
            return float(v) if isinstance(v, (int, float)) else None
        fin = {
            "ticker": ticker,
            "currency": info.get("financialCurrency") or "USD",
            "market_cap": g("marketCap"),
            "eps": g("trailingEps"),
            "roe": g("returnOnEquity"),          # 小數,顯示時 *100
            "total_debt": g("totalDebt"),
            "cash": g("totalCash"),
            "ebitda": g("ebitda"),
            "debt_ratio": None,
        }
        # 負債比 = 總負債 / 總資產(抓資產負債表;失敗就留空)
        try:
            bs = t.balance_sheet
            if bs is not None and not bs.empty:
                col = bs.columns[0]
                assets = float(bs.loc["Total Assets", col]) if "Total Assets" in bs.index else None
                liab = None
                for k in ("Total Liabilities Net Minority Interest", "Total Liab"):
                    if k in bs.index:
                        liab = float(bs.loc[k, col])
                        break
                if assets and liab:
                    fin["debt_ratio"] = round(liab / assets * 100, 1)
        except Exception as e:
            print(f"[BondSheet] balance_sheet {ticker}: {e}")
        if fin["ebitda"] and fin["total_debt"] is not None:
            net_debt = fin["total_debt"] - (fin["cash"] or 0)
            fin["net_debt_ebitda"] = round(net_debt / fin["ebitda"], 1)
        else:
            fin["net_debt_ebitda"] = None
        if not any([fin["market_cap"], fin["eps"], fin["total_debt"]]):
            return None
        return fin
    except Exception as e:
        print(f"[BondSheet] financials {ticker} fail: {e}")
        return None

def _fmt_b(v, ccy="USD"):
    """換算成億（1e8）並帶幣別"""
    if v is None:
        return "-"
    unit = {"USD": "億美元", "TWD": "億台幣", "JPY": "億日圓", "EUR": "億歐元", "AUD": "億澳幣", "GBP": "億英鎊"}.get(ccy, f"億{ccy}")
    return f"{v/1e8:,.0f}{unit}"

def _fin_rows(fin):
    ccy = fin.get("currency", "USD")
    roe = fin.get("roe")
    rows = [("市值", _fmt_b(fin.get("market_cap"), "USD")),
            ("EPS(近12月)", f"{fin['eps']:.2f} {ccy}" if fin.get("eps") is not None else "-"),
            ("ROE", f"{roe*100:.1f}%" if roe is not None else "-"),
            ("負債比", f"{fin['debt_ratio']:.0f}%" if fin.get("debt_ratio") is not None else "-"),
            ("淨負債/EBITDA", f"{fin['net_debt_ebitda']}x" if fin.get("net_debt_ebitda") is not None else "-")]
    return rows

def _clean(v, dash="-"):
    return dash if v in (None, "", 0, "#VALUE!", "#N/A") else v

def _ratings_of(bonds):
    for b in bonds:
        rt = " / ".join(x for x in str(b.get("ratings") or "").split(" / ") if x and x.upper() not in ("N/A", "NA", "NONE"))
        if rt:
            return rt
    return "-"

# ---------- A. LINE 文字版 ----------
def build_sheet_text(issuer, intro, bonds, fin=None, parent_note="", hist_map=None, fin_comment="", today=None):
    today = today or date.today()
    hist_map = hist_map or {}
    live = [b for b in bonds if not (b.get("maturity") and b["maturity"] < today)]
    lines = [f"📋 {issuer}｜銷售資訊（{today:%Y/%m/%d}）", ""]
    if intro:
        lines += ["【發行機構簡介】", intro, ""]
    lines += ["【信用評等 S&P/Moody's/Fitch】", _ratings_of(live), ""]
    if fin:
        src = f"（{parent_note}，代碼 {fin['ticker']}）" if parent_note else f"（{fin['ticker']}）"
        lines.append(f"【財務重點】{src}")
        lines += [f"{k}:{v}" for k, v in _fin_rows(fin)]
        if fin_comment:
            lines += ["", "【財務比率解讀（AI）】", fin_comment]
        lines.append("")
    elif parent_note:
        lines += [f"【財務重點】{parent_note}：無公開財報可查", ""]
    lines.append(f"【本行架上標的】共 {len(live)} 檔")
    for b in live[:15]:
        ytm = _clean(b.get("ytm"))
        callable_note = ""
        if isinstance(ytm, str) and "/" in ytm:
            callable_note = "｜可提前買回"
        sen = str(b.get("seniority") or "")
        sen_note = f"｜{sen}" if sen and sen != "優先無擔保" else ""
        h = hist_map.get(b["isin"], "")
        lines.append(f"▪ {b.get('code') or '-'} {b['name']}")
        lines.append(f"  {b['ccy']} {b['coupon']}% {b['freq']}｜Offer {_clean(b.get('offer'))}｜YTM {ytm}"
                     f"｜到期{b['maturity']:%Y/%m} {pi(b)}{callable_note}{sen_note}{h}")
    if len(live) > 15:
        lines.append(f"…另有 {len(live)-15} 檔（/issuer {issuer} 查看）")
    lines += ["", "※ 簡介、財務數字與解讀由 AI/公開資料彙整，僅供內部參考，非投資建議；"
              "報價以本行系統為準，詳細產品資訊（配息條件、提前買回條款、風險等）請以產品說明書為準"]
    return "\n".join(lines)

def pi(b):
    try:
        from bond_coupon_alert import pi_tag
        return pi_tag(b)
    except Exception:
        return ""

# ---------- B. PDF 版 ----------
def _register_cjk_font(pdfmetrics, UnicodeCIDFont):
    """
    註冊中文字型,依序嘗試:
    1. 環境變數 BOND_SHEET_FONT 指定的 .ttf/.ttc
    2. 系統常見的 Noto/文泉驛 CJK 字型(會實際嵌入 PDF,任何裝置都能看)
    3. Adobe CID 字型 MSung-Light(不嵌入,部分瀏覽器可能無法顯示,最後手段)
    """
    import os
    from reportlab.pdfbase.ttfonts import TTFont
    candidates = []
    if os.getenv("BOND_SHEET_FONT"):
        candidates.append(os.getenv("BOND_SHEET_FONT"))
    candidates += [
        "fonts/NotoSansTC-Regular.ttf",  # repo 內自帶(建議)
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    for path in candidates:
        try:
            if path and os.path.exists(path):
                pdfmetrics.registerFont(TTFont("CJK", path, subfontIndex=0))
                return "CJK"
        except Exception as e:
            print(f"[BondSheet] font {path} fail: {e}")
    pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))
    return "MSung-Light"


def build_sheet_pdf(out_path, issuer, intro, bonds, fin=None, parent_note="", hist_map=None, fin_comment="", today=None):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import ParagraphStyle

    F = _register_cjk_font(pdfmetrics, UnicodeCIDFont)
    NAVY = colors.HexColor("#0B2A4A"); GOLD = colors.HexColor("#C9A227"); GRAY = colors.HexColor("#666666")
    today = today or date.today()
    hist_map = hist_map or {}
    live = [b for b in bonds if not (b.get("maturity") and b["maturity"] < today)]

    st_title = ParagraphStyle("t", fontName=F, fontSize=16, textColor=NAVY, spaceAfter=2)
    st_sub = ParagraphStyle("s", fontName=F, fontSize=8.5, textColor=GRAY, spaceAfter=8)
    st_h = ParagraphStyle("h", fontName=F, fontSize=11, textColor=NAVY, spaceBefore=8, spaceAfter=3)
    st_p = ParagraphStyle("p", fontName=F, fontSize=9.5, leading=14)
    st_small = ParagraphStyle("sm", fontName=F, fontSize=7.5, textColor=GRAY, leading=10)

    doc = SimpleDocTemplate(out_path, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm, topMargin=14*mm, bottomMargin=12*mm)
    el = []
    el.append(Paragraph(f"{issuer}｜發行機構銷售資訊", st_title))
    el.append(Paragraph(f"固定收益科　{today:%Y/%m/%d}　內部參考", st_sub))

    el.append(Paragraph("發行機構簡介", st_h))
    el.append(Paragraph(intro or "-", st_p))

    el.append(Paragraph("信用評等（S&amp;P / Moody's / Fitch）", st_h))
    el.append(Paragraph(_ratings_of(live), st_p))

    el.append(Paragraph("財務重點" + (f"　（{parent_note}，代碼 {fin['ticker']}）" if fin and parent_note else (f"　（{fin['ticker']}）" if fin else "")), st_h))
    if fin:
        rows = _fin_rows(fin)
        t = Table([[k for k, _ in rows], [v for _, v in rows]], colWidths=[(180/len(rows))*mm]*len(rows))
        t.setStyle(TableStyle([
            ("FONTNAME", (0,0), (-1,-1), F), ("FONTSIZE", (0,0), (-1,0), 8), ("FONTSIZE", (0,1), (-1,1), 10),
            ("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("ALIGN", (0,0), (-1,-1), "CENTER"), ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#D0D0D0")),
            ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        el.append(t)
        if fin_comment:
            el.append(Spacer(1, 2*mm))
            el.append(Paragraph("財務比率解讀（AI）：" + fin_comment, st_p))
    else:
        el.append(Paragraph((parent_note + "：" if parent_note else "") + "無公開財報可查", st_p))

    el.append(Paragraph(f"本行架上標的（{len(live)} 檔）", st_h))
    hdr = ["產品代碼", "債券名稱", "幣別", "票面%", "頻率", "Offer", "YTM/YTC", "到期", "資格", "近30日"]
    data = [hdr]
    for b in live[:20]:
        data.append([b.get("code") or "-", b["name"], b["ccy"], str(b["coupon"]), b["freq"],
                     str(_clean(b.get("offer"))), str(_clean(b.get("ytm"))),
                     f"{b['maturity']:%Y/%m}" if b.get("maturity") else "-",
                     pi(b).replace("🔒", "").replace("💎", "").replace("專投", "專投 ").strip() or "一般",
                     hist_map.get(b["isin"], "").replace("｜近30日", "").strip() or "-"])
    t = Table(data, colWidths=[24*mm, 40*mm, 9*mm, 11*mm, 12*mm, 13*mm, 17*mm, 13*mm, 20*mm, 16*mm], repeatRows=1)
    style = [("FONTNAME", (0,0), (-1,-1), F), ("FONTSIZE", (0,0), (-1,0), 7.5), ("FONTSIZE", (0,1), (-1,-1), 7.5),
             ("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
             ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#D0D0D0")),
             ("TOPPADDING", (0,0), (-1,-1), 2.5), ("BOTTOMPADDING", (0,0), (-1,-1), 2.5),
             ("ALIGN", (2,1), (-1,-1), "CENTER")]
    for i, b in enumerate(live[:20], 1):
        ytm = b.get("ytm")
        if isinstance(ytm, str) and "/" in ytm:
            style.append(("TEXTCOLOR", (6, i), (6, i), GOLD))  # 可提前買回:金色標示
    t.setStyle(TableStyle(style))
    el.append(t)
    if len(live) > 20:
        el.append(Paragraph(f"…另有 {len(live)-20} 檔未列，完整清單請洽固定收益科", st_small))
    el.append(Spacer(1, 4*mm))
    el.append(Paragraph("YTM/YTC 欄呈現兩個數字者表示該券有提前買回條款（金色標示）。"
                        "簡介、財務數字與解讀由 AI 及公開資料彙整，僅供內部參考，非投資建議；"
                        "商品資訊與報價以本行系統為準，詳細產品資訊（配息條件、提前買回條款、風險等）請以產品說明書為準。", st_small))
    doc.build(el)
    return out_path
