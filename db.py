import asyncpg
from config import DATABASE_URL

CREATE_USUARIOS_SQL = """
CREATE TABLE IF NOT EXISTS usuarios (
    user_id TEXT PRIMARY KEY,
    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    weekly_xp INTEGER NOT NULL DEFAULT 0,
    last_message_timestamp DOUBLE PRECISION,
    rutina_hecha_today BOOLEAN DEFAULT FALSE,
    attachments_today INTEGER DEFAULT 0,
    last_rutina_date TEXT,
    last_attachment_date TEXT
);
"""

CREATE_CLASES_SQL = """
CREATE TABLE IF NOT EXISTS clases (
    id SERIAL PRIMARY KEY,
    tipo TEXT CHECK (tipo IN ('gratis','premium')) NOT NULL,
    descripcion TEXT
);
"""

async def init_db_pool():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está definida.")
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute(CREATE_USUARIOS_SQL)
        await conn.execute(CREATE_CLASES_SQL)
    return pool

async def ensure_user_exists(pool, user_id):
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM usuarios WHERE user_id=$1", str(user_id))
        if not exists:
            await conn.execute("INSERT INTO usuarios (user_id) VALUES ($1)", str(user_id))

async def get_user(pool, user_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM usuarios WHERE user_id=$1", str(user_id))

async def update_user(pool, user_id, **kwargs):
    allowed = ["xp","level","weekly_xp","last_message_timestamp",
               "rutina_hecha_today","attachments_today","last_rutina_date","last_attachment_date"]
    set_parts, values = [], []
    i = 1
    for k,v in kwargs.items():
        if k in allowed:
            set_parts.append(f"{k}=${i}")
            values.append(v)
            i += 1
    if not set_parts:
        return
    values.append(str(user_id))
    sql = f"UPDATE usuarios SET {', '.join(set_parts)} WHERE user_id=${i}"
    async with pool.acquire() as conn:
        await conn.execute(sql, *values)
