# tests/test_yerel_muhasebe.py — Yerel muhasebe sağlayıcısı: para işleyen akışlar
#
# Çalıştır:  python -m pytest -q

import io

from openpyxl import load_workbook


def _cari(client, ad):
    return next(c for c in client.get("/api/cariler").json()["cariler"] if c["ad"] == ad)


def _bekleyenler(client, cari=None):
    liste = client.get("/api/bekleyen-irsaliyeler").json()["irsaliyeler"]
    return [i for i in liste if cari is None or i["cari_adi"] == cari]


def test_saglayici_yerel(client):
    durum = client.get("/api/muhasebe/durum").json()
    assert durum["saglayici"] == "yerel"
    assert durum["ai_aktif"] is False


def test_demo_veri_ve_bakiye_isaretleri(client, demo):
    cariler = {c["ad"]: c for c in client.get("/api/cariler").json()["cariler"]}
    assert len(cariler) == 8
    # Müşteriye satış → bize borçlu (+); tedarikçiden alış → biz borçluyuz (−)
    assert cariler["Marmara Çelik Fabrikası A.Ş."]["bakiye"] > 0
    assert cariler["Yıldız Hurdacılık"]["bakiye"] < 0
    assert len(_bekleyenler(client)) == 4


def test_fis_irsaliye_fatura_odeme_akisi(client, demo):
    # 1) Fiş yükle — Claude anahtarı yok → elle giriş modu
    up = client.post("/api/upload", files={"file": ("fis.jpg", b"\xff\xd8\xff\xe0demo", "image/jpeg")}).json()
    assert up["bekliyor"] == "firma_sec" and up["manuel_giris"] is True
    assert up["conf_plaka"] == 0.0     # arayüz formu düzenleme modunda açar

    # 2) Firma seç + elle düzelt → irsaliye
    fs = client.post("/api/firma-sec", json={
        "ticket_id": up["ticket_id"], "firma": "Kaya Metal Hurda", "plaka": "34 ABC 123",
        "fis_no": "KF-T-1", "agirlik_kg": 16500, "fire_kg": 50, "tarih": "03.11.2026", "malzemeler": {},
    }).json()
    assert fs["bekliyor"] == "bitti", fs
    irs = next(i for i in _bekleyenler(client, "Kaya Metal Hurda") if i["toplam_kg"] == 16500)

    # 3) Fatura kes — tedarikçi olduğu için alış faturası
    bakiye_once = _cari(client, "Kaya Metal Hurda")["bakiye"]
    fk = client.post("/api/fatura-kes", json={
        "parasut_irsaliye_id": irs["parasut_irsaliye_id"], "cari_adi": "Kaya Metal Hurda",
        "agirlik_kg": 16500, "malzemeler": {}, "toplanan_fiyatlar": {"Hurda": 12.0},
    }).json()
    assert fk["basarili"], fk
    assert "AF" in fk["cevap"] and "198.000,00 TL" in fk["cevap"]
    fid = fk["fatura_no"]
    assert _cari(client, "Kaya Metal Hurda")["bakiye"] == round(bakiye_once - 198000, 2)
    assert irs["parasut_irsaliye_id"] not in [i["parasut_irsaliye_id"] for i in _bekleyenler(client)]

    # 4) Aynı irsaliyeye ikinci fatura kesilemez (çift tıklama koruması)
    tekrar = client.post("/api/fatura-kes", json={
        "parasut_irsaliye_id": irs["parasut_irsaliye_id"], "cari_adi": "Kaya Metal Hurda",
        "agirlik_kg": 16500, "malzemeler": {}, "toplanan_fiyatlar": {"Hurda": 12.0},
    }).json()
    assert tekrar["basarili"] is False

    # 5) Satış tipinde detay boş, alış tipinde dolu (frontend fallback davranışı)
    assert client.get(f"/api/fatura-detay/{fid}?tip=customer").json() == {}
    detay = client.get(f"/api/fatura-detay/{fid}?tip=supplier").json()
    assert detay["tutar"] == 198000 and detay["kalan"] == 198000

    # 6) Kısmi ödeme → kalan düşer; fazla ödeme reddedilir
    assert client.post("/api/odeme-ekle", json={"fatura_id": fid, "tutar": 98000}).json()["basarili"]
    assert client.get(f"/api/fatura-detay/{fid}?tip=supplier").json()["kalan"] == 100000
    fazla = client.post("/api/odeme-ekle", json={"fatura_id": fid, "tutar": 150000})
    assert fazla.status_code == 500 and fazla.json()["basarili"] is False

    # 7) Ödemesi olan fatura iptal edilemez; ödeme silinince edilebilir, irsaliye bekleyene döner
    assert client.post("/api/fatura-iptal", json={"fatura_id": fid, "tip": "supplier"}).json()["basarili"] is False
    odeme = client.get(f"/api/fatura-detay/{fid}?tip=supplier").json()["odemeler"][0]
    assert client.post("/api/tahsilat-sil", json={"odeme_id": odeme["id"],
                                                   "transaction_id": odeme["transaction_id"]}).json()["basarili"]
    assert client.post("/api/fatura-iptal", json={"fatura_id": fid, "tip": "supplier"}).json()["basarili"]
    assert irs["parasut_irsaliye_id"] in [i["parasut_irsaliye_id"] for i in _bekleyenler(client)]
    assert _cari(client, "Kaya Metal Hurda")["bakiye"] == bakiye_once


def test_toplu_fatura(client, demo):
    irsler = _bekleyenler(client, "Yıldız Hurdacılık")
    assert len(irsler) == 2
    toplam_kg = sum(i["toplam_kg"] for i in irsler)
    r = client.post("/api/toplu-fatura-kes", json={"cari_adi": "Yıldız Hurdacılık", "irsaliyeler": [
        {"parasut_irsaliye_id": i["parasut_irsaliye_id"], "agirlik_kg": i["toplam_kg"],
         "malzemeler": {}, "toplanan_fiyatlar": {"Hurda": 10}} for i in irsler]}).json()
    assert r["basarili"], r
    assert _bekleyenler(client, "Yıldız Hurdacılık") == []
    detay = client.get(f"/api/fatura-detay/{r['fatura_no']}?tip=supplier").json()
    assert detay["tutar"] == toplam_kg * 10
    assert len(detay["irsaliye_ids"]) == 2


def test_tahsilat_dagitim_ve_tip_kontrolu(client, demo):
    once = _cari(client, "Marmara Çelik Fabrikası A.Ş.")["bakiye"]
    r = client.post("/api/tahsilat", json={"cari_adi": "Marmara Çelik", "tutar": 200000, "onaylandi": True}).json()
    assert r["basarili"], r
    assert _cari(client, "Marmara Çelik Fabrikası A.Ş.")["bakiye"] == round(once - 200000, 2)
    # Tedarikçiye tahsilat girilmez → ödeme akışına yönlendirilir
    t = client.post("/api/tahsilat", json={"cari_adi": "Kaya Metal Hurda", "tutar": 1000}).json()
    assert t["basarili"] is False and t.get("yanlis_tip")


def test_kasa_transfer_ve_cari_odeme(client, demo):
    hesaplar = client.get("/api/hesaplar").json()["hesaplar"]
    kasa, banka = hesaplar[0], hesaplar[1]
    assert client.post("/api/para-transferi", json={"kaynak_id": banka["id"], "hedef_id": kasa["id"],
                                                     "tutar": 5000}).json()["basarili"]
    yeni = {h["id"]: h["balance"] for h in client.get("/api/hesaplar").json()["hesaplar"]}
    assert yeni[kasa["id"]] == kasa["balance"] + 5000
    assert yeni[banka["id"]] == banka["balance"] - 5000
    ege_once = _cari(client, "Ege Metal Toplama")["bakiye"]
    assert client.post("/api/cari-odeme", json={"cari_adi": "Ege Metal", "tutar": 3000}).json()["basarili"]
    assert _cari(client, "Ege Metal Toplama")["bakiye"] == round(ege_once + 3000, 2)


def test_cari_olustur_guncelle(client, demo):
    r = client.post("/api/cari-olustur", json={"firma_adi": "Test Hurda", "tip": "tedarikci"}).json()
    assert r["basarili"]
    assert client.post("/api/cari-olustur", json={"firma_adi": "test hurda"}).json()["basarili"] is False
    assert client.post("/api/cari-guncelle", json={"cari_adi": "Test Hurda", "telefon": "0555"}).json()["basarili"]
    bilgi = client.get("/api/cari-bilgi/Test Hurda").json()
    assert bilgi["telefon"] == "0555" and bilgi["tip"] == "supplier"


def test_e_fatura_yerelde_acikca_reddedilir(client, demo):
    r = client.post("/api/e-fatura-kes", json={"fatura_id": "1"})
    assert r.status_code == 500 and "yerel modda" in r.json()["hata"]


def test_excel_aktar(client, demo):
    r = client.get("/api/muhasebe/excel")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Cariler", "Faturalar", "İrsaliyeler", "Tahsilat-Ödeme", "Kasa-Banka", "Kantar Fişleri"]
    assert wb["Cariler"].max_row == 9   # başlık + 8 cari


def test_sohbet_anahtar_kelime_modu(client):
    r = client.post("/api/chat", json={"mesaj": "bekleyen irsaliyeler var mı", "session_id": "t"}).json()
    assert r["intent"] == "bekleyen_irsaliyeler"
    r = client.post("/api/chat", json={"mesaj": "tedarikçiye ödedik", "session_id": "t"}).json()
    assert r["intent"] == "odeme_ekle"


def test_tenant_izolasyonu(app_hazir, demo):
    """Başka tenant'ın cari/fatura kayıtlarına erişilemez."""
    from app.muhasebe.yerel import servis
    assert servis.cari_listesi_getir(tenant_id="baska-firma") == []
    varsayilan = servis.cari_listesi_getir()
    assert servis.cari_detay_getir(varsayilan[0]["id"], tenant_id="baska-firma") == {}
    assert servis.fatura_iptal_universal("1", "supplier", tenant_id="baska-firma")["basarili"] is False
