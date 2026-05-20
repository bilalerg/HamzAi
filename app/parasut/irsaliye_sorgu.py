# app/parasut/irsaliye_sorgu.py
# ---------------------------------------------------------------------
# IRSALIYE SORGU (mevcut app/parasut/irsaliye.py "olustur" islemleri icindir;
# bu modul okuma/listeleme/detay icin)
# - tum_bekleyen_irsaliyeleri_getir
# - cari_bekleyen_irsaliyeleri_getir
# - irsaliye_detay_getir
# ---------------------------------------------------------------------

from app.core.logger import logger

from ._state import _resolve_tid, _cari_caches
from .token import _tenant_credentials, get_tenant_config
from ._http import parasut_get
from .cari import cari_listesi_getir, cari_id_bul


def tum_bekleyen_irsaliyeleri_getir(
        baslangic: str = None,
        bitis: str = None,
        sayfa: int = 1,
        sayfa_basi: int = 25,
        tenant_id=None,
) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        tum_irsaliyeler = []
        page = 1
        while page <= 50:
            params = {
                "page[size]":    25,
                "page[number]":  page,
                "sort":          "-issue_date",
                "include": "stock_movements,contact",
            }
            sonuc    = parasut_get(f"/{cid}/shipment_documents", params=params, tenant_id=tid)
            data     = sonuc.get("data", [])
            if not data:
                break
            included = sonuc.get("included", [])
            sm_map   = {inc["id"]: inc.get("attributes", {}) for inc in included if inc.get("type") == "stock_movements"}
            contact_map = {inc["id"]: inc.get("attributes", {}).get("name", "-") for inc in included if inc.get("type") == "contacts"}

            for i in data:
                attr         = i.get("attributes", {})
                rels         = i.get("relationships", {})
                # Faturaya bağlı irsaliyeleri atla — invoices ilişkisi doluysa faturalanmış
                # Faturaya bağlı irsaliyeleri atla (Satış veya Alış faturası kesilmiş mi?)
                if rels.get("sales_invoice", {}).get("data") or rels.get("purchase_bill", {}).get("data"):
                    continue
                contact_data = rels.get("contact", {}).get("data", {})
                contact_id   = contact_data.get("id") if contact_data else None
                if not contact_id:
                    continue
                tarih = attr.get("issue_date", "")
                if baslangic and tarih < baslangic:
                    continue
                if bitis and tarih > bitis:
                    continue

                sm_rels  = rels.get("stock_movements", {}).get("data", [])
                kalemler = []
                for sm_ref in sm_rels:
                    sm_attr = sm_map.get(sm_ref["id"], {})
                    miktar  = float(sm_attr.get("quantity", 0))
                    if miktar > 0:
                        desc      = sm_attr.get("description") or ""
                        detail_no = sm_attr.get("detail_no", len(kalemler) + 1)
                        malzeme_adi = f"Malzeme {detail_no}"
                        if desc:
                            try:
                                norm    = desc.replace(' - ', '—').replace('–', '—')
                                parcalar = [p.strip() for p in norm.split("—")]
                                if len(parcalar) >= 3 and "kg" in parcalar[-1].lower():
                                    malzeme_adi = parcalar[1].strip()
                                elif len(parcalar) == 2 and "kg" not in parcalar[1].lower():
                                    malzeme_adi = parcalar[1].strip()
                                elif len(parcalar) == 1 and parcalar[0]:
                                    malzeme_adi = parcalar[0].strip()
                            except Exception:
                                pass
                        kalemler.append({"aciklama": malzeme_adi, "miktar_kg": miktar})

                tum_irsaliyeler.append({
                    "id":         i["id"],
                    "contact_id": contact_id,
                    "cari_adi":   contact_map.get(contact_id, "-"),
                    "tarih":      tarih,
                    "aciklama":   attr.get("description", "-"),
                    "kalemler":   kalemler,
                    "toplam_kg":  sum(k["miktar_kg"] for k in kalemler),
                })

            if baslangic and data:
                en_eski_tarih = data[-1].get("attributes", {}).get("issue_date", "")
                if en_eski_tarih and en_eski_tarih < baslangic:
                    break
            if len(data) < 25:
                break
            page += 1

        toplam        = len(tum_irsaliyeler)
        baslangic_idx = (sayfa - 1) * sayfa_basi
        bitis_idx     = baslangic_idx + sayfa_basi
        sayfa_irsaliyeler = tum_irsaliyeler[baslangic_idx:bitis_idx]
        sonraki_sayfa_var = toplam > bitis_idx

        gruplu = {}
        for irs in sayfa_irsaliyeler:
            cid_k = irs["contact_id"]
            if cid_k not in gruplu:
                gruplu[cid_k] = []
            gruplu[cid_k].append(irs)

        logger.info(f"Bekleyen irsaliyeler: {toplam} toplam, sayfa={sayfa}, bu sayfada={len(sayfa_irsaliyeler)} (tenant={tid})")
        return {
            "irsaliyeler":       gruplu,
            "sonraki_sayfa_var": sonraki_sayfa_var,
            "sayfa":             sayfa,
            "toplam":            toplam,
        }
    except Exception as e:
        logger.error(f"Tüm bekleyen irsaliye hatası: {e}")
        return {"irsaliyeler": {}, "sonraki_sayfa_var": False, "sayfa": sayfa, "toplam": 0}




def cari_bekleyen_irsaliyeleri_getir(cari_adi: str, tenant_id=None) -> list:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        c_id = cari_id_bul(cari_adi, tenant_id=tid)
        if not c_id:
            return []
        sonuc    = parasut_get(
            f"/{cid}/shipment_documents",
            params={"filter[contact_id]": c_id, "page[size]": 25, "sort": "-issue_date",
                    "include": "stock_movements"},
            tenant_id=tid,
        )
        included = sonuc.get("included", [])
        sm_map   = {inc["id"]: inc.get("attributes", {}) for inc in included if inc.get("type") == "stock_movements"}
        irsaliyeler = []
        for i in sonuc.get("data", []):
            attr    = i.get("attributes", {})
            rels    = i.get("relationships", {})
            sm_rels = rels.get("stock_movements", {}).get("data", [])
            kalemler = []
            for sm_ref in sm_rels:
                sm_attr = sm_map.get(sm_ref["id"], {})
                miktar  = float(sm_attr.get("quantity", 0))
                if miktar > 0:
                    desc      = sm_attr.get("description") or ""
                    detail_no = sm_attr.get("detail_no", len(kalemler) + 1)
                    malzeme_adi = f"Malzeme {detail_no}"
                    if desc:
                        try:
                            norm    = desc.replace(' - ', '—').replace('–', '—')
                            parcalar = [p.strip() for p in norm.split("—")]
                            if len(parcalar) >= 3 and "kg" in parcalar[-1].lower():
                                malzeme_adi = parcalar[1].strip()
                            elif len(parcalar) == 2 and "kg" not in parcalar[1].lower():
                                malzeme_adi = parcalar[1].strip()
                            elif len(parcalar) == 1 and parcalar[0]:
                                malzeme_adi = parcalar[0].strip()
                        except Exception:
                            pass
                    kalemler.append({"aciklama": malzeme_adi, "miktar_kg": miktar})
            irsaliyeler.append({
                "id":       i["id"],
                "tarih":    attr.get("issue_date", "-"),
                "aciklama": attr.get("description", "-"),
                "kalemler": kalemler,
                "toplam_kg": sum(k["miktar_kg"] for k in kalemler),
            })
        logger.info(f"{cari_adi} için {len(irsaliyeler)} irsaliye bulundu (tenant={tid})")
        return irsaliyeler
    except Exception as e:
        logger.error(f"Bekleyen irsaliye hatası: {e}")
        return []




def irsaliye_detay_getir(irsaliye_id: str, tenant_id=None) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc    = parasut_get(
            f"/{cid}/shipment_documents/{irsaliye_id}",
            params={"include": "stock_movements,stock_movements.product"},
            tenant_id=tid,
        )
        data     = sonuc.get("data", {})
        attr     = data.get("attributes", {})
        included = sonuc.get("included", [])
        sm_rels  = data.get("relationships", {}).get("stock_movements", {}).get("data", [])
        sm_ids   = {sm["id"] for sm in sm_rels}
        kalemler = []
        for inc in included:
            if inc.get("type") == "stock_movements" and inc["id"] in sm_ids:
                sm_attr = inc.get("attributes", {})
                miktar  = float(sm_attr.get("quantity", 0))
                if miktar > 0:
                    kalemler.append({"aciklama": sm_attr.get("description") or "Hurda", "miktar_kg": miktar})
        return {
            "id":        irsaliye_id,
            "tarih":     attr.get("issue_date", "-"),
            "aciklama":  attr.get("description", "-"),
            "kalemler":  kalemler,
            "toplam_kg": sum(k["miktar_kg"] for k in kalemler),
        }
    except Exception as e:
        logger.error(f"İrsaliye detay hatası: {e}")
        return {}


