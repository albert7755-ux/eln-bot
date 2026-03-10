"""
ppt_generator.py
LINE Bot /report ppt 指令：
  /report ppt <主題>
兩階段：
  1. Claude 規劃架構（JSON）
  2. pptxgenjs 生成 icon + 大字 PPT → 上傳 Google Drive
"""

import os, json, subprocess, tempfile, textwrap
from pathlib import Path
import anthropic
from pdf_generator import upload_to_drive   # 沿用原有 Drive 上傳函式

# ── 顏色主題
THEMES = {
    "navy": {   # 深藍金（預設）
        "bg":    "0A1628", "bg2": "162338", "bg3": "1E3150",
        "gold":  "C9A84C", "gold2": "E8C96A",
        "white": "F0F4F8", "silver": "B8C4CC", "muted": "5A7A90",
        "green": "3DAA6E", "red":   "C44536",
        "calm":  "3A7CA5", "purple":"8B5FBF",
        "orange":"E8873A", "teal":  "2A9D8F",
    },
    "green": {  # 深綠金
        "bg":    "0A1F12", "bg2": "12301A", "bg3": "1A4023",
        "gold":  "D4A84B", "gold2": "EAC96A",
        "white": "F0F5F1", "silver": "B0C8B5", "muted": "5A8060",
        "green": "4CC97A", "red":   "C44536",
        "calm":  "2A9D5C", "purple":"7B9F3A",
        "orange":"E8A03A", "teal":  "2ABF8F",
    },
    "dark": {   # 純黑銀
        "bg":    "0D0D0D", "bg2": "1A1A1A", "bg3": "252525",
        "gold":  "A8A8A8", "gold2": "D0D0D0",
        "white": "F5F5F5", "silver": "C0C0C0", "muted": "707070",
        "green": "5AC05A", "red":   "C44536",
        "calm":  "5A9AC0", "purple":"9A7AC0",
        "orange":"C09A5A", "teal":  "5AC0B0",
    },
}

# 預設使用 navy
THEME = THEMES["navy"]

# ── Icon 對照表：關鍵字 → icon 名稱
ICON_MAP = {
    # 金融商品
    "ELN": "shield", "股票連結": "shield", "結構型": "chart",
    "債券": "bond", "固定收益": "bond", "投資等級": "shield",
    "基金": "chart", "ETF": "chart", "股票": "chart",
    # 業務類型
    "質借": "key", "Lombard": "key", "信用額度": "key",
    "境外": "globe", "匯回": "globe", "海外": "globe",
    "信託": "shield", "規劃": "plan", "配置": "plan",
    "槓桿": "lightning", "再投資": "lightning",
    # 通用
    "風險": "warning", "注意": "warning",
    "客戶": "people", "輪廓": "people",
    "啟動": "rocket", "開始": "rocket",
    "資金": "coins", "現金": "coins",
    "銀行": "bank", "機構": "bank",
    "優勢": "star", "特色": "star",
    "比較": "compare",
}

DEFAULT_ICONS = ["chart", "shield", "key", "lightning", "coins", "people", "star", "warning", "rocket", "bank"]

def pick_icon(title: str, used: list, idx: int) -> str:
    """根據標題選擇最適合的 icon"""
    for kw, icon in ICON_MAP.items():
        if kw in title:
            return icon
    # 輪流使用預設 icon
    return DEFAULT_ICONS[idx % len(DEFAULT_ICONS)]

# ══════════════════════════════════════════════════════
# Step 0: Claude 推導視覺主題規格
# ══════════════════════════════════════════════════════
def design_theme(visual_desc: str, force_pattern: str = "") -> dict:
    """
    根據使用者輸入的主題描述，讓 Claude 推導完整視覺規格。
    force_pattern: 若有填寫，直接覆蓋 Claude 選的圖案。
    """
    client = anthropic.Anthropic()

    VALID_PATTERNS = {"circuit","wave","stars","hexagon","mountain","ripple","grid","diagonal","none"}
    pattern_hint = f"\n注意：圖案類型已由使用者指定為「{force_pattern}」，pattern 欄位請直接填 \"{force_pattern}\"，不要自行選擇。" if force_pattern else ""

    prompt = f"""
你是一位專業的簡報視覺設計師。根據以下主題描述，推導出一套完整的投影片視覺規格。

主題描述：「{visual_desc}」{pattern_hint}

請只輸出 JSON，不要任何其他文字：
{{
  "bg":      "深色背景色16進位，不含#",
  "bg2":     "次背景色（比bg稍亮一點）",
  "bg3":     "第三背景色（卡片底色）",
  "gold":    "主強調色（標題/重點）",
  "gold2":   "次強調色（略淡）",
  "white":   "主文字色",
  "silver":  "次文字色",
  "muted":   "淡色輔助文字",
  "green":   "正向指標色",
  "red":     "警示色",
  "calm":    "中性強調色1",
  "purple":  "中性強調色2",
  "orange":  "中性強調色3",
  "teal":    "中性強調色4",
  "pattern": "背景圖案類型，從以下選一個：circuit/wave/stars/hexagon/mountain/ripple/grid/diagonal/none",
  "pattern_color": "圖案顏色16進位，不含#，通常是gold或calm的暗化版",
  "pattern_alpha": 圖案透明度0.03到0.12之間的小數,
  "icon_tint": "icon主色調，從以下選：gold/blue/green/purple/orange/teal/white",
  "description": "用一句話描述這個視覺風格"
}}

設計原則：
- 背景必須夠深（亮度低），確保白色文字清晰可讀
- 強調色要鮮明有個性，符合主題描述的情境感
- 圖案要低調（alpha值小），不搶過文字
- 整體要有高端專業感，適合財富管理客戶

範例：
- 「深海星空」→ 深藍黑背景、銀白強調、stars圖案、teal icon
- 「科技電路板」→ 深灰背景、青綠強調、circuit圖案、teal icon
- 「日式禪風金色」→ 深墨色背景、金色強調、ripple圖案、gold icon
- 「宇宙紫金」→ 深紫背景、金色強調、stars圖案、purple icon
"""
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    text = resp.content[0].text.strip()
    if "```" in text:
        for part in text.split("```"):
            p = part.strip().lstrip("json").strip()
            if p.startswith("{"): text = p; break
    result = json.loads(text)

    # 強制覆蓋圖案（如果使用者有指定）
    if force_pattern and force_pattern in VALID_PATTERNS:
        result["pattern"] = force_pattern

    return result


def generate_custom_background(icon_dir: str, visual_desc: str, spec: dict) -> bool:
    """
    讓 Claude 即時生成 PIL 繪圖程式碼，畫出符合描述的背景圖。
    成功回傳 True，失敗回傳 False（由呼叫者 fallback 到 generate_background）。
    """
    client = anthropic.Anthropic()

    def hex_to_rgb_str(h):
        h = h.lstrip("#")
        r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
        return f"({r},{g},{b})"

    bg      = hex_to_rgb_str(spec.get("bg",     "0A1628"))
    bg2     = hex_to_rgb_str(spec.get("bg2",    "162338"))
    gold    = hex_to_rgb_str(spec.get("gold",   "C9A84C"))
    calm    = hex_to_rgb_str(spec.get("calm",   "3A7CA5"))
    accent2 = hex_to_rgb_str(spec.get("purple", "8B5FBF"))

    prompt = f"""
你是一位 Python 視覺藝術家，專門用 PIL 繪製投影片背景圖。

請生成一段 Python 程式碼，畫出符合以下描述的 1280×720 背景圖，存到 "{icon_dir}/bg.jpg"。

視覺描述：「{visual_desc}」

配色提示（請使用這些顏色）：
- 背景底色：{bg}
- 次背景色：{bg2}
- 主強調色（金/亮色）：{gold}
- 次強調色（冷色）：{calm}
- 第三強調色：{accent2}

技術規範：
- 只能使用 PIL（Pillow）和 Python 標準函式庫（math、random、os）
- 不能 import numpy 以外的第三方套件（numpy 可用）
- 圖案要低調，透明度低，不能蓋過投影片文字
- 最終存成 JPEG：img.convert("RGB").save("{icon_dir}/bg.jpg", quality=88)
- 程式碼必須可以直接執行，不需要任何 input

只輸出 Python 程式碼，不要任何說明文字、不要 markdown 格式、不要 ```python 標記。
第一行直接是 import 或程式碼。
"""

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    code = resp.content[0].text.strip()
    # 清掉 markdown
    if "```" in code:
        parts = code.split("```")
        for part in parts:
            p = part.strip()
            if p.startswith("python"): p = p[6:].strip()
            if "import" in p or "from PIL" in p:
                code = p
                break

    # 寫入暫存檔執行
    import tempfile, subprocess
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py",
                                    delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp_py = f.name

    try:
        result = subprocess.run(
            ["python3", tmp_py],
            capture_output=True, text=True, timeout=30
        )
        import os as _os
        if result.returncode == 0 and _os.path.exists(f"{icon_dir}/bg.jpg"):
            return True
        else:
            # 把錯誤記錄到 log，但不拋出
            print(f"[custom_bg] 執行失敗: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"[custom_bg] 例外: {e}")
        return False
    finally:
        try:
            import os as _os
            _os.unlink(tmp_py)
        except:
            pass



def plan_slides(topic: str, n_slides: int = 9) -> list:
    """呼叫 Claude 根據主題規劃投影片架構，回傳 JSON list"""
    client = anthropic.Anthropic()

    prompt = f"""
你是一位資深投資銀行簡報設計師，熟悉台灣高資產客戶（HNW）的投資商品。

請為以下主題設計一份{n_slides}張投影片的商品介紹簡報架構：
主題：「{topic}」
對象：銀行財富管理高資產客戶 + 行內專員教育訓練

**輸出格式要求**（只輸出 JSON 陣列，不要任何其他文字）：
[
  {{
    "slide_num": 1,
    "layout": "cover",
    "title": "投影片主標題",
    "subtitle": "副標題（封面用）",
    "points": [],
    "stat": "",
    "stat_unit": "",
    "icon_hint": "一個關鍵字，用來選擇icon，例如：債券/風險/客戶/啟動/銀行"
  }},
  {{
    "slide_num": 2,
    "layout": "steps",
    "title": "什麼是XXX？",
    "subtitle": "",
    "points": ["步驟1標題|說明文字", "步驟2標題|說明文字", "步驟3標題|說明文字"],
    "stat": "",
    "stat_unit": "",
    "icon_hint": "資金"
  }}
]

**layout 類型說明**：
- cover：封面（第1張）
- steps：左icon + 右側3步驟卡（說明型）
- stat_cards：3個大數字卡（強調數據）
- bar_list：左文字列表 + 右進度條（比較LTV/比例等）
- quad：四格情境卡（4個使用情境）
- compare：左推薦卡 + 右3個比較項目
- risk_list：風險條列（4條，帶左色條）
- profile_list：客戶輪廓（4條，帶適合度標籤）
- closing：結語（時間軸+金句）

**points 格式**：
- steps layout：每點用「標題|說明」格式，3個點
- stat_cards layout：每點用「數字|單位|說明」格式，3個點
- bar_list layout：每點用「名稱|百分比數字|說明」，4個點（百分比如70）
- quad layout：每點用「標題|說明|範例」，4個點
- compare layout：第一點是「推薦標題」，其後每點「比較項目|缺點1|缺點2」，4個點
- risk_list layout：每點用「標題|詳細說明」，4個點
- profile_list layout：每點用「客群名稱|條件說明|適合程度」，4個點
- closing layout：每點是步驟名稱，4個點

**重要**：
- 內容必須針對「{topic}」這個主題量身訂做，不要用通用內容
- 繁體中文，台灣金融用語
- 數據和說明要專業且正確
- 第1張固定是 cover，最後1張固定是 closing
"""

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    text = resp.content[0].text.strip()
    # 清理 markdown
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            p = part.strip()
            if p.startswith("json"): p = p[4:]
            p = p.strip()
            if p.startswith("["):
                text = p
                break

    return json.loads(text)

# ══════════════════════════════════════════════════════
# Step 2: 生成 icon PNG（PIL）
# ══════════════════════════════════════════════════════
def generate_icons(icon_dir: str):
    """生成所有需要的 icon PNG"""
    import math
    from PIL import Image, ImageDraw

    os.makedirs(icon_dir, exist_ok=True)
    s = 200

    def mk(name, fn):
        img = Image.new("RGBA", (s, s), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        fn(draw)
        img.save(f"{icon_dir}/{name}.png")

    GOLD   = (201,168,76,255)
    WHITE  = (240,244,248,255)
    BLUE   = (58,124,165,255)
    GREEN  = (61,170,110,255)
    RED    = (196,69,54,255)
    MUTED  = (122,154,176,255)
    PURPLE = (139,95,191,255)
    c = s//2

    def icon_bank(d):
        d.polygon([(c,10),(s-15,55),(15,55)], fill=GOLD)
        for x in [35,62,89,116,143]: d.rectangle([x,55,x+12,140], fill=WHITE[:3]+(220,))
        d.rectangle([15,140,s-15,158], fill=GOLD)
        d.rectangle([8,158,s-8,175], fill=(*GOLD[:3],200))
    mk("bank", icon_bank)

    def icon_key(d):
        r=45
        d.ellipse([c-r-20,c-r-20,c+r-20,c+r-20], outline=(*GOLD[:3],255), width=12)
        d.ellipse([c-r//2-20,c-r//2-20,c+r//2-20,c+r//2-20], fill=GOLD)
        d.rectangle([c+22,c-8,s-15,c+8], fill=GOLD)
        d.rectangle([s-35,c+8,s-22,c+25], fill=GOLD)
        d.rectangle([s-55,c+8,s-42,c+22], fill=GOLD)
    mk("key", icon_key)

    def icon_lightning(d):
        pts=[(c+15,10),(c-25,c+5),(c+10,c+5),(c-15,s-10),(c+30,c-5),(c-5,c-5)]
        d.polygon(pts, fill=GOLD)
    mk("lightning", icon_lightning)

    def icon_shield(d):
        pts=[(c,12),(s-18,40),(s-18,110),(c,s-12),(18,110),(18,40)]
        d.polygon(pts, fill=(*BLUE[:3],200))
        d.polygon(pts, outline=(*GOLD[:3],255), width=4)
        d.line([(c-28,c+5),(c-8,c+28),(c+32,c-22)], fill=WHITE, width=10)
    mk("shield", icon_shield)

    def icon_chart(d):
        for i in range(3): d.line([20,40+i*45,s-20,40+i*45], fill=(*MUTED[:3],60), width=1)
        for (x1,y1,x2,y2),col in [((30,130,65,170),(*BLUE[:3],180)),((85,100,120,170),(*BLUE[:3],210)),((140,65,175,170),GOLD)]:
            d.rectangle([x1,y1,x2,y2], fill=col)
        d.line([(47,125),(102,95),(157,58)], fill=GREEN, width=4)
        for pt in [(47,125),(102,95),(157,58)]: d.ellipse([pt[0]-5,pt[1]-5,pt[0]+5,pt[1]+5], fill=GREEN)
        d.line([20,170,s-20,170], fill=(*WHITE[:3],150), width=2)
    mk("chart", icon_chart)

    def icon_warning(d):
        d.polygon([(c,15),(s-18,s-18),(18,s-18)], fill=(*RED[:3],220))
        d.polygon([(c,15),(s-18,s-18),(18,s-18)], outline=RED, width=3)
        d.rectangle([c-6,55,c+6,120], fill=WHITE)
        d.ellipse([c-7,130,c+7,148], fill=WHITE)
    mk("warning", icon_warning)

    def icon_coins(d):
        for (cx,cy),a in [((40,100),160),((80,80),200),((120,60),255)]:
            d.ellipse([cx-27,cy-27,cx+27,cy+27], fill=(*GOLD[:3],a), outline=(*WHITE[:3],80), width=2)
        d.line([150,100,185,100], fill=GREEN, width=4)
        d.polygon([(182,88),(198,100),(182,112)], fill=GREEN)
    mk("coins", icon_coins)

    def icon_people(d):
        d.ellipse([c-22,20,c+22,64], fill=GOLD)
        d.ellipse([c-35,68,c+35,140], fill=(*GOLD[:3],200))
        d.ellipse([35,35,70,68], fill=(*BLUE[:3],200))
        d.ellipse([22,72,78,130], fill=(*BLUE[:3],160))
        d.ellipse([s-70,35,s-35,68], fill=(*GREEN[:3],200))
        d.ellipse([s-78,72,s-22,130], fill=(*GREEN[:3],160))
    mk("people", icon_people)

    def icon_rocket(d):
        d.polygon([(c,15),(c+28,80),(c+28,140),(c-28,140),(c-28,80)], fill=(*WHITE[:3],220))
        d.polygon([(c-18,140),(c,175),(c+18,140)], fill=GOLD)
        d.polygon([(c-10,145),(c,165),(c+10,145)], fill=(*RED[:3],200))
        d.ellipse([c-14,70,c+14,98], fill=BLUE)
        d.ellipse([c-8,76,c+8,92], fill=(*WHITE[:3],100))
        d.polygon([(c-28,90),(c-55,140),(c-28,130)], fill=(*GOLD[:3],200))
        d.polygon([(c+28,90),(c+55,140),(c+28,130)], fill=(*GOLD[:3],200))
    mk("rocket", icon_rocket)

    def icon_star(d):
        pts=[]
        for i in range(10):
            angle=math.radians(i*36-90)
            r=85 if i%2==0 else 40
            pts.append((int(c+r*math.cos(angle)), int(c+r*math.sin(angle))))
        d.polygon(pts, fill=GOLD)
    mk("star", icon_star)

    def icon_bond(d):
        # 紙張+曲線代表債券
        d.rectangle([30,25,s-30,s-25], fill=(*BLUE[:3],180), outline=(*GOLD[:3],255), width=3)
        for y in [65,90,115,140]:
            d.line([50,y,s-50,y], fill=(*WHITE[:3],120), width=2)
        # 金色圓印
        d.ellipse([c-28,c-28,c+28,c+28], fill=GOLD)
        d.ellipse([c-20,c-20,c+20,c+20], fill=(*GOLD[:3],100))
    mk("bond", icon_bond)

    def icon_globe(d):
        r=80
        d.ellipse([c-r,c-r,c+r,c+r], outline=(*BLUE[:3],220), width=8)
        d.ellipse([c-r,c-r,c+r,c+r], fill=(*BLUE[:3],60))
        # 緯線
        for dy in [-30,0,30]:
            rw=int((r**2-dy**2)**0.5) if abs(dy)<r else 0
            if rw>0: d.arc([c-rw,c+dy-8,c+rw,c+dy+8], 0, 180, fill=(*GOLD[:3],180), width=3)
        # 經線
        d.line([c,c-r,c,c+r], fill=(*GOLD[:3],150), width=3)
        d.arc([c-50,c-r,c+50,c+r], 0, 180, fill=(*GOLD[:3],150), width=3)
    mk("globe", icon_globe)

    def icon_plan(d):
        # 清單/計畫
        d.rectangle([25,20,s-25,s-20], fill=(*BLUE[:3],120), outline=(*GOLD[:3],200), width=3)
        for i,y in enumerate([60,90,120,150]):
            col = GOLD if i==0 else (*WHITE[:3],180)
            d.ellipse([40,y-6,54,y+8], fill=col)
            d.line([62,y+1,s-38,y+1], fill=(*WHITE[:3],150 if i>0 else 220), width=3 if i==0 else 2)
    mk("plan", icon_plan)

    def icon_compare(d):
        # 天平
        d.line([c,30,c,160], fill=(*GOLD[:3],200), width=6)
        d.line([25,85,s-25,85], fill=(*GOLD[:3],200), width=6)
        d.ellipse([c-8,22,c+8,38], fill=GOLD)
        d.rectangle([25,110,75,160], fill=(*GREEN[:3],180), outline=(*GREEN[:3],255), width=2)
        d.rectangle([s-75,120,s-25,165], fill=(*RED[:3],140), outline=(*RED[:3],180), width=2)
    mk("compare", icon_compare)


def generate_background(icon_dir: str, spec: dict):
    """
    根據視覺規格生成 bg.jpg 背景圖，供投影片使用。
    spec 包含 pattern、pattern_color、pattern_alpha、bg 等。
    """
    import math, numpy as np
    from PIL import Image, ImageDraw

    W, H = 1280, 720

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    bg_rgb    = hex_to_rgb(spec.get("bg", "0A1628"))
    bg2_rgb   = hex_to_rgb(spec.get("bg2", "162338"))
    pat_rgb   = hex_to_rgb(spec.get("pattern_color", spec.get("calm", "3A7CA5")))
    pat_alpha = int(float(spec.get("pattern_alpha", 0.06)) * 255)
    pattern   = spec.get("pattern", "grid")

    # 基底漸層
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    for ch in range(3):
        col = np.linspace(bg_rgb[ch], bg2_rgb[ch], H).astype(np.uint8)
        arr[:, :, ch] = col[:, None]
    img = Image.fromarray(arr, "RGB").convert("RGBA")
    draw = ImageDraw.Draw(img)

    np.random.seed(42)

    if pattern == "circuit":
        for _ in range(40):
            x1, y1 = np.random.randint(0,W), np.random.randint(0,H)
            length = np.random.randint(60, 250)
            if np.random.rand() > 0.5:
                draw.line([x1,y1,x1+length,y1], fill=(*pat_rgb,pat_alpha), width=1)
                draw.line([x1+length,y1,x1+length,y1+np.random.randint(30,120)], fill=(*pat_rgb,pat_alpha//2), width=1)
            else:
                draw.line([x1,y1,x1,y1+length], fill=(*pat_rgb,pat_alpha), width=1)
                draw.line([x1,y1+length,x1+np.random.randint(30,120),y1+length], fill=(*pat_rgb,pat_alpha//2), width=1)
        for _ in range(30):
            x,y = np.random.randint(0,W), np.random.randint(0,H)
            r = np.random.randint(2,5)
            draw.ellipse([x-r,y-r,x+r,y+r], fill=(*pat_rgb,min(pat_alpha*2,255)))

    elif pattern == "wave":
        for wi in range(6):
            pts = []
            amp, freq, phase = 40+wi*15, 0.008-wi*0.001, wi*0.8
            y_off = 100 + wi*100
            a = max(pat_alpha//(wi+1), 6)
            for x in range(0, W+10, 4):
                y = int(y_off + amp * math.sin(freq*x+phase))
                pts.append((x, y))
            if len(pts) > 1:
                draw.line(pts, fill=(*pat_rgb,a), width=2)

    elif pattern == "stars":
        for _ in range(120):
            x,y = np.random.randint(0,W), np.random.randint(0,H)
            r = np.random.randint(1,3)
            a = min(np.random.randint(40,pat_alpha*3+1), 255)
            draw.ellipse([x-r,y-r,x+r,y+r], fill=(*pat_rgb,a))
        for _ in range(8):
            x,y = np.random.randint(0,W), np.random.randint(0,H)
            for r in range(6,0,-1):
                draw.ellipse([x-r,y-r,x+r,y+r], fill=(*pat_rgb,min(pat_alpha*r*2,255)))

    elif pattern == "hexagon":
        hs = 55
        for row in range(-1, H//hs+2):
            for col in range(-1, W//hs+2):
                cx = int(col * hs * 1.73)
                cy = int(row * hs * 2) + (hs if col%2==1 else 0)
                pts = [(int(cx+hs*math.cos(math.radians(a))), int(cy+hs*math.sin(math.radians(a)))) for a in range(0,360,60)]
                draw.polygon(pts, outline=(*pat_rgb,pat_alpha))

    elif pattern == "mountain":
        for layer in range(4):
            pts = [(0,H)]
            x, y = 0, int(H*(0.4+layer*0.1))
            while x < W:
                x += int(np.random.randint(30,100))
                y = max(int(H*0.2), min(int(H*0.85), y+int(np.random.randint(-60,60))))
                pts.append((x,y))
            pts.append((W,H))
            draw.polygon(pts, fill=(*pat_rgb, max(pat_alpha//(layer+1),4)))

    elif pattern == "ripple":
        cx, cy = W//2, H//2
        for r in range(30, max(W,H), 55):
            draw.ellipse([cx-r,cy-r,cx+r,cy+r], outline=(*pat_rgb,max(pat_alpha-r//30,2)), width=1)

    elif pattern == "diagonal":
        for x in range(-H, W+H, 80):
            draw.line([x,0,x+H,H], fill=(*pat_rgb,pat_alpha), width=1)

    else:  # grid
        for x in range(0, W, 70):
            draw.line([x,0,x,H], fill=(*pat_rgb,pat_alpha), width=1)
        for y in range(0, H, 70):
            draw.line([0,y,W,y], fill=(*pat_rgb,pat_alpha), width=1)

    # 右上角金色光暈
    gold_rgb = hex_to_rgb(spec.get("gold", "C9A84C"))
    for i in range(6):
        r = 300 - i*40
        a = max(12-i*2, 0)
        draw.ellipse([W-r-50,-r//2,W-50+r,r//2+r], fill=(*gold_rgb,a))

    img.convert("RGB").save(f"{icon_dir}/bg.jpg", quality=88)



def slides_to_js(slides: list, icon_dir: str, output_path: str, theme: dict = None, use_bg: bool = False) -> str:
    """將架構 JSON 轉換為 pptxgenjs JavaScript 腳本"""

    C = theme if theme else THEMES["navy"]
    BG_PATH = f"{icon_dir}/bg.jpg"

    def esc(s):
        return s.replace("\\","\\\\").replace('"','\\"').replace("\n","\\n").replace("\r","")

    lines = [
        'const pptx = require("pptxgenjs");',
        'const pres = new pptx();',
        'pres.layout = "LAYOUT_16x9";',
        f'const IC = "{icon_dir}/";',
        '',
        '// 色彩常數',
        f'const C = {json.dumps(C)};',
        '',
        '// 共用標題列',
        'function titleBar(s, title, accent) {',
        '  accent = accent || C.gold;',
        '  s.addShape(pres.shapes.RECTANGLE, {x:0,y:0,w:10,h:1.05,fill:{color:C.bg2},line:{color:C.bg2,width:0}});',
        '  s.addShape(pres.shapes.RECTANGLE, {x:0,y:0,w:0.1,h:1.05,fill:{color:accent},line:{color:accent,width:0}});',
        '  s.addText(title, {x:0.28,y:0.15,w:9.4,h:0.72,fontSize:28,bold:true,color:C.white,fontFace:"Calibri",margin:0});',
        '  s.addShape(pres.shapes.RECTANGLE, {x:0.28,y:0.95,w:9.44,h:0.04,fill:{color:accent},line:{color:accent,width:0}});',
        '}',
        '',
    ]

    icon_cycle = ["chart","shield","key","lightning","coins","people","star","bond","globe","plan"]

    for i, slide in enumerate(slides):
        layout = slide.get("layout","steps")
        title  = esc(slide.get("title",""))
        sub    = esc(slide.get("subtitle",""))
        points = slide.get("points", [])
        stat   = esc(slide.get("stat",""))
        unit   = esc(slide.get("stat_unit",""))
        icon_hint = slide.get("icon_hint","")
        icon_name = pick_icon(icon_hint + title, [], i)

        # 顏色輪轉
        accent_colors = [C["calm"], C["gold"], C["green"], C["purple"], C["teal"], C["orange"]]
        accent = accent_colors[i % len(accent_colors)]

        lines.append(f'// ── Slide {i+1}: {title} [{layout}]')
        lines.append('{')
        lines.append('  const s = pres.addSlide();')
        if use_bg:
            lines.append(f'  s.addImage({{path:"{BG_PATH}",x:0,y:0,w:10,h:5.625}});')
        else:
            lines.append(f'  s.background = {{color: C.bg}};')

        if layout == "cover":
            lines += [
                '  s.addShape(pres.shapes.OVAL, {x:5.5,y:-1.5,w:6,h:6,fill:{color:C.bg3},line:{color:C.bg3,width:0}});',
                '  s.addShape(pres.shapes.OVAL, {x:6.2,y:-0.8,w:4.5,h:4.5,fill:{color:C.bg2},line:{color:C.bg2,width:0}});',
                '  s.addShape(pres.shapes.RECTANGLE, {x:0,y:0,w:0.12,h:5.625,fill:{color:C.gold},line:{color:C.gold,width:0}});',
                f'  s.addImage({{path: IC+"{icon_name}.png", x:6.5,y:0.8,w:2.8,h:2.8}});',
                f'  s.addText("{title}", {{x:0.35,y:0.55,w:6.5,h:1.1,fontSize:44,bold:true,color:C.white,fontFace:"Calibri",margin:0}});',
                f'  s.addText("{sub}", {{x:0.35,y:1.62,w:6.5,h:0.75,fontSize:26,color:C.gold,fontFace:"Calibri Light",italic:true,margin:0}});',
                '  s.addShape(pres.shapes.RECTANGLE, {x:0.35,y:2.5,w:4.8,h:0.05,fill:{color:C.gold},line:{color:C.gold,width:0}});',
                '  s.addText("讓資產動起來，不必賣掉它", {x:0.35,y:2.7,w:6.5,h:0.5,fontSize:17,color:C.silver,fontFace:"Calibri Light",margin:0});',
                '  s.addText("財富管理  ·  高資產客戶專屬  ·  2026", {x:0.35,y:5.2,w:6,h:0.25,fontSize:9,color:C.muted,margin:0});',
            ]
            # 封面 KPI（從 points 取前3個，格式"數字|單位"）
            kpis = [p.split("|") for p in points[:3]] if points else [["50-80%","可借成數"],["24-48","小時到位"],["100%","部位保留"]]
            for ki, kpi in enumerate(kpis[:3]):
                kv = esc(kpi[0]) if len(kpi)>0 else "—"
                kl = esc(kpi[1]) if len(kpi)>1 else ""
                lines.append(f'  s.addText("{kv}", {{x:{0.35+ki*2.5},y:3.5,w:2.3,h:0.65,fontSize:26,bold:true,color:C.gold,fontFace:"Calibri",align:"center",margin:0}});')
                lines.append(f'  s.addText("{kl}", {{x:{0.35+ki*2.5},y:4.15,w:2.3,h:0.3,fontSize:11,color:C.muted,align:"center",fontFace:"Calibri Light",margin:0}});')

        elif layout == "steps":
            lines += [
                f'  titleBar(s, "{title}");',
                f'  s.addImage({{path: IC+"{icon_name}.png", x:0.3,y:1.3,w:2.8,h:2.8}});',
            ]
            step_colors = [C["calm"], C["gold"], C["green"]]
            for si, pt in enumerate(points[:3]):
                parts = pt.split("|")
                st_title = esc(parts[0]) if parts else ""
                st_desc  = esc(parts[1]) if len(parts)>1 else ""
                sc = step_colors[si % 3]
                y  = 1.15 + si * 1.05
                lines += [
                    f'  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{x:3.5,y:{y},w:6.15,h:0.95,fill:{{color:C.bg2}},line:{{color:"{sc}",width:1.5}},rectRadius:0.08}});',
                    f'  s.addShape(pres.shapes.OVAL, {{x:3.65,y:{y+0.15},w:0.62,h:0.62,fill:{{color:"{sc}"}},line:{{color:"{sc}",width:0}}}});',
                    f'  s.addText("{si+1:02d}", {{x:3.65,y:{y+0.15},w:0.62,h:0.62,fontSize:13,bold:true,color:C.bg,align:"center",valign:"middle",margin:0}});',
                    f'  s.addText("{st_title}", {{x:4.42,y:{y+0.08},w:5.0,h:0.4,fontSize:17,bold:true,color:C.white,fontFace:"Calibri",margin:0}});',
                    f'  s.addText("{st_desc}", {{x:4.42,y:{y+0.48},w:5.0,h:0.32,fontSize:12,color:C.muted,fontFace:"Calibri Light",margin:0}});',
                ]
            # 底部提示框
            hint = esc(slide.get("hint",""))
            if hint:
                lines += [
                    f'  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{x:0.3,y:4.25,w:9.4,h:0.85,fill:{{color:C.bg3}},line:{{color:C.gold,width:1}},rectRadius:0.08}});',
                    f'  s.addText("💡  {hint}", {{x:0.5,y:4.32,w:9.0,h:0.7,fontSize:13,color:C.gold2,fontFace:"Calibri Light",valign:"middle",margin:0}});',
                ]

        elif layout == "stat_cards":
            lines.append(f'  titleBar(s, "{title}");')
            card_colors = [C["calm"], C["gold"], C["green"]]
            icon_names  = [icon_name, "lightning", "key"]
            for ci, pt in enumerate(points[:3]):
                parts = pt.split("|")
                num   = esc(parts[0]) if parts else "—"
                unit2 = esc(parts[1]) if len(parts)>1 else ""
                desc2 = esc(parts[2]) if len(parts)>2 else ""
                cc = card_colors[ci % 3]
                ic2 = icon_names[ci % 3]
                x = 0.35 + ci * 3.1
                lines += [
                    f'  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{x:{x},y:1.2,w:2.9,h:3.95,fill:{{color:C.bg2}},line:{{color:"{cc}",width:1.5}},rectRadius:0.1}});',
                    f'  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{x:{x},y:1.2,w:2.9,h:0.08,fill:{{color:"{cc}"}},line:{{color:"{cc}",width:0}},rectRadius:0.1}});',
                    f'  s.addImage({{path: IC+"{ic2}.png", x:{x+0.95},y:1.35,w:1.0,h:1.0}});',
                    f'  s.addText("{num}", {{x:{x},y:2.4,w:2.9,h:0.9,fontSize:52,bold:true,color:"{cc}",align:"center",fontFace:"Calibri",margin:0}});',
                    f'  s.addText("{unit2}", {{x:{x+0.1},y:3.3,w:2.7,h:0.35,fontSize:12,color:C.silver,align:"center",fontFace:"Calibri Light",margin:0}});',
                    f'  s.addShape(pres.shapes.RECTANGLE, {{x:{x+0.3},y:3.72,w:2.3,h:0.03,fill:{{color:C.bg3}},line:{{color:C.bg3,width:0}}}});',
                    f'  s.addText("{desc2}", {{x:{x+0.1},y:3.82,w:2.7,h:1.0,fontSize:12,color:C.muted,align:"center",fontFace:"Calibri Light",margin:0}});',
                ]

        elif layout == "bar_list":
            lines += [
                f'  titleBar(s, "{title}");',
                f'  s.addImage({{path: IC+"{icon_name}.png", x:7.8,y:1.2,w:1.9,h:1.9}});',
            ]
            bar_colors = [C["green"], C["calm"], C["calm"], C["muted"]]
            for bi, pt in enumerate(points[:4]):
                parts = pt.split("|")
                bname = esc(parts[0]) if parts else ""
                bpct  = float(parts[1])/100 if len(parts)>1 else 0.5
                bnote = esc(parts[2]) if len(parts)>2 else ""
                bc = bar_colors[bi % 4]
                y  = 1.3 + bi * 1.0
                lines += [
                    f'  s.addShape(pres.shapes.RECTANGLE, {{x:0.3,y:{y+0.12},w:0.08,h:0.72,fill:{{color:"{bc}"}},line:{{color:"{bc}",width:0}}}});',
                    f'  s.addText("{bname}", {{x:0.52,y:{y+0.1},w:2.8,h:0.42,fontSize:17,bold:true,color:C.white,fontFace:"Calibri",margin:0}});',
                    f'  s.addText("{bnote}", {{x:0.52,y:{y+0.52},w:2.8,h:0.28,fontSize:10,color:C.muted,fontFace:"Calibri Light",margin:0}});',
                    f'  s.addShape(pres.shapes.RECTANGLE, {{x:3.5,y:{y+0.28},w:3.5,h:0.28,fill:{{color:C.bg3}},line:{{color:C.bg3,width:0}}}});',
                    f'  s.addShape(pres.shapes.RECTANGLE, {{x:3.5,y:{y+0.28},w:{3.5*bpct:.2f},h:0.28,fill:{{color:"{bc}"}},line:{{color:"{bc}",width:0}}}});',
                    f'  s.addText("{int(bpct*100)}%", {{x:7.1,y:{y+0.1},w:1.5,h:0.45,fontSize:20,bold:true,color:"{bc}",fontFace:"Calibri",margin:0}});',
                    f'  s.addText("{bnote}", {{x:8.65,y:{y+0.18},w:1.0,h:0.3,fontSize:11,color:C.muted,fontFace:"Calibri Light",margin:0}});',
                ]
                if bi < 3:
                    lines.append(f'  s.addShape(pres.shapes.RECTANGLE, {{x:0.3,y:{y+0.9},w:9.4,h:0.02,fill:{{color:C.bg3}},line:{{color:C.bg3,width:0}}}});')
            lines.append(f'  s.addText("* 實際成數依市況與銀行規定調整", {{x:0.3,y:5.25,w:9,h:0.22,fontSize:8.5,color:C.muted,italic:true,margin:0}});')

        elif layout == "quad":
            lines.append(f'  titleBar(s, "{title}");')
            quad_colors = [C["calm"], C["gold"], C["green"], C["purple"]]
            quad_icons  = [icon_name, "key", "coins", "bank"]
            for qi, pt in enumerate(points[:4]):
                parts = pt.split("|")
                qt = esc(parts[0]) if parts else ""
                qd = esc(parts[1]) if len(parts)>1 else ""
                qe = esc(parts[2]) if len(parts)>2 else ""
                qc = quad_colors[qi % 4]
                qi2 = quad_icons[qi % 4]
                x = 0.25 + (qi%2)*4.88
                y = 1.15 + (qi//2)*2.2
                lines += [
                    f'  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{x:{x},y:{y},w:4.6,h:2.0,fill:{{color:C.bg2}},line:{{color:"{qc}",width:1.5}},rectRadius:0.1}});',
                    f'  s.addShape(pres.shapes.RECTANGLE, {{x:{x},y:{y},w:0.08,h:2.0,fill:{{color:"{qc}"}},line:{{color:"{qc}",width:0}}}});',
                    f'  s.addImage({{path: IC+"{qi2}.png", x:{x+0.15},y:{y+0.15},w:0.7,h:0.7}});',
                    f'  s.addText("{qt}", {{x:{x+1.0},y:{y+0.12},w:3.4,h:0.5,fontSize:18,bold:true,color:"{qc}",fontFace:"Calibri",margin:0}});',
                    f'  s.addText("{qd}", {{x:{x+0.15},y:{y+0.88},w:4.3,h:0.65,fontSize:13,color:C.silver,fontFace:"Calibri Light",margin:0}});',
                    f'  s.addShape(pres.shapes.RECTANGLE, {{x:{x+0.15},y:{y+1.55},w:4.3,h:0.38,fill:{{color:C.bg3}},line:{{color:C.bg3,width:0}}}});',
                    f'  s.addText("→ {qe}", {{x:{x+0.2},y:{y+1.56},w:4.25,h:0.36,fontSize:9.5,color:C.muted,italic:true,margin:2}});',
                ]

        elif layout == "compare":
            lines.append(f'  titleBar(s, "{title}");')
            recommend = esc(points[0]) if points else title
            others = points[1:4] if len(points) > 1 else []
            lines += [
                f'  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{x:0.3,y:1.2,w:4.2,h:3.9,fill:{{color:C.bg2}},line:{{color:C.gold,width:2}},rectRadius:0.1}});',
                f'  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{x:1.2,y:1.2,w:1.6,h:0.4,fill:{{color:C.gold}},line:{{color:C.gold,width:0}},rectRadius:0.06}});',
                f'  s.addText("★  推薦", {{x:1.2,y:1.2,w:1.6,h:0.4,fontSize:12,bold:true,color:C.bg,align:"center",valign:"middle",margin:0}});',
                f'  s.addText("{recommend}", {{x:0.5,y:1.65,w:3.8,h:1.1,fontSize:30,bold:true,color:C.gold,fontFace:"Calibri",margin:0}});',
            ]
            pros_labels = ["24-48小時到位","持倉完全保留","借貸成本低～中","資金用途無限制"]
            for pi, pl in enumerate(pros_labels):
                lines += [
                    f'  s.addShape(pres.shapes.RECTANGLE, {{x:0.45,y:{2.85+pi*0.5},w:3.9,h:0.46,fill:{{color:{"C.bg3" if pi%2==0 else "C.bg2"}}},line:{{color:C.bg3,width:0}}}});',
                    f'  s.addShape(pres.shapes.OVAL, {{x:0.55,y:{2.92+pi*0.5},w:0.28,h:0.28,fill:{{color:C.gold}},line:{{color:C.gold,width:0}}}});',
                    f'  s.addText("✓", {{x:0.55,y:{2.92+pi*0.5},w:0.28,h:0.28,fontSize:9,bold:true,color:C.bg,align:"center",valign:"middle",margin:0}});',
                    f'  s.addText("{pl}", {{x:0.92,y:{2.86+pi*0.5},w:3.3,h:0.44,fontSize:14,color:C.white,fontFace:"Calibri",valign:"middle",margin:0}});',
                ]
            other_colors = [C["muted"], C["muted"], C["red"]]
            for oi, opt in enumerate(others[:3]):
                parts = opt.split("|")
                oname = esc(parts[0]) if parts else ""
                ocon1 = esc(parts[1]) if len(parts)>1 else ""
                ocon2 = esc(parts[2]) if len(parts)>2 else ""
                oc = other_colors[oi]
                y  = 1.2 + oi * 1.35
                lines += [
                    f'  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{x:4.8,y:{y},w:4.85,h:1.2,fill:{{color:C.bg2}},line:{{color:"{oc}",width:1}},rectRadius:0.08}});',
                    f'  s.addText("{oname}", {{x:5.0,y:{y+0.1},w:3.0,h:0.42,fontSize:16,bold:true,color:C.silver,fontFace:"Calibri",margin:0}});',
                    f'  s.addText("✗  {ocon1}", {{x:5.0,y:{y+0.52},w:2.2,h:0.3,fontSize:12,color:"{oc}",fontFace:"Calibri Light",margin:0}});',
                    f'  s.addText("✗  {ocon2}", {{x:7.2,y:{y+0.52},w:2.2,h:0.3,fontSize:12,color:C.muted,fontFace:"Calibri Light",margin:0}});',
                ]

        elif layout == "risk_list":
            lines += [
                f'  titleBar(s, "{title}", C.red);',
                f'  s.addImage({{path: IC+"warning.png", x:8.2,y:1.15,w:1.6,h:1.6}});',
            ]
            risk_colors = [C["red"], C["orange"], C["gold"], C["muted"]]
            for ri, pt in enumerate(points[:4]):
                parts  = pt.split("|")
                rtitle = esc(parts[0]) if parts else ""
                rdesc  = esc(parts[1]) if len(parts)>1 else ""
                rc = risk_colors[ri % 4]
                y  = 1.25 + ri * 1.05
                lines += [
                    f'  s.addShape(pres.shapes.RECTANGLE, {{x:0.3,y:{y+0.1},w:0.08,h:0.8,fill:{{color:"{rc}"}},line:{{color:"{rc}",width:0}}}});',
                    f'  s.addText("{rtitle}", {{x:0.52,y:{y+0.06},w:9.0,h:0.42,fontSize:16,bold:true,color:"{rc}",fontFace:"Calibri",margin:0}});',
                    f'  s.addText("{rdesc}", {{x:0.52,y:{y+0.46},w:7.5,h:0.52,fontSize:11,color:C.silver,fontFace:"Calibri Light",margin:0}});',
                ]
                if ri < 3:
                    lines.append(f'  s.addShape(pres.shapes.RECTANGLE, {{x:0.3,y:{y+1.02},w:9.4,h:0.02,fill:{{color:C.bg3}},line:{{color:C.bg3,width:0}}}});')

        elif layout == "profile_list":
            lines += [
                f'  titleBar(s, "{title}");',
                f'  s.addImage({{path: IC+"people.png", x:8.2,y:1.15,w:1.6,h:1.6}});',
            ]
            prof_colors = [C["gold"], C["calm"], C["green"], C["purple"]]
            for pfi, pt in enumerate(points[:4]):
                parts   = pt.split("|")
                pftitle = esc(parts[0]) if parts else ""
                pfcrit  = esc(parts[1]) if len(parts)>1 else ""
                pffit   = esc(parts[2]) if len(parts)>2 else "適合"
                pfc = prof_colors[pfi % 4]
                y   = 1.25 + pfi * 1.05
                lines += [
                    f'  s.addShape(pres.shapes.RECTANGLE, {{x:0.3,y:{y+0.1},w:0.08,h:0.8,fill:{{color:"{pfc}"}},line:{{color:"{pfc}",width:0}}}});',
                    f'  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {{x:7.2,y:{y+0.14},w:2.5,h:0.32,fill:{{color:"{pfc}"}},line:{{color:"{pfc}",width:0}},rectRadius:0.05}});',
                    f'  s.addText("{pffit}", {{x:7.2,y:{y+0.14},w:2.5,h:0.32,fontSize:10,bold:true,color:C.bg,align:"center",valign:"middle",margin:0}});',
                    f'  s.addText("{pftitle}", {{x:0.52,y:{y+0.06},w:6.5,h:0.42,fontSize:17,bold:true,color:"{pfc}",fontFace:"Calibri",margin:0}});',
                    f'  s.addText("{pfcrit}", {{x:0.52,y:{y+0.48},w:9.1,h:0.44,fontSize:11,color:C.silver,fontFace:"Calibri Light",margin:0}});',
                ]
                if pfi < 3:
                    lines.append(f'  s.addShape(pres.shapes.RECTANGLE, {{x:0.3,y:{y+1.02},w:9.4,h:0.02,fill:{{color:C.bg3}},line:{{color:C.bg3,width:0}}}});')

        elif layout == "closing":
            quote = esc(slide.get("quote", "好的資產規劃，是讓財富持續為你工作。"))
            lines += [
                '  s.addShape(pres.shapes.RECTANGLE, {x:0,y:0,w:0.12,h:5.625,fill:{color:C.gold},line:{color:C.gold,width:0}});',
                '  s.addShape(pres.shapes.OVAL, {x:5,y:-1,w:7,h:7,fill:{color:C.bg2},line:{color:C.bg2,width:0}});',
                f'  s.addImage({{path: IC+"rocket.png", x:7.0,y:0.4,w:2.5,h:2.5}});',
                f'  s.addText("{title}", {{x:0.35,y:0.3,w:7,h:1.1,fontSize:52,bold:true,color:C.white,fontFace:"Calibri",margin:0}});',
                f'  s.addText("{sub}", {{x:0.35,y:1.35,w:7,h:0.6,fontSize:22,color:C.gold,fontFace:"Calibri Light",italic:true,margin:0}});',
                '  s.addShape(pres.shapes.RECTANGLE, {x:0.35,y:2.05,w:5.0,h:0.05,fill:{color:C.gold},line:{color:C.gold,width:0}});',
            ]
            step_labels = points[:4] if points else ["評估需求","討論方案","提交申請","資金到位"]
            for si, sl in enumerate(step_labels):
                x = 0.35 + si * 2.35
                lines += [
                    f'  s.addShape(pres.shapes.OVAL, {{x:{x},y:2.25,w:0.6,h:0.6,fill:{{color:C.gold}},line:{{color:C.gold,width:0}}}});',
                    f'  s.addText("{si+1}", {{x:{x},y:2.25,w:0.6,h:0.6,fontSize:16,bold:true,color:C.bg,align:"center",valign:"middle",margin:0}});',
                    f'  s.addText("{esc(sl)}", {{x:{x-0.3},y:2.95,w:1.2,h:0.45,fontSize:13,color:C.silver,align:"center",fontFace:"Calibri Light",margin:0}});',
                ]
                if si < 3:
                    lines.append(f'  s.addShape(pres.shapes.RECTANGLE, {{x:{x+0.62},y:2.515,w:1.7,h:0.06,fill:{{color:C.muted}},line:{{color:C.muted,width:0}}}});')
            lines += [
                '  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {x:0.35,y:3.6,w:9.3,h:1.5,fill:{color:C.bg2},line:{color:C.gold,width:1.5},rectRadius:0.1});',
                '  s.addText(\'"\', {x:0.45,y:3.58,w:0.55,h:0.7,fontSize:48,bold:true,color:C.gold,fontFace:"Georgia",margin:0});',
                f'  s.addText("{quote}", {{x:0.95,y:3.75,w:8.4,h:1.1,fontSize:17,italic:true,color:C.gold2,fontFace:"Calibri Light",valign:"middle",margin:0}});',
                '  s.addText("本文件僅供內部培訓與客戶說明參考，不構成投資建議。實際條件以銀行公告為準。", {x:0.35,y:5.3,w:9.2,h:0.22,fontSize:8,color:C.muted,align:"center",margin:0});',
            ]

        lines.append('}')
        lines.append('')

    lines += [
        f'pres.writeFile({{ fileName: "{output_path}" }})',
        '  .then(() => console.log("OK"))',
        '  .catch(e => { console.error(e); process.exit(1); });',
    ]

    return "\n".join(lines)

# ══════════════════════════════════════════════════════
# 主入口：generate_ppt(topic) → Drive URL
# ══════════════════════════════════════════════════════
def generate_ppt(topic: str, n_slides: int = 12, color_theme: str = "navy",
                 visual_theme: str = "", force_pattern: str = "",
                 custom_bg: bool = False) -> str:
    """
    color_theme  : "navy"/"green"/"dark"（固定預設配色）
    visual_theme : 自由描述，如「深海星空」，優先於 color_theme
    force_pattern: 強制指定圖案，如「stars」「circuit」（visual_theme 模式下有效）
    custom_bg    : True → Claude 即時寫 PIL 程式碼自由繪製背景，失敗自動 fallback 到內建圖案
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        icon_dir  = os.path.join(tmp, "icons")
        pptx_path = os.path.join(tmp, "output.pptx")
        js_path   = os.path.join(tmp, "gen.js")

        # 1. 決定視覺規格
        if visual_theme:
            spec  = design_theme(visual_theme, force_pattern=force_pattern)
            theme = spec
        else:
            theme = THEMES.get(color_theme, THEMES["navy"])
            spec  = dict(theme,
                         pattern=force_pattern if force_pattern else "grid",
                         pattern_color=theme.get("calm","3A7CA5"),
                         pattern_alpha=0.06)

        # 2. 生成 icons
        generate_icons(icon_dir)

        # 3. 生成背景圖
        if custom_bg and visual_theme:
            # Claude 即時寫 PIL 程式碼，失敗自動退回內建圖案
            ok = generate_custom_background(icon_dir, visual_theme, spec)
            if not ok:
                print("[generate_ppt] custom_bg 失敗，退回內建圖案")
                generate_background(icon_dir, spec)
        else:
            generate_background(icon_dir, spec)

        # 4. 規劃架構
        slides = plan_slides(topic, n_slides)

        # 5. 生成 JS
        js_code = slides_to_js(slides, icon_dir, pptx_path, theme=theme, use_bg=True)
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(js_code)

        # 6. 執行 pptxgenjs
        subprocess.run(["npm", "install", "pptxgenjs"], cwd=tmp,
                       capture_output=True, timeout=60)
        result = subprocess.run(
            ["node", js_path],
            capture_output=True, text=True, timeout=120, cwd=tmp
        )
        if result.returncode != 0:
            raise RuntimeError(f"PPT生成失敗: {result.stderr[:300]}")

        # 7. 上傳 Drive
        safe_name = topic.replace("/","").replace(" ","_")[:30]
        filename  = f"{safe_name}_簡報.pptx"
        url = upload_to_drive(pptx_path, filename)
        return url


if __name__ == "__main__":
    url = generate_ppt("ELN股票連結票據", n_slides=9)
    print("Drive URL:", url)
