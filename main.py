# main.py -- Profaix FastAPI Uygulamasi (yalin app + middleware + router kayitlari)
#
# Endpoint'ler app/routers/ altinda mantiksal modullere ayrildi.
# Helper fonksiyonlar app/routers/deps.py icinde paylasilir.

import asyncio
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import validate_config, ENV, ALLOWED_ORIGINS
from app.core.database import init_db, SessionLocal, engine
from app.core.logger import logger
from app.auth import token_durumu, ilk_admin_olustur
from app.routers.deps import _admin_kontrol


# ────────────────────────────────────────────────────────────────────
#  KEEP-ALIVE & STARTUP
# ────────────────────────────────────────────────────────────────────

async def _keep_alive():
    """Render'in 'idle uyutma' davranisini engellemek icin periyodik ping."""
    import httpx
    base_url = os.getenv("BASE_URL", "https://profaix-api.onrender.com")
    await asyncio.sleep(60)
    while True:
        try:
            async with httpx.AsyncClient() as client:
                await client.get(f"{base_url}/health", timeout=10)
            logger.info("✅ Keep-alive ping gönderildi")
        except Exception as e:
            logger.warning(f"Keep-alive ping başarısız: {e}")
        await asyncio.sleep(600)


async def _startup_background():
    """DB init + migrations + admin init -- arka planda kosar."""
    try:
        init_db()
    except Exception as e:
        logger.warning(f"⚠️ DB başlatma gecikmeli: {e}")
    try:
        from app.core import migrations
        db = SessionLocal()
        try:
            await asyncio.to_thread(migrations.calistir, engine, db)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"⚠️ Migration atlandı: {e}")
    try:
        ilk_admin_olustur()
    except Exception as e:
        logger.warning(f"⚠️ Admin oluşturma atlandı: {e}")
    asyncio.create_task(_keep_alive())


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Profaix başlatılıyor...")
    validate_config()
    asyncio.create_task(_startup_background())
    logger.info(f"✅ Profaix hazır — Ortam: {ENV}")
    yield
    logger.info("Profaix kapanıyor...")


# ────────────────────────────────────────────────────────────────────
#  APP + MIDDLEWARE
# ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Profaix",
    description="Muhasebe Otomasyon Ajanı",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
)

# ── STATIK DOSYALAR ────────────────────────────────────────────────
# /static/css/chat.css, /static/js/0X-*.js, /static/img/logo.jpg
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    logger.info(f"📁 Statik dosyalar mount edildi: {_STATIC_DIR}")
else:
    logger.warning(f"⚠️ Statik dosya klasörü bulunamadı: {_STATIC_DIR}")

# ── KIMLIK DOGRULAMA MIDDLEWARE ───────────────────────────────────────
# /api/login, /api/logout, /api/me herkese acik;
# /api/billing/* admin token ile de gecilebilir; diger /api/* giris zorunlu.
_ACIK_API_YOLLARI = {"/api/login", "/api/logout", "/api/me"}


@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/billing/") and _admin_kontrol(request):
        return await call_next(request)
    if path.startswith("/api/") and path not in _ACIK_API_YOLLARI:
        user, auth_reason = token_durumu(request)
        if not user:
            if auth_reason == "replaced":
                return JSONResponse(
                    {
                        "hata": "Bu hesap başka bir cihazda açıldığı için bu oturum kapatıldı.",
                        "code": "SESSION_REPLACED",
                    },
                    status_code=401,
                )
            return JSONResponse(
                {"hata": "Bu işlem için giriş yapmanız gerekiyor.", "code": "AUTH_REQUIRED"},
                status_code=401,
            )
    return await call_next(request)


# ────────────────────────────────────────────────────────────────────
#  ROUTER KAYITLARI
# ────────────────────────────────────────────────────────────────────
# Endpoint'lerin tum tanimlari app/routers/ altindadir.
# Yeni endpoint eklemek icin: ilgili router dosyasina @router.X olarak ekle.

from app.routers import (
    auth as auth_router,        # /health, /status, /chat, /login, /api/login,
                                # /api/logout, /api/me, /api/session/*
    chat as chat_router,        # /api/chat
    ocr as ocr_router,          # /api/upload, /api/fis-iptal, /api/firma-sec
    cari as cari_router,        # /api/cariler, /api/cari-bilgi, /api/cari-olustur, /api/cari-guncelle
    fatura as fatura_router,    # /api/fatura-* (satis), /api/toplu-fatura-kes, /api/cari-faturalar
    alis_fatura as alis_fatura_router,  # /api/alis-faturasi-*, /api/bekleyen-alis-faturalari, /api/e-fatura-*
    tahsilat as tahsilat_router,  # /api/tahsilat, /api/odeme-ekle, /api/cari-odeme,
                                  # /api/para-transferi, /api/hesaplar, /api/stok-hareketleri
    rapor as rapor_router,      # /api/hesap-ozeti, /api/islem-gecmisi, /api/bekleyen-irsaliyeler,
                                # /api/gunluk-ozet, /api/son-fisler, /api/gelir-gider-raporu
    billing as billing_router,  # /api/billing/*, /api/kullanim-ozeti
    admin as admin_router,      # /admin/*
    toplu as toplu_router,      # /api/toplu/onizle, /api/toplu/uygula
)

app.include_router(auth_router.router)
app.include_router(chat_router.router)
app.include_router(ocr_router.router)
app.include_router(cari_router.router)
app.include_router(fatura_router.router)
app.include_router(alis_fatura_router.router)
app.include_router(tahsilat_router.router)
app.include_router(rapor_router.router)
app.include_router(billing_router.router)
app.include_router(admin_router.router)
app.include_router(toplu_router.router)
