# app/routers/toplu.py — Toplu fiş içe alma (Excel/PDF) + kontrol + toplu irsaliye

import asyncio
from fastapi import APIRouter, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.database import SessionLocal
from app.core.logger import logger
from app.auth import mevcut_kullanici
from app.routers.deps import (
    _islem_log, _usage_kaydet, _tenant_id_from_user, _guvenli_hata,
)

router = APIRouter()

_MAX_BOYUT = 15 * 1024 * 1024  # 15MB


@router.post("/api/toplu/onizle")
async def toplu_onizle(request: Request, file: UploadFile = File(...)):
    """
    Excel/CSV/PDF yükle → ayrıştır + sistemdeki fişlerle mutabakat.
    Kayıt OLUŞTURMAZ — sadece önizleme/kontrol döner.
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    try:
        icerik = await file.read()
        if len(icerik) > _MAX_BOYUT:
            return JSONResponse({"basarili": False, "hata": "Dosya çok büyük (max 15MB)."}, status_code=400)

        from app.toplu.parser import dosya_ayristir
        from app.toplu.kontrol import mutabakat

        satirlar = await asyncio.to_thread(dosya_ayristir, icerik, file.filename or "")
        if not satirlar:
            return JSONResponse({"basarili": False, "hata": "Dosyada geçerli fiş satırı bulunamadı."}, status_code=400)

        rapor = await asyncio.to_thread(mutabakat, satirlar, tenant_id)
        return JSONResponse({"basarili": True, "dosya": file.filename, **rapor})
    except ValueError as ve:
        return JSONResponse({"basarili": False, "hata": str(ve)}, status_code=400)
    except Exception as e:
        logger.error(f"Toplu önizle hatası: {e}")
        return JSONResponse({"basarili": False, "hata": _guvenli_hata(e)}, status_code=500)


@router.post("/api/toplu/uygula")
async def toplu_uygula(request: Request, background_tasks: BackgroundTasks):
    """
    Onaylı satırlar için toplu irsaliye oluşturur.
    Body: {satirlar:[{fis_no,plaka,net_kg}], cari_adi, tarih?, resolver_tipi?}
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"basarili": False, "hata": "Geçersiz JSON"}, status_code=400)

    satirlar      = data.get("satirlar") or []
    cari_adi      = (data.get("cari_adi") or "").strip()
    tarih         = data.get("tarih")
    resolver_tipi = data.get("resolver_tipi") or "tek_cari"

    if not satirlar:
        return JSONResponse({"basarili": False, "hata": "İçe alınacak satır yok."}, status_code=400)
    # tek_cari stratejisinde cari zorunlu
    if resolver_tipi == "tek_cari" and not cari_adi:
        return JSONResponse({"basarili": False, "hata": "Cari (firma) seçilmedi."}, status_code=400)

    try:
        from app.toplu.ice_al import toplu_ice_al
        sonuc = await asyncio.to_thread(
            toplu_ice_al, satirlar, tenant_id, cari_adi, tarih, resolver_tipi
        )

        db = SessionLocal()
        try:
            _islem_log(db, "TOPLU_ICE_ALIM", cari_adi=cari_adi or None,
                       aciklama=f"{sonuc['ozet'].get('olusturuldu',0)} irsaliye / "
                                f"{sonuc['ozet'].get('atlandi',0)} atlandı / "
                                f"{sonuc['ozet'].get('hata',0)} hata",
                       request=request, user=aktif_user)
        finally:
            db.close()

        if aktif_user:
            background_tasks.add_task(
                _usage_kaydet, aktif_user["id"], aktif_user["username"],
                "TOPLU_ICE_ALIM",
                f"olusturuldu={sonuc['ozet'].get('olusturuldu',0)} cari={cari_adi}",
                sonuc["ozet"].get("olusturuldu", 0), aktif_user.get("profaix_user_id")
            )
        return JSONResponse(sonuc)
    except Exception as e:
        logger.error(f"Toplu uygula hatası: {e}")
        return JSONResponse({"basarili": False, "hata": _guvenli_hata(e)}, status_code=500)
