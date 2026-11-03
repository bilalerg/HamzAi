# yerel_baslat.py — Profaix'i lokalde tek komutla başlatır (Windows / macOS / Linux)
#
#   python yerel_baslat.py              → .env yoksa oluşturur, sunucuyu açar, tarayıcıyı açar
#   python yerel_baslat.py --sifirla    → demo veriyi sıfırlayıp yeniden yükler (video öncesi)
#   python yerel_baslat.py --port 8080  → farklı port
#
# Bağımlılıkların kurulu olduğu varsayılır (baslat.bat / baslat.sh bunu yapar).

import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

KOK = Path(__file__).resolve().parent
try:  # Windows konsolunda Türkçe karakter / emoji yazdırırken çökmesin
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
os.chdir(KOK)
sys.path.insert(0, str(KOK))


def env_hazirla():
    env = KOK / ".env"
    if env.exists():
        return
    ornek = KOK / ".env.example"
    satirlar = ornek.read_text(encoding="utf-8").splitlines() if ornek.exists() else []
    yeni = []
    for s in satirlar:
        if s.startswith("JWT_SECRET_KEY="):
            s = "JWT_SECRET_KEY=" + secrets.token_urlsafe(32)
        elif s.startswith("ADMIN_TOKEN="):
            s = "ADMIN_TOKEN=" + secrets.token_urlsafe(32)
        yeni.append(s)
    env.write_text("\n".join(yeni) + "\n", encoding="utf-8")
    print("✅ .env oluşturuldu (rastgele güvenlik anahtarlarıyla). Giriş: admin / profaix123")


def demo_sifirla():
    from app.core.database import init_db
    from app.auth import ilk_admin_olustur
    from app.muhasebe.yerel.demo_veri import demo_veri_yukle
    init_db()
    ilk_admin_olustur()
    demo_veri_yukle(sifirla=True)
    # Video için temiz ekran: sohbet geçmişlerini de temizle
    from app.core.database import SessionLocal, ChatSession
    db = SessionLocal()
    try:
        db.query(ChatSession).delete()
        db.commit()
    finally:
        db.close()
    print("✅ Demo veri sıfırlandı ve yeniden yüklendi (sohbet geçmişi temizlendi).")


def main():
    env_hazirla()
    port = 8000
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if "--sifirla" in sys.argv:
        demo_sifirla()

    url = f"http://localhost:{port}/login"
    print(f"\n🚀 Profaix başlıyor → {url}\n   Durdurmak için: Ctrl+C\n")

    def _tarayici():
        time.sleep(2.5)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    if "--tarayicisiz" not in sys.argv:
        threading.Thread(target=_tarayici, daemon=True).start()

    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
