# app/muhasebe/yerel/modeller.py — Yerel muhasebe sağlayıcısının tabloları
#
# Paraşüt bağlı değilken cari, irsaliye, fatura, tahsilat/ödeme ve kasa/banka
# kayıtları Profaix'in kendi veritabanında bu tablolarda tutulur.
# Tablolar app.core.database.Base'e kayıtlıdır → init_db() create_all ile oluşturur.
#
# Bakiye işaret kuralı (Paraşüt ile aynı):
#   bakiye > 0  → cari bize borçlu (alacağımız var)
#   bakiye < 0  → biz cariye borçluyuz
#   satış faturası +, alış faturası −, tahsilat −, ödeme +

from sqlalchemy import Column, String, Integer, Float, DateTime, Text
from sqlalchemy.sql import func

from app.core.database import Base


class YerelCari(Base):
    __tablename__ = "yerel_cariler"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id     = Column(String(100), nullable=False, index=True)
    ad            = Column(String(200), nullable=False, index=True)
    tip           = Column(String(30), nullable=False, default="customer")  # customer|supplier|customer_supplier
    email         = Column(String(200), nullable=True)
    telefon       = Column(String(50), nullable=True)
    vergi_no      = Column(String(30), nullable=True)
    vergi_dairesi = Column(String(100), nullable=True)
    adres         = Column(Text, nullable=True)
    created_at    = Column(DateTime, server_default=func.now())
    updated_at    = Column(DateTime, server_default=func.now(), onupdate=func.now())


class YerelHesap(Base):
    """Kasa / banka hesabı."""
    __tablename__ = "yerel_hesaplar"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id       = Column(String(100), nullable=False, index=True)
    ad              = Column(String(100), nullable=False)
    tip             = Column(String(20), nullable=False, default="cash")   # cash|bank
    para_birimi     = Column(String(10), nullable=False, default="TRY")
    acilis_bakiyesi = Column(Float, nullable=False, default=0.0)
    created_at      = Column(DateTime, server_default=func.now())


class YerelIrsaliye(Base):
    __tablename__ = "yerel_irsaliyeler"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id     = Column(String(100), nullable=False, index=True)
    cari_id       = Column(Integer, nullable=False, index=True)
    tarih         = Column(String(10), nullable=False)          # YYYY-MM-DD
    aciklama      = Column(Text, nullable=True)
    inflow        = Column(Integer, nullable=False, default=1)  # 1=gelen (alış), 0=giden
    plaka         = Column(String(20), nullable=True)
    kalemler_json = Column(Text, nullable=True)                 # [{"aciklama":..., "miktar_kg":...}]
    fatura_id     = Column(Integer, nullable=True, index=True)  # faturalanınca dolar
    silindi       = Column(Integer, nullable=False, default=0)
    created_at    = Column(DateTime, server_default=func.now())


class YerelFatura(Base):
    __tablename__ = "yerel_faturalar"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id      = Column(String(100), nullable=False, index=True)
    tip            = Column(String(20), nullable=False, index=True)   # customer (satış) | supplier (alış)
    cari_id        = Column(Integer, nullable=False, index=True)
    fatura_no      = Column(String(50), nullable=True)
    tarih          = Column(String(10), nullable=False)
    vade           = Column(String(10), nullable=True)
    aciklama       = Column(Text, nullable=True)
    para_birimi    = Column(String(10), nullable=False, default="TRY")
    kalemler_json  = Column(Text, nullable=True)   # [{"aciklama","miktar","birim_fiyat","kdv_orani"}]
    ara_toplam     = Column(Float, nullable=False, default=0.0)
    kdv_toplam     = Column(Float, nullable=False, default=0.0)
    brut_toplam    = Column(Float, nullable=False, default=0.0)
    durum          = Column(String(20), nullable=False, default="aktif")  # aktif|iptal|arsiv
    ek_alanlar_json = Column(Text, nullable=True)  # fatura-güncelle ile gelen diğer alanlar
    created_at     = Column(DateTime, server_default=func.now())
    updated_at     = Column(DateTime, server_default=func.now(), onupdate=func.now())


class YerelHareket(Base):
    """
    Para hareketi. tur değerleri:
      fatura_tahsilat  → satış faturasına tahsilat   (cari bakiyesi −, kasa +)
      cari_tahsilat    → faturasız cari tahsilatı     (cari bakiyesi −, kasa +)
      fatura_odeme     → alış faturasına ödeme        (cari bakiyesi +, kasa −)
      cari_odeme       → faturasız cari ödemesi       (cari bakiyesi +, kasa −)
      transfer_cikis / transfer_giris → hesaplar arası transfer
    """
    __tablename__ = "yerel_hareketler"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id  = Column(String(100), nullable=False, index=True)
    tur        = Column(String(30), nullable=False, index=True)
    cari_id    = Column(Integer, nullable=True, index=True)
    fatura_id  = Column(Integer, nullable=True, index=True)
    hesap_id   = Column(Integer, nullable=True, index=True)
    tutar      = Column(Float, nullable=False)
    tarih      = Column(String(10), nullable=False)
    aciklama   = Column(Text, nullable=True)
    silindi    = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
