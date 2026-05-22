"""
db_temizle.py — Veritabanı Fiş Temizleme Scripti
=================================================
Test sırasında aynı fişi tekrar tekrar yükleyince duplicate kontrolü
(fis_no eşleşmesi) çakıyor. Bu script DB'deki fiş zincirini temizler:

    invoices  →  waybills  →  weigh_tickets   (FK sırasına uygun)
    (opsiyonel) vehicles   → "yeni plaka" tespitini de sıfırlar

NOT: Bu sadece KENDİ veritabanını temizler, Paraşüt'e DOKUNMAZ.
     (Paraşüt'teki irsaliye/fatura için: parasut_temizle.py)

KULLANIM:
    python db_temizle.py

UYARI: GERİ ALINAMAZ. Sadece test verisi temizlemek için kullan.
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

# .env yükle (Render'da /etc/secrets/.env, lokalde ./.env)
secret_env = Path("/etc/secrets/.env")
load_dotenv(secret_env if secret_env.exists() else None)

from app.core.database import SessionLocal, WeighTicket, Waybill, Invoice, Vehicle


def _sayilar(db, tenant_id=None):
    q_t = db.query(WeighTicket)
    q_v = db.query(Vehicle)
    if tenant_id:
        q_t = q_t.filter(WeighTicket.tenant_id == tenant_id)
        q_v = q_v.filter(Vehicle.tenant_id == tenant_id)
    return q_t.count(), db.query(Waybill).count(), db.query(Invoice).count(), q_v.count()


def fisleri_sil(tenant_id=None, araclar_dahil=False):
    """
    Fiş zincirini FK-güvenli sırada siler.
    tenant_id verilirse sadece o tenant'ın fişleri; None → TÜM tenantlar.
    """
    db = SessionLocal()
    try:
        if tenant_id:
            # Tenant'a ait ticket id'leri üzerinden zinciri yürü (FK-güvenli)
            ticket_ids = [r[0] for r in db.query(WeighTicket.id)
                          .filter(WeighTicket.tenant_id == tenant_id).all()]
            waybill_ids = [r[0] for r in db.query(Waybill.id)
                           .filter(Waybill.ticket_id.in_(ticket_ids)).all()] if ticket_ids else []

            inv = db.query(Invoice).filter(Invoice.waybill_id.in_(waybill_ids)).delete(synchronize_session=False) if waybill_ids else 0
            wb  = db.query(Waybill).filter(Waybill.ticket_id.in_(ticket_ids)).delete(synchronize_session=False) if ticket_ids else 0
            tk  = db.query(WeighTicket).filter(WeighTicket.id.in_(ticket_ids)).delete(synchronize_session=False) if ticket_ids else 0
            vh  = 0
            if araclar_dahil:
                vh = db.query(Vehicle).filter(Vehicle.tenant_id == tenant_id).delete(synchronize_session=False)
        else:
            # TÜM tenantlar — tablo sırasıyla boşalt
            inv = db.query(Invoice).delete(synchronize_session=False)
            wb  = db.query(Waybill).delete(synchronize_session=False)
            tk  = db.query(WeighTicket).delete(synchronize_session=False)
            vh  = db.query(Vehicle).delete(synchronize_session=False) if araclar_dahil else 0

        db.commit()
        print(f"\n✅ Silindi → fatura: {inv} | irsaliye: {wb} | fiş: {tk} | araç: {vh}")
    except Exception as e:
        db.rollback()
        print(f"\n❌ Hata, işlem geri alındı: {e}")
        raise
    finally:
        db.close()


def main():
    print("=" * 60)
    print("  VERİTABANI FİŞ TEMİZLEME")
    print("=" * 60)

    db = SessionLocal()
    try:
        t, w, i, v = _sayilar(db)
        print(f"  Mevcut → fiş: {t} | irsaliye: {w} | fatura: {i} | araç: {v}")
    finally:
        db.close()

    print()
    print("Kapsam:")
    print("  1 — TÜM tenantların fişlerini sil")
    print("  2 — Belirli bir tenant'ın fişlerini sil (tenant_id girersin)")
    kapsam = input("Seçim (1/2): ").strip()

    tenant_id = None
    if kapsam == "2":
        tenant_id = input("tenant_id (profaix_user_id): ").strip()
        if not tenant_id:
            print("İptal: tenant_id boş.")
            sys.exit(0)
    elif kapsam != "1":
        print("İptal: geçersiz seçim.")
        sys.exit(0)

    araclar = input("Araçlar (vehicles) da silinsin mi? — yeni plaka tespiti sıfırlanır (e/h): ").strip().lower() == "e"

    hedef = "TÜM tenantlar" if tenant_id is None else f"tenant={tenant_id}"
    onay = input(f"\n⚠️  GERİ ALINAMAZ. {hedef} için fişler silinecek. Onaylıyor musun? (evet/hayir): ")
    if onay.lower() != "evet":
        print("İptal edildi.")
        sys.exit(0)

    fisleri_sil(tenant_id=tenant_id, araclar_dahil=araclar)
    print("\n✅ İşlem tamamlandı! Artık aynı fişleri tekrar yükleyebilirsin.")


if __name__ == "__main__":
    main()
