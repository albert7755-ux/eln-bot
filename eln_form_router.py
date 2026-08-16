"""
eln_form_router.py
ELN 商品資料維護網頁的後端 API
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, text
import os
from pathlib import Path

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

ADMIN_PASSWORD = "0000"

def init_eln_products_table():
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eln_products (
            id BIGSERIAL PRIMARY KEY,
            bond_id TEXT NOT NULL UNIQUE,
            trade_date DATE,
            product_type TEXT DEFAULT 'FCN',
            tenure_months INT,
            currency TEXT DEFAULT '美元',
            t1_code TEXT, t1_initial FLOAT,
            t2_code TEXT, t2_initial FLOAT,
            t3_code TEXT, t3_initial FLOAT,
            t4_code TEXT, t4_initial FLOAT,
            t5_code TEXT, t5_initial FLOAT,
            coupon_pct FLOAT,
            strike_pct FLOAT DEFAULT 100,
            ko_pct FLOAT DEFAULT 100,
            ko_type TEXT DEFAULT 'Daily Memory',
            ki_pct FLOAT DEFAULT 70,
            ki_type TEXT DEFAULT 'AKI',
            issue_date DATE,
            valuation_date DATE,
            maturity_date DATE,
            agent_name TEXT,
            line_id TEXT DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """))

try:
    init_eln_products_table()
except Exception as e:
    print(f"[ELN Form] 建表失敗: {e}")


@router.get("/eln-form", response_class=HTMLResponse)
async def eln_form_page():
    html_path = Path(__file__).parent / "eln_form.html"
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/eln-form/products")
async def get_products(agent: str = ""):
    """查詢商品清單（管理員查全部，理專查自己的）"""
    with engine.begin() as conn:
        if agent:
            rows = conn.execute(text("""
                SELECT id, bond_id, trade_date, product_type, tenure_months, currency,
                       t1_code, t1_initial, t2_code, t2_initial, t3_code, t3_initial,
                       t4_code, t4_initial, t5_code, t5_initial,
                       coupon_pct, strike_pct, ko_pct, ko_type, ki_pct, ki_type,
                       issue_date, valuation_date, maturity_date, agent_name, line_id,
                       created_at
                FROM eln_products
                WHERE agent_name ILIKE :a
                ORDER BY trade_date DESC
            """), {"a": f"%{agent}%"}).fetchall()
        else:
            rows = conn.execute(text("""
                SELECT id, bond_id, trade_date, product_type, tenure_months, currency,
                       t1_code, t1_initial, t2_code, t2_initial, t3_code, t3_initial,
                       t4_code, t4_initial, t5_code, t5_initial,
                       coupon_pct, strike_pct, ko_pct, ko_type, ki_pct, ki_type,
                       issue_date, valuation_date, maturity_date, agent_name, line_id,
                       created_at
                FROM eln_products
                ORDER BY trade_date DESC
            """)).fetchall()

    cols = ["id", "bond_id", "trade_date", "product_type", "tenure_months", "currency",
            "t1_code", "t1_initial", "t2_code", "t2_initial", "t3_code", "t3_initial",
            "t4_code", "t4_initial", "t5_code", "t5_initial",
            "coupon_pct", "strike_pct", "ko_pct", "ko_type", "ki_pct", "ki_type",
            "issue_date", "valuation_date", "maturity_date", "agent_name", "line_id",
            "created_at"]

    result = []
    for row in rows:
        d = dict(zip(cols, row))
        for k in ["trade_date", "issue_date", "valuation_date", "maturity_date", "created_at"]:
            if d[k]:
                d[k] = str(d[k])
        result.append(d)
    return {"products": result}


@router.post("/eln-form/add")
async def add_product(request: Request):
    """新增商品"""
    data = await request.json()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
            INSERT INTO eln_products (
                bond_id, trade_date, product_type, tenure_months, currency,
                t1_code, t1_initial, t2_code, t2_initial, t3_code, t3_initial,
                t4_code, t4_initial, t5_code, t5_initial,
                coupon_pct, strike_pct, ko_pct, ko_type, ki_pct, ki_type,
                issue_date, valuation_date, maturity_date, agent_name, line_id
            ) VALUES (
                :bond_id, :trade_date, :product_type, :tenure_months, :currency,
                :t1_code, :t1_initial, :t2_code, :t2_initial, :t3_code, :t3_initial,
                :t4_code, :t4_initial, :t5_code, :t5_initial,
                :coupon_pct, :strike_pct, :ko_pct, :ko_type, :ki_pct, :ki_type,
                :issue_date, :valuation_date, :maturity_date, :agent_name, :line_id
            )
            """), {
                "bond_id": data.get("bond_id", "").strip(),
                "trade_date": data.get("trade_date") or None,
                "product_type": data.get("product_type", "FCN"),
                "tenure_months": data.get("tenure_months") or None,
                "currency": data.get("currency", "美元"),
                "t1_code": data.get("t1_code", "").upper().strip() or None,
                "t1_initial": float(data["t1_initial"]) if data.get("t1_initial") else None,
                "t2_code": data.get("t2_code", "").upper().strip() or None,
                "t2_initial": float(data["t2_initial"]) if data.get("t2_initial") else None,
                "t3_code": data.get("t3_code", "").upper().strip() or None,
                "t3_initial": float(data["t3_initial"]) if data.get("t3_initial") else None,
                "t4_code": data.get("t4_code", "").upper().strip() or None,
                "t4_initial": float(data["t4_initial"]) if data.get("t4_initial") else None,
                "t5_code": data.get("t5_code", "").upper().strip() or None,
                "t5_initial": float(data["t5_initial"]) if data.get("t5_initial") else None,
                "coupon_pct": float(data["coupon_pct"]) if data.get("coupon_pct") else None,
                "strike_pct": float(data.get("strike_pct", 100)),
                "ko_pct": float(data.get("ko_pct", 100)),
                "ko_type": data.get("ko_type", "Daily Memory"),
                "ki_pct": float(data.get("ki_pct", 70)),
                "ki_type": data.get("ki_type", "AKI"),
                "issue_date": data.get("issue_date") or None,
                "valuation_date": data.get("valuation_date") or None,
                "maturity_date": data.get("maturity_date") or None,
                "agent_name": data.get("agent_name", "").strip(),
                "line_id": data.get("line_id", "").strip(),
            })
        return {"success": True, "message": f"✅ {data.get('bond_id')} 新增成功"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"新增失敗：{str(e)}")


@router.get("/eln-form/export")
async def export_products(password: str = ""):
    """匯出全部商品為 Excel（需密碼）"""
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="密碼錯誤")
    import pandas as pd
    import io
    from fastapi.responses import StreamingResponse
    from datetime import datetime as _dt

    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT bond_id, trade_date, product_type, tenure_months, currency,
                   t1_code, t1_initial, t2_code, t2_initial, t3_code, t3_initial,
                   t4_code, t4_initial, t5_code, t5_initial,
                   coupon_pct, strike_pct, ko_pct, ko_type, ki_pct, ki_type,
                   issue_date, valuation_date, maturity_date, agent_name, line_id,
                   created_at
            FROM eln_products
            ORDER BY trade_date ASC
        """)).fetchall()

    records = []
    for r in rows:
        records.append({
            "債券代號": r[0], "交易日": r[1], "商品類型": r[2], "天期 (月)": r[3], "幣別": r[4],
            "標的1": r[5], "標的1.1": r[6], "標的2": r[7], "標的2.1": r[8],
            "標的3": r[9], "標的3.1": r[10], "標的4": r[11], "標的4.1": r[12],
            "標的5": r[13], "標的5.1": r[14],
            "收益率(年化%)/": r[15], "執行價格(%)": r[16],
            "KO 價格(%)": r[17], "KO 類型": r[18], "KI 價格(%)": r[19], "KI 類型": r[20],
            "發行日": r[21], "最終評價日": r[22], "到期日": r[23],
            "理專": r[24], "LINE_ID": r[25], "建立時間": str(r[26])[:19] if r[26] else "",
        })

    df = pd.DataFrame(records)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    filename = f"ELN_products_{_dt.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/eln-form/delete")
async def delete_product(request: Request):
    """刪除商品（需密碼）"""
    data = await request.json()
    if data.get("password") != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="密碼錯誤")
    product_id = data.get("id")
    if not product_id:
        raise HTTPException(status_code=400, detail="缺少商品 ID")
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM eln_products WHERE id=:id"), {"id": product_id})
        return {"success": True, "message": "✅ 刪除成功"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"刪除失敗：{str(e)}")


@router.post("/eln-form/update")
async def update_product(request: Request):
    """修改商品（需密碼）"""
    data = await request.json()
    if data.get("password") != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="密碼錯誤")
    product_id = data.get("id")
    if not product_id:
        raise HTTPException(status_code=400, detail="缺少商品 ID")
    try:
        with engine.begin() as conn:
            conn.execute(text("""
            UPDATE eln_products SET
                bond_id=:bond_id, trade_date=:trade_date, product_type=:product_type,
                tenure_months=:tenure_months, currency=:currency,
                t1_code=:t1_code, t1_initial=:t1_initial,
                t2_code=:t2_code, t2_initial=:t2_initial,
                t3_code=:t3_code, t3_initial=:t3_initial,
                t4_code=:t4_code, t4_initial=:t4_initial,
                t5_code=:t5_code, t5_initial=:t5_initial,
                coupon_pct=:coupon_pct, strike_pct=:strike_pct,
                ko_pct=:ko_pct, ko_type=:ko_type, ki_pct=:ki_pct, ki_type=:ki_type,
                issue_date=:issue_date, valuation_date=:valuation_date,
                maturity_date=:maturity_date, agent_name=:agent_name,
                line_id=:line_id, updated_at=NOW()
            WHERE id=:id
            """), {
                "id": product_id,
                "bond_id": data.get("bond_id", "").strip(),
                "trade_date": data.get("trade_date") or None,
                "product_type": data.get("product_type", "FCN"),
                "tenure_months": data.get("tenure_months") or None,
                "currency": data.get("currency", "美元"),
                "t1_code": data.get("t1_code", "").upper().strip() or None,
                "t1_initial": float(data["t1_initial"]) if data.get("t1_initial") else None,
                "t2_code": data.get("t2_code", "").upper().strip() or None,
                "t2_initial": float(data["t2_initial"]) if data.get("t2_initial") else None,
                "t3_code": data.get("t3_code", "").upper().strip() or None,
                "t3_initial": float(data["t3_initial"]) if data.get("t3_initial") else None,
                "t4_code": data.get("t4_code", "").upper().strip() or None,
                "t4_initial": float(data["t4_initial"]) if data.get("t4_initial") else None,
                "t5_code": data.get("t5_code", "").upper().strip() or None,
                "t5_initial": float(data["t5_initial"]) if data.get("t5_initial") else None,
                "coupon_pct": float(data["coupon_pct"]) if data.get("coupon_pct") else None,
                "strike_pct": float(data.get("strike_pct", 100)),
                "ko_pct": float(data.get("ko_pct", 100)),
                "ko_type": data.get("ko_type", "Daily Memory"),
                "ki_pct": float(data.get("ki_pct", 70)),
                "ki_type": data.get("ki_type", "AKI"),
                "issue_date": data.get("issue_date") or None,
                "valuation_date": data.get("valuation_date") or None,
                "maturity_date": data.get("maturity_date") or None,
                "agent_name": data.get("agent_name", "").strip(),
                "line_id": data.get("line_id", "").strip(),
            })
        return {"success": True, "message": f"✅ 修改成功"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"修改失敗：{str(e)}")
