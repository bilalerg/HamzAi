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
# Render'da Secret Files /etc/secrets/.env konumunda olur.

import os
from pathlib import Path
from dotenv import load_dotenv

# Render'da Secret Files /etc/secrets/.env konumunda
# Local'de proje kökündeki .env dosyası
secret_env = Path("/etc/secrets/.env")
if secret_env.exists():
    load_dotenv(secret_env)
else:
    load_dotenv()

# ── ORTAM ──────────────────────────────────────────────────────────────
ENV = os.getenv("ENV", "development")
IS_PRODUCTION = ENV == "production"

# ── ANTHROPİC ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ── PARAŞÜT ────────────────────────────────────────────────────────────
PARASUT_CLIENT_ID        = os.getenv("PARASUT_CLIENT_ID")
PARASUT_CLIENT_SECRET    = os.getenv("PARASUT_CLIENT_SECRET")
PARASUT_USERNAME         = os.getenv("PARASUT_USERNAME")
PARASUT_PASSWORD         = os.getenv("PARASUT_PASSWORD")
PARASUT_COMPANY_ID       = os.getenv("PARASUT_COMPANY_ID")
PARASUT_BASE_URL         = os.getenv("PARASUT_BASE_URL", "https://api.parasut.com/v4")
PARASUT_CONTACT_IDS = {
    "UHT UFUK HURDA": os.getenv("PARASUT_DEMO_CONTACT_ID"),
    "SAMSUN MAKİNE":  "1061309834",
    "KROMAN":         "1061309836",
    "İÇTAŞ DEMİR ÇELİK TERSANE": "1061309838",
    "KAPTAN DEMİR ÇELİK": "1061309841",
}
PARASUT_HURDA_PRODUCT_ID = os.getenv("PARASUT_HURDA_PRODUCT_ID")

# ── VERİTABANI ─────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")

# ── MAİL — SMTP (gönderme) ─────────────────────────────────────────────
MAIL_HOST     = os.getenv("MAIL_HOST", "smtp.gmail.com")
MAIL_PORT     = int(os.getenv("MAIL_PORT", "587"))
MAIL_USER     = os.getenv("MAIL_USER", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM     = os.getenv("MAIL_FROM", "")
FABRIKA_MAIL  = os.getenv("FABRIKA_MAIL", "")

# ── MAİL — IMAP (okuma) ────────────────────────────────────────────────
# SMTP → mail göndermek için
# IMAP → mail okumak için (fabrika reply'larını yakalamak)
# Gmail IMAP: imap.gmail.com:993 (SSL)
IMAP_HOST     = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT     = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER     = os.getenv("IMAP_USER", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")

# ── WHATSAPP ───────────────────────────────────────────────────────────
WHATSAPP_PROVIDER    = os.getenv("WHATSAPP_PROVIDER", "twilio_sandbox")
TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")

# ── BİLDİRİM NUMARALARI ────────────────────────────────────────────────
MUHASEBECI_WHATSAPP = os.getenv("MUHASEBECI_WHATSAPP")
PATRON_WHATSAPP     = os.getenv("PATRON_WHATSAPP")


# ── DOĞRULAMA ──────────────────────────────────────────────────────────
def validate_config():
    """Kritik ayarların dolu olduğunu kontrol eder. Eksik varsa hata fırlatır."""

    kritik_alanlar = {
        "ANTHROPIC_API_KEY":     ANTHROPIC_API_KEY,
        "DATABASE_URL":          DATABASE_URL,
        "PARASUT_CLIENT_ID":     PARASUT_CLIENT_ID,
        "PARASUT_CLIENT_SECRET": PARASUT_CLIENT_SECRET,
        "PARASUT_USERNAME":      PARASUT_USERNAME,
        "PARASUT_PASSWORD":      PARASUT_PASSWORD,
        "PARASUT_COMPANY_ID":    PARASUT_COMPANY_ID,
    }

    eksikler = [isim for isim, deger in kritik_alanlar.items() if not deger]

    if eksikler:
        raise ValueError(
            f"Şu .env değerleri eksik: {', '.join(eksikler)}\n"
            f".env dosyasını kontrol et!"
        )

    print(f"✅ Config yüklendi — Ortam: {ENV}")