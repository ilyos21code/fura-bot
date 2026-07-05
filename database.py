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
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (trip_id) REFERENCES trips(id)
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()


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
        cur = await conn.execute(
            "INSERT INTO trucks (user_id, name) VALUES (?, ?)", (user_id, name)
        )
        await conn.commit()
        return cur.lastrowid


async def list_trucks(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT t.id, t.name, t.created_at,
                      COALESCE((SELECT SUM(amount) FROM truck_expenses WHERE truck_id=t.id), 0) as repair_total
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


async def add_truck_expense(truck_id: int, category: str, amount: float, note: str = ""):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO truck_expenses (truck_id, category, amount, note) VALUES (?, ?, ?, ?)",
            (truck_id, category, amount, note),
        )
        await conn.commit()
        return cur.lastrowid


async def list_truck_expenses(truck_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT id, category, amount, note, created_at FROM truck_expenses WHERE truck_id=? ORDER BY created_at DESC",
            (truck_id,),
        )
        return await cur.fetchall()


# ---------------- Trips ----------------
async def create_trip(user_id: int, truck_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO trips (user_id, truck_id, status) VALUES (?, ?, 'active')",
            (user_id, truck_id),
        )
        await conn.commit()
        return cur.lastrowid


async def get_active_trip(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT tr.id, tr.truck_id, t.name FROM trips tr
               JOIN trucks t ON t.id = tr.truck_id
               WHERE tr.user_id=? AND tr.status='active'
               ORDER BY tr.created_at DESC LIMIT 1""",
            (user_id,),
        )
        return await cur.fetchone()


async def finish_trip(user_id: int, trip_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE trips SET status='finished', finished_at=datetime('now') WHERE id=? AND user_id=?",
            (trip_id, user_id),
        )
        await conn.commit()


async def list_trips(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT tr.id, tr.truck_id, t.name, tr.status, tr.created_at, tr.finished_at,
                      COALESCE((SELECT SUM(price) FROM trip_legs WHERE trip_id=tr.id), 0) as income,
                      COALESCE((SELECT SUM(amount) FROM trip_expenses WHERE trip_id=tr.id), 0) as expense
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


async def add_trip_expense(trip_id: int, category: str, amount: float, note: str = ""):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO trip_expenses (trip_id, category, amount, note) VALUES (?, ?, ?, ?)",
            (trip_id, category, amount, note),
        )
        await conn.commit()
        return cur.lastrowid


async def list_trip_expenses(trip_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT id, category, amount, note, created_at FROM trip_expenses WHERE trip_id=? ORDER BY created_at DESC",
            (trip_id,),
        )
        return await cur.fetchall()


async def add_trip_leg(trip_id: int, from_point: str, to_point: str, price: float):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO trip_legs (trip_id, from_point, to_point, price) VALUES (?, ?, ?, ?)",
            (trip_id, from_point, to_point, price),
        )
        await conn.commit()
        return cur.lastrowid


async def list_trip_legs(trip_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT id, from_point, to_point, price, created_at FROM trip_legs WHERE trip_id=? ORDER BY created_at DESC",
            (trip_id,),
        )
        return await cur.fetchall()


# ---------------- Report ----------------
async def get_report(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        # Overall totals
        cur = await conn.execute(
            """SELECT COALESCE(SUM(price),0) FROM trip_legs
               WHERE trip_id IN (SELECT id FROM trips WHERE user_id=?)""",
            (user_id,),
        )
        total_income = (await cur.fetchone())[0]

        cur = await conn.execute(
            """SELECT COALESCE(SUM(amount),0) FROM trip_expenses
               WHERE trip_id IN (SELECT id FROM trips WHERE user_id=?)""",
            (user_id,),
        )
        total_trip_expense = (await cur.fetchone())[0]

        cur = await conn.execute(
            """SELECT COALESCE(SUM(amount),0) FROM truck_expenses
               WHERE truck_id IN (SELECT id FROM trucks WHERE user_id=?)""",
            (user_id,),
        )
        total_repair = (await cur.fetchone())[0]

        # By month (based on trip creation date)
        cur = await conn.execute(
            """SELECT strftime('%Y-%m', tr.created_at) as month,
                      COALESCE(SUM(l.price),0) as income,
                      COALESCE((SELECT SUM(amount) FROM trip_expenses WHERE trip_id=tr.id),0) as expense
               FROM trips tr
               LEFT JOIN trip_legs l ON l.trip_id = tr.id
               WHERE tr.user_id=?
               GROUP BY month ORDER BY month DESC""",
            (user_id,),
        )
        by_month = await cur.fetchall()

        # By truck
        cur = await conn.execute(
            """SELECT t.id, t.name,
                      COALESCE((SELECT SUM(l.price) FROM trip_legs l JOIN trips tr ON tr.id=l.trip_id WHERE tr.truck_id=t.id),0) as income,
                      COALESCE((SELECT SUM(e.amount) FROM trip_expenses e JOIN trips tr ON tr.id=e.trip_id WHERE tr.truck_id=t.id),0) as trip_expense,
                      COALESCE((SELECT SUM(amount) FROM truck_expenses WHERE truck_id=t.id),0) as repair
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
