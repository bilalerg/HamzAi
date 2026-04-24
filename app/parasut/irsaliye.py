# irsaliye.py — Paraşüt e-İrsaliye Modülü

import json
from datetime import datetime
from app.parasut.client import parasut_post, cari_id_bul
from app.core.config import PARASUT_COMPANY_ID, PARASUT_HURDA_PRODUCT_ID
from app.core.database import SessionLocal, WeighTicket, Waybill, TicketStatus
from app.core.logger import logger


def irsaliye_olustur(ticket_id: int, beklet: bool = False) -> dict:
    db = SessionLocal()
    try:
        ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()

        if not ticket:
            logger.error(f"Ticket bulunamadi: {ticket_id}")
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        logger.info(f"Irsaliye olusturuluyor: ticket_id={ticket_id} plaka={ticket.plaka} beklet={beklet}")

        ticket.status = TicketStatus.IRSALIYE_OLUSTURULUYOR
        db.commit()

        tarih_str = _tarih_formatla(ticket.fis_tarihi)

        contact_id = cari_id_bul(ticket.firma)
        if not contact_id:
            logger.warning(f"Cari bulunamadi: {ticket.firma}, ilk cari kullaniliyor")
            from app.parasut.client import cari_listesi_getir
            cariler = cari_listesi_getir()
            contact_id = cariler[0]["id"] if cariler else None

        if not contact_id:
            return {"basarili": False, "hata": "Cari bulunamadi"}

        attributes = {
            "description": f"Kantar Fisi — {ticket.plaka} — {ticket.agirlik_kg} kg",
            "issue_date": tarih_str,
            "inflow": False,
            "currency": "TRL",
        }
        if ticket.plaka:
            attributes["carrier_license_plate"] = ticket.plaka

        # Malzeme bazli stock_movements olustur
        malzemeler = {}
        if ticket.malzemeler:
            try:
                malzemeler = json.loads(ticket.malzemeler)
            except:
                pass

        if malzemeler:
            stock_movements = []
            for malzeme_adi, kg in malzemeler.items():
                if kg > 0:
                    stock_movements.append({
                        "type": "stock_movements",
                        "attributes": {
                            "quantity": kg,
                            "description": f"{ticket.plaka} — {malzeme_adi} — {kg:,} kg"
                        },
                        "relationships": {
                            "product": {
                                "data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}
                            }
                        }
                    })
            logger.info(f"Malzeme bazli irsaliye: {len(stock_movements)} kalem")
        else:
            stock_movements = [{
                "type": "stock_movements",
                "attributes": {
                    "quantity": ticket.agirlik_kg or 1,
                    "description": f"{ticket.plaka} — hurda malzeme"
                },
                "relationships": {
                    "product": {
                        "data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}
                    }
                }
            }]

        istek_verisi = {
            "data": {
                "type": "shipment_documents",
                "attributes": attributes,
                "relationships": {
                    "contact": {
                        "data": {"type": "contacts", "id": str(contact_id)}
                    },
                    "stock_movements": {
                        "data": stock_movements
                    }
                }
            }
        }

        sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/shipment_documents", istek_verisi)
        parasut_id = sonuc["data"]["id"]
        logger.info(f"Parasut irsaliye olusturuldu: parasut_id={parasut_id}")

        yeni_status = TicketStatus.FABRIKA_BEKLENIYOR if beklet else TicketStatus.IRSALIYE_TAMAMLANDI

        waybill = Waybill(
            irsaliye_no=str(parasut_id),
            parasut_irsaliye_id=str(parasut_id),
            status=yeni_status,
            ticket_id=ticket_id
        )
        db.add(waybill)
        db.commit()
        db.refresh(waybill)

        ticket.status = yeni_status
        db.commit()

        logger.info(f"Irsaliye tamamlandi: parasut_id={parasut_id} waybill_id={waybill.id} status={yeni_status}")

        return {
            "basarili": True,
            "irsaliye_no": str(parasut_id),
            "parasut_id": str(parasut_id),
            "waybill_id": waybill.id,
            "bekliyor_onay": beklet
        }

    except Exception as e:
        logger.error(f"Irsaliye olusturma hatasi: {e}")
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


def irsaliye_onayla(waybill_id: int) -> dict:
    db = SessionLocal()
    try:
        waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
        if not waybill:
            return {"basarili": False, "hata": "waybill_bulunamadi"}

        if waybill.status != TicketStatus.FABRIKA_BEKLENIYOR:
            return {"basarili": False, "hata": f"Bu irsaliye onay beklemiyor (status: {waybill.status})"}

        waybill.status = TicketStatus.IRSALIYE_TAMAMLANDI
        ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first()
        if ticket:
            ticket.status = TicketStatus.IRSALIYE_TAMAMLANDI
        db.commit()

        logger.info(f"Irsaliye onaylandi: waybill_id={waybill_id}")
        return {"basarili": True, "waybill_id": waybill_id}

    except Exception as e:
        logger.error(f"Irsaliye onay hatasi: {e}")
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