# llm_handler.py — Profaix WhatsApp Chatbot
#
# SORUMLULUK:
# 1. Kullanıcıdan isim alır (ilk mesajda)
# 2. DB'den güncel veri çeker (plaka bazlı ton, fatura listesi, bekleyenler)
# 3. Claude'a soru + DB context gönderir
# 4. Cevabı WhatsApp'a iletir

import anthropic
from datetime import date
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

# Kullanıcı numarası → isim eşleşmesi bellekte tutulur
kullanici_isimleri = {}


def db_context_hazirla(db) -> str:
    """
    DB'den güncel veriyi çekip Claude'a context olarak verir.
    Plaka bazlı ton dökümü, fatura listesi, bekleyenler dahil.
    """
    from app.core.database import WeighTicket, Waybill, Invoice, TicketStatus
    from sqlalchemy import func

    bugun = date.today()

    try:
        # ── Bugünkü fişler ────────────────────────────────────────────
        bugunun_fisleri = db.query(WeighTicket).filter(
            func.date(WeighTicket.created_at) == bugun
        ).all()

        bugun_toplam_kg = sum(t.agirlik_kg or 0 for t in bugunun_fisleri)
        bugun_toplam_ton = bugun_toplam_kg / 1000
        bugun_arac_sayisi = len(bugunun_fisleri)

        # Plaka bazlı ton dökümü
        plaka_dokumu = {}
        for t in bugunun_fisleri:
            if t.plaka:
                plaka_dokumu[t.plaka] = plaka_dokumu.get(t.plaka, 0) + (t.agirlik_kg or 0)

        plaka_listesi = "\n".join([
            f"  • {plaka}: {kg/1000:.2f} ton ({kg:,} kg)"
            for plaka, kg in plaka_dokumu.items()
        ]) if plaka_dokumu else "  Henüz araç gelmedi"

        # ── Bekleyen irsaliyeler ──────────────────────────────────────
        bekleyen_waybills = db.query(Waybill).filter(
            Waybill.status == TicketStatus.FABRIKA_BEKLENIYOR
        ).all()

        bekleyen_listesi = "\n".join([
            f"  • Ref: {w.ref_kodu} | İrsaliye: {w.parasut_irsaliye_id}"
            for w in bekleyen_waybills
        ]) if bekleyen_waybills else "  Bekleyen irsaliye yok"

        # ── Tüm faturalar ─────────────────────────────────────────────
        tum_faturalar = db.query(Invoice).order_by(
            Invoice.created_at.desc()
        ).limit(10).all()

        fatura_listesi = "\n".join([
            f"  • Fatura No: {f.fatura_no} | Paraşüt ID: {f.parasut_id} | Tarih: {f.created_at.strftime('%d.%m.%Y') if f.created_at else '-'}"
            for f in tum_faturalar
        ]) if tum_faturalar else "  Henüz fatura yok"

        # ── Bugünkü faturalar ─────────────────────────────────────────
        bugun_faturalar = db.query(Invoice).filter(
            func.date(Invoice.created_at) == bugun
        ).all()

        bugun_fatura_sayisi = len(bugun_faturalar)

        # ── Tamamlanan işlemler ───────────────────────────────────────
        tamamlananlar = db.query(WeighTicket).filter(
            WeighTicket.status == TicketStatus.TAMAMLANDI
        ).count()

        # ── Son fatura ────────────────────────────────────────────────
        son_fatura = tum_faturalar[0] if tum_faturalar else None
        son_fatura_bilgi = "Henüz fatura yok"
        if son_fatura:
            son_fatura_bilgi = f"Fatura No: {son_fatura.fatura_no} | Paraşüt ID: {son_fatura.parasut_id}"

        context = f"""
=== PROFAIX SİSTEM VERİLERİ ===
Tarih: {bugun.strftime('%d.%m.%Y')}

BUGÜNKÜ GENEL DURUM:
- Toplam araç sayısı: {bugun_arac_sayisi}
- Toplam ağırlık: {bugun_toplam_ton:.2f} ton ({bugun_toplam_kg:,} kg)
- Bugün kesilen fatura sayısı: {bugun_fatura_sayisi}
- Toplam tamamlanan işlem: {tamamlananlar}

PLAKA BAZLI TON DÖKÜMÜ (BUGÜN):
{plaka_listesi}

FABRIKA ONAYI BEKLEYEN İRSALİYELER:
{bekleyen_listesi}

SON 10 FATURA:
{fatura_listesi}

SON FATURA:
- {son_fatura_bilgi}
"""
        return context

    except Exception as e:
        logger.error(f"DB context hatası: {e}")
        return "DB verisi alınamadı."


def soru_cevapla(mesaj: str, db, gonderen: str = "") -> str:
    """
    Kullanıcının mesajını Claude'a gönderir, cevap döndürür.

    AKIŞ:
    1. Kullanıcı ilk kez yazıyorsa → isim sor
    2. İsim henüz verilmemişse → ismi kaydet
    3. İsim varsa → DB context + soru → Claude → cevap
    """

    # ── ADIM 1: İsim kontrolü ────────────────────────────────────────────
    if gonderen not in kullanici_isimleri:
        kullanici_isimleri[gonderen] = None
        return "Merhaba! Ben Profaix asistanıyım 🤖\n\nSize nasıl hitap etmemi istersiniz? (İsminizi yazabilirsiniz)"

    if kullanici_isimleri[gonderen] is None:
        isim = mesaj.strip()
        isim = isim.lower().replace("ben ", "").replace(" ben", "").replace("benim", "").strip().title()
        kullanici_isimleri[gonderen] = isim
        return (
            f"Merhaba {isim}! 👋\n\n"
            f"Profaix sistemine hoş geldiniz. Size nasıl yardımcı olabilirim?\n\n"
            f"Sorabilecekleriniz:\n"
            f"• Bugün kaç ton geldi?\n"
            f"• Hangi plakalar geldi?\n"
            f"• Plaka bazlı ton dökümü\n"
            f"• Bekleyen irsaliyeler var mı?\n"
            f"• Son fatura / tüm faturalar\n"
            f"• Bugün toplam kaç fatura kesildi?"
        )

    # ── ADIM 2: DB context hazırla ───────────────────────────────────────
    isim = kullanici_isimleri[gonderen]
    db_context = db_context_hazirla(db)

    # ── ADIM 3: Claude'a gönder ──────────────────────────────────────────
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    sistem_promptu = f"""Sen Profaix'in WhatsApp asistanısın. Hurda işletmesi için kantar takip sistemidir.
Kullanıcının adı: {isim}

Görevin:
- Kullanıcının sorularını Türkçe, kısa ve net cevapla
- Aşağıdaki sistem verisini kullanarak cevap ver
- Bilmediğin bir şeyi söyleme, "sistemde bu bilgi yok" de
- WhatsApp formatında yaz (kısa, emoji kullan)
- {isim} diye hitap et (Bey/Hanım ekleme, sadece isim)
- Liste istenirse madde madde göster
- Detay istenirse detaylı, özet istenirse kısa cevap ver

{db_context}

Önemli kurallar:
- Fatura kesmek için birim fiyat bilgisi lazım
- Sadece sistemdeki verilerle cevap ver
- Paraşüt ID ve Fatura No aynı şey — ikisini de gösterebilirsin
"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            system=sistem_promptu,
            messages=[
                {"role": "user", "content": mesaj}
            ]
        )

        cevap = response.content[0].text.strip()
        logger.info(f"🤖 LLM cevap: {cevap[:50]}...")
        return cevap

    except Exception as e:
        logger.error(f"Claude API hatası: {e}")
        return "❌ Şu an cevap veremiyorum, lütfen tekrar deneyin."