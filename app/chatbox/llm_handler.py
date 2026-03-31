# llm_handler.py — Profaix WhatsApp & Web Chatbot

import json
import re
import anthropic
from datetime import date
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

MAX_GECMIS = 20

SORU_BELIRTECLERI = [
    "?", "kaç", "var mı", "nedir", "nerede", "nasıl", "ne zaman",
    "hangi", "toplam", "bugün", "fatura", "plaka", "irsaliye", "ton"
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
    return db.query(ChatSession).filter(
        ChatSession.session_id == session_id
    ).first()


def _session_kaydet(db, session_id: str, isim: str, gecmis: list):
    from app.core.database import ChatSession
    try:
        session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
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


def db_context_hazirla(db) -> str:
    from app.core.database import WeighTicket, Waybill, Invoice, Alert, TicketStatus
    from sqlalchemy import func
    from datetime import date

    bugun = date.today()

    try:
        # Tüm fişler — tarih filtresi yok, fiş tarihi ile birlikte gösterilecek
        tum_fisler = db.query(WeighTicket).order_by(
            WeighTicket.fis_tarihi.desc(),
            WeighTicket.created_at.desc()
        ).limit(50).all()

        toplam_kg = sum(t.agirlik_kg or 0 for t in tum_fisler)
        toplam_ton = toplam_kg / 1000
        arac_sayisi = len(tum_fisler)

        # Fiş tarihi bazlı araç listesi
        fis_listesi = "\n".join([
            f"  • {t.fis_tarihi.strftime('%d.%m.%Y') if t.fis_tarihi else t.created_at.strftime('%d.%m.%Y')} — "
            f"{t.plaka}: net {(t.agirlik_kg or 0)/1000:.2f} ton"
            + (f" | fire: {t.fire_kg:,} kg" if t.fire_kg else "")
            + (f" | net tartım: {t.net_tartim_kg:,} kg" if t.net_tartim_kg else "")
            for t in tum_fisler
        ]) if tum_fisler else "  Henüz araç gelmedi"

        # Bugün sisteme giren fişler (created_at bugün)
        bugun_eklenen = [t for t in tum_fisler if t.created_at and t.created_at.date() == bugun]
        bugun_kg = sum(t.agirlik_kg or 0 for t in bugun_eklenen)

        bekleyen_waybills = db.query(Waybill).filter(
            Waybill.status == TicketStatus.FABRIKA_BEKLENIYOR
        ).all()

        bekleyen_listesi = "\n".join([
            f"  • Ref: {w.ref_kodu} | İrsaliye: {w.parasut_irsaliye_id}"
            for w in bekleyen_waybills
        ]) if bekleyen_waybills else "  Bekleyen irsaliye yok"

        tum_faturalar = db.query(Invoice).order_by(
            Invoice.created_at.desc()
        ).limit(10).all()

        fatura_listesi = "\n".join([
            f"  • Fatura No: {f.fatura_no} | Tutar: {f.tutar:,} TL"
            + (f" | Birim: {f.birim_fiyat:,} TL/kg" if f.birim_fiyat else "")
            + f" | Tarih: {f.created_at.strftime('%d.%m.%Y') if f.created_at else '-'}"
            for f in tum_faturalar
        ]) if tum_faturalar else "  Henüz fatura yok"

        bugun_faturalar = db.query(Invoice).filter(
            func.date(Invoice.created_at) == bugun
        ).all()

        tamamlananlar = db.query(WeighTicket).filter(
            WeighTicket.status == TicketStatus.TAMAMLANDI
        ).count()

        son_fatura = tum_faturalar[0] if tum_faturalar else None
        son_fatura_bilgi = "Henüz fatura yok"
        if son_fatura:
            son_fatura_bilgi = f"Fatura No: {son_fatura.fatura_no} | Tutar: {son_fatura.tutar:,} TL" + (f" | Birim: {son_fatura.birim_fiyat:,} TL/kg" if son_fatura.birim_fiyat else "")

        bugun_duplicateler = db.query(Alert).filter(
            Alert.tur == "DUPLICATE",
            func.date(Alert.created_at) == bugun
        ).all()

        duplicate_listesi = "\n".join([
            f"  • {a.mesaj}"
            for a in bugun_duplicateler
        ]) if bugun_duplicateler else "  Bugün mükerrer fiş yok"

        return f"""
=== PROFAIX SİSTEM VERİLERİ ===
Bugün: {bugun.strftime('%d.%m.%Y')}

GENEL DURUM:
- Toplam araç sayısı (sistemdeki): {arac_sayisi}
- Toplam ağırlık (sistemdeki): {toplam_ton:.2f} ton ({toplam_kg:,} kg)
- Bugün sisteme eklenen: {len(bugun_eklenen)} araç / {bugun_kg/1000:.2f} ton
- Bugün kesilen fatura: {len(bugun_faturalar)}
- Toplam tamamlanan işlem: {tamamlananlar}

ARAÇLAR (FİŞ TARİHİ BAZLI):
{fis_listesi}

FABRİKA ONAYI BEKLEYEN İRSALİYELER:
{bekleyen_listesi}

SON 10 FATURA:
{fatura_listesi}

SON FATURA:
- {son_fatura_bilgi}

BUGÜN TESPİT EDİLEN MÜKERRER FİŞLER:
{duplicate_listesi}
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

    sistem_promptu = f"""Sen Profaix'in akıllı muhasebe asistanısın. Hurda metal işletmesi için kantar takip ve muhasebe otomasyon sisteminde çalışıyorsun.

Kullanıcının adı: {isim}

KİMLİĞİN:
- Hurda sektörünü iyi bilirsin: kantar fişi, irsaliye, e-fatura, Paraşüt süreçleri sana yabancı değil
- Sayıları yorumlarsın ve bağlamsal bilgiler verebilirsin
- Kullanıcıyla sıcak ama profesyonel konuşursun
- Önceki mesajları hatırlarsın

DAVRANIŞ KURALLARI:
- {isim} diye hitap et ama her cümlede tekrarlama
- Kısa ve net cevap ver
- "Bugün kaç ton geldi?" sorusunda fişlerin üzerindeki giriş tarihine göre cevap ver
- Sistemde olmayan bilgiyi uydurma
- Emoji kullan ama abartma

GÜNCEL SİSTEM VERİLERİ:
{db_context}"""

    gecmis.append({"role": "user", "content": mesaj})

    if len(gecmis) > MAX_GECMIS:
        gecmis = gecmis[-MAX_GECMIS:]

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            system=sistem_promptu,
            messages=gecmis
        )

        cevap = response.content[0].text.strip()
        gecmis.append({"role": "assistant", "content": cevap})
        _session_kaydet(db, gonderen, isim=isim, gecmis=gecmis)

        logger.info(f"🤖 LLM cevap ({isim}): {cevap[:60]}...")
        return cevap

    except Exception as e:
        logger.error(f"Claude API hatası: {e}")
        if gecmis and gecmis[-1]["role"] == "user":
            gecmis.pop()
        return "❌ Şu an cevap veremiyorum, lütfen tekrar deneyin."