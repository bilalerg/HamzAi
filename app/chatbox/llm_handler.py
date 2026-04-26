# llm_handler.py — Profaix Web Chatbot

import json
import re
import anthropic
from datetime import date
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

# ── SABİTLER ──────────────────────────────────────────────────────────
MAX_GECMIS = 20       # Tool mesajları da dahil olduğu için artırıldı
MODEL      = "claude-haiku-4-5-20251001"

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

TOOLS = [
    {
        "name": "cari_listesi_getir",
        "description": "Paraşüt'teki carilerde arama yapar. arama_metni boş bırakılırsa tümünü listeler.",
        "input_schema": {
            "type": "object",
            "properties": {
                "arama_metni": {"type": "string", "description": "Aranacak firma adının bir kısmı (örn: 'Kroman', 'UHT'). Boş bırakılırsa tüm cariler gelir."}
            },
            "required": []
        }
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
        "description": "Belirli bir carinin faturalarını listeler. Tahsilat geçmişini görmek için de kullan — her fatura tahsilatları içerir.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cari_adi": {"type": "string", "description": "Cari adı"},
                "limit": {"type": "integer", "description": "Kaç fatura (varsayılan 10)", "default": 10}
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
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Kaç fatura (varsayılan 25)", "default": 25}
            },
            "required": []
        }
    },
    {
        "name": "hesap_ozeti_getir",
        "description": "Genel hesap özeti: toplam alacak, borç, cari sayısı.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "tahsilat_ekle",
        "description": "Paraşüt'te belirli bir cariye tahsilat kaydı ekler. ZORUNLU: Çağırmadan önce kullanıcıya 'X firmasına Y TL tahsilat ekleyeceğim, onaylıyor musunuz?' diye sor ve onay al.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cari_adi": {"type": "string", "description": "Cari adı"},
                "tutar": {"type": "number", "description": "Tahsilat tutarı (TL)"},
                "aciklama": {"type": "string", "description": "Açıklama (opsiyonel)"},
                "tarih": {"type": "string", "description": "Tarih — GG.AA.YYYY veya YYYY-MM-DD formatında (opsiyonel)"}
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

def _tool_calistir(tool_name: str, tool_input: dict, db=None) -> str:
    """
    Tool'u çalıştırır ve sonucu string olarak döndürür.
    db parametresi tahsilat_ekle'de IslemLog için kullanılır.
    """
    try:
        from app.parasut.client import (
            cari_listesi_getir, cari_detay_getir, cari_faturalari_getir,
            cari_irsaliyeleri_getir, acik_faturalar_getir, hesap_ozeti_getir,
            cari_olustur, cari_id_bul
        )

        if tool_name == "cari_listesi_getir":
            arama = tool_input.get("arama_metni", "").strip().lower()
            cariler = cari_listesi_getir()
            if arama:
                cariler = [c for c in cariler if arama in c["ad"].lower()]
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
            if tarih:
                from datetime import datetime
                tarih_parsed = None
                for fmt in ["%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"]:
                    try:
                        tarih_parsed = datetime.strptime(str(tarih).strip(), fmt).strftime("%Y-%m-%d")
                        break
                    except Exception:
                        continue
                tarih = tarih_parsed
            sonuc = tahsilat_ekle(cari_adi, tutar, aciklama, tarih)

            # FIX: LLM üzerinden yapılan tahsilatları da IslemLog'a yaz
            if sonuc.get("basarili") and db is not None:
                try:
                    from app.core.database import IslemLog
                    log = IslemLog(
                        islem_tipi="TAHSILAT_EKLENDI",
                        cari_adi=cari_adi,
                        tutar=int(float(tutar)),
                        aciklama=f"LLM | Tarih: {tarih or 'Bugün'} | {aciklama or ''}",
                    )
                    db.add(log)
                    db.commit()
                except Exception as log_err:
                    logger.warning(f"IslemLog yazılamadı: {log_err}")

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
        return json.dumps({"hata": "İşlem sırasında teknik bir sorun oluştu. Kullanıcıdan işlemi tekrar etmesini iste.", "detay": str(e)}, ensure_ascii=False)


# ── DB CONTEXT — MİNİMAL ─────────────────────────────────────────────

def db_context_hazirla(db) -> str:
    from app.core.database import WeighTicket, TicketStatus
    from sqlalchemy import func
    bugun = date.today()
    bugun_str = bugun.strftime("%Y-%m-%d")
    try:
        toplam_fis = db.query(func.count(WeighTicket.id)).filter(
            WeighTicket.created_at >= bugun_str
        ).scalar() or 0

        bugun_kg = db.query(func.sum(WeighTicket.agirlik_kg)).filter(
            WeighTicket.created_at >= bugun_str
        ).scalar() or 0

        tamamlanan = db.query(func.count(WeighTicket.id)).filter(
            WeighTicket.created_at >= bugun_str,
            WeighTicket.status == TicketStatus.TAMAMLANDI
        ).scalar() or 0

        bekleyen = db.query(func.count(WeighTicket.id)).filter(
            WeighTicket.created_at >= bugun_str,
            WeighTicket.status.notin_([
                TicketStatus.TAMAMLANDI, TicketStatus.IPTAL, TicketStatus.HATA
            ])
        ).scalar() or 0

        return (
            f"Bugün ({bugun.strftime('%d.%m.%Y')}): "
            f"{toplam_fis} fiş / {bugun_kg/1000:.1f} ton | "
            f"tamamlanan={tamamlanan} bekleyen={bekleyen}"
        )
    except Exception as e:
        logger.error(f"DB context hatası: {e}")
        return f"Bugün: {bugun.strftime('%d.%m.%Y')}"


# ── SESSION YÖNETİMİ ──────────────────────────────────────────────────

def _session_yukle(db, session_id: str):
    from app.core.database import ChatSession
    return db.query(ChatSession).filter(ChatSession.session_id == session_id).first()


def _session_kaydet(db, session_id: str, isim: str, gecmis_saf: list,
                    yeni_mesaj_user: dict = None, yeni_mesaj_asistan: dict = None,
                    tool_log: list = None):
    """
    gecmis_saf      : Sadece string content mesajlar — gecmis kolonuna yazılır.
    yeni_mesaj_user : Kullanıcı mesajı — tam_gecmis'e eklenir.
    yeni_mesaj_asistan: Claude cevabı — tam_gecmis'e eklenir.
    tool_log        : Tool çağrıları ve sonuçları — tam_gecmis'e eklenir.
                      Format: [{"tool": "cari_listesi_getir", "input": {}, "sonuc": "...", "ts": "..."}]
    """
    from app.core.database import ChatSession
    try:
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        gecmis_json = json.dumps(gecmis_saf, ensure_ascii=False)

        # tam_gecmis güncellemesi
        tam_gecmis = []
        if session and session.tam_gecmis:
            try:
                tam_gecmis = json.loads(session.tam_gecmis)
            except Exception:
                tam_gecmis = []

        bugun_str = str(date.today())

        # Kullanıcı mesajını ekle
        if yeni_mesaj_user:
            tam_gecmis.append({**yeni_mesaj_user, "ts": bugun_str})

        # Tool çağrılarını ekle (kullanıcı mesajından sonra, cevaptan önce)
        if tool_log:
            for t in tool_log:
                tam_gecmis.append({**t, "ts": bugun_str})

        # Claude cevabını ekle
        if yeni_mesaj_asistan:
            tam_gecmis.append({**yeni_mesaj_asistan, "ts": bugun_str})

        # Max 200 kayıt tut (tool logları eklenince daha fazla yer gerekiyor)
        if len(tam_gecmis) > 200:
            tam_gecmis = tam_gecmis[-200:]

        tam_gecmis_json = json.dumps(tam_gecmis, ensure_ascii=False)

        if session:
            session.isim       = isim
            session.gecmis     = gecmis_json
            session.tam_gecmis = tam_gecmis_json
        else:
            session = ChatSession(
                session_id=session_id,
                isim=isim,
                gecmis=gecmis_json,
                tam_gecmis=tam_gecmis_json
            )
            db.add(session)

        db.commit()
    except Exception as e:
        logger.error(f"Session kaydedilemedi: {e}")
        db.rollback()


# ── MESAJ GEÇMİŞİ SERİALİZASYONU ────────────────────────────────────

def _content_serialize(content) -> list | str:
    """
    Claude API'sinden gelen content bloklarını JSON-serileştirilebilir hale getirir.
    Hem string hem liste (tool_use blokları) desteklenir.
    """
    if isinstance(content, str):
        return content
    result = []
    for b in content:
        if hasattr(b, "type"):
            if b.type == "text":
                result.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                result.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif isinstance(b, dict):
            result.append(b)
    return result


def _gecmis_saf_cikart(gecmis: list) -> list:
    """
    Tam geçmişten (tool_use/tool_result içerebilir) sadece
    string content'li user/assistant mesajlarını çıkarır.
    Bunlar DB'de kısa özet geçmişi olarak tutulur.
    """
    saf = []
    for m in gecmis:
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            saf.append({"role": m["role"], "content": content})
    return saf


# ── ANA FONKSİYON ─────────────────────────────────────────────────────

def soru_cevapla(mesaj: str, db, gonderen: str = "") -> str:
    session = _session_yukle(db, gonderen)

    if session is None:
        _session_kaydet(db, gonderen, isim="Kullanıcı", gecmis_saf=[])
        session = _session_yukle(db, gonderen)

    isim   = (session.isim if session else None) or "Kullanıcı"

    # Tam geçmişi yükle (tool_use/tool_result blokları dahil)
    # gecmis kolonu artık tam geçmişi tutuyor — tool_use bağlamı korunur
    gecmis: list = []
    if session and session.tam_gecmis:
        try:
            # tam_gecmis'ten yükle — tool_use blokları burada
            raw = json.loads(session.tam_gecmis)
            # Sadece role/content yapısındakileri al (ts gibi extra alanlar API'ye gitmemeli)
            gecmis = [
                {"role": m["role"], "content": m["content"]}
                for m in raw
                if "role" in m and "content" in m
            ]
        except Exception:
            gecmis = []
    elif session and session.gecmis:
        # Fallback: eski kayıtlar için
        try:
            gecmis = json.loads(session.gecmis)
        except Exception:
            gecmis = []

    db_context = db_context_hazirla(db)

    sistem_promptu = f"""Sen Profaix muhasebe asistanısın. Hurda metal kantar takip ve muhasebe sistemi.
Kullanıcı: {isim} | {db_context}

GÖREVIN: Kullanıcıyı doğru butona veya komutu yönlendirmek. Serbest sorguları kendin cevaplama — her işlem için doğru yolu göster.

PANEL BUTONLARI VE KEYWORDLER:
- Bekleyen irsaliyeler / fatura kes → "bekleyen irsaliyeler" yazın veya sol menüden 📋 Bekleyen irsaliyeler
- Toplu fatura → "toplu fatura" yazın veya sol menüden 🧾 Toplu fatura
- Fatura sorgula → "fatura sorgula" yazın veya sol menüden 🔍 Fatura sorgula
- Tahsilat ekle → "tahsilat ekle" yazın veya sol menüden 💵 Tahsilat ekle
- Yeni cari → "yeni cari" yazın veya sol menüden ➕ Yeni cari
- Müşteri bakiyeleri → "müşteri bakiyeleri" yazın veya sol menüden 💰 Müşteri bakiyeleri
- Hesap özeti → "hesap özeti" yazın veya sol menüden 📊 Hesap özeti
- Günlük özet → "günlük özet" yazın veya sol menüden 📈 Günlük özet
- Son fişler → "son fişler" yazın veya sol menüden 🗂️ Son yüklenen fişler
- Fiş yükle → sol alttaki 📎 butonundan fotoğraf yükleyin

YÖNLENDIRME KURALLARI (ÇOK ÖNEMLİ):
- Kullanıcı yukarıdaki işlemlerden birini farklı kelimelerle isterse → tool KULLANMA, sadece doğru komutu söyle
- Örnek: "faturaları getir" → "Fatura sorgulamak için **'fatura sorgula'** yazın veya sol menüden 🔍 Fatura sorgula butonuna tıklayın."
- Örnek: "bakiyeler ne" → "Bakiyeleri görmek için **'müşteri bakiyeleri'** yazın veya sol menüden 💰 Müşteri bakiyeleri butonuna tıklayın."
- Örnek: "irsaliye var mı" → "Bekleyen irsaliyeleri görmek için **'bekleyen irsaliyeler'** yazın veya sol menüden 📋 butonuna tıklayın."

SADECE BUNLARI KENDİN YAP (tool kullanabilirsin):
- Tahsilat ekleme (onay alarak)
- Yeni cari oluşturma (bilgileri toplayarak)
- Spesifik cari bakiye veya detay sorusu (örn: "Kroman'ın bakiyesi kaç?")

BAKİYE KONVANSIYONU:
- POZİTİF bakiye = müşteri bize BORÇLU = tahsil edilecek
- NEGATİF bakiye = biz müşteriye borçluyuz
- Pozitif bakiyeyi "alacağımız" veya "müşteri borçlu" olarak göster

GENEL KURALLAR:
- Kısa ve net cevap ver, markdown kullanma
- Tahsilat eklemeden önce mutlaka onay al: "X firmasına Y TL tahsilat ekleyeceğim, onaylıyor musunuz?"
- Cari adı belirsizse cari_listesi_getir ile eşleştir, kullanıcıya tekrar sorma
- Fatura kesemezsin, irsaliye oluşturamazsın — bunlar için yönlendir"""

    # Yeni kullanıcı mesajını geçmişe ekle
    gecmis.append({"role": "user", "content": mesaj})

    # Geçmişi MAX_GECMIS ile sınırla (çiftler halinde kırp — tek mesaj kalmasın)
    if len(gecmis) > MAX_GECMIS:
        gecmis = gecmis[-MAX_GECMIS:]
        # İlk mesaj assistant ise bir tane daha at — geçmiş user ile başlamalı
        if gecmis and gecmis[0]["role"] != "user":
            gecmis = gecmis[1:]

    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=2000,  # FIX: 800'den 2000'e yükseltildi
            system=sistem_promptu,
            tools=TOOLS,
            messages=gecmis
        )

        # ── TOOL DÖNGÜSÜ ──────────────────────────────────────────────
        tool_log = []  # Bu konuşmadaki tüm tool çağrılarını biriktir

        while response.stop_reason == "tool_use":
            assistant_content = response.content

            # Tool_use bloklarını da içeren tam assistant mesajını geçmişe ekle
            gecmis.append({
                "role": "assistant",
                "content": _content_serialize(assistant_content)
            })

            # Tool'ları çalıştır, sonuçları topla ve logla
            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    logger.info(f"🔧 Tool: {block.name} — {block.input}")
                    sonuc = _tool_calistir(block.name, block.input, db=db)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": sonuc
                    })
                    # DB'ye kaydedilecek tool logu
                    tool_log.append({
                        "type": "tool_call",
                        "tool": block.name,
                        "input": block.input,
                        "sonuc_ozet": sonuc[:300] if isinstance(sonuc, str) else str(sonuc)[:300]
                    })

            # Tool sonuçlarını geçmişe ekle
            gecmis.append({"role": "user", "content": tool_results})

            response = _client.messages.create(
                model=MODEL,
                max_tokens=2000,
                system=sistem_promptu,
                tools=TOOLS,
                messages=gecmis
            )

        # ── SON CEVAP ─────────────────────────────────────────────────
        cevap_blocks = response.content
        cevap = "".join(b.text for b in cevap_blocks if hasattr(b, "text")).strip()

        # Son assistant mesajını da geçmişe ekle
        gecmis.append({"role": "assistant", "content": cevap})

        # FIX: Geçmişi sınırla (MAX_GECMIS içinde tut)
        if len(gecmis) > MAX_GECMIS:
            gecmis = gecmis[-MAX_GECMIS:]
            if gecmis and gecmis[0]["role"] != "user":
                gecmis = gecmis[1:]

        # FIX: Saf geçmiş (sadece string content'liler) — DB'de kısa özet
        gecmis_saf = _gecmis_saf_cikart(gecmis)

        # Tek seferde kaydet — user mesajı, tool logları, assistant cevabı
        _session_kaydet(
            db, gonderen, isim=isim,
            gecmis_saf=gecmis_saf,
            yeni_mesaj_user={"role": "user", "content": mesaj},
            yeni_mesaj_asistan={"role": "assistant", "content": cevap},
            tool_log=tool_log if tool_log else None
        )

        logger.info(f"🤖 Cevap ({isim}): {cevap[:60]}...")
        return cevap

    except Exception as e:
        logger.error(f"Claude API hatası: {e}")
        return "❌ Şu an cevap veremiyorum, lütfen tekrar deneyin."