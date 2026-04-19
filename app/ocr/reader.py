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

    prompt = """Bu bir kantar (tartı) fişi fotoğrafıdır. Fişten bilgileri çıkar ve SADECE JSON formatında döndür, başka hiçbir şey yazma.

JSON formatı:
{
  "plaka": "araç plaka numarası (sadece birinci/ana araç plakası, dorse veya 2. plaka değil)",
  "fis_no": "fiş numarası veya yükleme/boşaltma fiş no",
  "net_tartim_kg": net tartım değeri (sayı, kg birimi olmadan),
  "fire_kg": fire veya tenzil değeri (sayı, yoksa 0),
  "tarih": "GG.AA.YYYY formatında fiş tarihi",
  "firma": "fişte yazan fabrika/şirket adı (Samsun Makine, Kroman, İçtaş, Kaptan gibi — hurda satıcısı değil, kantarın sahibi fabrika)",
  "irsaliye_no": "irsaliye numarası (yoksa null)",
  "kantar_no": "kantar numarası (yoksa null)",
  "malzemeler": {}
}

PLAKA KURALI:
- "Plaka-1", "Araç Plakası", "Plaka No" gibi alanlardan ilk plakayı al
- "Plaka-2", "Dorse", "Römork" gibi ikinci plakayı ALMA
- Birden fazla plaka varsa sadece birincisini al

FİRMA KURALI:
- Fişin başlığındaki veya üst kısmındaki fabrika adını al (SAMSUN MAKİNE SANAYİ A.Ş., KAPTAN DEMİR ÇELİK, İÇDAŞ A.Ş. gibi)
- "Firma Adı" satırındaki hurda tedarikçisini (YEŞİL İZABE, MAJ DENİZ gibi) ALMA — bu hurda satan firma, fabrika değil
- Fişin sahibi kim? O fabrikanın adını yaz

NET TARTIM VE FİRE KURALI:
- "Net Tartım", "Net", "NET AĞIRLIK" satırındaki değeri net_tartim_kg olarak al
- "Fire", "Tenzil", "Fire Kg" satırındaki değeri fire_kg olarak al — ikisi aynı anlama gelir
- Eğer fişte malzeme tablosu varsa "Tenz.Miktar" sütunu da fire demektir, toplam tenzil miktarını fire_kg olarak yaz
- net_tartim_kg her zaman fire düşülmeden önceki değerdir

MALZEME KURALI — ÇOK ÖNEMLİ:
Fişte malzeme listesi/tablosu var mı?

EVET, tablo/liste varsa:
- Her malzeme satırını oku: malzeme adı → net miktarı
- Eğer "Tenz.Miktar" veya "Tenzil Miktar" sütunu varsa: net miktar = Miktar - Tenz.Miktar
- Eğer sadece "Miktar" sütunu varsa: net miktar = Miktar
- Sıfırdan büyük olan malzemeleri yaz
- Örnek: {"HURDA YERLİ EXTRA": 4627, "HURDA YERLİ 1.GRUP": 2644, "HURDA YERLİ 2.GRUP": 1983, "HURDA YERLİ EXTRA-KESİM": 3966}

HAYIR, sadece tek net kilo varsa:
- malzemeler: {} (boş bırak)
- Net tartım zaten net_tartim_kg alanında

NOT: "1.Tartı", "2.Tartı", "Brüt", "Dara", "Dolu Tartım", "Boş Tartım" gibi ara değerleri ASLA malzeme olarak ekleme — bunlar hesaplama aşamaları."""

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