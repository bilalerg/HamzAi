# config.py — Projenin Ayar Merkezi

import os
from pathlib import Path
from dotenv import load_dotenv

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
PARASUT_HURDA_PRODUCT_ID = os.getenv("PARASUT_HURDA_PRODUCT_ID")

# ── VERİTABANI ─────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")

# ── MAİL — SMTP ────────────────────────────────────────────────────────
MAIL_HOST     = os.getenv("MAIL_HOST", "smtp.gmail.com")
MAIL_PORT     = int(os.getenv("MAIL_PORT", "587"))
MAIL_USER     = os.getenv("MAIL_USER", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM     = os.getenv("MAIL_FROM", "")
FABRIKA_MAIL  = os.getenv("FABRIKA_MAIL", "")

# ── MAİL — IMAP ────────────────────────────────────────────────────────
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

# ── GÜVENLİK ───────────────────────────────────────────────────────────
# UYARI: Bunlar production'da mutlaka .env'de set edilmeli.
# Varsayılan değerler sadece local development içindir.
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
ADMIN_TOKEN    = os.getenv("ADMIN_TOKEN", "")

# Tenant credential şifreleme — Fernet simetrik şifreleme
# Üretmek için: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Boş bırakılırsa credential'lar düz metin saklanır (geriye uyumlu)
CREDENTIAL_ENCRYPTION_KEY = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")

# Profaix iframe girişinde uzun vadeli güvenli akış:
# Profaix.com, PROFAIX_EMBED_SECRET ile kısa ömürlü JWT üretir ve /chat?embed_token=... gönderir.
# Mobil Safari üçüncü taraf cookie'yi engellese bile chat.html API isteklerinde Authorization header kullanır.
PROFAIX_EMBED_SECRET = os.getenv("PROFAIX_EMBED_SECRET", "")
REQUIRE_SIGNED_EMBED_AUTH = os.getenv(
    "REQUIRE_SIGNED_EMBED_AUTH",
    "0"
).lower() in ("1", "true", "yes")

# ── UYGULAMA URL ───────────────────────────────────────────────────
# Demo ortamında https://profaix-demo.onrender.com olarak set edilmeli.
# Production'da boş bırakılır → profaix.com/login kullanılır.
BASE_URL = os.getenv("BASE_URL", "")

# ── CORS ───────────────────────────────────────────────────────────
# Üretimde izin verilen origin'leri .env'de virgülle ayırarak tanımlayın.
# Örnek: ALLOWED_ORIGINS=https://profaix.com,https://chat.profaix.com
#
# GÜVENLİK: Production'da ALLOWED_ORIGINS boş bırakılırsa "*" kullanmak
# yerine güvenli default (profaix.com aileleri) kullanılır. "*" ile
# allow_credentials=True birleştiğinde tarayıcı zaten reddeder, ama yine de
# kapalı tutalım.
_raw_origins    = os.getenv("ALLOWED_ORIGINS", "")
_default_origins = [
    "https://profaix.com",
    "https://www.profaix.com",
    "https://chat.profaix.com",
    "https://app.profaix.com",
    "https://profaix-api.onrender.com",
]
if _raw_origins:
    ALLOWED_ORIGINS: list = [o.strip() for o in _raw_origins.split(",") if o.strip()]
elif os.getenv("ENV", "development") == "production":
    ALLOWED_ORIGINS: list = _default_origins
else:
    ALLOWED_ORIGINS: list = ["*"]

# ── DOĞRULAMA ──────────────────────────────────────────────────────────
_GUVENSIZ_DEFAULTS = {
    "profaix-super-secret-key-2026",
    "profaix-admin-2026",
    "",
}

def validate_config():
    """Kritik ayarların dolu ve güvenli olduğunu kontrol eder."""

    kritik_alanlar = {
        "ANTHROPIC_API_KEY":     ANTHROPIC_API_KEY,
        "DATABASE_URL":          DATABASE_URL,
        "PARASUT_CLIENT_ID":     PARASUT_CLIENT_ID,
        "PARASUT_CLIENT_SECRET": PARASUT_CLIENT_SECRET,
        "PARASUT_USERNAME":      PARASUT_USERNAME,
        "PARASUT_PASSWORD":      PARASUT_PASSWORD,
        "PARASUT_COMPANY_ID":    PARASUT_COMPANY_ID,
        "JWT_SECRET_KEY":        JWT_SECRET_KEY,
        "ADMIN_TOKEN":           ADMIN_TOKEN,
    }
    if REQUIRE_SIGNED_EMBED_AUTH:
        kritik_alanlar["PROFAIX_EMBED_SECRET"] = PROFAIX_EMBED_SECRET

    eksikler = [isim for isim, deger in kritik_alanlar.items() if not deger]
    if eksikler:
        raise ValueError(
            f"Şu .env değerleri eksik: {', '.join(eksikler)}\n"
            f".env dosyasını kontrol et!"
        )

    # Production'da varsayılan/güvensiz değer kontrolü
    if IS_PRODUCTION:
        guvensiz = []
        if JWT_SECRET_KEY in _GUVENSIZ_DEFAULTS:
            guvensiz.append("JWT_SECRET_KEY")
        if ADMIN_TOKEN in _GUVENSIZ_DEFAULTS:
            guvensiz.append("ADMIN_TOKEN")
        if guvensiz:
            raise ValueError(
                f"PRODUCTION'DA GÜVENSİZ DEĞER: {', '.join(guvensiz)}\n"
                f"Bu değerleri .env'de benzersiz, güçlü bir değerle set edin!"
            )

    from app.core.logger import logger
    logger.info(f"✅ Config yüklendi — Ortam: {ENV}")