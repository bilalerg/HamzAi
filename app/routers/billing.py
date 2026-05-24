# app/routers/billing.py — Kullanım & Faturalama

import json as _json
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.database import SessionLocal
from app.core.logger import logger
from app.auth import mevcut_kullanici
from app.routers.deps import (
    _admin_kontrol, _billable_kaydet, _guvenli_hata,
    _PANEL_TIKLAMA_EVENT
)

router = APIRouter()

import os as _os
# İşlem başına ücret (TL) — env ile değiştirilebilir. profaix paneli buradan okur.
ISLEM_BIRIM_TL = float(_os.getenv("ISLEM_BIRIM_TL", "4.7"))


@router.get("/api/usage")
def company_usage_endpoint(request: Request, company_id: str = None,
                           baslangic: str = None, bitis: str = None, limit: int = 200):
    """
    Şirket bazlı kullanım/işlem geçmişi — profaix.com panelinin çektiği endpoint.
    Her işlem ISLEM_BIRIM_TL (4.7 TL) olarak ücretlenir.
    X-Admin-Token (servis-to-servis) ile korunur.
    """
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    if not company_id:
        return JSONResponse({"hata": "company_id zorunlu"}, status_code=400)

    from app.core.database import SessionLocal, BillableEvent
    from sqlalchemy import func as sqlfunc
    from datetime import datetime, time as _time

    db = SessionLocal()
    try:
        q = db.query(BillableEvent).filter(BillableEvent.company_id == company_id)
        if baslangic:
            q = q.filter(BillableEvent.created_at >= datetime.combine(
                datetime.strptime(baslangic, "%Y-%m-%d").date(), _time.min))
        if bitis:
            q = q.filter(BillableEvent.created_at <= datetime.combine(
                datetime.strptime(bitis, "%Y-%m-%d").date(), _time.max))

        toplam_adet   = q.with_entities(sqlfunc.coalesce(sqlfunc.sum(BillableEvent.amount), 0)).scalar() or 0
        toplam_tutar  = round(float(toplam_adet) * ISLEM_BIRIM_TL, 2)
        kayitlar = q.order_by(BillableEvent.created_at.desc()).limit(min(max(limit, 1), 1000)).all()

        return JSONResponse({
            "company_id":    company_id,
            "birim_tl":      ISLEM_BIRIM_TL,
            "toplam_islem":  int(toplam_adet),
            "toplam_tutar":  toplam_tutar,
            "kayitlar": [
                {
                    "log_id":    e.event_id,
                    "tarih":     e.created_at.isoformat() if e.created_at else None,
                    "user_id":   e.profaix_user_id,
                    "kullanici": e.username,
                    "islem":     e.event_type,
                    "endpoint":  e.source,
                    "payload":   e.description,
                    "durum":     "success",
                    "adet":      e.amount,
                    "tutar":     round((e.amount or 1) * ISLEM_BIRIM_TL, 2),
                }
                for e in kayitlar
            ],
        })
    except Exception as e:
        logger.error(f"Company usage endpoint hatası: {e}")
        return JSONResponse({"hata": _guvenli_hata(e, "Kullanım özeti alınamadı")}, status_code=500)
    finally:
        db.close()


@router.get("/api/billing/usage/{user_id}")
def billing_usage_endpoint(user_id: str, request: Request, baslangic: str = None,
                           bitis: str = None, limit: int = 100):
    """Profaix.com ücretlendirme sistemi için temiz faturalandırılabilir olay özeti."""
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)

    from app.core.database import SessionLocal, BillableEvent
    from sqlalchemy import func as sqlfunc, or_
    from datetime import datetime, time as _time

    db = SessionLocal()
    try:
        q = db.query(BillableEvent).filter(
            or_(BillableEvent.profaix_user_id == user_id, BillableEvent.username == user_id)
        )
        if baslangic:
            start_dt = datetime.combine(datetime.strptime(baslangic, "%Y-%m-%d").date(), _time.min)
            q = q.filter(BillableEvent.created_at >= start_dt)
        if bitis:
            end_dt = datetime.combine(datetime.strptime(bitis, "%Y-%m-%d").date(), _time.max)
            q = q.filter(BillableEvent.created_at <= end_dt)

        toplam_adet = q.count()
        toplam_amount = q.with_entities(sqlfunc.coalesce(sqlfunc.sum(BillableEvent.amount), 0)).scalar() or 0
        dagilim = q.with_entities(
            BillableEvent.event_type,
            sqlfunc.count(BillableEvent.id).label("adet"),
            sqlfunc.coalesce(sqlfunc.sum(BillableEvent.amount), 0).label("amount"),
        ).group_by(BillableEvent.event_type).all()
        olaylar = q.order_by(BillableEvent.created_at.desc()).limit(min(max(limit, 1), 500)).all()

        return JSONResponse({
            "user_id": user_id,
            "baslangic": baslangic,
            "bitis": bitis,
            "toplam_olay": toplam_adet,
            "toplam_birim": int(toplam_amount),
            "dagilim": [
                {"event_type": row.event_type, "adet": row.adet, "birim": int(row.amount)}
                for row in dagilim
            ],
            "olaylar": [
                {
                    "event_id": e.event_id,
                    "event_type": e.event_type,
                    "amount": e.amount,
                    "username": e.username,
                    "profaix_user_id": e.profaix_user_id,
                    "source": e.source,
                    "description": e.description,
                    "metadata": _json.loads(e.metadata_json) if e.metadata_json else {},
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in olaylar
            ],
        })
    except Exception as e:
        logger.error(f"Billing usage endpoint hatası: {e}")
        return JSONResponse({"hata": _guvenli_hata(e, "Billing özeti alınamadı")}, status_code=500)
    finally:
        db.close()


@router.post("/api/billing/panel-click")
async def billing_panel_click(request: Request, background_tasks: BackgroundTasks):
    """Sol menü / panelden açılan araçlar için ücretlendirme kaydı (her tıklama bir olay)."""
    aktif_user = mevcut_kullanici(request)
    if not aktif_user:
        return JSONResponse({"hata": "Bu işlem için giriş yapmanız gerekiyor.", "code": "AUTH_REQUIRED"}, status_code=401)
    try:
        data = await request.json()
    except Exception:
        data = {}
    panel = (data.get("panel") or "").strip()
    event_type = _PANEL_TIKLAMA_EVENT.get(panel)
    if not event_type:
        return JSONResponse({"hata": "Geçersiz panel.", "code": "UNKNOWN_PANEL"}, status_code=400)
    detail = data.get("detail")
    meta = detail if isinstance(detail, dict) else {}
    meta = {"panel": panel, "panel_acildi": True, **meta}
    background_tasks.add_task(
        _billable_kaydet,
        aktif_user,
        event_type,
        f"panel={panel}",
        1,
        "panel",
        meta,
        None,
    )
    return JSONResponse({"ok": True})


@router.get("/api/kullanim-ozeti/{user_id}")
def kullanim_ozeti_endpoint(user_id: str, request: Request):
    from app.core.database import SessionLocal, UsageLog
    from sqlalchemy import func as sqlfunc, text, or_
    from datetime import date, timedelta
    # Kullanıcı yalnızca kendi özetine erişebilir; adminler herkese bakabilir.
    aktif_user = mevcut_kullanici(request)
    if not _admin_kontrol(request):
        kendi_id       = str(aktif_user.get("profaix_user_id") or "")
        kendi_username = str(aktif_user.get("username") or "")
        if user_id not in {kendi_id, kendi_username, str(aktif_user.get("id", ""))}:
            return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=403)
    db = SessionLocal()
    try:
        from datetime import datetime, time as _time
        bugun              = date.today()
        bu_ay_baslangic    = datetime.combine(bugun.replace(day=1), _time.min)
        gecen_ay_son       = bugun.replace(day=1)
        gecen_ay_baslangic = datetime.combine((gecen_ay_son - timedelta(days=1)).replace(day=1), _time.min)
        filtre  = or_(UsageLog.profaix_user_id == user_id, UsageLog.username == user_id)
        toplam  = db.query(UsageLog).filter(filtre, UsageLog.ucretli == 1).count()
        dagilim = db.query(UsageLog.islem_turu, sqlfunc.count(UsageLog.id).label("adet")).filter(
            filtre, UsageLog.ucretli == 1).group_by(UsageLog.islem_turu).all()
        bu_ay   = db.query(UsageLog).filter(filtre, UsageLog.ucretli == 1, UsageLog.created_at >= bu_ay_baslangic).count()
        gecen_ay = db.query(UsageLog).filter(filtre, UsageLog.ucretli == 1,
                                              UsageLog.created_at >= gecen_ay_baslangic,
                                              UsageLog.created_at < bu_ay_baslangic).count()
        gunluk = db.execute(text("""
            SELECT DATE(created_at) as gun, COUNT(*) as adet FROM usage_logs
            WHERE (profaix_user_id = :uid OR username = :uid) AND ucretli = 1
              AND created_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(created_at) ORDER BY gun DESC
        """), {"uid": user_id}).fetchall()
        ilk_kayit       = db.query(UsageLog).filter(filtre).first()
        gosterilen_isim = ilk_kayit.username if ilk_kayit else user_id
        return JSONResponse({"user_id": user_id, "username": gosterilen_isim, "toplam_ucretli": toplam,
                              "bu_ay": bu_ay, "gecen_ay": gecen_ay,
                              "islem_detay": {row.islem_turu: row.adet for row in dagilim},
                              "gunluk_son30": [{"tarih": str(row.gun), "adet": row.adet} for row in gunluk]})
    except Exception as e:
        logger.error(f"Kullanım özeti hatası: {e}")
        return JSONResponse({"hata": _guvenli_hata(e, "Sunucu hatası")}, status_code=500)
    finally:
        db.close()
