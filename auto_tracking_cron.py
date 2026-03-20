“””
auto_tracking_cron.py
每天早上由 APScheduler 執行

1. 從 Supabase Storage 下載最新 Excel
1. 執行 ELN 自動追蹤計算
1. 更新資料庫
1. 推播結果到 LINE（龍蝦 bot）
1. 個別理專通知用 ELN Auto-Tracking bot 發送（理專已加好友）
   “””
   import os
   import traceback
   from datetime import datetime, timezone, timedelta
   from linebot import LineBotApi
   from linebot.models import TextSendMessage
   from sqlalchemy import create_engine, text
   from autotracking_core import calculate_from_file
   from eln_storage import download_latest_eln

TZ_TAIPEI = timezone(timedelta(hours=8))

# ── 兩個 bot ──

# 龍蝦：發給 Albert 自己

LINE_TOKEN = os.environ.get(“LINE_CHANNEL_ACCESS_TOKEN”, “”)

# ELN Auto-Tracking：發給理專（理專已加好友）

ELN_GROUP_TOKEN = “nv15/ftnhcP3EYo6pMGbvH+BxzMFHzF/b4NjRwG7v0nMm61aCmHVVJxCQOGZIF9+nO4dPzUHOSgTalXCFek09P0ft3LV1R6lSuJCszVPmbaZrJlxMPKilKAdWP4lzN9rwbqxuJcUQ9ouEZ1AkfOJKQdB04t89/1O/w1cDnyilFU=”

def get_env(key: str, default: str = “”) -> str:
val = os.environ.get(key, default)
if val is None:
val = “”
val = str(val).strip()
if not val and default == “”:
raise RuntimeError(f”Missing env var: {key}”)
return val

def push_long_message(bot_api: LineBotApi, target_id: str, text_msg: str, max_len: int = 4800):
if not text_msg:
return
text_msg = str(text_msg)
while text_msg:
chunk = text_msg[:max_len]
text_msg = text_msg[max_len:]
bot_api.push_message(target_id, TextSendMessage(text=chunk))

def normalize_db_url(db_url: str) -> str:
if db_url.startswith(“postgres://”):
return db_url.replace(“postgres://”, “postgresql+psycopg://”, 1)
if db_url.startswith(“postgresql://”):
return db_url.replace(“postgresql://”, “postgresql+psycopg://”, 1)
return db_url

def save_result_to_db(engine, chat_key: str, summary: str, top5_lines: list, detail_map: dict, agent_name_map: dict):
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

def save_job_log(engine, job_name: str, status: str, detail: str = “”):
with engine.begin() as conn:
conn.execute(text(”””
CREATE TABLE IF NOT EXISTS eln_job_log (
id BIGSERIAL PRIMARY KEY,
job_name TEXT NOT NULL,
status TEXT NOT NULL,
detail TEXT NOT NULL DEFAULT ‘’,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
“””))
conn.execute(text(”””
INSERT INTO eln_job_log(job_name, status, detail)
VALUES (:j, :s, :d)
“””), {“j”: job_name, “s”: status, “d”: detail[:4000]})

def ensure_pending_table(engine):
with engine.begin() as conn:
conn.execute(text(”””
CREATE TABLE IF NOT EXISTS eln_pending_notifications (
id BIGSERIAL PRIMARY KEY,
chat_key TEXT NOT NULL,
target_id TEXT NOT NULL,
agent_name TEXT NOT NULL DEFAULT ‘’,
bond_id TEXT NOT NULL DEFAULT ‘’,
status TEXT NOT NULL DEFAULT ‘’,
msg TEXT NOT NULL DEFAULT ‘’,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
“””))

def build_result(output: dict):
df = output.get(“results_df”)
report = output.get(“report_text”, “”) or “”
top5_lines = []
detail_map = {}
agent_name_map = {}
if df is not None and not df.empty:
for _, r in df.head(5).iterrows():
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
f”最終評價日: {r[‘最終評價日’] if ‘最終評價日’ in r.index else (’-’)}\n”
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

def save_pending_notifications(engine, chat_key: str, individual_messages: list):
ensure_pending_table(engine)
with engine.begin() as conn:
conn.execute(text(“DELETE FROM eln_pending_notifications WHERE chat_key=:k”), {“k”: chat_key})
for msg in individual_messages:
conn.execute(text(”””
INSERT INTO eln_pending_notifications
(chat_key, target_id, agent_name, bond_id, status, msg)
VALUES (:k, :t, :n, :b, :s, :m)
“””), {
“k”: chat_key,
“t”: msg.get(“target”, “”),
“n”: msg.get(“name”, “”),
“b”: msg.get(“id”, “”),
“s”: msg.get(“status”, “”),
“m”: msg.get(“msg”, “”)
})

def build_pending_text(individual_messages: list) -> str:
if not individual_messages:
return “✅ 今日無需通知理專”
lines = [f”📋 今日 ELN 通知待確認（{len(individual_messages)}筆）\n”]
for i, msg in enumerate(individual_messages, start=1):
lines.append(
f”{i}️⃣ {msg.get(‘name’, ‘’)} | {msg.get(‘id’, ‘’)} | {msg.get(‘status’, ‘’)}\n”
f”  /send {i} 發送　/skip {i} 略過”
)
lines.append(”\n/send all 全部發送　/skip all 全部略過”)
lines.append(”/send list 查看待確認清單”)
return “\n\n”.join(lines)

def main():
now = datetime.now(TZ_TAIPEI)
print(f”[{now.strftime(’%Y/%m/%d %H:%M’)}] auto_tracking_cron starting…”)

```
line_token = get_env("LINE_CHANNEL_ACCESS_TOKEN")
user_id = get_env("LINE_USER_ID")
db_url = normalize_db_url(get_env("DATABASE_URL"))
group_id = os.environ.get("ELN_GROUP_ID", "").strip()
push_group_summary = os.environ.get("ELN_PUSH_GROUP_SUMMARY", "true").strip().lower() == "true"

engine = create_engine(db_url, pool_pre_ping=True)
# 龍蝦：發給 Albert
line_bot_api = LineBotApi(line_token)
# ELN Auto-Tracking：發給理專
eln_group_bot_api = LineBotApi(ELN_GROUP_TOKEN)

personal_chat_key = f"user:{user_id}"
group_chat_key = f"group:{group_id}" if group_id else ""

try:
    print("Downloading latest Excel from Supabase Storage...")
    excel_path = download_latest_eln("/tmp/latest_eln.xlsx")
    print(f"Downloaded latest Excel: {excel_path}")

    print("Running autotracking...")
    out = calculate_from_file(excel_path, lookback_days=3, notify_ki_daily=True)
    summary, top5_lines, detail_map, agent_name_map = build_result(out)

    print("Saving result to database (personal)...")
    save_result_to_db(engine, personal_chat_key, summary, top5_lines, detail_map, agent_name_map)
    if group_chat_key:
        print("Saving result to database (group)...")
        save_result_to_db(engine, group_chat_key, summary, top5_lines, detail_map, agent_name_map)
    print(f"Saved {len(detail_map)} bonds to DB")

    print("Sending summary to personal LINE...")
    push_long_message(line_bot_api, user_id, summary[:20000])

    if group_id and push_group_summary:
        print("Sending summary to group LINE...")
        push_long_message(line_bot_api, group_id, summary[:20000])

    individual_messages = out.get("individual_messages", []) or []
    print("Saving pending notifications...")
    save_pending_notifications(engine, personal_chat_key, individual_messages)

    # ── 個別通知直接用 ELN Auto-Tracking bot 發送給理專 ──
    sent, failed = 0, 0
    for msg in individual_messages:
        target = msg.get("target", "")
        if not target:
            continue
        try:
            eln_group_bot_api.push_message(target, TextSendMessage(text=msg.get("msg", "")[:4900]))
            sent += 1
            print(f"[NOTIFY] 已發送給 {msg.get('name','')} | {msg.get('id','')}")
        except Exception as e:
            failed += 1
            print(f"[NOTIFY ERROR] {target}: {e}")

    if individual_messages:
        result_msg = f"📋 今日 ELN 通知\n已發送 {sent} 筆"
        if failed:
            result_msg += f"，失敗 {failed} 筆（請確認理專有加 ELN Auto-Tracking 為好友）"
        push_long_message(line_bot_api, user_id, result_msg)
    else:
        push_long_message(line_bot_api, user_id, "✅ 今日無需通知理專")

    save_job_log(
        engine,
        job_name="auto_tracking_cron",
        status="success",
        detail=f"Saved {len(detail_map)} bonds, notified={sent}, failed={failed}"
    )
    print("Done!")

except Exception as e:
    err = f"{type(e).__name__}: {e}"
    print(f"Error: {err}")
    print(traceback.format_exc())
    try:
        push_long_message(line_bot_api, user_id, f"⚠️ ELN 自動追蹤失敗：{err}")
    except Exception:
        pass
    try:
        save_job_log(
            engine,
            job_name="auto_tracking_cron",
            status="failed",
            detail=traceback.format_exc()
        )
    except Exception:
        pass
```

if **name** == “**main**”:
main()
