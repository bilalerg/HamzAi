# main.py — Profaix FastAPI Uygulaması

import os
import tempfile
import json as _json
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from app.core.config import validate_config, ENV, ADMIN_TOKEN
from app.core.database import init_db, SessionLocal, engine
from app.core.logger import logger
from app.core.database import UsageLog

from app.auth import (
    token_uret, mevcut_kullanici,
    giris_zorunlu, audit_kaydet, ilk_admin_olustur,
    profaix_kullanici_dogrula
)


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


def _chat_html() -> str:
    return _HTML_PATH.read_text(encoding="utf-8")


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
            await migrations.calistir(engine, db)
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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _islem_log(db, islem_tipi: str, cari_adi: str = None, tutar: int = None,
               fatura_id: str = None, irsaliye_id: str = None, plaka: str = None,
               aciklama: str = None, session_id: str = None):
    try:
        from app.core.database import IslemLog
        log = IslemLog(
            islem_tipi=islem_tipi, cari_adi=cari_adi,
            tutar=int(tutar) if tutar else None, fatura_id=fatura_id,
            irsaliye_id=irsaliye_id, plaka=plaka, aciklama=aciklama, session_id=session_id
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


def _session_kaydet(request: Request, islem_turu: str, aciklama: str = None):
    """Tüm endpoint çağrılarını session geçmişine kaydeder"""
    try:
        session_id = request.query_params.get("session_id")
        if not session_id:
            return

        # Session'a işlem kaydet
        from app.chatbox.llm_handler import session_kaydet_bg
        user_mesaj = f"🔍 {islem_turu}: {aciklama or ''}"
        bot_mesaj = f"✅ {islem_turu} işlemi tamamlandı."

        # Background task olarak session'a ekle
        import asyncio
        asyncio.create_task(
            asyncio.to_thread(session_kaydet_bg, session_id, user_mesaj, bot_mesaj)
        )
    except Exception as e:
        logger.warning(f"Session kaydı yapılamadı: {e}")


def _admin_kontrol(request: Request) -> bool:
    token = request.headers.get("X-Admin-Token")
    return bool(ADMIN_TOKEN) and token == ADMIN_TOKEN


# ── TEMEL ENDPOINT'LER ────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "env": ENV}


@app.get("/status")
async def status():
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus
        toplam = db.query(WeighTicket).count()
        tamamlandi = db.query(WeighTicket).filter(WeighTicket.status == TicketStatus.TAMAMLANDI).count()
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
    user_id_param = request.query_params.get("user_id")
    if user_id_param:
        kullanici = await profaix_kullanici_dogrula(user_id_param)
        if kullanici:
            token = token_uret(profaix_user_id=kullanici["id"],
                               full_name=kullanici.get("full_name", ""),
                               email=kullanici.get("email", ""), beni_hatirla=True)
            response = HTMLResponse(content=_chat_html())
            response.set_cookie(key="profaix_token", value=token, max_age=60 * 60 * 24 * 7,
                                httponly=True, secure=True, samesite="none")
            logger.info(f"✅ Otomatik giriş: {kullanici.get('full_name')} ({user_id_param})")
            return response
    user = mevcut_kullanici(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="https://profaix.com/login")
    return HTMLResponse(content=_chat_html())


@app.get("/login", response_class=HTMLResponse)
async def login_sayfasi(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://profaix.com/login")


@app.post("/api/login")
async def login(request: Request, background_tasks: BackgroundTasks):
    from passlib.context import CryptContext
    from app.core.database import SessionLocal, User
    data = await request.json()
    user_id = data.get("user_id", "").strip()
    username = data.get("username", "").strip()
    sifre = data.get("password", "").strip()
    beni_hatirla = data.get("beni_hatirla", False)
    max_age = 60 * 60 * 24 * 7 if beni_hatirla else 60 * 60 * 8

    if user_id:
        kullanici = await profaix_kullanici_dogrula(user_id)
        if not kullanici:
            return JSONResponse({"basarili": False, "hata": "Kullanıcı doğrulanamadı"})
        token = token_uret(profaix_user_id=kullanici["id"], full_name=kullanici.get("full_name", ""),
                           email=kullanici.get("email", ""), beni_hatirla=beni_hatirla)
        logger.info(f"✅ Profaix girişi: {kullanici.get('full_name')} ({user_id})")
        background_tasks.add_task(_usage_kaydet, None, kullanici.get("full_name", user_id),
                                  "GIRIS", f"profaix_id={user_id}", 0, user_id)
        response = JSONResponse({"basarili": True, "username": kullanici.get("full_name", ""),
                                 "ad_soyad": kullanici.get("full_name", ""), "rol": "user"})
        response.set_cookie(key="profaix_token", value=token, max_age=max_age,
                            httponly=True, secure=True, samesite="none")
        return response

    if not username or not sifre:
        return JSONResponse({"basarili": False, "hata": "Kullanıcı adı ve şifre gerekli"})

    db = SessionLocal()
    try:
        pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
        user = db.query(User).filter(User.username == username.strip().lower(), User.aktif == 1).first()
        if not user or not pwd.verify(sifre, user.password_hash):
            return JSONResponse({"basarili": False, "hata": "Kullanıcı adı veya şifre hatalı"})
        from datetime import datetime as _dt
        user.son_giris = _dt.utcnow()
        db.commit()
        user_id_local = user.id
        user_username = user.username
        user_ad = user.ad_soyad or user.username
        user_rol = user.rol
    finally:
        db.close()

    from jose import jwt as _jwt
    from datetime import datetime as _dt, timedelta as _td
    from app.core.config import JWT_SECRET_KEY
    _expire = _dt.utcnow() + (_td(days=7) if beni_hatirla else _td(hours=8))
    _token = _jwt.encode({"sub": str(user_id_local), "username": user_username,
                          "rol": user_rol, "exp": _expire}, JWT_SECRET_KEY, algorithm="HS256")
    logger.info(f"✅ Local giriş: {user_username}")
    background_tasks.add_task(_usage_kaydet, user_id_local, user_username, "GIRIS",
                              f"ip={request.client.host if request.client else '?'}", 0, None)
    response = JSONResponse({"basarili": True, "username": user_username, "ad_soyad": user_ad, "rol": user_rol})
    response.set_cookie(key="profaix_token", value=_token, max_age=max_age,
                        httponly=True, secure=True, samesite="none")
    return response


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"basarili": True})
    response.delete_cookie("profaix_token")
    return response


@app.get("/api/me")
async def me(request: Request):
    user = mevcut_kullanici(request)
    if not user:
        return JSONResponse({"giris_yapildi": False})
    return JSONResponse({"giris_yapildi": True, **user})


# ── CHAT ──────────────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat_api(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    mesaj = data.get("mesaj", "").strip()
    session_id = data.get("session_id", "web_user")
    if not mesaj:
        return JSONResponse({"cevap": "Mesaj boş olamaz.", "intent": "none"})
    aktif_user = mevcut_kullanici(request)
    try:
        from app.chatbox.llm_handler import soru_cevapla, session_kaydet_bg
        sonuc = soru_cevapla(mesaj, session_id)
        background_tasks.add_task(session_kaydet_bg, session_id, mesaj, sonuc.get("mesaj", ""))
        if aktif_user and sonuc.get("intent") != "none":
            background_tasks.add_task(audit_kaydet, aktif_user["id"], aktif_user["username"],
                                      sonuc.get("intent", ""), sonuc.get("params", {}),
                                      request.client.host if request.client else None)
        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                      "CHAT", f"intent={sonuc.get('intent', 'none')} | {mesaj[:80]}",
                                      1, aktif_user.get("profaix_user_id"))
        return JSONResponse({"cevap": sonuc.get("mesaj", ""), "intent": sonuc.get("intent", "none"),
                             "params": sonuc.get("params", {})})
    except Exception as e:
        logger.error(f"Chat API hatası: {e}")
        return JSONResponse({"cevap": "❌ Bir hata oluştu, lütfen tekrar deneyin.", "intent": "none"})


# ── FİŞ YÜKLE ────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_fis(request: Request, background_tasks: BackgroundTasks,
                     file: UploadFile = File(...), session_id: str = "web_user"):
    db = SessionLocal()
    tmp_path = None
    try:
        uzanti = os.path.splitext(file.filename)[1] if file.filename else ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=uzanti) as tmp:
            icerik = await file.read()
            tmp.write(icerik)
            tmp_path = tmp.name

        if len(icerik) > 15 * 1024 * 1024:
            return JSONResponse({"cevap": "❌ Dosya çok büyük (max 15MB)."})

        from app.ocr.reader import fis_oku_async, fis_db_kaydet
        fis = await fis_oku_async(tmp_path)

        if fis.hata:
            _islem_log(db, "OCR_HATA", plaka=None, aciklama=fis.hata, session_id=session_id)
            return JSONResponse({"cevap": f"❌ Fiş okunamadı: {fis.hata}\n\nFotoğrafı daha net çekip tekrar deneyin."})

        ticket = fis_db_kaydet(fis, tmp_path, db)
        aktif_user = mevcut_kullanici(request)
        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                      "FIS_OCR", f"plaka={fis.plaka} | {fis.firma or '?'}",
                                      1, aktif_user.get("profaix_user_id"))
        _islem_log(db, "FIS_YUKLENDI", plaka=fis.plaka,
                   aciklama=f"Fiş No: {fis.fis_no} | {fis.net_agirlik_kg} kg | {fis.firma}",
                   session_id=session_id)

        from app.ocr.plaka_kontrol import plaka_kontrol_et, PlakaSonuc
        plaka_sonuc = plaka_kontrol_et(fis.plaka, ticket.id, db, fis_no=fis.fis_no)

        if plaka_sonuc == PlakaSonuc.DUPLICATE:
            return JSONResponse({"cevap": (
                f"⚠️ Bu fiş daha önce sisteme girilmiş!\n\n"
                f"📋 Fiş No: {fis.fis_no}\n🚗 Plaka: {fis.plaka}\n\nMükerrer kayıt tespit edildi."
            )})

        yeni_plaka_uyari = "\n🆕 Bu plaka sisteme ilk kez eklendi." if plaka_sonuc == PlakaSonuc.YENI else ""
        malzemeler = fis.malzemeler or {}
        malzeme_ozet = ""
        if malzemeler:
            malzeme_ozet = "\n\n📦 Malzeme Grupları:\n"
            for isim, kg in malzemeler.items():
                malzeme_ozet += f"  • {isim}: {kg:,} kg ({kg / 1000:.2f} ton)\n"

        cevap = (
            f"✅ Fiş okundu!\n\n🚗 Plaka: {fis.plaka}\n📋 Fiş No: {fis.fis_no or '-'}\n"
            f"⚖️ Net Ağırlık: {fis.net_agirlik_kg:,} kg ({fis.net_agirlik_kg / 1000:.2f} ton)\n"
            f"🔥 Fire: {fis.fire_kg or 0:,} kg\n📅 Fiş Tarihi: {fis.tarih or '-'}\n"
            f"🏭 OCR Firma: {fis.firma or '-'}{yeni_plaka_uyari}{malzeme_ozet}\n"
            f"🏭 Bu fiş hangi fabrikaya ait?"
        )

        # Cari listesi async çekiliyor — event loop bloklanmıyor
        from app.parasut.client import async_cari_listesi_getir
        cariler = await async_cari_listesi_getir()
        firma_listesi = [c["ad"] for c in cariler]

        return JSONResponse({"cevap": cevap, "bekliyor": "firma_sec", "ticket_id": ticket.id,
                             "malzemeler": malzemeler, "firma_listesi": firma_listesi})
    except Exception as e:
        logger.error(f"Upload hatası: {e}")
        return JSONResponse({"cevap": f"❌ Hata oluştu: {str(e)}"})
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
    data = await request.json()
    ticket_id = data.get("ticket_id")
    secilen_firma = data.get("firma")
    malzemeler = data.get("malzemeler", {})
    db = SessionLocal()
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
        sonuc = await asyncio.to_thread(irsaliye_olustur, ticket_id, True)

        if not sonuc.get("basarili"):
            _islem_log(db, "IRSALIYE_HATA", cari_adi=secilen_firma, aciklama="İrsaliye oluşturulamadı")
            return JSONResponse({"cevap": "❌ İrsaliye oluşturulamadı, tekrar deneyin."})

        waybill_id = sonuc["waybill_id"]
        parasut_id = sonuc.get("parasut_id", "-")
        aktif_user = mevcut_kullanici(request)
        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                      "IRSALIYE", f"cari={secilen_firma}", 1,
                                      aktif_user.get("profaix_user_id"))
        _islem_log(db, "IRSALIYE_OLUSTURULDU", cari_adi=secilen_firma,
                   irsaliye_id=str(parasut_id), plaka=ticket.plaka, aciklama=f"{ticket.agirlik_kg} kg")

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
        return JSONResponse({"cevap": f"❌ Hata oluştu: {str(e)}"})
    finally:
        db.close()


# ── CARİLER ───────────────────────────────────────────────────────────

@app.get("/api/cariler")
async def cariler_getir(request: Request, background_tasks: BackgroundTasks, refresh: bool = False, auto_load: bool = False):
    aktif_user = mevcut_kullanici(request)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                  "SORGU", "cari_listesi" + ("_refresh" if refresh else ""),
                                  1, aktif_user.get("profaix_user_id"))

        # Session'a kaydet (sadece kullanıcı isteğiyle değilse)
        if not auto_load:
            _session_kaydet(request, "Cari Listesi",
                            f"Cari listesi görüntülendi{' (yenilendi)' if refresh else ''}")
    try:
        from app.parasut.client import async_cari_listesi_getir, async_cari_cache_yenile
        cariler = await async_cari_cache_yenile() if refresh else await async_cari_listesi_getir()
        sonuc = [{"id": c["id"], "ad": c["ad"], "bakiye": c.get("bakiye", 0)} for c in cariler]
        sonuc.sort(key=lambda x: x["bakiye"], reverse=True)
        return JSONResponse({"cariler": sonuc, "toplam": len(sonuc)})
    except Exception as e:
        logger.error(f"Cariler hatası: {e}")
        return JSONResponse({"cariler": [], "hata": str(e)})


# ── HESAP ÖZETİ ───────────────────────────────────────────────────────

@app.get("/api/hesap-ozeti")
async def hesap_ozeti(request: Request, background_tasks: BackgroundTasks):
    """
    Genel hesap özeti veya tek cari bakiyesi.
    ?cari=FirmaAdı ile tek cari sorgulanabilir.
    Dönem filtresi yoktur — bakiye anlık Paraşüt değeridir.
    """
    aktif_user = mevcut_kullanici(request)
    db = SessionLocal()
    try:
        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                      "SORGU", "hesap_ozeti", 1, aktif_user.get("profaix_user_id"))

            # İşlem geçmişine kaydet
            cari_adi = request.query_params.get("cari")
            _islem_log(db, "HESAP_OZETI", cari_adi=cari_adi,
                       aciklama=f"Hesap özeti sorgulandı: {cari_adi if cari_adi else 'Genel'}",
                       session_id=request.query_params.get("session_id"))

            # Session'a kaydet
            _session_kaydet(request, "Hesap Özeti",
                            f"Hesap özeti sorgulandı: {cari_adi if cari_adi else 'Genel'}")

        from app.parasut.client import async_hesap_ozeti_getir, async_cari_id_bul, async_cari_detay_getir
        cari_adi = request.query_params.get("cari")
        if cari_adi:
            cari_id = await async_cari_id_bul(cari_adi)
            if not cari_id:
                return JSONResponse({"hata": f"Cari bulunamadı: {cari_adi}"})
            detay = await async_cari_detay_getir(cari_id)
            bakiye = detay.get("bakiye", 0)
            return JSONResponse({
                "toplam_cari": 1,
                "toplam_alacak": bakiye if bakiye > 0 else 0,
                "toplam_borclu": abs(bakiye) if bakiye < 0 else 0,
                "cari_adi": detay.get("ad"),
                "bakiye": bakiye,
            })
        return JSONResponse(await async_hesap_ozeti_getir())
    except Exception as e:
        logger.error(f"Hesap özeti hatası: {e}")
        return JSONResponse({"hata": str(e)})
    finally:
        db.close()


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
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                  "SORGU", f"islem_gecmisi | cari={cari_adi}",
                                  1, aktif_user.get("profaix_user_id"))

        # Session'a kaydet
        _session_kaydet(request, "İşlem Geçmişi",
                        f"{cari_adi} için işlem geçmişi sorgulandı")
    try:
        from app.parasut.client import async_cari_id_bul, async_islem_gecmisi_getir
        from urllib.parse import unquote
        cari_adi = unquote(cari_adi)
        baslangic = request.query_params.get("baslangic")
        bitis = request.query_params.get("bitis")
        sayfa = int(request.query_params.get("sayfa", 1))
        limit = min(int(request.query_params.get("limit", 25)), 25)
        cari_id = await async_cari_id_bul(cari_adi)
        if not cari_id:
            return JSONResponse({"hata": f"Cari bulunamadı: {cari_adi}"})

        sonuc = await async_islem_gecmisi_getir(cari_id, baslangic, bitis)
        islemler = sonuc.get("islemler", [])

        # Client-side sayfalama — islem_gecmisi_getir tüm işlemleri çeker,
        # burada dilime alıyoruz
        baslangic_idx = (sayfa - 1) * limit
        bitis_idx = baslangic_idx + limit
        sayfa_islemleri = islemler[baslangic_idx:bitis_idx]
        sonraki_sayfa_var = len(islemler) > bitis_idx

        return JSONResponse({
            "cari_id": sonuc.get("cari_id"),
            "cari_adi": sonuc.get("cari_adi"),
            "bakiye": sonuc.get("bakiye"),
            "islemler": sayfa_islemleri,
            "toplam_islem": len(islemler),
            "sayfa": sayfa,
            "limit": limit,
            "sonraki_sayfa_var": sonraki_sayfa_var,
        })
    except Exception as e:
        logger.error(f"İşlem geçmişi hatası: {e}")
        return JSONResponse({"hata": str(e)})


# ── TAHSİLAT ─────────────────────────────────────────────────────────

@app.post("/api/tahsilat")
async def tahsilat_ekle_endpoint(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    cari_adi = data.get("cari_adi", "").strip()
    tutar = data.get("tutar", 0)
    aciklama = data.get("aciklama", "")
    tarih = data.get("tarih", "")
    onaylandi = data.get("onaylandi", False)
    session_id = data.get("session_id")

    if not cari_adi or not tutar:
        return JSONResponse({"basarili": False, "cevap": "Cari adı ve tutar gerekli."})

    # Session'a kaydet
    if session_id:
        try:
            from app.chatbox.llm_handler import session_kaydet_bg
            user_mesaj = f"💰 Tahsilat: {cari_adi} - {tr_para(float(tutar))} TL"
            bot_mesaj = f"✅ Tahsilat işlemi başlatıldı."
            import asyncio
            asyncio.create_task(
                asyncio.to_thread(session_kaydet_bg, session_id, user_mesaj, bot_mesaj)
            )
        except Exception as e:
            logger.warning(f"Tahsilat session kaydı yapılamadı: {e}")

    try:
        from app.parasut.client import async_tahsilat_ekle, async_cari_id_bul, async_cari_detay_getir
        from datetime import datetime

        if not onaylandi:
            try:
                cari_id = await async_cari_id_bul(cari_adi)
                if cari_id:
                    detay = await async_cari_detay_getir(cari_id)
                    guncel_bakiye = float(detay.get("bakiye", 0))
                    girilen_tutar = float(tutar)
                    if guncel_bakiye > 0 and girilen_tutar > guncel_bakiye:
                        return JSONResponse({
                            "basarili": False, "fazla_tutar": True,
                            "guncel_bakiye": guncel_bakiye, "girilen_tutar": girilen_tutar,
                            "cevap": (f"Fazla tutar uyarısı!\n\n{cari_adi}\n"
                                      f"Güncel bakiye: {tr_para(guncel_bakiye, 2)} TL\n"
                                      f"Girilen tutar: {tr_para(girilen_tutar, 2)} TL\n\n"
                                      f"Girilen tutar bakiyeden {tr_para(girilen_tutar - guncel_bakiye, 2)} TL fazla. "
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

        sonuc = await async_tahsilat_ekle(cari_adi, float(tutar), aciklama or f"{cari_adi} tahsilat", tarih_str)

        if sonuc.get("basarili"):
            aktif_user = mevcut_kullanici(request)
            if aktif_user:
                background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                          "TAHSILAT", f"cari={cari_adi} | {tutar} TL",
                                          1, aktif_user.get("profaix_user_id"))
            db = SessionLocal()
            try:
                _islem_log(db, "TAHSILAT_EKLENDI", cari_adi=cari_adi, tutar=int(float(tutar)),
                           aciklama=f"Tarih: {tarih or 'Bugün'} | {aciklama or ''}")
            finally:
                db.close()

            # Kısmi başarı uyarısı
            uyari = ""
            if sonuc.get("basarisiz_fatura", 0) > 0:
                uyari = f"\n⚠️ {sonuc['basarisiz_fatura']} faturaya tahsilat eklenemedi, kontrol edin."
            if sonuc.get("kalan_odenmedi", 0) > 0:
                uyari += f"\n⚠️ {tr_para(sonuc['kalan_odenmedi'], 2)} TL ödenemedi."

            return JSONResponse({
                "basarili": True,
                "cevap": (f"✅ Tahsilat kaydedildi!\n\n🏢 Cari: {cari_adi}\n"
                          f"💰 Tutar: {tr_para(float(tutar), 2)} TL\n"
                          f"📅 Tarih: {datetime.strptime(tarih_str, '%Y-%m-%d').strftime('%d.%m.%Y') if tarih_str else 'Bugün'}\n"
                          f"Paraşüt'e kaydedildi.{uyari}")
            })
        else:
            return JSONResponse({"basarili": False,
                                 "cevap": f"❌ Tahsilat eklenemedi: {sonuc.get('hata', 'Bilinmeyen hata')}"})
    except Exception as e:
        logger.error(f"Tahsilat endpoint hatası: {e}")
        return JSONResponse({"basarili": False, "cevap": f"❌ Hata: {str(e)}"})


# ── YENİ CARİ ────────────────────────────────────────────────────────

@app.post("/api/cari-olustur")
async def cari_olustur_endpoint(request: Request, background_tasks: BackgroundTasks):
    import asyncio
    data = await request.json()
    firma_adi = data.get("firma_adi", "").strip()
    email = data.get("email", "").strip() or None
    telefon = data.get("telefon", "").strip() or None
    vergi_no = data.get("vergi_no", "").strip() or None
    vergi_dairesi = data.get("vergi_dairesi", "").strip() or None
    adres = data.get("adres", "").strip() or None
    session_id = data.get("session_id")

    if not firma_adi:
        return JSONResponse({"basarili": False, "cevap": "❌ Firma adı gerekli."})

    # Session'a kaydet
    if session_id:
        try:
            from app.chatbox.llm_handler import session_kaydet_bg
            user_mesaj = f"🏢 Yeni Cari: {firma_adi}"
            bot_mesaj = f"✅ Yeni cari oluşturma işlemi başlatıldı."
            asyncio.create_task(
                asyncio.to_thread(session_kaydet_bg, session_id, user_mesaj, bot_mesaj)
            )
        except Exception as e:
            logger.warning(f"Cari oluşturma session kaydı yapılamadı: {e}")

    try:
        from app.parasut.client import cari_olustur
        sonuc = await asyncio.to_thread(cari_olustur, firma_adi, email, telefon, vergi_no, vergi_dairesi, adres)
        if sonuc.get("basarili"):
            aktif_user = mevcut_kullanici(request)
            if aktif_user:
                background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                          "CARI", f"firma={firma_adi}", 1, aktif_user.get("profaix_user_id"))
            db = SessionLocal()
            try:
                _islem_log(db, "CARI_OLUSTURULDU", cari_adi=firma_adi,
                           aciklama=f"Vergi No: {vergi_no or '-'} | Tel: {telefon or '-'}")
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
        return JSONResponse({"basarili": False, "cevap": f"❌ Hata: {str(e)}"})


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
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                  "SORGU", "bekleyen_irsaliyeler", 1, aktif_user.get("profaix_user_id"))
    db = SessionLocal()
    try:
        from app.parasut.client import async_tum_bekleyen_irsaliyeleri_getir, async_cari_listesi_getir
        from app.core.database import Waybill, TicketStatus
        from datetime import date

        # Tarih filtresi — default bu ayın başından bugüne
        bugun = date.today()
        baslangic = request.query_params.get("baslangic", bugun.replace(day=1).strftime("%Y-%m-%d"))
        bitis = request.query_params.get("bitis", bugun.strftime("%Y-%m-%d"))
        sayfa = int(request.query_params.get("sayfa", 1))
        sayfa_basi = min(int(request.query_params.get("sayfa_basi", 25)), 25)

        tum_waybiller = db.query(Waybill).all()
        waybill_map = {str(w.parasut_irsaliye_id).strip(): w for w in tum_waybiller if w.parasut_irsaliye_id}
        tamamlanan_ids = {str(w.parasut_irsaliye_id).strip() for w in tum_waybiller
                          if w.status == TicketStatus.TAMAMLANDI and w.parasut_irsaliye_id}

        cariler, api_sonuc = await _json_gather(
            async_cari_listesi_getir(),
            async_tum_bekleyen_irsaliyeleri_getir(baslangic, bitis, sayfa, sayfa_basi)
        )
        cari_id_to_ad = {c["id"]: c["ad"] for c in cariler}
        gruplu = api_sonuc.get("irsaliyeler", {})
        sonraki_sayfa_var = api_sonuc.get("sonraki_sayfa_var", False)

        sonuc = []
        for contact_id, irsaliyeler in gruplu.items():
            cari_adi = cari_id_to_ad.get(contact_id, irsaliyeler[0].get("cari_adi", "-") if irsaliyeler else "-")
            for irs in irsaliyeler:
                if str(irs["id"]).strip() in tamamlanan_ids:
                    continue
                waybill = waybill_map.get(str(irs["id"]).strip())
                kalemler = irs.get("kalemler", [])
                toplam_kg = irs.get("toplam_kg", 0)
                if waybill and getattr(waybill, 'malzemeler_json', None):
                    try:
                        m = _json.loads(waybill.malzemeler_json)
                        if m:
                            kalemler = [{"aciklama": ad, "miktar_kg": kg} for ad, kg in m.items() if kg > 0]
                            toplam_kg = sum(k["miktar_kg"] for k in kalemler)
                    except Exception:
                        pass
                elif waybill and waybill.ticket and getattr(waybill.ticket, 'malzemeler', None):
                    try:
                        m = _json.loads(waybill.ticket.malzemeler)
                        if m:
                            kalemler = [{"aciklama": ad, "miktar_kg": kg} for ad, kg in m.items() if kg > 0]
                            toplam_kg = sum(k["miktar_kg"] for k in kalemler)
                    except Exception:
                        pass
                sonuc.append({"parasut_irsaliye_id": irs["id"], "cari_adi": cari_adi,
                              "tarih": irs.get("tarih", "-"), "toplam_kg": toplam_kg,
                              "kalemler": kalemler, "aciklama": irs.get("aciklama", "-")})

        return JSONResponse({
            "irsaliyeler": sonuc,
            "toplam": len(sonuc),
            "sayfa": sayfa,
            "baslangic": baslangic,
            "bitis": bitis,
            "sonraki_sayfa_var": sonraki_sayfa_var,
        })
    except Exception as e:
        logger.error(f"Bekleyen irsaliye hatası: {e}")
        return JSONResponse({"irsaliyeler": [], "hata": str(e)})
    finally:
        db.close()


async def _json_gather(*coros):
    """Birden fazla coroutine'i paralel çalıştır."""
    import asyncio
    return await asyncio.gather(*coros)


@app.get("/api/gunluk-ozet")
async def gunluk_ozet(request: Request, background_tasks: BackgroundTasks):
    aktif_user = mevcut_kullanici(request)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                  "SORGU", "gunluk_ozet", 1, aktif_user.get("profaix_user_id"))
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus
        from datetime import date
        bugun = date.today().strftime("%Y-%m-%d")
        bugun_fisler = db.query(WeighTicket).filter(WeighTicket.created_at >= bugun).all()
        toplam_kg = sum(t.agirlik_kg or 0 for t in bugun_fisler)
        tamamlanan = sum(1 for t in bugun_fisler if t.status == TicketStatus.TAMAMLANDI)
        bekleyen = sum(1 for t in bugun_fisler if t.status not in [
            TicketStatus.TAMAMLANDI, TicketStatus.IPTAL, TicketStatus.HATA])
        return JSONResponse({"tarih": date.today().strftime("%d.%m.%Y"), "fis_sayisi": len(bugun_fisler),
                             "toplam_kg": toplam_kg, "toplam_ton": round(toplam_kg / 1000, 2),
                             "tamamlanan": tamamlanan, "bekleyen": bekleyen})
    except Exception as e:
        return JSONResponse({"hata": str(e)})
    finally:
        db.close()


@app.get("/api/son-fisler")
async def son_fisler(request: Request, background_tasks: BackgroundTasks):
    aktif_user = mevcut_kullanici(request)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                  "SORGU", "son_fisler", 1, aktif_user.get("profaix_user_id"))
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket
        fisler = db.query(WeighTicket).order_by(WeighTicket.created_at.desc()).limit(10).all()
        return JSONResponse({"fisler": [
            {"id": t.id, "plaka": t.plaka or "-", "firma": t.firma or "-",
             "agirlik_kg": t.agirlik_kg or 0,
             "tarih": t.fis_tarihi.strftime("%d.%m.%Y") if t.fis_tarihi else "-",
             "status": t.status.value if t.status else "-",
             "created_at": t.created_at.strftime("%d.%m.%Y %H:%M") if t.created_at else "-"}
            for t in fisler
        ]})
    except Exception as e:
        return JSONResponse({"fisler": [], "hata": str(e)})
    finally:
        db.close()


@app.get("/api/fatura-detay/{fatura_id}")
async def fatura_detay_endpoint(fatura_id: str, request: Request, background_tasks: BackgroundTasks):
    aktif_user = mevcut_kullanici(request)
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                  "SORGU", f"fatura_detay | id={fatura_id}", 1,
                                  aktif_user.get("profaix_user_id"))
    try:
        from app.parasut.client import async_fatura_detay_getir
        return JSONResponse(await async_fatura_detay_getir(fatura_id))
    except Exception as e:
        return JSONResponse({"hata": str(e)})


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
    if aktif_user:
        background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                  "SORGU", f"fatura_listesi | cari={cari_adi}", 1,
                                  aktif_user.get("profaix_user_id"))
    try:
        from app.parasut.client import async_cari_id_bul, async_cari_faturalari_getir
        from urllib.parse import unquote
        cari_adi = unquote(cari_adi)
        baslangic = request.query_params.get("baslangic")
        bitis = request.query_params.get("bitis")
        sayfa = int(request.query_params.get("sayfa", 1))
        limit = min(int(request.query_params.get("limit", 25)), 25)
        durum = request.query_params.get("durum")

        cari_id = await async_cari_id_bul(cari_adi)
        if not cari_id:
            return JSONResponse({"faturalar": [], "hata": "Cari bulunamadı"})

        sonuc = await async_cari_faturalari_getir(cari_id, limit, sayfa, baslangic, bitis, durum)
        return JSONResponse(sonuc)
    except Exception as e:
        return JSONResponse({"faturalar": [], "hata": str(e)})


# ── TOPLU FATURA KES ─────────────────────────────────────────────────

@app.post("/api/toplu-fatura-kes")
async def toplu_fatura_kes_endpoint(request: Request, background_tasks: BackgroundTasks):
    import asyncio
    data = await request.json()
    cari_adi = data.get("cari_adi")
    irsaliyeler_data = data.get("irsaliyeler", [])

    if not cari_adi or not irsaliyeler_data:
        return JSONResponse({"cevap": "❌ Eksik bilgi.", "basarili": False})

    db = SessionLocal()
    try:
        from app.parasut.client import async_cari_id_bul, async_parasut_post, async_parasut_delete, \
            async_cari_cache_yenile
        from app.core.config import PARASUT_COMPANY_ID, PARASUT_HURDA_PRODUCT_ID
        from app.core.database import Waybill, WeighTicket, TicketStatus
        from datetime import datetime

        contact_id = await async_cari_id_bul(cari_adi)
        if not contact_id:
            return JSONResponse({"cevap": f"❌ '{cari_adi}' carisi bulunamadı.", "basarili": False})

        bugun = datetime.now().strftime("%Y-%m-%d")
        detaylar = []
        shipment_docs = []
        toplam_tutar = 0
        irsaliye_ids = []

        for irs in irsaliyeler_data:
            pid = irs.get("parasut_irsaliye_id")
            malzemeler = irs.get("malzemeler", {})
            toplanan_fiyatlar = irs.get("toplanan_fiyatlar", {})
            agirlik_kg = irs.get("agirlik_kg", 0)
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
                                     "relationships": {"product": {
                                         "data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}}})
            else:
                birim_fiyat = float(list(toplanan_fiyatlar.values())[0]) if toplanan_fiyatlar else 0
                kg = float(agirlik_kg) if agirlik_kg else 0
                toplam_tutar += kg * birim_fiyat
                detaylar.append({"type": "sales_invoice_details",
                                 "attributes": {"quantity": kg, "unit_price": f"{float(birim_fiyat):.4f}",
                                                "vat_rate": 0, "description": f"Hurda — {tr_kg(kg)} kg"},
                                 "relationships": {
                                     "product": {"data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}}})

        if not detaylar:
            return JSONResponse({"cevap": "❌ Fatura kalemi oluşturulamadı.", "basarili": False})

        istek = {
            "data": {
                "type": "sales_invoices",
                "attributes": {"item_type": "invoice",
                               "description": f"Toplu Hurda Alım — {len(irsaliye_ids)} irsaliye",
                               "issue_date": bugun, "due_date": bugun, "currency": "TRL"},
                "relationships": {
                    "contact": {"data": {"type": "contacts", "id": str(contact_id)}},
                    "details": {"data": detaylar},
                    "shipment_documents": {"data": shipment_docs}
                }
            }
        }

        try:
            sonuc = await async_parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek)
        except Exception as e:
            logger.warning(f"İrsaliye bağlantılı toplu fatura başarısız: {e}")
            istek["data"]["relationships"].pop("shipment_documents", None)
            sonuc = await async_parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek)

        fatura_id = sonuc["data"]["id"]
        logger.info(f"✅ Toplu fatura kesildi: {fatura_id} ← {len(irsaliye_ids)} irsaliye")

        aktif_user = mevcut_kullanici(request)
        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                      "TOPLU_FATURA", f"cari={cari_adi} | {int(toplam_tutar)} TL",
                                      1, aktif_user.get("profaix_user_id"))
        _islem_log(db, "TOPLU_FATURA_KESILDI", cari_adi=cari_adi, tutar=int(toplam_tutar),
                   fatura_id=fatura_id, aciklama=f"{len(irsaliye_ids)} irsaliye birleştirildi")

        for pid in irsaliye_ids:
            try:
                waybill = db.query(Waybill).filter(Waybill.parasut_irsaliye_id == pid).first()
                if waybill:
                    waybill.status = TicketStatus.TAMAMLANDI
                    ticket = db.query(WeighTicket).filter(WeighTicket.id == waybill.ticket_id).first()
                    if ticket:
                        ticket.status = TicketStatus.TAMAMLANDI
                    db.commit()
            except Exception as e:
                logger.error(f"DB güncelleme hatası ({pid}): {e}")
            try:
                await async_parasut_delete(f"/{PARASUT_COMPANY_ID}/shipment_documents/{pid}")
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
        return JSONResponse({"cevap": f"❌ Toplu fatura kesilemedi: {str(e)}", "basarili": False})
    finally:
        db.close()


# ── FATURA KES ────────────────────────────────────────────────────────

@app.post("/api/fatura-kes")
async def fatura_kes_endpoint(request: Request, background_tasks: BackgroundTasks):
    import asyncio
    data = await request.json()
    parasut_irsaliye_id = data.get("parasut_irsaliye_id")
    cari_adi = data.get("cari_adi")
    agirlik_kg = data.get("agirlik_kg", 0)
    malzemeler = data.get("malzemeler", {})
    toplanan_fiyatlar = data.get("toplanan_fiyatlar", {})

    if not parasut_irsaliye_id or not cari_adi or not toplanan_fiyatlar:
        return JSONResponse({"cevap": "❌ Eksik bilgi: irsaliye ID, cari adı ve fiyat gerekli."})

    db = SessionLocal()
    try:
        from app.parasut.client import async_cari_id_bul, async_parasut_post, async_parasut_delete, \
            async_cari_cache_yenile
        from app.core.config import PARASUT_COMPANY_ID, PARASUT_HURDA_PRODUCT_ID
        from app.core.database import Waybill, WeighTicket, TicketStatus
        from datetime import datetime

        contact_id = await async_cari_id_bul(cari_adi)
        if not contact_id:
            return JSONResponse({"cevap": f"❌ '{cari_adi}' carisi bulunamadı."})

        bugun = datetime.now().strftime("%Y-%m-%d")

        if malzemeler and len(toplanan_fiyatlar) > 1:
            detaylar = []
            toplam_tutar = 0
            for malzeme_adi, fiyat in toplanan_fiyatlar.items():
                kg = malzemeler.get(malzeme_adi, 0)
                if kg <= 0:
                    continue
                toplam_tutar += kg * fiyat
                detaylar.append({"type": "sales_invoice_details",
                                 "attributes": {"quantity": kg, "unit_price": f"{float(fiyat):.4f}",
                                                "vat_rate": 0, "description": f"{malzeme_adi} — {kg:,} kg"},
                                 "relationships": {
                                     "product": {"data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}}})
        else:
            birim_fiyat = float(list(toplanan_fiyatlar.values())[0])
            kg = float(agirlik_kg) if agirlik_kg else float(sum(malzemeler.values())) if malzemeler else 0
            toplam_tutar = kg * birim_fiyat
            detaylar = [{"type": "sales_invoice_details",
                         "attributes": {"quantity": kg, "unit_price": f"{float(birim_fiyat):.4f}",
                                        "vat_rate": 0, "description": f"Hurda — {tr_kg(kg)} kg"},
                         "relationships": {
                             "product": {"data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}}}]

        istek = {
            "data": {
                "type": "sales_invoices",
                "attributes": {"item_type": "invoice", "description": f"Hurda Alım — İrsaliye {parasut_irsaliye_id}",
                               "issue_date": bugun, "due_date": bugun, "currency": "TRL"},
                "relationships": {
                    "contact": {"data": {"type": "contacts", "id": str(contact_id)}},
                    "details": {"data": detaylar},
                    "shipment_document": {"data": {"type": "shipment_documents", "id": str(parasut_irsaliye_id)}}
                }
            }
        }

        try:
            sonuc = await async_parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek)
        except Exception as e:
            logger.warning(f"İrsaliye bağlantılı fatura başarısız: {e}")
            istek["data"]["relationships"].pop("shipment_document", None)
            sonuc = await async_parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek)

        fatura_id = sonuc["data"]["id"]
        logger.info(f"✅ Fatura kesildi: {fatura_id} ← irsaliye {parasut_irsaliye_id}")

        aktif_user = mevcut_kullanici(request)
        if aktif_user:
            background_tasks.add_task(_usage_kaydet, aktif_user["id"], aktif_user["username"],
                                      "FATURA", f"cari={cari_adi} | {int(toplam_tutar)} TL",
                                      1, aktif_user.get("profaix_user_id"))
        _islem_log(db, "FATURA_KESILDI", cari_adi=cari_adi, tutar=int(toplam_tutar),
                   fatura_id=fatura_id, irsaliye_id=str(parasut_irsaliye_id))

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

        try:
            await async_parasut_delete(f"/{PARASUT_COMPANY_ID}/shipment_documents/{parasut_irsaliye_id}")
        except Exception as e:
            logger.warning(f"İrsaliye silinemedi: {e}")

        ozet = f"✅ Fatura kesildi!\n\n🏢 {cari_adi}\n"
        for malzeme_adi_ozet, fiyat_ozet in toplanan_fiyatlar.items():
            kg_val = malzemeler.get(malzeme_adi_ozet, agirlik_kg) if malzemeler else agirlik_kg
            ozet += f"  • {malzeme_adi_ozet if malzemeler and len(toplanan_fiyatlar) > 1 else 'Hurda'}: {tr_kg(kg_val)} kg × {tr_para(fiyat_ozet, 4)} TL/kg = {tr_para(kg_val * fiyat_ozet, 2)} TL\n"
        ozet += f"\n💵 Toplam: {int(toplam_tutar):,} TL\n🧾 Fatura No: {fatura_id}"

        try:
            await async_cari_cache_yenile()
        except Exception:
            pass

        return JSONResponse({"cevap": ozet, "basarili": True, "fatura_no": fatura_id})

    except Exception as e:
        logger.error(f"Fatura kes hatası: {e}")
        _islem_log(db, "FATURA_HATA", cari_adi=cari_adi, irsaliye_id=str(parasut_irsaliye_id), aciklama=str(e))
        return JSONResponse({"cevap": f"❌ Fatura kesilemedi: {str(e)}", "basarili": False})
    finally:
        db.close()


# ── KULLANIM ÖZETİ ────────────────────────────────────────────────────

@app.get("/api/kullanim-ozeti/{user_id}")
def kullanim_ozeti_endpoint(user_id: str):
    from app.core.database import SessionLocal, UsageLog
    from sqlalchemy import func as sqlfunc, text, or_
    from datetime import date, timedelta
    db = SessionLocal()
    try:
        bugun = date.today()
        bu_ay_baslangic = bugun.replace(day=1).strftime("%Y-%m-%d")
        gecen_ay_son = bugun.replace(day=1)
        gecen_ay_baslangic = (gecen_ay_son - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d")
        filtre = or_(UsageLog.profaix_user_id == user_id, UsageLog.username == user_id)
        toplam = db.query(UsageLog).filter(filtre, UsageLog.ucretli == 1).count()
        dagilim = db.query(UsageLog.islem_turu, sqlfunc.count(UsageLog.id).label("adet")).filter(
            filtre, UsageLog.ucretli == 1).group_by(UsageLog.islem_turu).all()
        bu_ay = db.query(UsageLog).filter(filtre, UsageLog.ucretli == 1, UsageLog.created_at >= bu_ay_baslangic).count()
        gecen_ay = db.query(UsageLog).filter(filtre, UsageLog.ucretli == 1,
                                             UsageLog.created_at >= gecen_ay_baslangic,
                                             UsageLog.created_at < bu_ay_baslangic).count()
        gunluk = db.execute(text("""
            SELECT DATE(created_at) as gun, COUNT(*) as adet FROM usage_logs
            WHERE (profaix_user_id = :uid OR username = :uid) AND ucretli = 1
              AND created_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(created_at) ORDER BY gun DESC
        """), {"uid": user_id}).fetchall()
        ilk_kayit = db.query(UsageLog).filter(filtre).first()
        gosterilen_isim = ilk_kayit.username if ilk_kayit else user_id
        return JSONResponse({"user_id": user_id, "username": gosterilen_isim, "toplam_ucretli": toplam,
                             "bu_ay": bu_ay, "gecen_ay": gecen_ay,
                             "islem_detay": {row.islem_turu: row.adet for row in dagilim},
                             "gunluk_son30": [{"tarih": str(row.gun), "adet": row.adet} for row in gunluk]})
    except Exception as e:
        logger.error(f"Kullanım özeti hatası: {e}")
        return JSONResponse({"hata": str(e)}, status_code=500)
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
        return JSONResponse({"hata": str(e)})


@app.get("/admin/islem-loglari")
def islem_loglari(request: Request, limit: int = 50, islem_tipi: str = None):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    db = SessionLocal()
    try:
        from app.core.database import IslemLog
        from sqlalchemy import desc
        q = db.query(IslemLog).order_by(desc(IslemLog.created_at))
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
        return JSONResponse({"hata": str(e)})
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
        return JSONResponse({"hata": str(e)})
    finally:
        db.close()


@app.get("/api/session/{session_id}")
async def get_session(session_id: str, request: Request):
    """Belirtilen session_id'nin geçmişini getirir (kullanıcıya özel)"""
    aktif_user = mevcut_kullanici(request)
    if not aktif_user:
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)

    db = SessionLocal()
    try:
        from app.core.database import ChatSession
        # Sadece giriş yapmış kullanıcıya ait session'ları getir
        session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id,
            ChatSession.session_id.like(f"web_%")  # Sadece web session'ları
        ).first()

        if not session or not session.tam_gecmis:
            return JSONResponse({"gecmis": []})

        try:
            gecmis = _json.loads(session.tam_gecmis)
            return JSONResponse({"gecmis": gecmis})
        except Exception:
            return JSONResponse({"gecmis": []})
    except Exception as e:
        return JSONResponse({"hata": str(e)})
    finally:
        db.close()


@app.get("/admin/accounts-debug")
async def accounts_debug(request: Request):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    try:
        from app.parasut.client import parasut_get
        from app.core.config import PARASUT_COMPANY_ID
        sonuc = parasut_get(f"/{PARASUT_COMPANY_ID}/accounts")
        hesaplar = [{"id": h["id"], "ad": h.get("attributes", {}).get("name"),
                     "tip": h.get("attributes", {}).get("account_type"),
                     "para_birimi": h.get("attributes", {}).get("currency"),
                     "bakiye": h.get("attributes", {}).get("balance")}
                    for h in sonuc.get("data", [])]
        return JSONResponse({"hesaplar": hesaplar})
    except Exception as e:
        return JSONResponse({"hata": str(e)})


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