import asyncio
import logging
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

import database as db
from auth import validate_init_data, InitDataError

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"

app = FastAPI(title="Fura Mini App")


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
    note: str = ""


class TripIn(BaseModel):
    truck_id: int


class TripExpenseIn(BaseModel):
    category: str
    amount: float
    note: str = ""


class TripLegIn(BaseModel):
    from_point: str
    to_point: str
    price: float


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
        {"id": r[0], "category": r[1], "amount": r[2], "note": r[3], "created_at": r[4]}
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
    expense_id = await db.add_truck_expense(truck_id, payload.category, payload.amount, payload.note)
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
    income = sum(l[3] for l in legs)
    expense = sum(e[2] for e in expenses)
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
            {"id": e[0], "category": e[1], "amount": e[2], "note": e[3], "created_at": e[4]}
            for e in expenses
        ],
        "legs": [
            {"id": l[0], "from_point": l[1], "to_point": l[2], "price": l[3], "created_at": l[4]}
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
    expense_id = await db.add_trip_expense(trip_id, payload.category, payload.amount, payload.note)
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
    leg_id = await db.add_trip_leg(
        trip_id, payload.from_point.strip(), payload.to_point.strip(), payload.price
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
    return await db.get_report(user_id)


# ---------------- Frontend ----------------
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


async def run_bot():
    from aiogram import Bot, Dispatcher
    from aiogram.filters import CommandStart
    from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

    webapp_url = os.getenv("WEBAPP_URL", "")
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message):
        if webapp_url:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Ochish", web_app=WebAppInfo(url=webapp_url))]]
            )
            await message.answer(
                "Assalomu alaykum! Fura reyslaringizni shu yerdan boshqaring:", reply_markup=kb
            )
        else:
            await message.answer("Bot sozlanmoqda, WEBAPP_URL hali berilmagan.")

    await db.init_db()
    await dp.start_polling(bot)


async def run_web():
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    await db.init_db()
    await asyncio.gather(run_web(), run_bot())


if __name__ == "__main__":
    asyncio.run(main())
