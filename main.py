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
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500;9..40,600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:        #f5f4f1;
    --surface:   #ffffff;
    --border:    #e6e2db;
    --border2:   #d0cbc3;
    --accent:    #2d5a3d;
    --accent-lt: #eaf2ec;
    --text:      #1c1c1a;
    --muted:     #8c8880;
    --muted2:    #bab7b0;
    --user-bg:   #2d5a3d;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
    --shadow:    0 2px 8px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* ── HEADER ── */
  .header {
    padding: 0 36px;
    height: 60px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
    background: var(--surface);
    box-shadow: var(--shadow-sm);
    position: relative;
    z-index: 10;
    flex-shrink: 0;
  }

  .logo-mark {
    width: 32px;
    height: 32px;
    background: var(--accent);
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .logo-mark svg {
    width: 16px; height: 16px;
    stroke: #fff; fill: none;
    stroke-width: 2; stroke-linecap: round;
  }

  .logo-text {
    font-family: 'Instrument Serif', serif;
    font-size: 19px;
    color: var(--text);
    letter-spacing: -0.2px;
  }

  .logo-text em { font-style: italic; color: var(--accent); }

  .sep { width: 1px; height: 18px; background: var(--border2); }

  .header-label {
    font-size: 11.5px;
    color: var(--muted);
    font-weight: 400;
    letter-spacing: 0.3px;
  }

  .status-pill {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--accent-lt);
    border: 1px solid #c5d9ca;
    padding: 4px 11px;
    border-radius: 100px;
  }

  .status-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--accent);
    animation: glow 2.5s ease-in-out infinite;
  }

  @keyframes glow {
    0%,100% { opacity: 1; }
    50% { opacity: 0.5; }
  }

  .status-label {
    font-size: 11px;
    font-weight: 500;
    color: var(--accent);
    letter-spacing: 0.2px;
  }

  /* ── MESSAGES ── */
  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 28px 36px;
    display: flex;
    flex-direction: column;
    gap: 18px;
    scroll-behavior: smooth;
  }

  .messages::-webkit-scrollbar { width: 4px; }
  .messages::-webkit-scrollbar-track { background: transparent; }
  .messages::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 4px; }

  /* ── WELCOME ── */
  .welcome {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    padding: 56px 24px 40px;
    gap: 14px;
  }

  .welcome-icon {
    width: 52px; height: 52px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: var(--shadow);
    margin-bottom: 4px;
  }

  .welcome-icon svg {
    width: 24px; height: 24px;
    stroke: var(--accent); fill: none; stroke-width: 1.5;
    stroke-linecap: round; stroke-linejoin: round;
  }

  .welcome h2 {
    font-family: 'Instrument Serif', serif;
    font-size: 30px;
    font-weight: 400;
    color: var(--text);
    letter-spacing: -0.4px;
    line-height: 1.25;
  }

  .welcome h2 em { font-style: italic; color: var(--accent); }

  .welcome p {
    font-size: 13.5px;
    color: var(--muted);
    line-height: 1.7;
    max-width: 320px;
    font-weight: 300;
  }

  .quick-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
    justify-content: center;
    max-width: 500px;
    margin-top: 6px;
  }

  .quick-btn {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    font-size: 12px;
    font-weight: 400;
    padding: 7px 14px;
    border-radius: 100px;
    cursor: pointer;
    transition: all 0.15s ease;
    box-shadow: var(--shadow-sm);
  }

  .quick-btn:hover {
    background: var(--accent-lt);
    border-color: #c5d9ca;
    color: var(--accent);
    transform: translateY(-1px);
    box-shadow: var(--shadow);
  }

  /* ── BUBBLES ── */
  .message {
    display: flex;
    flex-direction: column;
    max-width: 68%;
    animation: pop 0.25s cubic-bezier(0.34,1.56,0.64,1);
  }

  @keyframes pop {
    from { opacity: 0; transform: translateY(8px) scale(0.97); }
    to   { opacity: 1; transform: none; }
  }

  .message.user { align-self: flex-end; }
  .message.bot  { align-self: flex-start; }

  .msg-meta {
    font-size: 10px;
    color: var(--muted2);
    margin-bottom: 4px;
    letter-spacing: 0.2px;
  }

  .message.user .msg-meta { text-align: right; }

  .bubble {
    padding: 12px 16px;
    font-size: 13.5px;
    line-height: 1.75;
    white-space: pre-wrap;
    word-break: break-word;
    font-weight: 400;
  }

  .message.user .bubble {
    background: var(--user-bg);
    color: #fff;
    border-radius: 14px 14px 3px 14px;
    box-shadow: 0 2px 10px rgba(45,90,61,0.2);
  }

  .message.bot .bubble {
    background: var(--surface);
    color: var(--text);
    border-radius: 3px 14px 14px 14px;
    border: 1px solid var(--border);
    box-shadow: var(--shadow);
  }

  /* ── TYPING ── */
  .typing-wrap {
    display: flex;
    flex-direction: column;
    max-width: 68%;
    align-self: flex-start;
    animation: pop 0.25s cubic-bezier(0.34,1.56,0.64,1);
  }

  .typing-bubble {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 3px 14px 14px 14px;
    padding: 14px 18px;
    display: flex;
    gap: 5px;
    align-items: center;
    box-shadow: var(--shadow);
    width: fit-content;
  }

  .typing-bubble span {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--muted2);
    animation: bounce 1.3s ease-in-out infinite;
  }

  .typing-bubble span:nth-child(2) { animation-delay: 0.15s; }
  .typing-bubble span:nth-child(3) { animation-delay: 0.3s; }

  @keyframes bounce {
    0%,60%,100% { transform: translateY(0); opacity: 0.4; }
    30% { transform: translateY(-5px); opacity: 1; }
  }

  /* ── INPUT ── */
  .input-area {
    padding: 14px 36px 18px;
    border-top: 1px solid var(--border);
    background: var(--surface);
    flex-shrink: 0;
  }

  .input-box {
    display: flex;
    align-items: flex-end;
    gap: 8px;
    background: var(--bg);
    border: 1.5px solid var(--border2);
    border-radius: 13px;
    padding: 8px 8px 8px 16px;
    transition: border-color 0.18s, box-shadow 0.18s;
  }

  .input-box:focus-within {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(45,90,61,0.07);
  }

  textarea {
    flex: 1;
    background: transparent;
    border: none;
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    font-size: 13.5px;
    font-weight: 400;
    resize: none;
    outline: none;
    max-height: 120px;
    min-height: 24px;
    line-height: 1.6;
    padding: 3px 0;
  }

  textarea::placeholder { color: var(--muted2); }

  .send-btn {
    width: 36px; height: 36px;
    background: var(--accent);
    border: none;
    border-radius: 9px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    transition: all 0.15s;
  }

  .send-btn:hover { background: #245030; transform: scale(1.06); }
  .send-btn:disabled { opacity: 0.3; cursor: not-allowed; transform: none; }

  .send-btn svg {
    width: 15px; height: 15px;
    stroke: #fff; fill: none;
    stroke-width: 2; stroke-linecap: round; stroke-linejoin: round;
  }

  .input-hint {
    font-size: 10.5px;
    color: var(--muted2);
    margin-top: 7px;
    padding-left: 2px;
  }

  @media (max-width: 600px) {
    .header, .messages, .input-area { padding-left: 16px; padding-right: 16px; }
    .message, .typing-wrap { max-width: 90%; }
    .sep, .header-label { display: none; }
  }
</style>
</head>
<body>

<div class="header">
  <div class="logo-mark">
    <svg viewBox="0 0 16 16"><path d="M2 4h12M2 8h12M2 12h8"/></svg>
  </div>
  <div class="logo-text">Pro<em>faix</em></div>
  <div class="sep"></div>
  <div class="header-label">Sorgu Paneli</div>
  <div class="status-pill">
    <div class="status-dot"></div>
    <div class="status-label">Çevrimiçi</div>
  </div>
</div>

<div class="messages" id="messages">
  <div class="welcome" id="welcome">
    <div class="welcome-icon">
      <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
    </div>
    <h2>Merhaba,<br><em>Profaix</em>'e hoş geldiniz.</h2>
    <p>Kantar sistemi verileri hakkında soru sorabilir, günlük rapor alabilirsiniz.</p>
    <div class="quick-actions">
      <button class="quick-btn" onclick="quickSend('Bugün kaç ton geldi?')">Bugün kaç ton geldi?</button>
      <button class="quick-btn" onclick="quickSend('Hangi plakalar geldi?')">Hangi plakalar geldi?</button>
      <button class="quick-btn" onclick="quickSend('Bekleyen irsaliyeler var mı?')">Bekleyen irsaliyeler</button>
      <button class="quick-btn" onclick="quickSend('Son 10 fatura')">Son 10 fatura</button>
      <button class="quick-btn" onclick="quickSend('Bugün kaç fatura kesildi?')">Bugün kesilen faturalar</button>
    </div>
  </div>
</div>

<div class="input-area">
  <div class="input-box">
    <textarea
      id="input"
      placeholder="Mesajınızı yazın…"
      rows="1"
      onkeydown="handleKey(event)"
      oninput="autoResize(this)"
    ></textarea>
    <button class="send-btn" onclick="sendMessage()" id="sendBtn" title="Gönder">
      <svg viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
    </button>
  </div>
  <div class="input-hint">Enter → gönder &nbsp;·&nbsp; Shift+Enter → yeni satır</div>
</div>

<script>
  const SESSION_ID = 'web_' + Math.random().toString(36).substr(2, 9);
  const messagesEl = document.getElementById('messages');
  const inputEl    = document.getElementById('input');
  const sendBtn    = document.getElementById('sendBtn');

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

  function removeWelcome() {
    const w = document.getElementById('welcome');
    if (w) w.remove();
  }

  function now() {
    return new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
  }

  function addMessage(text, type) {
    removeWelcome();
    const wrap = document.createElement('div');
    wrap.className = 'message ' + type;

    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.textContent = (type === 'user' ? 'Siz' : 'Profaix AI') + ' · ' + now();

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;

    wrap.appendChild(meta);
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function showTyping() {
    removeWelcome();
    const wrap = document.createElement('div');
    wrap.className = 'typing-wrap';
    wrap.id = 'typing';

    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.textContent = 'Profaix AI yazıyor…';

    const bubble = document.createElement('div');
    bubble.className = 'typing-bubble';
    bubble.innerHTML = '<span></span><span></span><span></span>';

    wrap.appendChild(meta);
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
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
    } catch {
      removeTyping();
      addMessage('❌ Bağlantı hatası, lütfen tekrar deneyin.', 'bot');
    } finally {
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  function quickSend(text) {
    inputEl.value = text;
    sendMessage();
  }

  window.onload = () => inputEl.focus();
</script>
</body>
</html>"""