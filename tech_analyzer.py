import os
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.dates as mdates
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import anthropic

# 字體設定（使用預設英文字體，避免Render環境缺少中文字體）
import matplotlib.font_manager as fm

# ── 暴風雨前的寧靜色調（跟龍蝦日報一致）──────
BG       = '#0D1B2A'
BG_MID   = '#1B2A3B'
BG_LIGHT = '#22344A'
SILVER   = '#B8C4CC'
WHITE    = '#E8EDF2'
MUTED    = '#6A8090'
GOLD     = '#C9A84C'
WARNING  = '#D4574A'
CALM     = '#4A7FA5'
GREEN    = '#4CAF7A'

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ══════════════════════════════════════════════
# 工具函數
# ══════════════════════════════════════════════

def calc_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def ticker_display(ticker):
    """美股直接顯示，台股加.TW"""
    return ticker

def resolve_ticker(raw):
    """自動判斷台股/美股"""
    raw = raw.upper().strip()
    # 純數字 → 台股
    if raw.isdigit():
        return raw + ".TW"
    # 已有 .TW/.TWO
    if raw.endswith(".TW") or raw.endswith(".TWO"):
        return raw
    return raw

# ══════════════════════════════════════════════
# 單一股票技術分析
# ══════════════════════════════════════════════

def analyze_single(ticker_raw: str, months: int = 6) -> tuple[bytes, str]:
    """
    回傳 (PNG bytes, 文字摘要)
    """
    ticker = resolve_ticker(ticker_raw)
    end = datetime.today()
    start = end - timedelta(days=months * 31)

    stock = yf.Ticker(ticker)
    df = stock.history(start=start, end=end)
    info = stock.info

    if df.empty:
        raise ValueError(f"找不到 {ticker} 的數據，請確認股票代號")

    name = info.get("shortName", ticker)
    currency = info.get("currency", "USD")

    # 計算指標
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["RSI"]  = calc_rsi(df["Close"])

    # ── 畫圖 ───────────────────────────────────
    fig = plt.figure(figsize=(12, 9), facecolor=BG)
    gs = gridspec.GridSpec(3, 1, height_ratios=[3, 1, 1],
                           hspace=0.08, figure=fig)

    # --- 上圖：K線 + 均線 ---
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor(BG_MID)

    # K線（蠟燭）
    up   = df[df["Close"] >= df["Open"]]
    down = df[df["Close"] <  df["Open"]]

    # 漲（綠）
    ax1.bar(up.index,   up["Close"]-up["Open"],   bottom=up["Open"],   color=GREEN,   width=0.6, alpha=0.85)
    ax1.bar(up.index,   up["High"]-up["Close"],   bottom=up["Close"],  color=GREEN,   width=0.15, alpha=0.7)
    ax1.bar(up.index,   up["Open"]-up["Low"],     bottom=up["Low"],    color=GREEN,   width=0.15, alpha=0.7)
    # 跌（紅）
    ax1.bar(down.index, down["Open"]-down["Close"],bottom=down["Close"],color=WARNING, width=0.6, alpha=0.85)
    ax1.bar(down.index, down["High"]-down["Open"], bottom=down["Open"], color=WARNING, width=0.15, alpha=0.7)
    ax1.bar(down.index, down["Close"]-down["Low"], bottom=down["Low"],  color=WARNING, width=0.15, alpha=0.7)

    ax1.plot(df.index, df["MA20"], color=GOLD,  linewidth=1.4, label="MA20", alpha=0.9)
    ax1.plot(df.index, df["MA60"], color=CALM,  linewidth=1.4, label="MA60", alpha=0.9)

    ax1.set_title(f"{name} ({ticker}) | Past {months}M Technical Analysis",
                  color=WHITE, fontsize=13, fontweight="bold", pad=10)
    ax1.set_ylabel(f"Price ({currency})", color=MUTED, fontsize=9)
    ax1.legend(loc="upper left", fontsize=8, facecolor=BG_MID,
               edgecolor=MUTED, labelcolor=WHITE)
    ax1.tick_params(colors=MUTED, labelbottom=False)
    for sp in ax1.spines.values(): sp.set_color(BG_LIGHT)
    ax1.grid(axis="y", color=BG_LIGHT, linewidth=0.5, alpha=0.6)

    # 最新價標註
    last_price = df["Close"].iloc[-1]
    ax1.annotate(f"  {last_price:.2f}",
                 xy=(df.index[-1], last_price),
                 color=GOLD, fontsize=9, fontweight="bold",
                 xytext=(5, 0), textcoords="offset points")

    # --- 中圖：成交量 ---
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax2.set_facecolor(BG_MID)

    vol_colors = [GREEN if df["Close"].iloc[i] >= df["Open"].iloc[i] else WARNING
                  for i in range(len(df))]
    ax2.bar(df.index, df["Volume"] / 1e6, color=vol_colors, alpha=0.75, width=0.6)
    ax2.set_ylabel("Volume (M)", color=MUTED, fontsize=8)
    ax2.tick_params(colors=MUTED, labelbottom=False)
    for sp in ax2.spines.values(): sp.set_color(BG_LIGHT)
    ax2.grid(axis="y", color=BG_LIGHT, linewidth=0.5, alpha=0.4)

    # --- 下圖：RSI ---
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    ax3.set_facecolor(BG_MID)

    ax3.plot(df.index, df["RSI"], color=GOLD, linewidth=1.5)
    ax3.axhline(70, color=WARNING, linestyle="--", linewidth=0.9, alpha=0.7)
    ax3.axhline(30, color=GREEN,   linestyle="--", linewidth=0.9, alpha=0.7)
    ax3.fill_between(df.index, df["RSI"], 70,
                     where=(df["RSI"] >= 70), color=WARNING, alpha=0.15)
    ax3.fill_between(df.index, df["RSI"], 30,
                     where=(df["RSI"] <= 30), color=GREEN, alpha=0.15)
    ax3.text(df.index[-1], 71, " Overbought", color=WARNING, fontsize=7.5, va="bottom")
    ax3.text(df.index[-1], 29, " Oversold", color=GREEN,   fontsize=7.5, va="top")
    ax3.set_ylim(0, 100)
    ax3.set_ylabel("RSI(14)", color=MUTED, fontsize=8)
    ax3.tick_params(colors=MUTED)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax3.xaxis.set_major_locator(mdates.WeekdayLocator(interval=3))
    plt.setp(ax3.xaxis.get_majorticklabels(), rotation=30, ha="right")
    for sp in ax3.spines.values(): sp.set_color(BG_LIGHT)
    ax3.grid(axis="y", color=BG_LIGHT, linewidth=0.5, alpha=0.4)

    plt.tight_layout(pad=1.2)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    img_bytes = buf.read()

    # ── Claude 生成文字摘要 ──────────────────────
    rsi_now   = df["RSI"].iloc[-1]
    ret_6m    = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
    ma20_now  = df["MA20"].iloc[-1]
    ma60_now  = df["MA60"].iloc[-1]
    high_6m   = df["High"].max()
    low_6m    = df["Low"].min()
    vol_avg   = df["Volume"].mean()
    vol_latest= df["Volume"].iloc[-1]

    summary_prompt = (
        f"請用繁體中文，以投資輔銷的角度，針對以下技術數據給出簡短分析（約150字）：\n\n"
        f"股票：{name} ({ticker})\n"
        f"近{months}個月報酬：{ret_6m:.1f}%\n"
        f"現價：{last_price:.2f} {currency}\n"
        f"MA20：{ma20_now:.2f}  MA60：{ma60_now:.2f}\n"
        f"目前價格{'高於' if last_price > ma20_now else '低於'} MA20，"
        f"{'高於' if last_price > ma60_now else '低於'} MA60\n"
        f"RSI(14)：{rsi_now:.1f}（{'超買區' if rsi_now>70 else '超賣區' if rsi_now<30 else '中性區'}）\n"
        f"近{months}個月高點：{high_6m:.2f}  低點：{low_6m:.2f}\n"
        f"近期成交量{'高於' if vol_latest > vol_avg else '低於'}均量\n\n"
        "請包含：①趨勢判斷 ②支撐壓力 ③RSI訊號 ④一句操作建議\n"
        "格式：條列式，每點前面加 emoji，專業但口語"
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{"role": "user", "content": summary_prompt}]
    )
    summary = resp.content[0].text

    return img_bytes, summary


# ══════════════════════════════════════════════
# MAG7 比較分析（散點圖 + 表格）
# ══════════════════════════════════════════════

MAG7 = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "GOOGL": "Google",
    "AMZN": "Amazon",
    "NVDA": "NVIDIA",
    "META": "Meta",
    "TSLA": "Tesla",
}

def analyze_mag7(months: int = 6) -> tuple[bytes, str]:
    end   = datetime.today()
    start = end - timedelta(days=months * 31)

    results = {}
    for ticker, name in MAG7.items():
        try:
            df = yf.download(ticker, start=start, end=end, progress=False)
            if df.empty or len(df) < 10:
                continue
            close = df["Close"].squeeze()
            ret   = (close.iloc[-1] / close.iloc[0] - 1) * 100
            daily = close.pct_change().dropna()
            vol   = daily.std() * (252 ** 0.5) * 100
            sharpe = (ret / months * 12) / vol if vol > 0 else 0
            results[ticker] = {
                "name": name, "ret": float(ret),
                "vol": float(vol), "sharpe": float(sharpe),
                "price": float(close.iloc[-1])
            }
        except Exception:
            continue

    if not results:
        raise ValueError("無法取得 Mag7 數據")

    # ── 畫圖：散點圖（2x1）──────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), facecolor=BG)

    # 左：散點圖 報酬 vs 波動率
    ax = axes[0]
    ax.set_facecolor(BG_MID)

    colors_map = {t: (GREEN if results[t]["ret"] >= 0 else WARNING)
                  for t in results}

    for ticker, d in results.items():
        color = colors_map[ticker]
        size  = max(80, abs(d["sharpe"]) * 120)
        ax.scatter(d["vol"], d["ret"], s=size, color=color,
                   alpha=0.85, edgecolors=WHITE, linewidth=0.8, zorder=3)
        ax.annotate(ticker,
                    xy=(d["vol"], d["ret"]),
                    xytext=(5, 5), textcoords="offset points",
                    color=WHITE, fontsize=9, fontweight="bold")

    ax.axhline(0, color=MUTED, linewidth=0.8, linestyle="--", alpha=0.6)
    ax.set_xlabel("Annualized Volatility (%)", color=MUTED, fontsize=9)
    ax.set_ylabel(f"Return (%)", color=MUTED, fontsize=9)
    ax.set_title("Return vs Volatility", color=WHITE, fontsize=11, fontweight="bold")
    ax.tick_params(colors=MUTED)
    for sp in ax.spines.values(): sp.set_color(BG_LIGHT)
    ax.grid(color=BG_LIGHT, linewidth=0.5, alpha=0.5)

    # 圖例說明
    ax.text(0.02, 0.98, "Circle size = Sharpe Ratio",
            transform=ax.transAxes, color=MUTED, fontsize=7.5,
            va="top", ha="left")

    # 右：橫向長條圖 報酬排行
    ax2 = axes[1]
    ax2.set_facecolor(BG_MID)

    sorted_r = sorted(results.items(), key=lambda x: x[1]["ret"], reverse=True)
    tickers  = [r[0] for r in sorted_r]
    rets     = [r[1]["ret"] for r in sorted_r]
    bar_colors = [GREEN if r >= 0 else WARNING for r in rets]

    bars = ax2.barh(tickers, rets, color=bar_colors, height=0.55,
                    edgecolor="none", alpha=0.85)
    ax2.axvline(0, color=MUTED, linewidth=1, alpha=0.7)

    for bar, ret in zip(bars, rets):
        ax2.text(ret + (0.5 if ret >= 0 else -0.5),
                 bar.get_y() + bar.get_height() / 2,
                 f"{ret:+.1f}%",
                 va="center", ha="left" if ret >= 0 else "right",
                 color=WHITE, fontsize=9, fontweight="bold")

    ax2.set_xlabel(f"Return (%)", color=MUTED, fontsize=9)
    ax2.set_title("Return Ranking", color=WHITE, fontsize=11, fontweight="bold")
    ax2.tick_params(colors=MUTED)
    ax2.invert_yaxis()
    for sp in ax2.spines.values(): sp.set_color(BG_LIGHT)
    ax2.grid(axis="x", color=BG_LIGHT, linewidth=0.5, alpha=0.5)

    fig.suptitle(
        f"Magnificent Seven Technical Analysis | Past {months} Months "
        f"({(end - timedelta(days=months*31)).strftime('%Y/%m')} - {end.strftime('%Y/%m')})",
        color=WHITE, fontsize=13, fontweight="bold", y=1.01
    )
    plt.tight_layout(pad=1.5)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    img_bytes = buf.read()

    # ── Claude 生成文字摘要 ──────────────────────
    table_lines = []
    for ticker, d in sorted(results.items(), key=lambda x: x[1]["ret"], reverse=True):
        sign = "✅" if d["ret"] >= 0 else "❌"
        table_lines.append(
            f"{sign} {ticker} ({d['name']}): "
            f"報酬{d['ret']:+.1f}% | 波動{d['vol']:.1f}% | Sharpe {d['sharpe']:.2f}"
        )

    summary_prompt = (
        f"請用繁體中文，以投資輔銷的角度，針對以下Magnificent Seven近{months}個月表現給出分析（約200字）：\n\n"
        + "\n".join(table_lines)
        + "\n\n請包含：①最佳/最差表現 ②風險調整後表現(Sharpe) "
        "③整體科技股趨勢判斷 ④一句給客戶的佈局建議\n"
        "格式：條列式，每點前面加 emoji，專業但口語，結尾用這句：\n"
        "「市場修正是市場送你的禮物，敢不敢拆，決定了你未來的報酬。」🎁"
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        messages=[{"role": "user", "content": summary_prompt}]
    )
    summary = resp.content[0].text

    return img_bytes, summary
