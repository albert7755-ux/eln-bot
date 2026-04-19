# ==============================
# 以下兩段程式碼要加入 main.py
# ==============================

# ── 片段一：加在 job_article_reminder() 函式後面 ──

def job_fund_nav_update():
    """每天早上9點更新基金淨值（MoneyDJ）"""
    now = datetime.now(TZ_TAIPEI_PYTZ)
    if now.weekday() >= 5:
        write_job_log("基金淨值更新", "skipped", "週末跳過")
        return
    write_job_log("基金淨值更新", "started", now.strftime('%Y-%m-%d %H:%M'))
    try:
        import requests as _req
        from bs4 import BeautifulSoup
        import gspread
        from google.oauth2.service_account import Credentials as _Creds
        from google.auth.transport.requests import Request as _Request
        import re as _re
        import time as _time

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
        }

        # Google 連線（從環境變數讀取）
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
        if not creds_json:
            # 備用：從 GOOGLE_CREDENTIALS 讀取（龍蝦Bot原本的設定）
            creds_json = os.getenv("GOOGLE_CREDENTIALS", "")
        if not creds_json:
            write_job_log("基金淨值更新", "error", "缺少 Google 憑證環境變數")
            return

        creds_dict = json.loads(creds_json)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly"
        ]
        creds = _Creds.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)

        # 取得試算表清單
        drive_creds = _Creds.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/drive.readonly"])
        drive_creds.refresh(_Request())
        hdrs_auth = {"Authorization": f"Bearer {drive_creds.token}"}
        params = {
            "q": f"'{FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
            "fields": "files(id, name)", "pageSize": 200,
        }
        r = _req.get("https://www.googleapis.com/drive/v3/files", headers=hdrs_auth, params=params)
        all_sheets = {f["name"]: f["id"] for f in r.json().get("files", [])}

        def fetch_nav(ticker):
            urls = [
                f"https://www.moneydj.com/funddj/ya/yp010001.djhtm?a={ticker}",
                f"https://www.moneydj.com/funddj/ya/yp010000.djhtm?a={ticker}",
            ]
            req_hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "zh-TW,zh;q=0.9"}
            today = datetime.now(TZ_TAIPEI_PYTZ).date()
            current_year = today.year
            for url in urls:
                try:
                    resp = _req.get(url, headers=req_hdrs, timeout=20, verify=False)
                    resp.encoding = "big5"
                    from bs4 import BeautifulSoup as BS
                    soup = BS(resp.text, "html.parser")
                    nav_dict = {}
                    for table in soup.find_all("table"):
                        for row in table.find_all("tr"):
                            texts = [c.get_text(strip=True) for c in row.find_all("td")]
                            if len(texts) < 2:
                                continue
                            # YYYY/MM/DD 格式
                            for i, t in enumerate(texts):
                                if _re.match(r"20\d\d/\d{2}/\d{2}$", t):
                                    d = t.replace("/", "-")
                                    for j in range(i+1, min(i+4, len(texts))):
                                        try:
                                            v = float(texts[j].replace(",", ""))
                                            if 0.5 < v < 100000:
                                                nav_dict[d] = v
                                                break
                                        except:
                                            continue
                            # MM/DD 格式
                            i = 0
                            while i < len(texts) - 1:
                                t = texts[i]
                                if _re.match(r"^\d{2}/\d{2}$", t):
                                    mo, dy = int(t[:2]), int(t[3:])
                                    yr = current_year if mo <= today.month else current_year - 1
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
                except:
                    continue
            return {}

        updated = 0
        skipped = 0
        failed_list = []

        for sheet_name, info in FUND_DB.items():
            sheet_id = all_sheets.get(sheet_name)
            if not sheet_id:
                failed_list.append(info["name"])
                continue
            try:
                sh = gc.open_by_key(sheet_id)
                ws = sh.get_worksheet(0)
                existing_dates = set()
                for row in ws.get_all_values()[1:]:
                    if row and row[0]:
                        existing_dates.add(row[0].strip())

                _time.sleep(2)
                nav_dict = fetch_nav(info["moneydj"])
                if not nav_dict:
                    failed_list.append(info["name"])
                    continue

                new_rows = sorted([[d, v] for d, v in nav_dict.items() if d not in existing_dates])
                if not new_rows:
                    skipped += 1
                    continue

                for row in new_rows:
                    _time.sleep(0.3)
                    ws.append_row(row)
                updated += len(new_rows)

            except Exception as e:
                failed_list.append(f"{info['name']}({str(e)[:30]})")

        # 推播結果
        user_id = os.getenv("LINE_USER_ID", "")
        msg = f"📊 基金淨值更新完成\n✅ 新增：{updated} 筆\n⏭️ 已是最新：{skipped} 檔"
        if failed_list:
            msg += f"\n❌ 失敗：{', '.join(failed_list[:5])}"
        if user_id:
            line_bot_api.push_message(user_id, TextSendMessage(text=msg))

        write_job_log("基金淨值更新", "success", f"新增{updated}筆，跳過{skipped}，失敗{len(failed_list)}")

    except Exception as e:
        write_job_log("基金淨值更新", "error", str(e)[:200])
        user_id = os.getenv("LINE_USER_ID", "")
        if user_id:
            line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ 基金淨值更新失敗：{str(e)[:100]}"))


# ── 片段二：加在 start_scheduler() 函式裡，其他排程的後面 ──
# 找到這行：
#   scheduler.add_job(job_article_reminder, ...)
# 在它後面加上：

#   scheduler.add_job(
#       job_fund_nav_update,
#       CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=TZ_TAIPEI_PYTZ),
#       id="fund_nav_update",
#       name="基金淨值更新"
#   )
