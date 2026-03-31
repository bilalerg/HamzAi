# fatura.py — Paraşüt Satış Faturası Modülü
#
# SORUMLULUK:
# Birim fiyat alındıktan sonra Paraşüt'te satış faturası keser.
#
# AKIŞ:
# webhook.py → birim fiyat geldi → fatura_kes(waybill_id, birim_fiyat) → Paraşüt API → DB güncelle → WP bildir

from datetime import datetime
from app.parasut.client import parasut_post
from app.core.config import (
    PARASUT_COMPANY_ID,
    PARASUT_DEMO_CONTACT_ID,
    PARASUT_HURDA_PRODUCT_ID
)
from app.core.database import SessionLocal, Waybill, Invoice, WeighTicket, TicketStatus
from app.core.logger import logger
from app.chatbox.wp_sender import patrona_bildir, muhasebeciye_bildir


def fatura_kes(waybill_id: int, birim_fiyat: float = 0) -> dict:
    """
    Birim fiyat alındıktan sonra Paraşüt'te satış faturası keser.

    birim_fiyat: TL/kg cinsinden birim fiyat (şoförden alınır)
    """
    db = SessionLocal()
    try:
        waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()

        if not waybill:
            logger.error(f"Waybill bulunamadı: {waybill_id}")
            return {"basarili": False, "hata": "waybill_bulunamadi"}

        ticket = db.query(WeighTicket).filter(
            WeighTicket.id == waybill.ticket_id
        ).first()

        if not ticket:
            logger.error(f"Ticket bulunamadı: {waybill.ticket_id}")
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        logger.info(f"Fatura kesiliyor: waybill_id={waybill_id} plaka={ticket.plaka} birim_fiyat={birim_fiyat}")

        waybill.status = TicketStatus.FATURA_KESILIYOR
        ticket.status = TicketStatus.FATURA_KESILIYOR
        db.commit()

        bugun = datetime.now().strftime("%Y-%m-%d")
        fis_tarihi = ticket.fis_tarihi.strftime("%Y-%m-%d") if ticket.fis_tarihi else bugun

        # Toplam tutar = ağırlık (kg) × birim fiyat (TL/kg)
        toplam_tutar = (ticket.agirlik_kg or 0) * birim_fiyat

        istek_verisi = {
            "data": {
                "type": "sales_invoices",
                "attributes": {
                    "item_type": "invoice",
                    "description": f"Hurda Alım — {ticket.plaka} — {ticket.agirlik_kg} kg — {birim_fiyat:,.0f} TL/kg",
                    "issue_date": fis_tarihi,
                    "due_date": bugun,
                    "currency": "TRL"
                },
                "relationships": {
                    "contact": {
                        "data": {
                            "type": "contacts",
                            "id": str(PARASUT_DEMO_CONTACT_ID)
                        }
                    },
                    "details": {
                        "data": [
                            {
                                "type": "sales_invoice_details",
                                "attributes": {
                                    "quantity": ticket.agirlik_kg or 1,
                                    "unit_price": str(birim_fiyat),
                                    "vat_rate": 0,
                                    "description": f"{ticket.plaka} — {ticket.agirlik_kg} kg hurda malzeme — {birim_fiyat:,.0f} TL/kg"
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

        sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek_verisi)

        parasut_id = sonuc["data"]["id"]
        fatura_no = parasut_id
        logger.info(f"✅ Paraşüt fatura oluşturuldu: parasut_id={parasut_id}")

        invoice = Invoice(
            fatura_no=str(fatura_no),
            parasut_id=str(parasut_id),
            tutar=int(toplam_tutar),
            birim_fiyat=int(birim_fiyat),
            parasut_response=str(sonuc),
            status=TicketStatus.TAMAMLANDI,
            waybill_id=waybill_id
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        logger.info(f"Invoice kaydedildi: invoice_id={invoice.id}")

        waybill.status = TicketStatus.TAMAMLANDI
        ticket.status = TicketStatus.TAMAMLANDI
        db.commit()

        logger.info(f"✅ Fatura tamamlandı: fatura_no={fatura_no} tutar={toplam_tutar:,.0f} TL")

        # Patrona ve muhasebeciye bildir
        patrona_bildir(
            f"✅ Fatura kesildi!\n"
            f"🚗 Plaka: {ticket.plaka}\n"
            f"⚖️ Ağırlık: {ticket.agirlik_kg:,} kg\n"
            f"💰 Birim Fiyat: {birim_fiyat:,.0f} TL/kg\n"
            f"💵 Toplam: {toplam_tutar:,.0f} TL\n"
            f"🧾 Fatura No: {fatura_no}"
        )

        muhasebeciye_bildir(
            f"✅ İşlem tamamlandı!\n"
            f"🚗 Plaka: {ticket.plaka}\n"
            f"💰 Birim: {birim_fiyat:,.0f} TL/kg\n"
            f"💵 Toplam: {toplam_tutar:,.0f} TL\n"
            f"🧾 Fatura No: {fatura_no}"
        )

        return {
            "basarili": True,
            "fatura_no": str(fatura_no),
            "parasut_id": str(parasut_id),
            "invoice_id": invoice.id,
            "toplam_tutar": toplam_tutar
        }

    except Exception as e:
        logger.error(f"Fatura kesme hatası: {e}")
        try:
            waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
            ticket = db.query(WeighTicket).filter(
                WeighTicket.id == waybill.ticket_id
            ).first() if waybill else None
            if waybill:
                waybill.status = TicketStatus.HATA
            if ticket:
                ticket.status = TicketStatus.HATA
                ticket.hata_mesaji = str(e)
            db.commit()
        except:
            pass
        return {"basarili": False, "hata": str(e)}

    finally:
        db.close()