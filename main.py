# main.py — Profaix FastAPI Uygulaması

import os
import tempfile
import json as _json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from app.core.config import validate_config, ENV
from app.core.database import init_db, SessionLocal, engine
from app.core.logger import logger
from app.chatbox.webhook import router as webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Profaix başlatılıyor...")
    validate_config()
    init_db()
    await startup_recovery()
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
    return HTMLResponse(content=CHAT_HTML)


# ── CHAT (sadece bilgi sorguları, aksiyon yok) ────────────────────────
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

        logger.info(f"📸 Chatbot fotoğraf alındı: {tmp_path} ({len(icerik)} bytes)")

        from app.ocr.reader import fis_oku, fis_db_kaydet
        fis = fis_oku(tmp_path)

        if fis.hata:
            return JSONResponse({"cevap": f"❌ Fiş okunamadı: {fis.hata}\n\nFotoğrafı daha net çekip tekrar deneyin."})

        ticket = fis_db_kaydet(fis, tmp_path, db)

        from app.ocr.plaka_kontrol import plaka_kontrol_et, PlakaSonuc
        plaka_sonuc = plaka_kontrol_et(fis.plaka, ticket.id, db, fis_no=fis.fis_no)

        if plaka_sonuc == PlakaSonuc.DUPLICATE:
            return JSONResponse({
                "cevap": f"⚠️ Bu fiş daha önce sisteme girilmiş!\n\n"
                         f"📋 Fiş No: {fis.fis_no}\n"
                         f"🚗 Plaka: {fis.plaka}\n\n"
                         f"Mükerrer kayıt tespit edildi, işlem yapılmadı."
            })

        yeni_plaka_uyari = ""
        if plaka_sonuc == PlakaSonuc.YENI:
            yeni_plaka_uyari = "\n🆕 Bu plaka sisteme ilk kez eklendi."

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

        from app.parasut.irsaliye import irsaliye_olustur
        sonuc = irsaliye_olustur(ticket_id, beklet=True)

        if not sonuc.get("basarili"):
            return JSONResponse({"cevap": "❌ İrsaliye oluşturulamadı, tekrar deneyin."})

        waybill_id = sonuc["waybill_id"]
        parasut_id = sonuc.get("parasut_id", "-")

        # Malzeme bilgisini waybill'e kaydet
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
                f"İrsaliye sisteme kaydedildi. Fatura kesmek istediğinizde "
                f"'bekleyen irsaliyeler' yazın veya aşağıdaki butona tıklayın."
            ),
            "bekliyor": "bitti",   # artık fiyat beklemiyor, sadece bilgi
        })
    except Exception as e:
        logger.error(f"Firma seç hatası: {e}")
        return JSONResponse({"cevap": f"❌ Hata oluştu: {str(e)}"})
    finally:
        db.close()


# ── BEKLEYENİRSALİYELER (Python, Claude yok) ─────────────────────────
@app.get("/api/bekleyen-irsaliyeler")
async def bekleyen_irsaliyeler():
    """Tüm carilerin bekleyen irsaliyelerini Paraşüt'ten çeker."""
    try:
        from app.parasut.client import cari_listesi_getir, cari_bekleyen_irsaliyeleri_getir
        cariler = cari_listesi_getir()
        sonuc = []
        for cari in cariler:
            irsaliyeler = cari_bekleyen_irsaliyeleri_getir(cari["ad"])
            for irs in irsaliyeler:
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


# ── FATURA KES (waybill_id ile, Python, Claude yok) ──────────────────
@app.post("/api/fatura-kes")
async def fatura_kes_endpoint(request: Request):
    """
    Paraşüt irsaliye ID'si ve fiyat bilgisiyle direkt fatura keser.
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
        from datetime import datetime

        contact_id = cari_id_bul(cari_adi)
        if not contact_id:
            return JSONResponse({"cevap": f"❌ '{cari_adi}' carisi Paraşüt'te bulunamadı."})

        bugun = datetime.now().strftime("%Y-%m-%d")

        # Malzeme bazlı mı tek fiyat mı?
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
        except Exception:
            # İrsaliye bağlantısı kurulamazsa bağlantısız dene
            logger.warning(f"İrsaliye bağlantılı fatura başarısız, bağlantısız deneniyor")
            istek["data"]["relationships"].pop("shipment_document", None)
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices", istek)

        fatura_id = sonuc["data"]["id"]
        logger.info(f"✅ Fatura kesildi: {fatura_id} ← irsaliye {parasut_irsaliye_id} cari={cari_adi}")

        # Ozet mesaj
        ozet = f"✅ Fatura kesildi!\n\n🏢 {cari_adi}\n"
        for m, f in toplanan_fiyatlar.items():
            kg = malzemeler.get(m, agirlik_kg) if malzemeler else agirlik_kg
            ozet += f"  • {m if malzemeler else 'Hurda'}: {kg:,} kg × {f:,} TL/kg = {int(kg*f):,} TL\n"
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
                    "cevap": f"✅ {mevcut_malzeme}: {birim_fiyat:,} TL/kg kaydedildi.\n\n"
                             f"💰 {sonraki_malzeme} fiyatı nedir? ({sonraki_kg:,} kg / {sonraki_kg/1000:.2f} ton)\n"
                             f"Örnek: 14.85 veya 14850",
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
                "cevap": f"✅ Fatura kesildi!\n\n"
                         f"💰 Birim Fiyat: {birim_fiyat:,} TL/kg\n"
                         f"💵 Toplam Tutar: {int(toplam):,} TL\n"
                         f"🧾 Fatura No: {sonuc.get('fatura_no', '-')}\n"
                         f"Fatura Paraşüt'e kaydedildi."
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


CHAT_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Profaix — Sorgu Paneli</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500;9..40,600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#f5f4f1;--surface:#ffffff;--border:#e6e2db;--border2:#d0cbc3;
    --accent:#2d5a3d;--accent-lt:#eaf2ec;--text:#1c1c1a;--muted:#8c8880;
    --muted2:#bab7b0;--user-bg:#2d5a3d;
    --shadow-sm:0 1px 2px rgba(0,0,0,0.05);
    --shadow:0 2px 8px rgba(0,0,0,0.06),0 1px 2px rgba(0,0,0,0.04);
  }
  *{margin:0;padding:0;box-sizing:border-box;}
  body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden;}
  .header{padding:0 36px;height:60px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px;background:var(--surface);box-shadow:var(--shadow-sm);position:relative;z-index:10;flex-shrink:0;}
  .logo-mark{width:32px;height:32px;background:var(--accent);border-radius:8px;display:flex;align-items:center;justify-content:center;}
  .logo-mark svg{width:16px;height:16px;stroke:#fff;fill:none;stroke-width:2;stroke-linecap:round;}
  .logo-text{font-family:'Instrument Serif',serif;font-size:19px;color:var(--text);letter-spacing:-0.2px;}
  .logo-text em{font-style:italic;color:var(--accent);}
  .sep{width:1px;height:18px;background:var(--border2);}
  .header-label{font-size:11.5px;color:var(--muted);font-weight:400;letter-spacing:0.3px;}
  .status-pill{margin-left:auto;display:flex;align-items:center;gap:6px;background:var(--accent-lt);border:1px solid #c5d9ca;padding:4px 11px;border-radius:100px;}
  .status-dot{width:6px;height:6px;border-radius:50%;background:var(--accent);animation:glow 2.5s ease-in-out infinite;}
  @keyframes glow{0%,100%{opacity:1;}50%{opacity:0.5;}}
  .status-label{font-size:11px;font-weight:500;color:var(--accent);letter-spacing:0.2px;}
  .messages{flex:1;overflow-y:auto;padding:28px 36px;display:flex;flex-direction:column;gap:18px;scroll-behavior:smooth;}
  .messages::-webkit-scrollbar{width:4px;}
  .messages::-webkit-scrollbar-track{background:transparent;}
  .messages::-webkit-scrollbar-thumb{background:var(--border2);border-radius:4px;}
  .welcome{display:flex;flex-direction:column;align-items:center;text-align:center;padding:56px 24px 40px;gap:14px;}
  .welcome-icon{width:52px;height:52px;background:var(--surface);border:1px solid var(--border);border-radius:14px;display:flex;align-items:center;justify-content:center;box-shadow:var(--shadow);margin-bottom:4px;}
  .welcome-icon svg{width:24px;height:24px;stroke:var(--accent);fill:none;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;}
  .welcome h2{font-family:'Instrument Serif',serif;font-size:30px;font-weight:400;color:var(--text);letter-spacing:-0.4px;line-height:1.25;}
  .welcome h2 em{font-style:italic;color:var(--accent);}
  .welcome p{font-size:13.5px;color:var(--muted);line-height:1.7;max-width:320px;font-weight:300;}
  .quick-actions{display:flex;flex-wrap:wrap;gap:7px;justify-content:center;max-width:500px;margin-top:6px;}
  .quick-btn{background:var(--surface);border:1px solid var(--border);color:var(--text);font-family:'DM Sans',sans-serif;font-size:12px;font-weight:400;padding:7px 14px;border-radius:100px;cursor:pointer;transition:all 0.15s ease;box-shadow:var(--shadow-sm);}
  .quick-btn:hover{background:var(--accent-lt);border-color:#c5d9ca;color:var(--accent);transform:translateY(-1px);box-shadow:var(--shadow);}
  .message{display:flex;flex-direction:column;max-width:72%;animation:pop 0.25s cubic-bezier(0.34,1.56,0.64,1);}
  @keyframes pop{from{opacity:0;transform:translateY(8px) scale(0.97);}to{opacity:1;transform:none;}}
  .message.user{align-self:flex-end;}
  .message.bot{align-self:flex-start;}
  .msg-meta{font-size:10px;color:var(--muted2);margin-bottom:4px;letter-spacing:0.2px;}
  .message.user .msg-meta{text-align:right;}
  .bubble{padding:12px 16px;font-size:13.5px;line-height:1.75;white-space:pre-wrap;word-break:break-word;font-weight:400;}
  .message.user .bubble{background:var(--user-bg);color:#fff;border-radius:14px 14px 3px 14px;box-shadow:0 2px 10px rgba(45,90,61,0.2);}
  .message.bot .bubble{background:var(--surface);color:var(--text);border-radius:3px 14px 14px 14px;border:1px solid var(--border);box-shadow:var(--shadow);}
  .firma-btn{margin-top:6px;padding:9px 16px;background:var(--surface);color:var(--accent);border:1.5px solid #c5d9ca;border-radius:9px;font-family:'DM Sans',sans-serif;font-size:13px;font-weight:500;cursor:pointer;width:100%;transition:all 0.15s;text-align:left;}
  .firma-btn:hover{background:var(--accent-lt);border-color:var(--accent);}
  .irsaliye-btn{margin-top:6px;padding:9px 16px;background:var(--surface);color:var(--text);border:1.5px solid var(--border2);border-radius:9px;font-family:'DM Sans',sans-serif;font-size:13px;font-weight:400;cursor:pointer;width:100%;transition:all 0.15s;text-align:left;display:flex;justify-content:space-between;align-items:center;}
  .irsaliye-btn:hover{background:var(--accent-lt);border-color:var(--accent);color:var(--accent);}
  .irsaliye-btn .kg{font-size:11px;color:var(--muted);font-weight:400;}
  .fiyat-input-wrap{margin-top:10px;display:flex;gap:8px;}
  .fiyat-input{flex:1;padding:9px 12px;border:1.5px solid var(--border2);border-radius:9px;font-family:'DM Sans',sans-serif;font-size:13px;outline:none;background:var(--bg);}
  .fiyat-input:focus{border-color:var(--accent);}
  .fiyat-gonder-btn{padding:9px 18px;background:var(--accent);color:#fff;border:none;border-radius:9px;font-family:'DM Sans',sans-serif;font-size:13px;font-weight:500;cursor:pointer;transition:background 0.15s;}
  .fiyat-gonder-btn:hover{background:#245030;}
  .img-bubble{background:var(--user-bg);padding:6px;border-radius:14px 14px 3px 14px;box-shadow:0 2px 10px rgba(45,90,61,0.2);display:inline-block;}
  .img-bubble img{max-width:220px;max-height:180px;border-radius:9px;display:block;}
  .typing-wrap{display:flex;flex-direction:column;max-width:72%;align-self:flex-start;animation:pop 0.25s cubic-bezier(0.34,1.56,0.64,1);}
  .typing-bubble{background:var(--surface);border:1px solid var(--border);border-radius:3px 14px 14px 14px;padding:14px 18px;display:flex;gap:5px;align-items:center;box-shadow:var(--shadow);width:fit-content;}
  .typing-bubble span{width:6px;height:6px;border-radius:50%;background:var(--muted2);animation:bounce 1.3s ease-in-out infinite;}
  .typing-bubble span:nth-child(2){animation-delay:0.15s;}
  .typing-bubble span:nth-child(3){animation-delay:0.3s;}
  @keyframes bounce{0%,60%,100%{transform:translateY(0);opacity:0.4;}30%{transform:translateY(-5px);opacity:1;}}
  .input-area{padding:14px 36px 18px;border-top:1px solid var(--border);background:var(--surface);flex-shrink:0;}
  .input-box{display:flex;align-items:flex-end;gap:8px;background:var(--bg);border:1.5px solid var(--border2);border-radius:13px;padding:8px 8px 8px 16px;transition:border-color 0.18s,box-shadow 0.18s;}
  .input-box:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px rgba(45,90,61,0.07);}
  textarea{flex:1;background:transparent;border:none;color:var(--text);font-family:'DM Sans',sans-serif;font-size:13.5px;font-weight:400;resize:none;outline:none;max-height:120px;min-height:24px;line-height:1.6;padding:3px 0;}
  textarea::placeholder{color:var(--muted2);}
  .upload-btn{width:36px;height:36px;background:var(--bg);border:1.5px solid var(--border2);border-radius:9px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:all 0.15s;}
  .upload-btn:hover{background:var(--accent-lt);border-color:#c5d9ca;}
  .upload-btn svg{width:16px;height:16px;stroke:var(--muted);fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;}
  .upload-btn:hover svg{stroke:var(--accent);}
  .send-btn{width:36px;height:36px;background:var(--accent);border:none;border-radius:9px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:all 0.15s;}
  .send-btn:hover{background:#245030;transform:scale(1.06);}
  .send-btn:disabled{opacity:0.3;cursor:not-allowed;transform:none;}
  .send-btn svg{width:15px;height:15px;stroke:#fff;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;}
  .input-hint{font-size:10.5px;color:var(--muted2);margin-top:7px;padding-left:2px;}
  @media(max-width:600px){.header,.messages,.input-area{padding-left:16px;padding-right:16px;}.message,.typing-wrap{max-width:90%;}.sep,.header-label{display:none;}}
</style>
</head>
<body>

<div class="header">
  <div class="logo-mark">
    <svg viewBox="0 0 16 16"><path d="M2 4h12M2 8h12M2 12h8"/></svg>
  </div>
  <div class="logo-text">Pro<em>faix</em></div>
  <div class="sep"></div>
  <div class="header-label">Sorgu Paneli</div>
  <div class="status-pill">
    <div class="status-dot"></div>
    <div class="status-label">Çevrimiçi</div>
  </div>
</div>

<div class="messages" id="messages">
  <div class="welcome" id="welcome">
    <div class="welcome-icon">
      <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
    </div>
    <h2>Merhaba,<br><em>Profaix</em>'e hoş geldiniz.</h2>
    <p>Kantar fişi fotoğrafı yükleyin veya sistem verileri hakkında soru sorun.</p>
    <div class="quick-actions">
      <button class="quick-btn" onclick="bekleyenIrsaliyeleriGoster()">📋 Bekleyen irsaliyeler</button>
      <button class="quick-btn" onclick="quickSend('Bugün kaç ton geldi?')">Bugün kaç ton geldi?</button>
      <button class="quick-btn" onclick="quickSend('Müşteri bakiyeleri')">Müşteri bakiyeleri</button>
      <button class="quick-btn" onclick="quickSend('Son 10 fatura')">Son 10 fatura</button>
      <button class="quick-btn" onclick="quickSend('Bugün kaç fatura kesildi?')">Bugün kesilen faturalar</button>
    </div>
  </div>
</div>

<div class="input-area">
  <div class="input-box">
    <button class="upload-btn" onclick="document.getElementById('fileInput').click()" title="Fiş fotoğrafı yükle">
      <svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
    </button>
    <input type="file" id="fileInput" accept="image/*" style="display:none" onchange="uploadFis(this)">
    <textarea id="input" placeholder="Mesajınızı yazın veya fiş fotoğrafı yükleyin…" rows="1" onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
    <button class="send-btn" onclick="sendMessage()" id="sendBtn" title="Gönder">
      <svg viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
    </button>
  </div>
  <div class="input-hint">📎 Fiş yükle &nbsp;·&nbsp; Enter → gönder &nbsp;·&nbsp; Shift+Enter → yeni satır</div>
</div>

<script>
  const SESSION_ID = 'web_' + Math.random().toString(36).substr(2, 9);
  const messagesEl = document.getElementById('messages');
  const inputEl    = document.getElementById('input');
  const sendBtn    = document.getElementById('sendBtn');

  // Aktif fatura akışı state'i
  let faturaAkisi = null; // { irsaliyeler, index, toplanan_fiyatlar }

  function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  }

  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  function removeWelcome() {
    const w = document.getElementById('welcome');
    if (w) w.remove();
  }

  function now() {
    return new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
  }

  function addMessage(text, type, extraEl) {
    removeWelcome();
    const wrap = document.createElement('div');
    wrap.className = 'message ' + type;
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.textContent = (type === 'user' ? 'Siz' : 'Profaix') + ' · ' + now();
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;
    if (extraEl) bubble.appendChild(extraEl);
    wrap.appendChild(meta);
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
  }

  function addImageMessage(src) {
    removeWelcome();
    const wrap = document.createElement('div');
    wrap.className = 'message user';
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.style.textAlign = 'right';
    meta.textContent = 'Siz · ' + now();
    const imgWrap = document.createElement('div');
    imgWrap.className = 'img-bubble';
    const img = document.createElement('img');
    img.src = src;
    img.style.cursor = 'pointer';
    img.onclick = () => {
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center;z-index:999;cursor:pointer;';
      const bigImg = document.createElement('img');
      bigImg.src = src;
      bigImg.style.cssText = 'max-width:90%;max-height:90%;border-radius:12px;';
      overlay.appendChild(bigImg);
      overlay.onclick = () => overlay.remove();
      document.body.appendChild(overlay);
    };
    imgWrap.appendChild(img);
    wrap.appendChild(meta);
    wrap.appendChild(imgWrap);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function showTyping() {
    removeWelcome();
    const wrap = document.createElement('div');
    wrap.className = 'typing-wrap';
    wrap.id = 'typing';
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.textContent = 'Profaix yazıyor…';
    const bubble = document.createElement('div');
    bubble.className = 'typing-bubble';
    bubble.innerHTML = '<span></span><span></span><span></span>';
    wrap.appendChild(meta);
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function removeTyping() {
    const el = document.getElementById('typing');
    if (el) el.remove();
  }

  // ── CHAT (Claude — sadece bilgi) ──────────────────────────────────
  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text || sendBtn.disabled) return;

    // "bekleyen irsaliye" gibi yazılırsa direkt Python endpoint
    const lower = text.toLowerCase();
    if (lower.includes('bekleyen') && (lower.includes('irsaliy') || lower.includes('fatura'))) {
      addMessage(text, 'user');
      inputEl.value = '';
      inputEl.style.height = 'auto';
      await bekleyenIrsaliyeleriGoster();
      return;
    }

    addMessage(text, 'user');
    inputEl.value = '';
    inputEl.style.height = 'auto';
    sendBtn.disabled = true;
    showTyping();

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mesaj: text, session_id: SESSION_ID })
      });
      const data = await res.json();
      removeTyping();
      addMessage(data.cevap, 'bot');
    } catch {
      removeTyping();
      addMessage('❌ Bağlantı hatası, lütfen tekrar deneyin.', 'bot');
    } finally {
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  // ── FİŞ YÜKLE ────────────────────────────────────────────────────
  async function uploadFis(input) {
    const file = input.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => addImageMessage(e.target.result);
    reader.readAsDataURL(file);

    sendBtn.disabled = true;
    showTyping();

    const formData = new FormData();
    formData.append('file', file);
    formData.append('session_id', SESSION_ID);

    try {
      const res = await fetch('/api/upload', { method: 'POST', body: formData });
      const data = await res.json();
      removeTyping();

      if (data.bekliyor === 'firma_sec') {
        const wrap = document.createElement('div');
        (data.firma_listesi || []).forEach(firma => {
          const btn = document.createElement('button');
          btn.className = 'firma-btn';
          btn.textContent = '🏭 ' + firma;
          btn.onclick = () => firmaSecildi(data.ticket_id, firma, data.malzemeler, wrap);
          wrap.appendChild(btn);
        });
        addMessage(data.cevap, 'bot', wrap);
      } else {
        addMessage(data.cevap, 'bot');
      }
      inputEl.focus();
    } catch {
      removeTyping();
      addMessage('❌ Yükleme hatası, tekrar deneyin.', 'bot');
    } finally {
      sendBtn.disabled = false;
      input.value = '';
    }
  }

  // ── FİRMA SEÇ ────────────────────────────────────────────────────
  async function firmaSecildi(ticketId, firma, malzemeler, wrap) {
    if (wrap) Array.from(wrap.querySelectorAll('button')).forEach(b => b.disabled = true);
    addMessage('🏭 ' + firma, 'user');
    showTyping();
    try {
      const res = await fetch('/api/firma-sec', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticket_id: ticketId, firma, malzemeler, session_id: SESSION_ID })
      });
      const data = await res.json();
      removeTyping();
      addMessage(data.cevap, 'bot');
    } catch {
      removeTyping();
      addMessage('❌ Hata oluştu, tekrar deneyin.', 'bot');
    }
    inputEl.focus();
  }

  // ── BEKLEYENİRSALİYELER (Python, Claude yok) ─────────────────────
  async function bekleyenIrsaliyeleriGoster() {
    showTyping();
    try {
      const res = await fetch('/api/bekleyen-irsaliyeler');
      const data = await res.json();
      removeTyping();

      if (!data.irsaliyeler || data.irsaliyeler.length === 0) {
        addMessage('📋 Sistemde bekleyen irsaliye yok.', 'bot');
        return;
      }

      const wrap = document.createElement('div');
      const baslik = document.createElement('div');
      baslik.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:8px;';
      baslik.textContent = `${data.irsaliyeler.length} bekleyen irsaliye — fatura kesmek için seçin:`;
      wrap.appendChild(baslik);

      data.irsaliyeler.forEach((irs, idx) => {
        const btn = document.createElement('button');
        btn.className = 'irsaliye-btn';
        const sol = document.createElement('span');
        sol.textContent = `🏭 ${irs.cari_adi} · ${irs.tarih}`;
        const sag = document.createElement('span');
        sag.className = 'kg';
        sag.textContent = `${(irs.toplam_kg/1000).toFixed(2)} ton`;
        btn.appendChild(sol);
        btn.appendChild(sag);
        btn.onclick = () => irsaliyeSecildi(irs, wrap);
        wrap.appendChild(btn);
      });

      addMessage('📋 Bekleyen irsaliyeler:', 'bot', wrap);
    } catch {
      removeTyping();
      addMessage('❌ İrsaliyeler yüklenemedi, tekrar deneyin.', 'bot');
    }
  }

  // ── İRSALİYE SEÇ → FİYAT AKIŞI ──────────────────────────────────
  function irsaliyeSecildi(irs, wrap) {
    // Diğer butonları devre dışı bırak
    if (wrap) Array.from(wrap.querySelectorAll('button')).forEach(b => b.disabled = true);

    addMessage(`✅ Seçildi: ${irs.cari_adi} — ${(irs.toplam_kg/1000).toFixed(2)} ton`, 'user');

    // Malzeme bazlı mı tek fiyat mı?
    const kalemler = irs.kalemler || [];
    if (kalemler.length > 1) {
      // Malzeme bazlı fiyat akışı
      faturaAkisi = {
        irs,
        malzeme_listesi: kalemler.map(k => k.aciklama),
        malzemeler: Object.fromEntries(kalemler.map(k => [k.aciklama, k.miktar_kg])),
        toplanan_fiyatlar: {},
        index: 0
      };
      const ilk = kalemler[0];
      fiyatSor(`💰 ${ilk.aciklama} fiyatı nedir?\n(${ilk.miktar_kg.toLocaleString()} kg / ${(ilk.miktar_kg/1000).toFixed(2)} ton)\nÖrnek: 14.85 veya 14850`);
    } else {
      // Tek birim fiyat
      faturaAkisi = {
        irs,
        malzeme_listesi: [],
        malzemeler: {},
        toplanan_fiyatlar: {},
        index: 0
      };
      fiyatSor(`💰 Birim fiyatı nedir? (TL/kg)\n⚖️ ${(irs.toplam_kg/1000).toFixed(2)} ton\nÖrnek: 14.85 veya 14850`);
    }
  }

  function fiyatSor(mesaj) {
    // Fiyat input widget'ı göster
    const wrap = document.createElement('div');
    wrap.className = 'fiyat-input-wrap';
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'fiyat-input';
    inp.placeholder = 'Örn: 14.85 veya 14850';
    inp.autofocus = true;
    const btn = document.createElement('button');
    btn.className = 'fiyat-gonder-btn';
    btn.textContent = 'Gönder';
    const gonder = () => {
      const deger = inp.value.trim();
      if (!deger) return;
      inp.disabled = true;
      btn.disabled = true;
      fiyatGirildi(deger);
    };
    btn.onclick = gonder;
    inp.onkeydown = (e) => { if (e.key === 'Enter') gonder(); };
    wrap.appendChild(inp);
    wrap.appendChild(btn);
    addMessage(mesaj, 'bot', wrap);
    setTimeout(() => inp.focus(), 100);
  }

  async function fiyatGirildi(deger) {
    if (!faturaAkisi) return;
    addMessage(deger, 'user');

    const fiyat = parseFiyat(deger);
    if (!fiyat) {
      addMessage('❌ Geçersiz fiyat. Örnek: 14.85 veya 14850', 'bot');
      fiyatSor('💰 Tekrar deneyin:');
      return;
    }

    const { irs, malzeme_listesi, malzemeler, toplanan_fiyatlar, index } = faturaAkisi;

    if (malzeme_listesi.length > 0) {
      // Malzeme bazlı
      const mevcut = malzeme_listesi[index];
      toplanan_fiyatlar[mevcut] = fiyat;
      const sonraki = index + 1;

      if (sonraki < malzeme_listesi.length) {
        faturaAkisi.index = sonraki;
        const sonrakiMalzeme = malzeme_listesi[sonraki];
        const sonrakiKg = malzemeler[sonrakiMalzeme] || 0;
        fiyatSor(`✅ ${mevcut}: ${fiyat.toLocaleString()} TL/kg kaydedildi.\n\n💰 ${sonrakiMalzeme} fiyatı nedir?\n(${sonrakiKg.toLocaleString()} kg / ${(sonrakiKg/1000).toFixed(2)} ton)`);
      } else {
        // Tüm fiyatlar toplandı, özet göster ve kes
        await faturaOzetVeKes(irs, malzemeler, toplanan_fiyatlar);
      }
    } else {
      // Tek fiyat
      const tf = { 'hurda': fiyat };
      await faturaOzetVeKes(irs, {}, tf, irs.toplam_kg);
    }
  }

  async function faturaOzetVeKes(irs, malzemeler, toplanan_fiyatlar, agirlik_kg) {
    showTyping();
    try {
      const res = await fetch('/api/fatura-kes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parasut_irsaliye_id: irs.parasut_irsaliye_id,
          cari_adi: irs.cari_adi,
          agirlik_kg: agirlik_kg || irs.toplam_kg,
          malzemeler,
          toplanan_fiyatlar
        })
      });
      const data = await res.json();
      removeTyping();
      addMessage(data.cevap, 'bot');
      faturaAkisi = null;
    } catch {
      removeTyping();
      addMessage('❌ Fatura kesilemedi, tekrar deneyin.', 'bot');
      faturaAkisi = null;
    }
  }

  function parseFiyat(metin) {
    metin = metin.trim().toLowerCase()
      .replace('tl','').replace('lira','').replace('₺','').trim()
      .replace(',','.');
    const noktaSayisi = (metin.match(/\\./g) || []).length;
    if (noktaSayisi === 1) {
      const parca = metin.split('.');
      if (parca[1].length === 3) metin = metin.replace('.','');
    } else if (noktaSayisi > 1) {
      metin = metin.replace(/\\./g,'');
    }
    const f = parseFloat(metin);
    return (f > 0) ? f : null;
  }

  function quickSend(text) {
    inputEl.value = text;
    sendMessage();
  }

  window.onload = () => inputEl.focus();
</script>
</body>
</html>"""