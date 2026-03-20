# koordinatlar.py — Zirve Ekran Koordinat Haritası
#
# NEDEN BU DOSYA VAR?
# PyAutoGUI koordinat bazlı çalışır — "şu x,y noktasına tıkla" der.
# Tüm koordinatları tek bir yerde tutuyoruz.
# Ekran çözünürlüğü değişirse veya Zirve güncellerse
# sadece bu dosyayı güncelliyoruz — başka hiçbir yere dokunmuyoruz.
#
# ÖNEMLİ: Bu koordinatlar 1920x1080 çözünürlük için alındı.
# Farklı çözünürlükte çalışırsa koordinatları güncelle.

# ── LOGIN EKRANI ─────────────────────────────────────────────────────────
LOGIN = {
    "sifre_kutusu": (1298, 159),   # Kullanıcı şifresi input kutusu
    "firma_listesi": (977, 272),    # TEST firmasının listede olduğu yer
    "giris_butonu": (1390, 979),    # GİRİŞ (F2) butonu
}

# ── ANA MENÜ ─────────────────────────────────────────────────────────────
ANA_MENU = {
    "irsaliye_butonu": (472, 442),  # İrsaliye / Stok Fişleri
    "cari_butonu": (462, 536),      # Cari modülü
}

# ── İRSALİYE FORMU ───────────────────────────────────────────────────────
IRSALIYE = {
    "cari_unvani": (68, 508),       # Cari Unvanı input kutusu
    "stok_kodu": (88, 569),         # Stok Kodu kolonu (alt tablo)
    "miktar": (798, 570),           # Miktar kolonu
    "kaydet": (125, 89),            # Kaydet (F2) butonu
}

# ── POPUP KOORDİNATLARI ──────────────────────────────────────────────────
# Zirve'de çıkan popup'ların Tamam butonu koordinatı
# Her popup farklı yerde çıkabilir — Computer Use bunları yakalar
POPUP = {
    "tamam": (836, 248),            # "Cari Seçimi Yapılmalıdır" popup Tamam
}

# ── REFERANS GÖRÜNTÜLER ──────────────────────────────────────────────────
# sanity_check.py bu dosyaları kullanır
REFERANS = {
    "irsaliye_ekran": "app/zirve/zirve_ekran.png",  # İrsaliye formu açıkken alınan görüntü
}