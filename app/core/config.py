# config.py — Projenin Ayar Merkezi
#
# NEDEN BU DOSYA VAR?
# Projedeki her modül API key, şifre, URL gibi bilgilere ihtiyaç duyar.
# Bunları her dosyaya ayrı ayrı yazmak yerine tek bir yerden yönetiyoruz.
# Bir değer değişince sadece .env'i güncelliyoruz, kod hiç dokunulmuyor.
#
# NASIL ÇALIŞIR?
# python-dotenv kütüphanesi .env dosyasını okur ve
# os.getenv() ile bu değerlere ulaşırız.

import os
from dotenv import load_dotenv

# .env dosyasını yükle
# Bu satır olmadan os.getenv() hiçbir şey bulamaz
load_dotenv()

# ── ORTAM ──────────────────────────────────────────────────────────────
# ENV değişkeni sistemin hangi modda çalıştığını belirler.
# "development" → sahte mail, sandbox API, local DB
# "production"  → gerçek her şey
ENV = os.getenv("ENV", "development")
IS_PRODUCTION = ENV == "production"

# ── ANTHROPİC ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ── PARAŞÜT ────────────────────────────────────────────────────────────
PARASUT_API_KEY = os.getenv("PARASUT_API_KEY")
PARASUT_BASE_URL = os.getenv("PARASUT_BASE_URL", "https://api.parasut.com/v4")

# ── VERİTABANI ─────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")

# ── MAİL ───────────────────────────────────────────────────────────────
MAIL_HOST = os.getenv("MAIL_HOST", "localhost")
MAIL_PORT = int(os.getenv("MAIL_PORT", "1025"))
MAIL_USER = os.getenv("MAIL_USER", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", "hamza@hamzaai.com")

# ── WHATSAPP ───────────────────────────────────────────────────────────
WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "twilio_sandbox")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")

# ── ZİRVE ──────────────────────────────────────────────────────────────
ZIRVE_USERNAME = os.getenv("ZIRVE_USERNAME")
ZIRVE_PASSWORD = os.getenv("ZIRVE_PASSWORD")

# ── BİLDİRİM NUMARALARI ────────────────────────────────────────────────
MUHASEBECI_WHATSAPP = os.getenv("MUHASEBECI_WHATSAPP")
PATRON_WHATSAPP = os.getenv("PATRON_WHATSAPP")


# ── DOĞRULAMA ──────────────────────────────────────────────────────────
# NEDEN BU FONKSİYON VAR?
# Sistem başlarken kritik değerlerin dolu olup olmadığını kontrol eder.
# Boş key ile çalışmaya devam ederse sistem ileride sessizce çöker —
# bunu baştan yakalamak çok daha iyi.

def validate_config():
    """Kritik ayarların dolu olduğunu kontrol eder. Eksik varsa hata fırlatır."""

    kritik_alanlar = {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "DATABASE_URL": DATABASE_URL,
    }

    eksikler = [isim for isim, deger in kritik_alanlar.items() if not deger]

    if eksikler:
        raise ValueError(
            f"Şu .env değerleri eksik: {', '.join(eksikler)}\n"
            f".env dosyasını kontrol et!"
        )

    print(f"✅ Config yüklendi — Ortam: {ENV}")