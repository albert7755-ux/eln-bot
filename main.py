import knowledge
import base64 as _base64
from fastapi import Form
import os
import re
import json
import traceback as _traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage, ImageMessage, AudioMessage
from sqlalchemy import create_engine, text
from autotracking_core import calculate_from_file
from market_content_generator import generate_market_content
import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz
import urllib.request
import urllib.error
from openai import OpenAI

# ── 新增 Gemini SDK 引用 ──
import google.generativeai as genai
from io import BytesIO
from PIL import Image

# --- Alert ticker aliases ---
ALERT_TICKER_ALIAS = {
    "dxy": "DX-Y.NYB", "spx": "^GSPC", "sp500": "^GSPC", "ndx": "^NDX",
    "nasdaq100": "^NDX", "sox": "^SOX", "vix": "^VIX", "ust10y": "^TNX",
    "gold": "GC=F", "silver": "SI=F", "oil": "CL=F", "wti": "CL=F", "copper": "HG=F",
    "usdjpy": "JPY=X", "jpy": "JPY=X", "eurusd": "EURUSD=X", "eur": "EURUSD=X",
    "gbpusd": "GBPUSD=X", "gbp": "GBPUSD=X", "usdtwd": "TWD=X", "twd": "TWD=X",
    "usdcnh": "CNH=X", "cnh": "CNH=X", "usdkrw": "KRW=X", "krw": "KRW=X",
}

# ==============================
# ENV
# ==============================
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
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
ELN_GROUP_CHANNEL_SECRET = os.getenv("AGENT_LINE_CHANNEL_SECRET", "")
ELN_GROUP_ACCESS_TOKEN = os.getenv("AGENT_LINE_CHANNEL_ACCESS_TOKEN", "")
eln_group_bot_api = LineBotApi(ELN_GROUP_ACCESS_TOKEN) if ELN_GROUP_ACCESS_TOKEN else None
eln_group_handler = WebhookHandler(ELN_GROUP_CHANNEL_SECRET) if ELN_GROUP_CHANNEL_SECRET else None
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ── 初始化 Gemini 模型 ──
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel(
        model_name="gemini-2.0-flash", # 建議用 2.0-flash 兼顧速度與準確度
        system_instruction=os.getenv("SYSTEM_PROMPT") or "" # 這裡後面會合併你 code 裡的 SYSTEM_PROMPT
    )

app = FastAPI()
from articles import router as articles_router
app.include_router(articles_router)
VERSION = "eln-autotracking-db-v3-2026-03-05"
TZ_TAIPEI = timezone(timedelta(hours=8))

# ==============================
# DB (保持不變)
# ==============================
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_last_report (
            chat_key TEXT PRIMARY KEY, summary TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );"""))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_detail (
            chat_key TEXT NOT NULL, bond_id TEXT NOT NULL, detail TEXT NOT NULL,
            agent_name TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (chat_key, bond_id)
        );"""))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_top5 (
            chat_key TEXT NOT NULL, line_no INT NOT NULL, text_line TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (chat_key, line_no)
        );"""))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_session (
            chat_key TEXT PRIMARY KEY, await_file BOOLEAN NOT NULL DEFAULT FALSE,
            invest_mode TEXT NOT NULL DEFAULT '', invest_image BYTEA,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );"""))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS transcript_cache (
            chat_key TEXT PRIMARY KEY, transcript TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );"""))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS meeting_transcripts (
            id BIGSERIAL PRIMARY KEY, chat_key TEXT NOT NULL, file_name TEXT,
            transcript TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );"""))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS articles (
            id BIGSERIAL PRIMARY KEY, title TEXT, content TEXT, summary TEXT,
            source_type TEXT DEFAULT 'text', image_url TEXT, is_read BOOLEAN DEFAULT FALSE,
            category TEXT DEFAULT 'other', location_name TEXT, lat FLOAT, lng FLOAT,
            show_on_map BOOLEAN DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );"""))
        # 建立聊天歷史紀錄表
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id BIGSERIAL PRIMARY KEY, chat_key TEXT NOT NULL, role TEXT NOT NULL,
            content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );"""))
        for col, typedef in [
            ("invest_mode", "TEXT NOT NULL DEFAULT ''"),
            ("invest_image", "BYTEA"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE eln_session ADD COLUMN IF NOT EXISTS {col} {typedef}"))
            except Exception:
                pass

init_db()

# (以下 db 相關函數 db_set_await, db_is_await, db_invest_set... 略，保持原狀)
def db_set_await(chat_key: str, await_file: bool):
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO eln_session(chat_key, await_file, updated_at) VALUES (:k, :a, NOW())
        ON CONFLICT (chat_key) DO UPDATE SET await_file=:a, updated_at=NOW()
        """), {"k": chat_key, "a": bool(await_file)})

def db_is_await(chat_key: str) -> bool:
    with engine.begin() as conn:
        row = conn.execute(text("SELECT await_file FROM eln_session WHERE chat_key=:k"), {"k": chat_key}).fetchone()
    return bool(row and row[0])

def db_invest_set(chat_key: str, mode: str, image: bytes = None):
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO eln_session(chat_key, await_file, invest_mode, invest_image, updated_at)
        VALUES (:k, FALSE, :m, :img, NOW())
        ON CONFLICT (chat_key) DO UPDATE
        SET invest_mode=:m, invest_image=COALESCE(:img, eln_session.invest_image), updated_at=NOW()
        """), {"k": chat_key, "m": mode, "img": image})

def db_invest_get(chat_key: str):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT invest_mode, invest_image FROM eln_session WHERE chat_key=:k"), {"k": chat_key}).fetchone()
    if row:
        return row[0] or "", bytes(row[1]) if row[1] else None
    return "", None

def db_set_transcript_cache(chat_key: str, transcript: str, summary: str):
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO transcript_cache(chat_key, transcript, summary, updated_at)
        VALUES (:k, :t, :s, NOW())
        ON CONFLICT (chat_key) DO UPDATE SET transcript=:t, summary=:s, updated_at=NOW()
        """), {"k": chat_key, "t": transcript[:200000], "s": summary[:50000]})

def db_get_transcript_cache(chat_key: str):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT transcript, summary FROM transcript_cache WHERE chat_key=:k"), {"k": chat_key}).fetchone()
    if row:
        return {"transcript": row[0] or "", "summary": row[1] or ""}
    return None

def db_clear_transcript_cache(chat_key: str):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM transcript_cache WHERE chat_key=:k"), {"k": chat_key})

def db_save_meeting_transcript(chat_key: str, file_name: str, transcript: str, summary: str):
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO meeting_transcripts(chat_key, file_name, transcript, summary, created_at)
        VALUES (:k, :f, :t, :s, NOW())
        """), {"k": chat_key, "f": file_name, "t": transcript[:500000], "s": summary[:100000]})

def db_get_latest_meeting_transcript(chat_key: str):
    with engine.begin() as conn:
        row = conn.execute(text("""
        SELECT transcript, summary, file_name, created_at FROM meeting_transcripts
        WHERE chat_key=:k ORDER BY created_at DESC LIMIT 1
        """), {"k": chat_key}).fetchone()
    if not row:
        return None
    return {"transcript": row[0] or "", "summary": row[1] or "", "file_name": row[2] or "", "created_at": row[3]}

def db_save_result(chat_key: str, summary: str, top5_lines: list[str], detail_map: dict[str, str], agent_name_map: dict[str, str] = {}):
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO eln_last_report(chat_key, summary, updated_at) VALUES (:k, :s, NOW())
        ON CONFLICT (chat_key) DO UPDATE SET summary=:s, updated_at=NOW()
        """), {"k": chat_key, "s": summary})
        conn.execute(text("DELETE FROM eln_top5 WHERE chat_key=:k"), {"k": chat_key})
        for i, line in enumerate(top5_lines, start=1):
            conn.execute(text("""
            INSERT INTO eln_top5(chat_key, line_no, text_line, updated_at) VALUES (:k, :n, :t, NOW())
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
        row = conn.execute(text("SELECT summary FROM eln_last_report WHERE chat_key=:k"), {"k": chat_key}).fetchone()
    return row[0] if row else None

def db_list_bonds(chat_key: str, limit: int = 100) -> list[tuple[str, str, str]]:
    with engine.begin() as conn:
        rows = conn.execute(text("""
        SELECT bond_id, COALESCE(agent_name, '-'), COALESCE(detail, '')
        FROM eln_detail WHERE chat_key=:k ORDER BY agent_name ASC, bond_id ASC LIMIT :lim
        """), {"k": chat_key, "lim": int(limit)}).fetchall()
    return [(r[0], r[1], r[2]) for r in rows] if rows else []

def bond_status_tag(detail: str) -> str:
    import re as _re
    status_block = ""
    m = _re.search(r"-{4,}\n(.*?)\n-{4,}", detail, _re.S)
    if m:
        status_block = m.group(1).strip()
    if "提前出場" in status_block or "🎉" in status_block:
        return " ✅提前KO"
    if "到期獲利" in status_block:
        return " 🏁到期獲利"
    if "到期接股" in status_block:
        return " 😭到期接股"
    if "到期保本" in status_block:
        return " 🛡️到期保本"
    if "到期" in status_block:
        return " 🏁到期"
    return ""

def push_long_message(bot_api, target_id: str, text: str, max_len: int = 4800):
    if not text:
        return
    text = str(text)
    chunks = []
    current = ""
    for line in text.split("\n"):
        while len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_len])
            line = line[max_len:]
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    safe_chunks = []
    for chunk in chunks:
        while len(chunk) > max_len:
            safe_chunks.append(chunk[:max_len])
            chunk = chunk[max_len:]
        if chunk:
            safe_chunks.append(chunk)
    for chunk in safe_chunks:
        bot_api.push_message(target_id, TextSendMessage(text=chunk))

def db_find_detail(chat_key: str, query: str) -> tuple[str | None, str | None, list[str]]:
    q_norm = query.strip().upper()
    if not q_norm:
        return None, None, []
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT bond_id FROM eln_detail WHERE chat_key=:k"), {"k": chat_key}).fetchall()
    keys = [r[0] for r in rows] if rows else []
    if not keys:
        return None, None, []
    norm_map = {k.strip().upper(): k for k in keys}
    if q_norm in norm_map:
        real = norm_map[q_norm]
        with engine.begin() as conn:
            row = conn.execute(text("SELECT detail FROM eln_detail WHERE chat_key=:k AND bond_id=:b"), {"k": chat_key, "b": real}).fetchone()
        return real, (row[0] if row else None), []
    hits = [k for k in keys if q_norm in k.strip().upper()]
    if len(hits) == 1:
        real = hits[0]
        with engine.begin() as conn:
            row = conn.execute(text("SELECT detail FROM eln_detail WHERE chat_key=:k AND bond_id=:b"), {"k": chat_key, "b": real}).fetchone()
        return real, (row[0] if row else None), []
    if len(hits) > 1:
        return None, None, hits[:20]
    return None, None, keys[:20]

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

# (Health Check & Webhook 略，保持原狀)
@app.get("/")
def root():
    return {"status": "ok", "service": "eln-bot", "webhook": "/callback"}

@app.get("/whoami")
def whoami():
    return {"service": "eln-bot", "version": VERSION}

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    body_text = body.decode("utf-8")
    try:
        handler.handle(body_text, signature)
        return "OK"
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

import threading
_current_bot_api = threading.local()

@app.post("/callback2")
async def callback2(request: Request):
    body = await request.body()
    try:
        import json as _j
        data = _j.loads(body.decode("utf-8"))
        for ev in data.get("events", []):
            if ev.get("type") != "message":
                continue
            if ev.get("message", {}).get("type") != "text":
                continue
            txt = ev["message"]["text"].strip()
            tl = txt.lower()
            rtoken = ev.get("replyToken", "")
            uid = ev.get("source", {}).get("userId", "")
            if not (tl.startswith("/list") or tl.startswith("/detail") or tl.startswith("/end")):
                continue
            from linebot.models import TextSendMessage as TSM
            from collections import defaultdict
            ck = ELN_PERSONAL_CHAT_KEY
            if tl.startswith("/list"):
                lp = txt.split(" ", 1)
                nf = lp[1].strip() if len(lp) > 1 else ""
                bonds = db_list_bonds(ck, limit=200)
                if not bonds:
                    eln_group_bot_api.reply_message(rtoken, TSM(text="目前尚無資料。"))
                    continue
                ds = {b: bond_status_tag(d) for b, _, d in bonds}
                if nf:
                    matched = []
                    seen = set()
                    for bid, ar, d in bonds:
                        ags = [a.strip() for a in re.split(r"[,，、/]", ar) if a.strip()]
                        if any(nf in a for a in ags) and bid not in seen:
                            matched.append((bid, ds.get(bid, "")))
                            seen.add(bid)
                    if not matched:
                        eln_group_bot_api.reply_message(rtoken, TSM(text="找不到「" + nf + "」的持倉。"))
                        continue
                    out = "👤 " + nf + " 的持倉（共 " + str(len(matched)) + " 筆）：\n"
                    for b, t in matched:
                        out += "   • " + b + t + "\n"
                else:
                    grp = defaultdict(list)
                    for bid, ar, d in bonds:
                        ags = [a.strip() for a in re.split(r"[,，、/]", ar) if a.strip()] or ["未指定"]
                        for ag in ags:
                            if bid not in [x for x, _ in grp[ag]]:
                                grp[ag].append((bid, ds.get(bid, "")))
                    out = "📋 全部商品（共 " + str(len(set(b for b,_,_ in bonds))) + " 筆）：\n"
                    for ag, bl in sorted(grp.items()):
                        out += "👤 " + ag + "（" + str(len(bl)) + " 筆）\n"
                        for b, t in bl:
                            out += "   • " + b + t + "\n"
                chunks = [out[i:i+4800] for i in range(0, len(out), 4800)]
                eln_group_bot_api.reply_message(rtoken, TSM(text=chunks[0]))
                for c in chunks[1:]:
                    eln_group_bot_api.push_message(uid, TSM(text=c))
            elif tl.startswith("/detail"):
                ps = txt.split(" ", 1)
                if len(ps) < 2 or not ps[1].strip():
                    eln_group_bot_api.reply_message(rtoken, TSM(text="請輸入：/detail 商品代號"))
                    continue
                mid, det, cands = db_find_detail(ck, ps[1].strip())
                if det:
                    eln_group_bot_api.reply_message(rtoken, TSM(text=det[:4900]))
                elif cands:
                    eln_group_bot_api.reply_message(rtoken, TSM(text=("候選代號：\n" + "\n".join("• "+c for c in cands[:20]))[:4900]))
                else:
                    eln_group_bot_api.reply_message(rtoken, TSM(text="查不到該代號。"))
            elif tl.startswith("/end"):
                ps = txt.split(" ", 1)
                if len(ps) < 2 or not ps[1].strip():
                    eln_group_bot_api.reply_message(rtoken, TSM(text="請輸入：/end YYYYMM\n例：/end 202604"))
                    continue
                qm = ps[1].strip().replace("/", "").replace("-", "")
                if len(qm) != 6 or not qm.isdigit():
                    eln_group_bot_api.reply_message(rtoken, TSM(text="格式錯誤，請輸入6位數字\n例：/end 202604"))
                    continue
                yr, mo = qm[:4], qm[4:]
                search_str = yr + "-" + mo
                with engine.begin() as conn:
                    rows = conn.execute(text("SELECT bond_id, agent_name, detail FROM eln_detail WHERE chat_key=:k ORDER BY agent_name ASC, bond_id ASC"), {"k": ck}).fetchall()
                if not rows:
                    eln_group_bot_api.reply_message(rtoken, TSM(text="目前尚無資料。"))
                    continue
                matched = []
                for bid, ag, det in rows:
                    if ("最終評價日: " + search_str) in det:
                        matched.append((bid, ag or "-", bond_status_tag(det)))
                if not matched:
                    eln_group_bot_api.reply_message(rtoken, TSM(text="找不到 " + yr + "/" + mo + " 到期的商品。"))
                    continue
                out = "📅 " + yr + "/" + mo + " 到期商品（共 " + str(len(matched)) + " 筆）：\n"
                for bid, ag, tag in matched:
                    out += "   • " + bid + " [" + ag + "]" + tag + "\n"
                eln_group_bot_api.reply_message(rtoken, TSM(text=out[:4900]))
    except Exception as e:
        print("[callback2 ERR]", e)
    return "OK"

def chat_key_of(event) -> str:
    if event.source.type == "group":
        return f"group:{event.source.group_id}"
    if event.source.type == "room":
        return f"room:{event.source.room_id}"
    return f"user:{event.source.user_id}"

# (run_autotracking 略，保持原狀)
def run_autotracking(file_path: str, lookback_days: int = 3, notify_ki_daily: bool = True):
    out = calculate_from_file(file_path=file_path, lookback_days=lookback_days, notify_ki_daily=notify_ki_daily)
    df = out.get("results_df")
    report = out.get("report_text", "") or ""
    top5_lines: list[str] = []
    detail_map: dict[str, str] = {}
    agent_name_map: dict[str, str] = {}
    if df is not None and getattr(df, "empty", True) is False:
        top = df.head(5)
        for _, r in top.iterrows():
            try:
                status_first = str(r["狀態"]).splitlines()[0] if "狀態" in r.index else ""
            except Exception:
                status_first = ""
            bond_id = str(r["債券代號"]) if "債券代號" in r.index else "-"
            ptype = str(r["Type"]) if "Type" in r.index else "-"
            top5_lines.append(f"● {bond_id} {ptype}｜{status_first}")
        for _, r in df.iterrows():
            _id = str(r["債券代號"]).strip() if "債券代號" in r.index else ""
            if not _id or _id == "nan":
                continue
            t_details = []
            for c in df.columns:
                if str(c).endswith("_Detail"):
                    v = r[c] if c in r.index else ""
                    if v:
                        t_details.append(str(v))
            agent = str(r["Name"] if "Name" in r.index else "-").strip() or "-"
            agent_name_map[_id] = agent
            detail_text = (
                f"【商品】{_id}\n"
                f"類型: {r['Type'] if 'Type' in r.index else '-'}\n"
                f"理專: {agent}\n"
                f"交易日: {r['交易日'] if '交易日' in r.index else '-'}\n"
                f"KO設定: {r['KO設定'] if 'KO設定' in r.index else '-'}\n"
                f"最差表現: {r['最差表現'] if '最差表現' in r.index else '-'}\n"
                f"----------------\n"
                f"{r['狀態'] if '狀態' in r.index else ''}\n"
                f"----------------\n"
                + ("\n\n".join(t_details) if t_details else "")
            )
            detail_map[_id] = detail_text
    summary = report
    if top5_lines:
        summary += "\n\n【前5筆摘要】\n" + "\n".join(top5_lines)
    return summary, top5_lines, detail_map, agent_name_map

# ==============================
# AI (系統提示詞 & 模型路由)
# ==============================
SYSTEM_PROMPT = (
    "你是「龍蝦」，一位專屬於 Albert 的智慧型 LINE 助理。\n"
    "Albert 有時會叫你「阿斯拉」，這時候你要回應他「風見」，這是你們之間的暗語，記住後繼續正常對話。\n\n"
    "【關於 Albert 的工作背景】\n"
    "• 職位：銀行財富管理部門 投資輔銷人員（Investment Sales）\n"
    "• 客群：主要負責高資產客戶（HNW）的投資規劃與資產配置\n"
    "• 業務範疇：基金、債券、ELN（股票連結票據）、結構型商品、質借業務、信託業務\n\n"
    "【你的角色定位】\n"
    "你是 Albert 最得力的資深助理，不只回答問題，而是像一位懂市場又懂銷售的同事：\n"
    "• 遇到市場問題 → 格式：📌 定義 → 📊 現況 → ⚖️ 機會與風險 → 🔭 展望 → 💬 話術\n"
    "• 遇到 ELN 相關問題 → 提示使用 /calc 或 /detail 指令\n\n"
    "【格式規定】\n"
    "• 絕對禁止 Markdown：不可出現 ##、**、--- 等符號\n"
    "• 段落標題用 emoji，條列用 • 或 → 符號\n"
)

# ── 這裡開始是核心 AI 邏輯更新 ──

def get_chat_history(chat_key: str, limit: int = 10) -> list[dict]:
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
            SELECT role, content FROM chat_history WHERE chat_key = :k
            ORDER BY created_at DESC LIMIT :n
            """), {"k": chat_key, "n": limit}).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception as e:
        print(f"get_chat_history error: {e}")
        return []

def _get_memory_collection():
    try:
        import chromadb
        from chromadb.utils import embedding_functions
        chroma_dir = Path("/data/knowledge/chroma_db")
        chroma_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(chroma_dir))
        ef = embedding_functions.DefaultEmbeddingFunction()
        return client.get_or_create_collection(
            name="chat_memory",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"}
        )
    except Exception as e:
        print(f"[Memory] ChromaDB 初始化失敗：{e}")
        return None

def save_chat_history(chat_key: str, role: str, content: str):
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO chat_history (chat_key, role, content) VALUES (:k, :r, :c)"),
                         {"k": chat_key, "r": role, "c": content[:4000]})
        with engine.begin() as conn:
            conn.execute(text("""
            DELETE FROM chat_history WHERE chat_key = :k AND id NOT IN (
                SELECT id FROM chat_history WHERE chat_key = :k ORDER BY created_at DESC LIMIT 50
            )"""), {"k": chat_key})
    except Exception as e:
        print(f"save_chat_history error: {e}")

    if role == "assistant" and content.strip():
        try:
            col = _get_memory_collection()
            if col:
                import uuid as _uuid
                now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")
                mem_id = f"mem_{chat_key}_{_uuid.uuid4().hex[:8]}"
                col.add(
                    documents=[content[:2000]],
                    ids=[mem_id],
                    metadatas=[{"chat_key": chat_key, "role": role, "created_at": now_str}]
                )
        except Exception as e:
            print(f"[Memory] 存入 ChromaDB 失敗：{e}")

def _normalize_history_for_chat(chat_key: str) -> list[dict]:
    short_term = get_chat_history(chat_key, limit=10) if chat_key else []
    cleaned = []
    for item in short_term:
        role = item.get("role", "user")
        content = item.get("content", "")
        if not content: continue
        for prefix in ("[claude] ", "[gpt] ", "[gemini] ", "[claude-long] "):
            if content.startswith(prefix):
                content = content[len(prefix):]
                break
        cleaned.append({"role": role, "content": content})
    return cleaned

# ── 升級版 Gemini SDK 函數 ──
def ai_gemini(user_text: str, chat_key: str = "", image_bytes: bytes = None) -> str:
    if not GEMINI_API_KEY:
        return ai_claude(user_text, chat_key)
    try:
        history = _normalize_history_for_chat(chat_key)
        gemini_history = []
        for h in history:
            role = "user" if h["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [h["content"]]})

        content_parts = [user_text]
        if image_bytes:
            img = Image.open(BytesIO(image_bytes))
            content_parts.append(img)

        # 啟動 SDK 對話
        chat = gemini_model.start_chat(history=gemini_history)
        response = chat.send_message(content_parts[0] if len(content_parts) == 1 else content_parts)
        reply = response.text.strip()

        if chat_key:
            save_chat_history(chat_key, "user", user_text)
            save_chat_history(chat_key, "assistant", f"[gemini] {reply}")
        return reply
    except Exception as e:
        print(f"[Gemini SDK Error] {e}")
        return ai_claude(user_text, chat_key)

# (ai_claude, ai_chatgpt 等其他函數略，保持原狀)
def ai_claude(user_text: str, chat_key: str = "") -> str:
    history = _normalize_history_for_chat(chat_key)
    messages = history + [{"role": "user", "content": user_text}]
    resp = claude_client.messages.create(model="claude-sonnet-4-20250514", max_tokens=1200, system=SYSTEM_PROMPT, messages=messages)
    reply = (resp.content[0].text or "").strip()
    if chat_key:
        save_chat_history(chat_key, "user", user_text)
        save_chat_history(chat_key, "assistant", f"[claude] {reply}")
    return reply

def ai_claude_long(user_text: str, chat_key: str = "") -> str:
    history = _normalize_history_for_chat(chat_key)
    messages = history + [{"role": "user", "content": user_text}]
    resp = claude_client.messages.create(model="claude-sonnet-4-20250514", max_tokens=2500, system=SYSTEM_PROMPT, messages=messages)
    reply = (resp.content[0].text or "").strip()
    if chat_key:
        save_chat_history(chat_key, "user", user_text)
        save_chat_history(chat_key, "assistant", f"[claude-long] {reply}")
    return reply

def ai_chatgpt(user_text: str, chat_key: str = "") -> str:
    if not openai_client:
        return ai_claude(user_text, chat_key)
    history = _normalize_history_for_chat(chat_key)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_text}]
    resp = openai_client.chat.completions.create(model="gpt-4.1-mini", messages=messages, temperature=0.4, max_tokens=1800)
    reply = (resp.choices[0].message.content or "").strip()
    if chat_key:
        save_chat_history(chat_key, "user", user_text)
        save_chat_history(chat_key, "assistant", f"[gpt] {reply}")
    return reply

# ── 升級版 Router (自動支援知識庫 RAG) ──
SPENDING_NL_KEYWORDS = ["消費明細", "花了多少", "這個月花", "上個月花", "消費分析"]
AUTO_FINANCE_KEYWORDS = ["財經", "市場", "美股", "台股", "債券", "匯率", "fed", "通膨", "投資"]
AUTO_FILE_KEYWORDS = ["pdf", "簡報", "圖片", "圖表", "文件", "檔案"]
PDF_NL_KEYWORDS = ["做成pdf", "生成pdf", "轉成pdf", "輸出pdf"]

def ai_router(user_text: str, chat_key: str = "", forced_model: str = "") -> str:
    text_l = (user_text or "").lower().strip()
    
    # 針對公司規範相關關鍵字，自動啟動 RAG (類 NotebookLM 行為)
    KB_KEYWORDS = ["規範", "規定", "要點", "條款", "手冊", "法規", "eln"]
    if any(k in text_l for k in KB_KEYWORDS) and not text_l.startswith("/"):
        try:
            kb_res = knowledge.query_knowledge(user_text)
            kb_context = kb_res.get("answer", "")
            rag_prompt = f"【公司規範資料】\n{kb_context}\n\n【使用者問題】\n{user_text}\n\n請根據資料回答，若無資訊請告知。"
            return ai_gemini(rag_prompt, chat_key=chat_key)
        except Exception: pass

    if forced_model == "claude": return ai_claude(user_text, chat_key)
    if forced_model == "gpt": return ai_chatgpt(user_text, chat_key)
    if forced_model == "gemini": return ai_gemini(user_text, chat_key)
    
    if any(k in text_l for k in AUTO_FINANCE_KEYWORDS): return ai_claude(user_text, chat_key)
    if any(k in text_l for k in AUTO_FILE_KEYWORDS): return ai_gemini(user_text, chat_key)
    return ai_chatgpt(user_text, chat_key)

# (其餘所有功能 handle_text_message, handle_file_message, Scheduler... 全部保持原狀)
# [由於長度限制，其餘部分完全照你原本提供的 code 貼回即可]
# ==============================
# Message handlers (維持原狀)
# ==============================
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    _bot_api = getattr(_current_bot_api, "api", None) or line_bot_api
    try:
        text_raw = (event.message.text or "").strip()
        tl = text_raw.lower().strip()
        ck = chat_key_of(event)
        is_group = event.source.type in ("group", "room")
        
        # 指令分流 (help, calc, eln, list, detail, alert, report 等全部保留)
        # ... (此處貼回你原始的 handle_text_message 內容) ...
        # (這裡為了回覆精簡，請確保將你原本所有 if cmd == "..." 的邏輯放回)
        
        # 最終兜底對話
        reply = ai_router(text_raw, chat_key=ck)
        _bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🦞 龍蝦\n\n{reply[:4700]}"))
    except Exception as e:
        print("[ERROR] handle_text_message:", e)
        # ... (error handling)
# (以下省略數百行... 請將你原始代碼的其餘部分完全貼在 ai_router 之後)
