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
    --text3: #6a6260;
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

  /* ── 頂部 ── */
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
  .topbar-left { display: flex; align-items: center; gap: 10px; }
  .topbar-logo {
    font-family: 'Noto Serif TC', serif;
    font-size: 18px;
    font-weight: 700;
    color: var(--gold);
    letter-spacing: 0.05em;
  }
  .topbar-sub {
    font-size: 12px;
    color: var(--text3);
    margin-top: 2px;
  }
  .topbar-stats {
    display: flex;
    gap: 16px;
    font-size: 13px;
    color: var(--text2);
  }
  .stat-num { color: var(--gold); font-weight: 500; }

  /* ── 篩選列 ── */
  .filterbar {
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 12px 20px;
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: center;
  }
  .filter-btn {
    padding: 6px 14px;
    border-radius: 20px;
    border: 1px solid var(--border2);
    background: transparent;
    color: var(--text2);
    font-size: 13px;
    cursor: pointer;
    font-family: 'Noto Sans TC', sans-serif;
    transition: all 0.15s;
  }
  .filter-btn:hover { border-color: var(--gold); color: var(--gold); }
  .filter-btn.active { background: var(--gold); border-color: var(--gold); color: #000; font-weight: 500; }
  .search-box {
    flex: 1;
    min-width: 180px;
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

  /* ── 文章列表 ── */
  .articles-wrap {
    max-width: 760px;
    margin: 0 auto;
    padding: 20px 16px 60px;
  }
  .article-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 12px;
    overflow: hidden;
    transition: border-color 0.15s;
    cursor: pointer;
  }
  .article-card:hover { border-color: var(--border2); }
  .article-card.read { opacity: 0.55; }
  .card-header {
    padding: 16px 18px 12px;
    display: flex;
    align-items: flex-start;
    gap: 12px;
  }
  .card-icon {
    font-size: 20px;
    margin-top: 2px;
    flex-shrink: 0;
  }
  .card-main { flex: 1; min-width: 0; }
  .card-title {
    font-family: 'Noto Serif TC', serif;
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
    line-height: 1.4;
    margin-bottom: 6px;
    word-break: break-all;
  }
  .card-meta {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    font-size: 12px;
    color: var(--text3);
  }
  .badge {
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
  }
  .badge-unread { background: rgba(201,168,76,0.15); color: var(--gold); border: 1px solid rgba(201,168,76,0.3); }
  .badge-read { background: rgba(106,98,96,0.2); color: var(--text3); border: 1px solid var(--border); }
  .badge-url { background: rgba(82,136,224,0.15); color: var(--blue); border: 1px solid rgba(82,136,224,0.3); }
  .badge-image { background: rgba(82,168,120,0.15); color: var(--green); border: 1px solid rgba(82,168,120,0.3); }
  .badge-text { background: rgba(160,152,144,0.15); color: var(--text2); border: 1px solid var(--border2); }

  /* ── 展開內容 ── */
  .card-body {
    display: none;
    border-top: 1px solid var(--border);
  }
  .card-body.open { display: block; }
  .section {
    padding: 14px 18px;
  }
  .section + .section {
    border-top: 1px solid var(--border);
  }
  .section-label {
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.08em;
    color: var(--text3);
    text-transform: uppercase;
    margin-bottom: 8px;
  }
  .summary-text {
    font-size: 14px;
    color: var(--text2);
    line-height: 1.75;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .content-text {
    font-size: 13px;
    color: var(--text3);
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 300px;
    overflow-y: auto;
  }
  .content-text a {
    color: var(--blue);
    word-break: break-all;
  }
  .content-text::-webkit-scrollbar { width: 4px; }
  .content-text::-webkit-scrollbar-track { background: transparent; }
  .content-text::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }

  /* ── 底部操作 ── */
  .card-actions {
    padding: 10px 18px;
    border-top: 1px solid var(--border);
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }
  .btn {
    padding: 6px 16px;
    border-radius: 8px;
    border: none;
    font-size: 13px;
    font-family: 'Noto Sans TC', sans-serif;
    cursor: pointer;
    transition: all 0.15s;
  }
  .btn-mark {
    background: var(--gold);
    color: #000;
    font-weight: 500;
  }
  .btn-mark:hover { background: var(--gold2); }
  .btn-mark:disabled { background: var(--border2); color: var(--text3); cursor: default; }
  .btn-del {
    background: transparent;
    border: 1px solid var(--border2);
    color: var(--text3);
  }
  .btn-del:hover { border-color: var(--red); color: var(--red); }

  /* ── 空狀態 ── */
  .empty {
    text-align: center;
    padding: 60px 20px;
    color: var(--text3);
  }
  .empty-icon { font-size: 48px; margin-bottom: 12px; }
  .empty-text { font-size: 15px; }

  /* ── Toast ── */
  .toast {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%) translateY(80px);
    background: var(--bg3);
    border: 1px solid var(--border2);
    color: var(--text);
    padding: 10px 20px;
    border-radius: 20px;
    font-size: 13px;
    transition: transform 0.3s ease;
    z-index: 200;
    white-space: nowrap;
  }
  .toast.show { transform: translateX(-50%) translateY(0); }

  @media (max-width: 480px) {
    .topbar-stats { display: none; }
    .filterbar { padding: 10px 14px; }
    .articles-wrap { padding: 14px 10px 60px; }
  }
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-left">
    <div>
      <div class="topbar-logo">🦞 龍蝦文章庫</div>
      <div class="topbar-sub">Albert 的財經閱讀清單</div>
    </div>
  </div>
  <div class="topbar-stats">
    <span>全部 <span class="stat-num" id="cnt-all">0</span></span>
    <span>未讀 <span class="stat-num" id="cnt-unread">0</span></span>
  </div>
</div>

<div class="filterbar">
  <input class="search-box" type="text" placeholder="搜尋標題或內容..." id="search-input" oninput="filterArticles()">
  <button class="filter-btn active" onclick="setFilter('all', this)">全部</button>
  <button class="filter-btn" onclick="setFilter('unread', this)">未讀</button>
  <button class="filter-btn" onclick="setFilter('url', this)">🔗 網址</button>
  <button class="filter-btn" onclick="setFilter('image', this)">🖼️ 圖片</button>
  <button class="filter-btn" onclick="setFilter('text', this)">📝 文字</button>
</div>

<div class="articles-wrap">
  <div id="articles-container"></div>
</div>

<div class="toast" id="toast"></div>

<script>
const ARTICLES = __ARTICLES_JSON__;
let currentFilter = 'all';

const iconMap = { url: '🔗', image: '🖼️', text: '📝' };
const labelMap = { url: '網址', image: '圖片', text: '文字' };

function formatDate(iso) {
  const d = new Date(iso);
  const m = d.getMonth() + 1;
  const day = d.getDate();
  const h = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${m}/${day} ${h}:${min}`;
}

function renderArticles() {
  const q = document.getElementById('search-input').value.trim().toLowerCase();
  const container = document.getElementById('articles-container');

  const filtered = ARTICLES.filter(a => {
    if (currentFilter === 'unread' && a.is_read) return false;
    if (currentFilter === 'url' && a.source_type !== 'url') return false;
    if (currentFilter === 'image' && a.source_type !== 'image') return false;
    if (currentFilter === 'text' && a.source_type !== 'text') return false;
    if (q) {
      const hay = ((a.title || '') + (a.summary || '') + (a.content || '')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  document.getElementById('cnt-all').textContent = ARTICLES.length;
  document.getElementById('cnt-unread').textContent = ARTICLES.filter(a => !a.is_read).length;

  if (filtered.length === 0) {
    container.innerHTML = `<div class="empty"><div class="empty-icon">📭</div><div class="empty-text">沒有符合的文章</div></div>`;
    return;
  }

  container.innerHTML = filtered.map(a => {
    const icon = iconMap[a.source_type] || '📄';
    const typeBadge = `badge-${a.source_type || 'text'}`;
    const readBadge = a.is_read ? 'badge-read' : 'badge-unread';
    const readText = a.is_read ? '已讀' : '未讀';
    const cardClass = a.is_read ? 'article-card read' : 'article-card';

    // 原始內容區塊
    let contentBlock = '';
    if (a.source_type === 'url' && a.content && a.content.startsWith('http')) {
      contentBlock = `<div class="section">
        <div class="section-label">原始網址</div>
        <div class="content-text"><a href="${escHtml(a.content)}" target="_blank">${escHtml(a.content)}</a></div>
      </div>`;
    } else if (a.source_type === 'image') {
      contentBlock = `<div class="section">
        <div class="section-label">圖片來源</div>
        <div class="content-text">由 LINE 傳送的圖片，已由 Claude AI 分析</div>
      </div>`;
    } else if (a.content && a.content !== '（圖片）') {
      contentBlock = `<div class="section">
        <div class="section-label">原始內容</div>
        <div class="content-text">${escHtml(a.content)}</div>
      </div>`;
    }

    const markDisabled = a.is_read ? 'disabled' : '';
    const markText = a.is_read ? '✅ 已讀' : '標記已讀';

    return `<div class="${cardClass}" id="card-${a.id}">
      <div class="card-header" onclick="toggleCard(${a.id})">
        <div class="card-icon">${icon}</div>
        <div class="card-main">
          <div class="card-title">${escHtml(a.title || '無標題')}</div>
          <div class="card-meta">
            <span class="badge ${typeBadge}">${labelMap[a.source_type] || '文字'}</span>
            <span class="badge ${readBadge}">${readText}</span>
            <span>${formatDate(a.created_at)}</span>
          </div>
        </div>
      </div>
      <div class="card-body" id="body-${a.id}">
        <div class="section">
          <div class="section-label">Claude 重點摘要</div>
          <div class="summary-text">${escHtml(a.summary || '無摘要')}</div>
        </div>
        ${contentBlock}
        <div class="card-actions">
          <button class="btn btn-del" onclick="deleteArticle(${a.id}, event)">刪除</button>
          <button class="btn btn-mark" id="btn-${a.id}" onclick="markRead(${a.id}, event)" ${markDisabled}>${markText}</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function toggleCard(id) {
  const body = document.getElementById('body-' + id);
  body.classList.toggle('open');
}

function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  filterArticles();
}

function filterArticles() {
  renderArticles();
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2000);
}

async function markRead(id, e) {
  e.stopPropagation();
  const btn = document.getElementById('btn-' + id);
  btn.disabled = true;
  btn.textContent = '處理中...';
  try {
    const r = await fetch('/articles/read/' + id, { method: 'POST' });
    if (r.ok) {
      const card = document.getElementById('card-' + id);
      card.classList.add('read');
      btn.textContent = '✅ 已讀';
      // 更新本地資料
      const a = ARTICLES.find(x => x.id === id);
      if (a) a.is_read = true;
      document.getElementById('cnt-unread').textContent = ARTICLES.filter(x => !x.is_read).length;
      showToast('✅ 已標記為已讀');
    }
  } catch(err) {
    btn.disabled = false;
    btn.textContent = '標記已讀';
  }
}

async function deleteArticle(id, e) {
  e.stopPropagation();
  if (!confirm('確定要刪除這篇文章嗎？')) return;
  try {
    const r = await fetch('/articles/delete/' + id, { method: 'POST' });
    if (r.ok) {
      const idx = ARTICLES.findIndex(x => x.id === id);
      if (idx > -1) ARTICLES.splice(idx, 1);
      showToast('🗑️ 已刪除');
      renderArticles();
    }
  } catch(err) {
    showToast('❌ 刪除失敗');
  }
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
            SELECT id, title, content, summary, source_type, is_read, created_at
            FROM articles
            ORDER BY created_at DESC
            LIMIT 200
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
            "created_at": r[6].isoformat() if r[6] else ""
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
