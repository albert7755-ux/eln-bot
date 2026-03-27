from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

router = APIRouter()

def get_engine():
    from main import engine
    return engine

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🦞 龍蝦文章庫</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@400;600;700&family=Noto+Sans+TC:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  :root {
    --bg: #0f0f0f;
    --bg2: #161616;
    --bg3: #1e1e1e;
    --border: #2a2a2a;
    --border2: #333;
    --gold: #c9a84c;
    --gold2: #e8c96a;
    --text: #e8e4dc;
    --text2: #a09890;
    --text3: #b0a898;
    --red: #e05252;
    --green: #52a878;
    --blue: #5288e0;
    --radius: 12px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Noto Sans TC', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    font-size: 15px;
    line-height: 1.7;
  }
  .topbar {
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 16px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .topbar-logo {
    font-family: 'Noto Serif TC', serif;
    font-size: 18px;
    font-weight: 700;
    color: var(--gold);
    letter-spacing: 0.05em;
  }
  .topbar-sub { font-size: 12px; color: var(--text3); margin-top: 2px; }
  .topbar-stats { display: flex; gap: 16px; font-size: 13px; color: var(--text2); }
  .stat-num { color: var(--gold); font-weight: 500; }
  .filterbar {
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 10px 16px;
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
  }
  .search-box {
    flex: 1; min-width: 160px;
    padding: 6px 14px;
    border-radius: 20px;
    border: 1px solid var(--border2);
    background: var(--bg3);
    color: var(--text);
    font-size: 13px;
    font-family: 'Noto Sans TC', sans-serif;
    outline: none;
    transition: border-color 0.15s;
  }
  .search-box:focus { border-color: var(--gold); }
  .search-box::placeholder { color: var(--text3); }
  .filter-btn {
    padding: 5px 13px;
    border-radius: 20px;
    border: 1px solid var(--border2);
    background: transparent;
    color: var(--text2);
    font-size: 12px;
    cursor: pointer;
    font-family: 'Noto Sans TC', sans-serif;
    transition: all 0.15s;
    white-space: nowrap;
  }
  .filter-btn:hover { border-color: var(--gold); color: var(--gold); }
  .filter-btn.active { background: var(--gold); border-color: var(--gold); color: #000; font-weight: 500; }
  .viewbar {
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 8px 16px;
    display: flex;
    gap: 8px;
  }
  .view-btn {
    padding: 5px 14px;
    border-radius: 8px;
    border: 1px solid var(--border2);
    background: transparent;
    color: var(--text2);
    font-size: 13px;
    cursor: pointer;
    font-family: 'Noto Sans TC', sans-serif;
    transition: all 0.15s;
  }
  .view-btn.active { background: var(--bg3); border-color: var(--gold); color: var(--gold); }
  .articles-wrap {
    max-width: 760px;
    margin: 0 auto;
    padding: 16px 16px 60px;
  }
  .article-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 10px;
    overflow: hidden;
    transition: border-color 0.15s;
    cursor: pointer;
  }
  .article-card:hover { border-color: var(--border2); }
  .article-card.read { opacity: 0.5; }
  .card-header { padding: 14px 16px 10px; display: flex; align-items: flex-start; gap: 10px; }
  .card-icon { font-size: 18px; margin-top: 2px; flex-shrink: 0; }
  .card-main { flex: 1; min-width: 0; }
  .card-title {
    font-family: 'Noto Serif TC', serif;
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
    line-height: 1.4;
    margin-bottom: 5px;
    word-break: break-all;
  }
  .card-meta { display: flex; gap: 8px; flex-wrap: wrap; font-size: 11px; color: var(--text3); align-items: center; }
  .badge { padding: 2px 7px; border-radius: 10px; font-size: 11px; font-weight: 500; }
  .badge-unread { background: rgba(201,168,76,0.15); color: var(--gold); border: 1px solid rgba(201,168,76,0.3); }
  .badge-read { background: rgba(106,98,96,0.2); color: var(--text3); border: 1px solid var(--border); }
  .badge-url { background: rgba(82,136,224,0.15); color: var(--blue); border: 1px solid rgba(82,136,224,0.3); }
  .badge-image { background: rgba(82,168,120,0.15); color: var(--green); border: 1px solid rgba(82,168,120,0.3); }
  .badge-text { background: rgba(160,152,144,0.15); color: var(--text2); border: 1px solid var(--border2); }
  .badge-finance { background: rgba(82,136,224,0.12); color: #7aabf0; border: 1px solid rgba(82,136,224,0.25); }
  .badge-food { background: rgba(224,140,82,0.12); color: #f0b07a; border: 1px solid rgba(224,140,82,0.25); }
  .badge-travel { background: rgba(82,168,120,0.12); color: var(--green); border: 1px solid rgba(82,168,120,0.25); }
  .badge-shopping { background: rgba(168,82,168,0.12); color: #c07af0; border: 1px solid rgba(168,82,168,0.25); }
  .badge-other { background: rgba(160,152,144,0.12); color: var(--text2); border: 1px solid var(--border2); }
  .location-tag { color: var(--green); font-size: 11px; }
  .card-body { display: none; border-top: 1px solid var(--border); }
  .card-body.open { display: block; }
  .section { padding: 12px 16px; }
  .section + .section { border-top: 1px solid var(--border); }
  .section-label {
    font-size: 11px; font-weight: 500;
    letter-spacing: 0.08em; color: var(--text3);
    text-transform: uppercase; margin-bottom: 6px;
  }
  .summary-text { font-size: 14px; color: var(--text2); line-height: 1.75; white-space: pre-wrap; word-break: break-all; }
  .content-text {
    font-size: 13px; color: var(--text); line-height: 1.7;
    white-space: pre-wrap; word-break: break-all;
    max-height: 280px; overflow-y: auto;
  }
  .content-text a { color: var(--blue); word-break: break-all; }
  .content-text::-webkit-scrollbar { width: 4px; }
  .content-text::-webkit-scrollbar-track { background: transparent; }
  .content-text::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }
  .card-actions {
    padding: 8px 16px; border-top: 1px solid var(--border);
    display: flex; gap: 8px; justify-content: flex-end;
  }
  .btn { padding: 5px 14px; border-radius: 8px; border: none; font-size: 13px; font-family: 'Noto Sans TC', sans-serif; cursor: pointer; transition: all 0.15s; }
  .btn-mark { background: var(--gold); color: #000; font-weight: 500; }
  .btn-mark:hover { background: var(--gold2); }
  .btn-mark:disabled { background: var(--border2); color: var(--text3); cursor: default; }
  .btn-del { background: transparent; border: 1px solid var(--border2); color: var(--text3); }
  .btn-del:hover { border-color: var(--red); color: var(--red); }
  #map-view { display: none; }
  #map { height: calc(100vh - 165px); width: 100%; }
  .leaflet-popup-content-wrapper {
    background: #1e1e1e !important; color: #e8e4dc !important;
    border: 1px solid #333 !important; border-radius: 10px !important;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5) !important;
  }
  .leaflet-popup-tip { background: #1e1e1e !important; }
  .popup-title { font-family: 'Noto Serif TC', serif; font-size: 14px; font-weight: 600; margin-bottom: 4px; color: #e8e4dc; }
  .popup-cat { font-size: 11px; color: #b0a898; margin-bottom: 6px; }
  .popup-summary { font-size: 12px; color: #a09890; line-height: 1.5; max-height: 100px; overflow-y: auto; }
  .empty { text-align: center; padding: 60px 20px; color: var(--text3); }
  .empty-icon { font-size: 48px; margin-bottom: 12px; }
  .map-empty {
    position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
    text-align: center; color: #b0a898; z-index: 1000; pointer-events: none;
  }
  .toast {
    position: fixed; bottom: 24px; left: 50%;
    transform: translateX(-50%) translateY(80px);
    background: var(--bg3); border: 1px solid var(--border2);
    color: var(--text); padding: 10px 20px; border-radius: 20px;
    font-size: 13px; transition: transform 0.3s ease; z-index: 1000; white-space: nowrap;
  }
  .toast.show { transform: translateX(-50%) translateY(0); }
  @media (max-width: 480px) {
    .topbar-stats { display: none; }
    .filterbar { padding: 8px 12px; }
    .articles-wrap { padding: 12px 10px 60px; }
  }
</style>
</head>
<body>

<div class="topbar">
  <div>
    <div class="topbar-logo">🦞 龍蝦文章庫</div>
    <div class="topbar-sub">Albert 的閱讀 & 探索清單</div>
  </div>
  <div class="topbar-stats">
    <span>全部 <span class="stat-num" id="cnt-all">0</span></span>
    <span>未讀 <span class="stat-num" id="cnt-unread">0</span></span>
    <span>有地點 <span class="stat-num" id="cnt-loc">0</span></span>
  </div>
</div>

<div class="filterbar">
  <input class="search-box" type="text" placeholder="搜尋標題、地點、內容..." id="search-input" oninput="renderArticles()">
  <button class="filter-btn active" onclick="setFilter('all',this)">全部</button>
  <button class="filter-btn" onclick="setFilter('unread',this)">未讀</button>
  <button class="filter-btn" onclick="setFilter('finance',this)">📊 財經</button>
  <button class="filter-btn" onclick="setFilter('food',this)">🍜 美食</button>
  <button class="filter-btn" onclick="setFilter('travel',this)">📍 旅遊</button>
  <button class="filter-btn" onclick="setFilter('shopping',this)">🛍️ 購物</button>
  <button class="filter-btn" onclick="setFilter('other',this)">其他</button>
</div>

<div class="viewbar">
  <button class="view-btn active" onclick="setView('list',this)">📋 清單</button>
  <button class="view-btn" onclick="setView('map',this)">🗺️ 地圖</button>
</div>

<div id="list-view">
  <div class="articles-wrap">
    <div id="articles-container"></div>
  </div>
</div>

<div id="map-view" style="position:relative;">
  <div id="map"></div>
</div>

<div class="toast" id="toast"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const ARTICLES = __ARTICLES_JSON__;
let currentFilter = 'all';
let currentView = 'list';
let map = null;
let markers = [];

const catIcon  = { finance:'📊', food:'🍜', travel:'📍', shopping:'🛍️', other:'📄' };
const catLabel = { finance:'財經', food:'美食', travel:'旅遊', shopping:'購物', other:'其他' };
const srcIcon  = { url:'🔗', image:'🖼️', text:'📝' };
const srcLabel = { url:'網址', image:'圖片', text:'文字' };

function fmt(iso) {
  const d = new Date(iso);
  return `${d.getMonth()+1}/${d.getDate()}`;
}
function esc(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function getFiltered() {
  const q = document.getElementById('search-input').value.trim().toLowerCase();
  return ARTICLES.filter(a => {
    if (currentFilter === 'unread' && a.is_read) return false;
    if (['finance','food','travel','shopping','other'].includes(currentFilter) && a.category !== currentFilter) return false;
    if (q) {
      const hay = ((a.title||'')+(a.summary||'')+(a.content||'')+(a.location_name||'')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}
function updateStats() {
  document.getElementById('cnt-all').textContent = ARTICLES.length;
  document.getElementById('cnt-unread').textContent = ARTICLES.filter(a=>!a.is_read).length;
  document.getElementById('cnt-loc').textContent = ARTICLES.filter(a=>a.location_name).length;
}
function renderArticles() {
  updateStats();
  if (currentView === 'map') { renderMap(); return; }
  const filtered = getFiltered();
  const container = document.getElementById('articles-container');
  if (!filtered.length) {
    container.innerHTML = `<div class="empty"><div class="empty-icon">📭</div><div>沒有符合的文章</div></div>`;
    return;
  }
  container.innerHTML = filtered.map(a => {
    const ci = catIcon[a.category]||'📄';
    const catB = `<span class="badge badge-${a.category||'other'}">${ci} ${catLabel[a.category]||'其他'}</span>`;
    const srcB = `<span class="badge badge-${a.source_type||'text'}">${srcIcon[a.source_type]||'📄'} ${srcLabel[a.source_type]||'文字'}</span>`;
    const readB = a.is_read ? `<span class="badge badge-read">已讀</span>` : `<span class="badge badge-unread">未讀</span>`;
    const locT = a.location_name ? `<span class="location-tag">📍 ${esc(a.location_name)}</span>` : '';
    let contentBlock = '';
    if (a.source_type==='url' && a.content && a.content.startsWith('http')) {
      contentBlock = `<div class="section"><div class="section-label">原始網址</div><div class="content-text"><a href="${esc(a.content)}" target="_blank">${esc(a.content)}</a></div></div>`;
    } else if (a.source_type==='image') {
      contentBlock = `<div class="section"><div class="section-label">圖片來源</div><div class="content-text">由 LINE 傳送的圖片，已由 Claude AI 分析</div></div>`;
    } else if (a.content && a.content!=='（圖片）') {
      contentBlock = `<div class="section"><div class="section-label">原始內容</div><div class="content-text">${esc(a.content)}</div></div>`;
    }
    const md = a.is_read ? 'disabled' : '';
    const mt = a.is_read ? '✅ 已讀' : '標記已讀';
    return `<div class="article-card${a.is_read?' read':''}" id="card-${a.id}">
      <div class="card-header" onclick="toggleCard(${a.id})">
        <div class="card-icon">${ci}</div>
        <div class="card-main">
          <div class="card-title">${esc(a.title||'無標題')}</div>
          <div class="card-meta">${catB}${srcB}${readB}${locT}<span>${fmt(a.created_at)}</span></div>
        </div>
      </div>
      <div class="card-body" id="body-${a.id}">
        <div class="section">
          <div class="section-label">Claude 重點摘要</div>
          <div class="summary-text">${esc(a.summary||'無摘要')}</div>
        </div>
        ${contentBlock}
        <div class="card-actions">
          <button class="btn btn-del" onclick="deleteArticle(${a.id},event)">刪除</button>
          <button class="btn btn-mark" id="btn-${a.id}" onclick="markRead(${a.id},event)" ${md}>${mt}</button>
        </div>
      </div>
    </div>`;
  }).join('');
}
function renderMap() {
  if (!map) {
    map = L.map('map').setView([23.5, 121], 7);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution:'© OpenStreetMap © CARTO', maxZoom:19
    }).addTo(map);
  }
  markers.forEach(m => map.removeLayer(m));
  markers = [];
  const filtered = getFiltered().filter(a => a.lat && a.lng);
  if (!filtered.length) return;
  const bounds = [];
  filtered.forEach(a => {
    const ci = catIcon[a.category]||'📍';
    const m = L.marker([a.lat, a.lng], {
      icon: L.divIcon({ html:`<div style="font-size:26px;line-height:1;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.5))">${ci}</div>`, className:'', iconSize:[32,32], iconAnchor:[16,16] })
    }).addTo(map);
    m.bindPopup(`
      <div class="popup-title">${esc(a.title||'無標題')}</div>
      <div class="popup-cat">${ci} ${catLabel[a.category]||''} · ${esc(a.location_name||'')}</div>
      <div class="popup-summary">${esc((a.summary||'').slice(0,200))}</div>
    `);
    markers.push(m);
    bounds.push([a.lat, a.lng]);
  });
  if (bounds.length) map.fitBounds(bounds, { padding:[50,50] });
}
function toggleCard(id) {
  document.getElementById('body-'+id).classList.toggle('open');
}
function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderArticles();
}
function setView(v, btn) {
  currentView = v;
  document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('list-view').style.display = v==='list' ? 'block' : 'none';
  document.getElementById('map-view').style.display = v==='map' ? 'block' : 'none';
  if (v==='map') { renderMap(); setTimeout(()=>map&&map.invalidateSize(),150); }
}
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2200);
}
async function markRead(id, e) {
  e.stopPropagation();
  const btn = document.getElementById('btn-'+id);
  btn.disabled = true; btn.textContent = '處理中...';
  try {
    const r = await fetch('/articles/read/'+id, { method:'POST' });
    if (r.ok) {
      const a = ARTICLES.find(x=>x.id===id);
      if (a) a.is_read = true;
      document.getElementById('card-'+id).classList.add('read');
      btn.textContent = '✅ 已讀';
      updateStats();
      showToast('✅ 已標記為已讀');
    }
  } catch { btn.disabled=false; btn.textContent='標記已讀'; }
}
async function deleteArticle(id, e) {
  e.stopPropagation();
  if (!confirm('確定要刪除這篇文章嗎？')) return;
  try {
    const r = await fetch('/articles/delete/'+id, { method:'POST' });
    if (r.ok) {
      const idx = ARTICLES.findIndex(x=>x.id===id);
      if (idx>-1) ARTICLES.splice(idx,1);
      showToast('🗑️ 已刪除');
      renderArticles();
    }
  } catch { showToast('❌ 刪除失敗'); }
}
renderArticles();
</script>
</body>
</html>
"""

@router.get("/articles", response_class=HTMLResponse)
async def articles_page(request: Request):
    import json
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT id, title, content, summary, source_type, is_read, created_at,
                   COALESCE(category, 'other') as category,
                   COALESCE(location_name, '') as location_name,
                   lat, lng
            FROM articles
            ORDER BY created_at DESC
            LIMIT 300
        """)).fetchall()

    articles = []
    for r in rows:
        articles.append({
            "id": r[0],
            "title": r[1] or "無標題",
            "content": r[2] or "",
            "summary": r[3] or "",
            "source_type": r[4] or "text",
            "is_read": bool(r[5]),
            "created_at": r[6].isoformat() if r[6] else "",
            "category": r[7] or "other",
            "location_name": r[8] or "",
            "lat": float(r[9]) if r[9] else None,
            "lng": float(r[10]) if r[10] else None,
        })

    html = HTML_TEMPLATE.replace(
        "__ARTICLES_JSON__",
        json.dumps(articles, ensure_ascii=False)
    )
    return HTMLResponse(content=html)

@router.post("/articles/read/{article_id}")
async def mark_read(article_id: int):
    from fastapi.responses import JSONResponse
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("UPDATE articles SET is_read = TRUE WHERE id = :i"), {"i": article_id})
    return JSONResponse({"ok": True})

@router.post("/articles/delete/{article_id}")
async def delete_article(article_id: int):
    from fastapi.responses import JSONResponse
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM articles WHERE id = :i"), {"i": article_id})
    return JSONResponse({"ok": True})
