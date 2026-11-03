# app/routers/fatura.py — Satış Faturaları

import json as _json
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.database import SessionLocal
from app.core.logger import logger
from app.auth import mevcut_kullanici
from app.routers.deps import (
    _islem_log, _usage_kaydet, _billable_kaydet,
    _tenant_id_from_user, _guvenli_hata, tr_para, tr_kg
)

router = APIRouter()


@router.get("/api/fatura-detay/{fatura_id}")
async def fatura_detay_endpoint(fatura_id: str, request: Request, background_tasks: BackgroundTasks):
    """
    Fatura detayı. Tip query param ile satış / alış ayrımı.
    ?tip=customer → sales_invoices (default)
    ?tip=supplier → purchase_bills
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    tip = (request.query_params.get("tip") or "customer").strip().lower()
    if tip not in ("customer", "supplier"):
        tip = "customer"
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                   "SORGU", f"fatura_detay | id={fatura_id} | tip={tip}", 1,
                                   aktif_user.get("profaix_user_id"))
    try:
        from app.muhasebe import async_fatura_detay_universal
        detay = await async_fatura_detay_universal(fatura_id, tip=tip, tenant_id=tenant_id)
        if detay:
            detay["tip"] = tip
        return JSONResponse(detay)
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)})


@router.get("/api/cari-faturalar/{cari_adi}")
async def cari_faturalar_endpoint(cari_adi: str, request: Request, background_tasks: BackgroundTasks):
    """
    Carinin faturalarını filtreli ve sayfalı getirir.

    Query params:
        baslangic : YYYY-MM-DD
        bitis     : YYYY-MM-DD
        sayfa     : int (default 1)
        limit     : int (default 25, max 25)
        durum     : "paid" | "not_paid" | "overdue" | boş=hepsi

    Dönüş:
        {faturalar, toplam, toplam_kayit, sayfa, sonraki_sayfa_var}
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                   "SORGU", f"fatura_listesi | cari={cari_adi}", 1,
                                   aktif_user.get("profaix_user_id"))
    try:
        from app.muhasebe import async_cari_id_bul, async_cari_faturalari_getir
        from urllib.parse import unquote
        cari_adi  = unquote(cari_adi)
        baslangic = request.query_params.get("baslangic")
        bitis     = request.query_params.get("bitis")
        sayfa     = int(request.query_params.get("sayfa", 1))
        limit     = min(int(request.query_params.get("limit", 25)), 25)
        durum     = request.query_params.get("durum")
        # tip query param: 'customer' (default, sales_invoices),
        # 'supplier' (purchase_bills), 'both' (her ikisi birleştirilmiş)
        tip       = (request.query_params.get("tip") or "customer").strip().lower()
        if tip not in ("customer", "supplier", "both"):
            tip = "customer"

        cari_id = await async_cari_id_bul(cari_adi, tenant_id=tenant_id)
        if not cari_id:
            return JSONResponse({"faturalar": [], "hata": "Cari bulunamadı"})

        sonuc = await async_cari_faturalari_getir(
            cari_id, limit, sayfa, baslangic, bitis, durum, tip, tenant_id=tenant_id
        )
        return JSONResponse(sonuc)
    except Exception as e:
        return JSONResponse({"faturalar": [], "hata": _guvenli_hata(e)})


# ── TOPLU FATURA KES ─────────────────────────────────────────────────

@router.post("/api/toplu-fatura-kes")
async def toplu_fatura_kes_endpoint(request: Request, background_tasks: BackgroundTasks):
    import asyncio
    data             = await request.json()
    cari_adi         = data.get("cari_adi")
    irsaliyeler_data = data.get("irsaliyeler", [])

    if not cari_adi or not irsaliyeler_data:
        return JSONResponse({"cevap": "❌ Eksik bilgi.", "basarili": False})

    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    db = SessionLocal()
    try:
        from app.muhasebe import async_cari_id_bul, async_parasut_post, async_parasut_delete, async_cari_cache_yenile, get_tenant_config
        from app.core.database import Waybill, WeighTicket, TicketStatus
        from datetime import datetime

        cfg        = get_tenant_config(tenant_id)
        contact_id = await async_cari_id_bul(cari_adi, tenant_id=tenant_id)
        if not contact_id:
            return JSONResponse({"cevap": f"❌ '{cari_adi}' carisi bulunamadı.", "basarili": False})

        bugun         = datetime.now().strftime("%Y-%m-%d")
        detaylar      = []
        shipment_docs = []
        toplam_tutar  = 0
        irsaliye_ids  = []

        for irs in irsaliyeler_data:
            pid               = irs.get("parasut_irsaliye_id")
            malzemeler        = irs.get("malzemeler", {})
            toplanan_fiyatlar = irs.get("toplanan_fiyatlar", {})
            agirlik_kg        = irs.get("agirlik_kg", 0)
            if not pid:
                continue
            irsaliye_ids.append(str(pid))
            shipment_docs.append({"id": str(pid), "type": "shipment_documents"})
            if malzemeler and len(toplanan_fiyatlar) > 1:
                for malzeme_adi, fiyat in toplanan_fiyatlar.items():
                    kg = malzemeler.get(malzeme_adi, 0)
                    if kg <= 0:
                        continue
                    toplam_tutar += kg * fiyat
                    detaylar.append({"type": "sales_invoice_details",
                                      "attributes": {"quantity": kg, "unit_price": f"{float(fiyat):.4f}",
                                                     "vat_rate": 0, "description": f"{malzeme_adi} — {kg:,} kg"},
                                      "relationships": {"product": {"data": {"type": "products", "id": str(cfg["hurda_product_id"])}}}})
            else:
                birim_fiyat   = float(list(toplanan_fiyatlar.values())[0]) if toplanan_fiyatlar else 0
                kg            = float(agirlik_kg) if agirlik_kg else 0
                toplam_tutar += kg * birim_fiyat
                detaylar.append({"type": "sales_invoice_details",
                                  "attributes": {"quantity": kg, "unit_price": f"{float(birim_fiyat):.4f}",
                                                 "vat_rate": 0, "description": f"Hurda — {tr_kg(kg)} kg"},
                                  "relationships": {"product": {"data": {"type": "products", "id": str(cfg["hurda_product_id"])}}}})

        if not detaylar:
            return JSONResponse({"cevap": "❌ Fatura kalemi oluşturulamadı.", "basarili": False})

        from app.muhasebe import async_cari_tipi_bul
        from app.core.tenant import get_tenant_ozellik
        dogru_akis   = get_tenant_ozellik(tenant_id, "dogru_parasut_akisi", varsayilan=True)
        account_type = await async_cari_tipi_bul(cari_adi, tenant_id=tenant_id) if dogru_akis else "customer"
        is_supplier  = (account_type == "supplier")
        logger.info(f"Toplu fatura akışı: cari={cari_adi} account_type={account_type} is_supplier={is_supplier} dogru_akis={dogru_akis}")

        if dogru_akis and is_supplier:
            # Tedarikçiden alış: purchase_bills + irsaliyeler bağlı kalır
            detaylar_pb = [dict(d, type="purchase_bill_details") for d in detaylar]
            istek = {
                "data": {
                    "type": "purchase_bills",
                    "attributes": {"item_type": "invoice", "description": f"Toplu Hurda Alım — {len(irsaliye_ids)} irsaliye",
                                   "issue_date": bugun, "due_date": bugun, "currency": "TRL"},
                    "relationships": {
                        "supplier": {"data": {"type": "contacts", "id": str(contact_id)}},
                        "details": {"data": detaylar_pb},
                        "shipment_documents": {"data": shipment_docs}
                    }
                }
            }
            sonuc = await async_parasut_post(f"/{cfg['company_id']}/purchase_bills", istek, tenant_id=tenant_id)
        else:
            # Müşteriye satış VEYA Osman eski akışı: sales_invoices
            istek = {
                "data": {
                    "type": "sales_invoices",
                    "attributes": {"item_type": "invoice", "description": f"Toplu Hurda Alım — {len(irsaliye_ids)} irsaliye",
                                   "issue_date": bugun, "due_date": bugun, "currency": "TRY"},
                    "relationships": {
                        "contact": {"data": {"type": "contacts", "id": str(contact_id)}},
                        "details": {"data": detaylar},
                        "shipment_documents": {"data": shipment_docs}
                    }
                }
            }
            try:
                sonuc = await async_parasut_post(f"/{cfg['company_id']}/sales_invoices", istek, tenant_id=tenant_id)
            except Exception as e:
                logger.warning(f"İrsaliye bağlantılı toplu fatura başarısız: {e}")
                istek["data"]["relationships"].pop("shipment_documents", None)
                sonuc = await async_parasut_post(f"/{cfg['company_id']}/sales_invoices", istek, tenant_id=tenant_id)

        fatura_id = sonuc["data"]["id"]
        # Kullanıcıya gösterilecek numara: sağlayıcı fatura no verdiyse o (yerel: AF2026-00013), yoksa ID
        fatura_gorunen_no = (sonuc["data"].get("attributes") or {}).get("invoice_no") or fatura_id
        logger.info(f"✅ Toplu fatura kesildi: {fatura_id} ← {len(irsaliye_ids)} irsaliye")

        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                       "TOPLU_FATURA", f"cari={cari_adi} | {int(toplam_tutar)} TL",
                                       1, aktif_user.get("profaix_user_id"))
            background_tasks.add_task(_billable_kaydet, aktif_user, "TOPLU_FATURA_KESILDI",
                                      f"cari={cari_adi} | {int(toplam_tutar)} TL", 1, "parasut",
                                      {"fatura_id": fatura_id, "irsaliye_sayisi": len(irsaliye_ids),
                                       "toplam_tutar": int(toplam_tutar)},
                                      f"toplu_fatura:{fatura_id}")
        _islem_log(db, "TOPLU_FATURA_KESILDI", cari_adi=cari_adi, tutar=int(toplam_tutar),
                   fatura_id=fatura_id, aciklama=f"{len(irsaliye_ids)} irsaliye birleştirildi",
                   request=request, user=aktif_user)

        for pid in irsaliye_ids:
            try:
                waybill = db.query(Waybill).filter(Waybill.parasut_irsaliye_id == pid).first()
                if waybill:
                    waybill.status = TicketStatus.TAMAMLANDI
                    ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first()
                    if ticket:
                        ticket.status = TicketStatus.TAMAMLANDI
                else:
                    phantom = Waybill(
                        irsaliye_no=str(pid),
                        parasut_irsaliye_id=str(pid),
                        status=TicketStatus.TAMAMLANDI,
                        tenant_id=tenant_id,
                    )
                    db.add(phantom)
                    logger.info(f"Phantom waybill oluşturuldu: {pid}")
            except Exception as e:
                logger.error(f"DB güncelleme hatası ({pid}): {e}")
                db.rollback()
        try:
            db.commit()
        except Exception as e:
            logger.error(f"Toplu fatura DB commit hatası: {e}")
            db.rollback()

        if not dogru_akis:
            for pid in irsaliye_ids:
                try:
                    await async_parasut_delete(f"/{cfg['company_id']}/shipment_documents/{pid}", tenant_id=tenant_id)
                except Exception as e:
                    logger.warning(f"İrsaliye silinemedi ({pid}): {e}")

        try:
            await async_cari_cache_yenile(tenant_id=tenant_id)
        except Exception:
            pass

        return JSONResponse({
            "cevap": f"✅ Toplu fatura kesildi!\n\n🏢 {cari_adi}\n📦 {len(irsaliye_ids)} irsaliye birleştirildi\n💵 Toplam: {tr_para(toplam_tutar, 2)} TL\n🧾 Fatura No: {fatura_gorunen_no}",
            "basarili": True, "fatura_no": fatura_id
        })
    except Exception as e:
        logger.error(f"Toplu fatura kes hatası: {e}")
        return JSONResponse({"cevap": f"❌ Toplu fatura kesilemedi: {_guvenli_hata(e)}", "basarili": False})
    finally:
        db.close()


# ── FATURA KES ────────────────────────────────────────────────────────

@router.post("/api/fatura-kes")
async def fatura_kes_endpoint(request: Request, background_tasks: BackgroundTasks):
    import asyncio
    data                = await request.json()
    parasut_irsaliye_id = data.get("parasut_irsaliye_id")
    cari_adi            = data.get("cari_adi")
    agirlik_kg          = data.get("agirlik_kg", 0)
    malzemeler          = data.get("malzemeler", {})
    toplanan_fiyatlar   = data.get("toplanan_fiyatlar", {})

    if not parasut_irsaliye_id or not cari_adi or not toplanan_fiyatlar:
        return JSONResponse({"cevap": "❌ Eksik bilgi: irsaliye ID, cari adı ve fiyat gerekli."})

    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    db = SessionLocal()
    try:
        from app.muhasebe import async_cari_id_bul, async_parasut_post, async_parasut_delete, async_cari_cache_yenile, get_tenant_config
        from app.core.database import Waybill, WeighTicket, TicketStatus
        from datetime import datetime

        cfg        = get_tenant_config(tenant_id)
        contact_id = await async_cari_id_bul(cari_adi, tenant_id=tenant_id)
        if not contact_id:
            return JSONResponse({"cevap": f"❌ '{cari_adi}' carisi bulunamadı."})

        bugun = datetime.now().strftime("%Y-%m-%d")

        if malzemeler and len(toplanan_fiyatlar) > 1:
            detaylar     = []
            toplam_tutar = 0
            for malzeme_adi, fiyat in toplanan_fiyatlar.items():
                kg = malzemeler.get(malzeme_adi, 0)
                if kg <= 0:
                    continue
                toplam_tutar += kg * fiyat
                detaylar.append({"type": "sales_invoice_details",
                                  "attributes": {"quantity": kg, "unit_price": f"{float(fiyat):.4f}",
                                                 "vat_rate": 0, "description": f"{malzeme_adi} — {kg:,} kg"},
                                  "relationships": {"product": {"data": {"type": "products", "id": str(cfg["hurda_product_id"])}}}})
        else:
            birim_fiyat  = float(list(toplanan_fiyatlar.values())[0])
            kg           = float(agirlik_kg) if agirlik_kg else float(sum(malzemeler.values())) if malzemeler else 0
            toplam_tutar = kg * birim_fiyat
            detaylar     = [{"type": "sales_invoice_details",
                              "attributes": {"quantity": kg, "unit_price": f"{float(birim_fiyat):.4f}",
                                             "vat_rate": 0, "description": f"Hurda — {tr_kg(kg)} kg"},
                              "relationships": {"product": {"data": {"type": "products", "id": str(cfg["hurda_product_id"])}}}}]

        from app.muhasebe import async_cari_tipi_bul
        from app.core.tenant import get_tenant_ozellik
        dogru_akis   = get_tenant_ozellik(tenant_id, "dogru_parasut_akisi", varsayilan=True)
        account_type = await async_cari_tipi_bul(cari_adi, tenant_id=tenant_id) if dogru_akis else "customer"
        is_supplier  = (account_type == "supplier")
        logger.info(f"Fatura akışı: cari={cari_adi} account_type={account_type} is_supplier={is_supplier} dogru_akis={dogru_akis}")

        if dogru_akis and is_supplier:
            # Tedarikçiden alış: purchase_bills + gelen irsaliye bağlı kalır
            detaylar_pb = [dict(d, type="purchase_bill_details") for d in detaylar]
            istek = {
                "data": {
                    "type": "purchase_bills",
                    "attributes": {"item_type": "invoice", "description": f"Hurda Alım — İrsaliye {parasut_irsaliye_id}",
                                   "issue_date": bugun, "due_date": bugun, "currency": "TRL"},
                    "relationships": {
                        "supplier": {"data": {"type": "contacts", "id": str(contact_id)}},
                        "details": {"data": detaylar_pb},
                        "shipment_documents": {"data": [{"type": "shipment_documents", "id": str(parasut_irsaliye_id)}]}
                    }
                }
            }
            sonuc = await async_parasut_post(f"/{cfg['company_id']}/purchase_bills", istek, tenant_id=tenant_id)
        else:
            # Müşteriye satış VEYA Osman eski akışı: sales_invoices
            istek = {
                "data": {
                    "type": "sales_invoices",
                    "attributes": {"item_type": "invoice", "description": f"Hurda Satış — İrsaliye {parasut_irsaliye_id}",
                                   "issue_date": bugun, "due_date": bugun, "currency": "TRY"},
                    "relationships": {
                        "contact": {"data": {"type": "contacts", "id": str(contact_id)}},
                        "details": {"data": detaylar},
                        "shipment_documents": {"data": [{"type": "shipment_documents", "id": str(parasut_irsaliye_id)}]}
                    }
                }
            }
            try:
                sonuc = await async_parasut_post(f"/{cfg['company_id']}/sales_invoices", istek, tenant_id=tenant_id)
            except Exception as e:
                logger.warning(f"İrsaliye bağlantılı fatura başarısız: {e}")
                istek["data"]["relationships"].pop("shipment_documents", None)
                sonuc = await async_parasut_post(f"/{cfg['company_id']}/sales_invoices", istek, tenant_id=tenant_id)

        fatura_id = sonuc["data"]["id"]
        # Kullanıcıya gösterilecek numara: sağlayıcı fatura no verdiyse o (yerel: AF2026-00013), yoksa ID
        fatura_gorunen_no = (sonuc["data"].get("attributes") or {}).get("invoice_no") or fatura_id
        logger.info(f"✅ Fatura kesildi: {fatura_id} ← irsaliye {parasut_irsaliye_id}")

        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                       "FATURA", f"cari={cari_adi} | {int(toplam_tutar)} TL",
                                       1, aktif_user.get("profaix_user_id"))
            background_tasks.add_task(_billable_kaydet, aktif_user, "FATURA_KESILDI",
                                      f"cari={cari_adi} | {int(toplam_tutar)} TL", 1, "parasut",
                                      {"fatura_id": fatura_id, "irsaliye_id": str(parasut_irsaliye_id),
                                       "toplam_tutar": int(toplam_tutar)},
                                      f"fatura:{fatura_id}")
        _islem_log(db, "FATURA_KESILDI", cari_adi=cari_adi, tutar=int(toplam_tutar),
                   fatura_id=fatura_id, irsaliye_id=str(parasut_irsaliye_id),
                   request=request, user=aktif_user)

        try:
            waybill = db.query(Waybill).filter(Waybill.parasut_irsaliye_id == str(parasut_irsaliye_id)).first()
            if waybill:
                waybill.status = TicketStatus.TAMAMLANDI
                ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first()
                if ticket:
                    ticket.status = TicketStatus.TAMAMLANDI
            else:
                # Paraşüt'te var ama DB'de yok (reset sonrası) — phantom kayıt oluştur
                phantom = Waybill(
                    irsaliye_no=str(parasut_irsaliye_id),
                    parasut_irsaliye_id=str(parasut_irsaliye_id),
                    status=TicketStatus.TAMAMLANDI,
                    tenant_id=tenant_id,
                )
                db.add(phantom)
                logger.info(f"Phantom waybill oluşturuldu: {parasut_irsaliye_id}")
            db.commit()
        except Exception as e:
            logger.error(f"DB güncelleme hatası: {e}")

        if not dogru_akis:
            try:
                await async_parasut_delete(f"/{cfg['company_id']}/shipment_documents/{parasut_irsaliye_id}", tenant_id=tenant_id)
            except Exception as e:
                logger.warning(f"İrsaliye silinemedi: {e}")

        ozet = f"✅ Fatura kesildi!\n\n🏢 {cari_adi}\n"
        for malzeme_adi_ozet, fiyat_ozet in toplanan_fiyatlar.items():
            kg_val = malzemeler.get(malzeme_adi_ozet, agirlik_kg) if malzemeler else agirlik_kg
            ozet  += f"  • {malzeme_adi_ozet if malzemeler and len(toplanan_fiyatlar) > 1 else 'Hurda'}: {tr_kg(kg_val)} kg × {tr_para(fiyat_ozet, 4)} TL/kg = {tr_para(kg_val * fiyat_ozet, 2)} TL\n"
        ozet += f"\n💵 Toplam: {tr_para(toplam_tutar, 2)} TL\n🧾 Fatura No: {fatura_gorunen_no}"

        try:
            await async_cari_cache_yenile(tenant_id=tenant_id)
        except Exception:
            pass

        return JSONResponse({"cevap": ozet, "basarili": True, "fatura_no": fatura_id})

    except Exception as e:
        logger.error(f"Fatura kes hatası: {e}")
        _islem_log(db, "FATURA_HATA", cari_adi=cari_adi, irsaliye_id=str(parasut_irsaliye_id),
                   aciklama=str(e), request=request, user=aktif_user)
        return JSONResponse({"cevap": f"❌ Fatura kesilemedi: {_guvenli_hata(e)}", "basarili": False})
    finally:
        db.close()


@router.post("/api/fatura-iptal")
async def fatura_iptal_endpoint(request: Request, background_tasks: BackgroundTasks):
    """
    Satış veya alış faturasını iptal eder.
    Body: {fatura_id, tip?}
      tip='customer' (default) → sales_invoices/{id}/cancel
      tip='supplier'           → purchase_bills/{id}/cancel
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    data       = await request.json()
    fatura_id  = (data.get("fatura_id") or "").strip()
    tip        = (data.get("tip") or "customer").strip().lower()
    if tip not in ("customer", "supplier"):
        tip = "customer"
    if not fatura_id:
        return JSONResponse({"hata": "fatura_id zorunlu"}, status_code=400)

    from app.muhasebe import async_fatura_iptal_universal
    sonuc = await async_fatura_iptal_universal(fatura_id, tip=tip, tenant_id=tenant_id)
    if sonuc.get("basarili"):
        if aktif_user:
            event_type = "ALIS_FATURASI_IPTAL" if tip == "supplier" else "FATURA_IPTAL"
            background_tasks.add_task(_billable_kaydet, aktif_user, event_type,
                                      f"fatura={fatura_id} tip={tip}", 1, "parasut",
                                      {"fatura_id": fatura_id, "tip": tip},
                                      f"fatura_iptal:{tip}:{fatura_id}")
        return JSONResponse(sonuc)
    return JSONResponse(sonuc, status_code=500)


@router.post("/api/fatura-guncelle")
async def fatura_guncelle_endpoint(request: Request, background_tasks: BackgroundTasks):
    """
    Satış veya alış faturası üst bilgilerini günceller.
    Body: {fatura_id, tip?, ... Paraşüt'ün izin verdiği tüm alanlar ...}
      tip='customer' (default) → sales_invoices PUT
      tip='supplier'           → purchase_bills PUT
    Desteklenen alanlar (hepsi opsiyonel):
      Temel:    aciklama, issue_date, due_date, currency
      Resmi:    invoice_no, invoice_series, exchange_rate
      Vergi:    withholding_rate, vat_withholding_code
      İskonto:  invoice_discount_type, invoice_discount, invoice_discount_amount
      Adres:    billing_address, billing_phone, billing_fax,
                tax_office, tax_number, city, district, country
      Sipariş:  order_no, order_date
      Kargo:    shipment_addres, shipment_included  (sadece sales)
      Bayraklar: cash_sale, is_abroad, payer_tax_number
      İlişki:   contact_id (sales) / supplier_id (purchase),
                category_id, tag_ids (liste)
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    data       = await request.json()
    fatura_id  = (data.get("fatura_id") or "").strip()
    tip        = (data.get("tip") or "customer").strip().lower()
    if tip not in ("customer", "supplier"):
        tip = "customer"
    if not fatura_id:
        return JSONResponse({"hata": "fatura_id zorunlu"}, status_code=400)

    # Body'den gelen alanları toparla — sadece tanınanları geçir
    IZINLI_ALANLAR = {
        # attribute alanları (hem sales hem purchase)
        "description", "issue_date", "due_date", "currency",
        "invoice_no", "invoice_series", "exchange_rate",
        "withholding_rate", "vat_withholding_code",
        "invoice_discount_type", "invoice_discount", "invoice_discount_amount",
        "billing_address", "billing_phone", "billing_fax",
        "tax_office", "tax_number", "city", "district", "country",
        "order_no", "order_date",
        "shipment_addres", "shipment_included",
        "cash_sale", "is_abroad", "payer_tax_number",
        # ilişkiler
        "contact_id", "supplier_id", "category_id", "tag_ids",
    }
    diger_alanlar = {}
    for k, v in data.items():
        if k in IZINLI_ALANLAR and v not in (None, ""):
            diger_alanlar[k] = v

    # Backward-compat alias'lar
    aciklama   = data.get("aciklama") or diger_alanlar.pop("description", None)
    issue_date = (data.get("issue_date") or data.get("tarih")
                  or diger_alanlar.pop("issue_date", None))
    due_date   = (data.get("due_date") or data.get("vade")
                  or diger_alanlar.pop("due_date", None))
    currency   = data.get("currency") or diger_alanlar.pop("currency", None)

    from app.muhasebe import async_fatura_guncelle_universal
    sonuc = await async_fatura_guncelle_universal(
        fatura_id=fatura_id, tip=tip,
        aciklama=aciklama, issue_date=issue_date,
        due_date=due_date, currency=currency,
        tenant_id=tenant_id,
        **diger_alanlar,
    )
    if sonuc.get("basarili"):
        if aktif_user:
            ozet = list(diger_alanlar.keys()) + (["description"] if aciklama else []) + \
                   (["issue_date"] if issue_date else []) + (["due_date"] if due_date else []) + \
                   (["currency"] if currency else [])
            background_tasks.add_task(_billable_kaydet, aktif_user, "FATURA_GUNCELLENDI",
                                      f"fatura={fatura_id} tip={tip} alanlar={ozet}", 1, "parasut",
                                      {"fatura_id": fatura_id, "tip": tip, "alanlar": ozet}, None)
        return JSONResponse(sonuc)
    return JSONResponse(sonuc, status_code=500)
