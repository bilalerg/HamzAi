// 08-cari.js -- Cari islemleri (detay, duzenle, yeni, listele) + cache yenile

  async function cacheYenile(btn) {
    const orijinal = btn.innerHTML;
    btn.innerHTML = '<span class="btn-icon">⏳</span> Yenileniyor…';
    btn.disabled = true;
    try {
      await carileriGetir(true);  // Cache'i zorla yenile
      btn.innerHTML = '<span class="btn-icon">✅</span> Yenilendi!';
      billablePanelTikla('cari_cache_yenile');
      setTimeout(function() {
        btn.innerHTML = orijinal;
        btn.disabled = false;
      }, 2000);
      panelKapat();
      addMessage('✅ Bakiyeler yenilendi.', 'bot');
    } catch {
      btn.innerHTML = orijinal;
      btn.disabled = false;
      addMessage('❌ Yenileme başarısız.', 'bot');
    }
  }

  // ── PANEL ────────────────────────────────────────────────────────

  async function cariDetayGoster(onMusteri) {
    // Cari adı gelmemişse dropdown ile sor
    if (!onMusteri) {
      showTyping();
      let cariler = [];
      try { cariler = await carileriGetir(false, true); } catch {}
      removeTyping();
      const wrap = document.createElement('div');
      wrap.style.cssText = 'font-size:13px;';
      const baslik = document.createElement('div');
      baslik.style.cssText = 'font-weight:500;margin-bottom:10px;';
      baslik.textContent = '🏢 Cari Detay';
      wrap.appendChild(baslik);
      const { wrap: ddWrap } = cariSecimDropdown(cariler, function(c) {
        cariDetayGoster(c.ad);
      }, 'Cari adı yazın…');
      wrap.appendChild(ddWrap);
      addMessage('', 'bot', wrap);
      return;
    }

    showTyping();
    try {
      const res  = await fetch('/api/cari-bilgi/' + encodeURIComponent(onMusteri));
      const data = await res.json();
      removeTyping();
      if (data.hata) { addMessage('❌ ' + data.hata, 'bot'); return; }

      const wrap = document.createElement('div');
      wrap.style.cssText = 'font-size:13px;min-width:260px;';

      const baslik = document.createElement('div');
      baslik.style.cssText = 'font-weight:600;font-size:14px;margin-bottom:10px;';
      const tipLabel = data.tip === 'supplier' ? ' 🏭 Tedarikçi' : data.tip === 'both' || data.tip === 'customer_supplier' ? ' 🔄 Müşteri & Tedarikçi' : ' 👤 Müşteri';
      baslik.textContent = (data.ad || onMusteri) + tipLabel;
      wrap.appendChild(baslik);

      function satirEkle(icon, label, deger) {
        if (!deger || deger === '-') return;
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:8px;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;';
        row.innerHTML = '<span style="color:var(--muted);min-width:100px;">' + icon + ' ' + label + '</span><span style="font-weight:500;">' + safeHtml(deger) + '</span>';
        wrap.appendChild(row);
      }

      const bakiye = data.bakiye || 0;
      const bakiyeRow = document.createElement('div');
      bakiyeRow.style.cssText = 'padding:8px 12px;border-radius:8px;margin-bottom:10px;font-size:12px;font-weight:600;background:' + (bakiye > 0 ? 'rgba(239,68,68,.1)' : bakiye < 0 ? 'rgba(37,99,235,.1)' : 'var(--bg)') + ';color:' + (bakiye > 0 ? 'var(--alacak)' : bakiye < 0 ? '#2563eb' : 'var(--muted)') + ';';
      bakiyeRow.textContent = 'Bakiye: ' + bakiye.toLocaleString('tr-TR', {minimumFractionDigits:2}) + ' TL' + (bakiye > 0 ? ' ⚠️ Alacak' : bakiye < 0 ? ' 🔵 Borç' : ' ✅ Sıfır');
      wrap.appendChild(bakiyeRow);

      satirEkle('📧', 'E-posta', data.email);
      satirEkle('📞', 'Telefon', data.telefon);
      satirEkle('🏠', 'Adres', data.adres);
      satirEkle('🏛️', 'Vergi Dairesi', data.vergi_dairesi);
      satirEkle('🔢', 'Vergi No', data.vergi_no);

      // ── Düzenle butonu ─────────────────────────────────────────
      const btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;gap:6px;margin-top:12px;border-top:1px solid var(--border);padding-top:10px;';
      const duzenleBtn = document.createElement('button');
      duzenleBtn.className = 'btn-cancel';
      duzenleBtn.style.cssText = 'font-size:12px;flex:1;';
      duzenleBtn.textContent = '✏️ Düzenle';
      duzenleBtn.onclick = function() {
        cariDuzenleFormGoster({
          id:            data.id,
          ad:            data.ad || onMusteri,
          email:         data.email !== '-' ? data.email : '',
          telefon:       data.telefon !== '-' ? data.telefon : '',
          vergi_no:      data.vergi_no !== '-' ? data.vergi_no : '',
          vergi_dairesi: data.vergi_dairesi !== '-' ? data.vergi_dairesi : '',
          adres:         data.adres !== '-' ? data.adres : '',
          tip:           data.tip || 'customer',
        });
      };
      btnRow.appendChild(duzenleBtn);
      wrap.appendChild(btnRow);

      addMessage('', 'bot', wrap);
    } catch {
      removeTyping();
      addMessage('❌ Cari bilgisi alınamadı.', 'bot');
    }
  }

  // ── CARİ DÜZENLE FORMU ─────────────────────────────────────────────
  function cariDuzenleFormGoster(mevcut) {
    const wrap = document.createElement('div');
    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-size:13px;font-weight:500;color:var(--text);margin-bottom:10px;';
    baslik.textContent = '✏️ Cari Düzenle: ' + (mevcut.ad || '');
    wrap.appendChild(baslik);

    const info = document.createElement('div');
    info.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);';
    info.textContent = 'Sadece değiştirmek istediğiniz alanları doldurun. Boş bırakılan alanlar aynı kalır.';
    wrap.appendChild(info);

    const formWrap = document.createElement('div');
    formWrap.className = 'form-wrap';

    // Tip seçimi
    const tipRow = document.createElement('div');
    tipRow.className = 'form-row';
    const tipLabel = document.createElement('span');
    tipLabel.className = 'form-label';
    tipLabel.textContent = 'Cari tipi';
    const tipSel = document.createElement('select');
    tipSel.className = 'form-input';
    tipSel.style.cssText = 'cursor:pointer;';
    [
      { v: 'customer',          t: '👤 Müşteri' },
      { v: 'supplier',          t: '🏭 Tedarikçi' },
      { v: 'customer_supplier', t: '🔄 Her ikisi' },
    ].forEach(function(o) {
      const op = document.createElement('option');
      op.value = o.v; op.textContent = o.t;
      if (mevcut.tip === o.v || (o.v === 'customer_supplier' && mevcut.tip === 'both')) op.selected = true;
      tipSel.appendChild(op);
    });
    tipRow.appendChild(tipLabel);
    tipRow.appendChild(tipSel);
    formWrap.appendChild(tipRow);

    const alanlar = [
      { key: 'firma_adi',     label: 'Firma adı',     deger: mevcut.ad           || '' },
      { key: 'email',         label: 'Email',          deger: mevcut.email        || '' },
      { key: 'telefon',       label: 'Telefon',        deger: mevcut.telefon      || '' },
      { key: 'vergi_no',      label: 'Vergi No',       deger: mevcut.vergi_no     || '' },
      { key: 'vergi_dairesi', label: 'Vergi Dairesi',  deger: mevcut.vergi_dairesi|| '' },
      { key: 'adres',         label: 'Adres',          deger: mevcut.adres        || '' },
    ];
    const inputs = {};
    alanlar.forEach(function(a) {
      const row = document.createElement('div');
      row.className = 'form-row';
      const label = document.createElement('span');
      label.className = 'form-label';
      label.textContent = a.label;
      const inp = document.createElement('input');
      inp.type = 'text';
      inp.className = 'form-input';
      inp.value = a.deger;
      inputs[a.key] = inp;
      row.appendChild(label);
      row.appendChild(inp);
      formWrap.appendChild(row);
    });

    const btnRow = document.createElement('div');
    btnRow.className = 'btn-row';
    const kaydetBtn = document.createElement('button');
    kaydetBtn.className = 'btn-primary';
    kaydetBtn.textContent = 'Güncelle';
    kaydetBtn.onclick = async function() {
      kaydetBtn.disabled = true;
      kaydetBtn.textContent = 'Kaydediliyor…';
      try {
        const r = await fetch('/api/cari-guncelle', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            cari_id:       mevcut.id ? String(mevcut.id) : null,
            cari_adi:      mevcut.ad || null,
            firma_adi:     inputs.firma_adi.value.trim() || null,
            email:         inputs.email.value.trim() || null,
            telefon:       inputs.telefon.value.trim() || null,
            vergi_no:      inputs.vergi_no.value.trim() || null,
            vergi_dairesi: inputs.vergi_dairesi.value.trim() || null,
            adres:         inputs.adres.value.trim() || null,
            tip:           tipSel.value,
          }),
        });
        const d = await r.json();
        if (d && d.basarili) {
          addMessage('✅ Cari güncellendi.', 'bot');
          // Güncel detayı tekrar göster
          setTimeout(function() { cariDetayGoster(inputs.firma_adi.value.trim() || mevcut.ad); }, 300);
        } else {
          kaydetBtn.disabled = false;
          kaydetBtn.textContent = 'Güncelle';
          addMessage('❌ Güncellenemedi: ' + (d.hata || 'bilinmeyen hata'), 'bot');
        }
      } catch (e) {
        kaydetBtn.disabled = false;
        kaydetBtn.textContent = 'Güncelle';
        addMessage('❌ Güncellenemedi.', 'bot');
      }
    };
    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel';
    iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('İşlem iptal edildi.', 'bot'); };
    btnRow.appendChild(kaydetBtn);
    btnRow.appendChild(iptalBtn);
    formWrap.appendChild(btnRow);
    wrap.appendChild(formWrap);
    addMessage('', 'bot', wrap);
  }

  // ── YENİ CARİ FORMU ──────────────────────────────────────────────

  function cariFormGoster() {
    billablePanelTikla('yeni_cari');
    const wrap = document.createElement('div');
    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-size:13px;font-weight:500;color:var(--text);margin-bottom:10px;';
    baslik.textContent = '➕ Yeni Cari Oluştur';
    wrap.appendChild(baslik);
    const cariInfo = document.createElement('div');
    cariInfo.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);';
    cariInfo.textContent = 'Yeni cari bilgilerini doldurun. Yıldızlı (*) alanlar zorunludur.';
    wrap.appendChild(cariInfo);

    const formWrap = document.createElement('div');
    formWrap.className = 'form-wrap';

    // ── Tip seçimi (Müşteri / Tedarikçi / Her ikisi) ──────────────
    const tipRow = document.createElement('div');
    tipRow.className = 'form-row';
    const tipLabel = document.createElement('span');
    tipLabel.className = 'form-label';
    tipLabel.textContent = 'Cari tipi *';
    const tipSel = document.createElement('select');
    tipSel.className = 'form-input required';
    tipSel.style.cssText = 'cursor:pointer;';
    [
      { v: 'customer',          t: '👤 Müşteri (satış faturası kesilir)' },
      { v: 'supplier',          t: '🏭 Tedarikçi (alış faturası kesilir)' },
      { v: 'customer_supplier', t: '🔄 Her ikisi' },
    ].forEach(function(o) {
      const op = document.createElement('option');
      op.value = o.v; op.textContent = o.t;
      tipSel.appendChild(op);
    });
    tipRow.appendChild(tipLabel);
    tipRow.appendChild(tipSel);
    formWrap.appendChild(tipRow);

    const alanlar = [
      { key: 'firma_adi',     label: 'Firma adı *',    placeholder: 'Firma adı',         required: true  },
      { key: 'email',         label: 'Email',           placeholder: 'firma@mail.com',     required: false },
      { key: 'telefon',       label: 'Telefon',         placeholder: '05XX XXX XX XX',     required: false },
      { key: 'vergi_no',      label: 'Vergi No',        placeholder: '1234567890',         required: false },
      { key: 'vergi_dairesi', label: 'Vergi Dairesi',   placeholder: 'Vergi dairesi adı',  required: false },
      { key: 'adres',         label: 'Adres',           placeholder: 'Açık adres',         required: false }
    ];
    const inputs = {};
    alanlar.forEach(function(alan) {
      const row = document.createElement('div');
      row.className = 'form-row';
      const label = document.createElement('span');
      label.className = 'form-label';
      label.textContent = alan.label;
      const inp = document.createElement('input');
      inp.type = 'text';
      inp.className = 'form-input' + (alan.required ? ' required' : '');
      inp.placeholder = alan.placeholder;
      inputs[alan.key] = inp;
      row.appendChild(label);
      row.appendChild(inp);
      formWrap.appendChild(row);
    });

    const btnRow = document.createElement('div');
    btnRow.className = 'btn-row';
    const kaydetBtn = document.createElement('button');
    kaydetBtn.className = 'btn-primary';
    kaydetBtn.textContent = 'Cari Oluştur';
    kaydetBtn.onclick = async function() {
      const firmaAdi = inputs.firma_adi.value.trim();
      if (!firmaAdi) { inputs.firma_adi.style.borderColor = '#e53e3e'; addMessage('❌ Firma adı zorunludur.', 'bot'); return; }
      kaydetBtn.disabled = true;
      kaydetBtn.textContent = 'Oluşturuluyor…';
      addMessage('Yeni cari: ' + firmaAdi, 'user');
      try {
        const res = await fetch('/api/cari-olustur', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            firma_adi: firmaAdi,
            tip: tipSel.value,
            email: inputs.email.value.trim() || null,
            telefon: inputs.telefon.value.trim() || null,
            vergi_no: inputs.vergi_no.value.trim() || null,
            vergi_dairesi: inputs.vergi_dairesi.value.trim() || null,
            adres: inputs.adres.value.trim() || null
          })
        });
        const data = await res.json();
        addMessage(data.cevap, 'bot');
      } catch {
        addMessage('❌ Cari oluşturulamadı, tekrar deneyin.', 'bot');
        kaydetBtn.disabled = false;
        kaydetBtn.textContent = 'Cari Oluştur';
      }
    };
    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel';
    iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ Cari oluşturma iptal edildi.', 'bot'); };
    btnRow.appendChild(kaydetBtn);
    btnRow.appendChild(iptalBtn);
    formWrap.appendChild(btnRow);
    wrap.appendChild(formWrap);

    addMessage('', 'bot', wrap);
    sohbetPanelKaydet('yeni_cari', {});
    setTimeout(function() { inputs.firma_adi.focus(); }, 100);
  }

  // ── İRSALİYE SEÇ → FİYAT AKIŞI ──────────────────────────────────


  async function carileriGetir(forceRefresh) {
    const simdi = Date.now();
    if (!forceRefresh && _cariCache && (simdi - _cariCacheZamani) < CARI_CACHE_SURE) {
      return _cariCache;
    }
    try {
      const res = await fetch('/api/cariler' + (forceRefresh ? '?refresh=1' : ''));
      const data = await res.json();
      _cariCache = data.cariler || [];
      _cariCacheZamani = Date.now();
      return _cariCache;
    } catch {
      return _cariCache || [];
    }
  }

  // Sayfa açılınca arka planda yükle — kullanıcı beklemez
  (async function() {
    try {
      await carileriGetir(false, true);
      console.log('✅ Cari cache hazır: ' + (_cariCache ? _cariCache.length : 0) + ' cari');
    } catch {}
  })();

  // ── GLOBAL DROPDOWN KAPAMA — TEK LISTENER (memory leak yok) ──────
  // Tüm dropdown'lar için tek bir click listener — her dropdown'a ayrı ayrı eklenmez.
  document.addEventListener('click', function(e) {
    document.querySelectorAll('.cari-dropdown').forEach(function(dd) {
      if (dd.parentElement && !dd.parentElement.contains(e.target)) {
        dd.style.display = 'none';
      }
    });
  });

  (async function() {
    await ensureSessionReady();
  })();

