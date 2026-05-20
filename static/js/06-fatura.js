// 06-fatura.js -- Tum fatura/irsaliye akislari (alis, iptal, guncelle, e-fatura, liste, detay, toplu, fiyat akisi)

  async function alisFaturasiFormGoster(onMusteri) {
    billablePanelTikla('alis_faturasi_kes', onMusteri ? { musteri: onMusteri } : {});
    showTyping();
    let cariler = [];
    try {
      const tumCariler = await carileriGetir(false, true);
      // Sadece tedarikçiler (supplier / both / customer_supplier) — Paraşüt'teki ayrım
      cariler = tumCariler.filter(function(c) {
        const t = c.tip || '';
        return t === 'supplier' || t === 'both' || t === 'customer_supplier';
      });
    } catch {}
    removeTyping();

    const wrap = document.createElement('div');
    wrap.style.cssText = 'font-size:13px;';

    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-weight:500;margin-bottom:10px;';
    baslik.textContent = '📥 Alış Faturası Kes';
    wrap.appendChild(baslik);

    const bilgi = document.createElement('div');
    bilgi.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);';
    bilgi.textContent = 'Tedarikçi/hurda satıcısı adı seçin, tutarı girin. Basit tek tutarlı fatura kesilir.';
    wrap.appendChild(bilgi);

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

    let secilenCari = '';
    const { wrap: ddWrap, inp: ddInp } = cariSecimDropdown(cariler, function(c) {
      secilenCari = c.ad;
    }, 'Cari adı yazın…');
    if (onMusteri) {
      ddInp.value = onMusteri;
      const m = cariler.find(function(c) { return c.ad.toLowerCase().includes(String(onMusteri).toLowerCase()); });
      if (m) secilenCari = m.ad;
    }
    wrap.appendChild(satir('Tedarikçi / Cari *', ddWrap));

    const tutarInp = document.createElement('input');
    tutarInp.type = 'text'; tutarInp.className = 'fiyat-input'; tutarInp.placeholder = 'Örn: 15000';
    wrap.appendChild(satir('Tutar (₺) *', tutarInp));

    const aciklamaInp = document.createElement('input');
    aciklamaInp.type = 'text'; aciklamaInp.className = 'fiyat-input'; aciklamaInp.placeholder = 'Açıklama (opsiyonel) — örn: Hurda alım 37LK081';
    wrap.appendChild(satir('Açıklama', aciklamaInp));

    const tarihInp = document.createElement('input');
    tarihInp.type = 'date'; tarihInp.className = 'fiyat-input';
    tarihInp.style.maxWidth = '160px';
    tarihInp.value = new Date().toISOString().split('T')[0];
    wrap.appendChild(satir('Fatura Tarihi', tarihInp));

    const vadeInp = document.createElement('input');
    vadeInp.type = 'date'; vadeInp.className = 'fiyat-input';
    vadeInp.style.maxWidth = '160px';
    vadeInp.value = new Date().toISOString().split('T')[0];
    wrap.appendChild(satir('Vade Tarihi', vadeInp));

    const kdvSel = document.createElement('select');
    kdvSel.className = 'fiyat-input';
    [0, 1, 8, 10, 18, 20].forEach(function(v) {
      const o = document.createElement('option');
      o.value = String(v); o.textContent = '%' + v;
      kdvSel.appendChild(o);
    });
    kdvSel.value = '0';
    wrap.appendChild(satir('KDV Oranı', kdvSel));

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;margin-top:12px;';

    const kaydetBtn = document.createElement('button');
    kaydetBtn.className = 'btn-primary'; kaydetBtn.textContent = 'Fatura Kes';

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel'; iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ Alış faturası iptal edildi.', 'bot'); };

    kaydetBtn.onclick = async function() {
      const cariAdi = (secilenCari || ddInp.value || '').trim();
      const tutar   = parseFiyat(tutarInp.value);
      if (!cariAdi) { ddInp.style.borderColor = '#e53e3e'; return; }
      if (!tutar || tutar <= 0) { tutarInp.style.borderColor = '#e53e3e'; return; }
      kaydetBtn.disabled = true; iptalBtn.disabled = true;
      kaydetBtn.textContent = 'Kesiliyor…';
      try {
        const body = {
          cari_adi:   cariAdi,
          tutar:      tutar,
          aciklama:   aciklamaInp.value.trim() || null,
          issue_date: tarihInp.value || null,
          due_date:   vadeInp.value  || null,
          vat_rate:   parseFloat(kdvSel.value || '0'),
        };
        const res = await fetch('/api/alis-faturasi-kes', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (localStorage.getItem('token') || '') },
          body: JSON.stringify(body)
        });
        const d = await res.json();
        if (d.basarili) {
          addMessage('✅ Alış faturası kesildi: ' + safeHtml(cariAdi) + ' · ' + tutar.toLocaleString('tr-TR') + ' ₺ (fatura #' + d.fatura_id + ')', 'bot');
        } else {
          addMessage('❌ Fatura kesilemedi: ' + (d.hata || d.detail || 'Bilinmeyen hata'), 'bot');
          kaydetBtn.disabled = false; iptalBtn.disabled = false;
          kaydetBtn.textContent = 'Fatura Kes';
        }
      } catch {
        addMessage('❌ Fatura kesilemedi.', 'bot');
        kaydetBtn.disabled = false; iptalBtn.disabled = false;
        kaydetBtn.textContent = 'Fatura Kes';
      }
    };

    btnRow.appendChild(kaydetBtn);
    btnRow.appendChild(iptalBtn);
    wrap.appendChild(btnRow);
    addMessage('', 'bot', wrap);
    sohbetPanelKaydet('alis_faturasi_kes', onMusteri ? { musteri: onMusteri } : {});
    setTimeout(function() { (onMusteri ? tutarInp : ddInp).focus(); }, 100);
  }

  // ── FATURA İPTAL ─────────────────────────────────────────────────

  async function faturaIptalFormGoster(faturaId, onTip) {
    // Tip belirtilmemiş ve faturaId de yoksa önce tip soralım
    if (!onTip && !faturaId) {
      tipSecimSor(
        '❌ Fatura İptal',
        'Hangi tür faturayı iptal etmek istiyorsunuz?',
        function(secilenTip) { faturaIptalFormGoster(null, secilenTip); }
      );
      return;
    }
    onTip = (onTip === 'supplier') ? 'supplier' : 'customer';

    billablePanelTikla('fatura_iptal', faturaId ? { fatura_id: String(faturaId), tip: onTip } : { tip: onTip });

    const wrap = document.createElement('div');
    wrap.style.cssText = 'font-size:13px;';

    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-weight:500;margin-bottom:10px;color:#dc2626;';
    baslik.textContent = onTip === 'supplier' ? '❌ Alış Faturası İptal' : '❌ Satış Faturası İptal';
    wrap.appendChild(baslik);

    const uyari = document.createElement('div');
    uyari.style.cssText = 'font-size:12px;color:#78350f;background:#fffbeb;border:1.5px solid #f59e0b;border-radius:8px;padding:8px 12px;margin-bottom:10px;line-height:1.6;';
    const _odemeSozcuk = onTip === 'supplier' ? 'ödemesi' : 'tahsilatı';
    uyari.innerHTML = '⚠️ <b>Dikkat:</b> İptal edilen fatura Paraşüt\'te geri alınamayabilir. Ödenmiş faturalar önce ' + _odemeSozcuk + ' silinmeden iptal edilemez.';
    wrap.appendChild(uyari);

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

    const idInp = document.createElement('input');
    idInp.type = 'text'; idInp.className = 'fiyat-input'; idInp.placeholder = 'Fatura ID (Paraşüt URL\'den)';
    if (faturaId) idInp.value = String(faturaId);
    wrap.appendChild(satir('Fatura ID *', idInp));

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;margin-top:12px;';

    const iptalEtBtn = document.createElement('button');
    iptalEtBtn.className = 'btn-primary';
    iptalEtBtn.style.background = '#dc2626';
    iptalEtBtn.textContent = '❌ İptal Et';

    const vazgecBtn = document.createElement('button');
    vazgecBtn.className = 'btn-cancel'; vazgecBtn.textContent = '✕ Vazgeç';
    vazgecBtn.onclick = function() { addMessage('✅ İptal işlemi durduruldu.', 'bot'); };

    iptalEtBtn.onclick = async function() {
      const fid = idInp.value.trim();
      if (!fid) { idInp.style.borderColor = '#e53e3e'; return; }
      iptalEtBtn.disabled = true; vazgecBtn.disabled = true;
      iptalEtBtn.textContent = 'İptal ediliyor…';
      try {
        // Generic endpoint — backend tipe göre doğru cancel'i çağırır
        const res = await fetch('/api/fatura-iptal', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ fatura_id: fid, tip: onTip })
        });
        const d = await res.json();
        if (d.basarili) {
          addMessage('✅ Fatura iptal edildi (#' + fid + ').', 'bot');
        } else {
          addMessage('❌ İptal edilemedi: ' + (d.hata || d.detail || 'Bilinmeyen hata'), 'bot');
          iptalEtBtn.disabled = false; vazgecBtn.disabled = false;
          iptalEtBtn.textContent = '❌ İptal Et';
        }
      } catch {
        addMessage('❌ İptal işlemi başarısız.', 'bot');
        iptalEtBtn.disabled = false; vazgecBtn.disabled = false;
        iptalEtBtn.textContent = '❌ İptal Et';
      }
    };

    btnRow.appendChild(iptalEtBtn);
    btnRow.appendChild(vazgecBtn);
    wrap.appendChild(btnRow);
    addMessage('', 'bot', wrap);
    sohbetPanelKaydet('fatura_iptal', faturaId ? { fatura_id: String(faturaId), tip: onTip } : { tip: onTip });
    if (!faturaId) setTimeout(function() { idInp.focus(); }, 100);
  }

  // ── FATURA GÜNCELLE ──────────────────────────────────────────────

  async function faturaGuncelleFormGoster(faturaId, mevcut) {
    mevcut = mevcut || {};
    const faturaTipi = (mevcut.tip === 'supplier') ? 'supplier' : 'customer';
    const wrap = document.createElement('div');
    wrap.style.cssText = 'font-size:13px;';

    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-weight:500;margin-bottom:10px;';
    const _tipEt = faturaTipi === 'supplier' ? '📥 Alış' : '🧾 Satış';
    baslik.textContent = '✏️ ' + _tipEt + ' Faturası #' + faturaId + ' Güncelle';
    wrap.appendChild(baslik);

    const bilgi = document.createElement('div');
    bilgi.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);';
    bilgi.innerHTML = 'Sadece doldurduğun alanlar Paraşüt\'e gönderilir. Boş bırakılanlar değişmez.<br>⚠️ Kalemler ve tutar değişmez — onun için faturayı iptal et, yeniden kes.';
    wrap.appendChild(bilgi);

    function toIsoTarih(t) {
      if (!t || t === '-') return '';
      const m = String(t).match(/^(\d{2})\.(\d{2})\.(\d{4})/);
      if (m) return m[3] + '-' + m[2] + '-' + m[1];
      if (/^\d{4}-\d{2}-\d{2}/.test(String(t))) return String(t).substring(0, 10);
      return '';
    }

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

    // ── COLLAPSIBLE BÖLÜM HELPER ─────────────────────────────────
    function bolumOlustur(baslik, icerik, acik) {
      const sec = document.createElement('div');
      sec.style.cssText = 'border:1px solid var(--border);border-radius:9px;margin-bottom:8px;overflow:hidden;';

      const head = document.createElement('button');
      head.type = 'button';
      head.style.cssText = 'width:100%;display:flex;align-items:center;justify-content:space-between;padding:9px 12px;background:var(--bg);border:none;cursor:pointer;font-size:12px;font-weight:600;color:var(--text);text-align:left;';
      const titleEl = document.createElement('span');
      titleEl.textContent = baslik;
      const chev = document.createElement('span');
      chev.style.cssText = 'font-size:10px;color:var(--muted);transition:transform 0.2s;';
      chev.textContent = '▶';
      head.appendChild(titleEl);
      head.appendChild(chev);

      const body = document.createElement('div');
      body.style.cssText = 'padding:0 12px;max-height:0;overflow:hidden;transition:max-height 0.3s,padding 0.3s;';
      body.appendChild(icerik);

      head.onclick = function() {
        if (body.style.maxHeight && body.style.maxHeight !== '0px') {
          body.style.maxHeight = '0';
          body.style.padding = '0 12px';
          chev.style.transform = 'rotate(0deg)';
        } else {
          body.style.maxHeight = '600px';
          body.style.padding = '10px 12px 12px';
          chev.style.transform = 'rotate(90deg)';
        }
      };
      if (acik) head.onclick();
      sec.appendChild(head);
      sec.appendChild(body);
      return sec;
    }

    // ── 1. TEMEL (default açık) ─────────────────────────────────
    const temelBody = document.createElement('div');
    const aciklamaInp = document.createElement('input');
    aciklamaInp.type = 'text'; aciklamaInp.className = 'fiyat-input';
    aciklamaInp.value = mevcut.aciklama || '';
    temelBody.appendChild(satir('Açıklama', aciklamaInp));

    const tarihInp = document.createElement('input');
    tarihInp.type = 'date'; tarihInp.className = 'fiyat-input';
    tarihInp.style.maxWidth = '160px';
    tarihInp.value = toIsoTarih(mevcut.tarih);
    temelBody.appendChild(satir('Fatura Tarihi', tarihInp));

    const vadeInp = document.createElement('input');
    vadeInp.type = 'date'; vadeInp.className = 'fiyat-input';
    vadeInp.style.maxWidth = '160px';
    vadeInp.value = toIsoTarih(mevcut.vade);
    temelBody.appendChild(satir('Vade Tarihi', vadeInp));

    wrap.appendChild(bolumOlustur('📝 Temel Bilgiler', temelBody, true));

    // ── 2. RESMİ BİLGİ (Fatura no, seri, para birimi) ─────────────
    const resmiBody = document.createElement('div');
    const invoiceNoInp = document.createElement('input');
    invoiceNoInp.type = 'text'; invoiceNoInp.className = 'fiyat-input';
    invoiceNoInp.placeholder = 'Boş bırakılırsa Paraşüt otomatik atar';
    resmiBody.appendChild(satir('Fatura No', invoiceNoInp));

    const invoiceSeriesInp = document.createElement('input');
    invoiceSeriesInp.type = 'text'; invoiceSeriesInp.className = 'fiyat-input';
    invoiceSeriesInp.placeholder = 'Örn: A';
    resmiBody.appendChild(satir('Fatura Seri', invoiceSeriesInp));

    const currencySel = document.createElement('select');
    currencySel.className = 'fiyat-input';
    [['', 'Değiştirme'], ['TRL', 'TRL (₺)'], ['USD', 'USD ($)'], ['EUR', 'EUR (€)'], ['GBP', 'GBP (£)']].forEach(function(c) {
      const o = document.createElement('option'); o.value = c[0]; o.textContent = c[1];
      currencySel.appendChild(o);
    });
    resmiBody.appendChild(satir('Para Birimi', currencySel));

    const exchangeRateInp = document.createElement('input');
    exchangeRateInp.type = 'number'; exchangeRateInp.step = '0.0001';
    exchangeRateInp.className = 'fiyat-input';
    exchangeRateInp.placeholder = 'Örn: 34.5 (yabancı para için)';
    resmiBody.appendChild(satir('Döviz Kuru', exchangeRateInp));

    wrap.appendChild(bolumOlustur('🧾 Resmi Bilgi', resmiBody, false));

    // ── 3. VERGİ & STOPAJ ─────────────────────────────────────────
    const vergiBody = document.createElement('div');
    const withholdingInp = document.createElement('input');
    withholdingInp.type = 'number'; withholdingInp.step = '0.01';
    withholdingInp.className = 'fiyat-input';
    withholdingInp.placeholder = 'Örn: 5 (% olarak)';
    vergiBody.appendChild(satir('Stopaj Oranı (%)', withholdingInp));

    const vatWithholdingInp = document.createElement('input');
    vatWithholdingInp.type = 'text'; vatWithholdingInp.className = 'fiyat-input';
    vatWithholdingInp.placeholder = 'Örn: KDV 5/10 (Paraşüt kodu)';
    vergiBody.appendChild(satir('KDV Tevkifat Kodu', vatWithholdingInp));

    wrap.appendChild(bolumOlustur('💼 Vergi & Stopaj', vergiBody, false));

    // ── 4. ADRES & VERGİ NO ───────────────────────────────────────
    const adresBody = document.createElement('div');
    const billingAddressInp = document.createElement('input');
    billingAddressInp.type = 'text'; billingAddressInp.className = 'fiyat-input';
    billingAddressInp.placeholder = 'Fatura adresi';
    adresBody.appendChild(satir('Fatura Adresi', billingAddressInp));

    const taxOfficeInp = document.createElement('input');
    taxOfficeInp.type = 'text'; taxOfficeInp.className = 'fiyat-input';
    taxOfficeInp.placeholder = 'Vergi dairesi';
    adresBody.appendChild(satir('Vergi Dairesi', taxOfficeInp));

    const taxNumberInp = document.createElement('input');
    taxNumberInp.type = 'text'; taxNumberInp.className = 'fiyat-input';
    taxNumberInp.placeholder = 'VKN/TCKN';
    adresBody.appendChild(satir('Vergi No', taxNumberInp));

    const cityInp = document.createElement('input');
    cityInp.type = 'text'; cityInp.className = 'fiyat-input';
    adresBody.appendChild(satir('Şehir', cityInp));

    const districtInp = document.createElement('input');
    districtInp.type = 'text'; districtInp.className = 'fiyat-input';
    adresBody.appendChild(satir('İlçe', districtInp));

    wrap.appendChild(bolumOlustur('🏠 Adres & Vergi Bilgisi', adresBody, false));

    // ── 5. SİPARİŞ ─────────────────────────────────────────────────
    const siparisBody = document.createElement('div');
    const orderNoInp = document.createElement('input');
    orderNoInp.type = 'text'; orderNoInp.className = 'fiyat-input';
    orderNoInp.placeholder = 'Sipariş referans no';
    siparisBody.appendChild(satir('Sipariş No', orderNoInp));

    const orderDateInp = document.createElement('input');
    orderDateInp.type = 'date'; orderDateInp.className = 'fiyat-input';
    orderDateInp.style.maxWidth = '160px';
    siparisBody.appendChild(satir('Sipariş Tarihi', orderDateInp));

    wrap.appendChild(bolumOlustur('📦 Sipariş Bilgisi', siparisBody, false));

    // ── 6. CARİ DEĞİŞTİRME (yanlış cariye kesilmişse) ─────────────
    const cariBody = document.createElement('div');
    const cariUyari = document.createElement('div');
    cariUyari.style.cssText = 'font-size:11px;color:#92400e;background:#fffbeb;border:1px solid #fde68a;padding:6px 8px;border-radius:6px;margin-bottom:8px;';
    cariUyari.textContent = '⚠️ Yalnızca yanlış cariye kesildiğinde kullan. Cariyi değiştirmek bakiye taşır.';
    cariBody.appendChild(cariUyari);

    let secilenYeniCariId = null;
    let tumCariler = [];
    try {
      const cr = await fetch('/api/cariler');
      tumCariler = (await cr.json()).cariler || [];
      // Tipe uygun filtre
      if (faturaTipi === 'supplier') {
        tumCariler = tumCariler.filter(function(c) {
          const t = c.tip || 'customer';
          return t === 'supplier' || t === 'customer_supplier' || t === 'both';
        });
      } else {
        tumCariler = tumCariler.filter(function(c) {
          const t = c.tip || 'customer';
          return t === 'customer' || t === 'customer_supplier' || t === 'both';
        });
      }
    } catch {}

    const { wrap: cariDD } = cariSecimDropdown(tumCariler, function(c) {
      secilenYeniCariId = c.id;
    }, faturaTipi === 'supplier' ? 'Yeni tedarikçi seç...' : 'Yeni müşteri seç...');
    cariBody.appendChild(satir('Yeni ' + (faturaTipi === 'supplier' ? 'Tedarikçi' : 'Müşteri'), cariDD));

    wrap.appendChild(bolumOlustur(
      faturaTipi === 'supplier' ? '🔄 Tedarikçi Değiştir' : '🔄 Müşteri Değiştir',
      cariBody, false));

    // ── 7. BAYRAKLAR ────────────────────────────────────────────────
    const bayrakBody = document.createElement('div');
    const cashSaleSel = document.createElement('select');
    cashSaleSel.className = 'fiyat-input';
    [['', 'Değiştirme'], ['true', 'Peşin satış'], ['false', 'Vadeli']].forEach(function(c) {
      const o = document.createElement('option'); o.value = c[0]; o.textContent = c[1];
      cashSaleSel.appendChild(o);
    });
    bayrakBody.appendChild(satir('Peşin/Vadeli', cashSaleSel));

    if (faturaTipi === 'customer') {
      const isAbroadSel = document.createElement('select');
      isAbroadSel.className = 'fiyat-input';
      [['', 'Değiştirme'], ['true', 'Yurt dışı'], ['false', 'Yurt içi']].forEach(function(c) {
        const o = document.createElement('option'); o.value = c[0]; o.textContent = c[1];
        isAbroadSel.appendChild(o);
      });
      bayrakBody.appendChild(satir('Yurt İçi/Dışı', isAbroadSel));
      bayrakBody._isAbroadSel = isAbroadSel;
    }

    wrap.appendChild(bolumOlustur('⚙️ Diğer Bayraklar', bayrakBody, false));

    // ── KAYDET / İPTAL ─────────────────────────────────────────────
    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;margin-top:12px;';

    const kaydetBtn = document.createElement('button');
    kaydetBtn.className = 'btn-primary'; kaydetBtn.textContent = 'Kaydet';

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel'; iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ Güncelleme iptal edildi.', 'bot'); };

    kaydetBtn.onclick = async function() {
      kaydetBtn.disabled = true; iptalBtn.disabled = true;
      kaydetBtn.textContent = 'Kaydediliyor…';

      const body = { fatura_id: String(faturaId), tip: faturaTipi };

      // Temel
      const yeniAciklama = aciklamaInp.value.trim();
      const eskiAciklama = (mevcut.aciklama || '').trim();
      if (yeniAciklama !== eskiAciklama) body.aciklama = yeniAciklama;
      if (tarihInp.value && tarihInp.value !== toIsoTarih(mevcut.tarih)) body.issue_date = tarihInp.value;
      if (vadeInp.value && vadeInp.value !== toIsoTarih(mevcut.vade))   body.due_date   = vadeInp.value;

      // Resmi
      if (invoiceNoInp.value.trim())     body.invoice_no     = invoiceNoInp.value.trim();
      if (invoiceSeriesInp.value.trim()) body.invoice_series = invoiceSeriesInp.value.trim();
      if (currencySel.value)             body.currency       = currencySel.value;
      if (exchangeRateInp.value)         body.exchange_rate  = parseFloat(exchangeRateInp.value);

      // Vergi
      if (withholdingInp.value)          body.withholding_rate     = parseFloat(withholdingInp.value);
      if (vatWithholdingInp.value.trim())body.vat_withholding_code = vatWithholdingInp.value.trim();

      // Adres
      if (billingAddressInp.value.trim()) body.billing_address = billingAddressInp.value.trim();
      if (taxOfficeInp.value.trim())      body.tax_office      = taxOfficeInp.value.trim();
      if (taxNumberInp.value.trim())      body.tax_number      = taxNumberInp.value.trim();
      if (cityInp.value.trim())           body.city            = cityInp.value.trim();
      if (districtInp.value.trim())       body.district        = districtInp.value.trim();

      // Sipariş
      if (orderNoInp.value.trim())   body.order_no   = orderNoInp.value.trim();
      if (orderDateInp.value)        body.order_date = orderDateInp.value;

      // Cari değiştirme (tipe göre doğru anahtara)
      if (secilenYeniCariId) {
        if (faturaTipi === 'supplier') body.supplier_id = String(secilenYeniCariId);
        else                            body.contact_id  = String(secilenYeniCariId);
      }

      // Bayraklar
      if (cashSaleSel.value) body.cash_sale = (cashSaleSel.value === 'true');
      if (bayrakBody._isAbroadSel && bayrakBody._isAbroadSel.value) {
        body.is_abroad = (bayrakBody._isAbroadSel.value === 'true');
      }

      const degisenAlanlar = Object.keys(body).filter(function(k) {
        return k !== 'fatura_id' && k !== 'tip';
      });
      if (degisenAlanlar.length === 0) {
        addMessage('ℹ️ Değişiklik yapmadınız, fatura aynı kaldı.', 'bot');
        kaydetBtn.disabled = false; iptalBtn.disabled = false;
        kaydetBtn.textContent = 'Kaydet';
        return;
      }

      try {
        const res = await fetch('/api/fatura-guncelle', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const d = await res.json();
        if (d.basarili) {
          addMessage('✅ Fatura güncellendi (#' + faturaId + ') · değişen alanlar: ' + degisenAlanlar.join(', '), 'bot');
        } else {
          addMessage('❌ Güncellenemedi: ' + (d.hata || d.detail || 'Bilinmeyen hata'), 'bot');
          kaydetBtn.disabled = false; iptalBtn.disabled = false;
          kaydetBtn.textContent = 'Kaydet';
        }
      } catch (e) {
        addMessage('❌ Güncellenemedi.', 'bot');
        kaydetBtn.disabled = false; iptalBtn.disabled = false;
        kaydetBtn.textContent = 'Kaydet';
      }
    };

    btnRow.appendChild(kaydetBtn);
    btnRow.appendChild(iptalBtn);
    wrap.appendChild(btnRow);
    addMessage('', 'bot', wrap);
  }

  // ── E-FATURA / E-ARŞİV RESMİLEŞTİRME ─────────────────────────────

  async function eFaturaFormGoster(faturaId) {
    billablePanelTikla('e_fatura_kes', faturaId ? { fatura_id: String(faturaId) } : {});

    const wrap = document.createElement('div');
    wrap.style.cssText = 'font-size:13px;';

    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-weight:500;margin-bottom:10px;';
    baslik.textContent = '📤 E-Fatura / E-Arşiv';
    wrap.appendChild(baslik);

    const bilgi = document.createElement('div');
    bilgi.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);line-height:1.6;';
    bilgi.innerHTML = 'VKN girerseniz mükellef kontrolü yapılır:<br>· <b>Mükellef</b> → resmi <b>E-Fatura</b> kesilir<br>· <b>Değilse</b> → <b>E-Arşiv</b> kesilir<br>VKN boş bırakılırsa direkt E-Arşiv olarak gönderilir.';
    wrap.appendChild(bilgi);

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

    const idInp = document.createElement('input');
    idInp.type = 'text'; idInp.className = 'fiyat-input'; idInp.placeholder = 'Fatura ID';
    if (faturaId) idInp.value = String(faturaId);
    wrap.appendChild(satir('Satış Faturası ID *', idInp));

    const vknInp = document.createElement('input');
    vknInp.type = 'text'; vknInp.className = 'fiyat-input';
    vknInp.placeholder = '10 haneli VKN (opsiyonel)';
    vknInp.maxLength = 11;
    wrap.appendChild(satir('Alıcı VKN', vknInp));

    const tcknInp = document.createElement('input');
    tcknInp.type = 'text'; tcknInp.className = 'fiyat-input';
    tcknInp.placeholder = 'TCKN (sadece şahıs için)';
    tcknInp.maxLength = 11;
    wrap.appendChild(satir('Alıcı TCKN', tcknInp));

    // Mükellef kontrol önizleme alanı
    const onizleme = document.createElement('div');
    onizleme.style.cssText = 'font-size:11px;margin-top:4px;min-height:18px;';
    wrap.appendChild(onizleme);

    let mukellef_etiket = null;
    vknInp.onblur = async function() {
      const vkn = vknInp.value.trim();
      mukellef_etiket = null;
      if (!vkn || vkn.length < 10) { onizleme.textContent = ''; return; }
      onizleme.style.color = 'var(--muted)';
      onizleme.textContent = '⏳ Mükellef kontrol ediliyor…';
      try {
        const res = await fetch('/api/e-fatura-mukellef/' + encodeURIComponent(vkn));
        const d = await res.json();
        if (d.mukellef) {
          mukellef_etiket = d.etiket;
          onizleme.style.color = '#16a34a';
          onizleme.textContent = '✅ E-Fatura mükellefi: ' + (d.unvan || vkn) + (d.etiket ? ' (' + d.etiket + ')' : '');
        } else {
          onizleme.style.color = '#b45309';
          onizleme.textContent = 'ℹ️ Mükellef değil — E-Arşiv kesilecek.';
        }
      } catch {
        onizleme.style.color = '#dc2626';
        onizleme.textContent = '❌ Sorgu başarısız.';
      }
    };

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;margin-top:12px;';

    const gonderBtn = document.createElement('button');
    gonderBtn.className = 'btn-primary'; gonderBtn.textContent = '📤 Resmileştir';

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel'; iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ Resmileştirme iptal edildi.', 'bot'); };

    gonderBtn.onclick = async function() {
      const fid  = idInp.value.trim();
      const vkn  = vknInp.value.trim() || null;
      const tckn = tcknInp.value.trim() || null;
      if (!fid) { idInp.style.borderColor = '#e53e3e'; return; }
      gonderBtn.disabled = true; iptalBtn.disabled = true;
      gonderBtn.textContent = 'Gönderiliyor…';
      try {
        const body = { fatura_id: fid };
        if (vkn)  body.vkn  = vkn;
        if (tckn) body.tckn = tckn;
        const res = await fetch('/api/e-fatura-kes', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (localStorage.getItem('token') || '') },
          body: JSON.stringify(body)
        });
        const d = await res.json();
        if (d.basarili) {
          const tipAd = d.tip === 'e_fatura' ? 'E-Fatura' : 'E-Arşiv';
          addMessage('✅ ' + tipAd + ' başarıyla kesildi (#' + fid + ').', 'bot');
        } else {
          const hatalar = d.hatalar && d.hatalar.length ? ' — ' + d.hatalar.join(', ') : '';
          addMessage('❌ Resmileştirme başarısız: ' + (d.hata || d.durum || 'Bilinmeyen') + hatalar, 'bot');
          gonderBtn.disabled = false; iptalBtn.disabled = false;
          gonderBtn.textContent = '📤 Resmileştir';
        }
      } catch {
        addMessage('❌ Resmileştirme başarısız.', 'bot');
        gonderBtn.disabled = false; iptalBtn.disabled = false;
        gonderBtn.textContent = '📤 Resmileştir';
      }
    };

    btnRow.appendChild(gonderBtn);
    btnRow.appendChild(iptalBtn);
    wrap.appendChild(btnRow);
    addMessage('', 'bot', wrap);
    sohbetPanelKaydet('e_fatura_kes', faturaId ? { fatura_id: String(faturaId) } : {});
    if (!faturaId) setTimeout(function() { idInp.focus(); }, 100);
  }

  // ── CACHE YENİLE ─────────────────────────────────────────────────


  async function faturaListesiGoster(onMusteri, tip) {
    billablePanelTikla('fatura_sorgula', onMusteri ? { musteri: onMusteri } : {});

    // Tip belirtilmemişse önce kullanıcıya sor (intent'ten cari adı geldiyse
    // direkt müşteri varsayalım — eski davranış).
    if (!tip) {
      if (onMusteri) {
        tip = 'customer';
      } else {
        tipSecimSor(
          '🔍 Fatura Sorgula',
          'Hangi tür faturayı sorgulamak istiyorsunuz?',
          function(secilenTip) { faturaListesiGoster(null, secilenTip); }
        );
        return;
      }
    }

    showTyping();
    let cariler = [];
    try {
      const tumCariler = await carileriGetir(false, true);
      // Tip'e göre cari filtresi: müşteri faturası → customer + customer_supplier,
      // tedarikçi alış faturası → supplier + customer_supplier
      cariler = tumCariler.filter(function(c) {
        const t = c.tip || 'customer';
        if (tip === 'supplier') {
          return t === 'supplier' || t === 'both' || t === 'customer_supplier';
        }
        return t === 'customer' || t === 'both' || t === 'customer_supplier';
      });
    } catch {}
    removeTyping();

    // Params'tan müşteri geldi ve listede tam eşleşme var → direkt faturalarını aç
    if (onMusteri) {
      const filtre = onMusteri.toLowerCase();
      const eslesen = cariler.find(function(c) { return c.ad.toLowerCase().includes(filtre); });
      if (eslesen) {
        cariFaturalariniGetir(eslesen.ad, null, tip);
        return;
      }
    }

    const wrap = document.createElement('div');
    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-size:13px;font-weight:500;color:var(--text);margin-bottom:10px;';
    baslik.textContent = tip === 'supplier'
      ? '🏭 Tedarikçi (Alış) Faturaları'
      : '👤 Müşteri (Satış) Faturaları';
    wrap.appendChild(baslik);
    const faturaInfo = document.createElement('div');
    faturaInfo.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);';
    faturaInfo.textContent = tip === 'supplier'
      ? 'Alış faturalarını görüntülemek istediğiniz tedarikçiyi seçin.'
      : 'Satış faturalarını görüntülemek istediğiniz müşteriyi seçin.';
    wrap.appendChild(faturaInfo);

    const { wrap: ddWrap } = cariSecimDropdown(cariler, function(c) {
      cariFaturalariniGetir(c.ad, wrap, tip);
    }, 'Cari adı yazın...');
    wrap.appendChild(ddWrap);

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel';
    iptalBtn.style.marginTop = '8px';
    iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ İptal edildi.', 'bot'); };
    wrap.appendChild(iptalBtn);

    addMessage('', 'bot', wrap);

    sohbetPanelKaydet('fatura_sorgula', onMusteri ? { musteri: onMusteri } : {});

    // Params'tan müşteri adı geldiyse arama kutusuna yaz
    if (onMusteri) {
      setTimeout(function() {
        const inp = ddWrap.querySelector('input');
        if (inp) { inp.value = onMusteri; inp.dispatchEvent(new Event('input')); }
      }, 150);
    } else {
      setTimeout(function() { ddWrap.querySelector('input').focus(); }, 100);
    }
  }

  async function cariFaturalariniGetir(cariAdi, wrapParent, tip) {
    tip = tip || 'customer';
    // Filtre formu
    const listWrap = document.createElement('div');

    // Başlık + filtreler
    const filterRow = document.createElement('div');
    filterRow.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:10px;';

    const baslikEl = document.createElement('div');
    baslikEl.style.cssText = 'font-size:12px;color:var(--muted);width:100%;margin-bottom:4px;';
    const _tipEtiket = tip === 'supplier' ? '🏭 Alış' : '👤 Satış';
    baslikEl.textContent = cariAdi + ' — ' + _tipEtiket + ' faturaları:';
    listWrap.appendChild(baslikEl);

    // Tarih aralığı
    const bugun   = new Date().toISOString().split('T')[0];
    const yilBas  = new Date(new Date().getFullYear(), 0, 1).toISOString().split('T')[0];

    const baslangicInp = document.createElement('input');
    baslangicInp.type = 'date';
    baslangicInp.className = 'fiyat-input';
    baslangicInp.style.cssText = 'flex:1;min-width:120px;';
    baslangicInp.value = yilBas;
    baslangicInp.title = 'Başlangıç tarihi';

    const araSpan = document.createElement('span');
    araSpan.style.cssText = 'font-size:12px;color:var(--muted);';
    araSpan.textContent = '—';

    const bitisInp = document.createElement('input');
    bitisInp.type = 'date';
    bitisInp.className = 'fiyat-input';
    bitisInp.style.cssText = 'flex:1;min-width:120px;';
    bitisInp.value = bugun;
    bitisInp.title = 'Bitiş tarihi';

    // Durum filtresi
    const durumSel = document.createElement('select');
    durumSel.className = 'fiyat-input';
    durumSel.style.cssText = 'flex:1;min-width:110px;cursor:pointer;';
    [['', 'Tümü'], ['not_paid', '🟡 Ödenmemiş'], ['overdue', '🔴 Gecikmiş'], ['paid', '✅ Ödendi']].forEach(function([v, l]) {
      const opt = document.createElement('option');
      opt.value = v; opt.textContent = l;
      durumSel.appendChild(opt);
    });

    filterRow.appendChild(baslangicInp);
    filterRow.appendChild(araSpan);
    filterRow.appendChild(bitisInp);
    filterRow.appendChild(durumSel);
    listWrap.appendChild(filterRow);

    // Liste + sayfalama alanları
    const faturaListesi = document.createElement('div');
    const dahaFazlaWrap = document.createElement('div');
    listWrap.appendChild(faturaListesi);
    listWrap.appendChild(dahaFazlaWrap);

    listWrap._faturaUiPersisted = false;

    addMessage('', 'bot', listWrap);

    let mevcutSayfa = 1;
    async function faturalariYukle(sayfa, temizle) {
      if (temizle) faturaListesi.innerHTML = '';
      dahaFazlaWrap.innerHTML = '';

      const yukEl = document.createElement('div');
      yukEl.style.cssText = 'font-size:12px;color:var(--muted);padding:6px 0;';
      yukEl.textContent = 'Yükleniyor…';
      faturaListesi.appendChild(yukEl);

      try {
        const params = new URLSearchParams({ sayfa: sayfa, limit: 25, tip: tip });
        if (baslangicInp.value) params.set('baslangic', baslangicInp.value);
        if (bitisInp.value)     params.set('bitis', bitisInp.value);
        if (durumSel.value)     params.set('durum', durumSel.value);

        const res  = await fetch('/api/cari-faturalar/' + encodeURIComponent(cariAdi) + '?' + params.toString());
        const data = await res.json();
        faturaListesi.removeChild(yukEl);

        if (sayfa === 1 && !listWrap._faturaUiPersisted) {
          listWrap._faturaUiPersisted = true;
          sohbetPanelKaydet('fatura_sorgula', { musteri: cariAdi });
        }

        if (!data.faturalar || data.faturalar.length === 0) {
          if (sayfa === 1) {
            const bosEl = document.createElement('div');
            bosEl.style.cssText = 'font-size:12px;color:var(--muted);padding:6px 0;';
            bosEl.textContent = 'Bu kriterlere uygun fatura bulunamadı.';
            faturaListesi.appendChild(bosEl);
          }
          return;
        }

        if (sayfa === 1) {
          baslikEl.textContent = cariAdi + ' — ' + (data.toplam_kayit || data.toplam) + ' fatura';
        }

        data.faturalar.forEach(function(f) {
          const btn = document.createElement('button');
          btn.className = 'irsaliye-btn';
          btn.style.cssText = btn.style.cssText + 'flex-direction:column;align-items:flex-start;gap:3px;';

          const ust = document.createElement('div');
          ust.style.cssText = 'display:flex;justify-content:space-between;width:100%;';
          const sol = document.createElement('span');
          sol.style.fontSize = '13px';
          const durumEmoji = f.durum === 'paid' ? '✅' : f.durum === 'overdue' ? '🔴' : '🟡';
          sol.textContent = durumEmoji + ' ' + formatTarih(f.tarih) + ' — ' + (f.aciklama || 'Fatura').substring(0, 40);
          const sag = document.createElement('span');
          sag.className = 'kg';
          sag.textContent = parseFloat(f.tutar || 0).toLocaleString('tr-TR', {minimumFractionDigits:0}) + ' TL';
          ust.appendChild(sol); ust.appendChild(sag);
          btn.appendChild(ust);

          if (f.kalan > 0) {
            const alt = document.createElement('div');
            alt.style.cssText = 'font-size:11px;color:#b45309;';
            alt.textContent = 'Kalan: ' + parseFloat(f.kalan).toLocaleString('tr-TR', {minimumFractionDigits:0}) + ' TL';
            btn.appendChild(alt);
          }
          btn.onclick = function() {
            Array.from(faturaListesi.querySelectorAll('.irsaliye-btn')).forEach(function(b) { b.style.opacity='0.5'; b.disabled=true; });
            btn.style.opacity = '1'; btn.style.background = 'var(--accent-lt)'; btn.disabled = false;
            // Tip backend'den f.tip ile dönüyor; öncelik liste seçimindeki tip
            faturaDetayGoster(f.id, f.aciklama, cariAdi, f.tip || tip);
          };
          faturaListesi.appendChild(btn);
        });

        // Sayfalama
        if (data.sonraki_sayfa_var) {
          const dahaBtn = document.createElement('button');
          dahaBtn.className = 'btn-cancel';
          dahaBtn.style.cssText = 'margin-top:6px;width:100%;';
          dahaBtn.textContent = '⬇ Daha Fazla Yükle (' + data.faturalar.length + '/' + (data.toplam_kayit||'?') + ')';
          dahaBtn.onclick = async function() {
            mevcutSayfa++;
            dahaBtn.disabled = true; dahaBtn.textContent = 'Yükleniyor…';
            await faturalariYukle(mevcutSayfa, false);
          };
          dahaFazlaWrap.appendChild(dahaBtn);
        }
      } catch(e) {
        faturaListesi.removeChild(yukEl);
        const hatEl = document.createElement('div');
        hatEl.style.cssText = 'font-size:12px;color:#f85149;';
        hatEl.textContent = '❌ Faturalar yüklenemedi: ' + e.message;
        faturaListesi.appendChild(hatEl);
      }
    }

    // Filtre değişince sıfırla
    baslangicInp.onchange = function() { mevcutSayfa=1; faturalariYukle(1,true); };
    bitisInp.onchange     = function() { mevcutSayfa=1; faturalariYukle(1,true); };
    durumSel.onchange     = function() { mevcutSayfa=1; faturalariYukle(1,true); };

    faturalariYukle(1, true);
  }

  async function faturaDetayGoster(faturaId, faturaAdi, cariCtx, tip) {
    cariCtx = cariCtx || '';
    tip = (tip === 'supplier') ? 'supplier' : 'customer';
    showTyping();
    try {
      const res = await fetch('/api/fatura-detay/' + faturaId + '?tip=' + tip);
      const f = await res.json();
      removeTyping();
      // Backend response'unda tip alanı varsa ona güven; yoksa parametreyi kullan
      const faturaTipi = f.tip || tip;

      billablePanelTikla('fatura_detay', { fatura_id: String(faturaId), musteri: cariCtx || '', tip: faturaTipi });

      const detayWrap = document.createElement('div');

      // Özet
      const ozet = document.createElement('div');
      ozet.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;line-height:1.8;';
      const durumEmoji = f.durum === 'paid' ? '✅ Ödendi' : f.durum === 'overdue' ? '🔴 Gecikmiş' : '🟡 Bekliyor';
      ozet.innerHTML = '📅 Tarih: ' + formatTarih(f.tarih) + ' &nbsp;|&nbsp; Vade: ' + formatTarih(f.vade) + '<br>' +
        '💰 Toplam: <b>' + parseFloat(f.tutar||0).toLocaleString('tr-TR',{minimumFractionDigits:0}) + ' TL</b>' +
        (f.kalan > 0 ? ' &nbsp;|&nbsp; Kalan: <b style="color:#b45309">' + parseFloat(f.kalan).toLocaleString('tr-TR',{minimumFractionDigits:0}) + ' TL</b>' : '') + '<br>' +
        '📌 Durum: ' + durumEmoji;
      detayWrap.appendChild(ozet);

      // Kalemler
      if (f.kalemler && f.kalemler.length > 0) {
        const kalemBaslik = document.createElement('div');
        kalemBaslik.style.cssText = 'font-size:11px;color:var(--muted2);text-transform:uppercase;letter-spacing:0.4px;margin-bottom:5px;';
        kalemBaslik.textContent = 'Fatura Kalemleri';
        detayWrap.appendChild(kalemBaslik);
        f.kalemler.forEach(function(k) {
          const row = document.createElement('div');
          row.style.cssText = 'display:flex;justify-content:space-between;font-size:12px;padding:5px 10px;background:var(--bg);border-radius:7px;margin-bottom:4px;';
          const aciklama = k.aciklama || 'Hurda';
          const tutar = parseFloat(k.tutar||0);
          const miktarStr = k.miktar > 0 ? ' — ' + parseFloat(k.miktar).toLocaleString('tr-TR') + ' kg' : '';
          row.innerHTML = '<span>' + safeHtml(aciklama) + safeHtml(miktarStr) + '</span><span style="font-weight:600">' + tutar.toLocaleString('tr-TR',{minimumFractionDigits:0}) + ' TL</span>';
          detayWrap.appendChild(row);
        });
      }

      // Tahsilatlar
      if (f.tahsilatlar && f.tahsilatlar.length > 0) {
        const tahBaslik = document.createElement('div');
        tahBaslik.style.cssText = 'font-size:11px;color:var(--muted2);text-transform:uppercase;letter-spacing:0.4px;margin:10px 0 5px;';
        tahBaslik.textContent = 'Tahsilatlar';
        detayWrap.appendChild(tahBaslik);
        f.tahsilatlar.forEach(function(t) {
          const row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;padding:5px 10px;background:#f0fdf4;border-radius:7px;margin-bottom:4px;';

          const sol = document.createElement('span');
          sol.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;';
          sol.innerHTML = '✅ ' + safeHtml(t.tarih) + ' — ' + safeHtml(t.aciklama);

          const tutar = document.createElement('span');
          tutar.style.cssText = 'font-weight:600;color:var(--accent);white-space:nowrap;';
          tutar.textContent = parseFloat(t.tutar||0).toLocaleString('tr-TR',{minimumFractionDigits:0}) + ' TL';

          row.appendChild(sol);
          row.appendChild(tutar);

          if (t.id || t.transaction_id) {
            const silBtn = document.createElement('button');
            silBtn.title = 'Bu tahsilatı sil';
            silBtn.style.cssText = 'background:transparent;border:1px solid #fecaca;color:#dc2626;border-radius:6px;padding:2px 8px;font-size:11px;cursor:pointer;';
            silBtn.textContent = '🗑';
            silBtn.onclick = async function() {
              if (!confirm('Bu tahsilat silinsin mi?\n\n' + (t.tarih||'') + ' — ' + (t.tutar||0) + ' TL')) return;
              silBtn.disabled = true; silBtn.textContent = '…';
              try {
                const r = await fetch('/api/tahsilat-sil', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({
                    odeme_id: t.id ? String(t.id) : '',
                    transaction_id: t.transaction_id ? String(t.transaction_id) : null
                  }),
                });
                const d = await r.json();
                if (d && d.basarili) {
                  row.style.opacity = '0.35';
                  row.style.textDecoration = 'line-through';
                  silBtn.remove();
                  addMessage('✅ Tahsilat silindi.', 'bot');
                } else {
                  silBtn.disabled = false; silBtn.textContent = '🗑';
                  addMessage('❌ Tahsilat silinemedi: ' + (d.hata || 'bilinmeyen hata'), 'bot');
                }
              } catch (e) {
                silBtn.disabled = false; silBtn.textContent = '🗑';
                addMessage('❌ Tahsilat silinemedi.', 'bot');
              }
            };
            row.appendChild(silBtn);
          }

          detayWrap.appendChild(row);
        });
      }

      // Bağlı irsaliyeler
      if (f.irsaliye_ids && f.irsaliye_ids.length > 0) {
        const irsBaslik = document.createElement('div');
        irsBaslik.style.cssText = 'font-size:11px;color:var(--muted2);text-transform:uppercase;letter-spacing:0.4px;margin:10px 0 5px;';
        irsBaslik.textContent = 'Bağlı İrsaliyeler (' + f.irsaliye_ids.length + ')';
        detayWrap.appendChild(irsBaslik);
        f.irsaliye_ids.forEach(function(id) {
          const row = document.createElement('div');
          row.style.cssText = 'font-size:12px;padding:5px 10px;background:var(--bg);border-radius:7px;margin-bottom:4px;color:var(--muted);';
          row.textContent = '📦 İrsaliye #' + id;
          detayWrap.appendChild(row);
        });
      }

      // ── EYLEM BUTONLARI: İptal / Güncelle / E-Fatura/E-Arşiv ─────
      const actionRow = document.createElement('div');
      actionRow.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;margin-top:14px;border-top:1px solid var(--border);padding-top:12px;';

      // E-Fatura / E-Arşiv: SADECE satış faturası için anlamlıdır (Paraşüt
      // resmileştirme akışı sales_invoices üzerinden çalışır).
      if (faturaTipi !== 'supplier') {
        const efBtn = document.createElement('button');
        efBtn.className = 'btn-primary';
        efBtn.style.fontSize = '12px';
        efBtn.textContent = '📤 E-Fatura/E-Arşiv';
        efBtn.onclick = function() { eFaturaFormGoster(String(faturaId)); };
        actionRow.appendChild(efBtn);
      }

      const guncelleBtn = document.createElement('button');
      guncelleBtn.className = 'btn-cancel';
      guncelleBtn.style.fontSize = '12px';
      guncelleBtn.textContent = '✏️ Güncelle';
      guncelleBtn.onclick = function() {
        faturaGuncelleFormGoster(String(faturaId), {
          aciklama: f.aciklama || '',
          tarih:    f.tarih    || '',
          vade:     f.vade     || '',
          tip:      faturaTipi,
        });
      };
      actionRow.appendChild(guncelleBtn);

      // İptal sadece ödenmemiş faturalar için anlamlı
      // (Tahsilatlar veya ödemeler varsa önce onlar silinmeli)
      const odemeListesi = f.tahsilatlar || f.odemeler || [];
      const iptalEdilebilir = (parseFloat(f.kalan || 0) > 0 || odemeListesi.length === 0);
      if (iptalEdilebilir) {
        const iptalBtn = document.createElement('button');
        iptalBtn.className = 'btn-cancel';
        iptalBtn.style.cssText = 'font-size:12px;color:#dc2626;border-color:#fecaca;';
        iptalBtn.textContent = '❌ Faturayı İptal Et';
        iptalBtn.onclick = function() { faturaIptalFormGoster(String(faturaId), faturaTipi); };
        actionRow.appendChild(iptalBtn);
      }

      detayWrap.appendChild(actionRow);

      const _baslikIcon = faturaTipi === 'supplier' ? '📥 Alış Faturası' : '🧾 Satış Faturası';
      addMessage(_baslikIcon + ' #' + faturaId, 'bot', detayWrap);
      sohbetPanelKaydet('fatura_detay', {
        fatura_id: String(faturaId),
        aciklama: faturaAdi || '',
        musteri: cariCtx || '',
        tip: faturaTipi,
      });
    } catch(e) {
      removeTyping();
      addMessage('❌ Fatura detayı yüklenemedi.', 'bot');
    }
  }

  // ── TOPLU FATURA ─────────────────────────────────────────────────

  async function topluFaturaGoster() {
    billablePanelTikla('toplu_fatura');

    // Default tarih aralığı: yılbaşı → bugün (geniş, kullanıcı daraltabilir)
    const _bugun = new Date();
    const _yilbasi = new Date(_bugun.getFullYear(), 0, 1);
    function _toYMD(d) {
      const y = d.getFullYear();
      const m = String(d.getMonth()+1).padStart(2, '0');
      const g = String(d.getDate()).padStart(2, '0');
      return y + '-' + m + '-' + g;
    }

    const wrap = document.createElement('div');
    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-size:13px;font-weight:500;color:var(--text);margin-bottom:10px;';
    baslik.textContent = '🧾 Toplu Fatura';
    wrap.appendChild(baslik);

    // Tarih aralığı satırı (bekleyenIrsaliyeleriGoster ile aynı UX)
    const tarihRow = document.createElement('div');
    tarihRow.style.cssText = 'display:flex;gap:6px;align-items:center;margin-bottom:10px;flex-wrap:wrap;';
    const inpBas = document.createElement('input');
    inpBas.type = 'date';
    inpBas.value = _toYMD(_yilbasi);
    inpBas.style.cssText = 'padding:6px 8px;border:1px solid var(--border);border-radius:7px;font-size:12px;';
    const inpBit = document.createElement('input');
    inpBit.type = 'date';
    inpBit.value = _toYMD(_bugun);
    inpBit.style.cssText = 'padding:6px 8px;border:1px solid var(--border);border-radius:7px;font-size:12px;';
    const tireSpan = document.createElement('span');
    tireSpan.style.cssText = 'color:var(--muted);font-size:12px;';
    tireSpan.textContent = '—';
    tarihRow.appendChild(inpBas);
    tarihRow.appendChild(tireSpan);
    tarihRow.appendChild(inpBit);
    wrap.appendChild(tarihRow);

    const topluInfo = document.createElement('div');
    topluInfo.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;padding:8px 12px;background:var(--accent-lt);border-radius:8px;border-left:3px solid var(--accent);';
    topluInfo.textContent = 'Toplu fatura kesmek istediğiniz müşteriyi seçin. Birden fazla irsaliyeyi tek faturada birleştirebilirsiniz.';
    wrap.appendChild(topluInfo);

    const contentDiv = document.createElement('div');
    wrap.appendChild(contentDiv);

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel';
    iptalBtn.style.marginTop = '8px';
    iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ İptal edildi.', 'bot'); };
    wrap.appendChild(iptalBtn);

    addMessage('', 'bot', wrap);
    sohbetPanelKaydet('toplu_fatura', {});

    async function _yukle() {
      contentDiv.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px;">⏳ İrsaliyeler yükleniyor…</div>';
      try {
        const qs = '?baslangic=' + inpBas.value + '&bitis=' + inpBit.value + '&sayfa_basi=25';
        const res = await fetch('/api/bekleyen-irsaliyeler' + qs);
        const data = await res.json();

        contentDiv.innerHTML = '';

        if (!data.irsaliyeler || data.irsaliyeler.length === 0) {
          contentDiv.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px;">📋 Bu tarih aralığında bekleyen irsaliye yok. Aralığı genişletin.</div>';
          return;
        }

        // Cari bazlı grupla
        const cariMap = {};
        data.irsaliyeler.forEach(function(irs) {
          if (!cariMap[irs.cari_adi]) cariMap[irs.cari_adi] = [];
          cariMap[irs.cari_adi].push(irs);
        });

        const cariListesi = Object.keys(cariMap).map(function(ad) {
          return { ad: ad, bakiye: 0, adet: cariMap[ad].length };
        });

        const ozet = document.createElement('div');
        ozet.style.cssText = 'font-size:11px;color:var(--muted);margin-bottom:8px;';
        ozet.textContent = data.toplam + ' irsaliye, ' + cariListesi.length + ' cari';
        contentDiv.appendChild(ozet);

        const { wrap: ddWrap } = cariSecimDropdown(cariListesi, function(c) {
          ddWrap.querySelector('input').disabled = true;
          topluFaturaIrsaliyeSeç(c.ad, cariMap[c.ad], wrap);
        }, 'Cari adı yazın...');
        contentDiv.appendChild(ddWrap);
        setTimeout(function() { ddWrap.querySelector('input').focus(); }, 100);
      } catch (e) {
        contentDiv.innerHTML = '<div style="font-size:12px;color:#dc2626;padding:8px;">❌ İrsaliyeler yüklenemedi.</div>';
      }
    }

    inpBas.onchange = _yukle;
    inpBit.onchange = _yukle;
    _yukle();
  }

  function topluFaturaIrsaliyeSeç(cariAdi, irsaliyeler, wrapParent) {
    const seçimWrap = document.createElement('div');
    seçimWrap.style.cssText = 'margin-top:12px;border-top:1px solid var(--border);padding-top:12px;';

    const label = document.createElement('div');
    label.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:8px;';
    label.textContent = cariAdi + ' — İrsaliye seçin:';
    seçimWrap.appendChild(label);

    const checkboxlar = [];
    irsaliyeler.forEach(function(irs) {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 10px;border:1.5px solid var(--border2);border-radius:9px;margin-bottom:6px;cursor:pointer;transition:all 0.15s;';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = true;
      cb.style.cssText = 'width:16px;height:16px;accent-color:var(--accent);cursor:pointer;';

      const info = document.createElement('div');
      info.style.cssText = 'flex:1;font-size:13px;';
      const plakaToplu = plakacikar(irs.aciklama);
      info.textContent = formatTarih(irs.tarih) + ' · ' + (irs.toplam_kg||0).toLocaleString('tr-TR') + ' kg' + (plakaToplu ? ' · 🚗 ' + plakaToplu : '');
      if (irs.kalemler && irs.kalemler.length > 1) {
        const alt = document.createElement('div');
        alt.style.cssText = 'font-size:11px;color:var(--muted);margin-top:2px;';
        alt.textContent = irs.kalemler.map(function(k) { return k.aciklama; }).join(', ');
        info.appendChild(alt);
      }

      row.appendChild(cb);
      row.appendChild(info);
      row.onclick = function(e) {
        // Checkbox'a direkt tıklanınca row.onclick'i tetikleme — çift toggle olmasın
        if (e.target === cb) return;
        cb.checked = !cb.checked;
        row.style.borderColor = cb.checked ? 'var(--accent)' : 'var(--border2)';
        row.style.background = cb.checked ? 'var(--accent-lt)' : '';
      };
      cb.onchange = function() {
        row.style.borderColor = cb.checked ? 'var(--accent)' : 'var(--border2)';
        row.style.background = cb.checked ? 'var(--accent-lt)' : '';
      };
      row.style.borderColor = 'var(--accent)';
      row.style.background = 'var(--accent-lt)';

      seçimWrap.appendChild(row);
      checkboxlar.push({ cb, irs });
    });

    const btnRow = document.createElement('div');
    btnRow.className = 'btn-row';
    btnRow.style.marginTop = '10px';

    const devamBtn = document.createElement('button');
    devamBtn.className = 'btn-primary';
    devamBtn.textContent = 'Devam →';
    devamBtn.onclick = function() {
      const secilen = checkboxlar.filter(function(x) { return x.cb.checked; }).map(function(x) { return x.irs; });
      if (secilen.length === 0) { addMessage('❌ En az bir irsaliye seçin.', 'bot'); return; }
      Array.from(seçimWrap.querySelectorAll('input,button')).forEach(function(el) { el.disabled = true; });
      addMessage('✅ ' + secilen.length + ' irsaliye seçildi — fiyat giriliyor…', 'user');
      topluFaturaFiyatAkisi(cariAdi, secilen, 0, []);
    };

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel';
    iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ İptal edildi.', 'bot'); };

    btnRow.appendChild(devamBtn);
    btnRow.appendChild(iptalBtn);
    seçimWrap.appendChild(btnRow);
    wrapParent.appendChild(seçimWrap);
  }

  let topluAkis = null;

  function topluFaturaFiyatAkisi(cariAdi, irsaliyeler, index, toplanmis) {
    if (index >= irsaliyeler.length) {
      // Tümü toplandı, fatura kes
      topluFaturaGonder(cariAdi, toplanmis);
      return;
    }
    const irs = irsaliyeler[index];
    const kalemler = irs.kalemler || [];

    topluAkis = {
      cariAdi, irsaliyeler, index, toplanmis,
      irs, kalemler,
      malzemeler: (function() { const obj = {}; kalemler.forEach(function(k) { obj[k.aciklama] = k.miktar_kg; }); return obj; })(),
      toplanan_fiyatlar: {},
      kalem_index: 0
    };

    if (kalemler.length > 1) {
      const ilk = kalemler[0];
      topluFiyatSor('🧾 [' + (index+1) + '/' + irsaliyeler.length + '] ' + irs.cari_adi + '\n' + ilk.aciklama + ' fiyatı?\n(' + ilk.miktar_kg.toLocaleString('tr-TR') + ' kg)');
    } else {
      topluFiyatSor('🧾 [' + (index+1) + '/' + irsaliyeler.length + '] ' + formatTarih(irs.tarih) + ' — ' + (irs.toplam_kg||0).toLocaleString('tr-TR') + ' kg\nBirim fiyat? (TL/kg)');
    }
  }

  function topluFiyatSor(mesaj) {
    const wrap = document.createElement('div');
    wrap.className = 'fiyat-input-wrap';
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'fiyat-input';
    inp.placeholder = 'Örn: 14.85 veya 14850';
    const btn = document.createElement('button');
    btn.className = 'btn-primary';
    btn.textContent = 'Gönder';
    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel';
    iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() {
      inp.disabled = true; btn.disabled = true; iptalBtn.disabled = true;
      topluAkis = null;
      addMessage('❌ Toplu fatura iptal edildi.', 'bot');
    };
    const fiyatHintT = document.createElement('div');
    fiyatHintT.style.cssText = 'font-size:10.5px;color:var(--muted);margin-top:5px;line-height:1.6;';
    fiyatHintT.innerHTML = '&#128161; Ondalık için <b>virgül</b>: 14,4458 &nbsp;&middot;&nbsp; Binlik için <b>nokta</b>: 14.850';

    const fiyatHataT = document.createElement('div');
    fiyatHataT.style.cssText = 'font-size:11px;color:#e53e3e;margin-top:5px;display:none;';
    fiyatHataT.textContent = 'Geçersiz fiyat. Örnek: 14,85 veya 14.850 veya 14,4458';

    const gonder = function() {
      const deger = inp.value.trim();
      if (!deger || !parseFiyat(deger)) {
        fiyatHataT.style.display = 'block';
        inp.style.borderColor = '#e53e3e';
        return;
      }
      fiyatHataT.style.display = 'none';
      inp.style.borderColor = '';
      inp.disabled = true; btn.disabled = true; iptalBtn.disabled = true;
      topluFiyatGirildi(deger);
    };
    btn.onclick = gonder;
    inp.onkeydown = function(e) { if (e.key === 'Enter') gonder(); };
    inp.oninput = function() { fiyatHataT.style.display = 'none'; inp.style.borderColor = ''; };
    wrap.appendChild(inp);
    wrap.appendChild(btn);
    wrap.appendChild(iptalBtn);
    wrap.appendChild(fiyatHintT);
    wrap.appendChild(fiyatHataT);
    addMessage(mesaj, 'bot', wrap);
    setTimeout(function() { inp.focus(); }, 100);
  }

  function topluFiyatGirildi(deger) {
    if (!topluAkis) return;
    addMessage(deger, 'user');
    const fiyat = parseFiyat(deger);
    if (!fiyat) {
      addMessage('❌ Geçersiz fiyat.', 'bot');
      topluFiyatSor('💰 Tekrar deneyin:');
      return;
    }
    const { cariAdi, irsaliyeler, index, toplanmis, irs, kalemler, malzemeler, toplanan_fiyatlar } = topluAkis;
    const kalem_index = topluAkis.kalem_index;

    if (kalemler.length > 1) {
      const mevcut = kalemler[kalem_index];
      toplanan_fiyatlar[mevcut.aciklama] = fiyat;
      const sonraki = kalem_index + 1;
      if (sonraki < kalemler.length) {
        topluAkis.kalem_index = sonraki;
        const sm = kalemler[sonraki];
        topluFiyatSor('✅ ' + mevcut.aciklama + ': ' + fiyatGoster(fiyat) + ' TL/kg\n\n💰 ' + sm.aciklama + ' fiyatı?\n(' + sm.miktar_kg.toLocaleString('tr-TR') + ' kg)');
      } else {
        const yeni = toplanmis.concat([{ parasut_irsaliye_id: irs.parasut_irsaliye_id, malzemeler, toplanan_fiyatlar, agirlik_kg: irs.toplam_kg }]);
        topluFaturaFiyatAkisi(cariAdi, irsaliyeler, index + 1, yeni);
      }
    } else {
      const yeni = toplanmis.concat([{ parasut_irsaliye_id: irs.parasut_irsaliye_id, malzemeler: {}, toplanan_fiyatlar: { 'hurda': fiyat }, agirlik_kg: irs.toplam_kg }]);
      topluFaturaFiyatAkisi(cariAdi, irsaliyeler, index + 1, yeni);
    }
  }

  async function topluFaturaGonder(cariAdi, irsaliyelerData) {
    showTyping();
    try {
      const res = await fetch('/api/toplu-fatura-kes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cari_adi: cariAdi, irsaliyeler: irsaliyelerData })
      });
      const data = await res.json();
      removeTyping();
      addMessage(data.cevap, 'bot');
      topluAkis = null;
    } catch {
      removeTyping();
      addMessage('❌ Toplu fatura kesilemedi, tekrar deneyin.', 'bot');
      topluAkis = null;
    }
  }

  // ── CARİ DETAY ───────────────────────────────────────────────────


  function irsaliyeSecildi(irs, wrap) {
    if (wrap) Array.from(wrap.querySelectorAll('button')).forEach(function(b) { b.disabled = true; });
    addMessage('✅ Seçildi: ' + irs.cari_adi + ' — ' + (irs.toplam_kg||0).toLocaleString('tr-TR') + ' kg', 'user');
    const kalemler = irs.kalemler || [];
    if (kalemler.length > 1) {
      faturaAkisi = {
        irs: irs,
        malzeme_listesi: kalemler.map(function(k) { return k.aciklama; }),
        malzemeler: (function() {
          const obj = {};
          kalemler.forEach(function(k) { obj[k.aciklama] = k.miktar_kg; });
          return obj;
        })(),
        toplanan_fiyatlar: {},
        index: 0,
        otomatik_kalan: null,
        otomatik_index: 0
      };
      const ilk = kalemler[0];
      fiyatSor(ilk.aciklama + ' fiyatı nedir?\n(' + ilk.miktar_kg.toLocaleString('tr-TR') + ' kg)\nÖrnek: 14.85 veya 14850');
    } else {
      faturaAkisi = { irs: irs, malzeme_listesi: [], malzemeler: {}, toplanan_fiyatlar: {}, index: 0, otomatik_kalan: null, otomatik_index: 0 };
      fiyatSor('💰 Birim fiyatı nedir? (TL/kg)\n⚖️ ' + (irs.toplam_kg||0).toLocaleString('tr-TR') + ' kg\nÖrnek: 14.85 veya 14850');
    }
  }

  function fiyatSor(mesaj) {
    const wrap = document.createElement('div');
    wrap.className = 'fiyat-input-wrap';

    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'fiyat-input';
    inp.placeholder = 'Örn: 14.85 veya 14850';

    const btn = document.createElement('button');
    btn.className = 'btn-primary';
    btn.textContent = 'Gönder';

    // ── İPTAL BUTONU ──
    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel';
    iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() {
      inp.disabled = true;
      btn.disabled = true;
      iptalBtn.disabled = true;
      faturaAkisi = null;
      addMessage('❌ Fatura işlemi iptal edildi.', 'bot');
    };

    // Hint notu
    const fiyatHint2 = document.createElement('div');
    fiyatHint2.style.cssText = 'font-size:10.5px;color:var(--muted);margin-top:5px;line-height:1.6;';
    fiyatHint2.innerHTML = '💡 Ondalık için <b>virgül</b>: 14,4458 &nbsp;·&nbsp; Binlik için <b>nokta</b>: 14.850';

    // Hata mesajı
    const fiyatHata2 = document.createElement('div');
    fiyatHata2.style.cssText = 'font-size:11px;color:#e53e3e;margin-top:5px;display:none;';
    fiyatHata2.textContent = '⚠️ Geçersiz fiyat. Örnek: 14,85 · 14.850 · 14,4458';

    const gonder = function() {
      const deger = inp.value.trim();
      if (!deger) {
        fiyatHata2.style.display = 'block';
        inp.style.borderColor = '#e53e3e';
        return;
      }
      const fiyat = parseFiyat(deger);
      if (!fiyat) {
        fiyatHata2.style.display = 'block';
        inp.style.borderColor = '#e53e3e';
        return;
      }
      fiyatHata2.style.display = 'none';
      inp.style.borderColor = '';
      inp.disabled = true;
      btn.disabled = true;
      iptalBtn.disabled = true;
      fiyatGirildi(deger);
    };
    btn.onclick = gonder;
    inp.onkeydown = function(e) { if (e.key === 'Enter') gonder(); };
    inp.oninput = function() { fiyatHata2.style.display = 'none'; inp.style.borderColor = ''; };

    wrap.appendChild(inp);
    wrap.appendChild(btn);
    wrap.appendChild(iptalBtn);
    wrap.appendChild(fiyatHint2);
    wrap.appendChild(fiyatHata2);
    addMessage(mesaj, 'bot', wrap);
    setTimeout(function() { inp.focus(); }, 100);
  }

  async function fiyatGirildi(deger) {
    if (!faturaAkisi) return;
    addMessage(deger, 'user');
    const fiyat = parseFiyat(deger);
    if (!fiyat) {
      addMessage('❌ Geçersiz fiyat. Örnek: 14.85 veya 14850', 'bot');
      fiyatSor('💰 Tekrar deneyin:');
      return;
    }
    const irs              = faturaAkisi.irs;
    const malzeme_listesi  = faturaAkisi.malzeme_listesi;
    const malzemeler       = faturaAkisi.malzemeler;
    const toplanan_fiyatlar = faturaAkisi.toplanan_fiyatlar;
    const index            = faturaAkisi.index;
    const otomatik_kalan   = faturaAkisi.otomatik_kalan;
    const otomatik_index   = faturaAkisi.otomatik_index;

    if (malzeme_listesi.length > 0) {
      const mevcut    = malzeme_listesi[index];
      const mevcut_kg = malzemeler[mevcut] || 0;
      toplanan_fiyatlar[mevcut] = fiyat;
      const sonraki = index + 1;
      if (sonraki < malzeme_listesi.length) {
        faturaAkisi.index = sonraki;
        const sm = malzeme_listesi[sonraki];
        const sk = malzemeler[sm] || 0;
        fiyatSor('✅ ' + mevcut + ' (' + mevcut_kg.toLocaleString('tr-TR') + ' kg): ' + fiyatGoster(fiyat) + ' TL/kg kaydedildi.\n\n💰 ' + sm + ' fiyatı nedir?\n(' + sk.toLocaleString('tr-TR') + ' kg)');
      } else {
        await faturaKesVeDevam(irs, malzemeler, toplanan_fiyatlar, null, otomatik_kalan, otomatik_index);
      }
    } else {
      await faturaKesVeDevam(irs, {}, { 'hurda': fiyat }, irs.toplam_kg, otomatik_kalan, otomatik_index);
    }
  }

  async function faturaKesVeDevam(irs, malzemeler, toplanan_fiyatlar, agirlik_kg, otomatik_kalan, otomatik_index) {
    showTyping();
    try {
      const res = await fetch('/api/fatura-kes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parasut_irsaliye_id: irs.parasut_irsaliye_id,
          cari_adi: irs.cari_adi,
          agirlik_kg: agirlik_kg || irs.toplam_kg,
          malzemeler: malzemeler,
          toplanan_fiyatlar: toplanan_fiyatlar
        })
      });
      const data = await res.json();
      removeTyping();
      addMessage(data.cevap, 'bot');
      faturaAkisi = null;
      if (!data.basarili) return;
      if (otomatik_kalan && otomatik_index < otomatik_kalan.length) {
        setTimeout(function() { tumunuSiralaKes(otomatik_kalan, otomatik_index); }, 400);
      } else {
        await sonrakiIrsaliyeSor();
      }
    } catch {
      removeTyping();
      addMessage('❌ Fatura kesilemedi, tekrar deneyin.', 'bot');
      faturaAkisi = null;
    }
  }

  async function sonrakiIrsaliyeSor() {
    try {
      const res = await fetch('/api/bekleyen-irsaliyeler');
      const data = await res.json();
      if (!data.irsaliyeler || data.irsaliyeler.length === 0) {
        addMessage('🎉 Tüm irsaliyeler faturalandı!', 'bot');
        return;
      }
      const kalan = data.irsaliyeler;
      const wrap = document.createElement('div');
      const bilgi = document.createElement('div');
      bilgi.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;';
      bilgi.textContent = kalan.length + ' irsaliye daha bekliyor. Ne yapalım?';
      wrap.appendChild(bilgi);
      const btnRow = document.createElement('div');
      btnRow.className = 'btn-row';

      const devamBtn = document.createElement('button');
      devamBtn.className = 'btn-primary';
      devamBtn.textContent = '▶ Sıradaki (' + kalan[0].cari_adi.split(' ')[0] + ')';
      devamBtn.onclick = function() {
        Array.from(btnRow.querySelectorAll('button')).forEach(function(b) { b.disabled = true; });
        irsaliyeSecildi(kalan[0], null);
      };

      const hepsiBtn = document.createElement('button');
      hepsiBtn.className = 'btn-primary';
      hepsiBtn.style.background = '#1a3d28';
      hepsiBtn.textContent = '⚡ Hepsini kes (' + kalan.length + ')';
      hepsiBtn.onclick = function() {
        Array.from(btnRow.querySelectorAll('button')).forEach(function(b) { b.disabled = true; });
        tumunuSiralaKes(kalan, 0);
      };

      const durBtn = document.createElement('button');
      durBtn.className = 'btn-secondary';
      durBtn.textContent = '⏸ Dur';
      durBtn.onclick = function() {
        Array.from(btnRow.querySelectorAll('button')).forEach(function(b) { b.disabled = true; });
        addMessage(kalan.length + ' irsaliye beklemede kaldı.', 'bot');
      };

      btnRow.appendChild(devamBtn);
      btnRow.appendChild(hepsiBtn);
      btnRow.appendChild(durBtn);
      wrap.appendChild(btnRow);
      addMessage('', 'bot', wrap);
    } catch {}
  }

  function tumunuSiralaKes(irsaliyeler, index) {
    if (index >= irsaliyeler.length) {
      addMessage('🎉 Tüm irsaliyeler faturalandı!', 'bot');
      return;
    }
    const irs = irsaliyeler[index];
    const plakaOto = plakacikar(irs.aciklama);
    addMessage('⚡ Otomatik: ' + irs.cari_adi + (plakaOto ? ' · 🚗 ' + plakaOto : '') + ' (' + (irs.toplam_kg||0).toLocaleString('tr-TR') + ' kg)', 'bot');
    const kalemler = irs.kalemler || [];
    if (kalemler.length > 1) {
      faturaAkisi = {
        irs: irs,
        malzeme_listesi: kalemler.map(function(k) { return k.aciklama; }),
        malzemeler: (function() {
          const obj = {};
          kalemler.forEach(function(k) { obj[k.aciklama] = k.miktar_kg; });
          return obj;
        })(),
        toplanan_fiyatlar: {},
        index: 0,
        otomatik_kalan: irsaliyeler,
        otomatik_index: index + 1
      };
      const ilk = kalemler[0];
      fiyatSor(irs.cari_adi + ' — ' + ilk.aciklama + ' fiyatı?\n(' + ilk.miktar_kg.toLocaleString('tr-TR') + ' kg)');
    } else {
      faturaAkisi = {
        irs: irs,
        malzeme_listesi: [],
        malzemeler: {},
        toplanan_fiyatlar: {},
        index: 0,
        otomatik_kalan: irsaliyeler,
        otomatik_index: index + 1
      };
      fiyatSor(irs.cari_adi + ' — birim fiyat? (TL/kg)\n⚖️ ' + (irs.toplam_kg||0).toLocaleString('tr-TR') + ' kg');
    }
  }

  // ── CARİ ÖN YÜKLEME ─────────────────────────────────────────────
  // Sayfa açılınca arka planda cari listesini çek ve cache'e al.
  // Böylece kullanıcı herhangi bir modülü açtığında bekleme olmaz.
  let _cariCache = null;
  let _cariCacheZamani = 0;
  const CARI_CACHE_SURE = 15 * 60 * 1000; // 15 dakika

