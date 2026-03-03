import os
import json
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from autotracking_core import run_autotracking

# ==============================
# 環境變數
# ==============================

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE env vars")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = FastAPI()

# ==============================
# Render 可寫目錄
# ==============================

BASE_DIR = Path("/tmp")
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

TARGET_FILE = BASE_DIR / "targets.json"


# ==============================
# 讀寫推播目標
# ==============================

def load_targets():
    if TARGET_FILE.exists():
        return json.loads(TARGET_FILE.read_text(encoding="utf-8"))
    return {}

def save_targets(data):
    TARGET_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ==============================
# Webhook
# ==============================

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()

    try:
        handler.handle(body.decode(), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"


# ==============================
# 訊息處理
# ==============================

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):

    text_raw = event.message.text.strip()
    text = text_raw.lower()

    targets = load_targets()

    # --------------------------
    # hello
    # --------------------------
    if text in ["hello", "hi"]:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請問有什麼需要協助的？")
        )
        return

    # --------------------------
    # calc / clac
    # --------------------------
    if text in ["calc", "clac"] or text.startswith("calc") or text.startswith("clac"):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請上傳 Excel 檔案，我會幫您計算最新戰情狀況。")
        )
        return

    # --------------------------
    # report
    # --------------------------
    if text == "report":
        last_file = UPLOAD_DIR / "last.xlsx"
        if not last_file.exists():
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="尚未有檔案，請先輸入 calc 並上傳 Excel。")
            )
            return

        result = run_autotracking(str(last_file))
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=result["summary"])
        )
        return

    # --------------------------
    # detail <商品代號>
    # --------------------------
    if text.startswith("detail"):
        parts = text_raw.split(" ", 1)
        if len(parts) < 2:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請輸入 detail 商品代號")
            )
            return

        code = parts[1].strip()

        last_file = UPLOAD_DIR / "last.xlsx"
        if not last_file.exists():
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="尚未有檔案，請先 calc 上傳 Excel")
            )
            return

        result = run_autotracking(str(last_file))

        if code in result["detail"]:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=result["detail"][code])
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="找不到該商品代號")
            )
        return

    # --------------------------
    # settarget
    # --------------------------
    if text == "settarget":
        if event.source.type == "group":
            targets["default"] = event.source.group_id
            save_targets(targets)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="群組已設定為預設推播對象。")
            )
        else:
            targets["default"] = event.source.user_id
            save_targets(targets)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="已設定為您的個人推播對象。")
            )
        return

    # --------------------------
    # fallback
    # --------------------------
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="指令不明，請輸入 help。")
    )


# ==============================
# 檔案處理
# ==============================

@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):

    message_id = event.message.id
    content = line_bot_api.get_message_content(message_id)

    file_path = UPLOAD_DIR / "last.xlsx"

    with open(file_path, "wb") as f:
        for chunk in content.iter_content():
            f.write(chunk)

    result = run_autotracking(str(file_path))

    reply_text = result["summary"]

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

