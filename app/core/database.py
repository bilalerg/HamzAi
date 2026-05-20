# database.py — Veritabanı Bağlantısı ve Tablo Modelleri

from sqlalchemy import (
    create_engine, Column, String, Integer,
    DateTime, Text, Enum, ForeignKey, BigInteger, Float
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, relationship
from sqlalchemy.sql import func
import enum
from app.core.config import DATABASE_URL
from app.core.logger import logger

# ── BAĞLANTI HAVUZU ────────────────────────────────────────────────────
# Supabase ücretsiz plan ~20 bağlantı limiti var.
# pool_size=2     → kalıcı açık bağlantı sayısı (worker başına)
# max_overflow=3  → yoğunlukta ek bağlantı → worker başına max 5
# Render'da 4 worker × 5 = 20 bağlantı → Supabase limitine tam sığar
# pool_timeout=20 → bağlantı beklerken max süre (saniye)
# pool_recycle=300 → 5dk'da bir bağlantıyı yenile (Supabase idle timeout koruması)
# pool_pre_ping   → süresi dolmuş bağlantıyı kullanmadan önce test et

engine = create_engine(
    DATABASE_URL,
    pool_size=2,
    max_overflow=3,
    pool_timeout=20,
    pool_recycle=300,
    pool_pre_ping=True,
    echo=False
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class TicketStatus(str, enum.Enum):
    ALINDI                    = "ALINDI"
    OCR_TAMAMLANDI            = "OCR_TAMAMLANDI"
    OCR_DOGRULAMA_BEKLIYOR    = "OCR_DOGRULAMA_BEKLIYOR"
    PLAKA_KONTROL_BEKLENIYOR  = "PLAKA_KONTROL_BEKLENIYOR"
    IRSALIYE_OLUSTURULUYOR    = "IRSALIYE_OLUSTURULUYOR"
    IRSALIYE_TAMAMLANDI       = "IRSALIYE_TAMAMLANDI"
    FABRIKA_BEKLENIYOR        = "FABRIKA_BEKLENIYOR"
    FABRIKA_ITIRAZI           = "FABRIKA_ITIRAZI"
    FATURA_KESILIYOR          = "FATURA_KESILIYOR"
    TAMAMLANDI                = "TAMAMLANDI"
    IPTAL                     = "IPTAL"
    HATA                      = "HATA"


class Tenant(Base):
    __tablename__ = "tenants"
    id                       = Column(String(100), primary_key=True)  # profaix_user_id
    ad                       = Column(String(200), nullable=False)
    parasut_client_id        = Column(String(200), nullable=False)
    parasut_client_secret    = Column(String(200), nullable=False)
    parasut_username         = Column(String(200), nullable=False)
    # TODO: production'da encrypt edilmeli
    parasut_password         = Column(String(200), nullable=False)
    parasut_company_id       = Column(String(50), nullable=False)
    parasut_hurda_product_id = Column(String(50), nullable=True)
    aktif                    = Column(Integer, default=1)
    ozellikler               = Column(Text, nullable=True)  # JSON: {"dogru_parasut_akisi": true, ...}
    created_at               = Column(DateTime, server_default=func.now())
    updated_at               = Column(DateTime, server_default=func.now(), onupdate=func.now())
    def __repr__(self): return f"<Tenant id={self.id} ad={self.ad}>"


class Vehicle(Base):
    __tablename__ = "vehicles"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    plaka      = Column(String(20), unique=True, nullable=False, index=True)
    tenant_id  = Column(String(100), nullable=True, index=True)
    aktif      = Column(Integer, default=1)
    notlar     = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    tickets    = relationship("WeighTicket", back_populates="vehicle")
    def __repr__(self): return f"<Vehicle plaka={self.plaka}>"


class WeighTicket(Base):
    __tablename__ = "weigh_tickets"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id         = Column(String(100), nullable=True, index=True)
    foto_path         = Column(String(500), nullable=True)
    plaka             = Column(String(20), nullable=True, index=True)
    fis_no            = Column(String(50), nullable=True, index=True)
    agirlik_kg        = Column(Integer, nullable=True)
    net_tartim_kg     = Column(Integer, nullable=True)
    fire_kg           = Column(Integer, default=0, nullable=True)
    malzeme           = Column(String(100), nullable=True)
    firma             = Column(String(200), nullable=True)
    malzemeler        = Column(Text, nullable=True)
    fis_tarihi        = Column(DateTime, nullable=True)
    ocr_ham_cikti     = Column(Text, nullable=True)
    tesseract_agirlik = Column(Integer, nullable=True)
    ocr_uyusma             = Column(Integer, nullable=True)
    ocr_confidence_plaka   = Column(Float, nullable=True)
    ocr_confidence_agirlik = Column(Float, nullable=True)
    ocr_confidence_fis_no  = Column(Float, nullable=True)
    ocr_confidence_genel   = Column(Float, nullable=True)
    status            = Column(Enum(TicketStatus), default=TicketStatus.ALINDI, nullable=False)
    hata_mesaji       = Column(Text, nullable=True)
    vehicle_id        = Column(Integer, ForeignKey("vehicles.id"), nullable=True)
    created_at        = Column(DateTime, server_default=func.now())
    updated_at        = Column(DateTime, server_default=func.now(), onupdate=func.now())
    vehicle = relationship("Vehicle", back_populates="tickets")
    waybill = relationship("Waybill", back_populates="ticket", uselist=False)
    def __repr__(self): return f"<WeighTicket id={self.id} plaka={self.plaka} status={self.status}>"


class Waybill(Base):
    __tablename__ = "waybills"
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id           = Column(String(100), nullable=True, index=True)
    irsaliye_no         = Column(String(50), nullable=True, unique=True)
    ref_kodu            = Column(String(20), nullable=True, unique=True, index=True)
    fabrika_mail        = Column(String(200), nullable=True)
    mail_gonderildi_at  = Column(DateTime, nullable=True)
    teyit_geldi_at      = Column(DateTime, nullable=True)
    teyit_mail_icerik   = Column(Text, nullable=True)
    nlp_karar           = Column(String(20), nullable=True)
    parasut_irsaliye_id = Column(String(100), nullable=True)
    malzemeler_json     = Column(Text, nullable=True)
    status              = Column(Enum(TicketStatus), default=TicketStatus.IRSALIYE_OLUSTURULUYOR)
    ticket_id           = Column(Integer, ForeignKey("weigh_tickets.id"))
    created_at          = Column(DateTime, server_default=func.now())
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now())
    ticket  = relationship("WeighTicket", back_populates="waybill")
    invoice = relationship("Invoice", back_populates="waybill", uselist=False)
    def __repr__(self): return f"<Waybill irsaliye_no={self.irsaliye_no}>"


class Invoice(Base):
    __tablename__ = "invoices"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id         = Column(String(100), nullable=True, index=True)
    fatura_no         = Column(String(50), nullable=True, unique=True)
    tutar             = Column(BigInteger, nullable=True)
    birim_fiyat       = Column(Integer, nullable=True)
    malzeme_fiyatlari = Column(Text, nullable=True)
    parasut_id        = Column(String(100), nullable=True)
    parasut_response  = Column(Text, nullable=True)
    status            = Column(Enum(TicketStatus), default=TicketStatus.FATURA_KESILIYOR)
    waybill_id        = Column(Integer, ForeignKey("waybills.id"))
    created_at        = Column(DateTime, server_default=func.now())
    updated_at        = Column(DateTime, server_default=func.now(), onupdate=func.now())
    waybill = relationship("Waybill", back_populates="invoice")
    def __repr__(self): return f"<Invoice fatura_no={self.fatura_no} tutar={self.tutar}>"


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id  = Column(String(100), nullable=True, index=True)
    session_id = Column(String(100), unique=True, nullable=False, index=True)
    isim       = Column(String(100), nullable=True)
    gecmis     = Column(Text, nullable=True)
    tam_gecmis = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ActiveLogin(Base):
    __tablename__ = "active_logins"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    principal_key = Column(String(150), unique=True, nullable=False, index=True)
    session_id    = Column(String(100), nullable=False, index=True)
    username      = Column(String(100), nullable=True)
    user_agent    = Column(Text, nullable=True)
    ip_adresi     = Column(String(50), nullable=True)
    created_at    = Column(DateTime, server_default=func.now())
    updated_at    = Column(DateTime, server_default=func.now(), onupdate=func.now())
    def __repr__(self): return f"<ActiveLogin principal={self.principal_key}>"


class IslemLog(Base):
    __tablename__ = "islem_logs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id   = Column(String(100), nullable=True, index=True)
    islem_tipi  = Column(String(50), nullable=False, index=True)
    username    = Column(String(100), nullable=True, index=True)
    profaix_user_id = Column(String(100), nullable=True, index=True)
    cari_adi    = Column(String(200), nullable=True, index=True)
    tutar       = Column(BigInteger, nullable=True)
    fatura_id   = Column(String(100), nullable=True)
    irsaliye_id = Column(String(100), nullable=True)
    plaka       = Column(String(20), nullable=True)
    aciklama    = Column(Text, nullable=True)
    session_id  = Column(String(100), nullable=True)
    auth_session_id = Column(String(100), nullable=True)
    ip_adresi   = Column(String(50), nullable=True)
    created_at  = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<IslemLog tip={self.islem_tipi} cari={self.cari_adi}>"


class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    ad_soyad      = Column(String(100), nullable=True)
    rol           = Column(String(20), default="staff", nullable=False)
    aktif         = Column(Integer, default=1)
    son_giris     = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, server_default=func.now())
    def __repr__(self): return f"<User username={self.username} rol={self.rol}>"


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id  = Column(String(100), nullable=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=True)
    username   = Column(String(50), nullable=True)
    intent     = Column(String(50), nullable=True)
    params     = Column(Text, nullable=True)
    ip_adresi  = Column(String(50), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<AuditLog user={self.username} intent={self.intent}>"


class UsageLog(Base):
    __tablename__ = "usage_logs"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id       = Column(String(100), nullable=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=True)
    username        = Column(String(50), nullable=True, index=True)
    profaix_user_id = Column(String(100), nullable=True, index=True)
    islem_turu      = Column(String(50), nullable=False, index=True)
    aciklama        = Column(Text, nullable=True)
    ucretli         = Column(Integer, default=1)
    created_at      = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<UsageLog user={self.username} islem={self.islem_turu}>"


class BillableEvent(Base):
    __tablename__ = "billable_events"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    event_id        = Column(String(100), unique=True, nullable=False, index=True)
    event_key       = Column(String(200), unique=True, nullable=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=True)
    username        = Column(String(100), nullable=True, index=True)
    profaix_user_id = Column(String(100), nullable=True, index=True)
    event_type      = Column(String(50), nullable=False, index=True)
    amount          = Column(Integer, default=1, nullable=False)
    source          = Column(String(50), nullable=True)
    description     = Column(Text, nullable=True)
    metadata_json   = Column(Text, nullable=True)
    created_at      = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<BillableEvent user={self.username} event={self.event_type}>"


class HataLog(Base):
    __tablename__ = "hata_logs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    endpoint    = Column(String(100), nullable=True, index=True)
    hata_tipi   = Column(String(100), nullable=True)
    hata_mesaji = Column(Text, nullable=True)
    kullanici   = Column(String(100), nullable=True)
    ip_adresi   = Column(String(50), nullable=True)
    created_at  = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<HataLog endpoint={self.endpoint}>"


class TahsilatLog(Base):
    __tablename__ = "tahsilat_logs"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    cari_adi         = Column(String(200), nullable=False, index=True)
    cari_id          = Column(String(100), nullable=True)
    tutar            = Column(BigInteger, nullable=False)
    tarih            = Column(String(20), nullable=True)
    aciklama         = Column(Text, nullable=True)
    parasut_odeme_id = Column(String(100), nullable=True)
    kullanici        = Column(String(100), nullable=True)
    profaix_user_id  = Column(String(100), nullable=True)
    created_at       = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<TahsilatLog cari={self.cari_adi} tutar={self.tutar}>"


class OcrHataLog(Base):
    __tablename__ = "ocr_hata_logs"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    foto_boyut_kb    = Column(Integer, nullable=True)
    hata_mesaji      = Column(Text, nullable=True)
    claude_ham_cikti = Column(Text, nullable=True)
    kullanici        = Column(String(100), nullable=True)
    profaix_user_id  = Column(String(100), nullable=True)
    ip_adresi        = Column(String(50), nullable=True)
    created_at       = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<OcrHataLog kullanici={self.kullanici}>"


class GirisLog(Base):
    __tablename__ = "giris_logs"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    kullanici_adi = Column(String(100), nullable=True, index=True)
    basarili      = Column(Integer, default=0)
    giris_tipi    = Column(String(20), nullable=True)
    hata_sebebi   = Column(String(200), nullable=True)
    ip_adresi     = Column(String(50), nullable=True, index=True)
    created_at    = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<GirisLog kullanici={self.kullanici_adi} basarili={self.basarili}>"


def init_db():
    logger.info("Veritabanı tabloları oluşturuluyor...")
    Base.metadata.create_all(bind=engine)
    logger.info("✅ Tüm tablolar hazır")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()