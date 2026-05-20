# app/routers/ocr.py — Fiş Yükleme, İptal, Firma Seçimi

import os
import tempfile
import json as _json
from fastapi import APIRouter, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.database import SessionLocal
from app.core.logger import logger
from app.auth import mevcut_kullanici
from app.routers.deps import (
    canonical_chat_session_id, _islem_log, _usage_kaydet,
    _billable_kaydet, _tenant_id_from_user, _guvenli_hata
)

router = APIRouter()


# ── FİŞ YÜKLE ────────────────────────────────────────────────────────

@router.post("/api/upload")
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
        # Kantar fişi = gelen mal = tedarikçi → sadece supplier + both göster
        # Not: cache'lenmiş tam liste üzerinden filtreliyoruz; tüm carilere
        # ait account_type bilgisi zaten cache'de var, ek API çağrısı yapılmaz.
        from app.parasut.client import async_cari_listesi_getir
        cariler       = await async_cari_listesi_getir(tenant_id=tenant_id)
        firma_listesi = sorted({
            c["ad"] for c in cariler
            if c.get("account_type", "customer") in ("supplier", "both", "customer_supplier")
        })

        return JSONResponse({
            "cevap": cevap, "bekliyor": "firma_sec", "ticket_id": ticket.id,
            "malzemeler": malzemeler, "firma_listesi": firma_listesi,
            "ocr_plaka": fis.plaka or "",
            "ocr_fis_no": fis.fis_no or "",
            "ocr_agirlik_kg": fis.net_agirlik_kg or 0,
            "ocr_fire_kg": fis.fire_kg or 0,
            "ocr_tarih": fis.tarih or "",
            "ocr_firma": fis.firma or "",
            "plaka_uyari": fis.plaka_uyari,
            "firma_uyari": fis.firma_uyari,
            "firma_kaynak": fis.firma_kaynak,
            # OCR güven skorları — chat.html renklendirme ve Kaydet kilidi için
            "conf_plaka":   fis.plaka_confidence,
            "conf_agirlik": fis.agirlik_confidence,
            "conf_fis_no":  fis.fis_no_confidence,
            "conf_firma":   fis.firma_confidence,
            "conf_genel":   fis.genel_confidence,
        })
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

@router.post("/api/fis-iptal")
async def fis_iptal(request: Request, background_tasks: BackgroundTasks):
    """
    OCR sonrası kullanıcı '✕ İptal' bastığında çağrılır.
    İlgili WeighTicket'i IPTAL statusuna alır — duplicate kontrolünde
    artık dikkate alınmaz, kullanıcı aynı fişi yeniden yükleyebilir.
    Body: {ticket_id}
    """
    aktif_user = mevcut_kullanici(request)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"basarili": False, "hata": "Geçersiz JSON"}, status_code=400)
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        return JSONResponse({"basarili": False, "hata": "ticket_id zorunlu"}, status_code=400)
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus
        ticket = db.query(WeighTicket).filter(WeighTicket.id == int(ticket_id)).first()
        if not ticket:
            return JSONResponse({"basarili": False, "hata": "Fiş bulunamadı"}, status_code=404)
        # Sadece henüz Paraşüt'e gitmemiş kayıtlar iptal edilebilir
        if ticket.status not in (
            TicketStatus.ALINDI, TicketStatus.OCR_TAMAMLANDI,
            TicketStatus.OCR_DOGRULAMA_BEKLIYOR, TicketStatus.PLAKA_KONTROL_BEKLENIYOR,
            TicketStatus.HATA,
        ):
            return JSONResponse({
                "basarili": False,
                "hata": "Bu fiş zaten Paraşüt'e gönderilmiş, iptal edilemez."
            }, status_code=409)
        ticket.status = TicketStatus.IPTAL
        db.commit()
        _islem_log(db, "FIS_IPTAL", plaka=ticket.plaka,
                   aciklama=f"Fiş No: {ticket.fis_no or '-'} kullanıcı tarafından iptal edildi",
                   request=request, user=aktif_user)
        logger.info(f"Fiş iptal edildi: ticket_id={ticket_id} plaka={ticket.plaka}")
        return JSONResponse({"basarili": True})
    except Exception as e:
        logger.error(f"Fiş iptal hatası: {e}")
        return JSONResponse({"basarili": False, "hata": _guvenli_hata(e)}, status_code=500)
    finally:
        db.close()


@router.post("/api/firma-sec")
async def firma_sec(request: Request, background_tasks: BackgroundTasks):
    data          = await request.json()
    ticket_id     = data.get("ticket_id")
    secilen_firma = data.get("firma")
    malzemeler    = data.get("malzemeler", {})
    duz_plaka     = data.get("plaka")
    duz_fis_no    = data.get("fis_no")
    duz_agirlik   = data.get("agirlik_kg")
    duz_fire      = data.get("fire_kg")
    duz_tarih     = data.get("tarih")
    db            = SessionLocal()
    aktif_user = mevcut_kullanici(request)
    tenant_id  = _tenant_id_from_user(aktif_user)
    try:
        from app.core.database import WeighTicket, Waybill
        ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()
        if not ticket:
            return JSONResponse({"cevap": "❌ Fiş bulunamadı."})
        ticket.firma = secilen_firma
        # Kullanıcı düzeltmelerini uygula
        if duz_plaka is not None:
            temiz_p = str(duz_plaka).replace(' ', '').replace('-', '').upper()
            if temiz_p:
                ticket.plaka = temiz_p
        if duz_fis_no is not None:
            fno = str(duz_fis_no).strip()
            if fno:
                ticket.fis_no = fno
        if duz_agirlik is not None:
            try:
                ag   = int(duz_agirlik)
                fire = int(duz_fire or 0)
                if ag > 0:
                    ticket.agirlik_kg    = ag
                    ticket.fire_kg       = fire
                    ticket.net_tartim_kg = ag + fire
            except (ValueError, TypeError):
                pass
        if duz_tarih:
            from datetime import datetime as _dt
            for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                try:
                    ticket.fis_tarihi = _dt.strptime(str(duz_tarih), fmt)
                    break
                except ValueError:
                    continue
        if malzemeler:
            ticket.malzemeler = _json.dumps(malzemeler, ensure_ascii=False)
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
