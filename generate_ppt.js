#!/usr/bin/env node
/**
 * 客戶投資組合建議書生成器
 * 風格：完全複刻「雅涵客戶黃大哥」版本
 * 用法：node generate_ppt.js --data '{...}' --output output.pptx
 */

const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fs = require("fs");

const args   = process.argv.slice(2);
const dataIdx = args.indexOf("--data");
const outIdx  = args.indexOf("--output");
const data    = JSON.parse(args[dataIdx + 1]);
const outFile = args[outIdx + 1] || "output.pptx";

// ─── 設計系統（完全來自原件） ─────────────────────────────
const C = {
  NAVY:    "1a2744",   // 深海軍藍（封面底色、Header bar、KPI卡）
  GOLD:    "c8a030",   // 金色（封面字色、第6頁 header bar、總結標題）
  GREEN:   "2e7d32",   // 深綠（施羅德 header bar、圖表）
  TEAL:    "1565a0",   // 鋼藍（PIMCO header bar、圖表）
  WHITE:   "FFFFFF",
  LGRAY:   "f0f2f8",   // 內容頁背景
  DGRAY:   "555555",
  MGRAY:   "888888",
  RED:     "c62828",   // 警示紅
  REDLT:   "ffcccc",   // 警示淡紅
  GRNDARK: "1b5e20",   // 深綠文字
  BLUELT:  "e3f0ff",   // 藍色淡底
  NAVYLT:  "2d3d6b",   // 副藍
};

// icon helper
const { FaLink, FaChartBar, FaCheckSquare, FaMapPin,
        FaTrophy, FaMoneyBillWave, FaBan, FaExclamationTriangle } = require("react-icons/fa");

async function iconPng(IconComp, color = "FFFFFF", size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(IconComp, { color: "#" + color, size: String(size) })
  );
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

// ─── 主程式 ──────────────────────────────────────────────────
async function build() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.title  = `${data.client_name} 投資組合建議書`;
  const W = 10, H = 5.625;

  // ─── 工具：深色 Header bar（每頁頂部帶 header bar 的版面） ─
  function addHeaderBar(slide, title, barColor = C.NAVY) {
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: W, h: 0.7,
      fill: { color: barColor },
      line: { color: barColor, width: 0, transparency: 100 },
    });
    slide.addText(title, {
      x: 0.35, y: 0, w: W - 0.5, h: 0.7,
      fontSize: 20, bold: true, color: C.WHITE,
      valign: "middle", margin: 0,
    });
  }

  // 工具：圓角白色卡片
  function addCard(slide, x, y, w, h) {
    slide.addShape(pres.shapes.RECTANGLE, {
      x, y, w, h,
      fill: { color: C.WHITE },
      line: { color: "dddddd", width: 0.5 },
      shadow: { type: "outer", blur: 8, offset: 2, color: "000000", opacity: 0.08 },
    });
  }

  // ══════════════════════════════════════════════════════
  // Slide 1 — 封面（深藍底 + 右側裝飾圓 + 金色細節）
  // ══════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.NAVY };

    // 右側裝飾大圓（透明度）
    s.addShape(pres.shapes.OVAL, {
      x: 7.2, y: -0.8, w: 4.5, h: 4.5,
      fill: { color: C.NAVYLT, transparency: 60 },
      line: { color: C.NAVYLT, width: 0, transparency: 100 },
    });
    s.addShape(pres.shapes.OVAL, {
      x: 7.8, y: 2.2, w: 3.2, h: 3.2,
      fill: { color: C.NAVYLT, transparency: 70 },
      line: { color: C.NAVYLT, width: 0, transparency: 100 },
    });

    // 左側金色竪條
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 0.12, h: H,
      fill: { color: C.GOLD },
      line: { color: C.GOLD, width: 0, transparency: 100 },
    });

    // 上方小標（字間距寬）
    s.addText("資 產 配 置 建 議 書", {
      x: 0.3, y: 0.55, w: 6, h: 0.35,
      fontSize: 11, color: C.GOLD, bold: false,
      charSpacing: 4, margin: 0,
    });

    // 主標題
    s.addText("客戶專屬投資組合", {
      x: 0.3, y: 0.9, w: 8, h: 1.1,
      fontSize: 42, bold: true, color: C.WHITE, margin: 0,
    });

    // 策略副標
    const strategy = data.strategy_name || "雙核心策略";
    s.addText(strategy, {
      x: 0.3, y: 1.95, w: 7, h: 0.65,
      fontSize: 24, bold: true, color: C.GOLD, margin: 0,
    });

    // 金色分隔線
    s.addShape(pres.shapes.LINE, {
      x: 0.3, y: 2.7, w: 7.5, h: 0,
      line: { color: C.GOLD, width: 1.5 },
    });

    // 資訊列
    const info = [
      { label: "投資金額：", value: data.investment_amount || "—", bold: false },
      { label: "策略目標：", value: data.strategy_goal || "最大夏普比率", bold: true, valueColor: C.GOLD },
      { label: "預期年報酬：", value: data.expected_return || "—", bold: true, suffix: `　｜　月均配息：${data.monthly_income || "—"}` },
    ];
    info.forEach((item, i) => {
      const y = 2.88 + i * 0.43;
      s.addText([
        { text: item.label, options: { color: "aabbcc", fontSize: 13 } },
        { text: item.value, options: { color: item.valueColor || C.WHITE, fontSize: 13, bold: item.bold || false } },
        ...(item.suffix ? [{ text: item.suffix, options: { color: C.WHITE, fontSize: 13 } }] : []),
      ], { x: 0.3, y, w: 8, h: 0.38, valign: "middle", margin: 0 });
    });

    // 底部免責
    s.addShape(pres.shapes.LINE, {
      x: 0.3, y: H - 0.52, w: W - 0.4, h: 0,
      line: { color: "3a4a6a", width: 0.5 },
    });
    s.addText([
      { text: `製作日期：${data.report_date || new Date().toLocaleDateString("zh-TW")}　｜　`, options: { color: "7788aa", fontSize: 9 } },
      { text: "本報告僅供市場分析與模擬參考，不構成任何投資建議或邀約。", options: { color: "7788aa", fontSize: 9 } },
    ], { x: 0.3, y: H - 0.48, w: W - 0.4, h: 0.32, valign: "middle", margin: 0 });
  }

  // ══════════════════════════════════════════════════════
  // Slides 2～N — 各標的介紹
  // ══════════════════════════════════════════════════════
  const assets = data.assets || [];
  // 每個標的對應的 header bar 顏色（第1個綠、第2個藍、其餘交替）
  const assetColors = [C.GREEN, C.TEAL, "6a1b9a", "e65100", "00838f", C.NAVY];

  for (let ai = 0; ai < assets.length; ai++) {
    const asset = assets[ai];
    const barColor = asset.bar_color || assetColors[ai % assetColors.length];
    const s = pres.addSlide();
    s.background = { color: C.LGRAY };

    // Header bar
    addHeaderBar(s, `基金${["一","二","三","四","五","六"][ai]}：  ${asset.name}`, barColor);

    // 左側卡片：投資策略
    addCard(s, 0.22, 0.82, 4.62, 4.65);

    const strategies = asset.strategies || [];
    // 策略標題
    s.addText("三大投資策略", {
      x: 0.38, y: 0.95, w: 3.5, h: 0.35,
      fontSize: 13, bold: true, color: barColor, margin: 0,
    });

    strategies.forEach((st, si) => {
      const y = 1.42 + si * 1.28;
      // 左側豎條
      s.addShape(pres.shapes.RECTANGLE, {
        x: 0.38, y, w: 0.06, h: 0.85,
        fill: { color: barColor }, line: { color: barColor, width: 0, transparency: 100 },
      });
      s.addText(st.title || "", {
        x: 0.52, y, w: 4.1, h: 0.32,
        fontSize: 11, bold: true, color: C.NAVY, margin: 0,
      });
      s.addText(st.desc || "", {
        x: 0.52, y: y + 0.34, w: 4.1, h: 0.5,
        fontSize: 9.5, color: C.DGRAY, valign: "top", margin: 0,
      });
    });

    // 底部小字
    const footNote = asset.footnote || "";
    s.addText(footNote, {
      x: 0.38, y: 5.22, w: 4.3, h: 0.25,
      fontSize: 8.5, color: C.MGRAY, italic: true, margin: 0,
    });

    // 右側：績效表現
    addCard(s, 5.08, 0.82, 4.62, 2.55);
    const perf = asset.performance || {};
    s.addText(`績效表現 (截至${perf.as_of || ""})`, {
      x: 5.22, y: 0.92, w: 4.2, h: 0.3,
      fontSize: 11, bold: true, color: C.NAVY, margin: 0,
    });

    // 績效表格 header
    const perfRows = perf.rows || [];
    const perfHeader = [
      [
        { text: "期間", options: { bold: true, color: C.NAVY, fontSize: 10 } },
        { text: perf.col1 || "本基金", options: { bold: true, color: barColor, fontSize: 10 } },
        { text: perf.col2 || "同類型平均", options: { bold: true, color: C.DGRAY, fontSize: 10 } },
      ],
      ...perfRows.map((r, ri) => [
        { text: r.period, options: { bold: ri === perfRows.length - 1, color: C.NAVY, fontSize: 10 } },
        { text: r.val1, options: { bold: true, color: C.GRNDARK, fontSize: 11 } },
        { text: r.val2, options: { color: C.DGRAY, fontSize: 10 } },
      ]),
    ];
    s.addTable(perfHeader, {
      x: 5.22, y: 1.28, w: 4.35,
      colW: [1.2, 1.6, 1.55],
      fontSize: 10, align: "center",
      border: { pt: 0.3, color: "dddddd" },
    });
    // 四分位說明
    if (perf.rank_note) {
      s.addText(perf.rank_note, {
        x: 5.22, y: 2.82, w: 4.35, h: 0.3,
        fontSize: 8.5, color: C.MGRAY, italic: true, margin: 0,
      });
    }

    // 右下：最新資產配置
    addCard(s, 5.08, 3.52, 4.62, 1.94);
    const alloc = asset.allocation || {};
    s.addText(`最新資產配置 (${alloc.as_of || ""})`, {
      x: 5.22, y: 3.6, w: 4.2, h: 0.28,
      fontSize: 11, bold: true, color: C.NAVY, margin: 0,
    });

    const allocItems = alloc.items || [];
    const allocColors = [C.NAVY, barColor, C.TEAL, C.GOLD, "888888"];
    const barW = 4.2 / allocItems.length - 0.08;
    allocItems.forEach((item, ii) => {
      const bx = 5.22 + ii * (barW + 0.08);
      s.addShape(pres.shapes.RECTANGLE, {
        x: bx, y: 3.98, w: barW, h: 0.38,
        fill: { color: allocColors[ii % allocColors.length] },
        line: { color: allocColors[ii % allocColors.length], width: 0, transparency: 100 },
      });
      s.addText(item.pct || "", {
        x: bx, y: 4.42, w: barW, h: 0.3,
        fontSize: 11, bold: true, color: allocColors[ii % allocColors.length],
        align: "center", margin: 0,
      });
      s.addText(item.label || "", {
        x: bx, y: 4.73, w: barW, h: 0.25,
        fontSize: 8.5, color: C.DGRAY, align: "center", margin: 0,
      });
    });
  }

  // ══════════════════════════════════════════════════════
  // Slide — 排除分析（相關係數 + 夏普比較）
  // ══════════════════════════════════════════════════════
  const pageExcl = 2 + assets.length;
  {
    const s = pres.addSlide();
    s.background = { color: C.LGRAY };

    const excl = data.excluded || {};
    const exclTitle = excl.title || `為什麼不納入${excl.name || "候選標的"}？－科學回測告訴我們`;
    addHeaderBar(s, exclTitle, C.NAVY);

    // 左側卡：相關係數
    addCard(s, 0.22, 0.82, 4.62, 3.05);
    const linkIcon = await iconPng(FaLink, C.TEAL, 128);
    s.addImage({ data: linkIcon, x: 0.38, y: 0.94, w: 0.28, h: 0.28 });
    s.addText("相關係數分析", {
      x: 0.72, y: 0.94, w: 3.5, h: 0.28,
      fontSize: 12, bold: true, color: C.NAVY, margin: 0,
    });
    s.addText(excl.corr_desc || "", {
      x: 0.38, y: 1.3, w: 4.35, h: 0.55,
      fontSize: 9.5, color: C.DGRAY, margin: 0,
    });

    // 相關係數矩陣
    const corrM = excl.correlation_matrix || {};
    const corrLabels = corrM.labels || [];
    const corrVals   = corrM.values || [];
    if (corrLabels.length > 0) {
      const headerRow = [
        { text: "", options: { fill: { color: C.NAVY }, color: C.WHITE, bold: true, fontSize: 9 } },
        ...corrLabels.map(l => ({ text: l, options: { fill: { color: C.NAVY }, color: C.WHITE, bold: true, fontSize: 8.5 } })),
      ];
      const dataRows = corrLabels.map((rl, ri) => [
        { text: rl, options: { fill: { color: C.NAVY }, color: C.WHITE, bold: true, fontSize: 8.5 } },
        ...corrVals[ri].map((v, ci) => {
          const vn  = parseFloat(v);
          const isD = ri === ci;
          const isH = !isD && vn >= 0.7; // 高相關警示
          const bgc = isD ? C.NAVY : isH ? C.REDLT : C.WHITE;
          const fc  = isD ? C.WHITE : isH ? C.RED : C.DGRAY;
          return {
            text: isD ? "1.00" : vn.toFixed(2),
            options: { fill: { color: bgc }, color: fc, bold: isD || isH, fontSize: 10 }
          };
        }),
      ]);
      s.addTable([headerRow, ...dataRows], {
        x: 0.38, y: 1.9, w: 4.35,
        colW: [1.45, ...Array(corrLabels.length).fill((4.35 - 1.45) / corrLabels.length)],
        fontSize: 10, align: "center",
        border: { pt: 0.5, color: "cccccc" },
      });
    }

    // 右側卡：夏普比率比較
    addCard(s, 5.08, 0.82, 4.62, 3.05);
    const chartIcon = await iconPng(FaChartBar, C.GRNDARK, 128);
    s.addImage({ data: chartIcon, x: 5.24, y: 0.94, w: 0.28, h: 0.28 });
    s.addText("夏普比率比較", {
      x: 5.58, y: 0.94, w: 3.5, h: 0.28,
      fontSize: 12, bold: true, color: C.NAVY, margin: 0,
    });

    const sharpeItems = excl.sharpe_comparison || [];
    const maxSharpe = Math.max(...sharpeItems.map(i => parseFloat(i.sharpe) || 0));
    sharpeItems.forEach((item, si) => {
      const y = 1.35 + si * 0.82;
      const barPct = Math.min(parseFloat(item.sharpe) / maxSharpe, 1);
      const barColor2 = si === sharpeItems.length - 1 ? C.RED : C.GREEN; // 最後一個（排除）紅色
      const maxBarW = 4.1;

      s.addText(item.name || "", {
        x: 5.24, y, w: 4.35, h: 0.24,
        fontSize: 9.5, color: C.DGRAY, margin: 0,
      });
      s.addShape(pres.shapes.RECTANGLE, {
        x: 5.24, y: y + 0.26, w: maxBarW * barPct, h: 0.38,
        fill: { color: barColor2 },
        line: { color: barColor2, width: 0, transparency: 100 },
      });
      s.addText(`夏普 ${item.sharpe} | 年報酬 ${item.ret}`, {
        x: 5.28, y: y + 0.28, w: maxBarW * barPct - 0.1, h: 0.34,
        fontSize: 9.5, bold: true, color: C.WHITE, valign: "middle", margin: 0,
      });
    });

    // 建議配置 Banner
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.22, y: 3.97, w: W - 0.44, h: 0.65,
      fill: { color: C.NAVY },
      line: { color: C.NAVY, width: 0, transparency: 100 },
    });
    const checkIcon = await iconPng(FaCheckSquare, C.GREEN, 128);
    s.addImage({ data: checkIcon, x: 0.38, y: 4.08, w: 0.34, h: 0.34 });
    s.addText(excl.recommendation || "", {
      x: 0.8, y: 3.98, w: W - 1.1, h: 0.63,
      fontSize: 11, bold: true, color: C.WHITE, valign: "middle", margin: 0,
    });

    // 底部 3 個 KPI（無卡片框，直接白底）
    const kpiItems = [
      { label: "投資組合夏普比率", value: excl.portfolio_sharpe || "—", sub: excl.sharpe_vs || "" },
      { label: "年化報酬率",       value: excl.portfolio_ret   || "—", sub: excl.ret_vs    || "" },
      { label: "年化波動率",       value: excl.portfolio_vol   || "—", sub: excl.vol_note  || "" },
    ];
    kpiItems.forEach((kpi, ki) => {
      const x = 0.22 + ki * 3.26;
      addCard(s, x, 4.68, 3.1, 0.82);
      s.addText(kpi.label, {
        x: x + 0.15, y: 4.71, w: 2.8, h: 0.22,
        fontSize: 9, color: C.MGRAY, margin: 0,
      });
      s.addText(kpi.value, {
        x: x + 0.15, y: 4.92, w: 2.7, h: 0.38,
        fontSize: 22, bold: true, color: C.NAVY, margin: 0,
      });
      s.addText(kpi.sub, {
        x: x + 0.15, y: 5.28, w: 2.8, h: 0.2,
        fontSize: 8, color: C.MGRAY, margin: 0,
      });
    });
  }

  // ══════════════════════════════════════════════════════
  // Slide — 回測分析模組
  // ══════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.LGRAY };

    const bt = data.backtest || {};
    const btTitle = `回測分析模組　｜　歷史 ${bt.years || 5} 年數據驗證（${bt.period || ""}）`;
    addHeaderBar(s, btTitle, C.NAVY);

    // 4 個大 KPI 卡（深藍底）
    const kpis = [
      { label: "年化報酬率", value: bt.ann_ret  || "—", sub: "優於同類型均值" },
      { label: "年化波動率", value: bt.ann_vol  || "—", sub: "中低風險水準" },
      { label: "夏普比率",   value: bt.sharpe   || "—", sub: "風報比卓越" },
      { label: "最大回撤",   value: bt.mdd      || "—", sub: bt.mdd_note || "PIMCO 最低", gold: true },
    ];
    kpis.forEach((kpi, ki) => {
      const x = 0.22 + ki * 2.42;
      s.addShape(pres.shapes.RECTANGLE, {
        x, y: 0.82, w: 2.32, h: 1.22,
        fill: { color: C.NAVY },
        line: { color: C.NAVY, width: 0, transparency: 100 },
      });
      s.addText(kpi.value, {
        x: x + 0.12, y: 0.9, w: 2.1, h: 0.65,
        fontSize: 30, bold: true, color: kpi.gold ? C.GOLD : C.WHITE, margin: 0,
      });
      s.addText(kpi.label, {
        x: x + 0.12, y: 1.55, w: 2.1, h: 0.26,
        fontSize: 9.5, color: "aabbcc", margin: 0,
      });
      s.addText(kpi.sub, {
        x: x + 0.12, y: 1.76, w: 2.1, h: 0.22,
        fontSize: 8.5, color: C.GOLD, italic: true, margin: 0,
      });
    });

    // 左卡：年報酬信賴區間
    addCard(s, 0.22, 2.18, 5.45, 3.24);
    s.addText("年報酬信賴區間分析（常態分配）", {
      x: 0.38, y: 2.28, w: 5, h: 0.3,
      fontSize: 11, bold: true, color: C.NAVY, margin: 0,
    });

    const intervals = bt.confidence_intervals || [];
    const intColors = [C.TEAL, C.GREEN, C.GOLD];
    // 畫橫向區間 bar
    const mu = parseFloat(bt.ann_ret_num || bt.ann_ret) || 15;
    const sigma = parseFloat(bt.ann_vol_num || bt.ann_vol) || 8;
    const ranges = intervals.length > 0 ? intervals : [
      { label: "68% (1σ)",     lo: mu - sigma,         hi: mu + sigma },
      { label: "95% (1.645σ)", lo: mu - 1.645 * sigma, hi: mu + 1.645 * sigma },
      { label: "99% (2.326σ)", lo: mu - 2.326 * sigma, hi: mu + 2.326 * sigma },
    ];
    const allVals = ranges.flatMap(r => [r.lo, r.hi]);
    const minV = Math.min(...allVals) - 2;
    const maxV = Math.max(...allVals) + 2;
    const barAreaX = 1.5, barAreaW = 3.8;
    const toX = v => barAreaX + ((v - minV) / (maxV - minV)) * barAreaW;
    const muX = toX(mu);

    ranges.forEach((r, ri) => {
      const y = 2.72 + ri * 0.72;
      s.addText(r.label, {
        x: 0.38, y, w: 1.05, h: 0.28,
        fontSize: 9, color: C.DGRAY, margin: 0,
      });
      const bx = toX(r.lo);
      const bw = toX(r.hi) - bx;
      s.addShape(pres.shapes.RECTANGLE, {
        x: bx, y: y + 0.05, w: bw, h: 0.38,
        fill: { color: intColors[ri] },
        line: { color: intColors[ri], width: 0, transparency: 100 },
      });
      s.addText(`${r.lo.toFixed(2)}% ～ ${r.hi.toFixed(2)}%`, {
        x: bx, y: y + 0.07, w: bw, h: 0.34,
        fontSize: 8.5, bold: true, color: C.WHITE, align: "right", valign: "middle", margin: 4,
      });
    });
    // 預期均值虛線
    s.addShape(pres.shapes.LINE, {
      x: muX, y: 2.68, w: 0, h: 2.4,
      line: { color: C.RED, width: 1, dashType: "dash" },
    });
    s.addText(`預期 ${mu.toFixed(2)}%`, {
      x: muX - 0.5, y: 5.08, w: 1, h: 0.24,
      fontSize: 8, color: C.RED, align: "center", margin: 0,
    });

    // 右卡：正報酬機率
    addCard(s, 5.87, 2.18, 3.94, 3.24);
    s.addText("正報酬機率（歷史統計）", {
      x: 6.0, y: 2.28, w: 3.65, h: 0.3,
      fontSize: 11, bold: true, color: C.NAVY, margin: 0,
    });

    const winRates = bt.win_rates || [];
    if (winRates.length > 0) {
      const wr = data.assets || [];
      const colNames = ["持有期間", "投資組合", ...wr.slice(0, 2).map(a => a.short_name || a.name.slice(0, 4))];
      const wrHeader = colNames.map((c, ci) => ({
        text: c,
        options: { bold: true, color: ci === 0 ? C.NAVY : ci === 1 ? C.GREEN : C.TEAL, fontSize: 9.5 }
      }));
      const wrRows = winRates.map(r => [
        { text: r.period,    options: { bold: true, color: C.NAVY, fontSize: 9.5 } },
        { text: r.portfolio, options: { bold: true, color: C.GRNDARK, fontSize: 10 } },
        ...(r.funds || []).map(f => ({ text: f, options: { color: C.DGRAY, fontSize: 9.5 } })),
      ]);
      s.addTable([wrHeader, ...wrRows], {
        x: 6.0, y: 2.62, w: 3.65,
        colW: [1.0, 1.1, ...Array(Math.max(colNames.length - 2, 0)).fill((3.65 - 2.1) / Math.max(colNames.length - 2, 1))],
        fontSize: 9.5, align: "center",
        border: { pt: 0.3, color: "dddddd" },
        rowH: 0.38,
      });
    }
  }

  // ══════════════════════════════════════════════════════
  // Slide — AI 白話解讀（如有）
  // ══════════════════════════════════════════════════════
  if (data.ai_commentary) {
    const s = pres.addSlide();
    s.background = { color: C.LGRAY };
    addHeaderBar(s, "⏱  30秒投資組合解讀　｜　重點一次看清楚", C.NAVY);

    addCard(s, 0.22, 0.82, W - 0.44, H - 1.1);

    const paras = data.ai_commentary.split(/\n\n+/).filter(p => p.trim());
    const paraH = Math.min((H - 1.3) / Math.max(paras.length, 1), 1.35);
    paras.forEach((para, i) => {
      s.addText(para.trim(), {
        x: 0.42, y: 0.98 + i * (paraH + 0.08),
        w: W - 0.84, h: paraH,
        fontSize: 11.5, color: C.DARK,
        valign: "top", margin: 0,
        lineSpacingMultiple: 1.6,
      });
    });

    // 固定警語
    s.addText("⚠️ 過往績效不保證未來表現，投資前請審慎評估自身風險承受度。", {
      x: 0.35, y: H - 0.3, w: W - 0.55, h: 0.22,
      fontSize: 8, color: C.GRAY, margin: 0,
    });
  }

  // ══════════════════════════════════════════════════════
  // Slide — 配息現金流試算
  // ══════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.LGRAY };

    const cf = data.cashflow || {};
    addHeaderBar(s, "配息現金流試算　｜　月月入帳，穩定生活所需", C.GOLD);

    // 4個 KPI 卡（深藍底 + 金色 header 條）
    const cfKpis = [
      { label: "投資本金",   value: cf.principal   || data.investment_amount || "—", sub: "100%  配置" },
      { label: "年化配息率", value: cf.annual_rate  || "—", sub: "加權平均" },
      { label: "年領總息",   value: cf.annual_total || "—", sub: "12 個月合計" },
      { label: "月均領息",   value: cf.monthly_avg  || "—", sub: "每月穩定入帳" },
    ];
    cfKpis.forEach((kpi, ki) => {
      const x = 0.22 + ki * 2.42;
      // 金色 header 小條
      s.addShape(pres.shapes.RECTANGLE, {
        x, y: 0.82, w: 2.32, h: 0.22,
        fill: { color: C.GOLD },
        line: { color: C.GOLD, width: 0, transparency: 100 },
      });
      // 深藍主體
      s.addShape(pres.shapes.RECTANGLE, {
        x, y: 1.04, w: 2.32, h: 1.1,
        fill: { color: C.NAVY },
        line: { color: C.NAVY, width: 0, transparency: 100 },
      });
      s.addText(kpi.label, {
        x: x + 0.1, y: 1.06, w: 2.1, h: 0.26,
        fontSize: 9, color: "aabbcc", margin: 0,
      });
      s.addText(kpi.value, {
        x: x + 0.1, y: 1.3, w: 2.1, h: 0.52,
        fontSize: ki === 0 ? 16 : 22, bold: true, color: C.WHITE, margin: 0,
      });
      s.addText(kpi.sub, {
        x: x + 0.1, y: 1.82, w: 2.1, h: 0.24,
        fontSize: 8.5, color: C.GOLD, italic: true, margin: 0,
      });
    });

    // 左卡：各標的配息明細
    addCard(s, 0.22, 2.32, 5.45, 3.1);
    s.addText("各標的配息明細", {
      x: 0.38, y: 2.42, w: 5, h: 0.3,
      fontSize: 11, bold: true, color: C.NAVY, margin: 0,
    });

    const cfItems = cf.items || [];
    const cfTableHeader = [
      [
        { text: "標的",   options: { bold: true, color: C.NAVY, fontSize: 10 } },
        { text: "配置",   options: { bold: true, color: C.NAVY, fontSize: 10 } },
        { text: "金額",   options: { bold: true, color: C.NAVY, fontSize: 10 } },
        { text: "月配息", options: { bold: true, color: C.NAVY, fontSize: 10 } },
      ],
      ...cfItems.map((item, ii) => [
        { text: item.name || "—",   options: { color: C.DGRAY, fontSize: 9.5 } },
        { text: item.alloc || "—",  options: { color: C.DGRAY, fontSize: 9.5 } },
        { text: item.amount || "—", options: { color: C.DGRAY, fontSize: 9.5 } },
        { text: item.monthly || "—",options: { color: C.DGRAY, fontSize: 9.5 } },
      ]),
      // 合計列
      [
        { text: "合計", options: { bold: true, color: C.WHITE, fill: { color: C.NAVY }, fontSize: 10 } },
        { text: "100%", options: { bold: true, color: C.WHITE, fill: { color: C.NAVY }, fontSize: 10 } },
        { text: cf.principal || data.investment_amount || "—", options: { bold: true, color: C.WHITE, fill: { color: C.NAVY }, fontSize: 10 } },
        { text: cf.monthly_avg || "—", options: { bold: true, color: C.WHITE, fill: { color: C.NAVY }, fontSize: 10 } },
      ],
    ];
    s.addTable(cfTableHeader, {
      x: 0.38, y: 2.78, w: 5.15,
      colW: [1.6, 0.9, 1.5, 1.15],
      fontSize: 9.5, align: "center",
      border: { pt: 0.3, color: "dddddd" },
      rowH: 0.48,
    });

    // 右卡：逐月現金流長條圖
    addCard(s, 5.87, 2.32, 3.94, 3.1);
    s.addText("逐月現金流明細 (NT$)", {
      x: 6.0, y: 2.42, w: 3.65, h: 0.3,
      fontSize: 11, bold: true, color: C.NAVY, margin: 0,
    });

    // 堆疊長條圖
    const months = ["一","二","三","四","五","六","七","八","九","十","十一","十二"];
    const fund1Monthly = parseFloat((cf.items && cf.items[0] && cf.items[0].monthly_num) || 0);
    const fund2Monthly = parseFloat((cf.items && cf.items[1] && cf.items[1].monthly_num) || 0);
    const f1color = assetColors[1]; // PIMCO→藍
    const f2color = assetColors[0]; // 施羅德→綠

    if (fund1Monthly + fund2Monthly > 0) {
      const chartData = [
        { name: (cf.items && cf.items[0] && cf.items[0].short_name) || "基金1",
          labels: months, values: Array(12).fill(fund1Monthly) },
        { name: (cf.items && cf.items[1] && cf.items[1].short_name) || "基金2",
          labels: months, values: Array(12).fill(fund2Monthly) },
      ];
      s.addChart(pres.charts.BAR, chartData, {
        x: 5.92, y: 2.76, w: 3.82, h: 2.55,
        barDir: "col", barGrouping: "stacked",
        chartColors: [f1color, f2color],
        showLegend: true, legendPos: "b",
        showValue: false,
        valAxisHidden: true,
        catAxisLabelColor: C.DGRAY,
        chartArea: { fill: { color: C.WHITE } },
        valGridLine: { style: "none" },
        catGridLine: { style: "none" },
        dataLabelFontSize: 7,
      });
    }
  }

  // ══════════════════════════════════════════════════════
  // Slide — 投資組合重點總結
  // ══════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.NAVY };

    // 右側裝飾圓
    s.addShape(pres.shapes.OVAL, {
      x: 7.5, y: 2.5, w: 4, h: 4,
      fill: { color: C.NAVYLT, transparency: 70 },
      line: { color: C.NAVYLT, width: 0, transparency: 100 },
    });

    // 金色標題
    s.addText("投資組合重點總結", {
      x: 0.35, y: 0.22, w: 7, h: 0.7,
      fontSize: 28, bold: true, color: C.GOLD, margin: 0,
    });

    // 5 條重點（icon方塊 + 文字）
    const summary = data.summary || {};
    const summaryItems = summary.items || [];
    const iconList = [FaMapPin, FaTrophy, FaChartBar, FaMoneyBillWave, FaBan];
    const iconColors = [C.TEAL, C.GOLD, C.TEAL, C.GOLD, "c62828"];
    const bgColors   = [C.NAVYLT, "4a3a00", C.NAVYLT, "4a3a00", "5a1010"];

    for (let si = 0; si < summaryItems.length; si++) {
      const item = summaryItems[si];
      const y = 0.98 + si * 0.82;
      const iData = await iconPng(iconList[si % iconList.length], iconColors[si % iconColors.length], 128);

      // icon 背景方塊
      s.addShape(pres.shapes.RECTANGLE, {
        x: 0.35, y: y - 0.02, w: 0.55, h: 0.55,
        fill: { color: bgColors[si % bgColors.length] },
        line: { color: bgColors[si % bgColors.length], width: 0, transparency: 100 },
      });
      s.addImage({ data: iData, x: 0.42, y: y + 0.04, w: 0.35, h: 0.35 });

      s.addText(item, {
        x: 1.0, y: y, w: 7.5, h: 0.55,
        fontSize: 13, color: si === summaryItems.length - 1 ? C.GOLD : C.WHITE,
        valign: "middle", margin: 0,
        bold: si === summaryItems.length - 1,
      });
    }

    // 底部免責聲明 bar
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: H - 0.48, w: W, h: 0.48,
      fill: { color: "0d1a33" },
      line: { color: "0d1a33", width: 0, transparency: 100 },
    });
    const warnIcon = await iconPng(FaExclamationTriangle, C.GOLD, 128);
    s.addImage({ data: warnIcon, x: 0.2, y: H - 0.4, w: 0.22, h: 0.22 });
    s.addText(data.disclaimer || "免責聲明：本報告所有數據均基於歷史資料計算，不代表未來績效。配息金額以各機構實際公告為準。僅供內部教育訓練使用，請勿外流。", {
      x: 0.5, y: H - 0.45, w: W - 0.6, h: 0.4,
      fontSize: 8.5, color: "7788aa", valign: "middle", margin: 0,
    });
  }

  await pres.writeFile({ fileName: outFile });
  console.log("OK:" + outFile);
}

build().catch(e => { console.error("ERR:" + e.message); process.exit(1); });
