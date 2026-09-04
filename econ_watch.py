# -*- coding: utf-8 -*-
"""
econ_watch.py — 重要經濟數據 / 央行會議公布監控
=================================================
追蹤清單:
  美國: CPI, PCE, 非農(NFP), FOMC利率決議, ISM製造業PMI, ISM非製造業PMI
  央行: ECB, 澳洲央行(RBA), 日本央行(BOJ), 台灣央行

運作方式:
  高頻排程(每 5~10 分鐘)呼叫 check_econ_events(),
  對每個追蹤項目用 Claude + web_search 工具檢查「是否近24小時內剛正式公布」,
  已推播過的當日事件記錄在資料庫,不會重複推。
  找不到 ANTHROPIC_API_KEY 或 engine 時,呼叫端應自行略過。
"""
import os
import re
import json
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))

ECON_ITEMS = [
    {"key": "us_cpi", "label": "美國CPI", "query": "US CPI inflation report release"},
    {"key": "us_pce", "label": "美國PCE", "query": "US PCE inflation report release"},
    {"key": "us_nfp", "label": "美國非農就業", "query": "US nonfarm payrolls jobs report release"},
    {"key": "fomc", "label": "FOMC利率決議", "query": "FOMC Federal Reserve interest rate decision"},
    {"key": "ism_mfg", "label": "美國ISM製造業PMI", "query": "ISM manufacturing PMI release"},
    {"key": "ism_svc", "label": "美國ISM非製造業PMI", "query": "ISM services PMI release"},
    {"key": "ecb", "label": "ECB利率決議", "query": "ECB European Central Bank interest rate decision"},
    {"key": "rba", "label": "澳洲央行RBA利率決議", "query": "RBA Reserve Bank Australia interest rate decision"},
    {"key": "boj", "label": "日本央行BOJ利率決議", "query": "BOJ Bank of Japan interest rate decision"},
    {"key": "cbc", "label": "台灣央行利率決議", "query": "台灣央行 中央銀行 理監事會議 利率決議"},
]


# 各項目公布後換算成台北時間的查詢窗:(起始時, 結束時);
# 結束時 < 起始時 代表跨夜到隔天(例如 FOMC 美東下午,換算台北是凌晨)
ITEM_WINDOWS = {
    "us_cpi":  (19, 24),   # 美東8:30/10am,冬夏令約台北19:30~22:00,窗口放寬
    "us_pce":  (19, 24),
    "us_nfp":  (19, 24),
    "ism_mfg": (19, 24),
    "ism_svc": (19, 24),
    "fomc":    (23, 5),    # 美東下午2點決議,換算台北凌晨,跨夜
    "ecb":     (18, 22),   # 中歐13:15~13:45,冬夏令約台北19:15~20:45
    "rba":     (10, 14),   # 雪梨下午2:30,約台北11:30~12:30
    "boj":     (9, 16),    # 日本上午至下午,時間不固定,窗口放寬
    "cbc":     (14, 18),   # 台北本地下午3:30~4:30左右
}


def _in_time_window(key, now):
    """判斷現在的台北時間是否落在該項目理論上會公布結果的時段內"""
    win = ITEM_WINDOWS.get(key)
    if not win:
        return True
    start, end = win
    h = now.hour
    if start <= end:
        return start <= h < end
    return h >= start or h < end     # 跨夜窗口


def _today_key(now=None):
    now = now or datetime.now(TZ_TAIPEI)
    return now.strftime("%Y-%m-%d")


def _is_plausible_day(key, now, calendar=None):
    """
    判斷今天是否為該項目理論上可能公布的日子。
    優先使用當月日曆(fetch_month_calendar 查到的確切日期)精準比對;
    日曆查不到該項目時,才退回粗略的日期規律當備援,避免漏掉。
    """
    if calendar and key in calendar:
        return calendar[key] == now.date()
    # ---- 以下為備援規律(日曆缺該項目時使用) ----
    d, wd = now.day, now.weekday()  # wd: 0=一 ... 4=五 5=六 6=日
    if key == "us_cpi":
        return 8 <= d <= 16                          # 次月第二週前後
    if key == "us_pce":
        return d >= 24                                # 當月最後一週
    if key == "us_nfp":
        return d <= 8 and wd == 4                     # 第一個週五
    if key == "ism_mfg":
        return d <= 4                                 # 每月第1個營業日附近
    if key == "ism_svc":
        return 2 <= d <= 7                            # 每月第3個營業日附近
    if key == "fomc":
        return now.month in (1, 3, 5, 6, 7, 9, 11, 12) and wd in (2, 3) and 15 <= d <= 31
    if key == "ecb":
        return wd == 3                                # ECB 通常週四公布,每6週一次
    if key == "rba":
        return wd == 1 and d <= 8                     # 每月第一個週二(1月除外,寧可多查)
    if key == "boj":
        return 15 <= d <= 31                          # 每次會議多在月中後段,粗估
    if key == "cbc":
        return now.month in (3, 6, 9, 12) and wd == 3 and 15 <= d <= 25   # 季會,約третьей週四
    return True


def ensure_table(engine, text):
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS econ_event_seen (
            event_key TEXT NOT NULL, day_key TEXT NOT NULL,
            seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (event_key, day_key)
        );"""))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS econ_calendar (
            month_key TEXT NOT NULL, event_key TEXT NOT NULL,
            event_date DATE NOT NULL, note TEXT,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (month_key, event_key)
        );"""))


def already_pushed(engine, text, key, day_key):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT 1 FROM econ_event_seen WHERE event_key=:k AND day_key=:d"),
                           {"k": key, "d": day_key}).fetchone()
    return bool(row)


def mark_pushed(engine, text, key, day_key):
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO econ_event_seen(event_key, day_key) VALUES (:k,:d)
                             ON CONFLICT (event_key, day_key) DO NOTHING"""), {"k": key, "d": day_key})


def _month_key(now=None):
    now = now or datetime.now(TZ_TAIPEI)
    return now.strftime("%Y-%m")


def has_month_calendar(engine, text, month_key):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT COUNT(*) FROM econ_calendar WHERE month_key=:m"),
                           {"m": month_key}).fetchone()
    return bool(row and row[0])


def get_month_calendar(engine, text, month_key):
    """回傳 {event_key: date}"""
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT event_key, event_date FROM econ_calendar WHERE month_key=:m"),
                            {"m": month_key}).fetchall()
    return {r[0]: r[1] for r in rows}


def fetch_month_calendar(anthropic_client, month_key):
    """
    一次 API 呼叫,查詢當月所有追蹤項目的確切公布/會議日期(台北時區當地日期)。
    回傳 {event_key: date} 或 {}(失敗時)。
    """
    items_desc = "\n".join(f"- {it['key']}: {it['label']}" for it in ECON_ITEMS)
    prompt = (
        f"請搜尋 {month_key} 這個月份,以下每一項經濟數據公布或央行會議的確切公布時間:\n\n"
        f"{items_desc}\n\n"
        "請將每個事件的公布時間換算成台北時區(UTC+8),並給出『換算後落在台北的哪一個日曆日期』"
        "(例如:美國數據若為美東時間早上公布,換算到台北通常是同一個晚上;"
        "但美東時間下午公布的(如FOMC決議約美東下午2點),換算到台北會是隔天凌晨,"
        "此時應填隔天的日期,而不是美國當地的日期)。\n\n"
        "只回傳 JSON 物件,key 為上面的英文代碼,value 為換算後的台北日期字串 YYYY-MM-DD;"
        "若本月沒有該事件(例如非FOMC會議月份),該 key 就不要出現在結果中。"
        "不要有其他文字,不要用 markdown code block。"
    )
    try:
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            temperature=0.1,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        print(f"[EconWatch] 月曆查詢失敗: {e}")
        return {}
    full_text = "".join(getattr(b, "text", "") for b in message.content)
    got = _extract_json(full_text)
    if not isinstance(got, dict):
        return {}
    out = {}
    valid_keys = {it["key"] for it in ECON_ITEMS}
    for k, v in got.items():
        if k not in valid_keys:
            continue
        try:
            out[k] = datetime.strptime(str(v).strip(), "%Y-%m-%d").date()
        except Exception:
            continue
    return out


def save_month_calendar(engine, text, month_key, calendar):
    with engine.begin() as conn:
        for key, d in calendar.items():
            conn.execute(text("""INSERT INTO econ_calendar(month_key, event_key, event_date)
                                 VALUES (:m,:k,:d)
                                 ON CONFLICT (month_key, event_key) DO UPDATE SET event_date=EXCLUDED.event_date"""),
                        {"m": month_key, "k": key, "d": d})


def ensure_month_calendar(engine, text, anthropic_client, month_key=None):
    """若本月日曆尚未查過,查一次並存起來(供每日排程呼叫,已存在時直接跳過不耗 API)"""
    month_key = month_key or _month_key()
    ensure_table(engine, text)
    if has_month_calendar(engine, text, month_key):
        return get_month_calendar(engine, text, month_key)
    cal = fetch_month_calendar(anthropic_client, month_key)
    if cal:
        save_month_calendar(engine, text, month_key, cal)
        print(f"[EconWatch] {month_key} 日曆已建立,共 {len(cal)} 項")
    else:
        print(f"[EconWatch] {month_key} 日曆查詢無結果,將於下次排程重試")
    return cal


def _extract_json(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def check_one_item(item, anthropic_client, today_str):
    """
    用 Claude + web_search 工具檢查單一項目是否『近24小時內剛正式公布』。
    是的話回傳整理好的推播文字;否則回傳 None。
    """
    prompt = (
        f"今天是台北時間 {today_str}。請搜尋「{item['label']}」({item['query']}) 的最新消息,"
        "判斷這項數據或央行會議結果,是否已經在最近24小時內正式公布/公告。\n\n"
        "嚴格規則:\n"
        "- 只有搜尋結果明確顯示『已公布的實際數字或決議結果』才算已公布;"
        "如果只是『即將公布』『市場預期』『分析師預測』這類前瞻內容,視為未公布。\n"
        "- 不確定就回 published:false,絕對不要臆測數字或結果。\n"
        "- summary 只寫確定的事實數字(實際值、市場預期值、前值,或利率決議結果與是否符合預期),"
        "不要加入你自己的推測。\n"
        "- comment 是 1~2 句對市場/利率/債市影響的中性觀察,不做投資建議,不用果決斷言。\n\n"
        "分析完成後,只用下面這個 JSON 格式回覆(不要有其他文字、不要用 markdown code block):\n"
        '{"published": true 或 false, '
        '"headline": "15字內標題", '
        '"summary": "2~4行,含具體數字", '
        '"comment": "1~2句市場影響觀察"}'
    )
    try:
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=700,
            temperature=0.2,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        print(f"[EconWatch] {item['key']} API 呼叫失敗: {e}")
        return None

    full_text = "".join(getattr(b, "text", "") for b in message.content)
    got = _extract_json(full_text)
    if not got or not got.get("published"):
        return None

    headline = str(got.get("headline") or item["label"]).strip()
    summary = str(got.get("summary") or "").strip()
    comment = str(got.get("comment") or "").strip()
    if not summary:
        return None

    lines = [f"📊 {headline}", "", summary]
    if comment:
        lines += ["", comment]
    lines += ["", "（資料來源：公開新聞彙整，僅供參考，非投資建議）"]
    return "\n".join(lines)


def check_econ_events(engine, text, anthropic_client, push_fn):
    """
    主檢查函式:掃描 ECON_ITEMS,對每項判斷是否剛公布且今天尚未推播過,
    是的話呼叫 push_fn(message) 推播,並記錄已推播。
    回傳本次觸發的項目數。
    """
    ensure_table(engine, text)
    now = datetime.now(TZ_TAIPEI)
    today_str = _today_key(now)
    hit = 0
    # 先確保本月日曆已建立(已存在時不耗 API,直接讀取,幾乎零成本)
    calendar = ensure_month_calendar(engine, text, anthropic_client, _month_key(now))
    for item in ECON_ITEMS:
        if already_pushed(engine, text, item["key"], today_str):
            continue
        if not _in_time_window(item["key"], now):     # 不在該項目的公布時段,跳過
            continue
        if not _is_plausible_day(item["key"], now, calendar):   # 也不是可能公布的日子,跳過
            continue
        msg = check_one_item(item, anthropic_client, today_str)
        if msg:
            try:
                push_fn(msg)
                hit += 1
                print(f"[EconWatch] 推播: {item['label']}")
            except Exception as e:
                print(f"[EconWatch] 推播失敗 {item['key']}: {e}")
                continue   # 推播失敗不標記已推,下次重試
            mark_pushed(engine, text, item["key"], today_str)
    return hit
