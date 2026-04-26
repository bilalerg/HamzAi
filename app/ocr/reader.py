# reader.py — OCR Engine

import anthropic
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

OCR_MODEL      = "claude-haiku-4-5-20251001"
OCR_MAX_TOKENS = 600


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


OCR_SYSTEM = "Sen sadece JSON üreten bir API'sin. Yanıtın SADECE '{' ile başlamalı ve '}' ile bitmelidir. Hiçbir açıklama, başlık veya markdown ekleme."

OCR_PROMPT = """Kantar fişi fotoğrafı. Sadece JSON döndür, başka hiçbir şey yazma.

{"plaka":"ana araç plakası","fis_no":"fiş no","net_tartim_kg":SAYI,"fire_kg":SAYI_YA_DA_0,"tarih":"GG.AA.YYYY","firma":"fişin başlığındaki fabrika adı","irsaliye_no":null,"kantar_no":null,"malzemeler":{}}

KURALLAR:

PLAKA:
- "Araç - Dorse Plaka", "Plaka-1 / Plaka-2", "Ana Araç / Dorse" gibi alanlarda İLK plakayı al
- Tire veya bölü ile ayrılmış iki plaka varsa (örn: "37 LK 081 - 34 BSE 559") SADECE soldakini al
- Plaka formatı: 2 rakam + 1-3 harf + 2-4 rakam (örn: 37LK081, 34ABC123)

FİRMA:
- Fişin EN ÜSTÜNDEKI başlık satırı — genellikle büyük harfle şirket adı (örn: KAPTAN DEMİR ÇELİK END.A.Ş., SAMSUN MAKİNE, KROMAN ÇELİK)
- "Firma Adı", "Nakliye Firması", "Ad Soyad" satırlarındaki isimler tedarikçidir — YAZMA
- Fabrika adı yoksa null döndür

NET AĞIRLIK:
- ÖNCE "NET" veya "NET AĞIRLIK" satırını ara → bu değeri net_tartim_kg yaz
- "Fire/Tenzil" ayrıca belirtilmişse fire_kg yaz, yoksa 0
- BRÜT ve DARA değerlerini ASLA yazma
- Fiş formatı "BRÜT - DARA = NET" ise: NET satırındaki değeri al
- TAM SAYI: 15150 ✓  15.150 ✗  15,150 ✗

MALZEMELER:
- Malzeme tablosu VARSA doldur, YOKSA boş bırak {}
- FORMAT 1 — Kroman tipi: Hurda Tipi | Miktar | Tenzil sütunları → {malzeme_adi: miktar-tenzil}
- FORMAT 2 — Liste tipi: "1-) DKP ... 0", "2-) Topsis ... 26.950" → sadece 0'dan büyük olanlar → {"Topsis": 26950}
- FORMAT 3 — Tek malzeme: "Mal Cinsi: YERLI HURDA" → {"YERLI HURDA": net_tartim_kg}
- ASLA ekleme: Brüt, Dara, Tartı, Fire, Tenzil satırları

TÜM KG TAM SAYI: 15150 ✓  15.150 ✗"""


def _fotograf_to_base64(fotograf_yolu: str) -> tuple[str, str]:
    try:
        from PIL import Image
        import io as _io
        with Image.open(fotograf_yolu) as img:
            # RGBA → RGB dönüşümü (PNG şeffaflık sorunu)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            # Max 1500px — kantar fişi için fazlasıyla yeterli
            img.thumbnail((1500, 1500), Image.LANCZOS)
            buffer = _io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            data = base64.b64encode(buffer.getvalue()).decode("utf-8")
            logger.info(f"Fotoğraf boyutlandırıldı: {img.size} | {len(data)//1024}KB base64")
        return data, "image/jpeg"
    except ImportError:
        # Pillow yoksa eski yöntem
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


def claude_fis_oku(fotograf_yolu: str) -> FisVerisi:
    logger.info(f"OCR başlıyor: {fotograf_yolu} | model={OCR_MODEL}")

    foto_base64, media_type = _fotograf_to_base64(fotograf_yolu)

    response = _client.messages.create(
        model=OCR_MODEL,
        max_tokens=OCR_MAX_TOKENS,
        temperature=0.0,
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

    try:
        veri = _parse_json(claude_cevap)

        net_tartim  = _parse_kg(veri.get("net_tartim_kg"))
        fire        = _parse_kg(veri.get("fire_kg"))
        net_agirlik = net_tartim - fire

        if net_agirlik < 0:
            logger.warning(f"⚠️ Net ağırlık negatif (tartım={net_tartim}, fire={fire}), fire sıfırlanıyor")
            net_agirlik = net_tartim
            fire = 0

        if net_agirlik == 0 or net_tartim == 0:
            logger.error(f"⚠️ Sıfır ağırlık tespit edildi — fiş geçersiz olabilir")
            return FisVerisi(
                hata="Fişteki ağırlık değeri okunamadı veya sıfır. Fotoğrafı daha net çekip tekrar deneyin.",
                claude_ham_cikti=claude_cevap
            )

        FIRE_KELIMELERI = {'fire', 'tenzil', 'fire kg', 'kayıp', 'kayip', 'hurda fire'}
        malzemeler_ham = veri.get("malzemeler") or {}
        malzemeler = {
            k: _parse_kg(v) for k, v in malzemeler_ham.items()
            if v and _parse_kg(v) > 0 and k.strip().lower() not in FIRE_KELIMELERI
        }

        # Plaka normalizasyonu — boşluk/tire kaldır, büyük harf
        ham_plaka = veri.get("plaka")
        temiz_plaka = re.sub(r'[\s\-]', '', ham_plaka).upper() if ham_plaka else None

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
            claude_ham_cikti = claude_cevap
        )

    except Exception as e:
        logger.error(f"OCR parse hatası: {e} | ham: {claude_cevap[:200]}")
        return FisVerisi(hata=str(e), claude_ham_cikti=claude_cevap)


def fis_oku(fotograf_yolu: str) -> FisVerisi:
    fis = claude_fis_oku(fotograf_yolu)
    if fis.hata:
        logger.error(f"Fiş okunamadı: {fis.hata}")
    return fis


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