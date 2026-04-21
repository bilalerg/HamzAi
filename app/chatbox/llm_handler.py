# llm_handler.py — Profaix Web Chatbot (token-optimized)

import json
import re
import anthropic
from datetime import date
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

# ── SABİTLER ──────────────────────────────────────────────────────────
MAX_GECMIS = 6          # Mesaj sayısı sınırı (token tasarrufu)
MODEL      = "claude-haiku-4-5-20251001"   # Sonnet'in ~1/5 fiyatı

# Anthropic client — modül seviyesinde bir kez init
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SORU_BELIRTECLERI = [
    "?", "kaç", "var mı", "nedir", "nerede", "nasıl", "ne zaman",
    "hangi", "toplam", "bugün", "fatura", "plaka", "irsaliye", "ton"
]

TOOLS = [
    {
        "name": "cari_listesi_getir",
        "description": "Paraşüt'teki cari adlarını ve ID'lerini listeler.",
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
        "description": "Belirli bir carinin irsaliyelerini listeler.",
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
        "description": "Genel hesap özeti: toplam alacak, borç, cari sayısı. Cari listesi için cari_listesi_getir kullan.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "fatura_kes_waybill",
        "description": "Tek bir Paraşüt irsaliyesine fatura keser. Kullanıcıdan fiyat aldıktan sonra çağır.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cari_adi": {"type": "string", "description": "Cari/firma adı"},
                "parasut_irsaliye_id": {"type": "string", "description": "Paraşüt irsaliye ID'si"},
                "agirlik_kg": {"type": "number", "description": "Net ağırlık kg"},
                "malzemeler": {"type": "object", "description": "Malzeme adı → kg dict (opsiyonel)"},
                "toplanan_fiyatlar": {"type": "object", "description": "Malzeme adı → TL/kg fiyat dict"}
            },
            "required": ["cari_adi", "parasut_irsaliye_id", "toplanan_fiyatlar"]
        }
    },
    {
        "name": "irsaliye_faturalandir",
        "description": "Bir firmanın bekleyen irsaliyelerini listeler. Kullanıcı seçim yapınca sadece o irsaliye için fiyat sor, sonra fatura_kes_waybill çağır.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cari_adi": {"type": "string", "description": "Cari/firma adı"}
            },
            "required": ["cari_adi"]
        }
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


# ── TOOL SONUÇLARINI KIRP ─────────────────────────────────────────────

def _kirp_cari_listesi(cariler: list) -> list:
    """Cari listesinden sadece id ve ad döndür — bakiye/adres token harcar."""
    return [{"id": c["id"], "ad": c["ad"]} for c in cariler]


def _kirp_hesap_ozeti(ozet: dict) -> dict:
    """hesap_ozeti'nden cari detay listesini çıkar, sadece sayısal özet kalsın."""
    return {
        "toplam_cari": ozet.get("toplam_cari", 0),
        "toplam_alacak": ozet.get("toplam_alacak", 0),
        "toplam_borclu": ozet.get("toplam_borclu", 0),
    }


def _kirp_irsaliye_listesi(irsaliyeler: list) -> list:
    """İrsaliye listesinden kalem detaylarını kırp."""
    return [
        {
            "id": i["id"],
            "tarih": i.get("tarih", "-"),
            "aciklama": i.get("aciklama", "-")[:60],  # uzun açıklamayı kırp
            "toplam_kg": i.get("toplam_kg", 0),
        }
        for i in irsaliyeler
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
            return json.dumps(_kirp_cari_listesi(cariler), ensure_ascii=False)

        elif tool_name == "cari_detay_getir":
            cari_adi = tool_input.get("cari_adi", "")
            cari_id = cari_id_bul(cari_adi)
            if not cari_id:
                return f"'{cari_adi}' adlı cari bulunamadı."
            return json.dumps(cari_detay_getir(cari_id), ensure_ascii=False)

        elif tool_name == "cari_faturalari_getir":
            cari_adi = tool_input.get("cari_adi", "")
            limit = min(tool_input.get("limit", 5), 10)  # max 10
            cari_id = cari_id_bul(cari_adi)
            if not cari_id:
                return f"'{cari_adi}' adlı cari bulunamadı."
            return json.dumps(cari_faturalari_getir(cari_id, limit), ensure_ascii=False)

        elif tool_name == "cari_irsaliyeleri_getir":
            cari_adi = tool_input.get("cari_adi", "")
            limit = min(tool_input.get("limit", 5), 10)  # max 10
            cari_id = cari_id_bul(cari_adi)
            if not cari_id:
                return f"'{cari_adi}' adlı cari bulunamadı."
            sonuc = cari_irsaliyeleri_getir(cari_id, limit)
            return json.dumps(sonuc, ensure_ascii=False)

        elif tool_name == "acik_faturalar_getir":
            return json.dumps(acik_faturalar_getir(), ensure_ascii=False)

        elif tool_name == "hesap_ozeti_getir":
            ozet = hesap_ozeti_getir()
            return json.dumps(_kirp_hesap_ozeti(ozet), ensure_ascii=False)

        elif tool_name == "fatura_kes_waybill":
            from app.parasut.client import cari_id_bul as _cid, parasut_post as _post
            from app.core.config import PARASUT_COMPANY_ID as _CID, PARASUT_HURDA_PRODUCT_ID as _PROD
            from datetime import datetime as _dt

            cari_adi           = tool_input.get("cari_adi", "")
            parasut_irsaliye_id = tool_input.get("parasut_irsaliye_id")
            agirlik_kg         = tool_input.get("agirlik_kg", 0)
            malzemeler         = tool_input.get("malzemeler", {})
            toplanan_fiyatlar  = tool_input.get("toplanan_fiyatlar", {})

            contact_id = _cid(cari_adi)
            if not contact_id:
                return json.dumps({"basarili": False, "hata": f"{cari_adi} carisi bulunamadı."})

            bugun = _dt.now().strftime("%Y-%m-%d")

            if malzemeler and len(toplanan_fiyatlar) > 1:
                detaylar = []
                for malzeme_adi, fiyat in toplanan_fiyatlar.items():
                    kg = malzemeler.get(malzeme_adi, 0)
                    if kg <= 0:
                        continue
                    detaylar.append({
                        "type": "sales_invoice_details",
                        "attributes": {
                            "quantity": kg,
                            "unit_price": str(float(fiyat)),
                            "vat_rate": 0,
                            "description": f"{malzeme_adi} — {kg:,} kg"
                        },
                        "relationships": {
                            "product": {"data": {"type": "products", "id": str(_PROD)}}
                        }
                    })
            else:
                birim_fiyat = float(list(toplanan_fiyatlar.values())[0]) if toplanan_fiyatlar else 0
                kg = float(agirlik_kg) if agirlik_kg else float(sum(malzemeler.values())) if malzemeler else 0
                detaylar = [{
                    "type": "sales_invoice_details",
                    "attributes": {
                        "quantity": kg,
                        "unit_price": str(birim_fiyat),
                        "vat_rate": 0,
                        "description": f"Hurda — {kg:,.0f} kg"
                    },
                    "relationships": {
                        "product": {"data": {"type": "products", "id": str(_PROD)}}
                    }
                }]

            istek = {
                "data": {
                    "type": "sales_invoices",
                    "attributes": {
                        "item_type": "invoice",
                        "description": f"Hurda Alım — İrsaliye {parasut_irsaliye_id}",
                        "issue_date": bugun,
                        "due_date": bugun,
                        "currency": "TRL"
                    },
                    "relationships": {
                        "contact": {"data": {"type": "contacts", "id": str(contact_id)}},
                        "details": {"data": detaylar},
                        "shipment_document": {"data": {"type": "shipment_documents", "id": str(parasut_irsaliye_id)}}
                    }
                }
            }

            try:
                sonuc = _post(f"/{_CID}/sales_invoices", istek)
                fatura_id = sonuc["data"]["id"]
                logger.info(f"✅ İrsaliye bazlı fatura kesildi: {fatura_id}")
                return json.dumps({"basarili": True, "fatura_no": fatura_id}, ensure_ascii=False)
            except Exception as e:
                # irsaliye bağlantısı olmadan tekrar dene
                logger.warning(f"İrsaliye bağlantılı fatura başarısız, bağlantısız deneniyor: {e}")
                istek["data"]["relationships"].pop("shipment_document", None)
                try:
                    sonuc = _post(f"/{_CID}/sales_invoices", istek)
                    fatura_id = sonuc["data"]["id"]
                    return json.dumps({"basarili": True, "fatura_no": fatura_id, "uyari": "irsaliye_baglantisi_kurulamadi"}, ensure_ascii=False)
                except Exception as e2:
                    return json.dumps({"basarili": False, "hata": str(e2)}, ensure_ascii=False)

        elif tool_name == "irsaliye_faturalandir":
            from app.parasut.client import cari_bekleyen_irsaliyeleri_getir
            cari_adi = tool_input.get("cari_adi", "")
            irsaliyeler = cari_bekleyen_irsaliyeleri_getir(cari_adi)
            if not irsaliyeler:
                return f"{cari_adi} için Paraşüt'te irsaliye bulunamadı."
            return json.dumps({
                "irsaliyeler": _kirp_irsaliye_listesi(irsaliyeler),
                "cari_adi": cari_adi,
                "talimat": "Listele, kullanıcı seçsin, seçilen için fiyat sor, fatura_kes_waybill çağır."
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
    """Sadece bugünün özet sayılarını döndür — fiş listesi yok, token tasarrufu."""
    from app.core.database import WeighTicket, TicketStatus
    bugun = date.today()
    try:
        bugun_fisler = db.query(WeighTicket).filter(
            WeighTicket.created_at >= bugun.strftime("%Y-%m-%d")
        ).all()

        bugun_kg  = sum(t.agirlik_kg or 0 for t in bugun_fisler)
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


def _session_kaydet(db, session_id: str, isim: str, gecmis: list):
    from app.core.database import ChatSession
    try:
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        gecmis_json = json.dumps(gecmis, ensure_ascii=False)
        if session:
            session.isim  = isim
            session.gecmis = gecmis_json
        else:
            session = ChatSession(session_id=session_id, isim=isim, gecmis=gecmis_json)
            db.add(session)
        db.commit()
    except Exception as e:
        logger.error(f"Session kaydedilemedi: {e}")
        db.rollback()


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


# ── ANA FONKSİYON ─────────────────────────────────────────────────────

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

    isim      = session.isim or "Kullanıcı"
    gecmis    = json.loads(session.gecmis) if session.gecmis else []
    db_context = db_context_hazirla(db)

    sistem_promptu = f"""Sen Profaix muhasebe asistanısın. Hurda metal kantar takip ve muhasebe sistemi.
Kullanıcı: {isim} | {db_context}

KURALLAR:
- Bakiye/fatura/irsaliye → tool kullan, DB'ye bakma
- Fatura keserken tek irsaliye işle, onay almadan diğerine geçme
- Bilmediğini uydurma
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

        # Tool use döngüsü
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

            # Geçmişe sadece serializable hali ekle
            gecmis.append({"role": "assistant", "content": [
                {"type": b.type, **( {"text": b.text} if b.type == "text" else
                                     {"id": b.id, "name": b.name, "input": b.input} )}
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

        # Geçmişe sadece text mesajları kaydet (tool içerikleri atılır)
        gecmis_kayit = [m for m in gecmis if isinstance(m.get("content"), str)]
        gecmis_kayit.append({"role": "assistant", "content": cevap})
        if len(gecmis_kayit) > MAX_GECMIS:
            gecmis_kayit = gecmis_kayit[-MAX_GECMIS:]

        _session_kaydet(db, gonderen, isim=isim, gecmis=gecmis_kayit)
        logger.info(f"🤖 Cevap ({isim}): {cevap[:60]}...")
        return cevap

    except Exception as e:
        logger.error(f"Claude API hatası: {e}")
        return "❌ Şu an cevap veremiyorum, lütfen tekrar deneyin."