# fatura.py — Paraşüt Satış Faturası Modülü
#
# SORUMLULUK:
# Fabrika onayı gelince Paraşüt'te satış faturası keser.
#
# AKIŞ:
# parser.py → ONAY → fatura_kes() → Paraşüt API → DB güncelle → WP bildir

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


def fatura_kes(waybill_id: int) -> dict:
    """
    Onaylanan irsaliye için Paraşüt'te satış faturası keser.

    ADIMLAR:
    1. DB'den waybill ve ticket bilgilerini al
    2. Paraşüt'e satış faturası isteği gönder
    3. Invoice kaydı oluştur (DB)
    4. Status TAMAMLANDI yap
    5. Patrona WP bildirimi gönder

    Döndürür:
    {
        "basarili": True/False,
        "fatura_no": "...",
        "parasut_id": "..."
    }
    """
    db = SessionLocal()
    try:
        # ── ADIM 1: Waybill ve ticket bilgilerini al ──────────────────
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

        logger.info(f"Fatura kesiliyor: waybill_id={waybill_id} plaka={ticket.plaka}")

        # Status güncelle — işlem başladı
        waybill.status = TicketStatus.FATURA_KESILIYOR
        ticket.status = TicketStatus.FATURA_KESILIYOR
        db.commit()

        # ── ADIM 2: Paraşüt'e satış faturası isteği gönder ───────────
        # sales_invoices endpoint'i
        # item_type: "invoice" → normal satış faturası
        # due_date: vade tarihi — bugün olarak ayarlıyoruz
        bugun = datetime.now().strftime("%Y-%m-%d")
        fis_tarihi = ticket.fis_tarihi.strftime("%Y-%m-%d") if ticket.fis_tarihi else bugun

        istek_verisi = {
            "data": {
                "type": "sales_invoices",
                "attributes": {
                    "item_type": "invoice",
                    "description": f"Hurda Alım — {ticket.plaka} — {ticket.agirlik_kg} kg — {waybill.ref_kodu}",
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
                                    "unit_price": "0",
                                    "vat_rate": 0,
                                    "description": f"{ticket.plaka} — {ticket.agirlik_kg} kg hurda malzeme"
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

        # ── ADIM 3: Invoice kaydı oluştur ────────────────────────────
        invoice = Invoice(
            fatura_no=str(fatura_no),
            parasut_id=str(parasut_id),
            parasut_response=str(sonuc),
            status=TicketStatus.TAMAMLANDI,
            waybill_id=waybill_id
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        logger.info(f"Invoice kaydedildi: invoice_id={invoice.id}")

        # ── ADIM 4: Status TAMAMLANDI yap ────────────────────────────
        waybill.status = TicketStatus.TAMAMLANDI
        ticket.status = TicketStatus.TAMAMLANDI
        db.commit()

        logger.info(f"✅ Fatura tamamlandı: fatura_no={fatura_no}")

        # ── ADIM 5: Patrona WP bildirimi ─────────────────────────────
        patrona_bildir(
            f"✅ Fatura kesildi!\n"
            f"🚗 Plaka: {ticket.plaka}\n"
            f"⚖️ Ağırlık: {ticket.agirlik_kg:,} kg\n"
            f"📄 Ref: {waybill.ref_kodu}\n"
            f"🧾 Fatura No: {fatura_no}"
        )

        muhasebeciye_bildir(
            f"✅ İşlem tamamlandı!\n"
            f"🚗 Plaka: {ticket.plaka}\n"
            f"📄 Ref: {waybill.ref_kodu}\n"
            f"🧾 Fatura No: {fatura_no}"
        )

        return {
            "basarili": True,
            "fatura_no": str(fatura_no),
            "parasut_id": str(parasut_id),
            "invoice_id": invoice.id
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