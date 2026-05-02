import os
import re
import json
import traceback as _traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage, ImageMessage, AudioMessage
from sqlalchemy import create_engine, text
from autotracking_core import calculate_from_file

# ==============================
# ENV & 設定
# ==============================
# 主 Bot (eln autotracking): 負責所有「主動發信/推播」
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 副 Bot (Claw Bot / 龍蝦): 負責「群組內回覆指令」
ELN_GROUP_CHANNEL_SECRET = os.getenv("AGENT_LINE_CHANNEL_SECRET", "")
ELN_GROUP_ACCESS_TOKEN = os.getenv("AGENT_LINE_CHANNEL_ACCESS_TOKEN", "")
eln_group_bot_api = LineBotApi(ELN_GROUP_ACCESS_TOKEN) if ELN_GROUP_ACCESS_TOKEN else None
eln_group_handler = WebhookHandler(ELN_GROUP_CHANNEL_SECRET) if ELN_GROUP_CHANNEL_SECRET else None

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# 方案 A 關鍵：定義全域資料 Key，解決「查詢對象錯誤」問題
GLOBAL_CHAT_KEY = "GLOBAL_ELN_DATA" 

app = FastAPI()

# ==============================
# 改寫後的 Callback2 (Claw Bot 群組專用)
# ==============================
@app.post("/callback2")
async def callback2(request: Request):
    body = await request.body()
    try:
        data = json.loads(body.decode("utf-8"))
        for ev in data.get("events", []):
            if ev.get("type") != "message" or ev.get("message", {}).get("type") != "text":
                continue
            
            txt = ev["message"]["text"].strip()
            tl = txt.lower()
            rtoken = ev.get("replyToken", "")
            
            # 只處理 ELN 指令
            if not (tl.startswith("/list") or tl.startswith("/detail") or tl.startswith("/end")):
                continue

            # 使用全域 Key 讀取資料，確保新朋友也查得到
            ck = GLOBAL_CHAT_KEY 

            if tl.startswith("/list"):
                from collections import defaultdict
                lp = txt.split(" ", 1)
                nf = lp[1].strip() if len(lp) > 1 else ""
                
                # 從 Supabase 抓取資料[cite: 1]
                bonds = db_list_bonds(ck, limit=200)
                if not bonds:
                    eln_group_bot_api.reply_message(rtoken, TextSendMessage(text="目前尚無資料，請先上傳檔案。"))
                    continue

                # 邏輯：理專分類與模糊搜尋[cite: 1]
                if nf:
                    matched = [(bid, bond_status_tag(d)) for bid, ar, d in bonds if any(nf in a for a in re.split(r"[,，、/]", ar))]
                    if not matched:
                        eln_group_bot_api.reply_message(rtoken, TextSendMessage(text=f"找不到「{nf}」的持倉。"))
                        continue
                    out = f"👤 {nf} 的持倉：\n" + "\n".join([f" • {b}{t}" for b, t in matched])
                else:
                    grp = defaultdict(list)
                    for bid, ar, d in bonds:
                        ags = [a.strip() for a in re.split(r"[,，、/]", ar) if a.strip()] or ["未指定"]
                        for ag in ags: grp[ag].append((bid, bond_status_tag(d)))
                    out = "📋 全部商品（按理專排列）：\n"
                    for ag, bl in sorted(grp.items()):
                        out += f"👤 {ag}\n" + "\n".join([f" • {b}{t}" for b, t in bl]) + "\n"

                # ⚠️ 關鍵：群組內回覆使用 reply_message，不需加好友權限[cite: 1]
                eln_group_bot_api.reply_message(rtoken, TextSendMessage(text=out[:4900]))

            elif tl.startswith("/detail"):
                ps = txt.split(" ", 1)
                if len(ps) < 2:
                    eln_group_bot_api.reply_message(rtoken, TextSendMessage(text="請輸入：/detail 商品代號"))
                    continue
                mid, det, cands = db_find_detail(ck, ps[1].strip())
                if det:
                    eln_group_bot_api.reply_message(rtoken, TextSendMessage(text=det[:4900]))
                elif cands:
                    eln_group_bot_api.reply_message(rtoken, TextSendMessage(text="候選代號：\n" + "\n".join(cands[:10])))
                else:
                    eln_group_bot_api.reply_message(rtoken, TextSendMessage(text="查不到該代號。"))

    except Exception as e:
        print("[Claw Bot Error]", e)
    return "OK"

# ==============================
# 修改後的 /send 指令 (統一由主 Bot 發信)
# ==============================
# 在 handle_text_message 函數內
def handle_text_message(event):
    # ... 前段省略 ...
    if cmd == "send":
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        with engine.begin() as conn:
            rows = conn.execute(text("SELECT id, target_id, msg FROM eln_pending_notifications WHERE chat_key=:k"), {"k": GLOBAL_CHAT_KEY}).fetchall()
        
        # ⚠️ 關鍵：發送時統一調用 line_bot_api (eln autotracking)，因為它有新朋友的權限[cite: 1]
        sent, failed = 0, 0
        for row in rows:
            try:
                line_bot_api.push_message(row.target_id, TextSendMessage(text=row.msg[:4900]))
                sent += 1
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM eln_pending_notifications WHERE id=:i"), {"i": row.id})
            except Exception:
                failed += 1
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 發送完畢。成功: {sent}, 失敗: {failed} (失敗通常是未加好友)"))

# ==============================
# 修改後的 ELN 計算儲存 (統一 Chat Key)
# ==============================
def db_save_result(chat_key: str, summary: str, top5_lines: list[str], detail_map: dict[str, str], agent_name_map: dict[str, str] = {}):
    # 方案 A：不論傳入什麼 ck，強制存入全域 Key
    ck = GLOBAL_CHAT_KEY 
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO eln_last_report(chat_key, summary, updated_at) VALUES (:k, :s, NOW()) ON CONFLICT (chat_key) DO UPDATE SET summary=:s, updated_at=NOW()"), {"k": ck, "s": summary})
        # ... 後續刪除舊資料與寫入 eln_detail 邏輯相同[cite: 1]
