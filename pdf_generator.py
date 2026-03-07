import os
import json
import tempfile
from datetime import datetime
import pytz

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TZ_TAIPEI = pytz.timezone("Asia/Taipei")

# ==============================
# Google Drive 上傳
# ==============================
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

    # 找或建立資料夾
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

    # 上傳檔案
    file_metadata = {
        "name": filename,
        "parents": [folder_id]
    }
    media = MediaFileUpload(file_path, mimetype="application/pdf")
    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()
    file_id = uploaded["id"]

    # 設定公開分享
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"}
    ).execute()

    return f"https://drive.google.com/file/d/{file_id}/view"

# ==============================
# PDF 樣式設定（支援中文）
# ==============================
def get_styles():
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Normal"],
        fontSize=18,
        leading=24,
        textColor=colors.HexColor("#1a1a2e"),
        spaceAfter=6,
        fontName="Helvetica-Bold",
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#555555"),
        spaceAfter=12,
        fontName="Helvetica",
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Normal"],
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#16213e"),
        spaceBefore=12,
        spaceAfter=4,
        fontName="Helvetica-Bold",
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        leading=16,
        textColor=colors.HexColor("#333333"),
        spaceAfter=4,
        fontName="Helvetica",
    )
    return title_style, subtitle_style, section_style, body_style

# ==============================
# 財經日報 PDF
# ==============================
def generate_daily_report_pdf(report_text: str) -> str:
    now = datetime.now(TZ_TAIPEI)
    filename = f"財經日報_{now.strftime('%Y%m%d')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)

    doc = SimpleDocTemplate(
        tmp_path,
        pagesize=A4,
        rightMargin=20*mm,
        leftMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=20*mm,
    )

    title_style, subtitle_style, section_style, body_style = get_styles()
    story = []

    # 標題
    story.append(Paragraph("每日財經日報", title_style))
    story.append(Paragraph(now.strftime("%Y年%m月%d日"), subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1a1a2e")))
    story.append(Spacer(1, 8))

    # 內容
    for line in report_text.split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        if line.startswith("一、") or line.startswith("二、") or line.startswith("三、") or \
           line.startswith("四、") or line.startswith("五、") or line.startswith("【"):
            story.append(Paragraph(line, section_style))
        else:
            story.append(Paragraph(line, body_style))

    # 頁尾
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Paragraph("本報告由龍蝦 AI 自動生成，僅供參考。", subtitle_style))

    doc.build(story)
    return tmp_path, filename

# ==============================
# 市場觀點文案 PDF
# ==============================
def generate_market_pdf(content: str) -> str:
    now = datetime.now(TZ_TAIPEI)
    filename = f"市場觀點_{now.strftime('%Y%m%d_%H%M')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)

    doc = SimpleDocTemplate(
        tmp_path,
        pagesize=A4,
        rightMargin=20*mm,
        leftMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=20*mm,
    )

    title_style, subtitle_style, section_style, body_style = get_styles()
    story = []

    story.append(Paragraph("市場觀點報告", title_style))
    story.append(Paragraph(now.strftime("%Y年%m月%d日 %H:%M"), subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1a1a2e")))
    story.append(Spacer(1, 8))

    for line in content.split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        story.append(Paragraph(line, body_style))

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Paragraph("本報告由龍蝦 AI 自動生成，僅供參考。", subtitle_style))

    doc.build(story)
    return tmp_path, filename

# ==============================
# 檔案分析 PDF
# ==============================
def generate_analysis_pdf(analysis: str, original_filename: str) -> str:
    now = datetime.now(TZ_TAIPEI)
    filename = f"分析報告_{now.strftime('%Y%m%d_%H%M')}.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)

    doc = SimpleDocTemplate(
        tmp_path,
        pagesize=A4,
        rightMargin=20*mm,
        leftMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=20*mm,
    )

    title_style, subtitle_style, section_style, body_style = get_styles()
    story = []

    story.append(Paragraph("檔案分析報告", title_style))
    story.append(Paragraph(f"原始檔案：{original_filename}", subtitle_style))
    story.append(Paragraph(now.strftime("%Y年%m月%d日 %H:%M"), subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1a1a2e")))
    story.append(Spacer(1, 8))

    for line in analysis.split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        if any(line.startswith(e) for e in ["📌", "📊", "⚖️", "🔭", "💡", "📋"]):
            story.append(Paragraph(line, section_style))
        else:
            story.append(Paragraph(line, body_style))

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Paragraph("本報告由龍蝦 AI 自動生成，僅供參考。", subtitle_style))

    doc.build(story)
    return tmp_path, filename

# ==============================
# 統一入口
# ==============================
def create_and_upload_pdf(pdf_type: str, content: str, original_filename: str = "") -> str:
    if pdf_type == "daily":
        tmp_path, filename = generate_daily_report_pdf(content)
    elif pdf_type == "market":
        tmp_path, filename = generate_market_pdf(content)
    elif pdf_type == "analysis":
        tmp_path, filename = generate_analysis_pdf(content, original_filename)
    else:
        raise ValueError(f"Unknown pdf_type: {pdf_type}")

    link = upload_to_drive(tmp_path, filename)
    return link
