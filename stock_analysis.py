# -*- coding: utf-8 -*-
"""
stock_analysis.py — 個股完整分析報告（華爾街分析師視角）
=========================================================
指令: /stock <代碼>  例如 /stock intc

涵蓋:
  1. 商業模式與收入來源、產業趨勢
  2. 過去5年財務健康度(營收/淨利/FCF/利潤率/負債/ROE) + 體質變強/變弱判斷
  3. 護城河評分(品牌/網路效應/轉換成本/成本優勢/專利技術) 1-10分 + 同業比較
  4. 估值分析(本益比同業比較、簡化DCF情境敏感度表、產業平均、低估/高估結論)
  5. 未來成長潛力(市場規模、產業成長率、擴張機會、新產品、AI/技術優勢)
  6. 多空辯論(兩位分析師對話) + 中性綜合結論
  7. 投資評估(短期/長期展望、催化因素、風險、市場評估角度傾向)

設計原則:
  - 財務數字一律來自 yfinance 真實數據,AI 只負責解讀,不臆測數字
  - DCF 用真實FCF起點做「情境敏感度表」(多/中/空頭三組假設),
    不給單一「目標價」,避免虛假精確度
  - 結論用「市場評估角度:正向/中性/保守」取代直接的買賣建議,
    避免被誤用為正式投資建議
"""
import os
import re
import json
import tempfile

ANALYST_DISCLAIMER = (
    "本報告由 AI 依公開資訊與市場數據彙整分析，僅供內部研究參考與教育訓練使用，"
    "不構成任何投資建議、要約或勸誘。財務數字來源為公開市場資料，"
    "質化分析與情境推估僅反映公開資訊之綜合判斷，可能與實際狀況有落差，"
    "投資人應自行判斷並承擔投資風險，本報告不對任何投資決策負責。"
)


# ---------- 字型(與 bond_sheet.py 共用邏輯,獨立實作避免耦合) ----------
def _cjk_font():
    import glob
    base = os.path.dirname(os.path.abspath(__file__))
    cands = [os.getenv("BOND_SHEET_FONT", "")]
    for n in ("NotoSansTC-Regular.ttf", "NotoSansTC.ttf", "msjh.ttf"):
        cands += [os.path.join(base, "fonts", n), os.path.join(base, n)]
    cands += ["/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]
    cands += sorted(glob.glob(os.path.join(base, "fonts", "*.tt*")))

    def _usable(p):
        try:
            return p and os.path.exists(p) and os.path.getsize(p) > 50 * 1024
        except Exception:
            return False
    for c in cands:
        if _usable(c):
            return c
    return None


# ---------- 財務數據(真實,來自 yfinance) ----------
def get_5y_financials(ticker):
    """
    抓近5年年度財務數據 + 目前估值指標。
    回傳 dict 或 None(抓不到)。所有數字皆為 yfinance 實際回傳值,不臆測。
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        fin = t.financials          # 年度損益表
        bs = t.balance_sheet        # 年度資產負債表
        cf = t.cashflow             # 年度現金流量表

        def pick(df, names, col):
            if df is None or df.empty or col not in df.columns:
                return None
            for nm in names:
                if nm in df.index:
                    try:
                        v = df.loc[nm, col]
                        if hasattr(v, "iloc"):
                            v = v.iloc[0]
                        v = float(v)
                        return v if v == v else None
                    except Exception:
                        continue
            return None

        years = []
        if fin is not None and not fin.empty:
            cols = list(fin.columns)[:5]
            for c in cols:
                rev = pick(fin, ["Total Revenue", "Operating Revenue"], c)
                ni = pick(fin, ["Net Income", "Net Income Common Stockholders"], c)
                op = pick(fin, ["Operating Income"], c)
                ocf = pick(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"], c)
                capex = pick(cf, ["Capital Expenditure", "Capital Expenditures"], c)
                fcf = pick(cf, ["Free Cash Flow"], c)
                if fcf is None and ocf is not None and capex is not None:
                    fcf = ocf - abs(capex)
                debt = pick(bs, ["Total Debt", "Long Term Debt"], c)
                equity = pick(bs, ["Stockholders Equity", "Total Stockholder Equity",
                                   "Common Stock Equity"], c)
                cash = pick(bs, ["Cash And Cash Equivalents",
                                "Cash Cash Equivalents And Short Term Investments"], c)
                years.append({
                    "year": c.year, "revenue": rev, "net_income": ni, "op_income": op,
                    "fcf": fcf, "debt": debt, "equity": equity, "cash": cash,
                    "net_margin": (ni / rev * 100) if (ni and rev) else None,
                    "roe": (ni / equity * 100) if (ni and equity) else None,
                })
        years.sort(key=lambda x: x["year"])   # 舊→新

        cur = {
            "ticker": ticker, "name": info.get("shortName") or info.get("longName") or ticker,
            "sector": info.get("sector"), "industry": info.get("industry"),
            "currency": info.get("financialCurrency") or info.get("currency") or "USD",
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "market_cap": info.get("marketCap"),
            "pe_trailing": info.get("trailingPE"),
            "pe_forward": info.get("forwardPE"),
            "eps_trailing": info.get("trailingEps"),
            "shares_out": info.get("sharesOutstanding"),
            "beta": info.get("beta"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "dividend_yield": info.get("dividendYield"),
        }
        if not years and not cur.get("market_cap"):
            return None
        return {"current": cur, "years": years}
    except Exception as e:
        print(f"[StockAnalysis] {ticker} financials fail: {e}")
        return None


def get_peer_pe(tickers):
    """對每個代碼抓真實市值與本益比(用於同業比較,不臆測數字)"""
    import yfinance as yf
    out = []
    for tk in tickers[:4]:
        try:
            info = yf.Ticker(tk).info or {}
            out.append({
                "ticker": tk, "name": info.get("shortName") or tk,
                "pe": info.get("trailingPE"), "market_cap": info.get("marketCap"),
                "roe": info.get("returnOnEquity"),
            })
        except Exception as e:
            print(f"[StockAnalysis] peer {tk} fail: {e}")
    return [p for p in out if p.get("pe") or p.get("market_cap")]


def simple_dcf_scenarios(fin, discount_rate=0.09, years=5):
    """
    簡化二階段 DCF 情境敏感度(多/中/空頭三組成長假設),
    用真實FCF與淨負債起點,但成長率/折現率為示意假設。
    回傳 dict 或 None(缺FCF起點時)。
    """
    yrs = fin.get("years") or []
    fcf_hist = [y["fcf"] for y in yrs if y.get("fcf")]
    if not fcf_hist:
        return None
    fcf0 = fcf_hist[-1]
    cur = fin["current"]
    shares = cur.get("shares_out")
    if not shares:
        return None
    debt = (yrs[-1].get("debt") or 0) if yrs else 0
    cash = (yrs[-1].get("cash") or 0) if yrs else 0
    net_debt = (debt or 0) - (cash or 0)

    scenarios = {
        "空頭": {"g1": 0.00, "gT": 0.015},
        "中性": {"g1": 0.05, "gT": 0.025},
        "多頭": {"g1": 0.10, "gT": 0.030},
    }
    out = {}
    for name, sc in scenarios.items():
        g1, gT = sc["g1"], sc["gT"]
        pv = 0.0
        fcf_t = fcf0
        for t in range(1, years + 1):
            fcf_t = fcf_t * (1 + g1)
            pv += fcf_t / ((1 + discount_rate) ** t)
        terminal = fcf_t * (1 + gT) / (discount_rate - gT)
        pv_terminal = terminal / ((1 + discount_rate) ** years)
        equity_value = pv + pv_terminal - net_debt
        value_per_share = equity_value / shares if shares else None
        out[name] = {"growth": g1, "terminal_growth": gT,
                     "value_per_share": value_per_share}
    return {"fcf0": fcf0, "net_debt": net_debt, "discount_rate": discount_rate,
            "years": years, "scenarios": out}


# ---------- AI 質化分析(Claude + web_search) ----------
def generate_analysis(anthropic_client, ticker, fin, peers, dcf):
    """
    用 Claude + web_search 產生質化分析內容。
    財務數字已由我們提供真實數據,要求 AI 只根據提供的數字與搜尋到的公開資訊撰寫,
    不得臆測未提供的具體數字。回傳 dict 或 None。
    """
    cur = fin["current"]
    yrs = fin.get("years") or []
    fin_summary = {
        "公司": cur.get("name"), "產業": cur.get("industry"), "類別": cur.get("sector"),
        "股價": cur.get("price"), "市值": cur.get("market_cap"),
        "本益比_trailing": cur.get("pe_trailing"), "本益比_forward": cur.get("pe_forward"),
        "5年財務": [{"年度": y["year"], "營收": y["revenue"], "淨利": y["net_income"],
                    "自由現金流": y["fcf"], "淨利率%": round(y["net_margin"], 1) if y.get("net_margin") else None,
                    "ROE%": round(y["roe"], 1) if y.get("roe") else None,
                    "負債": y["debt"]} for y in yrs],
        "同業本益比": [{"代碼": p["ticker"], "名稱": p["name"], "PE": p.get("pe")} for p in peers],
    }
    dcf_summary = ""
    if dcf:
        sc = dcf["scenarios"]
        dcf_summary = ("簡化DCF情境試算(每股價值,USD/當地貨幣): " +
                       "; ".join(f"{k}:{v['value_per_share']:.1f}" if v.get("value_per_share") else f"{k}:N/A"
                                for k, v in sc.items()))

    prompt = (
        f"你是一位華爾街資深股票分析師,請對「{ticker}」({cur.get('name')})進行完整分析。\n\n"
        f"以下是真實財務數據,請基於這些數字進行判斷,不要臆測或編造其他具體數字:\n"
        f"{json.dumps(fin_summary, ensure_ascii=False)}\n\n"
        + (f"{dcf_summary}\n\n" if dcf_summary else "")
        + "請上網搜尋這家公司最新的業務動態、產業地位、競爭對手、分析師觀點與相關新聞,"
        "補充質化分析所需的資訊。\n\n"
        "撰寫原則:\n"
        "1. 用簡單易懂的方式解釋,但保有專業分析深度,像在跟同事解釋一樣。\n"
        "2. 只根據我提供的真實數字與你搜尋到的公開資訊撰寫,不要臆測未經證實的具體數字"
        "(例如不要編造精確的市占率百分比,除非你確實搜尋到)。\n"
        "3. 多空論點都要有數據支持,不能空泛。\n"
        "4. 最終結論用『市場評估角度』的中性表述(正向偏多／中性觀望／保守偏空),"
        "不要用第一人稱『我建議』『你應該』這種語氣下投資決定。\n"
        "5. 語氣專業中性,不做投資建議、不保證報酬。\n\n"
        "只回傳以下 JSON 格式(不要有其他文字,不要用 markdown code block):\n"
        "{\n"
        '  "business_overview": "商業模式與主要收入來源,150-200字",\n'
        '  "industry_trends": "產業趨勢,100-150字",\n'
        '  "moat": {\n'
        '    "brand": "品牌影響力評述,40-60字",\n'
        '    "network_effect": "網路效應評述,40-60字",\n'
        '    "switching_cost": "轉換成本評述,40-60字",\n'
        '    "cost_advantage": "成本優勢評述,40-60字",\n'
        '    "patents_tech": "專利或獨家技術評述,40-60字",\n'
        '    "peer_comparison": "與主要競爭對手的護城河比較,80-120字",\n'
        '    "score": 數字(1-10),\n'
        '    "score_rationale": "評分理由,50-80字"\n'
        "  },\n"
        '  "financial_verdict": "根據提供的5年數據,判斷體質正在變強/變弱/持平,並說明理由,120-180字",\n'
        '  "key_risks": ["風險1(30-50字)", "風險2", "風險3", "風險4"],\n'
        '  "valuation": {\n'
        '    "pe_comparison": "本益比與同業比較評述,80-120字",\n'
        '    "dcf_narrative": "對DCF情境試算結果的解讀與意義,80-120字",\n'
        '    "conclusion": "低估/合理/高估的結論與理由,60-100字"\n'
        "  },\n"
        '  "growth_potential": "未來5-10年成長潛力(市場規模/產業成長率/擴張機會/新產品/技術優勢),150-200字",\n'
        '  "bull_case": ["多頭論點1(附數據,40-60字)", "論點2", "論點3"],\n'
        '  "bear_case": ["空頭論點1(附數據,40-60字)", "論點2", "論點3"],\n'
        '  "debate_synthesis": "中性綜合結論,80-120字",\n'
        '  "outlook_short": "短期展望(1年內),60-100字",\n'
        '  "outlook_long": "長期展望(5年以上),60-100字",\n'
        '  "catalysts": ["催化因素1", "催化因素2", "催化因素3"],\n'
        '  "market_lean": "正向偏多 或 中性觀望 或 保守偏空",\n'
        '  "final_summary": "總結陳述,不做投資建議,100-150字"\n'
        "}"
    )
    try:
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            temperature=0.3,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        print(f"[StockAnalysis] API 呼叫失敗: {e}")
        return None
    full_text = "".join(getattr(b, "text", "") for b in message.content)
    raw = re.sub(r"^```(?:json)?|```$", "", full_text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        print(f"[StockAnalysis] 無法解析回傳內容")
        return None
    try:
        return json.loads(m.group(0))
    except Exception as e:
        print(f"[StockAnalysis] JSON 解析失敗: {e}")
        return None


# ---------- 圖表 ----------
def build_financial_chart(fin, out_png=None):
    """5年營收/淨利/FCF趨勢圖(matplotlib),回傳暫存PNG路徑或None"""
    yrs = fin.get("years") or []
    yrs = [y for y in yrs if y.get("revenue") is not None]
    if len(yrs) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from matplotlib import font_manager
        fp = _cjk_font()
        PROP = None
        if fp:
            try:
                PROP = font_manager.FontProperties(fname=fp)
                font_manager.fontManager.addfont(fp)
                matplotlib.rcParams["font.family"] = PROP.get_name()
            except Exception:
                PROP = None
        matplotlib.rcParams["axes.unicode_minus"] = False
        cur = fin["current"]
        unit = 1e8 if cur.get("currency") != "JPY" else 1e10
        labels = [str(y["year"]) for y in yrs]
        rev = [(y["revenue"] or 0) / unit for y in yrs]
        ni = [(y["net_income"] or 0) / unit for y in yrs]
        fcf = [(y["fcf"] or 0) / unit for y in yrs]

        fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), dpi=160)
        x = np.arange(len(labels))
        w = 0.35
        axes[0].bar(x - w/2, rev, w, label="營收", color="#1F8AC0")
        axes[0].bar(x + w/2, ni, w, label="淨利", color="#169B9B")
        axes[0].set_xticks(x); axes[0].set_xticklabels(labels, fontproperties=PROP, fontsize=9)
        axes[0].set_title("營收與淨利趨勢（億）", fontproperties=PROP, fontsize=11, color="#0B2A4A")
        axes[0].legend(prop=PROP, fontsize=8, frameon=False)
        axes[0].spines[["top", "right"]].set_visible(False)

        axes[1].plot(x, fcf, "o-", color="#C9553D", linewidth=2)
        axes[1].set_xticks(x); axes[1].set_xticklabels(labels, fontproperties=PROP, fontsize=9)
        axes[1].set_title("自由現金流趨勢（億）", fontproperties=PROP, fontsize=11, color="#0B2A4A")
        axes[1].spines[["top", "right"]].set_visible(False)
        axes[1].axhline(0, color="#999", linewidth=0.6)
        for i, v in enumerate(fcf):
            axes[1].annotate(f"{v:.0f}", (i, v), fontsize=8, ha="center",
                             va="bottom" if v >= 0 else "top")

        fig.tight_layout(pad=1.0)
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        fig.savefig(f.name, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        return f.name
    except Exception as e:
        print(f"[StockAnalysis] chart fail: {e}")
        return None


# ---------- PDF 報告 ----------
def build_report_pdf(out_path, ticker, fin, peers, dcf, analysis, chart_png=None, today=None):
    """產生多頁 PDF 股票分析報告"""
    from datetime import date as _date
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Image as RLImage, PageBreak)
    from reportlab.lib.styles import ParagraphStyle

    today = today or _date.today()
    fp = _cjk_font()
    FN = "MSung-Light"
    if fp:
        try:
            pdfmetrics.registerFont(TTFont("CJK", fp, subfontIndex=0))
            FN = "CJK"
        except Exception:
            pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))
    else:
        pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))

    NAVY = colors.HexColor("#0B2A4A"); BLUE = colors.HexColor("#1F8AC0")
    GOLD = colors.HexColor("#C9A227"); GRAY = colors.HexColor("#666666")
    GREEN = colors.HexColor("#1B7A3D"); RED = colors.HexColor("#B23A2E")
    LIGHT = colors.HexColor("#F2F6FA")
    W = 18.0 * cm

    st_title = ParagraphStyle("t", fontName=FN, fontSize=20, leading=26, textColor=NAVY)
    st_sub = ParagraphStyle("s", fontName=FN, fontSize=10, leading=14, textColor=GRAY)
    st_h = ParagraphStyle("h", fontName=FN, fontSize=13, leading=17, textColor=NAVY, spaceBefore=10, spaceAfter=4)
    st_h2 = ParagraphStyle("h2", fontName=FN, fontSize=10.5, leading=14, textColor=BLUE, spaceBefore=6, spaceAfter=2)
    st_p = ParagraphStyle("p", fontName=FN, fontSize=10, leading=15.5, textColor=colors.HexColor("#222222"))
    st_small = ParagraphStyle("sm", fontName=FN, fontSize=8, leading=11, textColor=GRAY)
    st_bull = ParagraphStyle("bl", fontName=FN, fontSize=9.5, leading=14.5, leftIndent=8, spaceAfter=3)
    st_bear = ParagraphStyle("br", fontName=FN, fontSize=9.5, leading=14.5, leftIndent=8, spaceAfter=3, textColor=RED)
    st_bullp = ParagraphStyle("blp", fontName=FN, fontSize=9.5, leading=14.5, leftIndent=8, spaceAfter=3, textColor=GREEN)

    def sec(title):
        t = Table([[Paragraph(f"<b>{title}</b>", st_h)]], colWidths=[W])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.2, BLUE),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        return t

    def box(flowables, border=BLUE):
        t = Table([[flowables]], colWidths=[W])
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, border),
                               ("TOPPADDING", (0, 0), (-1, -1), 8),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
        return t

    def _n(v, unit="", nd=1):
        if v is None:
            return "-"
        if abs(v) >= 1e8:
            return f"{v/1e8:,.{nd}f}億{unit}"
        return f"{v:,.{nd}f}{unit}"

    cur = fin["current"]
    a = analysis
    doc = SimpleDocTemplate(out_path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.3*cm, bottomMargin=1.2*cm)
    el = []

    # ===== 封面資訊 =====
    el.append(Paragraph(f"{cur.get('name', ticker)}（{ticker.upper()}）", st_title))
    el.append(Paragraph(f"個股分析報告　{cur.get('sector') or ''} {('· ' + cur.get('industry')) if cur.get('industry') else ''}　{today:%Y/%m/%d}", st_sub))
    el.append(Spacer(1, 0.3*cm))

    snap_rows = [
        ["股價", "市值", "本益比(TTM)", "本益比(預估)", "殖利率"],
        [_n(cur.get("price"), unit=cur.get("currency", "")[:0] or "", nd=2) if cur.get("price") else "-",
         _n(cur.get("market_cap")),
         f"{cur['pe_trailing']:.1f}x" if cur.get("pe_trailing") else "-",
         f"{cur['pe_forward']:.1f}x" if cur.get("pe_forward") else "-",
         f"{cur['dividend_yield']*100:.2f}%" if cur.get("dividend_yield") else "-"],
    ]
    t0 = Table(snap_rows, colWidths=[W/5]*5)
    t0.setStyle(TableStyle([("FONTNAME", (0,0), (-1,-1), FN), ("FONTSIZE", (0,0), (-1,0), 9),
                            ("FONTSIZE", (0,1), (-1,1), 13),
                            ("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                            ("BACKGROUND", (0,1), (-1,1), LIGHT), ("TEXTCOLOR", (0,1), (-1,1), NAVY),
                            ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                            ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
    el.append(t0)
    el.append(Spacer(1, 0.3*cm))

    # ===== 1. 商業模式與產業趨勢 =====
    el.append(sec("商業模式與收入來源"))
    el.append(Paragraph(a.get("business_overview", ""), st_p))
    el.append(sec("產業趨勢"))
    el.append(Paragraph(a.get("industry_trends", ""), st_p))

    # ===== 2. 財務健康度 =====
    el.append(sec("財務健康狀況（近5年）"))
    if chart_png:
        try:
            from PIL import Image as _PIL
            iw, ih = _PIL.open(chart_png).size
            w_ = W; h_ = w_ * ih / iw
            el.append(RLImage(chart_png, width=w_, height=h_))
        except Exception as e:
            print(f"[StockAnalysis] chart embed fail: {e}")
    yrs = fin.get("years") or []
    if yrs:
        rows_f = [["年度", "營收", "淨利", "淨利率", "ROE", "自由現金流"]]
        for y in yrs:
            rows_f.append([
                str(y["year"]), _n(y.get("revenue")), _n(y.get("net_income")),
                f"{y['net_margin']:.1f}%" if y.get("net_margin") else "-",
                f"{y['roe']:.1f}%" if y.get("roe") else "-",
                _n(y.get("fcf")),
            ])
        tf = Table(rows_f, colWidths=[W*0.12, W*0.22, W*0.18, W*0.15, W*0.13, W*0.20])
        tf.setStyle(TableStyle([("FONTNAME", (0,0), (-1,-1), FN), ("FONTSIZE", (0,0), (-1,-1), 8.5),
                                ("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                                ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#D9D9D9")),
                                ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                                ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
        el.append(Spacer(1, 0.15*cm)); el.append(tf)
    el.append(Spacer(1, 0.15*cm))
    el.append(box(Paragraph("<b>體質判斷：</b>" + a.get("financial_verdict", ""), st_p)))

    el.append(sec("關鍵風險"))
    for r in (a.get("key_risks") or []):
        el.append(Paragraph("● " + r, st_bull))

    el.append(PageBreak())

    # ===== 3. 護城河 =====
    el.append(sec(f"競爭護城河評分：{a.get('moat', {}).get('score', '-')} / 10"))
    moat = a.get("moat", {})
    moat_rows = [["構面", "評述"],
                 ["品牌影響力", moat.get("brand", "-")],
                 ["網路效應", moat.get("network_effect", "-")],
                 ["轉換成本", moat.get("switching_cost", "-")],
                 ["成本優勢", moat.get("cost_advantage", "-")],
                 ["專利/獨家技術", moat.get("patents_tech", "-")]]
    tm = Table(moat_rows, colWidths=[W*0.18, W*0.82])
    tm.setStyle(TableStyle([("FONTNAME", (0,0), (-1,-1), FN), ("FONTSIZE", (0,0), (-1,-1), 9),
                            ("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                            ("BACKGROUND", (0,1), (0,-1), LIGHT),
                            ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#D9D9D9")),
                            ("VALIGN", (0,0), (-1,-1), "TOP"),
                            ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6),
                            ("LEFTPADDING", (0,0), (-1,-1), 6)]))
    el.append(tm)
    el.append(Spacer(1, 0.15*cm))
    el.append(Paragraph("<b>評分理由：</b>" + moat.get("score_rationale", ""), st_p))
    el.append(Spacer(1, 0.1*cm))
    el.append(Paragraph("<b>同業比較：</b>" + moat.get("peer_comparison", ""), st_p))

    # ===== 4. 估值分析 =====
    el.append(sec("估值分析"))
    if peers:
        rows_p = [["公司", "代碼", "本益比(TTM)", "市值"]]
        rows_p.append([cur.get("name", ticker), ticker.upper(),
                       f"{cur['pe_trailing']:.1f}x" if cur.get("pe_trailing") else "-", _n(cur.get("market_cap"))])
        for p in peers:
            rows_p.append([p.get("name", p["ticker"]), p["ticker"],
                           f"{p['pe']:.1f}x" if p.get("pe") else "-", _n(p.get("market_cap"))])
        tp = Table(rows_p, colWidths=[W*0.34, W*0.14, W*0.24, W*0.28])
        style_p = [("FONTNAME", (0,0), (-1,-1), FN), ("FONTSIZE", (0,0), (-1,-1), 9),
                   ("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                   ("BACKGROUND", (0,1), (-1,1), LIGHT),
                   ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#D9D9D9")),
                   ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                   ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]
        tp.setStyle(TableStyle(style_p))
        el.append(tp)
        el.append(Spacer(1, 0.12*cm))
    el.append(Paragraph(a.get("valuation", {}).get("pe_comparison", ""), st_p))

    if dcf:
        el.append(Spacer(1, 0.2*cm))
        el.append(Paragraph("<b>簡化DCF情境敏感度</b>（示意模型，非專業估值，僅供參考）", st_h2))
        sc = dcf["scenarios"]
        rows_d = [["情境", "5年成長假設", "永續成長假設", "估算每股價值"]]
        for name in ("空頭", "中性", "多頭"):
            v = sc.get(name, {})
            vps = v.get("value_per_share")
            rows_d.append([name, f"{v.get('growth', 0)*100:.0f}%", f"{v.get('terminal_growth', 0)*100:.1f}%",
                           f"{vps:.1f}" if vps else "-"])
        td = Table(rows_d, colWidths=[W*0.2, W*0.27, W*0.27, W*0.26])
        td.setStyle(TableStyle([("FONTNAME", (0,0), (-1,-1), FN), ("FONTSIZE", (0,0), (-1,-1), 9),
                                ("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                                ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#D9D9D9")),
                                ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                                ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
        el.append(td)
        el.append(Spacer(1, 0.12*cm))
        el.append(Paragraph(a.get("valuation", {}).get("dcf_narrative", ""), st_p))
        _neg_note = ("起點自由現金流為負值時，模型估算之每股價值可能呈現負數，"
                     "僅反映「若現金流未能轉正」的極端情境，不代表實際股價可能為負；"
                     "" ) if dcf.get("fcf0", 0) < 0 else ""
        el.append(Paragraph(f"※ 假設折現率 {dcf['discount_rate']*100:.0f}%，"
                            f"以最近一期自由現金流（{_n(dcf['fcf0'])}）為起點推算，"
                            + _neg_note +
                            "實際價值受未來營運、利率環境與市場情緒影響，本表僅為情境示意，非專業估值模型。", st_small))
    el.append(Spacer(1, 0.15*cm))
    el.append(box(Paragraph("<b>估值結論：</b>" + a.get("valuation", {}).get("conclusion", ""), st_p), border=GOLD))

    el.append(PageBreak())

    # ===== 5. 成長潛力 =====
    el.append(sec("未來成長潛力（5-10年展望）"))
    el.append(Paragraph(a.get("growth_potential", ""), st_p))

    # ===== 6. 多空辯論 =====
    el.append(sec("多空辯論"))
    el.append(Paragraph("🟢 多頭觀點", ParagraphStyle("bh", fontName=FN, fontSize=10, textColor=GREEN, spaceBefore=4)))
    for pt in (a.get("bull_case") or []):
        el.append(Paragraph("▲ " + pt, st_bullp))
    el.append(Paragraph("🔴 空頭觀點", ParagraphStyle("brh", fontName=FN, fontSize=10, textColor=RED, spaceBefore=8)))
    for pt in (a.get("bear_case") or []):
        el.append(Paragraph("▼ " + pt, st_bear))
    el.append(Spacer(1, 0.15*cm))
    el.append(box(Paragraph("<b>綜合結論：</b>" + a.get("debate_synthesis", ""), st_p)))

    # ===== 7. 投資評估 =====
    el.append(sec("投資評估總結"))
    lean = a.get("market_lean", "中性觀望")
    lean_color = GREEN if "多" in lean else (RED if "空" in lean else GOLD)
    lean_tbl = Table([[Paragraph(f"<b>市場評估角度：{lean}</b>", ParagraphStyle("lt", fontName=FN, fontSize=13, alignment=1, textColor=colors.white))]], colWidths=[W])
    lean_tbl.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), lean_color),
                                  ("TOPPADDING", (0,0), (-1,-1), 8), ("BOTTOMPADDING", (0,0), (-1,-1), 8)]))
    el.append(lean_tbl)
    el.append(Spacer(1, 0.2*cm))
    el.append(Paragraph("<b>短期展望（1年內）：</b>" + a.get("outlook_short", ""), st_p))
    el.append(Spacer(1, 0.08*cm))
    el.append(Paragraph("<b>長期展望（5年以上）：</b>" + a.get("outlook_long", ""), st_p))
    el.append(Spacer(1, 0.15*cm))
    el.append(Paragraph("<b>關鍵催化因素</b>", st_h2))
    for c in (a.get("catalysts") or []):
        el.append(Paragraph("● " + c, st_bull))
    el.append(Spacer(1, 0.15*cm))
    el.append(box(Paragraph(a.get("final_summary", ""), st_p)))

    el.append(Spacer(1, 0.4*cm))
    el.append(Paragraph(f"（資料來源：Yahoo Finance 財務數據、公開新聞與市場資訊，{today:%Y/%m/%d}）", st_small))
    el.append(Paragraph(ANALYST_DISCLAIMER, st_small))

    def _footer(canv, doc_):
        canv.saveState()
        canv.setFont(FN, 8)
        canv.setFillColor(RED)
        canv.drawString(1.5*cm, 0.9*cm, "僅限內部研究與教育訓練使用，非投資建議")
        canv.setFillColor(GRAY)
        canv.drawRightString(A4[0]-1.5*cm, 0.9*cm, f"{ticker.upper()} 分析報告 · {today:%Y/%m/%d}")
        canv.restoreState()

    doc.build(el, onFirstPage=_footer, onLaterPages=_footer)
    return out_path


def build_summary_text(ticker, fin, analysis, dcf=None):
    """LINE 用的精簡文字摘要"""
    cur = fin["current"]
    a = analysis
    lines = [f"📈 {cur.get('name', ticker)}（{ticker.upper()}）分析摘要", ""]
    lines.append(f"市值 {cur['market_cap']/1e8:.0f}億" if cur.get("market_cap") else "市值 -")
    if cur.get("pe_trailing"):
        lines.append(f"本益比(TTM) {cur['pe_trailing']:.1f}x")
    lines.append("")
    lines.append("【護城河評分】" + str(a.get("moat", {}).get("score", "-")) + " / 10")
    lines.append(a.get("moat", {}).get("score_rationale", "")[:80])
    lines.append("")
    lines.append("【體質判斷】")
    lines.append(a.get("financial_verdict", "")[:150])
    lines.append("")
    lines.append("【估值結論】")
    lines.append(a.get("valuation", {}).get("conclusion", "")[:100])
    lines.append("")
    lines.append(f"【市場評估角度】{a.get('market_lean', '-')}")
    lines.append(a.get("final_summary", "")[:150])
    lines.append("")
    lines.append("（完整報告含財務圖表、護城河分析、DCF情境、多空辯論，見PDF）")
    lines.append(ANALYST_DISCLAIMER)
    return "\n".join(lines)
