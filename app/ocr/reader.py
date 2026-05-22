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
OCR_MAX_TOKENS = 3072  # chain-of-thought + çok kalemli layout için artırıldı
OCR_MAX_RETRY  = 2  # Başarısız OCR'da kaç kez tekrar denensin


@dataclass
class FisVerisi:
    plaka:              Optional[str]           = None
    fis_no:             Optional[str]           = None
    net_agirlik_kg:     Optional[int]           = None
    net_tartim_kg:      Optional[int]           = None
    fire_kg:            Optional[int]           = None
    tarih:              Optional[str]           = None
    firma:              Optional[str]           = None
    firma_kaynak:       Optional[str]           = None   # "firma_satiri" | "ust_baslik" | None
    irsaliye_no:        Optional[str]           = None
    kantar_no:          Optional[str]           = None
    malzemeler:         Optional[Dict[str,int]] = None
    claude_ham_cikti:   Optional[str]           = None
    hata:               Optional[str]           = None
    plaka_uyari:        Optional[str]           = None
    firma_uyari:        Optional[str]           = None   # ust_baslik'ten alınmışsa uyarı
    # OCR güven skorları — 0.0-1.0 arası float
    plaka_confidence:   Optional[float]         = None
    agirlik_confidence: Optional[float]         = None
    fis_no_confidence:  Optional[float]         = None
    firma_confidence:   Optional[float]         = None
    genel_confidence:   Optional[float]         = None


OCR_SYSTEM = """Sen Türkiye hurda/geri dönüşüm sektörüne ait kantar fişlerini okuyan profesyonel bir OCR motorusun.

Sahada BİRÇOK FARKLI kantar yazılımı vardır; etiketler firmadan firmaya değişir ama hepsinde şu temel bilgiler bulunur: plaka, tartım ağırlıkları, firma, tarih, fiş no, malzeme. Etikete değil, ANLAMA odaklan.

ÇOK ÖNEMLİ: 2 AŞAMALI ÇIKTI VERECEKSİN.

═══ AŞAMA 1 — TASVİR ═══
Önce fişin görsel layout'unu açıkla. Sadece GERÇEKTEN GÖRDÜĞÜN satırları yaz.
Her etiket ve karşısındaki değeri yan yana yaz. Bu aşama Markdown DEĞİL, düz metin.

═══ AŞAMA 2 — JSON ═══
Aşama 1'deki gözleme göre JSON çıktısı ver.
Aşama 1'de görmediğin değeri ASLA JSON'a koyma — null bırak.

ÇIKTI FORMATI (KESİN):
LAYOUT:
- [etiket]: [değer]
- ...

JSON:
{"plaka":...,"plaka_confidence":0.0-1.0,"fis_no":...,"fis_no_confidence":0.0-1.0,"net_tartim_kg":...,"agirlik_confidence":0.0-1.0,"fire_kg":...,"tarih":...,"firma":...,"firma_kaynak":"firma_satiri"|"ust_baslik","firma_confidence":0.0-1.0,"irsaliye_no":...,"kantar_no":...,"malzemeler":{}}

⚠️ SAYI FORMATI — EN SIK YAPILAN HATA:
Türk fişlerinde nokta/virgül BİNLİK AYRAÇTIR, ondalık DEĞİL.
"17.550" = 17550 kg.  "48.540" = 48540 kg.  "1.000" = 1000 kg.  "32.900" = 32900 kg.
JSON'a TÜM ağırlıkları AYRAÇSIZ TAM SAYI yaz (17550 gibi). Hurda kg değerleri DAİMA tam sayıdır, asla 17.55 yazma.

Güven skoru (confidence) kuralları:
- 0.9-1.0: Net, tam okunabilir, formatı doğru
- 0.7-0.9: Okunabilir ama tek harf/rakam belirsiz olabilir
- 0.4-0.7: Kısmen okundu, tahmin var
- 0.0-0.4: Çok belirsiz, büyük ihtimalle hatalı

⚠️ DÜRÜSTLÜK KURALI — confidence skorunu ŞİŞİRME:
- Tek bir karakter belirsizse confidence 0.7-0.85 arasına düşür
- Sadece çok net okuduğun alanlara 0.9+ ver
- Plaka okurken sık karışan karakterlere dikkat: "7↔1", "L↔I", "B↔8", "O↔0", "Z↔2", "S↔5", "G↔6". Şüphe varsa 0.5-0.7 ver.

firma_kaynak ALANI (zorunlu — firma null değilse):
- "firma_satiri": Eğer firma değerini "Firma" / "Firma Adı" etiketli satırdan aldıysan
- "ust_baslik": Eğer Firma satırı YOKTU ve firma değerini fişin EN ÜSTÜNDEKİ büyük başlıktan aldın
  → Bu durumda firma_confidence MUTLAKA 0.3 veya altına düşür (üst başlık genelde KANTAR/fabrika firmasıdır, tedarikçi DEĞİL)

UYDURMA YASAĞI — KESİN KURAL:
- SADECE bu görselde net OKUDUĞUN değerleri yaz. Göremediğin bir alanı (plaka, fiş no,
  firma, tarih) TAHMİN ETME, benzer bir değer ÜRETME → null bırak.
- Bu istemde geçen hiçbir biçim açıklamasını, etiket adını ya da yer-tutucu ifadeyi
  DEĞER olarak çıktına KOPYALAMA. İstemdeki bir numara/plaka/firma cevabında yer almasın.
- Her görsel bağımsızdır — başka fişlerden bilgi taşıma.
- Aşama 1'de tasvir etmediğin alanı Aşama 2'de yalnızca null yapabilirsin.
- El yazısı notları ve imzaları YOK SAY.
"""

OCR_PROMPT = """Bu kantar fişini 2 aşamada oku.

⚠️ LAYOUT: Türk kantar fişleri SOL kolonda ETİKET, SAĞ kolonda DEĞER içerir.
Etiket ile değer arası 5-15 boşluk olabilir — aldanma, sağdaki yazıyı OKU.
Etiket soluk olsa bile sağdaki değer okunaklıdır. Bazı fişler 2 BLOKLUDUR
(sol blok + sağ blok yan yana) — ikisini de tara.

═══ AŞAMA 1 — LAYOUT TASVİRİ ═══
Sırayla GÖZLEM YAP. Görmediğini yazma.

a) ÜST BAŞLIK: En tepedeki büyük şirket adı. Bu KANTARIN/alıcı fabrikanın adıdır —
   tedarikçi DEĞİLDİR, firma alanına yazma.

b) FİRMA / TEDARİKÇİ: "Firma", "Firma Adı", "FİRMA" etiketinin SAĞINDA ne yazıyor?
   Sağındaki firma adını OLDUĞU GİBİ oku (Türkçe karaktersiz yazılmışsa öyle bırak).
   ⚠️ Bu satır BOŞSA firma=null bırak. "Geldiği Yer", "Nakliye Firması",
   "Ad Soyad", "Adres" satırları firma DEĞİLDİR — bunları firma sanma.

c) PLAKA: "Plaka", "Araç Plakası", "Plaka-1", "Araç-Dorse Plaka" etiketinin
   sağındaki İLK plaka. Birden çok plaka varsa (araç-dorse) İLKİNİ al.
   Biçim: 2 rakam (il kodu 01-81) + 1-3 harf + 2-4 rakam. YALNIZCA görseldeki plakayı oku.

d) TARTIM AĞIRLIKLARI — kantar yazılımına göre etiketler DEĞİŞİR:
   • BRÜT (dolu araç): "BRÜT", "BÜRÜT", "Dolu Tartım", "Son Tartım", "2.Tartı"
   • DARA  (boş araç): "DARA", "Boş Tartım", "İlk Tartım", "1.Tartı"
   • Bazı fişlerde "Giriş Ağırlığı" / "Çıkış Ağırlığı" — biri dolu biri boştur.
   • NET: "NET", "Net Tartım", "Net Ağırlık"
   ⚠️ Hangi tartımın brüt hangisinin dara olduğu fişe göre TERS olabilir
   (araç dolu mu boş mu geldi belli olmaz). GÜVENİLİR KURAL:
   NET = iki tartımın FARKININ MUTLAK DEĞERİ. Fişte yazılı NET değeri varsa onu
   kullan; yoksa |büyük tartım − küçük tartım|. Her iki tartımı ve NET'i ayrı yaz.

e) FİRE / TENZİL: "Fire", "Tenzil", "6-) Fire" satırı. Varsa kg, yoksa 0.
   (Bu, NET'ten DÜŞÜLECEK kayıptır — malzeme DEĞİLDİR.)

f) MALZEME KALEMLERİ — ÇOK ÖNEMLİ:
   • TEK satır malzeme (örn "Mal Cinsi: YERLİ HURDA") → malzemeler={}.
   • KALEM LİSTESİ varsa → 0'dan BÜYÜK her kalemi ayrı yaz:
     - Tosçelik tipi: "1-) DKP", "2-) Topsis", "3-) Bonus", "4-) 1.Grup",
       "5-) Karma" → karşılarındaki kg.
     - Kroman tipi tablo: "Hurda Tipi | Miktar | Tenz.Miktar" → her satırın adı +
       "Miktar" kolonu (Tenz.Miktar kolonunu ALMA).
   ⚠️ "Fire" / "Tenzil" satırını malzeme listesine KOYMA (o e şıkkında).
   ⚠️ Kalem kg'ları zaten fire/tenzil düşülmüş NET değerlerdir; toplamları
   (NET − Fire) değerine yaklaşık eşit olmalı.

g) FİŞ NO: "Fiş No", "FİŞ NO", "Id.", "Id:", "Belge No", "Yükleme/Boşaltma Fiş No"
   etiketli RAKAM dizisi — çoğu zaman fişin EN ALTINDA küçük puntoyla "Id." sonrası,
   genelde 8-12 hane.
   ⚠️ "İrsaliye No" ile KARIŞTIRMA. Fiş no bir AĞIRLIK değeriyle (net/brüt/dara) AYNI
   OLAMAZ — ağırlığı fiş no yapma. Tarih de yazma. Net göremiyorsan null bırak.

h) İRSALİYE NO: "İrsaliye No" satırı. Tarih gibi görünüyorsa veya boşsa null.

i) TARİH: "Tarih", "Giriş Tarihi", "Geliş Tarihi" (GG.AA.YYYY).

═══ AŞAMA 2 — JSON ═══
Kurallar:
- plaka: İlk plaka; boşluk/tire silinmiş, bitişik, BÜYÜK harf.
  Şüpheli karakter varsa plaka_confidence ≤0.7.
- net_tartim_kg: Fişteki NET (yoksa iki tartımın farkı). AYRAÇSIZ tam sayı.
- fire_kg: Fire/Tenzil toplamı (yoksa 0). AYRAÇSIZ tam sayı.
- malzemeler: {ad: kg} — her kalem AYRAÇSIZ tam sayı. Tek malzemeyse {}.
- fis_no: g şıkkı (string). Göremiyorsan null + fis_no_confidence=0.
- firma + firma_kaynak: b'deki Firma satırından → "firma_satiri" (conf 0.85+).
  Firma satırı boşsa firma=null. Sadece üst başlık varsa firma=üst başlık +
  firma_kaynak="ust_baslik" + firma_confidence ≤0.3.
- tarih: GG.AA.YYYY. irsaliye_no: h şıkkı (yoksa null).

ÇIKTI FORMATI ÖRNEĞİ — aşağıdaki köşeli parantezler YER TUTUCUDUR (şablon).
Bu yer tutucuları ASLA çıktına yazma; sadece BİÇİMİ gösterir. Gerçek değerleri
yalnızca asıl görselden oku. Çok kalem varsa hepsini ekle; kalemler toplamı
yaklaşık (NET − Fire) olmalı.

LAYOUT:
- ÜST BAŞLIK: [kantar/fabrika adı]
- Fiş No: [numara veya yok]
- Plaka: [plaka]
- Firma Adı: [tedarikçi firma veya boş]
- Malzeme/tartımlar: [gördüğün kalemler ve kg]
- Tarih: [GG.AA.YYYY]

JSON (tek satır):
{"plaka":"[oku]","plaka_confidence":0.0,"fis_no":"[oku veya null]","fis_no_confidence":0.0,"net_tartim_kg":0,"agirlik_confidence":0.0,"fire_kg":0,"tarih":"[GG.AA.YYYY veya null]","firma":"[oku veya null]","firma_kaynak":"firma_satiri","firma_confidence":0.0,"irsaliye_no":null,"kantar_no":null,"malzemeler":{}}

ŞİMDİ asıl görseli oku. Yukarıdaki ŞABLON yer tutucularını DEĞİL, görseldeki
GERÇEK değerleri yaz. Aynı LAYOUT + JSON formatında cevap ver."""


def _fotograf_to_base64(fotograf_yolu: str) -> tuple[str, str, dict]:
    """
    Fotoğrafı base64'e çevirir + ADAPTİF ön-işleme uygular.

    Tek-tip agresif işleme her fişe uymaz:
      • Küçük/düşük çözünürlük foto → upscale + AGRESİF keskinleştirme/kontrast
      • Zaten büyük & temiz görsel (e-posta ekran görüntüsü, yüksek-çöz. foto)
        → HAFİF işle (agresif sharpen/kontrast pristine görseli BOZAR, halüsinasyon artar)
      • Karanlık foto (araç içi, loş ortam) → ölçülü parlaklık artışı
    """
    meta = {"width": 0, "height": 0, "kb": 0, "dusuk_kalite": False}
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageStat
        import io as _io
        with Image.open(fotograf_yolu) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            orig_w, orig_h = img.size
            buyuk_kenar = max(orig_w, orig_h)

            # Ortalama parlaklık (0-255) — karanlık tespiti için, İŞLEMEDEN ÖNCE ölç
            try:
                ort_parlaklik = ImageStat.Stat(img.convert("L")).mean[0]
            except Exception:
                ort_parlaklik = 128.0

            # Boyut: küçükse 2400'e upscale, çok büyükse 2400'e indir
            kucuk = buyuk_kenar < 1600
            if kucuk:
                oran = 2400 / buyuk_kenar
                img = img.resize((int(orig_w * oran), int(orig_h * oran)), Image.LANCZOS)
                logger.info(f"Fotoğraf upscale edildi: {orig_w}x{orig_h} → {img.size}")
            elif buyuk_kenar > 2400:
                img.thumbnail((2400, 2400), Image.LANCZOS)

            # İşleme yoğunluğu — küçük foto agresif, büyük/temiz foto hafif
            if kucuk:
                unsharp            = ImageFilter.UnsharpMask(radius=2, percent=170, threshold=2)
                kontrast, keskinlik = 1.40, 1.8
            else:
                unsharp            = ImageFilter.UnsharpMask(radius=1.5, percent=110, threshold=3)
                kontrast, keskinlik = 1.12, 1.25

            try:
                img = img.filter(unsharp)
            except Exception as fe:
                logger.warning(f"UnsharpMask atlandı: {fe}")
            try:
                img = ImageEnhance.Contrast(img).enhance(kontrast)
            except Exception as ce:
                logger.warning(f"Contrast enhance atlandı: {ce}")
            try:
                img = ImageEnhance.Sharpness(img).enhance(keskinlik)
            except Exception as se:
                logger.warning(f"Sharpness enhance atlandı: {se}")

            # Karanlık foto → ölçülü parlaklık artışı (yalnız gerçekten loşsa, hedef ~115).
            # Aydınlık/temiz görsele DOKUNMA (yazıyı yıkmamak için cap 1.4).
            parlaklik_faktor = 1.0
            if ort_parlaklik < 85:
                parlaklik_faktor = min(1.4, max(1.0, 115.0 / max(1.0, ort_parlaklik)))
                try:
                    img = ImageEnhance.Brightness(img).enhance(parlaklik_faktor)
                except Exception as be:
                    logger.warning(f"Brightness enhance atlandı: {be}")

            buffer = _io.BytesIO()
            img.save(buffer, format="JPEG", quality=95)
            data = base64.b64encode(buffer.getvalue()).decode("utf-8")
            meta["width"]  = img.size[0]
            meta["height"] = img.size[1]
            meta["kb"]     = len(data) // 1024
            if buyuk_kenar < 800 or meta["kb"] < 60:
                meta["dusuk_kalite"] = True
            logger.info(
                f"Fotoğraf hazırlandı: orig=({orig_w}x{orig_h}) final={img.size} | "
                f"{meta['kb']}KB | mod={'agresif' if kucuk else 'hafif'} | "
                f"parlaklık={ort_parlaklik:.0f}→×{parlaklik_faktor:.2f} | "
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
    """
    Ağırlık değerini tam sayı kg'a çevirir.

    Türk fişlerinde nokta/virgül BİNLİK AYRAÇTIR ("17.550" = 17550). Hurda kg
    değerleri daima tam sayıdır. Prompt artık ayraçsız tam sayı istiyor ama
    model "17.550" (string) veya 17.55 (float, yanlış ondalık) gönderebilir —
    her ikisini de güvenle düzeltiriz.
    """
    if deger is None:
        return 0
    # Float, ondalıklı ve < 1000 → büyük ihtimalle binlik ayraç ondalık sanıldı
    # (örn 17.550 → 17.55). ×1000 ile düzelt.
    if isinstance(deger, float) and deger != int(deger) and abs(deger) < 1000:
        sonuc = int(round(deger * 1000))
        logger.warning(f"⚠️ Ondalık kg binlik-ayraç olarak düzeltildi: {deger} → {sonuc}")
        return sonuc
    if isinstance(deger, (int, float)):
        return int(deger)
    # String: tüm binlik ayraçları (. ve ,) ve birim eklerini ("kg") temizle
    s = str(deger).strip().replace(" ", "").replace(".", "").replace(",", "")
    m = re.search(r"-?\d+", s)
    if not m:
        logger.error(f"kg parse hatası: {deger}")
        return 0
    return int(m.group())


def _confidence_hesapla(
    veri: dict,
    temiz_plaka: Optional[str],
    plaka_gecerli: bool,
    plaka_uydurma_var: bool,
    net_tartim: int,
    malzeme_tutarli: Optional[bool] = None,
) -> tuple[float, float, float, float, float]:
    """
    Hybrid confidence hesapla.

    Döndürür: (plaka_conf, agirlik_conf, fis_no_conf, firma_conf, genel_conf)

    Blend ağırlıkları:
    - PLAKA: Claude %75 + Heuristik %25
      (Regex format-geçerliliği gerçek doğruluğu garanti etmez — yanlış okuma
       da regex'e uyabilir, bu yüzden Claude'a daha çok güven.)
    - AĞIRLIK, FİŞ NO: Claude %60 + Heuristik %40
    - FİRMA: Claude %50 + Heuristik %50 (ust_baslik tespiti güçlü sinyal)

    Genel ağırlıklar: plaka %30, ağırlık %30, fiş_no %15, firma %15, tarih %10
    """
    # ── 1. Claude'un verdiği ham skorları al ──────────────────────────
    c_plaka   = float(veri.get("plaka_confidence")   or 0.0)
    c_agirlik = float(veri.get("agirlik_confidence") or 0.0)
    c_fis_no  = float(veri.get("fis_no_confidence")  or 0.0)
    c_firma   = float(veri.get("firma_confidence")   or 0.0)
    # Sınırla 0-1
    c_plaka   = max(0.0, min(1.0, c_plaka))
    c_agirlik = max(0.0, min(1.0, c_agirlik))
    c_fis_no  = max(0.0, min(1.0, c_fis_no))
    c_firma   = max(0.0, min(1.0, c_firma))

    # ── 2. Heuristik skorlar ──────────────────────────────────────────

    # PLAKA heuristic — Regex sadece format kontrolü, gerçek doğruluk kontrolü değil
    if plaka_uydurma_var or not temiz_plaka:
        h_plaka = 0.0
    elif plaka_gecerli:
        # Regex match → kabul edilebilir ama TAM GÜVEN DEĞİL (yanlış okuma da regex'e uyabilir)
        h_plaka = 0.70
    else:
        # Plaka var ama format geçersiz → düşük
        h_plaka = 0.30

    # AĞIRLIK heuristic — geçerli hurda yükü çok geniş aralıkta olabilir.
    # Küçük seferler 500-3000 kg da gerçektir (Tosçelik fişlerinde 1540 kg gördük);
    # sadece aşırı uçlar şüpheli. Eski 3000-40000 aralığı çok dardı.
    if net_tartim <= 0:
        h_agirlik = 0.0
    elif net_tartim < 300:
        h_agirlik = 0.25            # şüpheli ölçüde düşük
    elif 300 <= net_tartim <= 50000:
        h_agirlik = 1.0             # normal hurda yükü (geniş aralık)
    elif 50000 < net_tartim <= 70000:
        h_agirlik = 0.65            # çok ağır — mümkün ama sınırda
    else:
        h_agirlik = 0.25            # aşırı yüksek → büyük ihtimalle yanlış okuma

    # FİŞ NO heuristic — en az 4 karakter
    fis_no_str = str(veri.get("fis_no") or "")
    if len(fis_no_str) >= 8:
        h_fis_no = 0.95
    elif len(fis_no_str) >= 4:
        h_fis_no = 0.75
    elif len(fis_no_str) >= 1:
        h_fis_no = 0.35
    else:
        h_fis_no = 0.0

    # FİRMA heuristic — kaynağa göre belirleyici
    firma_str    = (veri.get("firma") or "").strip()
    firma_kaynak = (veri.get("firma_kaynak") or "").strip().lower()
    if not firma_str:
        h_firma = 0.0
    elif firma_kaynak == "ust_baslik":
        # Üst başlıktan alındı → çok büyük ihtimalle KANTAR firması, tedarikçi değil
        h_firma = 0.15
    elif firma_kaynak == "firma_satiri":
        # Firma: satırından net alındı → yüksek güven
        h_firma = 0.90
    else:
        # Kaynak belirsiz → orta güven
        h_firma = 0.50

    # ── 3. Hybrid blend ────────────────────────────────────────────────
    def blend(claude_s: float, heur_s: float, claude_w: float = 0.60) -> float:
        if claude_s <= 0.0:
            return round(heur_s * 0.8, 3)
        return round(claude_s * claude_w + heur_s * (1.0 - claude_w), 3)

    plaka_conf   = blend(c_plaka,   h_plaka,   claude_w=0.75)  # Plaka için Claude daha ağırlıklı
    agirlik_conf = blend(c_agirlik, h_agirlik, claude_w=0.60)
    fis_no_conf  = blend(c_fis_no,  h_fis_no,  claude_w=0.60)
    firma_conf   = blend(c_firma,   h_firma,   claude_w=0.50)  # Firma için heuristik (kaynak) güçlü

    # Malzeme kalemleri NET ile tutarlıysa ağırlık güvenini pekiştir; uyuşmuyorsa
    # bir değer yanlış okunmuş demektir → ağırlık güvenini belirgin düşür.
    if malzeme_tutarli is True:
        agirlik_conf = round(min(1.0, agirlik_conf + 0.05), 3)
    elif malzeme_tutarli is False:
        agirlik_conf = round(agirlik_conf * 0.75, 3)

    # Tarih için basit varlık skoru
    tarih_conf = 0.85 if veri.get("tarih") else 0.0

    # Genel ağırlıklı ortalama
    genel_conf = round(
        plaka_conf   * 0.30 +
        agirlik_conf * 0.30 +
        fis_no_conf  * 0.15 +
        firma_conf   * 0.15 +
        tarih_conf   * 0.10,
        3
    )

    logger.info(
        f"Confidence → plaka={plaka_conf} ağırlık={agirlik_conf} "
        f"fiş_no={fis_no_conf} firma={firma_conf} ({firma_kaynak}) genel={genel_conf}"
    )
    return plaka_conf, agirlik_conf, fis_no_conf, firma_conf, genel_conf


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

    # Fire/tenzil satırları malzeme DEĞİLDİR — substring ile ele (ör "6-) Fire")
    FIRE_KELIMELERI = ('fire', 'tenzil', 'kayıp', 'kayip')
    malzemeler_ham = veri.get("malzemeler") or {}
    malzemeler = {}
    for k, v in malzemeler_ham.items():
        anahtar = str(k).strip()
        kg      = _parse_kg(v)
        if kg <= 0:
            continue
        if any(fk in anahtar.lower() for fk in FIRE_KELIMELERI):
            continue
        malzemeler[anahtar] = kg

    # ── MALZEME ↔ NET TUTARLILIK ──────────────────────────────────────
    # Kalem toplamı (NET − Fire)'a yakın olmalı. Uyuşmazsa OCR bir değeri
    # yanlış okumuş demektir → confidence düşürülür.
    malzeme_tutarli = None  # None = malzeme yok / kontrol edilemedi
    if malzemeler and net_agirlik > 0:
        toplam_malzeme = sum(malzemeler.values())
        tolerans       = max(50, int(net_agirlik * 0.03))
        if toplam_malzeme > net_agirlik + tolerans:
            logger.warning(
                f"⚠️ Malzeme toplamı ({toplam_malzeme}) net ağırlığı ({net_agirlik}) "
                f"aşıyor, orantılı normalize ediliyor"
            )
            oran = net_agirlik / toplam_malzeme
            malzemeler = {k: max(1, int(v * oran)) for k, v in malzemeler.items()}
            malzeme_tutarli = False
        elif abs(toplam_malzeme - net_agirlik) <= tolerans:
            malzeme_tutarli = True
        else:
            logger.warning(
                f"⚠️ Malzeme toplamı ({toplam_malzeme}) net ağırlığın ({net_agirlik}) "
                f"belirgin altında — eksik/yanlış kalem olabilir"
            )
            malzeme_tutarli = False

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

    # ── Confidence hesapla ────────────────────────────────────────────
    plaka_conf, agirlik_conf, fis_no_conf, firma_conf, genel_conf = _confidence_hesapla(
        veri              = veri,
        temiz_plaka       = temiz_plaka,
        plaka_gecerli     = plaka_gecerli,
        plaka_uydurma_var = bool(plaka_uydurma_uyari),
        net_tartim        = net_tartim,
        malzeme_tutarli   = malzeme_tutarli,
    )

    # ── Firma uyarısı — üst başlıktan alındıysa kullanıcıyı bilgilendir ──
    firma_kaynak = (veri.get("firma_kaynak") or "").strip().lower()
    firma_uyari  = None
    firma_raw    = (veri.get("firma") or "").strip() or None
    if firma_raw and firma_kaynak == "ust_baslik":
        firma_uyari = (
            f"⚠️ Firma '{firma_raw}' fişin üst başlığından alındı (kantar/fabrika adı olabilir). "
            "Gerçek tedarikçi firmayı dropdown'dan seçin."
        )

    logger.info(
        f"OCR sonuç — plaka={temiz_plaka} tartım={net_tartim}kg fire={fire}kg "
        f"net={net_agirlik}kg firma={firma_raw} (kaynak={firma_kaynak or 'belirsiz'})"
    )

    return FisVerisi(
        plaka              = temiz_plaka,
        fis_no             = str(veri.get("fis_no")) if veri.get("fis_no") else None,
        net_tartim_kg      = net_tartim,
        fire_kg            = fire,
        net_agirlik_kg     = net_agirlik,
        tarih              = veri.get("tarih"),
        firma              = firma_raw,
        firma_kaynak       = firma_kaynak or None,
        irsaliye_no        = veri.get("irsaliye_no"),
        kantar_no          = veri.get("kantar_no"),
        malzemeler         = malzemeler or None,
        claude_ham_cikti   = claude_cevap,
        plaka_uyari        = plaka_uyari,
        firma_uyari        = firma_uyari,
        plaka_confidence   = plaka_conf,
        agirlik_confidence = agirlik_conf,
        fis_no_confidence  = fis_no_conf,
        firma_confidence   = firma_conf,
        genel_confidence   = genel_conf,
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
        foto_path              = foto_path,
        plaka                  = fis.plaka,
        fis_no                 = fis.fis_no,
        agirlik_kg             = fis.net_agirlik_kg,
        net_tartim_kg          = fis.net_tartim_kg,
        fire_kg                = fis.fire_kg or 0,
        firma                  = fis.firma,
        fis_tarihi             = fis_tarihi,
        malzeme                = "hurda",
        malzemeler             = json.dumps(fis.malzemeler, ensure_ascii=False) if fis.malzemeler else None,
        ocr_ham_cikti          = fis.claude_ham_cikti,
        ocr_confidence_plaka   = fis.plaka_confidence,
        ocr_confidence_agirlik = fis.agirlik_confidence,
        ocr_confidence_fis_no  = fis.fis_no_confidence,
        ocr_confidence_genel   = fis.genel_confidence,
        status                 = TicketStatus.OCR_TAMAMLANDI if not fis.hata else TicketStatus.HATA,
        hata_mesaji            = fis.hata
    )

    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    logger.info(
        f"✅ Fiş kaydedildi: id={ticket.id} plaka={ticket.plaka} "
        f"firma={ticket.firma} fis_no={ticket.fis_no} agirlik={ticket.agirlik_kg}kg"
    )
    return ticket