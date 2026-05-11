"""
債券歷史數據補齊腳本（GitHub Actions 版）
從 TradingView 下載 CSV，補齊 Google Sheets 裡缺少的歷史價格
"""
import os
import time
import json
import random
import requests
import gspread
from datetime import datetime, date, timedelta
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
import csv
import io
# ==========================================
# 設定
# ==========================================
TODAY = date.today().strftime("%Y-%m-%d")
BOND_DRIVE_FOLDER_ID = os.environ.get("BOND_DRIVE_FOLDER_ID", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
DOWNLOAD_DIR = "/tmp/tv_downloads"
TV_SESSION_ID = os.environ.get("TV_SESSION_ID", "")
# ==========================================
# Google Drive / Sheets 連線
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
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/drive"]
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
    return {f["name"]: f["id"] for f in files}
# ==========================================
# Selenium 設定
# ==========================================
def create_driver():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    )
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver

def download_tv_csv(driver, exchange: str, symbol: str) -> dict | None:
    """
    從 TradingView 下載歷史 CSV
    回傳 {日期: 收盤價} 的 dict
    """
    tv_symbol = f"{exchange}-{symbol}"
    url = f"https://www.tradingview.com/symbols/{tv_symbol}/"
    try:
        # 清空下載目錄
        for f in os.listdir(DOWNLOAD_DIR):
            os.remove(os.path.join(DOWNLOAD_DIR, f))

        driver.get(url)
        # ★ 等待時間加長到 12 秒，給慢速頁面更多時間載入
        time.sleep(12)

        # 嘗試右鍵選單
        try:
            # ★ 改用多個 selector 逐一嘗試，不再一次找不到就報錯
            chart = None
            for selector in [
                "canvas",
                ".chart-gui-wrapper",
                ".pane-legacy",
                "[class*='chart-container']",
                "[class*='tv-lightweight-charts']",
                "div[class*='chart']",
            ]:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        chart = elements[0]
                        print(f"  找到圖表元素：{selector}")
                        break
                except:
                    continue

            if chart:
                actions = ActionChains(driver)
                actions.context_click(chart).perform()
                time.sleep(2)

                # 找右鍵選單中的 Download chart data
                menu_items = driver.find_elements(By.CSS_SELECTOR, "[class*='item'], [role='menuitem'], [class*='menu'] span")
                download_item = None
                for item in menu_items:
                    try:
                        text = (item.text or "").strip()
                        if "download" in text.lower() or "chart data" in text.lower():
                            download_item = item
                            print(f"  找到下載選項：{text}")
                            break
                    except:
                        continue

                if not download_item:
                    spans = driver.find_elements(By.XPATH, "//*[contains(text(), 'Download chart data') or contains(text(), 'download chart')]")
                    if spans:
                        download_item = spans[0]
                        print(f"  找到下載選項（XPath）")

                if download_item:
                    download_item.click()
                    time.sleep(2)
                    download_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Download')]")
                    if download_btns:
                        download_btns[-1].click()
                        print(f"  點擊 Download 確認")
                        time.sleep(3)
                        for _ in range(20):
                            files = os.listdir(DOWNLOAD_DIR)
                            csv_files = [f for f in files if f.endswith('.csv') and not f.endswith('.crdownload')]
                            if csv_files:
                                csv_path = os.path.join(DOWNLOAD_DIR, csv_files[0])
                                print(f"  ✅ 下載完成：{csv_files[0]}")
                                return parse_tv_csv(csv_path)
                            time.sleep(1)
                        print(f"  ⚠️ 等待下載超時")
                    else:
                        print(f"  ⚠️ 找不到 Download 確認按鈕")
                else:
                    print(f"  ⚠️ 找不到右鍵選單下載選項，改用頁面價格")
            else:
                # ★ 找不到圖表元素時，等多 5 秒再試一次 fallback
                print(f"  ⚠️ 找不到圖表元素，等待 5 秒後改用頁面價格")
                time.sleep(5)

            # 關閉選單
            try:
                driver.find_element(By.TAG_NAME, "body").click()
            except:
                pass

        except Exception as e:
            print(f"  [右鍵選單錯誤] {e}")

        # ★ fallback 加強版：多種 regex 模式嘗試抓價格
        return fallback_get_current_price(driver)

    except Exception as e:
        print(f"  [ERROR] {e}")
        return None

def parse_tv_csv(csv_path: str) -> dict:
    """解析 TradingView 下載的 CSV，回傳 {日期: 收盤價}"""
    result = {}
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_str = row.get('time', '').strip()
                close_str = row.get('close', '').strip()
                if date_str and close_str:
                    try:
                        if 'T' in date_str:
                            date_str = date_str.split('T')[0]
                        close_val = float(close_str)
                        if 0 < close_val < 1000:
                            result[date_str] = close_val
                    except:
                        continue
        print(f"  📊 CSV 解析完成，共 {len(result)} 筆歷史數據")
    except Exception as e:
        print(f"  [CSV解析錯誤] {e}")
    return result

def fallback_get_current_price(driver) -> dict | None:
    """Fallback: 多種方式嘗試抓當前價格"""
    import re
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text

        # ★ 新增多種 regex 模式
        patterns = [
            r'([\d]{2,3}\.[\d]{1,4})\s*%?\s*of\s*par',          # XX.XX% of par
            r'Last\s+([\d]{2,3}\.[\d]{1,4})',                     # Last XX.XX
            r'Price\s+([\d]{2,3}\.[\d]{1,4})',                    # Price XX.XX
            r'Close\s+([\d]{2,3}\.[\d]{1,4})',                    # Close XX.XX
            r'\b((?:9[0-9]|1[0-1][0-9]|120)\.[\d]{1,4})\b',     # 90~120 之間的數字（債券常見價格區間）
        ]

        for pattern in patterns:
            match = re.search(pattern, page_text, re.IGNORECASE)
            if match:
                val = float(match.group(1))
                if 50 < val < 200:
                    print(f"  💡 Fallback 抓到價格：{val}（pattern: {pattern[:30]}）")
                    return {TODAY: val}

        print(f"  ⚠️ Fallback 也找不到價格")
    except Exception as e:
        print(f"  [Fallback錯誤] {e}")
    return None

# ==========================================
# 主程式
# ==========================================
def main():
    print(f"📅 執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 今天日期：{TODAY}\n")
    if not GOOGLE_CREDENTIALS_JSON:
        print("❌ 缺少 GOOGLE_CREDENTIALS_JSON")
        return
    if not BOND_DRIVE_FOLDER_ID:
        print("❌ 缺少 BOND_DRIVE_FOLDER_ID")
        return
    # 1. 讀取 bond_master
    print("📂 讀取 bond_master...")
    drive_files = get_drive_files(BOND_DRIVE_FOLDER_ID)
    client = get_gspread_client()
    master_file_id = drive_files.get(" bond_master") or drive_files.get("bond_master")
    if not master_file_id:
        for name, fid in drive_files.items():
            if "master" in name.lower():
                master_file_id = fid
                print(f"  模糊比對：{name}")
                break
    if not master_file_id:
        print("❌ 找不到 bond_master")
        return
    ws_master = client.open_by_key(master_file_id).get_worksheet(0)
    all_rows = ws_master.get_all_values()
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
    # 2. 啟動瀏覽器
    print("🌐 啟動瀏覽器...")
    driver = create_driver()
    # 注入 TradingView Session Cookie
    if TV_SESSION_ID:
        print("🔑 注入 TradingView Session Cookie...")
        driver.get("https://www.tradingview.com")
        time.sleep(3)
        driver.add_cookie({
            "name": "sessionid",
            "value": TV_SESSION_ID,
            "domain": ".tradingview.com",
            "path": "/",
        })
        driver.refresh()
        time.sleep(3)
        print("✅ Cookie 注入完成")
    updated_total = 0
    skipped_total = 0
    failed = []
    try:
        for bond in bonds:
            filename = bond["filename"]
            exchange = bond["exchange"]
            isin = bond["isin"]
            name = bond["name"]
            print(f"📊 {name}（{isin}）")
            # 找對應的 Google Sheets
            file_id = None
            for possible in [f"{filename}, 1D", filename, f"{filename}, 1D.csv"]:
                if possible in drive_files:
                    file_id = drive_files[possible]
                    break
            if not file_id:
                for fname, fid in drive_files.items():
                    if filename in fname:
                        file_id = fid
                        break
            if not file_id:
                print(f"  ⚠️ 找不到對應檔案，跳過")
                failed.append(isin)
                continue
            # 讀取現有數據
            time.sleep(2)
            try:
                sh = client.open_by_key(file_id)
                ws = sh.get_worksheet(0)
                all_vals = ws.get_all_values()
                existing_dates = set()
                for row in all_vals[1:]:
                    if row and row[0].strip():
                        existing_dates.add(row[0].strip())
                last_date = max(existing_dates) if existing_dates else "2020-01-01"
                print(f"  📋 最後日期：{last_date}，已有 {len(existing_dates)} 筆")
            except Exception as e:
                if "429" in str(e):
                    print(f"  ⏳ API 超頻，等待 60 秒...")
                    time.sleep(60)
                    try:
                        sh = client.open_by_key(file_id)
                        ws = sh.get_worksheet(0)
                        all_vals = ws.get_all_values()
                        existing_dates = set()
                        for row in all_vals[1:]:
                            if row and row[0].strip():
                                existing_dates.add(row[0].strip())
                        last_date = max(existing_dates) if existing_dates else "2020-01-01"
                        print(f"  📋 最後日期：{last_date}，已有 {len(existing_dates)} 筆")
                    except Exception as e2:
                        print(f"  ❌ 重試失敗：{e2}")
                        failed.append(isin)
                        continue
                else:
                    print(f"  ❌ 讀取失敗：{e}")
                    failed.append(isin)
                    continue
            print(f"  🔍 檢查歷史缺失，從 TradingView 下載完整數據...")
            time.sleep(random.uniform(2, 4))
            tv_data = download_tv_csv(driver, exchange, isin)
            if not tv_data:
                print(f"  ❌ 無法取得數據")
                failed.append(isin)
                continue
            # 只補最近60天
            cutoff = (date.today() - timedelta(days=60)).strftime("%Y-%m-%d")
            missing = {
                d: p for d, p in tv_data.items()
                if d not in existing_dates and d >= cutoff and d <= TODAY
            }
            if not missing:
                print(f"  ✅ 數據已是最新，無需補齊")
                skipped_total += 1
                continue
            sorted_missing = sorted(missing.items())
            print(f"  📝 補齊 {len(sorted_missing)} 筆缺失數據...")
            try:
                rows_to_add = [[d, p] for d, p in sorted_missing]
                ws.append_rows(rows_to_add)
                print(f"  ✅ 成功補齊：{sorted_missing[0][0]} ~ {sorted_missing[-1][0]}")
                updated_total += len(sorted_missing)
            except Exception as e:
                print(f"  ❌ 寫入失敗：{e}")
                failed.append(isin)
            print()
    finally:
        driver.quit()
        print("🔒 瀏覽器已關閉\n")
    print("=" * 50)
    print(f"✅ 成功補齊：{updated_total} 筆")
    print(f"⏭️ 已是最新：{skipped_total} 檔")
    print(f"❌ 失敗：{len(failed)} 筆")
    if failed:
        print(f"失敗清單：{', '.join(failed[:10])}")
    print("=" * 50)

if __name__ == "__main__":
    main()
