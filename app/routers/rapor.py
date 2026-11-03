# app/routers/rapor.py -- Raporlar (hesap ozeti, islem gecmisi, bekleyen irsaliyeler, gunluk ozet, son fisler, gelir/gider)

import json as _json

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.database import SessionLocal
from app.core.logger import logger
from app.auth import mevcut_kullanici
from app.routers.deps import (
    _islem_log, _usage_kaydet, _billable_kaydet,
    _tenant_id_from_user, _guvenli_hata, _json_gather,
    tr_para, tr_kg, canonical_chat_session_id
)

router = APIRouter()

@router.get("/api/hesap-ozeti")
async def hesap_ozeti(request: Request, background_tasks: BackgroundTasks):
    """
    Genel hesap özeti veya tek cari bakiyesi.
    ?cari=FirmaAdı ile tek cari sorgulanabilir.
    Dönem filtresi yoktur — bakiye anlık Paraşüt değeridir.
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                   "SORGU", "hesap_ozeti", 1, aktif_user.get("profaix_user_id"))
    try:
        from app.muhasebe import async_hesap_ozeti_getir, async_cari_id_bul, async_cari_detay_getir
        cari_adi = request.query_params.get("cari")
        if cari_adi:
            cari_id = await async_cari_id_bul(cari_adi, tenant_id=tenant_id)
            if not cari_id:
                return JSONResponse({"hata": f"Cari bulunamadı: {cari_adi}"})
            detay = await async_cari_detay_getir(cari_id, tenant_id=tenant_id)
            bakiye = detay.get("bakiye", 0)
            return JSONResponse({
                "toplam_cari":   1,
                "toplam_alacak": bakiye if bakiye > 0 else 0,
                "toplam_borclu": abs(bakiye) if bakiye < 0 else 0,
                "cari_adi":      detay.get("ad"),
                "bakiye":        bakiye,
            })
        return JSONResponse(await async_hesap_ozeti_getir(tenant_id=tenant_id))
    except Exception as e:
        logger.error(f"Hesap özeti hatası: {e}")
        return JSONResponse({"hata": _guvenli_hata(e)})



@router.get("/api/islem-gecmisi/{cari_adi}")
async def islem_gecmisi(cari_adi: str, request: Request, background_tasks: BackgroundTasks):
    """
    Carinin işlem geçmişi — tarih filtreli ve sayfalı.

    Query params:
        baslangic : YYYY-MM-DD
        bitis     : YYYY-MM-DD
        sayfa     : int (default 1)
        limit     : int (default 25, max 25)

    Dönüş:
        {bakiye, islemler: [...], sayfa, sonraki_sayfa_var}
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                   "SORGU", f"islem_gecmisi | cari={cari_adi}",
                                   1, aktif_user.get("profaix_user_id"))
    try:
        from app.muhasebe import async_cari_id_bul, async_islem_gecmisi_getir
        from urllib.parse import unquote
        cari_adi  = unquote(cari_adi)
        baslangic = request.query_params.get("baslangic")
        bitis     = request.query_params.get("bitis")
        sayfa     = int(request.query_params.get("sayfa", 1))
        limit     = min(int(request.query_params.get("limit", 25)), 25)
        cari_id   = await async_cari_id_bul(cari_adi, tenant_id=tenant_id)
        if not cari_id:
            return JSONResponse({"hata": f"Cari bulunamadı: {cari_adi}"})

        sonuc    = await async_islem_gecmisi_getir(cari_id, baslangic, bitis, tenant_id=tenant_id)
        islemler = sonuc.get("islemler", [])

        # Client-side sayfalama — islem_gecmisi_getir tüm işlemleri çeker,
        # burada dilime alıyoruz
        baslangic_idx    = (sayfa - 1) * limit
        bitis_idx        = baslangic_idx + limit
        sayfa_islemleri  = islemler[baslangic_idx:bitis_idx]
        sonraki_sayfa_var = len(islemler) > bitis_idx

        return JSONResponse({
            "cari_id":           sonuc.get("cari_id"),
            "cari_adi":          sonuc.get("cari_adi"),
            "bakiye":            sonuc.get("bakiye"),
            "islemler":          sayfa_islemleri,
            "toplam_islem":      len(islemler),
            "sayfa":             sayfa,
            "limit":             limit,
            "sonraki_sayfa_var": sonraki_sayfa_var,
        })
    except Exception as e:
        logger.error(f"İşlem geçmişi hatası: {e}")
        return JSONResponse({"hata": _guvenli_hata(e)})



@router.get("/api/bekleyen-irsaliyeler")
async def bekleyen_irsaliyeler(request: Request, background_tasks: BackgroundTasks):
    """
    Bekleyen irsaliyeleri tarih filtreli ve sayfalı getirir.

    Query params:
        baslangic : YYYY-MM-DD  (default: bu ayın 1'i)
        bitis     : YYYY-MM-DD  (default: bugün)
        sayfa     : int (default 1)
        sayfa_basi: int (default 25, max 25)

    Dönüş:
        {irsaliyeler: [...], toplam, sayfa, sonraki_sayfa_var}
    """
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                   "SORGU", "bekleyen_irsaliyeler", 1, aktif_user.get("profaix_user_id"))
    db = SessionLocal()
    try:
        from app.muhasebe import async_tum_bekleyen_irsaliyeleri_getir, async_cari_listesi_getir
        from app.core.database import Waybill, TicketStatus
        from datetime import date

        # Tarih filtresi — default son 12 ay (eski irsaliyeler de
        # faturalanabilir; "bu ayın 1'i" çok dar bir varsayılan idi ve
        # birkaç ay önce kesilmiş bekleyen irsaliyelerin görünmemesine
        # sebep oluyordu)
        from datetime import timedelta as _td
        bugun     = date.today()
        baslangic = request.query_params.get("baslangic", (bugun - _td(days=365)).strftime("%Y-%m-%d"))
        bitis     = request.query_params.get("bitis", bugun.strftime("%Y-%m-%d"))
        sayfa     = int(request.query_params.get("sayfa", 1))
        sayfa_basi = min(int(request.query_params.get("sayfa_basi", 25)), 25)

        tum_waybiller  = db.query(Waybill).filter(Waybill.tenant_id == tenant_id).all()
        waybill_map    = {str(w.parasut_irsaliye_id).strip(): w for w in tum_waybiller if w.parasut_irsaliye_id}
        tamamlanan_ids = {str(w.parasut_irsaliye_id).strip() for w in tum_waybiller
                          if w.status == TicketStatus.TAMAMLANDI and w.parasut_irsaliye_id}

        cariler, api_sonuc = await _json_gather(
            async_cari_listesi_getir(tenant_id=tenant_id),
            async_tum_bekleyen_irsaliyeleri_getir(baslangic, bitis, sayfa, sayfa_basi, tenant_id=tenant_id)
        )
        cari_id_to_ad     = {c["id"]: c["ad"] for c in cariler}
        gruplu            = api_sonuc.get("irsaliyeler", {})
        sonraki_sayfa_var = api_sonuc.get("sonraki_sayfa_var", False)

        sonuc = []
        for contact_id, irsaliyeler in gruplu.items():
            cari_adi = cari_id_to_ad.get(contact_id, irsaliyeler[0].get("cari_adi", "-") if irsaliyeler else "-")
            for irs in irsaliyeler:
                if str(irs["id"]).strip() in tamamlanan_ids:
                    continue
                waybill   = waybill_map.get(str(irs["id"]).strip())
                kalemler  = irs.get("kalemler", [])
                toplam_kg = irs.get("toplam_kg", 0)
                if waybill and getattr(waybill, 'malzemeler_json', None):
                    try:
                        m = _json.loads(waybill.malzemeler_json)
                        if m:
                            kalemler  = [{"aciklama": ad, "miktar_kg": kg} for ad, kg in m.items() if kg > 0]
                            toplam_kg = sum(k["miktar_kg"] for k in kalemler)
                    except Exception:
                        pass
                elif waybill and waybill.ticket and getattr(waybill.ticket, 'malzemeler', None):
                    try:
                        m = _json.loads(waybill.ticket.malzemeler)
                        if m:
                            kalemler  = [{"aciklama": ad, "miktar_kg": kg} for ad, kg in m.items() if kg > 0]
                            toplam_kg = sum(k["miktar_kg"] for k in kalemler)
                    except Exception:
                        pass
                sonuc.append({"parasut_irsaliye_id": irs["id"], "cari_adi": cari_adi,
                               "tarih": irs.get("tarih", "-"), "toplam_kg": toplam_kg,
                               "kalemler": kalemler, "aciklama": irs.get("aciklama", "-")})

        return JSONResponse({
            "irsaliyeler":       sonuc,
            "toplam":            len(sonuc),
            "sayfa":             sayfa,
            "baslangic":         baslangic,
            "bitis":             bitis,
            "sonraki_sayfa_var": sonraki_sayfa_var,
        })
    except Exception as e:
        logger.error(f"Bekleyen irsaliye hatası: {e}")
        return JSONResponse({"irsaliyeler": [], "hata": _guvenli_hata(e)})
    finally:
        db.close()



@router.get("/api/gunluk-ozet")
async def gunluk_ozet(request: Request, background_tasks: BackgroundTasks):
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                   "SORGU", "gunluk_ozet", 1, aktif_user.get("profaix_user_id"))
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus
        from datetime import date, datetime, time
        bugun_baslangic = datetime.combine(date.today(), time.min)
        bugun_fisler = db.query(WeighTicket).filter(
            WeighTicket.created_at >= bugun_baslangic,
            WeighTicket.tenant_id == tenant_id
        ).all()
        toplam_kg    = sum(t.agirlik_kg or 0 for t in bugun_fisler)
        tamamlanan   = sum(1 for t in bugun_fisler if t.status == TicketStatus.TAMAMLANDI)
        bekleyen     = sum(1 for t in bugun_fisler if t.status not in [
            TicketStatus.TAMAMLANDI, TicketStatus.IPTAL, TicketStatus.HATA])
        return JSONResponse({"tarih": date.today().strftime("%d.%m.%Y"), "fis_sayisi": len(bugun_fisler),
                              "toplam_kg": toplam_kg, "toplam_ton": round(toplam_kg / 1000, 2),
                              "tamamlanan": tamamlanan, "bekleyen": bekleyen})
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)})
    finally:
        db.close()



@router.get("/api/son-fisler")
async def son_fisler(request: Request, background_tasks: BackgroundTasks):
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                   "SORGU", "son_fisler", 1, aktif_user.get("profaix_user_id"))
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket
        fisler = db.query(WeighTicket).filter(
            WeighTicket.tenant_id == tenant_id
        ).order_by(WeighTicket.created_at.desc()).limit(10).all()
        return JSONResponse({"fisler": [
            {"id": t.id, "plaka": t.plaka or "-", "firma": t.firma or "-",
             "agirlik_kg": t.agirlik_kg or 0,
             "tarih": t.fis_tarihi.strftime("%d.%m.%Y") if t.fis_tarihi else "-",
             "status": t.status.value if t.status else "-",
             "created_at": t.created_at.strftime("%d.%m.%Y %H:%M") if t.created_at else "-"}
            for t in fisler
        ]})
    except Exception as e:
        return JSONResponse({"fisler": [], "hata": _guvenli_hata(e)})
    finally:
        db.close()



@router.get("/api/gelir-gider-raporu")
async def gelir_gider_raporu_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    baslangic: str = None,
    bitis: str = None,
):
    """Belirtilen tarih aralığı için gelir/gider raporu."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    from datetime import datetime, timedelta
    from app.muhasebe import async_gelir_gider_raporu_getir
    if not baslangic:
        baslangic = (datetime.now().replace(day=1)).strftime("%Y-%m-%d")
    if not bitis:
        bitis = datetime.now().strftime("%Y-%m-%d")
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user.get("id"), aktif_user.get("username"),
                                   "SORGU", "gelir_gider_raporu", 1, aktif_user.get("profaix_user_id"))
    try:
        rapor = await async_gelir_gider_raporu_getir(baslangic=baslangic, bitis=bitis, tenant_id=tenant_id)
        return JSONResponse(rapor)
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)


