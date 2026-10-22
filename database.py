import os

import aiosqlite

# Railway'da doimiy volume yo'liga yo'naltiriladi: DB_PATH=/data/fura.db
# Env berilmasa, lokal ishlash uchun "fura.db" ishlatiladi.
DB_PATH = os.getenv("DB_PATH", "fura.db")

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

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    sent_at TEXT DEFAULT (datetime('now')),
    UNIQUE(kind, ref_id)
);

-- Boshqa akkauntlar mashinani kuzatishi (faqat ko'rish). status: pending / approved / rejected
CREATE TABLE IF NOT EXISTS truck_watchers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    truck_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now')),
    decided_at TEXT,
    UNIQUE (truck_id, user_id),
    FOREIGN KEY (truck_id) REFERENCES trucks(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

# Eski bazalarga keyin qo'shilgan ustunlar: (jadval, ustun, turi)
COLUMN_MIGRATIONS = [
    ("users", "username", "TEXT"),
    ("trucks", "plate", "TEXT"),
    ("trucks", "brand", "TEXT"),
    ("trucks", "volume", "REAL"),
]

# So'mga aylantirilgan summa (har yozuv o'z muzlatilgan kursi bilan)
AMOUNT_UZS = "CASE WHEN currency='USD' THEN amount*rate ELSE amount END"
PRICE_UZS = "CASE WHEN currency='USD' THEN price*rate ELSE price END"

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
        for table, column, col_type in COLUMN_MIGRATIONS:
            cur = await conn.execute(f"PRAGMA table_info({table})")
            if column not in [row[1] for row in await cur.fetchall()]:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        # Bitta davlat raqami butun bazada faqat bir marta bo'lishi mumkin.
        # Eski (raqamsiz qo'shilgan) furalarda plate=NULL, ular bu cheklovga kirmaydi.
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trucks_plate ON trucks(plate) WHERE plate IS NOT NULL"
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


async def fix_legacy_usd_rates(current_rate: float):
    """Eski davrda (bot faqat USD da ishlaganda) yozuvlar rate=1 bilan saqlangan.
    Ularga joriy kursni beramiz — bir martalik tuzatish. Yangi yozuvlar
    doim o'z kiritilgan paytdagi kursi bilan saqlanadi."""
    if not current_rate or current_rate <= 1:
        return
    async with aiosqlite.connect(DB_PATH) as conn:
        for table, col in (("truck_expenses", "amount"), ("trip_expenses", "amount"), ("trip_legs", "price")):
            await conn.execute(
                f"UPDATE {table} SET rate=? WHERE currency='USD' AND rate<=1",
                (current_rate,),
            )
        await conn.commit()


async def get_or_create_user(telegram_id: int, full_name: str = "", username: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT id, full_name, username FROM users WHERE telegram_id=?", (telegram_id,)
        )
        row = await cur.fetchone()
        if row:
            # Ism yoki username o'zgargan bo'lsa yangilab qo'yamiz (kuzatish so'rovlarida ko'rinadi)
            if (row[1] or "", row[2] or "") != (full_name, username):
                await conn.execute(
                    "UPDATE users SET full_name=?, username=? WHERE id=?",
                    (full_name, username, row[0]),
                )
                await conn.commit()
            return row[0]
        cur = await conn.execute(
            "INSERT INTO users (telegram_id, full_name, username) VALUES (?, ?, ?)",
            (telegram_id, full_name, username),
        )
        await conn.commit()
        return cur.lastrowid


async def get_telegram_id(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT telegram_id FROM users WHERE id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else None


async def get_user_name(user_id: int):
    """(full_name, username)"""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT full_name, username FROM users WHERE id=?", (user_id,))
        return await cur.fetchone() or ("", "")


async def get_user_by_telegram_id(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT id FROM users WHERE telegram_id=?", (telegram_id,))
        row = await cur.fetchone()
        return row[0] if row else None


# ---------------- Trucks ----------------
async def add_truck(user_id: int, name: str, plate: str, brand: str, volume: float):
    """Yangi fura qo'shadi. Davlat raqami allaqachon band bo'lsa None qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as conn:
        try:
            cur = await conn.execute(
                "INSERT INTO trucks (user_id, name, plate, brand, volume) VALUES (?, ?, ?, ?, ?)",
                (user_id, name, plate, brand, volume),
            )
        except aiosqlite.IntegrityError:
            return None
        await conn.commit()
        return cur.lastrowid


async def count_trucks(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM trucks WHERE user_id=?", (user_id,))
        return (await cur.fetchone())[0]


async def find_truck_by_plate(plate: str):
    """(truck_id, egasining user_id, nomi) yoki None."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT id, user_id, name FROM trucks WHERE plate=?", (plate,))
        return await cur.fetchone()


async def list_trucks(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT t.id, t.name, t.created_at,
                      COALESCE((SELECT SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END)
                                FROM truck_expenses WHERE truck_id=t.id), 0) as repair_total,
                      t.plate, t.brand, t.volume
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
        # Furaning egasini tekshiramiz
        cur = await conn.execute("SELECT id FROM trucks WHERE id=? AND user_id=?", (truck_id, user_id))
        if not await cur.fetchone():
            return
        # Furaning barcha reyslari ID sini olamiz
        cur = await conn.execute("SELECT id FROM trips WHERE truck_id=? AND user_id=?", (truck_id, user_id))
        trip_ids = [row[0] for row in await cur.fetchall()]
        # Har bir reysning safar va xarajatlarini o'chiramiz
        for tid in trip_ids:
            await conn.execute("DELETE FROM trip_expenses WHERE trip_id=?", (tid,))
            await conn.execute("DELETE FROM trip_legs WHERE trip_id=?", (tid,))
        # Reyslarni o'chiramiz
        await conn.execute("DELETE FROM trips WHERE truck_id=? AND user_id=?", (truck_id, user_id))
        # Furaning ta'mirlash xarajatlarini va kuzatuvchilarini o'chiramiz
        await conn.execute("DELETE FROM truck_expenses WHERE truck_id=?", (truck_id,))
        await conn.execute("DELETE FROM truck_watchers WHERE truck_id=?", (truck_id,))
        # Furani o'chiramiz
        await conn.execute("DELETE FROM trucks WHERE id=? AND user_id=?", (truck_id, user_id))
        await conn.commit()


async def cleanup_orphans(user_id: int):
    """Egasiz (mashinasi o'chirilgan) reys/safar/xarajatlarni tozalaydi."""
    async with aiosqlite.connect(DB_PATH) as conn:
        # Bu foydalanuvchining mavjud fura ID lari
        cur = await conn.execute("SELECT id FROM trucks WHERE user_id=?", (user_id,))
        valid_truck_ids = [row[0] for row in await cur.fetchall()]

        # Bu foydalanuvchining reyslari orasidan, furasi mavjud bo'lmaganini topamiz
        cur = await conn.execute("SELECT id, truck_id FROM trips WHERE user_id=?", (user_id,))
        all_trips = await cur.fetchall()
        orphan_trip_ids = [tid for tid, truck_id in all_trips if truck_id not in valid_truck_ids]

        for tid in orphan_trip_ids:
            await conn.execute("DELETE FROM trip_expenses WHERE trip_id=?", (tid,))
            await conn.execute("DELETE FROM trip_legs WHERE trip_id=?", (tid,))
            await conn.execute("DELETE FROM trips WHERE id=?", (tid,))

        # Chinakam egasiz ta'mirlash xarajatlari: furasi umuman mavjud bo'lmagan
        # (o'chirilgan) yozuvlargina o'chiriladi. MUHIM: boshqa foydalanuvchilarning
        # furalariga tegilmaydi - faqat trucks jadvalida umuman yo'q truck_id lar.
        await conn.execute(
            "DELETE FROM truck_expenses WHERE truck_id NOT IN (SELECT id FROM trucks)"
        )
        await conn.execute(
            "DELETE FROM truck_watchers WHERE truck_id NOT IN (SELECT id FROM trucks)"
        )
        await conn.commit()
        return len(orphan_trip_ids)


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
               WHERE trip_id IN (SELECT id FROM trips WHERE user_id=? AND truck_id IN (SELECT id FROM trucks WHERE user_id=?))""",
            (user_id, user_id),
        )
        total_income = (await cur.fetchone())[0]

        cur = await conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END),0) FROM trip_expenses
               WHERE trip_id IN (SELECT id FROM trips WHERE user_id=? AND truck_id IN (SELECT id FROM trucks WHERE user_id=?))""",
            (user_id, user_id),
        )
        total_trip_expense = (await cur.fetchone())[0]

        cur = await conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END),0) FROM truck_expenses
               WHERE truck_id IN (SELECT id FROM trucks WHERE user_id=?)""",
            (user_id,),
        )
        total_repair = (await cur.fetchone())[0]

        # Oylar kesimida: avval har bir reys alohida hisoblanadi, keyin oy bo'yicha jamlanadi
        # (aks holda bir oyda bir nechta reys bo'lsa, xarajat faqat bittasidan olinardi)
        cur = await conn.execute(
            f"""SELECT strftime('%Y-%m', tr.created_at) as month,
                      SUM(COALESCE((SELECT SUM({PRICE_UZS}) FROM trip_legs WHERE trip_id=tr.id),0)) as income,
                      SUM(COALESCE((SELECT SUM({AMOUNT_UZS}) FROM trip_expenses WHERE trip_id=tr.id),0)) as expense
               FROM trips tr
               WHERE tr.user_id=? AND tr.truck_id IN (SELECT id FROM trucks WHERE user_id=?)
               GROUP BY month ORDER BY month DESC""",
            (user_id, user_id),
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


async def get_all_telegram_ids():
    """Broadcast uchun: barcha foydalanuvchilarning telegram ID lari."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT telegram_id FROM users")
        rows = await cur.fetchall()
        return [r[0] for r in rows]


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


# ---------------- RETENTION (qaytish eslatmalari) ----------------

async def already_reminded(kind: str, ref_id: int) -> bool:
    """Bu eslatma avval yuborilganmi (spam himoyasi - har eslatma bir marta)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM reminders WHERE kind=? AND ref_id=?", (kind, ref_id)
        )
        return await cur.fetchone() is not None


async def mark_reminded(kind: str, ref_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO reminders (kind, ref_id) VALUES (?, ?)", (kind, ref_id)
        )
        await conn.commit()


async def get_stale_open_trips(days_open: int = 3, days_quiet: int = 3):
    """3+ kundan beri ochiq va oxirgi 3 kunda hech narsa kiritilmagan reyslar.
    Qaytadi: (trip_id, telegram_id, truck_nomi, ochiq_kunlar)"""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            f"""
            SELECT tr.id, u.telegram_id, tk.name,
                   CAST(julianday('now') - julianday(tr.created_at) AS INTEGER)
            FROM trips tr
            JOIN users u ON u.id = tr.user_id
            JOIN trucks tk ON tk.id = tr.truck_id
            WHERE tr.status = 'active'
              AND julianday('now') - julianday(tr.created_at) >= {int(days_open)}
              AND NOT EXISTS (
                  SELECT 1 FROM trip_expenses e
                  WHERE e.trip_id = tr.id
                    AND julianday('now') - julianday(e.created_at) < {int(days_quiet)}
              )
              AND NOT EXISTS (
                  SELECT 1 FROM trip_legs l
                  WHERE l.trip_id = tr.id
                    AND julianday('now') - julianday(l.created_at) < {int(days_quiet)}
              )
              AND NOT EXISTS (
                  SELECT 1 FROM reminders r WHERE r.kind='stale_trip' AND r.ref_id=tr.id
              )
            """
        )
        return await cur.fetchall()


async def get_users_with_truck_no_trip(days: int = 2):
    """Mashina qo'shgan (2+ kun oldin), lekin hech qachon reys ochmagan foydalanuvchilar.
    Qaytadi: (user_id, telegram_id)"""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            f"""
            SELECT DISTINCT u.id, u.telegram_id
            FROM users u
            JOIN trucks tk ON tk.user_id = u.id
            WHERE julianday('now') - julianday(tk.created_at) >= {int(days)}
              AND NOT EXISTS (SELECT 1 FROM trips tr WHERE tr.user_id = u.id)
              AND NOT EXISTS (
                  SELECT 1 FROM reminders r WHERE r.kind='no_trip' AND r.ref_id=u.id
              )
            """
        )
        return await cur.fetchall()


async def get_trip_result_for_message(user_id: int, trip_id: int):
    """Reys yakuni xabari uchun: (foyda_so'mda, telegram_id, truck_nomi)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN currency='USD' THEN price*rate ELSE price END),0)
               FROM trip_legs WHERE trip_id=?""",
            (trip_id,),
        )
        income = (await cur.fetchone())[0]
        cur = await conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN currency='USD' THEN amount*rate ELSE amount END),0)
               FROM trip_expenses WHERE trip_id=?""",
            (trip_id,),
        )
        expense = (await cur.fetchone())[0]
        cur = await conn.execute(
            """SELECT u.telegram_id, tk.name FROM trips tr
               JOIN users u ON u.id=tr.user_id
               JOIN trucks tk ON tk.id=tr.truck_id
               WHERE tr.id=? AND tr.user_id=?""",
            (trip_id, user_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return (income - expense, row[0], row[1])


async def count_reminders():
    """Diagnostika: qancha eslatma belgilangan (turi bo'yicha)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT kind, COUNT(*) FROM reminders GROUP BY kind"
        )
        return dict(await cur.fetchall())


async def clear_reminders():
    """Barcha eslatma belgilarini tozalaydi - hammasi qayta yuborilishi mumkin bo'ladi."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM reminders")
        await conn.commit()


# ---------------- Kuzatish (boshqa akkauntdan faqat ko'rish) ----------------
async def get_watch(truck_id: int, user_id: int):
    """(id, status) yoki None."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT id, status FROM truck_watchers WHERE truck_id=? AND user_id=?",
            (truck_id, user_id),
        )
        return await cur.fetchone()


async def create_watch_request(truck_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO truck_watchers (truck_id, user_id, status) VALUES (?, ?, 'pending')",
            (truck_id, user_id),
        )
        await conn.commit()
        return cur.lastrowid


async def get_watch_request_for_owner(owner_id: int, watch_id: int):
    """So'rov shu egaga tegishli bo'lsa: (id, status, kuzatuvchining telegram_id, fura nomi)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT w.id, w.status, u.telegram_id, t.name
               FROM truck_watchers w
               JOIN trucks t ON t.id = w.truck_id
               JOIN users u ON u.id = w.user_id
               WHERE w.id=? AND t.user_id=?""",
            (watch_id, owner_id),
        )
        return await cur.fetchone()


async def decide_watch_request(owner_id: int, watch_id: int, approve: bool) -> bool:
    """Faqat kutilayotgan so'rovni hal qiladi. Muvaffaqiyatli bo'lsa True."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """UPDATE truck_watchers SET status=?, decided_at=datetime('now')
               WHERE id=? AND status='pending'
                 AND truck_id IN (SELECT id FROM trucks WHERE user_id=?)""",
            ("approved" if approve else "rejected", watch_id, owner_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def delete_watch(user_id: int, watch_id: int) -> bool:
    """Kuzatuvchi o'zi chiqib ketishi yoki mashina egasi uni olib tashlashi mumkin."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """DELETE FROM truck_watchers WHERE id=?
               AND (user_id=? OR truck_id IN (SELECT id FROM trucks WHERE user_id=?))""",
            (watch_id, user_id, user_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def list_incoming_requests(owner_id: int):
    """Egasiga kelgan, hali hal qilinmagan so'rovlar."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT w.id, t.id, t.name, u.full_name, u.username, w.created_at
               FROM truck_watchers w
               JOIN trucks t ON t.id = w.truck_id
               JOIN users u ON u.id = w.user_id
               WHERE t.user_id=? AND w.status='pending'
               ORDER BY w.created_at DESC""",
            (owner_id,),
        )
        return await cur.fetchall()


async def list_truck_watchers(owner_id: int, truck_id: int):
    """Egasi uchun: shu mashinani kuzatayotganlar (rad etilganlardan tashqari)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT w.id, u.full_name, u.username, w.status, w.created_at
               FROM truck_watchers w
               JOIN trucks t ON t.id = w.truck_id
               JOIN users u ON u.id = w.user_id
               WHERE t.id=? AND t.user_id=? AND w.status IN ('pending', 'approved')
               ORDER BY w.created_at DESC""",
            (truck_id, owner_id),
        )
        return await cur.fetchall()


async def list_watching(user_id: int):
    """Foydalanuvchi kuzatayotgan (yoki so'rov yuborgan) mashinalar."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT w.id, w.status, t.id, t.name, t.brand, t.volume
               FROM truck_watchers w
               JOIN trucks t ON t.id = w.truck_id
               WHERE w.user_id=?
               ORDER BY w.created_at DESC""",
            (user_id,),
        )
        return await cur.fetchall()


async def get_watched_truck(user_id: int, truck_id: int):
    """Faqat ruxsat olgan kuzatuvchiga: (id, name, plate, brand, volume, created_at, watch_id)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT t.id, t.name, t.plate, t.brand, t.volume, t.created_at, w.id
               FROM trucks t
               JOIN truck_watchers w ON w.truck_id = t.id
               WHERE t.id=? AND w.user_id=? AND w.status='approved'""",
            (truck_id, user_id),
        )
        return await cur.fetchone()


async def list_truck_trips(truck_id: int):
    """Bitta furaning barcha reyslari (list_trips bilan bir xil ko'rinishda)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            f"""SELECT tr.id, tr.truck_id, t.name, tr.status, tr.created_at, tr.finished_at,
                      COALESCE((SELECT SUM({PRICE_UZS}) FROM trip_legs WHERE trip_id=tr.id), 0) as income,
                      COALESCE((SELECT SUM({AMOUNT_UZS}) FROM trip_expenses WHERE trip_id=tr.id), 0) as expense
               FROM trips tr JOIN trucks t ON t.id = tr.truck_id
               WHERE tr.truck_id=?
               ORDER BY tr.created_at DESC""",
            (truck_id,),
        )
        return await cur.fetchall()


async def get_truck_trip(truck_id: int, trip_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT tr.id, tr.truck_id, t.name, tr.status, tr.created_at, tr.finished_at
               FROM trips tr JOIN trucks t ON t.id = tr.truck_id
               WHERE tr.id=? AND tr.truck_id=?""",
            (trip_id, truck_id),
        )
        return await cur.fetchone()
