# logger.py — Sistem Kayıt Tutucusu
#
# NEDEN LOGURU?
# Python'ın yerleşik logging kütüphanesi var ama kurulumu karmaşık.
# Loguru aynı işi çok daha temiz yapar:
# - Tek satırda kurulum
# - Otomatik renklendirme
# - Dosyaya yazma + otomatik arşivleme
# - Hata anında stack trace (hatanın tam nerede çıktığı)

from loguru import logger
import sys
from app.core.config import ENV

# Varsayılan handler'ı temizle
# Loguru başlangıçta stdout'a (ekrana) yazar — bunu özelleştireceğiz
logger.remove()

# ── EKRAN ÇIKTISI ───────────────────────────────────────────────────────
# format: her log satırının nasıl görüneceği
# {time}    → 2026-03-13 09:23:41
# {level}   → INFO, WARNING, ERROR
# {message} → bizim yazdığımız mesaj
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    level="DEBUG" if ENV == "development" else "INFO",
    colorize=True
)

# ── DOSYAYA YAZMA ───────────────────────────────────────────────────────
# NEDEN DOSYAYA?
# Sistem gece çalışırken ekran kimse görmüyor.
# Dosyaya yazılınca sabah "dün gece ne oldu?" diye bakabilirsin.
#
# rotation="1 day"  → Her gün yeni dosya açılır (hamzaai_2026-03-13.log)
# retention="30 days" → 30 günden eski loglar otomatik silinir (disk dolmasın)
# compression="zip" → Eski loglar sıkıştırılır

logger.add(
    "logs/hamzaai_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="DEBUG",
    rotation="1 day",
    retention="30 days",
    compression="zip",
    encoding="utf-8"
)

# ── HATA DOSYASI ────────────────────────────────────────────────────────
# Sadece ERROR ve üzeri seviyedeki loglar buraya gider.
# Normal logların arasında kaybolmadan hataları ayrı takip edebilirsin.
logger.add(
    "logs/errors.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="ERROR",
    rotation="1 week",
    retention="90 days",
    compression="zip",
    encoding="utf-8"
)

# logger'ı dışa aktar — diğer modüller bunu import eder
__all__ = ["logger"]