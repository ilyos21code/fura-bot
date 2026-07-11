"""USD/UZS kursini olish — uch qavatli himoya bilan.

1) Asosiy manba: cbu.uz (O'zbekiston Markaziy banki, rasmiy)
2) Zaxira manba: open.er-api.com (bepul, ochiq API)
3) Ikkalasi ham ishlamasa: bazadagi oxirgi saqlangan kurs ishlatiladi
   (bu main.py dagi get_usd_rate() da hal qilinadi)
"""
import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

CBU_URL = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/"
BACKUP_URL = "https://open.er-api.com/v6/latest/USD"
DEFAULT_RATE = 12650.0  # eng oxirgi chora (faqat birinchi ishga tushishda, baza bo'sh bo'lsa)

# Aqlga sig'adigan chegaralar - kurs shu oraliqdan chiqsa, noto'g'ri javob deb hisoblaymiz
MIN_SANE = 5000.0
MAX_SANE = 50000.0


def _sane(rate) -> bool:
    try:
        r = float(rate)
        return MIN_SANE <= r <= MAX_SANE
    except (TypeError, ValueError):
        return False


def _fetch_cbu():
    """Markaziy bank rasmiy kursi."""
    req = urllib.request.Request(CBU_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, list) and data:
        rate = float(str(data[0].get("Rate", "0")).replace(",", "."))
        if _sane(rate):
            return rate
    return None


def _fetch_backup():
    """Zaxira manba: open.er-api.com (USD -> UZS)."""
    req = urllib.request.Request(BACKUP_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    rate = data.get("rates", {}).get("UZS")
    if _sane(rate):
        return float(rate)
    return None


def fetch_usd_rate():
    """Joriy USD/UZS kursini oladi. Muvaffaqiyatsiz bo'lsa None qaytaradi
    (chaqiruvchi tomon bazadagi oxirgi kursdan foydalanadi)."""
    for name, fn in (("cbu.uz", _fetch_cbu), ("er-api.com", _fetch_backup)):
        try:
            rate = fn()
            if rate:
                logger.info("USD kursi olindi (%s): %s", name, rate)
                return rate
        except Exception as e:
            logger.warning("%s dan kurs olishda xato: %s", name, e)
    logger.warning("Hech qaysi manbadan kurs olinmadi - oxirgi saqlangan kurs ishlatiladi")
    return None
