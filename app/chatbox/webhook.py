# webhook.py — Twilio WhatsApp Webhook Handler

import base64
import httpx
from fastapi import APIRouter, Request, Form
from fastapi.responses import PlainTextResponse
from twilio.twiml.messaging_response import MessagingResponse
from app.core.logger import logger
from app.core.database import SessionLocal
from app.chatbox.wp_sender import tirciya_bildir

router = APIRouter()


# ── YARDIMCI: FOTOĞRAFI İNDİR ────────────────────────────────────────────
async def fotografi_indir(media_url: str, account_sid: str, auth_token: str) -> bytes:
    # Twilio'dan gelen fotoğrafı indirir
    # Twilio media URL'leri auth gerektiriyor — basic auth ile çekiyoruz
    async with httpx.AsyncClient() as client:
        response = await client.get(
            media_url,
            auth=(account_sid, auth_token)
        )
        return response.content


# ── ANA WEBHOOK: WHATSAPP MESAJI GELDİ ───────────────────────────────────
@router.post("/webhook/whatsapp", response_class=PlainTextResponse)
async def whatsapp_webhook(
    request: Request,
    From: str = Form(default=""),
    Body: str = Form(default=""),
    NumMedia: str = Form(default="0"),
    MediaUrl0: str = Form(default=""),
    MediaContentType0: str = Form(default=""),
):
    """
    Twilio her WhatsApp mesajında bu endpoint'i çağırır.

    PARAMETRELER:
    From         → Gönderenin numarası (whatsapp:+905XXXXXXXXX)
    Body         → Mesaj metni
    NumMedia     → Kaç medya dosyası var
    MediaUrl0    → İlk medya dosyasının URL'si
    MediaContentType0 → Dosya tipi (image/jpeg vs)

    NEDEN PlainTextResponse?
    Twilio TwiML formatında cevap bekliyor.
    TwiML = Twilio Markup Language — basit XML formatı.
    """

    logger.info(f"📱 WP mesajı geldi: {From} | Medya: {NumMedia}")

    resp = MessagingResponse()

    # ── FOTOĞRAF VAR MI? ─────────────────────────────────────────────────
    if int(NumMedia) > 0 and MediaContentType0.startswith("image/"):
        logger.info(f"📸 Fotoğraf tespit edildi: {MediaContentType0}")

        try:
            from app.core.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN

            # Fotoğrafı indir
            fotograf_bytes = await fotografi_indir(
                MediaUrl0,
                TWILIO_ACCOUNT_SID,
                TWILIO_AUTH_TOKEN
            )

            # Geçici dosyaya kaydet
            import tempfile
            import os
            suffix = ".jpg" if "jpeg" in MediaContentType0 else ".png"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(fotograf_bytes)
                tmp_path = tmp.name

            logger.info(f"Fotoğraf kaydedildi: {tmp_path}")

            # Tırçıya "alındı" mesajı gönder
            tirciya_bildir(From, "✅ Fişiniz alındı, işleniyor...")

            # OCR pipeline'ı arka planda başlat
            import asyncio
            asyncio.create_task(
                fis_isle(tmp_path, From)
            )

            resp.message("✅ Fişiniz alındı, işleniyor...")

        except Exception as e:
            logger.error(f"Fotoğraf işleme hatası: {e}")
            resp.message("❌ Bir hata oluştu, lütfen tekrar deneyin.")

    # ── SADECE METİN GELDİ ───────────────────────────────────────────────
    elif Body:
        logger.info(f"Metin mesajı: {Body}")
        resp.message("Merhaba! Kantar fişi fotoğrafı gönderin, gerisini hallederim. 🤖")

    else:
        logger.warning("Boş mesaj geldi")

    return str(resp)


# ── ARKA PLAN: FİŞ İŞLE ──────────────────────────────────────────────────
async def fis_isle(foto_path: str, gonderen: str):
    """
    Fotoğrafı OCR ile okur, tüm pipeline'ı başlatır.
    Arka planda çalışır — webhook hemen cevap verebilir.

    NEDEN ARKA PLANDA?
    OCR işlemi 5-10 saniye sürebilir.
    Twilio webhook'u 15 saniye içinde cevap bekliyor.
    Arka planda işleyip hemen cevap veriyoruz.
    """
    db = SessionLocal()
    try:
        from app.ocr.reader import fis_oku, fis_db_kaydet
        from app.ocr.plaka_kontrol import plaka_kontrol_et, PlakaSonuc

        # 1. OCR oku
        logger.info(f"OCR başlatılıyor: {foto_path}")
        sonuc = fis_oku(foto_path)

        if sonuc.hata:
            logger.error(f"OCR hatası: {sonuc.hata}")
            tirciya_bildir(gonderen, f"❌ Fiş okunamadı: {sonuc.hata}")
            return

        # 2. DB'ye kaydet
        ticket = fis_db_kaydet(sonuc, foto_path, db)
        logger.info(f"Fiş kaydedildi: ticket_id={ticket.id}")

        # 3. Plaka kontrolü
        plaka_sonuc = plaka_kontrol_et(sonuc.plaka, ticket.id, db)

        if plaka_sonuc == PlakaSonuc.DUPLICATE:
            tirciya_bildir(gonderen, f"⚠️ Bu araç bugün daha önce geldi: {sonuc.plaka}")
            return

        # 4. Tırçıya bilgi ver
        tirciya_bildir(
            gonderen,
            f"✅ Fiş okundu!\n"
            f"🚗 Plaka: {sonuc.plaka}\n"
            f"⚖️ Ağırlık: {sonuc.net_agirlik_kg:,} kg\n"
            f"📅 Tarih: {sonuc.tarih}\n"
            f"İrsaliye oluşturuluyor..."
        )

        # 5. Paraşüt irsaliye (Faz 4'te eklenecek)
        logger.info("Paraşüt irsaliye adımı Faz 4'te eklenecek")

    except Exception as e:
        logger.error(f"Fiş işleme hatası: {e}")
        tirciya_bildir(gonderen, "❌ İşlem sırasında hata oluştu.")
    finally:
        db.close()
        # Geçici dosyayı sil
        import os
        try:
            os.remove(foto_path)
        except:
            pass