# llm_handler.py — Profaix Intent Classifier
#
# MİMARİ DEĞİŞİKLİK:
# Eski: Haiku tool çağırarak Paraşüt'ten veri çekiyor, karar alıyordu (güvensiz)
# Yeni: Haiku SADECE kullanıcının niyetini tespit eder (intent classifier)
#       Gerçek veri çekme ve aksiyon tamamen frontend + FastAPI endpoint'lerde
#
# NEDEN DAHA GÜVENLİ?
# - Model hiç veri görmüyor → halüsinasyon riski yok
# - Paraşüt API'ye model üzerinden istek gitmiyor
# - 1500 cari / binlerce fatura verisi context'e girmiyor
# - Her yanıt deterministik — aynı input, aynı output

import json
import anthropic
from datetime import date
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

MODEL = "claude-haiku-4-5-20251001"
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── INTENT TANIMI ─────────────────────────────────────────────────────
# Her intent bir frontend fonksiyonuna karşılık gelir.
# Frontend bu string'i alır, doğru butonu/akışı tetikler.
# "none" → Genel soru, yönlendirme yok, Haiku cevap yazar
INTENTLER = {
    "bekleyen_irsaliyeler":  "Bekleyen (faturalanmamış) irsaliyeleri listeler",
    "toplu_fatura":          "Toplu fatura kesme akışını başlatır",
    "fatura_sorgula":        "Fatura listesi ve sorgulama ekranını açar",
    "tahsilat_ekle":         "Tahsilat ekleme formunu açar",
    "yeni_cari":             "Yeni cari oluşturma formunu açar",
    "musteri_bakiyeleri":    "Tüm müşteri bakiyelerini listeler",
    "hesap_ozeti":           "Genel hesap özetini gösterir",
    "gunluk_ozet":           "Günlük özeti gösterir",
    "son_fisler":            "Son yüklenen fişleri listeler",
    "none":                  "Genel soru veya sistem hakkında bilgi — yönlendirme yok"
}

INTENT_LISTESI = "\n".join([f"- {k}: {v}" for k, v in INTENTLER.items()])

SISTEM_PROMPTU = f"""Sen Profaix muhasebe sisteminin intent classifier'ısın.

GÖREVIN TEK VE NET:
Kullanıcının mesajını oku, hangi işlemi yapmak istediğini anla, sadece JSON döndür.

MEVCUT İNTENTLER:
{INTENT_LISTESI}

YANIT FORMATI (SADECE BU JSON, BAŞKA HİÇBİR ŞEY YAZMA):
{{"intent": "intent_adi", "mesaj": "Kullanıcıya gösterilecek kısa Türkçe mesaj"}}

KURALLAR:
- mesaj alanı maksimum 1 cümle, Türkçe, nazik
- Eğer istek açıkça bir intent'e karşılık geliyorsa o intent'i seç
- Eğer belirsizse veya genel bir soruysa intent = "none" yaz, mesaj alanında kısa cevap ver
- ASLA tool çağırma, ASLA veri getirme, ASLA hesaplama yapma
- Sadece niyeti tespit et

ÖRNEKLER:
"bekleyen irsaliyeler var mı" → {{"intent": "bekleyen_irsaliyeler", "mesaj": "Bekleyen irsaliyeleriniz getiriliyor..."}}
"kroman ne kadar borçlu" → {{"intent": "musteri_bakiyeleri", "mesaj": "Müşteri bakiyeleri açılıyor, oradan arayabilirsiniz."}}
"tahsilat girdim" → {{"intent": "tahsilat_ekle", "mesaj": "Tahsilat ekleme ekranı açılıyor."}}
"nasıl çalışıyorsun" → {{"intent": "none", "mesaj": "Sol menüden işlem seçebilir veya fiş fotoğrafı yükleyebilirsiniz."}}
"merhaba" → {{"intent": "none", "mesaj": "Merhaba! Size nasıl yardımcı olabilirim?"}}
"""


# ── SESSION YÖNETİMİ ──────────────────────────────────────────────────

def _session_yukle(db, session_id: str):
    from app.core.database import ChatSession
    return db.query(ChatSession).filter(ChatSession.session_id == session_id).first()


def _session_kaydet(db, session_id: str, user_mesaj: str, bot_mesaj: str):
    from app.core.database import ChatSession
    try:
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        bugun = str(date.today())

        tam_gecmis = []
        if session and session.tam_gecmis:
            try:
                tam_gecmis = json.loads(session.tam_gecmis)
            except Exception:
                tam_gecmis = []

        tam_gecmis.append({"role": "user",     "content": user_mesaj, "ts": bugun})
        tam_gecmis.append({"role": "assistant", "content": bot_mesaj,  "ts": bugun})

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
    except Exception as e:
        logger.error(f"Session kaydedilemedi: {e}")
        db.rollback()


# ── ANA FONKSİYON ─────────────────────────────────────────────────────

def soru_cevapla(mesaj: str, db, gonderen: str = "") -> dict:
    """
    Kullanıcı mesajını alır, intent + mesaj döndürür.

    Return:
        {
            "intent": "bekleyen_irsaliyeler",  # veya "none"
            "mesaj": "Bekleyen irsaliyeler getiriliyor..."
        }

    Frontend bu dict'i alır:
    - intent != "none" → ilgili fonksiyonu çağırır
    - intent == "none" → sadece mesajı chat baloncuğunda gösterir
    """
    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=200,
            system=SISTEM_PROMPTU,
            messages=[{"role": "user", "content": mesaj}]
        )

        ham_cevap = response.content[0].text.strip()
        logger.info(f"Intent ham cevap: {ham_cevap}")

        try:
            temiz = ham_cevap.strip()
            if temiz.startswith("```"):
                temiz = temiz.split("```")[1]
                if temiz.startswith("json"):
                    temiz = temiz[4:]
            sonuc = json.loads(temiz.strip())
        except Exception:
            logger.warning(f"Intent JSON parse hatası: {ham_cevap}")
            sonuc = {
                "intent": "none",
                "mesaj": "Sol menüden işlem seçebilir veya fiş fotoğrafı yükleyebilirsiniz."
            }

        # Geçersiz intent → none'a düşür
        if sonuc.get("intent") not in INTENTLER:
            sonuc["intent"] = "none"

        if not sonuc.get("mesaj"):
            sonuc["mesaj"] = "Sol menüden işlem seçebilirsiniz."

        logger.info(f"Intent: {sonuc['intent']} | Mesaj: {sonuc['mesaj'][:60]}")

        _session_kaydet(db, gonderen, mesaj, sonuc["mesaj"])

        return sonuc

    except Exception as e:
        logger.error(f"Intent classifier hatası: {e}")
        return {
            "intent": "none",
            "mesaj": "❌ Şu an cevap veremiyorum, lütfen tekrar deneyin."
        }