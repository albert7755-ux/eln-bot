"""
fund_nav.py — 基金淨值更新模組
供 main.py import 使用，也可獨立執行
"""

import requests
import time
import os
import json
import re
import gspread
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from datetime import datetime

try:
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except:
    pass

# ==========================================
# 設定
# ==========================================
FOLDER_ID = "1i1-zUzLNnuwo2NVWijubvBICLbladZQO"

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
# Google 連線（支援環境變數 & credentials.json）
# ==========================================
def _get_creds(scopes):
    """優先用環境變數，其次用本機 credentials.json"""
    creds_json = os.getenv("GOOGLE_CREDENTIALS") or os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        return Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    creds_path = os.path.join(os.path.dirname(__file__), "credentials.json")
    return Credentials.from_service_account_file(creds_path, scopes=scopes)

def get_client():
    creds = _get_creds([
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly"
    ])
    return gspread.authorize(creds)

def get_all_sheets(folder_id):
    creds = _get_creds(["https://www.googleapis.com/auth/drive.readonly"])
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    params = {
        "q": f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
        "fields": "files(id, name)", "pageSize": 200,
    }
    resp = requests.get("https://www.googleapis.com/drive/v3/files", headers=headers, params=params)
    return {f["name"]: f["id"] for f in resp.json().get("files", [])}

# ==========================================
# MoneyDJ 抓取
# ==========================================
def fetch_nav_history(moneydj_ticker):
    urls = [
        f"https://www.moneydj.com/funddj/ya/yp010001.djhtm?a={moneydj_ticker}",
        f"https://www.moneydj.com/funddj/ya/yp010000.djhtm?a={moneydj_ticker}",
    ]
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Referer": "https://www.moneydj.com/funddj/"
    }
    from datetime import date
    today = date.today()
    cur_year = today.year

    for url in urls:
        try:
            resp = requests.get(url, headers=hdrs, timeout=20, verify=False)
            resp.encoding = "big5"
            soup = BeautifulSoup(resp.text, "html.parser")
            nav_dict = {}
            for table in soup.find_all("table"):
                for row in table.find_all("tr"):
                    texts = [c.get_text(strip=True) for c in row.find_all("td")]
                    if len(texts) < 2:
                        continue
                    # 模式一：YYYY/MM/DD
                    for i, t in enumerate(texts):
                        if re.match(r"20\d\d/\d{2}/\d{2}$", t):
                            d = t.replace("/", "-")
                            for j in range(i+1, min(i+4, len(texts))):
                                try:
                                    v = float(texts[j].replace(",", ""))
                                    if 0.5 < v < 100000:
                                        nav_dict[d] = v
                                        break
                                except:
                                    continue
                    # 模式二：MM/DD
                    i = 0
                    while i < len(texts) - 1:
                        t = texts[i]
                        if re.match(r"^\d{2}/\d{2}$", t):
                            mo, dy = int(t[:2]), int(t[3:])
                            yr = cur_year if mo <= today.month else cur_year - 1
                            d = f"{yr}-{mo:02d}-{dy:02d}"
                            try:
                                v = float(texts[i+1].replace(",", ""))
                                if 0.5 < v < 100000:
                                    nav_dict[d] = v
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
# 主要更新函式（供 main.py 呼叫）
# ==========================================
def run_fund_nav_update(line_push_fn=None):
    """
    更新所有基金淨值到 Google Drive
    line_push_fn: 可選，傳入 LINE 推播函式 fn(user_id, message)
    回傳: (updated筆數, skipped檔數, failed檔數)
    """
    print(f"📅 基金淨值更新開始：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    client = get_client()
    all_sheets = get_all_sheets(FOLDER_ID)

    updated = 0
    skipped = 0
    failed_list = []

    for sheet_name, info in FUND_DB.items():
        fund_name = info["name"]
        moneydj_ticker = info["moneydj"]
        print(f"📊 {fund_name}（{sheet_name}）")

        sheet_id = all_sheets.get(sheet_name)
        if not sheet_id:
            print(f"  ⚠️  找不到試算表，跳過")
            failed_list.append(fund_name)
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
            nav_dict = fetch_nav_history(moneydj_ticker)

            if not nav_dict:
                print(f"  ❌ 無法取得淨值")
                failed_list.append(fund_name)
                continue

            print(f"  🌐 MoneyDJ 抓到 {len(nav_dict)} 筆（{min(nav_dict.keys())} ～ {max(nav_dict.keys())}）")

            new_rows = sorted([
                [d, nav_dict[d]] for d in nav_dict if d not in existing_dates
            ])

            if not new_rows:
                print(f"  ✅ 已是最新")
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
            failed_list.append(fund_name)

    failed = len(failed_list)
    print(f"\n✅ 新增：{updated} 筆 | ⏭️ 已是最新：{skipped} 檔 | ❌ 失敗：{failed} 檔")

    # LINE 推播
    ts = datetime.now().strftime('%m/%d %H:%M')
    msg_lines = [
        f"📊 基金淨值更新完成 {ts}",
        f"✅ 新增：{updated} 筆",
        f"⏭️ 已是最新：{skipped} 檔",
    ]
    if failed_list:
        msg_lines.append(f"❌ 失敗：{', '.join(failed_list)}")
    msg = "\n".join(msg_lines)

    if line_push_fn:
        try:
            user_id = os.getenv("LINE_USER_ID", "")
            if user_id:
                line_push_fn(user_id, msg)
        except Exception as e:
            print(f"⚠️ LINE 推播失敗：{e}")

    return updated, skipped, failed

# ==========================================
# 本機獨立執行（保留 LINE 推播）
# ==========================================
if __name__ == "__main__":
    def _local_line_push(user_id, msg):
        import urllib.request
        config_path = os.path.join(os.path.dirname(__file__), "line_config.json")
        if not os.path.exists(config_path):
            print("⚠️ 找不到 line_config.json，跳過推播")
            return
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        token = cfg.get("token", "")
        uid = cfg.get("user_id", user_id)
        if not token:
            return
        data = json.dumps({"to": uid, "messages": [{"type": "text", "text": msg}]}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/message/push",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"📱 LINE 推播：{resp.status}")

    run_fund_nav_update(line_push_fn=_local_line_push)
