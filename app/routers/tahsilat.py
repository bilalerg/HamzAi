# app/routers/tahsilat.py — Tahsilat, Ödeme, Hesap İşlemleri

import json as _json
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.database import SessionLocal
from app.core.logger import logger
from app.auth import mevcut_kullanici
from app.routers.deps import (
    _islem_log, _usage_kaydet, _billable_kaydet,
    _tenant_id_from_user, _guvenli_hata, tr_para
)

router = APIRouter()


# ── TAHSİLAT ─────────────────────────────────────────────────────────

@router.post("/api/tahsilat")
async def tahsilat_ekle_endpoint(request: Request, background_tasks: BackgroundTasks):
    data      = await request.json()
    cari_adi  = data.get("cari_adi", "").strip()
    tutar     = data.get("tutar", 0)
    aciklama  = data.get("aciklama", "")
    tarih     = data.get("tarih", "")
    onaylandi = data.get("onaylandi", False)

    if not cari_adi or not tutar:
        return JSONResponse({"basarili": False, "cevap": "Cari adı ve tutar gerekli."})

    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    try:
        from app.muhasebe import async_tahsilat_ekle, async_cari_id_bul, async_cari_detay_getir, async_cari_tipi_bul, saglayici_adi
        from app.core.tenant import get_tenant_ozellik
        from datetime import datetime

        # Osman eski akış (dogru_parasut_akisi=False) → tip kontrolü atlanır
        dogru_akis = get_tenant_ozellik(tenant_id, "dogru_parasut_akisi", varsayilan=True)

        if not onaylandi and dogru_akis:
            # ── Cari tip kontrolü: tedarikçiye tahsilat eklenmez ─────────
            #     (Sadece yeni akış tenant'larında — Osman'da atlanır)
            try:
                tip = await async_cari_tipi_bul(cari_adi, tenant_id=tenant_id)
                if tip == "supplier":
                    return JSONResponse({
                        "basarili": False, "yanlis_tip": True,
                        "onerilen_akis": "odeme",
                        "cevap": (
                            f"⚠️ '{cari_adi}' bir tedarikçi.\n\n"
                            f"Tedarikçilere TAHSİLAT değil ÖDEME eklenir.\n"
                            f"Ödeme ekranına yönlendirilecek."
                        )
                    })
            except Exception as tip_err:
                logger.warning(f"Cari tip kontrolü atlandı: {tip_err}")

            try:
                cari_id = await async_cari_id_bul(cari_adi, tenant_id=tenant_id)
                if cari_id:
                    detay         = await async_cari_detay_getir(cari_id, tenant_id=tenant_id)
                    guncel_bakiye = float(detay.get("bakiye", 0))
                    girilen_tutar = float(tutar)
                    if guncel_bakiye > 0 and girilen_tutar > guncel_bakiye:
                        return JSONResponse({
                            "basarili":      False, "fazla_tutar": True,
                            "guncel_bakiye": guncel_bakiye, "girilen_tutar": girilen_tutar,
                            "cevap": (f"Fazla tutar uyarısı!\n\n{cari_adi}\n"
                                      f"Güncel bakiye: {tr_para(guncel_bakiye,2)} TL\n"
                                      f"Girilen tutar: {tr_para(girilen_tutar,2)} TL\n\n"
                                      f"Girilen tutar bakiyeden {tr_para(girilen_tutar - guncel_bakiye,2)} TL fazla. "
                                      f"Yine de devam etmek istiyor musunuz?")
                        })
            except Exception as bakiye_err:
                logger.warning(f"Bakiye kontrolü atlandı: {bakiye_err}")

        tarih_str = None
        if tarih and tarih.strip():
            for fmt in ["%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"]:
                try:
                    tarih_str = datetime.strptime(tarih.strip(), fmt).strftime("%Y-%m-%d")
                    break
                except Exception:
                    continue

        sonuc = await async_tahsilat_ekle(cari_adi, float(tutar), aciklama or f"{cari_adi} tahsilat", tarih_str, tenant_id=tenant_id)

        if sonuc.get("basarili"):
            if aktif_user:
                background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                           "TAHSILAT", f"cari={cari_adi} | {tutar} TL",
                                           1, aktif_user.get("profaix_user_id"))
                background_tasks.add_task(_billable_kaydet, aktif_user, "TAHSILAT_EKLENDI",
                                          f"cari={cari_adi} | {tutar} TL", 1, "parasut",
                                          {"cari_adi": cari_adi, "tutar": tutar, "tarih": tarih_str})
            db = SessionLocal()
            try:
                _islem_log(db, "TAHSILAT_EKLENDI", cari_adi=cari_adi, tutar=int(float(tutar)),
                           aciklama=f"Tarih: {tarih or 'Bugün'} | {aciklama or ''}",
                           request=request, user=aktif_user)
            finally:
                db.close()

            # Kısmi başarı / fallback uyarısı
            uyari = ""
            if sonuc.get("basarisiz_fatura", 0) > 0:
                uyari = f"\n⚠️ {sonuc['basarisiz_fatura']} faturaya tahsilat eklenemedi, kontrol edin."
            if sonuc.get("kalan_odenmedi", 0) > 0:
                uyari += f"\n⚠️ {tr_para(sonuc['kalan_odenmedi'],2)} TL ödenemedi."
            yontem_bilgi = ""
            if sonuc.get("yontem") == "cari_hesap":
                yontem_bilgi = "\nℹ️ Açık fatura yoktu → cari hesabına direkt tahsilat olarak kaydedildi (avans)."

            return JSONResponse({
                "basarili": True,
                "cevap": (f"✅ Tahsilat kaydedildi!\n\n🏢 Cari: {cari_adi}\n"
                          f"💰 Tutar: {tr_para(float(tutar),2)} TL\n"
                          f"📅 Tarih: {datetime.strptime(tarih_str, '%Y-%m-%d').strftime('%d.%m.%Y') if tarih_str else 'Bugün'}\n"
                          f"{saglayici_adi()} sistemine kaydedildi.{yontem_bilgi}{uyari}")
            })
        else:
            return JSONResponse({"basarili": False,
                                  "cevap": f"❌ Tahsilat eklenemedi: {sonuc.get('hata', 'Bilinmeyen hata')}"})
    except Exception as e:
        logger.error(f"Tahsilat endpoint hatası: {e}")
        return JSONResponse({"basarili": False, "cevap": f"❌ Hata: {_guvenli_hata(e)}"})


# ── TAHSİLAT SİL ─────────────────────────────────────────────────────

@router.post("/api/tahsilat-sil")
async def tahsilat_sil_endpoint(request: Request, background_tasks: BackgroundTasks):
    """
    Bir tahsilat/ödeme kaydını Paraşüt'ten siler.
    Body: {odeme_id, transaction_id?}
    Paraşüt dökümantasyonu: tahsilat silmek için transaction_id gerekli.
    Frontend fatura detayından gelirken her ikisini de gönderir.
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    data       = await request.json()
    odeme_id        = str(data.get("odeme_id") or "").strip()
    transaction_id  = str(data.get("transaction_id") or "").strip() or None
    if not odeme_id and not transaction_id:
        return JSONResponse({"basarili": False, "hata": "odeme_id veya transaction_id zorunlu"}, status_code=400)
    try:
        from app.muhasebe import async_tahsilat_sil
        sonuc = await async_tahsilat_sil(odeme_id, tenant_id=tenant_id, transaction_id=transaction_id)
        if sonuc.get("basarili"):
            if aktif_user:
                background_tasks.add_task(_billable_kaydet, aktif_user, "TAHSILAT_SILINDI",
                                          f"odeme={odeme_id}", 1, "parasut",
                                          {"odeme_id": odeme_id}, f"tahsilat_sil:{odeme_id}")
            db = SessionLocal()
            try:
                _islem_log(db, "TAHSILAT_SILINDI", aciklama=f"Ödeme silindi: {odeme_id}",
                           request=request, user=aktif_user)
            finally:
                db.close()
            return JSONResponse(sonuc)
        return JSONResponse(sonuc, status_code=500)
    except Exception as e:
        logger.error(f"Tahsilat sil endpoint hatası: {e}")
        return JSONResponse({"basarili": False, "hata": _guvenli_hata(e)}, status_code=500)


# ── ÖDEME EKLE ────────────────────────────────────────────────────────

@router.post("/api/odeme-ekle")
async def odeme_ekle_endpoint(request: Request, background_tasks: BackgroundTasks):
    """Alış faturasına (purchase_bill) ödeme ekler."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    data       = await request.json()
    fatura_id  = (data.get("fatura_id") or "").strip()
    tutar      = float(data.get("tutar") or 0)
    kasa_id    = data.get("kasa_id")
    tarih      = data.get("tarih")
    notlar     = data.get("notlar")

    if not fatura_id or tutar <= 0:
        return JSONResponse({"hata": "fatura_id ve tutar zorunlu"}, status_code=400)

    # Not: Ödeme her zaman purchase_bills payment'e eklenir. Müşteri seçim
    # kontrolü UI tarafında yapılır (form müşteri fatura seçemez zaten).
    from app.muhasebe import async_alıs_faturasi_odeme_ekle
    sonuc = await async_alıs_faturasi_odeme_ekle(
        fatura_id=fatura_id, tutar=tutar, kasa_id=kasa_id,
        tarih=tarih, notlar=notlar, tenant_id=tenant_id
    )
    if sonuc.get("basarili"):
        if aktif_user:
            background_tasks.add_task(_billable_kaydet, aktif_user, "ODEME_EKLENDI",
                                      f"fatura={fatura_id} | {int(tutar)} TL", 1, "parasut",
                                      {"fatura_id": fatura_id, "tutar": int(tutar)},
                                      f"odeme:{fatura_id}:{int(tutar)}")
        return JSONResponse(sonuc)
    return JSONResponse(sonuc, status_code=500)


# ── CARİ ÖDEME ────────────────────────────────────────────────────────

@router.post("/api/cari-odeme")
async def cari_odeme_endpoint(request: Request, background_tasks: BackgroundTasks):
    """
    Fatura olmadan tedarikçi cari hesabına direkt ödeme.
    Paraşüt: POST /contacts/{id}/contact_credit_transactions
    Body: {cari_adi VEYA cari_id, tutar, aciklama?, tarih?, kasa_id?}
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    data       = await request.json()
    cari_adi   = (data.get("cari_adi") or "").strip()
    cari_id    = str(data.get("cari_id") or "").strip() or None
    tutar      = float(data.get("tutar") or 0)
    aciklama   = data.get("aciklama")
    tarih      = data.get("tarih")
    kasa_id    = data.get("kasa_id")

    if (not cari_id and not cari_adi) or tutar <= 0:
        return JSONResponse(
            {"basarili": False, "hata": "cari_adi (veya cari_id) ve tutar > 0 zorunlu"},
            status_code=400,
        )
    try:
        from app.muhasebe import async_cari_bazli_odeme, async_cari_id_bul
        if not cari_id:
            cari_id = await async_cari_id_bul(cari_adi, tenant_id=tenant_id)
            if not cari_id:
                return JSONResponse({"basarili": False, "hata": f"'{cari_adi}' bulunamadı"}, status_code=404)

        sonuc = await async_cari_bazli_odeme(
            cari_id=str(cari_id), tutar=tutar, aciklama=aciklama,
            tarih=tarih, kasa_id=kasa_id, tenant_id=tenant_id,
        )
        if sonuc.get("basarili"):
            if aktif_user:
                background_tasks.add_task(_billable_kaydet, aktif_user, "ODEME_EKLENDI",
                                          f"cari={cari_adi or cari_id} | {int(tutar)} TL", 1, "parasut",
                                          {"cari_id": cari_id, "tutar": int(tutar), "yontem": "cari_hesap"},
                                          None)
            db = SessionLocal()
            try:
                _islem_log(db, "ODEME_EKLENDI", cari_adi=cari_adi or str(cari_id),
                           tutar=int(tutar),
                           aciklama=f"Cari hesabına direkt ödeme | {aciklama or ''}",
                           request=request, user=aktif_user)
            finally:
                db.close()
            return JSONResponse({
                "basarili": True,
                "id": sonuc.get("id"),
                "cevap": (f"✅ Ödeme kaydedildi!\n\n🏢 Cari: {cari_adi or cari_id}\n"
                          f"💰 Tutar: {tr_para(tutar,2)} TL\n"
                          f"ℹ️ Cari hesabına direkt ödeme olarak kaydedildi.")
            })
        return JSONResponse(sonuc, status_code=500)
    except Exception as e:
        logger.error(f"Cari ödeme endpoint hatası: {e}")
        return JSONResponse({"basarili": False, "hata": _guvenli_hata(e)}, status_code=500)


# ── HESAPLAR ──────────────────────────────────────────────────────────

@router.get("/api/hesaplar")
async def hesaplar_getir(request: Request):
    """Kasa ve banka hesaplarını listeler."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    from app.muhasebe import async_hesap_listesi_getir
    try:
        hesaplar = await async_hesap_listesi_getir(tenant_id=tenant_id)
        return JSONResponse({"hesaplar": hesaplar, "toplam": len(hesaplar)})
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)


# ── PARA TRANSFERİ ───────────────────────────────────────────────────

@router.post("/api/para-transferi")
async def para_transferi_endpoint(request: Request, background_tasks: BackgroundTasks):
    """Kasa/banka hesapları arasında para transferi."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    data       = await request.json()
    kaynak_id  = (data.get("kaynak_id") or "").strip()
    hedef_id   = (data.get("hedef_id") or "").strip()
    tutar      = float(data.get("tutar") or 0)
    tarih      = data.get("tarih")
    aciklama   = data.get("aciklama")

    if not kaynak_id or not hedef_id or tutar <= 0:
        return JSONResponse({"hata": "kaynak_id, hedef_id ve tutar zorunlu"}, status_code=400)

    from app.muhasebe import async_para_transferi
    sonuc = await async_para_transferi(
        kaynak_id=kaynak_id, hedef_id=hedef_id, tutar=tutar,
        tarih=tarih, aciklama=aciklama, tenant_id=tenant_id
    )
    if sonuc.get("basarili"):
        if aktif_user:
            background_tasks.add_task(_billable_kaydet, aktif_user, "PARA_TRANSFERI",
                                      f"{int(tutar)} TL transfer", 1, "parasut",
                                      {"kaynak_id": kaynak_id, "hedef_id": hedef_id, "tutar": int(tutar)},
                                      None)
        return JSONResponse(sonuc)
    return JSONResponse(sonuc, status_code=500)


# ── STOK HAREKETLERİ ─────────────────────────────────────────────────

@router.get("/api/stok-hareketleri")
async def stok_hareketleri_endpoint(request: Request, urun_id: str = None, limit: int = 50):
    """Stok giriş/çıkış hareketleri."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    from app.muhasebe import async_stok_hareketleri_getir
    from app.core.database import SessionLocal, Waybill, WeighTicket
    try:
        hareketler = await async_stok_hareketleri_getir(urun_id=urun_id, limit=limit, tenant_id=tenant_id)

        # Paraşüt include=contact ile firma adı zaten gelir. Yine de
        # contact ilişkisi boş olabilen hareketler için (eski kayıtlar /
        # contact filtresinin desteklemediği durumlar) DB üzerinden
        # shipment_document → WeighTicket.firma ile fallback yapılır.
        eksik_irs_ids = [
            h["irsaliye_id"] for h in hareketler
            if h.get("irsaliye_id") and not h.get("firma")
        ]
        if eksik_irs_ids:
            db = SessionLocal()
            try:
                kayitlar = (
                    db.query(Waybill.parasut_irsaliye_id, WeighTicket.firma)
                    .join(WeighTicket, Waybill.ticket_id == WeighTicket.id)
                    .filter(Waybill.parasut_irsaliye_id.in_(eksik_irs_ids))
                    .all()
                )
                firma_map = {r.parasut_irsaliye_id: r.firma for r in kayitlar}
                for h in hareketler:
                    if not h.get("firma"):
                        h["firma"] = firma_map.get(h.get("irsaliye_id"), "")
            finally:
                db.close()

        return JSONResponse({"hareketler": hareketler, "toplam": len(hareketler)})
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)
