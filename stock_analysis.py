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
        # 最新一季實績(供「Q2 實績」這類敘事用)
        latest_q = {}
        try:
            qf = t.quarterly_financials
            if qf is not None and not qf.empty:
                cols_q = list(qf.columns)
                c0 = cols_q[0]
                rev_q = pick(qf, ["Total Revenue", "Operating Revenue"], c0)
                ni_q = pick(qf, ["Net Income", "Net Income Common Stockholders"], c0)
                gp_q = pick(qf, ["Gross Profit"], c0)
                op_q = pick(qf, ["Operating Income"], c0)
                rev_q_yoy = None
                if len(cols_q) >= 5:
                    rev_prev = pick(qf, ["Total Revenue", "Operating Revenue"], cols_q[4])
                    if rev_prev:
                        rev_q_yoy = (rev_q / rev_prev - 1) * 100 if rev_q else None
                latest_q = {"period": f"{c0.year}Q{(c0.month-1)//3+1}", "revenue": rev_q, "net_income": ni_q,
                            "gross_margin": (gp_q / rev_q * 100) if (gp_q and rev_q) else None,
                            "op_margin": (op_q / rev_q * 100) if (op_q and rev_q) else None,
                            "revenue_yoy": rev_q_yoy}
        except Exception as e:
            print(f"[StockAnalysis] quarterly fail: {e}")
        # 年初至今漲幅、52週位置
        try:
            hist = t.history(period="ytd")
            if hist is not None and len(hist) >= 2:
                cur["ytd_pct"] = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
        except Exception:
            pass
        cur["gross_margin"] = (info.get("grossMargins") or 0) * 100 or None
        cur["op_margin"] = (info.get("operatingMargins") or 0) * 100 or None
        cur["target_mean"] = info.get("targetMeanPrice")
        cur["analyst_count"] = info.get("numberOfAnalystOpinions")
        cur["recommendation"] = info.get("recommendationKey")
        if not years and not cur.get("market_cap"):
            return None
        return {"current": cur, "years": years, "latest_q": latest_q}
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
            ytd = None
            try:
                h = yf.Ticker(tk).history(period="ytd")
                if h is not None and len(h) >= 2:
                    ytd = (h["Close"].iloc[-1] / h["Close"].iloc[0] - 1) * 100
            except Exception:
                pass
            out.append({
                "ticker": tk, "name": info.get("shortName") or tk,
                "pe": info.get("trailingPE"), "pe_fwd": info.get("forwardPE"),
                "market_cap": info.get("marketCap"), "roe": info.get("returnOnEquity"),
                "gross_margin": (info.get("grossMargins") or 0) * 100 or None,
                "revenue_ttm": info.get("totalRevenue"), "ytd_pct": ytd,
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
    用 Claude + web_search 產生深度分析(對齊 sell-side 深度研究報告的敘事密度)。
    財務數字由我們提供真實數據;AI 負責解讀、找近期事件、寫有觀點的敘事。
    回傳 dict 或 None(失敗原因存於 generate_analysis.last_error)。
    """
    generate_analysis.last_error = ""
    cur = fin["current"]
    yrs = fin.get("years") or []
    lq = fin.get("latest_q") or {}

    def _r(v, nd=1):
        return round(v, nd) if isinstance(v, (int, float)) else None

    fin_summary = {
        "公司": cur.get("name"), "產業": cur.get("industry"), "類別": cur.get("sector"),
        "股價": cur.get("price"), "市值": cur.get("market_cap"), "年初至今漲幅%": _r(cur.get("ytd_pct")),
        "52週高": cur.get("52w_high"), "52週低": cur.get("52w_low"),
        "本益比_trailing": _r(cur.get("pe_trailing")), "本益比_forward": _r(cur.get("pe_forward")),
        "毛利率%": _r(cur.get("gross_margin")), "營業利益率%": _r(cur.get("op_margin")),
        "分析師平均目標價": cur.get("target_mean"), "分析師人數": cur.get("analyst_count"),
        "分析師共識": cur.get("recommendation"),
        "最新一季": {"期間": lq.get("period"), "營收": lq.get("revenue"), "營收年增%": _r(lq.get("revenue_yoy")),
                    "淨利": lq.get("net_income"), "毛利率%": _r(lq.get("gross_margin")),
                    "營益率%": _r(lq.get("op_margin"))} if lq else None,
        "近5年年度": [{"年度": y["year"], "營收": y["revenue"], "淨利": y["net_income"],
                      "自由現金流": y["fcf"], "淨利率%": _r(y.get("net_margin")),
                      "ROE%": _r(y.get("roe")), "總負債": y["debt"], "現金": y.get("cash")} for y in yrs],
        "同業": [{"代碼": p["ticker"], "名稱": p["name"], "PE": _r(p.get("pe")), "預估PE": _r(p.get("pe_fwd")),
                 "毛利率%": _r(p.get("gross_margin")), "年初至今%": _r(p.get("ytd_pct")),
                 "市值": p.get("market_cap"), "營收TTM": p.get("revenue_ttm")} for p in peers],
    }
    dcf_summary = ""
    if dcf:
        sc = dcf["scenarios"]
        dcf_summary = ("系統以最新FCF為起點、折現率9%做的簡化DCF每股價值: " +
                       "; ".join(f"{k}(5年成長{v['growth']*100:.0f}%):{v['value_per_share']:.1f}"
                                 if v.get("value_per_share") is not None else f"{k}:N/A"
                                 for k, v in sc.items()) +
                       "(僅供你參考,你可以在估值敘事中引用、也可以指出它的侷限)")

    prompt = (
        f"你是一位華爾街資深股票分析師,要為「{ticker}」({cur.get('name')})寫一份深度研究報告,"
        "讀者是銀行的理財專員與投資輔銷,他們要拿這份報告跟客戶討論。\n\n"
        "=== 真實財務數據(由系統提供,請以此為準,不要改動或臆測其他具體數字) ===\n"
        f"{json.dumps(fin_summary, ensure_ascii=False)}\n"
        + (f"{dcf_summary}\n" if dcf_summary else "")
        + "\n=== 你的任務 ===\n"
        "請上網搜尋這家公司最近三個月的重大事件、最新一季財報重點、管理層說法、分析師觀點、"
        "競爭對手動態與產業趨勢,然後寫出一份有觀點、有敘事、數字密集的深度報告。\n\n"
        "=== 寫作風格(非常重要) ===\n"
        "1. 像頂尖 sell-side 分析師寫給客戶的報告:有主張、有轉折、敢下判斷,不是資料堆砌。\n"
        "2. 每一段都要有具體數字支撐(營收、年增率、毛利率、市占、目標價…),數字來自我提供的資料或你搜尋到的公開資訊;"
        "沒有依據的具體數字寧可不寫。\n"
        "3. 適度用生活化比喻幫助理解(例如把 IDM 比喻成『自己研發菜單也自己開廚房的餐廳』),但不要濫用。\n"
        "4. 多空辯論要寫成兩位分析師的第一人稱發言,語氣像真人在辯論,各自引用數據互相反駁,"
        "每方至少 150 字,不要條列。\n"
        "5. 每個評分都要給分數和理由;和主要競爭對手比較時也給對手分數。\n"
        "6. 繁體中文,專業但口語,句子不要太長。\n"
        "7. 最後給明確評級(買入/持有/避免,可加修飾如『逢低分批買入』),並分別對「已持有者」「未持有者」「不適合的對象」給具體做法。"
        "這是研究觀點,報告本身會附完整免責聲明。\n\n"
        "=== 只回傳以下 JSON(不要有其他文字、不要用 markdown code block;所有字串用繁體中文) ===\n"
        "{\n"
        '  "one_line": "一句話總結(80-120字,點出目前最核心的矛盾或機會,要有觀點)",\n'
        '  "recent_changes": [{"item":"面向","before":"三個月前狀況","now":"現況","direction":"▲/▼/△ 一句話"}, ...共4-6項],\n'
        '  "business": {\n'
        '    "overview": "商業模式與收入來源,150-250字,可用比喻",\n'
        '    "segments": [{"name":"事業群","desc":"做什麼","latest":"最新實績(含數字)"}, ...共2-5個],\n'
        '    "ownership_note": "股東結構或其他值得注意的背景,0-120字,沒有就空字串"\n'
        "  },\n"
        '  "moat": {\n'
        '    "dims": [{"name":"品牌影響力","comment":"評述(60-100字,含數字)","score":數字1-10},\n'
        '             {"name":"網路效應",...}, {"name":"轉換成本",...}, {"name":"成本優勢",...}, {"name":"專利/獨家技術",...}],\n'
        '    "score": 總分數字1-10, "verdict": "總評(80-150字)",\n'
        '    "peers": [{"name":"競爭對手","score":數字,"note":"一句話"}, ...共2-4家]\n'
        "  },\n"
        '  "industry": [{"title":"趨勢標題","text":"說明(60-120字含數字)"}, ...共3-5項],\n'
        '  "financials": {\n'
        '    "year_notes": [{"year":年度,"note":"該年一句話註解(20-40字)"}, ...對應我提供的每個年度],\n'
        '    "metrics": [{"name":"營收成長","verdict":"轉強/轉弱/持平/改善中","text":"判讀(60-120字含數字)"},\n'
        '                {"name":"淨利趨勢",...},{"name":"自由現金流",...},{"name":"利潤率",...},{"name":"負債水準",...},{"name":"ROE",...}],\n'
        '    "overall": "體質總判定(100-180字,要有結論句,例如「明確變強且斜率加速」)"\n'
        "  },\n"
        '  "valuation": {\n'
        '    "table": [{"metric":"指標","self":"本公司數值","peer1":"對手1數值","peer2":"對手2數值","comment":"評語"}, ...共4-6列],\n'
        '    "peer_names": ["對手1名稱","對手2名稱"],\n'
        '    "dcf_reasoning": "DCF思路(150-250字):關鍵假設鏈 → 三情境合理股價區間(多/中/空各給區間)與現價的相對位置,可引用系統試算但要指出侷限",\n'
        '    "scenarios": {"bull":{"range":"股價區間","cond":"條件"},"base":{"range":"...","cond":"..."},"bear":{"range":"...","cond":"..."}},\n'
        '    "conclusion": "估值結論(100-180字):低估/合理/高估,引用分析師目標價與共識評級"\n'
        "  },\n"
        '  "growth": {\n'
        '    "items": [{"title":"市場規模/產品路線/新產品/擴張機會等","text":"說明(60-120字含數字)"}, ...共3-5項],\n'
        '    "scenarios_5_10y": {"bull":"樂觀情境描述(含營收/市值量級)","base":"基本情境","bear":"悲觀情境"}\n'
        "  },\n"
        '  "debate": {\n'
        '    "bull": "多頭分析師第一人稱發言(150-300字,引用數據)",\n'
        '    "bear": "空頭分析師第一人稱發言(150-300字,引用數據,要講出「沒人想聽的」)",\n'
        '    "neutral": "中性結論(80-150字,點出真正的矛盾是什麼)"\n'
        "  },\n"
        '  "final": {\n'
        '    "short_term": "短期展望1年內(100-180字,含催化與逆風,給股價區間預估)",\n'
        '    "long_term": "長期展望5年以上(80-150字,點出單一最重要的觀察指標)",\n'
        '    "catalysts": ["催化因素(含數字或時點)", ...共4-6項],\n'
        '    "risks": ["主要風險", ...共4-6項],\n'
        '    "rating": "買入 / 持有 / 避免(可加修飾,例如「持有 → 逢低分批買入」)",\n'
        '    "holders": "已持有者怎麼做(40-80字)",\n'
        '    "non_holders": "未持有者怎麼做(40-80字,可給區間)",\n'
        '    "unsuitable": "不適合的對象(30-60字)"\n'
        "  }\n"
        "}"
    )

    def _call(use_search, max_tokens):
        kwargs = dict(model="claude-sonnet-4-6", max_tokens=max_tokens, temperature=0.4,
                      messages=[{"role": "user", "content": prompt}])
        if use_search:
            kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
        return anthropic_client.messages.create(**kwargs)

    def _parse(message):
        full_text = "".join(getattr(b, "text", "") for b in message.content)
        raw = re.sub(r"^```(?:json)?|```$", "", full_text.strip(), flags=re.M).strip()
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None, f"回覆中找不到JSON(stop={getattr(message, 'stop_reason', '?')}, 長度{len(full_text)})"
        try:
            return json.loads(m.group(0)), ""
        except Exception as e:
            return None, f"JSON解析失敗:{str(e)[:80]}(stop={getattr(message, 'stop_reason', '?')})"

    last_err = ""
    try:
        got, err = _parse(_call(True, 16000))
        if got:
            return got
        last_err = err
        print(f"[StockAnalysis] 第一次(含搜尋)失敗: {err}")
    except Exception as e:
        last_err = f"{type(e).__name__}: {str(e)[:120]}"
        print(f"[StockAnalysis] API 呼叫失敗(含搜尋): {e}")
    try:
        got, err = _parse(_call(False, 16000))
        if got:
            got["_note"] = "本次未能使用即時搜尋,質化內容以財務數據與既有知識為主"
            return got
        last_err = err
    except Exception as e:
        last_err = f"{type(e).__name__}: {str(e)[:120]}"
    generate_analysis.last_error = last_err
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
def _g(d, *keys, default=""):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def build_report_pdf(out_path, ticker, fin, peers, dcf, analysis, chart_png=None, today=None):
    """深度研究報告 PDF(多頁,對齊 sell-side 報告層次)"""
    from datetime import date as _date
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Image as RLImage, PageBreak, KeepTogether)
    from reportlab.lib.styles import ParagraphStyle

    today = today or _date.today()
    fp = _cjk_font()
    FN = "MSung-Light"
    if fp:
        try:
            pdfmetrics.registerFont(TTFont("CJK", fp, subfontIndex=0)); FN = "CJK"
        except Exception:
            pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))
    else:
        pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))

    NAVY = colors.HexColor("#0B2A4A"); BLUE = colors.HexColor("#1F8AC0")
    GOLD = colors.HexColor("#C9A227"); GRAY = colors.HexColor("#666666")
    GREEN = colors.HexColor("#1B7A3D"); RED = colors.HexColor("#B23A2E")
    LIGHT = colors.HexColor("#F2F6FA"); CREAM = colors.HexColor("#FFF9E6")
    W = 18.0 * cm

    st_title = ParagraphStyle("t", fontName=FN, fontSize=19, leading=25, textColor=NAVY)
    st_sub = ParagraphStyle("s", fontName=FN, fontSize=9.5, leading=13, textColor=GRAY)
    st_h = ParagraphStyle("h", fontName=FN, fontSize=13, leading=17, textColor=NAVY, spaceBefore=10, spaceAfter=4)
    st_h2 = ParagraphStyle("h2", fontName=FN, fontSize=10.5, leading=14, textColor=BLUE, spaceBefore=6, spaceAfter=2)
    st_p = ParagraphStyle("p", fontName=FN, fontSize=10, leading=15.5, textColor=colors.HexColor("#222222"))
    st_cell = ParagraphStyle("c", fontName=FN, fontSize=8.5, leading=12, textColor=colors.HexColor("#222222"))
    st_cellb = ParagraphStyle("cb", fontName=FN, fontSize=8.5, leading=12, textColor=NAVY)
    st_small = ParagraphStyle("sm", fontName=FN, fontSize=8, leading=11, textColor=GRAY)
    st_bul = ParagraphStyle("bl", fontName=FN, fontSize=9.5, leading=14.5, leftIndent=8, spaceAfter=3)
    st_quote = ParagraphStyle("q", fontName=FN, fontSize=9.8, leading=15.5, textColor=colors.HexColor("#222222"))
    st_lead = ParagraphStyle("ld", fontName=FN, fontSize=11, leading=17, textColor=NAVY)

    def sec(title):
        t = Table([[Paragraph(f"<b>{title}</b>", st_h)]], colWidths=[W])
        t.setStyle(TableStyle([("LINEBELOW", (0,0), (-1,-1), 1.2, BLUE),
                               ("LEFTPADDING", (0,0), (-1,-1), 0), ("BOTTOMPADDING", (0,0), (-1,-1), 2)]))
        return t

    def box(flow, border=BLUE, bg=None):
        t = Table([[flow]], colWidths=[W])
        st = [("BOX", (0,0), (-1,-1), 0.9, border), ("TOPPADDING", (0,0), (-1,-1), 8),
              ("BOTTOMPADDING", (0,0), (-1,-1), 8), ("LEFTPADDING", (0,0), (-1,-1), 9),
              ("RIGHTPADDING", (0,0), (-1,-1), 9)]
        if bg:
            st.append(("BACKGROUND", (0,0), (-1,-1), bg))
        t.setStyle(TableStyle(st))
        return t

    def grid(rows, widths, head=True, font=8.5, zebra=True):
        data = [[Paragraph(str(c), st_cellb if (head and r == 0) else st_cell) for c in row] for r, row in enumerate(rows)]
        t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
        st = [("FONTNAME", (0,0), (-1,-1), FN), ("VALIGN", (0,0), (-1,-1), "TOP"),
              ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#D9D9D9")),
              ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
              ("LEFTPADDING", (0,0), (-1,-1), 5), ("RIGHTPADDING", (0,0), (-1,-1), 5)]
        if head:
            st += [("BACKGROUND", (0,0), (-1,0), LIGHT)]
        if zebra:
            for r in range(1 if head else 0, len(rows)):
                if r % 2 == 0:
                    st.append(("BACKGROUND", (0,r), (-1,r), colors.HexColor("#FAFBFC")))
        t.setStyle(TableStyle(st))
        return t

    def _n(v, nd=1):
        if v is None:
            return "-"
        if abs(v) >= 1e8:
            return f"{v/1e8:,.{nd}f}億"
        return f"{v:,.{nd}f}"

    cur = fin["current"]; a = analysis
    doc = SimpleDocTemplate(out_path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.3*cm, bottomMargin=1.3*cm)
    el = []

    # ===== 封面 =====
    el.append(Paragraph(f"{cur.get('name', ticker)}（{ticker.upper()}）深度研究報告", st_title))
    meta = f"華爾街觀點 × 12–24 個月展望｜{today:%Y/%m/%d}"
    if cur.get("price"):
        meta += f"｜現價 {cur['price']:.2f}"
    if cur.get("ytd_pct") is not None:
        meta += f"｜今年迄今 {cur['ytd_pct']:+.0f}%"
    if cur.get("52w_high") and cur.get("price"):
        meta += f"｜較52週高點 {(cur['price']/cur['52w_high']-1)*100:+.0f}%"
    el.append(Paragraph(meta, st_sub))
    el.append(Spacer(1, 0.25*cm))
    if a.get("one_line"):
        el.append(box(Paragraph(f"<b>一句話總結：</b>{a['one_line']}", st_lead), border=GOLD, bg=CREAM))
    el.append(Spacer(1, 0.25*cm))

    snap = [["股價", "市值", "本益比(TTM)", "本益比(預估)", "毛利率", "分析師目標價"],
            [f"{cur['price']:.2f}" if cur.get("price") else "-", _n(cur.get("market_cap"), 0),
             f"{cur['pe_trailing']:.1f}x" if cur.get("pe_trailing") else "-",
             f"{cur['pe_forward']:.1f}x" if cur.get("pe_forward") else "-",
             f"{cur['gross_margin']:.1f}%" if cur.get("gross_margin") else "-",
             (f"{cur['target_mean']:.0f}（{cur.get('analyst_count') or '?'}位）" if cur.get("target_mean") else "-")]]
    t0 = Table(snap, colWidths=[W/6]*6)
    t0.setStyle(TableStyle([("FONTNAME", (0,0), (-1,-1), FN), ("FONTSIZE", (0,0), (-1,0), 8.5),
                            ("FONTSIZE", (0,1), (-1,1), 11.5),
                            ("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                            ("BACKGROUND", (0,1), (-1,1), LIGHT), ("TEXTCOLOR", (0,1), (-1,1), NAVY),
                            ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                            ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
    el.append(t0)

    # ===== 〇 近期變化 =====
    rc = a.get("recent_changes") or []
    if rc:
        el.append(sec("〇、最近三個月發生了什麼"))
        rows = [["項目", "三個月前", "現況", "方向"]] + [[x.get("item",""), x.get("before",""), x.get("now",""), x.get("direction","")] for x in rc]
        el.append(grid(rows, [W*0.16, W*0.30, W*0.36, W*0.18]))

    # ===== 一 商業模式 =====
    el.append(sec("一、商業模式與收入來源"))
    el.append(Paragraph(_g(a, "business", "overview"), st_p))
    segs = _g(a, "business", "segments", default=[]) or []
    if segs:
        el.append(Spacer(1, 0.15*cm))
        rows = [["事業群", "內容", "最新實績"]] + [[x.get("name",""), x.get("desc",""), x.get("latest","")] for x in segs]
        el.append(grid(rows, [W*0.22, W*0.42, W*0.36]))
    own = _g(a, "business", "ownership_note")
    if own:
        el.append(Spacer(1, 0.12*cm)); el.append(Paragraph(own, st_p))

    # ===== 二 護城河 =====
    moat = a.get("moat") or {}
    el.append(sec(f"二、競爭護城河評估（總評 {moat.get('score','-')}/10）"))
    dims = moat.get("dims") or []
    if dims:
        rows = [["護城河來源", "評估", "分數"]] + [[x.get("name",""), x.get("comment",""), f"{x.get('score','-')}/10"] for x in dims]
        el.append(grid(rows, [W*0.18, W*0.70, W*0.12]))
    if moat.get("verdict"):
        el.append(Spacer(1, 0.12*cm))
        pr = moat.get("peers") or []
        peer_txt = ("　對比：" + "、".join(f"{x.get('name')}（{x.get('score','-')}/10，{x.get('note','')}）" for x in pr)) if pr else ""
        el.append(box(Paragraph(f"<b>護城河總評：{moat.get('score','-')}/10</b>　{moat['verdict']}{peer_txt}", st_p)))

    # ===== 三 產業趨勢 =====
    ind = a.get("industry") or []
    if ind:
        el.append(sec("三、產業趨勢"))
        for x in ind:
            el.append(Paragraph(f"● <b>{x.get('title','')}：</b>{x.get('text','')}", st_bul))

    el.append(PageBreak())

    # ===== 四 財務 =====
    el.append(sec("四、過去 5 年財務體質分析"))
    if chart_png:
        try:
            from PIL import Image as _PIL
            iw, ih = _PIL.open(chart_png).size
            el.append(RLImage(chart_png, width=W, height=W*ih/iw))
        except Exception as e:
            print(f"[StockAnalysis] chart embed fail: {e}")
    yrs = fin.get("years") or []
    notes = {str(x.get("year")): x.get("note","") for x in (_g(a, "financials", "year_notes", default=[]) or [])}
    if yrs:
        rows = [["年度", "營收", "淨利", "淨利率", "ROE", "自由現金流", "備註"]]
        for y in yrs:
            rows.append([str(y["year"]), _n(y.get("revenue")), _n(y.get("net_income")),
                         f"{y['net_margin']:.1f}%" if y.get("net_margin") is not None else "-",
                         f"{y['roe']:.1f}%" if y.get("roe") is not None else "-",
                         _n(y.get("fcf")), notes.get(str(y["year"]), "")])
        el.append(Spacer(1, 0.12*cm))
        el.append(grid(rows, [W*0.08, W*0.14, W*0.13, W*0.10, W*0.10, W*0.14, W*0.31]))
    lq = fin.get("latest_q") or {}
    if lq.get("revenue"):
        el.append(Spacer(1, 0.1*cm))
        el.append(Paragraph(f"最新一季（{lq.get('period')}）：營收 {_n(lq['revenue'])}"
                            + (f"、年增 {lq['revenue_yoy']:+.0f}%" if lq.get("revenue_yoy") is not None else "")
                            + (f"、毛利率 {lq['gross_margin']:.1f}%" if lq.get("gross_margin") else "")
                            + (f"、營益率 {lq['op_margin']:.1f}%" if lq.get("op_margin") else ""), st_p))
    mets = _g(a, "financials", "metrics", default=[]) or []
    if mets:
        el.append(Paragraph("六大指標判讀", st_h2))
        for x in mets:
            v = x.get("verdict", "")
            col = "#1B7A3D" if ("強" in v or "改善" in v) else ("#B23A2E" if "弱" in v else "#555555")
            el.append(Paragraph(f"● <b>{x.get('name','')}</b>：<font color='{col}'><b>{v}</b></font>　{x.get('text','')}", st_bul))
    if _g(a, "financials", "overall"):
        el.append(Spacer(1, 0.12*cm))
        el.append(box(Paragraph(f"<b>判定：</b>{_g(a,'financials','overall')}", st_p)))

    # ===== 五 估值 =====
    el.append(sec("五、估值分析（投行視角）"))
    val = a.get("valuation") or {}
    vt = val.get("table") or []
    pn = val.get("peer_names") or ["同業1", "同業2"]
    if vt:
        rows = [["指標", ticker.upper(), pn[0] if len(pn) > 0 else "同業1", pn[1] if len(pn) > 1 else "同業2", "評語"]]
        rows += [[x.get("metric",""), x.get("self",""), x.get("peer1",""), x.get("peer2",""), x.get("comment","")] for x in vt]
        el.append(grid(rows, [W*0.17, W*0.17, W*0.17, W*0.17, W*0.32]))
    if peers:
        el.append(Spacer(1, 0.1*cm))
        rows = [["公司", "代碼", "PE(TTM)", "預估PE", "毛利率", "今年迄今", "市值"]]
        rows.append([cur.get("name", ticker), ticker.upper(),
                     f"{cur['pe_trailing']:.1f}x" if cur.get("pe_trailing") else "-",
                     f"{cur['pe_forward']:.1f}x" if cur.get("pe_forward") else "-",
                     f"{cur['gross_margin']:.0f}%" if cur.get("gross_margin") else "-",
                     f"{cur['ytd_pct']:+.0f}%" if cur.get("ytd_pct") is not None else "-", _n(cur.get("market_cap"), 0)])
        for p_ in peers:
            rows.append([p_.get("name", p_["ticker"]), p_["ticker"],
                         f"{p_['pe']:.1f}x" if p_.get("pe") else "-",
                         f"{p_['pe_fwd']:.1f}x" if p_.get("pe_fwd") else "-",
                         f"{p_['gross_margin']:.0f}%" if p_.get("gross_margin") else "-",
                         f"{p_['ytd_pct']:+.0f}%" if p_.get("ytd_pct") is not None else "-", _n(p_.get("market_cap"), 0)])
        el.append(Paragraph("同業實際數據（Yahoo Finance）", st_h2))
        el.append(grid(rows, [W*0.26, W*0.10, W*0.12, W*0.12, W*0.12, W*0.13, W*0.15]))
    if val.get("dcf_reasoning"):
        el.append(Paragraph("簡化 DCF 思路", st_h2))
        el.append(Paragraph(val["dcf_reasoning"], st_p))
    scn = val.get("scenarios") or {}
    if scn:
        rows = [["情境", "合理股價區間", "成立條件"]]
        for k, lab in (("bull", "樂觀"), ("base", "基本"), ("bear", "悲觀")):
            x = scn.get(k) or {}
            rows.append([lab, x.get("range", "-"), x.get("cond", "-")])
        el.append(Spacer(1, 0.1*cm)); el.append(grid(rows, [W*0.12, W*0.25, W*0.63]))
    if dcf:
        sc = dcf["scenarios"]
        el.append(Paragraph("系統簡化 DCF 試算（折現率 9%，以最新 FCF 為起點）：" +
                            "、".join(f"{k} {v['value_per_share']:.1f}" if v.get("value_per_share") is not None else f"{k} -"
                                      for k, v in sc.items()) +
                            "；僅為情境示意，非專業估值模型。", st_small))
    if val.get("conclusion"):
        el.append(Spacer(1, 0.12*cm))
        el.append(box(Paragraph(f"<b>估值結論：</b>{val['conclusion']}", st_p), border=GOLD, bg=CREAM))

    el.append(PageBreak())

    # ===== 六 成長潛力 =====
    el.append(sec("六、未來成長潛力（5–10 年）"))
    for x in (_g(a, "growth", "items", default=[]) or []):
        el.append(Paragraph(f"● <b>{x.get('title','')}：</b>{x.get('text','')}", st_bul))
    gs = _g(a, "growth", "scenarios_5_10y", default={}) or {}
    if gs:
        el.append(Spacer(1, 0.1*cm))
        rows = [["情境", "5–10 年圖像"], ["樂觀", gs.get("bull","")], ["基本", gs.get("base","")], ["悲觀", gs.get("bear","")]]
        el.append(grid(rows, [W*0.12, W*0.88]))

    # ===== 七 多空辯論 =====
    el.append(sec("七、多空辯論：兩位分析師的對話"))
    db = a.get("debate") or {}
    el.append(Paragraph("<b>多頭 Bull（看漲）</b>", ParagraphStyle("bh", fontName=FN, fontSize=10.5, textColor=GREEN, spaceBefore=4, spaceAfter=3)))
    el.append(box(Paragraph("「" + db.get("bull", "") + "」", st_quote), border=GREEN))
    el.append(Spacer(1, 0.15*cm))
    el.append(Paragraph("<b>空頭 Bear（看跌）</b>", ParagraphStyle("brh", fontName=FN, fontSize=10.5, textColor=RED, spaceBefore=4, spaceAfter=3)))
    el.append(box(Paragraph("「" + db.get("bear", "") + "」", st_quote), border=RED))
    el.append(Spacer(1, 0.15*cm))
    if db.get("neutral"):
        el.append(box(Paragraph(f"<b>中性結論：</b>{db['neutral']}", st_p), border=GOLD, bg=CREAM))

    # ===== 八 最終評估 =====
    el.append(sec("八、最終投資評估"))
    fn_ = a.get("final") or {}
    el.append(Paragraph("<b>短期展望（1 年內）</b>", st_h2)); el.append(Paragraph(fn_.get("short_term",""), st_p))
    el.append(Paragraph("<b>長期展望（5 年以上）</b>", st_h2)); el.append(Paragraph(fn_.get("long_term",""), st_p))
    two = [[[Paragraph("<b>關鍵催化因素</b>", st_h2)] + [Paragraph("● " + c, st_bul) for c in (fn_.get("catalysts") or [])],
            [Paragraph("<b>主要風險</b>", st_h2)] + [Paragraph("● " + r, st_bul) for r in (fn_.get("risks") or [])]]]
    tt = Table(two, colWidths=[W/2, W/2])
    tt.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"), ("LEFTPADDING", (0,0), (-1,-1), 2)]))
    el.append(tt)
    rating = fn_.get("rating", "")
    rcol = GREEN if ("買" in rating and "避" not in rating) else (RED if "避" in rating else GOLD)
    el.append(Spacer(1, 0.2*cm))
    rt = Table([[Paragraph(f"<b>最終結論：{rating}</b>", ParagraphStyle("rt", fontName=FN, fontSize=13, alignment=1, textColor=colors.white))]], colWidths=[W])
    rt.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), rcol), ("TOPPADDING", (0,0), (-1,-1), 8), ("BOTTOMPADDING", (0,0), (-1,-1), 8)]))
    el.append(KeepTogether([rt, Spacer(1, 0.15*cm),
                            Paragraph(f"● <b>已持有者：</b>{fn_.get('holders','')}", st_bul),
                            Paragraph(f"● <b>未持有者：</b>{fn_.get('non_holders','')}", st_bul),
                            Paragraph(f"● <b>不適合對象：</b>{fn_.get('unsuitable','')}", st_bul)]))

    el.append(Spacer(1, 0.4*cm))
    if a.get("_note"):
        el.append(Paragraph("※ " + a["_note"], st_small))
    el.append(Paragraph(f"（資料來源：Yahoo Finance 財務數據、公司財報與 SEC 申報、公開新聞與分析師報告，資料截至 {today:%Y/%m/%d}）", st_small))
    el.append(Paragraph(ANALYST_DISCLAIMER, st_small))

    def _footer(canv, doc_):
        canv.saveState(); canv.setFont(FN, 8)
        canv.setFillColor(RED); canv.drawString(1.5*cm, 0.9*cm, "僅限內部研究與教育訓練使用，非投資建議")
        canv.setFillColor(GRAY); canv.drawRightString(A4[0]-1.5*cm, 0.9*cm, f"{ticker.upper()} 深度研究報告 · {today:%Y/%m/%d} · 第 {doc_.page} 頁")
        canv.restoreState()

    doc.build(el, onFirstPage=_footer, onLaterPages=_footer)
    return out_path


def build_summary_text(ticker, fin, analysis, dcf=None):
    """LINE 用的精簡文字摘要"""
    cur = fin["current"]; a = analysis; fn_ = a.get("final") or {}
    lines = [f"📈 {cur.get('name', ticker)}（{ticker.upper()}）深度研究摘要"]
    hdr = []
    if cur.get("price"): hdr.append(f"現價 {cur['price']:.2f}")
    if cur.get("ytd_pct") is not None: hdr.append(f"今年迄今 {cur['ytd_pct']:+.0f}%")
    if cur.get("target_mean"): hdr.append(f"分析師目標價 {cur['target_mean']:.0f}")
    if hdr: lines.append("｜".join(hdr))
    lines += ["", "【一句話總結】", a.get("one_line", "")]
    lines += ["", f"【護城河】{(a.get('moat') or {}).get('score','-')}/10", (a.get("moat") or {}).get("verdict", "")[:120]]
    lines += ["", "【財務體質】", _g(a, "financials", "overall")[:150]]
    lines += ["", "【估值】", _g(a, "valuation", "conclusion")[:150]]
    lines += ["", f"【最終結論】{fn_.get('rating','-')}", f"已持有：{fn_.get('holders','')}", f"未持有：{fn_.get('non_holders','')}"]
    lines += ["", "（完整報告含近期變化、事業群、護城河評分、六大指標判讀、估值比較表、DCF情境、成長情境、多空辯論，見PDF）"]
    lines.append(ANALYST_DISCLAIMER)
    return "\n".join(lines)
