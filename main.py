import os
import json
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from autotracking_core import calculate_from_file

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
# 推播目標儲存
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
# Adapter：包裝 calculate_from_file
# ==============================

def run_autotracking(file_path: str):
    out = calculate_from_file(
        file_path=file_path,
        lookback_days=3,
        notify_ki_daily=True
    )

    df = out.get("results_df")
    report = out.get("report_text", "")

    summary_lines = []
    detail_map = {}

    if df is not None and not df.empty:
        top = df.head(5)

        for _, r in top.iterrows():
            summary_lines.append(
                f"● {r.get('債券代號','-')} {r.get('Type','-')}｜{str(r.get('狀態','')).splitlines()[0]}"
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
# 文字訊息處理
# ==============================

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):

    text_raw = event.message.text.strip()
    text = text_raw.lower()

    # hello
    if text in ["hello", "hi"]:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請問有什麼需要協助的？")
        )
        return

    # calc / clac
    if text in ["calc", "clac"] or text.startswith("calc") or text.startswith("clac"):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請上傳 Excel 檔案，我會幫您計算最新戰情狀況。")
        )
        return

    # report
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

    # detail
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

    # fallback
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="指令不明，請輸入 calc / report / detail 商品代號")
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

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=result["summary"])
    )
