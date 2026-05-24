// 03-session.js -- Auth, oturum, sohbet kaydetme, mesaj yonlendirme

  async function cikisYap() {
    try {
      await fetch('/api/logout', { method: 'POST' });
    } catch {}
    try { localStorage.removeItem('profaix_auth_token'); } catch {}
    window.location.href = '/login';
  }

  async function sohbetiSil() {
    if (!confirm('Tüm sohbet geçmişi silinecek. Emin misiniz?')) return;
    if (SESSION_ID) {
      try {
        await fetch('/api/session/' + encodeURIComponent(SESSION_ID), { method: 'DELETE' });
      } catch {}
    }
    var ad = (document.getElementById('welcomeAd') || {}).textContent || '';
    messagesEl.innerHTML = '';
    messagesEl.classList.remove('chat-started');
    _welcomeRemoved = false;
    _aktifPanel = null;
    var w = document.createElement('div');
    w.className = 'welcome';
    w.id = 'welcome';
    w.innerHTML = '<h2>İyi günler, <em id="welcomeAd">' + safeHtml(ad) + '</em></h2><p>Muhasebe işlemleri için buradayım. Ne yapalım?</p>';
    messagesEl.appendChild(w);
  }

  async function loadSessionHistory() {
    if (!SESSION_ID) return;
    try {
      const res = await fetch('/api/session/' + encodeURIComponent(SESSION_ID));
      if (!res.ok) return;
      const data = await res.json();

      if (data.gecmis && data.gecmis.length > 0) {
        removeWelcome();
        _isRestoring = true;
        const restoredUiKeys = new Set();
        try {
          for (const msg of data.gecmis) {
            if (msg.role === 'ui' && msg.ui && msg.ui.kind) {
              const uiKey = JSON.stringify({ kind: String(msg.ui.kind), params: msg.ui.params || {} });
              if (restoredUiKeys.has(uiKey)) continue;
              restoredUiKeys.add(uiKey);
              await restoreUiPanel(msg.ui.kind, msg.ui.params || {});
              continue;
            }
            const c = msg.content != null ? String(msg.content) : '';
            if (msg.role === 'user') {
              if (c.indexOf('data:image') === 0) addImageMessage(c);
              else addMessage(c, 'user');
            } else if (msg.role === 'assistant') {
              addMessage(c, 'bot');
            }
          }
        } finally {
          _isRestoring = false;
        }
      }
    } catch (e) {
      console.log('Session geçmişi yüklenemedi:', e);
    }
  }

  function _yetkileriUygula(yetkiler) {
    try {
      const izin = new Set(yetkiler || []);
      document.querySelectorAll('.panel-btn[data-yetki]').forEach(function(btn) {
        btn.style.display = izin.has(btn.getAttribute('data-yetki')) ? '' : 'none';
      });
      // Tüm butonları gizlenmiş grupları da gizle
      document.querySelectorAll('.panel-group').forEach(function(grp) {
        const btns = grp.querySelectorAll('.panel-btn');
        const gorunur = Array.prototype.some.call(btns, function(b) { return b.style.display !== 'none'; });
        grp.style.display = gorunur ? '' : 'none';
      });
    } catch (e) { console.log('yetki uygula hatası', e); }
  }

  async function initChatSession() {
    try {
      const res = await fetch('/api/me');
      const user = await res.json();
      if (user.giris_yapildi) {
        const ad = user.ad_soyad || user.username || '';
        document.getElementById('userAdSoyad').textContent = ad;
        const wadEl = document.getElementById('welcomeAd');
        if (wadEl) wadEl.textContent = ad.split(' ')[0] || ad;
        _yetkileriUygula(user.yetkiler || []);
        if (user.chat_session_id) SESSION_ID = user.chat_session_id;
        else if (user.profaix_user_id) SESSION_ID = 'pfa_' + user.profaix_user_id;
        else if (user.id != null && user.id !== '') SESSION_ID = 'loc_' + user.id;
        else SESSION_ID = 'web_' + Math.random().toString(36).substr(2, 9);
      } else {
        SESSION_ID = 'web_' + Math.random().toString(36).substr(2, 9);
      }
      await loadSessionHistory();
    } catch (e) {
      console.log('initChatSession', e);
      if (!SESSION_ID) SESSION_ID = 'web_' + Math.random().toString(36).substr(2, 9);
    }
    _sessionReady = true;
  }

  async function ensureSessionReady() {
    if (_sessionReady) return;
    if (!_sessionBoot) {
      _sessionBoot = initChatSession().finally(function() { _sessionBoot = null; });
    }
    await _sessionBoot;
  }

  function sohbetParcaKaydet(userText, assistantText) {
    if (!SESSION_ID || !_sessionReady) return;
    const body = { session_id: SESSION_ID };
    if (userText != null && String(userText).trim() !== '') body.user = String(userText);
    if (assistantText != null && String(assistantText).trim() !== '') body.assistant = String(assistantText);
    if (!body.user && !body.assistant) return;
    fetch('/api/session/append', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).catch(function() {});
  }

  function sohbetPanelKaydet(kind, params) {
    if (!SESSION_ID || !_sessionReady || !kind || _isRestoring) return;
    fetch('/api/session/append', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: SESSION_ID,
        ui_panel: { kind: kind, params: params || {} }
      })
    }).catch(function() {});
  }

  function sohbetKeywordVePanelKaydet(userText, kind, params) {
    if (!SESSION_ID || !_sessionReady || !kind || _isRestoring) return;
    const body = {
      session_id: SESSION_ID,
      ui_panel: { kind: kind, params: params || {} }
    };
    if (userText != null && String(userText).trim() !== '') body.user = String(userText);
    fetch('/api/session/append', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).catch(function() {});
  }

  /** Panel / yazdığı kelime ile aynı araç açıldığında ücretlendirme (restore sırasında çağrılmaz). */
  function billablePanelTikla(kind, detail) {
    if (_isRestoring || _sessionReplaced) return;
    const body = { panel: kind };
    if (detail && typeof detail === 'object') body.detail = detail;
    fetch('/api/billing/panel-click', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).catch(function() {});
  }

  function _panelKapat() {
    if (_aktifPanel) {
      // Tüm buton ve inputları devre dışı bırak
      _aktifPanel.querySelectorAll('button, input, select').forEach(function(el) {
        el.disabled = true;
        el.style.opacity = '0.4';
        el.style.cursor = 'not-allowed';
      });
      _aktifPanel.style.opacity = '0.5';
      _aktifPanel = null;
    }
  }

  // ── GÜNLÜK ÖZET ──────────────────────────────────────────────────


  function mesajYonlendir(text) {
    const lower = text.toLowerCase().trim();
    for (const kural of YONLENDIRME) {
      for (const grup of kural.kelimeler) {
        if (grup.every(k => lower.includes(k))) return kural;
      }
    }
    return null;
  }

  // ── INTENT → FONKSİYON HARİTASI ────────────────────────────────────
  // Haiku'dan gelen intent string'i doğrudan bu map'e bakarak çözülür.
  // Yeni bir intent eklemek istersen buraya 1 satır eklemen yeterli.
  // INTENT_MAP — intent string'i → frontend fonksiyonu
  // params: Haiku'nun cümleden çıkardığı müşteri, tarih, tutar gibi değerler
  // Fonksiyonlar params'ı alır, formu önceden doldurur — kullanıcı tekrar seçmez
  const INTENT_MAP = {
    'bekleyen_irsaliyeler': (p) => bekleyenIrsaliyeleriGoster(),
    'toplu_fatura':         (p) => topluFaturaGoster(),
    'fatura_sorgula':       (p) => faturaListesiGoster(p.musteri),
    'fatura_detay':         (p) => faturaDetayGoster(String(p.fatura_id || p.faturaId || ''), p.aciklama || 'Fatura', p.musteri || ''),
    'tahsilat_ekle':        (p) => tahsilatFormGoster(p.musteri, p.tutar),
    'yeni_cari':            (p) => cariFormGoster(),
    'musteri_bakiyeleri':   (p) => bakiyeleriGoster(p.musteri),
    'hesap_ozeti':          (p) => hesapOzetiGoster(p.musteri, p.baslangic, p.bitis),
    'gunluk_ozet':          (p) => gunlukOzetGoster(),
    'son_fisler':           (p) => sonFisleriGoster(),
    'hesaplar':             (p) => hesaplarGoster(),
    'stok_hareketleri':     (p) => stokHareketleriGoster(),
    'gelir_gider_raporu':   (p) => gelirGiderRaporuGoster(),
    'odeme_ekle':           (p) => odemeFormGoster(),
    'para_transferi':       (p) => paraTransferiFormGoster(),
    'alis_faturasi_kes':    (p) => alisFaturasiFormGoster(p.musteri),
    'fatura_iptal':         (p) => faturaIptalFormGoster(p.fatura_id || p.faturaId),
    'e_fatura_kes':         (p) => eFaturaFormGoster(p.fatura_id || p.faturaId),
    'cari_detay':           (p) => cariDetayGoster(p.musteri),
    'satis_faturasi_kes':   (p) => topluFaturaGoster(),
  };

  async function restoreUiPanel(kind, params) {
    const fn = INTENT_MAP[kind];
    if (typeof fn !== 'function') return;
    try {
      await fn(params || {});
    } catch (e) {
      console.log('restoreUiPanel', kind, e);
    }
  }

  // ── CHAT ─────────────────────────────────────────────────────────

