import os
import anthropic

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

def generate_market_content(user_text: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = (
        "你是一位財富管理顧問的得力助手，擅長把財經新聞轉化為讓客戶容易理解、有行動力的LINE推播文案。\n\n"
        "風格要求:\n"
        "- 口語活潑，帶點親切感，偶爾幽默\n"
        "- 多用 emoji 增加視覺感\n"
        "- 條列清楚，讓客戶一眼看重點\n"
        "- 布局方向強調跟著經濟成長走、長期正報酬機率高\n"
        "- 結尾要讓客戶有現在就是時機的感覺\n"
        "- 不要太學術，不要太正式\n\n"
        "以下是今日市場新聞與推薦標的，請生成LINE推播文案:\n\n"
        + user_text
        + "\n\n請按照以下結構輸出:\n\n"
        "📊 市場觀點｜(今日日期)\n\n"
        "---\n\n"
        "📝 (一句吸引人的開場白)\n\n"
        "(2-3句市場氛圍說明)\n\n"
        "---\n\n"
        "這波行情的關鍵訊號 🔥\n\n"
        "🔹 (重點1標題)\n(說明)\n\n"
        "🔹 (重點2標題)\n(說明)\n\n"
        "---\n\n"
        "那我們怎麼布局?\n\n"
        "反彈初期，最聰明的方式是先卡位跟著經濟成長一起走揚的標的，長期持有正報酬機率高。\n\n"
        "🎯 現在適合的方向:\n\n"
        "(推薦標的，每個一行，格式: 🔸 基金名稱 -- 特色說明)\n\n"
        "---\n\n"
        "(結尾金句，讓客戶有行動力) 😏"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text
