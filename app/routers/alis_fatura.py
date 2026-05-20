# app/routers/alis_fatura.py — Alış Faturaları & E-Fatura

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.logger import logger
from app.auth import mevcut_kullanici
from app.routers.deps import (
    _billable_kaydet, _tenant_id_from_user, _guvenli_hata
)

router = APIRouter()


@router.post("/api/alis-faturasi-kes")
async def alis_faturasi_kes_endpoint(request: Request, background_tasks: BackgroundTasks):
    """
    Basit alış faturası keser — cari adı + tek tutar.
    Body: {cari_adi, tutar, aciklama?, issue_date?, due_date?, vat_rate?}
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    data       = await request.json()
    cari_adi   = (data.get("cari_adi") or "").strip()
    tutar      = float(data.get("tutar") or 0)
    aciklama   = data.get("aciklama")
    issue_date = data.get("issue_date") or data.get("tarih")
    due_date   = data.get("due_date") or data.get("vade")
    vat_rate   = float(data.get("vat_rate") or 0)

    if not cari_adi or tutar <= 0:
        return JSONResponse({"hata": "cari_adi ve tutar zorunlu"}, status_code=400)

    from app.parasut.client import async_alis_faturasi_basit_olustur
    sonuc = await async_alis_faturasi_basit_olustur(
        cari_adi=cari_adi, tutar=tutar, aciklama=aciklama,
        issue_date=issue_date, due_date=due_date, vat_rate=vat_rate,
        tenant_id=tenant_id,
    )
    if sonuc.get("basarili"):
        if aktif_user:
            background_tasks.add_task(_billable_kaydet, aktif_user, "ALIS_FATURASI_KESILDI",
                                      f"{cari_adi} | {int(tutar)} TL", 1, "parasut",
                                      {"cari_adi": cari_adi, "tutar": int(tutar)},
                                      f"alis_fatura:{cari_adi}:{int(tutar)}:{issue_date or ''}")
        return JSONResponse(sonuc)
    return JSONResponse(sonuc, status_code=500)


@router.post("/api/alis-faturasi-iptal")
async def alis_faturasi_iptal_endpoint(request: Request, background_tasks: BackgroundTasks):
    """Alış faturasını iptal eder."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    data       = await request.json()
    fatura_id  = (data.get("fatura_id") or "").strip()
    if not fatura_id:
        return JSONResponse({"hata": "fatura_id zorunlu"}, status_code=400)

    from app.parasut.client import async_alis_faturasi_iptal
    sonuc = await async_alis_faturasi_iptal(fatura_id, tenant_id=tenant_id)
    if sonuc.get("basarili"):
        if aktif_user:
            background_tasks.add_task(_billable_kaydet, aktif_user, "ALIS_FATURASI_IPTAL",
                                      f"fatura={fatura_id}", 1, "parasut",
                                      {"fatura_id": fatura_id}, f"alis_fatura_iptal:{fatura_id}")
        return JSONResponse(sonuc)
    return JSONResponse(sonuc, status_code=500)


@router.get("/api/alis-faturasi-detay/{fatura_id}")
async def alis_faturasi_detay_endpoint(request: Request, fatura_id: str):
    """Alış faturası detayını döner (kalemler + ödemeler)."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    from app.parasut.client import async_alis_faturasi_detay_getir
    try:
        detay = await async_alis_faturasi_detay_getir(fatura_id, tenant_id=tenant_id)
        return JSONResponse(detay)
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)


@router.get("/api/bekleyen-alis-faturalari")
async def bekleyen_alis_faturalari(request: Request):
    """Ödenmemiş alış faturalarını listeler."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    from app.parasut.client import async_son_alis_faturalari_getir
    try:
        faturalar = await async_son_alis_faturalari_getir(limit=30, tenant_id=tenant_id)
        return JSONResponse({"faturalar": faturalar, "toplam": len(faturalar)})
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)


# ── E-FATURA / E-ARŞİV RESMİLEŞTİRME ─────────────────────────────────

@router.get("/api/e-fatura-mukellef/{vkn}")
async def e_fatura_mukellef_endpoint(request: Request, vkn: str):
    """VKN'in e-fatura mükellefi olup olmadığını sorgular."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    from app.parasut.client import async_e_fatura_mukellef_kontrol
    try:
        sonuc = await async_e_fatura_mukellef_kontrol(vkn, tenant_id=tenant_id)
        return JSONResponse(sonuc)
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)


@router.post("/api/e-fatura-kes")
async def e_fatura_kes_endpoint(request: Request, background_tasks: BackgroundTasks):
    """
    Satış faturasını e-fatura veya e-arşiv olarak resmileştirir.
    Body: {fatura_id, vkn?, tckn?}
    VKN verilirse → mükellef kontrolü → e-fatura veya e-arşiv otomatik seçilir.
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    data       = await request.json()
    fatura_id  = (data.get("fatura_id") or "").strip()
    vkn        = (data.get("vkn") or "").strip() or None
    tckn       = (data.get("tckn") or "").strip() or None
    if not fatura_id:
        return JSONResponse({"hata": "fatura_id zorunlu"}, status_code=400)

    from app.parasut.client import async_fatura_resmilestir_otomatik
    sonuc = await async_fatura_resmilestir_otomatik(
        fatura_id=fatura_id, vkn=vkn, tckn=tckn,
        bekle=True, tenant_id=tenant_id,
    )
    if sonuc.get("basarili"):
        if aktif_user:
            background_tasks.add_task(_billable_kaydet, aktif_user, "E_FATURA_KESILDI",
                                      f"fatura={fatura_id} tip={sonuc.get('tip')}", 1, "parasut",
                                      {"fatura_id": fatura_id, "tip": sonuc.get("tip")},
                                      f"e_fatura:{fatura_id}")
        return JSONResponse(sonuc)
    return JSONResponse(sonuc, status_code=500)
