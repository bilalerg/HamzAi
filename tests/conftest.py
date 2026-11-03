# tests/conftest.py — Testler geçici bir SQLite veritabanında, yerel muhasebe
# sağlayıcısıyla ve Claude anahtarı OLMADAN çalışır (ağ/ücretli API çağrısı yok).

import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="profaix_test_")
os.environ.update({
    "DATABASE_URL":       f"sqlite:///{_TMP}/test.db",
    "MUHASEBE_SAGLAYICI": "yerel",
    "DEMO_VERI":          "0",
    "ENV":                "development",
    "JWT_SECRET_KEY":     "test-jwt-secret",
    "ADMIN_TOKEN":        "test-admin-token",
    "ADMIN_USERNAME":     "admin",
    "ADMIN_PASSWORD":     "test123",
    "ANTHROPIC_API_KEY":  "",
    "PARASUT_USERNAME":   "",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def app_hazir():
    from app.core.database import init_db
    from app.auth import ilk_admin_olustur
    init_db()
    ilk_admin_olustur()
    from main import app
    return app


@pytest.fixture()
def client(app_hazir):
    from fastapi.testclient import TestClient
    c = TestClient(app_hazir)
    r = c.post("/api/login", json={"username": "admin", "password": "test123"})
    assert r.json().get("basarili"), r.text
    c.headers["Authorization"] = "Bearer " + r.json()["auth_token"]
    return c


@pytest.fixture()
def demo(app_hazir):
    """Her test temiz demo verisiyle başlar."""
    from app.muhasebe.yerel.demo_veri import demo_veri_yukle
    demo_veri_yukle(sifirla=True)
