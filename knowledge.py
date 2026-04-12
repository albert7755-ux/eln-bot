import os
import uuid
import base64
from pathlib import Path

import anthropic
import urllib.request as _urllib_request
import json as _json_gemini

# ── Claude（只用於 Vision 讀圖，因為 Gemini Vision 也支援）──
claude_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ── Gemini（用於查詢回答，省 Anthropic token）──
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.0-flash"

def gemini_chat(prompt: str, images: list = None) -> str:
    """呼叫 Gemini API，支援文字+圖片"""
    if not GEMINI_API_KEY:
        raise ValueError("缺少 GEMINI_API_KEY 環境變數")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

    parts = []
    # 加入圖片（如果有）
    if images:
        for img_info in images:
            parts.append({
                "inline_data": {
                    "mime_type": img_info["media_type"],
                    "data": img_info["data"]
                }
            })
    # 加入文字
    parts.append({"text": prompt})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "maxOutputTokens": 3000,
            "temperature": 0.1
        }
    }

    req = _urllib_request.Request(
        url,
        data=_json_gemini.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with _urllib_request.urlopen(req, timeout=60) as resp:
        data = _json_gemini.loads(resp.read().decode("utf-8"))

    return (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
        .strip()
    )
import chromadb
from chromadb.utils import embedding_functions
import fitz  # PyMuPDF
from PIL import Image

# ── 路徑設定（存在 Render Disk /data 底下）──
BASE_DIR = Path("/data/knowledge")
UPLOAD_DIR = BASE_DIR / "uploads"
PAGES_DIR = BASE_DIR / "page_images"
CHROMA_DIR = BASE_DIR / "chroma_db"
TABLE_DIR = BASE_DIR / "table_images"   # 專門存表格圖片

for d in [UPLOAD_DIR, PAGES_DIR, CHROMA_DIR, TABLE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 表格圖片註冊表（doc_id → {filename, img_path}）
import json as _json
TABLE_INDEX_FILE = BASE_DIR / "table_index.json"

def _load_table_index() -> dict:
    if TABLE_INDEX_FILE.exists():
        try:
            return _json.loads(TABLE_INDEX_FILE.read_text(encoding="utf-8"))
        except:
            return {}
    return {}

def _save_table_index(index: dict):
    TABLE_INDEX_FILE.write_text(_json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

def register_table_image(doc_id: str, filename: str, img_path: Path):
    """把圖片登記為表格圖片，查詢時直接用 Vision 看"""
    index = _load_table_index()
    index[doc_id] = {"filename": filename, "img_path": str(img_path)}
    _save_table_index(index)
    print(f"[KB] 已登記表格圖片：{filename}")

def unregister_table_image(doc_id: str):
    """刪除表格圖片登記"""
    index = _load_table_index()
    if doc_id in index:
        del index[doc_id]
        _save_table_index(index)

def get_all_table_images() -> list[dict]:
    """取得所有已登記的表格圖片"""
    index = _load_table_index()
    result = []
    for doc_id, info in index.items():
        img_path = Path(info["img_path"])
        if img_path.exists():
            result.append({
                "doc_id": doc_id,
                "filename": info["filename"],
                "img_path": img_path
            })
    return result

# ── ChromaDB ──
chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = chroma_client.get_or_create_collection(
    name="knowledge_base",
    embedding_function=embedding_fn,
    metadata={"hnsw:space": "cosine"}
)

# ── Claude ──
claude_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def pdf_to_page_images(pdf_path: Path, doc_id: str) -> list:
    """PDF每頁轉圖片，回傳圖片路徑列表"""
    doc = fitz.open(str(pdf_path))
    image_paths = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)
        img_path = PAGES_DIR / f"{doc_id}_page_{page_num}.png"
        pix.save(str(img_path))
        image_paths.append(img_path)
    doc.close()
    return image_paths


def vision_read_page(img_path: Path) -> str:
    """用 Claude Vision 讀取單一頁面圖片，強制逐字擷取所有文字"""
    with open(img_path, "rb") as f:
        img_data = base64.standard_b64encode(f.read()).decode("utf-8")

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}},
                {"type": "text", "text": (
                    "請把這張圖片裡的所有文字內容完整擷取出來，規則如下：\n\n"
                    "1. 【文字】逐字照抄圖片中所有看得到的文字，一個字都不能省略\n"
                    "2. 【表格】如果有表格，用以下格式輸出每一格的內容：\n"
                    "   欄位名稱1 | 欄位名稱2 | 欄位名稱3\n"
                    "   數值1 | 數值2 | 數值3\n"
                    "   （每一行都要輸出，不能只說「表格列出了...」）\n"
                    "3. 【數字】所有數字、百分比、時間、金額必須完整保留\n"
                    "4. 【禁止】不可以只描述圖片外觀，例如不能說「表格以網格呈現」\n"
                    "5. 【禁止】不可以省略任何內容，不可以說「等」或「...」\n\n"
                    "直接輸出擷取的文字內容，不需要說明你在做什麼。"
                )}
            ]
        }]
    )
    return response.content[0].text


def extract_text_from_pdf(pdf_path: Path):
    """抽取PDF每頁文字，標記需要Vision的頁面"""
    doc = fitz.open(str(pdf_path))
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text().strip()
        pages.append({"page": i, "text": text, "needs_vision": len(text) < 50})
    doc.close()
    return pages


def process_image_file(img_path: Path):
    """獨立圖片檔案用 Claude Vision 逐字擷取所有文字"""
    suffix = img_path.suffix.lower()
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
    media_type = media_map.get(suffix, "image/png")

    with open(img_path, "rb") as f:
        img_data = base64.standard_b64encode(f.read()).decode("utf-8")

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_data}},
                {"type": "text", "text": (
                    "請把這張圖片裡的所有文字內容完整擷取出來，規則如下：\n\n"
                    "1. 【文字】逐字照抄圖片中所有看得到的文字，一個字都不能省略\n"
                    "2. 【表格】如果有表格，用以下格式輸出每一格的內容：\n"
                    "   欄位名稱1 | 欄位名稱2 | 欄位名稱3\n"
                    "   數值1 | 數值2 | 數值3\n"
                    "   （每一行都要輸出，不能只說「表格列出了...」）\n"
                    "3. 【數字】所有數字、百分比、時間、金額必須完整保留\n"
                    "4. 【禁止】不可以只描述圖片外觀，例如不能說「表格以網格呈現」\n"
                    "5. 【禁止】不可以省略任何內容，不可以說「等」或「...」\n\n"
                    "直接輸出擷取的文字內容，不需要說明你在做什麼。"
                )}
            ]
        }]
    )
    return [{"page": 0, "text": response.content[0].text}]


# 偵測這些關鍵字代表「比較型/對照型」內容，整頁不切割
COMPARISON_KEYWORDS = [
    "高資產", "專投", "專業投資人", "一般投資人",
    "A類", "B類", "C類", "甲類", "乙類",
    "比較", "對照", "差異", "區別", "vs", "VS",
    "專業客戶", "一般客戶", "自然人", "法人",
]

def is_comparison_page(text: str) -> bool:
    """判斷這頁是否為對照/比較表格，若是則不切割"""
    count = sum(1 for kw in COMPARISON_KEYWORDS if kw in text)
    return count >= 2  # 出現兩個以上關鍵字就視為比較頁


def chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> list:
    """
    中文友善切割：
    - 如果是比較型頁面（同時包含多種客戶類型）→ 整頁保留不切割
    - 否則按字數切割，chunk_size=400 平衡精準度與上下文
    """
    # 比較型頁面：整頁作為一個 chunk，避免高資產/專投被切開混淆
    if is_comparison_page(text):
        print(f"[KB] 偵測到比較型頁面，整頁保留不切割（{len(text)} 字）")
        # 如果整頁太長就切成兩半，但每半仍保留完整段落
        if len(text) <= chunk_size * 3:
            return [text] if len(text.strip()) > 10 else []
        # 超長就以段落為單位切，每段仍要保留完整客戶類型區塊
        paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
        chunks = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) <= chunk_size * 2:
                current = current + para if not current else current + "\n" + para
            else:
                if current:
                    chunks.append(current)
                current = para
        if current:
            chunks.append(current)
        return [c for c in chunks if len(c.strip()) > 10]

    # 一般頁面：正常按字數切割
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) <= chunk_size:
            current = current + para if not current else current + "\n" + para
        else:
            if current:
                chunks.append(current)
            if len(para) > chunk_size:
                for i in range(0, len(para), chunk_size - overlap):
                    chunks.append(para[i:i + chunk_size])
                current = para[-overlap:] if len(para) > overlap else para
            else:
                current = para

    if current:
        chunks.append(current)

    return [c for c in chunks if len(c.strip()) > 10]


def convert_to_pdf(file_path: Path):
    """PPT/Word 轉 PDF"""
    pdf_path = file_path.with_suffix(".pdf")
    os.system(f'libreoffice --headless --convert-to pdf "{file_path}" --outdir "{file_path.parent}"')
    return pdf_path if pdf_path.exists() else None


def _process_pdf_pages(pdf_path: Path, doc_id: str) -> list:
    """處理PDF：轉圖片 + 抽文字 + Vision補讀"""
    image_paths = pdf_to_page_images(pdf_path, doc_id)
    raw_pages = extract_text_from_pdf(pdf_path)
    pages_data = []

    for page_info in raw_pages:
        page_num = page_info["page"]
        text = page_info["text"]
        needs_vision = page_info["needs_vision"]

        if needs_vision:
            img_path = PAGES_DIR / f"{doc_id}_page_{page_num}.png"
            if img_path.exists():
                print(f"[KB] 第 {page_num+1} 頁文字少，改用 Vision 讀取")
                try:
                    vision_text = vision_read_page(img_path)
                    pages_data.append({"page": page_num, "text": f"[圖片頁]\n{vision_text}"})
                except Exception as e:
                    print(f"[KB] Vision 失敗：{e}")
                    if text:
                        pages_data.append({"page": page_num, "text": text})
        else:
            pages_data.append({"page": page_num, "text": text})

    return pages_data


def process_and_index_file(filename: str, file_bytes: bytes, as_table: bool = False) -> dict:
    """
    上傳並處理檔案，存入向量資料庫。
    as_table=True 時：圖片不存向量資料庫，改用 Vision 直查模式。
    """
    doc_id = str(uuid.uuid4())[:8]
    suffix = Path(filename).suffix.lower()
    saved_path = UPLOAD_DIR / f"{doc_id}{suffix}"

    with open(saved_path, "wb") as f:
        f.write(file_bytes)

    # ── 表格圖片直查模式 ──
    if as_table and suffix in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        # 複製到 table_images 資料夾
        img_dest = TABLE_DIR / f"{doc_id}{suffix}"
        import shutil
        shutil.copy(saved_path, img_dest)
        # 也存一份到 page_images 供預覽
        preview_dest = PAGES_DIR / f"{doc_id}_page_0.png"
        try:
            Image.open(saved_path).save(str(preview_dest))
        except:
            pass
        # 登記為表格圖片
        register_table_image(doc_id, filename, img_dest)
        # 同時也用 Vision 抽文字存進向量庫（雙保險）
        try:
            pages_data = process_image_file(saved_path)
            for page_info in pages_data:
                for chunk_idx, chunk in enumerate(chunk_text(page_info["text"])):
                    collection.add(
                        documents=[chunk],
                        ids=[f"{doc_id}_p0_c{chunk_idx}"],
                        metadatas=[{"doc_id": doc_id, "filename": filename, "page": 0, "chunk_idx": chunk_idx, "is_table_image": True}]
                    )
        except Exception as e:
            print(f"[KB] 表格圖片 Vision 存庫失敗（不影響直查）：{e}")
        return {"doc_id": doc_id, "filename": filename, "pages": 1, "chunks": 0, "mode": "table_direct"}

    pages_data = []

    if suffix == ".pdf":
        pages_data = _process_pdf_pages(saved_path, doc_id)

    elif suffix == ".txt":
        # 純文字直接讀取
        with open(saved_path, "r", encoding="utf-8") as f:
            text_content = f.read().strip()
        if text_content:
            # 如果是表格（含有 | 符號）→ 整份不切割，保持完整結構
            is_table = text_content.count("|") >= 4
            if is_table:
                print(f"[KB] 偵測到表格格式，整份保留不切割")
            pages_data = [{"page": 0, "text": text_content, "is_table": is_table}]

    elif suffix in [".pptx", ".ppt", ".docx", ".doc"]:
        pdf_path = convert_to_pdf(saved_path)
        if pdf_path:
            pages_data = _process_pdf_pages(pdf_path, doc_id)

    elif suffix in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        pages_data = process_image_file(saved_path)
        img_dest = PAGES_DIR / f"{doc_id}_page_0.png"
        try:
            Image.open(saved_path).save(str(img_dest))
        except Exception as e:
            print(f"[KB] 圖片存檔失敗：{e}")

    if not pages_data:
        raise ValueError("無法解析此檔案內容")

    total_chunks = 0
    for page_info in pages_data:
        page_num = page_info["page"]
        text = page_info["text"]
        is_table = page_info.get("is_table", False)
        if not text.strip():
            continue

        # 表格整份不切割，直接存成一個 chunk
        if is_table:
            chunks = [text]
        else:
            chunks = chunk_text(text)

        for chunk_idx, chunk in enumerate(chunks):
            collection.add(
                documents=[chunk],
                ids=[f"{doc_id}_p{page_num}_c{chunk_idx}"],
                metadatas=[{
                    "doc_id": doc_id,
                    "filename": filename,
                    "page": page_num,
                    "chunk_idx": chunk_idx
                }]
            )
            total_chunks += 1

    return {
        "doc_id": doc_id,
        "filename": filename,
        "pages": len(pages_data),
        "chunks": total_chunks
    }


# ── 財金專業術語同義詞對照表 ──────────────────────────────
SYNONYMS = {
    "專投": ["專業投資人", "專業投資機構", "專業投資"],
    "專業投資人": ["專投", "專業投資機構"],
    "一般投資人": ["一般投資", "散戶"],
    "hnw": ["高資產客戶", "高淨值客戶", "高淨值", "高資產"],
    "高資產": ["hnw", "高淨值客戶", "高資產客戶"],
    "vip": ["貴賓", "高資產客戶", "重要客戶"],
    "si": ["結構型商品", "組合式商品", "組合式產品", "結構型產品", "sn"],
    "sn": ["結構型商品", "組合式商品", "組合式產品", "結構型產品", "si"],
    "結構型商品": ["si", "sn", "組合式商品", "組合式產品", "結構型產品"],
    "組合式產品": ["si", "sn", "結構型商品", "結構型產品", "組合式商品"],
    "dci": ["雙元貨幣", "dual currency investment", "雙元投資"],
    "雙元貨幣": ["dci", "dual currency investment"],
    "ben": ["保本票據", "保本型", "barrier enhanced note"],
    "eln": ["股票連結票據", "股連結", "equity linked note"],
    "股票連結票據": ["eln", "股連結"],
    "ko": ["提前出場", "knock out", "提前到期"],
    "ki": ["knock in", "敲入", "保護線"],
    "strike": ["執行價", "履約價", "行使價"],
    "lbl": ["lombard", "lombard lending", "有價證券質借", "有價質借"],
    "lombard": ["lbl", "lombard lending", "有價證券質借", "有價質借"],
    "lombard lending": ["lbl", "lombard", "有價證券質借"],
    "有價證券質借": ["lbl", "lombard", "lombard lending", "有價質借"],
    "信託質借": ["信託質押借款", "信託借款", "質借信託"],
    "金市質借": ["黃金質借", "金市借款", "黃金質押"],
    "pimco": ["品浩", "pimco收益基金", "pimco income"],
    "收益基金": ["pimco收益", "income fund", "配息基金"],
    "配息": ["收益分配", "股息", "息收", "殖利率"],
    "高收益債": ["垃圾債", "high yield", "hy", "非投資等級債"],
    "hy": ["高收益債", "high yield bond", "非投資等級債"],
    "ig": ["投資等級債", "investment grade", "投資級債券"],
    "投資等級債": ["ig", "investment grade bond"],
    "可轉債": ["cb", "convertible bond", "轉換公司債"],
    "cb": ["可轉債", "convertible bond"],
    "etf": ["指數股票型基金", "指數型基金", "交易所交易基金"],
    "fed": ["聯準會", "美聯儲", "federal reserve", "fomc"],
    "聯準會": ["fed", "美聯儲", "fomc", "federal reserve"],
    "fomc": ["聯準會", "fed", "聯邦公開市場委員會"],
    "ecb": ["歐洲央行", "european central bank"],
    "boj": ["日本央行", "bank of japan"],
    "cpi": ["消費者物價指數", "通膨指標", "物價指數"],
    "pce": ["個人消費支出", "核心pce", "通膨指標"],
    "非農": ["非農就業", "nfp", "就業報告", "non-farm payroll"],
    "殖利率": ["yield", "利率", "收益率"],
    "利差": ["spread", "信用利差", "credit spread"],
    "dxy": ["美元指數", "美元強弱", "dollar index"],
    "spx": ["標普500", "s&p500", "標準普爾500"],
    "ndx": ["那斯達克100", "nasdaq 100"],
    "vix": ["恐慌指數", "波動率指數", "volatility index"],
    "kyc": ["認識客戶", "客戶盡職調查", "know your customer"],
    "aml": ["反洗錢", "防制洗錢", "anti money laundering"],
    "kid": ["關鍵資訊文件", "商品說明書"],
    "nav": ["淨值", "資產淨值", "net asset value"],
    "aum": ["資產管理規模", "管理資產", "assets under management"],
}


def expand_query_with_synonyms(question: str) -> str:
    """將問題術語展開成同義詞"""
    q_lower = question.lower()
    extra_terms = []
    for term, synonyms in SYNONYMS.items():
        if term.lower() in q_lower:
            extra_terms.extend(synonyms)
    if extra_terms:
        unique_extras = list(dict.fromkeys(extra_terms))[:10]
        print(f"[KB] 同義詞展開：{unique_extras}")
        return question + " " + " ".join(unique_extras)
    return question


def query_knowledge(question: str, top_k: int = 8) -> dict:
    """問問題：同時用 RAG 搜文字 + Vision 直看表格圖片"""
    count = collection.count()
    table_images = get_all_table_images()

    if count == 0 and not table_images:
        return {"answer": "資料庫中尚無任何文件，請先上傳。", "sources": []}

    expanded_question = expand_query_with_synonyms(question)
    context_parts = []
    sources = []
    seen = set()
    message_content = []

    # ── 第一部分：RAG 文字搜尋 ──
    if count > 0:
        results = collection.query(
            query_texts=[expanded_question],
            n_results=min(top_k, count)
        )
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        for doc, meta in zip(docs, metas):
            if meta.get("is_table_image"):
                continue  # 表格圖片用直查，不重複
            context_parts.append(f"【來源：{meta['filename']} 第{meta['page']+1}頁】\n{doc}")
            key = f"{meta['doc_id']}_p{meta['page']}"
            if key not in seen:
                seen.add(key)
                img_path = PAGES_DIR / f"{meta['doc_id']}_page_{meta['page']}.png"
                sources.append({
                    "filename": meta["filename"],
                    "page": meta["page"] + 1,
                    "doc_id": meta["doc_id"],
                    "has_image": img_path.exists(),
                    "relevant_text": doc[:200]
                })

    # ── 第二部分：表格圖片直查 ──
    table_image_names = []
    for tbl in table_images:
        try:
            with open(tbl["img_path"], "rb") as f:
                img_data = base64.standard_b64encode(f.read()).decode("utf-8")
            suffix = tbl["img_path"].suffix.lower()
            media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                         ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
            media_type = media_map.get(suffix, "image/png")
            message_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": img_data}
            })
            table_image_names.append(tbl["filename"])
            sources.append({
                "filename": f"📊 {tbl['filename']}（表格直查）",
                "page": 1,
                "doc_id": tbl["doc_id"],
                "has_image": True,
                "relevant_text": "【表格圖片直查模式】"
            })
            print(f"[KB] 附上表格圖片直查：{tbl['filename']}")
        except Exception as e:
            print(f"[KB] 讀取表格圖片失敗：{e}")

    # ── 組合 prompt ──
    system_prompt = """你是一個封閉式知識庫助手，專門服務台灣銀行財富管理業務。

【核心規則】
1. 只能根據提供的文件內容和圖片回答，絕對不能使用外部知識或自行推測
2. 找不到答案時，明確說「資料庫中無此資訊」
3. 回答時標明資訊來自哪個文件

【客戶類型區分】
嚴格區分不同客戶類型（高資產客戶、專業投資人、一般投資人）的規定，絕對不可混用。

【表格查詢】
如果有附上表格圖片，請直接從圖片逐格查找數值，確保正確，不可憑推測填寫。

【回答格式】
- 用繁體中文回答，數字、時間、條件務必完整列出"""

    text_part = ""
    if context_parts:
        text_part = "【文字資料庫內容】\n\n" + "\n\n---\n\n".join(context_parts) + "\n\n"

    table_part = ""
    if table_image_names:
        table_part = f"【表格圖片】以上圖片為：{', '.join(table_image_names)}，請直接從圖片查找答案。\n\n"

    user_text = (
        f"{system_prompt}\n\n"
        f"{text_part}{table_part}"
        f"問題：{question}\n\n"
        f"請直接根據以上資料回答，如有表格圖片請逐格核對後回答。"
    )

    # 組合圖片列表給 Gemini
    gemini_images = []
    for img_block in message_content:
        if img_block.get("type") == "image":
            src = img_block["source"]
            gemini_images.append({
                "media_type": src["media_type"],
                "data": src["data"]
            })

    try:
        answer = gemini_chat(user_text, gemini_images if gemini_images else None)
    except Exception as e:
        print(f"[KB] Gemini 失敗，改用 Claude：{e}")
        # Gemini 失敗時 fallback 到 Claude
        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            messages=[{"role": "user", "content": message_content + [{"type": "text", "text": user_text}]}]
        )
        answer = response.content[0].text

    return {"answer": answer, "sources": sources}


def get_page_image_base64(doc_id: str, page_num: int) -> str:
    img_path = PAGES_DIR / f"{doc_id}_page_{page_num}.png"
    if not img_path.exists():
        raise FileNotFoundError("頁面圖片不存在")
    with open(img_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def list_documents() -> list:
    try:
        all_items = collection.get()
        docs = {}
        for meta in all_items["metadatas"]:
            doc_id = meta["doc_id"]
            if doc_id not in docs:
                docs[doc_id] = {"doc_id": doc_id, "filename": meta["filename"], "pages": set()}
            docs[doc_id]["pages"].add(meta["page"])
        return [{"doc_id": d["doc_id"], "filename": d["filename"], "page_count": len(d["pages"])} for d in docs.values()]
    except:
        return []


def list_files_detail() -> list:
    try:
        all_items = collection.get()
        doc_map = {}
        for meta in all_items["metadatas"]:
            doc_id = meta["doc_id"]
            if doc_id not in doc_map:
                doc_map[doc_id] = {"doc_id": doc_id, "filename": meta["filename"], "pages": set()}
            doc_map[doc_id]["pages"].add(meta["page"])

        files = []
        for doc_id, info in doc_map.items():
            filename = info["filename"]
            suffix = Path(filename).suffix.lower()
            file_path = UPLOAD_DIR / f"{doc_id}{suffix}"
            size_bytes = 0
            modified_time = ""
            if file_path.exists():
                stat = file_path.stat()
                size_bytes = stat.st_size
                from datetime import datetime
                modified_time = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            if size_bytes < 1024:
                size_str = f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                size_str = f"{size_bytes/1024:.1f} KB"
            else:
                size_str = f"{size_bytes/1024/1024:.1f} MB"
            files.append({
                "doc_id": doc_id, "filename": filename,
                "page_count": len(info["pages"]),
                "size": size_str, "size_bytes": size_bytes,
                "modified": modified_time, "suffix": suffix.lstrip(".")
            })
        files.sort(key=lambda x: x["modified"], reverse=True)
        return files
    except Exception as e:
        print(f"[KB] list_files_detail error: {e}")
        return []


def delete_document(doc_id: str):
    try:
        all_items = collection.get()
        ids_to_delete = [
            id_ for id_, meta in zip(all_items["ids"], all_items["metadatas"])
            if meta["doc_id"] == doc_id
        ]
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
        for img_file in PAGES_DIR.glob(f"{doc_id}_*.png"):
            img_file.unlink()
        for f in UPLOAD_DIR.glob(f"{doc_id}.*"):
            f.unlink()
        # 表格圖片也一起刪
        for f in TABLE_DIR.glob(f"{doc_id}.*"):
            f.unlink()
        unregister_table_image(doc_id)
    except Exception as e:
        print(f"[KB] delete error: {e}")
        raise
