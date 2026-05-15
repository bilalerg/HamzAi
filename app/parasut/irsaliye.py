# irsaliye.py — Paraşüt e-İrsaliye Modülü

import json
from datetime import datetime
from app.parasut.client import parasut_post, cari_id_bul, get_tenant_config
from app.core.database import SessionLocal, WeighTicket, Waybill, TicketStatus
from app.core.logger import logger


def irsaliye_olustur(ticket_id: int, beklet: bool = False, tenant_id: str = None) -> dict:
    db = SessionLocal()
    try:
        ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()

        if not ticket:
            logger.error(f"Ticket bulunamadı: {ticket_id}")
            return {"basarili": False, "hata": "ticket_bulunamadi"}

        # ── DUPLICATE KORUMASI ────────────────────────────────────────────
        mevcut_waybill = db.query(Waybill).filter(
            Waybill.ticket_id == ticket_id
        ).first()
        if mevcut_waybill:
            logger.warning(
                f"⚠️ Duplicate irsaliye girişimi engellendi: "
                f"ticket_id={ticket_id} mevcut waybill_id={mevcut_waybill.id}"
            )
            return {
                "basarili": True,
                "irsaliye_no": str(mevcut_waybill.parasut_irsaliye_id),
                "parasut_id": str(mevcut_waybill.parasut_irsaliye_id),
                "waybill_id": mevcut_waybill.id,
                "bekliyor_onay": beklet,
                "duplicate": True
            }

        # ── RACE CONDITION KORUMASI ───────────────────────────────────────
        if ticket.status == TicketStatus.IRSALIYE_OLUSTURULUYOR:
            logger.warning(f"⚠️ İrsaliye zaten işleniyor: ticket_id={ticket_id}")
            return {"basarili": False, "hata": "irsaliye_zaten_isleniyor"}

        logger.info(f"İrsaliye oluşturuluyor: ticket_id={ticket_id} plaka={ticket.plaka} beklet={beklet} tenant={tenant_id}")

        ticket.status = TicketStatus.IRSALIYE_OLUSTURULUYOR
        db.commit()

        cfg = get_tenant_config(tenant_id)
        tarih_str = _tarih_formatla(ticket.fis_tarihi)

        contact_id = cari_id_bul(ticket.firma, tenant_id=tenant_id)

        # DÜZELTME: Cari bulunamazsa işlemi sert bir şekilde durdur!
        if not contact_id:
            hata_mesaji = f"'{ticket.firma}' adlı firma Paraşüt'te bulunamadı. Lütfen önce cari oluşturun."
            logger.warning(hata_mesaji)
            ticket.status = TicketStatus.HATA
            ticket.hata_mesaji = hata_mesaji
            db.commit()
            return {"basarili": False, "hata": hata_mesaji}

        from app.core.tenant import get_tenant_ozellik
        from app.parasut.client import cari_tipi_bul
        dogru_akis = get_tenant_ozellik(tenant_id, "dogru_parasut_akisi", varsayilan=False)

        if dogru_akis:
            # Kontak tipine göre: tedarikçi → gelen irsaliye, müşteri → giden irsaliye
            account_type = cari_tipi_bul(ticket.firma, tenant_id=tenant_id)
            inflow = (account_type == "supplier")
        else:
            inflow = False  # Osman: eski akış (her zaman giden)

        attributes = {
            "description": f"Kantar Fişi — {ticket.plaka} — {ticket.agirlik_kg} kg",
            "issue_date": tarih_str,
            "inflow": inflow,
            "currency": "TRL",
        }
        if ticket.plaka:
            attributes["carrier_license_plate"] = ticket.plaka

        malzemeler = {}
        if ticket.malzemeler:
            try:
                malzemeler = json.loads(ticket.malzemeler)
            except Exception:
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
                                "data": {"type": "products", "id": str(cfg["hurda_product_id"])}
                            }
                        }
                    })
            logger.info(f"Malzeme bazlı irsaliye: {len(stock_movements)} kalem")
        else:
            stock_movements = [{
                "type": "stock_movements",
                "attributes": {
                    "quantity": ticket.agirlik_kg or 1,
                    "description": f"{ticket.plaka} — hurda malzeme"
                },
                "relationships": {
                    "product": {
                        "data": {"type": "products", "id": str(cfg["hurda_product_id"])}
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

        sonuc = parasut_post(f"/{cfg['company_id']}/shipment_documents", istek_verisi, tenant_id=tenant_id)
        parasut_id = sonuc["data"]["id"]
        logger.info(f"Paraşüt irsaliye oluşturuldu: parasut_id={parasut_id}")

        yeni_status = TicketStatus.FABRIKA_BEKLENIYOR if beklet else TicketStatus.IRSALIYE_TAMAMLANDI

        waybill = Waybill(
            irsaliye_no=str(parasut_id),
            parasut_irsaliye_id=str(parasut_id),
            status=yeni_status,
            ticket_id=ticket_id,
            tenant_id=tenant_id
        )
        db.add(waybill)
        db.commit()
        db.refresh(waybill)

        ticket.status = yeni_status
        db.commit()

        logger.info(f"İrsaliye tamamlandı: parasut_id={parasut_id} waybill_id={waybill.id} status={yeni_status}")

        return {
            "basarili": True,
            "irsaliye_no": str(parasut_id),
            "parasut_id": str(parasut_id),
            "waybill_id": waybill.id,
            "bekliyor_onay": beklet,
            "duplicate": False
        }

    except Exception as e:
        logger.error(f"İrsaliye oluşturma hatası: {e}")
        try:
            ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()
            if ticket:
                ticket.status = TicketStatus.HATA
                ticket.hata_mesaji = str(e)
                db.commit()
        except Exception:
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

        logger.info(f"İrsaliye onaylandı: waybill_id={waybill_id}")
        return {"basarili": True, "waybill_id": waybill_id}

    except Exception as e:
        logger.error(f"İrsaliye onay hatası: {e}")
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
                except Exception:
                    continue
        except Exception:
            pass
    return datetime.now().strftime("%Y-%m-%d")