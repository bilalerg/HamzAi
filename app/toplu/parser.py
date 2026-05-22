# parser.py — Toplu fiş dosyası ayrıştırma (Excel / CSV / PDF)
#
# Fabrikalar (Tosyalı, vb.) tek tek fiş yerine bir özet liste gönderir:
#   Fiş No | Plaka | Şoför | Net Miktar | Tesis
# Bu modül o dosyayı okuyup normalize satırlara çevirir.

import io
import re
import csv as _csv
from app.core.logger import logger

# Başlık eşleştirme — fabrikadan fabrikaya kolon adları değişir, ANLAMA göre eşle.
_BASLIK_ESLEME = {
    "fis_no": ["fiş no", "fis no", "fişno", "fisno", "fiş", "fis", "belge no", "id", "no"],
    "plaka":  ["plaka", "araç plaka", "arac plaka", "araç", "arac"],
    "sofor":  ["şoför", "sofor", "soför", "sürücü", "surucu", "ad soyad", "adı soyadı"],
    "net_kg": ["net miktar", "net miktar (kg)", "net", "miktar", "net kg", "net tartım",
               "net tartim", "tonaj", "kg", "ağırlık", "agirlik"],
    "tesis":  ["tesis", "fabrika", "firma", "alıcı", "alici", "müşteri", "musteri"],
}


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _net_kg(v) -> int:
    """Türkçe binlik ayraçlı sayıyı tam sayı kg'a çevirir. '14.950' → 14950."""
    if v is None:
        return 0
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v))
    s = str(v).strip().replace(" ", "")
    if not s:
        return 0
    has_dot, has_comma = "." in s, "," in s
    try:
        if has_dot and has_comma:
            # 1.234,5 → 1234.5
            return int(round(float(s.replace(".", "").replace(",", "."))))
        if has_dot:
            parcatlar = s.split(".")
            # son grup 3 haneliyse binlik ayraç (14.950), değilse ondalık (14.95)
            if all(len(p) == 3 for p in parcatlar[1:]):
                return int(s.replace(".", ""))
            return int(round(float(s)))
        if has_comma:
            return int(round(float(s.replace(",", "."))))
        return int(re.sub(r"\D", "", s) or 0)
    except (ValueError, TypeError):
        return int(re.sub(r"\D", "", s) or 0)


def _plaka_temizle(v) -> str:
    return re.sub(r"[\s\-]", "", str(v or "")).upper()


def _kolon_haritasi(basliklar: list) -> dict:
    """Başlık satırından {alan: kolon_index} haritası üretir."""
    harita = {}
    for idx, basetiket in enumerate(basliklar):
        b = _norm(basetiket)
        if not b:
            continue
        for alan, adaylar in _BASLIK_ESLEME.items():
            if alan in harita:
                continue
            if any(b == a or b.startswith(a) or a in b for a in adaylar):
                harita[alan] = idx
                break
    return harita


def _baslik_satiri_bul(satirlar: list) -> int:
    """İçinde 'fiş'/'plaka' geçen ilk satırı başlık kabul eder."""
    for i, satir in enumerate(satirlar[:15]):
        hucreler = " ".join(_norm(c) for c in satir)
        if ("fiş" in hucreler or "fis" in hucreler) and "plaka" in hucreler:
            return i
        if "plaka" in hucreler and ("net" in hucreler or "miktar" in hucreler):
            return i
    return 0


def _satirlari_isle(ham_satirlar: list) -> list:
    """Ham hücre satırlarını (list of list) normalize fiş satırlarına çevirir."""
    if not ham_satirlar:
        return []
    bi = _baslik_satiri_bul(ham_satirlar)
    harita = _kolon_haritasi(ham_satirlar[bi])
    if "fis_no" not in harita and "plaka" not in harita:
        raise ValueError("Dosyada 'Fiş No' veya 'Plaka' kolonu bulunamadı. Başlık satırı doğru mu?")

    sonuc = []
    for satir in ham_satirlar[bi + 1:]:
        def al(alan):
            idx = harita.get(alan)
            if idx is None or idx >= len(satir):
                return None
            return satir[idx]

        fis_no = str(al("fis_no") or "").strip()
        plaka  = _plaka_temizle(al("plaka"))
        net    = _net_kg(al("net_kg"))
        # TOPLAM / boş / özet satırlarını atla
        if not fis_no and not plaka:
            continue
        if "toplam" in _norm(al("sofor")) or "toplam" in fis_no.lower():
            continue
        if not fis_no and net <= 0:
            continue
        sonuc.append({
            "fis_no": fis_no or None,
            "plaka":  plaka or None,
            "sofor":  (str(al("sofor")).strip() if al("sofor") else None),
            "net_kg": net,
            "tesis":  (str(al("tesis")).strip() if al("tesis") else None),
        })
    return sonuc


# ── Dosya tipi okuyucular ──────────────────────────────────────────────

def _xlsx_oku(icerik: bytes) -> list:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(icerik), data_only=True, read_only=True)
    ws = wb.active
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _csv_oku(icerik: bytes) -> list:
    for enc in ("utf-8-sig", "utf-8", "cp1254", "latin-1"):
        try:
            metin = icerik.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        metin = icerik.decode("utf-8", errors="ignore")
    ornek = metin[:2000]
    ayrac = ";" if ornek.count(";") >= ornek.count(",") else ","
    return [list(r) for r in _csv.reader(io.StringIO(metin), delimiter=ayrac)]


def _pdf_oku(icerik: bytes) -> list:
    """Metin-tabanlı PDF tablolarını ayrıştırır (pdfplumber). Taranmış PDF için OCR gerekir."""
    try:
        import pdfplumber
    except ImportError:
        raise ValueError("PDF okumak için 'pdfplumber' kurulu olmalı (pip install pdfplumber).")
    satirlar = []
    with pdfplumber.open(io.BytesIO(icerik)) as pdf:
        for sayfa in pdf.pages:
            for tablo in (sayfa.extract_tables() or []):
                satirlar.extend(tablo)
    if not satirlar:
        raise ValueError("PDF'te tablo bulunamadı. Taranmış/görüntü PDF olabilir (OCR gerekir).")
    return satirlar


def dosya_ayristir(icerik: bytes, dosya_adi: str) -> list:
    """
    Toplu fiş dosyasını (xlsx/xls/csv/pdf) normalize satır listesine çevirir.
    Dönüş: [{fis_no, plaka, sofor, net_kg, tesis}, ...]
    """
    ad = (dosya_adi or "").lower()
    if ad.endswith((".xlsx", ".xlsm", ".xls")):
        ham = _xlsx_oku(icerik)
    elif ad.endswith(".csv") or ad.endswith(".txt"):
        ham = _csv_oku(icerik)
    elif ad.endswith(".pdf"):
        ham = _pdf_oku(icerik)
    else:
        raise ValueError(f"Desteklenmeyen dosya türü: {dosya_adi}. Excel/CSV/PDF yükleyin.")
    satirlar = _satirlari_isle(ham)
    logger.info(f"Toplu dosya ayrıştırıldı: {dosya_adi} → {len(satirlar)} satır")
    return satirlar
