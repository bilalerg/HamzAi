# app/routers/cari.py — Cari Yönetimi

import json as _json
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.database import SessionLocal
from app.core.logger import logger
from app.auth import mevcut_kullanici
from app.routers.deps import (
    _islem_log, _usage_kaydet, _billable_kaydet,
    _tenant_id_from_user, _guvenli_hata
)

router = APIRouter()


@router.get("/api/cariler")
async def cariler_getir(request: Request, background_tasks: BackgroundTasks,
                        refresh: bool = False, tip: str = None):
    """
    Cari listesi.
    ?tip=customer / supplier / both filtresi ile sunucu tarafında süzme.
    'both' → customer + customer_supplier dahil edilir.
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                   "SORGU", "cari_listesi" + ("_refresh" if refresh else ""),
                                   1, aktif_user.get("profaix_user_id"))
    try:
        from app.muhasebe import async_cari_listesi_getir, async_cari_cache_yenile
        cariler = await async_cari_cache_yenile(tenant_id=tenant_id) if refresh else await async_cari_listesi_getir(tenant_id=tenant_id)
        # Tip filtresi — UI tarafındaki dropdown filtrelerini sunucuya çekiyoruz
        tip_norm = (tip or "").strip().lower()
        if tip_norm in ("customer", "musteri", "müşteri"):
            cariler = [c for c in cariler
                       if c.get("account_type", "customer") in ("customer", "both", "customer_supplier")]
        elif tip_norm in ("supplier", "tedarikci", "tedarikçi"):
            cariler = [c for c in cariler
                       if c.get("account_type", "customer") in ("supplier", "both", "customer_supplier")]
        sonuc   = [{"id": c["id"], "ad": c["ad"], "bakiye": c.get("bakiye", 0), "tip": c.get("account_type", "customer")} for c in cariler]
        sonuc.sort(key=lambda x: x["bakiye"], reverse=True)
        return JSONResponse({"cariler": sonuc, "toplam": len(sonuc)})
    except Exception as e:
        logger.error(f"Cariler hatası: {e}")
        return JSONResponse({"cariler": [], "hata": _guvenli_hata(e)})


# ── CARİ BİLGİ (iletişim / adres / vergi) ────────────────────────────

@router.get("/api/cari-bilgi/{cari_adi}")
async def cari_bilgi_getir(cari_adi: str, request: Request):
    mevcut_kullanici(request)  # auth kontrolü
    tenant_id = _tenant_id_from_user(mevcut_kullanici(request))
    try:
        from app.muhasebe import async_cari_id_bul, async_cari_detay_getir, cari_listesi_getir
        cari_id = await async_cari_id_bul(cari_adi, tenant_id=tenant_id)
        if not cari_id:
            return JSONResponse({"hata": f"'{cari_adi}' bulunamadı."}, status_code=404)
        detay = await async_cari_detay_getir(cari_id, tenant_id=tenant_id)
        # account_type cache'den al
        cariler = cari_listesi_getir(tenant_id=tenant_id)
        tip = next((c.get("account_type", "customer") for c in cariler if str(c["id"]) == str(cari_id)), "customer")
        detay["tip"] = tip
        return JSONResponse(detay)
    except Exception as e:
        logger.error(f"Cari bilgi hatası: {e}")
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)


@router.post("/api/cari-olustur")
async def cari_olustur_endpoint(request: Request, background_tasks: BackgroundTasks):
    import asyncio
    data          = await request.json()
    firma_adi     = data.get("firma_adi", "").strip()
    email         = data.get("email", "").strip() or None
    telefon       = data.get("telefon", "").strip() or None
    vergi_no      = data.get("vergi_no", "").strip() or None
    vergi_dairesi = data.get("vergi_dairesi", "").strip() or None
    adres         = data.get("adres", "").strip() or None
    tip_raw       = (data.get("tip") or "customer").strip().lower()
    # UI'dan gelen değerleri Paraşüt'ün beklediği değerlere normalize et
    _tip_map = {
        "customer": "customer", "musteri": "customer", "müşteri": "customer",
        "supplier": "supplier", "tedarikci": "supplier", "tedarikçi": "supplier",
        "both": "customer_supplier", "her_ikisi": "customer_supplier",
        "customer_supplier": "customer_supplier",
    }
    tip = _tip_map.get(tip_raw, "customer")

    if not firma_adi:
        return JSONResponse({"basarili": False, "cevap": "❌ Firma adı gerekli."})

    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    try:
        from app.muhasebe import cari_olustur, saglayici_adi
        sonuc = await asyncio.to_thread(cari_olustur, firma_adi, email, telefon, vergi_no, vergi_dairesi, adres, tenant_id, tip)
        if sonuc.get("basarili"):
            if aktif_user:
                background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                           "CARI", f"firma={firma_adi}", 1, aktif_user.get("profaix_user_id"))
                background_tasks.add_task(_billable_kaydet, aktif_user, "CARI_OLUSTURULDU",
                                          f"firma={firma_adi}", 1, "parasut",
                                          {"firma_adi": firma_adi, "cari_id": sonuc.get("id")},
                                          f"cari:{sonuc.get('id')}")
            db = SessionLocal()
            try:
                _islem_log(db, "CARI_OLUSTURULDU", cari_adi=firma_adi,
                           aciklama=f"Vergi No: {vergi_no or '-'} | Tel: {telefon or '-'}",
                           request=request, user=aktif_user)
            finally:
                db.close()
            return JSONResponse({"basarili": True, "cevap": (
                f"✅ Yeni cari oluşturuldu!\n\n🏢 Firma: {firma_adi}\n"
                f"📧 Email: {email or '-'}\n📞 Telefon: {telefon or '-'}\n"
                f"🔢 Vergi No: {vergi_no or '-'}\n{saglayici_adi()} sistemine kaydedildi."),
                "cari_id": sonuc.get("id")})
        else:
            return JSONResponse({"basarili": False,
                                  "cevap": f"❌ Cari oluşturulamadı: {sonuc.get('hata', 'Bilinmeyen hata')}"})
    except Exception as e:
        logger.error(f"Cari oluştur hatası: {e}")
        return JSONResponse({"basarili": False, "cevap": f"❌ Hata: {_guvenli_hata(e)}"})


@router.post("/api/cari-guncelle")
async def cari_guncelle_endpoint(request: Request, background_tasks: BackgroundTasks):
    """
    Mevcut bir cariyi günceller.
    Body: {cari_id veya cari_adi, firma_adi?, email?, telefon?, vergi_no?, vergi_dairesi?, adres?, tip?}
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    data       = await request.json()
    cari_id    = str(data.get("cari_id") or "").strip() or None
    cari_adi_kaynak = str(data.get("cari_adi") or "").strip()

    if not cari_id and not cari_adi_kaynak:
        return JSONResponse({"basarili": False, "hata": "cari_id veya cari_adi zorunlu"}, status_code=400)

    try:
        from app.muhasebe import async_cari_guncelle, async_cari_id_bul
        if not cari_id:
            cari_id = await async_cari_id_bul(cari_adi_kaynak, tenant_id=tenant_id)
            if not cari_id:
                return JSONResponse({"basarili": False, "hata": f"'{cari_adi_kaynak}' bulunamadı"}, status_code=404)

        tip_raw = (data.get("tip") or "").strip().lower()
        _tip_map = {
            "customer": "customer", "musteri": "customer", "müşteri": "customer",
            "supplier": "supplier", "tedarikci": "supplier", "tedarikçi": "supplier",
            "both": "customer_supplier", "her_ikisi": "customer_supplier",
            "customer_supplier": "customer_supplier",
        }
        tip = _tip_map.get(tip_raw) if tip_raw else None

        sonuc = await async_cari_guncelle(
            cari_id=str(cari_id),
            firma_adi=(data.get("firma_adi") or "").strip() or None,
            email=(data.get("email") or "").strip() or None,
            telefon=(data.get("telefon") or "").strip() or None,
            vergi_no=(data.get("vergi_no") or "").strip() or None,
            vergi_dairesi=(data.get("vergi_dairesi") or "").strip() or None,
            adres=(data.get("adres") or "").strip() or None,
            tip=tip,
            tenant_id=tenant_id,
        )
        if sonuc.get("basarili"):
            if aktif_user:
                background_tasks.add_task(_billable_kaydet, aktif_user, "CARI_GUNCELLENDI",
                                          f"cari_id={cari_id}", 1, "parasut",
                                          {"cari_id": cari_id}, None)
            db = SessionLocal()
            try:
                _islem_log(db, "CARI_GUNCELLENDI", cari_adi=cari_adi_kaynak or str(cari_id),
                           aciklama=f"Güncellenen: {cari_id}", request=request, user=aktif_user)
            finally:
                db.close()
            return JSONResponse(sonuc)
        return JSONResponse(sonuc, status_code=500)
    except Exception as e:
        logger.error(f"Cari güncelle endpoint hatası: {e}")
        return JSONResponse({"basarili": False, "hata": _guvenli_hata(e)}, status_code=500)
