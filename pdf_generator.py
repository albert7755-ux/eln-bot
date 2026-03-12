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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
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
        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder"
        }
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
        fontSize=18,
        leading=26,
        textColor=colors.HexColor("#1A1A2E"),
        spaceAfter=6,
        fontName=FONT_BOLD,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        fontSize=11,
        leading=16,
        textColor=colors.HexColor("#555555"),
        spaceAfter=10,
        fontName=FONT_NORMAL,
    )
    section_style = ParagraphStyle(
        "Section",
        fontSize=13,
        leading=20,
        textColor=colors.HexColor("#16213E"),
        spaceBefore=10,
        spaceAfter=4,
        fontName=FONT_BOLD,
    )
    body_style = ParagraphStyle(
        "Body",
        fontSize=10,
        leading=16,
        textColor=colors.HexColor("#333333"),
        spaceAfter=3,
        fontName=FONT_NORMAL,
    )
    return title_style, subtitle_style, section_style, body_style


def _safe_text(text: str) -> str:
    if text is None:
        return ""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def _paragraph_html(line: str) -> str:
    safe = html_escape(_safe_text(line))
    return safe.replace("\n", "<br/>")


def _is_section_line(line: str) -> bool:
    prefixes = (
        "一、", "二、", "三、", "四、", "五、", "六、", "七、", "八、", "九、", "十、",
        "【", "📌", "📊", "⚖️", "🔭", "💡", "📋", "✅", "❌"
    )
    return line.startswith(prefixes)


def _append_lines(story, text: str, section_style, body_style):
    for raw_line in _safe_text(text).split("\n"):
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        html = _paragraph_html(line)
        story.append(Paragraph(html, section_style if _is_section_line(line) else body_style))


def _build_doc(tmp_path: str, header_title: str, sublines: list[str], content: str):
    doc = SimpleDocTemplate(
        tmp_path,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm
    )

    title_style, subtitle_style, section_style, body_style = get_styles()
    story = []

    story.append(Paragraph(_paragraph_html(header_title), title_style))
    for sub in sublines:
        if sub:
            story.append(Paragraph(_paragraph_html(sub), subtitle_style))

    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1A1A2E")))
    story.append(Spacer(1, 8))

    _append_lines(story, content, section_style, body_style)

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    disclaimer = (
        "本報告內容僅供參考，不構成投資建議或買賣依據。"
        "投資涉及風險，過去績效不代表未來表現，投資人應審慎評估自身風險承受能力，並自行負擔投資損益。"
    )
    story.append(Paragraph(_paragraph_html(disclaimer), subtitle_style))
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
        header_title="檔案分析報告",
        sublines=[
            f"原始檔案：{original_filename or 'AI自動生成報告'}",
            now.strftime("%Y年%m月%d日 %H:%M")
        ],
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
