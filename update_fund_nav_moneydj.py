"""
基金淨值更新腳本（MoneyDJ 版）- Render 版
從 MoneyDJ 抓取最新淨值，寫入 Google Drive fund-data 試算表
"""

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
from bs4 import BeautifulSoup
import time
import os
import json
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from datetime import datetime

# ==========================================
# 設定區
# ==========================================

FOLDER_ID = "1i1-zUzLNnuwo2NVWijubvBICLbladZQO"

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

FUND_DB = {
    "F00001DRQQ_FO": {"moneydj": "pima4",   "name": "PIMCO收益增長"},
    "F0GBR04SG1_FO": {"moneydj": "JAZ03",   "name": "AV04駿利亨德森平衡基金"},
    "F00000ZXFV_FO": {"moneydj": "PYZR5",   "name": "施羅德環球收息債券"},
    "F00000PR1I_FO": {"moneydj": "FTZW6",   "name": "富達全球優質債券基金"},
    "F0000176Y4_FO": {"moneydj": "FTZX7",   "name": "富達永續發展全球存股優勢基金"},
    "F000011JGT_FO": {"moneydj": "ACCA204", "name": "群益潛力收益多重"},
    "F0GBR04MRL_FO": {"moneydj": "ALZ10",   "name": "聯博美國收益EA穩定月配"},
    "FOGBR05KHT_FO": {"moneydj": "PIK11",   "name": "PIMCO多元收益"},
    "F0000000P6_FO": {"moneydj": "SHZU0",   "name": "貝萊德全球智慧數據股票入息基金"},
    "F0GBR04AMK_FO": {"moneydj": "SHZB2",   "name": "貝萊德環球資產配置基金"},
    "F00000MLER_FO": {"moneydj": "ALBA0",   "name": "聯博-新興市場多元收益基金"},
    "F0GBR04MRF_FO": {"moneydj": "ALZ01",   "name": "聯博-美國成長基金"},
    "F00000PA64_FO": {"moneydj": "ALH37",   "name": "聯博-優化波動股票基金"},
    "F00000V557_FO": {"moneydj": "ALBG2",   "name": "聯博全球多元"},
    "F00001EQPP_FO": {"moneydj": "ACFP148", "name": "富邦台美雙星多重"},
    "F0HKG05X22_FO": {"moneydj": "ACDD04",  "name": "安聯台灣科技"},
    "F00001EBH4_FO": {"moneydj": "ACYT168", "name": "元大全球優質龍頭平衡基金"},
}

# ==========================================
# Google Drive 連線（從環境變數讀取）
# ==========================================

def get_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        raise RuntimeError("缺少 GOOGLE_CREDENTIALS_JSON 環境變數")
    creds_info = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(creds)

def get_all_sheets(folder_id):
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        raise RuntimeError("缺少 GOOGLE_CREDENTIALS_JSON 環境變數")
    creds_info = json.loads(creds_json)
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    params = {
        "q": f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
        "fields": "files(id, name)",
        "pageSize": 200,
    }
    resp = requests.get("https://www.googleapis.com/drive/v3/files",
                        headers=headers, params=params)
    return {f["name"]: f["id"] for f in resp.json().get("files", [])}

# ==========================================
# MoneyDJ 抓取淨值
# ==========================================

def fetch_nav_history_from_moneydj(moneydj_ticker):
    urls = [
        f"https://www.moneydj.com/funddj/ya/yp010001.djhtm?a={moneydj_ticker}",
        f"https://www.moneydj.com/funddj/ya/yp010000.djhtm?a={moneydj_ticker}",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Referer": "https://www.moneydj.com/funddj/"
    }

    import re
    from datetime import date
    today = date.today()
    current_year = today.year

    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=20, verify=False)
            resp.encoding = "big5"
            soup = BeautifulSoup(resp.text, "html.parser")
            nav_dict = {}

            tables = soup.find_all("table")
            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) < 2:
                        continue
                    texts = [c.get_text(strip=True) for c in cells]

                    for i, text in enumerate(texts):
                        if re.match(r"20\d\d/\d{2}/\d{2}$", text):
                            nav_date = text.replace("/", "-")
                            for j in range(i+1, min(i+4, len(texts))):
                                try:
                                    val = float(texts[j].replace(",", ""))
                                    if 0.5 < val < 100000:
                                        nav_dict[nav_date] = val
                                        break
                                except:
                                    continue

                    i = 0
                    while i < len(texts) - 1:
                        text = texts[i]
                        if re.match(r"^\d{2}/\d{2}$", text):
                            month = int(text[:2])
                            day   = int(text[3:])
                            year  = current_year if month <= today.month else current_year - 1
                            nav_date = f"{year}-{month:02d}-{day:02d}"
                            try:
                                val = float(texts[i+1].replace(",", ""))
                                if 0.5 < val < 100000:
                                    nav_dict[nav_date] = val
                            except:
                                pass
                        i += 1

            if nav_dict:
                return nav_dict

        except Exception as e:
            print(f"    {url} 失敗：{e}")
            continue

    return {}

# ==========================================
# 主程式
# ==========================================

def main():
    print(f"📅 執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📂 資料來源：MoneyDJ\n")

    try:
        client = get_client()
        all_sheets = get_all_sheets(FOLDER_ID)
    except Exception as e:
        msg = f"❌ Google Drive 連線失敗：{e}"
        print(msg)
        notify_line(0, 0, len(FUND_DB), error=msg)
        return

    updated = 0
    skipped = 0
    failed  = 0

    for sheet_name, info in FUND_DB.items():
        fund_name      = info["name"]
        moneydj_ticker = info["moneydj"]

        print(f"📊 {fund_name}（{sheet_name}）")

        sheet_id = all_sheets.get(sheet_name)
        if not sheet_id:
            print(f"  ⚠️  找不到試算表，跳過")
            failed += 1
            continue

        try:
            sh = client.open_by_key(sheet_id)
            ws = sh.get_worksheet(0)
            all_values = ws.get_all_values()

            existing_dates = set()
            last_date = ""
            for row in all_values[1:]:
                if row and row[0]:
                    d = row[0].strip()
                    existing_dates.add(d)
                    if d > last_date:
                        last_date = d

            print(f"  📋 最後日期：{last_date}，已有 {len(existing_dates)} 筆")

            time.sleep(2)
            nav_dict = fetch_nav_history_from_moneydj(moneydj_ticker)

            if not nav_dict:
                print(f"  ❌ 無法取得淨值")
                failed += 1
                continue

            print(f"  🌐 MoneyDJ 抓到 {len(nav_dict)} 筆（{min(nav_dict.keys())} ～ {max(nav_dict.keys())}）")

            new_rows = sorted([
                [d, nav_dict[d]] for d in nav_dict
                if d not in existing_dates
            ])

            if not new_rows:
                print(f"  ✅ 已是最新，無需更新")
                skipped += 1
                continue

            for row in new_rows:
                time.sleep(0.3)
                ws.append_row(row)
                print(f"  ➕ 新增：{row[0]} = {row[1]}")
            updated += len(new_rows)

        except Exception as e:
            if "429" in str(e):
                print(f"  ⏳ 超頻，等待 30 秒...")
                time.sleep(30)
            else:
                print(f"  ❌ 錯誤：{e}")
            failed += 1

        print()

    print("=" * 50)
    print(f"✅ 成功新增：{updated} 筆")
    print(f"⏭️  已是最新：{skipped} 檔")
    print(f"❌ 失敗：{failed} 檔")
    print("=" * 50)

    notify_line(updated, skipped, failed)
    return updated, skipped, failed


def notify_line(updated: int, skipped: int, failed: int, error: str = ""):
    """推播結果到 LINE"""
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("⚠️  缺少 LINE 設定，跳過推播")
        return
    try:
        ts = datetime.now().strftime('%m/%d %H:%M')
        if error:
            msg = f"📊 基金淨值更新失敗 {ts}\n{error}"
        else:
            lines = [
                f"📊 基金淨值更新完成 {ts}",
                f"✅ 新增：{updated} 筆",
                f"⏭️ 已是最新：{skipped} 檔",
            ]
            if failed > 0:
                lines.append(f"❌ 失敗：{failed} 檔")
            msg = "\n".join(lines)

        import urllib.request as _req
        data = json.dumps({
            "to": LINE_USER_ID,
            "messages": [{"type": "text", "text": msg}]
        }).encode("utf-8")

        req = _req.Request(
            "https://api.line.me/v2/bot/message/push",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
            },
            method="POST"
        )
        with _req.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print("📱 已推播結果到 LINE！")
    except Exception as e:
        print(f"⚠️  LINE 推播錯誤：{e}")


if __name__ == "__main__":
    main()
