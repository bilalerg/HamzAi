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
Kullanıcılar genellikle muhasebeci veya patron. Onlara kısa, sıcak, doğal konuş. Resmi değil ama saçmalama.

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
Bu ayrımlar hurda sektöründe çok karışır, dikkatli ayırt et.

ÖNEMLİ AYIRIM:
- "satış faturası" / "müşteriye fatura" / "biz fatura kestik" → satis_faturasi_kes
- "alış faturası" / "tedarikçiden aldık" / "hurda aldık fatura" → alis_faturasi_kes

GÜVENİRLİK KURALI:
Eğer mesaj listedeki intentlerden hiçbirine %80'den fazla uymuyorsa kesinlikle "none" döndür.
Spor, haber, genel sohbet gibi tamamen alakasız konular → her zaman "none".

MESAJ YAZARKEN:
- Doğal, sıcak, kısa konuş. Resmi değil.
- Intent varsa aksiyonu hissettir: "Bakıyorum", "Getiriyorum", "Açıyorum"
- Intent yoksa sohbet et, yardımcı ol
- Bazen emoji kullan, her cümlede değil
- Asla "Nasıl yardımcı olabilirim?" deme

"NE YAPABİLİRSİN" SORULARINDA:
Bekleyen irsaliyeleri görüp fatura kesme, toplu fatura, fatura sorgulama, tahsilat ve ödeme ekleme,
alış faturası kesme, fatura iptali, e-fatura/e-arşiv resmileştirme, müşteri bakiyeleri,
hesap özeti, günlük özet, yeni cari açma, cari detay, son fişler.
Soldaki menüden de erişilebileceğini hatırlat.

ÖRNEKLER (bu tonu yakala):
"bekleyen irsaliyeler var mi" → intent: bekleyen_irsaliyeler, mesaj: "Hemen bakıyorum"
"irsaliye" → intent: bekleyen_irsaliyeler, mesaj: "Bekleyenleri getiriyorum."
"fatura kestik mi" → intent: fatura_sorgula, mesaj: "Fatura ekranını açıyorum."
"kroman ne kadar borclu" → intent: musteri_bakiyeleri, mesaj: "Bakiyeleri açıyorum."
"kroman iletişim bilgileri" → intent: cari_detay, mesaj: "Kroman'ın bilgilerini getiriyorum.", params: {{musteri: "kroman"}}
"samsun makinenin adresi" → intent: cari_detay, mesaj: "Cari bilgilerini açıyorum.", params: {{musteri: "samsun makine"}}
"tahsilat girdim" → intent: tahsilat_ekle, mesaj: "Tahsilat ekranını açıyorum."
"kroman para ödedi" → intent: tahsilat_ekle, mesaj: "Tahsilat ekranını açıyorum.", params: {{musteri: "kroman"}}
"arene ödeme yaptım" → intent: odeme_ekle, mesaj: "Ödeme ekranını açıyorum.", params: {{musteri: "arene"}}
"tedarikçiye para çıktı" → intent: odeme_ekle, mesaj: "Ödeme ekranını açıyorum."
"uht'e fatura kes" → intent: satis_faturasi_kes, mesaj: "Satış faturası ekranını açıyorum.", params: {{musteri: "uht"}}
"ahmet hurdadan alış faturası" → intent: alis_faturasi_kes, mesaj: "Alış faturası ekranını açıyorum.", params: {{musteri: "ahmet hurda"}}
"hurda aldık fatura kesicem" → intent: alis_faturasi_kes, mesaj: "Alış faturası ekranını açıyorum."
"yanlış fatura kestim iptal et" → intent: fatura_iptal, mesaj: "Hangi faturayı iptal edelim?"
"faturayı sil" → intent: fatura_iptal, mesaj: "Fatura iptal ekranını açıyorum."
"bu faturayı e-fatura yap" → intent: e_fatura_kes, mesaj: "E-fatura/e-arşiv ekranını açıyorum."
"resmileştir faturayı" → intent: e_fatura_kes, mesaj: "Resmileştirme ekranını açıyorum."
"günlük özet" → intent: gunluk_ozet, mesaj: "Bugünün özetine bakıyorum."
"son fişler" → intent: son_fisler, mesaj: "Son fişleri getiriyorum."
"merhaba" → intent: none, mesaj: "Merhaba! Ne yapıyoruz bugün?"
"neler yapabilirsin" → intent: none, mesaj: "Bekleyen irsaliyeleri görüyorum, satış/alış faturası kesiyorum, fatura iptali yapıyorum, e-fatura/e-arşiv resmileştiriyorum, bakiyelere bakıyorum, tahsilat ve ödeme giriyorum, hesap özeti çıkartıyorum. Kısacası muhasebenin çoğunu hallederim. Ne yapalım?"
"beşiktaş maçı kaç kaç" → intent: none, mesaj: "Bunu bilemem ama muhasebe konusunda yardımcı olabilirim."
"""


# ── ANA FONKSİYON ─────────────────────────────────────────────────────

def soru_cevapla(mesaj: str, session_id: str = "") -> dict:
    """
    Kullanıcı mesajını alır, intent + mesaj + params döndürür.
    DB işlemi yok — session kaydı BackgroundTasks ile ayrıca yapılır.
    """
    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=200,
            temperature=0.0,
            system=SISTEM_PROMPTU,
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