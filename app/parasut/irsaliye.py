# irsaliye.py — Paraşüt e-İrsaliye Modülü

from datetime import datetime
from app.parasut.client import parasut_post
from app.core.config import (
    PARASUT_COMPANY_ID,
    PARASUT_HURDA_PRODUCT_ID,
    PARASUT_CONTACT_IDS,
)
from app.core.database import SessionLocal, WeighTicket, Waybill, TicketStatus
from app.core.logger import logger


def _contact_id_bul(firma_adi: str) -> str:
    """Firma adına göre Paraşüt cari ID'sini döndürür."""
    if not firma_adi:
        varsayilan = str(list(PARASUT_CONTACT_IDS.values())[0])
        logger.warning(f"Firma adı yok, varsayılan cari kullanılıyor: {varsayilan}")
        return varsayilan

    firma_upper = firma_adi.upper().strip()

    for anahtar, cid in PARASUT_CONTACT_IDS.items():
        if anahtar.upper() in firma_upper or firma_upper in anahtar.upper():
            logger.info(f"Firma eşleşti: {firma_adi} → {anahtar} (ID: {cid})")
            return str(cid)

    # Eşleşme yoksa ilk kayıt (UHT Ufuk Hurda) varsayılan
    varsayilan = str(list(PARASUT_CONTACT_IDS.values())[0])
    logger.warning(f"Firma bulunamadı: {firma_adi}, varsayılan cari kullanılıyor: {varsayilan}")
    return varsayilan


def irsaliye_olustur(ticket_id: int) -> dict:
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
        contact_id = _contact_id_bul(ticket.firma)

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
                            "id": contact_id
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