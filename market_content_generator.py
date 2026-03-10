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
        "- 如果用戶有指定推薦標的，就照用戶指定的方向與標的名稱寫；如果沒有指定，不要自己亂推標的\n"
        "- 結尾固定使用這句金句：「市場修正是市場送你的禮物，敢不敢拆，決定了你未來的報酬。」🎁\n"
        "- 法遵注意：不可直接引用特定機構（如BlackRock、摩根等）的操作建議，\n"
        "  請改用「部分機構法人」、「市場主流觀點」等說法\n\n"
        "以下是今日市場新聞或用戶提供的推播內容：\n\n"
        + user_text
        + "\n\n請按照以下結構輸出，格式請完整照抄，不要省略任何區塊：\n\n"
        f"📊 市場觀點｜{today_str}\n\n"
        "📝 [一段幽默開場白，結合當日最重要的市場主題，2-3句]\n\n"
        "🔥 關鍵消息\n\n"
        "🔹 **[事件1標題]**\n"
        "[具體數據 + 一句有趣評語]\n\n"
        "🔹 **[事件2標題]**\n"
        "[具體數據 + 一句有趣評語]\n\n"
        "💼 布局方向\n\n"
        "✅ **[策略方向1]**\n"
        "[如有指定標的就列出，沒有就寫方向建議]\n\n"
        "✅ **[策略方向2]**\n"
        "[如有指定標的就列出，沒有就寫方向建議]\n\n"
        "📈 **固定收益策略**：[一句觀點]\n\n"
        "🥇 **避險配置**：[一句觀點，如黃金、美元等]\n\n"
        "「市場修正是市場送你的禮物，敢不敢拆，決定了你未來的報酬。」🎁"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text
