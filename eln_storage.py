import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from supabase import create_client

TZ_TAIPEI = timezone(timedelta(hours=8))
BUCKET_NAME = "eln-bot-file"
LATEST_NAME = "latest_eln.xlsx"

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

if not SUPABASE_URL:
    raise RuntimeError("Missing env: SUPABASE_URL")
if not SUPABASE_KEY:
    raise RuntimeError("Missing env: SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def _timestamp():
    return datetime.now(TZ_TAIPEI).strftime("%Y%m%d_%H%M%S")

def upload_eln_excel(local_path: str):
    p = Path(local_path)
    if not p.exists():
        raise FileNotFoundError(local_path)

    file_bytes = p.read_bytes()
    filename = p.name
    content_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if filename.lower().endswith(".xlsx")
        else "application/vnd.ms-excel"
    )
    history_path = f"history/{_timestamp()}_{filename}"

    supabase.storage.from_(BUCKET_NAME).upload(
        history_path,
        file_bytes,
        {"content-type": content_type, "upsert": "false"},
    )

    supabase.storage.from_(BUCKET_NAME).upload(
        LATEST_NAME,
        file_bytes,
        {"content-type": content_type, "upsert": "true"},
    )

    return {"history": history_path, "latest": LATEST_NAME}

def download_latest_eln(local_path="/tmp/latest_eln.xlsx"):
    data = supabase.storage.from_(BUCKET_NAME).download(LATEST_NAME)
    with open(local_path, "wb") as f:
        f.write(data)
    return local_path

def list_history(limit=20):
    items = supabase.storage.from_(BUCKET_NAME).list("history")
    if not items:
        return []
    names = [x["name"] for x in items if isinstance(x, dict) and x.get("name")]
    names.sort(reverse=True)
    return names[:limit]

def latest_exists():
    items = supabase.storage.from_(BUCKET_NAME).list()
    if not items:
        return False
    return any(item.get("name") == LATEST_NAME for item in items if isinstance(item, dict))
