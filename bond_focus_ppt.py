# -*- coding: utf-8 -*-
"""
bond_focus_ppt.py — 「債市每日聚焦 / 富邦好債報」PPTX 產生器（直式 A4，兩頁）
使用 python-pptx（與 /report 相同的套件，不需 Node）
"""
from pptx import Presentation
from pptx.util import Cm, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
import os
import tempfile

NAVY = RGBColor(0x1F, 0x4E, 0x79)
BLUE = RGBColor(0x1F, 0x8A, 0xC0)
DEEP = RGBColor(0x1B, 0x7F, 0xA8)
TEAL = RGBColor(0x16, 0x9B, 0x9B)
GRAY = RGBColor(0x59, 0x59, 0x59)
RED = RGBColor(0xC0, 0x00, 0x00)
DARK = RGBColor(0x33, 0x33, 0x33)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
PEACH = RGBColor(0xFD, 0xF2, 0xEC)
F = "Microsoft JhengHei"

PAGE_W, PAGE_H = Cm(21.0), Cm(29.7)
M = Cm(1.15)
W = PAGE_W - 2 * M


def _txt(slide, x, y, w, h, runs, size=12, color=DARK, bold=False, align=PP_ALIGN.LEFT,
         line=1.35, anchor=MSO_ANCHOR.TOP, space_after=4):
    """runs: str 或 [(文字, {bold, color, size}), ...]；每個 tuple 為一個段落"""
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Cm(0.1)
    tf.margin_top = tf.margin_bottom = 0
    items = [(runs, {})] if isinstance(runs, str) else runs
    for i, item in enumerate(items):
        text, opt = (item if isinstance(item, tuple) else (item, {}))
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align
        para.line_spacing = line
        para.space_after = Pt(opt.get("space_after", space_after))
        for j, seg in enumerate(text if isinstance(text, list) else [(text, {})]):
            s_text, s_opt = (seg if isinstance(seg, tuple) else (seg, {}))
            r = para.add_run()
            r.text = s_text
            r.font.name = F
            r.font.size = Pt(s_opt.get("size", opt.get("size", size)))
            r.font.bold = s_opt.get("bold", opt.get("bold", bold))
            r.font.color.rgb = s_opt.get("color", opt.get("color", color))
    return tb


def _rect(slide, x, y, w, h, fill=None, line_color=None, line_w=1.0):
    from pptx.enum.shapes import MSO_SHAPE
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    sh.adjustments[0] = 0.04
    if fill:
        sh.fill.solid()
        sh.fill.fore_color.rgb = fill
    else:
        sh.fill.background()
    if line_color:
        sh.line.color.rgb = line_color
        sh.line.width = Pt(line_w)
    else:
        sh.line.fill.background()
    sh.shadow.inherit = False
    return sh


def _line(slide, x, y, w, color=BLUE, width=1.6):
    from pptx.enum.shapes import MSO_CONNECTOR
    ln = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x, y, x + w, y)
    ln.line.color.rgb = color
    ln.line.width = Pt(width)
    return ln


def _table(slide, x, y, w, rows_data, col_w, row_h=Cm(1.0), font=12,
           head_fill=None, body_fill=None, head_color=NAVY):
    rows, cols = len(rows_data), len(rows_data[0])
    tb = slide.shapes.add_table(rows, cols, x, y, w, row_h * rows).table
    for j, cw in enumerate(col_w):
        tb.columns[j].width = cw
    for i, row in enumerate(rows_data):
        tb.rows[i].height = row_h
        for j, cell in enumerate(row):
            text, opt = (cell if isinstance(cell, tuple) else (cell, {}))
            c = tb.cell(i, j)
            c.text = str(text)
            c.vertical_anchor = MSO_ANCHOR.MIDDLE
            c.margin_left = c.margin_right = Cm(0.08)
            c.margin_top = c.margin_bottom = 0
            p = c.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            for r in p.runs:
                r.font.name = F
                r.font.size = Pt(opt.get("size", font))
                r.font.bold = opt.get("bold", i == 0)
                r.font.color.rgb = opt.get("color", head_color if i == 0 else DARK)
            c.fill.solid()
            c.fill.fore_color.rgb = (head_fill or RGBColor(0xEA, 0xF3, 0xFA)) if i == 0 else (body_fill or WHITE)
    return tb


def _cjk_font():
    """找中文字型(與 bond_sheet 共用邏輯)"""
    base = os.path.dirname(os.path.abspath(__file__))
    import glob
    cands = [os.getenv("BOND_SHEET_FONT", "")]
    for n in ("NotoSansTC-Regular.ttf", "NotoSansTC.ttf", "msjh.ttf"):
        cands += [os.path.join(base, "fonts", n), os.path.join(base, n)]
    cands += ["/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]
    cands += sorted(glob.glob(os.path.join(base, "fonts", "*.tt*")))
    for c in cands:
        try:
            if c and os.path.exists(c) and os.path.getsize(c) > 50 * 1024:
                return c
        except Exception:
            pass
    return None


def _donut_png(rm):
    """用 matplotlib 畫營收結構甜甜圈,回傳暫存 PNG 路徑(失敗回 None)"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        fp = _cjk_font()
        PROP = None
        if fp:
            try:
                PROP = font_manager.FontProperties(fname=fp)
                font_manager.fontManager.addfont(fp)
                matplotlib.rcParams["font.family"] = PROP.get_name()
            except Exception:
                PROP = None
        labels = list(rm.get("labels") or [])
        values = [float(v) for v in rm.get("values") or []]
        if not values:
            return None
        colors = ["#169B9B", "#1F8AC0", "#BFBFBF", "#7F7F7F", "#4472C4"]
        fig, ax = plt.subplots(figsize=(4.2, 3.4), dpi=170)
        total = sum(values)
        wedges, _texts, autotexts = ax.pie(
            values, startangle=90, counterclock=False,
            colors=colors[:len(values)], wedgeprops=dict(width=0.42, edgecolor="white"),
            autopct=lambda pct: f"{pct:.0f}%" if pct >= 5 else "",
            pctdistance=0.78, textprops={"fontsize": 13, "color": "white",
                                         "fontproperties": PROP, "weight": "bold"})
        ax.legend(wedges, labels, loc="lower center", bbox_to_anchor=(0.5, -0.22),
                  ncol=2, frameon=False, fontsize=9,
                  prop=(PROP.copy() if PROP else None))
        if PROP:
            for lg in ax.get_legend().get_texts():
                lg.set_fontproperties(PROP)
                lg.set_fontsize(9)
        ax.set(aspect="equal")
        fig.tight_layout(pad=0.3)
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        fig.savefig(f.name, bbox_inches="tight", facecolor="white", transparent=False)
        plt.close(fig)
        return f.name
    except Exception as e:
        print(f"[BondFocus] donut fail: {e}")
        return None


def build_focus_pptx(out_path, D):
    prs = Presentation()
    prs.slide_width, prs.slide_height = PAGE_W, PAGE_H
    blank = prs.slide_layouts[6]

    # ================= P1 債市每日聚焦 =================
    s = prs.slides.add_slide(blank)
    _txt(s, M, Cm(0.75), Cm(1.6), Cm(1.4), "🔍", size=26)
    _txt(s, M + Cm(1.6), Cm(0.7), Cm(12), Cm(1.5), "債市每日聚焦", size=30, bold=True, color=NAVY)
    bar = s.shapes.add_shape(1, M, Cm(2.5), W, Cm(0.78))
    bar.fill.solid(); bar.fill.fore_color.rgb = BLUE; bar.line.fill.background(); bar.shadow.inherit = False
    _txt(s, M, Cm(2.58), W - Cm(0.3), Cm(0.7), D.get("date_str", ""), size=13, bold=True,
         color=WHITE, align=PP_ALIGN.RIGHT)

    _txt(s, M, Cm(3.65), Cm(6), Cm(1.0), "焦點新聞", size=20, bold=True, color=BLUE)
    _line(s, M, Cm(4.65), Cm(5.6))
    _txt(s, M, Cm(5.05), W, Cm(1.8), D.get("headline", ""), size=22, bold=True, color=DARK, line=1.25)

    bullets = [([("・", {"color": BLUE, "bold": True}), (t, {})], {"space_after": 8})
               for t in (D.get("news_bullets") or [])]
    _txt(s, M, Cm(7.1), W, Cm(8.0), bullets, size=15.5, line=1.5)

    _txt(s, M, Cm(16.0), Cm(6), Cm(1.0), "焦點債券", size=20, bold=True, color=BLUE)
    _line(s, M, Cm(17.0), Cm(5.6))
    if D.get("bond_tagline"):
        _txt(s, M + Cm(6), Cm(16.1), W - Cm(6), Cm(0.9), D["bond_tagline"], size=14, bold=True,
             color=NAVY, align=PP_ALIGN.RIGHT)

    head = [("債券代碼", {}), ("債券名稱", {}), ("票面%", {}), ("YTM%", {}), ("到期日", {})]
    body = []
    for b in D.get("bonds", []):
        body.append([(b.get("code", "-"), {}),
                     (b.get("name", "-"), {"color": TEAL, "bold": True}),
                     (str(b.get("coupon", "-")), {"color": RED, "bold": True}),
                     (str(b.get("ytm", "-")), {}),
                     (b.get("maturity", "-"), {})])
    _table(s, M, Cm(17.5), W, [head] + body,
           col_w=[Cm(4.5), Cm(4.6), Cm(2.5), Cm(2.4), Cm(4.7)],
           row_h=Cm(1.05), font=13.5, head_fill=PEACH, body_fill=PEACH)
    _txt(s, M, Cm(17.5) + Cm(1.05) * (len(body) + 1) + Cm(0.2), W, Cm(0.8),
         "※ 報價與可承作與否以本行系統為準；商品條件依產品說明書。", size=10.5, color=GRAY)

    _txt(s, M, Cm(27.8), Cm(8), Cm(0.8), "僅限內部教育訓練使用", size=12.5, bold=True, color=RED)
    _txt(s, M, Cm(27.8), W, Cm(0.8), "台北富邦銀行", size=12.5, bold=True, color=NAVY, align=PP_ALIGN.RIGHT)

    # ================= P2 富邦好債報 =================
    t = prs.slides.add_slide(blank)
    _rect(t, Cm(3.4), Cm(0.7), Cm(14.2), Cm(1.7), fill=DEEP)
    _txt(t, Cm(3.4), Cm(0.95), Cm(14.2), Cm(1.2), "富 邦 好 債 報", size=24, bold=True,
         color=WHITE, align=PP_ALIGN.CENTER)
    _txt(t, M, Cm(2.7), W, Cm(1.2), D.get("issuer", ""), size=24, bold=True, align=PP_ALIGN.CENTER)
    _txt(t, M, Cm(3.85), W, Cm(0.8), D.get("issuer_en", ""), size=13, bold=True,
         color=GRAY, align=PP_ALIGN.CENTER)

    _rect(t, M, Cm(4.75), W, Cm(3.0), fill=WHITE, line_color=BLUE)
    _txt(t, M + Cm(0.3), Cm(4.95), W - Cm(0.6), Cm(2.6), D.get("intro", ""), size=12, line=1.45)

    # 營運概況 / 營收結構
    half = (W - Cm(0.5)) / 2
    _txt(t, M, Cm(8.0), half, Cm(1.0), "營運概況", size=19, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
    _line(t, M + Cm(1.2), Cm(9.0), half - Cm(2.4))
    _rect(t, M, Cm(9.25), half, Cm(6.0), fill=WHITE, line_color=BLUE)
    ops = []
    for blk in (D.get("ops_blocks") or []):
        ops.append(([(str(blk[0]) + "：", {"bold": True, "color": BLUE})], {"space_after": 2}))
        ops.append(([(str(blk[1]), {})], {"space_after": 8}))
    _txt(t, M + Cm(0.25), Cm(9.45), half - Cm(0.5), Cm(5.6), ops, size=11, line=1.4)

    x2 = M + half + Cm(0.5)
    _txt(t, x2, Cm(8.0), half, Cm(1.0), "營收結構", size=19, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
    _line(t, x2 + Cm(1.2), Cm(9.0), half - Cm(2.4))
    _rect(t, x2, Cm(9.25), half, Cm(6.0), fill=WHITE, line_color=BLUE)
    rm = D.get("revenue_mix") or {}
    png = _donut_png(rm) if rm.get("values") else None
    if png:
        # 以高度為準置中,避免圖超出卡片
        try:
            from PIL import Image as _PIL
            iw, ih = _PIL.open(png).size
            img_h = Cm(4.7)
            img_w = int(img_h * iw / ih)
            if img_w > half - Cm(0.4):
                img_w = half - Cm(0.4)
                img_h = int(img_w * ih / iw)
            t.shapes.add_picture(png, x2 + (half - img_w) // 2, Cm(9.5), width=img_w, height=img_h)
        except Exception:
            t.shapes.add_picture(png, x2 + Cm(0.6), Cm(9.5), height=Cm(4.7))
        _txt(t, x2, Cm(14.55), half, Cm(0.7), rm.get("unit_note", ""), size=9, color=GRAY,
             align=PP_ALIGN.CENTER)
        try:
            os.remove(png)
        except Exception:
            pass
    else:
        _txt(t, x2, Cm(11.8), half, Cm(1.0), "（無公開部門別營收資料）", size=12, color=GRAY,
             align=PP_ALIGN.CENTER)

    # 信用評等
    _txt(t, M, Cm(15.6), W, Cm(1.0), "信用評等", size=19, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
    _line(t, M + Cm(6.0), Cm(16.6), W - Cm(12.0))
    R = D.get("ratings") or {}
    rows2 = [[("信用評等", {}), ("穆迪", {}), ("標普", {}), ("惠譽", {})],
             [("長期評等", {"bold": True}), (R.get("moody") or "--", {}), (R.get("sp") or "--", {}), (R.get("fitch") or "--", {})],
             [("評等展望", {"bold": True}), (R.get("moody_outlook") or "--", {}), (R.get("sp_outlook") or "--", {}), (R.get("fitch_outlook") or "--", {})],
             [("最近評等動作", {"bold": True}), (R.get("moody_date") or "--", {}), (R.get("sp_date") or "--", {}), (R.get("fitch_date") or "--", {})]]
    _table(t, M, Cm(16.95), W, rows2,
           col_w=[Cm(5.0), Cm(4.5), Cm(4.5), Cm(4.7)], row_h=Cm(0.95), font=12)

    # 信評公司評析（高度依內容自動調整）
    _txt(t, M, Cm(21.1), W, Cm(1.0), "信評公司評析", size=19, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
    _line(t, M + Cm(6.0), Cm(22.1), W - Cm(12.0))
    ac = []
    for c in (D.get("agency_comments") or []):
        ac.append(([(str(c[0]) + "－", {"bold": True, "color": BLUE}), (str(c[1]), {})], {"space_after": 7}))
    chars = sum(len(str(c[0])) + len(str(c[1])) for c in (D.get("agency_comments") or []))
    lines = -(-chars // 40) + len(D.get("agency_comments") or [])
    ac_h = min(Cm(5.6), max(Cm(2.4), Cm(0.62) * lines + Cm(0.7)))
    _rect(t, M, Cm(22.5), W, ac_h, fill=WHITE, line_color=BLUE)
    _txt(t, M + Cm(0.25), Cm(22.7), W - Cm(0.5), ac_h - Cm(0.4), ac, size=11, line=1.4)

    y_note = Cm(22.5) + ac_h + Cm(0.15)
    _txt(t, M, y_note, W, Cm(0.6), D.get("source_note", ""), size=8.5, color=GRAY)
    _txt(t, M, y_note + Cm(0.55), W, Cm(1.0),
         "「本內容僅供參考且不構成要約或要約引誘。特定標的之商品風險、申購之條件、限制與費用及其他相關權利義務，"
         "應依產品說明暨投資風險預告書等相關文件為準。」", size=8.5, color=GRAY, line=1.25)

    _txt(t, M, Cm(28.3), Cm(8), Cm(0.8), "僅限內部教育訓練使用", size=12, bold=True, color=RED)
    _txt(t, M, Cm(28.3), W, Cm(0.8), "台北富邦銀行", size=12, bold=True, color=NAVY, align=PP_ALIGN.RIGHT)

    prs.save(out_path)
    return out_path
