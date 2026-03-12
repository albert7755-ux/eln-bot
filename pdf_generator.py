import os
import json
import re
import tempfile
from datetime import datetime
from html import escape as html_escape

import pytz
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle,
    PageBreak, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TZ_TAIPEI = pytz.timezone("Asia/Taipei")

# Render / Linux 內建可用 CJK 字型
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))

FONT_NORMAL = "STSong-Light"
FONT_BOLD = "HeiseiKakuGo-W5"

NAVY = colors.HexColor("#12263F")
BLUE = colors.HexColor("#2F5D8A")
GOLD = colors.HexColor("#B08D57")
TEXT = colors.HexColor("#222222")
MUTED = colors.HexColor("#666666")
LIGHT = colors.HexColor("#E7EDF5")
CARD = colors.white
BG_SOFT = colors.HexColor("#F6F8FB")
GREEN = colors.HexColor("#2E7D5B")
RED = colors.HexColor("#B94A48")
AMBER = colors.HexColor("#D98E04")


def get_drive_service():
    token_json = os.environ.get("GOOGLE_TOKEN_JSON", "")
    if not token_json:
        raise RuntimeError("Missing GOOGLE_TOKEN_JSON env var")
    token_data = json.loads(token_json)
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes"),
    )
    return build("drive", "v3", credentials=creds)


def upload_to_drive(file_path: str, filename: str, folder_name: str = "龍蝦報告") -> str:
    service = get_drive_service()

    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get("files", [])

    if folders:
        folder_id = folders[0]["id"]
    else:
        folder_metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        folder = service.files().create(body=folder_metadata, fields="id").execute()
        folder_id = folder["id"]

    file_metadata = {"name": filename, "parents": [folder_id]}
    if file_path.endswith(".png"):
        mime = "image/png"
    elif file_path.endswith(".jpg") or file_path.endswith(".jpeg"):
        mime = "image/jpeg"
    elif file_path.endswith(".pptx"):
        mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    else:
        mime = "application/pdf"

    media = MediaFileUpload(file_path, mimetype=mime)
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = uploaded["id"]

    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"}
    ).execute()

    return f"https://drive.google.com/file/d/{file_id}/view"


def get_styles():
    title_style = ParagraphStyle(
        "ReportTitle",
        fontSize=25,
        leading=31,
        textColor=NAVY,
        spaceAfter=6,
        fontName=FONT_BOLD,
    )
    cover_subtitle_style = ParagraphStyle(
        "CoverSubtitle",
        fontSize=12.5,
        leading=18,
        textColor=MUTED,
        spaceAfter=8,
        fontName=FONT_NORMAL,
    )
    section_style = ParagraphStyle(
        "Section",
        fontSize=15,
        leading=22,
        textColor=NAVY,
        spaceBefore=10,
        spaceAfter=6,
        fontName=FONT_BOLD,
        backColor=colors.HexColor("#EEF3F9"),
        borderPadding=(6, 9, 6),
    )
    subsection_style = ParagraphStyle(
        "SubSection",
        fontSize=11.5,
        leading=18,
        textColor=BLUE,
        spaceBefore=6,
        spaceAfter=3,
        fontName=FONT_BOLD,
    )
    body_style = ParagraphStyle(
        "Body",
        fontSize=11,
        leading=20,
        textColor=TEXT,
        spaceAfter=5,
        fontName=FONT_NORMAL,
        firstLineIndent=22,
    )
    bullet_style = ParagraphStyle(
        "Bullet",
        fontSize=11,
        leading=19,
        textColor=TEXT,
        spaceAfter=4,
        leftIndent=14,
        firstLineIndent=0,
        fontName=FONT_NORMAL,
    )
    small_style = ParagraphStyle(
        "Small",
        fontSize=9.4,
        leading=14,
        textColor=MUTED,
        fontName=FONT_NORMAL,
    )
    tiny_style = ParagraphStyle(
        "Tiny",
        fontSize=8.6,
        leading=12,
        textColor=MUTED,
        fontName=FONT_NORMAL,
    )
    card_title_style = ParagraphStyle(
        "CardTitle",
        fontSize=10.5,
        leading=14,
        textColor=MUTED,
        alignment=1,
        fontName=FONT_NORMAL,
    )
    card_value_style = ParagraphStyle(
        "CardValue",
        fontSize=18,
        leading=22,
        textColor=NAVY,
        alignment=1,
        fontName=FONT_BOLD,
    )
    return {
        "title": title_style,
        "cover_subtitle": cover_subtitle_style,
        "section": section_style,
        "subsection": subsection_style,
        "body": body_style,
        "bullet": bullet_style,
        "small": small_style,
        "tiny": tiny_style,
        "card_title": card_title_style,
        "card_value": card_value_style,
    }


def _safe_text(text: str) -> str:
    if text is None:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[\U00010000-\U0010ffff]", "", s)
    s = s.replace("•", "．").replace("→", "➜").replace("—", "－")
    s = s.replace("\t", "    ")
    s = re.sub(r"[ \u3000]{2,}", " ", s)
    return s.strip()


def _paragraph_html(line: str) -> str:
    safe = html_escape(_safe_text(line))
    return safe.replace("\n", "<br/>")


def _clean_title(text: str) -> str:
    t = _safe_text(text)
    t = re.sub(r"^\s*(請幫我|幫我|生成|做一份|做成|請做一份|請給我一份)\s*", "", t)
    t = re.sub(r"\s*(的pdf|pdf|PDF)$", "", t, flags=re.IGNORECASE)
    return t or "研究報告"


def _split_sections(text: str):
    text = _safe_text(text)
    lines = [ln.strip() for ln in text.split("\n")]
    sections = []
    current_title = "前言"
    current_lines = []

    section_pattern = re.compile(r"^【(.+?)】$")
    numbered_pattern = re.compile(r"^[一二三四五六七八九十]+、")

    for line in lines:
        if not line:
            current_lines.append("")
            continue

        m = section_pattern.match(line)
        if m:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = m.group(1)
            current_lines = []
            continue

        if numbered_pattern.match(line) and len(line) <= 30:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = line
            current_lines = []
            continue

        current_lines.append(line)

    if current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))

    return [(t, c) for t, c in sections if t or c]


def _make_header_band(styles, now):
    tbl = Table([[
        Paragraph(_paragraph_html("作者｜Albert"), styles["small"]),
        Paragraph(_paragraph_html(f"日期｜{now.strftime('%Y年%m月%d日')}"), styles["small"]),
        Paragraph(_paragraph_html("版本｜正式版"), styles["small"]),
    ]], colWidths=[55*mm, 55*mm, 55*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EEF3FA")),
        ("BOX", (0, 0), (-1, -1), 0.6, LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tbl


def _make_cover_cards(styles, sections):
    items = [
        ("研究主題", sections[0][0] if sections else "市場研究"),
        ("報告類型", "深度研究"),
        ("作者", "Albert"),
    ]
    row = []
    for label, value in items:
        cell = [
            Paragraph(_paragraph_html(value[:18]), styles["card_value"]),
            Spacer(1, 2),
            Paragraph(_paragraph_html(label), styles["card_title"])
        ]
        row.append(cell)

    tbl = Table([row], colWidths=[52*mm, 52*mm, 52*mm], rowHeights=[22*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("BOX", (0, 0), (-1, -1), 0.6, LIGHT),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _make_exec_summary_box(styles, sections):
    summary_text = ""
    for title, body in sections:
        if "執行摘要" in title or "摘要" in title:
            summary_text = body
            break
    if not summary_text and sections:
        summary_text = sections[0][1][:220]

    summary_text = _safe_text(summary_text)
    if len(summary_text) > 220:
        summary_text = summary_text[:220] + "……"

    box = Table([[Paragraph(_paragraph_html("　　" + summary_text), styles["body"])]], colWidths=[170*mm])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FCFCFD")),
        ("BOX", (0, 0), (-1, -1), 0.8, GOLD),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return box


def _make_research_grid(styles, sections):
    rows = [["觀察面向", "研究重點", "評估方向"]]
    candidates = [
        ("核心主題", sections[0][0] if sections else "市場研究", "事件驅動"),
        ("關鍵驅動", sections[1][0] if len(sections) > 1 else "政策與基本面", "中期主線"),
        ("資產影響", sections[3][0] if len(sections) > 3 else "跨資產傳導", "配置評估"),
        ("風險焦點", sections[-1][0] if sections else "事件變數", "風控優先"),
    ]
    for a, b, c in candidates:
        rows.append([_paragraph_html(a), _paragraph_html(b), _paragraph_html(c)])

    tbl = Table(rows, colWidths=[34*mm, 94*mm, 42*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 9.8),
        ("BACKGROUND", (0, 1), (-1, -1), CARD),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG_SOFT]),
        ("GRID", (0, 0), (-1, -1), 0.4, LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _extract_keywords(sections):
    names = [t for t, _ in sections if t]
    pool = []
    for name in names[:6]:
        pool.append(name.replace("【", "").replace("】", "")[:14])
    while len(pool) < 6:
        pool.append("研究追蹤")
    return pool[:6]


def _make_theme_cards(styles, sections):
    labels = _extract_keywords(sections)
    cards = []
    for idx, lab in enumerate(labels):
        card_color = [BLUE, GOLD, GREEN, AMBER, NAVY, colors.HexColor("#7A5EA8")][idx % 6]
        cell = Table([[
            Paragraph(_paragraph_html(lab), styles["small"])
        ]], colWidths=[52*mm], rowHeights=[12*mm])
        cell.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.9, card_color),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        cards.append(cell)

    grid = Table([cards[:3], cards[3:6]], colWidths=[52*mm, 52*mm, 52*mm], rowHeights=[13*mm, 13*mm])
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return grid


def _scenario_rows_from_sections(sections):
    mapping = {"樂觀情境": "", "基準情境": "", "悲觀情境": ""}
    for title, body in sections:
        for key in mapping:
            if key in title:
                mapping[key] = body

    rows = [["情境", "市場描述", "配置含意"]]
    for key, color in [("樂觀情境", GREEN), ("基準情境", BLUE), ("悲觀情境", RED)]:
        body = _safe_text(mapping.get(key, ""))
        if not body:
            body = "尚未明確描述此情境，可於後續版本補充更細緻之市場推演。"
        desc = body[:95] + ("……" if len(body) > 95 else "")
        implication = "偏積極配置" if key == "樂觀情境" else ("均衡配置" if key == "基準情境" else "防禦與避險")
        rows.append([key, desc, implication])
    return rows


def _make_scenario_table(styles, sections):
    rows = _scenario_rows_from_sections(sections)
    tbl = Table(rows, colWidths=[28*mm, 112*mm, 30*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG_SOFT]),
        ("GRID", (0, 0), (-1, -1), 0.4, LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _add_cover(story, title, now, sections, styles):
    story.append(Spacer(1, 7*mm))
    story.append(Paragraph(_paragraph_html(title), styles["title"]))
    story.append(Paragraph(_paragraph_html("Macro Strategy / Industry Research Report"), styles["cover_subtitle"]))
    story.append(Spacer(1, 2*mm))
    story.append(_make_header_band(styles, now))
    story.append(Spacer(1, 6*mm))
    story.append(_make_cover_cards(styles, sections))
    story.append(Spacer(1, 5*mm))
    story.append(_make_exec_summary_box(styles, sections))
    story.append(Spacer(1, 5*mm))
    story.append(_make_research_grid(styles, sections))
    story.append(Spacer(1, 5*mm))
    story.append(_make_theme_cards(styles, sections))
    story.append(PageBreak())


def _build_content_blocks(story, sections, styles):
    inserted_scenario = False

    for idx, (sec_title, sec_body) in enumerate(sections, start=1):
        display_title = sec_title if sec_title else f"第{idx}節"
        story.append(Paragraph(_paragraph_html(f"【{display_title}】"), styles["section"]))
        story.append(Spacer(1, 1*mm))

        parts = [p.strip() for p in sec_body.split("\n") if p.strip()]
        if not parts:
            story.append(Paragraph(_paragraph_html("　　暫無內容。"), styles["body"]))
            story.append(Spacer(1, 3*mm))
            continue

        flow = []
        for part in parts:
            if len(part) <= 24 and not part.startswith("　"):
                flow.append(Paragraph(_paragraph_html(part), styles["subsection"]))
            elif re.match(r"^[0-9一二三四五六七八九十]+[\.、]", part) or part.startswith(("（一）", "（二）", "（三）", "（四）", "（五）")):
                flow.append(Paragraph(_paragraph_html(part), styles["bullet"]))
            else:
                normalized = part if part.startswith("　　") else f"　　{part}"
                flow.append(Paragraph(_paragraph_html(normalized), styles["body"]))

        story.append(KeepTogether(flow))
        story.append(Spacer(1, 3*mm))

        if ("情境" in display_title or "scenario" in display_title.lower()) and not inserted_scenario:
            story.append(Spacer(1, 1*mm))
            story.append(_make_scenario_table(styles, sections))
            story.append(Spacer(1, 4*mm))
            inserted_scenario = True


def _build_doc(tmp_path: str, header_title: str, sublines: list[str], content: str):
    doc = SimpleDocTemplate(
        tmp_path,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm
    )
    styles = get_styles()
    story = []

    combined = "\n".join([s for s in sublines if s]) + ("\n" if sublines else "") + _safe_text(content)
    sections = _split_sections(combined)
    now = datetime.now(TZ_TAIPEI)

    _add_cover(story, _clean_title(header_title), now, sections, styles)
    _build_content_blocks(story, sections, styles)

    disclaimer = (
        "本報告內容僅供研究與內部討論參考，不構成任何投資建議或買賣依據。"
        "市場變化快速，投資人仍應依自身風險承受能力與投資目標審慎判斷。"
    )
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(_paragraph_html(disclaimer), styles["small"]))

    doc.build(story)


def generate_daily_report_pdf(report_text: str):
    now = datetime.now(TZ_TAIPEI)
    filename = f"財經日報_{now.strftime('%Y%m%d')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    _build_doc(
        tmp_path=tmp_path,
        header_title="每日財經日報",
        sublines=[now.strftime("%Y年%m月%d日")],
        content=report_text,
    )
    return tmp_path, filename


def generate_market_pdf(content: str):
    now = datetime.now(TZ_TAIPEI)
    filename = f"市場觀點_{now.strftime('%Y%m%d_%H%M')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    _build_doc(
        tmp_path=tmp_path,
        header_title="市場觀點報告",
        sublines=[now.strftime("%Y年%m月%d日 %H:%M")],
        content=content,
    )
    return tmp_path, filename


def generate_analysis_pdf(analysis: str, original_filename: str):
    now = datetime.now(TZ_TAIPEI)
    filename = f"分析報告_{now.strftime('%Y%m%d_%H%M')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    _build_doc(
        tmp_path=tmp_path,
        header_title=original_filename or "研究報告",
        sublines=["作者｜Albert", now.strftime("%Y年%m月%d日 %H:%M")],
        content=analysis,
    )
    return tmp_path, filename


def generate_news_pdf(news_report: str):
    now = datetime.now(TZ_TAIPEI)
    filename = f"財經新聞_{now.strftime('%Y%m%d')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    _build_doc(
        tmp_path=tmp_path,
        header_title="每日財經新聞摘要",
        sublines=[now.strftime("%Y年%m月%d日")],
        content=news_report,
    )
    return tmp_path, filename


def create_and_upload_pdf(pdf_type: str, content: str, original_filename: str = "") -> str:
    if pdf_type == "daily":
        tmp_path, filename = generate_daily_report_pdf(content)
    elif pdf_type == "market":
        tmp_path, filename = generate_market_pdf(content)
    elif pdf_type == "analysis":
        tmp_path, filename = generate_analysis_pdf(content, original_filename)
    elif pdf_type == "news":
        tmp_path, filename = generate_news_pdf(content)
    else:
        raise ValueError(f"Unknown pdf_type: {pdf_type}")

    return upload_to_drive(tmp_path, filename)
