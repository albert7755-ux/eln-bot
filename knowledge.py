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

# ── ChromaDB：改用多語言 embedding 模型 ──
chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)
collection = chroma_client.get_or_create_collection(
    name="knowledge_base_v2",
    embedding_function=embedding_fn,
    metadata={"hnsw:space": "cosine"}
)

# ── Claude ──
claude_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def pdf_to_page_images(pdf_path: Path, doc_id: str):
    """PDF每頁轉圖片"""
    doc = fitz.open(str(pdf_path))
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)
        img_path = PAGES_DIR / f"{doc_id}_page_{page_num}.png"
        pix.save(str(img_path))
    doc.close()


def extract_text_from_pdf(pdf_path: Path):
    """抽取PDF每頁文字"""
    doc = fitz.open(str(pdf_path))
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text().strip()
        if text:
            pages.append({"page": i, "text": text})
    doc.close()
    return pages


def convert_to_pdf(file_path: Path) -> Path:
    """PPT/Word 轉 PDF"""
    pdf_path = file_path.with_suffix(".pdf")
    os.system(f'libreoffice --headless --convert-to pdf "{file_path}" --outdir "{file_path.parent}"')
    return pdf_path


def process_image_file(img_path: Path):
    """圖片用 Claude Vision 描述"""
    with open(img_path, "rb") as f:
        img_data = base64.standard_b64encode(f.read()).decode("utf-8")

    suffix = img_path.suffix.lower()
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
    media_type = media_map.get(suffix, "image/png")

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_data}},
                {"type": "text", "text": "請詳細描述這張圖片的所有內容，包含文字、圖表、數據等，用繁體中文回答。"}
            ]
        }]
    )
    return [{"page": 0, "text": response.content[0].text}]


def chunk_text(text: str, chunk_size: int = 300, overlap: int = 50) -> list[str]:
    """
    中文友善的切割方式：
    - 按字數切（不用空格）
    - 優先在句號、換行處切割
    - overlap 讓相鄰段落有重疊，避免答案被切斷
    """
    # 先按段落切
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    
    chunks = []
    current = ""
    
    for para in paragraphs:
        # 如果加入這段還不超過限制，直接合併
        if len(current) + len(para) <= chunk_size:
            current = current + para if not current else current + "\n" + para
        else:
            # 先把現有的存起來
            if current:
                chunks.append(current)
            # 如果單一段落超過限制，按字數強制切
            if len(para) > chunk_size:
                for i in range(0, len(para), chunk_size - overlap):
                    chunks.append(para[i:i + chunk_size])
                current = para[-(overlap):]  # 保留結尾作為下一段的開頭
            else:
                current = para
    
    if current:
        chunks.append(current)
    
    return [c for c in chunks if len(c.strip()) > 10]  # 過濾太短的段落


def process_and_index_file(filename: str, file_bytes: bytes) -> dict:
    """上傳並處理檔案，存入向量資料庫"""
    doc_id = str(uuid.uuid4())[:8]
    suffix = Path(filename).suffix.lower()
    saved_path = UPLOAD_DIR / f"{doc_id}{suffix}"

    with open(saved_path, "wb") as f:
        f.write(file_bytes)

    pages_data = []
    pdf_path = None

    if suffix == ".pdf":
        pdf_path = saved_path
        pages_data = extract_text_from_pdf(saved_path)

    elif suffix in [".pptx", ".ppt", ".docx", ".doc"]:
        pdf_path = convert_to_pdf(saved_path)
        if pdf_path.exists():
            pages_data = extract_text_from_pdf(pdf_path)

    elif suffix in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        pages_data = process_image_file(saved_path)
        img_dest = PAGES_DIR / f"{doc_id}_page_0.png"
        Image.open(saved_path).save(str(img_dest))

    if not pages_data:
        raise ValueError("無法解析此檔案內容")

    if pdf_path and pdf_path.exists():
        pdf_to_page_images(pdf_path, doc_id)

    total_chunks = 0
    for page_info in pages_data:
        page_num = page_info["page"]
        for chunk_idx, chunk in enumerate(chunk_text(page_info["text"])):
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


def query_knowledge(question: str, top_k: int = 8) -> dict:
    """問問題，從資料庫找答案（搜尋更多段落）"""
    count = collection.count()
    if count == 0:
        return {"answer": "資料庫中尚無任何文件，請先上傳。", "sources": []}

    results = collection.query(
        query_texts=[question],
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
    """取得頁面截圖的 base64"""
    img_path = PAGES_DIR / f"{doc_id}_page_{page_num}.png"
    if not img_path.exists():
        raise FileNotFoundError("頁面圖片不存在")
    with open(img_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def list_documents() -> list:
    """列出所有文件"""
    try:
        all_items = collection.get()
        docs = {}
        for meta in all_items["metadatas"]:
            doc_id = meta["doc_id"]
            if doc_id not in docs:
                docs[doc_id] = {
                    "doc_id": doc_id,
                    "filename": meta["filename"],
                    "pages": set()
                }
            docs[doc_id]["pages"].add(meta["page"])
        return [{"doc_id": d["doc_id"], "filename": d["filename"],
                 "page_count": len(d["pages"])} for d in docs.values()]
    except:
        return []


def delete_document(doc_id: str):
    """刪除文件"""
    all_items = collection.get()
    ids_to_delete = [
        id_ for id_, meta in zip(all_items["ids"], all_items["metadatas"])
        if meta["doc_id"] == doc_id
    ]
    if ids_to_delete:
        collection.delete(ids=ids_to_delete)
    for img_file in PAGES_DIR.glob(f"{doc_id}_*.png"):
        img_file.unlink()
