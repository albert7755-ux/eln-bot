import os
from datetime import datetime
import pytz
import anthropic

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

def generate_market_content(user_text: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    tw_tz = pytz.timezone("Asia/Taipei")
    today = datetime.now(tw_tz)
    weekday_map = ["週一","週二","週三","週四","週五","週六","週日"]
    today_str = f"{today.strftime('%Y年%m月%d日')}({weekday_map[today.weekday()]})"

    prompt = (
        f"今天日期是 {today_str}，請記得在文案標題使用這個正確日期。\n\n"
        "你是一位財富管理顧問的得力助手，擅長把財經新聞轉化為讓客戶容易理解、有行動力的LINE推播文案。\n\n"
        "風格要求：\n"
        "- 專業佔70%，幽默輕鬆佔30%\n"
        "- 口語化、有親切感，偶爾幽默但不失專業\n"
        "- 多用 emoji 增加視覺感與閱讀節奏\n"
        "- 條列清楚，讓客戶一眼看到重點\n"
        "- 投資方向與財經日報角度一致，除非用戶另外指定標的\n"
        "- 如果用戶有指定推薦標的，就照用戶指定的方向寫；如果沒有指定，不要自己亂推標的\n"
        "- 結尾要讓客戶感覺資訊有價值、顧問很專業\n\n"
        "以下是今日市場新聞或用戶提供的推播內容：\n\n"
        + user_text
        + "\n\n請按照以下結構輸出：\n\n"
        f"📊 市場觀點｜{today_str}\n\n"
        "---\n\n"
        "📝 (一句吸引人又帶點幽默的開場白)\n\n"
        "(2-3句市場氛圍說明，專業口語)\n\n"
        "---\n\n"
        "🔥 今日關鍵訊號\n\n"
        "🔹 (重點1標題)\n(說明，一到兩句)\n\n"
        "🔹 (重點2標題)\n(說明，一到兩句)\n\n"
        "---\n\n"
        "💼 布局方向\n\n"
        "(如果用戶有指定標的就列出；如果沒有，就寫與財經日報一致的方向建議，不指定特定商品)\n\n"
        "---\n\n"
        "(結尾一句話，專業中帶點幽默) 😄"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text
