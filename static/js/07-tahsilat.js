// 07-tahsilat.js -- Tahsilat/odeme/transfer formlari

  async function odemeFormGoster(mod) {
    // mod yoksa kullanıcıya akış seç
    if (!mod) {
      const wrap = document.createElement('div');
      const baslik = document.createElement('div');
      baslik.style.cssText = 'font-size:13px;font-weight:500;color:var(--text);margin-bottom:8px;';
      baslik.textContent = '💸 Ödeme Ekle';
      wrap.appendChild(baslik);
      const info = document.createElement('div');
      info.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);';
      info.textContent = 'Hangi şekilde ödeme yapacaksınız?';
      wrap.appendChild(info);

      const btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;';

      const faturaBtn = document.createElement('button');
      faturaBtn.style.cssText = 'flex:1;min-width:140px;padding:10px 14px;border-radius:9px;font-size:13px;font-weight:600;cursor:pointer;background:var(--accent,#bf5b2a);color:#fff;border:none;';
      faturaBtn.textContent = '📄 Açık faturaya ödeme';
      faturaBtn.onclick = function() {
        Array.from(btnRow.querySelectorAll('button')).forEach(function(b){ b.disabled=true; });
        odemeFormGoster('fatura');
      };

      const cariBtn = document.createElement('button');
      cariBtn.style.cssText = 'flex:1;min-width:140px;padding:10px 14px;border-radius:9px;font-size:13px;font-weight:600;cursor:pointer;background:transparent;color:var(--text);border:1.5px solid var(--border);';
      cariBtn.textContent = '🏢 Cari hesabına direkt';
      cariBtn.onclick = function() {
        Array.from(btnRow.querySelectorAll('button')).forEach(function(b){ b.disabled=true; });
        odemeFormGoster('cari');
      };

      btnRow.appendChild(faturaBtn);
      btnRow.appendChild(cariBtn);
      wrap.appendChild(btnRow);

      const iptalBtn = document.createElement('button');
      iptalBtn.className = 'btn-cancel';
      iptalBtn.style.cssText = 'margin-top:8px;font-size:12px;';
      iptalBtn.textContent = '✕ İptal';
      iptalBtn.onclick = function() {
        Array.from(btnRow.querySelectorAll('button')).forEach(function(b){ b.disabled=true; });
        iptalBtn.disabled = true;
        addMessage('❌ İptal edildi.', 'bot');
      };
      wrap.appendChild(iptalBtn);
      addMessage('', 'bot', wrap);
      return;
    }

    // ── CARİ HESABA DİREKT ÖDEME MODU ─────────────────────────────
    if (mod === 'cari') {
      await odemeCariBazliFormGoster();
      return;
    }

    // ── FATURA BAZLI ÖDEME (mevcut akış) ──────────────────────────
    billablePanelTikla('odeme_ekle');
    showTyping();
    let hesaplar = [], faturalar = [];
    try {
      const [hr, fr] = await Promise.all([
        fetch('/api/hesaplar'),
        fetch('/api/bekleyen-alis-faturalari')
      ]);
      hesaplar = (await hr.json()).hesaplar || [];
      faturalar = (await fr.json()).faturalar || [];
    } catch {}
    removeTyping();

    const wrap = document.createElement('div');
    wrap.style.cssText = 'font-size:13px;';

    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-weight:500;margin-bottom:10px;';
    baslik.textContent = '💸 Ödeme Ekle (Alış Faturası)';
    wrap.appendChild(baslik);

    function satir(label, el) {
      const d = document.createElement('div');
      d.style.cssText = 'margin-bottom:8px;';
      const l = document.createElement('div');
      l.style.cssText = 'font-size:11px;color:var(--muted);margin-bottom:3px;';
      l.textContent = label;
      d.appendChild(l);
      d.appendChild(el);
      return d;
    }

    // Fatura seçimi — ya dropdown ya manuel ID
    let seciliFaturaId = '';
    let seciliKalan = 0;
    const faturaSel = document.createElement('select');
    faturaSel.className = 'fiyat-input';
    const bos0 = document.createElement('option'); bos0.value = ''; bos0.textContent = faturalar.length ? 'Fatura seçin…' : 'Ödenmemiş fatura yok — ID manuel girin';
    faturaSel.appendChild(bos0);
    faturalar.forEach(function(f) {
      const o = document.createElement('option'); o.value = f.id;
      const kalan = f.kalan.toLocaleString('tr-TR', {minimumFractionDigits:2});
      o.textContent = `${f.cari} · ${f.tarih} · ${kalan} ₺`;
      faturaSel.appendChild(o);
    });
    wrap.appendChild(satir('Fatura (ödenmemiş)', faturaSel));

    const manuelInp = document.createElement('input');
    manuelInp.type = 'text'; manuelInp.className = 'fiyat-input';
    manuelInp.placeholder = MUHASEBE.saglayici === 'yerel' ? 'veya fatura ID girin' : 'veya fatura ID girin (Paraşüt URL\'den)';
    wrap.appendChild(satir('', manuelInp));

    const tutarInp = document.createElement('input');
    tutarInp.type = 'text'; tutarInp.className = 'fiyat-input'; tutarInp.placeholder = 'Örn: 15000';
    wrap.appendChild(satir('Tutar (₺)', tutarInp));

    // Fatura seçince kalan tutarı otomatik doldur
    faturaSel.onchange = function() {
      const f = faturalar.find(function(x) { return x.id === faturaSel.value; });
      if (f) { tutarInp.value = String(f.kalan); manuelInp.value = ''; }
    };

    const kasaSel = document.createElement('select');
    kasaSel.className = 'fiyat-input';
    const bosH = document.createElement('option'); bosH.value = ''; bosH.textContent = 'Kasa/Banka (opsiyonel)';
    kasaSel.appendChild(bosH);
    hesaplar.forEach(function(h) {
      const o = document.createElement('option'); o.value = h.id; o.textContent = h.name;
      kasaSel.appendChild(o);
    });
    wrap.appendChild(satir('Hesap', kasaSel));

    const tarihInp = document.createElement('input');
    tarihInp.type = 'date'; tarihInp.className = 'fiyat-input';
    tarihInp.style.maxWidth = '160px';
    tarihInp.value = new Date().toISOString().split('T')[0];
    wrap.appendChild(satir('Tarih', tarihInp));

    const notlarInp = document.createElement('input');
    notlarInp.type = 'text'; notlarInp.className = 'fiyat-input'; notlarInp.placeholder = 'Açıklama (opsiyonel)';
    wrap.appendChild(satir('Notlar', notlarInp));

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;margin-top:12px;';

    const kaydetBtn = document.createElement('button');
    kaydetBtn.className = 'btn-primary'; kaydetBtn.textContent = 'Kaydet';

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel'; iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ Ödeme iptal edildi.', 'bot'); };

    kaydetBtn.onclick = async function() {
      const fatura_id = faturaSel.value || manuelInp.value.trim();
      const tutar = parseFiyat(tutarInp.value);
      if (!fatura_id) { faturaSel.style.borderColor = '#e53e3e'; manuelInp.style.borderColor = '#e53e3e'; return; }
      if (!tutar || tutar <= 0) { tutarInp.style.borderColor = '#e53e3e'; return; }
      kaydetBtn.disabled = true; iptalBtn.disabled = true;
      kaydetBtn.textContent = 'Kaydediliyor…';
      try {
        const body = { fatura_id, tutar, tarih: tarihInp.value };
        if (kasaSel.value) body.kasa_id = kasaSel.value;
        if (notlarInp.value.trim()) body.notlar = notlarInp.value.trim();
        const res = await fetch('/api/odeme-ekle', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (localStorage.getItem('token') || '') },
          body: JSON.stringify(body)
        });
        const d = await res.json();
        if (d.basarili) {
          const label = faturalar.find(function(x) { return x.id === fatura_id; })?.cari || fatura_id;
          addMessage(`✅ Ödeme kaydedildi. (${label} · ${tutar.toLocaleString('tr-TR')} ₺)`, 'bot');
        } else {
          addMessage(`❌ Ödeme kaydedilemedi: ${d.hata || d.detail || 'Bilinmeyen hata'}`, 'bot');
          kaydetBtn.disabled = false; iptalBtn.disabled = false;
          kaydetBtn.textContent = 'Kaydet';
        }
      } catch { addMessage('❌ Ödeme kaydedilemedi.', 'bot'); }
    };

    btnRow.appendChild(kaydetBtn);
    btnRow.appendChild(iptalBtn);
    wrap.appendChild(btnRow);
    addMessage('', 'bot', wrap);
    sohbetPanelKaydet('odeme_ekle', {});
  }

  // ── CARİ HESABA DİREKT ÖDEME (fatura bağı yok) ────────────────────
  async function odemeCariBazliFormGoster() {
    billablePanelTikla('odeme_ekle', { mod: 'cari' });
    showTyping();
    let hesaplar = [], cariler = [];
    try {
      // Tüm carileri al — bakiye yönüne göre filtreleyeceğiz (Paraşüt UI mantığı)
      const [hr, cr] = await Promise.all([
        fetch('/api/hesaplar'),
        fetch('/api/cariler')
      ]);
      hesaplar = (await hr.json()).hesaplar || [];
      cariler  = (await cr.json()).cariler || [];
    } catch {}
    removeTyping();

    // Paraşüt UI mantığı: ödeme sadece bakiye <= 0 cariler için mantıklı
    //   - Negatif bakiye  → biz cariye borçluyuz → ödeme yapmalıyız ✓
    //   - Sıfır bakiye    → avans verme senaryosu ✓
    //   - Pozitif bakiye  → cari bize borçlu → ödeme yapmak mantıksız
    cariler = cariler.filter(function(c) { return (c.bakiye || 0) <= 0; });
    // Borçlular önce (negatif bakiyeliler), sonra sıfırlar
    cariler.sort(function(a, b) { return (a.bakiye || 0) - (b.bakiye || 0); });

    const wrap = document.createElement('div');
    wrap.style.cssText = 'font-size:13px;';

    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-weight:500;margin-bottom:10px;';
    baslik.textContent = '🏢 Cari Hesabına Ödeme (Tedarikçi)';
    wrap.appendChild(baslik);

    const info = document.createElement('div');
    info.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);';
    info.textContent = 'Fatura olmadan tedarikçi cari hesabına direkt ödeme. (Avans / borç kapatma)';
    wrap.appendChild(info);

    function satir(label, el) {
      const d = document.createElement('div');
      d.style.cssText = 'margin-bottom:8px;';
      const l = document.createElement('div');
      l.style.cssText = 'font-size:11px;color:var(--muted);margin-bottom:3px;';
      l.textContent = label;
      d.appendChild(l);
      d.appendChild(el);
      return d;
    }

    const { wrap: ddWrap } = cariSecimDropdown(cariler, function(c) {
      ddWrap._secilen = c;
    }, 'Tedarikçi adı yazın...');
    wrap.appendChild(satir('Tedarikçi *', ddWrap));

    const tutarInp = document.createElement('input');
    tutarInp.type = 'text'; tutarInp.className = 'fiyat-input';
    tutarInp.placeholder = 'Örn: 15000';
    wrap.appendChild(satir('Tutar (₺) *', tutarInp));

    const kasaSel = document.createElement('select');
    kasaSel.className = 'fiyat-input';
    const bosH = document.createElement('option'); bosH.value = ''; bosH.textContent = 'Kasa/Banka (opsiyonel)';
    kasaSel.appendChild(bosH);
    hesaplar.forEach(function(h) {
      const o = document.createElement('option'); o.value = h.id; o.textContent = h.name;
      kasaSel.appendChild(o);
    });
    wrap.appendChild(satir('Hesap', kasaSel));

    const tarihInp = document.createElement('input');
    tarihInp.type = 'date'; tarihInp.className = 'fiyat-input';
    tarihInp.style.maxWidth = '160px';
    tarihInp.value = new Date().toISOString().split('T')[0];
    wrap.appendChild(satir('Tarih', tarihInp));

    const notlarInp = document.createElement('input');
    notlarInp.type = 'text'; notlarInp.className = 'fiyat-input';
    notlarInp.placeholder = 'Açıklama (opsiyonel)';
    wrap.appendChild(satir('Açıklama', notlarInp));

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;margin-top:12px;';

    const kaydetBtn = document.createElement('button');
    kaydetBtn.className = 'btn-primary'; kaydetBtn.textContent = 'Kaydet';

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel'; iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ Ödeme iptal edildi.', 'bot'); };

    kaydetBtn.onclick = async function() {
      const inp = ddWrap.querySelector('input');
      const cariAdi = inp ? inp.value.trim() : '';
      const tutar = parseFiyat(tutarInp.value);
      if (!cariAdi) { inp.style.borderColor = '#e53e3e'; return; }
      if (!tutar || tutar <= 0) { tutarInp.style.borderColor = '#e53e3e'; return; }
      kaydetBtn.disabled = true; iptalBtn.disabled = true;
      kaydetBtn.textContent = 'Kaydediliyor…';
      try {
        const body = { cari_adi: cariAdi, tutar: tutar, tarih: tarihInp.value };
        if (kasaSel.value) body.kasa_id = kasaSel.value;
        if (notlarInp.value.trim()) body.aciklama = notlarInp.value.trim();
        const res = await fetch('/api/cari-odeme', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const d = await res.json();
        if (d.basarili) {
          addMessage(d.cevap || ('✅ Ödeme kaydedildi: ' + cariAdi + ' — ' + tutar.toLocaleString('tr-TR') + ' ₺'), 'bot');
        } else {
          addMessage('❌ Kaydedilemedi: ' + (d.hata || 'Bilinmeyen hata'), 'bot');
          kaydetBtn.disabled = false; iptalBtn.disabled = false;
          kaydetBtn.textContent = 'Kaydet';
        }
      } catch (e) {
        addMessage('❌ Kaydedilemedi.', 'bot');
        kaydetBtn.disabled = false; iptalBtn.disabled = false;
        kaydetBtn.textContent = 'Kaydet';
      }
    };

    btnRow.appendChild(kaydetBtn);
    btnRow.appendChild(iptalBtn);
    wrap.appendChild(btnRow);
    addMessage('', 'bot', wrap);
    setTimeout(function() {
      const inp = ddWrap.querySelector('input');
      if (inp) inp.focus();
    }, 100);
  }

  // ── PARA TRANSFERİ ────────────────────────────────────────────────

  async function paraTransferiFormGoster() {
    billablePanelTikla('para_transferi');
    showTyping();
    let hesaplar = [];
    try {
      const res = await fetch('/api/hesaplar');
      const d = await res.json();
      hesaplar = d.hesaplar || [];
    } catch {}
    removeTyping();

    if (hesaplar.length < 2) {
      addMessage('❌ Transfer için en az 2 hesap gerekli.', 'bot');
      return;
    }

    const wrap = document.createElement('div');
    wrap.style.cssText = 'font-size:13px;';

    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-weight:500;margin-bottom:10px;';
    baslik.textContent = '🔄 Para Transferi';
    wrap.appendChild(baslik);

    function satir(label, el) {
      const d = document.createElement('div');
      d.style.cssText = 'margin-bottom:8px;';
      const l = document.createElement('div');
      l.style.cssText = 'font-size:11px;color:var(--muted);margin-bottom:3px;';
      l.textContent = label;
      d.appendChild(l);
      d.appendChild(el);
      return d;
    }

    function hesapSelect() {
      const sel = document.createElement('select');
      sel.className = 'fiyat-input';
      const bos = document.createElement('option'); bos.value = ''; bos.textContent = 'Hesap seçin';
      sel.appendChild(bos);
      hesaplar.forEach(function(h) {
        const o = document.createElement('option'); o.value = h.id; o.textContent = h.name;
        sel.appendChild(o);
      });
      return sel;
    }

    const kaynakSel = hesapSelect();
    wrap.appendChild(satir('Kaynak Hesap', kaynakSel));

    const hedefSel = hesapSelect();
    wrap.appendChild(satir('Hedef Hesap', hedefSel));

    const tutarInp = document.createElement('input');
    tutarInp.type = 'text'; tutarInp.className = 'fiyat-input'; tutarInp.placeholder = 'Örn: 5000';
    wrap.appendChild(satir('Tutar (₺)', tutarInp));

    const tarihInp = document.createElement('input');
    tarihInp.type = 'date'; tarihInp.className = 'fiyat-input';
    tarihInp.style.maxWidth = '160px';
    tarihInp.value = new Date().toISOString().split('T')[0];
    wrap.appendChild(satir('Tarih', tarihInp));

    const aciklamaInp = document.createElement('input');
    aciklamaInp.type = 'text'; aciklamaInp.className = 'fiyat-input'; aciklamaInp.placeholder = 'Açıklama (opsiyonel)';
    wrap.appendChild(satir('Açıklama', aciklamaInp));

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;margin-top:12px;';

    const kaydetBtn = document.createElement('button');
    kaydetBtn.className = 'btn-primary'; kaydetBtn.textContent = 'Transfer Et';

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel'; iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ Transfer iptal edildi.', 'bot'); };

    kaydetBtn.onclick = async function() {
      const kaynak_id = kaynakSel.value;
      const hedef_id = hedefSel.value;
      const tutar = parseFiyat(tutarInp.value);
      if (!kaynak_id) { kaynakSel.style.borderColor = '#e53e3e'; return; }
      if (!hedef_id) { hedefSel.style.borderColor = '#e53e3e'; return; }
      if (kaynak_id === hedef_id) { addMessage('❌ Kaynak ve hedef hesap aynı olamaz.', 'bot'); return; }
      if (!tutar || tutar <= 0) { tutarInp.style.borderColor = '#e53e3e'; return; }
      kaydetBtn.disabled = true; iptalBtn.disabled = true;
      kaydetBtn.textContent = 'Transfering…';
      try {
        const body = { kaynak_id, hedef_id, tutar, tarih: tarihInp.value };
        if (aciklamaInp.value.trim()) body.aciklama = aciklamaInp.value.trim();
        const res = await fetch('/api/para-transferi', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (localStorage.getItem('token') || '') },
          body: JSON.stringify(body)
        });
        const d = await res.json();
        if (d.basarili) {
          const kaynakAd = hesaplar.find(function(h) { return h.id === kaynak_id; })?.name || kaynak_id;
          const hedefAd = hesaplar.find(function(h) { return h.id === hedef_id; })?.name || hedef_id;
          addMessage(`✅ Transfer tamamlandı: ${kaynakAd} → ${hedefAd} · ${tutar.toLocaleString('tr-TR')} ₺`, 'bot');
        } else {
          addMessage(`❌ Transfer başarısız: ${d.hata || d.detail || 'Bilinmeyen hata'}`, 'bot');
        }
      } catch { addMessage('❌ Transfer yapılamadı.', 'bot'); }
    };

    btnRow.appendChild(kaydetBtn);
    btnRow.appendChild(iptalBtn);
    wrap.appendChild(btnRow);
    addMessage('', 'bot', wrap);
    sohbetPanelKaydet('para_transferi', {});
  }

  // ── ALIŞ FATURASI KES (basit — tek tutar) ─────────────────────────


  async function tahsilatFormGoster(onMusteri, onTutar) {
    billablePanelTikla('tahsilat_ekle', onMusteri ? { musteri: onMusteri } : {});
    showTyping();
    let cariler = [];
    try {
      cariler = await carileriGetir(false, true);
    } catch {}
    removeTyping();

    // Paraşüt UI mantığı: tahsilat sadece bakiye >= 0 cariler için mantıklı
    // (negatif bakiye = biz borçluyuz → ödeme yapmamız lazım, tahsilat
    // almak borcu artırır). Tip fark etmez — Paraşüt UI da tipe bakmıyor.
    const uygunlar = cariler.filter(function(c) {
      return (c.bakiye || 0) >= 0;
    });
    // Alacaklıları (pozitif bakiyeliler) önce göster
    const alacaklilar = uygunlar.filter(function(c) { return c.bakiye > 0; });
    const sifirlar    = uygunlar.filter(function(c) { return (c.bakiye || 0) === 0; });
    const siralanmis  = alacaklilar.concat(sifirlar);

    const wrap = document.createElement('div');
    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-size:13px;font-weight:500;color:var(--text);margin-bottom:10px;';
    baslik.textContent = '💵 Tahsilat Ekle';
    wrap.appendChild(baslik);
    const tahsilatInfo = document.createElement('div');
    tahsilatInfo.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);';
    tahsilatInfo.textContent = 'Tahsilat eklemek istediğiniz müşteriyi aşağıdan seçin.';
    wrap.appendChild(tahsilatInfo);

    const { wrap: ddWrap } = cariSecimDropdown(siralanmis, function(c) {
      ddWrap.querySelector('input').disabled = true;
      tahsilatTutarGir(c.ad, wrap, onTutar);
    }, 'Cari adı yazın...');
    wrap.appendChild(ddWrap);

    addMessage('', 'bot', wrap);
    sohbetPanelKaydet('tahsilat_ekle', onMusteri ? { musteri: onMusteri } : {});

    // Params'tan müşteri adı geldiyse arama kutusuna yaz — kullanıcı görsün
    if (onMusteri) {
      setTimeout(function() {
        const inp = ddWrap.querySelector('input');
        if (inp) { inp.value = onMusteri; inp.dispatchEvent(new Event('input')); }
      }, 150);
    } else {
      setTimeout(function() { ddWrap.querySelector('input').focus(); }, 100);
    }
  }

  function tahsilatTutarGir(cariAdi, wrapParent, onTutar) {
    const tutarWrap = document.createElement('div');
    tutarWrap.style.cssText = 'margin-top:12px;border-top:1px solid var(--border);padding-top:12px;';

    const label = document.createElement('div');
    label.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:8px;';
    label.textContent = cariAdi + ' — Tahsilat tutarı:';
    tutarWrap.appendChild(label);

    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;';

    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'fiyat-input';
    inp.placeholder = 'Örn: 100000';
    // Params'tan tutar geldiyse önceden doldur
    if (onTutar) inp.value = String(onTutar);

    const tarihInp = document.createElement('input');
    tarihInp.type = 'date';
    tarihInp.className = 'fiyat-input';
    tarihInp.style.maxWidth = '150px';
    // Bugünü default olarak set et
    tarihInp.value = new Date().toISOString().split('T')[0];

    const btn = document.createElement('button');
    btn.className = 'btn-primary';
    btn.textContent = 'Kaydet';

    // ── İPTAL BUTONU ──
    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel';
    iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() {
      inp.disabled = true;
      tarihInp.disabled = true;
      btn.disabled = true;
      iptalBtn.disabled = true;
      addMessage('❌ Tahsilat iptal edildi.', 'bot');
    };

    const tahsilatGonder = async function(onaylandi) {
      const tutar = parseFiyat(inp.value);
      if (!tutar || tutar <= 0) {
        inp.style.borderColor = '#e53e3e';
        inp.placeholder = 'Geçersiz! Örn: 4.566 veya 4566,50';
        return;
      }
      inp.style.borderColor = '';
      btn.disabled = true;
      iptalBtn.disabled = true;
      btn.textContent = 'Kaydediliyor…';
      try {
        const res = await fetch('/api/tahsilat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            cari_adi: cariAdi,
            tutar: tutar,
            tarih: tarihInp.value.trim() || '',
            onaylandi: onaylandi || false
          })
        });
        const data = await res.json();

        // Backend yanlış tip uyarısı döndü (tedarikçiye tahsilat yapılamaz)
        if (!data.basarili && data.yanlis_tip) {
          btn.disabled = false; iptalBtn.disabled = false;
          btn.textContent = 'Kaydet';
          addMessage(data.cevap, 'bot');
          if (data.onerilen_akis === 'odeme') {
            // Kullanıcıya onay sor → onaylarsa ödeme formuna geç
            setTimeout(function() {
              if (confirm(cariAdi + ' bir tedarikçi.\n\nÖdeme ekranına geçilsin mi?')) {
                odemeFormGoster();
              }
            }, 100);
          }
          return;
        }

        // Backend fazla tutar uyarisi dondu
        if (!data.basarili && data.fazla_tutar) {
          btn.disabled = false;
          btn.textContent = 'Kaydet';
          tahsilatOnayDialogu(
            data.guncel_bakiye,
            data.girilen_tutar,
            cariAdi,
            function() { tahsilatGonder(true); }
          );
          return;
        }

        addMessage(cariAdi + ' — ' + tutar.toLocaleString('tr-TR') + ' TL tahsilat', 'user');
        addMessage(data.cevap, 'bot');
      } catch {
        btn.disabled = false;
        btn.textContent = 'Kaydet';
        addMessage('❌ Tahsilat eklenemedi, tekrar deneyin.', 'bot');
      }
    };
    const gonder = function() { tahsilatGonder(false); };

    btn.onclick = gonder;
    inp.onkeydown = function(e) { if (e.key === 'Enter') gonder(); };

    row.appendChild(inp);
    row.appendChild(tarihInp);
    row.appendChild(btn);
    row.appendChild(iptalBtn);
    tutarWrap.appendChild(row);

    const hint = document.createElement('div');
    hint.style.cssText = 'font-size:10.5px;color:var(--muted2);margin-top:5px;';
    hint.textContent = 'Tarih seçilmezse bugün kullanılır.';
    tutarWrap.appendChild(hint);

    wrapParent.appendChild(tutarWrap);
    setTimeout(function() { inp.focus(); }, 100);
  }

  function tahsilatManuelGir(wrap) {
    const formWrap = document.createElement('div');
    formWrap.className = 'form-wrap';
    const alanlar = [
      { key: 'cari_adi', label: 'Cari adı *', placeholder: 'Firma adı', required: true },
      { key: 'tutar',    label: 'Tutar (TL) *', placeholder: '100000', required: true },
      { key: 'tarih',    label: 'Tarih',        placeholder: '', required: false, type: 'date' }
    ];
    const inputs = {};
    alanlar.forEach(function(alan) {
      const row = document.createElement('div');
      row.className = 'form-row';
      const label = document.createElement('span');
      label.className = 'form-label';
      label.textContent = alan.label;
      const inp = document.createElement('input');
      inp.type = alan.type || 'text';
      inp.className = 'form-input' + (alan.required ? ' required' : '');
      inp.placeholder = alan.placeholder;
      if (alan.type === 'date') {
        inp.value = new Date().toISOString().split('T')[0];
      }
      inputs[alan.key] = inp;
      row.appendChild(label);
      row.appendChild(inp);
      formWrap.appendChild(row);
    });
    const btnRow = document.createElement('div');
    btnRow.className = 'btn-row';
    const kaydetBtn = document.createElement('button');
    kaydetBtn.className = 'btn-primary';
    kaydetBtn.textContent = 'Tahsilatı Kaydet';
    kaydetBtn.onclick = async function() {
      const cariAdi = inputs.cari_adi.value.trim();
      const tutar = parseFiyat(inputs.tutar.value || '');
      if (!cariAdi || !tutar || tutar <= 0) { addMessage('❌ Cari adı ve geçerli tutar giriniz.', 'bot'); return; }
      kaydetBtn.disabled = true;
      kaydetBtn.textContent = 'Kaydediliyor…';
      addMessage(cariAdi + ' — ' + tutar.toLocaleString('tr-TR') + ' TL tahsilat', 'user');
      try {
        const res = await fetch('/api/tahsilat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cari_adi: cariAdi, tutar: tutar, tarih: inputs.tarih.value.trim() })
        });
        const data = await res.json();
        addMessage(data.cevap, 'bot');
      } catch {
        addMessage('❌ Tahsilat eklenemedi, tekrar deneyin.', 'bot');
      }
    };
    const iptalBtn2 = document.createElement('button');
    iptalBtn2.className = 'btn-cancel';
    iptalBtn2.textContent = '✕ İptal';
    iptalBtn2.onclick = function() { addMessage('❌ Tahsilat iptal edildi.', 'bot'); };
    btnRow.appendChild(kaydetBtn);
    btnRow.appendChild(iptalBtn2);
    formWrap.appendChild(btnRow);
    wrap.appendChild(formWrap);
  }

  // ── CARİ SEÇİM DROPDOWN ──────────────────────────────────────────

