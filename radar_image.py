"""
配息雷達 → ChatGPT 生圖 → 推回 LINE 的小工具

提供三個網頁（全部要帶密碼 key）：
  /radar?key=xxx           今天的配息雷達文字（有一鍵複製按鈕）
  /radar.txt?key=xxx       同上，純文字版
  /radar-upload?key=xxx    上傳圖片頁（可直接 Ctrl+V / ⌘V 貼上圖片，或選檔案）
圖片上傳後，Bot 會用 LINE 推給 Albert（LINE_USER_ID）。

密碼：環境變數 RADAR_KEY；沒設的話沿用 BOND_UPLOAD_TOKEN（跟報價檔上傳同一組）。
"""
import io
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from linebot.models import ImageSendMessage, TextSendMessage

TZ_TAIPEI = timezone(timedelta(hours=8))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
KEEP_DAYS = 30


def _key() -> str:
    return os.getenv("RADAR_KEY") or os.getenv("BOND_UPLOAD_TOKEN", "")


def _base_url() -> str:
    url = os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "https://eln-bot.onrender.com"
    return url.rstrip("/")


def _data_dir() -> Path:
    for d in (Path("/data/radar"), Path("/tmp/radar")):
        try:
            (d / "img").mkdir(parents=True, exist_ok=True)
            probe = d / ".write_test"
            probe.write_text("ok")
            probe.unlink()
            return d
        except Exception:
            continue
    return Path("/tmp/radar")


DATA_DIR = _data_dir()
IMG_DIR = DATA_DIR / "img"
TEXT_FILE = DATA_DIR / "radar_latest.json"


# ---------- 給 main.py 呼叫：06:25 雷達產生後存一份 ----------
def save_radar_text(text: str) -> None:
    now = datetime.now(TZ_TAIPEI)
    payload = {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"), "text": text}
    tmp = TEXT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(TEXT_FILE)


def _load_radar():
    try:
        return json.loads(TEXT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_STYLE = """
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body{font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;background:#f5f7fb;color:#1c2b4a;margin:0;padding:16px}
  .card{max-width:760px;margin:0 auto;background:#fff;border-radius:14px;padding:20px;box-shadow:0 2px 12px rgba(0,0,0,.06)}
  h1{font-size:20px;margin:0 0 6px}
  .sub{color:#6b7a99;font-size:13px;margin-bottom:14px}
  pre{white-space:pre-wrap;word-break:break-word;background:#f8fafc;border:1px solid #e3e8f0;border-radius:10px;padding:14px;font-size:14px;line-height:1.6}
  button{background:#1c3f8a;color:#fff;border:0;border-radius:10px;padding:12px 18px;font-size:15px;cursor:pointer}
  button:disabled{background:#9aa8c4}
  .warn{background:#fff6e5;border:1px solid #f3d38a;border-radius:10px;padding:10px 12px;font-size:14px;margin-bottom:12px}
  .ok{background:#e9f7ef;border:1px solid #9bd3b0;border-radius:10px;padding:10px 12px}
  .err{background:#fdecec;border:1px solid #f0a8a8;border-radius:10px;padding:10px 12px}
  #zone{border:2px dashed #9aa8c4;border-radius:12px;padding:28px;text-align:center;color:#6b7a99;margin:12px 0}
  #zone.on{border-color:#1c3f8a;background:#eef3ff}
  #preview{max-width:100%;border-radius:10px;margin-top:10px;display:none}
  input[type=text]{width:100%;box-sizing:border-box;padding:10px;border:1px solid #d5dce8;border-radius:10px;font-size:14px}
</style>
"""


def register(app, line_bot_api):
    """在 main.py 裡呼叫：register(app, line_bot_api)"""

    def _bad_key(key: str) -> bool:
        k = _key()
        return (not k) or key != k

    # ---------- 1. 雷達文字頁 ----------
    @app.get("/radar", response_class=HTMLResponse)
    def radar_page(key: str = ""):
        if _bad_key(key):
            return HTMLResponse("❌ 密碼錯誤", status_code=403)
        r = _load_radar()
        today = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")
        if not r:
            body = '<div class="warn">還沒有任何配息雷達紀錄（等明早 06:25 推播後就會出現）。</div>'
            text = ""
        else:
            text = r.get("text", "")
            stale = "" if r.get("date") == today else \
                f'<div class="warn">⚠️ 這是 {r.get("date")} 的雷達，今天的還沒產生。</div>'
            body = (f'{stale}<div class="sub">產生時間：{r.get("date")} {r.get("time")}</div>'
                    f'<p><button id="copy">📋 一鍵複製全文</button> <span id="done"></span></p>'
                    f'<pre id="radar">{_esc(text)}</pre>')
        html = f"""<!doctype html><html><head><meta charset="utf-8"><title>配息雷達</title>{_STYLE}</head>
<body><div class="card"><h1>💧 今日海外債配息雷達</h1>{body}</div>
<script>
const b=document.getElementById('copy');
if(b)b.onclick=async()=>{{
  const t=document.getElementById('radar').innerText;
  try{{await navigator.clipboard.writeText(t);}}catch(e){{
    const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove();}}
  document.getElementById('done').textContent='✅ 已複製';
}};
</script></body></html>"""
        return HTMLResponse(html)

    @app.get("/radar.txt", response_class=PlainTextResponse)
    def radar_txt(key: str = ""):
        if _bad_key(key):
            return PlainTextResponse("密碼錯誤", status_code=403)
        r = _load_radar()
        if not r:
            return PlainTextResponse("（尚無配息雷達紀錄）")
        return PlainTextResponse(r.get("text", ""))

    # ---------- 2. 圖片上傳頁 ----------
    @app.get("/radar-upload", response_class=HTMLResponse)
    def radar_upload_page(key: str = ""):
        if _bad_key(key):
            return HTMLResponse("❌ 密碼錯誤", status_code=403)
        html = f"""<!doctype html><html><head><meta charset="utf-8"><title>雷達圖上傳</title>{_STYLE}</head>
<body><div class="card">
<h1>🖼️ 上傳配息雷達圖片</h1>
<div class="sub">上傳後會用 LINE 推給 Albert。</div>
<div id="zone">在這裡按 <b>Ctrl+V / ⌘V</b> 貼上圖片<br>或把圖片拖進來<br><br>
<input type="file" id="file" accept="image/*"></div>
<img id="preview">
<p><input type="text" id="note" placeholder="（選填）附一句說明，會跟圖片一起推播"></p>
<p><button id="send" disabled>📤 送出並推到 LINE</button></p>
<div id="msg"></div>
</div>
<script>
const KEY={json.dumps(key)};
let blob=null;
const zone=document.getElementById('zone'),prev=document.getElementById('preview'),
      send=document.getElementById('send'),msg=document.getElementById('msg');
function take(f){{ if(!f||!f.type.startsWith('image/'))return;
  blob=f; prev.src=URL.createObjectURL(f); prev.style.display='block'; send.disabled=false; msg.innerHTML=''; }}
document.addEventListener('paste',e=>{{ for(const it of e.clipboardData.items){{ if(it.type.startsWith('image/')){{take(it.getAsFile());break;}} }} }});
zone.addEventListener('dragover',e=>{{e.preventDefault();zone.classList.add('on');}});
zone.addEventListener('dragleave',()=>zone.classList.remove('on'));
zone.addEventListener('drop',e=>{{e.preventDefault();zone.classList.remove('on');take(e.dataTransfer.files[0]);}});
document.getElementById('file').onchange=e=>take(e.target.files[0]);
send.onclick=async()=>{{
  if(!blob)return; send.disabled=true; msg.innerHTML='上傳中…';
  const fd=new FormData(); fd.append('key',KEY); fd.append('note',document.getElementById('note').value);
  fd.append('file',blob,blob.name||'radar.png');
  try{{ const r=await fetch('/radar-upload',{{method:'POST',body:fd}}); const j=await r.json();
    msg.innerHTML='<div class="'+(j.ok?'ok':'err')+'">'+j.msg+'</div>'; }}
  catch(e){{ msg.innerHTML='<div class="err">❌ 上傳失敗：'+e+'</div>'; }}
  send.disabled=false;
}};
</script></body></html>"""
        return HTMLResponse(html)

    @app.post("/radar-upload")
    async def radar_upload_post(key: str = Form(""), note: str = Form(""), file: UploadFile = File(...)):
        if _bad_key(key):
            return JSONResponse({"ok": False, "msg": "❌ 密碼錯誤"}, status_code=403)
        user_id = os.getenv("LINE_USER_ID", "")
        if not user_id:
            return JSONResponse({"ok": False, "msg": "❌ Bot 沒有設定 LINE_USER_ID"})
        raw = await file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            return JSONResponse({"ok": False, "msg": "❌ 圖片超過 10MB"})
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw))
            img.load()
            img = img.convert("RGB")
        except Exception:
            return JSONResponse({"ok": False, "msg": "❌ 這不是可讀取的圖片檔"})

        name = uuid.uuid4().hex
        big = img.copy()
        big.thumbnail((2048, 2048))
        big.save(IMG_DIR / f"{name}.jpg", "JPEG", quality=90)
        small = img.copy()
        small.thumbnail((480, 480))
        small.save(IMG_DIR / f"{name}_p.jpg", "JPEG", quality=80)

        base = _base_url()
        try:
            line_bot_api.push_message(user_id, [
                TextSendMessage(text="🖼️ 今日配息雷達圖" + (f"\n{note.strip()[:200]}" if note.strip() else "")),
                ImageSendMessage(original_content_url=f"{base}/radar-img/{name}.jpg",
                                 preview_image_url=f"{base}/radar-img/{name}_p.jpg"),
            ])
        except Exception as e:
            return JSONResponse({"ok": False, "msg": f"❌ 圖片已存，但 LINE 推播失敗：{str(e)[:200]}"})

        _cleanup_old()
        return JSONResponse({"ok": True, "msg": "✅ 已推到 LINE"})

    # ---------- 3. 給 LINE 抓圖用 ----------
    @app.get("/radar-img/{fname}")
    def radar_img(fname: str):
        if not re.fullmatch(r"[0-9a-f]{32}(_p)?\.jpg", fname):
            return PlainTextResponse("not found", status_code=404)
        path = IMG_DIR / fname
        if not path.exists():
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(str(path), media_type="image/jpeg")


def _cleanup_old():
    cutoff = time.time() - KEEP_DAYS * 86400
    for p in IMG_DIR.glob("*.jpg"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except Exception:
            pass
