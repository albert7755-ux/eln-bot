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
    財務重點五指標:市值、EPS、ROE、負債比、淨負債/EBITDA。
    yfinance 的 .info 偶爾會失敗(限流/欄位缺),因此多層取數:info → fast_info → 財報表。
    回傳 dict;完全取不到時回 None,並把原因記在 print log。
    """
    if not ticker:
        print("[BondSheet] no ticker → skip financials")
        return None
    info, fast = {}, {}
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        try:
            info = t.info or {}
        except Exception as e:
            print(f"[BondSheet] .info fail {ticker}: {e}")
        try:
            fast = dict(t.fast_info) if t.fast_info else {}
        except Exception as e:
            print(f"[BondSheet] .fast_info fail {ticker}: {e}")
    except Exception as e:
        print(f"[BondSheet] yfinance import/init fail {ticker}: {e}")
        return None

    def g(*keys, src=None):
        src = src if src is not None else info
        for k in keys:
            v = src.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    fin = {
        "ticker": ticker,
        "currency": info.get("financialCurrency") or info.get("currency") or "USD",
        "market_cap": g("marketCap") or g("market_cap", "marketCap", src=fast)
                      or ((g("currentPrice") or g("last_price", "lastPrice", src=fast) or 0)
                          * (g("sharesOutstanding") or g("shares", src=fast) or 0)) or None,
        "eps": g("trailingEps", "epsTrailingTwelveMonths"),
        "roe": g("returnOnEquity"),
        "total_debt": g("totalDebt"),
        "cash": g("totalCash", "totalCashPerShare") if info.get("totalCash") else g("totalCash"),
        "ebitda": g("ebitda"),
        "debt_ratio": None,
        "net_debt_ebitda": None,
    }

    # 財報表補齊(info 缺值時)
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        bs = t.balance_sheet
        if bs is not None and not bs.empty:
            col = bs.columns[0]
            def bget(*names):
                for n in names:
                    if n in bs.index:
                        try:
                            return float(bs.loc[n, col])
                        except Exception:
                            pass
                return None
            assets = bget("Total Assets")
            liab = bget("Total Liabilities Net Minority Interest", "Total Liab")
            if assets and liab:
                fin["debt_ratio"] = round(liab / assets * 100, 1)
            if fin["total_debt"] is None:
                fin["total_debt"] = bget("Total Debt", "Long Term Debt")
            if fin["cash"] is None:
                fin["cash"] = bget("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
        if fin["eps"] is None or fin["ebitda"] is None:
            fs = t.financials
            if fs is not None and not fs.empty:
                col = fs.columns[0]
                if fin["ebitda"] is None and "EBITDA" in fs.index:
                    fin["ebitda"] = float(fs.loc["EBITDA", col])
    except Exception as e:
        print(f"[BondSheet] statements fail {ticker}: {e}")

    # EPS 備援:info 沒有 trailingEps 時,依序用 財報 Diluted EPS → 淨利/在外股數
    if fin["eps"] is None:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            fs = t.financials
            if fs is not None and not fs.empty:
                col = fs.columns[0]
                for k in ("Diluted EPS", "Basic EPS"):
                    if k in fs.index:
                        fin["eps"] = float(fs.loc[k, col]); break
            if fin["eps"] is None:
                ni = info.get("netIncomeToCommon")
                sh = info.get("sharesOutstanding") or fast.get("shares")
                if isinstance(ni, (int, float)) and isinstance(sh, (int, float)) and sh:
                    fin["eps"] = float(ni) / float(sh)
            if fin["eps"] is not None:
                print(f"[BondSheet] {ticker} EPS 由備援來源取得: {fin['eps']:.2f}")
        except Exception as e:
            print(f"[BondSheet] EPS fallback {ticker}: {e}")

    # ROE 補算:info 沒有 returnOnEquity 時,用 淨利 / 股東權益 自己算
    if fin["roe"] is None:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            ni = None
            fs = t.financials
            if fs is not None and not fs.empty:
                col = fs.columns[0]
                for k in ("Net Income", "Net Income Common Stockholders", "Net Income Continuous Operations"):
                    if k in fs.index:
                        ni = float(fs.loc[k, col]); break
            if ni is None:
                ni = info.get("netIncomeToCommon")
                ni = float(ni) if isinstance(ni, (int, float)) else None
            eq = None
            bs2 = t.balance_sheet
            if bs2 is not None and not bs2.empty:
                col = bs2.columns[0]
                for k in ("Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity",
                          "Total Equity Gross Minority Interest"):
                    if k in bs2.index:
                        eq = float(bs2.loc[k, col]); break
            if ni is not None and eq:
                fin["roe"] = ni / eq
                print(f"[BondSheet] {ticker} ROE 由淨利/股東權益補算: {fin['roe']:.3f}")
        except Exception as e:
            print(f"[BondSheet] ROE fallback {ticker}: {e}")

    if fin["ebitda"] and fin["total_debt"] is not None:
        fin["net_debt_ebitda"] = round((fin["total_debt"] - (fin["cash"] or 0)) / fin["ebitda"], 1)

    got = [k for k in ("market_cap", "eps", "roe", "debt_ratio", "net_debt_ebitda") if fin.get(k) is not None]
    print(f"[BondSheet] {ticker} 取得指標: {got}")
    return fin if got else None

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

UST_TENORS = [(0.25, "3M"), (2, "2Y"), (5, "5Y"), (10, "10Y"), (20, "20Y"), (30, "30Y")]

def get_ust_curve():
    """
    美債曲線 {年期: 殖利率%}。yfinance 取 3M/5Y/10Y/30Y，
    再用 FRED 補 2Y/20Y（曲線取樣點越密，內插出的同年期基準越準）。
    """
    out = {}
    try:
        import yfinance as yf
        for sym, yrs in (("^IRX", 0.25), ("^FVX", 5), ("^TNX", 10), ("^TYX", 30)):
            try:
                h = yf.Ticker(sym).history(period="5d")
                if not h.empty:
                    out[yrs] = round(float(h["Close"].iloc[-1]), 3)
            except Exception:
                pass
    except Exception as e:
        print(f"[BondSheet] UST curve(yf) fail: {e}")
    try:
        import requests, csv, io
        for series, yrs in (("DGS2", 2), ("DGS20", 20)):
            try:
                url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
                r = requests.get(url, timeout=15)
                rows = [x for x in csv.reader(io.StringIO(r.text))][1:]
                vals = [float(x[1]) for x in rows if len(x) > 1 and x[1] not in (".", "")]
                if vals:
                    out[yrs] = round(vals[-1], 3)
            except Exception:
                pass
    except Exception as e:
        print(f"[BondSheet] UST curve(FRED) fail: {e}")
    print(f"[BondSheet] UST curve: {sorted(out.items())}")
    return out

def _interp_ust(curve, years):
    """依剩餘年期在美債曲線上線性內插"""
    if not curve or years is None:
        return None
    pts = sorted(curve.items())
    if years <= pts[0][0]:
        return pts[0][1]
    if years >= pts[-1][0]:
        return pts[-1][1]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= years <= x2:
            return y1 + (y2 - y1) * (years - x1) / (x2 - x1)
    return None

def ust_spread_bp(bond, curve, today=None):
    """該債 YTM 減去同年期美債殖利率(bp)。非美元計價或資料不足回 None"""
    from datetime import date as _d
    today = today or _d.today()
    if str(bond.get("ccy", "")).upper() != "USD":
        return None
    y = first_num(bond.get("ytm"))
    if y is None or not bond.get("maturity"):
        return None
    years = (bond["maturity"] - today).days / 365.25
    base = _interp_ust(curve, years)
    if base is None:
        return None
    return round((y - base) * 100)

def call_info(bond):
    """
    提前買回資訊。只回傳「有/無」與可信的下一個買回日期。
    ※ 不解析買回價格:報價檔備註格式不一,曾誤把年份前三碼當成價格,
      價格請以產品說明書為準。
    """
    import re as _re
    remark = str(bond.get("remark") or "")
    ytm = str(bond.get("ytm") or "")
    has_call = "/" in ytm or ("買回" in remark) or ("贖回" in remark) or bool(_re.search(r"\bcall\b", remark, _re.I))
    if not has_call:
        return "無"
    # 日期需與買回字樣同時出現才採用,避免抓到到期日或其他日期
    m = None
    for kw in ("買回", "贖回", "call", "Call"):
        idx = remark.find(kw)
        if idx >= 0:
            window = remark[max(0, idx - 30): idx + 30]
            m = _re.search(r"(20\d{2})[/年.-](\d{1,2})", window)
            if m:
                break
    if m:
        return f"有\n{m.group(1)}/{int(m.group(2)):02d}"
    return "有"

def first_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    import re as _re
    m = _re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None

WATERMARK = "本內容僅供參考且不構成要約或要約引誘"

LEGAL_NOTE = ("【本內容僅供參考且不構成要約或要約引誘，特定標的之商品風險、申購之條件、"
              "限制費用及其他相關權利義務，應依產品說明暨投資風險預告書等相關文件為準。】")

RISK_ITEMS = [
    ("利率風險", "市場利率上升時債券價格將下跌，存續期間越長價格波動幅度越大；提前贖回或於到期前賣出可能發生本金損失。"),
    ("信用風險", "發行機構之信用狀況若惡化或發生違約，將影響利息與本金之支付，投資人可能損失全部或部分本金。信用評等僅為評等機構意見，不保證還本付息。"),
    ("匯率風險", "以外幣計價之債券，換回新臺幣時將受匯率變動影響，可能造成損益增減，匯率損失甚至可能超過債券利息收益。"),
    ("提前買回風險", "具提前買回條款之債券，發行機構可能於市場利率下降時提前買回，投資人將面臨再投資利率較低之風險，實際報酬可能低於預期。"),
    ("流動性風險", "海外債券次級市場流動性可能不足，於到期前出售未必能以合理價格成交，買賣價差亦可能擴大。"),
]


def _clean(v, dash="-", nd=2):
    """空值統一顯示 dash;浮點數四捨五入到 nd 位,避免報價檔原始浮點數整串印出"""
    if v in (None, "", 0, "#VALUE!", "#N/A"):
        return dash
    if isinstance(v, float):
        return f"{round(v, nd):g}"
    if isinstance(v, str):
        t = v.strip()
        # 「5.26/5.27」這種 YTM/YTC 也逐段處理
        if "/" in t:
            segs = []
            for part in t.split("/"):
                try:
                    segs.append(f"{round(float(part.strip()), nd):g}")
                except ValueError:
                    segs.append(part.strip())
            return "/".join(segs)
        try:
            return f"{round(float(t), nd):g}"
        except ValueError:
            return t
    return v

def _ratings_of(bonds):
    for b in bonds:
        rt = " / ".join(x for x in str(b.get("ratings") or "").split(" / ") if x and x.upper() not in ("N/A", "NA", "NONE"))
        if rt:
            return rt
    return "-"

# ---------- A. LINE 文字版 ----------
def build_sheet_text(issuer, intro, bonds, fin=None, parent_note="", hist_map=None, fin_comment="", peers="", rating_note="", ust_curve=None, today=None):
    today = today or date.today()
    hist_map = hist_map or {}
    live = [b for b in bonds if not (b.get("maturity") and b["maturity"] < today)]
    lines = [f"📋 {issuer}｜發行機構參考資訊（{today:%Y/%m/%d}）", f"⚠️ {WATERMARK}", ""]
    if intro:
        lines += ["【發行機構簡介】", intro, ""]
    rt_line = _ratings_of(live)
    if rating_note:
        rt_line += f"\n{rating_note}"
    lines += ["【信用評等 S&P/Moody's/Fitch】", rt_line, ""]
    if fin:
        src = f"（{parent_note}，代碼 {fin['ticker']}）" if parent_note else f"（{fin['ticker']}）"
        lines.append(f"【財務重點】{src}")
        lines += [f"{k}:{v}" for k, v in _fin_rows(fin)]
        if fin_comment:
            lines += ["", "【財務比率解讀（AI）】", fin_comment]
        if peers:
            lines += ["", "【同業比較】", peers]
        lines.append("")
    else:
        lines += ["【財務重點】財務資料暫時無法取得，請參閱發行機構最新公開財報", ""]
    lines.append(f"【債券標的一覽】共 {len(live)} 檔")
    ust_curve = ust_curve or {}
    for b in live[:15]:
        ytm = _clean(b.get("ytm"))
        h = hist_map.get(b["isin"], "")
        sp = ust_spread_bp(b, ust_curve, today)
        sp_s = f"｜較美債 {sp:+d}bp" if sp is not None else ""
        ci = call_info(b)
        call_s = f"｜提前買回:{ci}" if ci != "無" else ""
        lines.append(f"▪ {b.get('code') or '-'} {b['name']}")
        lines.append(f"  {b['ccy']} {b['coupon']}% {b['freq']}｜Offer {_clean(b.get('offer'))}｜YTM/YTC {ytm}{sp_s}")
        lines.append(f"  到期{b['maturity']:%Y/%m}｜剩餘年期 {_clean(b.get('years'))}｜{b.get('seniority') or '-'}"
                     f"｜最低申購 {_clean(b.get('min_amt'))}｜{pi(b)}{call_s}{h}")
    if len(live) > 15:
        lines.append(f"…另有 {len(live)-15} 檔（/issuer {issuer} 查看）")
    lines += ["", "【風險揭露】"]
    lines += [f"・{k}：{v}" for k, v in RISK_ITEMS]
    lines += ["", "※ 本資料由公開資訊彙整。", LEGAL_NOTE]
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
        # 黑體(近似微軟正黑體)優先;把字型檔放進 repo 的 fonts/ 目錄即可全平台一致
        "fonts/NotoSansTC-Regular.ttf",
        "fonts/msjh.ttf",
        "fonts/SourceHanSansTC-Regular.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",   # 文泉驛微米黑(黑體)
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",     # 文泉驛正黑(黑體)
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
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


def build_sheet_pdf(out_path, issuer, intro, bonds, fin=None, parent_note="", hist_map=None, fin_comment="", peers="", rating_note="", ust_curve=None, charts_png=None, today=None):
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

    st_title = ParagraphStyle("t", fontName=F, fontSize=16, leading=22, textColor=NAVY, spaceAfter=4)
    st_sub = ParagraphStyle("s", fontName=F, fontSize=8.5, leading=12, textColor=GRAY, spaceAfter=10)
    st_h = ParagraphStyle("h", fontName=F, fontSize=11, textColor=NAVY, spaceBefore=8, spaceAfter=3)
    st_p = ParagraphStyle("p", fontName=F, fontSize=9.5, leading=14)
    st_small = ParagraphStyle("sm", fontName=F, fontSize=7.5, textColor=GRAY, leading=10)
    st_risk = ParagraphStyle("rk", fontName=F, fontSize=7.5, leading=10.5, spaceAfter=1.2, leftIndent=3)
    st_legal = ParagraphStyle("lg", fontName=F, fontSize=8, leading=11.5, textColor=NAVY)

    def _watermark(canv, doc_):
        """斜向平鋪浮水印:僅供內部訓練參考，不構成推介行為"""
        canv.saveState()
        try:
            canv.setFont(F, 15)
        except Exception:
            canv.setFont("Helvetica", 15)
        canv.setFillColor(colors.HexColor("#0B2A4A"))
        try:
            canv.setFillAlpha(0.06)
        except Exception:
            canv.setFillColor(colors.HexColor("#E4E9EF"))
        w, h = A4
        canv.translate(w / 2, h / 2)
        canv.rotate(35)
        for row in range(-3, 4):
            for col in range(-1, 2):
                canv.drawCentredString(col * 330, row * 150, WATERMARK)
        canv.restoreState()

    doc = SimpleDocTemplate(out_path, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm, topMargin=14*mm, bottomMargin=12*mm)
    el = []
    el.append(Paragraph(f"{issuer}｜發行機構參考資訊", st_title))
    el.append(Spacer(1, 1.5*mm))
    el.append(Paragraph(f"資料日期：{today:%Y/%m/%d}", st_sub))

    el.append(Paragraph("發行機構簡介", st_h))
    el.append(Paragraph(intro or "-", st_p))

    el.append(Paragraph("信用評等（S&amp;P / Moody's / Fitch）", st_h))
    el.append(Paragraph(_ratings_of(live) + (f"　　{rating_note}" if rating_note else ""), st_p))

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
            el.append(Paragraph("財務比率解讀：" + fin_comment, st_p))
        if peers:
            el.append(Spacer(1, 1.5*mm))
            el.append(Paragraph("同業比較：" + peers, st_p))
    else:
        el.append(Paragraph("財務資料暫時無法取得，請參閱發行機構最新公開財報。", st_p))

    if charts_png:
        try:
            from reportlab.platypus import Image as RLImage
            from PIL import Image as PILImage
            iw, ih = PILImage.open(charts_png).size
            disp_w = 180 * mm
            el.append(Paragraph("近五季財報趨勢", st_h))
            el.append(RLImage(charts_png, width=disp_w, height=disp_w * ih / iw))
            el.append(Paragraph("資料來源：公開財報（yfinance）；單位為該公司報表幣別之億元。", st_small))
        except Exception as e:
            print(f"[BondSheet] embed charts fail: {e}")
    el.append(Paragraph(f"債券標的一覽（{len(live)} 檔）", st_h))
    ust_curve = ust_curve or {}
    hdr = ["產品代碼", "債券名稱", "幣別", "票面%", "頻率", "Offer", "YTM/YTC", "較美債", "到期", "剩餘\n年期", "順位", "最低\n申購", "提前\n買回", "近30日"]
    data = [hdr]
    for b in live[:20]:
        sp = ust_spread_bp(b, ust_curve, today)
        sen = str(b.get("seniority") or "-").replace("優先無擔保", "優先無擔").replace("次順位", "次順位")
        data.append([b.get("code") or "-", b["name"], b["ccy"], str(b["coupon"]), b["freq"],
                     str(_clean(b.get("offer"))), str(_clean(b.get("ytm"))),
                     f"{sp:+d}bp" if sp is not None else "-",
                     f"{b['maturity']:%Y/%m}" if b.get("maturity") else "-",
                     str(_clean(b.get("years"))), sen, str(_clean(b.get("min_amt"))),
                     call_info(b).replace("（", "\n（"),
                     hist_map.get(b["isin"], "").replace("｜近30日", "").strip() or "-"])
    # 欄寬總和須 ≤ 180mm(A4 扣左右邊界),否則表格會超出頁面
    t = Table(data, colWidths=[21*mm, 29*mm, 7*mm, 9*mm, 11*mm, 11*mm, 15*mm, 11*mm, 11*mm, 9*mm, 12*mm, 11*mm, 13*mm, 10*mm], repeatRows=1)
    style = [("FONTNAME", (0,0), (-1,-1), F), ("FONTSIZE", (0,0), (-1,0), 6.5), ("FONTSIZE", (0,1), (-1,-1), 6.5),
             ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
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
        el.append(Paragraph(f"…另有 {len(live)-20} 檔未列", st_small))
    el.append(Spacer(1, 3*mm))
    el.append(Paragraph("風險揭露", st_h))
    for k, v in RISK_ITEMS:
        el.append(Paragraph(f"● <b>{k}</b>：{v}", st_risk))
    el.append(Spacer(1, 2.5*mm))
    el.append(Paragraph(LEGAL_NOTE, st_legal))
    el.append(Spacer(1, 1.5*mm))
    el.append(Paragraph("YTM/YTC 欄呈現兩個數字者表示該券有提前買回條款（金色標示）；提前買回欄之日期與價格摘自報價檔備註。"
                        "本資料由公開資訊彙整，僅供參考，非投資建議或要約；"
                        "報價可能隨市場變動，詳細產品資訊（配息條件、提前買回條款、風險揭露等）請以產品說明書為準。", st_small))
    doc.build(el, onFirstPage=_watermark, onLaterPages=_watermark)
    return out_path

# ---------- 財報圖表（近 5 季）----------
def _cjk_font_path():
    import os
    for p in ("fonts/NotoSansTC-Regular.ttf", "fonts/msjh.ttf",
              "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if os.path.exists(p):
            return p
    return None

def get_quarterly_series(ticker, n=5):
    """
    近 n 季財報序列（單位：億）。回傳 dict 或 None。
    labels / revenue / op_income / ocf / fcf / debt / cash / debt_ebitda / int_cover
    """
    if not ticker:
        return None
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        fs = t.quarterly_financials
        bs = t.quarterly_balance_sheet
        cf = t.quarterly_cashflow
        if fs is None or fs.empty:
            return None
        cols = list(fs.columns)[:n][::-1]      # 舊 → 新
        def pick(df, names, col):
            if df is None or df.empty or col not in df.columns:
                return None
            for nm in names:
                if nm in df.index:
                    try:
                        v = float(df.loc[nm, col])
                        return v if v == v else None      # 濾掉 NaN
                    except Exception:
                        pass
            return None
        out = {"labels": [], "revenue": [], "op_income": [], "ocf": [], "fcf": [],
               "debt": [], "cash": [], "debt_ebitda": [], "int_cover": []}
        for c in cols:
            q = (c.month - 1) // 3 + 1
            out["labels"].append(f"{q}Q{str(c.year)[2:]}")
            rev = pick(fs, ["Total Revenue", "Operating Revenue"], c)
            opi = pick(fs, ["Operating Income", "EBIT"], c)
            ebitda = pick(fs, ["EBITDA", "Normalized EBITDA"], c)
            intexp = pick(fs, ["Interest Expense", "Interest Expense Non Operating"], c)
            ocf = pick(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"], c)
            capex = pick(cf, ["Capital Expenditure", "Capital Expenditures"], c)
            fcf = pick(cf, ["Free Cash Flow"], c)
            if fcf is None and ocf is not None and capex is not None:
                fcf = ocf + capex          # capex 通常為負值
            debt = pick(bs, ["Total Debt", "Long Term Debt"], c)
            cash = pick(bs, ["Cash And Cash Equivalents",
                             "Cash Cash Equivalents And Short Term Investments"], c)
            e8 = lambda v: round(v / 1e8, 1) if v is not None else None
            out["revenue"].append(e8(rev)); out["op_income"].append(e8(opi))
            out["ocf"].append(e8(ocf)); out["fcf"].append(e8(fcf))
            out["debt"].append(e8(debt)); out["cash"].append(e8(cash))
            out["debt_ebitda"].append(round(debt / (ebitda * 4), 1) if (debt and ebitda) else None)
            out["int_cover"].append(round(opi / abs(intexp), 1) if (opi and intexp) else None)
        if not any(x is not None for x in out["revenue"]):
            return None
        return out
    except Exception as e:
        print(f"[BondSheet] quarterly {ticker} fail: {e}")
        return None

def build_charts_png(q, out_png, title=""):
    """把近 5 季資料畫成 2x2 圖表（仿財報重點版型），成功回傳路徑，否則 None"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        fp = _cjk_font_path()
        if fp:
            font_manager.fontManager.addfont(fp) if fp.endswith(".ttf") else None
            try:
                name = font_manager.FontProperties(fname=fp).get_name()
                matplotlib.rcParams["font.family"] = name
            except Exception:
                pass
        matplotlib.rcParams["axes.unicode_minus"] = False
        BLUE, GREEN, NAVY = "#1F8AC0", "#3EA97A", "#0B2A4A"
        L = q["labels"]
        fig, axes = plt.subplots(2, 2, figsize=(11, 6.2), dpi=150)

        def bars(ax, a, b, la, lb, ttl):
            import numpy as np
            x = np.arange(len(L)); w = 0.38
            av = [v if v is not None else 0 for v in a]
            bv = [v if v is not None else 0 for v in b]
            r1 = ax.bar(x - w/2, av, w, label=la, color=BLUE)
            r2 = ax.bar(x + w/2, bv, w, label=lb, color=GREEN)
            for r in list(r1) + list(r2):
                h = r.get_height()
                if h:
                    ax.annotate(f"{h:,.0f}" if abs(h) >= 100 else f"{h:,.1f}",
                                (r.get_x() + r.get_width()/2, h), ha="center",
                                va="bottom" if h >= 0 else "top", fontsize=7)
            ax.set_title(ttl, fontsize=10, color=NAVY, pad=20)
            ax.set_xticks(x); ax.set_xticklabels(L, fontsize=8)
            ax.legend(fontsize=7, frameon=False, ncol=2, loc="lower left",
                      bbox_to_anchor=(0, 1.02))
            ax.spines[["top", "right"]].set_visible(False)
            ax.tick_params(axis="y", labelsize=7)
            ax.axhline(0, color="#999999", linewidth=0.6)

        def lines(ax, a, b, la, lb, ttl):
            import numpy as np
            x = np.arange(len(L))
            ax2 = ax.twinx()
            ax.plot(x, [v if v is not None else float("nan") for v in a], "o-", color=BLUE, label=la, linewidth=1.6)
            ax2.plot(x, [v if v is not None else float("nan") for v in b], "o-", color=GREEN, label=lb, linewidth=1.6)
            for i, v in enumerate(a):
                if v is not None:
                    ax.annotate(f"{v}", (i, v), fontsize=7, color=BLUE, ha="center", va="bottom")
            for i, v in enumerate(b):
                if v is not None:
                    ax2.annotate(f"{v}", (i, v), fontsize=7, color=GREEN, ha="center", va="bottom")
            ax.set_title(ttl, fontsize=10, color=NAVY, pad=20)
            ax.set_xticks(x); ax.set_xticklabels(L, fontsize=8)
            ax.tick_params(axis="y", labelsize=7, colors=BLUE)
            ax2.tick_params(axis="y", labelsize=7, colors=GREEN)
            ax.spines[["top"]].set_visible(False); ax2.spines[["top"]].set_visible(False)
            h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, fontsize=7, frameon=False, ncol=2,
                      loc="lower left", bbox_to_anchor=(0, 1.02))

        bars(axes[0][0], q["revenue"], q["op_income"], "營業收入(億)", "營業淨利(億)", "公司營運表現")
        bars(axes[0][1], q["ocf"], q["fcf"], "營業活動現金流(億)", "自由現金流量(億)", "公司現金流")
        bars(axes[1][0], q["debt"], q["cash"], "總債務(億)", "現金及約當現金(億)", "債務與現金")
        lines(axes[1][1], q["debt_ebitda"], q["int_cover"], "總債務/EBITDA", "利息保障倍數", "信用相關比率")
        fig.tight_layout(pad=1.6)
        fig.savefig(out_png, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        return out_png
    except Exception as e:
        print(f"[BondSheet] charts fail: {e}")
        return None
