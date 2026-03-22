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
import traceback
from datetime import datetime, timezone, timedelta

from linebot import LineBotApi
from linebot.models import TextSendMessage
from sqlalchemy import create_engine, text

from autotracking_core import calculate_from_file
from eln_storage import download_latest_eln

TZ_TAIPEI = timezone(timedelta(hours=8))


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


def save_result_to_db(engine, chat_key: str, summary: str, top5_lines: list, detail_map: dict, agent_name_map: dict):
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO eln_last_report(chat_key, summary, updated_at)
        VALUES (:k, :s, NOW())
        ON CONFLICT (chat_key) DO UPDATE
        SET summary=:s, updated_at=NOW()
        """), {"k": chat_key, "s": summary})

        conn.execute(text("DELETE FROM eln_top5 WHERE chat_key=:k"), {"k": chat_key})
        for i, line in enumerate(top5_lines, start=1):
            conn.execute(text("""
            INSERT INTO eln_top5(chat_key, line_no, text_line, updated_at)
            VALUES (:k, :n, :t, NOW())
            """), {"k": chat_key, "n": i, "t": line})

        conn.execute(text("DELETE FROM eln_detail WHERE chat_key=:k"), {"k": chat_key})
        for bond_id, detail in detail_map.items():
            agent = agent_name_map.get(bond_id, "-")
            conn.execute(text("""
            INSERT INTO eln_detail(chat_key, bond_id, detail, agent_name, updated_at)
            VALUES (:k, :b, :d, :a, NOW())
            """), {"k": chat_key, "b": bond_id, "d": detail, "a": agent})


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

            detail_text = (
                f"【商品】{_id}\n"
                f"類型: {r['Type'] if 'Type' in r.index else '-'}\n"
                f"理專: {agent}\n"
                f"交易日: {r['交易日'] if '交易日' in r.index else '-'}\n"
                f"最終評價日: {r['最終評價日'] if '最終評價日' in r.index else '-'}\n"
                f"KO設定: {r['KO設定'] if 'KO設定' in r.index else '-'}\n"
                f"KI類型: {r['KI類型'] if 'KI類型' in r.index else '-'}\n"
                f"Coupon: {r['Coupon'] if 'Coupon' in r.index else '-'}\n"
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

        for msg in individual_messages:
            conn.execute(text("""
                INSERT INTO eln_pending_notifications
                    (chat_key, target_id, agent_name, bond_id, status, msg)
                VALUES (:k, :t, :n, :b, :s, :m)
            """), {
                "k": chat_key,
                "t": msg.get("target", ""),
                "n": msg.get("name", ""),
                "b": msg.get("id", ""),
                "s": msg.get("status", ""),
                "m": msg.get("msg", "")
            })


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

    personal_chat_key = f"user:{user_id}"
    group_chat_key = f"group:{group_id}" if group_id else ""

    try:
        print("Downloading latest Excel from Supabase Storage...")
        excel_path = download_latest_eln("/tmp/latest_eln.xlsx")
        print(f"Downloaded latest Excel: {excel_path}")

        print("Running autotracking...")
        out = calculate_from_file(excel_path, lookback_days=3, notify_ki_daily=True)
        summary, top5_lines, detail_map, agent_name_map = build_result(out)

        print("Saving result to database (personal)...")
        save_result_to_db(engine, personal_chat_key, summary, top5_lines, detail_map, agent_name_map)

        if group_chat_key:
            print("Saving result to database (group)...")
            save_result_to_db(engine, group_chat_key, summary, top5_lines, detail_map, agent_name_map)

        print(f"Saved {len(detail_map)} bonds to DB")

        print("Sending summary to personal LINE...")
        push_long_message(line_bot_api, user_id, summary[:20000])

        if group_id and push_group_summary:
            print("Sending summary to group LINE...")
            push_long_message(line_bot_api, group_id, summary[:20000])

        individual_messages = out.get("individual_messages", []) or []

        # ── 方案B：接近觸價預警通知 ──
        warning_lines = []
        df = out.get("results_df")
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                bond_id = str(r.get("債券代號", "")).strip()
                if not bond_id or bond_id == "nan":
                    continue
                # 只看進行中的商品
                status = str(r.get("狀態", ""))
                if any(x in status for x in ["提前出場", "到期", "未發行"]):
                    continue
                for c in df.columns:
                    if not str(c).endswith("_Detail"):
                        continue
                    detail_val = str(r.get(c, "") or "")
                    if "⚠️距KO" in detail_val or "⚠️距KI" in detail_val or "⚠️距Strike" in detail_val:
                        # 擷取標的代號
                        import re as _re
                        m = _re.search(r"【(\w+)】", detail_val)
                        ticker = m.group(1) if m else "?"
                        # 擷取預警訊息
                        warnings = _re.findall(r"⚠️[^\n]+", detail_val)
                        for w in warnings:
                            warning_lines.append(f"• {bond_id} {ticker} {w.strip()}")
        if warning_lines:
            warn_msg = "⚠️ 今日接近觸價預警\n" + "─" * 16 + "\n"
            warn_msg += "\n".join(warning_lines)
            warn_msg += "\n\n請注意追蹤上述標的！"
            push_long_message(line_bot_api, user_id, warn_msg)
            print(f"[WARNING] 發送 {len(warning_lines)} 筆接近觸價預警")

        print("Saving pending notifications...")
        save_pending_notifications(engine, personal_chat_key, individual_messages)

        pending_text = build_pending_text(individual_messages)
        push_long_message(line_bot_api, user_id, pending_text)

        save_job_log(
            engine,
            job_name="auto_tracking_cron",
            status="success",
            detail=f"Saved {len(detail_map)} bonds, pending={len(individual_messages)}"
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
