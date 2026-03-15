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

    # ── 解析新聞內容與標的 ──
    # 格式：新聞內容（可多行）+ 空行 + 標的（每行一個）
    parts = user_text.strip().split("\n\n")
    news_content = parts[0].strip()

    # 空行後的每一行都是標的
    raw_targets = []
    if len(parts) > 1:
        for block in parts[1:]:
            for line in block.strip().splitlines():
                line = line.strip()
                if line:
                    raw_targets.append(line)

    targets_str = "、".join(raw_targets) if raw_targets else ""
    targets_list = "\n".join([f"• {t}" for t in raw_targets]) if raw_targets else "（用戶未指定標的，請只寫方向建議，不要自行推薦具體標的）"

    prompt = (
        f"今天日期是 {today_str}，請記得在文案標題使用這個正確日期。\n\n"
        "你是一位財富管理顧問的得力助手，擅長把財經新聞轉化為讓客戶容易理解、有行動力的LINE推播文案。\n\n"
        "【用戶提供的市場新聞】\n"
        f"{news_content}\n\n"
        "【用戶指定的推薦標的】\n"
        f"{targets_list}\n\n"
        "風格要求：\n"
        "- 專業佔85%，幽默輕鬆佔15%\n"
        "- 語氣必須正向積極，聚焦市場機會，不要渲染恐慌或放大風險\n"
        "- 避免「崩跌」、「大跌」、「恐慌」等負面字眼，改用「回檔」、「修正」、「整理」\n"
        "- 多用 emoji 增加視覺感\n"
        "- 法遵：不可引用特定機構操作建議，改用「部分機構法人」、「市場主流觀點」\n\n"
        "【標的分配規則 - 非常重要】\n"
        f"用戶指定了以下標的：{targets_str if targets_str else '無'}\n"
        "請根據每個標的的特性與當日市場情境，將它們分配到對應策略：\n"
        "- 長線佈局策略：長期趨勢型、波動較低的標的\n"
        "- 短線佈局策略：近期強勢主題、動能型的標的\n"
        "- 固定收益策略：債券基金、配息型商品等固收標的\n"
        "嚴格規定：布局方向只能列用戶指定的標的，絕對不能自己加入沒被提到的標的！\n"
        "若某策略方向沒有對應標的，只寫方向建議文字，不列任何標的名稱。\n\n"
        "關於 DCI（Dual Currency Investment）：固收策略可視情況帶入 DCI 說明，\n"
        "強調增強收益、靈活換匯、資金效率高，適合有換匯需求的客戶。\n\n"
        "結尾金句：每次根據當日市場主題創作一句全新投資金句，正向有力，後加符合情境 emoji。\n\n"
        "輸出格式必須完全如下：\n\n"
        f"📊 市場觀點｜{today_str}\n\n"
        "📝 [一段正向開場白，2-3句，聚焦機會而非風險]\n\n"
        "🔥 關鍵消息\n\n"
        "🔹 [事件1標題]\n"
        "[具體數據 + 正向評語]\n\n"
        "🔹 [事件2標題]\n"
        "[具體數據 + 正向評語]\n\n"
        "💼 布局方向\n\n"
        "📅 長線佈局策略\n"
        "[方向說明]\n"
        "[若有對應標的，列出：▶ 標的名稱]\n\n"
        "⚡ 短線佈局策略\n"
        "[方向說明]\n"
        "[若有對應標的，列出：▶ 標的名稱]\n\n"
        "📈 固定收益策略\n"
        "[方向說明，含DCI介紹]\n"
        "[若有對應標的，列出：▶ 標的名稱]\n\n"
        "🥇 避險配置：[一句觀點]\n\n"
        "[今日投資金句 + emoji]"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text
