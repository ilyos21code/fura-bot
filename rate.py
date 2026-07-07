"""Markaziy bank (cbu.uz) rasmiy USD/UZS kursini olish va keshda saqlash."""
import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

CBU_URL = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/"
DEFAULT_RATE = 12500.0  # cbu ishlamay qolsa, zaxira qiymat


def fetch_usd_rate() -> float:
    """cbu.uz dan joriy USD kursini oladi. Xato bo'lsa None qaytaradi."""
    try:
        req = urllib.request.Request(CBU_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # cbu javobi: [{"Ccy":"USD","Rate":"12345.67",...}]
        if isinstance(data, list) and data:
            rate = float(data[0].get("Rate", 0))
            if rate > 0:
                return rate
    except Exception as e:
        logger.warning("cbu.uz kursini olishda xato: %s", e)
    return None
