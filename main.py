import os
import re
import json
from fastapi import FastAPI, Request, HTTPException

from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

from openai import OpenAI

# ===== ENV =====
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-4o-mini")

# ===== Clients =====
app = FastAPI()
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# Health Check
# =========================

@app.get("/")
def root():
    return {"status": "ok", "service": "eln-bot", "webhook": "/callback"}

@app.get("/whoami")
def whoami():
    return {"service": "eln-bot", "version": "2026-03-05"}

# =========================
# Commands
# =========================

def is_command(text: str) -> bool:
    t = text.strip()
    return (
        t == "/help"
        or t.startswith("/calc")
        or t == "/report"
        or t.startswith("/detail")
    )


def handle_command(text: str):

    t = text.strip()

    if t == "/help":
        return (
            "ELN BOT 指令\n"
            "/help\n"
            "/calc 1+2*3\n"
            "/report\n"
            "/detail xxx\n\n"
            "其他任何文字都會進 AI 模式"
        )

    if t.startswith("/calc"):
        expr = t.replace("/calc", "").strip()

        if not expr:
            return "用法：/calc 1+2*3"

        if not re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s]+", expr):
            return "算式格式錯誤"

        try:
            result = eval(expr, {"__builtins__": {}})
            return f"{expr} = {result}"
        except:
            return "算式錯誤"

    if t == "/report":
        return "日報功能已接通"

    if t.startswith("/detail"):
        q = t.replace("/detail", "").strip()
        return f"detail 查詢：{q}"

    return "指令不明"

# =========================
# AI Chat
# =========================

def ai_chat(user_text: str):

    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是LINE助理，回答要簡短、清楚。",
            },
            {
                "role": "user",
                "content": user_text,
            },
        ],
        max_tokens=500,
        temperature=0.4,
    )

    return resp.choices[0].message.content.strip()


# =========================
# LINE Webhook
# =========================

@app.post("/callback")
async def callback(request: Request):

    signature = request.headers.get("X-Line-Signature")
    body = (await request.body()).decode("utf-8")

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:

        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):

            user_text = event.message.text.strip()

            if is_command(user_text):
                reply = handle_command(user_text)
            else:
                reply = ai_chat(user_text)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply[:4900])
            )

    return "OK"
