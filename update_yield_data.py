"""
每日自動更新美國國債殖利率到 Google Drive
跑在 GitHub Actions（台灣早上9點）
從 FRED 抓 DGS10/DGS20/DGS30 最新資料
"""
import requests
import pandas as pd
import gspread
import time
import os
import json
from io import StringIO
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from datetime import datetime

# ==========================================
# 設定
# ==========================================
FUND_FOLDER_ID = "1i1-zUzLNnuwo2NVWijubvBICLbladZQO"

YIELD_SHEET_IDS = {
    "DGS10": "1NZ2NewtsdHMtYaCsmnrFBqiHTIsEFvlrmZSHaf1vMu4",
    "DGS20": "1BOPMadnV9AZEmZDFi1wRxhqXDGkxcygqb6uDRS7qrek",
    "DGS30": "1mObJF2ULlLte6cmP9claGT9kHDOjAVcEXRz-xHbeltQ",
}
YIELD_SHEET_NAMES = {
    "DGS10": "US_YIELD_10Y",
    "DGS20": "US_YIELD_20Y",
    "DGS30": "US_YIELD_30Y",
}

# ==========================================
# Google Drive 連線
# ==========================================
def get_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        # 本機執行時用 credentials.json
        if os.path.exists("credentials.json"):
            scopes = ["https://www.googleapis.com/auth/spreadsheets",
                      "https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
        else:
            raise RuntimeError("缺少 GOOGLE_CREDENTIALS_JSON 環境變數或 credentials.json")
    else:
        creds_info = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(creds), creds

def get_all_sheets(folder_id, creds):
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    params = {
        "q": f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
        "fields": "files(id, name)", "pageSize": 200,
    }
    resp = requests.get("https://www.googleapis.com/drive/v3/files",
                        headers=headers, params=params)
    return {f["name"]: f["id"] for f in resp.json().get("files", [])}

# ==========================================
# 從 FRED 抓最新資料
# ==========================================
def fetch_fred_latest(ticker):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={ticker}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://fred.stlouisfed.org/",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    df = pd.read_csv(StringIO(resp.text))
    df.columns = ["日期", "殖利率"]
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    df["殖利率"] = pd.to_numeric(df["殖利率"], errors="coerce")
    df = df.dropna().sort_values("日期").reset_index(drop=True)
    return df

# ==========================================
# 主程式
# ==========================================
def main():
    print(f"📅 執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📂 更新美國國債殖利率\n")

    try:
        client, creds = get_client()
        all_sheets = get_all_sheets(FUND_FOLDER_ID, creds)
    except Exception as e:
        print(f"❌ Google Drive 連線失敗：{e}")
        return

    updated_total = 0
    failed_list = []

    for fred_ticker, sheet_id in YIELD_SHEET_IDS.items():
        sheet_name = YIELD_SHEET_NAMES[fred_ticker]
        print(f"📈 {sheet_name}（{fred_ticker}）")

        try:
            sh = client.open_by_key(sheet_id)
            ws = sh.get_worksheet(0)
            all_vals = ws.get_all_values()
            existing_dates = set()
            last_date = ""
            for row in all_vals[1:]:
                if row and row[0]:
                    d = row[0].strip()
                    existing_dates.add(d)
                    if d > last_date:
                        last_date = d
            print(f"  📋 最後日期：{last_date}，已有 {len(existing_dates)} 筆")

            # 抓最新資料
            df = fetch_fred_latest(fred_ticker)
            print(f"  🌐 FRED 資料：{len(df)} 筆，最新 {df['日期'].max()} = {df[df['日期'] == df['日期'].max()]['殖利率'].values[0]:.2f}%")

            new_rows = [[r["日期"], r["殖利率"]] for _, r in df.iterrows()
                        if r["日期"] not in existing_dates]

            if not new_rows:
                print(f"  ✅ 已是最新！")
                continue

            for row in new_rows:
                ws.append_row(row)
                print(f"  ➕ 新增：{row[0]} = {row[1]:.2f}%")
                time.sleep(0.3)

            updated_total += len(new_rows)

        except Exception as e:
            print(f"  ❌ 錯誤：{e}")
            failed_list.append(sheet_name)
        print()

    print("=" * 50)
    print(f"✅ 成功新增：{updated_total} 筆")
    if failed_list:
        print(f"❌ 失敗：{', '.join(failed_list)}")
    print("=" * 50)

if __name__ == "__main__":
    main()
