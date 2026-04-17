# fatura.py — Paraşüt Satış Faturası Modülü

from datetime import datetime
from app.parasut.client import parasut_post
from app.core.config import (
    PARASUT_COMPANY_ID,
    PARASUT_HURDA_PRODUCT_ID,
    PARASUT_CONTACT_IDS,
)
from app.core.database import SessionLocal, Waybill, Invoice, WeighTicket, TicketStatus
from app.core.logger import logger
from app.chatbox.wp_sender import patrona_bildir, muhasebeciye_bildir


def _contact_id_bul(firma_adi: str) -> str:
    if not firma_adi:
        return str(list(PARASUT_CONTACT_IDS.values())[0])
    firma_upper = firma_adi.upper().strip()
    for anahtar, cid in PARASUT_CONTACT_IDS.items():
        if anahtar.upper() in firma_upper or firma_upper in anahtar.upper():
            return str(cid)
    return str(list(PARASUT_CONTACT_IDS.values())[0])


def fatura_kes(waybill_id: int, birim_fiyat: float = 0) -> dict:
    """Tek birim fiyatla fatura keser (malzeme yoksa)."""
    db = SessionLocal()
    try:
        waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
        if not waybill:
            return {"basarili": False, "hata": "waybill_bulunamadi"}

        ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first()
        if not ticket:
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        logger.info(f"Fatura kesiliyor: waybill_id={waybill_id} plaka={ticket.plaka} birim_fiyat={birim_fiyat}")

        waybill.status = TicketStatus.FATURA_KESILIYOR
        ticket.status = TicketStatus.FATURA_KESILIYOR
        db.commit()

        bugun = datetime.now().strftime("%Y-%m-%d")
        fis_tarihi = ticket.fis_tarihi.strftime("%Y-%m-%d") if ticket.fis_tarihi else bugun
        toplam_tutar = (ticket.agirlik_kg or 0) * birim_fiyat
        contact_id = _contact_id_bul(ticket.firma)

        istek_verisi = {
            "data": {
                "type": "sales_invoices",
                "attributes": {
                    "item_type": "invoice",
                    "description": f"Hurda Alım — {ticket.plaka} — {ticket.agirlik_kg} kg",
                    "issue_date": fis_tarihi,
                    "due_date": bugun,
                    "currency": "TRL"
                },
                "relationships": {
                    "contact": {"data": {"type": "contacts", "id": contact_id}},
                    "details": {
                        "data": [{
                            "type": "sales_invoice_details",
                            "attributes": {
                                "quantity": ticket.agirlik_kg or 1,
                                "unit_price": str(birim_fiyat),
                                "vat_rate": 0,
                                "description": f"{ticket.plaka} — {ticket.agirlik_kg} kg hurda — {birim_fiyat:,.2f} TL/kg"
                            },
                            "relationships": {
                                "product": {"data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}
                            }
                        }]
                    }
                }
            }
        }

        sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek_verisi)
        parasut_id = sonuc["data"]["id"]

        invoice = Invoice(
            fatura_no=str(parasut_id),
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

        waybill.status = TicketStatus.TAMAMLANDI
        ticket.status = TicketStatus.TAMAMLANDI
        db.commit()

        logger.info(f"✅ Fatura tamamlandı: fatura_no={parasut_id} tutar={toplam_tutar:,.0f} TL")

        patrona_bildir(
            f"✅ Fatura kesildi!\n🚗 Plaka: {ticket.plaka}\n"
            f"⚖️ {ticket.agirlik_kg:,} kg | 💰 {birim_fiyat:,.0f} TL/kg\n"
            f"💵 Toplam: {toplam_tutar:,.0f} TL\n🧾 {parasut_id}"
        )
        muhasebeciye_bildir(
            f"✅ Tamamlandı!\n🚗 {ticket.plaka} | 💵 {toplam_tutar:,.0f} TL\n🧾 {parasut_id}"
        )

        return {
            "basarili": True,
            "fatura_no": str(parasut_id),
            "parasut_id": str(parasut_id),
            "invoice_id": invoice.id,
            "toplam_tutar": toplam_tutar
        }

    except Exception as e:
        logger.error(f"Fatura kesme hatası: {e}")
        try:
            waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
            ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first() if waybill else None
            if waybill: waybill.status = TicketStatus.HATA
            if ticket:
                ticket.status = TicketStatus.HATA
                ticket.hata_mesaji = str(e)
            db.commit()
        except:
            pass
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def fatura_kes_malzemeli(waybill_id: int, malzemeler: dict, toplanan_fiyatlar: dict) -> dict:
    """
    Malzeme bazlı fatura keser — her malzeme Paraşüt'te ayrı satır olarak görünür.
    malzemeler: {"BONUS": 7700, "KARMA": 6000}
    toplanan_fiyatlar: {"BONUS": 16.0, "KARMA": 14.0}
    """
    db = SessionLocal()
    try:
        waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
        if not waybill:
            return {"basarili": False, "hata": "waybill_bulunamadi"}

        ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first()
        if not ticket:
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        logger.info(f"Malzemeli fatura kesiliyor: waybill_id={waybill_id} plaka={ticket.plaka}")

        waybill.status = TicketStatus.FATURA_KESILIYOR
        ticket.status = TicketStatus.FATURA_KESILIYOR
        db.commit()

        bugun = datetime.now().strftime("%Y-%m-%d")
        fis_tarihi = ticket.fis_tarihi.strftime("%Y-%m-%d") if ticket.fis_tarihi else bugun
        contact_id = _contact_id_bul(ticket.firma)

        # Her malzeme için ayrı satır oluştur
        detaylar = []
        toplam_tutar = 0
        for malzeme_adi, fiyat in toplanan_fiyatlar.items():
            kg = malzemeler.get(malzeme_adi, 0)
            if kg <= 0:
                continue
            tutar = kg * fiyat
            toplam_tutar += tutar
            detaylar.append({
                "type": "sales_invoice_details",
                "attributes": {
                    "quantity": kg,
                    "unit_price": str(fiyat),
                    "vat_rate": 0,
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
                "type": "sales_invoices",
                "attributes": {
                    "item_type": "invoice",
                    "description": f"Hurda Alım — {ticket.plaka} — {ticket.agirlik_kg} kg",
                    "issue_date": fis_tarihi,
                    "due_date": bugun,
                    "currency": "TRL"
                },
                "relationships": {
                    "contact": {"data": {"type": "contacts", "id": contact_id}},
                    "details": {"data": detaylar}
                }
            }
        }

        sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek_verisi)
        parasut_id = sonuc["data"]["id"]
        logger.info(f"✅ Malzemeli fatura oluşturuldu: parasut_id={parasut_id} tutar={toplam_tutar:,.0f} TL")

        import json as _json
        invoice = Invoice(
            fatura_no=str(parasut_id),
            parasut_id=str(parasut_id),
            tutar=int(toplam_tutar),
            malzeme_fiyatlari=_json.dumps(toplanan_fiyatlar, ensure_ascii=False),
            parasut_response=str(sonuc),
            status=TicketStatus.TAMAMLANDI,
            waybill_id=waybill_id
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        waybill.status = TicketStatus.TAMAMLANDI
        ticket.status = TicketStatus.TAMAMLANDI
        db.commit()

        # Bildirim
        ozet_satir = " | ".join([f"{m}: {malzemeler.get(m,0):,}kg×{f:,.0f}₺" for m, f in toplanan_fiyatlar.items()])
        patrona_bildir(
            f"✅ Fatura kesildi!\n🚗 Plaka: {ticket.plaka}\n"
            f"{ozet_satir}\n"
            f"💵 Toplam: {toplam_tutar:,.0f} TL\n🧾 {parasut_id}"
        )
        muhasebeciye_bildir(
            f"✅ Tamamlandı!\n🚗 {ticket.plaka} | 💵 {toplam_tutar:,.0f} TL\n🧾 {parasut_id}"
        )

        return {
            "basarili": True,
            "fatura_no": str(parasut_id),
            "parasut_id": str(parasut_id),
            "invoice_id": invoice.id,
            "toplam_tutar": toplam_tutar
        }

    except Exception as e:
        logger.error(f"Malzemeli fatura kesme hatası: {e}")
        try:
            waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
            ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first() if waybill else None
            if waybill: waybill.status = TicketStatus.HATA
            if ticket:
                ticket.status = TicketStatus.HATA
                ticket.hata_mesaji = str(e)
            db.commit()
        except:
            pass
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()