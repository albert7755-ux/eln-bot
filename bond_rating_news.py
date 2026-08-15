# -*- coding: utf-8 -*-
"""
bond_rating_news.py — 外部信評異動雷達（Google News RSS 版）
==========================================================
Moody's / S&P / Fitch 官網沒有免費 API，直接爬會被擋，
所以改抓 Google News RSS：三大信評的評等動作幾乎都會在數小時內見報。

用法：
    from bond_rating_news import fetch_rating_news
    items = fetch_rating_news("Apple", zh_name="蘋果", days=2)
    # → [{"title","link","source","published","query_lang"}, ...]
"""
import re
import time
import html
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; LobsterBot/1.0; +https://line.me)"}
GNEWS = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"

# 評等動作關鍵字（標題至少要命中一個，過濾掉「Apple 發表新 iPhone」這種）
KW_EN = re.compile(r"\b(downgrad|upgrad|rating|ratings|outlook|watch negative|creditwatch|junk|investment grade|"
                   r"affirm|withdraw|default|moody'?s|s&p|fitch)\b", re.I)
KW_ZH = re.compile(r"(信評|評等|評級|調降|調升|降評|升評|展望|負向|正向|穆迪|標普|惠譽|違約|垃圾級|投資等級)")

def _fetch(query, hl, gl, ceid, timeout=15):
    url = GNEWS.format(q=urllib.parse.quote(query), hl=hl, gl=gl, ceid=ceid)
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return parse_rss(r.text)

def parse_rss(xml_text):
    """解析 Google News RSS → list[dict]"""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
        out.append({"title": title, "link": link, "source": source, "published": dt})
    return out

def fetch_rating_news(en_name, zh_name="", days=2, sleep=0.4):
    """
    針對一家發行機構抓英文＋中文的信評新聞，回傳去重、過濾、限最近 days 天的清單。
    en_name 給英文公司名（如 'Apple'、'Verizon'）；zh_name 給中文名（可空）。
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    seen, items = set(), []
    queries = []
    if en_name:
        queries.append((f'"{en_name}" (Moody\'s OR "S&P" OR Fitch) (downgrade OR upgrade OR rating OR outlook)',
                        "en-US", "US", "US:en", KW_EN, "en"))
    if zh_name:
        queries.append((f'{zh_name} 信評 (調降 OR 調升 OR 展望 OR 穆迪 OR 標普 OR 惠譽)',
                        "zh-TW", "TW", "TW:zh-Hant", KW_ZH, "zh"))
    for q, hl, gl, ceid, kw, lang in queries:
        try:
            for it in _fetch(q, hl, gl, ceid):
                if it["published"] and it["published"] < since:
                    continue
                if not kw.search(it["title"]):
                    continue
                key = re.sub(r"\W+", "", it["title"].lower())[:80]
                if key in seen:
                    continue
                seen.add(key)
                it["query_lang"] = lang
                items.append(it)
        except Exception as e:
            print(f"[RatingNews] {en_name or zh_name} {lang} fetch fail: {e}")
        time.sleep(sleep)
    items.sort(key=lambda x: x["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items

def format_news_block(issuer, items, max_items=4):
    if not items:
        return ""
    lines = [f"🏦 {issuer}"]
    for it in items[:max_items]:
        d = it["published"].astimezone(timezone(timedelta(hours=8))).strftime("%m/%d") if it["published"] else ""
        src = f"（{it['source']}）" if it["source"] else ""
        lines.append(f"▪ {d} {it['title']}{src}\n  {it['link']}")
    return "\n".join(lines)
