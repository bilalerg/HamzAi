# app/muhasebe/yerel/excel.py — Yerel muhasebe kayıtlarını Excel'e aktarma
#
# Tek .xlsx dosyası, her kayıt türü ayrı sayfa:
#   Cariler · Faturalar · İrsaliyeler · Tahsilat-Ödeme · Kasa-Banka · Kantar Fişleri

import io
import json
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.core.database import SessionLocal, WeighTicket
from app.muhasebe.yerel import servis
from app.muhasebe.yerel.modeller import YerelCari, YerelFatura, YerelHareket, YerelHesap, YerelIrsaliye

_BASLIK_DOLGU = PatternFill("solid", fgColor="1F4E78")
_BASLIK_YAZI  = Font(bold=True, color="FFFFFF")
_PARA_FORMAT  = '#,##0.00 "TL"'
_KG_FORMAT    = '#,##0 "kg"'

_TIP_ADI = {"customer": "Müşteri", "supplier": "Tedarikçi", "customer_supplier": "Müşteri + Tedarikçi"}
_DURUM_ADI = {"paid": "Ödendi", "overdue": "Gecikmiş", "not_due": "Bekliyor"}
_HAREKET_ADI = {
    "fatura_tahsilat": "Tahsilat (faturaya)", "cari_tahsilat": "Tahsilat (cari hesaba)",
    "fatura_odeme": "Ödeme (faturaya)", "cari_odeme": "Ödeme (cari hesaba)",
    "transfer_cikis": "Transfer çıkışı", "transfer_giris": "Transfer girişi",
}


_FIS_DURUM_ADI = {
    "ALINDI": "Alındı", "OCR_TAMAMLANDI": "Okundu", "IRSALIYE_OLUSTURULUYOR": "İrsaliye oluşturuluyor",
    "IRSALIYE_TAMAMLANDI": "İrsaliye kesildi", "FABRIKA_BEKLENIYOR": "İrsaliye kesildi",
    "FATURA_KESILIYOR": "Fatura kesiliyor", "TAMAMLANDI": "Faturalandı", "IPTAL": "İptal", "HATA": "Hata",
}


def _tr_tarih(ymd):
    """'2026-10-07' → '07.10.2026'"""
    if not ymd or len(str(ymd)) < 10:
        return ymd
    y, m, g = str(ymd)[:10].split("-")
    return f"{g}.{m}.{y}"


def _tr_kg(x) -> str:
    return f"{int(float(x or 0)):,}".replace(",", ".")


def _sayfa(wb, baslik, basliklar, satirlar, para_sutunlari=(), kg_sutunlari=()):
    ws = wb.create_sheet(baslik)
    ws.append(basliklar)
    for hucre in ws[1]:
        hucre.fill, hucre.font = _BASLIK_DOLGU, _BASLIK_YAZI
        hucre.alignment = Alignment(vertical="center")
    for satir in satirlar:
        ws.append(satir)
    for idx in range(1, len(basliklar) + 1):
        harf = get_column_letter(idx)
        en = max([len(str(basliklar[idx - 1]))] + [len(str(s[idx - 1] or "")) for s in satirlar]) + 2
        ws.column_dimensions[harf].width = min(max(en, 10), 50)
        if idx in para_sutunlari or idx in kg_sutunlari:
            for hucre in ws[harf][1:]:
                hucre.number_format = _PARA_FORMAT if idx in para_sutunlari else _KG_FORMAT
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


def excel_olustur(tenant_id) -> bytes:
    tid = servis._tid(tenant_id)
    db = SessionLocal()
    try:
        cariler = db.query(YerelCari).filter(YerelCari.tenant_id == tid).order_by(YerelCari.ad).all()
        cari_ad = {c.id: c.ad for c in cariler}
        bakiyeler = servis._cari_bakiyeleri(db, tid)
        faturalar = db.query(YerelFatura).filter(YerelFatura.tenant_id == tid).order_by(YerelFatura.tarih, YerelFatura.id).all()
        fatura_no = {f.id: f.fatura_no for f in faturalar}
        hesaplar = db.query(YerelHesap).filter(YerelHesap.tenant_id == tid).order_by(YerelHesap.id).all()
        hesap_ad = {h.id: h.ad for h in hesaplar}

        wb = Workbook()
        wb.remove(wb.active)

        _sayfa(wb, "Cariler",
               ["Cari", "Tip", "Telefon", "E-posta", "Vergi No", "Vergi Dairesi", "Adres", "Bakiye"],
               [[c.ad, _TIP_ADI.get(c.tip, c.tip), c.telefon, c.email, c.vergi_no, c.vergi_dairesi,
                 c.adres, bakiyeler.get(c.id, 0.0)] for c in cariler],
               para_sutunlari=(8,))

        fatura_satir = []
        for f in faturalar:
            odenen = servis._fatura_odenen(db, f.id)
            kalan = round(f.brut_toplam - odenen, 2)
            durum = "İptal" if f.durum == "iptal" else _DURUM_ADI.get(servis._fatura_durum(f, kalan), "-")
            fatura_satir.append([f.fatura_no, "Satış" if f.tip == "customer" else "Alış",
                                 cari_ad.get(f.cari_id, "-"), _tr_tarih(f.tarih), _tr_tarih(f.vade), f.aciklama,
                                 f.ara_toplam, f.kdv_toplam, f.brut_toplam, max(kalan, 0.0), durum])
        _sayfa(wb, "Faturalar",
               ["Fatura No", "Tür", "Cari", "Tarih", "Vade", "Açıklama",
                "Ara Toplam", "KDV", "Toplam", "Kalan", "Durum"],
               fatura_satir, para_sutunlari=(7, 8, 9, 10))

        irs_satir = []
        for i in db.query(YerelIrsaliye).filter(YerelIrsaliye.tenant_id == tid).order_by(YerelIrsaliye.tarih, YerelIrsaliye.id).all():
            kalemler = json.loads(i.kalemler_json or "[]")
            irs_satir.append([i.id, cari_ad.get(i.cari_id, "-"), _tr_tarih(i.tarih), i.plaka, i.aciklama,
                              ", ".join(f"{k.get('aciklama')}: {_tr_kg(k.get('miktar_kg'))} kg" for k in kalemler),
                              sum(float(k.get("miktar_kg") or 0) for k in kalemler),
                              "Gelen (alış)" if i.inflow else "Giden (satış)",
                              "Silindi" if i.silindi else (fatura_no.get(i.fatura_id) or "Bekliyor")])
        _sayfa(wb, "İrsaliyeler",
               ["İrsaliye No", "Cari", "Tarih", "Plaka", "Açıklama", "Kalemler", "Toplam", "Yön", "Fatura"],
               irs_satir, kg_sutunlari=(7,))

        _sayfa(wb, "Tahsilat-Ödeme",
               ["Tarih", "Tür", "Cari", "Fatura No", "Kasa/Banka", "Tutar", "Açıklama"],
               [[_tr_tarih(h.tarih), _HAREKET_ADI.get(h.tur, h.tur), cari_ad.get(h.cari_id, "-") if h.cari_id else "-",
                 fatura_no.get(h.fatura_id, "-") if h.fatura_id else "-", hesap_ad.get(h.hesap_id, "-"),
                 h.tutar, h.aciklama]
                for h in db.query(YerelHareket).filter(
                    YerelHareket.tenant_id == tid, YerelHareket.silindi == 0
                ).order_by(YerelHareket.tarih, YerelHareket.id).all()],
               para_sutunlari=(6,))

        _sayfa(wb, "Kasa-Banka", ["Hesap", "Tür", "Para Birimi", "Bakiye"],
               [[h["name"], "Banka" if h["account_type"] == "bank" else "Kasa", h["currency"], h["balance"]]
                for h in servis.hesap_listesi_getir(tenant_id=tid)],
               para_sutunlari=(4,))

        _sayfa(wb, "Kantar Fişleri",
               ["Fiş ID", "Fiş No", "Plaka", "Firma", "Net Ağırlık", "Fiş Tarihi", "Durum", "Yüklenme"],
               [[t.id, t.fis_no, t.plaka, t.firma, t.agirlik_kg or 0,
                 t.fis_tarihi.strftime("%d.%m.%Y") if t.fis_tarihi else "",
                 _FIS_DURUM_ADI.get(t.status.value, t.status.value) if t.status else "", t.created_at.strftime("%d.%m.%Y %H:%M") if t.created_at else ""]
                for t in db.query(WeighTicket).filter(WeighTicket.tenant_id == tid).order_by(WeighTicket.id).all()],
               kg_sutunlari=(5,))

        wb.properties.title = "Profaix muhasebe kayıtları"
        wb.properties.created = datetime.now()
        tampon = io.BytesIO()
        wb.save(tampon)
        return tampon.getvalue()
    finally:
        db.close()
