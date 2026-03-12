"""
report_generator.py — 投資銀行風格研究報告生成器（升級版）
封面：深藍+金色+左側寬條+評級標籤
內頁：左側藍邊條+章節icon+卡片式摘要+matplotlib圖表
"""
import os, re, json, tempfile, io
from datetime import datetime
import pytz
import anthropic

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, HRFlowable, PageBreak,
    Table, TableStyle, NextPageTemplate, Image
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

TZ_TAIPEI = pytz.timezone("Asia/Taipei")

# ── 字型
def _register_fonts():
    base = os.path.join(os.path.dirname(__file__), "fonts")
    for ext in ["ttf", "otf"]:
        reg  = os.path.join(base, f"NotoSansTC-Regular.{ext}")
        bold = os.path.join(base, f"NotoSansTC-Bold.{ext}")
        if os.path.exists(reg) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("NotoSansTC",      reg))
                pdfmetrics.registerFont(TTFont("NotoSansTC-Bold", bold))
                print(f"[Font] 載入成功：{ext}")
                return "NotoSansTC", "NotoSansTC-Bold"
            except Exception as e:
                print(f"[Font] {ext} 載入失敗: {e}")
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    print("[Font] 退回內建字型 HeiseiKakuGo-W5")
    return "HeiseiKakuGo-W5", "HeiseiKakuGo-W5"

FONT, FONT_BOLD = _register_fonts()

W, H = A4
ML, MR, MT, MB = 22*mm, 20*mm, 18*mm, 18*mm

# ── 色盤（IB風格）
C_NAVY   = colors.HexColor("#0B1F3A")
C_GOLD   = colors.HexColor("#C9993A")
C_BLUE   = colors.HexColor("#1A3A5C")
C_LBLUE  = colors.HexColor("#2C5F8A")
C_LGRAY  = colors.HexColor("#F4F6F9")
C_MGRAY  = colors.HexColor("#CDD5E0")
C_DGRAY  = colors.HexColor("#6B7A8D")
C_TEXT   = colors.HexColor("#1A1F2E")
C_WHITE  = colors.white
C_ACCENT = colors.HexColor("#E8F0F8")

# ── 章節 icon 對應
SECTION_ICONS = {
    0: "▐ ", 1: "▐ ", 2: "▐ ", 3: "▐ ", 4: "▐ ",
}

def S(name, **kw):
    base = dict(fontName=FONT, fontSize=10, leading=16, textColor=C_TEXT, spaceAfter=3)
    base.update(kw)
    return ParagraphStyle(name, **base)

# ── 元素工廠
def th(t):
    return Paragraph(t, S("th", fontSize=9, fontName=FONT_BOLD, textColor=C_WHITE, spaceAfter=0))

def td(t):
    return Paragraph(t, S("td", fontSize=9.5, leading=15, textColor=C_TEXT, fontName=FONT, spaceAfter=1))

def body(t):
    return Paragraph(t, S("body", fontSize=9.5, leading=17, textColor=C_TEXT, fontName=FONT, spaceAfter=4))

def bullet(t):
    return Paragraph(
        f"<font color='#{C_GOLD.hexval()[2:]}'>◆</font>  {t}",
        S("bul", fontSize=9.5, leading=16, textColor=C_TEXT, fontName=FONT, leftIndent=12, spaceAfter=3)
    )

def source(t):
    return Paragraph(
        f"📌 資料來源：{t}",
        S("src", fontSize=7.5, leading=12, textColor=C_DGRAY, fontName=FONT, spaceAfter=2)
    )

def sec_header(title, idx=0):
    """章節標題：左側金條 + 深藍底"""
    CW = W - ML - MR
    icon_map = ["01", "02", "03", "04", "05", "06", "07", "08"]
    num = icon_map[idx % len(icon_map)]
    t = Table([
        [
            Paragraph("", S("x")),
            Paragraph(f"<b>{title}</b>",
                S("sh", fontSize=11.5, leading=17, textColor=C_WHITE, fontName=FONT_BOLD, spaceAfter=0))
        ]
    ], colWidths=[4*mm, CW-4*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (0,0), C_GOLD),
        ("BACKGROUND",    (1,0), (1,0), C_NAVY),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (1,0), (1,0), 8),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    return [Spacer(1, 6), t, Spacer(1, 7)]

def kpi_card(label, value, sub=""):
    """KPI 卡片"""
    inner = [
        Paragraph(value, S("kv", fontSize=16, fontName=FONT_BOLD, textColor=C_NAVY, spaceAfter=1, leading=20)),
        Paragraph(label, S("kl", fontSize=8,  fontName=FONT,      textColor=C_DGRAY, spaceAfter=0)),
    ]
    if sub:
        inner.append(Paragraph(sub, S("ks", fontSize=7.5, fontName=FONT, textColor=C_GOLD, spaceAfter=0)))
    t = Table([[inner]], colWidths=[None])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), C_ACCENT),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("LINEBELOW",     (0,0), (-1,-1), 2.5, C_GOLD),
        ("BOX",           (0,0), (-1,-1), 0.5, C_MGRAY),
    ]))
    return t

def make_chart(sections):
    """用 matplotlib 生成簡易趨勢示意圖"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np

        fig, ax = plt.subplots(figsize=(7.5, 2.8))
        fig.patch.set_facecolor("#F4F6F9")
        ax.set_facecolor("#F4F6F9")

        # 生成示意趨勢線（基於章節數量）
        n = max(len(sections), 4)
        x = np.linspace(0, n-1, 50)
        y1 = np.sin(x * 0.8) * 15 + 100 + x * 2
        y2 = np.cos(x * 0.6) * 10 + 85 + x * 1.5

        ax.plot(x, y1, color="#0B1F3A", linewidth=2.5, label="主要指標", zorder=3)
        ax.plot(x, y2, color="#C9993A", linewidth=2, linestyle="--", label="參考基準", zorder=3)
        ax.fill_between(x, y1, y2, alpha=0.08, color="#2C5F8A")

        ax.set_xticks(range(n))
        ax.set_xticklabels([f"Q{i+1}" for i in range(n)], fontsize=8)
        ax.tick_params(colors="#6B7A8D", labelsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#CDD5E0")
        ax.spines["bottom"].set_color("#CDD5E0")
        ax.legend(loc="upper left", fontsize=8, framealpha=0.5)
        ax.set_title("趨勢走勢示意", fontsize=9, color="#6B7A8D", pad=6)
        ax.grid(axis="y", color="#CDD5E0", linewidth=0.5, alpha=0.7)

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"[Chart] 生成失敗: {e}")
        return None

# ══════════════════════════════
# Step 1: Claude 搜尋整理內容
# ══════════════════════════════
STYLE_PROMPTS = {
    "ib": "你是一位頂尖投資銀行（Goldman Sachs / Morgan Stanley）的資深研究分析師，請以專業、嚴謹、數據導向的投資銀行風格撰寫報告。語氣專業正式，強調具體數字、百分比、市場影響，每個論點都要有數據支撐。",
    "brief": "你是一位財經媒體首席編輯，請以極簡、一頁式摘要風格撰寫。每個觀點不超過20字，只留最核心的數字和結論，適合30秒內快速瀏覽。",
    "client": "你是一位親切的私人理財顧問，請以口語化、易懂的方式撰寫，像在跟客戶面對面解釋一樣。完全避免艱深術語，多用比喻和生活化例子，結尾要有明確的行動建議。",
    "academic": "你是一位財經學術研究員，請以嚴謹的學術風格撰寫，包含理論框架、多方觀點辯證、潛在風險討論、數據來源引用，語氣客觀中立不偏向任何立場。",
    "hybrid": "你是一位兼具投資銀行與學術背景的策略分析師，請結合IB的清晰架構與學術的深度分析，既有實務操作建議，也有理論依據支撐，適合機構投資人閱讀。",
    "custom": "{custom_prompt}",
}

STYLE_TITLES = {
    "ib":       "投資銀行研究報告",
    "brief":    "市場快訊摘要",
    "client":   "投資觀點分享",
    "academic": "深度研究分析",
    "hybrid":   "策略研究報告",
    "custom":   "自訂風格報告",
}

def research_topic(topic: str, style: str = "ib", custom_prompt: str = "") -> dict:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    style_desc = custom_prompt if (style == "custom" and custom_prompt) else STYLE_PROMPTS.get(style, STYLE_PROMPTS["ib"])

    prompt = f"""{style_desc}

主題：{topic}

請使用繁體中文，以 JSON 格式回覆，結構如下：
{{
  "title": "報告完整標題",
  "subtitle": "English Subtitle",
  "rating": "評級：如 中性 / 正面 / 謹慎",
  "date": "今日日期",
  "executive_summary": {{
    "key_data": ["重點數據1（含具體數字）", "重點數據2", "重點數據3"],
    "market_impact": ["市場影響1", "市場影響2", "市場影響3"],
    "recommendation": ["建議1", "建議2", "建議3"]
  }},
  "kpis": [
    {{"label": "指標名稱", "value": "數值", "sub": "變化或說明"}},
    {{"label": "指標名稱", "value": "數值", "sub": "變化或說明"}},
    {{"label": "指標名稱", "value": "數值", "sub": "變化或說明"}}
  ],
  "sections": [
    {{
      "title": "一、章節標題",
      "content": "章節內文（2到3段，每段100字以上）",
      "bullets": ["重點1（含數字）", "重點2", "重點3"],
      "source": "資料來源"
    }}
  ],
  "outlook": {{
    "indicators": [
      {{"name": "指標名稱", "current": "當前狀況", "warning": "警戒水位"}}
    ],
    "recommendations": ["建議1", "建議2", "建議3", "建議4"],
    "source": "資料來源"
  }}
}}

要求：
• 至少包含4個章節
• kpis 一定要有3個，數值要具體（有數字、%、$）
• rating 要明確填入
• 數據要具體，有數字、百分比
• 只回覆 JSON，不要有其他文字"""

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=5000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )

    full_text = ""
    for block in resp.content:
        if hasattr(block, "text"):
            full_text += block.text

    clean = re.sub(r"```json|```", "", full_text).strip()
    return json.loads(clean)

# ══════════════════════════════
# Step 2: 生成 IB 風格 PDF
# ══════════════════════════════
def build_pdf(data: dict, output_path: str):
    now = datetime.now(TZ_TAIPEI)
    CW = W - ML - MR
    rating = data.get("rating", "中性")
    rating_color = C_GOLD if "正面" in rating else (colors.HexColor("#C0392B") if "謹慎" in rating or "負面" in rating else C_LBLUE)

    # ── 封面
    def on_cover(c, doc):
        c.saveState()

        # 背景
        c.setFillColor(C_NAVY); c.rect(0, 0, W, H, fill=1, stroke=0)

        # 左側金色寬條（IB特色）
        c.setFillColor(C_GOLD); c.rect(0, 0, 8*mm, H, fill=1, stroke=0)

        # 上方細條
        c.setFillColor(C_LBLUE); c.rect(8*mm, H-2*mm, W-8*mm, 2*mm, fill=1, stroke=0)

        # 中間白色內容區
        box_y = H * 0.28
        box_h = H * 0.52
        c.setFillColor(colors.HexColor("#0F2845"))
        c.roundRect(16*mm, box_y, W-24*mm, box_h, 3*mm, fill=1, stroke=0)

        # 標題
        title = data.get("title", "研究報告")
        words = list(title)
        line1 = title[:16] if len(title) > 16 else title
        line2 = title[16:] if len(title) > 16 else ""

        c.setFillColor(C_WHITE); c.setFont(FONT_BOLD, 26)
        c.drawString(22*mm, box_y + box_h - 24*mm, line1)
        if line2:
            c.drawString(22*mm, box_y + box_h - 33*mm, line2)

        # 金線
        c.setStrokeColor(C_GOLD); c.setLineWidth(1.5)
        line_y = box_y + box_h - (38 if line2 else 30)*mm
        c.line(22*mm, line_y, W - 24*mm, line_y)

        # 英文副標題
        subtitle = data.get("subtitle", "Research Report")
        c.setFillColor(C_MGRAY); c.setFont(FONT, 10)
        c.drawString(22*mm, line_y - 8*mm, subtitle)

        # 評級標籤
        c.setFillColor(rating_color)
        c.roundRect(22*mm, line_y - 20*mm, 28*mm, 9*mm, 1.5*mm, fill=1, stroke=0)
        c.setFillColor(C_WHITE); c.setFont(FONT_BOLD, 8)
        c.drawString(24*mm, line_y - 16*mm, f"評級：{rating}")

        # 日期 & 部門
        c.setFillColor(C_DGRAY); c.setFont(FONT, 9)
        c.drawString(22*mm, box_y + 12*mm, f"報告日期：{now.strftime('%Y年%m月%d日')}")
        c.drawString(22*mm, box_y + 6*mm,  "投資研究部門  ｜  機密文件")

        # 底部免責
        c.setFillColor(C_DGRAY); c.setFont(FONT, 7)
        c.drawString(10*mm, 12*mm, "本報告內容僅供參考，不構成投資建議或買賣依據。投資涉及風險，過去績效不代表未來表現。")

        c.restoreState()

    # ── 內頁 header/footer
    def on_later(c, doc):
        c.saveState()
        # 頂部 header
        c.setFillColor(C_NAVY); c.rect(0, H-11*mm, W, 11*mm, fill=1, stroke=0)
        c.setFillColor(C_GOLD);  c.rect(0, H-11*mm, 5*mm, 11*mm, fill=1, stroke=0)
        c.setFont(FONT_BOLD, 8); c.setFillColor(C_WHITE)
        c.drawString(8*mm, H-7*mm, data.get("title","研究報告")[:22])
        c.setFont(FONT, 8); c.setFillColor(C_GOLD)
        c.drawRightString(W-MR, H-7*mm, f"投資研究  ｜  {now.strftime('%Y.%m.%d')}")

        # 底部 footer
        c.setFillColor(C_LGRAY); c.rect(0, 0, W, 9*mm, fill=1, stroke=0)
        c.setFillColor(C_GOLD);  c.rect(0, 0, 5*mm, 9*mm, fill=1, stroke=0)
        c.setFont(FONT, 7); c.setFillColor(C_DGRAY)
        c.drawString(8*mm, 3*mm, "本報告內容僅供參考，不構成投資建議。投資涉及風險，投資人應審慎評估。")
        c.setFont(FONT_BOLD, 8); c.setFillColor(C_BLUE)
        c.drawRightString(W-MR, 3*mm, f"第 {doc.page-1} 頁")
        c.restoreState()

    def on_toc(c, doc):
        c.saveState()
        c.setFillColor(C_LGRAY); c.rect(0, 0, W, 9*mm, fill=1, stroke=0)
        c.setFillColor(C_GOLD);  c.rect(0, 0, 5*mm, 9*mm, fill=1, stroke=0)
        c.restoreState()

    cover_frame = Frame(0, 0, W, H, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    toc_frame   = Frame(ML, MB+9*mm, CW, H-MT-MB-9*mm)
    body_frame  = Frame(ML, MB+9*mm, CW, H-MT-MB-11*mm-9*mm)

    doc = BaseDocTemplate(output_path, pagesize=A4)
    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame], onPage=on_cover),
        PageTemplate(id="TOC",   frames=[toc_frame],   onPage=on_toc),
        PageTemplate(id="Body",  frames=[body_frame],  onPage=on_later),
    ])

    story = []

    # 封面
    story.append(NextPageTemplate("TOC"))
    story.append(PageBreak())

    # ── 目錄頁
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("目　　錄", S("toc_title", fontSize=20, fontName=FONT_BOLD,
                                         textColor=C_NAVY, spaceAfter=4, leading=28)))
    story.append(HRFlowable(width="100%", thickness=2.5, color=C_GOLD, spaceAfter=6))
    story.append(Spacer(1, 4))

    sections = data.get("sections", [])
    toc_items = [("重點摘要（Executive Summary）", "01")]
    toc_items.append(("關鍵指標一覽", "01"))
    for i, s in enumerate(sections):
        toc_items.append((s.get("title", ""), f"0{i+2}" if i < 8 else str(i+2)))
    toc_items.append(("展望與投資建議", f"0{len(sections)+2}"))

    for i, (title_t, pg) in enumerate(toc_items):
        bg = C_ACCENT if i % 2 == 0 else C_WHITE
        t = Table([
            [Paragraph(f"<font color='#{C_GOLD.hexval()[2:]}'>{pg}</font>  {title_t}",
                       S("toc_i", fontSize=10, fontName=FONT, textColor=C_BLUE, leading=18)),
             Paragraph(f"<font color='#{C_GOLD.hexval()[2:]}'>────</font>",
                       S("toc_pg", fontSize=9, textColor=C_MGRAY, alignment=2))]
        ], colWidths=[CW-15*mm, 15*mm])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), bg),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING",   (0,0), (0,0),   8),
            ("LINEBELOW",     (0,0), (-1,-1), 0.3, C_MGRAY),
        ]))
        story.append(t)

    story.append(NextPageTemplate("Body"))
    story.append(PageBreak())

    # ── KPI 卡片區
    for e in sec_header("關鍵指標一覽", 0): story.append(e)
    kpis = data.get("kpis", [])
    if kpis:
        card_cols = []
        for kpi in kpis[:3]:
            card_cols.append(kpi_card(kpi.get("label",""), kpi.get("value",""), kpi.get("sub","")))
        kpi_t = Table([card_cols], colWidths=[CW/3-2*mm]*3, hAlign="LEFT")
        kpi_t.setStyle(TableStyle([
            ("LEFTPADDING",  (0,0), (-1,-1), 3),
            ("RIGHTPADDING", (0,0), (-1,-1), 3),
            ("VALIGN",       (0,0), (-1,-1), "TOP"),
        ]))
        story.append(kpi_t)
        story.append(Spacer(1, 10))

    # ── Executive Summary（三欄表格）
    for e in sec_header("重點摘要（Executive Summary）", 1): story.append(e)
    es = data.get("executive_summary", {})

    def es_col(items, icon, color):
        lines = [Paragraph(f"<font color='#{color.hexval()[2:]}'>{icon}</font>",
                           S("es_icon", fontSize=14, fontName=FONT, spaceAfter=2, leading=18))]
        for item in items:
            lines.append(bullet(item))
        return lines

    es_t = Table([
        [th("📊 關鍵數據"), th("📈 市場影響"), th("💡 投資建議")],
        [es_col(es.get("key_data",[]), "", C_BLUE),
         es_col(es.get("market_impact",[]), "", C_NAVY),
         es_col(es.get("recommendation",[]), "", C_GOLD)],
    ], colWidths=[CW/3]*3)
    es_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  C_NAVY),
        ("BACKGROUND",    (0,1), (-1,-1), C_LGRAY),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("GRID",          (0,0), (-1,-1), 0.4, C_MGRAY),
        ("LINEABOVE",     (0,0), (-1,0),  2, C_GOLD),
    ]))
    story.append(es_t)
    story.append(Spacer(1, 12))

    # ── 趨勢圖
    chart_buf = make_chart(sections)
    if chart_buf:
        img = Image(chart_buf, width=CW, height=2.8*28)
        img.hAlign = "LEFT"
        story.append(img)
        story.append(Spacer(1, 10))

    # ── 各章節
    for i, s in enumerate(sections):
        for e in sec_header(s.get("title",""), i+2): story.append(e)

        if s.get("content"):
            for para in s["content"].split("\n"):
                if para.strip():
                    story.append(body(para.strip()))
            story.append(Spacer(1, 4))

        # 重點列表（帶底色框）
        if s.get("bullets"):
            bul_items = [[bullet(b)] for b in s["bullets"]]
            bul_t = Table(bul_items, colWidths=[CW])
            bul_t.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,-1), C_ACCENT),
                ("TOPPADDING",    (0,0), (-1,-1), 3),
                ("BOTTOMPADDING", (0,0), (-1,-1), 3),
                ("LEFTPADDING",   (0,0), (-1,-1), 6),
                ("LINEBEFORE",    (0,0), (0,-1),  3, C_GOLD),
            ]))
            story.append(bul_t)

        if s.get("source"):
            story.append(Spacer(1, 3))
            story.append(source(s["source"]))
        story.append(Spacer(1, 10))

    # ── 展望與建議
    outlook = data.get("outlook", {})
    for e in sec_header("展望與投資建議", len(sections)+2): story.append(e)

    indicators = outlook.get("indicators", [])
    if indicators:
        ind_data = [[th("📌 指標"), th("當前狀況"), th("⚠️ 警戒水位")]]
        for j, ind in enumerate(indicators):
            bg = C_WHITE if j % 2 == 0 else C_LGRAY
            ind_data.append([
                Paragraph(f"<b>{ind.get('name','')}</b>",
                          S("in", fontSize=9.5, fontName=FONT_BOLD, textColor=C_BLUE)),
                td(ind.get("current","")),
                Paragraph(ind.get("warning",""),
                          S("iw", fontSize=9.5, fontName=FONT, textColor=colors.HexColor("#C0392B")))
            ])
        ind_t = Table(ind_data, colWidths=[52*mm, 65*mm, None])
        ind_t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0),  C_NAVY),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_LGRAY]),
            ("TOPPADDING",    (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
            ("LEFTPADDING",   (0,0), (-1,-1), 7),
            ("GRID",          (0,0), (-1,-1), 0.4, C_MGRAY),
            ("LINEABOVE",     (0,0), (-1,0),  2, C_GOLD),
            ("LINEBEFORE",    (0,0), (0,-1),  3, C_GOLD),
        ]))
        story.append(ind_t)
        story.append(Spacer(1, 10))

    # 建議列表
    recs = outlook.get("recommendations", [])
    if recs:
        rec_data = []
        for j, r in enumerate(recs):
            num_p = Paragraph(f"<b>{j+1:02d}</b>",
                              S("rn", fontSize=11, fontName=FONT_BOLD, textColor=C_WHITE))
            txt_p = Paragraph(r, S("rt", fontSize=9.5, fontName=FONT, textColor=C_TEXT, leading=16))
            rec_data.append([num_p, txt_p])
        rec_t = Table(rec_data, colWidths=[10*mm, CW-10*mm])
        rec_t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (0,-1),  C_GOLD),
            ("BACKGROUND",    (1,0), (1,-1),  C_LGRAY),
            ("TOPPADDING",    (0,0), (-1,-1), 7),
            ("BOTTOMPADDING", (0,0), (-1,-1), 7),
            ("LEFTPADDING",   (0,0), (-1,-1), 8),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ("LINEBELOW",     (0,0), (-1,-1), 0.4, C_MGRAY),
        ]))
        story.append(rec_t)

    if outlook.get("source"):
        story.append(Spacer(1, 5))
        story.append(source(outlook["source"]))

    doc.build(story)

# ══════════════════════════════
# 主入口
# ══════════════════════════════
def generate_research_report(topic: str, user_id: str = "", style: str = "ib", custom_prompt: str = "") -> str:
    from pdf_generator import upload_to_drive

    data = research_topic(topic, style=style, custom_prompt=custom_prompt)

    now = datetime.now(TZ_TAIPEI)
    style_name = STYLE_TITLES.get(style, "研究報告")
    filename = f"{style_name}_{now.strftime('%Y%m%d_%H%M')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    build_pdf(data, tmp_path)

    link = upload_to_drive(tmp_path, filename, folder_name="龍蝦研究報告")
    return link
