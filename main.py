# main.py — Profaix FastAPI Uygulaması

import os
import tempfile
import json as _json
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from app.core.config import validate_config, ENV
from app.core.database import init_db, SessionLocal, engine
from app.core.logger import logger
from app.chatbox.webhook import router as webhook_router

# HTML şablonunu dosyadan oku
_HTML_PATH = Path(__file__).parent / "chat.html"
_HTML_CACHE: str | None = None


def _chat_html() -> str:
    global _HTML_CACHE
    if _HTML_CACHE is None:
        _HTML_CACHE = _HTML_PATH.read_text(encoding="utf-8")
    return _HTML_CACHE


async def _keep_alive():
    """Her 10 dakikada bir kendi health endpoint'ine ping at — Render uyanık kalsın."""
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
    # DB ve migration'ı arka planda yap — port bind'ı engellemesin
    asyncio.create_task(_startup_background())
    logger.info(f"✅ Profaix hazır — Ortam: {ENV}")
    yield
    logger.info("Profaix kapanıyor...")


async def _startup_background():
    """DB init ve keep-alive'ı arka planda çalıştır."""
    import asyncio
    try:
        init_db()
    except Exception as e:
        logger.warning(f"⚠️ DB başlatma gecikmeli: {e}")
    try:
        await startup_recovery()
    except Exception as e:
        logger.warning(f"⚠️ Startup recovery atlandı: {e}")
    # Keep-alive task başlat
    asyncio.create_task(_keep_alive())


app = FastAPI(title="Profaix", description="Muhasebe Otomasyon Ajanı", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(webhook_router)


def _islem_log(db, islem_tipi: str, cari_adi: str = None, tutar: int = None,
               fatura_id: str = None, irsaliye_id: str = None, plaka: str = None,
               aciklama: str = None, session_id: str = None):
    """İşlem logunu DB'ye yazar. Hata olursa sessizce geçer."""
    try:
        from app.core.database import IslemLog
        log = IslemLog(
            islem_tipi  = islem_tipi,
            cari_adi    = cari_adi,
            tutar       = int(tutar) if tutar else None,
            fatura_id   = fatura_id,
            irsaliye_id = irsaliye_id,
            plaka       = plaka,
            aciklama    = aciklama,
            session_id  = session_id,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.warning(f"İşlem log yazılamadı: {e}")


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
async def chat_sayfasi():
    return HTMLResponse(content=_chat_html())


# ── CHAT (Claude — sadece bilgi sorguları) ────────────────────────────
@app.post("/api/chat")
async def chat_api(request: Request):
    data = await request.json()
    mesaj = data.get("mesaj", "").strip()
    session_id = data.get("session_id", "web_user")
    if not mesaj:
        return JSONResponse({"cevap": "Mesaj boş olamaz."})
    db = SessionLocal()
    try:
        from app.chatbox.llm_handler import soru_cevapla
        cevap = soru_cevapla(mesaj, db, session_id)
        return JSONResponse({"cevap": cevap})
    except Exception as e:
        logger.error(f"Chat API hatası: {e}")
        return JSONResponse({"cevap": "❌ Bir hata oluştu, lütfen tekrar deneyin."})
    finally:
        db.close()


# ── FİŞ YÜKLE ────────────────────────────────────────────────────────
@app.post("/api/upload")
async def upload_fis(file: UploadFile = File(...), session_id: str = "web_user"):
    db = SessionLocal()
    try:
        uzanti = os.path.splitext(file.filename)[1] if file.filename else ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=uzanti) as tmp:
            icerik = await file.read()
            tmp.write(icerik)
            tmp_path = tmp.name

        logger.info(f"📸 Fotoğraf alındı: {tmp_path} ({len(icerik)} bytes)")

        from app.ocr.reader import fis_oku, fis_db_kaydet
        fis = fis_oku(tmp_path)

        if fis.hata:
            _islem_log(db, "OCR_HATA", plaka=None, aciklama=fis.hata, session_id=session_id)
            return JSONResponse({"cevap": f"❌ Fiş okunamadı: {fis.hata}\n\nFotoğrafı daha net çekip tekrar deneyin."})

        ticket = fis_db_kaydet(fis, tmp_path, db)
        _islem_log(db, "FIS_YUKLENDI", plaka=fis.plaka,
                   aciklama=f"Fiş No: {fis.fis_no} | {fis.net_agirlik_kg} kg | OCR firma: {fis.firma}",
                   session_id=session_id)

        from app.ocr.plaka_kontrol import plaka_kontrol_et, PlakaSonuc
        plaka_sonuc = plaka_kontrol_et(fis.plaka, ticket.id, db, fis_no=fis.fis_no)

        if plaka_sonuc == PlakaSonuc.DUPLICATE:
            return JSONResponse({
                "cevap": (
                    f"⚠️ Bu fiş daha önce sisteme girilmiş!\n\n"
                    f"📋 Fiş No: {fis.fis_no}\n"
                    f"🚗 Plaka: {fis.plaka}\n\n"
                    f"Mükerrer kayıt tespit edildi, işlem yapılmadı."
                )
            })

        yeni_plaka_uyari = "\n🆕 Bu plaka sisteme ilk kez eklendi." if plaka_sonuc == PlakaSonuc.YENI else ""

        malzemeler = fis.malzemeler or {}
        malzeme_ozet = ""
        if malzemeler:
            malzeme_ozet = "\n\n📦 Malzeme Grupları:\n"
            for isim, kg in malzemeler.items():
                malzeme_ozet += f"  • {isim}: {kg:,} kg ({kg/1000:.2f} ton)\n"

        cevap = (
            f"✅ Fiş okundu!\n\n"
            f"🚗 Plaka: {fis.plaka}\n"
            f"📋 Fiş No: {fis.fis_no or '-'}\n"
            f"⚖️ Net Ağırlık: {fis.net_agirlik_kg:,} kg ({fis.net_agirlik_kg/1000:.2f} ton)\n"
            f"🔥 Fire: {fis.fire_kg or 0:,} kg\n"
            f"📅 Fiş Tarihi: {fis.tarih or '-'}\n"
            f"🏭 OCR Firma: {fis.firma or '-'}"
            f"{yeni_plaka_uyari}"
            f"{malzeme_ozet}\n"
            f"🏭 Bu fiş hangi fabrikaya ait?"
        )

        from app.parasut.client import cari_listesi_getir
        cariler = cari_listesi_getir()
        firma_listesi = [c["ad"] for c in cariler]

        return JSONResponse({
            "cevap": cevap,
            "bekliyor": "firma_sec",
            "ticket_id": ticket.id,
            "malzemeler": malzemeler,
            "firma_listesi": firma_listesi,
        })

    except Exception as e:
        logger.error(f"Upload hatası: {e}")
        return JSONResponse({"cevap": f"❌ Hata oluştu: {str(e)}"})
    finally:
        db.close()


# ── FİRMA SEÇ → İRSALİYE OLUŞTUR ────────────────────────────────────
@app.post("/api/firma-sec")
async def firma_sec(request: Request):
    data = await request.json()
    ticket_id    = data.get("ticket_id")
    secilen_firma = data.get("firma")
    malzemeler   = data.get("malzemeler", {})

    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, Waybill
        ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()
        if not ticket:
            return JSONResponse({"cevap": "❌ Fiş bulunamadı."})

        ticket.firma = secilen_firma
        db.commit()

        from app.parasut.irsaliye import irsaliye_olustur
        sonuc = irsaliye_olustur(ticket_id, beklet=True)

        if not sonuc.get("basarili"):
            _islem_log(db, "IRSALIYE_HATA", cari_adi=secilen_firma,
                      aciklama="İrsaliye oluşturulamadı")
            return JSONResponse({"cevap": "❌ İrsaliye oluşturulamadı, tekrar deneyin."})

        waybill_id = sonuc["waybill_id"]
        parasut_id = sonuc.get("parasut_id", "-")
        _islem_log(db, "IRSALIYE_OLUSTURULDU", cari_adi=secilen_firma,
                  irsaliye_id=str(parasut_id), plaka=ticket.plaka,
                  aciklama=f"{ticket.agirlik_kg} kg")

        waybill_obj = db.query(Waybill).filter(Waybill.id == waybill_id).first()
        if waybill_obj:
            if malzemeler:
                waybill_obj.malzemeler_json = _json.dumps(malzemeler, ensure_ascii=False)
            elif ticket.malzemeler:
                # Frontend malzeme göndermemişse ticket'taki malzemeleri kullan
                waybill_obj.malzemeler_json = ticket.malzemeler
            db.commit()
            logger.info(f"Waybill malzemeler_json kaydedildi: {waybill_obj.malzemeler_json}")

        malzeme_ozet = ""
        if malzemeler:
            malzeme_ozet = "\n\n📦 Malzemeler:\n"
            for isim, kg in malzemeler.items():
                malzeme_ozet += f"  • {isim}: {kg:,} kg\n"

        return JSONResponse({
            "cevap": (
                f"✅ {secilen_firma} seçildi, irsaliye Paraşüt'te oluşturuldu!\n"
                f"🆔 Paraşüt ID: {parasut_id}"
                f"{malzeme_ozet}\n"
                f"İrsaliye sisteme kaydedildi. Fatura kesmek için "
                f"'bekleyen irsaliyeler' yazın veya butona tıklayın."
            ),
            "bekliyor": "bitti",
        })
    except Exception as e:
        logger.error(f"Firma seç hatası: {e}")
        return JSONResponse({"cevap": f"❌ Hata oluştu: {str(e)}"})
    finally:
        db.close()


# ── CARİLER + BAKİYELER (Python, Paraşüt'ten direkt) ─────────────────
@app.get("/api/cariler")
async def cariler_getir(refresh: bool = False):
    """Tüm carileri ve güncel bakiyelerini Paraşüt'ten döndürür."""
    try:
        from app.parasut.client import cari_listesi_getir, cari_cache_yenile
        cariler = cari_cache_yenile() if refresh else cari_listesi_getir()
        sonuc = [
            {"id": c["id"], "ad": c["ad"], "bakiye": c.get("bakiye", 0)}
            for c in cariler
        ]
        sonuc.sort(key=lambda x: x["bakiye"], reverse=True)
        return JSONResponse({"cariler": sonuc, "toplam": len(sonuc)})
    except Exception as e:
        logger.error(f"Cariler hatası: {e}")
        return JSONResponse({"cariler": [], "hata": str(e)})


# ── HESAP ÖZETİ (Python, Paraşüt'ten direkt) ─────────────────────────
@app.get("/api/hesap-ozeti")
async def hesap_ozeti():
    """Genel hesap özetini Paraşüt'ten döndürür."""
    try:
        from app.parasut.client import hesap_ozeti_getir
        ozet = hesap_ozeti_getir()
        return JSONResponse(ozet)
    except Exception as e:
        logger.error(f"Hesap özeti hatası: {e}")
        return JSONResponse({"hata": str(e)})


# ── TAHSİLAT (Python, Claude yok) ────────────────────────────────────
@app.post("/api/tahsilat")
async def tahsilat_ekle_endpoint(request: Request):
    """
    Belirli bir cariye tahsilat kaydı ekler.
    Paraşüt'teki açık faturaya ödeme bağlar.
    Claude devreye girmez.
    """
    data = await request.json()
    cari_adi = data.get("cari_adi", "").strip()
    tutar    = data.get("tutar", 0)
    aciklama = data.get("aciklama", "")
    tarih    = data.get("tarih", "")

    if not cari_adi or not tutar:
        return JSONResponse({"basarili": False, "cevap": "❌ Cari adı ve tutar gerekli."})

    try:
        from app.parasut.client import tahsilat_ekle
        from datetime import datetime

        # Tarih formatla
        tarih_str = None
        if tarih:
            try:
                tarih_str = datetime.strptime(tarih, "%d.%m.%Y").strftime("%Y-%m-%d")
            except Exception:
                tarih_str = None

        sonuc = tahsilat_ekle(cari_adi, float(tutar), aciklama or f"{cari_adi} tahsilat", tarih_str)

        if sonuc.get("basarili"):
            logger.info(f"✅ Tahsilat eklendi: {cari_adi} — {tutar:,.0f} TL")
            db = SessionLocal()
            try:
                _islem_log(db, "TAHSILAT_EKLENDI", cari_adi=cari_adi,
                          tutar=int(float(tutar)),
                          aciklama=f"Tarih: {tarih or 'Bugün'} | {aciklama or ''}")
            finally:
                db.close()
            return JSONResponse({
                "basarili": True,
                "cevap": (
                    f"✅ Tahsilat kaydedildi!\n\n"
                    f"🏢 Cari: {cari_adi}\n"
                    f"💰 Tutar: {float(tutar):,.0f} TL\n"
                    f"📅 Tarih: {tarih or 'Bugün'}\n"
                    f"Paraşüt'e kaydedildi."
                )
            })
        else:
            db2 = SessionLocal()
            try:
                _islem_log(db2, "TAHSILAT_HATA", cari_adi=cari_adi,
                          tutar=int(float(tutar)),
                          aciklama=sonuc.get('hata', 'Bilinmeyen hata'))
            finally:
                db2.close()
            return JSONResponse({
                "basarili": False,
                "cevap": f"❌ Tahsilat eklenemedi: {sonuc.get('hata', 'Bilinmeyen hata')}"
            })
    except Exception as e:
        logger.error(f"Tahsilat endpoint hatası: {e}")
        return JSONResponse({"basarili": False, "cevap": f"❌ Hata: {str(e)}"})


# ── YENİ CARİ OLUŞTUR (Python, Claude yok) ───────────────────────────
@app.post("/api/cari-olustur")
async def cari_olustur_endpoint(request: Request):
    """
    Paraşüt'te yeni cari oluşturur.
    Claude devreye girmez.
    """
    data = await request.json()
    firma_adi    = data.get("firma_adi", "").strip()
    email        = data.get("email", "").strip() or None
    telefon      = data.get("telefon", "").strip() or None
    vergi_no     = data.get("vergi_no", "").strip() or None
    vergi_dairesi = data.get("vergi_dairesi", "").strip() or None
    adres        = data.get("adres", "").strip() or None

    if not firma_adi:
        return JSONResponse({"basarili": False, "cevap": "❌ Firma adı gerekli."})

    try:
        from app.parasut.client import cari_olustur
        sonuc = cari_olustur(firma_adi, email, telefon, vergi_no, vergi_dairesi, adres)

        if sonuc.get("basarili"):
            logger.info(f"✅ Yeni cari oluşturuldu: {firma_adi}")
            db = SessionLocal()
            try:
                _islem_log(db, "CARI_OLUSTURULDU", cari_adi=firma_adi,
                          aciklama=f"Vergi No: {vergi_no or '-'} | Tel: {telefon or '-'}")
            finally:
                db.close()
            return JSONResponse({
                "basarili": True,
                "cevap": (
                    f"✅ Yeni cari oluşturuldu!\n\n"
                    f"🏢 Firma: {firma_adi}\n"
                    f"📧 Email: {email or '-'}\n"
                    f"📞 Telefon: {telefon or '-'}\n"
                    f"🔢 Vergi No: {vergi_no or '-'}\n"
                    f"Paraşüt'e kaydedildi."
                ),
                "cari_id": sonuc.get("id")
            })
        else:
            return JSONResponse({
                "basarili": False,
                "cevap": f"❌ Cari oluşturulamadı: {sonuc.get('hata', 'Bilinmeyen hata')}"
            })
    except Exception as e:
        logger.error(f"Cari oluştur hatası: {e}")
        return JSONResponse({"basarili": False, "cevap": f"❌ Hata: {str(e)}"})


# ── BEKLEYENİRSALİYELER ──────────────────────────────────────────────
@app.get("/api/bekleyen-irsaliyeler")
async def bekleyen_irsaliyeler():
    """
    Tüm bekleyen irsaliyeleri TEK API isteğiyle getirir.
    Malzeme bilgisi DB'den okunur.
    """
    db = SessionLocal()
    try:
        from app.parasut.client import tum_bekleyen_irsaliyeleri_getir, cari_listesi_getir
        from app.core.database import Waybill, TicketStatus

        # DB'deki tüm waybill'leri çek
        tum_waybiller = db.query(Waybill).all()
        waybill_map = {w.parasut_irsaliye_id: w for w in tum_waybiller if w.parasut_irsaliye_id}
        tamamlanan_ids = {
            w.parasut_irsaliye_id for w in tum_waybiller
            if w.status == TicketStatus.TAMAMLANDI and w.parasut_irsaliye_id
        }
        logger.info(f"Tamamlanmış irsaliye ID'leri: {tamamlanan_ids}")

        # Cari ID → ad eşlemesi için cache kullan
        cariler = cari_listesi_getir()
        cari_id_to_ad = {c["id"]: c["ad"] for c in cariler}

        # Tek API isteğiyle tüm irsaliyeler
        gruplu = tum_bekleyen_irsaliyeleri_getir()

        sonuc = []
        for contact_id, irsaliyeler in gruplu.items():
            cari_adi = cari_id_to_ad.get(contact_id, irsaliyeler[0].get("cari_adi", "-") if irsaliyeler else "-")
            for irs in irsaliyeler:
                if str(irs["id"]) in tamamlanan_ids:
                    continue

                # Malzeme bilgisini DB'den al — hem string hem int ID dene
                waybill = waybill_map.get(str(irs["id"])) or waybill_map.get(str(irs["id"]).strip())
                logger.info(f"Waybill arama: irs_id={irs['id']} (type={type(irs['id']).__name__}) waybill_map_keys={list(waybill_map.keys())[:3]} bulundu={waybill is not None}")
                kalemler = irs.get("kalemler", [])
                toplam_kg = irs.get("toplam_kg", 0)

                if waybill and getattr(waybill, 'malzemeler_json', None):
                    try:
                        malzemeler = _json.loads(waybill.malzemeler_json)
                        if malzemeler:
                            kalemler = [
                                {"aciklama": ad, "miktar_kg": kg}
                                for ad, kg in malzemeler.items()
                                if kg > 0
                            ]
                            toplam_kg = sum(k["miktar_kg"] for k in kalemler)
                            logger.info(f"DB'den malzeme okundu: {list(malzemeler.keys())}")
                    except Exception as e:
                        logger.warning(f"malzemeler_json parse hatası: {e}")

                sonuc.append({
                    "parasut_irsaliye_id": irs["id"],
                    "cari_adi": cari_adi,
                    "tarih": irs.get("tarih", "-"),
                    "toplam_kg": toplam_kg,
                    "kalemler": kalemler,
                    "aciklama": irs.get("aciklama", "-"),
                })

        return JSONResponse({"irsaliyeler": sonuc, "toplam": len(sonuc)})
    except Exception as e:
        logger.error(f"Bekleyen irsaliye hatası: {e}")
        return JSONResponse({"irsaliyeler": [], "hata": str(e)})
    finally:
        db.close()


@app.get("/api/gunluk-ozet")
async def gunluk_ozet():
    """Bugünün özeti: fiş sayısı, toplam kg, fatura sayısı."""
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, Waybill, TicketStatus
        from datetime import date
        bugun = date.today().strftime("%Y-%m-%d")
        bugun_fisler = db.query(WeighTicket).filter(
            WeighTicket.created_at >= bugun
        ).all()
        toplam_kg = sum(t.agirlik_kg or 0 for t in bugun_fisler)
        tamamlanan = sum(1 for t in bugun_fisler if t.status == TicketStatus.TAMAMLANDI)
        bekleyen = sum(1 for t in bugun_fisler if t.status not in [
            TicketStatus.TAMAMLANDI, TicketStatus.IPTAL, TicketStatus.HATA
        ])
        return JSONResponse({
            "tarih": date.today().strftime("%d.%m.%Y"),
            "fis_sayisi": len(bugun_fisler),
            "toplam_kg": toplam_kg,
            "toplam_ton": round(toplam_kg / 1000, 2),
            "tamamlanan": tamamlanan,
            "bekleyen": bekleyen,
        })
    except Exception as e:
        return JSONResponse({"hata": str(e)})
    finally:
        db.close()


@app.get("/api/son-fisler")
async def son_fisler():
    """Son 10 yüklenen fişi döndürür."""
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket
        fisler = db.query(WeighTicket).order_by(
            WeighTicket.created_at.desc()
        ).limit(10).all()
        return JSONResponse({
            "fisler": [
                {
                    "id": t.id,
                    "plaka": t.plaka or "-",
                    "firma": t.firma or "-",
                    "agirlik_kg": t.agirlik_kg or 0,
                    "tarih": t.fis_tarihi.strftime("%d.%m.%Y") if t.fis_tarihi else "-",
                    "status": t.status.value if t.status else "-",
                    "created_at": t.created_at.strftime("%d.%m.%Y %H:%M") if t.created_at else "-",
                }
                for t in fisler
            ]
        })
    except Exception as e:
        return JSONResponse({"fisler": [], "hata": str(e)})
    finally:
        db.close()


# ── FATURA DETAY ─────────────────────────────────────────────────────
@app.get("/api/fatura-detay/{fatura_id}")
async def fatura_detay_endpoint(fatura_id: str):
    try:
        from app.parasut.client import fatura_detay_getir
        detay = fatura_detay_getir(fatura_id)
        return JSONResponse(detay)
    except Exception as e:
        return JSONResponse({"hata": str(e)})


@app.get("/api/cari-faturalar/{cari_adi}")
async def cari_faturalar_endpoint(cari_adi: str):
    try:
        from app.parasut.client import cari_id_bul, cari_faturalari_getir
        cari_id = cari_id_bul(cari_adi)
        if not cari_id:
            return JSONResponse({"faturalar": [], "hata": "Cari bulunamadı"})
        faturalar = cari_faturalari_getir(cari_id, limit=25)
        return JSONResponse({"faturalar": faturalar, "toplam": len(faturalar)})
    except Exception as e:
        return JSONResponse({"faturalar": [], "hata": str(e)})


# ── TOPLU FATURA KES ─────────────────────────────────────────────────
@app.post("/api/toplu-fatura-kes")
async def toplu_fatura_kes_endpoint(request: Request):
    """
    Seçilen birden fazla irsaliyeyi tek faturada birleştirir.
    Her irsaliye için ayrı malzeme+fiyat bilgisi alınır.
    """
    data = await request.json()
    cari_adi          = data.get("cari_adi")
    irsaliyeler_data  = data.get("irsaliyeler", [])  # [{parasut_irsaliye_id, malzemeler, toplanan_fiyatlar, agirlik_kg}]

    if not cari_adi or not irsaliyeler_data:
        return JSONResponse({"cevap": "❌ Eksik bilgi.", "basarili": False})

    db = SessionLocal()
    try:
        from app.parasut.client import cari_id_bul, parasut_post, parasut_delete, cari_cache_yenile
        from app.core.config import PARASUT_COMPANY_ID, PARASUT_HURDA_PRODUCT_ID
        from app.core.database import Waybill, WeighTicket, TicketStatus
        from datetime import datetime

        contact_id = cari_id_bul(cari_adi)
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
                    detaylar.append({
                        "type": "sales_invoice_details",
                        "attributes": {
                            "quantity": kg,
                            "unit_price": str(float(fiyat)),
                            "vat_rate": 0,
                            "description": f"{malzeme_adi} — {kg:,} kg"
                        },
                        "relationships": {
                            "product": {"data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}
                        }
                    })
            else:
                birim_fiyat = float(list(toplanan_fiyatlar.values())[0]) if toplanan_fiyatlar else 0
                kg = float(agirlik_kg) if agirlik_kg else 0
                toplam_tutar += kg * birim_fiyat
                detaylar.append({
                    "type": "sales_invoice_details",
                    "attributes": {
                        "quantity": kg,
                        "unit_price": str(birim_fiyat),
                        "vat_rate": 0,
                        "description": f"Hurda — {kg:,.0f} kg"
                    },
                    "relationships": {
                        "product": {"data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}
                    }
                })

        if not detaylar:
            return JSONResponse({"cevap": "❌ Fatura kalemi oluşturulamadı.", "basarili": False})

        istek = {
            "data": {
                "type": "sales_invoices",
                "attributes": {
                    "item_type": "invoice",
                    "description": f"Toplu Hurda Alım — {len(irsaliye_ids)} irsaliye",
                    "issue_date": bugun,
                    "due_date": bugun,
                    "currency": "TRL"
                },
                "relationships": {
                    "contact": {"data": {"type": "contacts", "id": str(contact_id)}},
                    "details": {"data": detaylar},
                    "shipment_documents": {"data": shipment_docs}
                }
            }
        }

        try:
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek)
        except Exception as e:
            logger.warning(f"İrsaliye bağlantılı toplu fatura başarısız, bağlantısız deneniyor: {e}")
            istek["data"]["relationships"].pop("shipment_documents", None)
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek)

        fatura_id = sonuc["data"]["id"]
        logger.info(f"✅ Toplu fatura kesildi: {fatura_id} ← {len(irsaliye_ids)} irsaliye cari={cari_adi}")
        _islem_log(db, "TOPLU_FATURA_KESILDI", cari_adi=cari_adi, tutar=int(toplam_tutar),
                   fatura_id=fatura_id, aciklama=f"{len(irsaliye_ids)} irsaliye birleştirildi")

        # DB güncelle ve irsaliyeleri sil
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
                parasut_delete(f"/{PARASUT_COMPANY_ID}/shipment_documents/{pid}")
                logger.info(f"✅ İrsaliye silindi: {pid}")
            except Exception as e:
                logger.warning(f"İrsaliye silinemedi ({pid}): {e}")

        try:
            cari_cache_yenile()
        except Exception:
            pass

        ozet = f"✅ Toplu fatura kesildi!\n\n🏢 {cari_adi}\n📦 {len(irsaliye_ids)} irsaliye birleştirildi\n💵 Toplam: {int(toplam_tutar):,} TL\n🧾 Fatura No: {fatura_id}"
        return JSONResponse({"cevap": ozet, "basarili": True, "fatura_no": fatura_id})

    except Exception as e:
        logger.error(f"Toplu fatura kes hatası: {e}")
        return JSONResponse({"cevap": f"❌ Toplu fatura kesilemedi: {str(e)}", "basarili": False})
    finally:
        db.close()


# ── FATURA KES (Python, Claude yok) ──────────────────────────────────
@app.post("/api/fatura-kes")
async def fatura_kes_endpoint(request: Request):
    """
    Paraşüt irsaliye ID'si ve fiyat bilgisiyle direkt fatura keser.
    Başarılı olunca DB'deki ilgili waybill ve ticket'ı TAMAMLANDI yapar.
    Claude devreye girmez.
    """
    data = await request.json()
    parasut_irsaliye_id = data.get("parasut_irsaliye_id")
    cari_adi            = data.get("cari_adi")
    agirlik_kg          = data.get("agirlik_kg", 0)
    malzemeler          = data.get("malzemeler", {})
    toplanan_fiyatlar   = data.get("toplanan_fiyatlar", {})

    if not parasut_irsaliye_id or not cari_adi or not toplanan_fiyatlar:
        return JSONResponse({"cevap": "❌ Eksik bilgi: irsaliye ID, cari adı ve fiyat gerekli."})

    db = SessionLocal()
    try:
        from app.parasut.client import cari_id_bul, parasut_post
        from app.core.config import PARASUT_COMPANY_ID, PARASUT_HURDA_PRODUCT_ID
        from app.core.database import Waybill, WeighTicket, TicketStatus
        from datetime import datetime

        contact_id = cari_id_bul(cari_adi)
        if not contact_id:
            return JSONResponse({"cevap": f"❌ '{cari_adi}' carisi Paraşüt'te bulunamadı."})

        bugun = datetime.now().strftime("%Y-%m-%d")

        # Fatura detaylarını oluştur
        if malzemeler and len(toplanan_fiyatlar) > 1:
            detaylar = []
            toplam_tutar = 0
            for malzeme_adi, fiyat in toplanan_fiyatlar.items():
                kg = malzemeler.get(malzeme_adi, 0)
                if kg <= 0:
                    continue
                toplam_tutar += kg * fiyat
                detaylar.append({
                    "type": "sales_invoice_details",
                    "attributes": {
                        "quantity": kg,
                        "unit_price": str(float(fiyat)),
                        "vat_rate": 0,
                        "description": f"{malzeme_adi} — {kg:,} kg"
                    },
                    "relationships": {
                        "product": {"data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}
                    }
                })
        else:
            birim_fiyat  = float(list(toplanan_fiyatlar.values())[0])
            kg           = float(agirlik_kg) if agirlik_kg else float(sum(malzemeler.values())) if malzemeler else 0
            toplam_tutar = kg * birim_fiyat
            detaylar     = [{
                "type": "sales_invoice_details",
                "attributes": {
                    "quantity": kg,
                    "unit_price": str(birim_fiyat),
                    "vat_rate": 0,
                    "description": f"Hurda — {kg:,.0f} kg"
                },
                "relationships": {
                    "product": {"data": {"type": "products", "id": str(PARASUT_HURDA_PRODUCT_ID)}}
                }
            }]

        istek = {
            "data": {
                "type": "sales_invoices",
                "attributes": {
                    "item_type": "invoice",
                    "description": f"Hurda Alım — İrsaliye {parasut_irsaliye_id}",
                    "issue_date": bugun,
                    "due_date": bugun,
                    "currency": "TRL"
                },
                "relationships": {
                    "contact": {"data": {"type": "contacts", "id": str(contact_id)}},
                    "details": {"data": detaylar},
                    "shipment_document": {"data": {"type": "shipment_documents", "id": str(parasut_irsaliye_id)}}
                }
            }
        }

        try:
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek)
        except Exception as e:
            logger.warning(f"İrsaliye bağlantılı fatura başarısız, bağlantısız deneniyor: {e}")
            istek["data"]["relationships"].pop("shipment_document", None)
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek)

        fatura_id = sonuc["data"]["id"]
        logger.info(f"✅ Fatura kesildi: {fatura_id} ← irsaliye {parasut_irsaliye_id} cari={cari_adi}")
        _islem_log(db, "FATURA_KESILDI", cari_adi=cari_adi, tutar=int(toplam_tutar),
                   fatura_id=fatura_id, irsaliye_id=str(parasut_irsaliye_id))

        # ── DB'yi güncelle: waybill ve ticket → TAMAMLANDI ──────────
        try:
            waybill = db.query(Waybill).filter(
                Waybill.parasut_irsaliye_id == str(parasut_irsaliye_id)
            ).first()
            if waybill:
                waybill.status = TicketStatus.TAMAMLANDI
                ticket = db.query(WeighTicket).filter(
                    WeighTicket.id == waybill.ticket_id
                ).first()
                if ticket:
                    ticket.status = TicketStatus.TAMAMLANDI
                db.commit()
                logger.info(f"✅ DB güncellendi: waybill={waybill.id} ticket={waybill.ticket_id} → TAMAMLANDI")
            else:
                logger.warning(f"⚠️ DB'de waybill bulunamadı: parasut_irsaliye_id={parasut_irsaliye_id}")
        except Exception as e:
            logger.error(f"DB güncelleme hatası: {e}")
            # Fatura kesildi, DB hatası kritik değil — devam et

        # ── Paraşüt'ten irsaliyeyi sil — listede bir daha çıkmasın ──
        try:
            from app.parasut.client import parasut_delete as _delete
            _delete(f"/{PARASUT_COMPANY_ID}/shipment_documents/{parasut_irsaliye_id}")
            logger.info(f"✅ Paraşüt irsaliye silindi: {parasut_irsaliye_id}")
        except Exception as e:
            logger.warning(f"⚠️ Paraşüt irsaliye silinemedi (kritik değil): {e}")

        # Özet mesaj
        ozet = f"✅ Fatura kesildi!\n\n🏢 {cari_adi}\n"
        for malzeme_adi_ozet, fiyat_ozet in toplanan_fiyatlar.items():
            kg_val = malzemeler.get(malzeme_adi_ozet, agirlik_kg) if malzemeler else agirlik_kg
            ozet += f"  • {malzeme_adi_ozet if malzemeler and len(toplanan_fiyatlar) > 1 else 'Hurda'}: {kg_val:,.0f} kg × {fiyat_ozet:,.0f} TL/kg = {int(kg_val * fiyat_ozet):,} TL\n"
        ozet += f"\n💵 Toplam: {int(toplam_tutar):,} TL\n"
        ozet += f"🧾 Fatura No: {fatura_id}"

        # Cache'i yenile — bakiye değişti
        try:
            from app.parasut.client import cari_cache_yenile
            cari_cache_yenile()
            logger.info("✅ Cari cache yenilendi")
        except Exception as e:
            logger.warning(f"Cache yenileme hatası: {e}")

        return JSONResponse({"cevap": ozet, "basarili": True, "fatura_no": fatura_id})

    except Exception as e:
        logger.error(f"Fatura kes hatası: {e}")
        _islem_log(db, "FATURA_HATA", cari_adi=cari_adi, irsaliye_id=str(parasut_irsaliye_id), aciklama=str(e))
        return JSONResponse({"cevap": f"❌ Fatura kesilemedi: {str(e)}", "basarili": False})
    finally:
        db.close()





# ── ADMIN TOKEN KONTROLÜ ──────────────────────────────────────────────
def _admin_kontrol(request: Request) -> bool:
    """Basit admin token kontrolü."""
    from app.core.config import ADMIN_TOKEN
    token = request.headers.get("X-Admin-Token") or request.query_params.get("token")
    return token == ADMIN_TOKEN


@app.get("/admin/irsaliye-debug/{irsaliye_id}")
async def irsaliye_debug(irsaliye_id: str, request: Request):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    try:
        from app.parasut.client import parasut_get
        from app.core.config import PARASUT_COMPANY_ID
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/shipment_documents/{irsaliye_id}",
            params={"include": "stock_movements,contact"}
        )
        return JSONResponse(sonuc)
    except Exception as e:
        return JSONResponse({"hata": str(e)})


@app.get("/admin/payment-debug")
async def payment_debug(request: Request):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    """Parasut'te odeme yapisini test eder."""
    try:
        from app.parasut.client import parasut_get, parasut_post
        from app.core.config import PARASUT_COMPANY_ID

        # Hesap detayini getir
        hesaplar = parasut_get(f"/{PARASUT_COMPANY_ID}/accounts")
        hesap_listesi = []
        for h in hesaplar.get("data", []):
            attr = h.get("attributes", {})
            hesap_listesi.append({
                "id": h["id"],
                "type": h["type"],
                "ad": attr.get("name"),
                "tip": attr.get("account_type"),
                "currency": attr.get("currency"),
            })

        # Son faturalardan birini bul
        faturalar = parasut_get(
            f"/{PARASUT_COMPANY_ID}/sales_invoices",
            params={"page[size]": 1, "sort": "-issue_date"}
        )
        son_fatura = None
        if faturalar.get("data"):
            f = faturalar["data"][0]
            son_fatura = {"id": f["id"], "type": f["type"]}

        return JSONResponse({
            "hesaplar": hesap_listesi,
            "son_fatura": son_fatura,
            "company_id": PARASUT_COMPANY_ID
        })
    except Exception as e:
        return JSONResponse({"hata": str(e)})


@app.get("/admin/islem-loglari")
async def islem_loglari(request: Request, limit: int = 50, islem_tipi: str = None):
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
            {
                "id": l.id,
                "islem_tipi": l.islem_tipi,
                "cari_adi": l.cari_adi,
                "tutar": l.tutar,
                "fatura_id": l.fatura_id,
                "irsaliye_id": l.irsaliye_id,
                "plaka": l.plaka,
                "aciklama": l.aciklama,
                "tarih": l.created_at.strftime("%d.%m.%Y %H:%M") if l.created_at else "-"
            }
            for l in loglar
        ], "toplam": len(loglar)})
    except Exception as e:
        return JSONResponse({"hata": str(e)})
    finally:
        db.close()


@app.get("/admin/konusmalar")
async def konusmalar(request: Request, limit: int = 20):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    db = SessionLocal()
    try:
        from app.core.database import ChatSession
        from sqlalchemy import desc
        sessionlar = db.query(ChatSession).order_by(desc(ChatSession.updated_at)).limit(limit).all()
        return JSONResponse({"sessionlar": [
            {
                "session_id": s.session_id,
                "isim": s.isim,
                "mesaj_sayisi": len(_json.loads(s.tam_gecmis)) if s.tam_gecmis else 0,
                "son_guncelleme": s.updated_at.strftime("%d.%m.%Y %H:%M") if s.updated_at else "-",
                "tam_gecmis": _json.loads(s.tam_gecmis) if s.tam_gecmis else []
            }
            for s in sessionlar
        ]})
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
        hesaplar = []
        for h in sonuc.get("data", []):
            attr = h.get("attributes", {})
            hesaplar.append({
                "id": h["id"],
                "ad": attr.get("name"),
                "tip": attr.get("account_type"),
                "para_birimi": attr.get("currency"),
                "bakiye": attr.get("balance"),
            })
        return JSONResponse({"hesaplar": hesaplar})
    except Exception as e:
        return JSONResponse({"hata": str(e)})


@app.get("/admin/reset-db")
async def reset_db(request: Request):
    if not _admin_kontrol(request):
        return JSONResponse({"hata": "Yetkisiz erişim"}, status_code=401)
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text('DELETE FROM islem_logs'))
        conn.execute(text('DELETE FROM chat_sessions'))
        conn.execute(text('DELETE FROM invoices'))
        conn.execute(text('DELETE FROM waybills'))
        conn.execute(text('DELETE FROM alerts'))
        conn.execute(text('DELETE FROM weigh_tickets'))
        conn.execute(text('DELETE FROM vehicles'))
        conn.execute(text('DELETE FROM system_logs'))
        conn.execute(text('DELETE FROM processed_mails'))
        conn.commit()
    logger.info("🗑️ DB tamamen temizlendi (admin)")
    return {"status": "temizlendi", "tablolar": [
        "islem_logs", "chat_sessions", "invoices", "waybills",
        "alerts", "weigh_tickets", "vehicles", "system_logs", "processed_mails"
    ]}


async def startup_recovery():
    db = SessionLocal()
    try:
        # Migration: malzemeler_json kolonu yoksa ekle
        from sqlalchemy import text
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE waybills ADD COLUMN IF NOT EXISTS malzemeler_json TEXT"))
                conn.commit()
                logger.info("✅ Migration: malzemeler_json kolonu hazır")
            except Exception as e:
                logger.warning(f"Migration atlandı: {e}")

            try:
                conn.execute(text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS tam_gecmis TEXT"))
                conn.commit()
                logger.info("✅ Migration: tam_gecmis kolonu hazır")
            except Exception as e:
                logger.warning(f"Migration tam_gecmis atlandı: {e}")

            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS islem_logs (
                        id SERIAL PRIMARY KEY,
                        islem_tipi VARCHAR(50) NOT NULL,
                        cari_adi VARCHAR(200),
                        tutar BIGINT,
                        fatura_id VARCHAR(100),
                        irsaliye_id VARCHAR(100),
                        plaka VARCHAR(20),
                        aciklama TEXT,
                        session_id VARCHAR(100),
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """))
                conn.commit()
                logger.info("✅ Migration: islem_logs tablosu hazır")
            except Exception as e:
                logger.warning(f"Migration islem_logs atlandı: {e}")

        from app.core.database import WeighTicket, TicketStatus
        bekleyenler = db.query(WeighTicket).filter(
            WeighTicket.status == TicketStatus.IRSALIYE_OLUSTURULUYOR
        ).all()
        if bekleyenler:
            logger.warning(f"⚠️ {len(bekleyenler)} yarım kalan işlem tespit edildi")
        else:
            logger.info("✅ Yarım kalan işlem yok")
    except Exception as e:
        logger.error(f"Startup recovery hatası: {e}")
    finally:
        db.close()