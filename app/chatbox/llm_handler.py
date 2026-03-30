# llm_handler.py — Profaix WhatsApp & Web Chatbot
#
# SORUMLULUK:
# 1. Kullanıcıdan isim alır (ilk mesajda)
# 2. Konuşma geçmişini bellekte tutar — Claude önceki soruları hatırlar
# 3. DB'den güncel veri çeker ve Claude'a context olarak verir
# 4. Cevabı döndürür

import anthropic
from datetime import date
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

# Kullanıcı numarası → isim
kullanici_isimleri = {}

# Kullanıcı numarası → konuşma geçmişi (messages listesi)
# Her eleman: {"role": "user"/"assistant", "content": "..."}
konusma_gecmisleri = {}

# Bellekte tutulacak max mesaj sayısı (çift olmalı: user+assistant)
MAX_GECMIS = 20


def db_context_hazirla(db) -> str:
    from app.core.database import WeighTicket, Waybill, Invoice, TicketStatus
    from sqlalchemy import func

    bugun = date.today()

    try:
        bugunun_fisleri = db.query(WeighTicket).filter(
            func.date(WeighTicket.created_at) == bugun
        ).all()

        bugun_toplam_kg = sum(t.agirlik_kg or 0 for t in bugunun_fisleri)
        bugun_toplam_ton = bugun_toplam_kg / 1000
        bugun_arac_sayisi = len(bugunun_fisleri)

        plaka_dokumu = {}
        for t in bugunun_fisleri:
            if t.plaka:
                plaka_dokumu[t.plaka] = plaka_dokumu.get(t.plaka, 0) + (t.agirlik_kg or 0)

        plaka_listesi = "\n".join([
            f"  • {plaka}: {kg/1000:.2f} ton ({kg:,} kg)"
            for plaka, kg in plaka_dokumu.items()
        ]) if plaka_dokumu else "  Henüz araç gelmedi"

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
            f"  • Fatura No: {f.fatura_no} | Paraşüt ID: {f.parasut_id} | Tarih: {f.created_at.strftime('%d.%m.%Y') if f.created_at else '-'}"
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
            son_fatura_bilgi = f"Fatura No: {son_fatura.fatura_no} | Paraşüt ID: {son_fatura.parasut_id}"

        context = f"""
=== PROFAIX SİSTEM VERİLERİ ===
Tarih: {bugun.strftime('%d.%m.%Y')}

BUGÜNKÜ GENEL DURUM:
- Toplam araç sayısı: {bugun_arac_sayisi}
- Toplam ağırlık: {bugun_toplam_ton:.2f} ton ({bugun_toplam_kg:,} kg)
- Bugün kesilen fatura sayısı: {len(bugun_faturalar)}
- Toplam tamamlanan işlem: {tamamlananlar}

PLAKA BAZLI TON DÖKÜMÜ (BUGÜN):
{plaka_listesi}

FABRİKA ONAYI BEKLEYEN İRSALİYELER:
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
    Kullanıcının mesajını işler ve cevap döndürür.

    AKIŞ:
    1. İlk mesaj → isim sor
    2. İsim henüz verilmemiş → ismi kaydet, hoş geldin mesajı
    3. İsim var → konuşma geçmişine ekle → DB context + geçmiş → Claude → cevap
    """

    # ── ADIM 1: İlk kez yazıyor → isim sor ─────────────────────────────
    if gonderen not in kullanici_isimleri:
        kullanici_isimleri[gonderen] = None
        konusma_gecmisleri[gonderen] = []
        return "Merhaba! Ben Profaix asistanıyım 🤖\n\nSize nasıl hitap etmemi istersiniz?"

    # ── ADIM 2: İsim henüz kaydedilmemiş → kaydet ───────────────────────
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

    # ── ADIM 3: Normal akış ──────────────────────────────────────────────
    isim = kullanici_isimleri[gonderen]
    db_context = db_context_hazirla(db)

    # Sistem promptu — zengin kimlik ve bağlam
    sistem_promptu = f"""Sen Profaix'in akıllı muhasebe asistanısın. Hurda metal işletmesi için kantar takip ve muhasebe otomasyon sisteminde çalışıyorsun.

Kullanıcının adı: {isim}

KİMLİĞİN:
- Hurda sektörünü iyi bilirsin: kantar fişi, irsaliye, e-fatura, Paraşüt, fabrika onayı süreçleri sana yabancı değil
- Sayıları anlıklı yorumlarsın: "21 ton gelmiş, dünden %15 fazla" gibi bağlamsal yorumlar yapabilirsin
- Kullanıcıyla sıcak ama profesyonel konuşursun
- Önceki mesajları hatırlarsın ve konuşmayı sürdürürsün

DAVRANIŞ KURALLARI:
- {isim} diye hitap et
- WhatsApp/web formatında yaz: kısa paragraflar, gerektiğinde emoji
- Eğer kullanıcı "peki ya dün?" veya "en çok hangisi?" gibi bağlamsal soru sorarsa, önceki konuşmadan anlayarak cevapla
- Sistemde olmayan bilgiyi uydurma, "bu bilgi sistemde yok" de
- Sayısal soruları net ve hızlı cevapla, detay istenince detaylandır
- Fatura kesmek için birim fiyat bilgisi gerekir, bu bilgi sistemde yoksa belirt
- Eğer kullanıcı teşekkür ederse kısa ve samimi karşılık ver
- Eğer selam/merhaba yazarsa sıcak karşıla ve ne sormak istediğini sor

GÜNCEL SİSTEM VERİLERİ:
{db_context}"""

    # Konuşma geçmişine kullanıcı mesajını ekle
    gecmis = konusma_gecmisleri[gonderen]
    gecmis.append({"role": "user", "content": mesaj})

    # Geçmiş çok uzadıysa en eski mesajları at (ilk 2'yi koru: bağlamı kaybetme)
    # MAX_GECMIS aşıldıysa en eski user+assistant çiftini sil
    if len(gecmis) > MAX_GECMIS:
        gecmis = gecmis[-MAX_GECMIS:]
        konusma_gecmisleri[gonderen] = gecmis

    # ── Claude'a gönder ──────────────────────────────────────────────────
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            system=sistem_promptu,
            messages=gecmis  # Tüm konuşma geçmişi gönderiliyor
        )

        cevap = response.content[0].text.strip()

        # Asistanın cevabını da geçmişe ekle — bir sonraki turda Claude hatırlasın
        gecmis.append({"role": "assistant", "content": cevap})

        logger.info(f"🤖 LLM cevap ({isim}): {cevap[:60]}...")
        return cevap

    except Exception as e:
        logger.error(f"Claude API hatası: {e}")
        # Hata durumunda son eklenen user mesajını geçmişten çıkar
        if gecmis and gecmis[-1]["role"] == "user":
            gecmis.pop()
        return "❌ Şu an cevap veremiyorum, lütfen tekrar deneyin."