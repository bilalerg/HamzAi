# llm_handler.py — Profaix Intent Classifier
#
# MİMARİ:
# Haiku SADECE kullanıcının niyetini tespit eder.
# Veri çekmez, karar almaz, Paraşüt'e istek atmaz.
# Gerçek işlemler frontend + FastAPI endpoint'lerde.
#
# İYİLEŞTİRMELER:
# 1. Tool use ile JSON zorlama — parse hatası sıfır
# 2. BackgroundTasks uyumlu session kayıt
# 3. %80 güven eşiği — alakasız sorularda "none" döner
# 4. İrsaliye/fatura ayrımı prompt'ta netleştirildi

import json
import anthropic
from datetime import date
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

MODEL = "claude-haiku-4-5-20251001"
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── INTENT TANIMI ─────────────────────────────────────────────────────
INTENTLER = {
    "bekleyen_irsaliyeler":  "Bekleyen (faturalanmamış) irsaliyeleri listeler",
    "toplu_fatura":          "Toplu fatura kesme akışını başlatır",
    "fatura_sorgula":        "Kesilmiş fatura listesi ve sorgulama ekranını açar",
    "tahsilat_ekle":         "Tahsilat ekleme formunu açar",
    "yeni_cari":             "Yeni cari oluşturma formunu açar",
    "musteri_bakiyeleri":    "Tüm müşteri bakiyelerini listeler",
    "hesap_ozeti":           "Genel hesap özetini gösterir",
    "gunluk_ozet":           "Günlük özeti gösterir",
    "son_fisler":            "Son yüklenen fişleri listeler",
    "cari_detay":            "Belirli bir carinin iletişim, adres ve vergi bilgilerini gösterir",
    "none":                  "Genel soru veya sistem hakkında bilgi"
}

INTENT_ENUM = [k for k in INTENTLER.keys()]

# ── İYİLEŞTİRME 1: TOOL USE İLE JSON ZORLAMA ─────────────────────────
# Model bu tool'u "çağırmak" zorunda — böylece direkt şemayı dolduruyor.
# json.loads + fence temizleme + fallback döngüsüne gerek kalmıyor.
# Parse hatası pratikte sıfır.

INTENT_TOOL = [
    {
        "name": "intent_tespit",
        "description": "Kullanıcının mesajındaki niyeti ve varsa müşteri/tarih gibi parametreleri tespit eder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": INTENT_ENUM,
                    "description": "Tespit edilen intent"
                },
                "mesaj": {
                    "type": "string",
                    "description": "Kullanıcıya gösterilecek kısa Türkçe mesaj (max 1 cümle)"
                },
                "params": {
                    "type": "object",
                    "description": "Cümleden çıkarılan parametreler. Sadece cümlede geçenler doldurulur, tahmin yapma.",
                    "properties": {
                        "musteri": {
                            "type": "string",
                            "description": "Cümlede geçen müşteri/firma adı. Örn: Kroman, UHT, Samsun Makine"
                        },
                        "tarih_baslangic": {
                            "type": "string",
                            "description": "Tarih aralığı başlangıcı YYYY-MM-DD formatında. 'geçen hafta', 'bu ay' gibi ifadeleri hesapla."
                        },
                        "tarih_bitis": {
                            "type": "string",
                            "description": "Tarih aralığı bitişi YYYY-MM-DD formatında."
                        },
                        "tutar": {
                            "type": "number",
                            "description": "Cümlede geçen tutar. Örn: '5000 TL tahsilat' → 5000"
                        }
                    }
                }
            },
            "required": ["intent", "mesaj"]
        }
    }
]

INTENT_LISTESI = "\n".join([f"- {k}: {v}" for k, v in INTENTLER.items()])

# ── İYİLEŞTİRME 3 + 4: GÜVEN EŞİĞİ + İRSALİYE/FATURA AYRIMI ─────────
SISTEM_PROMPTU = f"""Sen Profaix'in muhasebe asistanısın — hurda metal sektöründe çalışan, işi bilen, samimi bir yardımcı.
Kullanıcılar genellikle muhasebeci veya patron. Onlara kısa, sıcak, doğal konuş. Resmi değil ama saçmalama.

GÖREVIN:
Kullanıcının mesajını oku, ne yapmak istediğini anla, intent_tespit fonksiyonunu çağır.

MEVCUT İNTENTLER:
{INTENT_LISTESI}

SEKTÖRE ÖZEL AYRИМ (ÇOK ÖNEMLİ):
- Kantar fişi / tartım / irsaliye / bekleyen / faturalanmamış → bekleyen_irsaliyeler
- Kesilmiş fatura / ödeme durumu / tahsilat geçmişi / fatura sorgula → fatura_sorgula
Bu ikisi hurda sektöründe çok karışır, dikkatli ayırt et.

GÜVENİRLİK KURALI:
Eğer mesaj listedeki intentlerden hiçbirine %80'den fazla uymuyorsa kesinlikle "none" döndür.
Spor, haber, genel sohbet gibi tamamen alakasız konular → her zaman "none".

MESAJ YAZARKEN:
- Doğal, sıcak, kısa konuş. Resmi değil.
- Intent varsa aksiyonu hissettir: "Bakıyorum", "Getiriyorum", "Açıyorum"
- Intent yoksa sohbet et, yardımcı ol
- Bazen emoji kullan, her cümlede değil
- Asla "Nasıl yardımcı olabilirim?" deme

"NE YAPABİLİRSİN / NELER YAPABİLİRSİN / DETAY" SORULARINDA:
Şu özellikleri doğal sohbet diliyle anlat:
Bekleyen irsaliyeleri gösterip fatura kesme, fatura sorgulama, müşteri bakiyeleri,
tahsilat ekleme, yeni cari açma, hesap özeti, işlem geçmişi, günlük özet.
Soldaki menüden de erişilebileceğini hatırlat.

ÖRNEKLER (bu tonu yakala):
"bekleyen irsaliyeler var mi" = intent: bekleyen_irsaliyeler, mesaj: "Hemen bakiyorum"
"irsaliye" = intent: bekleyen_irsaliyeler, mesaj: "Bekleyenleri getiriyorum."
"fatura kestik mi" = intent: fatura_sorgula, mesaj: "Fatura ekranini aciyorum."
"kroman ne kadar borclu" = intent: musteri_bakiyeleri, mesaj: "Bakiyeleri aciyorum."
"kroman iletişim bilgileri" = intent: cari_detay, mesaj: "Kroman'ın bilgilerini getiriyorum."
"samsun makinenin adresi" = intent: cari_detay, mesaj: "Cari bilgilerini açıyorum."
"tahsilat girdim" = intent: tahsilat_ekle, mesaj: "Tahsilat ekranini aciyorum"
"kroman para odedi" = intent: tahsilat_ekle, mesaj: "Tahsilat ekranini aciyorum"
"arene odeme yaptim" = intent: odeme_ekle, mesaj: "Odeme ekranini aciyorum"
"tedarikcie para ciktı" = intent: odeme_ekle, mesaj: "Odeme ekranini aciyorum"
"uht e fatura kes" = intent: satis_faturasi_kes, mesaj: "Satis faturasi ekranini aciyorum"
"merhaba" = intent: none, mesaj: "Merhaba! Ne yapiyoruz bugun?"
"neler yapabilirsin" = intent: none, mesaj: "Bekleyen irsaliyeleri goruyorum, fatura kesiyorum, bakiyelere bakiyorum, tahsilat giriyorum, hesap ozeti cikartiyorum. Kisacasi muhasebe islerinin cogunu hallederim. Ne yapalim?"
"ne yapabiliyorsun detay" = intent: none, mesaj: "Hurda metal muhasebesinin tam ortasindayim. Irsaliye, fatura, tahsilat, bakiye, gunluk ozet hepsi burada. Soldaki menuye bir bak, istedigine tikla ya da yaz bana."
"besiktas maci kac kac" = intent: none, mesaj: "Bunu bilemem ama muhasebe konusunda yardimci olabilirim"
"""


# ── ANA FONKSİYON ─────────────────────────────────────────────────────

def soru_cevapla(mesaj: str, session_id: str = "") -> dict:
    """
    Kullanıcı mesajını alır, intent + mesaj döndürür.
    DB işlemi yok — session kaydı BackgroundTasks ile ayrıca yapılır.

    Return:
        {"intent": "bekleyen_irsaliyeler", "mesaj": "Hemen bakıyorum 📋"}
    """
    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=200,
            temperature=0.0,  # Intent classification'da tutarlılık şart
            system=SISTEM_PROMPTU,
            tools=INTENT_TOOL,
            tool_choice={"type": "any"},  # Modeli her zaman tool çağırmaya zorla
            messages=[{"role": "user", "content": mesaj}]
        )

        # Tool use ile gelen cevabı parse et
        for block in response.content:
            if block.type == "tool_use" and block.name == "intent_tespit":
                intent      = block.input.get("intent", "none")
                mesaj_cevap = block.input.get("mesaj", "Sol menüden işlem seçebilirsiniz.")
                params      = block.input.get("params", {}) or {}

                # Geçersiz intent kontrolü (şema bunu zaten engelliyor ama ekstra güvenlik)
                if intent not in INTENTLER:
                    intent = "none"

                logger.info(f"Intent: {intent} | Params: {params} | Mesaj: {mesaj_cevap[:60]}")
                return {"intent": intent, "mesaj": mesaj_cevap, "params": params}

        # Tool çağrılmadıysa fallback (olmaması lazım ama güvenlik için)
        logger.warning("Tool çağrılmadı, fallback kullanılıyor")
        return {
            "intent": "none",
            "mesaj": "Sol menüden işlem seçebilir veya fiş fotoğrafı yükleyebilirsiniz."
        }

    except Exception as e:
        logger.error(f"Intent classifier hatası: {e}")
        return {
            "intent": "none",
            "mesaj": "❌ Şu an cevap veremiyorum, lütfen tekrar deneyin."
        }


# ── İYİLEŞTİRME 2: BACKGROUND TASK UYUMLU SESSION KAYIT ──────────────
# main.py'de background_tasks.add_task(session_kaydet_bg, ...) ile çağrılır.
# Kullanıcı cevabı anında alır, DB kaydı arka planda tamamlanır.

def session_kaydet_bg(session_id: str, user_mesaj: str, bot_mesaj: str):
    """BackgroundTask olarak çalışır — kullanıcıyı bekletmez."""
    from app.core.database import SessionLocal, ChatSession
    db = SessionLocal()
    try:
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        bugun = str(date.today())

        tam_gecmis = []
        if session and session.tam_gecmis:
            try:
                tam_gecmis = json.loads(session.tam_gecmis)
            except Exception:
                tam_gecmis = []

        tam_gecmis.append({"role": "user",      "content": user_mesaj, "ts": bugun})
        tam_gecmis.append({"role": "assistant",  "content": bot_mesaj,  "ts": bugun})

        if len(tam_gecmis) > 100:
            tam_gecmis = tam_gecmis[-100:]

        tam_gecmis_json = json.dumps(tam_gecmis, ensure_ascii=False)

        if session:
            session.tam_gecmis = tam_gecmis_json
        else:
            session = ChatSession(
                session_id=session_id,
                isim="Kullanıcı",
                tam_gecmis=tam_gecmis_json
            )
            db.add(session)

        db.commit()
        logger.info(f"Session kaydedildi (bg): {session_id}")
    except Exception as e:
        logger.error(f"Session kaydedilemedi (bg): {e}")
        db.rollback()
    finally:
        db.close()