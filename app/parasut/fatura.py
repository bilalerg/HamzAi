# fatura.py — Paraşüt Satış Faturası Modülü

from datetime import datetime
from app.parasut.client import parasut_post, cari_id_bul, get_tenant_config
from app.core.database import SessionLocal, Waybill, Invoice, WeighTicket, TicketStatus
from app.core.logger import logger


def _contact_id_bul(firma_adi: str, tenant_id: str = None) -> str:
    cid = cari_id_bul(firma_adi, tenant_id=tenant_id)
    if cid:
        return str(cid)
    # CARİ BULUNAMAZSA DİREKT İŞLEMİ DURDUR
    hata_mesaji = f"'{firma_adi}' adlı firma Paraşüt'te bulunamadı. Lütfen önce cari kaydını oluşturun."
    logger.error(hata_mesaji)
    raise Exception(hata_mesaji)


def fatura_kes(waybill_id: int, birim_fiyat: float = 0, tenant_id: str = None) -> dict:
    """Tek birim fiyatla fatura keser."""
    db = SessionLocal()
    try:
        waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
        if not waybill:
            return {"basarili": False, "hata": "waybill_bulunamadi"}

        ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first()
        if not ticket:
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        # ── DUPLICATE KORUMASI ────────────────────────────────────────────
        if waybill.status == TicketStatus.TAMAMLANDI:
            logger.warning(f"⚠️ Duplicate fatura girişimi engellendi: waybill_id={waybill_id}")
            mevcut_invoice = db.query(Invoice).filter(Invoice.waybill_id == waybill_id).first()
            return {
                "basarili": True,
                "fatura_no": mevcut_invoice.fatura_no if mevcut_invoice else "-",
                "duplicate": True
            }

        # ── RACE CONDITION KORUMASI ───────────────────────────────────────
        if waybill.status == TicketStatus.FATURA_KESILIYOR:
            logger.warning(f"⚠️ Fatura zaten işleniyor: waybill_id={waybill_id}")
            return {"basarili": False, "hata": "fatura_zaten_isleniyor"}

        logger.info(f"Fatura kesiliyor: waybill_id={waybill_id} plaka={ticket.plaka} birim_fiyat={birim_fiyat}")

        waybill.status = TicketStatus.FATURA_KESILIYOR
        ticket.status  = TicketStatus.FATURA_KESILIYOR
        db.commit()

        cfg        = get_tenant_config(tenant_id)
        bugun      = datetime.now().strftime("%Y-%m-%d")
        fis_tarihi = ticket.fis_tarihi.strftime("%Y-%m-%d") if ticket.fis_tarihi else bugun
        toplam_tutar = (ticket.agirlik_kg or 0) * birim_fiyat
        contact_id   = _contact_id_bul(ticket.firma, tenant_id=tenant_id)

        istek_verisi = {
            "data": {
                "type": "sales_invoices",
                "attributes": {
                    "item_type": "invoice",
                    "description": f"Hurda Alım — {ticket.plaka} — {ticket.agirlik_kg} kg",
                    "issue_date": fis_tarihi,
                    "due_date":   bugun,
                    "currency":   "TRY"
                },
                "relationships": {
                    "contact": {"data": {"type": "contacts", "id": contact_id}},
                    "details": {
                        "data": [{
                            "type": "sales_invoice_details",
                            "attributes": {
                                "quantity":    ticket.agirlik_kg or 1,
                                "unit_price":  str(birim_fiyat),
                                "vat_rate":    0,
                                "description": f"{ticket.plaka} — {ticket.agirlik_kg} kg hurda — {birim_fiyat:,.2f} TL/kg"
                            },
                            "relationships": {
                                "product": {"data": {"type": "products", "id": str(cfg["hurda_product_id"])}}
                            }
                        }]
                    }
                }
            }
        }

        sonuc      = parasut_post(f"/{cfg['company_id']}/sales_invoices", istek_verisi, tenant_id=tenant_id)
        parasut_id = sonuc["data"]["id"]

        invoice = Invoice(
            fatura_no        = str(parasut_id),
            parasut_id       = str(parasut_id),
            tutar            = int(toplam_tutar),
            birim_fiyat      = int(birim_fiyat),
            parasut_response = str(sonuc),
            status           = TicketStatus.TAMAMLANDI,
            waybill_id       = waybill_id,
            tenant_id        = tenant_id
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        waybill.status = TicketStatus.TAMAMLANDI
        ticket.status  = TicketStatus.TAMAMLANDI
        db.commit()

        logger.info(f"✅ Fatura kesildi: fatura_no={parasut_id} tutar={toplam_tutar:,.0f} TL")

        return {
            "basarili":     True,
            "fatura_no":    str(parasut_id),
            "parasut_id":   str(parasut_id),
            "invoice_id":   invoice.id,
            "toplam_tutar": toplam_tutar,
            "duplicate":    False
        }

    except Exception as e:
        logger.error(f"Fatura kesme hatası: {e}")
        try:
            waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
            ticket  = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first() if waybill else None
            if waybill:
                waybill.status = TicketStatus.HATA
            if ticket:
                ticket.status      = TicketStatus.HATA
                ticket.hata_mesaji = str(e)
            db.commit()
        except Exception:
            pass
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def fatura_kes_malzemeli(waybill_id: int, malzemeler: dict, toplanan_fiyatlar: dict, tenant_id: str = None) -> dict:
    """Malzeme bazlı fatura keser — her malzeme Paraşüt'te ayrı satır."""
    db = SessionLocal()
    try:
        waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
        if not waybill:
            return {"basarili": False, "hata": "waybill_bulunamadi"}

        ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first()
        if not ticket:
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        # ── DUPLICATE KORUMASI ────────────────────────────────────────────
        if waybill.status == TicketStatus.TAMAMLANDI:
            logger.warning(f"⚠️ Duplicate malzemeli fatura girişimi engellendi: waybill_id={waybill_id}")
            mevcut_invoice = db.query(Invoice).filter(Invoice.waybill_id == waybill_id).first()
            return {
                "basarili":  True,
                "fatura_no": mevcut_invoice.fatura_no if mevcut_invoice else "-",
                "duplicate": True
            }

        # ── RACE CONDITION KORUMASI ───────────────────────────────────────
        if waybill.status == TicketStatus.FATURA_KESILIYOR:
            logger.warning(f"⚠️ Malzemeli fatura zaten işleniyor: waybill_id={waybill_id}")
            return {"basarili": False, "hata": "fatura_zaten_isleniyor"}

        logger.info(f"Malzemeli fatura kesiliyor: waybill_id={waybill_id} plaka={ticket.plaka}")

        waybill.status = TicketStatus.FATURA_KESILIYOR
        ticket.status  = TicketStatus.FATURA_KESILIYOR
        db.commit()

        cfg        = get_tenant_config(tenant_id)
        bugun      = datetime.now().strftime("%Y-%m-%d")
        fis_tarihi = ticket.fis_tarihi.strftime("%Y-%m-%d") if ticket.fis_tarihi else bugun
        contact_id = _contact_id_bul(ticket.firma, tenant_id=tenant_id)

        detaylar     = []
        toplam_tutar = 0
        for malzeme_adi, fiyat in toplanan_fiyatlar.items():
            kg = malzemeler.get(malzeme_adi, 0)
            if kg <= 0:
                continue
            tutar         = kg * fiyat
            toplam_tutar += tutar
            detaylar.append({
                "type": "sales_invoice_details",
                "attributes": {
                    "quantity":    kg,
                    "unit_price":  str(fiyat),
                    "vat_rate":    0,
                    "description": f"{ticket.plaka} — {malzeme_adi} — {kg:,} kg — {fiyat:,.2f} TL/kg"
                },
                "relationships": {
                    "product": {"data": {"type": "products", "id": str(cfg["hurda_product_id"])}}
                }
            })

        if not detaylar:
            waybill.status = TicketStatus.IRSALIYE_TAMAMLANDI
            ticket.status  = TicketStatus.IRSALIYE_TAMAMLANDI
            db.commit()
            return {"basarili": False, "hata": "Faturalanacak malzeme bulunamadı"}

        istek_verisi = {
            "data": {
                "type": "sales_invoices",
                "attributes": {
                    "item_type":   "invoice",
                    "description": f"Hurda Alım — {ticket.plaka} — {ticket.agirlik_kg} kg",
                    "issue_date":  fis_tarihi,
                    "due_date":    bugun,
                    "currency":    "TRL"
                },
                "relationships": {
                    "contact": {"data": {"type": "contacts", "id": contact_id}},
                    "details": {"data": detaylar}
                }
            }
        }

        sonuc      = parasut_post(f"/{cfg['company_id']}/sales_invoices", istek_verisi, tenant_id=tenant_id)
        parasut_id = sonuc["data"]["id"]
        logger.info(f"✅ Malzemeli fatura oluşturuldu: parasut_id={parasut_id} tutar={toplam_tutar:,.0f} TL")

        import json as _json
        invoice = Invoice(
            fatura_no         = str(parasut_id),
            parasut_id        = str(parasut_id),
            tutar             = int(toplam_tutar),
            malzeme_fiyatlari = _json.dumps(toplanan_fiyatlar, ensure_ascii=False),
            parasut_response  = str(sonuc),
            status            = TicketStatus.TAMAMLANDI,
            waybill_id        = waybill_id,
            tenant_id         = tenant_id
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        waybill.status = TicketStatus.TAMAMLANDI
        ticket.status  = TicketStatus.TAMAMLANDI
        db.commit()

        logger.info(f"✅ Malzemeli fatura kesildi: plaka={ticket.plaka} tutar={toplam_tutar:,.0f} TL parasut_id={parasut_id}")

        return {
            "basarili":     True,
            "fatura_no":    str(parasut_id),
            "parasut_id":   str(parasut_id),
            "invoice_id":   invoice.id,
            "toplam_tutar": toplam_tutar,
            "duplicate":    False
        }

    except Exception as e:
        logger.error(f"Malzemeli fatura kesme hatası: {e}")
        try:
            waybill = db.query(Waybill).filter(Waybill.id == waybill_id).first()
            ticket  = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first() if waybill else None
            if waybill:
                waybill.status = TicketStatus.HATA
            if ticket:
                ticket.status      = TicketStatus.HATA
                ticket.hata_mesaji = str(e)
            db.commit()
        except Exception:
            pass
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()