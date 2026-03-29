# main.py — Profaix FastAPI Uygulaması

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from app.core.config import validate_config, ENV
from app.core.database import init_db, SessionLocal, engine
from app.core.logger import logger
from app.chatbox.webhook import router as webhook_router


# ── STARTUP & SHUTDOWN ────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Profaix başlatılıyor...")
    validate_config()
    init_db()
    await startup_recovery()
    asyncio.create_task(mail_listener_baslat())
    logger.info(f"✅ Profaix hazır — Ortam: {ENV}")
    yield
    logger.info("Profaix kapanıyor...")


# ── FASTAPI UYGULAMASI ────────────────────────────────────────────────────
app = FastAPI(
    title="Profaix",
    description="Muhasebe Otomasyon Ajanı",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)


# ── HEALTH CHECK ─────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "env": ENV}


# ── STATUS ────────────────────────────────────────────────────────────────
@app.get("/status")
async def status():
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus

        toplam = db.query(WeighTicket).count()
        tamamlandi = db.query(WeighTicket).filter(
            WeighTicket.status == TicketStatus.TAMAMLANDI
        ).count()
        bekleyenler = db.query(WeighTicket).filter(
            WeighTicket.status.notin_([
                TicketStatus.TAMAMLANDI,
                TicketStatus.IPTAL,
                TicketStatus.HATA
            ])
        ).count()

        return {
            "toplam_fis": toplam,
            "tamamlandi": tamamlandi,
            "bekleyenler": bekleyenler,
        }
    except Exception as e:
        logger.error(f"Status hatası: {e}")
        return {"error": str(e)}
    finally:
        db.close()


# ── CHAT ARAYÜZÜ ─────────────────────────────────────────────────────────
@app.get("/chat", response_class=HTMLResponse)
async def chat_sayfasi():
    return HTMLResponse(content=CHAT_HTML)


@app.post("/api/chat")
async def chat_api(request: Request):
    """Web arayüzünden gelen mesajı LLM'e gönderir."""
    data = await request.json()
    mesaj = data.get("mesaj", "").strip()
    session_id = data.get("session_id", "web_user")

    if not mesaj:
        return JSONResponse({"cevap": "Mesaj boş olamaz."})

    db = SessionLocal()
    try:
        from app.chatbox.llm_handler import soru_cevapla
        cevap = soru_cevapla(mesaj, db, session_id)
        return JSONResponse({"cevap": cevap})
    except Exception as e:
        logger.error(f"Chat API hatası: {e}")
        return JSONResponse({"cevap": "❌ Bir hata oluştu, lütfen tekrar deneyin."})
    finally:
        db.close()


# ── ADMIN: DB TEMİZLE (sadece demo için) ──────────────────────────────────
@app.get("/admin/reset-db")
async def reset_db():
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text('DELETE FROM invoices'))
        conn.execute(text('DELETE FROM waybills'))
        conn.execute(text('DELETE FROM alerts'))
        conn.execute(text('DELETE FROM weigh_tickets'))
        conn.execute(text('DELETE FROM vehicles'))
        conn.execute(text('DELETE FROM processed_mails'))
        conn.commit()
    logger.info("🗑️ DB temizlendi (admin)")
    return {"status": "temizlendi"}


# ── STARTUP RECOVERY ──────────────────────────────────────────────────────
async def startup_recovery():
    db = SessionLocal()
    try:
        from app.core.database import WeighTicket, TicketStatus

        bekleyenler = db.query(WeighTicket).filter(
            WeighTicket.status == TicketStatus.IRSALIYE_OLUSTURULUYOR
        ).all()

        if bekleyenler:
            logger.warning(f"⚠️ {len(bekleyenler)} yarım kalan işlem tespit edildi")
            for ticket in bekleyenler:
                logger.info(f"Recovery: ticket_id={ticket.id} plaka={ticket.plaka}")
        else:
            logger.info("✅ Yarım kalan işlem yok")

    except Exception as e:
        logger.error(f"Startup recovery hatası: {e}")
    finally:
        db.close()


# ── MAIL LİSTENER ────────────────────────────────────────────────────────
async def mail_listener_baslat():
    import threading
    from app.mail.listener import gelen_kutu_dinle

    thread = threading.Thread(target=gelen_kutu_dinle, args=(60,), daemon=True)
    thread.start()
    logger.info("📬 Mail dinleyici bağımsız bir kanalda (Thread) başlatıldı.")


# ── CHAT HTML ARAYÜZÜ ─────────────────────────────────────────────────────
CHAT_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Profaix — Sorgu Paneli</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0a0f;
    --surface: #12121a;
    --border: #1e1e2e;
    --accent: #e8ff47;
    --accent2: #47ffb8;
    --text: #e8e8f0;
    --muted: #5a5a7a;
    --user-bg: #1a1a2e;
    --bot-bg: #0f1520;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Mono', monospace;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* Arka plan grid */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(rgba(232,255,71,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(232,255,71,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
    z-index: 0;
  }

  .header {
    padding: 20px 32px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
    background: var(--surface);
    position: relative;
    z-index: 1;
  }

  .logo {
    font-family: 'Syne', sans-serif;
    font-weight: 800;
    font-size: 22px;
    color: var(--accent);
    letter-spacing: -0.5px;
  }

  .logo span {
    color: var(--accent2);
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent2);
    box-shadow: 0 0 8px var(--accent2);
    animation: pulse 2s infinite;
    margin-left: auto;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  .status-text {
    font-size: 11px;
    color: var(--accent2);
    text-transform: uppercase;
    letter-spacing: 2px;
  }

  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 24px 32px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    position: relative;
    z-index: 1;
  }

  .messages::-webkit-scrollbar { width: 4px; }
  .messages::-webkit-scrollbar-track { background: transparent; }
  .messages::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  .message {
    max-width: 75%;
    animation: fadeIn 0.3s ease;
  }

  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .message.user {
    align-self: flex-end;
  }

  .message.bot {
    align-self: flex-start;
  }

  .message-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-bottom: 6px;
    color: var(--muted);
  }

  .message.user .message-label { text-align: right; }

  .message-bubble {
    padding: 14px 18px;
    border-radius: 2px;
    font-size: 14px;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .message.user .message-bubble {
    background: var(--user-bg);
    border: 1px solid var(--accent);
    border-radius: 2px 2px 0 2px;
    color: var(--accent);
  }

  .message.bot .message-bubble {
    background: var(--bot-bg);
    border: 1px solid var(--border);
    border-radius: 0 2px 2px 2px;
    color: var(--text);
    border-left: 2px solid var(--accent2);
  }

  .typing {
    display: flex;
    gap: 6px;
    align-items: center;
    padding: 14px 18px;
    background: var(--bot-bg);
    border: 1px solid var(--border);
    border-left: 2px solid var(--accent2);
    width: fit-content;
    border-radius: 0 2px 2px 2px;
  }

  .typing span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent2);
    animation: typing 1.2s infinite;
  }

  .typing span:nth-child(2) { animation-delay: 0.2s; }
  .typing span:nth-child(3) { animation-delay: 0.4s; }

  @keyframes typing {
    0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
    30% { transform: translateY(-6px); opacity: 1; }
  }

  .input-area {
    padding: 20px 32px;
    border-top: 1px solid var(--border);
    background: var(--surface);
    position: relative;
    z-index: 1;
  }

  .input-row {
    display: flex;
    gap: 12px;
    align-items: flex-end;
  }

  textarea {
    flex: 1;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: 'DM Mono', monospace;
    font-size: 14px;
    padding: 14px 16px;
    resize: none;
    outline: none;
    border-radius: 2px;
    max-height: 120px;
    min-height: 48px;
    transition: border-color 0.2s;
    line-height: 1.5;
  }

  textarea:focus {
    border-color: var(--accent);
  }

  textarea::placeholder {
    color: var(--muted);
  }

  button {
    background: var(--accent);
    color: var(--bg);
    border: none;
    padding: 14px 24px;
    font-family: 'Syne', sans-serif;
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 1px;
    text-transform: uppercase;
    cursor: pointer;
    border-radius: 2px;
    transition: all 0.2s;
    white-space: nowrap;
    height: 48px;
  }

  button:hover {
    background: var(--accent2);
    transform: translateY(-1px);
  }

  button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
    transform: none;
  }

  .hint {
    font-size: 11px;
    color: var(--muted);
    margin-top: 8px;
    letter-spacing: 0.5px;
  }

  .welcome {
    text-align: center;
    padding: 40px 20px;
    color: var(--muted);
  }

  .welcome h2 {
    font-family: 'Syne', sans-serif;
    font-size: 28px;
    font-weight: 800;
    color: var(--text);
    margin-bottom: 12px;
  }

  .welcome h2 span { color: var(--accent); }

  .welcome p {
    font-size: 13px;
    line-height: 1.8;
    max-width: 400px;
    margin: 0 auto;
  }

  @media (max-width: 600px) {
    .header, .messages, .input-area { padding-left: 16px; padding-right: 16px; }
    .message { max-width: 90%; }
  }
</style>
</head>
<body>

<div class="header">
  <div class="logo">PRO<span>FAIX</span></div>
  <div style="font-size:12px; color:var(--muted); letter-spacing:1px;">SORGU PANELİ</div>
  <div style="margin-left:auto; display:flex; align-items:center; gap:8px;">
    <div class="status-text">Aktif</div>
    <div class="status-dot"></div>
  </div>
</div>

<div class="messages" id="messages">
  <div class="welcome">
    <h2>PROFAIX <span>AI</span></h2>
    <p>Hurda kantar sistemi sorgu paneli.<br>Soru sormaya başlayın.</p>
  </div>
</div>

<div class="input-area">
  <div class="input-row">
    <textarea
      id="input"
      placeholder="Mesajınızı yazın..."
      rows="1"
      onkeydown="handleKey(event)"
      oninput="autoResize(this)"
    ></textarea>
    <button onclick="sendMessage()" id="sendBtn">Gönder</button>
  </div>
  <div class="hint">Enter → gönder &nbsp;|&nbsp; Shift+Enter → yeni satır</div>
</div>

<script>
  const SESSION_ID = 'web_' + Math.random().toString(36).substr(2, 9);
  const messagesEl = document.getElementById('messages');
  const inputEl = document.getElementById('input');
  const sendBtn = document.getElementById('sendBtn');
  let isFirstMessage = true;

  function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  }

  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  function addMessage(text, type) {
    // Welcome mesajını kaldır
    const welcome = messagesEl.querySelector('.welcome');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = `message ${type}`;

    const label = document.createElement('div');
    label.className = 'message-label';
    label.textContent = type === 'user' ? 'Siz' : 'Profaix AI';

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.textContent = text;

    div.appendChild(label);
    div.appendChild(bubble);
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function showTyping() {
    const welcome = messagesEl.querySelector('.welcome');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = 'message bot';
    div.id = 'typing';

    const label = document.createElement('div');
    label.className = 'message-label';
    label.textContent = 'Profaix AI';

    const typing = document.createElement('div');
    typing.className = 'typing';
    typing.innerHTML = '<span></span><span></span><span></span>';

    div.appendChild(label);
    div.appendChild(typing);
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function removeTyping() {
    const el = document.getElementById('typing');
    if (el) el.remove();
  }

  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text || sendBtn.disabled) return;

    addMessage(text, 'user');
    inputEl.value = '';
    inputEl.style.height = 'auto';
    sendBtn.disabled = true;
    showTyping();

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mesaj: text, session_id: SESSION_ID })
      });
      const data = await res.json();
      removeTyping();
      addMessage(data.cevap, 'bot');
    } catch (err) {
      removeTyping();
      addMessage('❌ Bağlantı hatası, lütfen tekrar deneyin.', 'bot');
    } finally {
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  // Sayfa yüklenince inputa odaklan
  window.onload = () => inputEl.focus();
</script>
</body>
</html>"""