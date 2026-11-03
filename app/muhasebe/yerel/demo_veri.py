# app/muhasebe/yerel/demo_veri.py — Tanıtım/demo için örnek muhasebe verisi
#
# Yerel modda ilk açılışta (o tenant'ın cari tablosu boşsa) çalışır; DEMO_VERI=0
# ile kapatılır. Firma adları ve plakalar UYDURMADIR.
#
# Elle sıfırlayıp yeniden yüklemek için:
#     python -m app.muhasebe.yerel.demo_veri --sifirla

import json
import random
import sys
from datetime import date, datetime, timedelta

from app.core.database import SessionLocal, WeighTicket, Waybill, TicketStatus
from app.core.logger import logger
from app.muhasebe.yerel import servis
from app.muhasebe.yerel.modeller import YerelCari, YerelFatura, YerelHareket, YerelHesap, YerelIrsaliye

TEDARIKCILER = [
    ("Yıldız Hurdacılık",          "0532 418 22 10", "Gebze / Kocaeli"),
    ("Demirtaş Geri Dönüşüm",      "0544 301 77 45", "Dilovası / Kocaeli"),
    ("Kaya Metal Hurda",           "0505 612 90 03", "Tuzla / İstanbul"),
    ("Anadolu Hurda Ticaret",      "0533 227 48 61", "Çayırova / Kocaeli"),
    ("Ege Metal Toplama",          "0542 889 15 37", "Kemalpaşa / İzmir"),
]
MUSTERILER = [
    ("Marmara Çelik Fabrikası A.Ş.", "0262 555 40 00", "Dilovası OSB / Kocaeli"),
    ("Körfez Döküm San. Tic.",       "0262 744 12 12", "Körfez / Kocaeli"),
]
IKISI_BIRDEN = [("Öztürk Metal Ltd. Şti.", "0216 493 30 30", "Pendik / İstanbul")]

PLAKALAR = ["41ABK312", "34TRK708", "41HD5521", "35EGM094", "16BRS620", "41KM2217", "34HRD415", "54SKR903"]
MALZEMELER = ["Ekstra", "DKP", "Talaş", "Greyder"]


def _gun(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")


def _sifirla(db, tid: str):
    for model in (YerelHareket, YerelFatura, YerelIrsaliye, YerelHesap, YerelCari):
        db.query(model).filter(model.tenant_id == tid).delete()
    ticket_ids = [t.id for t in db.query(WeighTicket.id).filter(WeighTicket.tenant_id == tid).all()]
    if ticket_ids:
        db.query(Waybill).filter(Waybill.ticket_id.in_(ticket_ids)).delete(synchronize_session=False)
    db.query(Waybill).filter(Waybill.tenant_id == tid).delete(synchronize_session=False)
    db.query(WeighTicket).filter(WeighTicket.tenant_id == tid).delete(synchronize_session=False)
    db.commit()


def _irsaliye_ve_fis(db, tid, cari, gun_once, plaka, kalemler: dict, durum: TicketStatus) -> YerelIrsaliye:
    """Kantar fişi + yerel irsaliye + waybill kaydını birlikte oluşturur (gerçek akıştaki gibi)."""
    toplam = sum(kalemler.values())
    tarih = _gun(gun_once)
    fis = WeighTicket(
        tenant_id=tid, plaka=plaka, fis_no=f"KF{random.randint(100000, 999999)}",
        agirlik_kg=toplam, net_tartim_kg=toplam + 40, fire_kg=40, malzeme="hurda",
        malzemeler=json.dumps(kalemler, ensure_ascii=False) if len(kalemler) > 1 else None,
        firma=cari.ad, fis_tarihi=datetime.strptime(tarih, "%Y-%m-%d"),
        ocr_ham_cikti="demo_veri", status=durum,
        ocr_confidence_plaka=0.95, ocr_confidence_agirlik=0.93,
        ocr_confidence_fis_no=0.9, ocr_confidence_genel=0.93,
    )
    db.add(fis)
    db.flush()
    if gun_once > 0:
        fis.created_at = datetime.strptime(tarih, "%Y-%m-%d") + timedelta(hours=random.randint(8, 17))
    irs = YerelIrsaliye(
        tenant_id=tid, cari_id=cari.id, tarih=tarih, inflow=1, plaka=plaka,
        aciklama=f"Kantar Fişi — {plaka} — {toplam} kg",
        kalemler_json=json.dumps([{"aciklama": ad if len(kalemler) > 1 else "Hurda", "miktar_kg": kg}
                                  for ad, kg in kalemler.items()], ensure_ascii=False),
    )
    db.add(irs)
    db.flush()
    db.add(Waybill(tenant_id=tid, irsaliye_no=str(irs.id), parasut_irsaliye_id=str(irs.id),
                   status=durum, ticket_id=fis.id,
                   malzemeler_json=json.dumps(kalemler, ensure_ascii=False) if len(kalemler) > 1 else None))
    db.commit()
    return irs


def demo_veri_yukle(tenant_id=None, sifirla: bool = False) -> bool:
    tid = servis._tid(tenant_id)
    db = SessionLocal()
    try:
        if sifirla:
            _sifirla(db, tid)
        elif db.query(YerelCari).filter(YerelCari.tenant_id == tid).first():
            return False   # zaten veri var — dokunma

        random.seed(2026)
        logger.info(f"🌱 Demo muhasebe verisi yükleniyor (tenant={tid})...")

        def _cari(ad, tel, adres, tip):
            c = YerelCari(tenant_id=tid, ad=ad, tip=tip, telefon=tel, adres=adres,
                          vergi_no=str(random.randint(1000000000, 9999999999)),
                          vergi_dairesi=adres.split("/")[-1].strip())
            db.add(c)
            return c

        tedarikciler = [_cari(*t, "supplier") for t in TEDARIKCILER]
        musteriler   = [_cari(*m, "customer") for m in MUSTERILER]
        ikisi        = [_cari(*i, "customer_supplier") for i in IKISI_BIRDEN]
        kasa  = YerelHesap(tenant_id=tid, ad="Merkez Kasa", tip="cash", acilis_bakiyesi=185000.0)
        banka = YerelHesap(tenant_id=tid, ad="Banka Hesabı (TL)", tip="bank", acilis_bakiyesi=1250000.0)
        db.add_all([kasa, banka])
        db.commit()

        alici_cariler = tedarikciler + ikisi
        # ── 1) Geçmiş alımlar: irsaliye → alış faturası → (çoğu) ödendi ──
        for gun in (27, 24, 21, 18, 15, 12, 9, 7):
            cari  = random.choice(alici_cariler)
            plaka = random.choice(PLAKALAR)
            if random.random() < 0.35:
                kalemler = {"Ekstra": random.randint(6, 12) * 1000, "DKP": random.randint(3, 8) * 1000}
            else:
                kalemler = {"Hurda": random.randint(12, 26) * 1000 + random.choice([0, 250, 500, 750])}
            irs = _irsaliye_ve_fis(db, tid, cari, gun, plaka, kalemler, TicketStatus.TAMAMLANDI)
            fiyatlar = {"Ekstra": 13.40, "DKP": 12.10, "Hurda": round(random.uniform(11.8, 13.2), 2)}
            f = servis._fatura_kaydet(
                db, tid, "supplier", cari.id,
                [{"aciklama": f"{ad} — {kg:,} kg", "miktar": kg, "birim_fiyat": fiyatlar[ad], "kdv_orani": 0}
                 for ad, kg in kalemler.items()],
                f"Hurda Alım — İrsaliye {irs.id}", _gun(gun), _gun(gun - 5 if gun > 5 else 0), "TRY", [irs.id])
            if gun > 10:   # eski faturalar ödenmiş, yeniler açık
                tutar = f.brut_toplam if random.random() < 0.7 else round(f.brut_toplam * 0.5, 2)
                servis._hareket_ekle(db, tid, "fatura_odeme", tutar, _gun(gun - 2), "Hurda alım ödemesi",
                                     cari_id=cari.id, fatura_id=f.id, hesap_id=banka.id)
                db.commit()

        # ── 2) Fabrikalara satışlar + tahsilatlar ──
        for gun, musteri, kg, fiyat, tahsil in (
            (26, musteriler[0], 48500, 14.60, 1.0),
            (19, musteriler[1], 31200, 14.35, 0.6),
            (13, musteriler[0], 52750, 14.80, 0.0),
            (6,  ikisi[0],      18400, 14.20, 0.0),
        ):
            f = servis._fatura_kaydet(
                db, tid, "customer", musteri.id,
                [{"aciklama": f"Hurda — {kg:,} kg", "miktar": kg, "birim_fiyat": fiyat, "kdv_orani": 0}],
                "Hurda Satış", _gun(gun), _gun(max(gun - 15, 0)), "TRY")
            if tahsil:
                servis._hareket_ekle(db, tid, "fatura_tahsilat", round(f.brut_toplam * tahsil, 2), _gun(gun - 3),
                                     f"{musteri.ad} tahsilat", cari_id=musteri.id, fatura_id=f.id,
                                     hesap_id=banka.id)
                db.commit()

        # ── 3) Faturası kesilmemiş (bekleyen) irsaliyeler — demo'da fatura kesilecek ──
        for gun, cari_idx, plaka, kalemler in (
            (4, 0, "41ABK312", {"Hurda": 18750}),
            (3, 2, "34TRK708", {"Ekstra": 9000, "DKP": 6500}),
            (2, 0, "41HD5521", {"Hurda": 21300}),
            (1, 3, "16BRS620", {"Hurda": 14800}),
        ):
            _irsaliye_ve_fis(db, tid, tedarikciler[cari_idx], gun, plaka, kalemler, TicketStatus.FABRIKA_BEKLENIYOR)

        # ── 4) Küçük kasa hareketleri ──
        servis._hareket_ekle(db, tid, "cari_odeme", 25000, _gun(5), "Nakit avans — hurda alımı",
                             cari_id=tedarikciler[4].id, hesap_id=kasa.id)
        servis._hareket_ekle(db, tid, "transfer_cikis", 100000, _gun(8), "Kasadan bankaya", hesap_id=kasa.id)
        servis._hareket_ekle(db, tid, "transfer_giris", 100000, _gun(8), "Kasadan bankaya", hesap_id=banka.id)
        db.commit()
        logger.info("✅ Demo muhasebe verisi yüklendi")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Demo veri yükleme hatası: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    from app.core.database import init_db
    init_db()
    yuklendi = demo_veri_yukle(sifirla="--sifirla" in sys.argv)
    print("Demo veri yüklendi." if yuklendi else "Zaten veri var (sıfırlamak için --sifirla).")
