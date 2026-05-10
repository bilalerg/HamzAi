# fatura.py — Paraşüt Alım Faturası (purchase_bills) Modülü
#
# DEĞİŞİKLİK: sales_invoices → purchase_bills
# Hurda ALIM işlemi olduğu için bu faturalar gider/alım faturasıdır.
# Paraşüt'te "Giderler > Fatura" kısmına düşer.

from datetime import datetime
from app.parasut.client import parasut_post, cari_id_bul, cari_listesi_getir
from app.core.config import PARASUT_COMPANY_ID, PARASUT_HURDA_PRODUCT_ID
from app.core.database import SessionLocal, Waybill, Invoice, WeighTicket, TicketStatus
from app.core.logger import logger


def _contact_id_bul(firma_adi: str) -> str:
    cid = cari_id_bul(firma_adi)
    if cid:
        return str(cid)
    cariler = cari_listesi_getir()
    if cariler:
        logger.warning(f"Cari bulunamadı: {firma_adi}, ilk cari kullanılıyor")
        return str(cariler[0]["id"])
    raise Exception("Hiç cari bulunamadı")


def fatura_kes(waybill_id: int, birim_fiyat: float = 0) -> dict:
    """Tek birim fiyatla alım faturası keser."""
    db = SessionLocal()
    try:
        waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
        if not waybill:
            return {"basarili": False, "hata": "waybill_bulunamadi"}

        ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first()
        if not ticket:
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        logger.info(f"Alım faturası kesiliyor: waybill_id={waybill_id} plaka={ticket.plaka} birim_fiyat={birim_fiyat}")

        waybill.status = TicketStatus.FATURA_KESILIYOR
        ticket.status  = TicketStatus.FATURA_KESILIYOR
        db.commit()

        bugun      = datetime.now().strftime("%Y-%m-%d")
        fis_tarihi = ticket.fis_tarihi.strftime("%Y-%m-%d") if ticket.fis_tarihi else bugun
        kg         = ticket.agirlik_kg or 0
        toplam_tutar = kg * birim_fiyat
        contact_id   = _contact_id_bul(ticket.firma)

        istek_verisi = {
            "data": {
                "type": "purchase_bills",
                "attributes": {
                    "item_type":  "purchase_bill",
                    "description": f"Hurda Alım — {ticket.plaka} — {kg} kg",
                    "issue_date": fis_tarihi,
                    "due_date":   bugun,
                    "currency":   "TRL",
                    "net_total":  toplam_tutar,
                    "total_vat":  0
                },
                "relationships": {
                    "contact": {"data": {"type": "contacts", "id": contact_id}},
                    "details": {
                        "data": [{
                            "type": "purchase_bill_details",
                            "attributes": {
                                "quantity":    kg,
                                "unit_price":  birim_fiyat,
                                "vat_rate":    0,
                                "description": f"{ticket.plaka} — {kg} kg hurda — {birim_fiyat:,.2f} TL/kg"
                            },
                            "relationships": {
                                "product": {"data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}
                            }
                        }]
                    }
                }
            }
        }

        sonuc      = parasut_post(f"/{PARASUT_COMPANY_ID}/purchase_bills", istek_verisi)
        parasut_id = sonuc["data"]["id"]

        invoice = Invoice(
            fatura_no        = str(parasut_id),
            parasut_id       = str(parasut_id),
            tutar            = int(toplam_tutar),
            birim_fiyat      = int(birim_fiyat),
            parasut_response = str(sonuc),
            status           = TicketStatus.TAMAMLANDI,
            waybill_id       = waybill_id
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        waybill.status = TicketStatus.TAMAMLANDI
        ticket.status  = TicketStatus.TAMAMLANDI
        db.commit()

        logger.info(f"✅ Alım faturası kesildi: fatura_no={parasut_id} tutar={toplam_tutar:,.0f} TL")
        return {
            "basarili":     True,
            "fatura_no":    str(parasut_id),
            "parasut_id":   str(parasut_id),
            "invoice_id":   invoice.id,
            "toplam_tutar": toplam_tutar
        }

    except Exception as e:
        logger.error(f"Alım faturası kesme hatası: {e}")
        try:
            waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
            ticket  = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first() if waybill else None
            if waybill: waybill.status = TicketStatus.HATA
            if ticket:
                ticket.status      = TicketStatus.HATA
                ticket.hata_mesaji = str(e)
            db.commit()
        except Exception:
            pass
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def fatura_kes_malzemeli(waybill_id: int, malzemeler: dict, toplanan_fiyatlar: dict) -> dict:
    """Malzeme bazlı alım faturası keser — her malzeme Paraşüt'te ayrı satır."""
    db = SessionLocal()
    try:
        waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
        if not waybill:
            return {"basarili": False, "hata": "waybill_bulunamadi"}

        ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first()
        if not ticket:
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        logger.info(f"Malzemeli alım faturası kesiliyor: waybill_id={waybill_id} plaka={ticket.plaka}")

        waybill.status = TicketStatus.FATURA_KESILIYOR
        ticket.status  = TicketStatus.FATURA_KESILIYOR
        db.commit()

        bugun      = datetime.now().strftime("%Y-%m-%d")
        fis_tarihi = ticket.fis_tarihi.strftime("%Y-%m-%d") if ticket.fis_tarihi else bugun
        contact_id = _contact_id_bul(ticket.firma)

        detaylar     = []
        toplam_tutar = 0
        for malzeme_adi, fiyat in toplanan_fiyatlar.items():
            kg = malzemeler.get(malzeme_adi, 0)
            if kg <= 0:
                continue
            tutar         = kg * fiyat
            toplam_tutar += tutar
            detaylar.append({
                "type": "purchase_bill_details",
                "attributes": {
                    "quantity":    kg,
                    "unit_price":  fiyat,
                    "vat_rate":    0,
                    "description": f"{ticket.plaka} — {malzeme_adi} — {kg:,} kg — {fiyat:,.2f} TL/kg"
                },
                "relationships": {
                    "product": {"data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}
                }
            })

        if not detaylar:
            return {"basarili": False, "hata": "Faturalanacak malzeme bulunamadı"}

        istek_verisi = {
            "data": {
                "type": "purchase_bills",
                "attributes": {
                    "item_type":   "purchase_bill",
                    "description": f"Hurda Alım — {ticket.plaka} — {ticket.agirlik_kg} kg",
                    "issue_date":  fis_tarihi,
                    "due_date":    bugun,
                    "currency":    "TRL",
                    "net_total":   toplam_tutar,
                    "total_vat":   0
                },
                "relationships": {
                    "contact": {"data": {"type": "contacts", "id": contact_id}},
                    "details": {"data": detaylar}
                }
            }
        }

        sonuc      = parasut_post(f"/{PARASUT_COMPANY_ID}/purchase_bills", istek_verisi)
        parasut_id = sonuc["data"]["id"]
        logger.info(f"✅ Malzemeli alım faturası oluşturuldu: parasut_id={parasut_id} tutar={toplam_tutar:,.0f} TL")

        import json as _json
        invoice = Invoice(
            fatura_no         = str(parasut_id),
            parasut_id        = str(parasut_id),
            tutar             = int(toplam_tutar),
            malzeme_fiyatlari = _json.dumps(toplanan_fiyatlar, ensure_ascii=False),
            parasut_response  = str(sonuc),
            status            = TicketStatus.TAMAMLANDI,
            waybill_id        = waybill_id
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        waybill.status = TicketStatus.TAMAMLANDI
        ticket.status  = TicketStatus.TAMAMLANDI
        db.commit()

        logger.info(f"✅ Malzemeli alım faturası kesildi: plaka={ticket.plaka} tutar={toplam_tutar:,.0f} TL parasut_id={parasut_id}")
        return {
            "basarili":     True,
            "fatura_no":    str(parasut_id),
            "parasut_id":   str(parasut_id),
            "invoice_id":   invoice.id,
            "toplam_tutar": toplam_tutar
        }

    except Exception as e:
        logger.error(f"Malzemeli alım faturası kesme hatası: {e}")
        try:
            waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
            ticket  = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first() if waybill else None
            if waybill: waybill.status = TicketStatus.HATA
            if ticket:
                ticket.status      = TicketStatus.HATA
                ticket.hata_mesaji = str(e)
            db.commit()
        except Exception:
            pass
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()