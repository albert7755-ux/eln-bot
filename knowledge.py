import os
import uuid
import base64
from pathlib import Path

import anthropic
import chromadb
from chromadb.utils import embedding_functions
import fitz  # PyMuPDF
from PIL import Image

# ── 路徑設定（存在 Render Disk /data 底下）──
BASE_DIR = Path("/data/knowledge")
UPLOAD_DIR = BASE_DIR / "uploads"
PAGES_DIR = BASE_DIR / "page_images"
CHROMA_DIR = BASE_DIR / "chroma_db"

for d in [UPLOAD_DIR, PAGES_DIR, CHROMA_DIR]:
    d.mkdir(parents=True, exist_ok=True)

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
    """用 Claude Vision 讀取單一頁面圖片"""
    with open(img_path, "rb") as f:
        img_data = base64.standard_b64encode(f.read()).decode("utf-8")

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}},
                {"type": "text", "text": (
                    "請完整擷取並描述這張圖片的所有內容，包含：\n"
                    "1. 所有文字內容（逐字擷取）\n"
                    "2. 表格內容（保留數字和欄位名稱）\n"
                    "3. 圖表說明（標題、數據、趨勢）\n"
                    "4. 重點標記或強調的內容\n"
                    "請用繁體中文回答，盡量完整不要省略。"
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
    """獨立圖片檔案用 Claude Vision 描述"""
    suffix = img_path.suffix.lower()
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
    media_type = media_map.get(suffix, "image/png")

    with open(img_path, "rb") as f:
        img_data = base64.standard_b64encode(f.read()).decode("utf-8")

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_data}},
                {"type": "text", "text": (
                    "請完整擷取並描述這張圖片的所有內容，包含：\n"
                    "1. 所有文字內容（逐字擷取）\n"
                    "2. 表格內容（保留數字和欄位名稱）\n"
                    "3. 圖表說明（標題、數據、趨勢）\n"
                    "4. 重點標記或強調的內容\n"
                    "請用繁體中文回答，盡量完整不要省略。"
                )}
            ]
        }]
    )
    return [{"page": 0, "text": response.content[0].text}]


def chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> list:
    """中文友善切割，chunk_size=400 平衡精準度與上下文"""
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


def process_and_index_file(filename: str, file_bytes: bytes) -> dict:
    """上傳並處理檔案，存入向量資料庫"""
    doc_id = str(uuid.uuid4())[:8]
    suffix = Path(filename).suffix.lower()
    saved_path = UPLOAD_DIR / f"{doc_id}{suffix}"

    with open(saved_path, "wb") as f:
        f.write(file_bytes)

    pages_data = []

    if suffix == ".pdf":
        pages_data = _process_pdf_pages(saved_path, doc_id)

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
        if not text.strip():
            continue
        for chunk_idx, chunk in enumerate(chunk_text(text)):
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
    """問問題，從資料庫找答案"""
    count = collection.count()
    if count == 0:
        return {"answer": "資料庫中尚無任何文件，請先上傳。", "sources": []}

    expanded_question = expand_query_with_synonyms(question)

    results = collection.query(
        query_texts=[expanded_question],
        n_results=min(top_k, count)
    )

    docs = results["documents"][0]
    metas = results["metadatas"][0]

    context_parts = []
    sources = []
    seen = set()

    for doc, meta in zip(docs, metas):
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

    context = "\n\n---\n\n".join(context_parts)

    system_prompt = """你是一個封閉式知識庫助手。
你只能根據使用者提供的文件內容來回答問題。
如果提供的資料中找不到答案，請明確說「資料庫中無此資訊」。
絕對不能使用任何外部知識或自行推測。
請用繁體中文回答，盡量詳細完整，並標明資訊來自哪個文件的哪一頁。"""

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=3000,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"以下是資料庫中找到的相關內容：\n\n{context}\n\n問題：{question}\n\n請根據以上內容詳細回答，不要省略重要細節。"
        }]
    )

    return {"answer": response.content[0].text, "sources": sources}


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
    except Exception as e:
        print(f"[KB] delete error: {e}")
        raise
