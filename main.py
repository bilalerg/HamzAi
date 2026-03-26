# main.py — HamzaAI FastAPI Uygulaması

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import validate_config, ENV
from app.core.database import init_db, SessionLocal
from app.core.logger import logger
from app.chatbox.webhook import router as webhook_router


# ── STARTUP & SHUTDOWN ────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sistem başlarken çalışır
    logger.info("🚀 HamzaAI başlatılıyor...")

    # Config kontrolü
    validate_config()

    # DB tabloları oluştur
    init_db()

    # Pending işlemleri tara (state recovery)
    await startup_recovery()

    # Mail listener'ı arka planda başlat
    asyncio.create_task(mail_listener_baslat())

    logger.info(f"✅ HamzaAI hazır — Ortam: {ENV}")

    yield

    # Sistem kapanırken çalışır
    logger.info("HamzaAI kapanıyor...")


# ── FASTAPI UYGULAMASI ────────────────────────────────────────────────────
app = FastAPI(
    title="HamzaAI",
    description="Muhasebe Otomasyon Ajanı",
    version="1.0.0",
    lifespan=lifespan
)

# CORS — dashboard ve dış istekler için
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Webhook router'ı ekle
app.include_router(webhook_router)


# ── HEALTH CHECK ─────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    # Render bu endpoint'i ping eder — sistem ayakta mı diye
    return {
        "status": "ok",
        "version": "1.0.0",
        "env": ENV
    }


# ── STATUS ────────────────────────────────────────────────────────────────
@app.get("/status")
async def status():
    # Dashboard için canlı veri
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus
        from sqlalchemy import func

        toplam = db.query(WeighTicket).count()
        tamamlandi = db.query(WeighTicket).filter(
            WeighTicket.status == TicketStatus.TAMAMLANDI
        ).count()
        bekleyenler = db.query(WeighTicket).filter(
            WeighTicket.status.notin_([
                TicketStatus.TAMAMLANDI,
                TicketStatus.IPTAL,
                TicketStatus.HATA
            ])
        ).count()

        return {
            "toplam_fis": toplam,
            "tamamlandi": tamamlandi,
            "bekleyenler": bekleyenler,
        }
    except Exception as e:
        logger.error(f"Status hatası: {e}")
        return {"error": str(e)}
    finally:
        db.close()


# ── STARTUP RECOVERY ──────────────────────────────────────────────────────
async def startup_recovery():
    """
    Sistem başlarken yarım kalan işlemleri tespit eder.
    Elektrik kesintisi veya çökme sonrası kaldığı yerden devam eder.
    """
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus

        # Yarım kalan işlemleri bul
        bekleyenler = db.query(WeighTicket).filter(
            WeighTicket.status == TicketStatus.IRSALIYE_OLUSTURULUYOR
        ).all()

        if bekleyenler:
            logger.warning(f"⚠️ {len(bekleyenler)} yarım kalan işlem tespit edildi")
            for ticket in bekleyenler:
                logger.info(f"Recovery: ticket_id={ticket.id} plaka={ticket.plaka}")
                # Paraşüt irsaliye adımı Faz 4'te eklenecek
        else:
            logger.info("✅ Yarım kalan işlem yok")

    except Exception as e:
        logger.error(f"Startup recovery hatası: {e}")
    finally:
        db.close()


# ── MAIL LİSTENER ────────────────────────────────────────────────────────
# main.py içindeki ilgili fonksiyonu bununla değiştir:

async def mail_listener_baslat():
    """
    Mail listener'ı ana döngüden tamamen koparıp ayrı bir kanalda çalıştırır.
    Böylece FastAPI (uvicorn) kantar fişlerini işlemeye devam edebilir.
    """
    import threading
    from app.mail.listener import gelen_kutu_dinle

    # Daemon=True sayesinde ana program kapanınca bu da kapanır
    thread = threading.Thread(target=gelen_kutu_dinle, args=(60,), daemon=True)
    thread.start()
    logger.info("📬 Mail dinleyici bağımsız bir kanalda (Thread) başlatıldı.")