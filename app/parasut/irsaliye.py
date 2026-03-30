# irsaliye.py — Paraşüt e-İrsaliye Modülü
#
# SORUMLULUK:
# 1. Kantar fişi verisinden Paraşüt'te e-İrsaliye oluşturur
# 2. Oluşturulan irsaliyenin ID'sini DB'ye kaydeder
# 3. Status'u IRSALIYE_TAMAMLANDI yapar (fabrika onayı beklenmez artık)
#
# AKIŞ:
# webhook.py → fis_isle() → irsaliye_olustur() → Paraşüt API → DB güncelle → birim fiyat sor

from datetime import datetime
from app.parasut.client import parasut_post
from app.core.config import (
    PARASUT_COMPANY_ID,
    PARASUT_DEMO_CONTACT_ID,
    PARASUT_HURDA_PRODUCT_ID,
)
from app.core.database import SessionLocal, WeighTicket, Waybill, TicketStatus
from app.core.logger import logger


def irsaliye_olustur(ticket_id: int) -> dict:
    """
    Verilen ticket_id için Paraşüt'te e-İrsaliye oluşturur.
    Fabrika onay maili artık gönderilmez — direkt birim fiyat sorulacak.
    """
    db = SessionLocal()
    try:
        ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()

        if not ticket:
            logger.error(f"Ticket bulunamadı: {ticket_id}")
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        logger.info(f"İrsaliye oluşturuluyor: ticket_id={ticket_id} plaka={ticket.plaka}")

        ticket.status = TicketStatus.IRSALIYE_OLUSTURULUYOR
        db.commit()

        tarih_str = _tarih_formatla(ticket.fis_tarihi)

        istek_verisi = {
            "data": {
                "type": "shipment_documents",
                "attributes": {
                    "description": f"Kantar Fişi — {ticket.plaka} — {ticket.agirlik_kg} kg",
                    "issue_date": tarih_str,
                    "inflow": False,
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
        logger.info(f"✅ Paraşüt irsaliye oluşturuldu: parasut_id={parasut_id}")

        waybill = Waybill(
            irsaliye_no=str(parasut_id),
            parasut_irsaliye_id=str(parasut_id),
            status=TicketStatus.IRSALIYE_TAMAMLANDI,
            ticket_id=ticket_id
        )
        db.add(waybill)
        db.commit()
        db.refresh(waybill)

        logger.info(f"Waybill kaydedildi: waybill_id={waybill.id}")

        # Ticket status güncelle
        ticket.status = TicketStatus.IRSALIYE_TAMAMLANDI
        db.commit()

        logger.info(f"✅ İrsaliye tamamlandı: parasut_id={parasut_id} waybill_id={waybill.id}")

        return {
            "basarili": True,
            "irsaliye_no": str(parasut_id),
            "parasut_id": str(parasut_id),
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