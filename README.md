# Fura Mini App

Telegram Mini App (ilova ichidagi to'liq sahifali interfeys) — fura egalari uchun
reyslar, xarajatlar va foydani boshqarish. Har bir foydalanuvchi faqat o'zining
ma'lumotlarini ko'radi.

## Loyihada nima bor

```
fura_miniapp/
  main.py          - server (API + botni ishga tushiradi)
  database.py       - ma'lumotlar bazasi (SQLite)
  auth.py           - Telegram foydalanuvchisini tekshirish
  static/index.html - ilovaning ko'rinishi (5 bo'lim: Moshinalar, Reyslar, Xarajatlar, Safarlar, Hisobot)
  requirements.txt  - kerakli kutubxonalar
  .env.example      - sozlamalar namunasi
  Procfile          - hosting uchun ishga tushirish buyrug'i
```

## Muhim tushuncha: nega "faqat mening kompyuterim" yetarli emas

Oddiy `/buyruq`li botdan farqli o'laroq, Mini App ochilishi uchun Telegram unga
**internetda doim ochiq turadigan https-manzil** talab qiladi. Uy kompyuteri
buni ta'minlay olmaydi (routerlar, IP manzil va xavfsizlik devori bunga
to'sqinlik qiladi).

Yaxshi xabar: bu degani kompyuteringiz doim yoniq turishi shart emas.
Kodni bepul/arzon **hosting** xizmatiga (masalan Render.com) joylab qo'yasiz,
u esa doim ishlab turadi — kompyuteringiz o'chiq bo'lsa ham bot ishlayveradi.

## Ishga tushirish uchun kerak bo'ladigan narsalar

1. **Telegram bot tokeni** — @BotFather orqali olinadi
2. **Hosting** — kodni joylashtirib, doim ishlab turadigan joy (masalan Render.com, bepul)
3. **GitHub akkaunt** — kodni hostingga yuklash uchun oraliq bosqich (bepul, faqat email kerak)

Bularning barchasini keyingi bosqichda birga, screenshot darajasida
qadam-baqadam sozlaymiz — hoziroq bezovta bo'lishingiz shart emas.

## Bot qanday ishlaydi (funksional tavsif)

### 🚛 Moshinalar
- Yangi fura qo'shish
- Har bir furaga ta'mirlash/balon kabi xarajatlarni yozib borish
- Har bir fura kartasida jami ta'mirlash summasi ko'rinadi

### 🧭 Reyslar
- Yangi reys boshlash (fura tanlanadi)
- Bir vaqtning o'zida faqat bitta faol reys bo'lishi mumkin
- Har bir reys kartasida: daromad, xarajat, sof foyda
- Reysni bosib, uning to'liq tafsilotini (safarlar + xarajatlar) ko'rish mumkin
- "Reysni yakunlash" tugmasi bilan reysni yopish

### 💸 Xarajatlar
- Joriy (faol) reys uchun xarajat qo'shish: Yoqilg'i / Yo'l haqi / Ovqatlanish / Boshqa
- Jami xarajat yuqorida katta raqam bilan ko'rinadi

### 📍 Safarlar
- Joriy reys davomida bir nechta yuk yo'nalishini qo'shish (qayerdan — qayerga — necha pulga)
- Jami daromad yuqorida katta raqam bilan ko'rinadi

### 📊 Hisobot
- Umumiy sof foyda/zarar
- Oylar kesimida daromad/xarajat taqqoslash
- Har bir fura bo'yicha alohida foyda/zarar

## Lokal sinov (ixtiyoriy, dasturchilar uchun)

Agar birov (masalan men) buni terminalda sinab ko'rmoqchi bo'lsa:

```
pip install -r requirements.txt
export BOT_TOKEN="tokeningiz"
export DEV_MODE=1
python main.py
```

`DEV_MODE=1` bo'lganda, Telegram ichida ochmasdan ham `http://localhost:8000`
manzilida ilovani brauzerda ko'rish mumkin (test uchun soxta foydalanuvchi bilan).

## Keyingi qadam

Endi buni haqiqiy Telegram botiga ulash va internetga chiqarish kerak. Bu:
1. @BotFather orqali bot va token olish
2. Render.com'da bepul hosting ochish
3. Kodni GitHub orqali hostingga yuklash
4. Bot tokeni va hosting manzilini bir-biriga bog'lash

Bu bosqichlarni birga, screenshot darajasida qadam-baqadam bajaramiz.
