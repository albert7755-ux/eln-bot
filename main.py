import os
import re
import json
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request, HTTPException

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage

from openai import OpenAI

from sqlalchemy import create_engine, text

from autotracking_core import calculate_from_file


# ==============================
# ENV
# ==============================
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # 沒有也能跑，只是 AI 不啟用
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

DATABASE_URL = os.getenv("DATABASE_URL")  # DB 永久記憶必須要有
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy 標準寫法
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE env vars: LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN")

if not DATABASE_URL:
    raise RuntimeError("Missing env var: DATABASE_URL (DB 永久記憶版需要它)")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = FastAPI()
VERSION = "eln-autotracking-db-2026-03-05"

TZ_TAIPEI = timezone(timedelta(hours=8))

# ==============================
# DB
# ==============================
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

def init_db():
    with engine.begin() as conn:
        # 每個聊天室（user/group/room）最後一次計算的 summary
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_last_report (
            chat_key TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """))

        # 每個聊天室、每個商品代號的 detail
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_detail (
            chat_key TEXT NOT NULL,
            bond_id TEXT NOT NULL,
            detail TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (chat_key, bond_id)
        );
        """))

        # 用來存 top5（可選，但實用）
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_top5 (
            chat_key TEXT NOT NULL,
            line_no INT NOT NULL,
            text_line TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (chat_key, line_no)
        );
        """))

        # 紀錄哪些聊天室正在等檔案（/calc 後）
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
        row = conn.execute(text("SELECT await_file FROM eln_session WHERE chat_key=:k"), {"k": chat_key}).fetchone()
    return bool(row and row[0])

def db_save_result(chat_key: str, summary: str, top5_lines: list[str], detail_map: dict[str, str]):
    with engine.begin() as conn:
        # summary
        conn.execute(text("""
        INSERT INTO eln_last_report(chat_key, summary, updated_at)
        VALUES (:k, :s, NOW())
        ON CONFLICT (chat_key) DO UPDATE
        SET summary=:s, updated_at=NOW()
        """), {"k": chat_key, "s": summary})

        # top5: 先清再寫
        conn.execute(text("DELETE FROM eln_top5 WHERE chat_key=:k"), {"k": chat_key})
        for i, line in enumerate(top5_lines, start=1):
            conn.execute(text("""
            INSERT INTO eln_top5(chat_key, line_no, text_line, updated_at)
            VALUES (:k, :n, :t, NOW())
            """), {"k": chat_key, "n": i, "t": line})

        # detail: 先清再寫（避免舊商品殘留）
        conn.execute(text("DELETE FROM eln_detail WHERE chat_key=:k"), {"k": chat_key})
        for bond_id, detail in detail_map.items():
            conn.execute(text("""
            INSERT INTO eln_detail(chat_key, bond_id, detail, updated_at)
            VALUES (:k, :b, :d, NOW())
            """), {"k": chat_key, "b": bond_id, "d": detail})

def db_get_report(chat_key: str) -> str | None:
    with engine.begin() as conn:
        row = conn.execute(text("SELECT summary FROM eln_last_report WHERE chat_key=:k"), {"k": chat_key}).fetchone()
    return row[0] if row else None

def db_get_top5(chat_key: str) -> list[str]:
    with engine.begin() as conn:
        rows = conn.execute(text("""
        SELECT line_no, text_line
        FROM eln_top5
        WHERE chat_key=:k
        ORDER BY line_no ASC
        """), {"k": chat_key}).fetchall()
    return [r[1] for r in rows] if rows else []

def db_find_detail(chat_key: str, query: str) -> tuple[str | None, str | None, list[str]]:
    """
    回傳：
    - matched_id
    - detail_text
    - candidates（如果有多筆）
    """
    q_norm = query.strip().upper()
    if not q_norm:
        return None, None, []

    with engine.begin() as conn:
        rows = conn.execute(text("""
        SELECT bond_id
        FROM eln_detail
        WHERE chat_key=:k
        """), {"k": chat_key}).fetchall()
    keys = [r[0] for r in rows] if rows else []

    if not keys:
        return None, None, []

    norm_map = {k.strip().upper(): k for k in keys}
    if q_norm in norm_map:
        real = norm_map[q_norm]
        with engine.begin() as conn:
            row = conn.execute(text("""
            SELECT detail FROM eln_detail
            WHERE chat_key=:k AND bond_id=:b
            """), {"k": chat_key, "b": real}).fetchone()
        return real, (row[0] if row else None), []

    hits = [k for k in keys if q_norm in k.strip().upper()]
    if len(hits) == 1:
        real = hits[0]
        with engine.begin() as conn:
            row = conn.execute(text("""
            SELECT detail FROM eln_detail
            WHERE chat_key=:k AND bond_id=:b
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
    return {"service": "eln-bot", "version": VERSION, "ai_enabled": bool(client)}

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

    if df is not None and getattr(df, "empty", True) is False:
        top = df.head(5)
        for _, r in top.iterrows():
            try:
                status_first = str(r.get("狀態", "")).splitlines()[0]
            except Exception:
                status_first = str(r.get("狀態", ""))

            top5_lines.append(f"● {r.get('債券代號','-')} {r.get('Type','-')}｜{status_first}")

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

            detail_text = (
                f"【商品】{_id}\n"
                f"類型: {r.get('Type','-')}\n"
                f"客戶: {r.get('Name','-')}\n"
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

    return summary, top5_lines, detail_map

# ==============================
# AI fallback
# ==============================
def ai_reply(user_text: str) -> str:
    if not client:
        return "（AI 模式尚未開啟：缺少 OPENAI_API_KEY。你可以輸入 /help，或用 /calc 上傳 Excel。）"

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.35,
        max_tokens=700,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是「龍蝦」LINE助理。回答要務實、可直接拿去用。\n"
                    "若問題跟 ELN 追蹤/KO/KI/狀態相關，可提示使用 /calc 上傳 Excel，或 /detail 查商品。"
                ),
            },
            {"role": "user", "content": user_text},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


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

        print("[TEXT]", ck, repr(text_raw))

        # 支援 /help 或 help
        if tl.startswith("/"):
            cmd = tl[1:]
            raw_cmd = text_raw[1:]
        else:
            cmd = tl
            raw_cmd = text_raw

        # HELP
        if cmd in ("help", "?", "指令", "幫助"):
            msg = (
                "可用指令：\n"
                "/help\n"
                "/calc  ：提示你上傳 Excel（收到檔案後自動計算並永久保存）\n"
                "/report：直接顯示最近一次結果（不用重上傳）\n"
                "/detail <商品代號>：查單筆完整 KO/KI/狀態（不用重上傳，支援模糊）\n"
                "/settarget：把目前聊天室設為預設推播對象\n"
                "\n"
                "其他任何文字：會進 AI 對話模式"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return

        # SETTARGET（可選）
        if cmd == "settarget":
            targets = load_targets()
            if event.source.type == "group":
                targets["default"] = event.source.group_id
                targets["default_type"] = "group"
                save_targets(targets)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已設定此群組為預設推播對象"))
            elif event.source.type == "room":
                targets["default"] = event.source.room_id
                targets["default_type"] = "room"
                save_targets(targets)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已設定此聊天室為預設推播對象"))
            else:
                targets["default"] = event.source.user_id
                targets["default_type"] = "user"
                save_targets(targets)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已設定您為預設推播對象"))
            return

        # CALC：檔案模式（/calc 之後才接受檔案）
        if cmd.startswith("calc") or cmd.startswith("clac"):
            # 若你仍想保留算式：/calc 1+2*3，就留著；不想要就刪掉這段
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

        # REPORT：直接讀 DB 最近一次 summary
        if cmd == "report":
            summary = db_get_report(ck)
            if not summary:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前尚無已保存結果，請先 /calc 上傳 Excel。"))
                return
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary[:4900]))
            return

        # DETAIL：從 DB 查
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
                # 多筆或找不到時給候選清單
                sample = "\n".join([f"• {c}" for c in candidates[:20]])
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"請再精準一點，候選代號如下：\n{sample}"[:4900])
                )
                return

            # 完全沒有資料
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="查不到該代號或目前沒有已保存結果。請先 /calc 上傳 Excel。")
            )
            return

        # 非指令：AI 模式
        reply = ai_reply(text_raw)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply[:4900]))

    except Exception as e:
        print("[ERROR] handle_text_message:", e)
        print(traceback.format_exc())
        try:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="我收到訊息但處理時出錯了。我已記錄錯誤；你可以先輸入 /help。")
            )
        except Exception:
            pass


# ==============================
# File message handler
# ==============================
BASE_DIR = Path("/tmp")
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    try:
        ck = chat_key_of(event)
        filename = getattr(event.message, "file_name", "") or ""
        size = getattr(event.message, "file_size", None)

        print("[FILE]", ck, filename, size)

        if not db_is_await(ck):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="我收到檔案了，但你尚未輸入 /calc。請先打 /calc，再上傳 Excel。")
            )
            return

        # 下載檔案內容（只用來當次計算，不做永久保存）
        message_id = event.message.id
        content = line_bot_api.get_message_content(message_id)

        # 存到 /tmp（就算重啟會不見也沒關係，因為結果會存 DB）
        tmp_path = UPLOAD_DIR / f"upload_{int(datetime.now(TZ_TAIPEI).timestamp())}.xlsx"
        with open(tmp_path, "wb") as f:
            for chunk in content.iter_content():
                f.write(chunk)

        # 先關掉 await，避免重複狀態
        db_set_await(ck, False)

        # 計算
        summary, top5_lines, detail_map = run_autotracking(str(tmp_path))

        # 存 DB（永久）
        db_save_result(ck, summary, top5_lines, detail_map)

        # 回覆 summary
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=(summary or "已收到檔案，但沒有產出內容")[:4900]))

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
                TextSendMessage(text="我收到檔案但處理失敗了。請確認是 .xlsx 檔，或稍後再試。")
            )
        except Exception:
            pass
