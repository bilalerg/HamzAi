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


def _chat_html() -> str:
    return _HTML_PATH.read_text(encoding="utf-8")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Profaix başlatılıyor...")
    validate_config()
    try:
        init_db()
    except Exception as e:
        logger.warning(f"⚠️ DB başlatma gecikmeli, devam ediliyor: {e}")
    try:
        await startup_recovery()
    except Exception as e:
        logger.warning(f"⚠️ Startup recovery atlandı: {e}")
    logger.info(f"✅ Profaix hazır — Ortam: {ENV}")
    yield
    logger.info("Profaix kapanıyor...")


app = FastAPI(title="Profaix", description="Muhasebe Otomasyon Ajanı", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(webhook_router)


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
            return JSONResponse({"cevap": f"❌ Fiş okunamadı: {fis.hata}\n\nFotoğrafı daha net çekip tekrar deneyin."})

        ticket = fis_db_kaydet(fis, tmp_path, db)

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
            return JSONResponse({"cevap": "❌ İrsaliye oluşturulamadı, tekrar deneyin."})

        waybill_id = sonuc["waybill_id"]
        parasut_id = sonuc.get("parasut_id", "-")

        waybill_obj = db.query(Waybill).filter(Waybill.id == waybill_id).first()
        if waybill_obj and malzemeler:
            waybill_obj.malzemeler_json = _json.dumps(malzemeler, ensure_ascii=False)
            db.commit()

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
async def cariler_getir():
    """Tüm carileri ve güncel bakiyelerini Paraşüt'ten döndürür."""
    try:
        from app.parasut.client import cari_listesi_getir
        cariler = cari_listesi_getir()
        # Sadece bakiyesi olan veya aktif carileri döndür
        sonuc = [
            {"id": c["id"], "ad": c["ad"], "bakiye": c.get("bakiye", 0)}
            for c in cariler
        ]
        # Bakiyeye göre sırala (en yüksek alacak önce)
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
    Tüm carilerin bekleyen irsaliyelerini getirir.
    DB'de TAMAMLANDI olan waybill'lerin parasut_irsaliye_id'lerini filtreler —
    faturası kesilmiş irsaliyeler listede çıkmaz.
    """
    db = SessionLocal()
    try:
        from app.parasut.client import cari_listesi_getir, cari_bekleyen_irsaliyeleri_getir
        from app.core.database import Waybill, TicketStatus

        # DB'de tamamlanmış irsaliyelerin Paraşüt ID'lerini çek
        tamamlananlar = db.query(Waybill).filter(
            Waybill.status == TicketStatus.TAMAMLANDI
        ).all()
        tamamlanan_ids = {w.parasut_irsaliye_id for w in tamamlananlar if w.parasut_irsaliye_id}
        logger.info(f"Tamamlanmış irsaliye ID'leri: {tamamlanan_ids}")

        cariler = cari_listesi_getir()
        sonuc = []
        for cari in cariler:
            irsaliyeler = cari_bekleyen_irsaliyeleri_getir(cari["ad"])
            for irs in irsaliyeler:
                # Faturası kesilmişse atla
                if str(irs["id"]) in tamamlanan_ids:
                    continue
                sonuc.append({
                    "parasut_irsaliye_id": irs["id"],
                    "cari_adi": cari["ad"],
                    "tarih": irs.get("tarih", "-"),
                    "toplam_kg": irs.get("toplam_kg", 0),
                    "kalemler": irs.get("kalemler", []),
                    "aciklama": irs.get("aciklama", "-"),
                })

        return JSONResponse({"irsaliyeler": sonuc, "toplam": len(sonuc)})
    except Exception as e:
        logger.error(f"Bekleyen irsaliye hatası: {e}")
        return JSONResponse({"irsaliyeler": [], "hata": str(e)})
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

        # Özet mesaj
        ozet = f"✅ Fatura kesildi!\n\n🏢 {cari_adi}\n"
        for m, f in toplanan_fiyatlar.items():
            kg_val = malzemeler.get(m, agirlik_kg) if malzemeler else agirlik_kg
            ozet += f"  • {m if malzemeler and len(toplanan_fiyatlar) > 1 else 'Hurda'}: {kg_val:,.0f} kg × {f:,.0f} TL/kg = {int(kg_val * f):,} TL\n"
        ozet += f"\n💵 Toplam: {int(toplam_tutar):,} TL\n"
        ozet += f"🧾 Fatura No: {fatura_id}"

        return JSONResponse({"cevap": ozet, "basarili": True, "fatura_no": fatura_id})

    except Exception as e:
        logger.error(f"Fatura kes hatası: {e}")
        return JSONResponse({"cevap": f"❌ Fatura kesilemedi: {str(e)}", "basarili": False})
    finally:
        db.close()


# ── ESKİ ENDPOINT'LER (geriye dönük uyumluluk) ───────────────────────
@app.post("/api/fiyat")
async def fiyat_gir(request: Request):
    data = await request.json()
    waybill_id        = data.get("waybill_id")
    fiyat_str         = data.get("fiyat", "").strip()
    malzeme_listesi   = data.get("malzeme_listesi", [])
    malzeme_index     = data.get("malzeme_index", 0)
    toplanan_fiyatlar = data.get("toplanan_fiyatlar", {})
    malzemeler        = data.get("malzemeler", {})

    db = SessionLocal()
    try:
        from app.chatbox.webhook import birim_fiyat_cikar
        from app.parasut.fatura import fatura_kes

        birim_fiyat = birim_fiyat_cikar(fiyat_str)
        if not birim_fiyat:
            return JSONResponse({"cevap": "❌ Geçersiz fiyat. Örnek: 14.85 veya 14850"})

        if malzeme_listesi:
            mevcut_malzeme = malzeme_listesi[malzeme_index]
            toplanan_fiyatlar[mevcut_malzeme] = birim_fiyat
            sonraki_index = malzeme_index + 1

            if sonraki_index < len(malzeme_listesi):
                sonraki_malzeme = malzeme_listesi[sonraki_index]
                sonraki_kg = malzemeler.get(sonraki_malzeme, 0)
                return JSONResponse({
                    "cevap": (
                        f"✅ {mevcut_malzeme}: {birim_fiyat:,} TL/kg kaydedildi.\n\n"
                        f"💰 {sonraki_malzeme} fiyatı nedir? ({sonraki_kg:,} kg / {sonraki_kg/1000:.2f} ton)\n"
                        f"Örnek: 14.85 veya 14850"
                    ),
                    "bekliyor": "malzeme_fiyat",
                    "waybill_id": waybill_id,
                    "malzemeler": malzemeler,
                    "malzeme_listesi": malzeme_listesi,
                    "malzeme_index": sonraki_index,
                    "toplanan_fiyatlar": toplanan_fiyatlar
                })
            else:
                ozet = "📋 Fiyat Özeti:\n"
                toplam_tutar = 0
                for malzeme_adi, fiyat in toplanan_fiyatlar.items():
                    kg = malzemeler.get(malzeme_adi, 0)
                    tutar = kg * fiyat
                    toplam_tutar += tutar
                    ozet += f"  • {malzeme_adi}: {kg:,} kg × {fiyat:,} TL/kg = {int(tutar):,} TL\n"
                ozet += f"\n💵 TOPLAM: {int(toplam_tutar):,} TL\n\nFatura kesilsin mi? (evet/hayır)"
                return JSONResponse({
                    "cevap": ozet,
                    "bekliyor": "onay",
                    "waybill_id": waybill_id,
                    "malzemeler": malzemeler,
                    "toplanan_fiyatlar": toplanan_fiyatlar,
                    "toplam_tutar": toplam_tutar
                })
        else:
            sonuc = fatura_kes(waybill_id, birim_fiyat)
            if not sonuc.get("basarili"):
                return JSONResponse({"cevap": f"❌ Fatura kesilemedi: {sonuc.get('hata')}"})
            toplam = sonuc.get("toplam_tutar", 0)
            return JSONResponse({
                "cevap": (
                    f"✅ Fatura kesildi!\n\n"
                    f"💰 Birim Fiyat: {birim_fiyat:,} TL/kg\n"
                    f"💵 Toplam Tutar: {int(toplam):,} TL\n"
                    f"🧾 Fatura No: {sonuc.get('fatura_no', '-')}\n"
                    f"Fatura Paraşüt'e kaydedildi."
                )
            })
    except Exception as e:
        logger.error(f"Fiyat hatası: {e}")
        return JSONResponse({"cevap": f"❌ Fatura kesilemedi: {str(e)}"})
    finally:
        db.close()


@app.post("/api/onay")
async def fatura_onayla(request: Request):
    data = await request.json()
    waybill_id        = data.get("waybill_id")
    toplanan_fiyatlar = data.get("toplanan_fiyatlar", {})
    malzemeler        = data.get("malzemeler", {})
    onay              = data.get("onay", "").strip().lower()

    if onay not in ["evet", "e", "yes", "tamam", "ok"]:
        return JSONResponse({"cevap": "❌ Fatura iptal edildi. Yeni fiş yükleyebilirsiniz."})

    db = SessionLocal()
    try:
        from app.parasut.fatura import fatura_kes_malzemeli
        sonuc = fatura_kes_malzemeli(waybill_id, malzemeler, toplanan_fiyatlar)
        if not sonuc.get("basarili"):
            return JSONResponse({"cevap": f"❌ Fatura kesilemedi: {sonuc.get('hata')}"})
        toplam_tutar = sonuc.get("toplam_tutar", 0)
        ozet = "✅ Fatura kesildi!\n\n"
        for malzeme_adi, fiyat in toplanan_fiyatlar.items():
            kg = malzemeler.get(malzeme_adi, 0)
            ozet += f"  • {malzeme_adi}: {kg:,} kg × {fiyat:,} TL/kg = {int(kg * fiyat):,} TL\n"
        ozet += f"\n💵 Toplam: {int(toplam_tutar):,} TL\n"
        ozet += f"🧾 Fatura No: {sonuc.get('fatura_no', '-')}\n"
        return JSONResponse({"cevap": ozet})
    except Exception as e:
        logger.error(f"Onay hatası: {e}")
        return JSONResponse({"cevap": f"❌ Fatura kesilemedi: {str(e)}"})
    finally:
        db.close()


@app.get("/admin/accounts-debug")
async def accounts_debug():
    """Parasut'teki tum hesaplari listeler — tahsilat icin dogru hesap ID'sini bulmak icin."""
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
async def reset_db():
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text('DELETE FROM invoices'))
        conn.execute(text('DELETE FROM waybills'))
        conn.execute(text('DELETE FROM alerts'))
        conn.execute(text('DELETE FROM weigh_tickets'))
        conn.execute(text('DELETE FROM vehicles'))
        conn.execute(text('DELETE FROM processed_mails'))
        conn.commit()
    logger.info("🗑️ DB temizlendi (admin)")
    return {"status": "temizlendi"}


async def startup_recovery():
    db = SessionLocal()
    try:
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