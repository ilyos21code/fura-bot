import aiosqlite

DB_PATH = "fura.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    full_name TEXT
);

CREATE TABLE IF NOT EXISTS trucks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS truck_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    truck_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'UZS',
    rate REAL NOT NULL DEFAULT 1,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (truck_id) REFERENCES trucks(id)
);

CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    truck_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (truck_id) REFERENCES trucks(id)
);

CREATE TABLE IF NOT EXISTS trip_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'UZS',
    rate REAL NOT NULL DEFAULT 1,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (trip_id) REFERENCES trips(id)
);

CREATE TABLE IF NOT EXISTS trip_legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id INTEGER NOT NULL,
    from_point TEXT NOT NULL,
    to_point TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'UZS',
    rate REAL NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (trip_id) REFERENCES trips(id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Har bir summa ASL valyutasida saqlanadi (amount + currency).
# 'rate' — yozuv kiritilgan PAYTDAGI USD kursi (muzlatilgan).
# So'mdagi qiymat = (currency='USD') ? amount * rate : amount
# Shunday qilib, kurs keyin o'zgarsa ham, o'tgan yozuvlar o'sha paytdagi kursda qoladi.

# Barcha summalar bazada UZS (so'm) da saqlanadi.
# USD kiritilsa, o'sha paytdagi kurs bo'yicha so'mga aylantirib saqlanadi.
# Bu — hisob-kitobni sodda va barqaror qiladi (kurs keyin o'zgarsa ham, o'tgan yozuvlar o'zgarmaydi).


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        # Migration: eski bazalarda 'currency'/'rate' ustunlari bo'lmasa qo'shamiz
        for table in ("truck_expenses", "trip_expenses", "trip_legs"):
            cur = await conn.execute(f"PRAGMA table_info({table})")
            cols = [row[1] for row in await cur.fetchall()]
            if "currency" not in cols:
                await conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN currency TEXT NOT NULL DEFAULT 'UZS'"
                )
            if "rate" not in cols:
                await conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN rate REAL NOT NULL DEFAULT 1"
                )
        await conn.commit()


# ---------------- Settings (kurs saqlash) ----------------
async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await conn.commit()


async def get_setting(key: str, default: str = None):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def get_or_create_user(telegram_id: int, full_name: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT id FROM users WHERE telegram_id=?", (telegram_id,))
        row = await cur.fetchone()
        if row:
            return row[0]
        cur = await conn.execute(
            "INSERT INTO users (telegram_id, full_name) VALUES (?, ?)",
            (telegram_id, full_name),
        )
        await conn.commit()
        return cur.lastrowid


# ---------------- Trucks ----------------
async def add_truck(user_id: int, name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        # Bir xil nomli fura allaqachon bor bo'lsa, yangisini qo'shmaymiz (takrorlanishning oldini olish)
        cur = await conn.execute(
            "SELECT id FROM trucks WHERE user_id=? AND name=?", (user_id, name)
        )
        existing = await cur.fetchone()
        if existing:
            return existing[0]
        cur = await conn.execute(
            "INSERT INTO trucks (user_id, name) VALUES (?, ?)", (user_id, name)
        )
        await conn.commit()
        return cur.lastrowid


async def list_trucks(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT t.id, t.name, t.created_at,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END)
                                FROM truck_expenses WHERE truck_id=t.id), 0) as repair_total
               FROM trucks t WHERE t.user_id=? ORDER BY t.created_at DESC""",
            (user_id,),
        )
        return await cur.fetchall()


async def get_truck(user_id: int, truck_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT id, name FROM trucks WHERE id=? AND user_id=?", (truck_id, user_id)
        )
        return await cur.fetchone()


async def delete_truck(user_id: int, truck_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM truck_expenses WHERE truck_id=?", (truck_id,))
        await conn.execute("DELETE FROM trucks WHERE id=? AND user_id=?", (truck_id, user_id))
        await conn.commit()


async def add_truck_expense(truck_id: int, category: str, amount: float, currency: str = "UZS", rate: float = 1, note: str = ""):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO truck_expenses (truck_id, category, amount, currency, rate, note) VALUES (?, ?, ?, ?, ?, ?)",
            (truck_id, category, amount, currency, rate, note),
        )
        await conn.commit()
        return cur.lastrowid


async def list_truck_expenses(truck_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT id, category, amount, currency, rate, note, created_at FROM truck_expenses WHERE truck_id=? ORDER BY created_at DESC",
            (truck_id,),
        )
        return await cur.fetchall()


async def delete_truck_expense(user_id: int, expense_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """DELETE FROM truck_expenses WHERE id=? AND truck_id IN
               (SELECT id FROM trucks WHERE user_id=?)""",
            (expense_id, user_id),
        )
        await conn.commit()


# ---------------- Trips ----------------
async def create_trip(user_id: int, truck_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO trips (user_id, truck_id, status) VALUES (?, ?, 'active')",
            (user_id, truck_id),
        )
        await conn.commit()
        return cur.lastrowid


async def get_active_trips(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT tr.id, tr.truck_id, t.name FROM trips tr
               JOIN trucks t ON t.id = tr.truck_id
               WHERE tr.user_id=? AND tr.status='active'
               ORDER BY tr.created_at DESC""",
            (user_id,),
        )
        return await cur.fetchall()


async def get_active_trip_for_truck(user_id: int, truck_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT id FROM trips
               WHERE user_id=? AND truck_id=? AND status='active'
               LIMIT 1""",
            (user_id, truck_id),
        )
        return await cur.fetchone()


async def finish_trip(user_id: int, trip_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE trips SET status='finished', finished_at=datetime('now') WHERE id=? AND user_id=?",
            (trip_id, user_id),
        )
        await conn.commit()


async def delete_trip(user_id: int, trip_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM trip_expenses WHERE trip_id=?", (trip_id,))
        await conn.execute("DELETE FROM trip_legs WHERE trip_id=?", (trip_id,))
        await conn.execute("DELETE FROM trips WHERE id=? AND user_id=?", (trip_id, user_id))
        await conn.commit()


async def delete_trip_expense(user_id: int, expense_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """DELETE FROM trip_expenses WHERE id=? AND trip_id IN
               (SELECT id FROM trips WHERE user_id=?)""",
            (expense_id, user_id),
        )
        await conn.commit()


async def delete_trip_leg(user_id: int, leg_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """DELETE FROM trip_legs WHERE id=? AND trip_id IN
               (SELECT id FROM trips WHERE user_id=?)""",
            (leg_id, user_id),
        )
        await conn.commit()


async def list_trips(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT tr.id, tr.truck_id, t.name, tr.status, tr.created_at, tr.finished_at,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN price*rate ELSE price END) FROM trip_legs WHERE trip_id=tr.id), 0) as income,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END) FROM trip_expenses WHERE trip_id=tr.id), 0) as expense
               FROM trips tr JOIN trucks t ON t.id = tr.truck_id
               WHERE tr.user_id=?
               ORDER BY tr.created_at DESC""",
            (user_id,),
        )
        return await cur.fetchall()


async def get_trip(user_id: int, trip_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT tr.id, tr.truck_id, t.name, tr.status, tr.created_at, tr.finished_at
               FROM trips tr JOIN trucks t ON t.id = tr.truck_id
               WHERE tr.id=? AND tr.user_id=?""",
            (trip_id, user_id),
        )
        return await cur.fetchone()


async def add_trip_expense(trip_id: int, category: str, amount: float, currency: str = "UZS", rate: float = 1, note: str = ""):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO trip_expenses (trip_id, category, amount, currency, rate, note) VALUES (?, ?, ?, ?, ?, ?)",
            (trip_id, category, amount, currency, rate, note),
        )
        await conn.commit()
        return cur.lastrowid


async def list_trip_expenses(trip_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT id, category, amount, currency, rate, note, created_at FROM trip_expenses WHERE trip_id=? ORDER BY created_at DESC",
            (trip_id,),
        )
        return await cur.fetchall()


async def add_trip_leg(trip_id: int, from_point: str, to_point: str, price: float, currency: str = "UZS", rate: float = 1):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO trip_legs (trip_id, from_point, to_point, price, currency, rate) VALUES (?, ?, ?, ?, ?, ?)",
            (trip_id, from_point, to_point, price, currency, rate),
        )
        await conn.commit()
        return cur.lastrowid


async def list_trip_legs(trip_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT id, from_point, to_point, price, currency, rate, created_at FROM trip_legs WHERE trip_id=? ORDER BY created_at DESC",
            (trip_id,),
        )
        return await cur.fetchall()


# ---------------- Report ----------------
async def get_report(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN currency='USD' THEN price*rate ELSE price END),0) FROM trip_legs
               WHERE trip_id IN (SELECT id FROM trips WHERE user_id=?)""",
            (user_id,),
        )
        total_income = (await cur.fetchone())[0]

        cur = await conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END),0) FROM trip_expenses
               WHERE trip_id IN (SELECT id FROM trips WHERE user_id=?)""",
            (user_id,),
        )
        total_trip_expense = (await cur.fetchone())[0]

        cur = await conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END),0) FROM truck_expenses
               WHERE truck_id IN (SELECT id FROM trucks WHERE user_id=?)""",
            (user_id,),
        )
        total_repair = (await cur.fetchone())[0]

        cur = await conn.execute(
            """SELECT strftime('%Y-%m', tr.created_at) as month,
                      COALESCE(SUM(CASE WHEN l.currency='USD' THEN l.price*l.rate ELSE l.price END),0) as income,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END) FROM trip_expenses WHERE trip_id=tr.id),0) as expense
               FROM trips tr
               LEFT JOIN trip_legs l ON l.trip_id = tr.id
               WHERE tr.user_id=?
               GROUP BY month ORDER BY month DESC""",
            (user_id,),
        )
        by_month = await cur.fetchall()

        cur = await conn.execute(
            """SELECT t.id, t.name,
                      COALESCE((SELECT SUM(CASE WHEN l.currency='USD' THEN l.price*l.rate ELSE l.price END) FROM trip_legs l JOIN trips tr ON tr.id=l.trip_id WHERE tr.truck_id=t.id),0) as income,
                      COALESCE((SELECT SUM(CASE WHEN e.currency='USD' THEN e.amount*e.rate ELSE e.amount END) FROM trip_expenses e JOIN trips tr ON tr.id=e.trip_id WHERE tr.truck_id=t.id),0) as trip_expense,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END) FROM truck_expenses WHERE truck_id=t.id),0) as repair
               FROM trucks t WHERE t.user_id=?""",
            (user_id,),
        )
        by_truck = await cur.fetchall()

        return {
            "total_income": total_income,
            "total_expense": total_trip_expense + total_repair,
            "total_trip_expense": total_trip_expense,
            "total_repair": total_repair,
            "net_profit": total_income - total_trip_expense - total_repair,
            "by_month": [
                {"month": m, "income": i, "expense": e, "profit": i - e}
                for m, i, e in by_month
            ],
            "by_truck": [
                {
                    "id": tid,
                    "name": name,
                    "income": income,
                    "expense": trip_exp + repair,
                    "profit": income - trip_exp - repair,
                }
                for tid, name, income, trip_exp, repair in by_truck
            ],
        }


async def get_truck_name(truck_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT name FROM trucks WHERE id=?", (truck_id,))
        row = await cur.fetchone()
        return row[0] if row else "Noma'lum fura"


# ---------------- Admin statistika ----------------
async def get_admin_stats():
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM users")
        total_users = (await cur.fetchone())[0]

        cur = await conn.execute("SELECT COUNT(*) FROM trucks")
        total_trucks = (await cur.fetchone())[0]

        cur = await conn.execute("SELECT COUNT(*) FROM trips")
        total_trips = (await cur.fetchone())[0]

        cur = await conn.execute("SELECT COUNT(*) FROM trips WHERE status='active'")
        active_trips = (await cur.fetchone())[0]

        cur = await conn.execute(
            """SELECT COUNT(DISTINCT user_id) FROM (
                   SELECT user_id FROM trips WHERE created_at >= datetime('now','-7 days')
                   UNION
                   SELECT user_id FROM trucks WHERE created_at >= datetime('now','-7 days')
               )"""
        )
        active_users_7d = (await cur.fetchone())[0]

        cur = await conn.execute(
            """SELECT u.id, u.full_name, u.telegram_id,
                      (SELECT COUNT(*) FROM trucks WHERE user_id=u.id) as trucks,
                      (SELECT COUNT(*) FROM trips WHERE user_id=u.id) as trips
               FROM users u ORDER BY u.id DESC"""
        )
        users = await cur.fetchall()

        return {
            "total_users": total_users,
            "total_trucks": total_trucks,
            "total_trips": total_trips,
            "active_trips": active_trips,
            "active_users_7d": active_users_7d,
            "users": [
                {"id": u[0], "name": u[1] or "—", "telegram_id": u[2], "trucks": u[3], "trips": u[4]}
                for u in users
            ],
        }


async def admin_users_rows():
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT u.id, COALESCE(u.full_name,'—'), u.telegram_id,
                      (SELECT COUNT(*) FROM trucks WHERE user_id=u.id) as trucks,
                      (SELECT COUNT(*) FROM trips WHERE user_id=u.id) as trips,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN price*rate ELSE price END)
                                FROM trip_legs WHERE trip_id IN (SELECT id FROM trips WHERE user_id=u.id)),0) as income,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END)
                                FROM trip_expenses WHERE trip_id IN (SELECT id FROM trips WHERE user_id=u.id)),0) as trip_exp,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END)
                                FROM truck_expenses WHERE truck_id IN (SELECT id FROM trucks WHERE user_id=u.id)),0) as repair
               FROM users u ORDER BY u.id"""
        )
        return await cur.fetchall()


async def admin_trucks_rows():
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT t.id, t.name, COALESCE(u.full_name,'—'), u.telegram_id, t.created_at,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END)
                                FROM truck_expenses WHERE truck_id=t.id),0) as repair
               FROM trucks t JOIN users u ON u.id=t.user_id ORDER BY t.id"""
        )
        return await cur.fetchall()


async def admin_trips_rows():
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT tr.id, tk.name, COALESCE(u.full_name,'—'), tr.status, tr.created_at, tr.finished_at,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN price*rate ELSE price END) FROM trip_legs WHERE trip_id=tr.id),0) as income,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END) FROM trip_expenses WHERE trip_id=tr.id),0) as expense
               FROM trips tr
               JOIN trucks tk ON tk.id=tr.truck_id
               JOIN users u ON u.id=tr.user_id
               ORDER BY tr.id"""
        )
        return await cur.fetchall()
