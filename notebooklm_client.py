import os
import logging
import requests
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

class NotebookLMClient:
    def __init__(self, notebook_id):
        """
        初始化 NotebookLM 客戶端
        """
        self.notebook_id = notebook_id
        
        # 從環境變數讀取機密 Cookie
        self.session_cookie = os.getenv("NOTEBOOKLM_COOKIE")
        if not self.session_cookie:
            logging.error("找不到 NotebookLM Cookie，請確認環境變數 NOTEBOOKLM_COOKIE 是否設定正確。")
            self.session_cookie = ""

        # 模擬正常瀏覽器的標頭
        self.headers = {
            "Cookie": f"__Secure-1PSID={self.session_cookie}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json"
        }
        
        # NotebookLM 實驗性 API 端點
        self.base_url = "https://notebooklm.google.com/api/experimental"

    def ask_regulation(self, user_query):
        """
        向 NotebookLM 法規知識庫提問並取得完整回答
        """
        if not self.session_cookie:
            return "系統尚未綁定 NotebookLM 憑證，請通知管理員檢查環境變數設定。"

        url = f"{self.base_url}/notebooks/{self.notebook_id}/query"
        payload = {"query": user_query}

        try:
            # 設定 30 秒 timeout
            response = requests.post(url, headers=self.headers, json=payload, timeout=30)
            response.raise_for_status()
            
            # 解析回傳的 JSON 資料
            data = response.json()
            return data.get("answer", "NotebookLM 已收到請求，但無法解析文字回答。")
            
        except requests.exceptions.Timeout:
            logging.warning("NotebookLM 查詢超時。")
            return "目前法規資料庫連線稍微壅塞，請稍後再試一次。"
        except requests.exceptions.RequestException as e:
            logging.error(f"NotebookLM API 連線異常: {e}")
            return "抱歉，無法連線至法規知識庫，這可能是 Google 阻擋了連線，或是 Cookie 已失效。"
