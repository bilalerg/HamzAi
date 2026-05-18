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

import os as _os
# OCR modeli — env'den override edilebilir (örn. claude-opus-4-...)
OCR_MODEL      = _os.getenv("OCR_MODEL", "claude-sonnet-4-20250514")
OCR_MAX_TOKENS = 2048  # chain-of-thought için artırıldı
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


OCR_SYSTEM = """Sen Türkiye hurda/geri dönüşüm sektörüne ait kantar fişlerini okuyan profesyonel bir OCR motorusun.

ÇOK ÖNEMLİ: 2 AŞAMALI ÇIKTI VERECEKSİN.

═══ AŞAMA 1 — TASVİR ═══
Önce fişin görsel layout'unu açıkla. Sadece GERÇEKTEN GÖRDÜĞÜN satırları yaz.
Her etiket ve karşısındaki değeri yan yana yaz. Bu aşama Markdown DEĞİL, düz metin.

═══ AŞAMA 2 — JSON ═══
Aşama 1'deki gözleme göre JSON çıktısı ver.
Aşama 1'de görmediğin değeri ASLA JSON'a koyma — null bırak.

ÇIKTI FORMATI (KESİN):
LAYOUT:
- [üst başlık]: [değer]
- [etiket]: [değer]
- ...

JSON:
{"plaka":...,"fis_no":...,"net_tartim_kg":...,"fire_kg":...,"tarih":...,"firma":...,"irsaliye_no":...,"kantar_no":...,"malzemeler":{}}

UYDURMA YASAĞI:
- Tipik plaka örnekleri ("34BEG354", "06FH8714") veya tipik fiş no'lar ("1234567", "15150") UYDURMA
- Önceki fişlerden hiçbir bilgi HATIRLAMA — her görsel bağımsız
- Aşama 1'de tasvir etmediğin alanı Aşama 2'de SADECE null yapabilirsin
"""

OCR_PROMPT = """Bu fişi 2 aşamada oku.

⚠️ KRİTİK LAYOUT UYARISI:
Türk kantar fişleri tipik olarak SOL kolonda ETİKET (örn: "Plaka :", "Firma :")
ve SAĞ kolonda DEĞER (örn: "37 LK 081", "YEŞIL IZABE") içerir.
Etiket ve değer arasında 5-15 karakter BOŞLUK olabilir — aldanma, aynı satırdaki
sağ taraftaki yazıyı OKU. Etiket soluk görünse bile sağdaki değer okunaklıdır.

═══ AŞAMA 1 — LAYOUT TASVİRİ ═══
Aşağıdaki sırada GÖZLEM YAP — her satır için sol kolon (etiket) + sağ kolon (değer)
yapısını dikkate al. Eğer etiket soluk ama sağ tarafta yazı varsa o yazıyı oku:

a) ÜST BAŞLIK: Fişin en tepesinde hangi şirket adı yazıyor? (Bu KANTARIN/alıcı fabrikanın adıdır)
b) "Firma :" SATIRI: Bu satırın SAĞ TARAFINDA ne yazıyor? (Genelde fişin üst-orta kısmında)
   Örnek: "Firma : YEŞIL IZABE GERİ DÖNÜŞÜM NAK" → değer="YEŞIL IZABE GERİ DÖNÜŞÜM NAK"
c) "Nakliye Firması:" SATIRI: sağ tarafında ne yazıyor? (firma DEĞİL, sadece kaydet)
d) "Ad Soyad:" SATIRI: sağ tarafında ne yazıyor? (firma DEĞİL — şoför)
e) PLAKA SATIRI: "Araç-Dorse Plaka :" veya "Plaka :" etiketinin SAĞ TARAFINDA ne yazıyor?
   Örnek: "Araç - Dorse Plaka : 37 LK 081 - 34 BSE 559" → değer="37 LK 081 - 34 BSE 559"
   ⚠️ Plaka tipik biçim: 2 rakam + 1-3 harf + 2-4 rakam (örn: 37LK081, 06FH8714)
f) BRÜT değeri: kaç kg?
g) DARA değeri: kaç kg?
h) NET değeri: kaç kg? (fişte NET satırı varsa o değer, yoksa BRÜT-DARA hesabı)
i) Tarih: "Geliş Tarihi", "Tarih" alanı? (GG.AA.YYYY)
j) Fiş ID veya Fiş No: "Id.", "Fiş No", "No" etiketli alan? Genelde fişin ALT kısmında
   küçük yazıyla yazılı (örn: "Id.130002187968")
k) Mal Cinsi: ne yazıyor?

⚠️ Bir alan için "görülmüyor" yazma hakkın var, ama önce DİKKATLİCE bak.
Sol kolon etiketi flu olsa bile sağ kolondaki değer çoğu zaman okunaklıdır.

═══ AŞAMA 2 — JSON ═══
Yukarıdaki AŞAMA 1 gözleminize göre JSON'u doldur.

Kurallar:
- plaka: Aşama 1-e'de İLK plakayı al, boşluk/tire sil, büyük harf yap → "37LK081"
- fis_no: Aşama 1-j'deki numara
- firma: Aşama 1-b'deki TEDARIKÇİ adı (Firma: etiketli). YOKSA → Aşama 1-a'daki üst başlık
  ⚠️ Nakliye Firması (1-c) veya Ad Soyad (1-d) ASLA firma değildir
- net_tartim_kg: Aşama 1-h'deki NET değer (tam sayı, virgül/nokta yok)
- fire_kg: "Fire" veya "Tenzil" satırı yoksa 0
- tarih: Aşama 1-i'den, GG.AA.YYYY formatı
- malzemeler: Mal Cinsi tek satırsa {}, çoklu liste varsa {"isim": kg, ...}

ÇIKTI ÖRNEĞİ (yapın bu formatta, başka şey eklemeyin):

LAYOUT:
- ÜST BAŞLIK: KAPTAN DEMİR ÇELİK END.A.Ş
- Firma: YEŞIL IZABE GERİ DÖNÜŞÜM NAK
- Nakliye Firması: İMAJ DENIZ
- Ad Soyad: MEHMET ÖZDEMİR
- Plaka: 37 LK 081 - 34 BSE 559
- BRÜT: 33000 Kg
- DARA: 17850 Kg
- NET: 15150 Kg
- Tarih: 12.03.2026
- Id.: 130002187968
- Mal Cinsi: YERLİ HURDA

JSON:
{"plaka":"37LK081","fis_no":"130002187968","net_tartim_kg":15150,"fire_kg":0,"tarih":"12.03.2026","firma":"YEŞIL IZABE GERİ DÖNÜŞÜM NAK","irsaliye_no":null,"kantar_no":null,"malzemeler":{}}

ŞİMDİ asıl fişe geçelim. Aynı formatta cevap ver."""


def _fotograf_to_base64(fotograf_yolu: str) -> tuple[str, str, dict]:
    """
    Fotoğrafı base64'e çevirir + Claude'un daha iyi okuyabilmesi için
    pre-processing uygular:
      1. Küçük fotoğrafları upscale (LANCZOS)
      2. UNSHARP MASK ile keskinleştirme
      3. Kontrast +25%
      4. Sharpness +50%
    """
    meta = {"width": 0, "height": 0, "kb": 0, "dusuk_kalite": False}
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        import io as _io
        with Image.open(fotograf_yolu) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            orig_w, orig_h = img.size
            # 1. Çok küçük gelen fotoğrafları AGRESİF upscale et — Claude vision
            #    yüksek çözünürlükte etiketli alanları çok daha iyi okuyor.
            #    Hedef: en az 2200px (uzun kenar)
            if max(orig_w, orig_h) < 2000:
                oran = 2400 / max(orig_w, orig_h)
                yeni_boyut = (int(orig_w * oran), int(orig_h * oran))
                img = img.resize(yeni_boyut, Image.LANCZOS)
                logger.info(f"Fotoğraf upscale edildi: {orig_w}x{orig_h} → {img.size}")
            else:
                img.thumbnail((2400, 2400), Image.LANCZOS)

            # 2. Unsharp mask — daha agresif (etiket/değer ayrımı için)
            try:
                img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=2))
            except Exception as fe:
                logger.warning(f"UnsharpMask atlandı: {fe}")

            # 3. Kontrast +50% (etiket görünürlüğü için)
            try:
                img = ImageEnhance.Contrast(img).enhance(1.5)
            except Exception as ce:
                logger.warning(f"Contrast enhance atlandı: {ce}")

            # 4. Sharpness +100%
            try:
                img = ImageEnhance.Sharpness(img).enhance(2.0)
            except Exception as se:
                logger.warning(f"Sharpness enhance atlandı: {se}")

            # 5. Brightness küçük artış — soluk metinler için
            try:
                img = ImageEnhance.Brightness(img).enhance(1.05)
            except Exception as be:
                logger.warning(f"Brightness enhance atlandı: {be}")

            buffer = _io.BytesIO()
            img.save(buffer, format="JPEG", quality=95)
            data = base64.b64encode(buffer.getvalue()).decode("utf-8")
            meta["width"]  = img.size[0]
            meta["height"] = img.size[1]
            meta["kb"]     = len(data) // 1024
            if max(orig_w, orig_h) < 800 or meta["kb"] < 60:
                meta["dusuk_kalite"] = True
            logger.info(
                f"Fotoğraf hazırlandı: orig=({orig_w}x{orig_h}) "
                f"final={img.size} | {meta['kb']}KB base64 | "
                f"preprocessing=unsharp+contrast+sharpness | "
                f"düşük_kalite={meta['dusuk_kalite']}"
            )
        return data, "image/jpeg", meta
    except ImportError:
        with open(fotograf_yolu, "rb") as f:
            raw = f.read()
            data = base64.standard_b64encode(raw).decode("utf-8")
        uzanti = Path(fotograf_yolu).suffix.lower()
        media_type = "image/jpeg" if uzanti in [".jpg", ".jpeg"] else "image/png"
        meta["kb"] = len(data) // 1024
        return data, media_type, meta


def _parse_json(metin: str) -> dict:
    """Chain-of-thought formatlı yanıttan JSON kısmını çıkarır."""
    metin = metin.strip()
    # Önce "JSON:" markeri sonrası kısma bak — chain-of-thought formatı
    json_marker = re.search(r"JSON\s*:\s*\n?(\{.*?\})\s*$", metin, re.DOTALL)
    if json_marker:
        return json.loads(json_marker.group(1))
    # Code fence (```json {...} ```)
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", metin, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    # Son çare: en sondaki { ... }
    obj_matches = list(re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", metin, re.DOTALL))
    if obj_matches:
        # En son JSON bloğunu al (chain-of-thought'ta JSON en sonda)
        return json.loads(obj_matches[-1].group())
    # Greedy son çare
    obj = re.search(r"\{.*\}", metin, re.DOTALL)
    if obj:
        return json.loads(obj.group())
    raise ValueError(f"JSON bulunamadı: {metin[:100]}")


def _layout_kisminı_cikar(metin: str) -> str:
    """Chain-of-thought yanıttan LAYOUT kısmını çıkarır (doğrulama için)."""
    metin = metin.strip()
    layout_match = re.search(r"LAYOUT\s*:\s*\n?(.*?)\n\s*JSON\s*:", metin, re.DOTALL | re.IGNORECASE)
    if layout_match:
        return layout_match.group(1).strip()
    return ""


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
    foto_base64, media_type, foto_meta = _fotograf_to_base64(fotograf_yolu)

    # Düşük kalite fotoğraflarda DİKKATLİ OKU mesajı (paranoya yaratmadan)
    ek_uyari = ""
    if foto_meta.get("dusuk_kalite"):
        ek_uyari = (
            "\n\nNot: Bu fotoğraf nispeten düşük çözünürlüklü. "
            "Yine de tüm görebildiğin değerleri DİKKATLİCE oku ve doldur. "
            "Sadece tek karakter dahi göremediğin alanları null bırak."
        )

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
                {"type": "text", "text": OCR_PROMPT + ek_uyari}
            ]
        }]
    )

    claude_cevap = response.content[0].text
    logger.info(f"OCR ham cevap: {claude_cevap}")

    veri = _parse_json(claude_cevap)
    layout_text = _layout_kisminı_cikar(claude_cevap)

    # Kritik alanların tamamı null → fotoğraf okunamadı
    plaka_raw  = veri.get("plaka")
    tartim_raw = veri.get("net_tartim_kg")
    firma_raw  = veri.get("firma")
    if not plaka_raw and not tartim_raw and not firma_raw:
        raise ValueError(
            "Fişteki bilgiler okunamadı. Fotoğrafı daha net çekip tekrar deneyin."
        )

    # ── HALÜSİNASYON DOĞRULAMA: Plaka layout tasvirinde geçiyor mu? ──
    # Eğer LAYOUT bölümünde plaka'nın karakterleri yoksa, Claude büyük
    # ihtimalle uyduruyor. Plakayı null'a düşür ve kullanıcı uyarısı ekle.
    plaka_uydurma_uyari = None
    if plaka_raw and layout_text:
        plaka_normalized = re.sub(r'[\s\-]', '', str(plaka_raw)).upper()
        # Plakanın "anlamlı" bir alt parçası (en az 4 karakter) layout'ta geçmeli
        layout_upper = layout_text.upper().replace(" ", "").replace("-", "")
        if len(plaka_normalized) >= 6:
            # En az 4 ardışık karakter LAYOUT'ta var mı?
            bulundu = False
            for baslangic in range(len(plaka_normalized) - 3):
                if plaka_normalized[baslangic:baslangic+4] in layout_upper:
                    bulundu = True
                    break
            if not bulundu:
                logger.warning(
                    f"⚠️ HALÜSİNASYON ŞÜPHESİ: plaka='{plaka_normalized}' "
                    f"LAYOUT tasvirinde GEÇMİYOR — null'a düşürüldü"
                )
                plaka_uydurma_uyari = (
                    f"⚠️ Plaka okunamadı ('{plaka_raw}' tespit edildi ama doğrulanamadı). "
                    f"Lütfen kontrol edip Düzenle'den girin."
                )
                plaka_raw = None
                veri["plaka"] = None

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
    if plaka_uydurma_uyari:
        plaka_uyari = plaka_uydurma_uyari
    elif temiz_plaka and not plaka_gecerli:
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
    Tüm denemeler temperature=0.0 ile yapılır (deterministik, halüsinasyon riskini azaltır).
    Tüm denemeler başarısız olursa hata döndürür.
    """
    logger.info(f"OCR başlıyor: {fotograf_yolu} | model={OCR_MODEL}")
    son_hata = None

    for deneme in range(OCR_MAX_RETRY):
        # Her zaman temperature=0.0 — halüsinasyon riskini en aza indirir.
        # Önceki kodda retry'da 0.2'ye çıkıyordu, bu YANLIŞ idi: daha yaratıcı
        # = daha çok uydurma. Şimdi düzeltildi.
        try:
            if deneme > 0:
                logger.info(f"OCR yeniden deneniyor ({deneme + 1}/{OCR_MAX_RETRY})")
            return _claude_fis_oku_once(fotograf_yolu, temperature=0.0)
        except Exception as e:
            son_hata = e
            logger.warning(f"OCR deneme {deneme + 1} başarısız: {e}")

    logger.error(f"OCR tüm denemeler başarısız: {son_hata}")
    return FisVerisi(
        hata=(
            f"Fiş okunamadı: {son_hata}. "
            "Fotoğrafı net ve aydınlık çekip tekrar deneyin."
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