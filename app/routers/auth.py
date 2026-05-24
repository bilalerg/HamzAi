# app/routers/auth.py — Kimlik Doğrulama, Oturum & Temel Endpoint'ler

import json as _json
from pathlib import Path
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.config import ENV, REQUIRE_SIGNED_EMBED_AUTH
from app.core.database import SessionLocal
from app.core.logger import logger
from app.auth import (
    token_uret, mevcut_kullanici, token_durumu,
    profaix_kullanici_dogrula, embed_token_coz,
    aktif_oturum_baslat, aktif_oturum_kapat
)
from app.routers.deps import (
    canonical_chat_session_id, _usage_kaydet, _guvenli_hata
)

router = APIRouter()

_HTML_PATH = Path(__file__).resolve().parent.parent.parent / "chat.html"

def _chat_html(auth_token: str = "") -> str:
    html = _HTML_PATH.read_text(encoding="utf-8")
    if not auth_token:
        return html
    bootstrap = (
        "<script>"
        "window.PROFAIX_BOOTSTRAP_TOKEN = " + _json.dumps(auth_token) + ";"
        "</script>\n"
    )
    FIRST_MODULE = '<script src="/static/js/01-state.js"></script>'
    if FIRST_MODULE in html:
        return html.replace(FIRST_MODULE, bootstrap + FIRST_MODULE, 1)
    LEGACY_SINGLE = '<script src="/static/js/chat.js"></script>'
    if LEGACY_SINGLE in html:
        return html.replace(LEGACY_SINGLE, bootstrap + LEGACY_SINGLE, 1)
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
                        request: Request, beni_hatirla: bool = True,
                        rol: str = None, company_id: str = None) -> str:
    sid = aktif_oturum_baslat("pfa:" + str(profaix_user_id), full_name, request)
    return token_uret(
        profaix_user_id=profaix_user_id,
        full_name=full_name,
        email=email,
        beni_hatirla=beni_hatirla,
        auth_session_id=sid,
        rol=rol,
        company_id=company_id,
    )


async def _embed_rol_coz(profaix_user_id: str, payload: dict):
    """Embed (iframe) girişinde rol + company_id çöz. İmzalı token'da varsa onu kullan;
    yoksa profaix /users endpoint'inden çek. Dönüş: (rol, is_active, company_id)."""
    rol = (payload or {}).get("role") or (payload or {}).get("rol")
    comp = (payload or {}).get("company_id")
    if rol and comp:
        return rol, True, comp
    try:
        k = await profaix_kullanici_dogrula(profaix_user_id)
        if k:
            return (rol or k.get("role")), bool(k.get("is_active", True)), (comp or k.get("company_id"))
    except Exception as e:
        logger.warning(f"Embed rol çözümleme hatası: {e}")
    return (rol or None), True, comp  # çözülemezse rol=None → owner grace


# ── TEMEL ENDPOINT'LER ────────────────────────────────────────────────

@router.get("/health")
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


@router.get("/status")
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


@router.get("/chat", response_class=HTMLResponse)
async def chat_sayfasi(request: Request):
    embed_token = request.query_params.get("embed_token") or request.query_params.get("token")
    if embed_token:
        payload = embed_token_coz(embed_token)
        if payload:
            profaix_user_id = str(payload.get("sub") or payload.get("user_id") or "")
            full_name = payload.get("full_name") or payload.get("name") or payload.get("username") or profaix_user_id
            email = payload.get("email", "")
            if profaix_user_id:
                rol, aktif, comp = await _embed_rol_coz(profaix_user_id, payload)
                if not aktif:
                    return HTMLResponse(
                        "<h3 style='font-family:sans-serif;text-align:center;margin-top:60px'>Hesabınız pasif. Yöneticinize başvurun.</h3>",
                        status_code=403)
                token = _profaix_token_uret(profaix_user_id, full_name, email, request, True, rol=rol, company_id=comp)
                logger.info(f"✅ İmzalı embed giriş: {full_name} ({profaix_user_id}) rol={rol}")
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
                rol=kullanici.get("role"),
                company_id=kullanici.get("company_id"),
            )
            logger.info(f"✅ Otomatik giriş: {kullanici.get('full_name')} ({user_id_param}) rol={kullanici.get('role')}")
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


@router.get("/login", response_class=HTMLResponse)
async def login_sayfasi(request: Request):
    from pathlib import Path as _Path
    _login_file = _Path(__file__).parent.parent.parent / "login.html"
    if _login_file.exists():
        return HTMLResponse(content=_login_file.read_text(encoding="utf-8"))
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://profaix.com/login")


@router.post("/api/login")
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
        embed_rol, embed_aktif, embed_comp = await _embed_rol_coz(profaix_user_id, payload)
        if not embed_aktif:
            return JSONResponse({"basarili": False, "hata": "Hesabınız pasif durumda."}, status_code=403)
        token = _profaix_token_uret(profaix_user_id, full_name, email, request, beni_hatirla,
                                    rol=embed_rol, company_id=embed_comp)
        response = JSONResponse({"basarili": True, "username": full_name,
                                  "ad_soyad": full_name, "rol": embed_rol or "owner",
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
        if not kullanici.get("is_active", True):
            return JSONResponse({"basarili": False, "hata": "Hesabınız pasif durumda. Yöneticinize başvurun."}, status_code=403)
        kullanici_rol = kullanici.get("role")
        token = _profaix_token_uret(
            kullanici["id"],
            kullanici.get("full_name", ""),
            kullanici.get("email", ""),
            request,
            beni_hatirla,
            rol=kullanici_rol,
            company_id=kullanici.get("company_id"),
        )
        logger.info(f"✅ Profaix girişi: {kullanici.get('full_name')} ({user_id}) rol={kullanici_rol}")
        background_tasks.add_task(_usage_kaydet, None, kullanici.get("full_name", user_id),
                                  "GIRIS", f"profaix_id={user_id}", 0, user_id)
        response = JSONResponse({"basarili": True, "username": kullanici.get("full_name", ""),
                                  "ad_soyad": kullanici.get("full_name", ""), "rol": kullanici_rol or "owner",
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


@router.post("/api/logout")
async def logout(request: Request):
    user = mevcut_kullanici(request)
    if user:
        principal = "pfa:" + str(user["profaix_user_id"]) if user.get("profaix_user_id") else "loc:" + str(user.get("id"))
        aktif_oturum_kapat(principal, user.get("auth_session_id"))
    response = JSONResponse({"basarili": True})
    response.delete_cookie("profaix_token", secure=True, samesite="none")
    return response


@router.get("/api/me")
async def me(request: Request):
    user, auth_reason = token_durumu(request)
    if not user:
        code = "SESSION_REPLACED" if auth_reason == "replaced" else "AUTH_REQUIRED"
        return JSONResponse({"giris_yapildi": False, "code": code})
    out = {"giris_yapildi": True, **user}
    from app.core.yetki import yetkiler_getir
    out["yetkiler"] = yetkiler_getir(user.get("rol"))
    sid = canonical_chat_session_id(user)
    if sid:
        out["chat_session_id"] = sid
    return JSONResponse(out)


@router.get("/api/session/{session_id}")
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


@router.post("/api/session/append")
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


@router.delete("/api/session/{session_id}")
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
