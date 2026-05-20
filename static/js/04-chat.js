// 04-chat.js -- sendMessage, quickSend, uploadFis, firmaSecildi

  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text || sendBtn.disabled) return;
    await ensureSessionReady();
    if (_sessionReplaced) {
      addMessage(SESSION_REPLACED_MESSAGE, 'bot');
      inputEl.value = '';
      return;
    }

    // 1. Önce frontend keyword eşleştirmesi — hızlı, API isteği yok
    const eslesen = mesajYonlendir(text);
    if (eslesen) {
      addMessage(text, 'user');
      inputEl.value = '';
      inputEl.style.height = 'auto';
      await eslesen.fn();
      if (eslesen.kind) sohbetKeywordVePanelKaydet(text, eslesen.kind, {});
      return;
    }

    // 2. Keyword eşleşmedi → Haiku'ya intent sorusu
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

      if (data && data.code === 'SESSION_REPLACED') {
        oturumDegistiUyarisi();
        return;
      }
      if (!res.ok) {
        addMessage(data.hata || '❌ İşlem yapılamadı, lütfen tekrar deneyin.', 'bot');
        return;
      }

      const intent = data.intent || 'none';
      const mesaj  = data.cevap  || '';
      const params = data.params || {};

      if (intent !== 'none' && INTENT_MAP[intent]) {
        // Haiku bir intent tespit etti → önce mesajı göster, sonra akışı tetikle
        if (mesaj) addMessage(mesaj, 'bot');
        await INTENT_MAP[intent](params);
      } else {
        // intent = "none" → sadece mesajı göster
        if (mesaj) addMessage(mesaj, 'bot');
      }

    } catch {
      removeTyping();
      addMessage('❌ Bağlantı hatası, lütfen tekrar deneyin.', 'bot');
    } finally {
      if (!_sessionReplaced) {
        sendBtn.disabled = false;
        inputEl.focus();
      }
    }
  }

  function quickSend(text) {
    inputEl.value = text;
    sendMessage();
  }

  // ── FİŞ YÜKLE ────────────────────────────────────────────────────

  async function uploadFis(input) {
    const file = input.files[0];
    if (!file) return;
    await ensureSessionReady();
    const reader = new FileReader();
    reader.onload = (e) => addImageMessage(e.target.result);
    reader.readAsDataURL(file);
    sendBtn.disabled = true;
    showTyping();
    const formData = new FormData();
    formData.append('file', file);
    formData.append('session_id', SESSION_ID);
    try {
      const res = await fetch('/api/upload', { method: 'POST', body: formData });
      const data = await res.json();
      removeTyping();
      if (data.bekliyor === 'firma_sec') {
        const wrap = document.createElement('div');
        wrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;min-width:280px;max-width:440px;';

        // Shared state
        const firmaCariler = (data.firma_listesi || []).map(function(f) { return { ad: f, bakiye: 0 }; });
        let secilenFirma = '';
        // Düzenleme alanları (form açıldığında oluşur)
        let plakaInp, fisNoInp, agirlikInp, fireInp, tarihInp, malzRows = [], malzContainer;

        // ── CONFIDENCE YARDIMCI ──────────────────────────────────────
        // Kritik alan eşikleri: >0.85 yeşil, 0.40-0.85 sarı, ≤0.40 kırmızı
        const CONF_GREEN  = 0.85;
        const CONF_YELLOW = 0.40;
        function confLevel(c) {
          if (c == null) return 'none';
          if (c > CONF_GREEN)  return 'green';
          if (c > CONF_YELLOW) return 'yellow';
          return 'red';
        }
        function applyConfStyle(inp, level) {
          if (level === 'green' || level === 'none') {
            inp.style.borderColor = '';
            inp.style.background  = '';
          } else if (level === 'yellow') {
            inp.style.borderColor = '#f59e0b';
            inp.style.background  = 'rgba(245,158,11,0.08)';
          } else { // red
            inp.style.borderColor = '#ef4444';
            inp.style.background  = 'rgba(239,68,68,0.08)';
          }
        }
        // Kırmızı olup henüz düzenlenmemiş alan sayısı — 0 olunca Kaydet açılır
        let kirmizi_duzenlenmemis = 0;

        // Kritik alan (plaka veya ağırlık) kırmızıysa form otomatik açılır
        const kritikKirmizi = confLevel(data.conf_plaka) === 'red' || confLevel(data.conf_agirlik) === 'red';
        const herhangiBirKirmizi = kritikKirmizi || confLevel(data.conf_fis_no) === 'red';

        // ── HIZLI MOD ────────────────────────────────────────────────
        const hizliWrap = document.createElement('div');
        hizliWrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;';

        if (data.plaka_uyari) {
          const uyari = document.createElement('div');
          uyari.style.cssText = 'color:#f59e0b;font-size:12px;padding:4px 8px;background:rgba(245,158,11,0.12);border-radius:6px;';
          uyari.textContent = data.plaka_uyari;
          hizliWrap.appendChild(uyari);
        }
        if (data.firma_uyari) {
          const fuyari = document.createElement('div');
          fuyari.style.cssText = 'color:#f59e0b;font-size:12px;padding:4px 8px;background:rgba(245,158,11,0.12);border-radius:6px;';
          fuyari.textContent = data.firma_uyari;
          hizliWrap.appendChild(fuyari);
        }

        // Confidence özet banner
        if (data.conf_genel != null) {
          const lvl = confLevel(data.conf_genel);
          if (lvl !== 'green') {
            const confBanner = document.createElement('div');
            const pct = Math.round((data.conf_genel || 0) * 100);
            const icon   = lvl === 'red' ? '🔴' : '🟡';
            const renk   = lvl === 'red' ? '#ef4444' : '#f59e0b';
            const bg     = lvl === 'red' ? 'rgba(239,68,68,0.08)' : 'rgba(245,158,11,0.08)';
            const mesaj  = lvl === 'red'
              ? `OCR güven skoru düşük (%${pct}) — kırmızı alanları düzeltin.`
              : `OCR güven skoru orta (%${pct}) — sarı alanları kontrol edin.`;
            confBanner.style.cssText = `font-size:12px;padding:5px 9px;border-radius:6px;color:${renk};background:${bg};border:1px solid ${renk};`;
            confBanner.textContent = icon + ' ' + mesaj;
            hizliWrap.appendChild(confBanner);
          }
        }

        const { wrap: ddWrap, inp: ddInp } = cariSecimDropdown(firmaCariler, function(c) {
          secilenFirma = c.ad;
        }, 'Firma adı yazın...');
        hizliWrap.appendChild(ddWrap);

        const hizliBtnRow = document.createElement('div');
        hizliBtnRow.style.cssText = 'display:flex;gap:6px;';

        const onayHizliBtn = document.createElement('button');
        onayHizliBtn.style.cssText = 'flex:1;padding:7px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;background:var(--accent,#6366f1);color:#fff;border:none;';
        onayHizliBtn.textContent = '✓ İrsaliye Oluştur';

        const duzleBtn = document.createElement('button');
        duzleBtn.style.cssText = 'padding:7px 12px;border-radius:8px;font-size:12px;cursor:pointer;border:1px solid var(--border);background:transparent;color:var(--muted);';
        duzleBtn.textContent = '✏️ Düzenle';

        const iptalHizliBtn = document.createElement('button');
        iptalHizliBtn.className = 'btn-cancel';
        iptalHizliBtn.style.cssText = 'padding:7px 12px;border-radius:8px;font-size:12px;cursor:pointer;';
        iptalHizliBtn.textContent = '✕';
        iptalHizliBtn.onclick = async function() {
          onayHizliBtn.disabled = true; duzleBtn.disabled = true; iptalHizliBtn.disabled = true;
          // Backend'e iptal bilgisi gönder — duplicate kontrolünde dikkate alınmasın
          try {
            await fetch('/api/fis-iptal', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ ticket_id: data.ticket_id }),
            });
          } catch (e) { /* sessizce geç */ }
          addMessage('❌ Fiş işlemi iptal edildi.', 'bot');
        };

        onayHizliBtn.onclick = function() {
          const firma = secilenFirma || ddInp.value.trim();
          if (!firma) { alert('Lütfen firma seçin.'); return; }
          onayHizliBtn.disabled = true; duzleBtn.disabled = true; iptalHizliBtn.disabled = true;
          firmaSecildi(data.ticket_id, firma, data.malzemeler || {}, null, wrap);
        };

        // ── DÜZENLEME FORMU (gizli, "Düzenle" ile açılır) ────────────
        const formWrap = document.createElement('div');
        formWrap.style.cssText = 'display:none;flex-direction:column;gap:6px;border-top:1px solid var(--border);padding-top:8px;margin-top:2px;';

        function labeledRow(emoji, label, val, type, conf) {
          const row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:center;gap:6px;';
          const lbl = document.createElement('span');
          lbl.style.cssText = 'font-size:12px;color:var(--muted);width:130px;flex-shrink:0;';
          lbl.textContent = emoji + ' ' + label;
          const inp = document.createElement('input');
          inp.type = type || 'text'; inp.value = val;
          inp.style.cssText = 'flex:1;padding:5px 8px;border:1px solid var(--border);border-radius:6px;background:var(--input-bg,var(--bg));color:var(--text);font-size:13px;transition:border-color 0.2s,background 0.2s;';

          const level = confLevel(conf);
          applyConfStyle(inp, level);

          // Kırmızı alan: sayaç artır, kullanıcı düzenlince temizle
          if (level === 'red') {
            kirmizi_duzenlenmemis++;
            inp._conf_kirmizi = true;
            inp.addEventListener('input', function() {
              if (inp._conf_kirmizi) {
                inp._conf_kirmizi = false;
                kirmizi_duzenlenmemis = Math.max(0, kirmizi_duzenlenmemis - 1);
                applyConfStyle(inp, 'none');
                _guncelleSaveLock();
              }
            }, { once: true });
          }

          // Confidence badge (küçük yüzde etiketi)
          if (conf != null && level !== 'none') {
            const badge = document.createElement('span');
            const pct = Math.round(conf * 100);
            const bc  = level === 'green' ? '#22c55e' : level === 'yellow' ? '#f59e0b' : '#ef4444';
            badge.style.cssText = `font-size:10px;color:${bc};opacity:0.85;white-space:nowrap;`;
            badge.textContent = pct + '%';
            row.appendChild(lbl); row.appendChild(inp); row.appendChild(badge);
            return { row, inp };
          }

          row.appendChild(lbl); row.appendChild(inp);
          return { row, inp };
        }

        function _guncelleSaveLock() {
          // Düzenleme formundaki Kaydet butonu: kırmızı düzenlenmemiş alan varsa kilitli
          if (onayHizliBtn && kirmizi_duzenlenmemis > 0) {
            onayHizliBtn.disabled = true;
            onayHizliBtn.title = 'Kırmızı alanları düzeltin, sonra kaydedebilirsiniz.';
          } else if (onayHizliBtn) {
            onayHizliBtn.disabled = false;
            onayHizliBtn.title = '';
          }
        }

        duzleBtn.onclick = function() {
          if (formWrap.style.display === 'none') {
            // Form henüz oluşturulmadıysa oluştur
            if (!plakaInp) {
              const r1 = labeledRow('🚗', 'Plaka',             data.ocr_plaka    || '',  'text',   data.conf_plaka);
              const r2 = labeledRow('📋', 'Fiş No',             data.ocr_fis_no   || '',  'text',   data.conf_fis_no);
              const r3 = labeledRow('⚖️', 'Net Ağırlık (kg)',  data.ocr_agirlik_kg != null ? String(data.ocr_agirlik_kg) : '', 'number', data.conf_agirlik);
              const r4 = labeledRow('🔥', 'Fire (kg)',           data.ocr_fire_kg  != null ? String(data.ocr_fire_kg)  : '0',  'number', null);
              const r5 = labeledRow('📅', 'Tarih (GG.AA.YYYY)', data.ocr_tarih    || '',  'text',   null);
              plakaInp = r1.inp; fisNoInp = r2.inp; agirlikInp = r3.inp; fireInp = r4.inp; tarihInp = r5.inp;
              [r1,r2,r3,r4,r5].forEach(function(r) { formWrap.appendChild(r.row); });

              // Malzemeler
              const malzLbl = document.createElement('div');
              malzLbl.style.cssText = 'font-size:12px;color:var(--muted);margin-top:2px;';
              malzLbl.textContent = '📦 Malzemeler';
              formWrap.appendChild(malzLbl);
              malzContainer = document.createElement('div');
              malzContainer.style.cssText = 'display:flex;flex-direction:column;gap:4px;';

              function addMalzRow(ad, kg) {
                const row = document.createElement('div');
                row.style.cssText = 'display:flex;gap:5px;align-items:center;';
                const adInp = document.createElement('input');
                adInp.type = 'text'; adInp.value = ad; adInp.placeholder = 'Malzeme adı';
                adInp.style.cssText = 'flex:1;padding:4px 7px;border:1px solid var(--border);border-radius:6px;font-size:12px;background:var(--input-bg,var(--bg));color:var(--text);';
                const kgInp = document.createElement('input');
                kgInp.type = 'number'; kgInp.value = kg; kgInp.placeholder = 'kg';
                kgInp.style.cssText = 'width:78px;padding:4px 7px;border:1px solid var(--border);border-radius:6px;font-size:12px;background:var(--input-bg,var(--bg));color:var(--text);';
                const delBtn = document.createElement('button');
                delBtn.textContent = '×';
                delBtn.style.cssText = 'padding:2px 8px;border:1px solid var(--border);border-radius:6px;background:transparent;color:var(--muted);cursor:pointer;font-size:15px;line-height:1;';
                const rowObj = { adInp, kgInp };
                delBtn.onclick = function() { malzContainer.removeChild(row); malzRows.splice(malzRows.indexOf(rowObj), 1); };
                row.appendChild(adInp); row.appendChild(kgInp); row.appendChild(delBtn);
                malzContainer.appendChild(row);
                malzRows.push(rowObj);
              }
              Object.entries(data.malzemeler || {}).forEach(function(e) { addMalzRow(e[0], e[1]); });
              formWrap.appendChild(malzContainer);
              const ekleBtn = document.createElement('button');
              ekleBtn.textContent = '+ Malzeme Ekle';
              ekleBtn.style.cssText = 'align-self:flex-start;padding:3px 10px;border:1px dashed var(--border);border-radius:6px;background:transparent;color:var(--muted);cursor:pointer;font-size:12px;';
              ekleBtn.onclick = function() { addMalzRow('', 0); };
              formWrap.appendChild(ekleBtn);
            }
            formWrap.style.display = 'flex';
            duzleBtn.textContent = '▲ Gizle';
            // Kırmızı alan varsa Kaydet butonunu kilitle
            _guncelleSaveLock();
            // Hızlı "İrsaliye Oluştur" butonunu "Kaydet ve Oluştur" yap
            onayHizliBtn.textContent = '✓ Kaydet ve İrsaliye Oluştur';
            onayHizliBtn.onclick = function() {
              const firma = secilenFirma || ddInp.value.trim();
              if (!firma) { alert('Lütfen firma seçin.'); return; }
              const malzOzet = {};
              malzRows.forEach(function(r) {
                const ad = r.adInp.value.trim(); const kg = parseInt(r.kgInp.value) || 0;
                if (ad && kg > 0) malzOzet[ad] = kg;
              });
              onayHizliBtn.disabled = true; duzleBtn.disabled = true; iptalHizliBtn.disabled = true;
              firmaSecildi(data.ticket_id, firma, malzOzet, {
                plaka:      plakaInp.value.trim().toUpperCase().replace(/[\s\-]/g, ''),
                fis_no:     fisNoInp.value.trim(),
                agirlik_kg: parseInt(agirlikInp.value) || 0,
                fire_kg:    parseInt(fireInp.value) || 0,
                tarih:      tarihInp.value.trim(),
              }, wrap);
            };
          } else {
            formWrap.style.display = 'none';
            duzleBtn.textContent = '✏️ Düzenle';
            onayHizliBtn.textContent = '✓ İrsaliye Oluştur';
            onayHizliBtn.disabled = false;  // Hızlı modda kilit yok
            onayHizliBtn.title = '';
            onayHizliBtn.onclick = function() {
              const firma = secilenFirma || ddInp.value.trim();
              if (!firma) { alert('Lütfen firma seçin.'); return; }
              onayHizliBtn.disabled = true; duzleBtn.disabled = true; iptalHizliBtn.disabled = true;
              firmaSecildi(data.ticket_id, firma, data.malzemeler || {}, null, wrap);
            };
          }
        };

        hizliBtnRow.appendChild(onayHizliBtn);
        hizliBtnRow.appendChild(duzleBtn);
        hizliBtnRow.appendChild(iptalHizliBtn);
        hizliWrap.appendChild(hizliBtnRow);

        wrap.appendChild(hizliWrap);
        wrap.appendChild(formWrap);

        // ── KRİTİK KIRMIZI → FORMU OTOMATİK AÇ ────────────────────
        if (kritikKirmizi) {
          // Sanki Düzenle'ye basılmış gibi formu hemen aç
          setTimeout(function() { duzleBtn.onclick && duzleBtn.onclick(); }, 0);
        }

        addMessage(data.cevap, 'bot', wrap);
        sohbetParcaKaydet('📎 Fiş fotoğrafı', data.cevap);
        setTimeout(function() { ddInp.focus(); }, 150);
      } else {
        addMessage(data.cevap, 'bot');
        sohbetParcaKaydet('📎 Fiş fotoğrafı', data.cevap);
      }
      inputEl.focus();
    } catch {
      removeTyping();
      addMessage('❌ Yükleme hatası, tekrar deneyin.', 'bot');
    } finally {
      sendBtn.disabled = false;
      input.value = '';
    }
  }

  // ── FİRMA SEÇ ────────────────────────────────────────────────────

  async function firmaSecildi(ticketId, firma, malzemeler, duzeltmeler, wrap) {
    if (wrap) Array.from(wrap.querySelectorAll('button')).forEach(function(b) { b.disabled = true; });
    addMessage('🏭 ' + firma, 'user');
    showTyping();
    try {
      const body = Object.assign(
        { ticket_id: ticketId, firma: firma, malzemeler: malzemeler, session_id: SESSION_ID },
        duzeltmeler || {}
      );
      const res = await fetch('/api/firma-sec', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const data = await res.json();
      removeTyping();
      addMessage(data.cevap, 'bot');
      sohbetParcaKaydet('🏭 ' + firma, data.cevap);
    } catch {
      removeTyping();
      addMessage('❌ Hata oluştu, tekrar deneyin.', 'bot');
    }
    inputEl.focus();
  }

  // ── BEKLEYENİRSALİYELER ──────────────────────────────────────────

