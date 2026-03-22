"""
eln_chart.py
給 /chart 商品代號 使用
從 DB 撈標的資料 → yfinance 下載股價 → matplotlib 畫圖 → Imgur 上傳
"""
import os
import io
import base64
import requests
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import pandas as pd

IMGUR_CLIENT_ID = os.environ.get("IMGUR_CLIENT_ID", "")


def upload_to_imgur(image_bytes: bytes) -> str:
    if not IMGUR_CLIENT_ID:
        raise RuntimeError("Missing IMGUR_CLIENT_ID")
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    resp = requests.post(
        "https://api.imgur.com/3/image",
        headers={"Authorization": f"Client-ID {IMGUR_CLIENT_ID}"},
        data={"image": b64, "type": "base64"},
        timeout=30,
    )
    data = resp.json()
    if resp.status_code == 200 and data.get("success"):
        return data["data"]["link"]
    raise RuntimeError(f"Imgur upload failed: {data}")


def generate_eln_chart(bond_id: str, engine) -> str:
    """
    從 DB 撈商品資料，畫走勢圖 + 防守線，回傳 Imgur URL
    """
    from sqlalchemy import text

    # 從 DB 撈 detail
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT detail FROM eln_detail WHERE bond_id=:b LIMIT 1"),
            {"b": bond_id.upper()}
        ).fetchone()

    if not row or not row[0]:
        raise ValueError(f"找不到商品 {bond_id}")

    detail = row[0]

    # 解析 detail 取得標的資訊
    import re
    assets = []
    # 找每個標的區塊：【TICKER】\n原: xxx\n現: xxx\n...\n KO價: xxx\n KI價: xxx (xx%)
    blocks = re.split(r"\n(?=【)", detail)
    for block in blocks:
        m_ticker = re.search(r"【(\w+)】", block)
        m_initial = re.search(r"原:\s*([\d.]+)", block)
        m_ko = re.search(r"KO價:\s*([\d.]+)", block)
        m_ki = re.search(r"KI價:\s*([\d.]+)", block)
        m_strike = re.search(r"Strike:\s*([\d.]+)", block)
        if not m_ticker or not m_initial:
            continue
        asset = {
            "ticker": m_ticker.group(1),
            "initial": float(m_initial.group(1)),
            "ko": float(m_ko.group(1)) if m_ko else None,
            "ki": float(m_ki.group(1)) if m_ki else None,
            "strike": float(m_strike.group(1)) if m_strike else None,
        }
        assets.append(asset)

    if not assets:
        raise ValueError(f"找不到 {bond_id} 的標的資料")

    # 解析發行日
    m_trade = re.search(r"交易日:\s*(\d{4}-\d{2}-\d{2})", detail)
    if m_trade:
        start_date = datetime.strptime(m_trade.group(1), "%Y-%m-%d") - timedelta(days=7)
    else:
        start_date = datetime.now() - timedelta(days=180)

    end_date = datetime.now() + timedelta(days=1)

    # 下載股價
    tickers = [a["ticker"] for a in assets]
    try:
        hist = yf.download(tickers, start=start_date, end=end_date, auto_adjust=True)["Close"]
        if isinstance(hist, pd.Series):
            hist = hist.to_frame(name=tickers[0])
    except Exception as e:
        raise ValueError(f"股價下載失敗: {e}")

    # 畫圖
    n = len(assets)
    fig, axes = plt.subplots(n, 1, figsize=(10, 4 * n), facecolor="#0D1117")
    if n == 1:
        axes = [axes]

    fig.suptitle(f"📊 {bond_id} 防守線走勢圖", fontsize=14, color="#D4A843",
                 fontweight="bold", y=1.01)

    for ax, asset in zip(axes, assets):
        ticker = asset["ticker"]
        ax.set_facecolor("#161B22")

        if ticker not in hist.columns:
            ax.text(0.5, 0.5, f"{ticker} 無資料", transform=ax.transAxes,
                    ha="center", color="white")
            continue

        prices = hist[ticker].dropna()
        if prices.empty:
            continue

        # 畫股價線
        ax.plot(prices.index, prices.values, color="#58A6FF", linewidth=1.5,
                label=f"{ticker} 股價")
        ax.fill_between(prices.index, prices.values, alpha=0.1, color="#58A6FF")

        # KO 線（綠色）
        if asset["ko"]:
            ax.axhline(y=asset["ko"], color="#3FB950", linestyle="--",
                       linewidth=1.2, label=f"🟢 KO {asset['ko']:.2f}")
            ax.text(prices.index[-1], asset["ko"], f" KO {asset['ko']:.2f}",
                    color="#3FB950", va="bottom", fontsize=8)

        # KI 線（紅色）
        if asset["ki"]:
            ax.axhline(y=asset["ki"], color="#F85149", linestyle="--",
                       linewidth=1.2, label=f"🔴 KI {asset['ki']:.2f}")
            ax.text(prices.index[-1], asset["ki"], f" KI {asset['ki']:.2f}",
                    color="#F85149", va="top", fontsize=8)

        # Strike 線（藍色）
        if asset["strike"]:
            ax.axhline(y=asset["strike"], color="#79C0FF", linestyle="--",
                       linewidth=1.2, label=f"🔵 Strike {asset['strike']:.2f}")
            ax.text(prices.index[-1], asset["strike"], f" Strike {asset['strike']:.2f}",
                    color="#79C0FF", va="bottom", fontsize=8)

        ax.set_title(f"{ticker}", color="#E6EDF3", fontsize=12, fontweight="bold")
        ax.tick_params(colors="#8B949E")
        ax.spines["bottom"].set_color("#30363D")
        ax.spines["left"].set_color("#30363D")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.legend(loc="upper left", fontsize=8, facecolor="#161B22",
                  labelcolor="#E6EDF3", framealpha=0.8)
        ax.yaxis.label.set_color("#8B949E")
        ax.grid(axis="y", color="#30363D", linewidth=0.5, alpha=0.5)

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor="#0D1117")
    plt.close(fig)
    buf.seek(0)

    img_bytes = buf.read()
    url = upload_to_imgur(img_bytes)
    return url
