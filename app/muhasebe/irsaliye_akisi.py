# irsaliye_akisi.py — Kantar fişi → irsaliye akışı (sağlayıcıdan bağımsız)
#
# Eskiden app/parasut/irsaliye.py idi. Duplicate/race korumaları ve fiş durum
# geçişleri burada; irsaliyenin yazıldığı yer aktif muhasebe sağlayıcısıdır
# (yerel veritabanı veya Paraşüt). app/parasut/irsaliye.py geriye uyumluluk
# için bu modülü re-export eder.

import json
from datetime import datetime
from app.muhasebe import parasut_post, cari_id_bul, get_tenant_config, saglayici_adi
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
            hata_mesaji = f"'{ticket.firma}' adlı firma {saglayici_adi()} carilerinde bulunamadı. Lütfen önce cari oluşturun."
            logger.warning(hata_mesaji)
            ticket.status = TicketStatus.HATA
            ticket.hata_mesaji = hata_mesaji
            db.commit()
            return {"basarili": False, "hata": hata_mesaji}

        from app.muhasebe import cari_tipi_bul
        from app.core.tenant import get_tenant_ozellik
        # varsayilan=True → yeni tenantlar standart akış; Osman dogru_parasut_akisi=False → eski akış
        dogru_akis = get_tenant_ozellik(tenant_id, "dogru_parasut_akisi", varsayilan=True)
        if not dogru_akis:
            inflow = False  # Osman: daima giden irsaliye
        else:
            account_type = cari_tipi_bul(ticket.firma, tenant_id=tenant_id)
            inflow = (account_type == "supplier")
            logger.info(f"İrsaliye yönü: cari={ticket.firma} account_type={account_type} inflow={inflow}")

        attributes = {
            "description": f"Kantar Fişi — {ticket.plaka} — {ticket.agirlik_kg} kg",
            "issue_date": tarih_str,
            "inflow": inflow,
            "currency": "TRY",
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
        logger.info(f"İrsaliye oluşturuldu ({saglayici_adi()}): id={parasut_id}")

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


def toplu_irsaliye_olustur(cari_adi: str, toplam_kg: int, tarih_str=None,
                           aciklama: str = None, tenant_id: str = None) -> dict:
    """
    TEK konsolide irsaliye oluşturur (tek toplam satır) — toplu içe alım için.
    Paraşüt'e tek shipment_document + tek stock_movement (toplam kg) yazar.
    WeighTicket'lara DOKUNMAZ — çağıran (ice_al) ticket/waybill yönetir.
    """
    try:
        cfg = get_tenant_config(tenant_id)
        contact_id = cari_id_bul(cari_adi, tenant_id=tenant_id)
        if not contact_id:
            return {"basarili": False, "hata": f"'{cari_adi}' {saglayici_adi()} carilerinde bulunamadı."}

        from app.muhasebe import cari_tipi_bul
        from app.core.tenant import get_tenant_ozellik
        dogru_akis = get_tenant_ozellik(tenant_id, "dogru_parasut_akisi", varsayilan=True)
        if not dogru_akis:
            inflow = False
        else:
            inflow = (cari_tipi_bul(cari_adi, tenant_id=tenant_id) == "supplier")

        attributes = {
            "description": aciklama or f"Toplu alım — {cari_adi}",
            "issue_date":  _tarih_formatla(tarih_str),
            "inflow":      inflow,
            "currency":    "TRY",
        }
        stock_movements = [{
            "type": "stock_movements",
            "attributes": {"quantity": int(toplam_kg), "description": "Toplu hurda"},
            "relationships": {
                "product": {"data": {"type": "products", "id": str(cfg["hurda_product_id"])}}
            }
        }]
        istek = {"data": {
            "type": "shipment_documents",
            "attributes": attributes,
            "relationships": {
                "contact": {"data": {"type": "contacts", "id": str(contact_id)}},
                "stock_movements": {"data": stock_movements},
            }
        }}
        sonuc = parasut_post(f"/{cfg['company_id']}/shipment_documents", istek, tenant_id=tenant_id)
        pid = sonuc["data"]["id"]
        logger.info(f"Toplu konsolide irsaliye oluşturuldu: parasut_id={pid} cari={cari_adi} toplam={toplam_kg}kg")
        return {"basarili": True, "parasut_id": str(pid)}
    except Exception as e:
        logger.error(f"Toplu irsaliye oluşturma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


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