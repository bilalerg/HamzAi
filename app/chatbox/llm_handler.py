# llm_handler.py — Profaix WhatsApp & Web Chatbot

import json
import re
import anthropic
from datetime import date
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

MAX_GECMIS = 10

SORU_BELIRTECLERI = [
    "?", "kaç", "var mı", "nedir", "nerede", "nasıl", "ne zaman",
    "hangi", "toplam", "bugün", "fatura", "plaka", "irsaliye", "ton"
]

# ── TOOL TANIMLARI ────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "cari_listesi_getir",
        "description": "Paraşüt'teki tüm cari hesapları (müşteri/tedarikçi) listeler. Bakiye, iletişim bilgileri dahil.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "cari_detay_getir",
        "description": "Belirli bir carinin detaylarını getirir: adres, telefon, email, vergi no, bakiye.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cari_adi": {"type": "string", "description": "Cari adı (örn: Samsun Makine, Kroman)"}
            },
            "required": ["cari_adi"]
        }
    },
    {
        "name": "cari_faturalari_getir",
        "description": "Belirli bir carinin faturalarını listeler.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cari_adi": {"type": "string", "description": "Cari adı"},
                "limit": {"type": "integer", "description": "Kaç fatura getirileceği (varsayılan 10)", "default": 10}
            },
            "required": ["cari_adi"]
        }
    },
    {
        "name": "cari_irsaliyeleri_getir",
        "description": "Belirli bir carinin irsaliyelerini listeler.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cari_adi": {"type": "string", "description": "Cari adı"},
                "limit": {"type": "integer", "description": "Kaç irsaliye getirileceği (varsayılan 10)", "default": 10}
            },
            "required": ["cari_adi"]
        }
    },
    {
        "name": "acik_faturalar_getir",
        "description": "Tüm ödenmemiş/gecikmiş faturaları listeler.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "hesap_ozeti_getir",
        "description": "Genel hesap özetini getirir: toplam alacak, borç, cari sayısı.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
]


def _isim_mi(metin: str) -> bool:
    metin_lower = metin.lower().strip()
    for belirtec in SORU_BELIRTECLERI:
        if belirtec in metin_lower:
            return False
    if len(metin.split()) > 3:
        return False
    if re.search(r'\d', metin):
        return False
    return True


def _session_yukle(db, session_id: str):
    from app.core.database import ChatSession
    return db.query(ChatSession).filter(ChatSession.session_id == session_id).first()


def _session_kaydet(db, session_id: str, isim: str, gecmis: list):
    from app.core.database import ChatSession
    try:
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        gecmis_json = json.dumps(gecmis, ensure_ascii=False)
        if session:
            session.isim = isim
            session.gecmis = gecmis_json
        else:
            session = ChatSession(session_id=session_id, isim=isim, gecmis=gecmis_json)
            db.add(session)
        db.commit()
    except Exception as e:
        logger.error(f"Session kaydedilemedi: {e}")
        db.rollback()


def _cari_id_bul(cari_adi: str) -> str | None:
    """Cari adından Paraşüt ID'si bulur."""
    from app.core.config import PARASUT_CONTACT_IDS
    cari_adi_upper = cari_adi.upper().strip()
    for anahtar, cid in PARASUT_CONTACT_IDS.items():
        if anahtar.upper() in cari_adi_upper or cari_adi_upper in anahtar.upper():
            return str(cid)
    return None


def _tool_calistir(tool_name: str, tool_input: dict) -> str:
    """Tool çağrısını çalıştırır ve sonucu string olarak döndürür."""
    try:
        from app.parasut.client import (
            cari_listesi_getir, cari_detay_getir, cari_faturalari_getir,
            cari_irsaliyeleri_getir, acik_faturalar_getir, hesap_ozeti_getir
        )

        if tool_name == "cari_listesi_getir":
            sonuc = cari_listesi_getir()
            return json.dumps(sonuc, ensure_ascii=False)

        elif tool_name == "cari_detay_getir":
            cari_adi = tool_input.get("cari_adi", "")
            cari_id = _cari_id_bul(cari_adi)
            if not cari_id:
                return f"'{cari_adi}' adlı cari bulunamadı."
            sonuc = cari_detay_getir(cari_id)
            return json.dumps(sonuc, ensure_ascii=False)

        elif tool_name == "cari_faturalari_getir":
            cari_adi = tool_input.get("cari_adi", "")
            limit = tool_input.get("limit", 10)
            cari_id = _cari_id_bul(cari_adi)
            if not cari_id:
                return f"'{cari_adi}' adlı cari bulunamadı."
            sonuc = cari_faturalari_getir(cari_id, limit)
            return json.dumps(sonuc, ensure_ascii=False)

        elif tool_name == "cari_irsaliyeleri_getir":
            cari_adi = tool_input.get("cari_adi", "")
            limit = tool_input.get("limit", 10)
            cari_id = _cari_id_bul(cari_adi)
            if not cari_id:
                return f"'{cari_adi}' adlı cari bulunamadı."
            sonuc = cari_irsaliyeleri_getir(cari_id, limit)
            return json.dumps(sonuc, ensure_ascii=False)

        elif tool_name == "acik_faturalar_getir":
            sonuc = acik_faturalar_getir()
            return json.dumps(sonuc, ensure_ascii=False)

        elif tool_name == "hesap_ozeti_getir":
            sonuc = hesap_ozeti_getir()
            return json.dumps(sonuc, ensure_ascii=False)

        else:
            return f"Bilinmeyen tool: {tool_name}"

    except Exception as e:
        logger.error(f"Tool çalıştırma hatası ({tool_name}): {e}")
        return f"Hata: {str(e)}"


def db_context_hazirla(db) -> str:
    from app.core.database import WeighTicket, Waybill, Invoice, Alert, TicketStatus
    from sqlalchemy import func

    bugun = date.today()

    try:
        tum_fisler = db.query(WeighTicket).order_by(
            WeighTicket.fis_tarihi.desc(),
            WeighTicket.created_at.desc()
        ).limit(20).all()

        toplam_kg = sum(t.agirlik_kg or 0 for t in tum_fisler)
        arac_sayisi = len(tum_fisler)

        fis_listesi = "\n".join([
            f"  • {t.fis_tarihi.strftime('%d.%m.%Y') if t.fis_tarihi else t.created_at.strftime('%d.%m.%Y')} — "
            f"{t.plaka} (Fiş No: {t.fis_no or '-'}): net {(t.agirlik_kg or 0)/1000:.2f} ton"
            + (f" | fire: {t.fire_kg:,} kg" if t.fire_kg else "")
            + (f" | firma: {t.firma}" if t.firma else "")
            for t in tum_fisler
        ]) if tum_fisler else "  Henüz araç gelmedi"

        bugun_eklenen = [t for t in tum_fisler if t.created_at and t.created_at.date() == bugun]
        bugun_kg = sum(t.agirlik_kg or 0 for t in bugun_eklenen)

        bekleyen_waybills = db.query(Waybill).filter(
            Waybill.status == TicketStatus.FABRIKA_BEKLENIYOR
        ).all()

        bekleyen_listesi = "\n".join([
            f"  • Plaka: {w.ticket.plaka if w.ticket else '-'} | "
            f"Ağırlık: {(w.ticket.agirlik_kg or 0)/1000:.2f} ton | "
            f"Firma: {w.ticket.firma if w.ticket else '-'} | "
            f"waybill_id: {w.id}"
            for w in bekleyen_waybills
        ]) if bekleyen_waybills else "  Bekleyen irsaliye yok"

        tum_faturalar = db.query(Invoice).order_by(Invoice.created_at.desc()).limit(10).all()

        fatura_listesi = "\n".join([
            f"  • Fatura No: {f.fatura_no} | Tutar: {f.tutar:,} TL"
            + (f" | Birim: {f.birim_fiyat:,} TL/kg" if f.birim_fiyat else "")
            + f" | Tarih: {f.created_at.strftime('%d.%m.%Y') if f.created_at else '-'}"
            for f in tum_faturalar
        ]) if tum_faturalar else "  Henüz fatura yok"

        bugun_faturalar = db.query(Invoice).filter(func.date(Invoice.created_at) == bugun).all()
        tamamlananlar = db.query(WeighTicket).filter(WeighTicket.status == TicketStatus.TAMAMLANDI).count()

        return f"""
=== PROFAIX SİSTEM VERİLERİ ===
Bugün: {bugun.strftime('%d.%m.%Y')}

GENEL DURUM:
- Toplam araç: {arac_sayisi} | Toplam: {toplam_kg/1000:.2f} ton
- Bugün eklenen: {len(bugun_eklenen)} araç / {bugun_kg/1000:.2f} ton
- Bugün fatura: {len(bugun_faturalar)} | Tamamlanan: {tamamlananlar}

ARAÇLAR:
{fis_listesi}

BEKLEYEN İRSALİYELER:
{bekleyen_listesi}

SON 10 FATURA:
{fatura_listesi}
"""
    except Exception as e:
        logger.error(f"DB context hatası: {e}")
        return "DB verisi alınamadı."


def soru_cevapla(mesaj: str, db, gonderen: str = "") -> str:
    session = _session_yukle(db, gonderen)

    if session is None:
        _session_kaydet(db, gonderen, isim=None, gecmis=[])
        return "Merhaba! Ben Profaix asistanıyım 🤖\n\nSize nasıl hitap etmemi istersiniz?"

    if session.isim is None:
        if _isim_mi(mesaj):
            isim = mesaj.strip()
            isim = isim.lower().replace("ben ", "").replace(" ben", "").replace("benim", "").strip().title()
            _session_kaydet(db, gonderen, isim=isim, gecmis=[])
            return f"Merhaba {isim}! 👋\n\nProfaix sistemine hoş geldiniz. Size nasıl yardımcı olabilirim?"
        else:
            _session_kaydet(db, gonderen, isim="Kullanıcı", gecmis=[])
            session = _session_yukle(db, gonderen)

    isim = session.isim or "Kullanıcı"
    gecmis = json.loads(session.gecmis) if session.gecmis else []
    db_context = db_context_hazirla(db)

    sistem_promptu = f"""Sen Profaix'in muhasebe asistanısın. Hurda metal kantar takip ve muhasebe otomasyon sistemi.

Kullanıcı: {isim}

SİSTEMİN YAPABİLDİKLERİ:
- 📎 Sol alttaki butonla kantar fişi fotoğrafı yüklenebilir
- Sistem fişi OCR ile okur: plaka, ağırlık, fire, malzeme grupları, firma
- Paraşüt'te otomatik irsaliye oluşturulur (taslak olarak bekletilir)
- Kullanıcı hangi fabrikaya ait olduğunu seçer (Samsun Makine, Kroman, İçtaş, Kaptan, UHT)
- İrsaliye onaylandıktan sonra malzeme bazlı fiyat girilir
- Her malzeme ayrı kalem olarak Paraşüt'e fatura kesilir
- Birden fazla fiş bekletilip sırayla onaylanabilir
- Paraşüt'teki cari bilgileri, faturalar, irsaliyeler sorgulanabilir

KURALLAR:
- Kısa ve net cevap ver
- Paraşüt verisi gerekiyorsa tool kullan
- Sistemde olmayan bilgiyi uydurma
- Emoji kullan ama abartma

PROFAIX VERİTABANI:
{db_context}"""

    gecmis.append({"role": "user", "content": mesaj})
    if len(gecmis) > MAX_GECMIS:
        gecmis = gecmis[-MAX_GECMIS:]

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        # İlk API çağrısı — tool use etkin
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            system=sistem_promptu,
            tools=TOOLS,
            messages=gecmis
        )

        # Tool çağrısı var mı?
        while response.stop_reason == "tool_use":
            # Tüm tool çağrılarını işle
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"🔧 Tool çağrısı: {block.name} — {block.input}")
                    sonuc = _tool_calistir(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": sonuc
                    })

            # Assistant mesajını geçmişe ekle
            gecmis.append({"role": "assistant", "content": response.content})
            # Tool sonuçlarını geçmişe ekle
            gecmis.append({"role": "user", "content": tool_results})

            # Tekrar Claude'a sor
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1000,
                system=sistem_promptu,
                tools=TOOLS,
                messages=gecmis
            )

        # Nihai cevabı al
        cevap = ""
        for block in response.content:
            if hasattr(block, "text"):
                cevap += block.text

        cevap = cevap.strip()

        # Geçmişe sadece son user/assistant çiftini kaydet
        gecmis_kayit = [m for m in gecmis if isinstance(m.get("content"), str)]
        gecmis_kayit.append({"role": "assistant", "content": cevap})
        if len(gecmis_kayit) > MAX_GECMIS:
            gecmis_kayit = gecmis_kayit[-MAX_GECMIS:]

        _session_kaydet(db, gonderen, isim=isim, gecmis=gecmis_kayit)
        logger.info(f"🤖 LLM cevap ({isim}): {cevap[:60]}...")
        return cevap

    except Exception as e:
        logger.error(f"Claude API hatası: {e}")
        return "❌ Şu an cevap veremiyorum, lütfen tekrar deneyin."