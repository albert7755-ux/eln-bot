# -*- coding: utf-8 -*-
import os
import re
import requests
from datetime import datetime, timezone, timedelta
from openai import OpenAI

TZ_TAIPEI = timezone(timedelta(hours=8))

def to_float(v):
    try:
        if v is None:
            return None
        v = str(v).strip()
        if v == "" or v.lower() == "n/a":
            return None
        return float(v)
    except Exception:
        return None

def fnum(x, digits=2, suffix=""):
    if x is None:
        return "N/A"
    return f"{x:,.{digits}f}{suffix}"

def abs_pct(p):
    if p is None:
        return "N/A"
    return f"{abs(p):.2f}%"

def sign_word(p):
    if p is None:
        return "變動"
    return "上漲" if p >= 0 else "下跌"

def market_tone(spx_chg):
    if spx_chg is None:
        return "震盪"
    if spx_chg <= -1.2:
        return "回檔加深"
    if spx_chg <= -0.3:
        return "回檔整理"
    if spx_chg >= 1.2:
        return "強勢推進"
    if spx_chg >= 0.3:
        return "偏多續行"
    return "區間震盪"

def yahoo_quote(symbols):
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    params = {"symbols": ",".join(symbols)}
    r = requests.get(url, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    out = {}
    for item in data.get("quoteResponse", {}).get("result", []):
        sym = item.get("symbol")
        if sym:
            out[sym] = item
    return out

def yf_close(q):
    return to_float(q.get("regularMarketPreviousClose"))

def yf_chg_pct(q):
    # 你原本是直接吃 regularMarketChangePercent
    return to_float(q.get("regularMarketChangePercent"))

def yf_yield_pct_from_yahoo_index(q):
    v = to_float(q.get("regularMarketPreviousClose"))
    if v is None:
        v = to_float(q.get("regularMarketPrice"))
    return (v / 100.0) if v is not None else None

def get_snapshot():
    syms = ["^DJI", "^GSPC", "^IXIC", "^TNX", "^TYX", "GC=F", "SI=F", "CL=F"]
    q = yahoo_quote(syms)

    return {
        "dji": yf_close(q.get("^DJI", {})),
        "dji_chg": yf_chg_pct(q.get("^DJI", {})),

        "spx": yf_close(q.get("^GSPC", {})),
        "spx_chg": yf_chg_pct(q.get("^GSPC", {})),

        "ndq": yf_close(q.get("^IXIC", {})),
        "ndq_chg": yf_chg_pct(q.get("^IXIC", {})),

        "y10": yf_yield_pct_from_yahoo_index(q.get("^TNX", {})),
        "y30": yf_yield_pct_from_yahoo_index(q.get("^TYX", {})),

        "gold": yf_close(q.get("GC=F", {})),
        "gold_chg": yf_chg_pct(q.get("GC=F", {})),

        "silver": yf_close(q.get("SI=F", {})),
        "silver_chg": yf_chg_pct(q.get("SI=F", {})),

        "wti": yf_close(q.get("CL=F", {})),
        "wti_chg": yf_chg_pct(q.get("CL=F", {})),
    }

def build_prompt(now, s):
    week = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經日報】"
    tone = market_tone(s.get("spx_chg"))

    prompt = f"""
你是「品牌型每日財經日報」總編：文風像券商早報＋盤勢觀點，理專可直接轉貼客戶。
寫法要有盤感、有畫面：用兩三句講清楚昨晚為什麼這樣走、盤中轉折是什麼、收盤留下什麼結論。

硬規則（違反任一條就重寫）：
1) 全文 750～1,050 字（含標點）
2) 嚴禁使用 Markdown（不要 ###、不要 **粗體**、不要條列符號「-」「•」）
3) 嚴禁任何稱呼/寒暄（不要問候語）
4) 禁止出現「觀望」兩字
5) 數字只能使用我提供的市場數據（指數、殖利率、金銀油），不得自行編數字
6) 新聞/事件不要寫具體指名或細節；只能用「市場關注點」描述
7) 版型必須完全照下方輸出，且「三、股債匯操作策略建議」一定要完整四行

請只輸出以下版型（不可多一行、不可少一段）：

{title}

（開頭 2–3 句：昨晚盤勢屬於「{tone}」。一定要點到：利率/債市或地緣其一；語氣像快報）

一、 全球市場數據概覽
1. 美股三大指數表現

道瓊工業指數： 收在 {fnum(s.get('dji'),2)} 點，{sign_word(s.get('dji_chg'))} {abs_pct(s.get('dji_chg'))}。（一句話：盤感原因）
標普500指數： 收在 {fnum(s.get('spx'),2)} 點，{sign_word(s.get('spx_chg'))} {abs_pct(s.get('spx_chg'))}。（一句話：盤感原因）
那斯達克指數： 收在 {fnum(s.get('ndq'),2)} 點，{sign_word(s.get('ndq_chg'))} {abs_pct(s.get('ndq_chg'))}。（一句話：盤感原因）

2. 美國國債收益率

10年期美債： 報 {fnum(s.get('y10'),3,'%')}。（一句話：利率走勢對股市情緒的解讀）
30年期美債： 報 {fnum(s.get('y30'),3,'%')}。（一句話）

3. 原物料商品表現

黃金： 報 ${fnum(s.get('gold'),2)}。（一句話：避險/利率/美元邏輯）
白銀： 報 ${fnum(s.get('silver'),2)}。（一句話：波動/資金/工業金融雙屬性）
原油（WTI）： 報 ${fnum(s.get('wti'),2)}。（一句話：地緣/供需/風險溢價）

二、 焦點新聞摘要
【總體經濟】用 2–3 句：寫市場正在盯的關鍵訊號以及它怎麼影響利率與股市（不寫具體數字結果）。
【市場主題】用 2 句：寫地緣/油價/風險情緒/AI資金輪動等關注點，講清楚對盤面的直接影響。
【焦點個股】用 2–3 句：用快報口吻寫 1–2 個主線代表題材，不喊單、不寫未證實消息。

三、 股債匯操作策略建議
股市策略：3 句。主軸必須是拉回分批、逢低承接主流龍頭與AI落地，並加一句風險控管（分批/部位/回測）。
債市策略：2 句。用白話講息收/避險/長短搭配的做法。
匯市與原物料策略：2 句。提金銀/油的分批做法與波動提醒（避免用觀望）。
風險提示：1 句（非投資建議）
"""
    return prompt.strip()

def _ok(text: str) -> bool:
    if not text:
        return False
    must = [
        "一、 全球市場數據概覽",
        "二、 焦點新聞摘要",
        "三、 股債匯操作策略建議",
        "股市策略：",
        "債市策略：",
        "匯市與原物料策略：",
        "風險提示：",
    ]
    if any(m not in text for m in must):
        return False
    if "觀望" in text:
        return False
    if "親愛的" in text or "您好" in text:
        return False
    if "###" in text or "**" in text:
        return False
    if "\n-" in text or "\n•" in text:
        return False
    if len(text) < 520 or len(text) > 2200:
        return False
    return True

def generate_report_from_prompt(prompt: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("缺少環境變數 OPENAI_API_KEY")

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key)

    last = ""
    for _ in range(2):
        resp = client.chat.completions.create(
            model=model,
            temperature=0.25,
            max_tokens=950,
            messages=[
                {"role": "system", "content": "你是投資輔銷市場總編：必須照版型、短、有盤感、不可杜撰、不可使用“觀望”。"},
                {"role": "user", "content": prompt},
                {"role": "user", "content": "輸出後自我檢查：段落是否齊全、策略四行是否存在、是否未出現“觀望”、是否沒有markdown/條列/稱呼。不合格請立刻重寫並只輸出最終版。"},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        last = text
        if _ok(text):
            return text
        prompt += "\n\n（提醒：上次輸出不合格。不得缺少策略段、不得出現“觀望”、不得使用markdown/條列/稱呼，且不可杜撰具體事件。）"

    return last

def generate_report_today(style: str = "brief") -> str:
    now = datetime.now(TZ_TAIPEI)
    snap = get_snapshot()
    prompt = build_prompt(now, snap)
    report = generate_report_from_prompt(prompt)

    if not report or len(report) < 200:
        return f"【{now.strftime('%Y年%m月%d日')} 財經日報】\n系統提示：今日生成內容不足，請稍後重試。"
    return report
