import asyncio
import logging
import os
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

import database as db
import rate as rate_module
from auth import validate_init_data, InitDataError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"

app = FastAPI(title="Fura Mini App")

# Joriy USD kursi (xotirada), doimiy yangilanib turadi
_current_usd_rate = rate_module.DEFAULT_RATE


async def get_usd_rate() -> float:
    """Joriy USD kursini qaytaradi (bazadagi so'nggi saqlangan qiymatdan)."""
    global _current_usd_rate
    saved = await db.get_setting("usd_rate")
    if saved:
        try:
            _current_usd_rate = float(saved)
        except ValueError:
            pass
    return _current_usd_rate


def to_uzs(amount: float, currency: str, usd_rate: float) -> float:
    """Kiritilgan summani so'mga aylantiradi (bazada hamma narsa so'mda saqlanadi)."""
    if currency == "USD":
        return amount * usd_rate
    return amount


async def update_rate_loop():
    """Har 6 soatda cbu.uz dan kursni yangilab turadi."""
    global _current_usd_rate
    while True:
        fetched = rate_module.fetch_usd_rate()
        if fetched:
            _current_usd_rate = fetched
            await db.set_setting("usd_rate", str(fetched))
            logger.info("USD kursi yangilandi: %s", fetched)
        await asyncio.sleep(6 * 3600)


async def _run_retention_once(r_bot, open_kb):
    """Bitta retention tsikli. Qaytaradi: (nomzodA, yubA, nomzodB, yubB, xatolar).
    MUHIM mantiq: faqat yuborilganda yoki foydalanuvchi botni bloklaganda
    belgilanadi. Vaqtinchalik xatoda belgilanmaydi - keyingi tsiklda qayta uriniladi."""
    from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

    sent_a = sent_b = errors = 0

    stale = await db.get_stale_open_trips()
    for trip_id, tg_id, truck_name, days in stale:
        try:
            await r_bot.send_message(
                tg_id,
                f"🚛 {truck_name} bo'yicha reysingiz {days} kundan beri ochiq.\n\n"
                f"Xarajatlaringizni kiritib qo'ydingizmi? Yo'lda yozib borsangiz, "
                f"reys oxirida aniq sof foyda chiqadi 👇",
                reply_markup=open_kb(),
            )
            await db.mark_reminded("stale_trip", trip_id)
            sent_a += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            # bloklagan yoki o'chirilgan akkaunt - qayta urinish foydasiz, belgilaymiz
            await db.mark_reminded("stale_trip", trip_id)
        except Exception as e:
            errors += 1
            logger.warning("Retention A yuborishda xato (tg=%s): %s", tg_id, e)
        await asyncio.sleep(0.05)

    idle = await db.get_users_with_truck_no_trip()
    for user_id, tg_id in idle:
        try:
            await r_bot.send_message(
                tg_id,
                "Mashinangiz qo'shilgan, lekin birinchi reys hali boshlanmagan 🙂\n\n"
                "Reys boshlash 10 soniya oladi — keyingi safaringizda sinab ko'ring: "
                "daromad va xarajatni kiritsangiz, foydangiz o'zi hisoblanadi 👇",
                reply_markup=open_kb(),
            )
            await db.mark_reminded("no_trip", user_id)
            sent_b += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            await db.mark_reminded("no_trip", user_id)
        except Exception as e:
            errors += 1
            logger.warning("Retention B yuborishda xato (tg=%s): %s", tg_id, e)
        await asyncio.sleep(0.05)

    logger.info(
        "Retention tsikli: A nomzod=%d yuborildi=%d | B nomzod=%d yuborildi=%d | xato=%d",
        len(stale), sent_a, len(idle), sent_b, errors,
    )
    return len(stale), sent_a, len(idle), sent_b, errors


def _make_open_kb():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
    webapp = os.getenv("WEBAPP_URL", "")

    def open_kb():
        if not webapp:
            return None
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚛 Ochish", web_app=WebAppInfo(url=webapp))]
        ])
    return open_kb


async def retention_loop():
    """Qaytish eslatmalari: har 6 soatda tekshirib, kerakli foydalanuvchilarga
    shaxsiy turtki yuboradi. Har eslatma bir marta yuboriladi (spam yo'q).

    A) 3+ kun ochiq, 3 kun jim reys -> "xarajatlaringizni kiritib qo'ydingizmi?"
    B) Mashina qo'shgan, 2+ kun reys ochmagan -> "reys boshlash 10 soniya"
    """
    from aiogram import Bot

    r_bot = Bot(token=BOT_TOKEN)
    open_kb = _make_open_kb()
    await asyncio.sleep(120)  # server to'liq ishga tushsin

    while True:
        try:
            await _run_retention_once(r_bot, open_kb)
        except Exception as e:
            logger.exception("Retention siklida kutilmagan xato: %s", e)
        await asyncio.sleep(6 * 3600)


async def daily_backup_loop():
    """Har 24 soatda baza faylini adminga Telegram orqali yuboradi -
    qo'shimcha himoya qatlami: server nima bo'lsa ham, admin qo'lida
    har kungi zaxira nusxa bo'ladi."""
    from aiogram import Bot
    from aiogram.types import BufferedInputFile
    from datetime import datetime

    admin_id = os.getenv("ADMIN_TELEGRAM_ID", "")
    if not admin_id:
        logger.warning("ADMIN_TELEGRAM_ID yo'q - kunlik zaxira o'chirilgan")
        return
    backup_bot = Bot(token=BOT_TOKEN)
    await asyncio.sleep(60)  # server to'liq ishga tushishini kutamiz
    while True:
        try:
            if os.path.exists(db.DB_PATH):
                with open(db.DB_PATH, "rb") as f:
                    data = f.read()
                stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
                file = BufferedInputFile(data, filename=f"zaxira_fura_{stamp}.db")
                await backup_bot.send_document(
                    admin_id, file,
                    caption=f"🗄 Kunlik avtomatik zaxira ({stamp})\nBu faylni saqlab qo'ying.",
                )
                logger.info("Kunlik zaxira yuborildi")
        except Exception as e:
            logger.warning("Zaxira yuborishda xato: %s", e)
        await asyncio.sleep(24 * 3600)


async def get_user_id(x_telegram_init_data: str = Header(default="")) -> int:
    if DEV_MODE and not x_telegram_init_data:
        # Faqat lokal test uchun: haqiqiy Telegram ma'lumoti bo'lmasa ham ishlash imkonini beradi
        return await db.get_or_create_user(telegram_id=999999999, full_name="Dev User")
    try:
        user = validate_init_data(x_telegram_init_data, BOT_TOKEN)
    except InitDataError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return await db.get_or_create_user(
        telegram_id=user["id"], full_name=user.get("first_name", "")
    )


# ---------------- Pydantic modellar ----------------
class TruckIn(BaseModel):
    name: str


class TruckExpenseIn(BaseModel):
    category: str
    amount: float
    currency: str = "UZS"
    note: str = ""


class TripIn(BaseModel):
    truck_id: int


class TripExpenseIn(BaseModel):
    category: str
    amount: float
    currency: str = "UZS"
    note: str = ""


class TripLegIn(BaseModel):
    from_point: str
    to_point: str
    price: float
    currency: str = "UZS"


# ---------------- Kurs ----------------
@app.get("/api/rate")
async def api_rate():
    r = await get_usd_rate()
    return {"usd": r}


# ---------------- Trucks ----------------
@app.get("/api/trucks")
async def api_list_trucks(x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    rows = await db.list_trucks(user_id)
    return [
        {"id": r[0], "name": r[1], "created_at": r[2], "repair_total": r[3]}
        for r in rows
    ]


@app.post("/api/trucks")
async def api_add_truck(payload: TruckIn, x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    truck_id = await db.add_truck(user_id, payload.name.strip())
    return {"id": truck_id, "name": payload.name.strip()}


@app.delete("/api/trucks/{truck_id}")
async def api_delete_truck(truck_id: int, x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    await db.delete_truck(user_id, truck_id)
    return {"ok": True}


@app.get("/api/trucks/{truck_id}/expenses")
async def api_list_truck_expenses(truck_id: int, x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    truck = await db.get_truck(user_id, truck_id)
    if not truck:
        raise HTTPException(404, "Fura topilmadi")
    rows = await db.list_truck_expenses(truck_id)
    return [
        {"id": r[0], "category": r[1], "amount": r[2], "currency": r[3], "rate": r[4], "note": r[5], "created_at": r[6]}
        for r in rows
    ]


@app.post("/api/trucks/{truck_id}/expenses")
async def api_add_truck_expense(
    truck_id: int, payload: TruckExpenseIn, x_telegram_init_data: str = Header(default="")
):
    user_id = await get_user_id(x_telegram_init_data)
    truck = await db.get_truck(user_id, truck_id)
    if not truck:
        raise HTTPException(404, "Fura topilmadi")
    usd_rate = await get_usd_rate()
    expense_id = await db.add_truck_expense(truck_id, payload.category, payload.amount, payload.currency, usd_rate, payload.note)
    return {"id": expense_id}


@app.delete("/api/trucks/{truck_id}/expenses/{expense_id}")
async def api_delete_truck_expense(
    truck_id: int, expense_id: int, x_telegram_init_data: str = Header(default="")
):
    user_id = await get_user_id(x_telegram_init_data)
    truck = await db.get_truck(user_id, truck_id)
    if not truck:
        raise HTTPException(404, "Fura topilmadi")
    await db.delete_truck_expense(user_id, expense_id)
    return {"ok": True}


# ---------------- Trips ----------------
@app.get("/api/trips")
async def api_list_trips(x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    rows = await db.list_trips(user_id)
    return [
        {
            "id": r[0],
            "truck_id": r[1],
            "truck_name": r[2],
            "status": r[3],
            "created_at": r[4],
            "finished_at": r[5],
            "income": r[6],
            "expense": r[7],
            "profit": r[6] - r[7],
        }
        for r in rows
    ]


@app.get("/api/trips/active")
async def api_active_trips(x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    rows = await db.get_active_trips(user_id)
    return [{"id": r[0], "truck_id": r[1], "truck_name": r[2]} for r in rows]


@app.post("/api/trips")
async def api_create_trip(payload: TripIn, x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    truck = await db.get_truck(user_id, payload.truck_id)
    if not truck:
        raise HTTPException(404, "Fura topilmadi")
    existing = await db.get_active_trip_for_truck(user_id, payload.truck_id)
    if existing:
        raise HTTPException(400, "Bu fura uchun allaqachon faol reys bor")
    trip_id = await db.create_trip(user_id, payload.truck_id)
    return {"id": trip_id}


@app.post("/api/trips/{trip_id}/finish")
async def api_finish_trip(trip_id: int, x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    trip = await db.get_trip(user_id, trip_id)
    if not trip:
        raise HTTPException(404, "Reys topilmadi")
    await db.finish_trip(user_id, trip_id)
    # Reys yakuni "g'alaba" xabari - odat mustahkamlanadi, bot chat tepasiga chiqadi
    try:
        result = await db.get_trip_result_for_message(user_id, trip_id)
        if result:
            profit, tg_id, truck_name = result
            sign = "+" if profit >= 0 else ""
            profit_txt = f"{sign}{profit:,.0f}".replace(",", " ")
            from aiogram import Bot as _Bot
            _b = _Bot(token=BOT_TOKEN)
            await _b.send_message(
                tg_id,
                f"✅ Reys yakunlandi! ({truck_name})\n\n"
                f"💰 Sof natija: {profit_txt} so'm\n\n"
                f"Keyingi reysda ham omad! 🚛",
            )
            await _b.session.close()
    except Exception:
        pass  # xabar ketmasa ham reys yopilishi buzilmasin
    return {"ok": True}


@app.delete("/api/trips/{trip_id}")
async def api_delete_trip(trip_id: int, x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    trip = await db.get_trip(user_id, trip_id)
    if not trip:
        raise HTTPException(404, "Reys topilmadi")
    await db.delete_trip(user_id, trip_id)
    return {"ok": True}


@app.get("/api/trips/{trip_id}")
async def api_get_trip(trip_id: int, x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    trip = await db.get_trip(user_id, trip_id)
    if not trip:
        raise HTTPException(404, "Reys topilmadi")
    expenses = await db.list_trip_expenses(trip_id)
    legs = await db.list_trip_legs(trip_id)
    # list_trip_legs: id, from, to, price, currency, rate, created_at
    income = sum((l[3] * l[5]) if l[4] == "USD" else l[3] for l in legs)
    # list_trip_expenses: id, category, amount, currency, rate, note, created_at
    expense = sum((e[2] * e[4]) if e[3] == "USD" else e[2] for e in expenses)
    return {
        "id": trip[0],
        "truck_id": trip[1],
        "truck_name": trip[2],
        "status": trip[3],
        "created_at": trip[4],
        "finished_at": trip[5],
        "income": income,
        "expense": expense,
        "profit": income - expense,
        "expenses": [
            {"id": e[0], "category": e[1], "amount": e[2], "currency": e[3], "rate": e[4], "note": e[5], "created_at": e[6]}
            for e in expenses
        ],
        "legs": [
            {"id": l[0], "from_point": l[1], "to_point": l[2], "price": l[3], "currency": l[4], "rate": l[5], "created_at": l[6]}
            for l in legs
        ],
    }


@app.post("/api/trips/{trip_id}/expenses")
async def api_add_trip_expense(
    trip_id: int, payload: TripExpenseIn, x_telegram_init_data: str = Header(default="")
):
    user_id = await get_user_id(x_telegram_init_data)
    trip = await db.get_trip(user_id, trip_id)
    if not trip:
        raise HTTPException(404, "Reys topilmadi")
    usd_rate = await get_usd_rate()
    expense_id = await db.add_trip_expense(trip_id, payload.category, payload.amount, payload.currency, usd_rate, payload.note)
    return {"id": expense_id}


@app.delete("/api/trips/{trip_id}/expenses/{expense_id}")
async def api_delete_trip_expense(
    trip_id: int, expense_id: int, x_telegram_init_data: str = Header(default="")
):
    user_id = await get_user_id(x_telegram_init_data)
    trip = await db.get_trip(user_id, trip_id)
    if not trip:
        raise HTTPException(404, "Reys topilmadi")
    await db.delete_trip_expense(user_id, expense_id)
    return {"ok": True}


@app.post("/api/trips/{trip_id}/legs")
async def api_add_trip_leg(
    trip_id: int, payload: TripLegIn, x_telegram_init_data: str = Header(default="")
):
    user_id = await get_user_id(x_telegram_init_data)
    trip = await db.get_trip(user_id, trip_id)
    if not trip:
        raise HTTPException(404, "Reys topilmadi")
    usd_rate = await get_usd_rate()
    leg_id = await db.add_trip_leg(
        trip_id, payload.from_point.strip(), payload.to_point.strip(), payload.price, payload.currency, usd_rate
    )
    return {"id": leg_id}


@app.delete("/api/trips/{trip_id}/legs/{leg_id}")
async def api_delete_trip_leg(
    trip_id: int, leg_id: int, x_telegram_init_data: str = Header(default="")
):
    user_id = await get_user_id(x_telegram_init_data)
    trip = await db.get_trip(user_id, trip_id)
    if not trip:
        raise HTTPException(404, "Reys topilmadi")
    await db.delete_trip_leg(user_id, leg_id)
    return {"ok": True}


# ---------------- Report ----------------
@app.get("/api/report")
async def api_report(x_telegram_init_data: str = Header(default="")):
    user_id = await get_user_id(x_telegram_init_data)
    # Hisobot so'ralganda avval egasiz (mashinasi o'chirilgan) ma'lumotlarni tozalaymiz
    await db.cleanup_orphans(user_id)
    return await db.get_report(user_id)


# ---------------- Admin ----------------
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "")


async def get_telegram_id(x_telegram_init_data: str) -> int:
    if DEV_MODE and not x_telegram_init_data:
        return 999999999
    try:
        user = validate_init_data(x_telegram_init_data, BOT_TOKEN)
    except InitDataError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return user["id"]


@app.get("/api/admin/stats")
async def api_admin_stats(x_telegram_init_data: str = Header(default="")):
    telegram_id = await get_telegram_id(x_telegram_init_data)
    # Faqat admin ko'ra oladi
    if not ADMIN_TELEGRAM_ID or str(telegram_id) != str(ADMIN_TELEGRAM_ID):
        raise HTTPException(status_code=403, detail="Ruxsat yo'q")
    return await db.get_admin_stats()


@app.get("/api/admin/check")
async def api_admin_check(x_telegram_init_data: str = Header(default="")):
    """Foydalanuvchi admin ekanligini tekshiradi (Ko'proq bo'limida admin tugmasini ko'rsatish uchun)."""
    telegram_id = await get_telegram_id(x_telegram_init_data)
    is_admin = bool(ADMIN_TELEGRAM_ID) and str(telegram_id) == str(ADMIN_TELEGRAM_ID)
    return {"is_admin": is_admin}


# ---------------- Frontend ----------------
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
async def serve_index():
    return FileResponse(
        os.path.join(STATIC_DIR, "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


async def run_bot():
    import time
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import CommandStart, Command
    from aiogram.types import (
        Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
        WebAppInfo, BufferedInputFile,
    )
    import excel_export

    webapp_url = os.getenv("WEBAPP_URL", "")
    admin_id = os.getenv("ADMIN_TELEGRAM_ID", "")
    version_tag = str(int(time.time()))
    if webapp_url:
        separator = "&" if "?" in webapp_url else "?"
        webapp_url_versioned = f"{webapp_url}{separator}v={version_tag}"
    else:
        webapp_url_versioned = webapp_url

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    def is_admin(message_or_cb) -> bool:
        return admin_id and str(message_or_cb.from_user.id) == str(admin_id)

    @dp.message(CommandStart())
    async def start(message: Message):
        if webapp_url_versioned:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🚛 Ochish", web_app=WebAppInfo(url=webapp_url_versioned))],
                    [InlineKeyboardButton(text="📖 Video yo'riqnoma", url="https://t.me/depohisobkitobchat/3")],
                ]
            )
            await message.answer(
                "Assalomu alaykum! 👋\n\n"
                "DEPO HisobKitob — fura reyslari, xarajat va foydangizni avtomatik hisoblaydi.\n\n"
                "🚛 Boshlash uchun \"Ochish\" tugmasini bosing.\n"
                "📖 Qanday ishlatishni bilmasangiz — video yo'riqnomani ko'ring.",
                reply_markup=kb,
            )
        else:
            await message.answer("Bot sozlanmoqda, WEBAPP_URL hali berilmagan.")

    def admin_menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Umumiy statistika", callback_data="admin_stats")],
            [InlineKeyboardButton(text="👤 Foydalanuvchilar (Excel)", callback_data="admin_users")],
            [InlineKeyboardButton(text="🚛 Moshinalar (Excel)", callback_data="admin_trucks")],
            [InlineKeyboardButton(text="🧭 Reyslar (Excel)", callback_data="admin_trips")],
            [InlineKeyboardButton(text="📣 Hammaga xabar yuborish", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="🔔 Eslatmalarni hozir yuborish", callback_data="admin_retention_run")],
            [InlineKeyboardButton(text="♻️ Eslatma belgilarini tozalash", callback_data="admin_retention_reset")],
        ])

    @dp.message(Command("admin"))
    async def admin_panel(message: Message):
        if not is_admin(message):
            return  # admin bo'lmasa, javob bermaydi (buyruq mavjudligini oshkor qilmaslik uchun)
        await message.answer(
            "🛠 <b>Admin panel</b>\nKerakli bo'limni tanlang:",
            reply_markup=admin_menu(),
            parse_mode="HTML",
        )

    @dp.callback_query(F.data == "admin_stats")
    async def cb_stats(callback: CallbackQuery):
        if not is_admin(callback):
            await callback.answer("Ruxsat yo'q", show_alert=True)
            return
        s = await db.get_admin_stats()
        text = (
            "📊 <b>Umumiy statistika</b>\n\n"
            f"👤 Foydalanuvchilar: <b>{s['total_users']}</b>\n"
            f"🚛 Furalar: <b>{s['total_trucks']}</b>\n"
            f"🧭 Reyslar: <b>{s['total_trips']}</b> (faol: {s['active_trips']})\n"
            f"🔥 So'nggi 7 kunda faol: <b>{s['active_users_7d']}</b>"
        )
        await callback.message.answer(text, parse_mode="HTML")
        await callback.answer()

    @dp.callback_query(F.data == "admin_users")
    async def cb_users(callback: CallbackQuery):
        if not is_admin(callback):
            await callback.answer("Ruxsat yo'q", show_alert=True)
            return
        rows = await db.admin_users_rows()
        data = excel_export.make_users_excel(rows)
        file = BufferedInputFile(data, filename="foydalanuvchilar.xlsx")
        await callback.message.answer_document(file, caption="👤 Foydalanuvchilar ro'yxati")
        await callback.answer()

    @dp.callback_query(F.data == "admin_trucks")
    async def cb_trucks(callback: CallbackQuery):
        if not is_admin(callback):
            await callback.answer("Ruxsat yo'q", show_alert=True)
            return
        rows = await db.admin_trucks_rows()
        data = excel_export.make_trucks_excel(rows)
        file = BufferedInputFile(data, filename="moshinalar.xlsx")
        await callback.message.answer_document(file, caption="🚛 Moshinalar ro'yxati")
        await callback.answer()

    @dp.callback_query(F.data == "admin_trips")
    async def cb_trips(callback: CallbackQuery):
        if not is_admin(callback):
            await callback.answer("Ruxsat yo'q", show_alert=True)
            return
        rows = await db.admin_trips_rows()
        data = excel_export.make_trips_excel(rows)
        file = BufferedInputFile(data, filename="reyslar.xlsx")
        await callback.message.answer_document(file, caption="🧭 Reyslar ro'yxati")
        await callback.answer()

    # ---------------- BROADCAST (hammaga xabar) ----------------
    # broadcast_state: {"waiting": True} - matn kutilyapti; {"text": "..."} - tasdiqlash kutilyapti
    broadcast_state = {}

    @dp.callback_query(F.data == "admin_retention_run")
    async def cb_retention_run(callback: CallbackQuery):
        """Retention eslatmalarini DARHOL yuborish (6 soat kutmasdan) + hisobot."""
        if not is_admin(callback):
            await callback.answer("Ruxsat yo'q", show_alert=True)
            return
        await callback.answer()
        marked = await db.count_reminders()
        await callback.message.answer(
            f"🔔 Eslatma tsikli boshlandi...\n"
            f"(Avval belgilanganlar: jim reys={marked.get('stale_trip', 0)}, "
            f"reyssiz={marked.get('no_trip', 0)})"
        )
        try:
            na, sa, nb, sb, err = await _run_retention_once(bot, _make_open_kb())
            await callback.message.answer(
                f"✅ Yakunlandi:\n\n"
                f"🚛 Jim qolgan reyslar: {na} nomzod, {sa} ta yuborildi\n"
                f"📥 Mashina bor/reys yo'q: {nb} nomzod, {sb} ta yuborildi\n"
                f"⚠️ Xatolar: {err}\n\n"
                f"(Nomzod 0 bo'lsa - hammasi avval belgilangan. "
                f"Qayta yuborish uchun: ♻️ tozalash → 🔔 yuborish)"
            )
        except Exception as e:
            await callback.message.answer(f"❌ Xato: {e}")

    @dp.callback_query(F.data == "admin_retention_reset")
    async def cb_retention_reset(callback: CallbackQuery):
        if not is_admin(callback):
            await callback.answer("Ruxsat yo'q", show_alert=True)
            return
        marked = await db.count_reminders()
        await db.clear_reminders()
        await callback.message.answer(
            f"♻️ Belgilar tozalandi (jim reys={marked.get('stale_trip', 0)}, "
            f"reyssiz={marked.get('no_trip', 0)} edi).\n\n"
            f"Endi 🔔 tugmasini bossangiz, barcha mos foydalanuvchilarga qaytadan yuboriladi."
        )
        await callback.answer()

    @dp.callback_query(F.data == "admin_broadcast")
    async def cb_broadcast_start(callback: CallbackQuery):
        if not is_admin(callback):
            await callback.answer("Ruxsat yo'q", show_alert=True)
            return
        broadcast_state.clear()
        broadcast_state["waiting"] = True
        await callback.message.answer(
            "📣 Barcha foydalanuvchilarga yuboriladigan xabar matnini yozing.\n\n"
            "Bekor qilish uchun: /cancel"
        )
        await callback.answer()

    @dp.message(Command("cancel"))
    async def cancel_broadcast(message: Message):
        if not is_admin(message):
            return
        if broadcast_state:
            broadcast_state.clear()
            await message.answer("❌ Bekor qilindi.")

    @dp.callback_query(F.data == "broadcast_confirm")
    async def cb_broadcast_confirm(callback: CallbackQuery):
        if not is_admin(callback):
            await callback.answer("Ruxsat yo'q", show_alert=True)
            return
        text = broadcast_state.get("text")
        broadcast_state.clear()
        if not text:
            await callback.answer("Xabar topilmadi, qaytadan boshlang", show_alert=True)
            return
        await callback.answer()
        ids = await db.get_all_telegram_ids()
        await callback.message.answer(f"⏳ Yuborilmoqda... ({len(ids)} foydalanuvchi)")
        sent, failed = 0, 0
        for tid in ids:
            try:
                await bot.send_message(tid, text)
                sent += 1
            except Exception:
                failed += 1  # botni bloklagan yoki o'chirilgan akkauntlar
            await asyncio.sleep(0.05)  # Telegram limitiga rioya (soniyasiga ~20 xabar)
        await callback.message.answer(
            f"✅ Yuborildi: {sent} ta\n"
            f"🚫 Yetmadi (bloklagan/o'chirilgan): {failed} ta"
        )

    @dp.callback_query(F.data == "broadcast_cancel")
    async def cb_broadcast_cancel(callback: CallbackQuery):
        if not is_admin(callback):
            return
        broadcast_state.clear()
        await callback.message.answer("❌ Bekor qilindi.")
        await callback.answer()

    @dp.message(F.text & ~F.text.startswith("/"))
    async def catch_broadcast_text(message: Message):
        """Admin broadcast matnini kutayotgan holatda yozgan matnini ushlaydi."""
        if not is_admin(message) or not broadcast_state.get("waiting"):
            return
        broadcast_state.clear()
        broadcast_state["text"] = message.text
        ids = await db.get_all_telegram_ids()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"✅ Yuborish ({len(ids)} kishiga)", callback_data="broadcast_confirm"),
                InlineKeyboardButton(text="❌ Bekor", callback_data="broadcast_cancel"),
            ]
        ])
        await message.answer(
            "Quyidagi xabar yuboriladi:\n\n" + message.text + "\n\nTasdiqlaysizmi?",
            reply_markup=kb,
        )

    await db.init_db()
    await dp.start_polling(bot)


async def run_web():
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


def _restore_seed_if_needed():
    """Yangi serverga birinchi ko'chirishda: volume bo'sh bo'lsa, seed bazani
    ko'chiradi. Baza allaqachon mavjud bo'lsa hech narsa qilmaydi (ma'lumot
    ustidan yozib yubormaydi)."""
    import shutil
    seed = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_fura.sqlite")
    target = db.DB_PATH
    if os.path.exists(seed) and not os.path.exists(target):
        d = os.path.dirname(target)
        if d:
            os.makedirs(d, exist_ok=True)
        shutil.copy(seed, target)
        print(f"[SEED] Boshlang'ich baza ko'chirildi: {seed} -> {target}", flush=True)


async def main():
    _restore_seed_if_needed()
    await db.init_db()
    # Ishga tushishda darhol kursni olamiz (ishlamasa, bazadagi oxirgi kurs qoladi)
    fetched = rate_module.fetch_usd_rate()
    if fetched:
        await db.set_setting("usd_rate", str(fetched))
    # Eski davrdan qolgan USD yozuvlarni tuzatish: ular rate=1 bilan saqlangan
    # (o'sha paytda bot faqat USD da ishlagan). Ularga joriy kursni beramiz,
    # shunda so'mga aylantirish to'g'ri ishlaydi.
    current = await get_usd_rate()
    await db.fix_legacy_usd_rates(current)
    await asyncio.gather(run_web(), run_bot(), update_rate_loop(), daily_backup_loop(), retention_loop())


if __name__ == "__main__":
    asyncio.run(main())
