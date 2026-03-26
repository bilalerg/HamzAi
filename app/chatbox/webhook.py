# app/chatbox/webhook.py — Twilio WhatsApp Webhook Handler

import asyncio
import tempfile
import os
import httpx
from fastapi import APIRouter, Request, Form, BackgroundTasks
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse
from starlette.concurrency import run_in_threadpool
from app.core.logger import logger
from app.core.database import SessionLocal
from app.chatbox.wp_sender import tirciya_bildir

router = APIRouter()


# ── YARDIMCI: FOTOĞRAFI İNDİR ────────────────────────────────────────────
async def fotografi_indir(media_url: str, account_sid: str, auth_token: str) -> bytes:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            media_url,
            auth=(account_sid, auth_token),
            follow_redirects=True
        )
        return response.content


# ── ANA WEBHOOK: WHATSAPP MESAJI GELDİ ───────────────────────────────────
@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
        request: Request,
        background_tasks: BackgroundTasks,  # <-- EKLENDİ: Arka plan görevleri için
        From: str = Form(default=""),
        Body: str = Form(default=""),
        NumMedia: str = Form(default="0"),
        MediaUrl0: str = Form(default=""),
        MediaContentType0: str = Form(default=""),
):
    logger.info(f"📱 WP mesajı geldi: {From} | Medya: {NumMedia}")

    resp = MessagingResponse()

    if int(NumMedia) > 0 and MediaContentType0.startswith("image/"):
        logger.info(f"📸 Fotoğraf tespit edildi: {MediaContentType0}")

        try:
            from app.core.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN

            fotograf_bytes = await fotografi_indir(
                MediaUrl0,
                TWILIO_ACCOUNT_SID,
                TWILIO_AUTH_TOKEN
            )

            suffix = ".jpg" if "jpeg" in MediaContentType0 else ".png"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(fotograf_bytes)
                tmp_path = tmp.name

            logger.info(f"Fotoğraf kaydedildi: {tmp_path} ({os.path.getsize(tmp_path)} bytes)")

            # <-- DÜZELTME 1: Twilio senkron olduğu için threadpool'a attık (Sistemi kilitlemez)
            await run_in_threadpool(tirciya_bildir, From, "✅ Fişiniz alındı, analiz ediliyor...")

            # <-- DÜZELTME 2: Ağır OCR ve Paraşüt işlemlerini background_tasks'a verdik
            background_tasks.add_task(fis_isle, tmp_path, From)

        except Exception as e:
            logger.error(f"Fotoğraf işleme hatası: {e}")
            resp.message("❌ Bir hata oluştu, lütfen tekrar deneyin.")

    elif Body:
        logger.info(f"Metin mesajı: {Body}")
        resp.message("Merhaba! Kantar fişi fotoğrafı gönderin, gerisini hallederim. 🤖")

    else:
        logger.warning("Boş mesaj geldi")

    return Response(content=str(resp), media_type="text/xml")


# ── ARKA PLAN: FİŞ İŞLE ──────────────────────────────────────────────────
# <-- DÜZELTME 3: "async def" başındaki "async" kelimesini sildik!
# Çünkü içindeki veritabanı, OCR ve Paraşüt işlemleri zaten senkron.
def fis_isle(foto_path: str, gonderen: str):
    db = SessionLocal()
    try:
        from app.ocr.reader import fis_oku, fis_db_kaydet
        from app.ocr.plaka_kontrol import plaka_kontrol_et, PlakaSonuc
        from app.parasut.irsaliye import irsaliye_olustur  # <-- Paraşüt entegrasyonu eklendi

        logger.info(f"OCR başlatılıyor: {foto_path}")
        sonuc = fis_oku(foto_path)

        if sonuc.hata:
            logger.error(f"OCR hatası: {sonuc.hata}")
            tirciya_bildir(gonderen, f"❌ Fiş okunamadı: {sonuc.hata}")
            return

        ticket = fis_db_kaydet(sonuc, foto_path, db)
        logger.info(f"Fiş kaydedildi: ticket_id={ticket.id}")

        plaka_sonuc = plaka_kontrol_et(sonuc.plaka, ticket.id, db)

        if plaka_sonuc == PlakaSonuc.DUPLICATE:
            tirciya_bildir(gonderen, f"⚠️ Bu araç bugün daha önce geldi: {sonuc.plaka}")
            return

        tirciya_bildir(
            gonderen,
            f"✅ Fiş okundu!\n"
            f"🚗 Plaka: {sonuc.plaka}\n"
            f"⚖️ Ağırlık: {sonuc.net_agirlik_kg:,} kg\n"
            f"📅 Tarih: {sonuc.tarih}\n"
            f"İrsaliye oluşturuluyor..."
        )

        # <-- DÜZELTME 4: Paraşüt e-İrsaliye adımını tetikledik!
        logger.info(f"Paraşüt irsaliye adımı başlatılıyor: ticket_id={ticket.id}")
        irsaliye_sonuc = irsaliye_olustur(ticket.id)

        if irsaliye_sonuc.get("basarili"):
            logger.info("✅ İrsaliye başarıyla oluşturuldu.")
            tirciya_bildir(
                gonderen,
                f"✅ İrsaliye Paraşüt'te oluşturuldu!\n"
                f"📄 İrsaliye No: {irsaliye_sonuc.get('irsaliye_no')}\n\n"
                f"Fabrika onayı bekleniyor..."
            )
        else:
            logger.error(f"❌ İrsaliye oluşturulamadı: {irsaliye_sonuc.get('hata')}")
            tirciya_bildir(gonderen, f"❌ İrsaliye oluşturulamadı: Lütfen muhasebeye bilgi verin.")

    except Exception as e:
        logger.error(f"Fiş işleme hatası: {e}")
        tirciya_bildir(gonderen, "❌ İşlem sırasında bir hata oluştu.")
    finally:
        db.close()
        try:
            os.remove(foto_path)
        except:
            pass