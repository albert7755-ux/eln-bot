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

import matplotlib.font_manager as fm

# ── 色調 ──────────────────────────────────────
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

def resolve_ticker(raw):
    raw = raw.upper().strip()
    if raw.isdigit():
        return raw + ".TW"
    if raw.endswith(".TW") or raw.endswith(".TWO"):
        return raw
    return raw

def calc_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

# ══════════════════════════════════════════════
# 完整三面向分析
# ══════════════════════════════════════════════

def full_analysis(ticker_raw: str, months: int = 6) -> tuple[bytes, str]:
    ticker = resolve_ticker(ticker_raw)
    end   = datetime.today()
    start = end - timedelta(days=months * 31)

    stock = yf.Ticker(ticker)
    df    = stock.history(start=start, end=end)
    info  = stock.info

    if df.empty:
        raise ValueError(f"Cannot find data for {ticker}")

    name     = info.get("shortName", ticker)
    currency = info.get("currency", "USD")

    # ── 技術指標 ──────────────────────────────
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["RSI"]  = calc_rsi(df["Close"])

    # ── 基本面數據 ────────────────────────────
    pe_ratio     = info.get("trailingPE", None)
    fwd_pe       = info.get("forwardPE", None)
    eps          = info.get("trailingEps", None)
    gross_margin = info.get("grossMargins", None)
    debt_equity  = info.get("debtToEquity", None)
    roe          = info.get("returnOnEquity", None)
    rev_growth   = info.get("revenueGrowth", None)
    mkt_cap      = info.get("marketCap", None)
    div_yield    = info.get("dividendYield", None)

    # 季度 EPS 和營收
    try:
        earnings_q = stock.quarterly_earnings
    except Exception:
        earnings_q = None
    try:
        financials_q = stock.quarterly_financials
    except Exception:
        financials_q = None

    # ── 新聞 ──────────────────────────────────
    try:
        news = stock.news[:10] if stock.news else []
    except Exception:
        news = []

    # ══════════════════════════════════════════
    # 畫圖：3行2列
    # ══════════════════════════════════════════
    fig = plt.figure(figsize=(14, 12), facecolor=BG)
    gs  = gridspec.GridSpec(3, 2, figure=fig,
                            hspace=0.45, wspace=0.32,
                            left=0.07, right=0.97,
                            top=0.93, bottom=0.06)

    # ── 圖1：K線 + 均線（左上，跨兩行）──────
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_facecolor(BG_MID)

    up   = df[df["Close"] >= df["Open"]]
    down = df[df["Close"] <  df["Open"]]
    ax1.bar(up.index,   up["Close"]-up["Open"],   bottom=up["Open"],   color=GREEN,   width=0.6, alpha=0.85)
    ax1.bar(up.index,   up["High"]-up["Close"],   bottom=up["Close"],  color=GREEN,   width=0.15)
    ax1.bar(up.index,   up["Open"]-up["Low"],     bottom=up["Low"],    color=GREEN,   width=0.15)
    ax1.bar(down.index, down["Open"]-down["Close"],bottom=down["Close"],color=WARNING, width=0.6, alpha=0.85)
    ax1.bar(down.index, down["High"]-down["Open"], bottom=down["Open"], color=WARNING, width=0.15)
    ax1.bar(down.index, down["Close"]-down["Low"], bottom=down["Low"],  color=WARNING, width=0.15)
    ax1.plot(df.index, df["MA20"], color=GOLD, linewidth=1.3, label="MA20")
    ax1.plot(df.index, df["MA60"], color=CALM, linewidth=1.3, label="MA60")

    last_price = df["Close"].iloc[-1]
    ret_6m = (last_price / df["Close"].iloc[0] - 1) * 100
    ret_color = GREEN if ret_6m >= 0 else WARNING
    ax1.set_title(f"{name}  {last_price:.2f} {currency}  ({ret_6m:+.1f}%)",
                  color=ret_color, fontsize=10, fontweight="bold")
    ax1.legend(fontsize=7, facecolor=BG_MID, edgecolor=MUTED, labelcolor=WHITE, loc="upper left")
    ax1.tick_params(colors=MUTED, labelsize=7)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax1.xaxis.set_major_locator(mdates.WeekdayLocator(interval=4))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=6)
    for sp in ax1.spines.values(): sp.set_color(BG_LIGHT)
    ax1.grid(axis="y", color=BG_LIGHT, linewidth=0.5, alpha=0.5)
    ax1.set_ylabel(f"Price ({currency})", color=MUTED, fontsize=8)

    # ── 圖2：RSI（左中）──────────────────────
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.set_facecolor(BG_MID)
    ax2.plot(df.index, df["RSI"], color=GOLD, linewidth=1.5)
    ax2.axhline(70, color=WARNING, linestyle="--", linewidth=0.9, alpha=0.7)
    ax2.axhline(30, color=GREEN,   linestyle="--", linewidth=0.9, alpha=0.7)
    ax2.fill_between(df.index, df["RSI"], 70, where=(df["RSI"]>=70), color=WARNING, alpha=0.15)
    ax2.fill_between(df.index, df["RSI"], 30, where=(df["RSI"]<=30), color=GREEN,   alpha=0.15)
    rsi_now = df["RSI"].iloc[-1]
    ax2.set_title(f"RSI(14): {rsi_now:.1f}  {'Overbought' if rsi_now>70 else 'Oversold' if rsi_now<30 else 'Neutral'}",
                  color=WARNING if rsi_now>70 else GREEN if rsi_now<30 else GOLD, fontsize=9)
    ax2.set_ylim(0, 100)
    ax2.tick_params(colors=MUTED, labelsize=7)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax2.xaxis.set_major_locator(mdates.WeekdayLocator(interval=4))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=6)
    for sp in ax2.spines.values(): sp.set_color(BG_LIGHT)
    ax2.grid(axis="y", color=BG_LIGHT, linewidth=0.5, alpha=0.4)
    ax2.set_ylabel("RSI", color=MUTED, fontsize=8)

    # ── 圖3：成交量（左下）──────────────────
    ax3 = fig.add_subplot(gs[2, 0])
    ax3.set_facecolor(BG_MID)
    vol_colors = [GREEN if df["Close"].iloc[i] >= df["Open"].iloc[i] else WARNING
                  for i in range(len(df))]
    ax3.bar(df.index, df["Volume"]/1e6, color=vol_colors, width=0.6, alpha=0.75)
    vol_avg = df["Volume"].mean()
    ax3.axhline(vol_avg/1e6, color=GOLD, linestyle="--", linewidth=1, alpha=0.7)
    ax3.set_title("Volume (M)", color=SILVER, fontsize=9)
    ax3.tick_params(colors=MUTED, labelsize=7)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax3.xaxis.set_major_locator(mdates.WeekdayLocator(interval=4))
    plt.setp(ax3.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=6)
    for sp in ax3.spines.values(): sp.set_color(BG_LIGHT)
    ax3.grid(axis="y", color=BG_LIGHT, linewidth=0.5, alpha=0.4)

    # ── 圖4：基本面 KPI 卡（右上）────────────
    ax4 = fig.add_subplot(gs[0, 1])
    ax4.set_facecolor(BG_MID)
    ax4.axis("off")
    ax4.set_title("Fundamentals", color=GOLD, fontsize=10, fontweight="bold")

    def fmt_val(v, fmt=".1f", suffix=""):
        if v is None: return "N/A"
        if isinstance(v, float) and (v != v): return "N/A"
        try:
            return f"{v:{fmt}}{suffix}"
        except Exception:
            return "N/A"

    def fmt_pct(v):
        if v is None: return "N/A"
        return f"{v*100:.1f}%"

    def fmt_cap(v):
        if v is None: return "N/A"
        if v >= 1e12: return f"${v/1e12:.2f}T"
        if v >= 1e9:  return f"${v/1e9:.1f}B"
        return f"${v/1e6:.0f}M"

    kpis = [
        ("Market Cap",     fmt_cap(mkt_cap)),
        ("P/E (TTM)",      fmt_val(pe_ratio, ".1f", "x")),
        ("Forward P/E",    fmt_val(fwd_pe,   ".1f", "x")),
        ("EPS (TTM)",      fmt_val(eps,      ".2f", f" {currency}")),
        ("Gross Margin",   fmt_pct(gross_margin)),
        ("ROE",            fmt_pct(roe)),
        ("Revenue Growth", fmt_pct(rev_growth)),
        ("Debt/Equity",    fmt_val(debt_equity, ".1f", "x") if debt_equity else "N/A"),
        ("Dividend Yield", fmt_pct(div_yield) if div_yield else "N/A"),
    ]

    for i, (label, value) in enumerate(kpis):
        row = i // 3
        col = i % 3
        x = 0.05 + col * 0.33
        y = 0.82 - row * 0.28

        # 判斷顏色
        val_color = SILVER
        if value != "N/A":
            if label in ["Revenue Growth", "ROE", "Gross Margin"]:
                try:
                    num = float(value.replace("%","").replace("x",""))
                    val_color = GREEN if num > 0 else WARNING
                except Exception:
                    pass

        ax4.text(x, y, label, transform=ax4.transAxes,
                 color=MUTED, fontsize=7.5, va="top")
        ax4.text(x, y - 0.08, value, transform=ax4.transAxes,
                 color=val_color, fontsize=10, fontweight="bold", va="top")

    # ── 圖5：季度EPS趨勢（右中）──────────────
    ax5 = fig.add_subplot(gs[1, 1])
    ax5.set_facecolor(BG_MID)
    ax5.set_title("Quarterly EPS Trend", color=SILVER, fontsize=9)

    plotted_eps = False
    if earnings_q is not None and not earnings_q.empty:
        try:
            eq = earnings_q.tail(8)
            quarters = [str(q)[:7] for q in eq.index]
            eps_vals  = eq["Earnings"].values if "Earnings" in eq.columns else eq.iloc[:, 0].values
            bar_colors = [GREEN if v >= 0 else WARNING for v in eps_vals]
            ax5.bar(range(len(quarters)), eps_vals, color=bar_colors, width=0.6, alpha=0.85)
            ax5.set_xticks(range(len(quarters)))
            ax5.set_xticklabels(quarters, rotation=45, ha="right", fontsize=6.5)
            ax5.axhline(0, color=MUTED, linewidth=0.8)
            plotted_eps = True
        except Exception:
            pass

    if not plotted_eps:
        ax5.text(0.5, 0.5, "EPS data\nnot available",
                 transform=ax5.transAxes, ha="center", va="center",
                 color=MUTED, fontsize=9)

    ax5.tick_params(colors=MUTED, labelsize=7)
    for sp in ax5.spines.values(): sp.set_color(BG_LIGHT)
    ax5.grid(axis="y", color=BG_LIGHT, linewidth=0.5, alpha=0.4)
    ax5.set_ylabel(f"EPS ({currency})", color=MUTED, fontsize=8)

    # ── 圖6：季度營收成長（右下）──────────────
    ax6 = fig.add_subplot(gs[2, 1])
    ax6.set_facecolor(BG_MID)
    ax6.set_title("Quarterly Revenue", color=SILVER, fontsize=9)

    plotted_rev = False
    if financials_q is not None and not financials_q.empty:
        try:
            rev_row = None
            for row_name in ["Total Revenue", "Revenue"]:
                if row_name in financials_q.index:
                    rev_row = financials_q.loc[row_name]
                    break
            if rev_row is not None:
                rev_row = rev_row.dropna().sort_index()[-8:]
                quarters = [str(q)[:7] for q in rev_row.index]
                rev_vals = rev_row.values / 1e9
                ax6.bar(range(len(quarters)), rev_vals, color=CALM, width=0.6, alpha=0.85)
                # 成長率折線
                if len(rev_vals) > 1:
                    growth = [(rev_vals[i]/rev_vals[i-1]-1)*100 if rev_vals[i-1] != 0 else 0
                              for i in range(1, len(rev_vals))]
                    ax6_twin = ax6.twinx()
                    ax6_twin.plot(range(1, len(quarters)), growth, color=GOLD,
                                  linewidth=1.5, marker="o", markersize=3)
                    ax6_twin.axhline(0, color=MUTED, linewidth=0.5, linestyle="--")
                    ax6_twin.set_ylabel("YoY Growth (%)", color=GOLD, fontsize=7)
                    ax6_twin.tick_params(colors=MUTED, labelsize=6)
                    for sp in ax6_twin.spines.values(): sp.set_color(BG_LIGHT)

                ax6.set_xticks(range(len(quarters)))
                ax6.set_xticklabels(quarters, rotation=45, ha="right", fontsize=6.5)
                ax6.set_ylabel("Revenue (B)", color=MUTED, fontsize=8)
                plotted_rev = True
        except Exception:
            pass

    if not plotted_rev:
        ax6.text(0.5, 0.5, "Revenue data\nnot available",
                 transform=ax6.transAxes, ha="center", va="center",
                 color=MUTED, fontsize=9)

    ax6.tick_params(colors=MUTED, labelsize=7)
    for sp in ax6.spines.values(): sp.set_color(BG_LIGHT)
    ax6.grid(axis="y", color=BG_LIGHT, linewidth=0.5, alpha=0.4)

    # 大標題
    fig.suptitle(
        f"{name} ({ticker})  |  Full Analysis  |  {datetime.today().strftime('%Y/%m/%d')}",
        color=WHITE, fontsize=13, fontweight="bold"
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    img_bytes = buf.read()

    # ══════════════════════════════════════════
    # Claude 三面向文字摘要
    # ══════════════════════════════════════════

    # 新聞整理
    news_lines = []
    for n in news[:10]:
        title = n.get("title", "")
        if title:
            news_lines.append(f"- {title}")
    news_text = "\n".join(news_lines) if news_lines else "No recent news available"

    # 技術面數據
    rsi_now  = df["RSI"].iloc[-1]
    ma20_now = df["MA20"].iloc[-1]
    ma60_now = df["MA60"].iloc[-1]

    prompt = (
        f"請用繁體中文，以投資輔銷顧問的角度，對以下數據做完整三面向分析（約300字）：\n\n"
        f"【股票】{name} ({ticker})\n\n"
        f"【技術面】\n"
        f"現價: {last_price:.2f} {currency}，近{months}個月報酬: {ret_6m:+.1f}%\n"
        f"MA20: {ma20_now:.2f}，MA60: {ma60_now:.2f}\n"
        f"RSI(14): {rsi_now:.1f}（{'超買' if rsi_now>70 else '超賣' if rsi_now<30 else '中性'}）\n\n"
        f"【基本面】\n"
        f"市值: {fmt_cap(mkt_cap)}，P/E: {fmt_val(pe_ratio,'.1f','x')}，Forward P/E: {fmt_val(fwd_pe,'.1f','x')}\n"
        f"EPS: {fmt_val(eps,'.2f')}，毛利率: {fmt_pct(gross_margin)}，ROE: {fmt_pct(roe)}\n"
        f"營收成長: {fmt_pct(rev_growth)}，負債比: {fmt_val(debt_equity,'.1f','x')}\n\n"
        f"【消息面 - 最新10則新聞標題】\n{news_text}\n\n"
        "請按以下格式輸出：\n"
        "📊 技術面分析\n（趨勢、支撐壓力、RSI訊號）\n\n"
        "📋 基本面分析\n（估值、獲利能力、成長性評估）\n\n"
        "📰 消息面分析\n（利多利空整理，情緒：正面/負面/中立 + 評分1-10）\n\n"
        "🎯 綜合建議\n（一句話操作建議，結尾加上金句：「市場修正是市場送你的禮物，敢不敢拆，決定了你未來的報酬。」🎁）"
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    summary = resp.content[0].text

    return img_bytes, summary
