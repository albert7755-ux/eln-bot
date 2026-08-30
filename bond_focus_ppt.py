# -*- coding: utf-8 -*-
"""
bond_focus_ppt.py — 「債市每日聚焦 / 富邦好債報」PPTX 產生器（直式 A4，兩頁）
=========================================================================
用法：
    from bond_focus_ppt import build_focus_pptx
    path = build_focus_pptx(out_path, data)

data 需要的欄位（全部由 main.py 準備，避免本模組自行臆測）：
    issuer, issuer_en, intro, headline, news_bullets[list],
    ops_blocks[[標題, 內文], ...], revenue_mix{labels, values, unit_note},
    ratings{moody, sp, fitch, moody_outlook, sp_outlook, fitch_outlook, moody_date, sp_date, fitch_date},
    agency_comments[[機構, 評析], ...], bonds[list of dict], date_str
"""
import json
import os
import subprocess
import tempfile

TEMPLATE = r"""
const pptxgen = require("pptxgenjs");
const D = __DATA__;
const p = new pptxgen();
p.defineLayout({ name: "A4P", width: 8.27, height: 11.69 });
p.layout = "A4P";
const NAVY="1F4E79", BLUE="1F8AC0", TEAL="169B9B", GRAY="595959", RED="C00000";
const F="Microsoft JhengHei";
const M=0.45, W=8.27-2*M;

// ===== P1 債市每日聚焦 =====
let s = p.addSlide();
s.addText("🔍", { x:M, y:0.30, w:0.75, h:0.6, fontSize:30, isTextBox:true });
s.addText("債市每日聚焦", { x:M+0.72, y:0.28, w:5.2, h:0.65, fontSize:34, bold:true, color:NAVY, fontFace:F, isTextBox:true });
s.addShape(p.ShapeType.rect, { x:M, y:0.98, w:W, h:0.30, fill:{color:BLUE} });
s.addText(D.date_str, { x:M, y:0.98, w:W-0.15, h:0.30, fontSize:14, bold:true, color:"FFFFFF", align:"right", fontFace:F, isTextBox:true, margin:0 });

s.addText("焦點新聞", { x:M, y:1.45, w:2.2, h:0.38, fontSize:18, bold:true, color:BLUE, fontFace:F, isTextBox:true });
s.addShape(p.ShapeType.line, { x:M, y:1.86, w:2.1, h:0, line:{color:BLUE, width:2} });
s.addText(D.headline, { x:M, y:2.02, w:W, h:0.62, fontSize:19, bold:true, color:"222222", fontFace:F, isTextBox:true });

const nb = D.news_bullets.map((t,i)=>({ text:t, options:{ bullet:true, breakLine:i<D.news_bullets.length-1, paraSpaceAfter:10 } }));
s.addText(nb, { x:M, y:2.72, w:W, h:3.1, fontSize:13, color:"333333", fontFace:F, lineSpacing:21, isTextBox:true });

s.addText("焦點債券", { x:M, y:6.15, w:2.2, h:0.38, fontSize:18, bold:true, color:BLUE, fontFace:F, isTextBox:true });
s.addShape(p.ShapeType.line, { x:M, y:6.56, w:2.1, h:0, line:{color:BLUE, width:2} });
if (D.bond_tagline) s.addText(D.bond_tagline, { x:M+2.3, y:6.15, w:W-2.3, h:0.38, fontSize:13.5, bold:true, color:NAVY, align:"right", fontFace:F, isTextBox:true });

const head = ["債券代碼","債券名稱","票面利率%","YTM%","到期日"].map(t=>({text:t, options:{bold:true}}));
const rows = [head].concat(D.bonds.map(b=>[
  b.code,
  { text:b.name, options:{ color:TEAL, bold:true } },
  { text:String(b.coupon), options:{ color:RED, bold:true } },
  String(b.ytm), b.maturity ]));
s.addTable(rows, { x:M, y:6.75, w:7.37, colW:[1.95,2.05,1.15,0.95,1.27], rowH:0.34,
  fontSize:12, fontFace:F, align:"center", valign:"middle",
  border:{type:"solid", color:"D9D9D9", pt:0.5}, fill:{color:"FDF2EC"} });
s.addText("※ 報價與可承作與否以本行系統為準；商品條件依產品說明書。",
  { x:M, y:6.79+0.34*rows.length, w:W, h:0.3, fontSize:10, color:GRAY, fontFace:F, isTextBox:true });

s.addText("僅限內部教育訓練使用", { x:M, y:11.05, w:3.2, h:0.3, fontSize:13, bold:true, color:RED, fontFace:F, isTextBox:true });
s.addText("台北富邦銀行", { x:M, y:11.05, w:W, h:0.3, fontSize:13, bold:true, color:NAVY, align:"right", fontFace:F, isTextBox:true });

// ===== P2 富邦好債報 =====
let t = p.addSlide();
t.background = { color:"FFFFFF" };   // 白底(對照底稿)
t.addShape(p.ShapeType.roundRect, { x:1.35, y:0.28, w:5.55, h:0.66, fill:{color:"1B7FA8"}, rectRadius:0.06 });
t.addText("富邦好債報", { x:1.35, y:0.28, w:5.55, h:0.66, fontSize:26, bold:true, color:"FFFFFF", align:"center", fontFace:F, isTextBox:true, charSpacing:6 });
t.addText(D.issuer, { x:M, y:1.05, w:W, h:0.45, fontSize:26, bold:true, color:"222222", align:"center", fontFace:F, isTextBox:true });
t.addText(D.issuer_en, { x:M, y:1.48, w:W, h:0.3, fontSize:14, bold:true, color:GRAY, align:"center", fontFace:F, isTextBox:true });

t.addShape(p.ShapeType.roundRect, { x:M, y:1.85, w:W, h:1.15, fill:{color:"FFFFFF"}, line:{color:BLUE, width:1}, rectRadius:0.08 });
t.addText(D.intro, { x:M+0.12, y:1.93, w:W-0.24, h:1.0, fontSize:12, color:"333333", fontFace:F, lineSpacing:19, valign:"top", isTextBox:true });

t.addText("營運概況", { x:M, y:3.02, w:3.6, h:0.42, fontSize:21, bold:true, color:NAVY, align:"center", fontFace:F, isTextBox:true });
t.addShape(p.ShapeType.line, { x:M+0.35, y:3.44, w:2.9, h:0, line:{color:BLUE, width:1.5} });
t.addShape(p.ShapeType.roundRect, { x:M, y:3.54, w:3.6, h:2.36, fill:{color:"FFFFFF"}, line:{color:BLUE, width:1}, rectRadius:0.08 });
let ops=[];
D.ops_blocks.forEach((blk,i)=>{
  ops.push({ text:blk[0]+"：", options:{ bold:true, color:BLUE, breakLine:true } });
  ops.push({ text:blk[1], options:{ breakLine:i<D.ops_blocks.length-1, paraSpaceAfter:8 } });
});
t.addText(ops, { x:M+0.12, y:3.62, w:3.36, h:2.2, fontSize:11, color:"333333", fontFace:F, lineSpacing:16, valign:"top", isTextBox:true });

t.addText("營收結構", { x:4.25, y:3.02, w:3.57, h:0.42, fontSize:21, bold:true, color:NAVY, align:"center", fontFace:F, isTextBox:true });
t.addShape(p.ShapeType.line, { x:4.60, y:3.44, w:2.87, h:0, line:{color:BLUE, width:1.5} });
t.addShape(p.ShapeType.roundRect, { x:4.25, y:3.54, w:3.57, h:2.36, fill:{color:"FFFFFF"}, line:{color:BLUE, width:1}, rectRadius:0.08 });
if (D.revenue_mix && D.revenue_mix.values && D.revenue_mix.values.length) {
  t.addChart(p.ChartType.doughnut,
    [{ name:"營收結構", labels:D.revenue_mix.labels, values:D.revenue_mix.values }],
    { x:4.28, y:3.50, w:3.51, h:2.08, holeSize:30, showLegend:true, legendPos:"b", legendFontSize:8.5,
      chartColors:["169B9B","1F8AC0","BFBFBF","7F7F7F"], showValue:false, showPercent:true,
      dataLabelFontSize:11, dataLabelColor:"333333", dataLabelPosition:"outEnd", fontFace:F });
  t.addText(D.revenue_mix.unit_note||"", { x:4.28, y:5.60, w:3.51, h:0.26, fontSize:8.5, color:GRAY, align:"center", fontFace:F, isTextBox:true });
} else {
  t.addText("（無公開部門別營收資料）", { x:4.25, y:4.4, w:3.57, h:0.4, fontSize:12, color:GRAY, align:"center", fontFace:F, isTextBox:true });
}

t.addText("信用評等", { x:M, y:5.92, w:W, h:0.42, fontSize:21, bold:true, color:NAVY, align:"center", fontFace:F, isTextBox:true });
t.addShape(p.ShapeType.line, { x:2.6, y:6.34, w:3.1, h:0, line:{color:BLUE, width:1.5} });
const R=D.ratings||{};
const r2 = [
 ["信用評等","穆迪","標普","惠譽"].map(x=>({text:x,options:{bold:true}})),
 ["長期評等", R.moody||"--", R.sp||"--", R.fitch||"--"],
 ["評等展望", R.moody_outlook||"--", R.sp_outlook||"--", R.fitch_outlook||"--"],
 ["最近評等動作", R.moody_date||"--", R.sp_date||"--", R.fitch_date||"--"],
];
t.addTable(r2, { x:M, y:6.46, w:W, colW:[2.2,1.72,1.72,1.73], rowH:0.32, fontSize:12, fontFace:F,
  align:"center", valign:"middle", border:{type:"solid", color:"D9D9D9", pt:0.5} });

t.addText("信評公司評析", { x:M, y:7.78, w:W, h:0.42, fontSize:21, bold:true, color:NAVY, align:"center", fontFace:F, isTextBox:true });
t.addShape(p.ShapeType.line, { x:2.6, y:8.20, w:3.1, h:0, line:{color:BLUE, width:1.5} });
// 依文字量估算卡片高度(每行約 44 個中文字,每行 0.19")
const acChars = D.agency_comments.reduce((a,c)=>a+String(c[0]).length+String(c[1]).length, 0);
const acLines = Math.ceil(acChars/42) + D.agency_comments.length;
const acH = Math.max(1.05, Math.min(2.42, acLines*0.20 + 0.28));
t.addShape(p.ShapeType.roundRect, { x:M, y:8.30, w:W, h:acH, fill:{color:"FFFFFF"}, line:{color:BLUE, width:1}, rectRadius:0.08 });
let ac=[];
D.agency_comments.forEach((c,i)=>{
  ac.push({ text:c[0]+"－", options:{ bold:true, color:BLUE } });
  ac.push({ text:c[1], options:{ breakLine:i<D.agency_comments.length-1, paraSpaceAfter:9 } });
});
t.addText(ac, { x:M+0.12, y:8.38, w:W-0.24, h:acH-0.14, fontSize:11, color:"333333", fontFace:F, lineSpacing:17, valign:"top", isTextBox:true });

t.addText(D.source_note||"", { x:M, y:8.30+acH+0.08, w:W, h:0.22, fontSize:8.5, color:GRAY, fontFace:F, isTextBox:true });
t.addText("「本內容僅供參考且不構成要約或要約引誘。特定標的之商品風險、申購之條件、限制與費用及其他相關權利義務，應依產品說明暨投資風險預告書等相關文件為準。」",
  { x:M, y:8.30+acH+0.28, w:W, h:0.35, fontSize:8.5, color:GRAY, fontFace:F, isTextBox:true });
t.addText("僅限內部教育訓練使用", { x:M, y:11.30, w:3.2, h:0.28, fontSize:12, bold:true, color:RED, fontFace:F, isTextBox:true });
t.addText("台北富邦銀行", { x:M, y:11.30, w:W, h:0.28, fontSize:12, bold:true, color:NAVY, align:"right", fontFace:F, isTextBox:true });

p.writeFile({ fileName: __OUT__ }).then(()=>console.log("OK"));
"""


def build_focus_pptx(out_path, data):
    """產生 PPTX，回傳路徑；失敗丟例外"""
    js = (TEMPLATE
          .replace("__DATA__", json.dumps(data, ensure_ascii=False))
          .replace("__OUT__", json.dumps(out_path, ensure_ascii=False)))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
        js_path = f.name
    try:
        r = subprocess.run(["node", js_path], capture_output=True, text=True, timeout=120,
                           cwd=os.path.dirname(os.path.abspath(out_path)) or ".")
        if r.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError((r.stderr or r.stdout or "node failed")[:400])
        return out_path
    finally:
        try:
            os.remove(js_path)
        except Exception:
            pass
