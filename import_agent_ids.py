import os
from sqlalchemy import create_engine, text

db = os.getenv("DATABASE_URL","")
if db.startswith("postgres://"): db = db.replace("postgres://","postgresql+psycopg://",1)
elif db.startswith("postgresql://"): db = db.replace("postgresql://","postgresql+psycopg://",1)
engine = create_engine(db, pool_pre_ping=True)

with engine.begin() as conn:
    conn.execute(text("""
    CREATE TABLE IF NOT EXISTS agent_line_ids (
        agent_name TEXT PRIMARY KEY,
        line_id TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """))

agent_map = {'JOSH': 'Uf652d8d47a0dcdcabf655daf9d5a0d2d', '舒舒': 'Uf903f2a360319a4b555740dbfd6d3414', '曉莉': 'U24f03903d2a5c0290e6c8ed0dc1addf1', '小美': 'U2dcb9edca302a4f6b8af61e4d9b99dbe', '人豪': 'U1b18e8d615005c1cdb7e2eb3cfb564e3', '明湖': 'U112e6ae9c8130916bb788833e45a3c29', '亭諭': 'U54c69b5e94eda5f3c6ad61b0f8e9b003', '亦峻': 'U374d7ce64bc2c64b14628f0a99fcc6e7', '馨怡': 'U01469cec6244bd23e372fa770008d310', '尚曄': 'U6b66fc4725fe5121b2d1956c5fd09184', '怡婷': 'U2adecaba6886f5c826879c710d406ccd', '蕙菁': 'Uca4fbbe1adb998924e671b8914588a40', '張原': 'Udec430d57b1712ed2fc9798c95808ef6', 'Rita': 'Uf543cbc8a95e175c39b699f9eeb9e2c6', 'Hank': 'U3bb46e49aebe0fa28ed9c6261f7afdeb', '冠宏': 'Uc32d4669d60f4b399f43cfcfb41057a2', 'Dino': 'Ud07f7df3c48ee93b5f9301dd801785b0', '育嘉': 'U6cee36766b3a34da724eec80ddec6269', '冠緯': 'Ue00f0a1796e74b50ad2961ed98f6597e', '庚霖': 'U0d2088bb0cb604f382cefe75aa20e0c3', '雅涵': 'Ua77b5018c307e1574a9988446231b930', 'Alex': 'Ude0f08c67351289cdbaba1c4e7688532', '怡雯': 'Uae50c9ff50f368e4dc8dbc9b639a61c5', '良哲': 'Uf4bf37f59a2bfb74a6859db71522772d', '小珺': 'Uea936f13b01fd392a2c4215f8029da8d'}

with engine.begin() as conn:
    for name, lid in agent_map.items():
        conn.execute(text("""
        INSERT INTO agent_line_ids (agent_name, line_id)
        VALUES (:n, :l)
        ON CONFLICT (agent_name) DO UPDATE SET line_id=EXCLUDED.line_id, updated_at=NOW()
        """), {"n": name, "l": lid})

print(f"✅ 成功匯入 {len(agent_map)} 位理專的 LINE ID 對照表")
