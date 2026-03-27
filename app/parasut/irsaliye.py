# irsaliye.py — Paraşüt e-İrsaliye Modülü
#
# SORUMLULUK:
# 1. Kantar fişi verisinden Paraşüt'te e-İrsaliye oluşturur
# 2. Oluşturulan irsaliyenin ID'sini DB'ye kaydeder
# 3. Status'u FABRIKA_BEKLENIYOR yapar
#
# AKIŞ:
# webhook.py → fis_isle() → irsaliye_olustur() → Paraşüt API → DB güncelle

from datetime import datetime
from app.parasut.client import parasut_post
from app.core.config import (
    PARASUT_COMPANY_ID,
    PARASUT_DEMO_CONTACT_ID,
    PARASUT_HURDA_PRODUCT_ID,
    FABRIKA_MAIL
)
from app.core.database import SessionLocal, WeighTicket, Waybill, TicketStatus
from app.core.logger import logger
from app.mail.sender import fabrikaya_irsaliye_gonder


def irsaliye_olustur(ticket_id: int) -> dict:
    """
    Verilen ticket_id için Paraşüt'te e-İrsaliye oluşturur.

    ADIMLAR:
    1. DB'den ticket bilgilerini al
    2. Paraşüt'e irsaliye isteği gönder
    3. Waybill kaydı oluştur (DB)
    4. Fabrikaya mail gönder
    5. Status güncelle

    Döndürür:
    {
        "basarili": True/False,
        "irsaliye_no": "...",
        "parasut_id": "...",
        "ref_kodu": "HZ-0001"
    }
    """
    db = SessionLocal()
    try:
        # ── ADIM 1: Ticket bilgilerini al ────────────────────────────
        ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()

        if not ticket:
            logger.error(f"Ticket bulunamadı: {ticket_id}")
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        logger.info(f"İrsaliye oluşturuluyor: ticket_id={ticket_id} plaka={ticket.plaka}")

        # Status güncelle — işlem başladı
        ticket.status = TicketStatus.IRSALIYE_OLUSTURULUYOR
        db.commit()

        # ── ADIM 2: Tarih formatını düzelt ───────────────────────────
        tarih_str = _tarih_formatla(ticket.fis_tarihi)

        # ── ADIM 3: Paraşüt'e irsaliye isteği gönder ─────────────────
        # inflow: True  → giriş irsaliyesi (hurda bize geliyor)
        # inflow: False → çıkış irsaliyesi
        istek_verisi = {
            "data": {
                "type": "shipment_documents",
                "attributes": {
                    "description": f"Kantar Fişi — {ticket.plaka} — {ticket.agirlik_kg} kg",
                    "issue_date": tarih_str,
                    "inflow": True,
                    "currency": "TRL"
                },
                "relationships": {
                    "contact": {
                        "data": {
                            "type": "contacts",
                            "id": str(PARASUT_DEMO_CONTACT_ID)
                        }
                    },
                    "stock_movements": {
                        "data": [
                            {
                                "type": "stock_movements",
                                "attributes": {
                                    "quantity": ticket.agirlik_kg or 1,
                                    "description": f"{ticket.plaka} — hurda malzeme"
                                },
                                "relationships": {
                                    "product": {
                                        "data": {
                                            "type": "products",
                                            "id": str(PARASUT_HURDA_PRODUCT_ID)
                                        }
                                    }
                                }
                            }
                        ]
                    }
                }
            }
        }

        sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/shipment_documents", istek_verisi)

        parasut_id = sonuc["data"]["id"]
        irsaliye_no = sonuc["data"]["attributes"].get("procurement_number", parasut_id)

        logger.info(f"✅ Paraşüt irsaliye oluşturuldu: parasut_id={parasut_id}")

        # ── ADIM 4: Waybill kaydı oluştur ────────────────────────────
        # fabrika_mail artık hardcode değil — .env'deki FABRIKA_MAIL'den geliyor
        # Canlıda sadece .env değişecek, kod aynı kalacak
        waybill = Waybill(
            irsaliye_no=str(irsaliye_no),
            parasut_irsaliye_id=str(parasut_id),
            fabrika_mail=FABRIKA_MAIL,
            status=TicketStatus.IRSALIYE_TAMAMLANDI,
            ticket_id=ticket_id
        )
        db.add(waybill)
        db.commit()
        db.refresh(waybill)

        logger.info(f"Waybill kaydedildi: waybill_id={waybill.id}")

        # ── ADIM 5: Fabrikaya mail gönder ────────────────────────────
        tarih_goster = ticket.fis_tarihi.strftime("%d.%m.%Y") if ticket.fis_tarihi else tarih_str

        ref_kodu = fabrikaya_irsaliye_gonder(
            fabrika_mail=waybill.fabrika_mail,
            waybill_id=waybill.id,
            plaka=ticket.plaka or "BILINMIYOR",
            agirlik_kg=ticket.agirlik_kg or 0,
            tarih=tarih_goster,
            irsaliye_no=str(irsaliye_no)
        )

        # Ref kodunu waybill'e kaydet — fabrika reply'ını eşleştirmek için
        waybill.ref_kodu = ref_kodu
        waybill.mail_gonderildi_at = datetime.now()
        waybill.status = TicketStatus.FABRIKA_BEKLENIYOR
        db.commit()

        # ── ADIM 6: Ticket status güncelle ───────────────────────────
        ticket.status = TicketStatus.FABRIKA_BEKLENIYOR
        db.commit()

        logger.info(f"✅ İrsaliye tamamlandı: ref={ref_kodu} parasut_id={parasut_id}")

        return {
            "basarili": True,
            "irsaliye_no": str(irsaliye_no),
            "parasut_id": str(parasut_id),
            "ref_kodu": ref_kodu,
            "waybill_id": waybill.id
        }

    except Exception as e:
        logger.error(f"İrsaliye oluşturma hatası: {e}")

        try:
            ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()
            if ticket:
                ticket.status = TicketStatus.HATA
                ticket.hata_mesaji = str(e)
                db.commit()
        except:
            pass

        return {"basarili": False, "hata": str(e)}

    finally:
        db.close()


def _tarih_formatla(fis_tarihi) -> str:
    """
    Fiş tarihini Paraşüt'ün beklediği YYYY-MM-DD formatına çevirir.
    """
    if fis_tarihi:
        try:
            if isinstance(fis_tarihi, datetime):
                return fis_tarihi.strftime("%Y-%m-%d")
            for fmt in ["%d.%m.%Y", "%Y-%m-%d"]:
                try:
                    return datetime.strptime(str(fis_tarihi), fmt).strftime("%Y-%m-%d")
                except:
                    continue
        except:
            pass

    return datetime.now().strftime("%Y-%m-%d")