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
OCR_MAX_TOKENS = 500


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


OCR_PROMPT = """Kantar fişi fotoğrafı. Sadece JSON döndür, başka hiçbir şey yazma.

{"plaka":"ana araç plakası (dorse/2.plaka değil)","fis_no":"fiş no","net_tartim_kg":SAYI,"fire_kg":SAYI_YA_DA_0,"tarih":"GG.AA.YYYY","firma":"fişin başlığındaki fabrika adı (hurda tedarikçisi değil)","irsaliye_no":null,"kantar_no":null,"malzemeler":{}}

KURALLAR:
- plaka: Plaka-1/Ana Araç alanı, birden fazlaysa sadece ilki
- firma: Fiş başlığındaki fabrika (SAMSUN MAKİNE, KAPTAN DEMİR, İÇDAŞ, TOSCELIK gibi). "Firma Adı" satırındaki tedarikçiyi yazma.
- net_tartim_kg: "Net Tartım/Net/NET AĞIRLIK" değeri (fire düşülmemiş). TAM SAYI yaz, ondalık kullanma. 30.680 değil 30680 yaz.
- fire_kg: "Fire/Tenzil/Fire Kg" değeri, yoksa 0. TAM SAYI yaz.
- malzemeler: Malzeme tablosu VARSA doldur, YOKSA boş bırak {}.
  FORMAT 1 — Kroman tipi tablo: Hurda Tipi | Miktar | Tenzil.Miktar sütunları → {malzeme_adi: net_miktar} (net_miktar = Miktar - Tenzil.Miktar)
  FORMAT 2 — Tosyalı/UHT tipi liste: "1-) DKP ... 0", "2-) Topsis ... 26.950" şeklinde → sadece değeri 0'dan büyük olanları al → {"Topsis": 26950}
  FORMAT 3 — Tek malzeme: "MALZEME: HURDA İÇ PİYASA GENEL" gibi → {"HURDA İÇ PİYASA GENEL": net_tartim_kg}
- malzemelere ASLA ekleme: Brüt, Dara, 1.Tartı, 2.Tartı, Dolu/Boş Tartım
- TÜM KG DEĞERLERİ TAM SAYI OLMALI: 30680 ✓  30.680 ✗  30,680 ✗"""


def _fotograf_to_base64(fotograf_yolu: str) -> tuple[str, str]:
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

        malzemeler_ham = veri.get("malzemeler") or {}
        malzemeler = {k: _parse_kg(v) for k, v in malzemeler_ham.items() if v and _parse_kg(v) > 0}

        logger.info(f"OCR sonuç — plaka={veri.get('plaka')} tartım={net_tartim}kg fire={fire}kg net={net_agirlik}kg firma={veri.get('firma')}")

        return FisVerisi(
            plaka            = veri.get("plaka"),
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