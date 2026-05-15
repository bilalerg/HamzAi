# main.py — Profaix FastAPI Uygulaması

import os
import tempfile
import json as _json
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from app.core.config import (
    validate_config, ENV, ADMIN_TOKEN, ALLOWED_ORIGINS,
    REQUIRE_SIGNED_EMBED_AUTH
)
from app.core.database import init_db, SessionLocal, engine
from app.core.logger import logger
from app.core.database import UsageLog

from app.auth import (
    token_uret, mevcut_kullanici, token_durumu,
    giris_zorunlu, audit_kaydet, ilk_admin_olustur,
    profaix_kullanici_dogrula, embed_token_coz,
    aktif_oturum_baslat, aktif_oturum_kapat
)
from typing import Optional


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


_HTML_PATH = Path(__file__).parent / "chat.html"

def _chat_html(auth_token: str = "") -> str:
    html = _HTML_PATH.read_text(encoding="utf-8")
    if not auth_token:
        return html
    bootstrap = (
        "<script>"
        "window.PROFAIX_BOOTSTRAP_TOKEN = " + _json.dumps(auth_token) + ";"
        "</script>\n"
    )
    return html.replace("<script>", bootstrap + "<script>", 1)


def _tokenli_chat_response(token: str) -> HTMLResponse:
    response = HTMLResponse(content=_chat_html(token))
    response.set_cookie(
        key="profaix_token",
        value=token,
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        secure=True,
        samesite="none",
    )
    return response


def _profaix_token_uret(profaix_user_id: str, full_name: str, email: str,
                        request: Request, beni_hatirla: bool = True) -> str:
    sid = aktif_oturum_baslat("pfa:" + str(profaix_user_id), full_name, request)
    return token_uret(
        profaix_user_id=profaix_user_id,
        full_name=full_name,
        email=email,
        beni_hatirla=beni_hatirla,
        auth_session_id=sid,
    )


async def _keep_alive():
    import asyncio
    import httpx
    base_url = os.getenv("BASE_URL", "https://profaix-api.onrender.com")
    await asyncio.sleep(60)
    while True:
        try:
            async with httpx.AsyncClient() as client:
                await client.get(f"{base_url}/health", timeout=10)
            logger.info("✅ Keep-alive ping gönderildi")
        except Exception as e:
            logger.warning(f"Keep-alive ping başarısız: {e}")
        await asyncio.sleep(600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    logger.info("🚀 Profaix başlatılıyor...")
    validate_config()
    asyncio.create_task(_startup_background())
    logger.info(f"✅ Profaix hazır — Ortam: {ENV}")
    yield
    logger.info("Profaix kapanıyor...")


async def _startup_background():
    import asyncio
    try:
        init_db()
    except Exception as e:
        logger.warning(f"⚠️ DB başlatma gecikmeli: {e}")
    try:
        from app.core import migrations
        db = SessionLocal()
        try:
            await asyncio.to_thread(migrations.calistir, engine, db)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"⚠️ Migration atlandı: {e}")
    try:
        ilk_admin_olustur()
    except Exception as e:
        logger.warning(f"⚠️ Admin oluşturma atlandı: {e}")
    asyncio.create_task(_keep_alive())


app = FastAPI(title="Profaix", description="Muhasebe Otomasyon Ajanı", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
)

# ── KİMLİK DOĞRULAMA MIDDLEWARE ───────────────────────────────────────────────
# /api/login, /api/logout ve /api/me herkese açık; diğer tüm /api/* giriş zorunlu.
_ACIK_API_YOLLARI = {"/api/login", "/api/logout", "/api/me"}

@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/billing/") and _admin_kontrol(request):
        return await call_next(request)
    if path.startswith("/api/") and path not in _ACIK_API_YOLLARI:
        user, auth_reason = token_durumu(request)
        if not user:
            if auth_reason == "replaced":
                return JSONResponse(
                    {
                        "hata": "Bu hesap başka bir cihazda açıldığı için bu oturum kapatıldı.",
                        "code": "SESSION_REPLACED",
                    },
                    status_code=401,
                )
            return JSONResponse(
                {"hata": "Bu işlem için giriş yapmanız gerekiyor.", "code": "AUTH_REQUIRED"},
                status_code=401,
            )
    return await call_next(request)


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
}


def _admin_kontrol(request: Request) -> bool:
    token = request.headers.get("X-Admin-Token")
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


# ── TEMEL ENDPOINT'LER ────────────────────────────────────────────────

@app.get("/health")
async def health():
    db_ok = False
    try:
        from sqlalchemy import text as _text
        db = SessionLocal()
        try:
            db.execute(_text("SELECT 1"))
            db_ok = True
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Health check DB hatası: {e}")
    return JSONResponse(
        {"status": "ok" if db_ok else "degraded", "db": db_ok, "version": "1.0.0", "env": ENV},
        status_code=200 if db_ok else 503,
    )


@app.get("/status")
async def status():
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus
        toplam      = db.query(WeighTicket).count()
        tamamlandi  = db.query(WeighTicket).filter(WeighTicket.status == TicketStatus.TAMAMLANDI).count()
        bekleyenler = db.query(WeighTicket).filter(
            WeighTicket.status.notin_([TicketStatus.TAMAMLANDI, TicketStatus.IPTAL, TicketStatus.HATA])
        ).count()
        return {"toplam_fis": toplam, "tamamlandi": tamamlandi, "bekleyenler": bekleyenler}
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


@app.get("/chat", response_class=HTMLResponse)
async def chat_sayfasi(request: Request):
    embed_token = request.query_params.get("embed_token") or request.query_params.get("token")
    if embed_token:
        payload = embed_token_coz(embed_token)
        if payload:
            profaix_user_id = str(payload.get("sub") or payload.get("user_id") or "")
            full_name = payload.get("full_name") or payload.get("name") or payload.get("username") or profaix_user_id
            email = payload.get("email", "")
            if profaix_user_id:
                token = _profaix_token_uret(profaix_user_id, full_name, email, request, True)
                logger.info(f"✅ İmzalı embed giriş: {full_name} ({profaix_user_id})")
                return _tokenli_chat_response(token)
        logger.warning("Geçersiz embed_token ile /chat isteği alındı")

    user_id_param = request.query_params.get("user_id")
    if user_id_param and not REQUIRE_SIGNED_EMBED_AUTH:
        kullanici = await profaix_kullanici_dogrula(user_id_param)
        if kullanici:
            token = _profaix_token_uret(
                kullanici["id"],
                kullanici.get("full_name", ""),
                kullanici.get("email", ""),
                request,
                True,
            )
            logger.info(f"✅ Otomatik giriş: {kullanici.get('full_name')} ({user_id_param})")
            return _tokenli_chat_response(token)
    elif user_id_param:
        logger.warning("user_id ile /chat girişi reddedildi; embed_token gerekli")

    user = mevcut_kullanici(request)
    if not user:
        from fastapi.responses import RedirectResponse
        from app.core.config import BASE_URL as _BASE_URL
        _login_url = f"{_BASE_URL}/login" if _BASE_URL else "https://profaix.com/login"
        return RedirectResponse(url=_login_url)
    token = request.cookies.get("profaix_token", "")
    return HTMLResponse(content=_chat_html(token))


@app.get("/login", response_class=HTMLResponse)
async def login_sayfasi(request: Request):
    from pathlib import Path as _Path
    _login_file = _Path(__file__).parent / "login.html"
    if _login_file.exists():
        return HTMLResponse(content=_login_file.read_text(encoding="utf-8"))
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://profaix.com/login")


@app.post("/api/login")
async def login(request: Request, background_tasks: BackgroundTasks):
    from passlib.context import CryptContext
    from app.core.database import SessionLocal, User
    data         = await request.json()
    embed_token  = data.get("embed_token", "").strip()
    user_id      = data.get("user_id", "").strip()
    username     = data.get("username", "").strip()
    sifre        = data.get("password", "").strip()
    beni_hatirla = data.get("beni_hatirla", False)
    max_age      = 60 * 60 * 24 * 7 if beni_hatirla else 60 * 60 * 8

    if embed_token:
        payload = embed_token_coz(embed_token)
        if not payload:
            return JSONResponse({"basarili": False, "hata": "Güvenli giriş tokenı geçersiz"})
        profaix_user_id = str(payload.get("sub") or payload.get("user_id") or "")
        full_name = payload.get("full_name") or payload.get("name") or payload.get("username") or profaix_user_id
        email = payload.get("email", "")
        if not profaix_user_id:
            return JSONResponse({"basarili": False, "hata": "Güvenli giriş tokenı eksik"})
        token = _profaix_token_uret(profaix_user_id, full_name, email, request, beni_hatirla)
        response = JSONResponse({"basarili": True, "username": full_name,
                                  "ad_soyad": full_name, "rol": "user",
                                  "auth_token": token})
        response.set_cookie(key="profaix_token", value=token, max_age=max_age,
                            httponly=True, secure=True, samesite="none")
        return response

    if user_id:
        if REQUIRE_SIGNED_EMBED_AUTH:
            return JSONResponse({"basarili": False, "hata": "Güvenli giriş tokenı gerekli"}, status_code=401)
        kullanici = await profaix_kullanici_dogrula(user_id)
        if not kullanici:
            return JSONResponse({"basarili": False, "hata": "Kullanıcı doğrulanamadı"})
        token = _profaix_token_uret(
            kullanici["id"],
            kullanici.get("full_name", ""),
            kullanici.get("email", ""),
            request,
            beni_hatirla,
        )
        logger.info(f"✅ Profaix girişi: {kullanici.get('full_name')} ({user_id})")
        background_tasks.add_task(_usage_kaydet, None, kullanici.get("full_name", user_id),
                                  "GIRIS", f"profaix_id={user_id}", 0, user_id)
        response = JSONResponse({"basarili": True, "username": kullanici.get("full_name", ""),
                                  "ad_soyad": kullanici.get("full_name", ""), "rol": "user",
                                  "auth_token": token})
        response.set_cookie(key="profaix_token", value=token, max_age=max_age,
                            httponly=True, secure=True, samesite="none")
        return response

    if not username or not sifre:
        return JSONResponse({"basarili": False, "hata": "Kullanıcı adı ve şifre gerekli"})

    db = SessionLocal()
    try:
        pwd  = CryptContext(schemes=["bcrypt"], deprecated="auto")
        user = db.query(User).filter(User.username == username.strip().lower(), User.aktif == 1).first()
        if not user or not pwd.verify(sifre, user.password_hash):
            return JSONResponse({"basarili": False, "hata": "Kullanıcı adı veya şifre hatalı"})
        from datetime import datetime as _dt
        user.son_giris = _dt.utcnow()
        db.commit()
        user_id_local = user.id
        user_username = user.username
        user_ad       = user.ad_soyad or user.username
        user_rol      = user.rol
    finally:
        db.close()

    from jose import jwt as _jwt
    from datetime import datetime as _dt, timedelta as _td
    from app.core.config import JWT_SECRET_KEY
    _expire = _dt.utcnow() + (_td(days=7) if beni_hatirla else _td(hours=8))
    _sid = aktif_oturum_baslat("loc:" + str(user_id_local), user_username, request)
    _token  = _jwt.encode({"sub": str(user_id_local), "username": user_username,
                            "rol": user_rol, "sid": _sid, "typ": "local",
                            "exp": _expire}, JWT_SECRET_KEY, algorithm="HS256")
    logger.info(f"✅ Local giriş: {user_username}")
    background_tasks.add_task(_usage_kaydet, user_id_local, user_username, "GIRIS",
                               f"ip={request.client.host if request.client else '?'}", 0, None)
    response = JSONResponse({
        "basarili": True,
        "username": user_username,
        "ad_soyad": user_ad,
        "rol": user_rol,
        "auth_token": _token,
    })
    response.set_cookie(key="profaix_token", value=_token, max_age=max_age,
                        httponly=True, secure=True, samesite="none")
    return response


@app.post("/api/logout")
async def logout(request: Request):
    user = mevcut_kullanici(request)
    if user:
        principal = "pfa:" + str(user["profaix_user_id"]) if user.get("profaix_user_id") else "loc:" + str(user.get("id"))
        aktif_oturum_kapat(principal, user.get("auth_session_id"))
    response = JSONResponse({"basarili": True})
    response.delete_cookie("profaix_token", secure=True, samesite="none")
    return response


@app.get("/api/me")
async def me(request: Request):
    user, auth_reason = token_durumu(request)
    if not user:
        code = "SESSION_REPLACED" if auth_reason == "replaced" else "AUTH_REQUIRED"
        return JSONResponse({"giris_yapildi": False, "code": code})
    out = {"giris_yapildi": True, **user}
    sid = canonical_chat_session_id(user)
    if sid:
        out["chat_session_id"] = sid
    return JSONResponse(out)


@app.get("/api/session/{session_id}")
async def session_gecmis_oku(request: Request, session_id: str):
    """Kayıtlı sohbet geçmişi (tam_gecmis). Sadece oturum sahibi kendi session_id'sini okuyabilir."""
    user = mevcut_kullanici(request)
    beklenen = canonical_chat_session_id(user)
    if not user or not beklenen:
        return JSONResponse({"gecmis": []}, status_code=401)
    if session_id != beklenen:
        return JSONResponse({"gecmis": []}, status_code=403)
    db = SessionLocal()
    try:
        from app.core.database import ChatSession
        row = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        if not row or not row.tam_gecmis:
            return JSONResponse({"gecmis": []})
        try:
            arr = _json.loads(row.tam_gecmis)
        except Exception:
            arr = []
        gecmis = []
        for x in arr:
            role = x.get("role")
            if role == "ui" and x.get("ui") and (x.get("ui") or {}).get("kind"):
                item = {"role": "ui", "content": x.get("content", "")}
                item["ui"] = x["ui"]
                gecmis.append(item)
                continue
            if role in ("user", "assistant") and "content" in x:
                item = {"role": role, "content": x.get("content", "")}
                if x.get("ui"):
                    item["ui"] = x["ui"]
                gecmis.append(item)
        return JSONResponse({"gecmis": gecmis})
    finally:
        db.close()


@app.post("/api/session/append")
async def session_gecmis_append(request: Request, background_tasks: BackgroundTasks):
    """Sohbete ek kullanıcı/asistan satırları (ör. fiş yükleme) — arka planda DB'ye yazılır."""
    user = mevcut_kullanici(request)
    beklenen = canonical_chat_session_id(user)
    if not user or not beklenen:
        return JSONResponse({"ok": False}, status_code=401)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    if data.get("session_id") != beklenen:
        return JSONResponse({"ok": False}, status_code=403)
    satirlar = []
    u = data.get("user")
    a = data.get("assistant")
    if u is not None and str(u).strip() != "":
        satirlar.append({"role": "user", "content": str(u)})
    if a is not None and str(a).strip() != "":
        satirlar.append({"role": "assistant", "content": str(a)})
    panel = data.get("ui_panel")
    if isinstance(panel, dict) and panel.get("kind"):
        satirlar.append({
            "role": "ui",
            "content": "",
            "ui": {"kind": str(panel["kind"]), "params": panel.get("params") or {}},
        })
    if not satirlar:
        return JSONResponse({"ok": True})
    from app.chatbox.llm_handler import session_satirlari_ekle_bg
    background_tasks.add_task(session_satirlari_ekle_bg, beklenen, satirlar)
    return JSONResponse({"ok": True})


@app.delete("/api/session/{session_id}")
async def session_gecmis_sil(request: Request, session_id: str):
    """Kullanıcının sohbet geçmişini tamamen siler."""
    user = mevcut_kullanici(request)
    beklenen = canonical_chat_session_id(user)
    if not user or not beklenen:
        return JSONResponse({"ok": False}, status_code=401)
    if session_id != beklenen:
        return JSONResponse({"ok": False}, status_code=403)
    db = SessionLocal()
    try:
        from app.core.database import ChatSession
        row = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        if row:
            row.tam_gecmis = _json.dumps([])
            db.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        db.rollback()
        return JSONResponse({"ok": False, "hata": _guvenli_hata(e)}, status_code=500)
    finally:
        db.close()


# ── CHAT ──────────────────────────────────────────────────────────────

@app.post("/api/chat")
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
        sonuc = soru_cevapla(mesaj, session_id)
        intent = sonuc.get("intent") or "none"
        params = sonuc.get("params") or {}
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


# ── FİŞ YÜKLE ────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_fis(request: Request, background_tasks: BackgroundTasks,
                     file: UploadFile = File(...), session_id: str = "web_user"):
    db       = SessionLocal()
    tmp_path = None
    aktif_user = mevcut_kullanici(request)
    if aktif_user:
        session_id = canonical_chat_session_id(aktif_user) or session_id
    tenant_id = _tenant_id_from_user(aktif_user)
    try:
        uzanti = os.path.splitext(file.filename)[1] if file.filename else ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=uzanti) as tmp:
            icerik   = await file.read()
            tmp.write(icerik)
            tmp_path = tmp.name

        if len(icerik) > 15 * 1024 * 1024:
            return JSONResponse({"cevap": "❌ Dosya çok büyük (max 15MB)."})

        from app.ocr.reader import fis_oku_async, fis_db_kaydet
        fis = await fis_oku_async(tmp_path)

        if fis.hata:
            _islem_log(db, "OCR_HATA", plaka=None, aciklama=fis.hata, session_id=session_id,
                       request=request, user=aktif_user)
            return JSONResponse({"cevap": f"❌ Fiş okunamadı: {fis.hata}\n\nFotoğrafı daha net çekip tekrar deneyin."})

        ticket     = fis_db_kaydet(fis, tmp_path, db)
        ticket.tenant_id = tenant_id
        db.commit()
        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                       "FIS_OCR", f"plaka={fis.plaka} | {fis.firma or '?'}",
                                       1, aktif_user.get("profaix_user_id"))
            background_tasks.add_task(_billable_kaydet, aktif_user, "FIS_OCR",
                                      f"plaka={fis.plaka} | {fis.firma or '?'}", 1, "ocr",
                                      {"ticket_id": ticket.id, "fis_no": fis.fis_no})
        _islem_log(db, "FIS_YUKLENDI", plaka=fis.plaka,
                   aciklama=f"Fiş No: {fis.fis_no} | {fis.net_agirlik_kg} kg | {fis.firma}",
                   session_id=session_id, request=request, user=aktif_user)

        from app.ocr.plaka_kontrol import plaka_kontrol_et, PlakaSonuc
        plaka_sonuc = plaka_kontrol_et(fis.plaka, ticket.id, db, fis_no=fis.fis_no)

        if plaka_sonuc == PlakaSonuc.DUPLICATE:
            return JSONResponse({"cevap": (
                f"⚠️ Bu fiş daha önce sisteme girilmiş!\n\n"
                f"📋 Fiş No: {fis.fis_no}\n🚗 Plaka: {fis.plaka}\n\nMükerrer kayıt tespit edildi."
            )})

        yeni_plaka_uyari = "\n🆕 Bu plaka sisteme ilk kez eklendi." if plaka_sonuc == PlakaSonuc.YENI else ""
        malzemeler       = fis.malzemeler or {}
        malzeme_ozet     = ""
        if malzemeler:
            malzeme_ozet = "\n\n📦 Malzeme Grupları:\n"
            for isim, kg in malzemeler.items():
                malzeme_ozet += f"  • {isim}: {kg:,} kg ({kg/1000:.2f} ton)\n"

        cevap = (
            f"✅ Fiş okundu!\n\n🚗 Plaka: {fis.plaka}\n📋 Fiş No: {fis.fis_no or '-'}\n"
            f"⚖️ Net Ağırlık: {fis.net_agirlik_kg:,} kg ({fis.net_agirlik_kg/1000:.2f} ton)\n"
            f"🔥 Fire: {fis.fire_kg or 0:,} kg\n📅 Fiş Tarihi: {fis.tarih or '-'}\n"
            f"🏭 OCR Firma: {fis.firma or '-'}{yeni_plaka_uyari}{malzeme_ozet}\n"
            f"🏭 Bu fiş hangi fabrikaya ait?"
        )

        # Cari listesi async çekiliyor — event loop bloklanmıyor
        from app.parasut.client import async_cari_listesi_getir
        cariler       = await async_cari_listesi_getir(tenant_id=tenant_id)
        firma_listesi = [c["ad"] for c in cariler]

        return JSONResponse({"cevap": cevap, "bekliyor": "firma_sec", "ticket_id": ticket.id,
                              "malzemeler": malzemeler, "firma_listesi": firma_listesi})
    except Exception as e:
        logger.error(f"Upload hatası: {e}")
        return JSONResponse({"cevap": f"❌ Hata oluştu: {_guvenli_hata(e)}"})
    finally:
        db.close()
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


# ── FİRMA SEÇ → İRSALİYE OLUŞTUR ────────────────────────────────────

@app.post("/api/firma-sec")
async def firma_sec(request: Request, background_tasks: BackgroundTasks):
    data          = await request.json()
    ticket_id     = data.get("ticket_id")
    secilen_firma = data.get("firma")
    malzemeler    = data.get("malzemeler", {})
    db            = SessionLocal()
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    try:
        from app.core.database import WeighTicket, Waybill
        ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()
        if not ticket:
            return JSONResponse({"cevap": "❌ Fiş bulunamadı."})
        ticket.firma = secilen_firma
        db.commit()

        # irsaliye_olustur senkron ama içinde Parasut çağrısı var
        # to_thread ile sarıyoruz
        import asyncio
        from app.parasut.irsaliye import irsaliye_olustur
        sonuc = await asyncio.to_thread(irsaliye_olustur, ticket_id, True, tenant_id)

        if not sonuc.get("basarili"):
            _islem_log(db, "IRSALIYE_HATA", cari_adi=secilen_firma, aciklama="İrsaliye oluşturulamadı",
                       request=request, user=aktif_user)
            return JSONResponse({"cevap": "❌ İrsaliye oluşturulamadı, tekrar deneyin."})

        waybill_id = sonuc["waybill_id"]
        parasut_id = sonuc.get("parasut_id", "-")
        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                       "IRSALIYE", f"cari={secilen_firma}", 1,
                                       aktif_user.get("profaix_user_id"))
            background_tasks.add_task(_billable_kaydet, aktif_user, "IRSALIYE_OLUSTURULDU",
                                      f"cari={secilen_firma}", 1, "parasut",
                                      {"waybill_id": waybill_id, "parasut_id": parasut_id},
                                      f"irsaliye:{parasut_id}")
        _islem_log(db, "IRSALIYE_OLUSTURULDU", cari_adi=secilen_firma,
                   irsaliye_id=str(parasut_id), plaka=ticket.plaka, aciklama=f"{ticket.agirlik_kg} kg",
                   request=request, user=aktif_user)

        waybill_obj = db.query(Waybill).filter(Waybill.id == waybill_id).first()
        if waybill_obj:
            if malzemeler:
                waybill_obj.malzemeler_json = _json.dumps(malzemeler, ensure_ascii=False)
            elif ticket.malzemeler:
                waybill_obj.malzemeler_json = ticket.malzemeler
            db.commit()

        malzeme_ozet = ""
        if malzemeler:
            malzeme_ozet = "\n\n📦 Malzemeler:\n"
            for isim, kg in malzemeler.items():
                malzeme_ozet += f"  • {isim}: {kg:,} kg\n"

        return JSONResponse({
            "cevap": (f"✅ {secilen_firma} seçildi, irsaliye Paraşüt'te oluşturuldu!\n"
                      f"🆔 Paraşüt ID: {parasut_id}{malzeme_ozet}\n"
                      f"Fatura kesmek için 'bekleyen irsaliyeler' yazın veya butona tıklayın."),
            "bekliyor": "bitti"
        })
    except Exception as e:
        logger.error(f"Firma seç hatası: {e}")
        return JSONResponse({"cevap": f"❌ Hata oluştu: {_guvenli_hata(e)}"})
    finally:
        db.close()


# ── CARİLER ───────────────────────────────────────────────────────────

@app.get("/api/cariler")
async def cariler_getir(request: Request, background_tasks: BackgroundTasks, refresh: bool = False):
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                   "SORGU", "cari_listesi" + ("_refresh" if refresh else ""),
                                   1, aktif_user.get("profaix_user_id"))
    try:
        from app.parasut.client import async_cari_listesi_getir, async_cari_cache_yenile
        cariler = await async_cari_cache_yenile(tenant_id=tenant_id) if refresh else await async_cari_listesi_getir(tenant_id=tenant_id)
        sonuc   = [{"id": c["id"], "ad": c["ad"], "bakiye": c.get("bakiye", 0)} for c in cariler]
        sonuc.sort(key=lambda x: x["bakiye"], reverse=True)
        return JSONResponse({"cariler": sonuc, "toplam": len(sonuc)})
    except Exception as e:
        logger.error(f"Cariler hatası: {e}")
        return JSONResponse({"cariler": [], "hata": _guvenli_hata(e)})


# ── HESAP ÖZETİ ───────────────────────────────────────────────────────

@app.get("/api/hesap-ozeti")
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
        from app.parasut.client import async_hesap_ozeti_getir, async_cari_id_bul, async_cari_detay_getir
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


@app.get("/api/islem-gecmisi/{cari_adi}")
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
        from app.parasut.client import async_cari_id_bul, async_islem_gecmisi_getir
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


# ── TAHSİLAT ─────────────────────────────────────────────────────────

@app.post("/api/tahsilat")
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
        from app.parasut.client import async_tahsilat_ekle, async_cari_id_bul, async_cari_detay_getir
        from datetime import datetime

        if not onaylandi:
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

            # Kısmi başarı uyarısı
            uyari = ""
            if sonuc.get("basarisiz_fatura", 0) > 0:
                uyari = f"\n⚠️ {sonuc['basarisiz_fatura']} faturaya tahsilat eklenemedi, kontrol edin."
            if sonuc.get("kalan_odenmedi", 0) > 0:
                uyari += f"\n⚠️ {tr_para(sonuc['kalan_odenmedi'],2)} TL ödenemedi."

            return JSONResponse({
                "basarili": True,
                "cevap": (f"✅ Tahsilat kaydedildi!\n\n🏢 Cari: {cari_adi}\n"
                          f"💰 Tutar: {tr_para(float(tutar),2)} TL\n"
                          f"📅 Tarih: {datetime.strptime(tarih_str, '%Y-%m-%d').strftime('%d.%m.%Y') if tarih_str else 'Bugün'}\n"
                          f"Paraşüt'e kaydedildi.{uyari}")
            })
        else:
            return JSONResponse({"basarili": False,
                                  "cevap": f"❌ Tahsilat eklenemedi: {sonuc.get('hata', 'Bilinmeyen hata')}"})
    except Exception as e:
        logger.error(f"Tahsilat endpoint hatası: {e}")
        return JSONResponse({"basarili": False, "cevap": f"❌ Hata: {_guvenli_hata(e)}"})


# ── YENİ CARİ ────────────────────────────────────────────────────────

@app.post("/api/cari-olustur")
async def cari_olustur_endpoint(request: Request, background_tasks: BackgroundTasks):
    import asyncio
    data          = await request.json()
    firma_adi     = data.get("firma_adi", "").strip()
    email         = data.get("email", "").strip() or None
    telefon       = data.get("telefon", "").strip() or None
    vergi_no      = data.get("vergi_no", "").strip() or None
    vergi_dairesi = data.get("vergi_dairesi", "").strip() or None
    adres         = data.get("adres", "").strip() or None

    if not firma_adi:
        return JSONResponse({"basarili": False, "cevap": "❌ Firma adı gerekli."})

    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    try:
        from app.parasut.client import cari_olustur
        sonuc = await asyncio.to_thread(cari_olustur, firma_adi, email, telefon, vergi_no, vergi_dairesi, adres, tenant_id)
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
                f"🔢 Vergi No: {vergi_no or '-'}\nParaşüt'e kaydedildi."),
                "cari_id": sonuc.get("id")})
        else:
            return JSONResponse({"basarili": False,
                                  "cevap": f"❌ Cari oluşturulamadı: {sonuc.get('hata', 'Bilinmeyen hata')}"})
    except Exception as e:
        logger.error(f"Cari oluştur hatası: {e}")
        return JSONResponse({"basarili": False, "cevap": f"❌ Hata: {_guvenli_hata(e)}"})


# ── BEKLEYENİRSALİYELER ──────────────────────────────────────────────

@app.get("/api/bekleyen-irsaliyeler")
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
        from app.parasut.client import async_tum_bekleyen_irsaliyeleri_getir, async_cari_listesi_getir
        from app.core.database import Waybill, TicketStatus
        from datetime import date

        # Tarih filtresi — default bu ayın başından bugüne
        bugun     = date.today()
        baslangic = request.query_params.get("baslangic", bugun.replace(day=1).strftime("%Y-%m-%d"))
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


async def _json_gather(*coros):
    """Birden fazla coroutine'i paralel çalıştır."""
    import asyncio
    return await asyncio.gather(*coros)


@app.get("/api/gunluk-ozet")
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


@app.get("/api/son-fisler")
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


@app.get("/api/fatura-detay/{fatura_id}")
async def fatura_detay_endpoint(fatura_id: str, request: Request, background_tasks: BackgroundTasks):
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                   "SORGU", f"fatura_detay | id={fatura_id}", 1,
                                   aktif_user.get("profaix_user_id"))
    try:
        from app.parasut.client import async_fatura_detay_getir
        return JSONResponse(await async_fatura_detay_getir(fatura_id, tenant_id=tenant_id))
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)})


@app.get("/api/cari-faturalar/{cari_adi}")
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
        from app.parasut.client import async_cari_id_bul, async_cari_faturalari_getir
        from urllib.parse import unquote
        cari_adi  = unquote(cari_adi)
        baslangic = request.query_params.get("baslangic")
        bitis     = request.query_params.get("bitis")
        sayfa     = int(request.query_params.get("sayfa", 1))
        limit     = min(int(request.query_params.get("limit", 25)), 25)
        durum     = request.query_params.get("durum")

        cari_id = await async_cari_id_bul(cari_adi, tenant_id=tenant_id)
        if not cari_id:
            return JSONResponse({"faturalar": [], "hata": "Cari bulunamadı"})

        sonuc = await async_cari_faturalari_getir(cari_id, limit, sayfa, baslangic, bitis, durum, tenant_id=tenant_id)
        return JSONResponse(sonuc)
    except Exception as e:
        return JSONResponse({"faturalar": [], "hata": _guvenli_hata(e)})


# ── TOPLU FATURA KES ─────────────────────────────────────────────────

@app.post("/api/toplu-fatura-kes")
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
        from app.parasut.client import async_cari_id_bul, async_parasut_post, async_parasut_delete, async_cari_cache_yenile, get_tenant_config
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

        from app.parasut.client import async_cari_tipi_bul
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
                                   "issue_date": bugun, "due_date": bugun, "currency": "TRL"},
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
            await async_cari_cache_yenile()
        except Exception:
            pass

        return JSONResponse({
            "cevap": f"✅ Toplu fatura kesildi!\n\n🏢 {cari_adi}\n📦 {len(irsaliye_ids)} irsaliye birleştirildi\n💵 Toplam: {int(toplam_tutar):,} TL\n🧾 Fatura No: {fatura_id}",
            "basarili": True, "fatura_no": fatura_id
        })
    except Exception as e:
        logger.error(f"Toplu fatura kes hatası: {e}")
        return JSONResponse({"cevap": f"❌ Toplu fatura kesilemedi: {_guvenli_hata(e)}", "basarili": False})
    finally:
        db.close()


# ── FATURA KES ────────────────────────────────────────────────────────

@app.post("/api/fatura-kes")
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
        from app.parasut.client import async_cari_id_bul, async_parasut_post, async_parasut_delete, async_cari_cache_yenile, get_tenant_config
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

        from app.parasut.client import async_cari_tipi_bul
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
                                   "issue_date": bugun, "due_date": bugun, "currency": "TRL"},
                    "relationships": {
                        "contact": {"data": {"type": "contacts", "id": str(contact_id)}},
                        "details": {"data": detaylar},
                        "shipment_document": {"data": {"type": "shipment_documents", "id": str(parasut_irsaliye_id)}}
                    }
                }
            }
            try:
                sonuc = await async_parasut_post(f"/{cfg['company_id']}/sales_invoices", istek, tenant_id=tenant_id)
            except Exception as e:
                logger.warning(f"İrsaliye bağlantılı fatura başarısız: {e}")
                istek["data"]["relationships"].pop("shipment_document", None)
                sonuc = await async_parasut_post(f"/{cfg['company_id']}/sales_invoices", istek, tenant_id=tenant_id)

        fatura_id = sonuc["data"]["id"]
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
        ozet += f"\n💵 Toplam: {int(toplam_tutar):,} TL\n🧾 Fatura No: {fatura_id}"

        try:
            await async_cari_cache_yenile()
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


# ── KULLANIM ÖZETİ ────────────────────────────────────────────────────

@app.get("/api/billing/usage/{user_id}")
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


@app.post("/api/billing/panel-click")
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


@app.get("/api/kullanim-ozeti/{user_id}")
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


# ── ADMIN ────────────────────────────────────────────────────────────

@app.get("/admin/irsaliye-debug/{irsaliye_id}")
def irsaliye_debug(irsaliye_id: str, request: Request):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    try:
        from app.parasut.client import parasut_get
        from app.core.config import PARASUT_COMPANY_ID
        return JSONResponse(parasut_get(f"/{PARASUT_COMPANY_ID}/shipment_documents/{irsaliye_id}",
                                         params={"include": "stock_movements,contact"}))
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)})


@app.get("/api/hesaplar")
async def hesaplar_getir(request: Request):
    """Kasa ve banka hesaplarını listeler."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    from app.parasut.client import async_hesap_listesi_getir
    try:
        hesaplar = await async_hesap_listesi_getir(tenant_id=tenant_id)
        return JSONResponse({"hesaplar": hesaplar, "toplam": len(hesaplar)})
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)


@app.get("/api/bekleyen-alis-faturalari")
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


@app.post("/api/odeme-ekle")
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

    from app.parasut.client import async_alıs_faturasi_odeme_ekle
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


@app.get("/api/stok-hareketleri")
async def stok_hareketleri_endpoint(request: Request, urun_id: str = None, limit: int = 50):
    """Stok giriş/çıkış hareketleri."""
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    from app.parasut.client import async_stok_hareketleri_getir
    try:
        hareketler = await async_stok_hareketleri_getir(urun_id=urun_id, limit=limit, tenant_id=tenant_id)
        return JSONResponse({"hareketler": hareketler, "toplam": len(hareketler)})
    except Exception as e:
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)


@app.get("/api/gelir-gider-raporu")
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
    from app.parasut.client import async_gelir_gider_raporu_getir
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


@app.post("/api/para-transferi")
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

    from app.parasut.client import async_para_transferi
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


@app.get("/admin/islem-loglari")
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


@app.get("/admin/konusmalar")
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


@app.get("/admin/accounts-debug")
async def accounts_debug(request: Request):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    try:
        from app.parasut.client import parasut_get
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

@app.post("/admin/reset-db")
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


@app.post("/admin/tenant-ekle")
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
        import json as _json
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