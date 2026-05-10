"""
債券更新腳本：
1. 根據 bond_list_update.xlsx 更新 Google Sheets 的 bond_master
2. 把 bond_csv 資料夾裡的 CSV 補進對應的 Google Sheets
使用方法：python update_bonds.py <bond_list_update.xlsx路徑> <bond_csv資料夾路徑>
例：python update_bonds.py bond_list_update.xlsx bond_csv
"""

import sys
import os
import csv
import time
import requests
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request

# ==========================================
# 設定
# ==========================================
CREDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
BOND_DRIVE_FOLDER_ID = "1k0RxJn5KKCTWdTEDZqq0Q5hnfwkuPgGK"
BOND_MASTER_NAME = " bond_master"  # Drive 裡的名稱（有空格）

# ==========================================
# Google 連線
# ==========================================
def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(CREDS_PATH, scopes=scopes)
    return gspread.authorize(creds)

def get_drive_files():
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(CREDS_PATH, scopes=scopes)
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    params = {
        "q": f"'{BOND_DRIVE_FOLDER_ID}' in parents and trashed=false",
        "fields": "files(id, name)",
        "pageSize": 500,
    }
    resp = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        headers=headers, params=params
    )
    return {f["name"]: f["id"] for f in resp.json().get("files", [])}

# ==========================================
# 建立新 Google Sheets
# ==========================================
def create_new_sheet(creds_path, folder_id, sheet_name):
    """在 Google Drive 指定資料夾建立新的試算表"""
    scopes = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    creds.refresh(Request())
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json"
    }
    # 建立新試算表
    body = {
        "name": sheet_name,
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [folder_id]
    }
    resp = requests.post(
        "https://www.googleapis.com/drive/v3/files",
        headers=headers,
        json=body
    )
    if resp.status_code == 200:
        file_id = resp.json().get("id")
        print(f"  ✅ 建立新試算表：{sheet_name}（{file_id}）")
        return file_id
    else:
        print(f"  ❌ 建立失敗：{resp.text}")
        return None

# ==========================================
# 讀取 TradingView CSV
# ==========================================
def read_tv_csv(csv_path):
    tv_data = {}
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
                            tv_data[date_str] = close_val
                    except:
                        continue
    except Exception as e:
        print(f"  ❌ 讀取 CSV 失敗：{e}")
    return tv_data

# ==========================================
# 找對應 Drive 檔案
# ==========================================
def find_sheet(drive_files, sheet_name):
    """根據檔名找 Google Sheets file_id，支援模糊比對"""
    # 完全一致
    if sheet_name in drive_files:
        return drive_files[sheet_name]
    # 加上 ", 1D"
    if f"{sheet_name}, 1D" in drive_files:
        return drive_files[f"{sheet_name}, 1D"]
    # 去掉 ", 1D"
    base = sheet_name.replace(', 1D', '').replace('_1D', '').strip()
    if base in drive_files:
        return drive_files[base]
    # 模糊比對（包含關係）
    for name, fid in drive_files.items():
        if base in name or name in base:
            return fid
    return None

# ==========================================
# 主程式
# ==========================================
def main():
    if len(sys.argv) < 3:
        print("使用方法：python update_bonds.py <bond_list_update.xlsx> <bond_csv資料夾>")
        return

    xlsx_path = sys.argv[1]
    csv_folder = sys.argv[2]

    if not os.path.exists(xlsx_path):
        print(f"❌ 找不到 Excel：{xlsx_path}")
        return
    if not os.path.exists(csv_folder):
        print(f"❌ 找不到資料夾：{csv_folder}")
        return

    # 讀取 bond_list_update.xlsx
    print("📂 讀取 bond_list_update.xlsx...")
    df = pd.read_excel(xlsx_path, header=1)
    df.columns = ['檔名', '交易所', 'ISIN', '債券名稱', '發行機構']
    df = df.dropna(subset=['檔名'])
    print(f"✅ 共 {len(df)} 筆債券\n")

    # 連線 Google
    print("📊 連線 Google Drive...")
    client = get_client()
    drive_files = get_drive_files()
    print(f"✅ Drive 裡有 {len(drive_files)} 個檔案\n")

    # ==========================================
    # 步驟一：更新 bond_master
    # ==========================================
    print("=" * 50)
    print("步驟一：更新 bond_master")
    print("=" * 50)

    master_id = drive_files.get(BOND_MASTER_NAME) or drive_files.get("bond_master")
    if not master_id:
        for name, fid in drive_files.items():
            if "master" in name.lower():
                master_id = fid
                break

    if master_id:
        try:
            sh = client.open_by_key(master_id)
            ws = sh.get_worksheet(0)
            # 清除舊資料並重寫
            ws.clear()
            headers = ['檔名', '交易所', 'ISIN/代碼', '債券名稱', '發行機構']
            all_rows = [headers]
            for _, row in df.iterrows():
                all_rows.append([
                    str(row['檔名']).strip(),
                    str(row['交易所']).strip(),
                    str(row['ISIN']).strip(),
                    str(row['債券名稱']).strip(),
                    str(row['發行機構']).strip(),
                ])
            ws.update('A1', all_rows)
            print(f"✅ bond_master 更新完成（{len(all_rows)-1} 筆）\n")
        except Exception as e:
            print(f"❌ bond_master 更新失敗：{e}\n")
    else:
        print("⚠️ 找不到 bond_master，跳過更新\n")

    # ==========================================
    # 步驟二：補齊 CSV 數據
    # ==========================================
    print("=" * 50)
    print("步驟二：補齊 CSV 數據")
    print("=" * 50)

    # 列出 bond_csv 裡的所有 CSV
    csv_files = [f for f in os.listdir(csv_folder) if f.endswith('.csv')]
    print(f"📂 bond_csv 裡有 {len(csv_files)} 個 CSV 檔案\n")

    # 建立 CSV 檔名 → bond_list 檔名 的對應
    # bond_list 的檔名 → Drive 的 Sheets ID
    updated_total = 0
    skipped_total = 0
    failed = []

    for _, bond_row in df.iterrows():
        bond_filename = str(bond_row['檔名']).strip()
        bond_name = str(bond_row['債券名稱']).strip()

        # 找對應的 CSV 檔案（可能有 ", 1D" 或沒有）
        matched_csv = None
        for csv_file in csv_files:
            csv_base = csv_file.replace('.csv', '').strip()
            bond_base = bond_filename.replace(', 1D', '').strip()
            if csv_base == bond_filename or csv_base == bond_base:
                matched_csv = csv_file
                break
            # 模糊比對
            if bond_base.lower() in csv_base.lower() or csv_base.lower() in bond_base.lower():
                matched_csv = csv_file
                break

        if not matched_csv:
            # 沒有對應 CSV，跳過
            continue

        csv_path = os.path.join(csv_folder, matched_csv)
        print(f"📊 {bond_name}（{bond_filename}）")
        print(f"  CSV：{matched_csv}")

        # 讀取 CSV
        tv_data = read_tv_csv(csv_path)
        if not tv_data:
            print(f"  ❌ CSV 讀取失敗")
            failed.append(bond_filename)
            continue

        print(f"  數據：{len(tv_data)} 筆（{min(tv_data.keys())} ~ {max(tv_data.keys())}）")

        # 找對應 Google Sheets
        file_id = find_sheet(drive_files, bond_filename)
        if not file_id:
            # 自動建立新試算表
            print(f"  🆕 找不到對應試算表，自動建立...")
            new_name = f"{bond_filename}, 1D" if not bond_filename.endswith(", 1D") else bond_filename
            file_id = create_new_sheet(CREDS_PATH, BOND_DRIVE_FOLDER_ID, new_name)
            if not file_id:
                failed.append(bond_filename)
                continue
            # 更新 drive_files 快取
            drive_files[new_name] = file_id
            # 寫入標題行
            time.sleep(1)
            try:
                sh = client.open_by_key(file_id)
                ws = sh.get_worksheet(0)
                ws.update('A1', [['time', 'close']])
            except Exception as e:
                print(f"  ⚠️ 寫入標題失敗：{e}")

        # 讀取現有數據
        time.sleep(1.5)
        try:
            sh = client.open_by_key(file_id)
            ws = sh.get_worksheet(0)
            all_vals = ws.get_all_values()
            existing_dates = set()
            for row in all_vals[1:]:
                if row and row[0].strip():
                    existing_dates.add(row[0].strip())
            last_date = max(existing_dates) if existing_dates else "無"
            print(f"  Sheets：{len(existing_dates)} 筆，最後日期：{last_date}")
        except Exception as e:
            if "429" in str(e):
                print(f"  ⏳ API 超頻，等待 30 秒...")
                time.sleep(30)
                try:
                    all_vals = ws.get_all_values()
                    existing_dates = set(row[0].strip() for row in all_vals[1:] if row and row[0].strip())
                except:
                    print(f"  ❌ 重試失敗")
                    failed.append(bond_filename)
                    continue
            else:
                print(f"  ❌ 讀取失敗：{e}")
                failed.append(bond_filename)
                continue

        # 找缺失數據
        missing = {d: p for d, p in tv_data.items() if d not in existing_dates}
        if not missing:
            print(f"  ✅ 已是最新，跳過")
            skipped_total += 1
            print()
            continue

        sorted_missing = sorted(missing.items())
        print(f"  📝 補齊 {len(sorted_missing)} 筆：{sorted_missing[0][0]} ~ {sorted_missing[-1][0]}")

        try:
            rows_to_add = [[d, p] for d, p in sorted_missing]
            ws.append_rows(rows_to_add)
            print(f"  ✅ 成功！")
            updated_total += len(sorted_missing)
        except Exception as e:
            print(f"  ❌ 寫入失敗：{e}")
            failed.append(bond_filename)

        print()

    print("=" * 50)
    print(f"✅ bond_master 已更新")
    print(f"✅ 成功補齊：{updated_total} 筆")
    print(f"⏭️ 已是最新：{skipped_total} 檔")
    print(f"❌ 失敗：{len(failed)} 筆")
    if failed:
        print(f"失敗清單：")
        for f in failed:
            print(f"  - {f}")
    print("=" * 50)

if __name__ == "__main__":
    main()
