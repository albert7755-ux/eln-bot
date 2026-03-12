import os
import json
import tempfile
from datetime import datetime
from html import escape as html_escape

import pytz
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TZ_TAIPEI = pytz.timezone("Asia/Taipei")

pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

FONT_NORMAL = "HeiseiKakuGo-W5"
FONT_BOLD = "HeiseiKakuGo-W5"

PRIMARY = colors.HexColor("#12263A")
ACCENT = colors.HexColor("#C9A84C")
TEXT = colors.HexColor("#2F3A45")
SUBTEXT = colors.HexColor("#667085")
PANEL = colors.HexColor("#EEF2F6")


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
        folder = service.files().create(
            body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"},
            fields="id"
        ).execute()
        folder_id = folder["id"]

    file_metadata = {"name": filename, "parents": [folder_id]}
    if file_path.endswith(".png"):
        mime = "image/png"
    elif file_path.endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    elif file_path.endswith(".pptx"):
        mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    else:
        mime = "application/pdf"

    media = MediaFileUpload(file_path, mimetype=mime)
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = uploaded["id"]
    service.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()
    return f"https://drive.google.com/file/d/{file_id}/view"


def get_styles():
    title_style = ParagraphStyle(
        "ReportTitle", fontSize=22, leading=30, textColor=PRIMARY,
        spaceAfter=6, fontName=FONT_BOLD,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", fontSize=11, leading=16, textColor=SUBTEXT,
        spaceAfter=10, fontName=FONT_NORMAL,
    )
    section_style = ParagraphStyle(
        "Section", fontSize=14, leading=21, textColor=PRIMARY,
        spaceBefore=10, spaceAfter=4, fontName=FONT_BOLD,
        backColor=PANEL, borderPadding=(4, 6, 4),
    )
    body_style = ParagraphStyle(
        "Body", fontSize=10.5, leading=17, textColor=TEXT,
        spaceAfter=4, fontName=FONT_NORMAL,
    )
    quote_style = ParagraphStyle(
        "Quote", fontSize=11, leading=18, textColor=PRIMARY,
        spaceAfter=6, fontName=FONT_BOLD,
    )
    return title_style, subtitle_style, section_style, body_style, quote_style


def _safe_text(text: str) -> str:
    if text is None:
        return ""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def _html(text: str) -> str:
    return html_escape(_safe_text(text)).replace("\n", "<br/>")


def _is_section(line: str) -> bool:
    prefixes = (
        "【", "一、", "二、", "三、", "四、", "五、", "六、", "七、", "八、", "九、", "十、",
        "📌", "📊", "⚖️", "🔭", "💡", "📋", "✅", "❌"
    )
    return line.startswith(prefixes)


def _is_bullet(line: str) -> bool:
    return line.startswith(("•", "-", "→"))


def _cover_story(title: str, sublines: list[str]):
    title_style, subtitle_style, _, _, _ = get_styles()
    story = []
    story.append(Spacer(1, 25 * mm))
    story.append(Paragraph(_html(title), ParagraphStyle(
        "CoverTitle", parent=title_style, fontSize=26, leading=34, textColor=PRIMARY, spaceAfter=8
    )))
    story.append(HRFlowable(width="55%", thickness=2, color=ACCENT))
    story.append(Spacer(1, 8 * mm))
    for sub in sublines:
        if sub:
            story.append(Paragraph(_html(sub), ParagraphStyle(
                "CoverSub", parent=subtitle_style, fontSize=12, leading=18, textColor=SUBTEXT, spaceAfter=4
            )))
    story.append(Spacer(1, 18 * mm))
    story.append(Paragraph(_html("研究報告｜Albert Claw Bot"), ParagraphStyle(
        "CoverTag", parent=subtitle_style, fontSize=11, textColor=ACCENT
    )))
    story.append(PageBreak())
    return story


def _append_content(story, content: str):
    _, _, section_style, body_style, quote_style = get_styles()
    for raw in _safe_text(content).split("\n"):
        line = raw.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        if _is_section(line):
            story.append(Paragraph(_html(line), section_style))
        elif _is_bullet(line):
            story.append(Paragraph(_html(line), body_style))
        elif line.startswith("結論") or line.startswith("建議"):
            story.append(Paragraph(_html(line), quote_style))
        else:
            story.append(Paragraph(_html(line), body_style))


def _build_doc(tmp_path: str, title: str, sublines: list[str], content: str):
    doc = SimpleDocTemplate(
        tmp_path,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    _, subtitle_style, _, _, _ = get_styles()
    story = []
    story.extend(_cover_story(title, sublines))
    story.append(Paragraph(_html(title), ParagraphStyle(
        "InnerTitle", fontName=FONT_BOLD, fontSize=18, leading=24, textColor=PRIMARY, spaceAfter=6
    )))
    for sub in sublines:
        if sub:
            story.append(Paragraph(_html(sub), subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY))
    story.append(Spacer(1, 6))
    _append_content(story, content)
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#B8C0CC")))
    disclaimer = (
        "本報告內容僅供參考，不構成投資建議或買賣依據。投資涉及風險，"
        "過去績效不代表未來表現，投資人應審慎評估自身風險承受能力，並自行負擔投資損益。"
    )
    story.append(Paragraph(_html(disclaimer), subtitle_style))
    doc.build(story)


def generate_daily_report_pdf(report_text: str):
    now = datetime.now(TZ_TAIPEI)
    filename = f"財經日報_{now.strftime('%Y%m%d')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    _build_doc(tmp_path, "每日財經日報", [now.strftime('%Y年%m月%d日')], report_text)
    return tmp_path, filename


def generate_market_pdf(content: str):
    now = datetime.now(TZ_TAIPEI)
    filename = f"市場觀點_{now.strftime('%Y%m%d_%H%M')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    _build_doc(tmp_path, "市場觀點報告", [now.strftime('%Y年%m月%d日 %H:%M')], content)
    return tmp_path, filename


def generate_analysis_pdf(analysis: str, original_filename: str):
    now = datetime.now(TZ_TAIPEI)
    filename = f"分析報告_{now.strftime('%Y%m%d_%H%M')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    _build_doc(
        tmp_path,
        "投資研究分析報告",
        [f"原始檔案：{original_filename or 'AI自動生成報告'}", now.strftime('%Y年%m月%d日 %H:%M')],
        analysis,
    )
    return tmp_path, filename


def generate_news_pdf(news_report: str):
    now = datetime.now(TZ_TAIPEI)
    filename = f"財經新聞_{now.strftime('%Y%m%d')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    _build_doc(tmp_path, "每日財經新聞摘要", [now.strftime('%Y年%m月%d日')], news_report)
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
