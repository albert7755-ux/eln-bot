import os
import json
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage

from autotracking_core import calculate_from_file

# ==============================
# ENV
# ==============================
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE env vars: LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = FastAPI()

# ==============================
# Writable paths on Render
# ==============================
BASE_DIR = Path("/tmp")
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

LAST_FILE = UPLOAD_DIR / "last.xlsx"
TARGET_FILE = BASE_DIR / "targets.json"


# ==============================
# Optional: store default push target (not required for reply)
# ==============================
def load_targets():
    if TARGET_FILE.exists():
        try:
            return json.loads(TARGET_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_targets(data: dict):
    TARGET_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
            status_first = ""
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

            # collect *_Detail columns (T1_Detail..)
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
    text_raw = (event.message.text or "").strip()
    text = text_raw.lower().strip()

    # HELP
    if text in ("help", "?", "指令", "幫助"):
        msg = (
            "可用指令：\n"
            "• hello\n"
            "• calc（或 clac）：提示你上傳Excel\n"
            "• report：用最後一次上傳的Excel重算並回日報\n"
            "• detail <商品代號>：查單筆完整KO/KI/狀態（可打部分代號）\n"
            "• settarget：把目前聊天室設為預設推播對象\n"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # HELLO
    if text in ("hello", "hi"):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請問有什麼需要協助的？"))
        return

    # SETTARGET (optional)
    if text == "settarget":
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

    # CALC / CLAC (tolerant)
    if text in ("calc", "clac") or text.startswith("calc") or text.startswith("clac"):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="收到！請直接把 Excel 檔案傳給我，我會計算並回傳戰情快報。")
        )
        return

    # REPORT
    if text == "report":
        if not LAST_FILE.exists():
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="尚未有檔案，請先輸入 calc 並上傳 Excel。"))
            return

        result = run_autotracking(str(LAST_FILE))
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result["summary"] or "沒有產出內容"))
        return

    # DETAIL <id>
    if text.startswith("detail"):
        parts = text_raw.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入：detail 商品代號（例：detail U123）"))
            return

        if not LAST_FILE.exists():
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="尚未有檔案，請先輸入 calc 並上傳 Excel。"))
            return

        query = parts[1].strip()
        q_norm = query.strip().upper()

        result = run_autotracking(str(LAST_FILE))
        detail_map = result.get("detail", {}) or {}
        keys = list(detail_map.keys())

        # exact match (case-insensitive)
        norm_map = {str(k).strip().upper(): k for k in keys}
        if q_norm in norm_map:
            real_key = norm_map[q_norm]
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=detail_map[real_key]))
            return

        # fuzzy contains
        hits = [k for k in keys if q_norm in str(k).strip().upper()]

        if len(hits) == 1:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=detail_map[hits[0]]))
            return

        if len(hits) > 1:
            sample = "\n".join([f"• {h}" for h in hits[:20]])
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"找到多筆相符，請再精準一點：\n{sample}"))
            return

        # not found -> show sample list
        sample = "\n".join([f"• {k}" for k in keys[:20]]) if keys else "(目前無可查資料)"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"找不到代號：{query}\n\n目前可查代號(前20)：\n{sample}")
        )
        return

    # fallback
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="指令不明。請輸入 help 看可用指令。"))


# ==============================
# File message handler
# ==============================
@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    """
    LINE 的 FileMessage 不一定保證是 xlsx，但你這裡先當 xlsx 存 last.xlsx
    若你有 csv，也可以再加判斷副檔名。
    """
    message_id = event.message.id
    content = line_bot_api.get_message_content(message_id)

    # save as last.xlsx
    with open(LAST_FILE, "wb") as f:
        for chunk in content.iter_content():
            f.write(chunk)

    result = run_autotracking(str(LAST_FILE))
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result["summary"] or "已收到檔案，但沒有產出內容"))
