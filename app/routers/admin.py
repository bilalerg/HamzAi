# app/routers/admin.py — Admin Debug & Yönetim

import json as _json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.database import SessionLocal, engine
from app.core.logger import logger
from app.routers.deps import _admin_kontrol, _guvenli_hata

router = APIRouter()


@router.get("/admin/cari-debug")
def cari_debug(request: Request):
    """Paraşüt'ten cariler ham attribute'larıyla — UI'a girmeden bakmak için."""
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    try:
        from app.muhasebe import parasut_get
        from app.core.config import PARASUT_COMPANY_ID
        ozet = []
        page = 1
        tip_sayilari = {}
        while page <= 10:
            sonuc = parasut_get(
                f"/{PARASUT_COMPANY_ID}/contacts",
                params={"page[size]": 25, "page[number]": page},
            )
            data = sonuc.get("data", [])
            if not data:
                break
            for c in data:
                attr = c.get("attributes", {})
                raw_tip = attr.get("account_type")
                tip_sayilari[str(raw_tip)] = tip_sayilari.get(str(raw_tip), 0) + 1
                ozet.append({
                    "id":           c["id"],
                    "ad":           attr.get("name", "-"),
                    "account_type": raw_tip,
                    "balance":      attr.get("balance"),
                    "tax_number":   attr.get("tax_number"),
                    "contact_type": attr.get("contact_type"),
                    # tüm raw attribute anahtarlarını da göster — gizli alan var mı?
                    "raw_keys":     sorted(attr.keys()),
                })
            if len(data) < 25:
                break
            page += 1
        return JSONResponse({
            "toplam": len(ozet),
            "tip_dagilimi": tip_sayilari,
            "cariler": ozet,
        })
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)


@router.get("/admin/irsaliye-debug/{irsaliye_id}")
def irsaliye_debug(irsaliye_id: str, request: Request):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    try:
        from app.muhasebe import parasut_get
        from app.core.config import PARASUT_COMPANY_ID
        return JSONResponse(parasut_get(f"/{PARASUT_COMPANY_ID}/shipment_documents/{irsaliye_id}",
                                         params={"include": "stock_movements,contact"}))
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)})


@router.get("/admin/islem-loglari")
def islem_loglari(request: Request, limit: int = 50, islem_tipi: str = None):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    db = SessionLocal()
    try:
        from app.core.database import IslemLog
        from sqlalchemy import desc
        q      = db.query(IslemLog).order_by(desc(IslemLog.created_at))
        if islem_tipi:
            q = q.filter(IslemLog.islem_tipi == islem_tipi)
        loglar = q.limit(limit).all()
        return JSONResponse({"loglar": [
            {"id": l.id, "islem_tipi": l.islem_tipi, "cari_adi": l.cari_adi, "tutar": l.tutar,
             "fatura_id": l.fatura_id, "irsaliye_id": l.irsaliye_id, "plaka": l.plaka,
             "aciklama": l.aciklama, "tarih": l.created_at.strftime("%d.%m.%Y %H:%M") if l.created_at else "-"}
            for l in loglar
        ], "toplam": len(loglar)})
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)})
    finally:
        db.close()


@router.get("/admin/konusmalar")
def konusmalar(request: Request, limit: int = 20):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    db = SessionLocal()
    try:
        from app.core.database import ChatSession
        from sqlalchemy import desc
        sessionlar = db.query(ChatSession).order_by(desc(ChatSession.updated_at)).limit(limit).all()
        return JSONResponse({"sessionlar": [
            {"session_id": s.session_id, "isim": s.isim,
             "mesaj_sayisi": len(_json.loads(s.tam_gecmis)) if s.tam_gecmis else 0,
             "son_guncelleme": s.updated_at.strftime("%d.%m.%Y %H:%M") if s.updated_at else "-",
             "tam_gecmis": _json.loads(s.tam_gecmis) if s.tam_gecmis else []}
            for s in sessionlar
        ]})
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)})
    finally:
        db.close()


@router.get("/admin/accounts-debug")
async def accounts_debug(request: Request):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    try:
        from app.muhasebe import parasut_get
        from app.core.config import PARASUT_COMPANY_ID
        sonuc    = parasut_get(f"/{PARASUT_COMPANY_ID}/accounts")
        hesaplar = [{"id": h["id"], "ad": h.get("attributes", {}).get("name"),
                     "tip": h.get("attributes", {}).get("account_type"),
                     "para_birimi": h.get("attributes", {}).get("currency"),
                     "bakiye": h.get("attributes", {}).get("balance")}
                    for h in sonuc.get("data", [])]
        return JSONResponse({"hesaplar": hesaplar})
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)})


# ── ADMIN RESET-DB — GET'ten POST'a alındı ────────────────────────────
# GET istekleri tarayıcı history, prefetch veya log araçlarından
# yanlışlıkla tetiklenebilir. POST zorunlu kılarak bu riski ortadan kaldırıyoruz.

@router.post("/admin/reset-db")
def reset_db(request: Request):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    from sqlalchemy import text
    with engine.connect() as conn:
        for tablo in ["usage_logs", "audit_logs", "islem_logs", "chat_sessions",
                      "invoices", "waybills", "weigh_tickets", "vehicles"]:
            conn.execute(text(f"DELETE FROM {tablo}"))
        conn.commit()
    logger.info("🗑️ DB tamamen temizlendi (admin)")
    return {"status": "temizlendi", "tablolar": [
        "usage_logs", "audit_logs", "islem_logs", "chat_sessions",
        "invoices", "waybills", "weigh_tickets", "vehicles"
    ]}


@router.post("/admin/tenant-ekle")
async def tenant_ekle(request: Request):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=403)
    data = await request.json()
    tenant_id             = (data.get("tenant_id") or "").strip()
    ad                    = (data.get("ad") or "").strip()
    parasut_client_id     = (data.get("parasut_client_id") or "").strip()
    parasut_client_secret = (data.get("parasut_client_secret") or "").strip()
    parasut_username      = (data.get("parasut_username") or "").strip()
    parasut_password      = (data.get("parasut_password") or "").strip()
    parasut_company_id    = (data.get("parasut_company_id") or "").strip()
    parasut_hurda_product_id = (data.get("parasut_hurda_product_id") or "").strip() or None
    ozellikler               = data.get("ozellikler", None)  # JSON dict opsiyonel
    if isinstance(ozellikler, dict):
        ozellikler = _json.dumps(ozellikler, ensure_ascii=False)

    if not all([tenant_id, ad, parasut_client_id, parasut_client_secret,
                parasut_username, parasut_password, parasut_company_id]):
        return JSONResponse({"hata": "Eksik alan: tenant_id, ad, parasut_client_id, parasut_client_secret, "
                                      "parasut_username, parasut_password, parasut_company_id zorunlu"},
                            status_code=400)

    db = SessionLocal()
    try:
        from app.core.database import Tenant
        from app.core.tenant import invalidate_tenant_cache, _encrypt_cred
        mevcut = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if mevcut:
            return JSONResponse({"hata": f"Tenant zaten mevcut: {tenant_id}"}, status_code=409)
        tenant = Tenant(
            id=tenant_id,
            ad=ad,
            parasut_client_id=parasut_client_id,
            parasut_client_secret=_encrypt_cred(parasut_client_secret),
            parasut_username=parasut_username,
            parasut_password=_encrypt_cred(parasut_password),
            parasut_company_id=parasut_company_id,
            parasut_hurda_product_id=parasut_hurda_product_id,
            ozellikler=ozellikler,
            aktif=1,
        )
        db.add(tenant)
        db.commit()
        invalidate_tenant_cache(tenant_id)
        logger.info(f"✅ Yeni tenant eklendi: {tenant_id} ({ad})")
        return JSONResponse({"ok": True, "tenant_id": tenant_id, "ad": ad})
    except Exception as e:
        db.rollback()
        logger.error(f"Tenant ekleme hatası: {e}")
        return JSONResponse({"hata": str(e)}, status_code=500)
    finally:
        db.close()
