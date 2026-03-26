# reader.py — OCR Engine
#
# SORUMLULUK:
# 1. Fotoğrafı alır
# 2. Claude Vision'a gönderir → plaka, ağırlık, tarih, firma çıkarır
# 3. Tesseract ile ağırlığı çapraz kontrol eder
# 4. Sonucu döndürür
#
# NEDEN İKİ OCR?
# Claude Vision akıllı ama pahalı ve bazen halüsinasyon yapar.
# Tesseract ücretsiz ve yerel ama aptal — sadece düz metin okur.
# İkisini birleştirince hem akıllı hem güvenilir oluyoruz.

import anthropic
import base64
import pytesseract
import re
from PIL import Image
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger


# ── VERİ YAPISI ─────────────────────────────────────────────────────────
# @dataclass → Python'da basit veri taşıyıcı sınıf
# Normalde sınıf yazınca __init__, __repr__ gibi metodları kendin yazarsın.
# @dataclass bunları otomatik oluşturur — temiz ve az kod.
@dataclass
class FisVerisi:
    """Kantar fişinden çıkarılan veriler"""
    plaka: Optional[str] = None
    net_agirlik_kg: Optional[int] = None
    tarih: Optional[str] = None
    firma: Optional[str] = None
    irsaliye_no: Optional[str] = None
    kantar_no: Optional[str] = None

    # Çapraz kontrol sonuçları
    tesseract_agirlik: Optional[int] = None
    ocr_uyusma: Optional[bool] = None

    # Ham çıktılar — debug için
    claude_ham_cikti: Optional[str] = None
    hata: Optional[str] = None


# ── YARDIMCI FONKSİYON: FOTOĞRAF → BASE64 ───────────────────────────────
def fotograf_to_base64(fotograf_yolu: str) -> str:
    """
    Fotoğraf dosyasını base64 string'e çevirir.

    NEDEN BASE64?
    Claude API'ye binary (ham) fotoğraf gönderemezsin.
    base64 binary veriyi düz ASCII metne çevirir.
    API bu metni alıp tekrar fotoğrafa çevirir.

    Örnek: her 3 byte → 4 karakter
    """
    with open(fotograf_yolu, "rb") as f:  # "rb" = read binary
        return base64.standard_b64encode(f.read()).decode("utf-8")


# ── ANA FONKSİYON: CLAUDE VİSİON ────────────────────────────────────────
def claude_fis_oku(fotograf_yolu: str) -> FisVerisi:
    """
    Claude Vision API ile kantar fişini okur.

    NASIL ÇALIŞIR?
    1. Fotoğrafı base64'e çevir
    2. Claude'a gönder + ne çıkarması gerektiğini söyle (prompt)
    3. Claude JSON formatında cevap verir
    4. JSON'u parse et → FisVerisi nesnesine dönüştür
    """
    logger.info(f"Claude Vision ile fiş okunuyor: {fotograf_yolu}")

    # Fotoğrafı base64'e çevir
    foto_base64 = fotograf_to_base64(fotograf_yolu)

    # Dosya uzantısından media type belirle
    # .jpg/.jpeg → image/jpeg, .png → image/png
    uzanti = Path(fotograf_yolu).suffix.lower()
    media_type = "image/jpeg" if uzanti in [".jpg", ".jpeg"] else "image/png"

    # Claude istemcisini oluştur
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # ── PROMPT ──────────────────────────────────────────────────────────
    # NEDEN BÖYLE BİR PROMPT?
    # Claude'a ne istediğimizi çok net söylüyoruz.
    # "Sadece JSON döndür" diyoruz çünkü başka metin gelirse
    # parse etmek zorlaşır. Kesin format istemek = güvenilir çıktı.
    prompt = """Bu bir kantar (tartı) fişi fotoğrafıdır. 
Fişten aşağıdaki bilgileri çıkar ve SADECE JSON formatında döndür, başka hiçbir şey yazma:

{
  "plaka": "araç plaka numarası",
  "net_agirlik_kg": net ağırlık sayı olarak (sadece rakam, kg birimi olmadan),
  "tarih": "GG.AA.YYYY formatında tarih",
  "firma": "firma adı",
  "irsaliye_no": "irsaliye numarası",
  "kantar_no": "kantar numarası"
}

Eğer bir bilgi fişte yoksa veya okunamıyorsa null yaz.
Net ağırlık için önce "Net Tartım" veya "Net" yazan satıra bak.
Plaka için "Araç Plakası" satırına bak."""

    # API isteği gönder
    # NEDEN BU YAPI?
    # Anthropic API'si messages listesi alıyor.
    # Her mesaj role (user/assistant) ve content içeriyor.
    # content listesi metin ve görüntü bloklarından oluşuyor.
    response = client.messages.create(
        model="claude-opus-4-5",
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

    # Cevabı al
    claude_cevap = response.content[0].text
    logger.debug(f"Claude ham cevap: {claude_cevap}")

    # JSON parse et
    # NEDEN TRY/EXCEPT?
    # Claude bazen ```json ``` bloğu içinde verir.
    # Bazen düz JSON verir.
    # Her ihtimale karşı temizleyip parse ediyoruz.
    import json
    try:
        # ```json ... ``` bloğunu temizle
        temiz_cevap = claude_cevap.strip()
        if temiz_cevap.startswith("```"):
            temiz_cevap = temiz_cevap.split("```")[1]
            if temiz_cevap.startswith("json"):
                temiz_cevap = temiz_cevap[4:]

        veri = json.loads(temiz_cevap)

        return FisVerisi(
            plaka=veri.get("plaka"),
            net_agirlik_kg=veri.get("net_agirlik_kg"),
            tarih=veri.get("tarih"),
            firma=veri.get("firma"),
            irsaliye_no=veri.get("irsaliye_no"),
            kantar_no=veri.get("kantar_no"),
            claude_ham_cikti=claude_cevap
        )

    except Exception as e:
        logger.error(f"Claude cevabı parse edilemedi: {e}")
        return FisVerisi(hata=str(e), claude_ham_cikti=claude_cevap)


# ── TESSERACT ÇAPRAZ KONTROL ─────────────────────────────────────────────
def tesseract_agirlik_oku(fotograf_yolu: str) -> Optional[int]:
    """
    Fotoğrafın alt bölgesini kırpıp Tesseract ile ağırlığı okur.

    NEDEN SADECE ALT BÖLGE?
    Kantar fişlerinde ağırlık bilgisi genelde altta olur.
    Tüm fişi Tesseract'a vermek yerine sadece ilgili bölgeyi veriyoruz.
    Hem daha hızlı hem daha doğru sonuç.
    """
    logger.debug(f"Tesseract ile ağırlık okunuyor: {fotograf_yolu}")

    try:
        img = Image.open(fotograf_yolu)
        genislik, yukseklik = img.size

        # Alt %40'lık bölgeyi kırp — ağırlık genelde burada
        # Net Tartım fişin ortasında — alt kısımda imza/el yazısı olabilir
        alt_bolge = img.crop((0, int(yukseklik * 0.4), genislik, int(yukseklik * 0.75)))
        # Tesseract ile oku — Türkçe dil paketi kullan
        tesseract_metin = pytesseract.image_to_string(
            alt_bolge,
            lang="tur",
            config="--psm 6"  # psm 6 = tek tip metin bloğu olarak işle
        )

        logger.debug(f"Tesseract ham metin: {tesseract_metin}")

        # "Net Tartım" veya "Net" satırındaki sayıyı bul
        # Regex ile sayı ara: nokta veya virgülle ayrılmış rakam grupları
        satirlar = tesseract_metin.split('\n')
        for satir in satirlar:
            if 'net' in satir.lower() and 'tartım' in satir.lower():
                # Sayıları bul: 21.820 veya 21,820 formatında
                sayilar = re.findall(r'\d+[.,]\d+', satir)
                if sayilar:
                    # Nokta/virgülü kaldır, tam sayıya çevir
                    sayi = sayilar[-1].replace('.', '').replace(',', '')
                    return int(sayi)

        return None

    except Exception as e:
        logger.error(f"Tesseract hatası: {e}")
        return None


# ── ANA FONKSİYON: ÇAPRAZ KONTROL DAHİL TAM OKUMA ───────────────────────
def fis_oku(fotograf_yolu: str) -> FisVerisi:
    """
    Tam OCR pipeline:
    1. Claude Vision ile fişi oku
    2. Tesseract ile ağırlığı çapraz kontrol et
    3. Sonucu döndür

    Bu fonksiyon dışarıdan çağrılacak tek nokta.
    Diğer modüller sadece bunu import eder.
    """
    logger.info(f"Fiş okuma başladı: {fotograf_yolu}")

    # 1. Claude Vision ile oku
    fis = claude_fis_oku(fotograf_yolu)

    if fis.hata:
        logger.error(f"Claude okuma hatası: {fis.hata}")
        return fis

    # 2. Tesseract ile çapraz kontrol
    tesseract_agirlik = tesseract_agirlik_oku(fotograf_yolu)
    fis.tesseract_agirlik = tesseract_agirlik

    # 3. Karşılaştır
    if fis.net_agirlik_kg and tesseract_agirlik:
        fark = abs(fis.net_agirlik_kg - tesseract_agirlik)
        tolerans = fis.net_agirlik_kg * 0.05  # %5 tolerans

        if fark <= tolerans:
            fis.ocr_uyusma = True
            logger.info(f"✅ OCR uyuştu: Claude={fis.net_agirlik_kg}kg, Tesseract={tesseract_agirlik}kg")
        else:
            fis.ocr_uyusma = False
            logger.warning(f"⚠️ OCR uyuşmadı: Claude={fis.net_agirlik_kg}kg, Tesseract={tesseract_agirlik}kg")
    else:
        logger.warning("Tesseract ağırlık bulamadı, çapraz kontrol yapılamadı")

    return fis


# ── VERİTABANINA KAYDET ──────────────────────────────────────────────────
def fis_db_kaydet(fis: FisVerisi, foto_path: str, db) -> object:
    from app.core.database import WeighTicket, TicketStatus
    from datetime import datetime

    # Tarihi string'den datetime'a çevir
    # Fişten "16.12.2025" formatında geliyor, DB datetime istiyor
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
        agirlik_kg=fis.net_agirlik_kg,
        fis_tarihi=fis_tarihi,          # ← eklendi
        malzeme="hurda",
        ocr_ham_cikti=fis.claude_ham_cikti,
        tesseract_agirlik=fis.tesseract_agirlik,
        ocr_uyusma=1 if fis.ocr_uyusma else 0,
        status=TicketStatus.OCR_TAMAMLANDI if not fis.hata else TicketStatus.HATA,
        hata_mesaji=fis.hata
    )

    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    logger.info(f"✅ Fiş DB'ye kaydedildi: ticket_id={ticket.id} plaka={ticket.plaka} status={ticket.status}")

    return ticket