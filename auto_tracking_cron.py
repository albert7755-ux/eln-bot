"""
auto_tracking_cron.py
每天早上由 Render Cron Job 執行
1. 從 Supabase Storage 下載最新 Excel
2. 執行 ELN 自動追蹤計算
3. 更新資料庫（可同時寫入個人與群組）
4. 推播結果到 LINE
5. 把理專個別通知放進 pending，等待 /send /skip 確認
"""

import os
import time
import traceback
from datetime import datetime, timezone, timedelta

from linebot import LineBotApi
from linebot.models import TextSendMessage
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, DBAPIError, TimeoutError as SATimeoutError

import json

from autotracking_core import calculate_from_file, load_products_from_db, calculate_from_records
try:
    from eln_storage import download_latest_eln
except Exception:
    download_latest_eln = None

TZ_TAIPEI = timezone(timedelta(hours=8))


# ── 重試機制：資料庫連線逾時/瞬斷時，等幾秒再試，最多重試 2 次 ──
def with_db_retry(fn, *args, retries: int = 2, base_delay: float = 3.0, label: str = "", **kwargs):
    """執行 fn(*args, **kwargs)，遇到資料庫連線類錯誤時自動重試。
    label 只用於印在 log 裡方便看是哪一步在重試。"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except (OperationalError, DBAPIError, SATimeoutError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                delay = base_delay * (attempt + 1)
                print(f"[DB RETRY] {label} 第{attempt + 1}次失敗（{type(e).__name__}），{delay:.0f}秒後重試...")
                time.sleep(delay)
            else:
                print(f"[DB RETRY] {label} 重試{retries}次後仍失敗，放棄")
    raise last_err


def get_env(key: str, default: str = "") -> str:
    val = os.environ.get(key, default)
    if val is None:
        val = ""
    val = str(val).strip()
    if not val and default == "":
        raise RuntimeError(f"Missing env var: {key}")
    return val


def push_long_message(bot_api: LineBotApi, target_id: str, text_msg: str, max_len: int = 4800):
    if not text_msg:
        return
    text_msg = str(text_msg)
    while text_msg:
        chunk = text_msg[:max_len]
        text_msg = text_msg[max_len:]
        bot_api.push_message(target_id, TextSendMessage(text=chunk))


def normalize_db_url(db_url: str) -> str:
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql+psycopg://", 1)
    if db_url.startswith("postgresql://"):
        return db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return db_url


def save_result_to_db_multi(engine, entries: list):
    """entries = [(chat_key, summary, top5_lines, detail_map, agent_name_map), ...]
    把個人 + 群組（原本各自開一次交易）合併成同一次資料庫交易，
    減少 COMMIT 次數（減少 Disk IO 次數）"""
    with engine.begin() as conn:
        for chat_key, summary, top5_lines, detail_map, agent_name_map in entries:
            _save_result_in_conn(conn, chat_key, summary, top5_lines, detail_map, agent_name_map)


def save_result_to_db(engine, chat_key: str, summary: str, top5_lines: list, detail_map: dict, agent_name_map: dict):
    """單一 chat_key 版本（保留給其他地方相容呼叫用）"""
    with engine.begin() as conn:
        _save_result_in_conn(conn, chat_key, summary, top5_lines, detail_map, agent_name_map)


def _save_result_in_conn(conn, chat_key: str, summary: str, top5_lines: list, detail_map: dict, agent_name_map: dict):
        conn.execute(text("""
        INSERT INTO eln_last_report(chat_key, summary, updated_at)
        VALUES (:k, :s, NOW())
        ON CONFLICT (chat_key) DO UPDATE
        SET summary=:s, updated_at=NOW()
        """), {"k": chat_key, "s": summary})

        conn.execute(text("DELETE FROM eln_top5 WHERE chat_key=:k"), {"k": chat_key})
        if top5_lines:
            top5_params = [
                {"k": chat_key, "n": i, "t": line}
                for i, line in enumerate(top5_lines, start=1)
            ]
            conn.execute(text("""
            INSERT INTO eln_top5(chat_key, line_no, text_line, updated_at)
            VALUES (:k, :n, :t, NOW())
            """), top5_params)

        conn.execute(text("DELETE FROM eln_detail WHERE chat_key=:k"), {"k": chat_key})
        if detail_map:
            detail_params = [
                {"k": chat_key, "b": bond_id, "d": detail, "a": agent_name_map.get(bond_id, "-")}
                for bond_id, detail in detail_map.items()
            ]
            conn.execute(text("""
            INSERT INTO eln_detail(chat_key, bond_id, detail, agent_name, updated_at)
            VALUES (:k, :b, :d, :a, NOW())
            """), detail_params)


def save_job_log(engine, job_name: str, status: str, detail: str = ""):
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_job_log (
            id BIGSERIAL PRIMARY KEY,
            job_name TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """))
        conn.execute(text("""
        INSERT INTO eln_job_log(job_name, status, detail)
        VALUES (:j, :s, :d)
        """), {"j": job_name, "s": status, "d": detail[:4000]})


# ==============================
# 登錄表每日備份（存在資料庫的 eln_products_backup 表，保留最近 7 天）
# ==============================
BACKUP_KEEP_DAYS = 7


def save_products_backup(engine, records: list):
    """每天讀取登錄表成功後存一份備份，並刪掉超過 7 天的舊備份"""
    data = json.dumps(records, ensure_ascii=False, default=str)
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_products_backup (
            id BIGSERIAL PRIMARY KEY,
            row_count INT NOT NULL DEFAULT 0,
            data TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """))
        conn.execute(text("INSERT INTO eln_products_backup(row_count, data) VALUES (:n, :d)"),
                     {"n": len(records), "d": data})
        conn.execute(text(f"DELETE FROM eln_products_backup WHERE created_at < NOW() - INTERVAL '{BACKUP_KEEP_DAYS} days'"))
    print(f"[BACKUP] 已備份 {len(records)} 檔商品（保留最近 {BACKUP_KEEP_DAYS} 天）")


def load_latest_backup(engine):
    """讀最近一次的備份，回傳 (records, 備份時間文字)"""
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT data, created_at FROM eln_products_backup
            ORDER BY created_at DESC LIMIT 1
        """)).fetchone()
    if not row:
        raise ValueError("還沒有任何備份")
    records = json.loads(row[0])
    when = row[1].astimezone(TZ_TAIPEI).strftime("%m/%d %H:%M") if row[1] else "?"
    return records, when


def build_skipped_text(skipped: list) -> str:
    """把沒算到的商品整理成通知（只列前 20 筆）"""
    if not skipped:
        return ""
    lines = [f"⚠️ 以下 {len(skipped)} 檔商品今天沒有計算（/list 查不到），請修正登錄資料後 /runnow："]
    for s in skipped[:20]:
        lines.append(f"• {s.get('id', '?')}（{s.get('name', '') or '-'}）：{s.get('reason', '')}")
    if len(skipped) > 20:
        lines.append(f"…另外還有 {len(skipped) - 20} 檔")
    return "\n".join(lines)


def ensure_pending_table(engine):
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_pending_notifications (
            id BIGSERIAL PRIMARY KEY,
            chat_key TEXT NOT NULL,
            target_id TEXT NOT NULL,
            agent_name TEXT NOT NULL DEFAULT '',
            bond_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            msg TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """))


def build_result(output: dict):
    df = output.get("results_df")
    report = output.get("report_text", "") or ""

    top5_lines = []
    detail_map = {}
    agent_name_map = {}

    if df is not None and not df.empty:
        for _, r in df.head(5).iterrows():
            try:
                status_first = str(r["狀態"]).splitlines()[0] if "狀態" in r.index else ""
            except Exception:
                status_first = ""
            bond_id = str(r["債券代號"]) if "債券代號" in r.index else "-"
            ptype = str(r["Type"]) if "Type" in r.index else "-"
            top5_lines.append(f"● {bond_id} {ptype}｜{status_first}")

        for _, r in df.iterrows():
            _id = str(r["債券代號"]).strip() if "債券代號" in r.index else ""
            if not _id or _id == "nan":
                continue

            t_details = []
            for c in df.columns:
                if str(c).endswith("_Detail"):
                    v = r[c] if c in r.index else ""
                    if v:
                        t_details.append(str(v))

            agent = str(r["Name"] if "Name" in r.index else "-").strip() or "-"
            agent_name_map[_id] = agent

            # ── 修正：加入最終評價日、到期日、Coupon ──
            detail_text = (
                f"【商品】{_id}\n"
                f"類型: {r['Type'] if 'Type' in r.index else '-'}\n"
                f"理專: {agent}\n"
                f"交易日: {r['交易日'] if '交易日' in r.index else '-'}\n"
                f"最終評價日: {r['最終評價日'] if '最終評價日' in r.index else '-'}\n"
                f"到期日: {r['到期日'] if '到期日' in r.index else '-'}\n"
                f"Coupon: {r['Coupon'] if 'Coupon' in r.index else '-'}\n"
                f"KO設定: {r['KO設定'] if 'KO設定' in r.index else '-'}\n"
                f"KI類型: {r['KI類型'] if 'KI類型' in r.index else '-'}\n"
                f"執行價格: {r['執行價格'] if '執行價格' in r.index else '-'}\n"
                f"最差表現: {r['最差表現'] if '最差表現' in r.index else '-'}\n"
                f"----------------\n"
                f"{r['狀態'] if '狀態' in r.index else ''}\n"
                f"----------------\n"
                + ("\n\n".join(t_details) if t_details else "")
            )
            detail_map[_id] = detail_text

    summary = report
    if top5_lines:
        summary += "\n\n【前5筆摘要】\n" + "\n".join(top5_lines)

    return summary, top5_lines, detail_map, agent_name_map


def save_pending_notifications(engine, chat_key: str, individual_messages: list):
    ensure_pending_table(engine)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM eln_pending_notifications WHERE chat_key=:k"), {"k": chat_key})

        if individual_messages:
            params = [
                {
                    "k": chat_key,
                    "t": msg.get("target", ""),
                    "n": msg.get("name", ""),
                    "b": msg.get("id", ""),
                    "s": msg.get("status", ""),
                    "m": msg.get("msg", "")
                }
                for msg in individual_messages
            ]
            conn.execute(text("""
                INSERT INTO eln_pending_notifications
                    (chat_key, target_id, agent_name, bond_id, status, msg)
                VALUES (:k, :t, :n, :b, :s, :m)
            """), params)


def build_pending_text(individual_messages: list) -> str:
    if not individual_messages:
        return "✅ 今日無需通知理專"

    lines = [f"📋 今日 ELN 通知待確認（{len(individual_messages)}筆）\n"]
    for i, msg in enumerate(individual_messages, start=1):
        lines.append(
            f"{i}️⃣ {msg.get('name', '')} | {msg.get('id', '')} | {msg.get('status', '')}\n"
            f"  /send {i} 發送　/skip {i} 略過"
        )
    lines.append("\n/send all 全部發送　/skip all 全部略過")
    lines.append("/send list 查看待確認清單")
    return "\n\n".join(lines)


# ── 自動推播的重要事件關鍵字（其他保留手動 /send）──
# 「首次跌破」涵蓋：📉 首次跌破 EKI 觀察價、🚨 首次跌破 AKI（都只在第一次跌破那天出現）
AUTO_PUSH_KEYWORDS = ["提前出場", "到期獲利", "到期接股", "到期保本", "首次跌破"]


def is_auto_push_event(status: str) -> bool:
    s = str(status or "")
    return any(k in s for k in AUTO_PUSH_KEYWORDS)


def load_agent_line_ids(engine) -> dict:
    """讀取理專 LINE ID 對照表"""
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("SELECT agent_name, line_id FROM agent_line_ids")).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception as e:
        print(f"[WARN] 讀取 agent_line_ids 失敗: {e}")
        return {}


def resolve_targets(msg: dict, agent_ids: dict) -> list:
    """回傳這筆商品所有理專的 LINE ID（多理專都要發）
    先用理專名查對照表，查不到才用商品的 line_id 補"""
    import re as _re
    targets = []
    name_raw = (msg.get("name", "") or "").strip()
    names = [n.strip() for n in _re.split(r"[,，、/]", name_raw) if n.strip()]
    for n in names:
        if n in agent_ids and agent_ids[n] not in targets:
            targets.append(agent_ids[n])
    # 對照表完全查不到時，才退回用商品自己的 line_id
    if not targets:
        t = (msg.get("target", "") or "").strip()
        if t:
            targets.append(t)
    return targets


def auto_push_important(line_bot_api, individual_messages: list, agent_ids: dict = None):
    """自動推播重要事件給所有相關理專（有 LINE ID 才發）
    回傳 (已自動發送清單, 剩餘待確認清單)"""
    agent_ids = agent_ids or {}
    auto_sent = []
    remaining = []
    sent_pairs = set()  # (商品代號, LINE ID)：同一檔商品同一人只發一次
    for msg in individual_messages:
        status = msg.get("status", "")
        if not is_auto_push_event(status):
            remaining.append(msg)
            continue
        targets = resolve_targets(msg, agent_ids)
        if not targets:
            print(f"[AUTO PUSH SKIP] {msg.get('name','')} | {msg.get('id','')} | 查無 LINE ID")
            remaining.append(msg)
            continue
        bond_key = (msg.get("id", "") or "").strip()
        new_targets = [t for t in targets if (bond_key, t) not in sent_pairs]
        if not new_targets:
            # 這檔商品已發過給這些人（多理專造成的重複筆），不再重發
            print(f"[AUTO PUSH DEDUP] {msg.get('name','')} | {bond_key} | 已發過，跳過重複")
            auto_sent.append(msg)
            continue
        ok_any = False
        for t in new_targets:
            try:
                push_long_message(line_bot_api, t, msg.get("msg", ""))
                ok_any = True
                sent_pairs.add((bond_key, t))
                print(f"[AUTO PUSH] {msg.get('name','')} | {msg.get('id','')} | {status} -> {t[:12]}...")
            except Exception as e:
                print(f"[AUTO PUSH FAIL] {msg.get('name','')} | {t[:12]}... | {e}")
        if ok_any:
            auto_sent.append(msg)
        else:
            remaining.append(msg)
    return auto_sent, remaining


def build_auto_sent_text(auto_sent: list) -> str:
    if not auto_sent:
        return ""
    lines = [f"📤 已自動推播給理專（{len(auto_sent)}筆）"]
    for msg in auto_sent:
        lines.append(f"   ✅ {msg.get('name','')} | {msg.get('id','')} | {msg.get('status','')}")
    return "\n".join(lines)


def main():
    now = datetime.now(TZ_TAIPEI)
    print(f"[{now.strftime('%Y/%m/%d %H:%M')}] auto_tracking_cron starting...")

    line_token = get_env("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = get_env("LINE_USER_ID")
    db_url = normalize_db_url(get_env("DATABASE_URL"))
    group_id = os.environ.get("ELN_GROUP_ID", "").strip()
    push_group_summary = os.environ.get("ELN_PUSH_GROUP_SUMMARY", "true").strip().lower() == "true"

    engine = create_engine(db_url, pool_pre_ping=True)
    line_bot_api = LineBotApi(line_token)

    # ── 推播給理專要用 ELN Autotracking bot 的 token（user ID 綁定該 bot）──
    agent_token = os.environ.get("AGENT_LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    agent_bot_api = LineBotApi(agent_token) if agent_token else line_bot_api
    if agent_token:
        print("[INFO] 理專推播使用 AGENT bot token")
    else:
        print("[WARN] 未設定 AGENT_LINE_CHANNEL_ACCESS_TOKEN，改用個人 bot token")

    personal_chat_key = f"user:{user_id}"
    group_chat_key = f"group:{group_id}" if group_id else ""

    try:
        # ── 讀取商品資料：資料庫 → 最近一次備份 → 舊 Excel，走到備援一定通知 Albert ──
        # （只有「讀取」失敗才走備援；讀取成功但計算失敗，就直接報錯，不會改讀舊資料）
        out = None
        source = "db"
        source_warning = ""
        records, skipped_db = None, []
        try:
            print("從 eln_products 資料庫讀取...")
            records, skipped_db = with_db_retry(load_products_from_db, engine, label="讀取 eln_products")
            print(f"✅ 從資料庫讀取 {len(records)} 檔")
            try:
                with_db_retry(save_products_backup, engine, records, label="備份登錄表")
            except Exception as bk_err:
                print(f"[WARN] 備份失敗（不影響今天的計算）: {bk_err}")
        except Exception as db_err:
            _first_line = str(db_err).strip().split("\n")[0]
            _first_line = _first_line.split(") ", 1)[-1] if _first_line.startswith("(") else _first_line
            db_reason = f"{type(db_err).__name__}: {_first_line[:120]}"
            print(f"[WARNING] DB 讀取失敗({db_reason})，改用最近一次備份...")
            try:
                records, backup_when = with_db_retry(load_latest_backup, engine, label="讀取備份")
                source = "backup"
                source_warning = (
                    f"⚠️ 【ELN 備援通知】今天資料庫讀取失敗，改用 {backup_when} 的備份（{len(records)} 檔）。\n"
                    f"備份之後才新登錄或修改的商品，今天的結果不會反映。\n"
                    f"失敗原因：{db_reason}\n"
                    f"資料庫恢復後請打 /runnow 重新計算。"
                )
            except Exception as bk_err:
                print(f"[WARNING] 備份也讀不到({bk_err})，改用舊 Excel...")
                if not download_latest_eln:
                    raise db_err
                excel_path = download_latest_eln("/tmp/latest_eln.xlsx")
                print(f"Downloaded latest Excel: {excel_path}")
                source = "excel"
                source_warning = (
                    f"🚨 【ELN 備援通知】資料庫和備份都讀不到，今天改用「舊 Excel」計算！\n"
                    f"這份 Excel 是 8 月的舊資料，之後用網頁登錄的商品全部缺漏，今天的 /list 結果不完整。\n"
                    f"資料庫失敗原因：{db_reason}\n"
                    f"請盡快確認 Supabase 狀態，恢復後打 /runnow。"
                )
                out = calculate_from_file(excel_path, lookback_days=3, notify_ki_daily=True)

        if out is None:
            out = calculate_from_records(records, lookback_days=3, notify_ki_daily=True)
        print(f"✅ 計算完成（資料來源：{source}）")

        # 走到備援 → 先通知 Albert（在每日摘要之前）
        if source_warning:
            try:
                push_long_message(line_bot_api, user_id, source_warning)
            except Exception as e:
                print(f"[WARN] 備援通知發送失敗: {e}")

        # 有商品因資料問題沒算到 → 通知 Albert 是哪幾檔、什麼原因
        skipped_all = list(skipped_db) + list(out.get("skipped_rows", []) or [])
        skipped_text = build_skipped_text(skipped_all)
        if skipped_text:
            try:
                push_long_message(line_bot_api, user_id, skipped_text)
            except Exception as e:
                print(f"[WARN] 跳過清單通知發送失敗: {e}")

        print("Running build_result...")
        summary, top5_lines, detail_map, agent_name_map = build_result(out)

        # ── 個人 + 群組合併成同一次資料庫交易寫入，減少 COMMIT 次數 ──
        save_entries = [(personal_chat_key, summary, top5_lines, detail_map, agent_name_map)]
        if group_chat_key:
            save_entries.append((group_chat_key, summary, top5_lines, detail_map, agent_name_map))

        print(f"Saving result to database ({'personal + group' if group_chat_key else 'personal'})...")
        with_db_retry(save_result_to_db_multi, engine, save_entries, label="寫入 eln_last_report/top5/detail")

        print(f"Saved {len(detail_map)} bonds to DB")

        print("Sending summary to personal LINE...")
        push_long_message(line_bot_api, user_id, summary[:20000])

        # 推播 admin 摘要（包含提前出場、KI 等重要事件）
        admin_text = out.get("admin_text", "")
        if admin_text:
            print("[WARNING] 發送 admin 摘要")
            push_long_message(line_bot_api, user_id, admin_text[:4900])

        if group_id and push_group_summary:
            print("Sending summary to group LINE...")
            push_long_message(line_bot_api, group_id, summary[:20000])

        individual_messages = out.get("individual_messages", []) or []

        # ── 自動推播重要事件（提前出場/到期）給理專，其餘保留手動 /send ──
        agent_ids = with_db_retry(load_agent_line_ids, engine, label="讀取 agent_line_ids")
        print(f"[INFO] 理專對照表 {len(agent_ids)} 位")
        auto_sent, remaining_messages = auto_push_important(agent_bot_api, individual_messages, agent_ids)

        auto_sent_text = build_auto_sent_text(auto_sent)
        if auto_sent_text:
            push_long_message(line_bot_api, user_id, auto_sent_text)

        print("Saving pending notifications...")
        with_db_retry(save_pending_notifications, engine, personal_chat_key, remaining_messages, label="寫入待確認通知")

        pending_text = build_pending_text(remaining_messages)
        push_long_message(line_bot_api, user_id, pending_text)

        with_db_retry(
            save_job_log, engine,
            job_name="auto_tracking_cron",
            status="success",
            detail=f"source={source}, saved {len(detail_map)} bonds, skipped={len(skipped_all)}, auto_sent={len(auto_sent)}, pending={len(remaining_messages)}",
            label="寫入 job log"
        )

        print("Done!")

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"Error: {err}")
        print(traceback.format_exc())

        try:
            push_long_message(line_bot_api, user_id, f"⚠️ ELN 自動追蹤失敗：{err}")
        except Exception:
            pass

        try:
            save_job_log(
                engine,
                job_name="auto_tracking_cron",
                status="failed",
                detail=traceback.format_exc()
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
