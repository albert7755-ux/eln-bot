"""
債券價格自動更新腳本（GitHub Actions 版）
從 TradingView 抓取最新價格，更新到 Google Drive CSV 檔案
"""

import os
import time
import json
import random
import re
import requests
import gspread
from datetime import datetime, date
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ==========================================
# 設定
# ==========================================
TODAY = date.today().strftime("%Y-%m-%d")
BOND_DRIVE_FOLDER_ID = os.environ.get("BOND_DRIVE_FOLDER_ID", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

# ==========================================
# Google Drive 連線
# ==========================================
def get_gspread_client():
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(creds)


def get_drive_files(folder_id: str) -> dict:
    """列出 Google Drive 資料夾內所有檔案（含 Sheets 和 CSV），回傳 {檔名: file_id}"""
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    params = {
        "q": f"'{folder_id}' in parents and trashed=false",
        "fields": "files(id, name, mimeType)",
        "pageSize": 500,
    }
    resp = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        headers=headers, params=params
    )
    files = resp.json().get("files", [])
    print(f"  [Drive] 共找到 {len(files)} 個檔案")
    return {f["name"]: f["id"] for f in files}


def download_csv_content(file_id: str) -> list[list]:
    """下載 Google Drive CSV 內容"""
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    resp = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media",
        headers=headers
    )
    lines = resp.text.strip().split("\n")
    return [line.split(",") for line in lines if line.strip()]


def append_to_csv(file_id: str, date_str: str, price: float, existing_rows: list):
    """把新一行追加到 Google Drive CSV"""
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    creds.refresh(Request())
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "text/csv",
    }
    # 重建整個 CSV（追加新行）
    new_row = f"{date_str},{price}"
    all_rows = [",".join(r) for r in existing_rows] + [new_row]
    content = "\n".join(all_rows) + "\n"

    resp = requests.patch(
        f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media",
        headers=headers,
        data=content.encode("utf-8")
    )
    return resp.status_code == 200


# ==========================================
# Selenium 設定
# ==========================================
def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--window-size=1280,800")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    )
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def get_price_from_tradingview(driver, exchange: str, isin: str, timeout: int = 20) -> float | None:
    """從 TradingView 抓取債券價格"""
    tv_symbol = f"{exchange}-{isin}"
    url = f"https://www.tradingview.com/symbols/{tv_symbol}/"
    try:
        driver.get(url)
        time.sleep(6)

        page_text = driver.find_element(By.TAG_NAME, "body").text

        # 方法一：抓 "% of par" 格式（SWB 歐洲交易所）
        match = re.search(r'([\d]{2,3}\.[\d]{1,4})\s*%?\s*of\s*par', page_text)
        if match:
            val = float(match.group(1))
            if 50 < val < 200:
                print(f"  [% of par] {val}")
                return val

        # 方法二：抓一般成交價格
        selectors = [
            "span[class*='last-']",
            "div[class*='lastContainer-'] span",
            "span[class*='price-']",
            "div[class*='priceWrapper'] span",
        ]
        wait = WebDriverWait(driver, timeout)
        for sel in selectors:
            try:
                el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
                text = el.text.strip().replace(",", "")
                if text:
                    val = float(text)
                    if 50 < val < 200:
                        print(f"  [CSS] {val}")
                        return val
            except:
                continue

        # 方法三：從頁面文字用 regex 抓數字
        numbers = re.findall(r'\b(\d{2,3}\.\d{1,4})\b', page_text)
        for n in numbers:
            val = float(n)
            if 60 < val < 160:
                print(f"  [regex] {val}")
                return val

    except Exception as e:
        print(f"  [ERROR] {e}")

    return None


# ==========================================
# 主程式
# ==========================================
def main():
    print(f"📅 執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 更新日期：{TODAY}\n")

    if not GOOGLE_CREDENTIALS_JSON:
        print("❌ 缺少 GOOGLE_CREDENTIALS_JSON")
        return
    if not BOND_DRIVE_FOLDER_ID:
        print("❌ 缺少 BOND_DRIVE_FOLDER_ID")
        return

    # 1. 讀取 bond_master.csv（從 Google Drive）
    print("📂 讀取 bond_master.csv...")
    drive_files = get_drive_files(BOND_DRIVE_FOLDER_ID)

    # bond_master 是 Google Sheets 格式
    print(f"  所有檔案清單（共{len(drive_files)}個）：")
    for name in sorted(drive_files.keys()):
        print(f"    - {repr(name)}")
    master_file_id = drive_files.get("bond_master")
    if not master_file_id:
        # 嘗試模糊比對
        for name, fid in drive_files.items():
            if "bond_master" in name.lower() or "master" in name.lower():
                master_file_id = fid
                print(f"  ✅ 模糊比對找到：{name}")
                break
    if not master_file_id:
        print("❌ 找不到 bond_master")
        return

    client = get_gspread_client()
    ws = client.open_by_key(master_file_id).get_worksheet(0)
    all_rows = ws.get_all_values()
    header = all_rows[0]
    bonds = []
    for row in all_rows[1:]:
        if len(row) >= 3 and row[0].strip():
            bonds.append({
                "filename": row[0].strip(),
                "exchange": row[1].strip(),
                "isin": row[2].strip(),
                "name": row[3].strip() if len(row) > 3 else "",
            })

    print(f"✅ 共 {len(bonds)} 筆債券\n")

    # 2. 啟動 Selenium
    print("🌐 啟動瀏覽器...")
    driver = create_driver()

    updated = 0
    skipped = 0
    failed = []

    try:
        for bond in bonds:
            filename = bond["filename"]
            exchange = bond["exchange"]
            isin = bond["isin"]
            name = bond["name"]

            print(f"📊 {name}（{isin}）")

            # 找對應的 CSV 檔案（可能有 ", 1D" 後綴）
            csv_filename = None
            file_id = None
            for possible in [f"{filename}.csv", f"{filename}, 1D.csv", f"{filename}_1D.csv"]:
                if possible in drive_files:
                    csv_filename = possible
                    file_id = drive_files[possible]
                    break
            # 模糊比對
            if not file_id:
                for name, fid in drive_files.items():
                    if filename in name and name.endswith(".csv"):
                        csv_filename = name
                        file_id = fid
                        break
            if not file_id:
                print(f"  ⚠️ 找不到對應 CSV（{filename}），跳過")
                failed.append(isin)
                continue

            # 確認今天是否已更新
            try:
                rows = download_csv_content(file_id)
                if rows and len(rows) > 1:
                    last_date = rows[-1][0].strip()
                    if last_date == TODAY:
                        print(f"  ⏭️ 今日已更新（{last_date}），跳過")
                        skipped += 1
                        continue
                    print(f"  📋 最後日期：{last_date}")
            except Exception as e:
                print(f"  ❌ 讀取失敗：{e}")
                rows = [["time", "close"]]

            # 抓取價格
            time.sleep(random.uniform(3, 6))
            price = get_price_from_tradingview(driver, exchange, isin)

            if price is None:
                print(f"  ❌ 抓取失敗")
                failed.append(isin)
                continue

            # 寫入 CSV
            try:
                success = append_to_csv(file_id, TODAY, price, rows)
                if success:
                    print(f"  ✅ 寫入成功：{TODAY} = {price}")
                    updated += 1
                else:
                    print(f"  ❌ 寫入失敗")
                    failed.append(isin)
            except Exception as e:
                print(f"  ❌ 寫入錯誤：{e}")
                failed.append(isin)

            print()

    finally:
        driver.quit()
        print("🔒 瀏覽器已關閉")

    print("=" * 50)
    print(f"✅ 成功更新：{updated} 筆")
    print(f"⏭️ 已是最新：{skipped} 筆")
    print(f"❌ 失敗：{len(failed)} 筆")
    if failed:
        print(f"失敗清單：{', '.join(failed[:10])}")
    print("=" * 50)


if __name__ == "__main__":
    main()
