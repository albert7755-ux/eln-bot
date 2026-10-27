"""報價檔上傳後，把全部債券的最新報價送到「海外債知識群島」的私人 repo（quotes/latest.json）。

- 只寫群島 repo 的這一個檔案，不碰龍蝦自己的資料庫和其他功能。
- 需要 Render 環境變數 ISLAND_GH_TOKEN（只能寫 bond-islands 的 GitHub 通行碼）。
  沒設就直接略過，不影響報價檔上傳。
"""
import base64
import json
import os
from datetime import datetime

import requests

ISLAND_REPO = os.getenv("ISLAND_REPO", "albert7755-ux/bond-islands")
ISLAND_BRANCH = os.getenv("ISLAND_BRANCH", "main")
ISLAND_PATH = "quotes/latest.json"


def _s(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    return str(v).strip()


def sync_quotes_to_island(path: str, snap_date=None) -> str:
    """回傳一行結果文字（給上傳頁顯示）。任何錯誤都只回傳訊息，不丟例外。"""
    token = os.getenv("ISLAND_GH_TOKEN", "").strip()
    if not token:
        return "群島同步：未設定（略過）"
    try:
        from bond_coupon_alert import read_bonds
        bonds = []
        for b in read_bonds(path):
            mat = b.get("maturity")
            bonds.append({
                "code": _s(b.get("code")), "isin": b.get("isin", ""), "name": b.get("name", ""),
                "ccy": _s(b.get("ccy")), "ratings": b.get("ratings", ""), "seniority": _s(b.get("seniority")),
                "coupon": _s(b.get("coupon")), "freq": b.get("freq", ""), "offer": _s(b.get("offer")),
                "bid": _s(b.get("bid")), "ytm": _s(b.get("ytm")),
                "maturity": mat.isoformat() if hasattr(mat, "isoformat") else _s(mat),
                "years": _s(b.get("years")), "risk": _s(b.get("risk")), "min_amt": _s(b.get("min_amt")),
                "remark": b.get("remark", ""),
            })
        d = (snap_date or datetime.now().date())
        payload = {"date": d.isoformat(), "updated": datetime.now().isoformat(timespec="seconds"),
                   "bonds": bonds}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        url = f"https://api.github.com/repos/{ISLAND_REPO}/contents/{ISLAND_PATH}"
        head = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}
        body = {"message": f"報價同步 {d.isoformat()}（{len(bonds)} 檔）",
                "content": base64.b64encode(data).decode(), "branch": ISLAND_BRANCH}
        r = requests.get(url + f"?ref={ISLAND_BRANCH}", headers=head, timeout=20)
        if r.status_code == 200:
            body["sha"] = r.json().get("sha")
        r = requests.put(url, headers=head, json=body, timeout=40)
        if r.status_code in (200, 201):
            return f"✅ 群島價格已同步（{len(bonds)} 檔）"
        return f"⚠️ 群島同步失敗（{r.status_code}），群島會先沿用前一次的價格"
    except Exception as e:
        print(f"[IslandSync] {e}")
        return "⚠️ 群島同步失敗，群島會先沿用前一次的價格"
