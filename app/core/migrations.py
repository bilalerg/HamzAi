# migrations.py — Startup Migration ve Recovery
#
# main.py'de inline SQL migration vardı — bu dosyaya taşındı.
# İleride Alembic'e geçişte sadece bu dosyayı değiştirmek yeterli.

from app.core.logger import logger


def calistir(engine, db):
    """
    Uygulama başlangıcında çalışır.
    1. Eksik kolonları ekler (ADD COLUMN IF NOT EXISTS)
    2. Eksik tabloları oluşturur
    3. Yarım kalan işlemleri tespit eder
    4. İlk tenant kaydını oluşturur

    Senkron fonksiyon — main.py'de asyncio.to_thread ile çağrılır.
    """
    from sqlalchemy import text

    from app.core.config import IS_SQLITE
    if IS_SQLITE:
        # SQLite (lokal/demo): şema create_all ile modellerden eksiksiz kurulur;
        # aşağıdaki SQL'ler PostgreSQL'e özgü olduğundan atlanır.
        logger.info("✅ SQLite: şema modellerden kuruldu, PostgreSQL migration'ları atlandı")
    else:
        with engine.connect() as conn:

            # ── TABLO MİGRATIONLARI ───────────────────────────────────────
            _tablo_olustur(conn, text("""
                CREATE TABLE IF NOT EXISTS tenants (
                    id VARCHAR(100) PRIMARY KEY,
                    ad VARCHAR(200) NOT NULL,
                    parasut_client_id VARCHAR(200) NOT NULL,
                    parasut_client_secret VARCHAR(200) NOT NULL,
                    parasut_username VARCHAR(200) NOT NULL,
                    parasut_password VARCHAR(200) NOT NULL,
                    parasut_company_id VARCHAR(50) NOT NULL,
                    parasut_hurda_product_id VARCHAR(50),
                    aktif INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """), "tenants")

            _tablo_olustur(conn, text("""
                CREATE TABLE IF NOT EXISTS islem_logs (
                    id SERIAL PRIMARY KEY,
                    islem_tipi VARCHAR(50) NOT NULL,
                    cari_adi VARCHAR(200),
                    tutar BIGINT,
                    fatura_id VARCHAR(100),
                    irsaliye_id VARCHAR(100),
                    plaka VARCHAR(20),
                    aciklama TEXT,
                    session_id VARCHAR(100),
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """), "islem_logs")

            _tablo_olustur(conn, text("""
                CREATE TABLE IF NOT EXISTS active_logins (
                    id SERIAL PRIMARY KEY,
                    principal_key VARCHAR(150) UNIQUE NOT NULL,
                    session_id VARCHAR(100) NOT NULL,
                    username VARCHAR(100),
                    user_agent TEXT,
                    ip_adresi VARCHAR(50),
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """), "active_logins")

            _tablo_olustur(conn, text("""
                CREATE TABLE IF NOT EXISTS billable_events (
                    id SERIAL PRIMARY KEY,
                    event_id VARCHAR(100) UNIQUE NOT NULL,
                    event_key VARCHAR(200) UNIQUE,
                    user_id INTEGER,
                    username VARCHAR(100),
                    profaix_user_id VARCHAR(100),
                    event_type VARCHAR(50) NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 1,
                    source VARCHAR(50),
                    description TEXT,
                    metadata_json TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """), "billable_events")

            # ── KOLON MİGRATIONLARI ───────────────────────────────────────
            _kolon_ekle(conn, text(
                "ALTER TABLE waybills ADD COLUMN IF NOT EXISTS malzemeler_json TEXT"
            ), "malzemeler_json (waybills)")

            _kolon_ekle(conn, text(
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS tam_gecmis TEXT"
            ), "tam_gecmis (chat_sessions)")

            for kolon, sql_tip in [
                ("username", "VARCHAR(100)"),
                ("profaix_user_id", "VARCHAR(100)"),
                ("auth_session_id", "VARCHAR(100)"),
                ("ip_adresi", "VARCHAR(50)"),
            ]:
                _kolon_ekle(conn, text(
                    f"ALTER TABLE islem_logs ADD COLUMN IF NOT EXISTS {kolon} {sql_tip}"
                ), f"{kolon} (islem_logs)")

            # ── TENANT_ID KOLON MİGRATIONLARI ────────────────────────────
            tenant_kolonlu_tablolar = [
                "weigh_tickets",
                "waybills",
                "invoices",
                "islem_logs",
                "usage_logs",
                "chat_sessions",
                "vehicles",
                "audit_logs",
            ]
            for tablo in tenant_kolonlu_tablolar:
                _kolon_ekle(conn, text(
                    f"ALTER TABLE {tablo} ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(100)"
                ), f"tenant_id ({tablo})")

            # ── TENANT ÖZELLİKLERİ KOLONU ───────────────────────────────────
            _kolon_ekle(conn, text(
                "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ozellikler TEXT"
            ), "ozellikler (tenants)")

            # ── OCR CONFIDENCE KOLONLARI ──────────────────────────────────────
            _kolon_ekle(conn, text(
                "ALTER TABLE weigh_tickets ADD COLUMN IF NOT EXISTS ocr_confidence_plaka FLOAT"
            ), "ocr_confidence_plaka (weigh_tickets)")
            _kolon_ekle(conn, text(
                "ALTER TABLE weigh_tickets ADD COLUMN IF NOT EXISTS ocr_confidence_agirlik FLOAT"
            ), "ocr_confidence_agirlik (weigh_tickets)")
            _kolon_ekle(conn, text(
                "ALTER TABLE weigh_tickets ADD COLUMN IF NOT EXISTS ocr_confidence_fis_no FLOAT"
            ), "ocr_confidence_fis_no (weigh_tickets)")
            _kolon_ekle(conn, text(
                "ALTER TABLE weigh_tickets ADD COLUMN IF NOT EXISTS ocr_confidence_genel FLOAT"
            ), "ocr_confidence_genel (weigh_tickets)")

            # ── TOKEN/BILLING: company_id (sirket bazli kullanim sayaci) ──────
            _kolon_ekle(conn, text(
                "ALTER TABLE billable_events ADD COLUMN IF NOT EXISTS company_id VARCHAR(100)"
            ), "company_id (billable_events)")

            # ── INDEX MİGRATIONLARI ───────────────────────────────────────
            _index_olustur(conn, text(
                "CREATE INDEX IF NOT EXISTS idx_weigh_tickets_tenant_status "
                "ON weigh_tickets(tenant_id, status)"
            ), "idx_weigh_tickets_tenant_status")

            _index_olustur(conn, text(
                "CREATE INDEX IF NOT EXISTS idx_waybills_tenant_status "
                "ON waybills(tenant_id, status)"
            ), "idx_waybills_tenant_status")

            _index_olustur(conn, text(
                "CREATE INDEX IF NOT EXISTS idx_invoices_tenant_status "
                "ON invoices(tenant_id, status)"
            ), "idx_invoices_tenant_status")

            _index_olustur(conn, text(
                "CREATE INDEX IF NOT EXISTS idx_billable_events_user_date "
                "ON billable_events(profaix_user_id, created_at)"
            ), "idx_billable_events_user_date")

            _index_olustur(conn, text(
                "CREATE INDEX IF NOT EXISTS idx_waybills_parasut_irsaliye_id "
                "ON waybills(parasut_irsaliye_id)"
            ), "idx_waybills_parasut_irsaliye_id")

    # ── YARIMA KALMIŞ İŞLEM TESPİTİ ──────────────────────────────────
    from app.core.database import WeighTicket, TicketStatus
    bekleyenler = db.query(WeighTicket).filter(
        WeighTicket.status == TicketStatus.IRSALIYE_OLUSTURULUYOR
    ).all()
    if bekleyenler:
        logger.warning(f"⚠️ {len(bekleyenler)} yarım kalan işlem tespit edildi (IRSALIYE_OLUSTURULUYOR)")
    else:
        logger.info("✅ Yarım kalan işlem yok")

    # ── İLK TENANT OLUŞTUR ────────────────────────────────────────────
    _ilk_tenant_olustur(db, engine)

    # ── CREDENTIAL ŞİFRELEME ─────────────────────────────────────────
    _credential_sifreleme_migrasyonu(db)


def _ilk_tenant_olustur(db, engine):
    """
    Tenants tablosu boşsa Bilal'in tenant kaydını oluşturur.
    Mevcut NULL tenant_id kayıtlarını bu tenant'a atar.
    Sadece bir kez çalışır — idempotent.
    """
    from app.core.database import Tenant
    from app.core.config import (
        PARASUT_CLIENT_ID, PARASUT_CLIENT_SECRET,
        PARASUT_USERNAME, PARASUT_PASSWORD,
        PARASUT_COMPANY_ID, PARASUT_HURDA_PRODUCT_ID,
    )
    from sqlalchemy import text

    if not PARASUT_USERNAME:
        from app.core.config import MUHASEBE_SAGLAYICI
        if MUHASEBE_SAGLAYICI == "parasut":
            logger.warning("⚠️ PARASUT_USERNAME boş — varsayılan tenant oluşturulamadı")
        return

    try:
        mevcut = db.query(Tenant).first()
        if not mevcut:
            tenant = Tenant(
                id=PARASUT_USERNAME,
                ad="Varsayılan Firma",
                parasut_client_id=PARASUT_CLIENT_ID or "",
                parasut_client_secret=PARASUT_CLIENT_SECRET or "",
                parasut_username=PARASUT_USERNAME,
                parasut_password=PARASUT_PASSWORD or "",
                parasut_company_id=PARASUT_COMPANY_ID or "",
                parasut_hurda_product_id=PARASUT_HURDA_PRODUCT_ID or None,
                aktif=1,
            )
            db.add(tenant)
            db.commit()
            logger.info(f"✅ Varsayılan tenant oluşturuldu: id={PARASUT_USERNAME}")
        else:
            logger.info(f"✅ Tenant kaydı mevcut, atlandı")
            PARASUT_USERNAME  # varsayılan tenant id'yi al

        # NULL tenant_id kayıtlarını varsayılan tenanta bağla
        default_tid = PARASUT_USERNAME
        with engine.connect() as conn:
            for tablo in ["weigh_tickets", "waybills", "invoices",
                          "islem_logs", "usage_logs", "chat_sessions",
                          "vehicles", "audit_logs"]:
                try:
                    result = conn.execute(text(
                        f"UPDATE {tablo} SET tenant_id = :tid WHERE tenant_id IS NULL"
                    ), {"tid": default_tid})
                    conn.commit()
                    if result.rowcount:
                        logger.info(f"✅ {result.rowcount} {tablo} kaydı varsayılan tenanta atandı")
                except Exception as e:
                    logger.warning(f"NULL tenant_id güncelleme atlandı ({tablo}): {e}")
    except Exception as e:
        logger.error(f"İlk tenant oluşturma hatası: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def _credential_sifreleme_migrasyonu(db):
    """
    CREDENTIAL_ENCRYPTION_KEY varsa mevcut düz metin credential'ları şifreler.
    Zaten şifreli olanları (gAAAAA ile başlayan) atlar. Idempotent.
    """
    from app.core.config import CREDENTIAL_ENCRYPTION_KEY
    if not CREDENTIAL_ENCRYPTION_KEY:
        return
    from app.core.database import Tenant
    from app.core.tenant import _encrypt_cred
    try:
        tenantlar = db.query(Tenant).all()
        guncellenen = 0
        for tenant in tenantlar:
            degisti = False
            if tenant.parasut_client_secret and not tenant.parasut_client_secret.startswith("gAAAAA"):
                tenant.parasut_client_secret = _encrypt_cred(tenant.parasut_client_secret)
                degisti = True
            if tenant.parasut_password and not tenant.parasut_password.startswith("gAAAAA"):
                tenant.parasut_password = _encrypt_cred(tenant.parasut_password)
                degisti = True
            if degisti:
                guncellenen += 1
        if guncellenen:
            db.commit()
            logger.info(f"✅ {guncellenen} tenant credential şifrelendi")
        else:
            logger.info("✅ Credential şifreleme: tüm kayıtlar güncel")
    except Exception as e:
        logger.error(f"Credential şifreleme migrasyonu hatası: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def _index_olustur(conn, stmt, isim: str):
    try:
        conn.execute(stmt)
        conn.commit()
        logger.info(f"✅ Migration: {isim} index hazır")
    except Exception as e:
        logger.warning(f"Migration atlandı ({isim}): {e}")


def _kolon_ekle(conn, stmt, isim: str):
    try:
        conn.execute(stmt)
        conn.commit()
        logger.info(f"✅ Migration: {isim} kolonu hazır")
    except Exception as e:
        logger.warning(f"Migration atlandı ({isim}): {e}")


def _tablo_olustur(conn, stmt, isim: str):
    try:
        conn.execute(stmt)
        conn.commit()
        logger.info(f"✅ Migration: {isim} tablosu hazır")
    except Exception as e:
        logger.warning(f"Migration atlandı ({isim}): {e}")
