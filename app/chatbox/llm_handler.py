# llm_handler.py — Profaix Web Chatbot

import json
import re
import anthropic
from datetime import date
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

# ── SABİTLER ──────────────────────────────────────────────────────────
MAX_GECMIS = 10
MODEL      = "claude-haiku-4-5-20251001"

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

TOOLS = [
    {
        "name": "cari_listesi_getir",
        "description": "Paraşüt'teki carileri listeler: ad, id ve bakiye.",
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
                "limit": {"type": "integer", "description": "Kaç fatura (varsayılan 5)", "default": 5}
            },
            "required": ["cari_adi"]
        }
    },
    {
        "name": "cari_irsaliyeleri_getir",
        "description": "Belirli bir carinin Paraşüt'teki irsaliyelerini listeler. NOT: Fatura kesilen irsaliyeler silinir, bu nedenle çoğunlukla boş döner. Bekleyen irsaliyeler için bu tool yerine kullanıcıya 'bekleyen irsaliyeler butonuna tıkla' de.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cari_adi": {"type": "string", "description": "Cari adı"},
                "limit": {"type": "integer", "description": "Kaç irsaliye (varsayılan 5)", "default": 5}
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
        "description": "Genel hesap özeti: toplam alacak, borç, cari sayısı.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "tahsilat_ekle",
        "description": "Paraşüt'te belirli bir cariye tahsilat kaydı ekler.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cari_adi": {"type": "string", "description": "Cari adı"},
                "tutar": {"type": "number", "description": "Tahsilat tutarı (TL)"},
                "aciklama": {"type": "string", "description": "Açıklama (opsiyonel)"},
                "tarih": {"type": "string", "description": "GG.AA.YYYY formatında tarih (opsiyonel)"}
            },
            "required": ["cari_adi", "tutar"]
        }
    },
    {
        "name": "fatura_detay_getir",
        "description": "Belirli bir faturanın detaylarını getirir: kalemler, tahsilatlar, bağlı irsaliyeler.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fatura_id": {"type": "string", "description": "Fatura ID (örn: 1083916460)"}
            },
            "required": ["fatura_id"]
        }
    },
    {
        "name": "cari_olustur",
        "description": "Paraşüt'te yeni cari oluşturur. Tüm bilgiler toplandıktan sonra çağır.",
        "input_schema": {
            "type": "object",
            "properties": {
                "firma_adi": {"type": "string", "description": "Firma adı (zorunlu)"},
                "email": {"type": "string"},
                "telefon": {"type": "string"},
                "vergi_no": {"type": "string"},
                "vergi_dairesi": {"type": "string"},
                "adres": {"type": "string"}
            },
            "required": ["firma_adi"]
        }
    },
]


# ── TOOL ÇALIŞTIR ─────────────────────────────────────────────────────

def _tool_calistir(tool_name: str, tool_input: dict) -> str:
    try:
        from app.parasut.client import (
            cari_listesi_getir, cari_detay_getir, cari_faturalari_getir,
            cari_irsaliyeleri_getir, acik_faturalar_getir, hesap_ozeti_getir,
            cari_olustur, cari_id_bul
        )

        if tool_name == "cari_listesi_getir":
            cariler = cari_listesi_getir()
            kirpilmis = [{"id": c["id"], "ad": c["ad"], "bakiye": c.get("bakiye", 0)} for c in cariler]
            return json.dumps(kirpilmis, ensure_ascii=False)

        elif tool_name == "cari_detay_getir":
            cari_adi = tool_input.get("cari_adi", "")
            cari_id = cari_id_bul(cari_adi)
            if not cari_id:
                return f"'{cari_adi}' adlı cari bulunamadı."
            return json.dumps(cari_detay_getir(cari_id), ensure_ascii=False)

        elif tool_name == "cari_faturalari_getir":
            cari_adi = tool_input.get("cari_adi", "")
            limit = min(tool_input.get("limit", 5), 10)
            cari_id = cari_id_bul(cari_adi)
            if not cari_id:
                return f"'{cari_adi}' adlı cari bulunamadı."
            return json.dumps(cari_faturalari_getir(cari_id, limit), ensure_ascii=False)

        elif tool_name == "cari_irsaliyeleri_getir":
            cari_adi = tool_input.get("cari_adi", "")
            limit = min(tool_input.get("limit", 5), 10)
            cari_id = cari_id_bul(cari_adi)
            if not cari_id:
                return f"'{cari_adi}' adlı cari bulunamadı."
            return json.dumps(cari_irsaliyeleri_getir(cari_id, limit), ensure_ascii=False)

        elif tool_name == "acik_faturalar_getir":
            return json.dumps(acik_faturalar_getir(), ensure_ascii=False)

        elif tool_name == "hesap_ozeti_getir":
            ozet = hesap_ozeti_getir()
            return json.dumps({
                "toplam_cari": ozet.get("toplam_cari", 0),
                "toplam_alacak": ozet.get("toplam_alacak", 0),
                "toplam_borclu": ozet.get("toplam_borclu", 0),
            }, ensure_ascii=False)

        elif tool_name == "tahsilat_ekle":
            from app.parasut.client import tahsilat_ekle
            cari_adi = tool_input.get("cari_adi", "")
            tutar    = tool_input.get("tutar", 0)
            aciklama = tool_input.get("aciklama")
            tarih    = tool_input.get("tarih")
            if tarih and "." in tarih:
                try:
                    from datetime import datetime
                    tarih = datetime.strptime(tarih, "%d.%m.%Y").strftime("%Y-%m-%d")
                except Exception:
                    tarih = None
            sonuc = tahsilat_ekle(cari_adi, tutar, aciklama, tarih)
            return json.dumps(sonuc, ensure_ascii=False)

        elif tool_name == "fatura_detay_getir":
            from app.parasut.client import fatura_detay_getir
            fatura_id = tool_input.get("fatura_id", "")
            detay = fatura_detay_getir(fatura_id)
            return json.dumps(detay, ensure_ascii=False)

        elif tool_name == "cari_olustur":
            sonuc = cari_olustur(
                firma_adi=tool_input.get("firma_adi"),
                email=tool_input.get("email"),
                telefon=tool_input.get("telefon"),
                vergi_no=tool_input.get("vergi_no"),
                vergi_dairesi=tool_input.get("vergi_dairesi"),
                adres=tool_input.get("adres")
            )
            return json.dumps(sonuc, ensure_ascii=False)

        else:
            return f"Bilinmeyen tool: {tool_name}"

    except Exception as e:
        logger.error(f"Tool çalıştırma hatası ({tool_name}): {e}")
        return f"Hata: {str(e)}"


# ── DB CONTEXT — MİNİMAL ─────────────────────────────────────────────

def db_context_hazirla(db) -> str:
    from app.core.database import WeighTicket, TicketStatus
    bugun = date.today()
    try:
        bugun_fisler = db.query(WeighTicket).filter(
            WeighTicket.created_at >= bugun.strftime("%Y-%m-%d")
        ).all()
        bugun_kg   = sum(t.agirlik_kg or 0 for t in bugun_fisler)
        tamamlanan = sum(1 for t in bugun_fisler if t.status == TicketStatus.TAMAMLANDI)
        bekleyen   = sum(1 for t in bugun_fisler if t.status not in [
            TicketStatus.TAMAMLANDI, TicketStatus.IPTAL, TicketStatus.HATA
        ])
        return (
            f"Bugün ({bugun.strftime('%d.%m.%Y')}): "
            f"{len(bugun_fisler)} fiş / {bugun_kg/1000:.1f} ton | "
            f"tamamlanan={tamamlanan} bekleyen={bekleyen}"
        )
    except Exception as e:
        logger.error(f"DB context hatası: {e}")
        return f"Bugün: {bugun.strftime('%d.%m.%Y')}"


# ── SESSION YÖNETİMİ ──────────────────────────────────────────────────

def _session_yukle(db, session_id: str):
    from app.core.database import ChatSession
    return db.query(ChatSession).filter(ChatSession.session_id == session_id).first()


def _session_kaydet(db, session_id: str, isim: str, gecmis: list, yeni_mesaj: dict = None):
    from app.core.database import ChatSession
    try:
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        gecmis_json = json.dumps(gecmis, ensure_ascii=False)

        # Tam geçmiş — tüm mesajları kaydet
        tam_gecmis = []
        if session and session.tam_gecmis:
            try:
                tam_gecmis = json.loads(session.tam_gecmis)
            except Exception:
                tam_gecmis = []
        if yeni_mesaj:
            tam_gecmis.append(yeni_mesaj)

        tam_gecmis_json = json.dumps(tam_gecmis, ensure_ascii=False)

        if session:
            session.isim       = isim
            session.gecmis     = gecmis_json
            session.tam_gecmis = tam_gecmis_json
        else:
            session = ChatSession(
                session_id = session_id,
                isim       = isim,
                gecmis     = gecmis_json,
                tam_gecmis = tam_gecmis_json
            )
            db.add(session)
        db.commit()
    except Exception as e:
        logger.error(f"Session kaydedilemedi: {e}")
        db.rollback()



# ── ANA FONKSİYON ─────────────────────────────────────────────────────

def soru_cevapla(mesaj: str, db, gonderen: str = "") -> str:
    session = _session_yukle(db, gonderen)

    if session is None:
        _session_kaydet(db, gonderen, isim="Kullanıcı", gecmis=[])
        session = _session_yukle(db, gonderen)

    if session.isim is None:
        _session_kaydet(db, gonderen, isim="Kullanıcı", gecmis=[])
        session = _session_yukle(db, gonderen)

    isim       = session.isim or "Kullanıcı"
    gecmis     = json.loads(session.gecmis) if session.gecmis else []
    db_context = db_context_hazirla(db)

    sistem_promptu = f"""Sen Profaix muhasebe asistanısın. Hurda metal kantar takip ve muhasebe sistemi.
Kullanıcı: {isim} | {db_context}

GÖREVIN: Sadece bilgi sorgula ve cevapla. Fatura kesme veya irsaliye oluşturma işlemleri sistem tarafından yapılır, sen yapma.

YAPABILECEKLERIN:
- Cari bakiye, fatura, irsaliye sorgulama
- Hesap özeti getirme
- Cari detay getirme
- Yeni cari oluşturma
- Tahsilat kaydı ekleme

YAPAMAYACAKLARIN:
- Fatura kesemezsin — kullanıcıya "bekleyen irsaliyeler butonuna tıklayın" de
- İrsaliye oluşturamazsın — fiş yükleme işlemi bunu otomatik yapar

BAKİYE KONVANSIYONU (ÇOK ÖNEMLİ):
- Paraşüt'te bakiye POZİTİF = müşteri bize BORÇLU = TAHSİL EDİLECEK
- Paraşüt'te bakiye NEGATİF = biz müşteriye borçluyuz = ÖDENECEKTİR
- Örnek: bakiye=60120 → "İçtaş 60.120 TL borçlu, biz tahsil edeceğiz"
- hesap_ozeti_getir'den gelen toplam_alacak = bize borçlu olanların toplamı
- Pozitif bakiyeyi "alacağımız" veya "müşteri borçlu" olarak göster

KURALLAR:
- Bilgi için mutlaka tool kullan, tahmin etme veya uydurma
- Bilmediğini uydurma
- Cari adı kısaltma veya belirsizse direkt cari_listesi_getir çağır, eşleştir, sonra işlemi yap. Kullanıcıya tekrar sorma.
- Fatura listelerken: tarih, tutar, durum, irsaliye_id (varsa) göster
- Kısa ve net cevap ver, gereksiz markdown kullanma
- Yeni cari: firma adı→email→tel→vergi no→vergi dairesi→adres sor, özet göster, onay al, cari_olustur çağır"""

    gecmis.append({"role": "user", "content": mesaj})
    if len(gecmis) > MAX_GECMIS:
        gecmis = gecmis[-MAX_GECMIS:]

    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=800,
            system=sistem_promptu,
            tools=TOOLS,
            messages=gecmis
        )

        while response.stop_reason == "tool_use":
            tool_results = []
            assistant_content = response.content

            for block in assistant_content:
                if block.type == "tool_use":
                    logger.info(f"🔧 Tool: {block.name} — {block.input}")
                    sonuc = _tool_calistir(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": sonuc
                    })

            gecmis.append({"role": "assistant", "content": [
                {"type": b.type, **(
                    {"text": b.text} if b.type == "text"
                    else {"id": b.id, "name": b.name, "input": b.input}
                )}
                for b in assistant_content
            ]})
            gecmis.append({"role": "user", "content": tool_results})

            response = _client.messages.create(
                model=MODEL,
                max_tokens=800,
                system=sistem_promptu,
                tools=TOOLS,
                messages=gecmis
            )

        cevap = "".join(b.text for b in response.content if hasattr(b, "text")).strip()

        gecmis_kayit = [m for m in gecmis if isinstance(m.get("content"), str)]
        gecmis_kayit.append({"role": "assistant", "content": cevap})
        if len(gecmis_kayit) > MAX_GECMIS:
            gecmis_kayit = gecmis_kayit[-MAX_GECMIS:]

        # Yeni mesajları tam geçmişe ekle
        _session_kaydet(db, gonderen, isim=isim, gecmis=gecmis_kayit, yeni_mesaj={"role": "user", "content": mesaj, "ts": str(date.today())})
        # Cevabı da ekle
        _session_kaydet(db, gonderen, isim=isim, gecmis=gecmis_kayit, yeni_mesaj={"role": "assistant", "content": cevap, "ts": str(date.today())})
        logger.info(f"🤖 Cevap ({isim}): {cevap[:60]}...")
        return cevap

    except Exception as e:
        logger.error(f"Claude API hatası: {e}")
        return "❌ Şu an cevap veremiyorum, lütfen tekrar deneyin."