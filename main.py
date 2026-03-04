import os
import re
import json
import traceback
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage

from openai import OpenAI

from autotracking_core import calculate_from_file


# ==============================
# ENV
# ==============================
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # 沒有也能跑，只是 AI 不啟用
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE env vars: LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = FastAPI()
VERSION = "eln-autotracking-full-2026-03-05"


# ==============================
# Writable paths on Render
# ==============================
BASE_DIR = Path("/tmp")
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

LAST_FILE = UPLOAD_DIR / "last.xlsx"
TARGET_FILE = BASE_DIR / "targets.json"
STATE_FILE = BASE_DIR / "state.json"  # 存「哪些聊天室正在等檔案」


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
# JSON helpers
# ==============================
def _read_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def _write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ==============================
# Optional: store default push target
# ==============================
def load_targets():
    return _read_json(TARGET_FILE, {})

def save_targets(data: dict):
    _write_json(TARGET_FILE, data)


# ==============================
# State machine: await file after /calc
# ==============================
def _chat_key(event) -> str:
    if event.source.type == "group":
        return f"group:{event.source.group_id}"
    if event.source.type == "room":
        return f"room:{event.source.room_id}"
    return f"user:{event.source.user_id}"

def load_state():
    return _read_json(STATE_FILE, {})

def save_state(data: dict):
    _write_json(STATE_FILE, data)

def set_await_file(chat_key: str, val: bool):
    st = load_state()
    st[chat_key] = {"await_file": bool(val)}
    save_state(st)

def is_await_file(chat_key: str) -> bool:
    st = load_state()
    return bool(st.get(chat_key, {}).get("await_file", False))


# ==============================
# Adapter: core -> (summary/detail map)
# ==============================
def run_autotracking(file_path: str, lookback_days: int = 3, notify_ki_daily: bool = True):
    """
    calculate_from_file() should return dict with at least:
      - results_df: pandas.DataFrame
      - report_text: str
    """
    out = calculate_from_file(
        file_path=file_path,
        lookback_days=lookback_days,
        notify_ki_daily=notify_ki_daily
    )

    df = out.get("results_df")
    report = out.get("report_text", "") or ""

    summary_lines = []
    detail_map = {}

    if df is not None and getattr(df, "empty", True) is False:
        # summary top 5
        top = df.head(5)
        for _, r in top.iterrows():
            try:
                status_first = str(r.get("狀態", "")).splitlines()[0]
            except Exception:
                status_first = str(r.get("狀態", ""))

            summary_lines.append(
                f"● {r.get('債券代號','-')} {r.get('Type','-')}｜{status_first}"
            )

        # detail map for every ID
        for _, r in df.iterrows():
            _id = str(r.get("債券代號", "")).strip()
            if not _id:
                continue

            # collect *_Detail columns
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

    summary_text = report
    if summary_lines:
        summary_text += "\n\n【前5筆摘要】\n" + "\n".join(summary_lines)

    return {"summary": summary_text, "detail": detail_map}


# ==============================
# AI fallback
# ==============================
def ai_reply(user_text: str) -> str:
    if not client:
        return "（AI 模式尚未開啟：缺少 OPENAI_API_KEY。你可以輸入 /help，或用 /calc 上傳 Excel 進行 ELN 追蹤。）"

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.35,
        max_tokens=700,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是「龍蝦」LINE助理。回答要務實、可直接拿去用。\n"
                    "如果問題跟 ELN 追蹤/KO/KI/狀態相關，可提示使用 /calc 上傳 Excel，或 /detail 查商品。"
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
        chat_key = _chat_key(event)

        print("[TEXT]", chat_key, repr(text_raw))

        # 兼容：允許 /help 或 help
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
                "/calc  ：提示你上傳 Excel（收到檔案後自動計算並回前5筆）\n"
                "/report：用最後一次上傳的 Excel 重算並回日報\n"
                "/detail <商品代號>：查單筆完整 KO/KI/狀態（可打部分代號）\n"
                "/settarget：把目前聊天室設為預設推播對象\n"
                "\n"
                "其他任何文字：會進 AI 對話模式"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return

        # SETTARGET (optional)
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

        # CALC：你要的 ELN 檔案模式
        # - /calc（無參數）=> 等上傳檔案
        # - /calc 1+2*3（可選）=> 計算機（不想要就刪掉這段）
        if cmd.startswith("calc") or cmd.startswith("clac"):
            parts = raw_cmd.split(" ", 1)  # raw_cmd 是去掉 / 的原字串

            # 你想保留計算機功能就留著；不想要就把這段整個刪掉
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

            # 無參數 => 進入等檔案模式
            set_await_file(chat_key, True)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="收到！請直接把 Excel 檔案傳給我（用 LINE 的『檔案』上傳），我會計算並回傳戰情快報＋前5筆摘要。")
            )
            return

        # REPORT：用最後一次 Excel 重算
        if cmd == "report":
            if not LAST_FILE.exists():
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="尚未有檔案，請先輸入 /calc 並上傳 Excel。"))
                return

            result = run_autotracking(str(LAST_FILE))
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=(result["summary"] or "沒有產出內容")[:4900]))
            return

        # DETAIL：查單筆（支援模糊）
        if cmd.startswith("detail"):
            parts = text_raw.split(" ", 1)
            if len(parts) < 2 or not parts[1].strip():
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入：/detail 商品代號（例：/detail U123）"))
                return

            if not LAST_FILE.exists():
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="尚未有檔案，請先輸入 /calc 並上傳 Excel。"))
                return

            query = parts[1].strip()
            q_norm = query.upper().strip()

            result = run_autotracking(str(LAST_FILE))
            detail_map = result.get("detail", {}) or {}
            keys = list(detail_map.keys())

            norm_map = {str(k).strip().upper(): k for k in keys}
            if q_norm in norm_map:
                real_key = norm_map[q_norm]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=detail_map[real_key][:4900]))
                return

            hits = [k for k in keys if q_norm in str(k).strip().upper()]
            if len(hits) == 1:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=detail_map[hits[0]][:4900]))
                return

            if len(hits) > 1:
                sample = "\n".join([f"• {h}" for h in hits[:20]])
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"找到多筆相符，請再精準一點：\n{sample}"[:4900]))
                return

            sample = "\n".join([f"• {k}" for k in keys[:20]]) if keys else "(目前無可查資料)"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"找不到代號：{query}\n\n目前可查代號(前20)：\n{sample}"[:4900])
            )
            return

        # ===== 非指令：AI 模式 =====
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
@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    try:
        chat_key = _chat_key(event)
        filename = getattr(event.message, "file_name", "") or ""
        size = getattr(event.message, "file_size", None)

        print("[FILE]", chat_key, filename, size)

        if not is_await_file(chat_key):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="我收到檔案了，但你尚未輸入 /calc。請先打 /calc，再上傳 Excel。")
            )
            return

        # 下載檔案
        message_id = event.message.id
        content = line_bot_api.get_message_content(message_id)

        # 存成 last.xlsx（你若要支援 csv 也可以再加判斷）
        with open(LAST_FILE, "wb") as f:
            for chunk in content.iter_content():
                f.write(chunk)

        # 先清掉 await_file，避免同一個聊天室一直處於等待狀態
        set_await_file(chat_key, False)

        # 計算
        result = run_autotracking(str(LAST_FILE))
        summary = result["summary"] or "已收到檔案，但沒有產出內容"

        # 回覆
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary[:4900]))

    except Exception as e:
        print("[ERROR] handle_file_message:", e)
        print(traceback.format_exc())
        try:
            set_await_file(_chat_key(event), False)
        except Exception:
            pass
        try:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="我收到檔案但處理失敗了。請確認是 .xlsx 檔，或稍後再試。")
            )
        except Exception:
            pass
