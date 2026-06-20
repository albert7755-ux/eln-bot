def handle_user_message(user_message):
    
    # 清除訊息前後的空白，避免不小心多按空白鍵影響判斷
    user_message = user_message.strip()
    
    # 策略一：你提議的最高優先級「斜線指令」
    if user_message.startswith("/內規"):
        # 把 "/內規" 這幾個字切掉，只把真正的問題丟給 NotebookLM 大腦
        actual_query = user_message.replace("/內規", "").strip()
        
        # 防呆機制：如果同事只打了 "/內規" 但忘記打問題
        if not actual_query:
            return "請在指令後面加上想查詢的內容喔！例如：『/內規 75歲高齡客戶承做ELN的條件』"
            
        return nl_client.ask_regulation(actual_query)
        
    # 策略二：保留原本的「關鍵字防呆」 (當同事忘記打 /內規 時的備案)
    elif any(keyword in user_message for keyword in ["法規", "規定", "條款", "規範"]):
        return nl_client.ask_regulation(user_message)
        
    # 策略三：走你原本的 ELN 結構型商品報價或回報率查詢邏輯
    else:
        # 這裡接回你原本 Albert Claw Bot 的主邏輯
        return "執行原有的商品查詢邏輯..."
