# app/routers/deps.py — Tüm router'lar tarafından paylaşılan yardımcı fonksiyonlar

import json as _json
import uuid
import os
from typing import Optional
from pathlib import Path
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import ADMIN_TOKEN, ENV
from app.core.database import SessionLocal
from app.core.logger import logger
from app.auth import mevcut_kullanici


def canonical_chat_session_id(user: Optional[dict]) -> Optional[str]:
    """Giriş yapmış kullanıcı için kalıcı sohbet oturum anahtarı (iframe yenilense aynı kalır)."""
    if not user:
        return None
    pid = user.get("profaix_user_id")
    if pid:
        return "pfa_" + str(pid)
    uid = user.get("id")
    if uid is not None:
        return "loc_" + str(uid)
    return None


def tr_para(sayi: float, ondalik: int = 2) -> str:
    try:
        f = f"{sayi:,.{ondalik}f}"
        return f.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(sayi)


def tr_kg(sayi: float) -> str:
    try:
        return f"{int(sayi):,}".replace(",", ".")
    except Exception:
        return str(sayi)


def _islem_log(db, islem_tipi: str, cari_adi: str = None, tutar: int = None,
               fatura_id: str = None, irsaliye_id: str = None, plaka: str = None,
               aciklama: str = None, session_id: str = None,
               request: Request = None, user: dict = None):
    try:
        from app.core.database import IslemLog
        aktif_user = user or (mevcut_kullanici(request) if request else None)
        if aktif_user and not session_id:
            session_id = canonical_chat_session_id(aktif_user)
        log = IslemLog(
            islem_tipi=islem_tipi, cari_adi=cari_adi,
            tutar=int(tutar) if tutar else None, fatura_id=fatura_id,
            irsaliye_id=irsaliye_id, plaka=plaka, aciklama=aciklama, session_id=session_id,
            username=aktif_user.get("username") if aktif_user else None,
            profaix_user_id=aktif_user.get("profaix_user_id") if aktif_user else None,
            auth_session_id=aktif_user.get("auth_session_id") if aktif_user else None,
            ip_adresi=request.client.host if request and request.client else None,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.warning(f"İşlem log yazılamadı: {e}")


def _usage_kaydet(user_id, username: str, islem_turu: str,
                  aciklama: str = None, ucretli: int = 1, profaix_user_id: str = None):
    from app.core.database import UsageLog
    db = SessionLocal()
    try:
        log = UsageLog(user_id=user_id, username=username, profaix_user_id=profaix_user_id,
                       islem_turu=islem_turu, aciklama=aciklama, ucretli=ucretli)
        db.add(log)
        db.commit()
    except Exception as e:
        logger.warning(f"Usage log yazılamadı: {e}")
    finally:
        db.close()


def _billable_kaydet(user: dict, event_type: str, description: str = None,
                     amount: int = 1, source: str = None, metadata: dict = None,
                     event_key: str = None):
    """Fiyatlandırmaya esas temiz kullanım olayı. Duplicate için event_key opsiyoneldir."""
    if not user or not event_type:
        return
    db = SessionLocal()
    try:
        from app.core.database import BillableEvent
        if event_key:
            mevcut = db.query(BillableEvent).filter(BillableEvent.event_key == event_key).first()
            if mevcut:
                return
        event = BillableEvent(
            event_id=uuid.uuid4().hex,
            event_key=event_key,
            user_id=user.get("id"),
            username=user.get("username") or user.get("full_name"),
            profaix_user_id=user.get("profaix_user_id"),
            company_id=user.get("company_id"),
            event_type=str(event_type),
            amount=int(amount or 1),
            source=source,
            description=description,
            metadata_json=_json.dumps(metadata or {}, ensure_ascii=False),
        )
        db.add(event)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Billable event yazılamadı: {e}")
    finally:
        db.close()


# Panel / hızlı menü tıklaması → billable_events (sadece kullanıcı sözlüğündeki türler).
# Ayırt için metadata.panel_acildi=True; gerçek Paraşüt işlemlerinde bu bayrak yoktur.
_PANEL_TIKLAMA_EVENT = {
    "bekleyen_irsaliyeler": "IRSALIYE_OLUSTURULDU",
    "toplu_fatura": "TOPLU_FATURA_KESILDI",
    "fatura_sorgula": "FATURA_KESILDI",
    "tahsilat_ekle": "TAHSILAT_EKLENDI",
    "yeni_cari": "CARI_OLUSTURULDU",
    "musteri_bakiyeleri": "AI_CHAT",
    "hesap_ozeti": "AI_CHAT",
    "gunluk_ozet": "AI_CHAT",
    "son_fisler": "AI_CHAT",
    "fatura_detay": "FATURA_KESILDI",
    "cari_cache_yenile": "AI_CHAT",
    "hesaplar": "AI_CHAT",
    "stok_hareketleri": "AI_CHAT",
    "gelir_gider_raporu": "AI_CHAT",
    "odeme_ekle": "TAHSILAT_EKLENDI",
    "para_transferi": "TAHSILAT_EKLENDI",
    "alis_faturasi_kes": "ALIS_FATURASI_KESILDI",
    "fatura_iptal":      "FATURA_IPTAL",
    "e_fatura_kes":      "E_FATURA_KESILDI",
}


def _admin_kontrol(request: Request) -> bool:
    # Header (servis-to-servis) veya ?token= (tarayıcıdan hızlı kontrol) kabul edilir
    token = request.headers.get("X-Admin-Token") or request.query_params.get("token")
    return bool(ADMIN_TOKEN) and token == ADMIN_TOKEN


def _tenant_id_from_user(user: Optional[dict]) -> str:
    from app.core.tenant import get_default_tenant_id
    if not user:
        return get_default_tenant_id()
    pid = user.get("profaix_user_id")
    if pid:
        return str(pid)
    return get_default_tenant_id()


def _guvenli_hata(e: Exception, genel: str = "Bir hata oluştu, lütfen tekrar deneyin.") -> str:
    """Production'da dahili hata detaylarını client'a sızdırmaz."""
    if ENV != "production":
        return str(e)
    return genel


async def _json_gather(*coros):
    """Birden fazla coroutine'i paralel çalıştır."""
    import asyncio
    return await asyncio.gather(*coros)
