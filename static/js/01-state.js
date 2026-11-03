// 01-state.js -- Global state + DOM element refs

  let SESSION_ID = null;
  let _sessionReady = false;
  let _sessionBoot = null;
  let _isRestoring = false; // Restore sırasında DB'ye tekrar yazılmasını engeller
  let AUTH_TOKEN = (window.PROFAIX_BOOTSTRAP_TOKEN || '').trim();
  // Aktif muhasebe sağlayıcısı (/api/me → muhasebe). 'yerel' | 'parasut'
  let MUHASEBE = { saglayici: 'parasut', ad: 'Paraşüt', excel_aktar: false, ai_aktif: true };

  try {
    if (AUTH_TOKEN) localStorage.setItem('profaix_auth_token', AUTH_TOKEN);
    else AUTH_TOKEN = localStorage.getItem('profaix_auth_token') || '';
  } catch {}

  const _nativeFetch = window.fetch.bind(window);
  let _sessionReplaced = false;
  let _sessionReplacedShown = false;
  const SESSION_REPLACED_MESSAGE = '❌ Bu hesap başka bir cihazda açıldığı için bu oturum kapatıldı.';


  const messagesEl = document.getElementById('messages');
  const inputEl    = document.getElementById('chatInput');
  const sendBtn    = document.getElementById('sendBtn');
  let faturaAkisi  = null;
  let _aktifPanel  = null; // Aktif panel elementi

  // ── SESSION GEÇMİŞİ YÜKLEME ───────────────────────────────────────
