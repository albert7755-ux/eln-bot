import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import re
from dateutil.relativedelta import relativedelta
from typing import Dict, Any, List, Tuple


# =========================
# Helpers (from your script)
# =========================

def clean_ticker_symbol(ticker):
    if pd.isna(ticker):
        return ""
    t = str(ticker).strip().upper()
    t = re.sub(r'\s+(UW|UN|UQ|UP|US)$', '', t)
    if t.endswith(" JT"):
        return t.replace(" JT", ".T")
    if t.endswith(" TT"):
        return t.replace(" TT", ".TW")
    if t.endswith(" HK"):
        return t.replace(" HK", ".HK")
    return t


def parse_ko_settings(ko_price_val):
    s = str(ko_price_val).strip()
    initial_ko = 100.0
    step_rate = 0.0
    if pd.isna(ko_price_val) or s == "":
        return initial_ko, step_rate

    match = re.search(r'^(\d+(?:\.\d+)?)', s)
    if match:
        initial_ko = float(match.group(1))

    step_match = re.search(r'[\(（].*?(\d+(?:\.\d+)?)%?\s*(?:遞減|step|less|down)', s, re.IGNORECASE)
    if step_match:
        step_rate = float(step_match.group(1))

    return initial_ko, step_rate


def parse_nc_months(ko_type_val):
    s = str(ko_type_val).upper().strip()
    if pd.isna(ko_type_val) or s == "" or s == "NAN":
        return 1
    match = re.search(r'(?:NC|LOCK|NON-CALL)\s*[:\-]?\s*(\d+)', s)
    if match:
        return int(match.group(1))
    if "DAILY" in s:
        return 1
    return 1


def is_period_end_check(ko_type_val):
    s = str(ko_type_val).upper().strip()
    return "PERIOD END" in s or "MONTHLY" in s


def calculate_maturity(row, issue_date_col, tenure_col):
    if 'MaturityDate' in row and pd.notna(row['MaturityDate']):
        return row['MaturityDate']
    issue_date = row.get(issue_date_col)
    tenure_str = str(row.get(tenure_col, ""))
    if pd.isna(issue_date) or issue_date == pd.NaT:
        return pd.NaT
    try:
        months_to_add = 0
        match_m = re.search(r'(\d+)\s*M', tenure_str, re.IGNORECASE)
        match_y = re.search(r'(\d+)\s*Y', tenure_str, re.IGNORECASE)
        if match_m:
            months_to_add = int(match_m.group(1))
        elif match_y:
            months_to_add = int(match_y.group(1)) * 12
        elif tenure_str.isdigit():
            months_to_add = int(tenure_str)
        if months_to_add > 0:
            return issue_date + relativedelta(months=months_to_add)
    except:
        pass
    return pd.NaT


def clean_percentage(val):
    if pd.isna(val) or str(val).strip() == "":
        return None
    try:
        s = str(val).replace('%', '').replace(',', '').strip()
        s = re.split(r'[\(（]', s)[0]
        return float(s)
    except:
        return None


def clean_name_str(val):
    if pd.isna(val):
        return "貴賓"
    s = str(val).strip()
    if s.lower() == 'nan' or s == "":
        return "貴賓"
    return s


def find_col_index(columns, include_keywords, exclude_keywords=None):
    for idx, col_name in enumerate(columns):
        col_str = str(col_name).strip().lower().replace(" ", "")
        if exclude_keywords:
            if any(ex in col_str for ex in exclude_keywords):
                continue
        if any(inc in col_str for inc in include_keywords):
            return idx, col_name
    return None, None


def _read_input_file(file_path: str) -> pd.DataFrame:
    try:
        df = pd.read_excel(file_path, sheet_name=0, header=0, engine='openpyxl')
    except Exception:
        df = pd.read_csv(file_path)
    df = df.dropna(how='all')
    if len(df) > 0 and df.iloc[0].astype(str).str.contains("進場價").any():
        df = df.iloc[1:].reset_index(drop=True)
    return df


def _ensure_history_df(history_close, tickers: List[str]) -> pd.DataFrame:
    # yfinance: for 1 ticker, history_close may be Series; for many, DataFrame
    if isinstance(history_close, pd.Series):
        t = tickers[0] if tickers else "T1"
        return history_close.to_frame(name=t)
    if isinstance(history_close, pd.DataFrame):
        # sometimes 1 ticker still a DataFrame with column name ticker already
        return history_close
    raise ValueError("history_close format not supported")


# =========================
# Core entry
# =========================

def calculate_from_file(file_path: str, lookback_days: int = 3, notify_ki_daily: bool = True) -> Dict[str, Any]:
    """
    Return:
      - report_text: 群組快報文字
      - admin_text: 管理員摘要（若有）
      - results_df: 監控表 DataFrame（含 KO/KI/到期狀態與 T*_Detail）
      - individual_messages: 可發給客戶的訊息清單（不在 core 發送）
    """

    real_today = datetime.now()
    today_ts = pd.Timestamp(real_today)
    lookback_date = today_ts - timedelta(days=lookback_days)

    df = _read_input_file(file_path)
    cols = df.columns.tolist()

    # 欄位定位（沿用你原本）
    id_idx, _ = find_col_index(cols, ["債券", "代號", "id", "商品代號"]) or (0, "")
    type_idx, _ = find_col_index(cols, ["商品類型", "producttype", "type"], exclude_keywords=["ko", "ki"])
    strike_idx, _ = find_col_index(cols, ["strike", "執行", "履約"])
    ko_idx, _ = find_col_index(cols, ["ko", "提前"], exclude_keywords=["strike", "執行", "ki", "type"])
    ko_type_idx, _ = find_col_index(cols, ["ko類型", "kotype"]) or find_col_index(cols, ["類型", "type"], exclude_keywords=["ki", "ko", "商品"])
    ki_idx, _ = find_col_index(cols, ["ki", "下檔"], exclude_keywords=["ko", "type"])
    ki_type_idx, _ = find_col_index(cols, ["ki類型", "kitype"])
    t1_idx, _ = find_col_index(cols, ["標的1", "ticker1"])
    trade_date_idx, _ = find_col_index(cols, ["交易日"])
    issue_date_idx, _ = find_col_index(cols, ["發行日"])
    final_date_idx, _ = find_col_index(cols, ["最終", "評價"])
    maturity_date_idx, _ = find_col_index(cols, ["到期", "maturity"])
    tenure_idx, _ = find_col_index(cols, ["天期", "term", "tenure"])
    name_idx, _ = find_col_index(cols, ["理專", "姓名", "客戶"])
    line_id_idx, _ = find_col_index(cols, ["line_id", "lineid", "lineuserid", "uid", "lind"])

    if t1_idx is None:
        raise ValueError("❌ 無法辨識「標的1」欄位，請檢查 Excel 表頭。")

    clean_df = pd.DataFrame()
    clean_df["ID"] = df.iloc[:, id_idx]
    clean_df["Name"] = df.iloc[:, name_idx].apply(clean_name_str) if name_idx is not None else "貴賓"
    clean_df["Line_ID"] = df.iloc[:, line_id_idx].astype(str).replace("nan", "").str.strip() if line_id_idx is not None else ""
    clean_df["Product_Type"] = df.iloc[:, type_idx].astype(str).fillna("FCN") if type_idx is not None else "FCN"

    clean_df["TradeDate"] = pd.to_datetime(df.iloc[:, trade_date_idx], errors="coerce") if trade_date_idx is not None else pd.NaT
    clean_df["IssueDate"] = pd.to_datetime(df.iloc[:, issue_date_idx], errors="coerce") if issue_date_idx is not None else pd.NaT
    if maturity_date_idx is not None:
        clean_df["MaturityDate"] = pd.to_datetime(df.iloc[:, maturity_date_idx], errors="coerce")
    else:
        clean_df["MaturityDate"] = pd.NaT
    clean_df["ValuationDate"] = pd.to_datetime(df.iloc[:, final_date_idx], errors="coerce") if final_date_idx is not None else pd.NaT
    clean_df["TenureStr"] = df.iloc[:, tenure_idx] if tenure_idx is not None else ""

    # 補 Maturity/Valuation
    for idx, row in clean_df.iterrows():
        if pd.isna(row["MaturityDate"]):
            calc_date = calculate_maturity(row, "IssueDate", "TenureStr")
            clean_df.at[idx, "MaturityDate"] = calc_date
            if pd.isna(row["ValuationDate"]):
                clean_df.at[idx, "ValuationDate"] = calc_date

    # KO/KI/Strike
    if ko_idx is None:
        raise ValueError("❌ 找不到 KO 欄位，請檢查表頭（ko/提前）。")
    if ki_idx is None:
        raise ValueError("❌ 找不到 KI 欄位，請檢查表頭（ki/下檔）。")

    clean_df["KO_Initial"], clean_df["KO_Step"] = zip(*df.iloc[:, ko_idx].apply(parse_ko_settings))
    clean_df["KI_Pct"] = df.iloc[:, ki_idx].apply(clean_percentage)
    clean_df["Strike_Pct"] = df.iloc[:, strike_idx].apply(clean_percentage) if strike_idx is not None else 100.0
    clean_df["KO_Type"] = df.iloc[:, ko_type_idx] if ko_type_idx is not None else "NC1"
    clean_df["KI_Type"] = df.iloc[:, ki_type_idx] if ki_type_idx is not None else "AKI"

    # 標的 1~5
    for i in range(1, 6):
        if i == 1:
            tx_idx = t1_idx
        else:
            tx_idx, _ = find_col_index(cols, [f"標的{i}"])
            if tx_idx is None:
                possible_idx = t1_idx + (i - 1) * 2
                if possible_idx < len(df.columns):
                    tx_idx = possible_idx

        if tx_idx is not None and tx_idx < len(df.columns):
            raw_ticker = df.iloc[:, tx_idx]
            clean_df[f"T{i}_Code"] = raw_ticker.apply(clean_ticker_symbol)

            if tx_idx + 1 < len(df.columns):
                sample_val = df.iloc[0, tx_idx + 1]
                try:
                    float(sample_val)
                    clean_df[f"T{i}_Initial"] = pd.to_numeric(df.iloc[:, tx_idx + 1], errors="coerce").fillna(0)
                except:
                    clean_df[f"T{i}_Initial"] = 0
            else:
                clean_df[f"T{i}_Initial"] = 0
        else:
            clean_df[f"T{i}_Code"] = ""
            clean_df[f"T{i}_Initial"] = 0

    clean_df = clean_df.dropna(subset=["ID"])

    # ===== 下載行情 =====
    min_trade_date = clean_df["TradeDate"].min()
    if pd.isna(min_trade_date):
        start_download_date = today_ts - timedelta(days=30)
    else:
        start_download_date = min_trade_date - timedelta(days=7)

    all_tickers = []
    for i in range(1, 6):
        ts = clean_df[f"T{i}_Code"].dropna().unique().tolist()
        all_tickers.extend([t for t in ts if t != ""])
    all_tickers = list(set(all_tickers))

    if not all_tickers:
        raise ValueError("❌ 找不到有效的標的代號。")

    try:
        history_close = yf.download(all_tickers, start=start_download_date, end=today_ts + timedelta(days=1))["Close"]
    except Exception as e:
        raise ValueError(f"美股連線失敗: {e}")

    history_data = _ensure_history_df(history_close, all_tickers)

    # ===== 核心計算 =====
    results = []
    individual_messages_data = []
    group_summary_lines = []
    admin_summary_list = []

    for index, row in clean_df.iterrows():
        try:
            if pd.isna(row["IssueDate"]):
                continue

            ki_thresh_val = row["KI_Pct"] if pd.notna(row["KI_Pct"]) else 60.0
            strike_thresh_val = row["Strike_Pct"] if pd.notna(row["Strike_Pct"]) else 100.0
            ko_initial_val = row["KO_Initial"]
            ko_step_val = row["KO_Step"]
            ki_thresh = ki_thresh_val / 100.0
            strike_thresh = strike_thresh_val / 100.0
            nc_months = parse_nc_months(row["KO_Type"])

            try:
                nc_end_date = row["IssueDate"] + relativedelta(months=nc_months)
            except:
                nc_end_date = row["IssueDate"]

            is_dra = "DRA" in str(row["Product_Type"]).upper()
            is_period_end = is_period_end_check(row["KO_Type"])
            is_aki = "AKI" in str(row["KI_Type"]).upper()

            assets = []
            for i in range(1, 6):
                code = row.get(f"T{i}_Code", "")
                if code == "":
                    continue

                initial = float(row.get(f"T{i}_Initial", 0) or 0)

                # 若 initial=0，嘗試用交易日後第一個收盤補上
                if initial == 0:
                    trade_date = row["TradeDate"]
                    if pd.notna(trade_date):
                        try:
                            s = history_data[code] if code in history_data.columns else None
                            if s is not None:
                                price_on_trade = s[s.index >= trade_date].dropna().head(1)
                                if not price_on_trade.empty:
                                    initial = float(price_on_trade.iloc[0])
                        except:
                            initial = 0

                if initial > 0:
                    assets.append({
                        "code": code,
                        "initial": initial,
                        "strike_price": initial * strike_thresh,
                        "locked_ko": False,
                        "hit_ki": False,
                        "perf": 0.0,
                        "price": 0.0,
                        "ko_record": "",
                        "ki_record": "",
                        "eki_risk": False,
                        "eki_fresh_breach": False,
                    })

            if not assets:
                continue

            # 今日價格 + EKI首次跌破判斷
            for asset in assets:
                try:
                    s = history_data[asset["code"]] if asset["code"] in history_data.columns else None
                    if s is None:
                        asset["price"] = 0
                        continue

                    valid_s = s[s.index <= today_ts].dropna()
                    if not valid_s.empty:
                        curr = float(valid_s.iloc[-1])
                        asset["price"] = curr
                        asset["perf"] = curr / asset["initial"]

                        # EKI（非 AKI 且非 DRA）
                        if (not is_aki) and (not is_dra):
                            if asset["perf"] < ki_thresh:
                                post_issue_data = valid_s[valid_s.index >= row["IssueDate"]]
                                if len(post_issue_data) > 1:
                                    past_data = post_issue_data.iloc[:-1]
                                    breach_threshold_price = asset["initial"] * ki_thresh
                                    asset["eki_fresh_breach"] = not (past_data < breach_threshold_price).any()
                                else:
                                    asset["eki_fresh_breach"] = True
                except:
                    asset["price"] = 0

            # KO 目前門檻
            months_passed = 0
            if pd.notna(row["IssueDate"]):
                try:
                    months_passed = (today_ts.year - row["IssueDate"].year) * 12 + today_ts.month - row["IssueDate"].month
                    if months_passed < 0:
                        months_passed = 0
                except:
                    months_passed = 0

            current_ko_pct = ko_initial_val - (ko_step_val * months_passed)
            current_ko_thresh = current_ko_pct / 100.0

            # 回測 KO 鎖定/提前出場
            product_status = "Running"
            early_redemption_date = None

            if row["IssueDate"] <= today_ts:
                backtest_data = history_data[(history_data.index >= row["IssueDate"]) & (history_data.index <= today_ts)]
                if not backtest_data.empty:
                    for date, prices in backtest_data.iterrows():
                        if product_status == "Early Redemption":
                            break

                        is_post_nc = date >= nc_end_date
                        is_obs_date = True
                        if is_period_end:
                            if date.day != row["IssueDate"].day:
                                is_obs_date = False

                        m_pass = (date.year - row["IssueDate"].year) * 12 + date.month - row["IssueDate"].month
                        if date.day < row["IssueDate"].day:
                            m_pass -= 1
                        if m_pass < 0:
                            m_pass = 0

                        day_ko_val = ko_initial_val - (ko_step_val * m_pass)
                        day_ko_thresh = day_ko_val / 100.0

                        all_locked = True
                        for asset in assets:
                            try:
                                price = float(prices[asset["code"]]) if asset["code"] in prices else float("nan")
                            except:
                                price = float("nan")

                            if pd.isna(price) or price == 0:
                                if not asset["locked_ko"]:
                                    all_locked = False
                                continue

                            perf = price / asset["initial"]
                            date_str = date.strftime("%Y/%m/%d")

                            if is_aki and perf < ki_thresh and not asset["hit_ki"]:
                                asset["hit_ki"] = True
                                asset["ki_record"] = f"@{price:.2f} ({date_str})"

                            if not asset["locked_ko"]:
                                if is_post_nc:
                                    if is_period_end and not is_obs_date:
                                        pass
                                    else:
                                        if perf >= day_ko_thresh:
                                            asset["locked_ko"] = True
                                            asset["ko_record"] = f"@{price:.2f} ({date_str})"

                            if not asset["locked_ko"]:
                                all_locked = False

                        if all_locked:
                            product_status = "Early Redemption"
                            early_redemption_date = date

            locked_list = []
            waiting_list = []
            hit_ki_list = []
            detail_cols = {}
            any_below_strike_today = False
            dra_fail_list = []
            any_eki_risk_today = False
            any_eki_fresh = False

            for i, asset in enumerate(assets):
                if asset["price"] > 0:
                    if is_aki:
                        if asset["perf"] < ki_thresh:
                            asset["hit_ki"] = True
                    else:
                        if asset["perf"] < ki_thresh:
                            asset["eki_risk"] = True
                            any_eki_risk_today = True
                            if asset["eki_fresh_breach"]:
                                any_eki_fresh = True

                    if is_dra and asset["perf"] < strike_thresh:
                        any_below_strike_today = True
                        dra_fail_list.append(asset["code"])

                if asset["locked_ko"]:
                    locked_list.append(asset["code"])
                else:
                    waiting_list.append(asset["code"])
                if asset["hit_ki"]:
                    hit_ki_list.append(asset["code"])

                p_pct = round(asset["perf"] * 100, 2) if asset["price"] > 0 else 0.0
                status_icon = "✅" if asset["locked_ko"] else ("⚠️" if asset["hit_ki"] else "")
                if asset["eki_risk"]:
                    status_icon = "📉"
                if is_dra and asset["price"] > 0:
                    status_icon += "🛑無息" if asset["perf"] < strike_thresh else "💸"

                price_display = round(asset["price"], 2) if asset["price"] > 0 else "N/A"
                initial_display = round(asset["initial"], 2)

                ko_trigger_val = asset["initial"] * current_ko_thresh

                cell_text = f"【{asset['code']}】\n原: {initial_display}\n現: {price_display}\n({p_pct}%) {status_icon}"
                cell_text += f"\n👉 KO價: {round(ko_trigger_val, 2)}"
                if asset["locked_ko"]:
                    cell_text += f"\nKO {asset['ko_record']}"
                if asset["hit_ki"]:
                    cell_text += f"\nKI {asset['ki_record']}"

                detail_cols[f"T{i+1}_Detail"] = cell_text

            hit_any_ki = any(a["hit_ki"] for a in assets)
            all_above_strike_now = all((a["perf"] >= strike_thresh if a["price"] > 0 else False) for a in assets)
            valid_assets = [a for a in assets if a["perf"] > 0]
            worst_perf = min(valid_assets, key=lambda x: x["perf"])["perf"] if valid_assets else 0

            status_msgs = []
            line_status_short = ""
            group_status_short = ""
            need_notify = False

            if today_ts < row["IssueDate"]:
                status_msgs.append("⏳ 未發行")
            elif product_status == "Early Redemption":
                status_msgs.append(f"🎉 提前出場 ({early_redemption_date.strftime('%Y-%m-%d')})")
                if early_redemption_date is not None and early_redemption_date >= lookback_date:
                    line_status_short = "🎉 恭喜！已提前出場 (KO)"
                    group_status_short = "🎉 提前出場 (KO)"
                    need_notify = True
            elif pd.notna(row["ValuationDate"]) and today_ts >= row["ValuationDate"]:
                final_hit_ki = any(a["perf"] < ki_thresh for a in assets)

                if all_above_strike_now:
                    status_msgs.append("💰 到期獲利")
                    line_status_short = "💰 到期獲利"
                elif final_hit_ki:
                    status_msgs.append("😭 到期接股")
                    line_status_short = "😭 到期接股"
                else:
                    status_msgs.append("🛡️ 到期保本")
                    line_status_short = "🛡️ 到期保本"

                if row["ValuationDate"] >= lookback_date:
                    need_notify = True
            else:
                if today_ts < nc_end_date:
                    status_msgs.append(f"🔒 NC閉鎖期 (至 {nc_end_date.strftime('%Y-%m-%d')})")
                else:
                    status_msgs.append("👀 比價中 (月月比)" if is_period_end else "👀 比價中 (Daily)")

                if ko_step_val > 0:
                    status_msgs.append(f"📉 目前KO: {current_ko_pct}%")

                if hit_any_ki:
                    status_msgs.insert(0, f"☠️ 已跌破KI ({','.join(hit_ki_list)})")
                    if notify_ki_daily:
                        line_status_short = f"⚠️ 警告：已跌破 KI ({','.join(hit_ki_list)})"
                        group_status_short = f"⚠️ 跌破 KI ({','.join(hit_ki_list)})"
                        need_notify = True
                elif any_eki_fresh:
                    status_msgs.insert(0, "📉 注意：首次跌破 EKI")
                    line_status_short = "📉 首次跌破 EKI 觀察價"
                    need_notify = True
                elif any_eki_risk_today:
                    status_msgs.insert(0, "📉 市價低於KI (EKI觀察中)")

                if is_dra:
                    if any_below_strike_today:
                        status_msgs.append(f"🛑 DRA暫停計息 ({','.join(dra_fail_list)})")
                        if notify_ki_daily:
                            if not line_status_short:
                                line_status_short = f"🛑 DRA 暫停計息 ({','.join(dra_fail_list)} 跌破)"
                            else:
                                line_status_short += " & 🛑 DRA 暫停"
                            if not group_status_short:
                                group_status_short = "🛑 DRA 暫停計息"
                            need_notify = True
                    else:
                        status_msgs.append("💸 DRA計息中")

            final_status = "\n".join(status_msgs)

            if line_status_short:
                admin_summary_list.append(f"● {row['ID']} ({row['Name']}): {line_status_short}")
            if group_status_short:
                group_summary_lines.append(f"● {row['ID']}: {group_status_short}")

            line_ids = [x.strip() for x in re.split(r"[;,，]", str(row.get("Line_ID", ""))) if x.strip()]
            mat_date_str = row["MaturityDate"].strftime("%Y-%m-%d") if pd.notna(row["MaturityDate"]) else "-"

            asset_detail_str = "\n".join([v for _, v in detail_cols.items()]) + "\n"

            common_msg_body = (
                f"Hi {row['Name']} 您好，\n"
                f"您的結構型商品 {row['ID']} ({row['Product_Type']}) 最新狀態：\n\n"
                f"【{line_status_short}】\n\n"
                f"{asset_detail_str}"
                f"📅 到期日: {mat_date_str}\n"
                f"------------------\n"
                f"貼心通知"
            )

            copy_text_body = (
                f"【商品查詢】{row['ID']}\n"
                f"----------------\n"
                f"📅 交易日: {row['TradeDate'].strftime('%Y/%m/%d') if pd.notna(row['TradeDate']) else '-'}\n"
                f"📅 到期日: {mat_date_str}\n"
                f"🔒 閉鎖期: {nc_end_date.strftime('%Y/%m/%d')}\n"
                f"👀 比價日: {'每月' if is_period_end else '每日'}\n"
                f"📊 標的表現:\n"
            )
            for i, asset in enumerate(assets):
                this_asset_ko = asset["initial"] * current_ko_thresh
                copy_text_body += f"{i+1}. {asset['code']}: {round(asset['price'],2)} ({round(asset['perf']*100,2)}%) ➤ KO: {round(this_asset_ko, 2)}\n"
            copy_text_body += f"🚀 目前狀態: {product_status if product_status != 'Running' else ('KI觀察中' if any_eki_risk_today else '正常比價')}"

            if need_notify and line_status_short and line_ids:
                for uid in line_ids:
                    if uid.startswith("U") or uid.startswith("C"):
                        individual_messages_data.append({
                            "send": False,
                            "name": row["Name"],
                            "id": row["ID"],
                            "status": line_status_short,
                            "target": uid,
                            "msg": common_msg_body
                        })

            row_res = {
                "債券代號": row["ID"],
                "Name": row["Name"],
                "Type": row["Product_Type"],
                "狀態": final_status,
                "最差表現": f"{round(worst_perf*100, 2)}%",
                "交易日": row["TradeDate"].strftime("%Y-%m-%d") if pd.notna(row["TradeDate"]) else "-",
                "NC月份": f"{nc_months}M",
                "KO設定": f"{ko_initial_val}% (-{ko_step_val}%)" if ko_step_val > 0 else f"{ko_initial_val}%",
                "SearchText": copy_text_body,
                "Line_ID_Raw": row.get("Line_ID", "")
            }
            row_res.update(detail_cols)
            results.append(row_res)

        except Exception as e:
            print(f"Skipping row {index}: {e}")
            continue

    results_df = pd.DataFrame(results)

    report_text = f"【ELN 戰情快報】\n📅 {real_today.strftime('%Y/%m/%d')}\n----------------\n"
    if group_summary_lines:
        report_text += "🔥 重點關注：\n" + "\n".join(group_summary_lines)
    else:
        report_text += "🍵 今日市場平穩，無特殊觸價事件。"
    report_text += "\n\n(以上資訊僅供參考，詳細報價請見監控表)"

    admin_text = ""
    if admin_summary_list:
        admin_text = f"【ELN 戰情快報 (Admin)】\n📅 {real_today.strftime('%Y/%m/%d')}\n----------------\n" + "\n".join(admin_summary_list)

    return {
        "report_text": report_text,
        "admin_text": admin_text,
        "results_df": results_df,
        "individual_messages": individual_messages_data
    }
