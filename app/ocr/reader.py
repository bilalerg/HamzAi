# reader.py — OCR Engine

import anthropic
import base64
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict

from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger


@dataclass
class FisVerisi:
    """Kantar fişinden çıkarılan veriler"""
    plaka: Optional[str] = None
    fis_no: Optional[str] = None
    net_agirlik_kg: Optional[int] = None
    net_tartim_kg: Optional[int] = None
    fire_kg: Optional[int] = None
    tarih: Optional[str] = None
    firma: Optional[str] = None
    irsaliye_no: Optional[str] = None
    kantar_no: Optional[str] = None
    malzemeler: Optional[Dict[str, int]] = None

    claude_ham_cikti: Optional[str] = None
    hata: Optional[str] = None


def fotograf_to_base64(fotograf_yolu: str) -> str:
    with open(fotograf_yolu, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def claude_fis_oku(fotograf_yolu: str) -> FisVerisi:
    logger.info(f"Claude Vision ile fiş okunuyor: {fotograf_yolu}")

    foto_base64 = fotograf_to_base64(fotograf_yolu)
    uzanti = Path(fotograf_yolu).suffix.lower()
    media_type = "image/jpeg" if uzanti in [".jpg", ".jpeg"] else "image/png"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = """Bu bir kantar (tartı) fişi fotoğrafıdır.
Fişten aşağıdaki bilgileri çıkar ve SADECE JSON formatında döndür, başka hiçbir şey yazma:

{
  "plaka": "araç plaka numarası",
  "fis_no": "Yukleme/Bosaltma Fis No veya fiş numarası",
  "net_tartim_kg": Net Tartım değeri sayı olarak (sadece rakam, kg birimi olmadan),
  "fire_kg": Fire değeri sayı olarak (sadece rakam, 0 olabilir),
  "tarih": "GG.AA.YYYY formatında giriş tarihi",
  "firma": "Firma Adı satırındaki tam şirket adı (örn: SAMSUN MAKİNE SANAYİ A.Ş., KROMAN ÇELİK, İÇTAŞ DEMİR ÇELİK — sadece şirket adını yaz, hurda veya malzeme gibi kelimeler değil)",
  "irsaliye_no": "irsaliye numarası",
  "kantar_no": "kantar numarası",
  "malzemeler": {
    "DKP": 0,
    "TOPSİS": 0,
    "BONUS": 0,
    "1.GRUP": 0,
    "KARMA": 0
  }
}

Önemli kurallar:
- "Net Tartım" satırındaki değeri net_tartim_kg olarak al
- "Fire" satırındaki değeri fire_kg olarak al (yoksa 0 yaz)
- "Fis No" veya "Yukleme / Bosaltma Fis No" satırındaki numarayı fis_no olarak al
- Plaka için "Araç Plakası" satırına bak
- firma: Fişte "Firma Adı" satırındaki şirket adını al. "Hurda", "Malzeme", "Ürün" gibi kelimeler firma adı değildir
- malzemeler: Fişin "Malzeme Tanısı" bölümündeki her malzeme grubunu ve karşısındaki Net değerini oku
  - Değer 0 ise 0 yaz, yoksa null değil 0 yaz
  - Fişte olmayan malzeme grubu varsa 0 yaz
  - Malzeme adını fişte yazan şekilde yaz (büyük harf)
- Eğer bir bilgi fişte yoksa null yaz"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": foto_base64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ],
            }
        ],
    )

    claude_cevap = response.content[0].text
    logger.info(f"Claude ham cevap: {claude_cevap}")

    try:
        temiz_cevap = claude_cevap.strip()
        if temiz_cevap.startswith("```"):
            temiz_cevap = temiz_cevap.split("```")[1]
            if temiz_cevap.startswith("json"):
                temiz_cevap = temiz_cevap[4:]

        veri = json.loads(temiz_cevap)

        net_tartim = veri.get("net_tartim_kg") or 0
        fire = veri.get("fire_kg") or 0

        net_agirlik = int(net_tartim) - int(fire)
        if net_agirlik < 0:
            net_agirlik = int(net_tartim)

        malzemeler_ham = veri.get("malzemeler") or {}
        malzemeler = {k: int(v) for k, v in malzemeler_ham.items() if v and int(v) > 0}

        logger.info(f"Net Tartım: {net_tartim} kg | Fire: {fire} kg | Net Ağırlık: {net_agirlik} kg")
        logger.info(f"Firma: {veri.get('firma')} | Malzemeler: {malzemeler}")

        return FisVerisi(
            plaka=veri.get("plaka"),
            fis_no=str(veri.get("fis_no")) if veri.get("fis_no") else None,
            net_tartim_kg=int(net_tartim),
            fire_kg=int(fire),
            net_agirlik_kg=net_agirlik,
            tarih=veri.get("tarih"),
            firma=veri.get("firma"),
            irsaliye_no=veri.get("irsaliye_no"),
            kantar_no=veri.get("kantar_no"),
            malzemeler=malzemeler if malzemeler else None,
            claude_ham_cikti=claude_cevap
        )

    except Exception as e:
        logger.error(f"Claude cevabı parse edilemedi: {e}")
        return FisVerisi(hata=str(e), claude_ham_cikti=claude_cevap)


def fis_oku(fotograf_yolu: str) -> FisVerisi:
    logger.info(f"Fiş okuma başladı: {fotograf_yolu}")
    fis = claude_fis_oku(fotograf_yolu)
    if fis.hata:
        logger.error(f"Claude okuma hatası: {fis.hata}")
    return fis


def fis_db_kaydet(fis: FisVerisi, foto_path: str, db) -> object:
    from app.core.database import WeighTicket, TicketStatus
    from datetime import datetime

    fis_tarihi = None
    if fis.tarih:
        try:
            fis_tarihi = datetime.strptime(fis.tarih, "%d.%m.%Y")
        except:
            try:
                fis_tarihi = datetime.strptime(fis.tarih, "%Y-%m-%d")
            except:
                pass

    ticket = WeighTicket(
        foto_path=foto_path,
        plaka=fis.plaka,
        fis_no=fis.fis_no,
        agirlik_kg=fis.net_agirlik_kg,
        net_tartim_kg=fis.net_tartim_kg,
        fire_kg=fis.fire_kg or 0,
        firma=fis.firma,
        fis_tarihi=fis_tarihi,
        malzeme="hurda",
        malzemeler=json.dumps(fis.malzemeler, ensure_ascii=False) if fis.malzemeler else None,
        ocr_ham_cikti=fis.claude_ham_cikti,
        status=TicketStatus.OCR_TAMAMLANDI if not fis.hata else TicketStatus.HATA,
        hata_mesaji=fis.hata
    )

    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    logger.info(f"✅ Fiş DB'ye kaydedildi: ticket_id={ticket.id} plaka={ticket.plaka} firma={ticket.firma} fis_no={ticket.fis_no} agirlik={ticket.agirlik_kg}kg")

    return ticket