# -*- coding: utf-8 -*-
"""
一般信託質借試算 — 債券設質借台幣，再買海外債
資料來源：Supabase bond_price_history（每日報價檔經龍蝦Bot /bond-upload 寫入）
僅供內部教育訓練／試算參考，實際以核貸結果為準
"""
import math
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="一般信託質借試算", page_icon="🏦", layout="centered")

NAVY, GOLD = "#0B2A5B", "#C9A227"
st.markdown(f"""
<style>
.stApp {{ background: #F7F8FB; }}
h1, h2, h3 {{ color: {NAVY}; }}
.tag {{ display:inline-block; background:{GOLD}; color:#fff; padding:2px 10px;
        border-radius:12px; font-size:0.8rem; }}
.card {{ background:#fff; border:1px solid #E3E7EF; border-left:5px solid {NAVY};
         border-radius:10px; padding:12px 16px; margin:6px 0; }}
.big {{ font-size:1.6rem; font-weight:700; color:{NAVY}; }}
.muted {{ color:#6B7280; font-size:0.85rem; }}
</style>
""", unsafe_allow_html=True)

MAX_RATIO = 0.50            # 一般信託質借成數上限
LINES = [                   # 擔保維持率門檻
    (170, "補足後須達"),
    (160, "接近補倉通知"),
    (140, "補倉通知（T1 起 3 個營業日內）"),
    (130, "平倉預告（T 起 2 個營業日內）"),
]
DEFAULT_FX = {"USD": 32.0, "AUD": 21.0, "EUR": 35.0, "GBP": 41.0, "JPY": 0.21, "CNY": 4.4, "NZD": 19.0}


# ───────────────────────── 密碼 ─────────────────────────
def check_password():
    pw = st.secrets.get("APP_PASSWORD", "")
    if not pw or st.session_state.get("auth_ok"):
        return True
    st.title("🏦 一般信託質借試算")
    st.markdown('<span class="tag">僅供內部教育訓練使用・請勿外流</span>', unsafe_allow_html=True)
    v = st.text_input("請輸入密碼", type="password")
    if v:
        if v == pw:
            st.session_state["auth_ok"] = True
            st.rerun()
        st.error("密碼錯誤")
    return False


if not check_password():
    st.stop()


# ───────────────────────── 資料 ─────────────────────────
@st.cache_data(ttl=600, show_spinner="讀取最新報價…")
def load_quotes():
    url = st.secrets["SUPABASE_URL"].rstrip("/")
    key = st.secrets["SUPABASE_KEY"]
    h = {"apikey": key}
    if key.startswith("eyJ"):          # 舊版 anon key 需要 Bearer
        h["Authorization"] = f"Bearer {key}"
    api = f"{url}/rest/v1/bond_price_history"
    r = requests.get(api, params={"select": "snap_date", "order": "snap_date.desc", "limit": 1},
                     headers=h, timeout=20)
    r.raise_for_status()
    snap = r.json()[0]["snap_date"]
    r = requests.get(api, params={"select": "*", "snap_date": f"eq.{snap}", "limit": 5000},
                     headers={**h, "Range": "0-4999"}, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    for c in ["offer", "bid", "ytm", "ltv", "coupon", "min_amt"]:
        if c not in df.columns:
            df[c] = None
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["offer"]).copy()
    df["bid"] = df["bid"].fillna(df["offer"])
    df["ccy"] = df["ccy"].fillna("USD").astype(str).str.strip().str.upper()
    df["label"] = (df["bond_name"].astype(str) + "｜" + df["ccy"] + "｜到期 " + df["maturity"].astype(str)
                   + "｜" + df["isin"].astype(str))
    df = df.sort_values(["ccy", "bond_name"]).drop_duplicates("isin").set_index("label")
    return snap, df


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fx(ccy):
    try:
        import yfinance as yf
        h = yf.Ticker("TWD=X" if ccy == "USD" else f"{ccy}TWD=X").history(period="5d")
        v = float(h["Close"].dropna().iloc[-1])
        return round(v, 4) if v > 0 else None
    except Exception:
        return None


try:
    snap_date, quotes = load_quotes()
except Exception as e:
    st.error(f"讀取報價失敗，請稍後再試或聯絡管理者。（{e}）")
    st.stop()


def fmt(v, d=0):
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:,.{d}f}"


def status_of(ratio_pct):
    if ratio_pct > 160:
        return "🟢 正常"
    if ratio_pct > 140:
        return "🟡 接近補倉"
    if ratio_pct > 130:
        return "🟠 補倉通知"
    return "🔴 平倉預告"


# ───────────────────────── 標題 ─────────────────────────
st.title("🏦 一般信託質借試算")
st.markdown(f'<span class="tag">僅供內部教育訓練使用・請勿外流</span> '
            f'<span class="muted">報價日期：{snap_date}（共 {len(quotes)} 檔）</span>',
            unsafe_allow_html=True)
st.caption("用客戶在本行的特定金錢信託（外國債券）設質借**台幣**，再拿動用資金買新的海外債。"
           "成數依 Lombard 鑑價，超過 50% 以 50% 為限；擔保品市值以 **Bid 價** 計算。")

if quotes["ltv"].isna().all():
    st.warning("今日報價尚未帶入 LTV，暫以 50% 計算可借上限。請到 bond-upload 重新上傳今日報價檔。")

# ───────────────────────── ① 原持有債券 ─────────────────────────
st.header("① 客戶要設質的債券")
labels = list(quotes.index)
orig_sel = st.multiselect("搜尋債券名稱或 ISIN（可多選）", labels, key="orig_sel",
                          placeholder="例如：蘋果、Meta、US0378")

# ───────────────────────── 匯率／利率 ─────────────────────────
new_sel_preview = st.session_state.get("new_sel", [])
ccys = sorted({quotes.loc[l, "ccy"] for l in orig_sel + list(new_sel_preview)} or {"USD"})
with st.expander("💱 匯率與借款利率設定", expanded=False):
    fx = {}
    cols = st.columns(min(len(ccys), 3) or 1)
    for i, c in enumerate(ccys):
        live = fetch_fx(c)
        with cols[i % len(cols)]:
            fx[c] = st.number_input(f"{c}/TWD 匯率", min_value=0.0001, value=float(live or DEFAULT_FX.get(c, 30.0)),
                                    step=0.01, format="%.4f", key=f"fx_{c}",
                                    help="預設抓最近收盤匯率，可自行修改")
    rate = st.number_input("質借年利率（%）", min_value=0.0, max_value=20.0, value=2.6, step=0.05, format="%.2f")

if not orig_sel:
    st.info("先選客戶要拿來設質的債券。")
    st.stop()

orig_rows, ltv_missing = [], []
for l in orig_sel:
    q = quotes.loc[l]
    step = 1000.0
    face = st.number_input(f"{q['bond_name']}（{q['ccy']}）面額", min_value=0.0,
                           value=float(max(0 if pd.isna(q["min_amt"]) else q["min_amt"], 10000)), step=step, format="%.0f",
                           key=f"face_{q['isin']}")
    ltv = q["ltv"]
    if pd.isna(ltv):
        ltv_missing.append(q["bond_name"])
        eff = MAX_RATIO
    else:
        eff = min(float(ltv), MAX_RATIO)
    mv = face * q["bid"] / 100 * fx[q["ccy"]]
    orig_rows.append(dict(債券=q["bond_name"], 幣別=q["ccy"], 面額=face, Bid=q["bid"],
                          LTV=None if pd.isna(ltv) else float(ltv), 採用成數=eff,
                          市值_TWD=mv, 可借_TWD=mv * eff))
orig = pd.DataFrame(orig_rows)
orig_mv = orig["市值_TWD"].sum()
max_loan = orig["可借_TWD"].sum()

st.dataframe(orig.style.format({"面額": "{:,.0f}", "Bid": "{:.2f}", "LTV": "{:.0%}", "採用成數": "{:.0%}",
                                "市值_TWD": "{:,.0f}", "可借_TWD": "{:,.0f}"}, na_rep="—"),
             hide_index=True, width="stretch")
if ltv_missing:
    st.caption("⚠️ 報價檔未提供 LTV，暫以 50% 計：" + "、".join(ltv_missing))

c1, c2 = st.columns(2)
c1.markdown(f'<div class="card">擔保品市值（台幣）<div class="big">{fmt(orig_mv)}</div></div>', unsafe_allow_html=True)
c2.markdown(f'<div class="card">可借上限（台幣）<div class="big">{fmt(max_loan)}</div></div>', unsafe_allow_html=True)

if max_loan <= 0:
    st.stop()

# ───────────────────────── ② 動用資金 ─────────────────────────
st.header("② 動用資金")
st.caption("一般信託質借只能借一次。建議**不要借滿**：隔年續約時若債價下跌，50% 的額度會跟著變小，原借款可能超過新額度而要先還一部分。")
default_loan = min(math.floor(orig_mv * 0.30 / 10000) * 10000, math.floor(max_loan / 10000) * 10000)
loan = st.number_input("動用金額（台幣）", min_value=0.0, max_value=float(math.floor(max_loan)),
                       value=float(default_loan), step=10000.0, format="%.0f")
use_pct = loan / orig_mv if orig_mv else 0
st.progress(min(use_pct / MAX_RATIO, 1.0), text=f"動用成數 {use_pct:.1%}（上限 50%）")
if use_pct > 0.40:
    st.warning("動用成數已超過 40%，債價或匯率小幅下跌就可能接近補倉線，建議降低動用金額。")

if loan <= 0:
    st.stop()

# ───────────────────────── ③ 買新債 ─────────────────────────
st.header("③ 用動用資金買新債")
new_sel = st.multiselect("選擇要買的海外債（可多選）", labels, key="new_sel",
                         placeholder="例如：可口可樂、微軟")
# 新債若有新幣別，匯率輸入框在上方展開區補上
for l in new_sel:
    c = quotes.loc[l, "ccy"]
    if c not in fx:
        fx[c] = fetch_fx(c) or DEFAULT_FX.get(c, 30.0)

new_rows = []
if new_sel:
    left = loan
    for i, l in enumerate(new_sel):
        q = quotes.loc[l]
        dflt = math.floor(loan / len(new_sel) / 10000) * 10000
        alloc = st.number_input(f"{q['bond_name']} 投入金額（台幣）", min_value=0.0, value=float(dflt),
                                step=10000.0, format="%.0f", key=f"alloc_{q['isin']}")
        f = fx[q["ccy"]]
        min_amt = float(q["min_amt"]) if not pd.isna(q["min_amt"]) else 0.0
        face = math.floor(alloc / f / (q["offer"] / 100) / 1000) * 1000
        note = ""
        if min_amt and face < min_amt:
            note = f"未達最低申購 {fmt(min_amt)}"
            face = 0
        cpn = q["coupon"]
        new_rows.append(dict(債券=q["bond_name"], 幣別=q["ccy"], Offer=q["offer"], YTM=q["ytm"],
                             票面利率=cpn, 可買面額=face,
                             成本_TWD=face * q["offer"] / 100 * f,
                             年配息_TWD=None if pd.isna(cpn) else face * cpn / 100 * f,
                             擔保市值_TWD=face * q["bid"] / 100 * f, 備註=note))
    new = pd.DataFrame(new_rows)
    st.dataframe(new.style.format({"Offer": "{:.2f}", "YTM": "{:.2f}%", "票面利率": "{:.3f}%",
                                   "可買面額": "{:,.0f}", "成本_TWD": "{:,.0f}", "年配息_TWD": "{:,.0f}",
                                   "擔保市值_TWD": "{:,.0f}"}, na_rep="—"),
                 hide_index=True, width="stretch")
    spent = new["成本_TWD"].sum()
    if spent > loan + 1:
        st.error(f"投入金額合計超過動用資金 {fmt(spent - loan)} 元，請調整。")
    else:
        st.caption(f"實際買進成本 {fmt(spent)} 元，剩餘未用 {fmt(loan - spent)} 元（面額以 1,000 為單位無條件捨去；未含前手息）")
else:
    new = pd.DataFrame(columns=["成本_TWD", "年配息_TWD", "擔保市值_TWD", "YTM", "可買面額"])
    st.info("選擇要買的新債，就能算配息與淨收益。")

pledge_new = st.checkbox("新買的債券也一起設質（追繳時才會用到，預設不計入）", value=False)

# ───────────────────────── ④ 試算結果 ─────────────────────────
st.header("④ 試算結果")
new_mv = new["擔保市值_TWD"].sum() if len(new) else 0.0
coll_mv = orig_mv + (new_mv if pledge_new else 0.0)
ratio = coll_mv / loan * 100
interest = loan * rate / 100
cpn_income = new["年配息_TWD"].sum(min_count=1) if len(new) else float("nan")
net = None if pd.isna(cpn_income) else cpn_income - interest

c1, c2, c3 = st.columns(3)
c1.metric("擔保維持率", f"{ratio:.0f}%", status_of(ratio), delta_color="off")
c2.metric("年利息（台幣）", fmt(interest))
c3.metric("新債年配息（台幣）", fmt(cpn_income))
if net is not None:
    st.markdown(f'<div class="card">每年淨增加收益（配息 − 利息）'
                f'<div class="big">{fmt(net)} 元</div>'
                f'<span class="muted">約每月 {fmt(net / 12)} 元；未計匯率變動、手續費與信管費</span></div>',
                unsafe_allow_html=True)
elif new_sel:
    st.caption("今日報價尚未帶入票面利率，重新上傳今日報價檔後即可顯示配息。")

st.caption("擔保維持率 ＝ 設質信託受益權總市價 ÷ 現放餘額 × 100%"
           + ("（含新買債券）" if pledge_new else "（僅原設質債券）"))

st.subheader("擔保品要跌多少會碰到各條線")
line_rows = []
for L, desc in LINES:
    drop = 1 - (L / 100) * loan / coll_mv
    line_rows.append({"擔保維持率": f"{L}%", "說明": desc,
                      "擔保品市值剩下（台幣）": fmt(loan * L / 100),
                      "整體下跌幅度": "已低於" if drop <= 0 else f"{drop:.1%}"})
st.dataframe(pd.DataFrame(line_rows), hide_index=True, width="stretch")
st.caption("下跌幅度＝債價與匯率合計的影響。借的是台幣、擔保品是外幣，**台幣升值也會讓維持率下降**。")

# ───────────────────────── ⑤ 壓力測試 ─────────────────────────
st.header("⑤ 壓力測試／一年後續約")
c1, c2 = st.columns(2)
p_chg = c1.slider("債價變動（%）", -40, 20, -10)
f_chg = c2.slider("外幣兌台幣匯率變動（%）", -15, 15, 0, help="負值＝台幣升值")
k = (1 + p_chg / 100) * (1 + f_chg / 100)
s_mv = coll_mv * k
s_ratio = s_mv / loan * 100
renew_cap = (orig["可借_TWD"].sum() + (new_mv * MAX_RATIO if pledge_new else 0)) * k
gap = max(0.0, loan - renew_cap)

c1, c2, c3 = st.columns(3)
c1.metric("情境後擔保市值", fmt(s_mv))
c2.metric("情境後維持率", f"{s_ratio:.0f}%", status_of(s_ratio), delta_color="off")
c3.metric("續約時可借上限", fmt(renew_cap))
if s_ratio <= 140:
    need_pay = max(0.0, loan - s_mv / 1.7)
    st.error(f"此情境已達補倉線：償還後擔保維持率須達 170%，約需償還 {fmt(need_pay)} 元或補提等值擔保品。")
if gap > 0:
    st.warning(f"一年後續約：新額度 {fmt(renew_cap)} 元，低於原借款 {fmt(loan)} 元，約需先還 {fmt(gap)} 元。")
else:
    st.success("此情境下續約額度仍足以涵蓋原借款。")

# ───────────────────────── 規定摘要 ─────────────────────────
with st.expander("📋 一般信託質借規定摘要"):
    st.markdown("""
- **對象**：專業投資人（20～80 歲）
- **成數**：依 Lombard 鑑價辦法計算，超過 50% 以 50% 為限
- **擔保維持率**：越高越安全；≤160% 接近補倉通知、≤140% 補倉通知、≤130% 平倉預告
- **補倉**：T1 起 3 個營業日（含）內償還或補提，償還後須達 170%；未補足發 T2，2 個營業日（含）內補足，否則處分設質標的
- **平倉預告**：通知日 T 起 2 個營業日（含）內償還或補提，否則處分設質標的
- **借款期間**：不得超過信託資產到期日；非循環型最長 1 年；循環型不得超過專業投資人資格有效期限，且最長 1 年
- **還款方式**：每月繳息、到期還本；P1、P2 可選循環或非循環，P3、P4 只能非循環
- **幣別**：外幣信託可借外幣或台幣（可承作幣別依財管存授業務處公告）
- **動支期限**：核准日起 2 個月內動撥或設定額度，延長最長不超過 3 個月
""")

st.markdown("---")
st.caption("本試算僅供內部教育訓練與試算參考，報價、成數、匯率及利率皆以實際核貸與交易結果為準。"
           "債券價格與匯率會變動，質借投資可能放大損失，並可能被要求補繳擔保品或處分設質標的。")
