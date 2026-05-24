# app/routers/chat.py — Sohbet API

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.logger import logger
from app.auth import mevcut_kullanici, audit_kaydet
from app.routers.deps import (
    canonical_chat_session_id, _usage_kaydet, _billable_kaydet
)

router = APIRouter()

# Intent → gereken yetki (RBAC). Listede olmayan intent (none) herkese açık.
_INTENT_YETKI = {
    "bekleyen_irsaliyeler": "fatura", "toplu_fatura": "fatura", "fatura_sorgula": "fatura",
    "satis_faturasi_kes": "fatura", "alis_faturasi_kes": "fatura", "fatura_iptal": "fatura",
    "e_fatura_kes": "fatura",
    "tahsilat_ekle": "tahsilat", "odeme_ekle": "tahsilat",
    "yeni_cari": "cari", "cari_detay": "cari", "musteri_bakiyeleri": "cari",
    "hesap_ozeti": "rapor", "gunluk_ozet": "rapor", "son_fisler": "rapor",
}


@router.post("/api/chat")
async def chat_api(request: Request, background_tasks: BackgroundTasks):
    data       = await request.json()
    mesaj      = data.get("mesaj", "").strip()
    session_id = data.get("session_id", "web_user")
    aktif_user = mevcut_kullanici(request)
    beklenen   = canonical_chat_session_id(aktif_user)
    if aktif_user and beklenen:
        session_id = beklenen
    if not mesaj:
        return JSONResponse({"cevap": "Mesaj boş olamaz.", "intent": "none"})
    try:
        from app.chatbox.llm_handler import soru_cevapla, session_kaydet_bg, SESSION_UI_RESTORE_INTENTS
        from app.core.yetki import yetkiler_getir, yetki_var
        yetkiler = yetkiler_getir(aktif_user.get("rol")) if aktif_user else None
        sonuc = soru_cevapla(mesaj, session_id, yetkiler=yetkiler)
        intent = sonuc.get("intent") or "none"
        params = sonuc.get("params") or {}
        # ── YETKI KAPISI: yetkisiz intent'i engelle ──
        gereken = _INTENT_YETKI.get(intent)
        if gereken and aktif_user and not yetki_var(aktif_user.get("rol"), gereken):
            logger.info(f"Chat intent engellendi: {intent} (yetki {gereken} yok) user={aktif_user.get('username')}")
            return JSONResponse({"cevap": "Bu işlem için yetkiniz yok. Sadece fiş fotoğrafı yükleyebilirsiniz.",
                                  "intent": "none"})
        ui_kind = intent if intent != "none" and intent in SESSION_UI_RESTORE_INTENTS else None
        background_tasks.add_task(
            session_kaydet_bg, session_id, mesaj, sonuc.get("mesaj", ""),
            ui_kind, params if ui_kind else None,
        )
        if aktif_user and sonuc.get("intent") != "none":
            background_tasks.add_task(audit_kaydet, aktif_user["id"], aktif_user["username"],
                                       sonuc.get("intent", ""), sonuc.get("params", {}),
                                       request.client.host if request.client else None)
        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                       "CHAT", f"intent={sonuc.get('intent','none')} | {mesaj[:80]}",
                                       1, aktif_user.get("profaix_user_id"))
            background_tasks.add_task(_billable_kaydet, aktif_user, "AI_CHAT",
                                      f"intent={sonuc.get('intent','none')}", 1, "chat",
                                      {"intent": sonuc.get("intent", "none"), "mesaj": mesaj[:120]})
        return JSONResponse({"cevap": sonuc.get("mesaj", ""), "intent": sonuc.get("intent", "none"),
                              "params": sonuc.get("params", {})})
    except Exception as e:
        logger.error(f"Chat API hatası: {e}")
        return JSONResponse({"cevap": "❌ Bir hata oluştu, lütfen tekrar deneyin.", "intent": "none"})
