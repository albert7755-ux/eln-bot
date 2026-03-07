"""
report_generator.py
接收主題 → 用 Claude 上網搜尋整理 → 生成投資銀行風格 PDF → 上傳 Google Drive
"""
import os
import re
import json
import tempfile
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
    Table, TableStyle, NextPageTemplate
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

TZ_TAIPEI = pytz.timezone("Asia/Taipei")
pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
FONT = "HeiseiKakuGo-W5"

W, H = A4
ML, MR, MT, MB = 20*mm, 20*mm, 18*mm, 18*mm

C_NAVY  = colors.HexColor("#0a1628")
C_GOLD  = colors.HexColor("#c9a84c")
C_BLUE  = colors.HexColor("#1e3a5f")
C_LGRAY = colors.HexColor("#f4f6f9")
C_MGRAY = colors.HexColor("#d0d7e3")
C_TEXT  = colors.HexColor("#1a1a2a")
C_SUB   = colors.HexColor("#5a6a7e")
C_WHITE = colors.white

def S(name, **kw):
    base = dict(fontName=FONT, fontSize=10, leading=16, textColor=C_TEXT, spaceAfter=3)
    base.update(kw)
    return ParagraphStyle(name, **base)

TS = [
    ("TOPPADDING",    (0,0),(-1,-1), 5),
    ("BOTTOMPADDING", (0,0),(-1,-1), 5),
    ("LEFTPADDING",   (0,0),(-1,-1), 7),
    ("VALIGN",        (0,0),(-1,-1), "TOP"),
    ("GRID",          (0,0),(-1,-1), 0.4, C_MGRAY),
]

def th(t): return Paragraph(t, S("th", fontSize=9, textColor=C_WHITE, fontName=FONT))
def td(t): return Paragraph(t, S("td", fontSize=9.5, leading=15, textColor=C_TEXT, fontName=FONT, spaceAfter=2))
def body(t): return Paragraph(t, S("body", fontSize=9.5, leading=16, textColor=C_TEXT, spaceAfter=3))
def bullet(t): return Paragraph(f"▸  {t}", S("bul", fontSize=9.5, leading=16, textColor=C_TEXT, leftIndent=14, spaceAfter=2))
def source(t): return Paragraph(f"資料來源：{t}", S("src", fontSize=8, leading=12, textColor=C_SUB, spaceAfter=2))
def sec(title):
    return [Spacer(1,4), Paragraph(title, S("sec", fontSize=12, leading=18, textColor=C_WHITE,
            spaceAfter=0, backColor=C_NAVY, borderPadding=(5,8,5,8))), Spacer(1,6)]

# ══════════════════════════════
# Step 1: Claude 搜尋並整理報告內容（JSON格式）
# ══════════════════════════════
def research_topic(topic: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    prompt = f"""你是一位資深投資研究分析師，請針對以下主題進行研究並產出專業報告內容。

主題：{topic}

請使用繁體中文，以 JSON 格式回覆，結構如下：
{{
  "title": "報告完整標題",
  "subtitle": "英文副標題",
  "date": "今日日期",
  "executive_summary": {{
    "key_data": ["重點數據1", "重點數據2", "重點數據3"],
    "market_impact": ["市場影響1", "市場影響2", "市場影響3"],
    "recommendation": ["建議1", "建議2", "建議3"]
  }},
  "sections": [
    {{
      "title": "一、章節標題",
      "content": "章節內文（2到3段）",
      "bullets": ["重點1", "重點2", "重點3"],
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
• 數據要具體（有數字、百分比、來源）
• 觀點客觀中立，不偏多不偏空
• 只回覆 JSON，不要有其他文字"""

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )

    # 取得文字內容
    full_text = ""
    for block in resp.content:
        if hasattr(block, "text"):
            full_text += block.text

    # 解析JSON
    clean = re.sub(r"```json|```", "", full_text).strip()
    return json.loads(clean)

# ══════════════════════════════
# Step 2: 生成投資銀行風格 PDF
# ══════════════════════════════
def build_pdf(data: dict, output_path: str):
    now = datetime.now(TZ_TAIPEI)

    def on_cover(c, doc):
        c.saveState()
        c.setFillColor(C_NAVY); c.rect(0,0,W,H,fill=1,stroke=0)
        c.setFillColor(C_GOLD); c.rect(0, H-3*mm, W, 3*mm, fill=1, stroke=0)
        c.setFillColor(C_BLUE); c.rect(0, 0, W, 40*mm, fill=1, stroke=0)
        c.setFillColor(C_GOLD); c.rect(ML, H*0.35, 1.5*mm, H*0.40, fill=1, stroke=0)
        title = data.get("title", "研究報告")
        subtitle = data.get("subtitle", "Research Report")
        # 標題自動換行
        if len(title) > 14:
            mid = len(title)//2
            line1, line2 = title[:mid], title[mid:]
        else:
            line1, line2 = title, ""
        c.setFillColor(C_WHITE); c.setFont(FONT, 28)
        c.drawString(ML+8*mm, H*0.68, line1)
        if line2:
            c.drawString(ML+8*mm, H*0.61, line2)
            y_sub = H*0.55
        else:
            y_sub = H*0.60
        c.setStrokeColor(C_GOLD); c.setLineWidth(0.8)
        c.line(ML+8*mm, y_sub-3*mm, W*0.78, y_sub-3*mm)
        c.setFillColor(C_MGRAY); c.setFont(FONT, 10)
        c.drawString(ML+8*mm, y_sub-9*mm, subtitle)
        c.setFillColor(C_GOLD); c.setFont(FONT, 10)
        c.drawString(ML+8*mm, y_sub-16*mm, f"報告日期：{now.strftime('%Y年%m月%d日')}")
        c.setFillColor(C_WHITE); c.setFont(FONT, 9)
        c.drawString(ML+8*mm, y_sub-22*mm, "研究報告  |  投資策略部門")
        c.setFillColor(C_MGRAY); c.setFont(FONT, 7.5)
        c.drawString(ML, 26*mm, "本報告內容僅供參考，不構成投資建議或買賣依據。")
        c.drawString(ML, 21*mm, "投資涉及風險，過去績效不代表未來表現，投資人應審慎評估。")
        c.restoreState()

    def on_later(c, doc):
        c.saveState()
        if doc.page >= 3:
            c.setFillColor(C_NAVY); c.rect(0, H-12*mm, W, 12*mm, fill=1, stroke=0)
            c.setFont(FONT, 8); c.setFillColor(C_GOLD)
            c.drawString(ML, H-7.5*mm, data.get("title","研究報告")[:20])
            c.setFillColor(C_WHITE)
            c.drawRightString(W-MR, H-7.5*mm, f"研究報告  |  {now.strftime('%Y年%m月')}")
            c.setFillColor(C_LGRAY); c.rect(0,0,W,10*mm,fill=1,stroke=0)
            c.setFont(FONT,7.5); c.setFillColor(C_NAVY)
            c.drawString(ML, 3.5*mm, "本報告內容僅供參考，不構成投資建議。投資涉及風險，投資人應審慎評估。")
            c.setFont(FONT,8); c.setFillColor(C_BLUE)
            c.drawRightString(W-MR, 3.5*mm, f"第 {doc.page-2} 頁")
        c.restoreState()

    def on_toc(c, doc): pass

    cover_frame = Frame(0, 0, W, H, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    toc_frame   = Frame(ML, MB, W-ML-MR, H-MT-MB)
    body_frame  = Frame(ML, MB+10*mm, W-ML-MR, H-MT-MB-12*mm-10*mm)

    doc = BaseDocTemplate(output_path, pagesize=A4)
    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame], onPage=on_cover),
        PageTemplate(id="TOC",   frames=[toc_frame],   onPage=on_toc),
        PageTemplate(id="Body",  frames=[body_frame],  onPage=on_later),
    ])

    story = []
    CW = W - ML - MR

    # 封面
    story.append(NextPageTemplate("TOC"))
    story.append(PageBreak())

    # 目錄
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph("目　　錄", S("th2", fontSize=18, leading=26, textColor=C_NAVY, spaceAfter=6, fontName=FONT)))
    story.append(HRFlowable(width="100%", thickness=2, color=C_GOLD))
    story.append(Spacer(1, 8))

    toc_items = [("重點摘要（Executive Summary）", "1")]
    sections = data.get("sections", [])
    for i, s in enumerate(sections):
        toc_items.append((s.get("title",""), str(i+1)))
    toc_items.append(("展望與投資建議", str(len(sections)+1)))

    for title_t, pg in toc_items:
        t = Table([[Paragraph(title_t, S("toc", fontSize=10, leading=18, textColor=C_BLUE, fontName=FONT)),
                    Paragraph(pg, S("pg", fontSize=10, leading=18, textColor=C_GOLD, fontName=FONT, alignment=2))]],
                  colWidths=[CW-12*mm, 12*mm])
        t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                ("LINEBELOW",(0,0),(-1,-1),0.3,C_MGRAY),
                                ("TOPPADDING",(0,0),(-1,-1),5),
                                ("BOTTOMPADDING",(0,0),(-1,-1),5)]))
        story.append(t)

    story.append(NextPageTemplate("Body"))
    story.append(PageBreak())

    # Executive Summary
    for e in sec("重點摘要（Executive Summary）"): story.append(e)
    es = data.get("executive_summary", {})
    kd  = "\n".join([f"• {x}" for x in es.get("key_data",[])])
    mi  = "\n".join([f"• {x}" for x in es.get("market_impact",[])])
    rec = "\n".join([f"• {x}" for x in es.get("recommendation",[])])
    sum_t = Table([
        [th("關鍵數據"), th("市場影響"), th("投資建議")],
        [Paragraph(kd,  S("s1",fontSize=9.5,leading=15,textColor=C_TEXT,fontName=FONT,spaceAfter=2,leftIndent=4)),
         Paragraph(mi,  S("s2",fontSize=9.5,leading=15,textColor=C_TEXT,fontName=FONT,spaceAfter=2,leftIndent=4)),
         Paragraph(rec, S("s3",fontSize=9.5,leading=15,textColor=C_TEXT,fontName=FONT,spaceAfter=2,leftIndent=4))],
    ], colWidths=[CW/3]*3)
    sum_t.setStyle(TableStyle(TS + [("BACKGROUND",(0,0),(-1,0),C_NAVY),
                                     ("BACKGROUND",(0,1),(-1,1),C_LGRAY)]))
    story.append(sum_t)
    story.append(Spacer(1, 10))

    # 各章節
    for s in sections:
        for e in sec(s.get("title","")): story.append(e)
        if s.get("content"):
            for para in s["content"].split("\n"):
                if para.strip():
                    story.append(body(para.strip()))
            story.append(Spacer(1,4))
        for b in s.get("bullets", []):
            story.append(bullet(b))
        if s.get("source"):
            story.append(Spacer(1,3))
            story.append(source(s["source"]))
        story.append(Spacer(1,8))

    # 展望與建議
    outlook = data.get("outlook", {})
    for e in sec("展望與投資建議"): story.append(e)

    indicators = outlook.get("indicators", [])
    if indicators:
        ind_data = [[th("指標"), th("當前狀況"), th("警戒水位")]]
        for ind in indicators:
            ind_data.append([td(ind.get("name","")), td(ind.get("current","")), td(ind.get("warning",""))])
        ind_t = Table(ind_data, colWidths=[50*mm, 60*mm, None])
        ind_t.setStyle(TableStyle(TS + [("BACKGROUND",(0,0),(-1,0),C_BLUE),
                                         ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE,C_LGRAY])]))
        story.append(ind_t)
        story.append(Spacer(1,8))

    for r in outlook.get("recommendations", []):
        story.append(bullet(r))
    if outlook.get("source"):
        story.append(Spacer(1,4))
        story.append(source(outlook["source"]))

    doc.build(story)

# ══════════════════════════════
# 主入口
# ══════════════════════════════
def generate_research_report(topic: str, user_id: str = "") -> str:
    from pdf_generator import upload_to_drive

    # 研究主題
    data = research_topic(topic)

    # 生成PDF
    now = datetime.now(TZ_TAIPEI)
    filename = f"研究報告_{now.strftime('%Y%m%d_%H%M')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    build_pdf(data, tmp_path)

    # 上傳Google Drive
    link = upload_to_drive(tmp_path, filename, folder_name="龍蝦研究報告")
    return link
