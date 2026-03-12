import os
import re
import json
import traceback as _traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, HTTPException
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

# 龍蝦主Bot
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
            invest_mode TEXT NOT NULL DEFAULT '',
            invest_image BYTEA,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """))
        # 舊表補欄位（已存在的 table 不會重建）
        for col, typedef in [
            ("invest_mode", "TEXT NOT NULL DEFAULT ''"),
            ("invest_image", "BYTEA"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE eln_session ADD COLUMN IF NOT EXISTS {col} {typedef}"))
            except Exception:
                pass

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

# ── /invest 三步驟狀態管理 ──
def db_invest_set(chat_key: str, mode: str, image: bytes = None):
    """mode: '' / 'await_image' / 'await_reason'"""
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO eln_session(chat_key, await_file, invest_mode, invest_image, updated_at)
        VALUES (:k, FALSE, :m, :img, NOW())
        ON CONFLICT (chat_key) DO UPDATE
        SET invest_mode=:m, invest_image=COALESCE(:img, eln_session.invest_image), updated_at=NOW()
        """), {"k": chat_key, "m": mode, "img": image})

def db_invest_get(chat_key: str):
    """回傳 (mode, image_bytes)"""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT invest_mode, invest_image FROM eln_session WHERE chat_key=:k"),
            {"k": chat_key}
        ).fetchone()
    if row:
        return row[0] or "", bytes(row[1]) if row[1] else None
    return "", None

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

def db_list_bonds(chat_key: str, limit: int = 100) -> list[tuple[str, str]]:
    with engine.begin() as conn:
        rows = conn.execute(text("""
        SELECT bond_id, COALESCE(agent_name, '-')
        FROM eln_detail
        WHERE chat_key=:k
        ORDER BY agent_name ASC, bond_id ASC
        LIMIT :lim
        """), {"k": chat_key, "lim": int(limit)}).fetchall()
    return [(r[0], r[1]) for r in rows] if rows else []

def push_long_message(bot_api, target_id: str, text: str, max_len: int = 4800):
    """長文字自動分段 push_message"""
    if not text:
        return
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    for chunk in chunks:
        bot_api.push_message(target_id, TextSendMessage(text=chunk))


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
# AI fallback (Claude)
# ==============================
SYSTEM_PROMPT = (
    "你是「龍蝦」，一位專屬於 Albert 的智慧型 LINE 助理。\n\n"

    "【關於 Albert 的工作背景】\n"
    "• 職位：銀行財富管理部門 投資輔銷人員（Investment Sales）\n"
    "• 客群：主要負責高資產客戶（HNW）的投資規劃與資產配置\n"
    "• 業務範疇：\n"
    "  → 投資商品：基金、債券、ELN（股票連結票據）、結構型商品、ETF\n"
    "  → 質借業務：Lombard Lending（有價證券質借）、金市債券質借、信託質借\n"
    "  → 信託業務：資產信託規劃、境外資金匯回配置\n"
    "  → 教育訓練：經常幫行內專員上課，教導基金、債券、結構型產品、ELN等商品知識\n"
    "• 常見需求：市場分析、商品說明、客戶推播文案、專員教育訓練教材、投資建議\n\n"

    "【你的角色定位】\n"
    "你是 Albert 最得力的資深助理，不只回答問題，而是像一位懂市場又懂銷售的同事：\n"
    "• 用投資輔銷的角度思考，理解他面對的是高資產客戶與行內專員\n"
    "• 遇到市場問題 → 提供深度分析，並附上「可以這樣跟客戶說」的話術\n"
    "• 遇到商品問題 → 說明商品特性、適合的高資產客群、風險與機會\n"
    "• 遇到質借/信託問題 → 說明業務邏輯、適用情境、常見客戶疑問\n"
    "• 遇到教學需求 → 以簡單易懂的方式說明，適合用來對專員解說\n"
    "• 遇到文案需求 → 直接產出可複製貼上的推播內容\n"
    "• 遇到 ELN 相關問題 → 提示使用 /calc 或 /detail 指令\n\n"

    "【回答原則】\n"
    "1. 有深度：提供背景、現況、影響、展望，不能太簡短\n"
    "2. 結構清晰：重點分段，讓人一眼看懂\n"
    "3. 客觀中立：呈現多空兩面，讓 Albert 自行判斷\n"
    "4. 實用導向：一般問題結尾補充「💬 可以這樣跟客戶/專員說：...」\n"
    "5. 市場問題格式：📌 定義 → 📊 現況 → ⚖️ 機會與風險 → 🔭 展望 → 💬 話術\n"
    "6. 商品教學格式：📌 商品定義 → 🔧 運作方式 → 👤 適合客群 → ⚠️ 風險提示 → 💬 話術\n"
    "7. 質借業務格式：📌 業務說明 → 💡 適用情境 → 📊 利率/條件 → ❓ 常見客戶問題\n\n"

    "【格式規定】\n"
    "• 絕對禁止 Markdown：不可出現 ##、**、--- 等符號\n"
    "• 段落標題用 emoji，例如 📌 📊 ⚖️ 🔭 💡 💬 🔧 👤 ⚠️\n"
    "• 條列用 • 或 → 符號\n"
    "• 數字、百分比、金額要具體，不要模糊帶過\n"
    "• 回答長度要足夠，高資產客戶的問題不能給太簡短的答案\n"
)

def get_chat_history(chat_key: str, limit: int = 10) -> list[dict]:
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
            SELECT role, content FROM chat_history
            WHERE chat_key = :k
            ORDER BY created_at DESC
            LIMIT :n
            """), {"k": chat_key, "n": limit}).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception as e:
        print(f"get_chat_history error: {e}")
        return []

def save_chat_history(chat_key: str, role: str, content: str):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
            INSERT INTO chat_history (chat_key, role, content)
            VALUES (:k, :r, :c)
            """), {"k": chat_key, "r": role, "c": content[:4000]})
        with engine.begin() as conn:
            conn.execute(text("""
            DELETE FROM chat_history
            WHERE chat_key = :k
              AND id NOT IN (
                SELECT id FROM chat_history
                WHERE chat_key = :k
                ORDER BY created_at DESC
                LIMIT 50
              )
            """), {"k": chat_key})
    except Exception as e:
        print(f"save_chat_history error: {e}")

def ai_reply(user_text: str, chat_key: str = "") -> str:
    history = get_chat_history(chat_key) if chat_key else []
    messages = history + [{"role": "user", "content": user_text}]
    resp = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=messages
    )
    reply = (resp.content[0].text or "").strip()
    if chat_key:
        save_chat_history(chat_key, "user", user_text)
        save_chat_history(chat_key, "assistant", reply)
    return reply

# ==============================
# Webhook endpoint
# ==============================
# 用 threading.local 記錄當前是哪個 Bot 在處理
# event -> bot_api 對應表
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



# ==============================
# Text message handler
# ==============================
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    _bot_api = line_bot_api
    try:
        text_raw = (event.message.text or "").strip()
        tl = text_raw.lower().strip()
        ck = chat_key_of(event)
        is_group = event.source.type in ("group", "room")
        print("[TEXT]", ck, repr(text_raw))

        if tl.startswith("/"):
            cmd = tl[1:].split()[0] if tl[1:].split() else ""
            raw_cmd = text_raw[1:]
        else:
            cmd = tl.split()[0] if tl.split() else tl
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
                    "🦞 龍蝦指令清單\n"
                    "─────────────────\n"
                    "📊 ELN 追蹤\n"
                    "/calc — 上傳 Excel 計算並保存\n"
                    "/list — 列出所有可查商品代號\n"
                    "/detail <代號> — 查詢單筆 KO/KI/狀態\n"
                    "─────────────────\n"
                    "📰 財經資訊\n"
                    "/daily — 產生今日財經日報\n"
                    "/daily cache — 回傳今早已生成的日報\n"
                    "/market <新聞+標的> — 生成客戶推播市場觀點\n"
                    "─────────────────\n"
                    "📑 研究報告 & 簡報\n"
                    "/report ppt <主題> — 🎨 Icon設計版PPT（深藍金）\n"
                    "/report ppt <主題> green — 深綠金配色\n"
                    "/report ppt <主題> dark — 純黑銀配色\n"
                    "/report <主題> — 投資銀行風格PDF\n"
                    "/report <主題> brief — 簡報摘要\n"
                    "/report <主題> client — 客戶推播風格\n"
                    "/report <主題> academic — 學術研究\n"
                    "/report <主題> hybrid — 投銀+研究混合\n"
                    "/report <主題> custom <說明> — 自訂風格\n"
                    "─────────────────\n"
                    "📈 投資推播\n"
                    "/invest — 上傳新聞截圖，生成投資推播文\n"
                    "─────────────────\n"
                    "📤 ELN 理專通知\n"
                    "/send <編號> — 發送第N筆通知給理專\n"
                    "/skip <編號> — 略過第N筆通知\n"
                    "/send all — 全部發送\n"
                    "/skip all — 全部略過\n"
                    "/send list — 查看待確認清單\n"
                    "─────────────────\n"
                    "📄 PDF\n"
                    "/pdf daily — 財經日報 PDF\n"
                    "/pdf market <內容> — 市場觀點 PDF\n"
                    "/news pdf — 最新新聞整理成 PDF\n"
                    "─────────────────\n"
                    "📧 郵件\n"
                    "/mail — 未讀郵件摘要\n"
                    "/mail unread — 只看重要未讀\n"
                    "─────────────────\n"
                    "🔔 價格警示\n"
                    "/analysis <股票> — 完整三面向分析 (技術+基本面+消息面)\n"
                    "/tech <股票> — 技術分析圖表 (K線/RSI/成交量)\n"
                    "/tech mag7 — Magnificent Seven 比較分析\n"
                    "/alert add <標的> <價格> above/below\n"
                    "/alert list — 查看所有警示\n"
                    "/alert del <編號> — 刪除警示\n"
                    "─────────────────\n"
                    "⚙️ 其他\n"
                    "其他文字 — Claude AI 對話（有記憶）\n"
                    "上傳檔案 — 自動分析 PDF/Excel/Word/PPT\n"
                    "/forget — 清除對話記憶\n"
                    "/help — 顯示本說明"
                )
            _bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return

        # SEND / SKIP — ELN 理專通知確認
        if cmd in ("send", "skip"):
            arg = parts[1].strip().lower() if len(parts) > 1 else ""
            if not arg:
                _bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="請指定編號或 all\n範例：/send 1　/skip 2　/send all"
                ))
                return

            # 從 DB 撈 pending 清單
            with engine.begin() as conn:
                rows = conn.execute(text(
                    "SELECT id, target_id, agent_name, bond_id, status, msg "
                    "FROM eln_pending_notifications WHERE chat_key=:k ORDER BY id"
                ), {"k": ck}).fetchall()

            if not rows:
                _bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="目前沒有待確認的通知。"
                ))
                return

            # 決定要處理哪幾筆
            if arg == "all":
                targets = list(rows)
            else:
                try:
                    idx = int(arg) - 1
                    if idx < 0 or idx >= len(rows):
                        raise ValueError
                    targets = [rows[idx]]
                except ValueError:
                    _bot_api.reply_message(event.reply_token, TextSendMessage(
                        text=f"編號不正確，請輸入 1～{len(rows)} 或 all"
                    ))
                    return

            if cmd == "send":
                sent, failed = 0, 0
                for row in targets:
                    try:
                        line_bot_api.push_message(
                            row.target_id,
                            TextSendMessage(text=row.msg[:4900])
                        )
                        sent += 1
                        print(f"[SEND] {row.agent_name} | {row.bond_id} | {row.status}")
                    except Exception as e:
                        failed += 1
                        print(f"[SEND ERROR] {row.target_id}: {e}")
                    # 發完就從 pending 刪掉
                    with engine.begin() as conn:
                        conn.execute(text(
                            "DELETE FROM eln_pending_notifications WHERE id=:i"
                        ), {"i": row.id})

                result_text = f"✅ 已發送 {sent} 筆"
                if failed:
                    result_text += f"，失敗 {failed} 筆"
            else:  # skip
                for row in targets:
                    with engine.begin() as conn:
                        conn.execute(text(
                            "DELETE FROM eln_pending_notifications WHERE id=:i"
                        ), {"i": row.id})
                result_text = f"⏭️ 已略過 {len(targets)} 筆"

            # 看看還有沒有剩的
            with engine.begin() as conn:
                remaining = conn.execute(text(
                    "SELECT COUNT(*) FROM eln_pending_notifications WHERE chat_key=:k"
                ), {"k": ck}).scalar()

            if remaining > 0:
                result_text += f"\n\n還有 {remaining} 筆待處理，打 /send list 查看"
            else:
                result_text += "\n\n✅ 所有通知已處理完畢"

            _bot_api.reply_message(event.reply_token, TextSendMessage(text=result_text))
            return

        # SEND LIST — 查看待確認清單
        if cmd == "send" and len(parts) > 1 and parts[1].strip().lower() == "list":
            with engine.begin() as conn:
                rows = conn.execute(text(
                    "SELECT id, agent_name, bond_id, status "
                    "FROM eln_pending_notifications WHERE chat_key=:k ORDER BY id"
                ), {"k": ck}).fetchall()
            if not rows:
                _bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="目前沒有待確認的通知。"
                ))
            else:
                lines = [f"📋 待確認通知（{len(rows)}筆）\n"]
                for i, row in enumerate(rows, start=1):
                    lines.append(f"{i}️⃣ {row.agent_name} | {row.bond_id} | {row.status}\n  /send {i}　/skip {i}")
                lines.append("\n/send all 全部發送　/skip all 全部略過")
                _bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="\n\n".join(lines)
                ))
            return

        # INVEST 投資推播
        if cmd == "invest":
            db_invest_set(ck, "await_image")
            _bot_api.reply_message(event.reply_token, TextSendMessage(
                text="📰 請上傳新聞截圖\n\n收到圖片後，我會請你補上投資理由和標的。"
            ))
            return

        # INVEST 第三步：收到理由和標的
        invest_mode, invest_image = db_invest_get(ck)
        if invest_mode == "await_reason" and invest_image:
            raw = text_raw.strip()
            # 解析理由和標的（允許各種格式）
            reason = ""
            targets = ""
            for line in raw.replace("，", ",").splitlines():
                l = line.strip()
                if l.startswith("理由"):
                    reason = l.split("：", 1)[-1].split(":", 1)[-1].strip()
                elif l.startswith("標的"):
                    targets = l.split("：", 1)[-1].split(":", 1)[-1].strip()
            # 若沒有明確標記，把整段當理由
            if not reason and not targets:
                reason = raw

            db_invest_set(ck, "")  # 清除狀態
            _bot_api.reply_message(event.reply_token, TextSendMessage(
                text="✍️ 整理中，請稍候..."
            ))
            try:
                posts = generate_invest_post(invest_image, reason, targets)
                _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=posts[:4900]))
            except Exception as e:
                _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(
                    text=f"生成失敗：{str(e)[:200]}"
                ))
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
                        _bot_api.reply_message(event.reply_token, TextSendMessage(text=row[0][:4900]))
                    else:
                        _bot_api.reply_message(event.reply_token, TextSendMessage(text="尚無快取日報，請用 /daily 產生最新版本。"))
                except Exception as e:
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text=f"讀取快取失敗: {e}"))
                return

            _bot_api.reply_message(event.reply_token, TextSendMessage(text="產生中，請稍候約30秒..."))
            try:
                from daily_report import generate_report, save_report_to_db
                report = generate_report()
                save_report_to_db(report)
                _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=report[:4900]))
            except Exception as e:
                _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"日報產生失敗: {e}"))
            return

        # SETTARGET
        if cmd == "settarget":
            targets = load_targets()
            if event.source.type == "group":
                targets["default"] = event.source.group_id
                targets["default_type"] = "group"
                save_targets(targets)
                _bot_api.reply_message(event.reply_token, TextSendMessage(text="已設定此群組為預設推播對象"))
            elif event.source.type == "room":
                targets["default"] = event.source.room_id
                targets["default_type"] = "room"
                save_targets(targets)
                _bot_api.reply_message(event.reply_token, TextSendMessage(text="已設定此聊天室為預設推播對象"))
            else:
                targets["default"] = event.source.user_id
                targets["default_type"] = "user"
                save_targets(targets)
                _bot_api.reply_message(event.reply_token, TextSendMessage(text="已設定您為預設推播對象"))
            return

        # LIST
        if cmd == "list":
            bonds = db_list_bonds(ck, limit=100)
            if not bonds:
                _bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="目前尚無已保存結果。請先 /calc 上傳 Excel。"
                ))
                return

            from collections import defaultdict
            grouped = defaultdict(list)
            for bond_id, agent_raw in bonds:
                agents = [a.strip() for a in re.split(r"[,，、/]", agent_raw) if a.strip()]
                if not agents:
                    agents = ["未指定"]
                for agent in agents:
                    grouped[agent].append(bond_id)

            lines = [f"📋 全部商品（共 {len(bonds)} 筆，按理專排列）：\n"]
            for agent, bond_ids in sorted(grouped.items()):
                unique_bonds = list(dict.fromkeys(bond_ids))
                lines.append(f"👤 {agent}（{len(unique_bonds)} 筆）")
                for b in unique_bonds:
                    lines.append(f"   • {b}")

            # 分段發送，每段不超過 4800 字元
            full_text = "\n".join(lines)
            chunks = []
            current = ""
            for line in full_text.split("\n"):
                if len(current) + len(line) + 1 > 4800:
                    chunks.append(current)
                    current = line
                else:
                    current = current + "\n" + line if current else line
            if current:
                chunks.append(current)

            _bot_api.reply_message(event.reply_token, TextSendMessage(text=chunks[0]))
            for chunk in chunks[1:]:
                _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=chunk))
            return

        # CALC
        if cmd.startswith("calc") or cmd.startswith("clac"):
            parts = raw_cmd.split(" ", 1)
            if len(parts) > 1 and parts[1].strip():
                expr = parts[1].strip()
                if not re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s]+", expr):
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text="算式格式錯誤"))
                    return
                try:
                    result = eval(expr, {"__builtins__": {}})
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text=f"{expr} = {result}"))
                    return
                except Exception:
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text="算式錯誤"))
                    return
            db_set_await(ck, True)
            _bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="收到！請直接把 Excel 檔案傳給我（用 LINE 的『檔案』上傳），我會計算並保存結果。")
            )
            return

        # REPORT
        if cmd == "report" and len(raw_cmd.strip().split()) == 1:
            summary = db_get_report(ck)
            if not summary:
                _bot_api.reply_message(event.reply_token, TextSendMessage(text="目前尚無已保存結果，請先 /calc 上傳 Excel。"))
                return
            _bot_api.reply_message(event.reply_token, TextSendMessage(text=summary[:4900]))
            return

        # MARKET CONTENT
        if cmd.startswith("market"):
            parts = text_raw.split(" ", 1)
            if len(parts) < 2 or not parts[1].strip():
                _bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="請輸入新聞內容和推薦標的\n\n格式範例:\n/market 美股反彈，高盛喊買。\n\n推薦標的: PIMCO收益增長、駿利平衡基金"
                    )
                )
                return
            news_text = parts[1].strip()
            content = generate_market_content(news_text)
            _bot_api.reply_message(
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
                _bot_api.reply_message(event.reply_token, TextSendMessage(text="產生財經日報 PDF 中，請稍候約30秒..."))
                try:
                    from daily_report import generate_report
                    report = generate_report()
                    link = create_and_upload_pdf("daily", report)
                    _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"📄 財經日報 PDF 已產生！\n\n{link}"))
                except Exception as e:
                    _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"PDF 產生失敗: {e}"))
                return

            if sub == "market":
                if len(parts) < 3 or not parts[2].strip():
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入內容\n範例：/pdf market 美股反彈，推薦PIMCO"))
                    return
                _bot_api.reply_message(event.reply_token, TextSendMessage(text="產生市場觀點 PDF 中，請稍候..."))
                try:
                    content = generate_market_content(parts[2].strip())
                    link = create_and_upload_pdf("market", content)
                    _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"📄 市場觀點 PDF 已產生！\n\n{link}"))
                except Exception as e:
                    _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"PDF 產生失敗: {e}"))
                return

            if sub == "make":
                content_text = parts[2].strip() if len(parts) > 2 else ""
                if not content_text:
                    _bot_api.reply_message(event.reply_token, TextSendMessage(
                        text="請在指令後面直接輸入內容\n\n範例：\n/pdf make 第一點：市場回顧 第二點：投資建議"
                    ))
                    return
                _bot_api.reply_message(event.reply_token, TextSendMessage(text="整理內容並產生 PDF 中，請稍候..."))
                try:
                    import anthropic as _anthropic
                    _client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
                    _resp = _client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=2000,
                        messages=[{
                            "role": "user",
                            "content": f"請將以下內容整理成清楚的報告格式，使用繁體中文，標題用【】標示，條列用•符號，不要用Markdown語法：\n\n{content_text}"
                        }]
                    )
                    organized = _resp.content[0].text
                    link = create_and_upload_pdf("analysis", organized, "自訂報告")
                    _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"📄 PDF 已產生！\n\n{link}"))
                except Exception as e:
                    _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"PDF 產生失敗: {e}"))
                return

            _bot_api.reply_message(event.reply_token, TextSendMessage(
                text="PDF 指令用法：\n/pdf daily → 財經日報 PDF\n/pdf market <內容> → 市場觀點 PDF\n/pdf make <內容> → 自訂內容 PDF"
            ))
            return

        # REPORT 研究報告（多種風格）
        if cmd.startswith("report"):
            parts = text_raw.split(" ")
            style_codes = {"ib", "brief", "client", "academic", "hybrid", "custom"}
            style_names = {"ib":"投資銀行", "brief":"簡報摘要", "client":"客戶推播", "academic":"學術研究", "hybrid":"混合風格", "custom":"自訂風格"}
            style = "ib"
            custom_prompt = ""

            # ── PPT 子指令：/report ppt <主題> [選項]
            # 固定配色：navy / green / dark
            # 自訂視覺：主題:深海星空
            # 指定圖案：圖案:stars（8種內建）或 圖案:自由（Claude即時繪製）
            if len(parts) > 1 and parts[1].lower() == "ppt":
                ppt_color_codes = {"navy", "green", "dark"}
                ppt_color_names = {"navy":"深藍金","green":"深綠金","dark":"純黑銀"}
                VALID_PATTERNS  = {"circuit","wave","stars","hexagon","mountain","ripple","grid","diagonal","none"}

                raw          = " ".join(parts[2:]).strip()
                visual_theme = ""
                color_theme  = "navy"
                force_pattern = ""
                custom_bg    = False

                # 解析 圖案:XXX（先解析，避免干擾後面邏輯）
                if "圖案:" in raw:
                    pi = raw.index("圖案:")
                    pat_val = raw[pi+3:].split()[0].strip()
                    raw = (raw[:pi] + raw[pi+3+len(pat_val):]).strip()
                    if pat_val == "自由":
                        custom_bg = True
                    elif pat_val in VALID_PATTERNS:
                        force_pattern = pat_val

                # 解析 主題:XXXX
                if "主題:" in raw:
                    idx = raw.index("主題:")
                    visual_theme = raw[idx+3:].strip()
                    raw = raw[:idx].strip()
                    if custom_bg:
                        color_label = f"自由繪製（{visual_theme}）"
                    elif force_pattern:
                        color_label = f"自訂（{visual_theme}，{force_pattern}圖案）"
                    else:
                        color_label = f"自訂（{visual_theme}）"
                elif raw and raw.split()[-1].lower() in ppt_color_codes:
                    color_theme = raw.split()[-1].lower()
                    raw = " ".join(raw.split()[:-1]).strip()
                    color_label = ppt_color_names[color_theme]
                else:
                    color_label = "深藍金"

                topic = raw
                if not topic:
                    _bot_api.reply_message(event.reply_token, TextSendMessage(
                        text="請輸入簡報主題！\n\n─ 固定配色 ─\n/report ppt 債券投資入門\n/report ppt 債券投資入門 green\n/report ppt 信託規劃 dark\n\n─ 自訂視覺主題 ─\n/report ppt ELN介紹 主題:深海星空\n/report ppt Lombard Lending 主題:科技電路板\n\n─ 指定圖案 ─\n/report ppt 債券投資 主題:宇宙紫金 圖案:stars\n/report ppt 信託規劃 主題:日式禪風 圖案:ripple\n\n─ Claude自由繪製背景（最自由！）─\n/report ppt ELN介紹 主題:珊瑚礁海底 圖案:自由\n/report ppt 債券配置 主題:富士山日出 圖案:自由"
                    ))
                    return

                # 等待提示
                wait_steps = []
                if custom_bg:    wait_steps.append("🎨 Claude自由繪製背景")
                elif visual_theme: wait_steps.append("🎨 推導視覺風格")
                wait_steps += ["📐 規劃架構", "🖼️ 生成投影片", "☁️ 上傳雲端"]
                wait_text = " → ".join(wait_steps)

                _bot_api.reply_message(event.reply_token, TextSendMessage(
                    text=f"🎨 正在製作「{topic}」簡報\n\n配色：{color_label}\n風格：Icon + 大字 + 繁體中文\n頁數：12張\n\n{wait_text}\n\n請稍候約{'120' if custom_bg else '90'}秒..."
                ))
                try:
                    from ppt_generator import generate_ppt
                    link = generate_ppt(
                        topic, n_slides=12,
                        color_theme=color_theme,
                        visual_theme=visual_theme,
                        force_pattern=force_pattern,
                        custom_bg=custom_bg
                    )
                    bg_note = "\n⚠️ 背景為 Claude 自由創作，風格可能每次略有不同" if custom_bg else ""
                    _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(
                        text=f"✅ 簡報製作完成！\n\n📌 主題：{topic}\n🎨 配色：{color_label}\n📄 頁數：12張{bg_note}\n\n{link}\n\n💡 下載後可在 PowerPoint 自由修改內容"
                    ))
                except Exception as e:
                    _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(
                        text=f"❌ 簡報生成失敗：{str(e)[:200]}"
                    ))
                return

            # 偵測 custom：/report <主題> custom <自訂說明>
            if "custom" in [p.lower() for p in parts[2:]]:
                custom_idx = next(i for i,p in enumerate(parts) if p.lower() == "custom")
                topic = " ".join(parts[1:custom_idx]).strip()
                custom_prompt = " ".join(parts[custom_idx+1:]).strip()
                style = "custom"
                if not custom_prompt:
                    _bot_api.reply_message(event.reply_token, TextSendMessage(
                        text="自訂風格請在 custom 後面加上說明！\n\n範例：\n/report 台積電展望 custom 請用輕鬆幽默的風格，適合分享給非專業投資人"
                    ))
                    return
            elif len(parts) > 2 and parts[-1].lower() in style_codes:
                style = parts[-1].lower()
                topic = " ".join(parts[1:-1]).strip()
            else:
                topic = " ".join(parts[1:]).strip()

            if not topic:
                _bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="請輸入報告主題\n\n風格選擇（加在主題後面）：\n預設 → 投資銀行\nbrief → 簡報摘要\nclient → 客戶推播\nacademic → 學術研究\nhybrid → 投銀+研究混合\ncustom <說明> → 自訂風格\n\n範例：\n/report 聯準會降息對債市影響\n/report 聯準會降息對債市影響 client\n/report 台積電展望 custom 請用輕鬆幽默的風格，適合分享給非專業投資人"
                ))
                return
            _bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"📊 正在研究「{topic}」\n風格：{style_names.get(style,'投資銀行')}\n\n搜尋資料 → 整理分析 → 生成PDF\n請稍候約60至90秒..."
            ))
            try:
                from report_generator import generate_research_report
                link = generate_research_report(topic, ck.split(":", 1)[1], style=style, custom_prompt=custom_prompt)
                _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(
                    text=f"📑 研究報告已完成！\n\n主題：{topic}\n風格：{style_names.get(style,'投資銀行')}\n\n{link}"
                ))
            except Exception as e:
                _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(
                    text=f"報告生成失敗：{e}"
                ))
            return

        # ALERT 價格警示
        if cmd.startswith("alert"):
            parts = text_raw.split(" ")
            sub = parts[1].strip().lower() if len(parts) > 1 else ""

            # /alert list
            if sub == "list":
                with engine.begin() as conn:
                    rows = conn.execute(text("""\n                    SELECT id, symbol, alert_type, condition, target_value, ma_period\n                    FROM price_alerts\n                    WHERE chat_key=:k AND deleted=FALSE\n                    ORDER BY id ASC\n                    """), {"k": ck}).fetchall()
                if not rows:
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text="目前沒有任何警示設定。\n\n新增範例：\n/alert add AAPL 200 above\n/alert add AAPL ma20 below"))
                    return
                msg = "目前警示清單：\n"
                for r in rows:
                    rid, sym, atype, cond, tval, maper = r
                    cond_str = "漲到" if cond == "above" else "跌到"
                    cross_str = "漲破" if cond == "above" else "跌破"
                    if atype == "price":
                        msg += f"#{rid} {sym} {cond_str} {tval}\n"
                    else:
                        msg += f"#{rid} {sym} {cross_str} MA{maper}\n"
                _bot_api.reply_message(event.reply_token, TextSendMessage(text=msg.strip()))
                return

            # /alert del <id> 或 /alert del（顯示清單讓用戶選）
            if sub == "del":
                if len(parts) < 3:
                    # 沒給編號 → 先顯示清單
                    with engine.begin() as conn:
                        rows = conn.execute(text("""
                        SELECT id, symbol, alert_type, condition, target_value, ma_period, trigger_count
                        FROM price_alerts
                        WHERE chat_key=:k AND deleted=FALSE
                        ORDER BY id ASC
                        """), {"k": ck}).fetchall()
                    if not rows:
                        _bot_api.reply_message(event.reply_token, TextSendMessage(text="目前沒有任何警示設定。"))
                        return
                    msg = "請輸入要刪除的編號：\n\n"
                    for r in rows:
                        rid, sym, atype, cond, tval, maper, tcount = r
                        cond_str = "漲到" if cond == "above" else "跌到"
                        cross_str = "漲破" if cond == "above" else "跌破"
                        remain = 2 - (tcount or 0)
                        if atype == "price":
                            msg += f"#{rid} {sym} {cond_str} {tval}（剩餘{remain}次）\n"
                        else:
                            msg += f"#{rid} {sym} {cross_str} MA{maper}（剩餘{remain}次）\n"
                    msg += "\n輸入：/alert del <編號>"
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text=msg.strip()))
                    return
                try:
                    del_id = int(parts[2])
                    with engine.begin() as conn:
                        conn.execute(text("UPDATE price_alerts SET deleted=TRUE WHERE id=:i AND chat_key=:k"), {"i": del_id, "k": ck})
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 警示 #{del_id} 已刪除"))
                except Exception as e:
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text=f"刪除失敗：{e}"))
                return

            # /alert add <symbol> <target/maXX> <above/below>
            if sub == "add":
                # 格式：/alert add AAPL 200 above
                #       /alert add AAPL ma20 below
                if len(parts) < 5:
                    _bot_api.reply_message(event.reply_token, TextSendMessage(
                        text="格式說明：\n\n目標價：\n/alert add AAPL 200 above\n/alert add 0050.TW 150 below\n/alert add USDTWD=X 32.5 below\n\n均線突破：\n/alert add AAPL ma20 below\n/alert add 2330.TW ma60 above"
                    ))
                    return
                symbol = parts[2].upper()
                value_str = parts[3].lower()
                direction = parts[4].lower()

                if direction not in ("above", "below"):
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text="方向請輸入 above（漲到/漲破）或 below（跌到/跌破）"))
                    return

                try:
                    if value_str.startswith("ma"):
                        # 均線警示
                        ma_period = int(value_str[2:])
                        with engine.begin() as conn:
                            conn.execute(text("""\n                            INSERT INTO price_alerts(chat_key, symbol, alert_type, condition, ma_period)\n                            VALUES (:k, :s, 'ma', :c, :m)\n                            """), {"k": ck, "s": symbol, "c": direction, "m": ma_period})
                        cross = "漲破" if direction == "above" else "跌破"
                        _bot_api.reply_message(event.reply_token, TextSendMessage(
                            text=f"✅ 均線警示已設定！\n標的：{symbol}\n條件：{cross} MA{ma_period}\n\n當條件成立時龍蝦會通知你 🔔"
                        ))
                    else:
                        # 目標價警示
                        target = float(value_str)
                        with engine.begin() as conn:
                            conn.execute(text("""\n                            INSERT INTO price_alerts(chat_key, symbol, alert_type, condition, target_value)\n                            VALUES (:k, :s, 'price', :c, :t)\n                            """), {"k": ck, "s": symbol, "c": direction, "t": target})
                        cond_str = "漲到" if direction == "above" else "跌到"
                        _bot_api.reply_message(event.reply_token, TextSendMessage(
                            text=f"✅ 價格警示已設定！\n標的：{symbol}\n條件：{cond_str} {target}\n\n當條件成立時龍蝦會通知你 🔔"
                        ))
                except Exception as e:
                    _bot_api.reply_message(event.reply_token, TextSendMessage(text=f"設定失敗：{e}"))
                return

            # /alert 說明
            _bot_api.reply_message(event.reply_token, TextSendMessage(
                text="價格警示指令：\n/alert add <標的> <目標價/均線> <above/below>\n/alert list → 查看清單\n/alert del <編號> → 刪除\n\n範例：\n/alert add AAPL 200 above\n/alert add 2330.TW ma20 below\n/alert add USDTWD=X 32.5 below"
            ))
            return

        # NEWS PDF
        if cmd == "news pdf" or cmd == "news":
            from news_fetcher import generate_news_report
            from pdf_generator import create_and_upload_pdf
            _bot_api.reply_message(event.reply_token, TextSendMessage(text="正在抓取最新財經新聞並整理中，請稍候約30秒..."))
            try:
                report = generate_news_report()
                link = create_and_upload_pdf("news", report)
                _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"📰 今日財經新聞摘要 PDF 已產生！\n\n{link}"))
            except Exception as e:
                _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(text=f"新聞抓取失敗: {e}"))
            return

        # DETAIL
        if cmd.startswith("detail"):
            parts = text_raw.split(" ", 1)
            if len(parts) < 2 or not parts[1].strip():
                _bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入：/detail 商品代號（例：/detail U123）"))
                return
            query = parts[1].strip()
            matched_id, detail, candidates = db_find_detail(ck, query)
            if detail:
                _bot_api.reply_message(event.reply_token, TextSendMessage(text=detail[:4900]))
                return
            if candidates and matched_id is None:
                sample = "\n".join([f"• {c}" for c in candidates[:20]])
                _bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"請再精準一點，候選代號如下：\n{sample}"[:4900])
                )
                return
            _bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="查不到該代號或目前沒有已保存結果。請先 /calc 上傳 Excel。")
            )
            return

        # MAIL 郵件摘要
        if cmd.startswith("mail"):
            parts = text_raw.split(" ", 1)
            sub = parts[1].strip().lower() if len(parts) > 1 else ""
            _bot_api.reply_message(event.reply_token, TextSendMessage(text="📧 正在讀取郵件並分析中，請稍候..."))
            try:
                from gmail_manager import daily_email_summary, get_gmail_service, get_unread_emails, analyze_emails, format_line_message
                if sub == "unread":
                    service = get_gmail_service()
                    emails = get_unread_emails(service, max_results=10)
                    if not emails:
                        _bot_api.push_message(ck.split(":",1)[1], TextSendMessage(text="📧 目前沒有未讀郵件 ✅"))
                    else:
                        analysis = analyze_emails(emails)
                        msg = format_line_message(analysis, emails)
                        _bot_api.push_message(ck.split(":",1)[1], TextSendMessage(text=msg[:4900]))
                else:
                    summary = daily_email_summary()
                    _bot_api.push_message(ck.split(":",1)[1], TextSendMessage(text=summary[:4900]))
            except Exception as e:
                _bot_api.push_message(ck.split(":",1)[1], TextSendMessage(text=f"郵件讀取失敗：{e}"))
            return

        # ANALYSIS 完整三面向分析
        if cmd.startswith("analysis"):
            parts = text_raw.split(" ", 1)
            arg = parts[1].strip() if len(parts) > 1 else ""

            if not arg:
                _bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="📊 完整三面向分析指令：\n\n"
                         "/analysis NVDA — 技術+基本面+消息面\n"
                         "/analysis 2330 — 台積電完整分析\n"
                         "/analysis AAPL 3 — 指定月數（預設6個月）\n\n"
                         "包含：K線/RSI/成交量/EPS趨勢/營收成長/最新新聞情緒分析"
                ))
                return

            arg_parts = arg.split()
            symbol = arg_parts[0]
            months = 6
            if len(arg_parts) > 1 and arg_parts[1].isdigit():
                months = min(max(int(arg_parts[1]), 1), 12)

            _bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"📊 正在進行 {symbol.upper()} 完整三面向分析，請稍候約20秒..."
            ))

            try:
                from stock_analyzer import full_analysis
                from pdf_generator import upload_to_drive
                import tempfile, os

                img_bytes, summary = full_analysis(symbol, months=months)

                tmp_path = f"/tmp/analysis_{symbol}_{months}.png"
                with open(tmp_path, "wb") as f:
                    f.write(img_bytes)

                link = upload_to_drive(tmp_path, f"{symbol.upper()} Full Analysis {months}M.png")
                os.remove(tmp_path)

                msg = f"📊 {symbol.upper()} 完整分析 (近{months}個月)\n\n{summary}\n\n🔗 圖表：{link}"
                _bot_api.push_message(ck.split(":",1)[1], TextSendMessage(text=msg[:4900]))

            except Exception as e:
                _bot_api.push_message(ck.split(":",1)[1], TextSendMessage(
                    text=f"分析失敗：{str(e)[:200]}"
                ))
            return

        # TECH 技術分析
        if cmd.startswith("tech"):
            parts = text_raw.split(" ", 1)
            arg = parts[1].strip() if len(parts) > 1 else ""

            if not arg:
                _bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="📊 技術分析指令：\n\n"
                         "/tech mag7 — Magnificent Seven 比較分析\n"
                         "/tech AAPL — 單一股票分析 (美股)\n"
                         "/tech 2330 — 單一股票分析 (台股)\n"
                         "/tech AAPL 3 — 指定月數（預設6個月）\n\n"
                         "範例：\n/tech mag7\n/tech NVDA\n/tech 2330 3"
                ))
                return

            # 解析月數
            arg_parts = arg.split()
            symbol = arg_parts[0]
            months = 6
            if len(arg_parts) > 1 and arg_parts[1].isdigit():
                months = min(max(int(arg_parts[1]), 1), 12)

            _bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"📊 正在分析 {symbol.upper()}，請稍候約15秒..."
            ))

            try:
                from tech_analyzer import analyze_single, analyze_mag7
                import base64
                from linebot.models import ImageSendMessage

                if symbol.lower() == "mag7":
                    img_bytes, summary = analyze_mag7(months=months)
                    title = f"Magnificent Seven 技術分析 (近{months}個月)"
                else:
                    img_bytes, summary = analyze_single(symbol, months=months)
                    title = f"{symbol.upper()} 技術分析 (近{months}個月)"

                # 上傳圖片到 imgbb 或用 LINE image message
                # 這裡先存到 /tmp 再用 LINE upload
                import tempfile, os
                tmp_path = f"/tmp/tech_{symbol}_{months}.png"
                with open(tmp_path, "wb") as f:
                    f.write(img_bytes)

                # 上傳到 Google Drive 取得公開連結
                from pdf_generator import upload_to_drive
                link = upload_to_drive(tmp_path, f"{title}.png")
                os.remove(tmp_path)

                msg = f"📊 {title}\n\n{summary}\n\n🔗 圖表連結：{link}"
                _bot_api.push_message(ck.split(":",1)[1], TextSendMessage(text=msg[:4900]))

            except Exception as e:
                _bot_api.push_message(ck.split(":",1)[1], TextSendMessage(
                    text=f"技術分析失敗：{str(e)[:200]}"
                ))
            return

        # TRACKLOG — 查看排程執行記錄
        if cmd == "tracklog":
            with engine.begin() as conn:
                rows = conn.execute(text("""
                    SELECT job_name, status, message, executed_at
                    FROM eln_job_log
                    ORDER BY executed_at DESC
                    LIMIT 20
                """)).fetchall()
            if not rows:
                _bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="目前沒有執行記錄。"
                ))
                return
            lines = ["📋 最近排程記錄（最新20筆）：\n"]
            status_icon = {"success": "✅", "error": "❌", "started": "🔄", "skipped": "⏭️"}
            for r in rows:
                icon = status_icon.get(r[1], "•")
                tw_time = r[3].astimezone(TZ_TAIPEI_PYTZ).strftime("%m/%d %H:%M")
                msg = f"  {r[2]}" if r[2] else ""
                lines.append(f"{icon} {tw_time} {r[0]}{msg}")
            _bot_api.reply_message(event.reply_token, TextSendMessage(
                text="\n".join(lines)[:4900]
            ))
            return

        # FORGET 清除記憶
        if cmd == "forget":
            try:
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM chat_history WHERE chat_key = :k"), {"k": ck})
                _bot_api.reply_message(event.reply_token, TextSendMessage(text="🧹 記憶已清除！龍蝦從頭開始囉。"))
            except Exception as e:
                _bot_api.reply_message(event.reply_token, TextSendMessage(text=f"清除失敗：{e}"))
            return

        # AI fallback (Claude)
        reply = ai_reply(text_raw, chat_key=ck)
        _bot_api.reply_message(event.reply_token, TextSendMessage(text=reply[:4900]))

    except Exception as e:
        print("[ERROR] handle_text_message:", e)
        print(_traceback.format_exc())
        try:
            _bot_api.reply_message(
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


def generate_invest_post(image_data: bytes, reason: str, targets: str) -> str:
    """
    根據新聞截圖 + 用戶理由 + 標的，生成專業版和輕鬆版投資推播文。
    """
    import base64
    image_b64 = base64.b64encode(image_data).decode("utf-8")

    user_input = ""
    if reason:
        user_input += f"投資理由：{reason}\n"
    if targets:
        user_input += f"建議標的：{targets}\n"

    prompt = f"""你是一位台灣私人銀行的投資輔銷人員，正在為高資產客戶撰寫 LINE 群組推播文。

根據上方的新聞截圖，結合以下我提供的投資觀點，生成兩個版本的推播文：

{user_input}

【規格要求】
- 每個版本 100-250 字
- 繁體中文
- 不使用 Markdown（不用 ** # 等符號）
- 用 emoji 當標題和分段符號
- 結尾附上建議標的

【版本一：專業版】
適合傳給高資產客戶，語氣專業簡練，強調市場邏輯和風險意識。

【版本二：輕鬆版】
適合一般投資群組，語氣親切，用比喻讓人容易理解，帶點觀點但不失專業。

格式：
===專業版===
（內容）

===輕鬆版===
（內容）"""

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
                        "media_type": "image/jpeg",
                        "data": image_b64
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        }]
    )
    return (resp.content[0].text or "").strip()


@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    _bot_api = line_bot_api
    try:
        ck = chat_key_of(event)
        filename = getattr(event.message, "file_name", "") or ""
        ext = Path(filename).suffix.lower()
        print("[FILE]", ck, filename)

        message_id = event.message.id
        content = _bot_api.get_message_content(message_id)
        tmp_path = UPLOAD_DIR / f"upload_{int(datetime.now(TZ_TAIPEI).timestamp())}{ext}"
        with open(tmp_path, "wb") as f:
            for chunk in content.iter_content():
                f.write(chunk)

        # 音檔轉文字（mp3/m4a/wav/ogg/mp4）
        if ext in (".mp3", ".m4a", ".wav", ".ogg", ".mp4", ".webm"):
            _bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"🎙️ 收到音檔 {filename}，轉文字中，請稍候..."
            ))
            with open(tmp_path, "rb") as f:
                audio_data = f.read()
            text_result = transcribe_audio(audio_data, filename=filename)
            if not text_result:
                _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(
                    text="❌ 無法辨識語音內容，請確認音檔有聲音。"
                ))
                return
            push_long_message(_bot_api, ck.split(":", 1)[1], f"📝 語音轉文字：\n\n{text_result}")
            # 把轉出來的文字當對話繼續處理
            save_chat_history(ck, "user", f"[語音訊息] {text_result}")
            reply = chat_with_claude(ck, text_result)
            save_chat_history(ck, "assistant", reply)
            push_long_message(_bot_api, ck.split(":", 1)[1], reply)
            return

        # ELN 模式：有先打 /calc 且是 Excel
        if ext in (".xlsx", ".xls") and db_is_await(ck):
            db_set_await(ck, False)
            summary, top5_lines, detail_map, agent_name_map = run_autotracking(str(tmp_path))
            db_save_result(ck, summary, top5_lines, detail_map, agent_name_map)
            _bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=(summary or "已收到檔案，但沒有產出內容")[:4900])
            )
            return

        # 通用分析模式
        if ext in (".xlsx", ".xls", ".pdf", ".docx", ".pptx"):
            _bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"收到！正在分析 {filename}，請稍候...")
            )
            text = extract_text_from_file(str(tmp_path), filename)
            if not text:
                _bot_api.push_message(
                    ck.split(":", 1)[1],
                    TextSendMessage(text="檔案解析失敗，可能是掃描版 PDF 或格式不支援。")
                )
                return
            analysis = analyze_file_with_claude(text, filename)
            _bot_api.push_message(
                ck.split(":", 1)[1],
                TextSendMessage(text=analysis[:4900])
            )
            # 自動產生PDF
            try:
                from pdf_generator import create_and_upload_pdf
                link = create_and_upload_pdf("analysis", analysis, filename)
                _bot_api.push_message(
                    ck.split(":", 1)[1],
                    TextSendMessage(text=f"📄 分析報告 PDF：\n{link}")
                )
            except Exception as e:
                print(f"PDF upload error: {e}")
            return

        # 不支援的格式
        _bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"目前支援的檔案格式: PDF、Word、PowerPoint、Excel\n收到的格式 {ext} 暫不支援。")
        )

    except Exception as e:
        print("[ERROR] handle_file_message:", e)
        print(_traceback.format_exc())
        try:
            db_set_await(chat_key_of(event), False)
        except Exception:
            pass
        try:
            _bot_api.reply_message(
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
    _bot_api = line_bot_api
    try:
        ck = chat_key_of(event)
        print("[IMAGE]", ck)

        message_id = event.message.id
        content = _bot_api.get_message_content(message_id)

        image_data = b""
        for chunk in content.iter_content():
            image_data += chunk

        # ── /invest 模式：等待圖片
        invest_mode, _ = db_invest_get(ck)
        if invest_mode == "await_image":
            db_invest_set(ck, "await_reason", image=image_data)
            _bot_api.reply_message(event.reply_token, TextSendMessage(
                text="✅ 收到截圖！\n\n請輸入你的投資理由和標的：\n\n理由：（你認為能投資的原因）\n標的：（股票/ETF代號）"
            ))
            return

        # ── 一般圖片分析
        _bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="收到圖片！正在分析，請稍候...")
        )
        analysis = analyze_image_with_claude(image_data, "image/jpeg")
        _bot_api.push_message(
            ck.split(":", 1)[1],
            TextSendMessage(text=analysis[:4900])
        )

    except Exception as e:
        print("[ERROR] handle_image_message:", e)
        print(_traceback.format_exc())
        try:
            _bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="圖片處理時出錯了，請稍後再試。")
            )
        except Exception:
            pass



    @agent_handler.add(MessageEvent, message=FileMessage)
    def agent_handle_file(event):
        handle_file_message(event, _override_bot_api=agent_line_bot_api)

    @agent_handler.add(MessageEvent, message=ImageMessage)
    def agent_handle_image(event):
        handle_image_message(event, _override_bot_api=agent_line_bot_api)


def transcribe_audio(audio_data: bytes, filename: str = "audio.m4a") -> str:
    """
    將音檔壓縮後送 Whisper API 轉文字。
    超過 24MB 先用 pydub 壓縮成低位元率 mp3。
    """
    import tempfile, os
    from openai import OpenAI
    openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

    MAX_BYTES = 24 * 1024 * 1024  # 24MB 安全緩衝

    with tempfile.TemporaryDirectory() as tmp:
        ext = os.path.splitext(filename)[1].lower() or ".m4a"
        src_path = os.path.join(tmp, f"audio_input{ext}")
        with open(src_path, "wb") as f:
            f.write(audio_data)

        # 判斷是否需要壓縮
        if len(audio_data) > MAX_BYTES:
            try:
                from pydub import AudioSegment
                out_path = os.path.join(tmp, "audio_compressed.mp3")
                audio = AudioSegment.from_file(src_path)
                # 壓縮：單聲道、16kHz、32kbps — 足夠語音辨識
                audio = audio.set_channels(1).set_frame_rate(16000)
                audio.export(out_path, format="mp3", bitrate="32k")
                send_path = out_path
            except Exception as e:
                print(f"[Audio] 壓縮失敗，嘗試直接送原檔: {e}")
                send_path = src_path
        else:
            send_path = src_path

        with open(send_path, "rb") as f:
            resp = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=(os.path.basename(send_path), f),
                language="zh"
            )
        return resp.text.strip()


@handler.add(MessageEvent, message=AudioMessage)
def handle_audio_message(event, _override_bot_api=None):
    _bot_api = _override_bot_api or line_bot_api
    ck = chat_key_of(event)
    print(f"[AUDIO] {ck}")

    try:
        _bot_api.reply_message(event.reply_token, TextSendMessage(
            text="🎙️ 收到語音，轉文字中，請稍候..."
        ))

        # 下載音檔
        message_id = event.message.id
        content = _bot_api.get_message_content(message_id)
        audio_data = b""
        for chunk in content.iter_content():
            audio_data += chunk

        # 轉文字
        text_result = transcribe_audio(audio_data)

        if not text_result:
            _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(
                text="❌ 無法辨識語音內容，請確認音檔有聲音。"
            ))
            return

        # 推播轉文字結果
        push_long_message(_bot_api, ck.split(":", 1)[1], f"📝 語音轉文字：\n\n{text_result}")

        # 把轉出來的文字當對話繼續處理（存入對話記憶）
        save_chat_history(ck, "user", f"[語音訊息] {text_result}")
        reply = chat_with_claude(ck, text_result)
        save_chat_history(ck, "assistant", reply)
        push_long_message(_bot_api, ck.split(":", 1)[1], reply)

    except Exception as e:
        print(f"[ERROR] handle_audio_message: {e}")
        try:
            _bot_api.push_message(ck.split(":", 1)[1], TextSendMessage(
                text=f"❌ 語音處理失敗：{str(e)[:200]}"
            ))
        except Exception:
            pass

# ==============================
# 內建排程（取代獨立 Cron Jobs）
# ==============================

TZ_TAIPEI_PYTZ = pytz.timezone("Asia/Taipei")

def job_daily_report():
    """每天早上 06:00 台北時間 — 財經日報"""
    now = datetime.now(TZ_TAIPEI_PYTZ)
    if now.weekday() >= 5:
        print("[Scheduler] 週末跳過財經日報")
        write_job_log("財經日報", "skipped", "週末跳過")
        return
    print(f"[Scheduler] 開始產生財經日報 {now.strftime('%Y-%m-%d %H:%M')}")
    write_job_log("財經日報", "started", now.strftime('%Y-%m-%d %H:%M'))
    try:
        from daily_report import main as report_main
        report_main()
        print("[Scheduler] 財經日報推播成功")
        write_job_log("財經日報", "success", "推播成功")
    except Exception as e:
        print(f"[Scheduler] 財經日報失敗: {e}")
        write_job_log("財經日報", "error", str(e))

def job_alert_monitor():
    """每 15 分鐘 — 價格警示監控"""
    print(f"[Scheduler] 執行價格警示監控 {datetime.now(TZ_TAIPEI_PYTZ).strftime('%H:%M')}")
    try:
        from alert_monitor import main as alert_main
        alert_main()
    except Exception as e:
        print(f"[Scheduler] 價格警示失敗: {e}")

def job_mail_monitor():
    """每 15 分鐘 — 郵件監控"""
    print(f"[Scheduler] 執行郵件監控 {datetime.now(TZ_TAIPEI_PYTZ).strftime('%H:%M')}")
    try:
        from mail_monitor import main as mail_main
        mail_main()
    except Exception as e:
        print(f"[Scheduler] 郵件監控失敗: {e}")

def write_job_log(job_name: str, status: str, message: str = ""):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO eln_job_log (job_name, status, message, executed_at)
                VALUES (:j, :s, :m, NOW())
            """), {"j": job_name, "s": status, "m": message[:1000]})
    except Exception as e:
        print(f"[LOG] 寫入失敗: {e}")

def job_auto_tracking():
    """每天早上 06:02 台北時間 — ELN 自動追蹤"""
    now = datetime.now(TZ_TAIPEI_PYTZ)
    if now.weekday() >= 5:
        print("[Scheduler] 週末跳過 ELN 追蹤")
        write_job_log("ELN追蹤", "skipped", "週末跳過")
        return
    print(f"[Scheduler] 開始 ELN 自動追蹤 {now.strftime('%Y-%m-%d %H:%M')}")
    write_job_log("ELN追蹤", "started", now.strftime('%Y-%m-%d %H:%M'))
    try:
        from auto_tracking_cron import main as tracking_main
        tracking_main()
        print("[Scheduler] ELN 追蹤完成")
        write_job_log("ELN追蹤", "success", "追蹤完成")
    except Exception as e:
        print(f"[Scheduler] ELN 追蹤失敗: {e}")
        write_job_log("ELN追蹤", "error", str(e))

def start_scheduler():
    scheduler = BackgroundScheduler(timezone=TZ_TAIPEI_PYTZ)

    # 財經日報：每天 06:00 台北時間（週一到週五）
    scheduler.add_job(
        job_daily_report,
        CronTrigger(hour=6, minute=0, timezone=TZ_TAIPEI_PYTZ),
        id="daily_report",
        name="財經日報"
    )

    # ELN 自動追蹤：每天 06:00 台北時間（週一到週五）
    scheduler.add_job(
        job_auto_tracking,
        CronTrigger(hour=6, minute=2, timezone=TZ_TAIPEI_PYTZ),
        id="auto_tracking",
        name="ELN自動追蹤"
    )

    # 價格警示：每 15 分鐘
    scheduler.add_job(
        job_alert_monitor,
        IntervalTrigger(minutes=15),
        id="alert_monitor",
        name="價格警示"
    )

    # 郵件監控：每 15 分鐘（比警示延遲 5 分鐘，避免同時執行）
    scheduler.add_job(
        job_mail_monitor,
        IntervalTrigger(minutes=15, start_date=datetime.now(TZ_TAIPEI_PYTZ).replace(second=0, microsecond=0)),
        id="mail_monitor",
        name="郵件監控"
    )

    scheduler.start()
    print("[Scheduler] 排程啟動完成 ✅")
    print("[Scheduler] 財經日報: 每天 06:00（週一至週五）")
    print("[Scheduler] ELN追蹤: 每天 06:02（週一至週五）")
    print("[Scheduler] 價格警示: 每 15 分鐘")
    print("[Scheduler] 郵件監控: 每 15 分鐘")
    return scheduler

# 啟動排程
_scheduler = start_scheduler()
