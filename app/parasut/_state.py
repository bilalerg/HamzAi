# app/parasut/_state.py
# ---------------------------------------------------------------------
# PARAŞÜT MODÜLLERİ İÇİN PAYLAŞILAN STATE (TEK KAYNAK)
#
# ÇOK ÖNEMLİ — MULTI-TENANT İZOLASYONU:
# Tüm dict'ler {tenant_id: ...} key'iyle PER-TENANT izole tutulur.
# Diğer modüller bu dict'leri MUTASYON edebilir (örn. _tokens[tid] = ...)
# ama ASLA yeniden bind ETMEMELİDİR (`_tokens = {}` yazılmamalı; aksi halde
# ayrı bir dict yaratılır ve modüller arası tenant izolasyonu çöker).
#
# Import şekli (zorunlu):
#     from app.parasut._state import _tokens, _cari_caches, ...
# ---------------------------------------------------------------------

import threading
from app.core.config import PARASUT_USERNAME

# ── PER-TENANT STATE ─────────────────────────────────────────────────
# {tid: {"access": str, "refresh": str, "expires_at": float}}
_tokens:           dict = {}
_token_locks:      dict = {}   # {tid: threading.Lock}

# Cari cache: liste + son yenileme zamanı + lock
_cari_caches:      dict = {}   # {tid: list[dict]}
_cari_cache_times: dict = {}   # {tid: float}
_cache_locks:      dict = {}   # {tid: threading.Lock}

# Kasa hesabı id cache (her tenant'ın kasa hesabı sabit kalır)
_kasa_hesabi_ids:  dict = {}   # {tid: str | None}

# Rate limit — son istek zamanı
_rate_limit_times: dict = {}   # {tid: float}

# Lock dict'lerine eşzamanlı erişimde tek bir kapı muhafızı
_meta_lock = threading.Lock()

# ── SABİTLER ─────────────────────────────────────────────────────────
CACHE_SURE      = 900    # 15 dakika — cari cache TTL
MIN_ISTEK_ARASI = 0.35   # saniye — Paraşüt rate limit korumalı bekleme


# ── ORTAK YARDIMCILAR ───────────────────────────────────────────────

def _resolve_tid(tenant_id) -> str:
    """tenant_id=None → DEFAULT_TENANT_ID (config'deki PARASUT_USERNAME)."""
    if tenant_id:
        return str(tenant_id)
    return PARASUT_USERNAME or "__default__"


def _get_lock(locks_dict: dict, tid: str) -> threading.Lock:
    """Tenant başına lock üretir/getirir (double-checked locking)."""
    if tid not in locks_dict:
        with _meta_lock:
            if tid not in locks_dict:
                locks_dict[tid] = threading.Lock()
    return locks_dict[tid]
