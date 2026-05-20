// 02-core.js -- Fetch intercept + UI helpers (panel, dialog, format, addMessage, dropdown, fiyat)

  function oturumDegistiUyarisi() {
    _sessionReplaced = true;
    AUTH_TOKEN = '';
    try { localStorage.removeItem('profaix_auth_token'); } catch {}
    if (typeof inputEl !== 'undefined' && inputEl) {
      inputEl.value = '';
      inputEl.disabled = true;
      inputEl.placeholder = 'Bu oturum başka cihazda açıldığı için kapatıldı.';
    }
    if (typeof sendBtn !== 'undefined' && sendBtn) {
      sendBtn.disabled = true;
      sendBtn.style.opacity = '0.45';
      sendBtn.style.cursor = 'not-allowed';
    }
    if (_sessionReplacedShown) return;
    _sessionReplacedShown = true;
    if (typeof addMessage === 'function') {
      addMessage(SESSION_REPLACED_MESSAGE, 'bot');
    } else {
      alert(SESSION_REPLACED_MESSAGE);
    }
  }

  window.fetch = function(input, init) {
    init = init || {};
    const url = typeof input === 'string' ? input : ((input && input.url) || '');
    const sameOriginApi = url.indexOf('/api/') === 0 || url.indexOf('/admin/') === 0;
    if (AUTH_TOKEN && sameOriginApi) {
      const headers = new Headers(init.headers || {});
      if (!headers.has('Authorization')) headers.set('Authorization', 'Bearer ' + AUTH_TOKEN);
      init = Object.assign({}, init, { headers: headers });
    }
    return _nativeFetch(input, init).then(function(res) {
      if (res.status === 401 && sameOriginApi) {
        res.clone().json().then(function(data) {
          if (data && data.code === 'SESSION_REPLACED') oturumDegistiUyarisi();
        }).catch(function() {});
      }
      return res;
    });
  };


  function panelAc() {
    document.getElementById('quickPanel').classList.add('open');
    document.getElementById('panelOverlay').classList.add('open');
    document.getElementById('panelToggle').style.display = 'none';
  }
  function panelKapat() {
    document.getElementById('quickPanel').classList.remove('open');
    document.getElementById('panelOverlay').classList.remove('open');
    document.getElementById('panelToggle').style.display = 'flex';
  }
  // ── Sol panel grupları (akordeon): aynı anda birden fazla açık olabilir,
  // ama tek bir grubu açtığında diğerlerinin kapanmasını isteyen kullanıcılar
  // için ALT tuşuyla "tek seferde tek açık" davranışı.
  // ── TİP SEÇİM HELPER ─────────────────────────────────────────────
  // Fatura sorgu/iptal/güncelle gibi her iki tarafa da çalışan işlemlerde
  // kullanıcıya 'Müşteri mi Tedarikçi mi?' sorar. Onay sonrası callback
  // 'customer' veya 'supplier' ile tetiklenir.
  function tipSecimSor(baslik, aciklama, callback) {
    const wrap = document.createElement('div');
    const h = document.createElement('div');
    h.style.cssText = 'font-size:13px;font-weight:500;color:var(--text);margin-bottom:8px;';
    h.textContent = baslik || '🔍 İşlem yönü';
    wrap.appendChild(h);

    if (aciklama) {
      const info = document.createElement('div');
      info.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);';
      info.textContent = aciklama;
      wrap.appendChild(info);
    }

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;';

    function btn(label, tip, primary) {
      const b = document.createElement('button');
      b.style.cssText = primary
        ? 'flex:1;min-width:140px;padding:10px 14px;border-radius:9px;font-size:13px;font-weight:600;cursor:pointer;background:var(--accent,#bf5b2a);color:#fff;border:none;'
        : 'flex:1;min-width:140px;padding:10px 14px;border-radius:9px;font-size:13px;font-weight:600;cursor:pointer;background:transparent;color:var(--text);border:1.5px solid var(--border);';
      b.textContent = label;
      b.onclick = function() {
        // Butonları devre dışı bırak
        Array.from(btnRow.querySelectorAll('button')).forEach(function(x) { x.disabled = true; });
        callback(tip);
      };
      return b;
    }
    btnRow.appendChild(btn('👤 Müşteri (Satış faturası)', 'customer', true));
    btnRow.appendChild(btn('🏭 Tedarikçi (Alış faturası)', 'supplier', false));
    wrap.appendChild(btnRow);

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel';
    iptalBtn.style.cssText = 'margin-top:8px;font-size:12px;';
    iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() {
      Array.from(btnRow.querySelectorAll('button')).forEach(function(x) { x.disabled = true; });
      iptalBtn.disabled = true;
      addMessage('❌ İptal edildi.', 'bot');
    };
    wrap.appendChild(iptalBtn);

    addMessage('', 'bot', wrap);
  }

  function panelGrupTogle(headBtn) {
    const grup = headBtn.parentElement;
    if (!grup) return;
    const tum = document.querySelectorAll('.panel-group');
    if (window.event && window.event.altKey) {
      tum.forEach(function(g) { if (g !== grup) g.classList.remove('open'); });
    }
    grup.classList.toggle('open');
    try { localStorage.setItem('panel_grup_son', grup.getAttribute('data-pg') || ''); } catch (e) {}
  }
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') panelKapat();
  });

  function formatTarih(tarih) {
    // "2026-03-27" → "27.03.2026"
    if (!tarih || tarih === '-') return tarih;
    const m = tarih.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return m[3] + '.' + m[2] + '.' + m[1];
    return tarih;
  }
  // ── FAZLA TAHSİLAT ONAY DIALOGU ─────────────────────────────────
  function tahsilatOnayDialogu(guncelBakiye, girilenTutar, cariAdi, onayCallback) {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'background:#fffbeb;border:1.5px solid #f59e0b;border-radius:11px;padding:14px 16px;';

    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-size:13px;font-weight:600;color:#92400e;margin-bottom:8px;';
    baslik.textContent = '⚠️ Fazla Tutar Uyarısı';
    wrap.appendChild(baslik);

    const bilgi = document.createElement('div');
    bilgi.style.cssText = 'font-size:12px;color:#78350f;line-height:1.8;margin-bottom:12px;';
    bilgi.innerHTML =
      '🏢 <b>' + cariAdi + '</b><br>' +
      '📊 Güncel bakiye: <b>' + guncelBakiye.toLocaleString('tr-TR', {minimumFractionDigits:0}) + ' TL</b><br>' +
      '💰 Girilen tutar: <b>' + girilenTutar.toLocaleString('tr-TR', {minimumFractionDigits:0}) + ' TL</b><br>' +
      '⚡ Fark: <b>' + (girilenTutar - guncelBakiye).toLocaleString('tr-TR', {minimumFractionDigits:0}) + ' TL fazla</b><br><br>' +
      'Bu cari eksiye düşecek. Yine de devam etmek istiyor musunuz?';
    wrap.appendChild(bilgi);

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;';

    const onayBtn = document.createElement('button');
    onayBtn.className = 'btn-primary';
    onayBtn.style.background = '#b45309';
    onayBtn.textContent = '✅ Evet, devam et';
    onayBtn.onclick = function() {
      onayBtn.disabled = true;
      iptalOnayBtn.disabled = true;
      onayCallback();
    };

    const iptalOnayBtn = document.createElement('button');
    iptalOnayBtn.className = 'btn-cancel';
    iptalOnayBtn.textContent = '✕ İptal';
    iptalOnayBtn.onclick = function() {
      onayBtn.disabled = true;
      iptalOnayBtn.disabled = true;
      addMessage('❌ Tahsilat iptal edildi.', 'bot');
    };

    btnRow.appendChild(onayBtn);
    btnRow.appendChild(iptalOnayBtn);
    wrap.appendChild(btnRow);

    addMessage('', 'bot', wrap);
  }

  function plakacikar(aciklama) {
    if (!aciklama) return null;
    const m = aciklama.match(/\b(\d{2}[A-Z]{1,3}\d{2,4})\b/i);
    return m ? m[1].toUpperCase() : null;
  }

  function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  }

  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  }

  var _welcomeRemoved = false;
  function removeWelcome() {
    if (_welcomeRemoved) return;
    const w = document.getElementById('welcome');
    if (w) {
      _welcomeRemoved = true;
      messagesEl.classList.add('chat-started');
      w.style.transition = 'opacity 0.2s ease';
      w.style.opacity = '0';
      setTimeout(function() { if(w.parentNode) w.remove(); }, 200);
    }
  }

  function now() {
    return new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
  }

  // API'den gelen metinleri innerHTML'de kullanmadan önce HTML karakter kaçışı uygular.
  function safeHtml(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function addMessage(text, type, extraEl) {
    removeWelcome();
    // Önceki aktif paneli kapat
    if (extraEl && type === 'bot') {
      _panelKapat();
    }
    const wrap = document.createElement('div');
    wrap.className = 'message ' + type;
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.textContent = (type === 'user' ? 'Siz' : 'Profaix') + ' · ' + now();
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    if (text) bubble.textContent = text;
    if (extraEl) {
      bubble.appendChild(extraEl);
      if (type === 'bot') _aktifPanel = bubble;
    }
    wrap.appendChild(meta);
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    requestAnimationFrame(function() {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    });
    return bubble;
  }

  function addImageMessage(src) {
    removeWelcome();
    const wrap = document.createElement('div');
    wrap.className = 'message user';
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.style.textAlign = 'right';
    meta.textContent = 'Siz · ' + now();
    const imgWrap = document.createElement('div');
    imgWrap.className = 'img-bubble';
    const img = document.createElement('img');
    img.src = src;
    img.style.cursor = 'pointer';
    img.onclick = () => {
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center;z-index:999;cursor:pointer;';
      const bigImg = document.createElement('img');
      bigImg.src = src;
      bigImg.style.cssText = 'max-width:90%;max-height:90%;border-radius:12px;';
      overlay.appendChild(bigImg);
      overlay.onclick = () => overlay.remove();
      document.body.appendChild(overlay);
    };
    imgWrap.appendChild(img);
    wrap.appendChild(meta);
    wrap.appendChild(imgWrap);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function showTyping() {
    removeWelcome();
    // Mevcut typing varsa önce kaldır — duplicate olmasın
    removeTyping();
    const wrap = document.createElement('div');
    wrap.className = 'typing-wrap';
    wrap.id = 'typing';
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.textContent = 'Profaix yazıyor…';
    const bubble = document.createElement('div');
    bubble.className = 'typing-bubble';
    bubble.innerHTML = '<span></span><span></span><span></span>';
    wrap.appendChild(meta);
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    // requestAnimationFrame: DOM render tamamlandıktan sonra scroll yap
    requestAnimationFrame(function() {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    });
  }

  function removeTyping() {
    const el = document.getElementById('typing');
    if (el) el.remove();
  }

  function fiyatGoster(fiyat) {
    // Fiyatı tam olarak göster — gereksiz sıfırları kırp ama değeri kesme
    // 14.4458 → "14,4458" | 14 → "14" | 14.5 → "14,5"
    if (fiyat === null || fiyat === undefined) return '';
    // Maksimum 4 ondalık hane, sondaki sıfırları kaldır
    let str = fiyat.toFixed(4).replace('.', ',').replace(/,?0+$/, '');
    // Binlik ayraç ekle (tam kısım için)
    let parcalar = str.split(',');
    parcalar[0] = parcalar[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    return parcalar.join(',');
  }

  function parseFiyat(metin) {
    // KURAL: Nokta = binlik ayraç (yoksay), Virgül = ondalık ayraç
    // Sadece rakam, nokta ve virgül kabul et — harf içeriyorsa geçersiz
    metin = metin.trim()
      .replace(/tl/gi, '').replace(/lira/gi, '').replace('₺', '').trim();
    // Rakam, nokta ve virgül dışında karakter varsa geçersiz
    if (/[^0-9.,]/.test(metin)) return null;
    // Önce noktaları kaldır (binlik ayraç), sonra virgülü noktaya çevir (ondalık)
    metin = metin.replace(/\./g, '').replace(',', '.');
    const f = parseFloat(metin);
    return (f > 0) ? f : null;
  }

  // ── MESAJ YÖNLENDİRME ────────────────────────────────────────────

  const YONLENDIRME = [
    {
      kind: 'bekleyen_irsaliyeler',
      fn: () => bekleyenIrsaliyeleriGoster(),
      kelimeler: [
        ['bekleyen', 'irsaliy'], ['bekleyen', 'fatura'], ['faturalanmamis'],
        ['faturalanmamış'], ['irsaliye', 'listele'], ['irsaliye', 'var mi'],
        ['irsaliye', 'var mı'], ['bekleyenler'], ['irsaliyeler'],
        ['açık irsaliy'], ['acik irsaliy'], ['fatura kesilmemiş'], ['fatura kesilmemis']
      ]
    },
    {
      kind: 'musteri_bakiyeleri',
      fn: () => bakiyeleriGoster(),
      kelimeler: [
        ['müşteri bakiy'], ['musteri bakiy'], ['tüm bakiy'], ['tum bakiy'],
        ['bakiye listesi'], ['bakiyeleri göster'], ['bakiyeleri goster'],
        ['alacaklarım'], ['alacaklarim'], ['borçlular'], ['borcular'],
        ['tahsilat edilecek'], ['tahsil edilecek'], ['cari listesi']
      ]
    },
    {
      kind: 'hesap_ozeti',
      fn: () => hesapOzetiGoster(),
      kelimeler: [
        ['hesap özeti'], ['hesap ozeti'], ['genel özet'], ['genel ozet'],
        ['genel durum'], ['toplam alacak'], ['toplam borç'], ['toplam borc'],
        ['durum ne'], ['genel bakış'], ['genel bakis'], ['özet göster'],
        ['ozet goster'], ['duruma bak'], ['finansal durum']
      ]
    },
    {
      kind: 'tahsilat_ekle',
      fn: () => tahsilatFormGoster(),
      kelimeler: [
        ['tahsilat ekle'], ['ödeme aldım'], ['odeme aldim'], ['para aldım'], ['para aldim'],
        ['ödeme ekle'], ['odeme ekle'], ['ödeme geldi'], ['odeme geldi'],
        ['para geldi'], ['tahsilat yap'], ['tahsil ettim'],
        ['para çek'], ['para cek'], ['yeni tahsilat'], ['ödeme al'],
        ['odeme al'], ['para tahsil'], ['tahsilat girişi'], ['tahsilat girisi'],
        ['tahsilat ekle']
      ]
    },
    {
      kind: 'toplu_fatura',
      fn: () => topluFaturaGoster(),
      kelimeler: [
        ['toplu fatura'], ['toplu satış'], ['toplu satis'],
        ['birleştir'], ['birlestir'], ['fatura birleştir'],
        ['irsaliyeleri birleştir'], ['irsaliyeleri birlestir'],
        ['tek fatura'], ['hepsini tek faturada'], ['toplu kes']
      ]
    },
    {
      kind: 'fatura_sorgula',
      fn: () => faturaListesiGoster(),
      kelimeler: [
        ['fatura sorgula'], ['fatura listesi'], ['fatura sorgulamak'],
        ['faturalara bak'], ['fatura ekranı']
      ]
    },
    {
      kind: 'yeni_cari',
      fn: () => cariFormGoster(),
      kelimeler: [
        ['yeni cari'], ['cari ekle'], ['cari olustur'], ['cari oluştur'],
        ['yeni musteri'], ['yeni müşteri'], ['firma ekle'], ['musteri ekle'],
        ['müşteri ekle'], ['yeni firma']
      ]
    },
    {
      kind: 'alis_faturasi_kes',
      fn: () => alisFaturasiFormGoster(),
      kelimeler: [
        ['alış faturası'], ['alis faturasi'], ['alış faturasi'], ['alis faturası'],
        ['alış faturası kes'], ['alis faturasi kes'],
        ['tedarikçiden fatura'], ['tedarikciden fatura'],
        ['hurda aldım fatura'], ['hurda aldim fatura']
      ]
    },
    {
      kind: 'fatura_iptal',
      fn: () => faturaIptalFormGoster(),
      kelimeler: [
        ['fatura iptal'], ['faturayi iptal'], ['faturayı iptal'],
        ['yanlış fatura'], ['yanlis fatura'], ['fatura sil'], ['faturayı sil'], ['faturayi sil'],
        ['hatalı fatura'], ['hatali fatura']
      ]
    },
    {
      kind: 'e_fatura_kes',
      fn: () => eFaturaFormGoster(),
      kelimeler: [
        ['e-fatura'], ['efatura'], ['e fatura'], ['e-arşiv'], ['e arsiv'], ['e-arsiv'],
        ['resmileştir'], ['resmilestir'], ['e fatura kes'], ['efatura kes']
      ]
    }
  ];


  function cariSecimDropdown(cariler, onSec, placeholder) {
    placeholder = placeholder || 'Cari adı yazın...';
    const wrap = document.createElement('div');
    wrap.className = 'cari-search-wrap';

    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'cari-search-input';
    inp.placeholder = placeholder;
    inp.autocomplete = 'off';

    const dropdown = document.createElement('div');
    dropdown.className = 'cari-dropdown';
    dropdown.style.display = 'none';

    function renderDropdown(filtre) {
      dropdown.innerHTML = '';
      if (!filtre || filtre.length === 0) {
        dropdown.style.display = 'none';
        return;
      }
      const liste = cariler.filter(function(c) {
        return c.ad.toLowerCase().includes(filtre.toLowerCase());
      });
      if (liste.length === 0) {
        const bos = document.createElement('div');
        bos.className = 'cari-dropdown-empty';
        bos.textContent = 'Cari bulunamadı';
        dropdown.appendChild(bos);
      } else {
        liste.forEach(function(c) {
          const item = document.createElement('div');
          item.className = 'cari-dropdown-item';
          const ad = document.createElement('span');
          ad.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
          ad.textContent = c.ad;
          item.appendChild(ad);
          // Tip etiketi (Paraşüt account_type'dan)
          const tip = c.tip || '';
          if (tip) {
            const tipBadge = document.createElement('span');
            tipBadge.style.cssText = 'flex-shrink:0;margin-left:6px;font-size:10px;padding:1px 5px;border-radius:4px;white-space:nowrap;';
            if (tip === 'supplier') {
              tipBadge.textContent = 'Tedarikçi';
              tipBadge.style.background = '#fef3c7';
              tipBadge.style.color = '#92400e';
            } else if (tip === 'customer') {
              tipBadge.textContent = 'Müşteri';
              tipBadge.style.background = '#dbeafe';
              tipBadge.style.color = '#1e40af';
            } else {
              tipBadge.textContent = 'Her ikisi';
              tipBadge.style.background = '#f0fdf4';
              tipBadge.style.color = '#166534';
            }
            item.appendChild(tipBadge);
          }
          const bakiye = c.bakiye || 0;
          if (bakiye !== 0) {
            const bak = document.createElement('span');
            bak.className = 'dd-bakiye';
            bak.style.cssText = 'flex-shrink:0;margin-left:8px;white-space:nowrap;';
            if (bakiye > 0) {
              bak.textContent = bakiye.toLocaleString('tr-TR', {minimumFractionDigits:0}) + ' TL ⚠️';
            } else {
              bak.style.color = '#2563eb';
              bak.textContent = Math.abs(bakiye).toLocaleString('tr-TR', {minimumFractionDigits:0}) + ' TL 🔵';
            }
            item.appendChild(bak);
          }
          item.onclick = function(e) {
            e.stopPropagation();
            inp.value = c.ad;
            dropdown.style.display = 'none';
            onSec(c);
          };
          dropdown.appendChild(item);
        });
      }
      dropdown.style.display = 'block';
    }

    inp.addEventListener('input', function() {
      renderDropdown(inp.value);
    });
    inp.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') dropdown.style.display = 'none';
    });
    wrap.appendChild(inp);
    wrap.appendChild(dropdown);
    return { wrap, inp };
  }

  // ── FATURA LİSTESİ & DETAY ───────────────────────────────────────

