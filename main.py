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
import urllib.request
import urllib.error
from openai import OpenAI

# — Alert ticker aliases —

ALERT_TICKER_ALIAS = {
# 指數
“dxy”: “DX-Y.NYB”,
“spx”: “^GSPC”,
“sp500”: “^GSPC”,
“ndx”: “^NDX”,
“nasdaq100”: “^NDX”,
“sox”: “^SOX”,
“vix”: “^VIX”,
“ust10y”: “^TNX”,
# 大宗商品
“gold”: “GC=F”,
“silver”: “SI=F”,
“oil”: “CL=F”,
“wti”: “CL=F”,
“copper”: “HG=F”,
# 外匯（yfinance 格式）
“usdjpy”: “JPY=X”,
“jpy”: “JPY=X”,
“eurusd”: “EURUSD=X”,
“eur”: “EURUSD=X”,
“gbpusd”: “GBPUSD=X”,
“gbp”: “GBPUSD=X”,
“usdtwd”: “TWD=X”,
“twd”: “TWD=X”,
“usdcnh”: “CNH=X”,
“cnh”: “CNH=X”,
“usdkrw”: “KRW=X”,
“krw”: “KRW=X”,
}

# ==============================

# ENV

# ==============================

LINE_CHANNEL_SECRET = os.getenv(“LINE_CHANNEL_SECRET”)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv(“LINE_CHANNEL_ACCESS_TOKEN”)
ANTHROPIC_API_KEY = os.getenv(“ANTHROPIC_API_KEY”)
OPENAI_API_KEY = os.getenv(“OPENAI_API_KEY”, “”)
GEMINI_API_KEY = os.getenv(“GEMINI_API_KEY”, “”)
DATABASE_URL = os.getenv(“DATABASE_URL”)
if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
raise RuntimeError(“Missing LINE env vars: LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN”)
if not DATABASE_URL:
raise RuntimeError(“Missing env var: DATABASE_URL”)
if not ANTHROPIC_API_KEY:
raise RuntimeError(“Missing env var: ANTHROPIC_API_KEY”)
if DATABASE_URL.startswith(“postgres://”):
DATABASE_URL = DATABASE_URL.replace(“postgres://”, “postgresql+psycopg://”, 1)
elif DATABASE_URL.startswith(“postgresql://”):
DATABASE_URL = DATABASE_URL.replace(“postgresql://”, “postgresql+psycopg://”, 1)

# 龍蝦主Bot

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ── ELN Auto-Tracking 群組 Bot（第二個 handler）──

ELN_GROUP_CHANNEL_SECRET = os.getenv(“ELN_GROUP_CHANNEL_SECRET”, “”)
ELN_GROUP_ACCESS_TOKEN = os.getenv(“ELN_GROUP_ACCESS_TOKEN”, “”)
eln_group_bot_api = LineBotApi(ELN_GROUP_ACCESS_TOKEN) if ELN_GROUP_ACCESS_TOKEN else None
eln_group_handler = WebhookHandler(ELN_GROUP_CHANNEL_SECRET) if ELN_GROUP_CHANNEL_SECRET else None
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
app = FastAPI()
VERSION = “eln-autotracking-db-v3-2026-03-05”
TZ_TAIPEI = timezone(timedelta(hours=8))

# ==============================

# DB

# ==============================

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
def init_db():
with engine.begin() as conn:
conn.execute(text(”””
CREATE TABLE IF NOT EXISTS eln_last_report (
chat_key TEXT PRIMARY KEY,
summary TEXT NOT NULL,
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
“””))
conn.execute(text(”””
CREATE TABLE IF NOT EXISTS eln_detail (
chat_key TEXT NOT NULL,
bond_id TEXT NOT NULL,
detail TEXT NOT NULL,
agent_name TEXT,
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY (chat_key, bond_id)
);
“””))
conn.execute(text(”””
CREATE TABLE IF NOT EXISTS eln_top5 (
chat_key TEXT NOT NULL,
line_no INT NOT NULL,
text_line TEXT NOT NULL,
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY (chat_key, line_no)
);
“””))
conn.execute(text(”””
CREATE TABLE IF NOT EXISTS eln_session (
chat_key TEXT PRIMARY KEY,
await_file BOOLEAN NOT NULL DEFAULT FALSE,
invest_mode TEXT NOT NULL DEFAULT ‘’,
invest_image BYTEA,
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
“””))
conn.execute(text(”””
CREATE TABLE IF NOT EXISTS transcript_cache (
chat_key TEXT PRIMARY KEY,
transcript TEXT NOT NULL DEFAULT ‘’,
summary TEXT NOT NULL DEFAULT ‘’,
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
“””))
conn.execute(text(”””
CREATE TABLE IF NOT EXISTS meeting_transcripts (
id BIGSERIAL PRIMARY KEY,
chat_key TEXT NOT NULL,
file_name TEXT,
transcript TEXT NOT NULL DEFAULT ‘’,
summary TEXT NOT NULL DEFAULT ‘’,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
“””))
for col, typedef in [
(“invest_mode”, “TEXT NOT NULL DEFAULT ‘’”),
(“invest_image”, “BYTEA”),
]:
try:
conn.execute(text(f”ALTER TABLE eln_session ADD COLUMN IF NOT EXISTS {col} {typedef}”))
except Exception:
pass
init_db()
def db_set_await(chat_key: str, await_file: bool):
with engine.begin() as conn:
conn.execute(text(”””
INSERT INTO eln_session(chat_key, await_file, updated_at)
VALUES (:k, :a, NOW())
ON CONFLICT (chat_key) DO UPDATE
SET await_file=:a, updated_at=NOW()
“””), {“k”: chat_key, “a”: bool(await_file)})
def db_is_await(chat_key: str) -> bool:
with engine.begin() as conn:
row = conn.execute(
text(“SELECT await_file FROM eln_session WHERE chat_key=:k”),
{“k”: chat_key}
).fetchone()
return bool(row and row[0])
def db_invest_set(chat_key: str, mode: str, image: bytes = None):
with engine.begin() as conn:
conn.execute(text(”””
INSERT INTO eln_session(chat_key, await_file, invest_mode, invest_image, updated_at)
VALUES (:k, FALSE, :m, :img, NOW())
ON CONFLICT (chat_key) DO UPDATE
SET invest_mode=:m, invest_image=COALESCE(:img, eln_session.invest_image), updated_at=NOW()
“””), {“k”: chat_key, “m”: mode, “img”: image})
def db_invest_get(chat_key: str):
with engine.begin() as conn:
row = conn.execute(
text(“SELECT invest_mode, invest_image FROM eln_session WHERE chat_key=:k”),
{“k”: chat_key}
).fetchone()
if row:
return row[0] or “”, bytes(row[1]) if row[1] else None
return “”, None
def db_set_transcript_cache(chat_key: str, transcript: str, summary: str):
with engine.begin() as conn:
conn.execute(text(”””
INSERT INTO transcript_cache(chat_key, transcript, summary, updated_at)
VALUES (:k, :t, :s, NOW())
ON CONFLICT (chat_key) DO UPDATE
SET transcript=:t, summary=:s, updated_at=NOW()
“””), {“k”: chat_key, “t”: transcript[:200000], “s”: summary[:50000]})
def db_get_transcript_cache(chat_key: str):
with engine.begin() as conn:
row = conn.execute(text(“SELECT transcript, summary FROM transcript_cache WHERE chat_key=:k”), {“k”: chat_key}).fetchone()
if row:
return {“transcript”: row[0] or “”, “summary”: row[1] or “”}
return None
def db_clear_transcript_cache(chat_key: str):
with engine.begin() as conn:
conn.execute(text(“DELETE FROM transcript_cache WHERE chat_key=:k”), {“k”: chat_key})
def db_save_meeting_transcript(chat_key: str, file_name: str, transcript: str, summary: str):
with engine.begin() as conn:
conn.execute(text(”””
INSERT INTO meeting_transcripts(chat_key, file_name, transcript, summary, created_at)
VALUES (:k, :f, :t, :s, NOW())
“””), {“k”: chat_key, “f”: file_name, “t”: transcript[:500000], “s”: summary[:100000]})
def db_get_latest_meeting_transcript(chat_key: str):
with engine.begin() as conn:
row = conn.execute(text(”””
SELECT transcript, summary, file_name, created_at
FROM meeting_transcripts
WHERE chat_key=:k
ORDER BY created_at DESC
LIMIT 1
“””), {“k”: chat_key}).fetchone()
if not row:
return None
return {“transcript”: row[0] or “”, “summary”: row[1] or “”, “file_name”: row[2] or “”, “created_at”: row[3]}
def db_save_result(chat_key: str, summary: str, top5_lines: list[str], detail_map: dict[str, str], agent_name_map: dict[str, str] = {}):
with engine.begin() as conn:
conn.execute(text(”””
INSERT INTO eln_last_report(chat_key, summary, updated_at)
VALUES (:k, :s, NOW())
ON CONFLICT (chat_key) DO UPDATE
SET summary=:s, updated_at=NOW()
“””), {“k”: chat_key, “s”: summary})
conn.execute(text(“DELETE FROM eln_top5 WHERE chat_key=:k”), {“k”: chat_key})
for i, line in enumerate(top5_lines, start=1):
conn.execute(text(”””
INSERT INTO eln_top5(chat_key, line_no, text_line, updated_at)
VALUES (:k, :n, :t, NOW())
“””), {“k”: chat_key, “n”: i, “t”: line})
conn.execute(text(“DELETE FROM eln_detail WHERE chat_key=:k”), {“k”: chat_key})
for bond_id, detail in detail_map.items():
agent = agent_name_map.get(bond_id, “-”)
conn.execute(text(”””
INSERT INTO eln_detail(chat_key, bond_id, detail, agent_name, updated_at)
VALUES (:k, :b, :d, :a, NOW())
“””), {“k”: chat_key, “b”: bond_id, “d”: detail, “a”: agent})
def db_get_report(chat_key: str) -> str | None:
with engine.begin() as conn:
row = conn.execute(
text(“SELECT summary FROM eln_last_report WHERE chat_key=:k”),
{“k”: chat_key}
).fetchone()
return row[0] if row else None
def db_list_bonds(chat_key: str, limit: int = 100) -> list[tuple[str, str, str]]:
“”“回傳 (bond_id, agent_name, detail) 三個欄位”””
with engine.begin() as conn:
rows = conn.execute(text(”””
SELECT bond_id, COALESCE(agent_name, ‘-’), COALESCE(detail, ‘’)
FROM eln_detail
WHERE chat_key=:k
ORDER BY agent_name ASC, bond_id ASC
LIMIT :lim
“””), {“k”: chat_key, “lim”: int(limit)}).fetchall()
return [(r[0], r[1], r[2]) for r in rows] if rows else []

def bond_status_tag(detail: str) -> str:
“””
根據 detail 文字判斷整張商品狀態，回傳標記。
狀態文字在兩條 –––––––– 之間。
“””
# 擷取狀態區段（兩條分隔線之間）
import re as _re
status_block = “”
m = _re.search(r”-{4,}\n(.*?)\n-{4,}”, detail, _re.S)
if m:
status_block = m.group(1).strip()
# 判斷順序：提前KO > 到期 > 其他
if “提前出場” in status_block or “🎉” in status_block:
return “ ✅提前KO”
if “到期獲利” in status_block:
return “ 🏁到期獲利”
if “到期接股” in status_block:
return “ 😭到期接股”
if “到期保本” in status_block:
return “ 🛡️到期保本”
if “到期” in status_block:
return “ 🏁到期”
return “”
def push_long_message(bot_api, target_id: str, text: str, max_len: int = 4800):
if not text:
return
text = str(text)
chunks = []
current = “”
for line in text.split(”\n”):
while len(line) > max_len:
if current:
chunks.append(current)
current = “”
chunks.append(line[:max_len])
line = line[max_len:]
candidate = line if not current else current + “\n” + line
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
rows = conn.execute(text(”””
SELECT bond_id FROM eln_detail WHERE chat_key=:k
“””), {“k”: chat_key}).fetchall()
keys = [r[0] for r in rows] if rows else []
if not keys:
return None, None, []
norm_map = {k.strip().upper(): k for k in keys}
if q_norm in norm_map:
real = norm_map[q_norm]
with engine.begin() as conn:
row = conn.execute(text(”””
SELECT detail FROM eln_detail WHERE chat_key=:k AND bond_id=:b
“””), {“k”: chat_key, “b”: real}).fetchone()
return real, (row[0] if row else None), []
hits = [k for k in keys if q_norm in k.strip().upper()]
if len(hits) == 1:
real = hits[0]
with engine.begin() as conn:
row = conn.execute(text(”””
SELECT detail FROM eln_detail WHERE chat_key=:k AND bond_id=:b
“””), {“k”: chat_key, “b”: real}).fetchone()
return real, (row[0] if row else None), []
if len(hits) > 1:
return None, None, hits[:20]
return None, None, keys[:20]

# ==============================

# Optional: store default push target

# ==============================

BASE_DIR = Path(”/tmp”)
TARGET_FILE = BASE_DIR / “targets.json”
def _read_json(path: Path, default):
if path.exists():
try:
return json.loads(path.read_text(encoding=“utf-8”))
except Exception:
return default
return default
def _write_json(path: Path, data):
path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding=“utf-8”)
def load_targets():
return _read_json(TARGET_FILE, {})
def save_targets(data: dict):
_write_json(TARGET_FILE, data)

# ==============================

# Health check

# ==============================

@app.get(”/”)
def root():
return {“status”: “ok”, “service”: “eln-bot”, “webhook”: “/callback”}
@app.get(”/whoami”)
def whoami():
return {“service”: “eln-bot”, “version”: VERSION}

# ==============================

# Webhook endpoint

# ==============================

@app.post(”/callback”)
async def callback(request: Request):
signature = request.headers.get(“X-Line-Signature”)
body = await request.body()
body_text = body.decode(“utf-8”)
try:
handler.handle(body_text, signature)
return “OK”
except InvalidSignatureError:
raise HTTPException(status_code=400, detail=“Invalid signature”)

# 用 threading.local 記錄當前使用哪個 bot_api

import threading
_current_bot_api = threading.local()

@app.post(”/callback2”)
async def callback2(request: Request):
“”“ELN Auto-Tracking 群組 Bot 的 Webhook”””
if not eln_group_handler:
raise HTTPException(status_code=503, detail=“ELN group handler not configured”)
signature = request.headers.get(“X-Line-Signature”)
body = await request.body()
body_text = body.decode(“utf-8”)
try:
eln_group_handler.handle(body_text, signature)
return “OK”
except InvalidSignatureError:
raise HTTPException(status_code=400, detail=“Invalid signature”)

# ==============================

# Chat key

# ==============================

def chat_key_of(event) -> str:
if event.source.type == “group”:
return f”group:{event.source.group_id}”
if event.source.type == “room”:
return f”room:{event.source.room_id}”
return f”user:{event.source.user_id}”

# ==============================

# Adapter: core -> (summary, top5, detail_map)

# ==============================

def run_autotracking(file_path: str, lookback_days: int = 3, notify_ki_daily: bool = True):
out = calculate_from_file(
file_path=file_path,
lookback_days=lookback_days,
notify_ki_daily=notify_ki_daily
)
df = out.get(“results_df”)
report = out.get(“report_text”, “”) or “”
top5_lines: list[str] = []
detail_map: dict[str, str] = {}
agent_name_map: dict[str, str] = {}
if df is not None and getattr(df, “empty”, True) is False:
top = df.head(5)
for _, r in top.iterrows():
try:
status_first = str(r[“狀態”]).splitlines()[0] if “狀態” in r.index else “”
except Exception:
status_first = “”
bond_id = str(r[“債券代號”]) if “債券代號” in r.index else “-”
ptype = str(r[“Type”]) if “Type” in r.index else “-”
top5_lines.append(f”● {bond_id} {ptype}｜{status_first}”)
for _, r in df.iterrows():
_id = str(r[“債券代號”]).strip() if “債券代號” in r.index else “”
if not _id or _id == “nan”:
continue
t_details = []
for c in df.columns:
if str(c).endswith(”_Detail”):
v = r[c] if c in r.index else “”
if v:
t_details.append(str(v))
agent = str(r[“Name”] if “Name” in r.index else “-”).strip() or “-”
agent_name_map[_id] = agent
detail_text = (
f”【商品】{_id}\n”
f”類型: {r[‘Type’] if ‘Type’ in r.index else ‘-’}\n”
f”理專: {agent}\n”
f”交易日: {r[‘交易日’] if ‘交易日’ in r.index else ‘-’}\n”
f”KO設定: {r[‘KO設定’] if ‘KO設定’ in r.index else ‘-’}\n”
f”最差表現: {r[‘最差表現’] if ‘最差表現’ in r.index else ‘-’}\n”
f”––––––––\n”
f”{r[‘狀態’] if ‘狀態’ in r.index else ‘’}\n”
f”––––––––\n”
+ (”\n\n”.join(t_details) if t_details else “”)
)
detail_map[_id] = detail_text
summary = report
if top5_lines:
summary += “\n\n【前5筆摘要】\n” + “\n”.join(top5_lines)
return summary, top5_lines, detail_map, agent_name_map

# ==============================

# AI fallback (Claude)

# ==============================

SYSTEM_PROMPT = (
“你是「龍蝦」，一位專屬於 Albert 的智慧型 LINE 助理。\n\n”
“【關於 Albert 的工作背景】\n”
“• 職位：銀行財富管理部門 投資輔銷人員（Investment Sales）\n”
“• 客群：主要負責高資產客戶（HNW）的投資規劃與資產配置\n”
“• 業務範疇：\n”
“  → 投資商品：基金、債券、ELN（股票連結票據）、結構型商品、ETF\n”
“  → 質借業務：Lombard Lending（有價證券質借）、金市債券質借、信託質借\n”
“  → 信託業務：資產信託規劃、境外資金匯回配置\n”
“  → 教育訓練：經常幫行內專員上課，教導基金、債券、結構型產品、ELN等商品知識\n”
“• 常見需求：市場分析、商品說明、客戶推播文案、專員教育訓練教材、投資建議\n\n”
“【你的角色定位】\n”
“你是 Albert 最得力的資深助理，不只回答問題，而是像一位懂市場又懂銷售的同事：\n”
“• 用投資輔銷的角度思考，理解他面對的是高資產客戶與行內專員\n”
“• 遇到市場問題 → 提供深度分析，並附上「可以這樣跟客戶說」的話術\n”
“• 遇到商品問題 → 說明商品特性、適合的高資產客群、風險與機會\n”
“• 遇到質借/信託問題 → 說明業務邏輯、適用情境、常見客戶疑問\n”
“• 遇到教學需求 → 以簡單易懂的方式說明，適合用來對專員解說\n”
“• 遇到文案需求 → 直接產出可複製貼上的推播內容\n”
“• 遇到 ELN 相關問題 → 提示使用 /calc 或 /detail 指令\n\n”
“【回答原則】\n”
“1. 有深度：提供背景、現況、影響、展望，不能太簡短\n”
“2. 結構清晰：重點分段，讓人一眼看懂\n”
“3. 客觀中立：呈現多空兩面，讓 Albert 自行判斷\n”
“4. 實用導向：一般問題結尾補充「💬 可以這樣跟客戶/專員說：…」\n”
“5. 市場問題格式：📌 定義 → 📊 現況 → ⚖️ 機會與風險 → 🔭 展望 → 💬 話術\n”
“6. 商品教學格式：📌 商品定義 → 🔧 運作方式 → 👤 適合客群 → ⚠️ 風險提示 → 💬 話術\n”
“7. 質借業務格式：📌 業務說明 → 💡 適用情境 → 📊 利率/條件 → ❓ 常見客戶問題\n\n”
“【格式規定】\n”
“• 絕對禁止 Markdown：不可出現 ##、**、— 等符號\n”
“• 段落標題用 emoji，例如 📌 📊 ⚖️ 🔭 💡 💬 🔧 👤 ⚠️\n”
“• 條列用 • 或 → 符號\n”
“• 數字、百分比、金額要具體，不要模糊帶過\n”
“• 回答長度要足夠，高資產客戶的問題不能給太簡短的答案\n”
)
def get_chat_history(chat_key: str, limit: int = 10) -> list[dict]:
try:
with engine.begin() as conn:
rows = conn.execute(text(”””
SELECT role, content FROM chat_history
WHERE chat_key = :k
ORDER BY created_at DESC
LIMIT :n
“””), {“k”: chat_key, “n”: limit}).fetchall()
return [{“role”: r[0], “content”: r[1]} for r in reversed(rows)]
except Exception as e:
print(f”get_chat_history error: {e}”)
return []
def save_chat_history(chat_key: str, role: str, content: str):
try:
with engine.begin() as conn:
conn.execute(text(”””
INSERT INTO chat_history (chat_key, role, content)
VALUES (:k, :r, :c)
“””), {“k”: chat_key, “r”: role, “c”: content[:4000]})
with engine.begin() as conn:
conn.execute(text(”””
DELETE FROM chat_history
WHERE chat_key = :k
AND id NOT IN (
SELECT id FROM chat_history
WHERE chat_key = :k
ORDER BY created_at DESC
LIMIT 50
)
“””), {“k”: chat_key})
except Exception as e:
print(f”save_chat_history error: {e}”)
AUTO_FINANCE_KEYWORDS = [
“財經”, “市場”, “美股”, “台股”, “債券”, “殖利率”, “基金”, “匯率”, “美元”,
“聯準會”, “fed”, “fomc”, “通膨”, “cpi”, “pce”, “非農”, “失業率”,
“投資”, “分析”, “總經”, “景氣”, “eln”, “結構型”, “信用利差”, “公司債”
]
AUTO_FILE_KEYWORDS = [
“pdf”, “簡報”, “圖片”, “圖表”, “文件”, “檔案”, “word”, “excel”
]
PDF_NL_KEYWORDS = [
“pdf”, “做成pdf”, “生成pdf”, “轉成pdf”, “輸出pdf”, “匯出pdf”,
“做成 pdf”, “生成 pdf”, “轉成 pdf”, “輸出 pdf”, “匯出 pdf”,
“做成報告”, “生成報告”, “轉成報告”
]
def _normalize_history_for_chat(chat_key: str) -> list[dict]:
history = get_chat_history(chat_key) if chat_key else []
cleaned = []
for item in history:
role = item.get(“role”, “user”)
content = item.get(“content”, “”)
if not content:
continue
if role not in (“user”, “assistant”):
role = “user”
if content.startswith(”[claude] “):
content = content[len(”[claude] “):]
elif content.startswith(”[gpt] “):
content = content[len(”[gpt] “):]
elif content.startswith(”[gemini] “):
content = content[len(”[gemini] “):]
cleaned.append({“role”: role, “content”: content})
return cleaned
def ai_claude(user_text: str, chat_key: str = “”) -> str:
history = _normalize_history_for_chat(chat_key)
messages = history + [{“role”: “user”, “content”: user_text}]
resp = claude_client.messages.create(
model=“claude-sonnet-4-20250514”,
max_tokens=1200,
system=SYSTEM_PROMPT,
messages=messages
)
reply = (resp.content[0].text or “”).strip()
if chat_key:
save_chat_history(chat_key, “user”, user_text)
save_chat_history(chat_key, “assistant”, f”[claude] {reply}”)
return reply
def ai_claude_long(user_text: str, chat_key: str = “”) -> str:
history = _normalize_history_for_chat(chat_key)
messages = history + [{“role”: “user”, “content”: user_text}]
resp = claude_client.messages.create(
model=“claude-sonnet-4-20250514”,
max_tokens=2500,
system=SYSTEM_PROMPT,
messages=messages
)
reply = (resp.content[0].text or “”).strip()
if chat_key:
save_chat_history(chat_key, “user”, user_text)
save_chat_history(chat_key, “assistant”, f”[claude-long] {reply}”)
return reply
def ai_chatgpt(user_text: str, chat_key: str = “”) -> str:
if not openai_client:
return ai_claude(user_text, chat_key)
history = _normalize_history_for_chat(chat_key)
messages = [{“role”: “system”, “content”: SYSTEM_PROMPT}] + history + [{“role”: “user”, “content”: user_text}]
resp = openai_client.chat.completions.create(
model=“gpt-4.1-mini”,
messages=messages,
temperature=0.4,
max_tokens=1800
)
reply = (resp.choices[0].message.content or “”).strip()
if chat_key:
save_chat_history(chat_key, “user”, user_text)
save_chat_history(chat_key, “assistant”, f”[gpt] {reply}”)
return reply
def ai_gemini(user_text: str, chat_key: str = “”) -> str:
if not GEMINI_API_KEY:
return ai_claude(user_text, chat_key)
history = _normalize_history_for_chat(chat_key)
history_text = “\n”.join([f”{m[‘role’]}: {m[‘content’]}” for m in history[-10:]])
prompt = (
f”{SYSTEM_PROMPT}\n\n”
f”以下是近期對話：\n{history_text}\n\n”
f”使用者最新問題：\n{user_text}”
)
url = f”https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}”
payload = {
“contents”: [
{
“parts”: [
{“text”: prompt}
]
}
]
}
req = urllib.request.Request(
url,
data=json.dumps(payload).encode(“utf-8”),
headers={“Content-Type”: “application/json”},
method=“POST”
)
try:
with urllib.request.urlopen(req, timeout=60) as resp:
data = json.loads(resp.read().decode(“utf-8”))
reply = (
data.get(“candidates”, [{}])[0]
.get(“content”, {})
.get(“parts”, [{}])[0]
.get(“text”, “”)
.strip()
)
if not reply:
return ai_claude(user_text, chat_key)
if chat_key:
save_chat_history(chat_key, “user”, user_text)
save_chat_history(chat_key, “assistant”, f”[gemini] {reply}”)
return reply
except urllib.error.HTTPError as e:
print(f”[Gemini HTTPError] {e}”)
return ai_claude(user_text, chat_key)
except Exception as e:
print(f”[Gemini Error] {e}”)
return ai_claude(user_text, chat_key)
def ai_router(user_text: str, chat_key: str = “”, forced_model: str = “”) -> str:
text_l = (user_text or “”).lower().strip()
if forced_model == “claude”:
return ai_claude(user_text, chat_key)
if forced_model == “gpt”:
return ai_chatgpt(user_text, chat_key)
if forced_model == “gemini”:
return ai_gemini(user_text, chat_key)
if any(k in text_l for k in AUTO_FINANCE_KEYWORDS):
return ai_claude(user_text, chat_key)
if any(k in text_l for k in AUTO_FILE_KEYWORDS):
return ai_gemini(user_text, chat_key)
return ai_chatgpt(user_text, chat_key)
def classify_report_topic(user_text: str) -> str:
text_l = (user_text or “”).lower()
macro_keywords = [
“戰爭”, “衝突”, “重建”, “制裁”, “關稅”, “降息”, “升息”, “聯準會”, “fed”, “通膨”,
“景氣”, “衰退”, “地緣政治”, “原油”, “油價”, “中東”, “美元”, “公債”, “殖利率”,
“金融市場”, “總經”, “非農”, “cpi”, “pce”, “失業率”, “財政”, “重建行情”
]
equity_keywords = [
“股票”, “股價”, “公司”, “企業”, “財報”, “估值”, “獲利”, “eps”, “ai”, “gpu”,
“供應鏈”, “半導體”, “伺服器”, “金融股”, “銀行股”, “科技股”, “產業”, “競爭力”,
“台積電”, “nvidia”, “nvda”, “amd”, “avgo”, “smci”, “aapl”, “meta”
]
product_keywords = [
“基金”, “債券”, “etf”, “eln”, “結構型”, “信託”, “質借”, “lombard”, “票據”,
“商品”, “配息”, “收益”, “信用債”, “投資等級”, “高收益債”, “可轉債”
]
if any(k in text_l for k in macro_keywords):
return “macro”
if any(k in text_l for k in equity_keywords):
return “equity”
if any(k in text_l for k in product_keywords):
return “product”
return “general”
def build_macro_prompt(user_text: str) -> str:
return f”””
你是一位頂級總經與跨資產策略研究員，服務對象為高資產客戶、機構投資人與銀行投顧團隊。
請根據以下主題，撰寫一份可直接輸出成 PDF 的繁體中文深度研究報告。
研究主題：
{user_text}
請依序撰寫以下章節，且每一節都要有足夠內容，不可過短：
【封面摘要】
【一、事件與市場背景】
【二、行情形成機制】
【三、受惠產業與資產主線】
【四、金融市場影響】
【五、情境分析】
【六、投資機會與策略建議】
【七、主要風險與反證】
【八、結論】
寫作要求：
• 採正式 sell-side / 投資銀行研究報告語氣
• 內容要有脈絡、有推演、有跨資產傳導
• 要寫出市場為何交易這個主題，而不只是摘要事件
• 每節至少 2 到 4 段，內容充實
• 可使用【】作章節標題
• 禁止 Markdown 符號，例如 #、*、-、—
• 直接輸出完整報告正文
“””
def build_equity_prompt(user_text: str) -> str:
return f”””
你是一位資深產業與股票研究員，請根據以下主題撰寫一份可直接輸出成 PDF 的繁體中文深度研究報告。
研究主題：
{user_text}
請依序撰寫以下章節：
【封面摘要】
【一、產業與公司背景】
【二、成長動能與投資邏輯】
【三、競爭格局與關鍵優勢】
【四、財務與估值觀察】
【五、市場可能如何交易這個題材】
【六、投資機會與布局方式】
【七、主要風險與反證】
【八、結論】
寫作要求：
• 語氣像大型券商或資產管理公司的產業研究報告
• 不能只講故事，必須講商業模式、獲利邏輯、估值與市場預期
• 每節至少 2 到 4 段，內容充實
• 若題目涉及單一公司，也要帶出其所屬產業位置與市場定價邏輯
• 可使用【】作章節標題
• 禁止 Markdown 符號
• 直接輸出完整報告正文
“””
def build_product_prompt(user_text: str) -> str:
return f”””
你是一位銀行財富管理研究員，請根據以下主題撰寫一份可直接輸出成 PDF 的繁體中文深度研究報告。
研究主題：
{user_text}
請依序撰寫以下章節：
【封面摘要】
【一、商品定位與市場背景】
【二、報酬來源與運作機制】
【三、適合客群與資產配置角色】
【四、優勢、限制與常見誤解】
【五、目前市場環境下的投資價值】
【六、投資建議與配置思維】
【七、主要風險與注意事項】
【八、結論】
寫作要求：
• 語氣像銀行投顧或產品研究報告
• 要兼具專業性與可銷售性，讓內容可以拿去和高資產客戶討論
• 內容要有深度，不可只有商品說明書口吻
• 每節至少 2 到 4 段，內容充實
• 可使用【】作章節標題
• 禁止 Markdown 符號
• 直接輸出完整報告正文
“””
def build_general_prompt(user_text: str) -> str:
return f”””
你是一位資深投資研究員，請根據以下主題撰寫一份可直接輸出成 PDF 的繁體中文深度研究報告。
研究主題：
{user_text}
請依序撰寫以下章節：
【封面摘要】
【一、主題背景】
【二、核心分析】
【三、市場與產業影響】
【四、投資機會】
【五、主要風險】
【六、結論與建議】
寫作要求：
• 採正式研究報告語氣
• 要有完整段落與分析，不可像聊天摘要
• 每節至少 2 到 4 段，內容充實
• 可使用【】作章節標題
• 禁止 Markdown 符號
• 直接輸出完整報告正文
“””
def build_pdf_report_content(user_text: str, chat_key: str = “”) -> str:
topic_type = classify_report_topic(user_text)
if topic_type == “macro”:
prompt = build_macro_prompt(user_text)
elif topic_type == “equity”:
prompt = build_equity_prompt(user_text)
elif topic_type == “product”:
prompt = build_product_prompt(user_text)
else:
prompt = build_general_prompt(user_text)
return ai_claude_long(prompt, chat_key)
def build_transcript_summary(transcript: str, chat_key: str = “”) -> str:
prompt = f”””
你是一位專業會議紀錄助理。請將以下逐字稿整理為繁體中文重點摘要。
要求：

1. 先寫【會議摘要】
1. 再寫【重點整理】
1. 再寫【待辦事項】
1. 條列清楚、內容具體
1. 禁止 Markdown 符號
1. 內容務必根據逐字稿，不要憑空補充
   逐字稿：
   {transcript}
   “””
   return ai_claude(prompt, chat_key)
   def build_transcript_pdf_content(transcript: str, summary: str, chat_key: str = “”) -> str:
   prompt = f”””
   你是一位專業研究助理，請把以下會議逐字稿與摘要整理成可直接輸出為 PDF 的繁體中文正式會議報告。
   請使用這個結構：
   【封面摘要】
   【一、會議背景】
   【二、會議重點】
   【三、逐字稿重點整理】
   【四、結論】
   【五、待辦事項】
   要求：
   • 語氣正式、清楚、可直接給主管或客戶閱讀
   • 不要使用 Markdown 符號
   • 內容比摘要更完整，但不要逐字照抄全部逐字稿
   • 以摘要為主、逐字稿為輔，整理成正式文件
   會議摘要：
   {summary}
   逐字稿：
   {transcript[:120000]}
   “””
   return ai_claude_long(prompt, chat_key)
   @handler.add(MessageEvent, message=TextMessage)
   def handle_text_message(event):
   _bot_api = getattr(_current_bot_api, “api”, None) or line_bot_api
   try:
   text_raw = (event.message.text or “”).strip()
   tl = text_raw.lower().strip()
   ck = chat_key_of(event)
   is_group = event.source.type in (“group”, “room”)
   print(”[TEXT]”, ck, repr(text_raw))
   if tl.startswith(”/”):
   cmd = tl[1:].split()[0] if tl[1:].split() else “”
   raw_cmd = text_raw[1:]
   else:
   cmd = tl.split()[0] if tl.split() else tl
   raw_cmd = text_raw
   parts = text_raw.split(” “, 1)
   if is_group and not tl.startswith(”/”):
   return
   if cmd in (“help”, “?”, “指令”, “幫助”):
   help_arg = parts[1].strip().lower() if len(parts) > 1 else “”
   if is_group:
   msg = (
   “群組可用指令：\n”
   “/detail <商品代號>：查詢標的完整狀況（支援模糊搜尋）\n”
   “/list：列出所有可查商品代號\n”
   )
   else:
   if help_arg in (“alert”, “警示”):
   msg = (
   “🔔 Alert 指令說明\n”
   “─────────────────\n”
   “/alert add <標的> <價格> above/below\n”
   “/alert add <標的> above/below <價格>\n”
   “/alert add <標的> ma20 above/below\n”
   “/alert add <標的> ma5 cross ma20\n”
   “/alert add <標的> ma5 under ma20\n”
   “/alert list\n”
   “/alert del <編號>\n”
   “─────────────────\n”
   “別名：dxy / spx / ndx / sox / vix / ust10y / gold / silver / oil\n”
   “範例：\n”
   “/alert add dxy below 100\n”
   “/alert add ust10y above 45\n”
   “/alert add NVDA ma20 above\n”
   “/alert add NVDA ma5 cross ma20”
   )
   elif help_arg in (“eln”,):
   msg = (
   “📊 ELN 指令說明\n”
   “─────────────────\n”
   “/calc — 上傳 Excel 計算並保存\n”
   “/list — 列出所有可查商品代號\n”
   “/detail <代號> — 查詢單筆 KO/KI/狀態\n”
   “/eln upload — 上傳 Excel 並同步到 Supabase\n”
   “/eln run — 立即重跑最新 ELN\n”
   “/eln history — 查看歷史 Excel\n”
   “/eln result — 查看最近結果\n”
   “/runnow — 手動執行追蹤\n”
   “/tracklog — 查看最近排程紀錄”
   )
   elif help_arg in (“report”, “pdf”, “報告”, “簡報”):
   msg = (
   “📑 報告 / PDF 指令說明\n”
   “─────────────────\n”
   “/report <主題>\n”
   “/report <主題> brief/client/academic/hybrid\n”
   “/report <主題> custom <說明>\n”
   “/pdf market <內容>\n”
   “/pdf make <內容>\n”
   “自然語言也可直接說：\n”
   “請幫我做一份 XX 的 pdf”
   )
   else:
   msg = (
   “🦞 龍蝦指令清單\n”
   “─────────────────\n”
   “📊 ELN\n”
   “/calc  /list  /detail\n”
   “/eln upload  /eln run  /eln history  /eln result\n”
   “/runnow  /tracklog\n”
   “─────────────────\n”
   “📰 財經\n”
   “/daily  /daily cache  /market\n”
   “─────────────────\n”
   “📑 報告\n”
   “/report  /pdf\n”
   “─────────────────\n”
   “🔔 警示\n”
   “/alert add  /alert list  /alert del\n”
   “輸入 /help alert 看完整範例\n”
   “─────────────────\n”
   “📧 其他\n”
   “/mail  /invest  /forget\n”
   “上傳錄音 → 自動逐字稿 / 摘要\n”
   “上傳檔案 → 自動分析\n”
   “─────────────────\n”
   “進階說明：/help alert、/help eln、/help report”
   )
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
   return
   transcript_cache = db_get_transcript_cache(ck)
   if transcript_cache:
   if any(x in tl for x in [“不用”, “不用了”, “先不用”, “取消”, “不用做”]):
   db_clear_transcript_cache(ck)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“👌 好的，已保留逐字稿與摘要回覆，不另外生成 PDF。”))
   return
   if any(x in tl for x in [“做成pdf”, “生成pdf”, “轉成pdf”, “做成 pdf”, “生成 pdf”, “轉成 pdf”, “輸出pdf”, “輸出 pdf”]):
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“📄 正在根據逐字稿重點生成 PDF，請稍候…”))
   try:
   from pdf_generator import create_and_upload_pdf
   report_text = build_transcript_pdf_content(
   transcript_cache[“transcript”],
   transcript_cache[“summary”],
   chat_key=ck
   )
   link = create_and_upload_pdf(“analysis”, report_text, “會議重點報告”)
   db_clear_transcript_cache(ck)
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”✅ 會議重點 PDF 已生成完成！\n\n{link}”))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”❌ PDF 生成失敗：{str(e)[:250]}”))
   return
   if cmd in (“send”, “skip”):
   arg = parts[1].strip().lower() if len(parts) > 1 else “”
   if not arg:
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“請指定編號或 all\n範例：/send 1　/skip 2　/send all”
   ))
   return
   with engine.begin() as conn:
   rows = conn.execute(text(
   “SELECT id, target_id, agent_name, bond_id, status, msg “
   “FROM eln_pending_notifications WHERE chat_key=:k ORDER BY id”
   ), {“k”: ck}).fetchall()
   if not rows:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“目前沒有待確認的通知。”))
   return
   if arg == “all”:
   targets = list(rows)
   else:
   try:
   idx = int(arg) - 1
   if idx < 0 or idx >= len(rows):
   raise ValueError
   targets = [rows[idx]]
   except ValueError:
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=f”編號不正確，請輸入 1～{len(rows)} 或 all”
   ))
   return
   if cmd == “send”:
   sent, failed = 0, 0
   for row in targets:
   try:
   eln_group_bot_api.push_message(row.target_id, TextSendMessage(text=row.msg[:4900]))
   sent += 1
   except Exception as e:
   failed += 1
   print(f”[SEND ERROR] {row.target_id}: {e}”)
   with engine.begin() as conn:
   conn.execute(text(“DELETE FROM eln_pending_notifications WHERE id=:i”), {“i”: row.id})
   result_text = f”✅ 已發送 {sent} 筆”
   if failed:
   result_text += f”，失敗 {failed} 筆”
   else:
   for row in targets:
   with engine.begin() as conn:
   conn.execute(text(“DELETE FROM eln_pending_notifications WHERE id=:i”), {“i”: row.id})
   result_text = f”⏭️ 已略過 {len(targets)} 筆”
   with engine.begin() as conn:
   remaining = conn.execute(text(
   “SELECT COUNT(*) FROM eln_pending_notifications WHERE chat_key=:k”
   ), {“k”: ck}).scalar()
   if remaining > 0:
   result_text += f”\n\n還有 {remaining} 筆待處理，打 /send list 查看”
   else:
   result_text += “\n\n✅ 所有通知已處理完畢”
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=result_text))
   return
   if cmd == “send” and len(parts) > 1 and parts[1].strip().lower() == “list”:
   with engine.begin() as conn:
   rows = conn.execute(text(
   “SELECT id, agent_name, bond_id, status “
   “FROM eln_pending_notifications WHERE chat_key=:k ORDER BY id”
   ), {“k”: ck}).fetchall()
   if not rows:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“目前沒有待確認的通知。”))
   else:
   lines = [f”📋 待確認通知（{len(rows)}筆）\n”]
   for i, row in enumerate(rows, start=1):
   lines.append(f”{i}️⃣ {row.agent_name} | {row.bond_id} | {row.status}\n  /send {i}　/skip {i}”)
   lines.append(”\n/send all 全部發送　/skip all 全部略過”)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=”\n\n”.join(lines)))
   return
   if cmd == “invest”:
   db_invest_set(ck, “await_image”)
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“📰 請上傳新聞截圖\n\n收到圖片後，我會請你補上投資理由和標的。”
   ))
   return
   invest_mode, invest_image = db_invest_get(ck)
   if invest_mode == “await_reason” and invest_image:
   raw = text_raw.strip()
   reason = “”
   targets = “”
   for line in raw.replace(”，”, “,”).splitlines():
   l = line.strip()
   if l.startswith(“理由”):
   reason = l.split(”：”, 1)[-1].split(”:”, 1)[-1].strip()
   elif l.startswith(“標的”):
   targets = l.split(”：”, 1)[-1].split(”:”, 1)[-1].strip()
   if not reason and not targets:
   reason = raw
   db_invest_set(ck, “”)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“✍️ 整理中，請稍候…”))
   try:
   posts = generate_invest_post(invest_image, reason, targets)
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=posts[:4900]))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”生成失敗：{str(e)[:200]}”))
   return
   # ── DAILY REPORT ──────────────────────────────────────────
   if cmd.startswith(“daily”):
   parts = text_raw.split(” “, 1)
   use_cache = len(parts) > 1 and parts[1].strip().lower() == “cache”
   if use_cache:
   try:
   from sqlalchemy import create_engine, text as sa_text
   db_url = DATABASE_URL
   eng = create_engine(db_url, pool_pre_ping=True)
   with eng.begin() as conn:
   row = conn.execute(sa_text(”””
   SELECT report_text FROM daily_report_cache
   ORDER BY created_at DESC LIMIT 1
   “””)).fetchone()
   if row:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=row[0][:4900]))
   else:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“尚無快取日報，請用 /daily 產生最新版本。”))
   except Exception as e:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”讀取快取失敗: {e}”))
   return
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“產生中，請稍候約30秒…”))
   try:
   from daily_report import generate_report, save_report_to_db
   report, image_url, weekly_calendar = generate_report()
   save_report_to_db(report)
   if weekly_calendar:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=weekly_calendar[:4900]))
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=report[:4900]))
   if image_url:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”📊 今日市場摘要圖\n{image_url}”))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”日報產生失敗: {e}”))
   return
   # ──────────────────────────────────────────────────────────
   if cmd == “settarget”:
   targets = load_targets()
   if event.source.type == “group”:
   targets[“default”] = event.source.group_id
   targets[“default_type”] = “group”
   save_targets(targets)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“已設定此群組為預設推播對象”))
   elif event.source.type == “room”:
   targets[“default”] = event.source.room_id
   targets[“default_type”] = “room”
   save_targets(targets)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“已設定此聊天室為預設推播對象”))
   else:
   targets[“default”] = event.source.user_id
   targets[“default_type”] = “user”
   save_targets(targets)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“已設定您為預設推播對象”))
   return
   if cmd == “eln”:
   sub_parts = text_raw.split()
   sub = sub_parts[1].lower() if len(sub_parts) > 1 else “”
   if sub == “upload”:
   db_set_await(ck, True)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“📥 請直接上傳 ELN Excel 檔案，我會計算並同步保存到 Supabase。”))
   return
   if sub == “run”:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“🔄 正在重新計算最新 ELN，請稍候…”))
   try:
   try:
   from eln_storage import download_latest_eln
   latest_file = download_latest_eln(”/tmp/latest_eln.xlsx”)
   except Exception:
   latest_file = “/tmp/latest_eln.xlsx”
   summary, top5_lines, detail_map, agent_name_map = run_autotracking(latest_file)
   db_save_result(ck, summary, top5_lines, detail_map, agent_name_map)
   msg = “✅ ELN 已重新計算完成\n\n” + (”\n”.join(top5_lines[:5]) if top5_lines else (summary or “沒有可顯示摘要”))
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=msg[:4900]))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”❌ ELN 計算失敗：{str(e)[:250]}”))
   return
   if sub == “history”:
   try:
   from eln_storage import list_history
   items = list_history()
   msg = “📁 ELN Excel 歷史版本\n\n” + (”\n”.join(items[:20]) if items else “目前沒有歷史 Excel”)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=msg[:4900]))
   except Exception as e:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”讀取歷史失敗：{str(e)[:250]}”))
   return
   if sub == “result”:
   summary = db_get_report(ck)
   if not summary:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“目前沒有 ELN 結果，請先 /calc 或 /eln run。”))
   return
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=summary[:4900]))
   return
   if cmd == “list”:
   from collections import defaultdict
   list_parts = text_raw.split(” “, 1)
   name_filter = list_parts[1].strip() if len(list_parts) > 1 else “”
   bonds = db_list_bonds(ck, limit=200)
   if not bonds:
   *bot_api.reply_message(event.reply_token, TextSendMessage(text=“目前尚無已保存結果。請先 /calc 上傳 Excel。”))
   return
   # 建立 bond_id -> (detail, status_tag) 對照
   detail_map = {}
   for bond_id, agent_raw, detail in bonds:
   detail_map[bond_id] = bond_status_tag(detail)
   if name_filter:
   # 按姓名查詢模式
   matched = []
   seen = set()
   for bond_id, agent_raw, detail in bonds:
   agents = [a.strip() for a in re.split(r”[,，、/]”, agent_raw) if a.strip()]
   if any(name_filter in a for a in agents) and bond_id not in seen:
   matched.append((bond_id, detail_map.get(bond_id, “”)))
   seen.add(bond_id)
   if not matched:
   *bot_api.reply_message(event.reply_token, TextSendMessage(text=f”找不到「{name_filter}」的持倉。”))
   return
   lines = [f”👤 {name_filter} 的持倉（共 {len(matched)} 筆）：\n”]
   for b, tag in matched:
   lines.append(f”   • {b}{tag}”)
   full_text = “\n”.join(lines)
   else:
   # 全部模式，按理專分組
   grouped = defaultdict(list)
   for bond_id, agent_raw, detail in bonds:
   agents = [a.strip() for a in re.split(r”[,，、/]”, agent_raw) if a.strip()]
   if not agents:
   agents = [“未指定”]
   for agent in agents:
   if bond_id not in [b for b, _ in grouped[agent]]:
   grouped[agent].append((bond_id, detail_map.get(bond_id, “”)))
   lines = [f”📋 全部商品（共 {len(set(b for b,*,* in bonds))} 筆，按理專排列）：\n”]
   for agent, bond_list in sorted(grouped.items()):
   lines.append(f”👤 {agent}（{len(bond_list)} 筆）”)
   for b, tag in bond_list:
   lines.append(f”   • {b}{tag}”)
   full_text = “\n”.join(lines)
   chunks = []
   current = “”
   for line in full_text.split(”\n”):
   if len(current) + len(line) + 1 > 4800:
   chunks.append(current)
   current = line
   else:
   current = current + “\n” + line if current else line
   if current:
   chunks.append(current)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=chunks[0]))
   for chunk in chunks[1:]:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=chunk))
   return
   if cmd.startswith(“calc”) or cmd.startswith(“clac”):
   parts = raw_cmd.split(” “, 1)
   if len(parts) > 1 and parts[1].strip():
   expr = parts[1].strip()
   if not re.fullmatch(r”[0-9.+-*/()\s]+”, expr):
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“算式格式錯誤”))
   return
   try:
   result = eval(expr, {”**builtins**”: {}})
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”{expr} = {result}”))
   return
   except Exception:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“算式錯誤”))
   return
   db_set_await(ck, True)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“收到！請直接把 Excel 檔案傳給我（用 LINE 的『檔案』上傳），我會計算並保存結果。”))
   return
   if cmd == “report” and len(raw_cmd.strip().split()) == 1:
   summary = db_get_report(ck)
   if not summary:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“目前尚無已保存結果，請先 /calc 上傳 Excel。”))
   return
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=summary[:4900]))
   return
   if cmd.startswith(“market”):
   parts = text_raw.split(” “, 1)
   if len(parts) < 2 or not parts[1].strip():
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“請輸入新聞內容和推薦標的\n\n格式範例:\n/market 美股反彈，高盛喊買。\n\n推薦標的: PIMCO收益增長、駿利平衡基金”
   ))
   return
   news_text = parts[1].strip()
   content = generate_market_content(news_text)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=content[:4900]))
   return
   if (not tl.startswith(”/pdf”)) and any(k in tl for k in PDF_NL_KEYWORDS):
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“📄 正在整理內容並生成研究報告 PDF，請稍候…”))
   try:
   from pdf_generator import create_and_upload_pdf
   report_text = build_pdf_report_content(text_raw, chat_key=ck)
   link = create_and_upload_pdf(“analysis”, report_text, “AI自動生成研究報告”)
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”✅ 研究報告 PDF 已生成完成！\n\n{link}”))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”❌ PDF 生成失敗：{str(e)[:250]}”))
   return
   if cmd.startswith(“pdf”):
   from pdf_generator import create_and_upload_pdf
   parts = text_raw.split(” “, 2)
   sub = parts[1].strip().lower() if len(parts) > 1 else “”
   if sub == “market”:
   if len(parts) < 3 or not parts[2].strip():
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“請輸入內容\n範例：/pdf market 美股反彈，推薦PIMCO”))
   return
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“產生市場觀點 PDF 中，請稍候…”))
   try:
   content = generate_market_content(parts[2].strip())
   link = create_and_upload_pdf(“market”, content)
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”📄 市場觀點 PDF 已產生！\n\n{link}”))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”PDF 產生失敗: {e}”))
   return
   if sub == “make”:
   content_text = parts[2].strip() if len(parts) > 2 else “”
   if not content_text:
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“請在指令後面直接輸入內容\n\n範例：\n/pdf make 美伊戰爭後的重建行情研究報告”
   ))
   return
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“整理內容並產生研究報告 PDF 中，請稍候…”))
   try:
   report_text = build_pdf_report_content(content_text, chat_key=ck)
   link = create_and_upload_pdf(“analysis”, report_text, “自訂研究報告”)
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”📄 研究報告 PDF 已產生！\n\n{link}”))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”PDF 產生失敗: {e}”))
   return
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“PDF 指令用法：\n/pdf market <內容> → 市場觀點 PDF\n/pdf make <內容> → 研究報告 PDF”
   ))
   return
   if cmd.startswith(“report”):
   parts = text_raw.split(” “)
   style_codes = {“ib”, “brief”, “client”, “academic”, “hybrid”, “custom”}
   style_names = {“ib”:“投資銀行”, “brief”:“簡報摘要”, “client”:“客戶推播”, “academic”:“學術研究”, “hybrid”:“混合風格”, “custom”:“自訂風格”}
   style = “ib”
   custom_prompt = “”
   if “custom” in [p.lower() for p in parts[2:]]:
   custom_idx = next(i for i,p in enumerate(parts) if p.lower() == “custom”)
   topic = “ “.join(parts[1:custom_idx]).strip()
   custom_prompt = “ “.join(parts[custom_idx+1:]).strip()
   style = “custom”
   if not custom_prompt:
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“自訂風格請在 custom 後面加上說明！\n\n範例：\n/report 台積電展望 custom 請用輕鬆幽默的風格，適合分享給非專業投資人”
   ))
   return
   elif len(parts) > 2 and parts[-1].lower() in style_codes:
   style = parts[-1].lower()
   topic = “ “.join(parts[1:-1]).strip()
   else:
   topic = “ “.join(parts[1:]).strip()
   if not topic:
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“請輸入報告主題\n\n風格選擇（加在主題後面）：\n預設 → 投資銀行\nbrief → 簡報摘要\nclient → 客戶推播\nacademic → 學術研究\nhybrid → 投銀+研究混合\ncustom <說明> → 自訂風格\n\n範例：\n/report 聯準會降息對債市影響\n/report 聯準會降息對債市影響 client\n/report 台積電展望 custom 請用輕鬆幽默的風格，適合分享給非專業投資人”
   ))
   return
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=f”📊 正在研究「{topic}」\n風格：{style_names.get(style,‘投資銀行’)}\n\n搜尋資料 → 整理分析 → 生成PDF\n請稍候約60至90秒…”
   ))
   try:
   from report_generator import generate_research_report
   link = generate_research_report(topic, ck.split(”:”, 1)[1], style=style, custom_prompt=custom_prompt)
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(
   text=f”📑 研究報告已完成！\n\n主題：{topic}\n風格：{style_names.get(style,‘投資銀行’)}\n\n{link}”
   ))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”報告生成失敗：{e}”))
   return
   if cmd.startswith(“alert”):
   parts = text_raw.split(” “)
   sub = parts[1].strip().lower() if len(parts) > 1 else “”
   if sub == “list”:
   with engine.begin() as conn:
   rows = conn.execute(text(”””
   SELECT id, symbol, alert_type, condition, target_value, ma_period
   FROM price_alerts
   WHERE chat_key=:k AND deleted=FALSE
   ORDER BY id ASC
   “””), {“k”: ck}).fetchall()
   if not rows:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“目前沒有任何警示設定。\n\n新增範例：\n/alert add AAPL 200 above\n/alert add AAPL ma20 below”))
   return
   msg = “目前警示清單：\n”
   for r in rows:
   rid, sym, atype, cond, tval, maper = r
   cond_str = “漲到” if cond == “above” else “跌到”
   cross_str = “漲破” if cond == “above” else “跌破”
   if atype == “price”:
   msg += f”#{rid} {sym} {cond_str} {tval}\n”
   else:
   msg += f”#{rid} {sym} {cross_str} MA{maper}\n”
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=msg.strip()))
   return
   if sub == “del”:
   if len(parts) < 3:
   with engine.begin() as conn:
   rows = conn.execute(text(”””
   SELECT id, symbol, alert_type, condition, target_value, ma_period, trigger_count
   FROM price_alerts
   WHERE chat_key=:k AND deleted=FALSE
   ORDER BY id ASC
   “””), {“k”: ck}).fetchall()
   if not rows:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“目前沒有任何警示設定。”))
   return
   msg = “請輸入要刪除的編號：\n\n”
   for r in rows:
   rid, sym, atype, cond, tval, maper, tcount = r
   cond_str = “漲到” if cond == “above” else “跌到”
   cross_str = “漲破” if cond == “above” else “跌破”
   remain = 2 - (tcount or 0)
   if atype == “price”:
   msg += f”#{rid} {sym} {cond_str} {tval}（剩餘{remain}次）\n”
   else:
   msg += f”#{rid} {sym} {cross_str} MA{maper}（剩餘{remain}次）\n”
   msg += “\n輸入：/alert del <編號>”
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=msg.strip()))
   return
   try:
   del_id = int(parts[2])
   with engine.begin() as conn:
   conn.execute(text(“UPDATE price_alerts SET deleted=TRUE WHERE id=:i AND chat_key=:k”), {“i”: del_id, “k”: ck})
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”✅ 警示 #{del_id} 已刪除”))
   except Exception as e:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”刪除失敗：{e}”))
   return
   if sub == “add”:
   if len(parts) < 5:
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“格式說明：\n\n價格警示：\n/alert add NVDA 190 above\n/alert add NVDA above 190\n/alert add dxy below 100\n/alert add ust10y above 45\n\n價格 vs 均線：\n/alert add NVDA ma20 above\n/alert add 2330.TW ma60 below\n\n均線交叉：\n/alert add NVDA ma5 cross ma20\n/alert add NVDA ma5 under ma20”
   ))
   return
   raw_symbol = parts[2]
   symbol = ALERT_TICKER_ALIAS.get(raw_symbol.lower(), raw_symbol).upper()
   p3 = parts[3].lower()
   p4 = parts[4].lower()
   p5 = parts[5].lower() if len(parts) > 5 else “”
   try:
   if p3.startswith(“ma”) and p4 in (“cross”, “under”) and p5.startswith(“ma”):
   ma_short = int(p3[2:])
   ma_long = int(p5[2:])
   with engine.begin() as conn:
   conn.execute(text(”””
   INSERT INTO price_alerts(chat_key, symbol, alert_type, condition, ma_short, ma_long)
   VALUES (:k, :s, ‘ma_cross’, :c, :ms, :ml)
   “””), {“k”: ck, “s”: symbol, “c”: p4, “ms”: ma_short, “ml”: ma_long})
   label = “黃金交叉” if p4 == “cross” else “死亡交叉”
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=f”✅ 均線交叉警示已設定！\n標的：{symbol}\n條件：MA{ma_short} {label} MA{ma_long}\n\n當條件成立時龍蝦會通知你 🔔”
   ))
   return
   if p3.startswith(“ma”) and p4 in (“above”, “below”):
   ma_period = int(p3[2:])
   with engine.begin() as conn:
   conn.execute(text(”””
   INSERT INTO price_alerts(chat_key, symbol, alert_type, condition, ma_period)
   VALUES (:k, :s, ‘ma’, :c, :m)
   “””), {“k”: ck, “s”: symbol, “c”: p4, “m”: ma_period})
   cross = “漲破” if p4 == “above” else “跌破”
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=f”✅ 均線警示已設定！\n標的：{symbol}\n條件：{cross} MA{ma_period}\n\n當條件成立時龍蝦會通知你 🔔”
   ))
   return
   if p3 in (“above”, “below”):
   direction = p3
   value_str = p4
   elif p4 in (“above”, “below”):
   value_str = p3
   direction = p4
   else:
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“方向請輸入 above 或 below，或使用 ma / 均線交叉格式\n\n例：\n/alert add NVDA above 190\n/alert add NVDA ma20 above\n/alert add NVDA ma5 cross ma20”
   ))
   return
   target = float(value_str)
   with engine.begin() as conn:
   conn.execute(text(”””
   INSERT INTO price_alerts(chat_key, symbol, alert_type, condition, target_value)
   VALUES (:k, :s, ‘price’, :c, :t)
   “””), {“k”: ck, “s”: symbol, “c”: direction, “t”: target})
   cond_str = “漲到” if direction == “above” else “跌到”
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=f”✅ 價格警示已設定！\n標的：{symbol}\n條件：{cond_str} {target}\n\n當條件成立時龍蝦會通知你 🔔”
   ))
   except Exception as e:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”設定失敗：{e}”))
   return
   _bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“價格警示指令：\n/alert add <標的> <價格> <above/below>\n/alert add <標的> <above/below> <價格>\n/alert add <標的> ma20 <above/below>\n/alert add <標的> ma5 cross ma20\n/alert add <標的> ma5 under ma20\n/alert list → 查看清單\n/alert del <編號> → 刪除\n\n別名：dxy / spx / ndx / sox / vix / ust10y / gold / silver / oil\n\n範例：\n/alert add dxy below 100\n/alert add ust10y above 45\n/alert add NVDA ma20 above\n/alert add NVDA ma5 cross ma20”
   ))
   return
   if cmd == “news pdf” or cmd == “news”:
   from news_fetcher import generate_news_report
   from pdf_generator import create_and_upload_pdf
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“正在抓取最新財經新聞並整理中，請稍候約30秒…”))
   try:
   report = generate_news_report()
   link = create_and_upload_pdf(“news”, report)
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”📰 今日財經新聞摘要 PDF 已產生！\n\n{link}”))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”新聞抓取失敗: {e}”))
   return
   if cmd.startswith(“detail”):
   parts = text_raw.split(” “, 1)
   if len(parts) < 2 or not parts[1].strip():
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“請輸入：/detail 商品代號（例：/detail U123）”))
   return
   query = parts[1].strip()
   matched_id, detail, candidates = db_find_detail(ck, query)
   if detail:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=detail[:4900]))
   return
   if candidates and matched_id is None:
   sample = “\n”.join([f”• {c}” for c in candidates[:20]])
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”請再精準一點，候選代號如下：\n{sample}”[:4900]))
   return
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“查不到該代號或目前沒有已保存結果。請先 /calc 上傳 Excel。”))
   return
   if cmd.startswith(“mail”):
   parts = text_raw.split(” “, 1)
   sub = parts[1].strip().lower() if len(parts) > 1 else “”
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“📧 正在讀取郵件並分析中，請稍候…”))
   try:
   from gmail_manager import daily_email_summary, get_gmail_service, get_unread_emails, analyze_emails, format_line_message
   if sub == “unread”:
   service = get_gmail_service()
   emails = get_unread_emails(service, max_results=10)
   if not emails:
   _bot_api.push_message(ck.split(”:”,1)[1], TextSendMessage(text=“📧 目前沒有未讀郵件 ✅”))
   else:
   analysis = analyze_emails(emails)
   msg = format_line_message(analysis, emails)
   _bot_api.push_message(ck.split(”:”,1)[1], TextSendMessage(text=msg[:4900]))
   else:
   summary = daily_email_summary()
   _bot_api.push_message(ck.split(”:”,1)[1], TextSendMessage(text=summary[:4900]))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”,1)[1], TextSendMessage(text=f”郵件讀取失敗：{e}”))
   return
   if cmd.startswith(“analysis”):
   parts = text_raw.split(” “, 1)
   arg = parts[1].strip() if len(parts) > 1 else “”
   if not arg:
   *bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“📊 完整三面向分析指令：\n\n/analysis NVDA — 技術+基本面+消息面\n/analysis 2330 — 台積電完整分析\n/analysis AAPL 3 — 指定月數（預設6個月）\n\n包含：K線/RSI/成交量/EPS趨勢/營收成長/最新新聞情緒分析”
   ))
   return
   arg_parts = arg.split()
   symbol = arg_parts[0]
   months = 6
   if len(arg_parts) > 1 and arg_parts[1].isdigit():
   months = min(max(int(arg_parts[1]), 1), 12)
   *bot_api.reply_message(event.reply_token, TextSendMessage(text=f”📊 正在進行 {symbol.upper()} 完整三面向分析，請稍候約20秒…”))
   try:
   from stock_analyzer import full_analysis
   from pdf_generator import upload_to_drive
   img_bytes, summary = full_analysis(symbol, months=months)
   tmp_path = f”/tmp/analysis*{symbol}*{months}.png”
   with open(tmp_path, “wb”) as f:
   f.write(img_bytes)
   link = upload_to_drive(tmp_path, f”{symbol.upper()} Full Analysis {months}M.png”)
   os.remove(tmp_path)
   msg = f”📊 {symbol.upper()} 完整分析 (近{months}個月)\n\n{summary}\n\n🔗 圖表：{link}”
   _bot_api.push_message(ck.split(”:”,1)[1], TextSendMessage(text=msg[:4900]))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”,1)[1], TextSendMessage(text=f”分析失敗：{str(e)[:200]}”))
   return
   if cmd.startswith(“tech”):
   parts = text_raw.split(” “, 1)
   arg = parts[1].strip() if len(parts) > 1 else “”
   if not arg:
   *bot_api.reply_message(event.reply_token, TextSendMessage(
   text=“📊 技術分析指令：\n\n/tech mag7 — Magnificent Seven 比較分析\n/tech AAPL — 單一股票分析 (美股)\n/tech 2330 — 單一股票分析 (台股)\n/tech AAPL 3 — 指定月數（預設6個月）\n\n範例：\n/tech mag7\n/tech NVDA\n/tech 2330 3”
   ))
   return
   arg_parts = arg.split()
   symbol = arg_parts[0]
   months = 6
   if len(arg_parts) > 1 and arg_parts[1].isdigit():
   months = min(max(int(arg_parts[1]), 1), 12)
   *bot_api.reply_message(event.reply_token, TextSendMessage(text=f”📊 正在分析 {symbol.upper()}，請稍候約15秒…”))
   try:
   from tech_analyzer import analyze_single, analyze_mag7
   from pdf_generator import upload_to_drive
   if symbol.lower() == “mag7”:
   img_bytes, summary = analyze_mag7(months=months)
   title = f”Magnificent Seven 技術分析 (近{months}個月)”
   else:
   img_bytes, summary = analyze_single(symbol, months=months)
   title = f”{symbol.upper()} 技術分析 (近{months}個月)”
   tmp_path = f”/tmp/tech*{symbol}*{months}.png”
   with open(tmp_path, “wb”) as f:
   f.write(img_bytes)
   link = upload_to_drive(tmp_path, f”{title}.png”)
   os.remove(tmp_path)
   msg = f”📊 {title}\n\n{summary}\n\n🔗 圖表連結：{link}”
   _bot_api.push_message(ck.split(”:”,1)[1], TextSendMessage(text=msg[:4900]))
   except Exception as e:
   _bot_api.push_message(ck.split(”:”,1)[1], TextSendMessage(text=f”技術分析失敗：{str(e)[:200]}”))
   return
   if cmd == “runnow”:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“🔄 手動觸發 ELN 追蹤中，請稍候約30秒…”))
   try:
   from auto_tracking_cron import main as tracking_main
   tracking_main()
   write_job_log(“ELN追蹤(手動)”, “success”, “手動觸發完成”)
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=“✅ ELN 追蹤執行完成！\n\n打 /tracklog 查看記錄”))
   except Exception as e:
   write_job_log(“ELN追蹤(手動)”, “error”, str(e))
   _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”❌ 執行失敗：{str(e)[:300]}”))
   return
   if cmd == “tracklog”:
   with engine.begin() as conn:
   rows = conn.execute(text(”””
   SELECT job_name, status, message, executed_at
   FROM eln_job_log
   ORDER BY executed_at DESC
   LIMIT 20
   “””)).fetchall()
   if not rows:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“目前沒有執行記錄。”))
   return
   lines = [“📋 最近排程記錄（最新20筆）：\n”]
   status_icon = {“success”: “✅”, “error”: “❌”, “started”: “🔄”, “skipped”: “⏭️”}
   for r in rows:
   icon = status_icon.get(r[1], “•”)
   tw_time = r[3].astimezone(TZ_TAIPEI_PYTZ).strftime(”%m/%d %H:%M”)
   msg = f”  {r[2]}” if r[2] else “”
   lines.append(f”{icon} {tw_time} {r[0]}{msg}”)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=”\n”.join(lines)[:4900]))
   return
   if cmd in (“claude”, “gpt”, “gemini”):
   forced_prompt = text_raw.split(” “, 1)[1].strip() if “ “ in text_raw else “”
   if not forced_prompt:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”請在 /{cmd} 後面加上問題\n\n例如：/{cmd} 今天美股怎麼看？”))
   return
   model_map = {“claude”: “Claude”, “gpt”: “ChatGPT”, “gemini”: “Gemini”}
   reply = ai_router(forced_prompt, chat_key=ck, forced_model=cmd)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”🦞 龍蝦（{model_map[cmd]}）\n\n{reply[:4700]}”))
   return
   if cmd == “forget”:
   try:
   with engine.begin() as conn:
   conn.execute(text(“DELETE FROM chat_history WHERE chat_key = :k”), {“k”: ck})
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“🧹 記憶已清除！龍蝦從頭開始囉。”))
   except Exception as e:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”清除失敗：{e}”))
   return
   reply = ai_router(text_raw, chat_key=ck)
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”🦞 龍蝦\n\n{reply[:4700]}”))
   except Exception as e:
   print(”[ERROR] handle_text_message:”, e)
   print(_traceback.format_exc())
   try:
   _bot_api.reply_message(event.reply_token, TextSendMessage(text=“我收到訊息但處理時出錯了。你可以先輸入 /help。”))
   except Exception:
   pass

# ==============================

# File message handler

# ==============================

UPLOAD_DIR = Path(”/tmp/uploads”)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
def extract_text_from_file(file_path: str, filename: str) -> str:
ext = Path(filename).suffix.lower()
text = “”
try:
if ext == “.pdf”:
import pdfplumber
with pdfplumber.open(file_path) as pdf:
for page in pdf.pages:
page_text = page.extract_text()
if page_text:
text += page_text + “\n”
elif ext == “.docx”:
from docx import Document
doc = Document(file_path)
for para in doc.paragraphs:
if para.text.strip():
text += para.text + “\n”
elif ext == “.pptx”:
from pptx import Presentation
prs = Presentation(file_path)
for i, slide in enumerate(prs.slides, start=1):
text += f”\n— 第 {i} 頁 —\n”
for shape in slide.shapes:
if hasattr(shape, “text”) and shape.text.strip():
text += shape.text + “\n”
elif ext in (”.xlsx”, “.xls”):
import pandas as pd
xl = pd.ExcelFile(file_path)
for sheet in xl.sheet_names:
df = xl.parse(sheet)
text += f”\n— 工作表: {sheet} —\n”
text += df.to_string(index=False) + “\n”
except Exception as e:
print(f”Extract error: {e}”)
text = “”
return text.strip()
def analyze_file_with_claude(text: str, filename: str) -> str:
ext = Path(filename).suffix.lower()
type_map = {”.pdf”: “PDF文件”, “.docx”: “Word文件”, “.pptx”: “PowerPoint簡報”, “.xlsx”: “Excel試算表”, “.xls”: “Excel試算表”}
file_type = type_map.get(ext, “文件”)
prompt = (
f”我收到一份{file_type}，內容如下:\n\n”
f”{text[:6000]}\n\n”
“請幫我:\n”
“1. 用2-3句話說明這份文件的主題與目的\n”
“2. 條列出5-8個最重要的重點\n”
“3. 如果有數據或結論，特別標示出來\n”
“4. 最後一句話說明這份文件的主要價值或建議行動\n\n”
“格式規定: 不使用 Markdown 符號（禁止 ## ** — 等），標題用 emoji，條列用 •”
)
resp = claude_client.messages.create(
model=“claude-sonnet-4-20250514”,
max_tokens=1500,
messages=[{“role”: “user”, “content”: prompt}]
)
return (resp.content[0].text or “”).strip()
def analyze_image_with_claude(image_data: bytes, media_type: str) -> str:
import base64
image_b64 = base64.b64encode(image_data).decode(“utf-8”)
resp = claude_client.messages.create(
model=“claude-sonnet-4-20250514”,
max_tokens=1500,
messages=[{
“role”: “user”,
“content”: [
{“type”: “image”, “source”: {“type”: “base64”, “media_type”: media_type, “data”: image_b64}},
{“type”: “text”, “text”: “請分析這張圖片，幫我:\n1. 說明圖片的主要內容\n2. 如果有文字或數據，擷取重要資訊\n3. 條列出重點\n格式規定: 不使用 Markdown 符號，標題用 emoji，條列用 •”}
]
}]
)
return (resp.content[0].text or “”).strip()
def generate_invest_post(image_data: bytes, reason: str, targets: str) -> str:
import base64
image_b64 = base64.b64encode(image_data).decode(“utf-8”)
user_input = “”
if reason:
user_input += f”投資理由：{reason}\n”
if targets:
user_input += f”建議標的：{targets}\n”
prompt = f””“你是一位台灣私人銀行的投資輔銷人員，正在為高資產客戶撰寫 LINE 群組推播文。
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
  （內容）”””
  resp = claude_client.messages.create(
  model=“claude-sonnet-4-20250514”,
  max_tokens=1500,
  messages=[{
  “role”: “user”,
  “content”: [
  {“type”: “image”, “source”: {“type”: “base64”, “media_type”: “image/jpeg”, “data”: image_b64}},
  {“type”: “text”, “text”: prompt}
  ]
  }]
  )
  return (resp.content[0].text or “”).strip()
  @handler.add(MessageEvent, message=FileMessage)
  def handle_file_message(event):
  _bot_api = getattr(_current_bot_api, “api”, None) or line_bot_api
  try:
  ck = chat_key_of(event)
  filename = getattr(event.message, “file_name”, “”) or “”
  ext = Path(filename).suffix.lower()
  print(”[FILE]”, ck, filename)
  message_id = event.message.id
  content = *bot_api.get_message_content(message_id)
  tmp_path = UPLOAD_DIR / f”upload*{int(datetime.now(TZ_TAIPEI).timestamp())}{ext}”
  with open(tmp_path, “wb”) as f:
  for chunk in content.iter_content():
  f.write(chunk)
  if ext in (”.mp3”, “.m4a”, “.wav”, “.ogg”, “.mp4”, “.webm”):
  _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”🎙️ 收到音檔 {filename}，正在轉逐字稿…”))
  with open(tmp_path, “rb”) as f:
  audio_data = f.read()
  text_result = transcribe_audio(audio_data, filename=filename)
  if not text_result:
  _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=“❌ 無法辨識語音內容，請確認音檔有聲音。”))
  return
  summary = build_transcript_summary(text_result, chat_key=ck)
  db_set_transcript_cache(ck, text_result, summary)
  db_save_meeting_transcript(ck, filename, text_result, summary)
  preview = text_result[:2000]
  push_long_message(_bot_api, ck.split(”:”, 1)[1], f”📝 逐字稿（前段）：\n\n{preview}”)
  push_long_message(_bot_api, ck.split(”:”, 1)[1], f”📌 會議摘要：\n\n{summary}”)
  _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=“要不要把這份會議重點做成 PDF？\n\n可直接回：做成 PDF / 不用”))
  return
  if ext in (”.xlsx”, “.xls”) and db_is_await(ck):
  db_set_await(ck, False)
  summary, top5_lines, detail_map, agent_name_map = run_autotracking(str(tmp_path))
  db_save_result(ck, summary, top5_lines, detail_map, agent_name_map)
  try:
  from eln_storage import upload_eln_excel
  storage_info = upload_eln_excel(str(tmp_path))
  print(”[ELN Storage] uploaded:”, storage_info)
  except Exception as e:
  print(”[ELN Storage] upload failed:”, e)
  _bot_api.reply_message(event.reply_token, TextSendMessage(text=(summary or “已收到檔案，但沒有產出內容”)[:4900]))
  return
  if ext in (”.xlsx”, “.xls”, “.pdf”, “.docx”, “.pptx”):
  _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”收到！正在分析 {filename}，請稍候…”))
  text = extract_text_from_file(str(tmp_path), filename)
  if not text:
  _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=“檔案解析失敗，可能是掃描版 PDF 或格式不支援。”))
  return
  analysis = analyze_file_with_claude(text, filename)
  _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=analysis[:4900]))
  try:
  from pdf_generator import create_and_upload_pdf
  link = create_and_upload_pdf(“analysis”, analysis, filename)
  _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”📄 分析報告 PDF：\n{link}”))
  except Exception as e:
  print(f”PDF upload error: {e}”)
  return
  _bot_api.reply_message(event.reply_token, TextSendMessage(text=f”目前支援的檔案格式: PDF、Word、PowerPoint、Excel\n收到的格式 {ext} 暫不支援。”))
  except Exception as e:
  print(”[ERROR] handle_file_message:”, e)
  print(_traceback.format_exc())
  try:
  db_set_await(chat_key_of(event), False)
  except Exception:
  pass
  try:
  _bot_api.reply_message(event.reply_token, TextSendMessage(text=“檔案處理時出錯了，請稍後再試。”))
  except Exception:
  pass
  @handler.add(MessageEvent, message=ImageMessage)
  def handle_image_message(event):
  _bot_api = getattr(_current_bot_api, “api”, None) or line_bot_api
  try:
  ck = chat_key_of(event)
  print(”[IMAGE]”, ck)
  message_id = event.message.id
  content = _bot_api.get_message_content(message_id)
  image_data = b””
  for chunk in content.iter_content():
  image_data += chunk
  invest_mode, _ = db_invest_get(ck)
  if invest_mode == “await_image”:
  db_invest_set(ck, “await_reason”, image=image_data)
  _bot_api.reply_message(event.reply_token, TextSendMessage(
  text=“✅ 收到截圖！\n\n請輸入你的投資理由和標的：\n\n理由：（你認為能投資的原因）\n標的：（股票/ETF代號）”
  ))
  return
  _bot_api.reply_message(event.reply_token, TextSendMessage(text=“收到圖片！正在分析，請稍候…”))
  analysis = analyze_image_with_claude(image_data, “image/jpeg”)
  _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=analysis[:4900]))
  except Exception as e:
  print(”[ERROR] handle_image_message:”, e)
  print(_traceback.format_exc())
  try:
  _bot_api.reply_message(event.reply_token, TextSendMessage(text=“圖片處理時出錯了，請稍後再試。”))
  except Exception:
  pass
  def transcribe_audio(audio_data: bytes, filename: str = “audio.m4a”) -> str:
  import tempfile
  if not openai_client:
  raise RuntimeError(“缺少 OPENAI_API_KEY，無法使用語音轉文字”)
  MAX_BYTES = 24 * 1024 * 1024
  with tempfile.TemporaryDirectory() as tmp:
  ext = os.path.splitext(filename)[1].lower() or “.m4a”
  src_path = os.path.join(tmp, f”audio_input{ext}”)
  with open(src_path, “wb”) as f:
  f.write(audio_data)
  if len(audio_data) > MAX_BYTES:
  try:
  from pydub import AudioSegment
  out_path = os.path.join(tmp, “audio_compressed.mp3”)
  audio = AudioSegment.from_file(src_path)
  audio = audio.set_channels(1).set_frame_rate(16000)
  audio.export(out_path, format=“mp3”, bitrate=“32k”)
  send_path = out_path
  except Exception as e:
  print(f”[Audio] 壓縮失敗，嘗試直接送原檔: {e}”)
  send_path = src_path
  else:
  send_path = src_path
  with open(send_path, “rb”) as f:
  resp = openai_client.audio.transcriptions.create(
  model=“whisper-1”,
  file=(os.path.basename(send_path), f),
  language=“zh”
  )
  return resp.text.strip()
  @handler.add(MessageEvent, message=AudioMessage)
  def handle_audio_message(event, _override_bot_api=None):
  _bot_api = _override_bot_api or line_bot_api
  ck = chat_key_of(event)
  print(f”[AUDIO] {ck}”)
  try:
  _bot_api.reply_message(event.reply_token, TextSendMessage(text=“🎙️ 收到語音，轉文字中，請稍候…”))
  message_id = event.message.id
  content = _bot_api.get_message_content(message_id)
  audio_data = b””
  for chunk in content.iter_content():
  audio_data += chunk
  text_result = transcribe_audio(audio_data)
  if not text_result:
  _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=“❌ 無法辨識語音內容，請確認音檔有聲音。”))
  return
  push_long_message(_bot_api, ck.split(”:”, 1)[1], f”📝 語音轉文字：\n\n{text_result}”)
  reply = ai_router(text_result, chat_key=ck)
  push_long_message(_bot_api, ck.split(”:”, 1)[1], reply)
  except Exception as e:
  print(f”[ERROR] handle_audio_message: {e}”)
  try:
  _bot_api.push_message(ck.split(”:”, 1)[1], TextSendMessage(text=f”❌ 語音處理失敗：{str(e)[:200]}”))
  except Exception:
  pass

# ==============================

# ELN Auto-Tracking 群組專用 handler

# 只處理 /list 和 /detail，資料來自龍蝦的 personal chat_key

# ==============================

ELN_PERSONAL_CHAT_KEY = f”user:{os.getenv(‘LINE_USER_ID’, ‘’)}”

@eln_group_handler.add(MessageEvent, message=TextMessage)
def handle_eln_group_message(event):
try:
text_raw = (event.message.text or “”).strip()
tl = text_raw.lower().strip()
print(f”[ELN-GROUP] {repr(text_raw)}”)

```
    # 只處理 /list 和 /detail，其他靜音
    if not (tl.startswith("/list") or tl.startswith("/detail")):
        return

    ck = ELN_PERSONAL_CHAT_KEY  # 固定用龍蝦的資料

    if tl.startswith("/list"):
        from collections import defaultdict
        list_parts = text_raw.split(" ", 1)
        name_filter = list_parts[1].strip() if len(list_parts) > 1 else ""
        bonds = db_list_bonds(ck, limit=200)
        if not bonds:
            eln_group_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前尚無資料。"))
            return
        detail_map_status = {}
        for bond_id, agent_raw, detail in bonds:
            detail_map_status[bond_id] = bond_status_tag(detail)
        if name_filter:
            matched = []
            seen = set()
            for bond_id, agent_raw, detail in bonds:
                agents = [a.strip() for a in re.split(r"[,，、/]", agent_raw) if a.strip()]
                if any(name_filter in a for a in agents) and bond_id not in seen:
                    matched.append((bond_id, detail_map_status.get(bond_id, "")))
                    seen.add(bond_id)
            if not matched:
                eln_group_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"找不到「{name_filter}」的持倉。"))
                return
            lines = [f"👤 {name_filter} 的持倉（共 {len(matched)} 筆）：\n"]
            for b, tag in matched:
                lines.append(f"   • {b}{tag}")
            full_text = "\n".join(lines)
        else:
            grouped = defaultdict(list)
            for bond_id, agent_raw, detail in bonds:
                agents = [a.strip() for a in re.split(r"[,，、/]", agent_raw) if a.strip()]
                if not agents:
                    agents = ["未指定"]
                for agent in agents:
                    if bond_id not in [b for b, _ in grouped[agent]]:
                        grouped[agent].append((bond_id, detail_map_status.get(bond_id, "")))
            lines = [f"📋 全部商品（共 {len(set(b for b,_,_ in bonds))} 筆，按理專排列）：\n"]
            for agent, bond_list in sorted(grouped.items()):
                lines.append(f"👤 {agent}（{len(bond_list)} 筆）")
                for b, tag in bond_list:
                    lines.append(f"   • {b}{tag}")
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
        eln_group_bot_api.reply_message(event.reply_token, TextSendMessage(text=chunks[0]))
        for chunk in chunks[1:]:
            eln_group_bot_api.push_message(event.source.user_id, TextSendMessage(text=chunk))
        return

    if tl.startswith("/detail"):
        parts = text_raw.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            eln_group_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入：/detail 商品代號"))
            return
        query = parts[1].strip()
        matched_id, detail, candidates = db_find_detail(ck, query)
        if detail:
            eln_group_bot_api.reply_message(event.reply_token, TextSendMessage(text=detail[:4900]))
            return
        if candidates and matched_id is None:
            sample = "\n".join([f"• {c}" for c in candidates[:20]])
            eln_group_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"請再精準一點，候選代號：\n{sample}"[:4900]))
            return
        eln_group_bot_api.reply_message(event.reply_token, TextSendMessage(text="查不到該代號。"))
        return
except Exception as e:
    print(f"[ELN-GROUP ERROR] {e}")
```

# ==============================

# 內建排程

# ==============================

TZ_TAIPEI_PYTZ = pytz.timezone(“Asia/Taipei”)
def job_daily_report():
now = datetime.now(TZ_TAIPEI_PYTZ)
if now.weekday() >= 5:
print(”[Scheduler] 週末跳過財經日報”)
write_job_log(“財經日報”, “skipped”, “週末跳過”)
return
print(f”[Scheduler] 開始產生財經日報 {now.strftime(’%Y-%m-%d %H:%M’)}”)
write_job_log(“財經日報”, “started”, now.strftime(’%Y-%m-%d %H:%M’))
try:
from daily_report import main as report_main
report_main()
print(”[Scheduler] 財經日報推播成功”)
write_job_log(“財經日報”, “success”, “推播成功”)
except Exception as e:
print(f”[Scheduler] 財經日報失敗: {e}”)
write_job_log(“財經日報”, “error”, str(e))
def job_alert_monitor():
print(f”[Scheduler] 執行價格警示監控 {datetime.now(TZ_TAIPEI_PYTZ).strftime(’%H:%M’)}”)
try:
from alert_monitor import main as alert_main
alert_main()
except Exception as e:
print(f”[Scheduler] 價格警示失敗: {e}”)
def job_mail_monitor():
print(f”[Scheduler] 執行郵件監控 {datetime.now(TZ_TAIPEI_PYTZ).strftime(’%H:%M’)}”)
try:
from mail_monitor import main as mail_main
mail_main()
except Exception as e:
print(f”[Scheduler] 郵件監控失敗: {e}”)
def write_job_log(job_name: str, status: str, message: str = “”):
try:
with engine.begin() as conn:
conn.execute(text(”””
INSERT INTO eln_job_log (job_name, status, message, executed_at)
VALUES (:j, :s, :m, NOW())
“””), {“j”: job_name, “s”: status, “m”: message[:1000]})
except Exception as e:
print(f”[LOG] 寫入失敗: {e}”)
def job_auto_tracking():
now = datetime.now(TZ_TAIPEI_PYTZ)
if now.weekday() >= 5:
print(”[Scheduler] 週末跳過 ELN 追蹤”)
write_job_log(“ELN追蹤”, “skipped”, “週末跳過”)
return
print(f”[Scheduler] 開始 ELN 自動追蹤 {now.strftime(’%Y-%m-%d %H:%M’)}”)
write_job_log(“ELN追蹤”, “started”, now.strftime(’%Y-%m-%d %H:%M’))
try:
from auto_tracking_cron import main as tracking_main
tracking_main()
print(”[Scheduler] ELN 追蹤完成”)
write_job_log(“ELN追蹤”, “success”, “追蹤完成”)
except Exception as e:
print(f”[Scheduler] ELN 追蹤失敗: {e}”)
write_job_log(“ELN追蹤”, “error”, str(e))
def start_scheduler():
scheduler = BackgroundScheduler(timezone=TZ_TAIPEI_PYTZ)
scheduler.add_job(
job_daily_report,
CronTrigger(day_of_week=“mon-fri”, hour=6, minute=30, timezone=TZ_TAIPEI_PYTZ),
id=“daily_report”, name=“財經日報”
)
scheduler.add_job(
job_auto_tracking,
CronTrigger(day_of_week=“mon-fri”, hour=7, minute=0, timezone=TZ_TAIPEI_PYTZ),
id=“auto_tracking”, name=“ELN自動追蹤”
)
scheduler.add_job(
job_alert_monitor,
IntervalTrigger(minutes=15),
id=“alert_monitor”, name=“價格警示”
)
scheduler.add_job(
job_mail_monitor,
IntervalTrigger(minutes=15, start_date=datetime.now(TZ_TAIPEI_PYTZ).replace(second=0, microsecond=0) + timedelta(minutes=5)),
id=“mail_monitor”, name=“郵件監控”
)
scheduler.start()
print(”[Scheduler] 排程啟動完成 ✅”)
return scheduler
_scheduler = start_scheduler()
