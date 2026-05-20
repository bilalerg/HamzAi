# app/parasut/rapor.py
# ---------------------------------------------------------------------
# RAPORLAR / OZET / LISTELER
# - hesap_ozeti_getir       (sales + purchase ozeti)
# - islem_gecmisi_getir     (cari icin fatura + payment hareketleri)
# - hesap_listesi_getir     (kasa/banka hesaplari)
# - stok_hareketleri_getir
# - gelir_gider_raporu_getir
# ---------------------------------------------------------------------

from app.core.logger import logger

from ._state import _resolve_tid
from .token import _tenant_credentials, get_tenant_config
from ._http import parasut_get
from .cari import cari_listesi_getir, cari_detay_getir
from .satis import _sales_faturalari_page, _fatura_dict_olustur
from .alis import _purchase_faturalari_page


def hesap_ozeti_getir(tenant_id=None) -> dict:
    tid = _resolve_tid(tenant_id)
    try:
        cariler         = cari_listesi_getir(tenant_id=tid)
        toplam_alacak   = sum(c["bakiye"] for c in cariler if c["bakiye"] > 0)
        toplam_odeyecek = sum(abs(c["bakiye"]) for c in cariler if c["bakiye"] < 0)
        return {
            "toplam_cari":   len(cariler),
            "toplam_alacak": toplam_alacak,
            "toplam_borclu": toplam_odeyecek,
        }
    except Exception as e:
        logger.error(f"Hesap özeti hatası: {e}")
        return {}




def islem_gecmisi_getir(cari_id: str, baslangic: str = None, bitis: str = None, tenant_id=None) -> dict:
    """
    Carinin işlem geçmişi — hem satış (sales_invoices) hem alış (purchase_bills)
    + bağlı ödemeler. Müşteri/Tedarikçi ayrımı yapmadan iki kanaldan da çekilir;
    cari hangisiyse onlar dolu döner. Hurda sektöründe çoğu cari "both" tipinde.
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        cari   = cari_detay_getir(cari_id, tenant_id=tid)
        bakiye = cari.get("bakiye", 0.0)
        islemler = []
        eklenen_tahsilat_ids = set()

        # ── 1) SATIŞ FATURALARI + TAHSİLATLAR ──────────────────────────
        page = 1
        while page <= 50:
            fatura_sonuc = parasut_get(
                f"/{cid}/sales_invoices",
                params={"filter[contact_id]": cari_id, "include": "payments",
                        "page[size]": 25, "page[number]": page, "sort": "-issue_date"},
                tenant_id=tid,
            )
            faturalar = fatura_sonuc.get("data", [])
            included  = fatura_sonuc.get("included", [])
            payment_map = {item["id"]: item for item in included if item.get("type") == "payments"}

            for f in faturalar:
                attr  = f.get("attributes", {})
                tarih = attr.get("issue_date", "-")
                if not baslangic or tarih >= baslangic:
                    if not bitis or tarih <= bitis:
                        try:
                            tutar = float(attr.get("gross_total") or 0)
                        except (ValueError, TypeError):
                            tutar = 0.0
                        islemler.append({
                            "id": f.get("id"), "tarih": tarih, "tur": "sales_invoice",
                            "aciklama": attr.get("description") or "Satış Faturası",
                            "tutar": tutar, "durum": attr.get("payment_status", "-"),
                            "kalan": float(attr.get("remaining") or 0),
                        })
                for pid in [d["id"] for d in f.get("relationships", {}).get("payments", {}).get("data", [])]:
                    if pid in eklenen_tahsilat_ids:
                        continue
                    p = payment_map.get(pid)
                    if not p:
                        continue
                    eklenen_tahsilat_ids.add(pid)
                    pattr  = p.get("attributes", {})
                    ptarih = pattr.get("date", "-")
                    if baslangic and ptarih < baslangic:
                        continue
                    if bitis and ptarih > bitis:
                        continue
                    try:
                        ptutar = float(pattr.get("amount") or 0)
                    except (ValueError, TypeError):
                        ptutar = 0.0
                    islemler.append({
                        "id": pid, "tarih": ptarih, "tur": "payment",
                        "aciklama": pattr.get("notes") or pattr.get("description") or "Tahsilat",
                        "tutar": -ptutar, "durum": "paid", "kalan": 0,
                    })
            if len(faturalar) < 25:
                break
            page += 1

        # ── 2) ALIŞ FATURALARI + ÖDEMELER (tedarikçi geçmişi) ──────────
        page = 1
        eklenen_odeme_ids = set()
        while page <= 50:
            try:
                pb_sonuc = parasut_get(
                    f"/{cid}/purchase_bills",
                    params={"filter[supplier_id]": cari_id, "include": "payments",
                            "page[size]": 25, "page[number]": page, "sort": "-issue_date"},
                    tenant_id=tid,
                )
            except Exception as e:
                # Bazı eski Paraşüt instansları filter[supplier_id] yerine
                # filter[contact_id] istiyor — fallback dene
                logger.warning(f"purchase_bills filter[supplier_id] başarısız, fallback: {e}")
                try:
                    pb_sonuc = parasut_get(
                        f"/{cid}/purchase_bills",
                        params={"filter[contact_id]": cari_id, "include": "payments",
                                "page[size]": 25, "page[number]": page, "sort": "-issue_date"},
                        tenant_id=tid,
                    )
                except Exception:
                    pb_sonuc = {"data": [], "included": []}
            alis_faturalar = pb_sonuc.get("data", [])
            pb_included    = pb_sonuc.get("included", [])
            pb_payment_map = {it["id"]: it for it in pb_included if it.get("type") == "payments"}

            for f in alis_faturalar:
                attr  = f.get("attributes", {})
                tarih = attr.get("issue_date", "-")
                if baslangic and tarih < baslangic:
                    continue
                if bitis and tarih > bitis:
                    continue
                try:
                    tutar = float(attr.get("gross_total") or 0)
                except (ValueError, TypeError):
                    tutar = 0.0
                islemler.append({
                    "id": f.get("id"), "tarih": tarih, "tur": "purchase_bill",
                    "aciklama": attr.get("description") or "Alış Faturası",
                    # Alış faturası bizim için borç (-)
                    "tutar": -tutar, "durum": attr.get("payment_status", "-"),
                    "kalan": float(attr.get("remaining") or 0),
                })
                for pid in [d["id"] for d in f.get("relationships", {}).get("payments", {}).get("data", [])]:
                    if pid in eklenen_odeme_ids:
                        continue
                    p = pb_payment_map.get(pid)
                    if not p:
                        continue
                    eklenen_odeme_ids.add(pid)
                    pattr  = p.get("attributes", {})
                    ptarih = pattr.get("date", "-")
                    if baslangic and ptarih < baslangic:
                        continue
                    if bitis and ptarih > bitis:
                        continue
                    try:
                        ptutar = float(pattr.get("amount") or 0)
                    except (ValueError, TypeError):
                        ptutar = 0.0
                    islemler.append({
                        "id": pid, "tarih": ptarih, "tur": "supplier_payment",
                        "aciklama": pattr.get("notes") or pattr.get("description") or "Tedarikçi ödemesi",
                        # Tedarikçiye ödeme bizim için para çıkışı (+)
                        "tutar": ptutar, "durum": "paid", "kalan": 0,
                    })
            if len(alis_faturalar) < 25:
                break
            page += 1

        islemler.sort(key=lambda x: x.get("tarih", ""), reverse=True)
        logger.info(f"İşlem geçmişi: cari_id={cari_id} toplam={len(islemler)} işlem (tenant={tid})")
        return {"cari_id": cari_id, "cari_adi": cari.get("ad", "-"), "bakiye": bakiye, "islemler": islemler}
    except Exception as e:
        logger.error(f"İşlem geçmişi hatası: {e}")
        return {"bakiye": 0, "islemler": []}



def hesap_listesi_getir(tenant_id=None) -> list:
    """Tüm kasa ve banka hesaplarını döner."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc = parasut_get(f"/{cid}/accounts", tenant_id=tid)
        hesaplar = []
        for h in sonuc.get("data", []):
            attr = h.get("attributes", {})
            hesaplar.append({
                "id":           h["id"],
                "name":         attr.get("name", "-"),
                "account_type": attr.get("account_type", "-"),
                "currency":     attr.get("currency", "TRY"),
                "balance":      float(attr.get("balance", 0) or 0),
            })
        return hesaplar
    except Exception as e:
        logger.error(f"Hesap listesi hatası: {e}")
        return []




def stok_hareketleri_getir(urun_id: str = None, limit: int = 50, tenant_id=None) -> list:
    """
    Stok giriş/çıkış hareketlerini döner. urun_id ile filtrelenebilir.
    include=product,contact — ürün adı + cari adı doğrudan Paraşüt'ten gelir;
    böylece main.py'de DB'ye join atmaya gerek kalmaz (firma değeri 'firma'
    alanına yazılır, fallback olarak shipment_document üzerinden DB'den de
    çekilebilir).
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        params = {"page[size]": min(limit, 25), "sort": "-date", "include": "product,contact"}
        if urun_id:
            params["filter[product_id]"] = urun_id
        try:
            sonuc = parasut_get(f"/{cid}/stock_movements", params=params, tenant_id=tid)
        except Exception as e:
            # include=contact desteklenmezse fallback (eski davranış)
            logger.warning(f"stock_movements include=contact başarısız, product'a düşülüyor: {e}")
            params["include"] = "product"
            sonuc = parasut_get(f"/{cid}/stock_movements", params=params, tenant_id=tid)
        included = sonuc.get("included", [])
        # Ürün adlarını included'dan al
        urun_map = {
            inc["id"]: inc.get("attributes", {}).get("name", "-")
            for inc in included
            if inc.get("type") == "products"
        }
        # Cari adlarını included'dan al
        contact_map = {
            inc["id"]: inc.get("attributes", {}).get("name", "-")
            for inc in included
            if inc.get("type") == "contacts"
        }
        hareketler = []
        for h in sonuc.get("data", []):
            attr = h.get("attributes", {})
            rels = h.get("relationships", {})
            # Ürün adını ilişkiden bul
            urun_rel  = rels.get("product", {}).get("data") or {}
            urun_id_r = urun_rel.get("id")
            urun_adi  = urun_map.get(urun_id_r) if urun_id_r else None
            # Cari adını ilişkiden bul (varsa)
            contact_rel = rels.get("contact", {}).get("data") or {}
            contact_id  = contact_rel.get("id") if isinstance(contact_rel, dict) else None
            firma_adi   = contact_map.get(contact_id) if contact_id else ""
            aciklama  = attr.get("description") or "-"   # "{plaka} — {malzeme} — {kg} kg"
            # Shipment document ID → fallback olarak DB'den firma çekmek için
            sd_rel    = rels.get("shipment_document", {}).get("data") or {}
            sd_id     = sd_rel.get("id")
            hareketler.append({
                "id":          h["id"],
                "tarih":       (attr.get("date") or attr.get("created_at") or "-")[:10],
                "miktar":      float(attr.get("quantity", 0) or 0),
                "firma":       firma_adi or "",
                "urun":        urun_adi or "-",
                "aciklama":    aciklama,
                "giris_mi":    attr.get("inflow", False),
                "irsaliye_id": sd_id,
            })
        logger.info(f"Stok hareketleri: {len(hareketler)} kayıt (tenant={tid})")
        return hareketler
    except Exception as e:
        logger.error(f"Stok hareketleri hatası: {e}")
        return []




def gelir_gider_raporu_getir(baslangic: str, bitis: str, tenant_id=None) -> dict:
    """
    Belirtilen tarih aralığı için gelir/gider raporu üretir.
    Paraşüt'te özel rapor endpoint'i olmadığından Python'da hesaplanır.
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    def _tum_sayfalari_cek(endpoint, extra_params, tid):
        """Paraşüt max page[size]=25 olduğundan tüm sayfaları döngüyle çeker."""
        tum_data = []
        page = 1
        while True:
            params = {"page[size]": 25, "page[number]": page, **extra_params}
            sonuc = parasut_get(endpoint, params=params, tenant_id=tid)
            data = sonuc.get("data", [])
            tum_data.extend(data)
            meta = sonuc.get("meta", {})
            total_pages = meta.get("total_pages", 1)
            if page >= total_pages or not data:
                break
            page += 1
        return tum_data

    try:
        # Satış faturaları (gelir)
        satis_data = _tum_sayfalari_cek(
            f"/{cid}/sales_invoices",
            {"sort": "-issue_date"},
            tid,
        )
        toplam_gelir  = 0.0
        toplam_gelir_kdv = 0.0
        satis_sayisi  = 0
        for f in satis_data:
            attr  = f.get("attributes", {})
            tarih = attr.get("issue_date", "")
            if baslangic and tarih < baslangic:
                continue
            if bitis and tarih > bitis:
                continue
            toplam_gelir     += float(attr.get("gross_total", 0) or 0)
            toplam_gelir_kdv += float(attr.get("total_vat", 0) or 0)
            satis_sayisi     += 1

        # Alış faturaları (gider)
        alis_data = _tum_sayfalari_cek(
            f"/{cid}/purchase_bills",
            {"sort": "-issue_date"},
            tid,
        )
        toplam_gider     = 0.0
        toplam_gider_kdv = 0.0
        alis_sayisi      = 0
        for f in alis_data:
            attr  = f.get("attributes", {})
            tarih = attr.get("issue_date", "")
            if baslangic and tarih < baslangic:
                continue
            if bitis and tarih > bitis:
                continue
            toplam_gider     += float(attr.get("gross_total", 0) or 0)
            toplam_gider_kdv += float(attr.get("total_vat", 0) or 0)
            alis_sayisi      += 1

        net_kar = toplam_gelir - toplam_gider
        logger.info(f"Gelir/gider raporu: gelir={toplam_gelir:,.0f} gider={toplam_gider:,.0f} (tenant={tid})")
        return {
            "baslangic":       baslangic,
            "bitis":           bitis,
            "toplam_gelir":    toplam_gelir,
            "gelir_kdv":       toplam_gelir_kdv,
            "satis_sayisi":    satis_sayisi,
            "toplam_gider":    toplam_gider,
            "gider_kdv":       toplam_gider_kdv,
            "alis_sayisi":     alis_sayisi,
            "net_kar":         net_kar,
            "kdv_beyan":       toplam_gelir_kdv - toplam_gider_kdv,
        }
    except Exception as e:
        logger.error(f"Gelir/gider raporu hatası: {e}")
        return {}


