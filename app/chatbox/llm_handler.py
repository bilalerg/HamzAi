# llm_handler.py — Profaix Intent Classifier

import json
import anthropic
from datetime import date
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

MODEL = "claude-haiku-4-5-20251001"
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _ui_key(ui: dict) -> str:
    try:
        return json.dumps(
            {"kind": str(ui.get("kind", "")), "params": ui.get("params") or {}},
            ensure_ascii=False,
            sort_keys=True,
        )
    except Exception:
        return str(ui)

# ── INTENT TANIMI ─────────────────────────────────────────────────────
INTENTLER = {
    "bekleyen_irsaliyeler":  "Bekleyen (faturalanmamış) irsaliyeleri listeler",
    "toplu_fatura":          "Toplu fatura kesme akışını başlatır",
    "fatura_sorgula":        "Kesilmiş fatura listesi ve sorgulama ekranını açar",
    "satis_faturasi_kes":    "Belirli bir cariye yeni satış faturası keser",
    "alis_faturasi_kes":     "Tedarikçiye/hurda satıcısına alış faturası kesme formunu açar",
    "fatura_iptal":          "Yanlış kesilmiş satış faturasını iptal etme akışını başlatır",
    "e_fatura_kes":          "Kesilmiş satış faturasını e-fatura veya e-arşiv olarak resmileştirir",
    "tahsilat_ekle":         "Tahsilat ekleme formunu açar",
    "odeme_ekle":            "Tedarikçiye ödeme kaydetme formunu açar",
    "yeni_cari":             "Yeni cari oluşturma formunu açar",
    "cari_detay":            "Belirli bir carinin iletişim, adres ve vergi bilgilerini gösterir",
    "musteri_bakiyeleri":    "Tüm müşteri bakiyelerini listeler",
    "hesap_ozeti":           "Genel hesap özetini gösterir",
    "gunluk_ozet":           "Günlük özeti gösterir",
    "son_fisler":            "Son yüklenen fişleri listeler",
    "none":                  "Genel soru veya sistem hakkında bilgi",
}

INTENT_ENUM = list(INTENTLER.keys())

# /api/chat sonrası DB'ye yazılacak ve chat.html INTENT_MAP ile geri yüklenebilen paneller
SESSION_UI_RESTORE_INTENTS = frozenset({
    "bekleyen_irsaliyeler",
    "toplu_fatura",
    "fatura_sorgula",
    "fatura_detay",
    "tahsilat_ekle",
    "yeni_cari",
    "musteri_bakiyeleri",
    "hesap_ozeti",
    "gunluk_ozet",
    "son_fisler",
    "alis_faturasi_kes",
    "cari_detay",
})

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
                    "description": "Cümlede geçen parametreler. Sadece açıkça belirtilenleri doldur, tahmin yapma.",
                    "properties": {
                        "musteri": {
                            "type": "string",
                            "description": "Müşteri/firma adı. Örn: Kroman, UHT, Samsun Makine"
                        },
                        "tarih_baslangic": {
                            "type": "string",
                            "description": "Tarih aralığı başlangıcı YYYY-MM-DD. 'geçen hafta', 'bu ay' gibi ifadeleri hesapla."
                        },
                        "tarih_bitis": {
                            "type": "string",
                            "description": "Tarih aralığı bitişi YYYY-MM-DD."
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

SISTEM_PROMPTU = f"""Sen Profaix'in muhasebe asistanısın — hurda metal sektöründe çalışan, işi bilen, samimi bir yardımcı.

KİMLİĞİN VE BAĞLANTIN (ÖNEMLİ — SORULURSA HER ZAMAN BUNU SÖYLE):
- Sen Profaix sistemi içinde çalışan AI asistanısın
- **Paraşüt muhasebe yazılımına API üzerinden bağlısın** — fatura, irsaliye, tahsilat, ödeme,
  cari hesap, kasa/banka gibi tüm muhasebe verilerini Paraşüt'ten okuyorsun ve oraya yazıyorsun
- OCR motorun var: kantar fişi fotoğrafı yüklenince otomatik okur (plaka, ağırlık, firma, tarih)
- Asla "Paraşüt'e bağlı değilim" deme — bu YALAN olur. Hep doğruyu söyle.

KULLANICILAR GENELLİKLE muhasebeci veya patron. Onlara kısa, sıcak, doğal konuş. Resmi değil ama saçmalama.

GÖREVIN:
Kullanıcının mesajını oku, ne yapmak istediğini anla, intent_tespit fonksiyonunu çağır.

MEVCUT İNTENTLER:
{INTENT_LISTESI}

SEKTÖRE ÖZEL AYIRIM (ÇOK ÖNEMLİ):
- Kantar fişi / tartım / irsaliye / bekleyen / faturalanmamış → bekleyen_irsaliyeler
- Kesilmiş fatura / ödeme durumu / tahsilat geçmişi / fatura sorgula → fatura_sorgula
- Müşteriden para geldi / tahsilat / ödeme aldık → tahsilat_ekle
- Tedarikçiye para çıktı / ödeme yaptık / biz ödedik → odeme_ekle
- Fatura kes (belirli bir cariye yeni SATIŞ faturası) → satis_faturasi_kes
- Hurda aldık / tedarikçiden alış / alış faturası kes → alis_faturasi_kes
- Yanlış fatura / faturayı iptal / fatura sil / hatalı kestik → fatura_iptal
- E-fatura yap / e-arşiv kes / resmileştir / vergi dairesine gönder → e_fatura_kes
- Cari iletişim / adres / vergi bilgisi / telefon → cari_detay

ÖNEMLİ AYIRIM:
- "satış faturası" / "müşteriye fatura" / "biz fatura kestik" → satis_faturasi_kes
- "alış faturası" / "tedarikçiden aldık" / "hurda aldık fatura" → alis_faturasi_kes

GÜVENİRLİK KURALI:
Mesaj intentlerden hiçbirine uymuyorsa "none" döndür.
Spor, haber, alakasız konular → her zaman "none".

MESAJ YAZARKEN (KESİN KURALLAR):
- Doğal, sıcak konuş. Resmi DEĞİL.
- Intent varsa aksiyonu hissettir: "Bakıyorum", "Getiriyorum", "Açıyorum"
- Intent yoksa BİLGİLENDİRİCİ cevap ver — geri soru sorma, doğrudan anlat.
- "Nasıl yardımcı olabilirim?", "Ne yapalım?", "Ne anlatayım?" gibi BOŞ DÖNÜŞ sorularını ASLA SORMA.
  Bunlar konuşmayı sıkıyor. Hep BİLGİ ver.
- Belirsiz mesajlarda ("anlat", "hepsini", "neler", "tamam") → kullanıcı bilgi istiyor demektir,
  YETENEKLERİNİ DETAYLI ANLAT. Geri sorma.
- Aynı cevabı tekrar etme. Konuyu ilerletmeyen yanıt verme.

"NEREYE BAĞLISIN" / "NEDİR BU SİSTEM" SORULARINDA:
"Paraşüt muhasebe yazılımına API üzerinden bağlıyım. Senin tüm faturalarını, irsaliyelerini,
cari hesaplarını, kasa/banka hareketlerini Paraşüt'ten okuyorum. Sen bir şey yapmamı istediğinde
de Paraşüt'e yazıyorum. Ayrıca kantar fişi fotoğraflarını OCR ile okuyabiliyorum."

"NE YAPABİLİRSİN" / "NELERE ERİŞEBİLİYORSUN" / "ANLAT" / "HEPSİNİ" SORULARINDA:
Şu detayda anlat:
"📋 Bekleyen irsaliyeleri listeleyebilirim — Paraşüt'te faturası kesilmemiş tüm kantar fişlerin.
🧾 Tek fatura veya toplu fatura kesebilirim (satış veya alış).
🔍 Geçmiş faturalarını sorgulayabilirim — müşteri veya tedarikçi bazında, tarih filtreli.
💵 Tahsilat ekleyebilirim — açık fatura varsa onlara, yoksa direkt cari hesabına (avans).
💸 Ödeme ekleyebilirim — alış faturalarına veya direkt tedarikçi cari hesabına.
❌ Yanlış kesilmiş faturayı iptal edip e-fatura/e-arşiv olarak resmileştirebilirim.
👥 Yeni cari oluşturabilirim, mevcut cari bilgilerini görüp düzenleyebilirim.
💰 Müşteri bakiyelerini, hesap özetini, günlük özet, gelir/gider raporunu çıkartabilirim.
🏦 Kasa/banka hesaplarını, stok hareketlerini gösterebilirim.
🔄 Hesaplar arasında para transferi yapabilirim.
📷 Kantar fişi fotoğrafı yüklersen OCR ile okuyup irsaliye oluşturabilirim.
Soldaki sol menüden de bunlara doğrudan erişebilirsin."

ÖRNEKLER (bu tonu yakala):
"bekleyen irsaliyeler var mi" → intent: bekleyen_irsaliyeler, mesaj: "Hemen bakıyorum"
"irsaliye" → intent: bekleyen_irsaliyeler, mesaj: "Bekleyenleri getiriyorum."
"fatura kestik mi" → intent: fatura_sorgula, mesaj: "Fatura ekranını açıyorum."
"kroman ne kadar borclu" → intent: musteri_bakiyeleri, mesaj: "Bakiyeleri açıyorum."
"kroman iletişim bilgileri" → intent: cari_detay, mesaj: "Kroman'ın bilgilerini getiriyorum.", params: {{musteri: "kroman"}}
"tahsilat girdim" → intent: tahsilat_ekle, mesaj: "Tahsilat ekranını açıyorum."
"kroman para ödedi" → intent: tahsilat_ekle, mesaj: "Tahsilat ekranını açıyorum.", params: {{musteri: "kroman"}}
"arene ödeme yaptım" → intent: odeme_ekle, mesaj: "Ödeme ekranını açıyorum.", params: {{musteri: "arene"}}
"uht'e fatura kes" → intent: satis_faturasi_kes, mesaj: "Satış faturası ekranını açıyorum.", params: {{musteri: "uht"}}
"ahmet hurdadan alış faturası" → intent: alis_faturasi_kes, mesaj: "Alış faturası ekranını açıyorum.", params: {{musteri: "ahmet hurda"}}
"yanlış fatura kestim iptal et" → intent: fatura_iptal, mesaj: "Fatura iptal ekranını açıyorum."
"bu faturayı e-fatura yap" → intent: e_fatura_kes, mesaj: "E-fatura/e-arşiv ekranını açıyorum."
"günlük özet" → intent: gunluk_ozet, mesaj: "Bugünün özetine bakıyorum."
"son fişler" → intent: son_fisler, mesaj: "Son fişleri getiriyorum."

GENEL SOHBET ÖRNEKLERİ (intent: none ama BİLGİLENDİRİCİ cevap):
"merhaba" → "Merhaba! Bugün ne yapıyoruz? Bekleyen irsaliyeleri kontrol edelim mi, yoksa fatura mı keseceğiz?"
"nasılsın" → "İyiyim, çalışıyorum! Sen nasılsın? Bugün muhasebede ne var?"
"nereye bağlısın" / "hangi sisteme bağlısın" → "Paraşüt'e bağlıyım — API üzerinden direkt çalışıyorum. Fatura kesip cari bakiyelerini oradan okuyorum. Bir de OCR ile kantar fişi okuyabiliyorum."
"paraşüt'e mi bağlısın" → "Evet, Paraşüt API'ye bağlıyım. Tüm fatura, cari, kasa ve irsaliye verilerin orada — ben okuyup yazıyorum."
"neler yapabilirsin" / "nelere erişebiliyorsun" / "anlat" / "hepsini" / "ne yapabilirsin sistem üzerinden" →
  Yukarıdaki uzun yetenek listesini (📋📥💵💸 emojilerle) ver. Detaylı, gerçek bilgi. Geri soru sorma.
"ne yapabilirsin" → DETAYLI listeyi ver (yukarıdaki). Kısa kesme.
"tamam" / "ok" → "Tamamdır, hazırım. Ne yapıyoruz?" — sonrası kullanıcıya kalmış
"teşekkürler" → "Rica ederim, başka bir şey olursa söyle."
"beşiktaş maçı kaç kaç" → "Bunu bilemem, muhasebe işine bakıyorum sadece. Fatura, irsaliye, tahsilat… bu konularda yardım edebilirim."
"""


# ── ANA FONKSİYON ─────────────────────────────────────────────────────

def _yetki_notu(yetkiler) -> str:
    """Kısıtlı rol için sistem prompt'una eklenecek not — yapamayacağı işleri önermesin."""
    if yetkiler is None:
        return ""
    izin = set(yetkiler)
    yapamaz = []
    if "fatura"   not in izin: yapamaz.append("fatura kesme/sorgulama, bekleyen irsaliyeler, e-fatura")
    if "tahsilat" not in izin: yapamaz.append("tahsilat/ödeme/transfer")
    if "cari"     not in izin: yapamaz.append("cari yönetimi ve müşteri bakiyeleri")
    if "rapor"    not in izin: yapamaz.append("raporlar (hesap özeti, günlük özet, gelir-gider, stok, son fişler)")
    if "toplu"    not in izin: yapamaz.append("toplu fiş içe alma")
    if not yapamaz:
        return ""
    return ("\n\n⚠️ ÖNEMLİ — BU KULLANICININ YETKİSİ KISITLI. Şu işlemleri YAPAMAZ, ASLA ÖNERME ve "
            "intent olarak SEÇME: " + "; ".join(yapamaz) + ". Kullanıcı bunları isterse kibarca "
            "'Bu işlem için yetkiniz yok' de ve intent=none ver. Sadece izinli işlere — özellikle "
            "fiş fotoğrafı yüklemeye — yönlendir.")


def soru_cevapla(mesaj: str, session_id: str = "", yetkiler=None) -> dict:
    """
    Kullanıcı mesajını alır, intent + mesaj + params döndürür.
    DB işlemi yok — session kaydı BackgroundTasks ile ayrıca yapılır.
    yetkiler verilirse kısıtlı rol için LLM yapamayacağı işleri önermez.
    """
    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=600,  # 200 idi — uzun yetenek listesi cevapları kesilmesin
            temperature=0.0,
            system=SISTEM_PROMPTU + _yetki_notu(yetkiler),
            tools=INTENT_TOOL,
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": mesaj}]
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "intent_tespit":
                intent      = block.input.get("intent", "none")
                mesaj_cevap = block.input.get("mesaj", "Sol menüden işlem seçebilirsiniz.")
                params      = block.input.get("params", {}) or {}

                if intent not in INTENTLER:
                    intent = "none"

                logger.info(f"Intent: {intent} | Params: {params} | Mesaj: {mesaj_cevap[:60]}")
                return {"intent": intent, "mesaj": mesaj_cevap, "params": params}

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


# ── BACKGROUND TASK UYUMLU SESSION KAYIT ──────────────────────────────

def session_satirlari_ekle_bg(session_id: str, satirlar: list):
    """tam_gecmis'e bir veya daha fazla satır ekler. satirlar: [{role, content}, ...]"""
    if not session_id or not satirlar:
        return
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

        for s in satirlar:
            role = s.get("role")
            if role == "ui":
                ui = s.get("ui") or {}
                if not ui.get("kind"):
                    continue
                yeni_ui = {"kind": str(ui["kind"]), "params": ui.get("params") or {}}
                if tam_gecmis:
                    son = tam_gecmis[-1]
                    if son.get("role") == "ui" and _ui_key(son.get("ui") or {}) == _ui_key(yeni_ui):
                        continue
                tam_gecmis.append({
                    "role": "ui",
                    "content": s.get("content", "") or "",
                    "ui": yeni_ui,
                    "ts": bugun,
                })
                continue
            if role not in ("user", "assistant"):
                continue
            row = {"role": role, "content": s.get("content", ""), "ts": bugun}
            if s.get("ui"):
                row["ui"] = s["ui"]
            tam_gecmis.append(row)

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
        logger.info(f"Session satırları kaydedildi (bg): {session_id} (+{len(satirlar)})")
    except Exception as e:
        logger.error(f"Session satırları kaydedilemedi (bg): {e}")
        db.rollback()
    finally:
        db.close()


def session_kaydet_bg(session_id: str, user_mesaj: str, bot_mesaj: str,
                      ui_kind: str = None, ui_params: dict = None):
    """BackgroundTask — kullanıcı + asistan + (opsiyonel) panel durumu."""
    rows = [
        {"role": "user", "content": user_mesaj},
        {"role": "assistant", "content": bot_mesaj},
    ]
    if ui_kind and str(ui_kind) != "none":
        rows.append({
            "role": "ui",
            "content": "",
            "ui": {"kind": str(ui_kind), "params": ui_params or {}},
        })
    session_satirlari_ekle_bg(session_id, rows)