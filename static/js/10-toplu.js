// 10-toplu.js -- Toplu fiş içe alma (Excel/PDF) -> kontrol/mutabakat -> toplu irsaliye

  function _kgFmt(n) { return Number(n || 0).toLocaleString('tr-TR'); }

  function topluFisGoster() {
    const inp = document.getElementById('topluFileInput');
    if (inp) { inp.value = ''; inp.click(); }
  }

  async function topluDosyaSec(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    addMessage('📑 ' + file.name, 'user');
    const bubble = addMessage('⏳ Dosya işleniyor, sistemle karşılaştırılıyor…', 'bot');
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/toplu/onizle', { method: 'POST', body: fd });
      const data = await r.json();
      if (!data.basarili) {
        bubble.textContent = '❌ ' + (data.hata || 'Dosya okunamadı.');
        return;
      }
      bubble.textContent = '';
      _topluKontrolRender(bubble, data);
    } catch (e) {
      bubble.textContent = '❌ Hata: ' + e;
    }
  }

  function _topluKontrolRender(bubble, data) {
    const o = data.ozet || {};
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:10px;min-width:0;';

    // ── Başlık + özet ──
    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-weight:600;font-size:14px;';
    baslik.textContent = '📑 ' + (data.dosya || 'Toplu liste') + ' — ' + (o.toplam || 0) + ' satır';
    wrap.appendChild(baslik);

    const ozet = document.createElement('div');
    ozet.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;font-size:12px;';
    function chip(txt, bg, fg) {
      const c = document.createElement('span');
      c.style.cssText = `padding:3px 9px;border-radius:100px;background:${bg};color:${fg};font-weight:500;`;
      c.textContent = txt;
      return c;
    }
    ozet.appendChild(chip('🆕 ' + (o.yeni || 0) + ' yeni', 'rgba(34,197,94,0.15)', '#16a34a'));
    ozet.appendChild(chip('✅ ' + (o.mevcut || 0) + ' mevcut', 'rgba(100,116,139,0.15)', '#64748b'));
    if (o.uyusmazlik) ozet.appendChild(chip('⚠️ ' + o.uyusmazlik + ' uyuşmazlık', 'rgba(245,158,11,0.18)', '#d97706'));
    if (o.fis_no_yok) ozet.appendChild(chip('❔ ' + o.fis_no_yok + ' fiş no yok', 'rgba(239,68,68,0.12)', '#dc2626'));
    ozet.appendChild(chip('Σ ' + _kgFmt(o.toplam_net_kg) + ' kg', 'rgba(59,130,246,0.12)', '#2563eb'));
    wrap.appendChild(ozet);

    // ── Cari + tarih kontrolleri ──
    const ctrl = document.createElement('div');
    ctrl.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px;border:1px solid var(--border);border-radius:8px;background:var(--bg);';

    const cariInp = document.createElement('input');
    cariInp.type = 'text';
    cariInp.placeholder = 'Cari (firma) seç/yaz…';
    cariInp.setAttribute('list', 'topluCariList');
    cariInp.className = 'fiyat-input';
    cariInp.style.cssText = 'flex:1;min-width:160px;';
    const dl = document.createElement('datalist');
    dl.id = 'topluCariList';
    cariInp.appendChild(dl);
    fetch('/api/cariler').then(r => r.json()).then(d => {
      (d.cariler || []).forEach(c => {
        const op = document.createElement('option');
        op.value = c.ad;
        dl.appendChild(op);
      });
    }).catch(() => {});

    const tarihInp = document.createElement('input');
    tarihInp.type = 'date';
    tarihInp.className = 'fiyat-input';
    tarihInp.style.cssText = 'width:150px;';
    tarihInp.value = new Date().toISOString().slice(0, 10);

    const cariLbl = document.createElement('span');
    cariLbl.style.cssText = 'font-size:12px;color:var(--muted);';
    cariLbl.textContent = '🏢';
    const tarLbl = document.createElement('span');
    tarLbl.style.cssText = 'font-size:12px;color:var(--muted);';
    tarLbl.textContent = '📅';
    ctrl.appendChild(cariLbl); ctrl.appendChild(cariInp);
    ctrl.appendChild(tarLbl); ctrl.appendChild(tarihInp);
    wrap.appendChild(ctrl);

    // ── Tablo ──
    const tableWrap = document.createElement('div');
    tableWrap.style.cssText = 'max-height:340px;overflow:auto;border:1px solid var(--border);border-radius:8px;';
    const tbl = document.createElement('table');
    tbl.style.cssText = 'width:100%;border-collapse:collapse;font-size:12px;';
    const thead = document.createElement('thead');
    thead.innerHTML = '<tr style="position:sticky;top:0;background:var(--bg2,#f1f5f9);">' +
      '<th style="padding:6px 8px;"><input type="checkbox" id="topluHepsi"></th>' +
      '<th style="padding:6px 8px;text-align:left;">Fiş No</th>' +
      '<th style="padding:6px 8px;text-align:left;">Plaka</th>' +
      '<th style="padding:6px 8px;text-align:left;">Şoför</th>' +
      '<th style="padding:6px 8px;text-align:right;">Net (kg)</th>' +
      '<th style="padding:6px 8px;text-align:center;">Durum</th></tr>';
    tbl.appendChild(thead);
    const tbody = document.createElement('tbody');

    const secimler = [];
    (data.satirlar || []).forEach(s => {
      const tr = document.createElement('tr');
      tr.style.cssText = 'border-top:1px solid var(--border);';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      // yeni & fis_no_yok varsayılan seçili; mevcut kilitli; uyuşmazlık opsiyonel
      if (s.durum === 'mevcut') { cb.checked = false; cb.disabled = true; }
      else if (s.durum === 'uyusmazlik') { cb.checked = false; }
      else { cb.checked = true; }
      const durumRenk = { yeni: '#16a34a', mevcut: '#64748b', uyusmazlik: '#d97706', fis_no_yok: '#dc2626' }[s.durum] || '#64748b';
      const durumTxt = { yeni: '🆕 Yeni', mevcut: '✅ Mevcut', uyusmazlik: '⚠️ Uyuşmazlık', fis_no_yok: '❔ Fiş no yok' }[s.durum] || s.durum;
      const uyari = (s.durum === 'uyusmazlik' && s.mevcut_net != null)
        ? `<div style="font-size:10px;color:#d97706;">sistemde: ${_kgFmt(s.mevcut_net)} kg</div>` : '';
      const td = (html, align) => `<td style="padding:5px 8px;text-align:${align || 'left'};">${html}</td>`;
      tr.innerHTML =
        '<td style="padding:5px 8px;text-align:center;"></td>' +
        td(s.fis_no || '-') + td(s.plaka || '-') + td(s.sofor || '-') +
        td(_kgFmt(s.net_kg), 'right') +
        td(`<span style="color:${durumRenk};font-weight:500;">${durumTxt}</span>${uyari}`, 'center');
      tr.children[0].appendChild(cb);
      tbody.appendChild(tr);
      secimler.push({ cb, satir: s });
    });
    tbl.appendChild(tbody);
    tableWrap.appendChild(tbl);
    wrap.appendChild(tableWrap);

    // "hepsini seç" toggle (kilitli olanlar hariç)
    setTimeout(() => {
      const hepsi = document.getElementById('topluHepsi');
      if (hepsi) hepsi.onclick = () => secimler.forEach(x => { if (!x.cb.disabled) x.cb.checked = hepsi.checked; });
    }, 0);

    // ── Aksiyon + sonuç ──
    const sonucEl = document.createElement('div');
    sonucEl.style.cssText = 'font-size:12.5px;';

    const btn = document.createElement('button');
    btn.textContent = '✓ Seçili satırlar için İrsaliye Oluştur';
    btn.className = 'fiyat-input';
    btn.style.cssText = 'padding:9px 14px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer;align-self:flex-start;';
    btn.onclick = async () => {
      const cari = cariInp.value.trim();
      if (!cari) { alert('Lütfen cari (firma) seçin.'); cariInp.focus(); return; }
      const secili = secimler.filter(x => x.cb.checked)
        .map(x => ({ fis_no: x.satir.fis_no, plaka: x.satir.plaka, net_kg: x.satir.net_kg }));
      if (!secili.length) { alert('Hiç satır seçilmedi.'); return; }
      if (!confirm(secili.length + ' satır için "' + cari + '" cariye irsaliye oluşturulacak. Onaylıyor musunuz?')) return;
      btn.disabled = true; btn.textContent = '⏳ Oluşturuluyor…';
      try {
        const r = await fetch('/api/toplu/uygula', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ satirlar: secili, cari_adi: cari, tarih: tarihInp.value, resolver_tipi: 'tek_cari' })
        });
        const res = await r.json();
        const ro = res.ozet || {};
        let html = `<div style="margin-top:8px;padding:10px;border-radius:8px;background:rgba(34,197,94,0.1);">` +
          `<b>✅ ${ro.olusturuldu || 0} irsaliye oluşturuldu</b>` +
          (ro.atlandi ? ` · ${ro.atlandi} atlandı` : '') +
          (ro.hata ? ` · <span style="color:#dc2626;">${ro.hata} hata</span>` : '') + '</div>';
        const hatalar = (res.sonuclar || []).filter(x => x.durum === 'hata');
        if (hatalar.length) {
          html += '<div style="margin-top:6px;font-size:11.5px;color:#dc2626;">';
          hatalar.slice(0, 12).forEach(h => { html += `• ${h.fis_no || h.plaka || '-'}: ${h.hata}<br>`; });
          html += '</div>';
        }
        if (ro.olusturuldu) html += '<div style="margin-top:6px;color:var(--muted);">💡 Fiyatlandırmak için <b>Bekleyen irsaliyeler → Toplu fatura</b> ekranını kullanın.</div>';
        sonucEl.innerHTML = html;
        btn.textContent = '✓ Tamamlandı';
      } catch (e) {
        sonucEl.innerHTML = '<div style="color:#dc2626;margin-top:8px;">❌ Hata: ' + e + '</div>';
        btn.disabled = false; btn.textContent = '✓ Tekrar dene';
      }
    };
    wrap.appendChild(btn);
    wrap.appendChild(sonucEl);

    bubble.appendChild(wrap);
  }
