# main.py — Profaix FastAPI Uygulaması

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import validate_config, ENV
from app.core.database import init_db, SessionLocal, engine
from app.core.logger import logger
from app.chatbox.webhook import router as webhook_router


# ── STARTUP & SHUTDOWN ────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Profaix başlatılıyor...")
    validate_config()
    init_db()
    await startup_recovery()
    asyncio.create_task(mail_listener_baslat())
    logger.info(f"✅ Profaix hazır — Ortam: {ENV}")
    yield
    logger.info("Profaix kapanıyor...")


# ── FASTAPI UYGULAMASI ────────────────────────────────────────────────────
app = FastAPI(
    title="Profaix",
    description="Muhasebe Otomasyon Ajanı",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)


# ── HEALTH CHECK ─────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "env": ENV}


# ── STATUS ────────────────────────────────────────────────────────────────
@app.get("/status")
async def status():
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus

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


# ── ADMIN: DB TEMİZLE (sadece demo için) ──────────────────────────────────
@app.get("/admin/reset-db")
async def reset_db():
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text('DELETE FROM invoices'))
        conn.execute(text('DELETE FROM waybills'))
        conn.execute(text('DELETE FROM alerts'))
        conn.execute(text('DELETE FROM weigh_tickets'))
        conn.execute(text('DELETE FROM vehicles'))
        conn.execute(text('DELETE FROM processed_mails'))  # restart güvenliği için
        conn.commit()
    logger.info("🗑️ DB temizlendi (admin)")
    return {"status": "temizlendi"}


# ── STARTUP RECOVERY ──────────────────────────────────────────────────────
async def startup_recovery():
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus

        bekleyenler = db.query(WeighTicket).filter(
            WeighTicket.status == TicketStatus.IRSALIYE_OLUSTURULUYOR
        ).all()

        if bekleyenler:
            logger.warning(f"⚠️ {len(bekleyenler)} yarım kalan işlem tespit edildi")
            for ticket in bekleyenler:
                logger.info(f"Recovery: ticket_id={ticket.id} plaka={ticket.plaka}")
        else:
            logger.info("✅ Yarım kalan işlem yok")

    except Exception as e:
        logger.error(f"Startup recovery hatası: {e}")
    finally:
        db.close()


# ── MAIL LİSTENER ────────────────────────────────────────────────────────
async def mail_listener_baslat():
    import threading
    from app.mail.listener import gelen_kutu_dinle

    thread = threading.Thread(target=gelen_kutu_dinle, args=(60,), daemon=True)
    thread.start()
    logger.info("📬 Mail dinleyici bağımsız bir kanalda (Thread) başlatıldı.")