# app/muhasebe/yerel/servis.py — Yerel muhasebe sağlayıcısı (Paraşüt'süz)
#
# app/parasut/client.py ile AYNI fonksiyon adlarını, AYNI parametre sırasını ve
# AYNI dönüş formatını sağlar. Böylece router'lar sağlayıcıdan habersiz çalışır.
# Veriler Profaix'in kendi veritabanında (yerel_* tabloları) tutulur.
#
# Tüm fonksiyonlar senkron; async_* sürümleri app/muhasebe/yerel/__init__.py'de
# asyncio.to_thread ile üretilir.
#
# Tenant izolasyonu: her sorgu tenant_id ile filtrelenir. Başka tenant'ın
# kaydına id ile erişilmeye çalışılırsa "bulunamadı" döner.

import difflib
import json
import re
from datetime import date, datetime

from app.core.database import SessionLocal
from app.core.logger import logger
from app.muhasebe.yerel.modeller import (
    YerelCari, YerelHesap, YerelIrsaliye, YerelFatura, YerelHareket,
)

SAGLAYICI_ADI = "Profaix Yerel Kayıt"

_GECERLI_TIPLER = ("customer", "supplier", "customer_supplier")
_TAHSILAT_TURLERI = ("fatura_tahsilat", "cari_tahsilat")   # cari bakiyesi −, kasa +
_ODEME_TURLERI    = ("fatura_odeme", "cari_odeme")         # cari bakiyesi +, kasa −


# ────────────────────────────────────────────────────────────────────
#  YARDIMCILAR
# ────────────────────────────────────────────────────────────────────

def _tid(tenant_id) -> str:
    if tenant_id:
        return str(tenant_id)
    from app.core.tenant import get_default_tenant_id
    return get_default_tenant_id()


def _int_id(deger):
    try:
        return int(str(deger).strip())
    except (TypeError, ValueError):
        return None


def _bugun() -> str:
    return date.today().strftime("%Y-%m-%d")


def _tarih_normalize(deger) -> str:
    """YYYY-MM-DD / DD.MM.YYYY / datetime → YYYY-MM-DD. Boşsa bugün."""
    if not deger:
        return _bugun()
    if isinstance(deger, datetime):
        return deger.strftime("%Y-%m-%d")
    if isinstance(deger, date):
        return deger.strftime("%Y-%m-%d")
    s = str(deger).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return _bugun()


def _r(x) -> float:
    return round(float(x or 0), 2)


def _json_oku(metin, varsayilan):
    if not metin:
        return varsayilan
    try:
        return json.loads(metin)
    except Exception:
        return varsayilan


def _bos_tire(x):
    return x if x not in (None, "") else "-"


# ── Cari eşleştirme (Paraşüt modülündeki mantığın aynısı) ────────────
_GEREKSIZ_KELIMELER = {"A.S", "A.Ş", "LTD", "ŞTİ", "STI", "SAN", "TIC", "TİC", "VE", "GERI", "DONUSUM", "AS"}
_FUZZY_ESIK = 0.72


def _normalize_cari_adi(ad: str) -> str:
    ad = (ad or "").upper().strip()
    ad = re.sub(r'[().،,]', ' ', ad)
    return " ".join(k for k in ad.split() if k not in _GEREKSIZ_KELIMELER)


def _fuzzy_skor(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


# ── Bakiye / fatura hesapları ─────────────────────────────────────────

def _fatura_odenen(db, fatura_id: int) -> float:
    toplam = 0.0
    for h in db.query(YerelHareket).filter(
        YerelHareket.fatura_id == fatura_id, YerelHareket.silindi == 0
    ).all():
        toplam += h.tutar
    return _r(toplam)


def _fatura_durum(f: YerelFatura, kalan: float) -> str:
    if kalan <= 0.005:
        return "paid"
    if f.vade and f.vade < _bugun():
        return "overdue"
    return "not_due"


def _cari_bakiyeleri(db, tid: str) -> dict:
    """cari_id → bakiye (Paraşüt işaret kuralı: + cari bize borçlu)."""
    bakiye: dict = {}
    for f in db.query(YerelFatura).filter(
        YerelFatura.tenant_id == tid, YerelFatura.durum != "iptal"
    ).all():
        isaret = 1 if f.tip == "customer" else -1
        bakiye[f.cari_id] = bakiye.get(f.cari_id, 0.0) + isaret * f.brut_toplam
    for h in db.query(YerelHareket).filter(
        YerelHareket.tenant_id == tid, YerelHareket.silindi == 0,
        YerelHareket.cari_id.isnot(None),
    ).all():
        if h.tur in _TAHSILAT_TURLERI:
            bakiye[h.cari_id] = bakiye.get(h.cari_id, 0.0) - h.tutar
        elif h.tur in _ODEME_TURLERI:
            bakiye[h.cari_id] = bakiye.get(h.cari_id, 0.0) + h.tutar
    return {k: _r(v) for k, v in bakiye.items()}


def _cari_dict(c: YerelCari, bakiye: float) -> dict:
    return {
        "id":            str(c.id),
        "ad":            c.ad,
        "email":         _bos_tire(c.email),
        "telefon":       _bos_tire(c.telefon),
        "bakiye":        _r(bakiye),
        "vergi_no":      _bos_tire(c.vergi_no),
        "adres":         _bos_tire(c.adres),
        "vergi_dairesi": _bos_tire(c.vergi_dairesi),
        "account_type":  c.tip or "customer",
    }


def _cari_getir(db, tid: str, cari_id) -> YerelCari | None:
    cid = _int_id(cari_id)
    if cid is None:
        return None
    return db.query(YerelCari).filter(YerelCari.id == cid, YerelCari.tenant_id == tid).first()


def _varsayilan_hesap_id(db, tid: str) -> int:
    """İlk kasa/banka hesabı; hiç yoksa 'Kasa' hesabı açar."""
    h = db.query(YerelHesap).filter(YerelHesap.tenant_id == tid).order_by(YerelHesap.id).first()
    if h:
        return h.id
    h = YerelHesap(tenant_id=tid, ad="Kasa", tip="cash", para_birimi="TRY", acilis_bakiyesi=0.0)
    db.add(h)
    db.commit()
    db.refresh(h)
    return h.id


def _hesap_dogrula(db, tid: str, kasa_id) -> int | None:
    if not kasa_id:
        return _varsayilan_hesap_id(db, tid)
    hid = _int_id(kasa_id)
    if hid is None:
        return None
    h = db.query(YerelHesap).filter(YerelHesap.id == hid, YerelHesap.tenant_id == tid).first()
    return h.id if h else None


# ────────────────────────────────────────────────────────────────────
#  AYAR / HAM İSTEK UYUMLULUĞU
# ────────────────────────────────────────────────────────────────────

def get_tenant_config(tenant_id=None) -> dict:
    """Paraşüt'teki company_id / hurda_product_id yerine sabit yerel değerler."""
    return {"company_id": "yerel", "hurda_product_id": "hurda", "tenant_id": _tid(tenant_id)}


def _malzeme_adi_coz(aciklama: str, sira: int) -> str:
    """'34ABC12 — Demir — 1.200 kg' → 'Demir' (Paraşüt sorgu modülündeki kural)."""
    malzeme_adi = f"Malzeme {sira}"
    if not aciklama:
        return malzeme_adi
    try:
        norm = aciklama.replace(' - ', '—').replace('–', '—')
        parcalar = [p.strip() for p in norm.split("—")]
        if len(parcalar) >= 3 and "kg" in parcalar[-1].lower():
            return parcalar[1] or malzeme_adi
        if len(parcalar) == 2 and "kg" not in parcalar[1].lower():
            if parcalar[1].lower() == "hurda malzeme":   # irsaliye_akisi tek kalem açıklaması
                return "Hurda"
            return parcalar[1] or malzeme_adi
        if len(parcalar) == 2:
            return "Hurda"
        if len(parcalar) == 1 and parcalar[0]:
            return parcalar[0]
    except Exception:
        pass
    return malzeme_adi


def _rel_id(rels: dict, *anahtarlar):
    for k in anahtarlar:
        d = (rels.get(k) or {}).get("data")
        if isinstance(d, dict) and d.get("id"):
            return d["id"]
    return None


def _irsaliye_yaz(db, tid: str, veri: dict) -> YerelIrsaliye:
    data  = veri.get("data", {})
    attrs = data.get("attributes", {})
    rels  = data.get("relationships", {})
    cari  = _cari_getir(db, tid, _rel_id(rels, "contact"))
    if not cari:
        raise ValueError("İrsaliye için cari bulunamadı")
    kalemler = []
    for i, sm in enumerate((rels.get("stock_movements") or {}).get("data", []) or [], start=1):
        a = sm.get("attributes", {})
        miktar = float(a.get("quantity") or 0)
        if miktar <= 0:
            continue
        kalemler.append({"aciklama": _malzeme_adi_coz(a.get("description") or "", i),
                         "miktar_kg": miktar})
    irs = YerelIrsaliye(
        tenant_id=tid, cari_id=cari.id,
        tarih=_tarih_normalize(attrs.get("issue_date")),
        aciklama=attrs.get("description") or "İrsaliye",
        inflow=1 if attrs.get("inflow") else 0,
        plaka=attrs.get("carrier_license_plate"),
        kalemler_json=json.dumps(kalemler, ensure_ascii=False),
    )
    db.add(irs)
    db.commit()
    db.refresh(irs)
    logger.info(f"📦 Yerel irsaliye oluşturuldu: id={irs.id} cari={cari.ad} tenant={tid}")
    return irs


def _fatura_kaydet(db, tid: str, tip: str, cari_id: int, kalemler: list, aciklama: str,
                   tarih: str, vade: str, para_birimi: str, irsaliye_ids=None) -> YerelFatura:
    """Kalemlerden toplamları hesaplayıp faturayı yazar; irsaliyeleri faturaya bağlar."""
    irsaliyeler = []
    for iid in (irsaliye_ids or []):
        irs = db.query(YerelIrsaliye).filter(
            YerelIrsaliye.id == _int_id(iid), YerelIrsaliye.tenant_id == tid,
            YerelIrsaliye.silindi == 0,
        ).first()
        if not irs:
            raise ValueError(f"İrsaliye bulunamadı: {iid}")
        if irs.fatura_id:
            raise ValueError(f"İrsaliye {iid} zaten faturalanmış (fatura {irs.fatura_id})")
        irsaliyeler.append(irs)

    ara = kdv = 0.0
    temiz = []
    for k in kalemler:
        miktar = float(k.get("miktar") or 0)
        bf     = float(k.get("birim_fiyat") or 0)
        oran   = float(k.get("kdv_orani") or 0)
        tutar  = miktar * bf
        ara += tutar
        kdv += tutar * oran / 100.0
        temiz.append({"aciklama": k.get("aciklama") or "Hurda", "miktar": miktar,
                      "birim_fiyat": bf, "kdv_orani": oran})
    if not temiz:
        raise ValueError("Fatura kalemi yok")

    f = YerelFatura(
        tenant_id=tid, tip=tip, cari_id=cari_id,
        tarih=_tarih_normalize(tarih), vade=_tarih_normalize(vade or tarih),
        aciklama=aciklama or ("Satış faturası" if tip == "customer" else "Alış faturası"),
        para_birimi="TRY" if (para_birimi or "TRY").upper() in ("TRL", "TRY") else para_birimi.upper(),
        kalemler_json=json.dumps(temiz, ensure_ascii=False),
        ara_toplam=_r(ara), kdv_toplam=_r(kdv), brut_toplam=_r(ara + kdv),
        durum="aktif",
    )
    db.add(f)
    db.flush()
    f.fatura_no = f"{'SF' if tip == 'customer' else 'AF'}{f.tarih[:4]}-{f.id:05d}"
    for irs in irsaliyeler:
        irs.fatura_id = f.id
    db.commit()
    db.refresh(f)
    logger.info(f"🧾 Yerel fatura kesildi: {f.fatura_no} tip={tip} tutar={f.brut_toplam} tenant={tid}")
    return f


def _fatura_payload_yaz(db, tid: str, veri: dict, tip: str) -> YerelFatura:
    data  = veri.get("data", {})
    attrs = data.get("attributes", {})
    rels  = data.get("relationships", {})
    cari  = _cari_getir(db, tid, _rel_id(rels, "contact", "supplier"))
    if not cari:
        raise ValueError("Fatura için cari bulunamadı")
    kalemler = []
    for d in (rels.get("details") or {}).get("data", []) or []:
        a = d.get("attributes", {})
        kalemler.append({"aciklama": a.get("description"), "miktar": a.get("quantity"),
                         "birim_fiyat": a.get("unit_price"), "kdv_orani": a.get("vat_rate")})
    irs_ids = [s.get("id") for s in (rels.get("shipment_documents") or {}).get("data", []) or []]
    return _fatura_kaydet(db, tid, tip, cari.id, kalemler, attrs.get("description"),
                          attrs.get("issue_date"), attrs.get("due_date"),
                          attrs.get("currency"), irs_ids)


def parasut_post(endpoint: str, veri: dict, tenant_id=None) -> dict:
    """
    Router'larda Paraşüt formatında kurulan 3 yazma isteğini yerelde karşılar:
      /{cid}/shipment_documents  → irsaliye
      /{cid}/sales_invoices      → satış faturası
      /{cid}/purchase_bills      → alış faturası
    Dönüş Paraşüt'le aynı: {"data": {"id": "..."}}
    """
    tid = _tid(tenant_id)
    son = endpoint.rstrip("/").split("/")[-1]
    db = SessionLocal()
    try:
        if son == "shipment_documents":
            kayit = _irsaliye_yaz(db, tid, veri)
            return {"data": {"id": str(kayit.id), "type": "shipment_documents"}}
        if son == "sales_invoices":
            kayit = _fatura_payload_yaz(db, tid, veri, "customer")
            return {"data": {"id": str(kayit.id), "type": "sales_invoices",
                             "attributes": {"invoice_no": kayit.fatura_no}}}
        if son == "purchase_bills":
            kayit = _fatura_payload_yaz(db, tid, veri, "supplier")
            return {"data": {"id": str(kayit.id), "type": "purchase_bills",
                             "attributes": {"invoice_no": kayit.fatura_no}}}
        raise NotImplementedError(f"Yerel modda desteklenmeyen istek: POST {endpoint}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def parasut_delete(endpoint: str, tenant_id=None) -> bool:
    """Sadece irsaliye silme (eski Osman akışı) yerelde desteklenir."""
    tid = _tid(tenant_id)
    parca = endpoint.rstrip("/").split("/")
    if len(parca) >= 2 and parca[-2] == "shipment_documents":
        db = SessionLocal()
        try:
            irs = db.query(YerelIrsaliye).filter(
                YerelIrsaliye.id == _int_id(parca[-1]), YerelIrsaliye.tenant_id == tid
            ).first()
            if irs:
                irs.silindi = 1
                db.commit()
            return True
        finally:
            db.close()
    raise NotImplementedError(f"Yerel modda desteklenmeyen istek: DELETE {endpoint}")


def parasut_get(endpoint: str, params: dict = None, tenant_id=None) -> dict:
    raise NotImplementedError("Paraşüt bağlı değil (MUHASEBE_SAGLAYICI=yerel) — ham Paraşüt sorgusu yapılamaz.")


def parasut_put(endpoint: str, veri: dict, tenant_id=None) -> dict:
    raise NotImplementedError("Paraşüt bağlı değil (MUHASEBE_SAGLAYICI=yerel).")


def parasut_patch(endpoint: str, veri: dict = None, tenant_id=None) -> dict:
    raise NotImplementedError("Paraşüt bağlı değil (MUHASEBE_SAGLAYICI=yerel).")


# ────────────────────────────────────────────────────────────────────
#  CARİ
# ────────────────────────────────────────────────────────────────────

def cari_listesi_getir(force_refresh: bool = False, tenant_id=None) -> list:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        bakiyeler = _cari_bakiyeleri(db, tid)
        cariler = db.query(YerelCari).filter(YerelCari.tenant_id == tid).order_by(YerelCari.ad).all()
        return [_cari_dict(c, bakiyeler.get(c.id, 0.0)) for c in cariler]
    finally:
        db.close()


def cari_cache_yenile(tenant_id=None) -> list:
    # Yerelde önbellek yok — her okuma güncel.
    return cari_listesi_getir(tenant_id=tenant_id)


def cari_id_bul(firma_adi: str, tenant_id=None) -> str | None:
    if not firma_adi:
        return None
    cariler = cari_listesi_getir(tenant_id=tenant_id)
    firma_upper = firma_adi.upper().strip()
    firma_norm  = _normalize_cari_adi(firma_adi)

    for c in cariler:                                   # 1) tam eşleşme
        if c["ad"].upper() == firma_upper:
            return c["id"]
    for c in cariler:                                   # 2) içerme
        cu = c["ad"].upper()
        if firma_upper in cu or cu in firma_upper:
            return c["id"]
    firma_kelimeler = set(firma_norm.split())           # 3) ≥2 anlamlı ortak kelime
    for c in cariler:
        ortak = firma_kelimeler & set(_normalize_cari_adi(c["ad"]).split())
        if len(ortak) >= 2:
            return c["id"]
    en_iyi, skor = None, 0.0                            # 4) fuzzy
    for c in cariler:
        s = _fuzzy_skor(firma_norm, _normalize_cari_adi(c["ad"]))
        if s > skor:
            en_iyi, skor = c, s
    if en_iyi and skor >= _FUZZY_ESIK:
        logger.info(f"Cari fuzzy eşleşme (skor={skor:.2f}): {firma_adi} → {en_iyi['ad']}")
        return en_iyi["id"]
    logger.warning(f"Cari bulunamadı (yerel): {firma_adi} (en iyi skor {skor:.2f})")
    return None


def cari_detay_getir(cari_id: str, tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        c = _cari_getir(db, tid, cari_id)
        if not c:
            return {}
        d = _cari_dict(c, _cari_bakiyeleri(db, tid).get(c.id, 0.0))
        d.pop("account_type", None)
        return d
    finally:
        db.close()


def cari_olustur(firma_adi: str, email: str = None, telefon: str = None,
                 vergi_no: str = None, vergi_dairesi: str = None, adres: str = None,
                 tenant_id=None, tip: str = "customer") -> dict:
    tid = _tid(tenant_id)
    firma_adi = (firma_adi or "").strip()
    if not firma_adi:
        return {"basarili": False, "hata": "Firma adı gerekli"}
    db = SessionLocal()
    try:
        mevcut = db.query(YerelCari).filter(YerelCari.tenant_id == tid).all()
        if any(c.ad.upper() == firma_adi.upper() for c in mevcut):
            return {"basarili": False, "hata": f"'{firma_adi}' adlı cari zaten kayıtlı"}
        c = YerelCari(tenant_id=tid, ad=firma_adi,
                      tip=tip if tip in _GECERLI_TIPLER else "customer",
                      email=email, telefon=telefon, vergi_no=vergi_no,
                      vergi_dairesi=vergi_dairesi, adres=adres)
        db.add(c)
        db.commit()
        db.refresh(c)
        logger.info(f"Yerel cari oluşturuldu: {firma_adi} (ID {c.id}, tenant={tid})")
        return {"basarili": True, "id": str(c.id), "ad": firma_adi}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def cari_guncelle(cari_id: str, firma_adi: str = None, email: str = None, telefon: str = None,
                  vergi_no: str = None, vergi_dairesi: str = None, adres: str = None,
                  tip: str = None, tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        c = _cari_getir(db, tid, cari_id)
        if not c:
            return {"basarili": False, "hata": "Cari bulunamadı"}
        degisen = {}
        for alan, deger in (("ad", firma_adi), ("email", email), ("telefon", telefon),
                            ("vergi_no", vergi_no), ("vergi_dairesi", vergi_dairesi),
                            ("adres", adres)):
            if deger:
                setattr(c, alan, deger)
                degisen[alan] = deger
        if tip and tip in _GECERLI_TIPLER:
            c.tip = tip
            degisen["account_type"] = tip
        if not degisen:
            return {"basarili": False, "hata": "Güncellenecek alan yok"}
        db.commit()
        return {"basarili": True, "id": str(c.id), "veri": degisen}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def cari_tipi_bul(cari_adi: str, tenant_id=None) -> str:
    """Paraşüt sürümüyle aynı kural: supplier / customer_supplier → 'supplier'."""
    cari_id = cari_id_bul(cari_adi, tenant_id=tenant_id)
    if not cari_id:
        return "customer"
    for c in cari_listesi_getir(tenant_id=tenant_id):
        if c["id"] == str(cari_id):
            return "supplier" if c.get("account_type") in ("supplier", "both", "customer_supplier") else "customer"
    return "customer"


# ────────────────────────────────────────────────────────────────────
#  İRSALİYE
# ────────────────────────────────────────────────────────────────────

def _irsaliye_dict(irs: YerelIrsaliye, cari_adi: str = "-") -> dict:
    kalemler = _json_oku(irs.kalemler_json, [])
    return {
        "id":         str(irs.id),
        "contact_id": str(irs.cari_id),
        "cari_adi":   cari_adi,
        "tarih":      irs.tarih,
        "aciklama":   irs.aciklama or "-",
        "kalemler":   kalemler,
        "toplam_kg":  sum(float(k.get("miktar_kg") or 0) for k in kalemler),
    }


def tum_bekleyen_irsaliyeleri_getir(baslangic: str = None, bitis: str = None,
                                     sayfa: int = 1, sayfa_basi: int = 25, tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        adlar = {c.id: c.ad for c in db.query(YerelCari).filter(YerelCari.tenant_id == tid).all()}
        q = db.query(YerelIrsaliye).filter(
            YerelIrsaliye.tenant_id == tid, YerelIrsaliye.silindi == 0,
            YerelIrsaliye.fatura_id.is_(None),
        )
        if baslangic:
            q = q.filter(YerelIrsaliye.tarih >= baslangic)
        if bitis:
            q = q.filter(YerelIrsaliye.tarih <= bitis)
        tumu = [_irsaliye_dict(i, adlar.get(i.cari_id, "-"))
                for i in q.order_by(YerelIrsaliye.tarih.desc(), YerelIrsaliye.id.desc()).all()]
        bas = (sayfa - 1) * sayfa_basi
        dilim = tumu[bas:bas + sayfa_basi]
        gruplu: dict = {}
        for irs in dilim:
            gruplu.setdefault(irs["contact_id"], []).append(irs)
        return {"irsaliyeler": gruplu, "sonraki_sayfa_var": len(tumu) > bas + sayfa_basi,
                "sayfa": sayfa, "toplam": len(tumu)}
    finally:
        db.close()


def cari_bekleyen_irsaliyeleri_getir(cari_adi: str, tenant_id=None) -> list:
    tid = _tid(tenant_id)
    cid = _int_id(cari_id_bul(cari_adi, tenant_id=tid))
    if cid is None:
        return []
    db = SessionLocal()
    try:
        q = db.query(YerelIrsaliye).filter(
            YerelIrsaliye.tenant_id == tid, YerelIrsaliye.cari_id == cid,
            YerelIrsaliye.silindi == 0, YerelIrsaliye.fatura_id.is_(None),
        ).order_by(YerelIrsaliye.tarih.desc())
        sonuc = []
        for i in q.all():
            d = _irsaliye_dict(i)
            sonuc.append({k: d[k] for k in ("id", "tarih", "aciklama", "kalemler", "toplam_kg")})
        return sonuc
    finally:
        db.close()


def irsaliye_detay_getir(irsaliye_id: str, tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        i = db.query(YerelIrsaliye).filter(
            YerelIrsaliye.id == _int_id(irsaliye_id), YerelIrsaliye.tenant_id == tid
        ).first()
        if not i:
            return {}
        d = _irsaliye_dict(i)
        return {k: d[k] for k in ("id", "tarih", "aciklama", "kalemler", "toplam_kg")}
    finally:
        db.close()


# ────────────────────────────────────────────────────────────────────
#  FATURA
# ────────────────────────────────────────────────────────────────────

def _fatura_getir(db, tid: str, fatura_id, tip: str = None) -> YerelFatura | None:
    q = db.query(YerelFatura).filter(YerelFatura.id == _int_id(fatura_id), YerelFatura.tenant_id == tid)
    if tip:
        q = q.filter(YerelFatura.tip == tip)
    return q.first()


def _fatura_liste_dict(db, f: YerelFatura) -> dict:
    kalan = _r(f.brut_toplam - _fatura_odenen(db, f.id))
    irs_ids = [str(i.id) for i in db.query(YerelIrsaliye).filter(YerelIrsaliye.fatura_id == f.id).all()]
    plaka = None
    m = re.search(r'\b(\d{2}\s*[A-ZÇŞĞÜÖİ]{1,3}\s*\d{2,4})\b', f.aciklama or "")
    if m:
        plaka = m.group(1).replace(" ", "")
    return {
        "id":           str(f.id),
        "tip":          f.tip,
        "fatura_no":    f.fatura_no,
        "tarih":        f.tarih,
        "vade":         f.vade or "-",
        "tutar":        _r(f.brut_toplam),
        "kalan":        max(kalan, 0.0),
        "durum":        _fatura_durum(f, kalan),
        "aciklama":     f.aciklama or "-",
        "plaka":        plaka,
        "irsaliye_id":  irs_ids[0] if irs_ids else None,
        "irsaliye_ids": irs_ids,
    }


def cari_faturalari_getir(cari_id: str, limit: int = 25, sayfa: int = 1, baslangic: str = None,
                          bitis: str = None, durum: str = None, tip: str = "customer",
                          tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    tip_temiz = tip if tip in ("customer", "supplier", "both") else "customer"
    db = SessionLocal()
    try:
        q = db.query(YerelFatura).filter(
            YerelFatura.tenant_id == tid, YerelFatura.cari_id == _int_id(cari_id),
            YerelFatura.durum != "iptal",
        )
        if tip_temiz != "both":
            q = q.filter(YerelFatura.tip == tip_temiz)
        if baslangic:
            q = q.filter(YerelFatura.tarih >= baslangic)
        if bitis:
            q = q.filter(YerelFatura.tarih <= bitis)
        liste = [_fatura_liste_dict(db, f) for f in q.order_by(YerelFatura.tarih.desc(), YerelFatura.id.desc()).all()]
        if durum == "not_paid":
            liste = [f for f in liste if f["durum"] != "paid"]
        elif durum:
            liste = [f for f in liste if f["durum"] == durum]
        adim = min(limit, 25)
        bas = (sayfa - 1) * adim
        dilim = liste[bas:bas + adim]
        return {"faturalar": dilim, "toplam": len(dilim), "toplam_kayit": len(liste),
                "sayfa": sayfa, "tip": tip_temiz, "sonraki_sayfa_var": len(liste) > bas + adim}
    finally:
        db.close()


def _fatura_detay(fatura_id, tip: str, tenant_id) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        f = _fatura_getir(db, tid, fatura_id, tip)
        if not f:
            return {}
        d = _fatura_liste_dict(db, f)
        kalemler = [{"aciklama": k.get("aciklama") or "Hurda", "miktar": k.get("miktar", 0),
                     "birim_fiyat": k.get("birim_fiyat", 0),
                     "tutar": _r(float(k.get("miktar") or 0) * float(k.get("birim_fiyat") or 0))}
                    for k in _json_oku(f.kalemler_json, [])]
        odemeler = [{"id": str(h.id), "transaction_id": str(h.id), "tarih": h.tarih,
                     "tutar": _r(h.tutar), "aciklama": h.aciklama or "-"}
                    for h in db.query(YerelHareket).filter(
                        YerelHareket.fatura_id == f.id, YerelHareket.silindi == 0
                    ).order_by(YerelHareket.tarih).all()]
        cari = db.query(YerelCari).filter(YerelCari.id == f.cari_id).first()
        sonuc = {
            "id": str(f.id), "fatura_no": f.fatura_no, "tarih": f.tarih, "vade": f.vade or "-",
            "tutar": d["tutar"], "kalan": d["kalan"], "durum": d["durum"],
            "aciklama": f.aciklama or "-", "kalemler": kalemler,
            "irsaliye_ids": d["irsaliye_ids"], "cari_adi": cari.ad if cari else "-",
            "iptal": f.durum == "iptal",
        }
        if tip == "supplier":
            sonuc["odemeler"] = odemeler
        else:
            sonuc["tahsilatlar"] = odemeler
        return sonuc
    finally:
        db.close()


def fatura_detay_getir(fatura_id: str, tenant_id=None) -> dict:
    return _fatura_detay(fatura_id, "customer", tenant_id)


def alis_faturasi_detay_getir(fatura_id: str, tenant_id=None) -> dict:
    return _fatura_detay(fatura_id, "supplier", tenant_id)


def fatura_detay_universal(fatura_id: str, tip: str = "customer", tenant_id=None) -> dict:
    return _fatura_detay(fatura_id, "supplier" if tip == "supplier" else "customer", tenant_id)


def _fatura_iptal(fatura_id, tip: str, tenant_id) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        f = _fatura_getir(db, tid, fatura_id, tip)
        if not f:
            return {"basarili": False, "hata": "Fatura bulunamadı"}
        if f.durum == "iptal":
            return {"basarili": False, "hata": "Fatura zaten iptal edilmiş"}
        if _fatura_odenen(db, f.id) > 0:
            return {"basarili": False,
                    "hata": "Bu faturaya tahsilat/ödeme yapılmış. Önce ödemeleri silin, sonra iptal edin."}
        f.durum = "iptal"
        # Bağlı irsaliyeler yeniden "bekleyen" olur; fiş/waybill durumu da
        # TAMAMLANDI'dan geri alınır (yoksa bekleyen listesinde görünmez).
        from app.core.database import Waybill, WeighTicket, TicketStatus
        for irs in db.query(YerelIrsaliye).filter(YerelIrsaliye.fatura_id == f.id).all():
            irs.fatura_id = None
            for wb in db.query(Waybill).filter(Waybill.tenant_id == tid,
                                               Waybill.parasut_irsaliye_id == str(irs.id)).all():
                if wb.status == TicketStatus.TAMAMLANDI:
                    wb.status = TicketStatus.IRSALIYE_TAMAMLANDI
                if wb.ticket_id:
                    t = db.query(WeighTicket).filter(WeighTicket.id == wb.ticket_id).first()
                    if t and t.status == TicketStatus.TAMAMLANDI:
                        t.status = TicketStatus.IRSALIYE_TAMAMLANDI
        db.commit()
        logger.info(f"Yerel fatura iptal: {f.fatura_no} tenant={tid}")
        return {"basarili": True, "fatura_id": str(f.id), "durum": "iptal"}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def satis_faturasi_iptal(fatura_id: str, tenant_id=None) -> dict:
    return _fatura_iptal(fatura_id, "customer", tenant_id)


def alis_faturasi_iptal(fatura_id: str, tenant_id=None) -> dict:
    return _fatura_iptal(fatura_id, "supplier", tenant_id)


def fatura_iptal_universal(fatura_id: str, tip: str = "customer", tenant_id=None) -> dict:
    return _fatura_iptal(fatura_id, "supplier" if tip == "supplier" else "customer", tenant_id)


def fatura_guncelle_universal(fatura_id: str, tip: str = "customer", aciklama: str = None,
                              issue_date: str = None, due_date: str = None, currency: str = None,
                              tenant_id=None, **diger_alanlar) -> dict:
    tid = _tid(tenant_id)
    tip = "supplier" if tip == "supplier" else "customer"
    db = SessionLocal()
    try:
        f = _fatura_getir(db, tid, fatura_id, tip)
        if not f:
            return {"basarili": False, "hata": "Fatura bulunamadı"}
        degisen = {}
        if aciklama:
            f.aciklama = aciklama; degisen["description"] = aciklama
        if issue_date:
            f.tarih = _tarih_normalize(issue_date); degisen["issue_date"] = f.tarih
        if due_date:
            f.vade = _tarih_normalize(due_date); degisen["due_date"] = f.vade
        if currency:
            f.para_birimi = "TRY" if currency.upper() in ("TRL", "TRY") else currency.upper()
            degisen["currency"] = f.para_birimi
        if diger_alanlar.get("invoice_no"):
            f.fatura_no = str(diger_alanlar.pop("invoice_no")); degisen["invoice_no"] = f.fatura_no
        yeni_cari = diger_alanlar.pop("contact_id", None) or diger_alanlar.pop("supplier_id", None)
        if yeni_cari:
            c = _cari_getir(db, tid, yeni_cari)
            if not c:
                return {"basarili": False, "hata": "Yeni cari bulunamadı"}
            f.cari_id = c.id; degisen["cari_id"] = str(c.id)
        if diger_alanlar:
            ek = _json_oku(f.ek_alanlar_json, {})
            ek.update(diger_alanlar)
            f.ek_alanlar_json = json.dumps(ek, ensure_ascii=False)
            degisen.update(diger_alanlar)
        if not degisen:
            return {"basarili": False, "hata": "Güncellenecek alan belirtilmedi"}
        db.commit()
        return {"basarili": True, "fatura_id": str(f.id), "veri": degisen}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def alis_faturasi_olustur(cari_adi: str, kalemler: list, aciklama: str = None,
                          issue_date: str = None, due_date: str = None, currency: str = "TRL",
                          item_type: str = "purchase_bill", tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    cari_id = _int_id(cari_id_bul(cari_adi, tenant_id=tid))
    if cari_id is None:
        return {"basarili": False, "hata": f"'{cari_adi}' adlı cari bulunamadı."}
    gecerli = [{"aciklama": k.get("aciklama") or "-", "miktar": k.get("miktar"),
                "birim_fiyat": k.get("birim_fiyat"), "kdv_orani": k.get("vat_rate", 0)}
               for k in (kalemler or [])
               if float(k.get("miktar") or 0) > 0 and float(k.get("birim_fiyat") or 0) > 0]
    if not gecerli:
        return {"basarili": False, "hata": "Geçerli kalem yok"}
    db = SessionLocal()
    try:
        f = _fatura_kaydet(db, tid, "supplier", cari_id, gecerli,
                           aciklama or f"{cari_adi} alış", issue_date, due_date, currency)
        return {"basarili": True, "fatura_id": str(f.id), "tutar": f.brut_toplam,
                "cari": cari_adi, "cari_id": str(cari_id)}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def alis_faturasi_basit_olustur(cari_adi: str, tutar: float, aciklama: str = None,
                                issue_date: str = None, due_date: str = None,
                                currency: str = "TRL", vat_rate: float = 0, tenant_id=None) -> dict:
    return alis_faturasi_olustur(
        cari_adi=cari_adi,
        kalemler=[{"aciklama": aciklama or "Hurda alım", "miktar": 1,
                   "birim_fiyat": float(tutar), "vat_rate": vat_rate}],
        aciklama=aciklama, issue_date=issue_date, due_date=due_date,
        currency=currency, tenant_id=tenant_id,
    )


def son_alis_faturalari_getir(limit: int = 30, tenant_id=None) -> list:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        adlar = {c.id: c.ad for c in db.query(YerelCari).filter(YerelCari.tenant_id == tid).all()}
        sonuc = []
        for f in db.query(YerelFatura).filter(
            YerelFatura.tenant_id == tid, YerelFatura.tip == "supplier", YerelFatura.durum != "iptal"
        ).order_by(YerelFatura.tarih.desc(), YerelFatura.id.desc()).all():
            kalan = _r(f.brut_toplam - _fatura_odenen(db, f.id))
            if kalan <= 0:
                continue
            sonuc.append({"id": str(f.id), "cari": adlar.get(f.cari_id, "-"), "tarih": f.tarih,
                          "tutar": _r(f.brut_toplam), "kalan": kalan, "aciklama": f.aciklama or ""})
            if len(sonuc) >= limit:
                break
        return sonuc
    finally:
        db.close()


# ────────────────────────────────────────────────────────────────────
#  TAHSİLAT / ÖDEME / KASA
# ────────────────────────────────────────────────────────────────────

def _hareket_ekle(db, tid, tur, tutar, tarih, aciklama, cari_id=None, fatura_id=None, hesap_id=None):
    h = YerelHareket(tenant_id=tid, tur=tur, tutar=_r(tutar), tarih=_tarih_normalize(tarih),
                     aciklama=aciklama, cari_id=cari_id, fatura_id=fatura_id, hesap_id=hesap_id)
    db.add(h)
    db.flush()
    return h


def fatura_tahsilat_ekle(fatura_id: str, tutar: float, aciklama: str = None,
                         tarih: str = None, tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        f = _fatura_getir(db, tid, fatura_id, "customer")
        if not f or f.durum == "iptal":
            return {"basarili": False, "hata": "Satış faturası bulunamadı"}
        h = _hareket_ekle(db, tid, "fatura_tahsilat", tutar, tarih, aciklama or "Tahsilat",
                          cari_id=f.cari_id, fatura_id=f.id, hesap_id=_varsayilan_hesap_id(db, tid))
        db.commit()
        return {"basarili": True, "id": str(h.id), "fatura_id": str(f.id), "tutar": h.tutar, "tarih": h.tarih}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def tahsilat_ekle(cari_adi: str, tutar: float, aciklama: str = None, tarih: str = None,
                  tenant_id=None) -> dict:
    """Açık satış faturalarına eskiden yeniye dağıtır; açık fatura yoksa cari hesaba yazar."""
    tid = _tid(tenant_id)
    cari_id = _int_id(cari_id_bul(cari_adi, tenant_id=tid))
    if cari_id is None:
        return {"basarili": False, "hata": f"'{cari_adi}' adlı cari bulunamadı."}
    db = SessionLocal()
    try:
        hesap_id = _varsayilan_hesap_id(db, tid)
        acik = []
        for f in db.query(YerelFatura).filter(
            YerelFatura.tenant_id == tid, YerelFatura.cari_id == cari_id,
            YerelFatura.tip == "customer", YerelFatura.durum != "iptal",
        ).order_by(YerelFatura.tarih, YerelFatura.id).all():
            kalan = _r(f.brut_toplam - _fatura_odenen(db, f.id))
            if kalan > 0:
                acik.append((f, kalan))

        tarih_str = _tarih_normalize(tarih)
        if not acik:
            h = _hareket_ekle(db, tid, "cari_tahsilat", tutar, tarih_str,
                              aciklama or f"{cari_adi} cari tahsilatı", cari_id=cari_id, hesap_id=hesap_id)
            db.commit()
            return {"basarili": True, "cari": cari_adi, "tutar": _r(tutar), "tarih": tarih_str,
                    "odenen_faturalar": [], "cari_hesap_id": str(h.id),
                    "kalan_odenmedi": 0, "basarisiz_fatura": 0, "yontem": "cari_hesap"}

        kalan_tutar = float(tutar)
        odenen = []
        for f, kalan in acik:
            if kalan_tutar <= 0.005:
                break
            miktar = min(kalan_tutar, kalan)
            _hareket_ekle(db, tid, "fatura_tahsilat", miktar, tarih_str, aciklama or f"{cari_adi} tahsilat",
                          cari_id=cari_id, fatura_id=f.id, hesap_id=hesap_id)
            odenen.append({"fatura_id": str(f.id), "odenen": _r(miktar)})
            kalan_tutar -= miktar
        # Faturaları aşan kısım avans olarak cari hesaba yazılır (para kaybolmaz)
        if kalan_tutar > 0.005:
            _hareket_ekle(db, tid, "cari_tahsilat", kalan_tutar, tarih_str,
                          (aciklama or f"{cari_adi} tahsilat") + " (avans)", cari_id=cari_id, hesap_id=hesap_id)
        db.commit()
        return {"basarili": True, "cari": cari_adi, "tutar": _r(tutar), "tarih": tarih_str,
                "odenen_faturalar": odenen, "kalan_odenmedi": 0, "basarisiz_fatura": 0}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def tahsilat_sil(odeme_id: str, tenant_id=None, transaction_id: str = None) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        h = db.query(YerelHareket).filter(
            YerelHareket.id == _int_id(transaction_id or odeme_id), YerelHareket.tenant_id == tid,
            YerelHareket.silindi == 0,
        ).first()
        if not h or h.tur.startswith("transfer"):
            return {"basarili": False, "hata": "Tahsilat/ödeme kaydı bulunamadı"}
        h.silindi = 1
        db.commit()
        return {"basarili": True, "id": str(h.id), "transaction_id": str(h.id)}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def alıs_faturasi_odeme_ekle(fatura_id: str, tutar: float, kasa_id: str = None, tarih: str = None,
                             notlar: str = None, tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        f = _fatura_getir(db, tid, fatura_id, "supplier")
        if not f or f.durum == "iptal":
            return {"basarili": False, "hata": "Alış faturası bulunamadı"}
        kalan = _r(f.brut_toplam - _fatura_odenen(db, f.id))
        if float(tutar) > kalan + 0.01:
            return {"basarili": False, "hata": f"Ödeme tutarı faturanın kalan tutarından ({kalan} TL) fazla"}
        hesap_id = _hesap_dogrula(db, tid, kasa_id)
        if not hesap_id:
            return {"basarili": False, "hata": "Kasa/banka hesabı bulunamadı."}
        h = _hareket_ekle(db, tid, "fatura_odeme", tutar, tarih, notlar or "Hurda alım ödemesi",
                          cari_id=f.cari_id, fatura_id=f.id, hesap_id=hesap_id)
        db.commit()
        return {"basarili": True, "id": str(h.id), "fatura_id": str(f.id), "tutar": h.tutar, "tarih": h.tarih}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


alis_faturasi_odeme_ekle = alıs_faturasi_odeme_ekle   # ASCII alias (Paraşüt modülüyle aynı)


def _cari_hareketi(tur: str, cari_id, tutar, aciklama, tarih, kasa_id, tenant_id) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        c = _cari_getir(db, tid, cari_id)
        if not c:
            return {"basarili": False, "hata": "Cari bulunamadı"}
        hesap_id = _hesap_dogrula(db, tid, kasa_id)
        if not hesap_id:
            return {"basarili": False, "hata": "Kasa/banka hesabı bulunamadı."}
        varsayilan = "Cari hesap tahsilatı" if tur == "cari_tahsilat" else "Cari hesap ödemesi"
        h = _hareket_ekle(db, tid, tur, tutar, tarih, aciklama or varsayilan, cari_id=c.id, hesap_id=hesap_id)
        db.commit()
        return {"basarili": True, "id": str(h.id), "cari_id": str(c.id), "tutar": h.tutar, "tarih": h.tarih}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


def cari_bazli_tahsilat(cari_id: str, tutar: float, aciklama: str = None, tarih: str = None,
                        kasa_id: str = None, tenant_id=None) -> dict:
    return _cari_hareketi("cari_tahsilat", cari_id, tutar, aciklama, tarih, kasa_id, tenant_id)


def cari_bazli_odeme(cari_id: str, tutar: float, aciklama: str = None, tarih: str = None,
                     kasa_id: str = None, tenant_id=None) -> dict:
    return _cari_hareketi("cari_odeme", cari_id, tutar, aciklama, tarih, kasa_id, tenant_id)


def acik_faturalar_getir(tenant_id=None) -> list:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        sonuc = []
        for f in db.query(YerelFatura).filter(
            YerelFatura.tenant_id == tid, YerelFatura.tip == "customer", YerelFatura.durum != "iptal"
        ).order_by(YerelFatura.tarih.desc()).all():
            d = _fatura_liste_dict(db, f)
            if d["kalan"] > 0:
                sonuc.append({k: d[k] for k in ("id", "tarih", "vade", "tutar", "kalan", "aciklama", "irsaliye_id")})
        return sonuc
    finally:
        db.close()


def hesap_listesi_getir(tenant_id=None) -> list:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        _varsayilan_hesap_id(db, tid)   # hiç hesap yoksa Kasa açılır
        bakiye: dict = {}
        for h in db.query(YerelHareket).filter(
            YerelHareket.tenant_id == tid, YerelHareket.silindi == 0, YerelHareket.hesap_id.isnot(None)
        ).all():
            isaret = 1 if (h.tur in _TAHSILAT_TURLERI or h.tur == "transfer_giris") else -1
            bakiye[h.hesap_id] = bakiye.get(h.hesap_id, 0.0) + isaret * h.tutar
        return [{"id": str(h.id), "name": h.ad, "account_type": h.tip, "currency": h.para_birimi,
                 "balance": _r(h.acilis_bakiyesi + bakiye.get(h.id, 0.0))}
                for h in db.query(YerelHesap).filter(YerelHesap.tenant_id == tid).order_by(YerelHesap.id).all()]
    finally:
        db.close()


def para_transferi(kaynak_id: str, hedef_id: str, tutar: float, tarih: str = None,
                   aciklama: str = None, tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        k = _hesap_dogrula(db, tid, kaynak_id) if kaynak_id else None
        h = _hesap_dogrula(db, tid, hedef_id) if hedef_id else None
        if not k or not h:
            return {"basarili": False, "hata": "Kaynak veya hedef hesap bulunamadı"}
        if k == h:
            return {"basarili": False, "hata": "Kaynak ve hedef hesap aynı olamaz"}
        not_ = aciklama or "Hesaplar arası transfer"
        _hareket_ekle(db, tid, "transfer_cikis", tutar, tarih, not_, hesap_id=k)
        _hareket_ekle(db, tid, "transfer_giris", tutar, tarih, not_, hesap_id=h)
        db.commit()
        return {"basarili": True, "tutar": _r(tutar), "tarih": _tarih_normalize(tarih),
                "kaynak_id": str(k), "hedef_id": str(h)}
    except Exception as e:
        db.rollback()
        return {"basarili": False, "hata": str(e)}
    finally:
        db.close()


# ────────────────────────────────────────────────────────────────────
#  RAPORLAR
# ────────────────────────────────────────────────────────────────────

def hesap_ozeti_getir(tenant_id=None) -> dict:
    cariler = cari_listesi_getir(tenant_id=tenant_id)
    return {
        "toplam_cari":   len(cariler),
        "toplam_alacak": _r(sum(c["bakiye"] for c in cariler if c["bakiye"] > 0)),
        "toplam_borclu": _r(sum(abs(c["bakiye"]) for c in cariler if c["bakiye"] < 0)),
    }


def islem_gecmisi_getir(cari_id: str, baslangic: str = None, bitis: str = None, tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        c = _cari_getir(db, tid, cari_id)
        if not c:
            return {"bakiye": 0, "islemler": []}
        bakiye = _cari_bakiyeleri(db, tid).get(c.id, 0.0)

        def _aralikta(t):
            return (not baslangic or t >= baslangic) and (not bitis or t <= bitis)

        islemler = []
        for f in db.query(YerelFatura).filter(
            YerelFatura.tenant_id == tid, YerelFatura.cari_id == c.id, YerelFatura.durum != "iptal"
        ).all():
            if not _aralikta(f.tarih):
                continue
            d = _fatura_liste_dict(db, f)
            satis = f.tip == "customer"
            islemler.append({
                "id": str(f.id), "tarih": f.tarih, "tur": "sales_invoice" if satis else "purchase_bill",
                "aciklama": f.aciklama or ("Satış Faturası" if satis else "Alış Faturası"),
                "tutar": d["tutar"] if satis else -d["tutar"], "durum": d["durum"], "kalan": d["kalan"],
            })
        for h in db.query(YerelHareket).filter(
            YerelHareket.tenant_id == tid, YerelHareket.cari_id == c.id, YerelHareket.silindi == 0
        ).all():
            if not _aralikta(h.tarih):
                continue
            tahsilat = h.tur in _TAHSILAT_TURLERI
            islemler.append({
                "id": str(h.id), "tarih": h.tarih, "tur": "payment" if tahsilat else "supplier_payment",
                "aciklama": h.aciklama or ("Tahsilat" if tahsilat else "Tedarikçi ödemesi"),
                "tutar": -_r(h.tutar) if tahsilat else _r(h.tutar), "durum": "paid", "kalan": 0,
            })
        islemler.sort(key=lambda x: (x.get("tarih", ""), x.get("id", "")), reverse=True)
        return {"cari_id": str(c.id), "cari_adi": c.ad, "bakiye": bakiye, "islemler": islemler}
    finally:
        db.close()


def stok_hareketleri_getir(urun_id: str = None, limit: int = 50, tenant_id=None) -> list:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        adlar = {c.id: c.ad for c in db.query(YerelCari).filter(YerelCari.tenant_id == tid).all()}
        hareketler = []
        for irs in db.query(YerelIrsaliye).filter(
            YerelIrsaliye.tenant_id == tid, YerelIrsaliye.silindi == 0
        ).order_by(YerelIrsaliye.tarih.desc(), YerelIrsaliye.id.desc()).all():
            for i, k in enumerate(_json_oku(irs.kalemler_json, []), start=1):
                hareketler.append({
                    "id": f"{irs.id}-{i}", "tarih": irs.tarih,
                    "miktar": float(k.get("miktar_kg") or 0),
                    "firma": adlar.get(irs.cari_id, ""), "urun": k.get("aciklama") or "Hurda",
                    "aciklama": irs.aciklama or "-", "giris_mi": bool(irs.inflow),
                    "irsaliye_id": str(irs.id),
                })
                if len(hareketler) >= limit:
                    return hareketler
        return hareketler
    finally:
        db.close()


def gelir_gider_raporu_getir(baslangic: str, bitis: str, tenant_id=None) -> dict:
    tid = _tid(tenant_id)
    db = SessionLocal()
    try:
        q = db.query(YerelFatura).filter(YerelFatura.tenant_id == tid, YerelFatura.durum != "iptal")
        if baslangic:
            q = q.filter(YerelFatura.tarih >= baslangic)
        if bitis:
            q = q.filter(YerelFatura.tarih <= bitis)
        gelir = gelir_kdv = gider = gider_kdv = 0.0
        satis = alis = 0
        for f in q.all():
            if f.tip == "customer":
                gelir += f.brut_toplam; gelir_kdv += f.kdv_toplam; satis += 1
            else:
                gider += f.brut_toplam; gider_kdv += f.kdv_toplam; alis += 1
        return {
            "baslangic": baslangic, "bitis": bitis,
            "toplam_gelir": _r(gelir), "gelir_kdv": _r(gelir_kdv), "satis_sayisi": satis,
            "toplam_gider": _r(gider), "gider_kdv": _r(gider_kdv), "alis_sayisi": alis,
            "net_kar": _r(gelir - gider), "kdv_beyan": _r(gelir_kdv - gider_kdv),
        }
    finally:
        db.close()


# ────────────────────────────────────────────────────────────────────
#  E-FATURA / E-ARŞİV — resmi entegrasyon gerektirir
# ────────────────────────────────────────────────────────────────────

_E_FATURA_NOTU = ("E-fatura / e-arşiv resmi GİB entegrasyonu gerektirir; yerel modda kesilemez. "
                  "Paraşüt bağlandığında (MUHASEBE_SAGLAYICI=parasut) bu özellik çalışır.")


def e_fatura_mukellef_kontrol(vkn: str, tenant_id=None) -> dict:
    return {"mukellef": False, "vkn": vkn, "hata": _E_FATURA_NOTU}


def fatura_resmilestir_otomatik(fatura_id: str, vkn: str = None, tckn: str = None,
                                bekle: bool = True, tenant_id=None) -> dict:
    return {"basarili": False, "hata": _E_FATURA_NOTU}
