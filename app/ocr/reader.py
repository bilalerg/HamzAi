# reader.py — OCR Engine

import anthropic
import asyncio
import base64
import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict
from datetime import datetime

from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

OCR_MODEL      = "claude-sonnet-4-20250514"
OCR_MAX_TOKENS = 1024
OCR_MAX_RETRY  = 2  # Başarısız OCR'da kaç kez tekrar denensin


@dataclass
class FisVerisi:
    plaka:            Optional[str]           = None
    fis_no:           Optional[str]           = None
    net_agirlik_kg:   Optional[int]           = None
    net_tartim_kg:    Optional[int]           = None
    fire_kg:          Optional[int]           = None
    tarih:            Optional[str]           = None
    firma:            Optional[str]           = None
    irsaliye_no:      Optional[str]           = None
    kantar_no:        Optional[str]           = None
    malzemeler:       Optional[Dict[str,int]] = None
    claude_ham_cikti: Optional[str]           = None
    hata:             Optional[str]           = None
    plaka_uyari:      Optional[str]           = None


OCR_SYSTEM = "Sen sadece JSON üreten bir API'sin. Yanıtın SADECE '{' ile başlamalı ve '}' ile bitmelidir. Hiçbir açıklama, başlık veya markdown ekleme."

OCR_PROMPT = """Kantar fişi fotoğrafı. Sadece JSON döndür, başka hiçbir şey yazma.

{"plaka":"ana araç plakası","fis_no":"fiş no","net_tartim_kg":SAYI,"fire_kg":SAYI_YA_DA_0,"tarih":"GG.AA.YYYY","firma":"fabrika adı","irsaliye_no":null,"kantar_no":null,"malzemeler":{}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ADIM 1 — FİŞ FORMATINI TESPİT ET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Aşağıdaki 4 formattan hangisi olduğunu belirle, sonra o formata göre oku:

FORMAT A — TOSCELİK/TOSYALI KANTAR
Tanıma: Başlıkta "TOSCELIK", "TOSÇELIK" veya "TOSYALI KANTAR" yazar VE başlıkta "KAPTAN" veya "KROMAN" YOKTUR.
- fis_no     → "Yukleme / Bosaltma Fis No" satırındaki sayı
- plaka      → "Araç Plakası" satırı
- tarih      → "Giris Tarihi" satırındaki tarih (GG.AA.YYYY)
- firma      → "Firma Adı" satırı (UHT, Yeşil İzabe gibi tedarikçi firma — doğru bu)
              ⚠️ firma hiçbir zaman "TOSCELIK" veya "TOSYALI" olamaz — o kantarın adı
- net_tartim_kg → "Net Tartım" satırı
- fire_kg    → "6-) Fire" satırındaki değer (0 ise 0 yaz)
- malzemeler → "1-) DKP", "2-) Topsis", "3-) Bonus", "4-) 1. Grup", "5-) Karma" satırları
               SADECE 0'dan büyük olanları al, Fire satırını ASLA ekleme
               ⚠️ malzeme toplamı fire_kg dahil net_tartim_kg'ı AŞAMAZ
               Örnek: net=15150, fire=800 → malzeme toplamı max 15150 olabilir
               {"Karma": 15700, "Fire": 800} → YANLIŞ (fire eklendi + toplam net'i geçiyor)
               {"Karma": 15150} → DOĞRU

FORMAT B — KROMAN E-POSTA BİLDİRİMİ
Tanıma: Gönderen "HURDA@kromancelik.com.tr" veya üstte "Fişno:" başlığı var, tablo formatında.
- fis_no     → "Fiş No" satırı
- plaka      → "Plaka No" satırı
- tarih      → "Tarih" satırı
- firma      → Her zaman sabit: "KROMAN ÇELİK" yaz
- net_tartim_kg → "Net Tartım" satırındaki değer (ham, tenzil düşülmemiş)
- fire_kg    → "Tenzil" satırındaki değer
- malzemeler → Alt tablodaki "Hurda Tipi | Miktar | Tenz.Miktar" satırları
               Net miktar = Miktar - Tenz.Miktar
               Örnek: HURDA YERLİ EXTRA | 4.711 | 84 → {"HURDA YERLİ EXTRA": 4627}

FORMAT C — KAPTAN DEMİR ÇELİK
Tanıma: Başlıkta veya fişin herhangi bir yerinde "KAPTAN DEMİR" veya "KAPTAN ÇELİK" yazar.
         Bu format tanınırsa BAŞKA FORMAT DENEME — FORMAT C kesindir.
- fis_no     → "Id:" satırındaki numara
- plaka      → "Araç - Dorse Plaka" satırında TİRE veya BÖLÜ ile ayrılmış iki plaka var
               SADECE İLK (soldaki) plakayı al → "59 KB 753 - 59 AFM 863" → "59KB753"
- tarih      → "Geliş - Gidiş Tarihi" satırındaki ilk tarih
- firma      → Sabit: "KAPTAN DEMİR ÇELİK" yaz — fişte başka isim görsen de değiştirme
- net_tartim_kg → "NET" satırındaki değer
- fire_kg    → 0
- malzemeler → "Mal Cinsi" satırındaki değer, ağırlık = net_tartim_kg

FORMAT D — DİĞER/BİLİNMEYEN FORMAT
- fis_no     → Fiş/slip/makbuz numarası
- plaka      → Araç plakası — iki plaka varsa sadece ilki
- tarih      → Tarihi bul
- firma      → Fişin EN ÜSTÜNDE büyük harfle yazan şirket/fabrika adı
- net_tartim_kg → NET veya NET TARTIM satırı
- fire_kg    → Fire/Tenzil satırı, yoksa 0
- malzemeler → Malzeme tablosu varsa doldur, yoksa {}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GENEL KURALLAR (TÜM FORMATLAR)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PLAKA FORMAT: boşluk ve tire olmadan büyük harf → 06FH8714, 34BEG354, 59KB753
TÜM KG TAM SAYI: nokta ve virgül OLMADAN → 15150 ✓  15.150 ✗  15,150 ✗
NET AĞIRLIK KONTROLÜ: net_tartim_kg - fire_kg = gerçek net (negatif olamaz)
TARİH FORMAT: GG.AA.YYYY → 02.04.2026"""


def _fotograf_to_base64(fotograf_yolu: str) -> tuple[str, str]:
    try:
        from PIL import Image
        import io as _io
        with Image.open(fotograf_yolu) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.thumbnail((1500, 1500), Image.LANCZOS)
            buffer = _io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            data = base64.b64encode(buffer.getvalue()).decode("utf-8")
            logger.info(f"Fotoğraf boyutlandırıldı: {img.size} | {len(data)//1024}KB base64")
        return data, "image/jpeg"
    except ImportError:
        with open(fotograf_yolu, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")
        uzanti = Path(fotograf_yolu).suffix.lower()
        media_type = "image/jpeg" if uzanti in [".jpg", ".jpeg"] else "image/png"
        return data, media_type


def _parse_json(metin: str) -> dict:
    metin = metin.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", metin, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    obj = re.search(r"\{.*\}", metin, re.DOTALL)
    if obj:
        return json.loads(obj.group())
    raise ValueError(f"JSON bulunamadı: {metin[:100]}")


def _parse_kg(deger) -> int:
    if deger is None:
        return 0
    try:
        f = float(str(deger).replace(",", "."))
        if f != int(f) and f < 1000:
            sonuc = int(round(f * 1000))
            logger.warning(f"⚠️ Ondalıklı kg düzeltildi: {deger} → {sonuc}")
            return sonuc
        return int(f)
    except (ValueError, TypeError):
        logger.error(f"kg parse hatası: {deger}")
        return 0


def _claude_fis_oku_once(fotograf_yolu: str, temperature: float = 0.0) -> FisVerisi:
    """Tek OCR denemesi."""
    foto_base64, media_type = _fotograf_to_base64(fotograf_yolu)

    response = _client.messages.create(
        model=OCR_MODEL,
        max_tokens=OCR_MAX_TOKENS,
        temperature=temperature,
        system=OCR_SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": foto_base64}
                },
                {"type": "text", "text": OCR_PROMPT}
            ]
        }]
    )

    claude_cevap = response.content[0].text
    logger.info(f"OCR ham cevap: {claude_cevap}")

    veri = _parse_json(claude_cevap)

    net_tartim  = _parse_kg(veri.get("net_tartim_kg"))
    fire        = _parse_kg(veri.get("fire_kg"))
    net_agirlik = net_tartim - fire

    if net_agirlik < 0:
        logger.warning(f"⚠️ Net ağırlık negatif (tartım={net_tartim}, fire={fire}), fire sıfırlanıyor")
        net_agirlik = net_tartim
        fire = 0

    if net_agirlik == 0 or net_tartim == 0:
        raise ValueError("Fişteki ağırlık değeri okunamadı veya sıfır.")

    FIRE_KELIMELERI = {'fire', 'tenzil', 'fire kg', 'kayıp', 'kayip', 'hurda fire'}
    malzemeler_ham = veri.get("malzemeler") or {}
    malzemeler = {
        k: _parse_kg(v) for k, v in malzemeler_ham.items()
        if v and _parse_kg(v) > 0 and k.strip().lower() not in FIRE_KELIMELERI
    }
    # Malzeme toplamı net ağırlığı geçemez — geçerse net ağırlığa normalize et
    if malzemeler and net_agirlik > 0:
        toplam_malzeme = sum(malzemeler.values())
        if toplam_malzeme > net_agirlik:
            logger.warning(f"⚠️ Malzeme toplamı ({toplam_malzeme}) net ağırlığı ({net_agirlik}) geçiyor, normalize ediliyor")
            oran = net_agirlik / toplam_malzeme
            malzemeler = {k: max(1, int(v * oran)) for k, v in malzemeler.items()}

    ham_plaka = veri.get("plaka")
    temiz_plaka = re.sub(r'[\s\-]', '', ham_plaka).upper() if ham_plaka else None

    plaka_gecerli = bool(re.match(r'^\d{2}[A-Z]{1,3}\d{2,4}$', temiz_plaka)) if temiz_plaka else False
    if temiz_plaka and not plaka_gecerli:
        logger.warning(f"⚠️ GEÇERSİZ PLAKA FORMATI: '{temiz_plaka}'")

    plaka_uyari = None
    if temiz_plaka and not plaka_gecerli:
        plaka_uyari = f"⚠️ Plaka '{temiz_plaka}' geçersiz format — lütfen kontrol edin."

    logger.info(f"OCR sonuç — plaka={temiz_plaka} tartım={net_tartim}kg fire={fire}kg net={net_agirlik}kg firma={veri.get('firma')}")

    return FisVerisi(
        plaka            = temiz_plaka,
        fis_no           = str(veri.get("fis_no")) if veri.get("fis_no") else None,
        net_tartim_kg    = net_tartim,
        fire_kg          = fire,
        net_agirlik_kg   = net_agirlik,
        tarih            = veri.get("tarih"),
        firma            = veri.get("firma"),
        irsaliye_no      = veri.get("irsaliye_no"),
        kantar_no        = veri.get("kantar_no"),
        malzemeler       = malzemeler or None,
        claude_ham_cikti = claude_cevap,
        plaka_uyari      = plaka_uyari
    )


def claude_fis_oku(fotograf_yolu: str) -> FisVerisi:
    """
    OCR işlemini çalıştırır.
    Başarısız olursa OCR_MAX_RETRY kez tekrar dener (artan temperature ile).
    Tüm denemeler başarısız olursa hata döndürür.
    """
    logger.info(f"OCR başlıyor: {fotograf_yolu} | model={OCR_MODEL}")
    son_hata = None

    for deneme in range(OCR_MAX_RETRY):
        # İlk deneme temperature=0.0 (deterministik), sonrakiler biraz higher
        temperature = 0.0 if deneme == 0 else 0.2
        try:
            if deneme > 0:
                logger.info(f"OCR yeniden deneniyor ({deneme + 1}/{OCR_MAX_RETRY}), temperature={temperature}")
            return _claude_fis_oku_once(fotograf_yolu, temperature=temperature)
        except Exception as e:
            son_hata = e
            logger.warning(f"OCR deneme {deneme + 1} başarısız: {e}")

    logger.error(f"OCR tüm denemeler başarısız: {son_hata}")
    return FisVerisi(
        hata=(
            "Fişteki ağırlık değeri okunamadı veya sıfır. "
            "Fotoğrafı daha net çekip tekrar deneyin."
        )
    )


# ── ASYNC WRAPPER — FastAPI event loop'unu bloklamaz ──────────────────

async def claude_fis_oku_async(fotograf_yolu: str) -> FisVerisi:
    """
    Senkron OCR işlemini thread pool'a taşır.
    FastAPI async endpoint'lerinden bu fonksiyon çağrılmalı.
    """
    return await asyncio.to_thread(claude_fis_oku, fotograf_yolu)


def fis_oku(fotograf_yolu: str) -> FisVerisi:
    """Senkron çağrı — background task veya senkron bağlamlar için."""
    fis = claude_fis_oku(fotograf_yolu)
    if fis.hata:
        logger.error(f"Fiş okunamadı: {fis.hata}")
    return fis


async def fis_oku_async(fotograf_yolu: str) -> FisVerisi:
    """Async çağrı — FastAPI endpoint'lerinden kullanılmalı."""
    return await claude_fis_oku_async(fotograf_yolu)


def fis_db_kaydet(fis: FisVerisi, foto_path: str, db) -> object:
    from app.core.database import WeighTicket, TicketStatus

    fis_tarihi = None
    if fis.tarih:
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                fis_tarihi = datetime.strptime(fis.tarih, fmt)
                break
            except ValueError:
                continue

    ticket = WeighTicket(
        foto_path     = foto_path,
        plaka         = fis.plaka,
        fis_no        = fis.fis_no,
        agirlik_kg    = fis.net_agirlik_kg,
        net_tartim_kg = fis.net_tartim_kg,
        fire_kg       = fis.fire_kg or 0,
        firma         = fis.firma,
        fis_tarihi    = fis_tarihi,
        malzeme       = "hurda",
        malzemeler    = json.dumps(fis.malzemeler, ensure_ascii=False) if fis.malzemeler else None,
        ocr_ham_cikti = fis.claude_ham_cikti,
        status        = TicketStatus.OCR_TAMAMLANDI if not fis.hata else TicketStatus.HATA,
        hata_mesaji   = fis.hata
    )

    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    logger.info(
        f"✅ Fiş kaydedildi: id={ticket.id} plaka={ticket.plaka} "
        f"firma={ticket.firma} fis_no={ticket.fis_no} agirlik={ticket.agirlik_kg}kg"
    )
    return ticket