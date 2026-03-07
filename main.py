import os
import re
import json
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage, ImageMessage
from sqlalchemy import create_engine, text
from autotracking_core import calculate_from_file
from market_content_generator import generate_market_content
import anthropic

# ==============================
# ENV
# ==============================
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE env vars: LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN")
if not DATABASE_URL:
    raise RuntimeError("Missing env var: DATABASE_URL")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("Missing env var: ANTHROPIC_API_KEY")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
app = FastAPI()

VERSION = "eln-autotracking-db-v3-2026-03-05"
TZ_TAIPEI = timezone(timedelta(hours=8))

# ==============================
# DB
# ==============================
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_last_report (
            chat_key TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_detail (
            chat_key TEXT NOT NULL,
            bond_id TEXT NOT NULL,
            detail TEXT NOT NULL,
            agent_name TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (chat_key, bond_id)
        );
        """))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_top5 (
            chat_key TEXT NOT NULL,
            line_no INT NOT NULL,
            text_line TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (chat_key, line_no)
        );
        """))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_session (
            chat_key TEXT PRIMARY KEY,
            await_file BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """))

init_db()

def db_set_await(chat_key: str, await_file: bool):
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO eln_session(chat_key, await_file, updated_at)
        VALUES (:k, :a, NOW())
        ON CONFLICT (chat_key) DO UPDATE
        SET await_file=:a, updated_at=NOW()
        """), {"k": chat_key, "a": bool(await_file)})

def db_is_await(chat_key: str) -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT await_file FROM eln_session WHERE chat_key=:k"),
            {"k": chat_key}
        ).fetchone()
    return bool(row and row[0])

def db_save_result(chat_key: str, summary: str, top5_lines: list[str], detail_map: dict[str, str], agent_name_map: dict[str, str] = {}):
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO eln_last_report(chat_key, summary, updated_at)
        VALUES (:k, :s, NOW())
        ON CONFLICT (chat_key) DO UPDATE
        SET summary=:s, updated_at=NOW()
        """), {"k": chat_key, "s": summary})
        conn.execute(text("DELETE FROM eln_top5 WHERE chat_key=:k"), {"k": chat_key})
        for i, line in enumerate(top5_lines, start=1):
            conn.execute(text("""
            INSERT INTO eln_top5(chat_key, line_no, text_line, updated_at)
            VALUES (:k, :n, :t, NOW())
            """), {"k": chat_key, "n": i, "t": line})
        conn.execute(text("DELETE FROM eln_detail WHERE chat_key=:k"), {"k": chat_key})
        for bond_id, detail in detail_map.items():
            agent = agent_name_map.get(bond_id, "-")
            conn.execute(text("""
            INSERT INTO eln_detail(chat_key, bond_id, detail, agent_name, updated_at)
            VALUES (:k, :b, :d, :a, NOW())
            """), {"k": chat_key, "b": bond_id, "d": detail, "a": agent})

def db_get_report(chat_key: str) -> str | None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT summary FROM eln_last_report WHERE chat_key=:k"),
            {"k": chat_key}
        ).fetchone()
    return row[0] if row else None

def db_list_bonds(chat_key: str, limit: int = 50) -> list[tuple[str, str]]:
    with engine.begin() as conn:
        rows = conn.execute(text("""
        SELECT bond_id, COALESCE(agent_name, '-')
        FROM eln_detail
        WHERE chat_key=:k
        ORDER BY bond_id ASC
        LIMIT :lim
        """), {"k": chat_key, "lim": int(limit)}).fetchall()
    return [(r[0], r[1]) for r in rows] if rows else []

def db_find_detail(chat_key: str, query: str) -> tuple[str | None, str | None, list[str]]:
    q_norm = query.strip().upper()
    if not q_norm:
        return None, None, []
    with engine.begin() as conn:
        rows = conn.execute(text("""
        SELECT bond_id FROM eln_detail WHERE chat_key=:k
        """), {"k": chat_key}).fetchall()
    keys = [r[0] for r in rows] if rows else []
    if not keys:
        return None, None, []
    norm_map = {k.strip().upper(): k for k in keys}
    if q_norm in norm_map:
        real = norm_map[q_norm]
        with engine.begin() as conn:
            row = conn.execute(text("""
            SELECT detail FROM eln_detail WHERE chat_key=:k AND bond_id=:b
            """), {"k": chat_key, "b": real}).fetchone()
        return real, (row[0] if row else None), []
    hits = [k for k in keys if q_norm in k.strip().upper()]
    if len(hits) == 1:
        real = hits[0]
        with engine.begin() as conn:
            row = conn.execute(text("""
            SELECT detail FROM eln_detail WHERE chat_key=:k AND bond_id=:b
            """), {"k": chat_key, "b": real}).fetchone()
        return real, (row[0] if row else None), []
    if len(hits) > 1:
        return None, None, hits[:20]
    return None, None, keys[:20]

# ==============================
# Optional: store default push target
# ==============================
BASE_DIR = Path("/tmp")
TARGET_FILE = BASE_DIR / "targets.json"

def _read_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def _write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def load_targets():
    return _read_json(TARGET_FILE, {})

def save_targets(data: dict):
    _write_json(TARGET_FILE, data)

# ==============================
# Health check
# ==============================
@app.get("/")
def root():
    return {"status": "ok", "service": "eln-bot", "webhook": "/callback"}

@app.get("/whoami")
def whoami():
    return {"service": "eln-bot", "version": VERSION}

# ==============================
# Chat key
# ==============================
def chat_key_of(event) -> str:
    if event.source.type == "group":
        return f"group:{event.source.group_id}"
    if event.source.type == "room":
        return f"room:{event.source.room_id}"
    return f"user:{event.source.user_id}"

# ==============================
# Adapter: core -> (summary, top5, detail_map)
# ==============================
def run_autotracking(file_path: str, lookback_days: int = 3, notify_ki_daily: bool = True):
    out = calculate_from_file(
        file_path=file_path,
        lookback_days=lookback_days,
        notify_ki_daily=notify_ki_daily
    )
    df = out.get("results_df")
    report = out.get("report_text", "") or ""
    top5_lines: list[str] = []
    detail_map: dict[str, str] = {}
    agent_name_map: dict[str, str] = {}
    if df is not None and getattr(df, "empty", True) is False:
        top = df.head(5)
        for _, r in top.iterrows():
            try:
                status_first = str(r.get("狀態", "")).splitlines()[0]
            except Exception:
                status_first = str(r.get("狀態", ""))
            top5_lines.append(
                f"● {r.get('債券代號','-')} {r.get('Type','-')}｜{status_first}"
            )
        for _, r in df.iterrows():
            _id = str(r.get("債券代號", "")).strip()
            if not _id:
                continue
            t_details = []
            for c in df.columns:
                if str(c).endswith("_Detail"):
                    v = r.get(c, "")
                    if v:
                        t_details.append(str(v))
            agent = str(r.get("Name", "-") or "-").strip()
            agent_name_map[_id] = agent
            detail_text = (
                f"【商品】{_id}\n"
                f"類型: {r.get('Type','-')}\n"
                f"理專: {agent}\n"
                f"交易日: {r.get('交易日','-')}\n"
                f"KO設定: {r.get('KO設定','-')}\n"
                f"最差表現: {r.get('最差表現','-')}\n"
                f"----------------\n"
                f"{r.get('狀態','')}\n"
                f"----------------\n"
                + ("\n\n".join(t_details) if t_details else "")
            )
            detail_map[_id] = detail_text
    summary = report
    if top5_lines:
        summary += "\n\n【前5筆摘要】\n" + "\n".join(top5_lines)
    return summary, top5_lines, detail_map, agent_name_map

# ==============================
# AI fallback (Claude)
# ==============================
def ai_reply(user_text: str) -> str:
    resp = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=(
            "你是「龍蝦」LINE助理，專門服務投資輔銷人員，具備深厚的財經與投資專業知識。\n\n"
            "回答原則:\n"
            "1. 回答要有深度，針對問題提供完整的背景、現況、影響與展望\n"
            "2. 結構清楚，適當分段，讓人一眼看懂重點\n"
            "3. 語氣專業客觀，不偏多也不偏空，呈現市場綜合觀點與已知資訊\n"
            "4. 遇到市場、產品、趨勢類問題，要包含: 定義說明、市場現況、主要特性、適合投資人類型、當前市場綜合觀點\n"
            "5. 回答長度要足夠，不要過於簡短，讓提問者真正獲得有價值的資訊\n"
            "6. 客觀呈現機會與風險兩面，讓提問者自行判斷\n"
            "7. 若問題跟 ELN 追蹤/KO/KI/狀態相關，可提示使用 /calc 上傳 Excel 或 /detail 查商品\n"
            "8. 其他財經問題請直接深入回答，不要硬往 ELN 引導\n"
            "9. 格式規定: 絕對禁止使用 Markdown 語法，不可出現 ## 、** 、--- 等符號\n"
            "   段落標題請用 emoji，例如 📌 市場定義、📊 市場現況、⚖️ 機會與風險、🔭 後市展望\n"
            "   條列項目用 • 或 → 符號\n"
        ),
        messages=[{"role": "user", "content": user_text}]
    )
    return (resp.content[0].text or "").strip()

# ==============================
# Webhook endpoint
# ==============================
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

# ==============================
# Text message handler
# ==============================
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    try:
        text_raw = (event.message.text or "").strip()
        tl = text_raw.lower().strip()
        ck = chat_key_of(event)
        is_group = event.source.type in ("group", "room")
        print("[TEXT]", ck, repr(text_raw))

        if tl.startswith("/"):
            cmd = tl[1:]
            raw_cmd = text_raw[1:]
        else:
            cmd = tl
            raw_cmd = text_raw

        # 群組模式：非指令訊息直接靜音
        if is_group and not tl.startswith("/"):
            return

        # 群組模式 HELP：只顯示查詢相關指令
        if cmd in ("help", "?", "指令", "幫助"):
            if is_group:
                msg = (
                    "群組可用指令：\n"
                    "/detail <商品代號>：查詢標的完整狀況（支援模糊搜尋）\n"
                    "/list：列出所有可查商品代號\n"
                )
            else:
                msg = (
                    "可用指令：\n"
                    "/help\n"
                    "/calc：提示你上傳 Excel（收到檔案後自動計算並永久保存）\n"
                    "/report：直接顯示最近一次結果（不用重上傳）\n"
                    "/detail <商品代號>：查單筆完整 KO/KI/狀態（支援模糊）\n"
                    "/list：列出目前可查商品代號（前50）\n"
                    "/market <新聞+標的>：自動生成客戶推播文案\n"
                    "/pdf daily：產生今日財經日報 PDF\n"
                    "/pdf market <內容>：產生市場觀點 PDF\n"
                    "/daily cache：回傳今天早上已產生的日報\n"
                    "/settarget：把目前聊天室設為預設推播對象\n"
                    "\n"
                    "其他任何文字：Claude AI 對話模式"
                )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return

        # DAILY REPORT
        if cmd.startswith("daily"):
            parts = text_raw.split(" ", 1)
            use_cache = len(parts) > 1 and parts[1].strip().lower() == "cache"

            if use_cache:
                try:
                    from sqlalchemy import create_engine, text as sa_text
                    db_url = DATABASE_URL
                    if db_url.startswith("postgres://"):
                        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
                    elif db_url.startswith("postgresql://"):
                        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
                    eng = create_engine(db_url, pool_pre_ping=True)
                    with eng.begin() as conn:
                        row = conn.execute(sa_text("""
                        SELECT report_text FROM daily_report_cache
                        ORDER BY created_at DESC LIMIT 1
                        """)).fetchone()
                    if row:
                        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=row[0][:4900]))
                    else:
                        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="尚無快取日報，請用 /daily 產生最新版本。"))
                except Exception as e:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"讀取快取失敗: {e}"))
                return

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="產生中，請稍候約30秒..."))
            try:
                from daily_report import generate_report, save_report_to_db
                report = generate_report()
                save_report_to_db(report)
                line_bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=report[:4900]))
            except Exception as e:
                line_bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"日報產生失敗: {e}"))
            return

        # SETTARGET
        if cmd == "settarget":
            targets = load_targets()
            if event.source.type == "group":
                targets["default"] = event.source.group_id
                targets["default_type"] = "group"
                save_targets(targets)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="已設定此群組為預設推播對象"))
            elif event.source.type == "room":
                targets["default"] = event.source.room_id
                targets["default_type"] = "room"
                save_targets(targets)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="已設定此聊天室為預設推播對象"))
            else:
                targets["default"] = event.source.user_id
                targets["default_type"] = "user"
                save_targets(targets)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="已設定您為預設推播對象"))
            return

        # LIST
        if cmd == "list":
            bonds = db_list_bonds(ck, limit=50)
            if not bonds:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前尚無已保存結果。請先 /calc 上傳 Excel。"))
                return
            msg = "目前可查商品代號：\n" + "\n".join([f"• {bond_id} - {agent}" for bond_id, agent in bonds])
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg[:4900]))
            return

        # CALC
        if cmd.startswith("calc") or cmd.startswith("clac"):
            parts = raw_cmd.split(" ", 1)
            if len(parts) > 1 and parts[1].strip():
                expr = parts[1].strip()
                if not re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s]+", expr):
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="算式格式錯誤"))
                    return
                try:
                    result = eval(expr, {"__builtins__": {}})
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"{expr} = {result}"))
                    return
                except Exception:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="算式錯誤"))
                    return
            db_set_await(ck, True)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="收到！請直接把 Excel 檔案傳給我（用 LINE 的『檔案』上傳），我會計算並保存結果。")
            )
            return

        # REPORT
        if cmd == "report":
            summary = db_get_report(ck)
            if not summary:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前尚無已保存結果，請先 /calc 上傳 Excel。"))
                return
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary[:4900]))
            return

        # MARKET CONTENT
        if cmd.startswith("market"):
            parts = text_raw.split(" ", 1)
            if len(parts) < 2 or not parts[1].strip():
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="請輸入新聞內容和推薦標的\n\n格式範例:\n/market 美股反彈，高盛喊買。\n\n推薦標的: PIMCO收益增長、駿利平衡基金"
                    )
                )
                return
            news_text = parts[1].strip()
            content = generate_market_content(news_text)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=content[:4900])
            )
            return

        # PDF 生成
        if cmd.startswith("pdf"):
            from pdf_generator import create_and_upload_pdf
            parts = text_raw.split(" ", 2)
            sub = parts[1].strip().lower() if len(parts) > 1 else ""

            if sub == "daily":
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="產生財經日報 PDF 中，請稍候約30秒..."))
                try:
                    from daily_report import generate_report
                    report = generate_report()
                    link = create_and_upload_pdf("daily", report)
                    line_bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"📄 財經日報 PDF 已產生！\n\n{link}"))
                except Exception as e:
                    line_bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"PDF 產生失敗: {e}"))
                return

            if sub == "market":
                if len(parts) < 3 or not parts[2].strip():
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入內容\n範例：/pdf market 美股反彈，推薦PIMCO"))
                    return
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="產生市場觀點 PDF 中，請稍候..."))
                try:
                    content = generate_market_content(parts[2].strip())
                    link = create_and_upload_pdf("market", content)
                    line_bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"📄 市場觀點 PDF 已產生！\n\n{link}"))
                except Exception as e:
                    line_bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"PDF 產生失敗: {e}"))
                return

            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="PDF 指令用法：\n/pdf daily → 財經日報 PDF\n/pdf market <內容> → 市場觀點 PDF"
            ))
            return

        # DETAIL
        if cmd.startswith("detail"):
            parts = text_raw.split(" ", 1)
            if len(parts) < 2 or not parts[1].strip():
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入：/detail 商品代號（例：/detail U123）"))
                return
            query = parts[1].strip()
            matched_id, detail, candidates = db_find_detail(ck, query)
            if detail:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=detail[:4900]))
                return
            if candidates and matched_id is None:
                sample = "\n".join([f"• {c}" for c in candidates[:20]])
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"請再精準一點，候選代號如下：\n{sample}"[:4900])
                )
                return
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="查不到該代號或目前沒有已保存結果。請先 /calc 上傳 Excel。")
            )
            return

        # AI fallback (Claude)
        reply = ai_reply(text_raw)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply[:4900]))

    except Exception as e:
        print("[ERROR] handle_text_message:", e)
        print(traceback.format_exc())
        try:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="我收到訊息但處理時出錯了。你可以先輸入 /help。")
            )
        except Exception:
            pass

# ==============================
# File message handler
# ==============================
UPLOAD_DIR = Path("/tmp/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

def extract_text_from_file(file_path: str, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    text = ""

    try:
        if ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"

        elif ext == ".docx":
            from docx import Document
            doc = Document(file_path)
            for para in doc.paragraphs:
                if para.text.strip():
                    text += para.text + "\n"

        elif ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(file_path)
            for i, slide in enumerate(prs.slides, start=1):
                text += f"\n--- 第 {i} 頁 ---\n"
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        text += shape.text + "\n"

        elif ext in (".xlsx", ".xls"):
            import pandas as pd
            xl = pd.ExcelFile(file_path)
            for sheet in xl.sheet_names:
                df = xl.parse(sheet)
                text += f"\n--- 工作表: {sheet} ---\n"
                text += df.to_string(index=False) + "\n"

    except Exception as e:
        print(f"Extract error: {e}")
        text = ""

    return text.strip()

def analyze_file_with_claude(text: str, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    type_map = {
        ".pdf": "PDF文件",
        ".docx": "Word文件",
        ".pptx": "PowerPoint簡報",
        ".xlsx": "Excel試算表",
        ".xls": "Excel試算表",
    }
    file_type = type_map.get(ext, "文件")

    prompt = (
        f"我收到一份{file_type}，內容如下:\n\n"
        f"{text[:6000]}\n\n"
        "請幫我:\n"
        "1. 用2-3句話說明這份文件的主題與目的\n"
        "2. 條列出5-8個最重要的重點\n"
        "3. 如果有數據或結論，特別標示出來\n"
        "4. 最後一句話說明這份文件的主要價值或建議行動\n\n"
        "格式規定: 不使用 Markdown 符號（禁止 ## ** --- 等），標題用 emoji，條列用 •"
    )

    resp = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return (resp.content[0].text or "").strip()

def analyze_image_with_claude(image_data: bytes, media_type: str) -> str:
    import base64
    image_b64 = base64.b64encode(image_data).decode("utf-8")

    resp = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_b64
                    }
                },
                {
                    "type": "text",
                    "text": (
                        "請分析這張圖片，幫我:\n"
                        "1. 說明圖片的主要內容\n"
                        "2. 如果有文字或數據，擷取重要資訊\n"
                        "3. 條列出重點\n"
                        "格式規定: 不使用 Markdown 符號，標題用 emoji，條列用 •"
                    )
                }
            ]
        }]
    )
    return (resp.content[0].text or "").strip()

@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    try:
        ck = chat_key_of(event)
        filename = getattr(event.message, "file_name", "") or ""
        ext = Path(filename).suffix.lower()
        print("[FILE]", ck, filename)

        message_id = event.message.id
        content = line_bot_api.get_message_content(message_id)
        tmp_path = UPLOAD_DIR / f"upload_{int(datetime.now(TZ_TAIPEI).timestamp())}{ext}"
        with open(tmp_path, "wb") as f:
            for chunk in content.iter_content():
                f.write(chunk)

        # ELN 模式：有先打 /calc 且是 Excel
        if ext in (".xlsx", ".xls") and db_is_await(ck):
            db_set_await(ck, False)
            summary, top5_lines, detail_map, agent_name_map = run_autotracking(str(tmp_path))
            db_save_result(ck, summary, top5_lines, detail_map, agent_name_map)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=(summary or "已收到檔案，但沒有產出內容")[:4900])
            )
            return

        # 通用分析模式
        if ext in (".xlsx", ".xls", ".pdf", ".docx", ".pptx"):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"收到！正在分析 {filename}，請稍候...")
            )
            text = extract_text_from_file(str(tmp_path), filename)
            if not text:
                line_bot_api.push_message(
                    ck.split(":", 1)[1],
                    TextSendMessage(text="檔案解析失敗，可能是掃描版 PDF 或格式不支援。")
                )
                return
            analysis = analyze_file_with_claude(text, filename)
            line_bot_api.push_message(
                ck.split(":", 1)[1],
                TextSendMessage(text=analysis[:4900])
            )
            # 自動產生PDF
            try:
                from pdf_generator import create_and_upload_pdf
                link = create_and_upload_pdf("analysis", analysis, filename)
                line_bot_api.push_message(
                    ck.split(":", 1)[1],
                    TextSendMessage(text=f"📄 分析報告 PDF：\n{link}")
                )
            except Exception as e:
                print(f"PDF upload error: {e}")
            return

        # 不支援的格式
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"目前支援的檔案格式: PDF、Word、PowerPoint、Excel\n收到的格式 {ext} 暫不支援。")
        )

    except Exception as e:
        print("[ERROR] handle_file_message:", e)
        print(traceback.format_exc())
        try:
            db_set_await(chat_key_of(event), False)
        except Exception:
            pass
        try:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="檔案處理時出錯了，請稍後再試。")
            )
        except Exception:
            pass

# ==============================
# Image message handler
# ==============================
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    try:
        ck = chat_key_of(event)
        print("[IMAGE]", ck)

        message_id = event.message.id
        content = line_bot_api.get_message_content(message_id)

        image_data = b""
        for chunk in content.iter_content():
            image_data += chunk

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="收到圖片！正在分析，請稍候...")
        )

        analysis = analyze_image_with_claude(image_data, "image/jpeg")
        line_bot_api.push_message(
            ck.split(":", 1)[1],
            TextSendMessage(text=analysis[:4900])
        )

    except Exception as e:
        print("[ERROR] handle_image_message:", e)
        print(traceback.format_exc())
        try:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="圖片處理時出錯了，請稍後再試。")
            )
        except Exception:
            pass
