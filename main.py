from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from linebot.exceptions import InvalidSignatureError
from openai import OpenAI
import os
from pathlib import Path
import json
import pandas as pd
from datetime import datetime

from autotracking_core import calculate_from_file

app = FastAPI()

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Missing env vars: LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN / OPENAI_API_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

WAITING_FOR_FILE = set()

DOWNLOAD_DIR = Path("/root/uploads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

TARGET_FILE = Path("/root/targets.json")

# ✅ 保存「最近一次計算結果」（記憶體內）
LAST_RUN = {
    "at": None,            # datetime
    "report_text": "",
    "admin_text": "",
    "results_df": None,    # pandas DataFrame
}

def load_targets():
    if TARGET_FILE.exists():
        return json.loads(TARGET_FILE.read_text(encoding="utf-8"))
    return {}

def save_targets(data):
    TARGET_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def get_chat_id(event):
    if event.source.type == "group":
        return ("group", event.source.group_id)
    if event.source.type == "room":
        return ("room", event.source.room_id)
    return ("user", event.source.user_id)

def safe_trim(text: str, limit: int = 3500) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= limit else (text[:limit] + "\n...(內容過長已截斷)")

def make_summary_block(results_df: pd.DataFrame, n: int = 5) -> str:
    if results_df is None or results_df.empty:
        return ""
    # 期待至少有：債券代號、狀態
    cols = set(results_df.columns)
    if "債券代號" not in cols or "狀態" not in cols:
        return ""

    top_n = results_df.head(n)
    lines = []
    for _, row in top_n.iterrows():
        bond_id = str(row.get("債券代號", "")).strip()
        status = str(row.get("狀態", "")).strip()
        if bond_id:
            lines.append(f"● {bond_id} - {status}")
    if not lines:
        return ""
    return "\n\n📋 商品摘要（前5筆）：\n" + "\n".join(lines)

def find_detail(results_df: pd.DataFrame, query: str) -> str:
    if results_df is None or results_df.empty:
        return "目前還沒有計算結果。請先輸入 calc 並上傳檔案。"

    if "債券代號" not in results_df.columns:
        return "結果缺少「債券代號」欄位，無法查詢。"

    q = query.strip().upper()
    if not q:
        return "請用：detail <商品代號> 例如：detail U123"

    # 支援部分匹配
    mask = results_df["債券代號"].astype(str).str.upper().str.contains(q, na=False)
    hits = results_df[mask]

    if hits.empty:
        return f"找不到包含「{q}」的商品。"

    # 取第一筆
    row = hits.iloc[0].to_dict()

    # 盡量多顯示：狀態、KO設定、最差表現、標的細節欄位
    lines = []
    lines.append(f"🔎 商品：{row.get('債券代號','')}")
    if "Type" in row: lines.append(f"類型：{row.get('Type','')}")
    if "Name" in row: lines.append(f"姓名：{row.get('Name','')}")
    if "交易日" in row: lines.append(f"交易日：{row.get('交易日','')}")
    if "KO設定" in row: lines.append(f"KO設定：{row.get('KO設定','')}")
    if "最差表現" in row: lines.append(f"最差表現：{row.get('最差表現','')}")
    if "狀態" in row:
        lines.append("\n📌 狀態：")
        lines.append(str(row.get("狀態","")).strip())

    # 把 T?_Detail 全部帶出來（如果 core 有產）
    detail_cols = [k for k in row.keys() if isinstance(k, str) and k.endswith("_Detail")]
    detail_cols.sort()
    if detail_cols:
        lines.append("\n📊 標的明細：")
        for k in detail_cols:
            v = str(row.get(k, "")).strip()
            if v:
                lines.append(v)

    # 如果同時命中多筆，提示一下
    if len(hits) > 1:
        lines.append(f"\n（提示：共有 {len(hits)} 筆命中，已顯示第一筆。你可以把代號打更完整。）")

    return "\n".join(lines).strip()

@app.post("/callback")
async def callback(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature")
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        return "Invalid signature"
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text_raw = event.message.text.strip()
    text = text_raw.lower()

    if text == "help":
        reply_text = (
            "📌 指令：\n"
            "help - 指令清單\n"
            "calc - 進入計算模式（接著上傳 Excel/CSV）\n"
            "cancel - 取消計算模式\n"
            "last - 重新顯示上次日報+摘要\n"
            "detail <商品代號> - 查單一商品（例：detail U123）\n"
            "settarget - 把「目前聊天室」設為推播目標\n"
            "target - 查看推播目標\n"
            "cleartarget - 清除推播目標\n"
            "其他文字 - 正常聊天"
        )

    elif text == "calc":
        WAITING_FOR_FILE.add(event.source.user_id)
        reply_text = "🧮 好的！請上傳 Excel/CSV 檔案，我收到後會回『日報 + 前5筆摘要』，也可用 detail 查單一商品。"

    elif text == "cancel":
        WAITING_FOR_FILE.discard(event.source.user_id)
        reply_text = "✅ 已取消計算模式。"

    elif text == "last":
        if LAST_RUN["at"] is None:
            reply_text = "目前還沒有計算結果。請先輸入 calc 並上傳檔案。"
        else:
            summary = make_summary_block(LAST_RUN["results_df"], n=5)
            ts = LAST_RUN["at"].strftime("%Y-%m-%d %H:%M")
            reply_text = f"🕘 上次更新：{ts}\n\n{LAST_RUN['report_text']}{summary}"

    elif text.startswith("detail"):
        # 支援：detail U123
        parts = text_raw.split(maxsplit=1)
        q = parts[1] if len(parts) > 1 else ""
        reply_text = find_detail(LAST_RUN["results_df"], q)

    elif text == "settarget":
        targets = load_targets()
        ttype, tid = get_chat_id(event)
        targets["default"] = {"type": ttype, "id": tid}
        save_targets(targets)
        reply_text = f"✅ 已設定推播目標：{ttype}:{tid}"

    elif text == "target":
        d = load_targets().get("default")
        reply_text = f"📌 目前推播目標：{d['type']}:{d['id']}" if d else "📌 尚未設定推播目標，請輸入 settarget"

    elif text == "cleartarget":
        targets = load_targets()
        targets.pop("default", None)
        save_targets(targets)
        reply_text = "✅ 已清除推播目標"

    else:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "你是專業且簡潔的工作助理。用繁體中文回答重點，不要每次都問『有什麼我可以幫助您』。資訊不足最多問1個關鍵問題。"
                },
                {"role": "user", "content": text_raw},
            ],
            max_tokens=220,
        )
        reply_text = resp.choices[0].message.content

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=safe_trim(reply_text)))

@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    user_id = event.source.user_id

    if user_id not in WAITING_FOR_FILE:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="我收到檔案了，但你還沒輸入 calc。請先輸入「calc」，再上傳檔案我才會開始計算喔。")
        )
        return

    message_id = event.message.id
    original_name = event.message.file_name or f"{message_id}"
    save_path = DOWNLOAD_DIR / original_name

    # 下載檔案
    content = line_bot_api.get_message_content(message_id)
    with open(save_path, "wb") as f:
        for chunk in content.iter_content():
            f.write(chunk)

    try:
        r = calculate_from_file(
            str(save_path),
            lookback_days=3,
            notify_ki_daily=True
        )

        report_text = r.get("report_text", "✅ 計算完成")
        admin_text = r.get("admin_text", "")
        results_df = r.get("results_df")

        # ✅ 存起來給 detail/last 用
        LAST_RUN["at"] = datetime.now()
        LAST_RUN["report_text"] = report_text
        LAST_RUN["admin_text"] = admin_text
        LAST_RUN["results_df"] = results_df

        summary = make_summary_block(results_df, n=5)
        result_text = report_text + summary
        # 若你希望 admin_text 也一起回（較長），把下面兩行取消註解即可：
        # if admin_text:
        #     result_text += "\n\n" + admin_text

    except Exception as e:
        result_text = f"❌ 計算失敗：{type(e).__name__}: {e}"

    WAITING_FOR_FILE.discard(user_id)

    # 回覆當下聊天室
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=safe_trim(result_text)))

    # 若有設定推播目標，另外推播一份（同樣是日報+摘要）
    d = load_targets().get("default")
    if d:
        try:
            line_bot_api.push_message(d["id"], TextSendMessage(text=safe_trim(result_text)))
        except Exception:
            pass
