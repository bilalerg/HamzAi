# app/chatbox/webhook.py — Twilio WhatsApp Webhook Handler

import asyncio
import tempfile
import os
import re
import httpx
from fastapi import APIRouter, Request, Form, BackgroundTasks
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse
from starlette.concurrency import run_in_threadpool
from app.core.logger import logger
from app.core.database import SessionLocal
from app.chatbox.wp_sender import tirciya_bildir

router = APIRouter()

# Birim fiyat beklenen şoförler — telefon → waybill_id
# Şoför fiyat yazana kadar burada bekler
fiyat_bekleniyor = {}


# ── YARDIMCI: FOTOĞRAFI İNDİR ────────────────────────────────────────────
async def fotografi_indir(media_url: str, account_sid: str, auth_token: str) -> bytes:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            media_url,
            auth=(account_sid, auth_token),
            follow_redirects=True
        )
        return response.content


# ── YARDIMCI: METİNDEN BİRİM FİYAT ÇIKAR ────────────────────────────────
def birim_fiyat_cikar(metin: str) -> float | None:
    """
    Birim fiyat parse eder. Kurallar:
    - "14850"    → 14850.0  (tam sayı)
    - "14.85"    → 14.85    (ondalıklı, TL kuruş)
    - "14,85"    → 14.85    (virgüllü ondalıklı)
    - "14.850"   → 14850.0  (nokta binlik ayraç — 3 hane sonra)
    - "14850 tl" → 14850.0
    """
    metin = metin.strip().lower()
    metin = metin.replace("tl", "").replace("lira", "").replace("₺", "").strip()

    # Virgülü noktaya çevir
    metin = metin.replace(",", ".")

    # Nokta sayısına göre karar ver
    nokta_sayisi = metin.count(".")
    if nokta_sayisi == 1:
        # Tek nokta: ondalık mı binlik mi?
        parca = metin.split(".")
        if len(parca[1]) == 3:
            # "14.850" → binlik ayraç → 14850
            metin = metin.replace(".", "")
        # else: "14.85" → ondalıklı, olduğu gibi bırak
    elif nokta_sayisi > 1:
        # Birden fazla nokta: binlik ayraç, hepsini kaldır
        metin = metin.replace(".", "")

    try:
        fiyat = float(metin)
        if fiyat > 0:
            return fiyat
    except:
        pass
    return None


# ── ANA WEBHOOK: WHATSAPP MESAJI GELDİ ───────────────────────────────────
@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        From: str = Form(default=""),
        Body: str = Form(default=""),
        NumMedia: str = Form(default="0"),
        MediaUrl0: str = Form(default=""),
        MediaContentType0: str = Form(default=""),
):
    logger.info(f"📱 WP mesajı geldi: {From} | Medya: {NumMedia}")

    resp = MessagingResponse()

    # ── Fotoğraf geldi → fiş işle ────────────────────────────────────────
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

            await run_in_threadpool(tirciya_bildir, From, "✅ Fişiniz alındı, analiz ediliyor...")
            background_tasks.add_task(fis_isle, tmp_path, From)

        except Exception as e:
            logger.error(f"Fotoğraf işleme hatası: {e}")
            resp.message("❌ Bir hata oluştu, lütfen tekrar deneyin.")

    # ── Metin geldi → birim fiyat mı yoksa normal mesaj mı? ──────────────
    elif Body:
        logger.info(f"Metin mesajı: {Body}")

        # Bu şoförden birim fiyat bekleniyor mu?
        if From in fiyat_bekleniyor:
            fiyat = birim_fiyat_cikar(Body)

            if fiyat is not None:
                # Geçerli fiyat geldi — fatura kes
                waybill_id = fiyat_bekleniyor.pop(From)
                logger.info(f"💰 Birim fiyat alındı: {fiyat} TL | waybill_id={waybill_id}")
                await run_in_threadpool(tirciya_bildir, From, f"💰 Birim fiyat alındı: {fiyat:,.0f} TL\nFatura kesiliyor...")
                background_tasks.add_task(fatura_kes_gorev, waybill_id, fiyat, From)
            else:
                # Geçersiz fiyat — tekrar sor
                tirciya_bildir(From,
                    "⚠️ Geçersiz fiyat formatı.\n"
                    "Lütfen sadece rakam yazın.\n"
                    "Örnek: 4500"
                )
        else:
            resp.message("Merhaba! Kantar fişi fotoğrafı gönderin, gerisini hallederim. 🤖")

    else:
        logger.warning("Boş mesaj geldi")

    return Response(content=str(resp), media_type="text/xml")


# ── ARKA PLAN: FİŞ İŞLE ──────────────────────────────────────────────────
def fis_isle(foto_path: str, gonderen: str):
    db = SessionLocal()
    try:
        from app.ocr.reader import fis_oku, fis_db_kaydet
        from app.ocr.plaka_kontrol import plaka_kontrol_et, PlakaSonuc
        from app.parasut.irsaliye import irsaliye_olustur

        logger.info(f"OCR başlatılıyor: {foto_path}")
        sonuc = fis_oku(foto_path)

        if sonuc.hata:
            logger.error(f"OCR hatası: {sonuc.hata}")
            tirciya_bildir(gonderen, f"❌ Fiş okunamadı: {sonuc.hata}")
            return

        ticket = fis_db_kaydet(sonuc, foto_path, db)
        logger.info(f"Fiş kaydedildi: ticket_id={ticket.id}")

        plaka_sonuc = plaka_kontrol_et(sonuc.plaka, ticket.id, db, fis_no=sonuc.fis_no)

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

        irsaliye_sonuc = irsaliye_olustur(ticket.id)

        if not irsaliye_sonuc.get("basarili"):
            logger.error(f"❌ İrsaliye oluşturulamadı: {irsaliye_sonuc.get('hata')}")
            tirciya_bildir(gonderen, "❌ İrsaliye oluşturulamadı. Lütfen muhasebeye bilgi verin.")
            return

        waybill_id = irsaliye_sonuc.get("waybill_id")
        logger.info(f"✅ İrsaliye oluşturuldu: waybill_id={waybill_id}")

        # Birim fiyat bekleme listesine ekle
        fiyat_bekleniyor[gonderen] = waybill_id

        tirciya_bildir(
            gonderen,
            f"✅ İrsaliye Paraşüt'te oluşturuldu!\n"
            f"📄 İrsaliye No: {irsaliye_sonuc.get('irsaliye_no')}\n\n"
            f"💰 Fatura kesmek için birim fiyatı yazın (TL/kg):\n"
            f"Örnek: 4500"
        )

    except Exception as e:
        logger.error(f"Fiş işleme hatası: {e}")
        tirciya_bildir(gonderen, "❌ İşlem sırasında bir hata oluştu.")
    finally:
        db.close()
        try:
            os.remove(foto_path)
        except:
            pass


# ── ARKA PLAN: FATURA KES ─────────────────────────────────────────────────
def fatura_kes_gorev(waybill_id: int, birim_fiyat: float, gonderen: str):
    """Birim fiyat alındıktan sonra fatura keser."""
    try:
        from app.parasut.fatura import fatura_kes
        fatura_sonuc = fatura_kes(waybill_id, birim_fiyat=birim_fiyat)

        if fatura_sonuc.get("basarili"):
            logger.info(f"✅ Fatura kesildi: {fatura_sonuc.get('fatura_no')}")
            tirciya_bildir(
                gonderen,
                f"✅ Fatura kesildi!\n"
                f"🧾 Fatura No: {fatura_sonuc.get('fatura_no')}\n"
                f"💰 Birim Fiyat: {birim_fiyat:,.0f} TL/kg\n"
                f"✅ İşlem tamamlandı!"
            )
        else:
            logger.error(f"❌ Fatura kesilemedi: {fatura_sonuc.get('hata')}")
            tirciya_bildir(gonderen, "❌ Fatura kesilemedi. Lütfen muhasebeye bilgi verin.")
            # Hata durumunda tekrar fiyat beklemeye al
            fiyat_bekleniyor[gonderen] = waybill_id

    except Exception as e:
        logger.error(f"Fatura kesme hatası: {e}")
        tirciya_bildir(gonderen, "❌ Fatura kesme hatası. Lütfen muhasebeye bilgi verin.")