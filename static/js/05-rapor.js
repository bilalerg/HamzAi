// 05-rapor.js -- Raporlar (gunluk, son fisler, hesaplar, stok, gelir/gider, bekleyenler, bakiyeler, hesap ozeti)

  async function gunlukOzetGoster() {
    billablePanelTikla('gunluk_ozet');
    showTyping();
    try {
      const res = await fetch('/api/gunluk-ozet');
      const d = await res.json();
      removeTyping();
      const durum = d.bekleyen > 0
        ? `⚠️ ${d.bekleyen} fiş bekliyor`
        : '✅ Tüm fişler tamamlandı';
      const wrap = document.createElement('div');
      wrap.style.cssText = 'font-size:13px;line-height:2;';
      wrap.innerHTML =
        `<b>📈 ${d.tarih} Günlük Özet</b><br>` +
        `📋 Yüklenen fiş: <b>${d.fis_sayisi}</b><br>` +
        `⚖️ Toplam ağırlık: <b>${(d.toplam_kg||0).toLocaleString('tr-TR')} kg</b><br>` +
        `✅ Tamamlanan: <b>${d.tamamlanan}</b><br>` +
        `🕐 Bekleyen: <b>${d.bekleyen}</b><br>` +
        `${durum}`;
      addMessage('', 'bot', wrap);
      sohbetPanelKaydet('gunluk_ozet', {});
    } catch {
      removeTyping();
      addMessage('❌ Günlük özet yüklenemedi.', 'bot');
    }
  }

  // ── SON FİŞLER ───────────────────────────────────────────────────

  async function sonFisleriGoster() {
    billablePanelTikla('son_fisler');
    showTyping();
    try {
      const res = await fetch('/api/son-fisler');
      const d = await res.json();
      removeTyping();
      if (!d.fisler || d.fisler.length === 0) {
        addMessage('📂 Henüz fiş yüklenmemiş.', 'bot');
        return;
      }
      const wrap = document.createElement('div');
      const baslik = document.createElement('div');
      baslik.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:8px;';
      baslik.textContent = 'Son ' + d.fisler.length + ' yüklenen fiş:';
      wrap.appendChild(baslik);
      d.fisler.forEach(function(f) {
        const row = document.createElement('div');
        row.style.cssText = 'padding:8px 12px;background:var(--bg);border-radius:9px;margin-bottom:5px;font-size:12px;';
        const statusEmoji = f.status === 'TAMAMLANDI' ? '✅' : f.status === 'HATA' ? '❌' : '🕐';
        row.innerHTML =
          `<div style="display:flex;justify-content:space-between;margin-bottom:2px;">` +
          `<b style="font-size:13px;">${f.plaka}</b>` +
          `<span style="color:var(--muted)">${f.created_at}</span></div>` +
          `<div style="color:var(--muted)">${f.firma} · ${(f.agirlik_kg||0).toLocaleString('tr-TR')} kg · ${statusEmoji}</div>`;
        wrap.appendChild(row);
      });
      addMessage('🗂️ Son yüklenen fişler:', 'bot', wrap);
      sohbetPanelKaydet('son_fisler', {});
    } catch {
      removeTyping();
      addMessage('❌ Fişler yüklenemedi.', 'bot');
    }
  }

  // ── HESAPLAR (KASA/BANKA) ─────────────────────────────────────────

  async function hesaplarGoster() {
    billablePanelTikla('hesaplar');
    showTyping();
    try {
      const res = await fetch('/api/hesaplar');
      const d = await res.json();
      removeTyping();
      if (!d.hesaplar || d.hesaplar.length === 0) {
        addMessage('🏦 Kayıtlı hesap bulunamadı.', 'bot');
        return;
      }
      const wrap = document.createElement('div');
      wrap.style.cssText = 'font-size:13px;';
      const baslik = document.createElement('div');
      baslik.style.cssText = 'font-weight:500;margin-bottom:10px;';
      baslik.textContent = '🏦 Kasa / Banka Hesapları';
      wrap.appendChild(baslik);
      d.hesaplar.forEach(function(h) {
        const row = document.createElement('div');
        row.style.cssText = 'padding:8px 12px;background:var(--bg);border-radius:9px;margin-bottom:5px;display:flex;justify-content:space-between;align-items:center;';
        const tip = h.account_type === 'cash' ? '💵' : '🏦';
        const bakiye = (h.balance || 0).toLocaleString('tr-TR', {minimumFractionDigits:2});
        row.innerHTML = `<div>${tip} <b>${h.name}</b><div style="font-size:11px;color:var(--muted)">${h.currency || 'TRL'}</div></div><div style="font-weight:600;">${bakiye} ₺</div>`;
        wrap.appendChild(row);
      });
      addMessage('', 'bot', wrap);
      sohbetPanelKaydet('hesaplar', {});
    } catch {
      removeTyping();
      addMessage('❌ Hesaplar yüklenemedi.', 'bot');
    }
  }

  // ── STOK HAREKETLERİ ──────────────────────────────────────────────

  async function stokHareketleriGoster() {
    billablePanelTikla('stok_hareketleri');
    showTyping();
    try {
      const res = await fetch('/api/stok-hareketleri?limit=20');
      const d = await res.json();
      removeTyping();
      if (!d.hareketler || d.hareketler.length === 0) {
        addMessage('📦 Stok hareketi bulunamadı.', 'bot');
        return;
      }
      const wrap = document.createElement('div');
      wrap.style.cssText = 'font-size:13px;';
      const baslik = document.createElement('div');
      baslik.style.cssText = 'font-weight:500;margin-bottom:10px;';
      baslik.textContent = '📦 Son Stok Hareketleri';
      wrap.appendChild(baslik);
      d.hareketler.forEach(function(h) {
        const row = document.createElement('div');
        row.style.cssText = 'padding:8px 12px;background:var(--bg);border-radius:9px;margin-bottom:5px;font-size:12px;';
        const girismi = h.giris_mi !== undefined ? h.giris_mi : (h.direction === 'in' || (h.quantity || h.miktar || 0) > 0);
        const yon = girismi ? '⬆️ Giriş' : '⬇️ Çıkış';
        const miktar = Math.abs(h.miktar || h.quantity || 0).toLocaleString('tr-TR');
        const tarih = h.tarih || h.date || '';
        const urun   = h.urun && h.urun !== '-' ? h.urun : '';
        const firma  = h.firma || '';
        const aciklama = h.aciklama || h.description || '—';  // plaka burada
        row.innerHTML =
          `<div style="display:flex;justify-content:space-between;margin-bottom:2px;">` +
          `<b>${safeHtml(aciklama)}</b>` +
          `<span style="color:var(--muted);font-size:11px;">${tarih}</span></div>` +
          (firma ? `<div style="font-size:11px;color:var(--text);margin-bottom:1px;">🏢 ${safeHtml(firma)}</div>` : '') +
          (urun  ? `<div style="font-size:11px;color:var(--accent);margin-bottom:2px;">${safeHtml(urun)}</div>` : '') +
          `<div style="color:var(--muted)">${yon} · ${miktar} kg</div>`;
        wrap.appendChild(row);
      });
      addMessage('', 'bot', wrap);
      sohbetPanelKaydet('stok_hareketleri', {});
    } catch {
      removeTyping();
      addMessage('❌ Stok hareketleri yüklenemedi.', 'bot');
    }
  }

  // ── GELİR/GİDER RAPORU ───────────────────────────────────────────

  async function gelirGiderRaporuGoster() {
    billablePanelTikla('gelir_gider_raporu');
    // Son 30 gün
    const bitis = new Date().toISOString().split('T')[0];
    const baslangicD = new Date(); baslangicD.setDate(baslangicD.getDate() - 30);
    const baslangic = baslangicD.toISOString().split('T')[0];
    showTyping();
    try {
      const res = await fetch(`/api/gelir-gider-raporu?baslangic=${baslangic}&bitis=${bitis}`);
      const d = await res.json();
      removeTyping();
      const fmt = function(n) { return (n || 0).toLocaleString('tr-TR', {minimumFractionDigits:2}); };
      const netRenk = (d.net_kar || 0) >= 0 ? '#38a169' : '#e53e3e';
      const wrap = document.createElement('div');
      wrap.style.cssText = 'font-size:13px;line-height:2;';
      wrap.innerHTML =
        `<b>📉 Gelir/Gider Raporu</b><br>` +
        `<span style="font-size:11px;color:var(--muted)">${baslangic} – ${bitis}</span><br>` +
        `💚 Toplam gelir: <b>${fmt(d.toplam_gelir)} ₺</b><br>` +
        `🔴 Toplam gider: <b>${fmt(d.toplam_gider)} ₺</b><br>` +
        `<span style="color:${netRenk}">⚖️ Net kar: <b>${fmt(d.net_kar)} ₺</b></span><br>` +
        `🧾 KDV beyan: <b>${fmt(d.kdv_beyan)} ₺</b><br>` +
        `📄 Fatura sayısı: <b>${d.fatura_sayisi || 0}</b>`;
      addMessage('', 'bot', wrap);
      sohbetPanelKaydet('gelir_gider_raporu', {});
    } catch {
      removeTyping();
      addMessage('❌ Rapor yüklenemedi.', 'bot');
    }
  }

  // ── ÖDEME EKLE (alış faturası) ────────────────────────────────────


  async function bekleyenIrsaliyeleriGoster() {
    billablePanelTikla('bekleyen_irsaliyeler');
    // Tarih filtresi seçim formu göster
    const wrap = document.createElement('div');

    // Başlık
    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-size:13px;font-weight:500;color:var(--text);margin-bottom:10px;';
    baslik.textContent = '📋 Bekleyen İrsaliyeler';
    wrap.appendChild(baslik);

    // Tarih satırı
    const tarihRow = document.createElement('div');
    tarihRow.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:10px;';

    const bugun = new Date().toISOString().split('T')[0];
    const buAyBas = new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString().split('T')[0];

    const baslangicInp = document.createElement('input');
    baslangicInp.type = 'date';
    baslangicInp.className = 'fiyat-input';
    baslangicInp.style.flex = '1';
    baslangicInp.value = buAyBas;

    const araSpan = document.createElement('span');
    araSpan.style.cssText = 'font-size:12px;color:var(--muted);';
    araSpan.textContent = '—';

    const bitisInp = document.createElement('input');
    bitisInp.type = 'date';
    bitisInp.className = 'fiyat-input';
    bitisInp.style.flex = '1';
    bitisInp.value = bugun;

    tarihRow.appendChild(baslangicInp);
    tarihRow.appendChild(araSpan);
    tarihRow.appendChild(bitisInp);
    wrap.appendChild(tarihRow);

    // Liste alanı
    const listeWrap = document.createElement('div');
    wrap.appendChild(listeWrap);

    // Daha fazla butonu alanı
    const dahaFazlaWrap = document.createElement('div');
    wrap.appendChild(dahaFazlaWrap);

    addMessage('', 'bot', wrap);
    sohbetPanelKaydet('bekleyen_irsaliyeler', {});

    // İrsaliyeleri yükle
    let mevcutSayfa = 1;
    async function irsaliyeleriYukle(sayfa, temizle) {
      if (temizle) listeWrap.innerHTML = '';
      dahaFazlaWrap.innerHTML = '';

      const yukleniyorEl = document.createElement('div');
      yukleniyorEl.style.cssText = 'font-size:12px;color:var(--muted);padding:8px 0;';
      yukleniyorEl.textContent = 'Yükleniyor…';
      listeWrap.appendChild(yukleniyorEl);

      try {
        const params = new URLSearchParams({
          baslangic: baslangicInp.value,
          bitis: bitisInp.value,
          sayfa: sayfa,
          sayfa_basi: 25
        });
        const res  = await fetch('/api/bekleyen-irsaliyeler?' + params.toString());
        const data = await res.json();
        listeWrap.removeChild(yukleniyorEl);

        if (!data.irsaliyeler || data.irsaliyeler.length === 0) {
          if (sayfa === 1) {
            const bosEl = document.createElement('div');
            bosEl.style.cssText = 'font-size:12px;color:var(--muted);padding:8px 0;';
            bosEl.textContent = '📋 Bu tarih aralığında bekleyen irsaliye yok.';
            listeWrap.appendChild(bosEl);
          }
          return;
        }

        // Başlık
        if (sayfa === 1) {
          const sayacEl = document.createElement('div');
          sayacEl.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:8px;';
          sayacEl.textContent = data.toplam + ' irsaliye — ' + (baslangicInp.value || '?') + ' / ' + (bitisInp.value || '?');
          listeWrap.insertBefore(sayacEl, listeWrap.firstChild);
        }

        data.irsaliyeler.forEach(function(irs) {
          const btn = document.createElement('button');
          btn.className = 'irsaliye-btn';
          const sol = document.createElement('span');
          sol.style.fontWeight = '500';
          sol.textContent = '🏭 ' + irs.cari_adi;
          const sag = document.createElement('span');
          sag.className = 'kg';
          sag.textContent = (irs.toplam_kg||0).toLocaleString('tr-TR') + ' kg';
          const ust = document.createElement('div');
          ust.style.cssText = 'display:flex;justify-content:space-between;width:100%;';
          ust.appendChild(sol);
          ust.appendChild(sag);
          btn.appendChild(ust);
          const alt = document.createElement('div');
          alt.style.cssText = 'font-size:11px;color:var(--muted);';
          const plaka = plakacikar(irs.aciklama);
          alt.textContent = formatTarih(irs.tarih) + (plaka ? '  ·  🚗 ' + plaka : '');
          btn.appendChild(alt);
          btn.onclick = function() { irsaliyeSecildi(irs, wrap); };
          listeWrap.appendChild(btn);
        });

        // Daha fazla butonu
        if (data.sonraki_sayfa_var) {
          const dahaBtn = document.createElement('button');
          dahaBtn.className = 'btn-cancel';
          dahaBtn.style.cssText = 'margin-top:6px;width:100%;';
          dahaBtn.textContent = '⬇ Daha Fazla Yükle';
          dahaBtn.onclick = async function() {
            mevcutSayfa++;
            dahaBtn.disabled = true;
            dahaBtn.textContent = 'Yükleniyor…';
            await irsaliyeleriYukle(mevcutSayfa, false);
          };
          dahaFazlaWrap.appendChild(dahaBtn);
        }
      } catch(e) {
        listeWrap.removeChild(yukleniyorEl);
        const hatEl = document.createElement('div');
        hatEl.style.cssText = 'font-size:12px;color:#f85149;';
        hatEl.textContent = '❌ İrsaliyeler yüklenemedi: ' + e.message;
        listeWrap.appendChild(hatEl);
      }
    }

    // Tarih değişince yeniden yükle
    baslangicInp.onchange = function() { mevcutSayfa = 1; irsaliyeleriYukle(1, true); };
    bitisInp.onchange     = function() { mevcutSayfa = 1; irsaliyeleriYukle(1, true); };

    // İlk yükleme
    irsaliyeleriYukle(1, true);
  }

  // ── BAKİYELER ────────────────────────────────────────────────────

  async function bakiyeleriGoster(onMusteriFiltreParam) {
    billablePanelTikla('musteri_bakiyeleri', onMusteriFiltreParam ? { musteri: onMusteriFiltreParam } : {});
    showTyping();
    try {
      const carilerData = await carileriGetir(false, true);
      removeTyping();
      if (!carilerData || carilerData.length === 0) {
        addMessage('Kayıtlı cari bulunamadı.', 'bot');
        return;
      }
      // Params'tan müşteri adı geldiyse otomatik filtrele
      let cariler = carilerData;
      if (onMusteriFiltreParam) {
        const filtre = onMusteriFiltreParam.toLowerCase();
        const eslesen = cariler.filter(function(c) { return c.ad.toLowerCase().includes(filtre); });
        if (eslesen.length > 0) cariler = eslesen;
      }

      // Paraşüt tipine göre grupla
      function tipSirala(liste) {
        const s = ['customer', 'both', 'customer_supplier', 'supplier', ''];
        return liste.slice().sort(function(a, b) {
          return s.indexOf(a.tip || '') - s.indexOf(b.tip || '');
        });
      }

      const wrap = document.createElement('div');

      // Belirli bir cari aranmıyorsa başlık + tip ayırıcıları göster
      if (!onMusteriFiltreParam) {
        const musteriler  = tipSirala(carilerData.filter(function(c) { const t = c.tip||'customer'; return t==='customer'; }));
        const tedarikciler = tipSirala(carilerData.filter(function(c) { const t = c.tip||''; return t==='supplier'; }));
        const herkesi      = tipSirala(carilerData.filter(function(c) { const t = c.tip||''; return t==='both'||t==='customer_supplier'; }));
        cariler = musteriler.concat(herkesi).concat(tedarikciler);
      }

      const baslik = document.createElement('div');
      baslik.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:8px;';
      baslik.textContent = onMusteriFiltreParam
        ? onMusteriFiltreParam + " bakiyesi (Parasut):"
        : "Güncel bakiyeler (Parasut):";
      wrap.appendChild(baslik);

      let sonTip = null;
      cariler.forEach(function(c) {
        const t = c.tip || 'customer';
        // Grup başlığı
        if (!onMusteriFiltreParam && t !== sonTip) {
          sonTip = t;
          const grupBaslik = document.createElement('div');
          grupBaslik.style.cssText = 'font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin:8px 0 3px;padding-left:4px;';
          if (t === 'customer') grupBaslik.textContent = '👤 Müşteriler';
          else if (t === 'supplier') grupBaslik.textContent = '🏭 Tedarikçiler';
          else grupBaslik.textContent = '🔄 Müşteri & Tedarikçi';
          wrap.appendChild(grupBaslik);
        }
        const row = document.createElement('div');
        row.className = 'cari-row';
        const ad = document.createElement('span');
        ad.className = 'cari-ad';
        ad.textContent = c.ad;
        const bak = document.createElement('span');
        const bakiye = c.bakiye || 0;
        if (bakiye > 0) {
          bak.className = 'cari-bakiye alacak';
          bak.textContent = bakiye.toLocaleString('tr-TR', { minimumFractionDigits: 2 }) + ' TL ⚠️';
        } else if (bakiye < 0) {
          bak.className = 'cari-bakiye';
          bak.style.color = '#2563eb';
          bak.textContent = Math.abs(bakiye).toLocaleString('tr-TR', { minimumFractionDigits: 2 }) + ' TL 🔵';
        } else {
          bak.className = 'cari-bakiye sifir';
          bak.textContent = '0 TL ✅';
        }
        row.appendChild(ad);
        row.appendChild(bak);
        wrap.appendChild(row);
      });
      const toplamAlacak = carilerData.reduce(function(s, c) { return s + (c.bakiye > 0 ? c.bakiye : 0); }, 0);
      const toplamOdeyecek = carilerData.reduce(function(s, c) { return s + (c.bakiye < 0 ? Math.abs(c.bakiye) : 0); }, 0);
      if (toplamAlacak > 0 || toplamOdeyecek > 0) {
        const toplamRow = document.createElement('div');
        toplamRow.style.cssText = 'margin-top:8px;padding:8px 14px;background:var(--accent-lt);border-radius:9px;font-size:12px;font-weight:600;color:var(--accent);';
        toplamRow.innerHTML =
          '<div style="display:flex;justify-content:space-between;margin-bottom:3px;"><span>Toplam Alacak (⚠️)</span><span>' + toplamAlacak.toLocaleString('tr-TR', {minimumFractionDigits:2}) + ' TL</span></div>' +
          (toplamOdeyecek > 0 ? '<div style="display:flex;justify-content:space-between;color:#2563eb;"><span>Toplam Ödenecek (🔵)</span><span>' + toplamOdeyecek.toLocaleString('tr-TR', {minimumFractionDigits:2}) + ' TL</span></div>' : '');
        wrap.appendChild(toplamRow);
      }
      addMessage('💰 Müşteri Bakiyeleri:', 'bot', wrap);
      sohbetPanelKaydet('musteri_bakiyeleri', onMusteriFiltreParam ? { musteri: onMusteriFiltreParam } : {});
    } catch {
      removeTyping();
      addMessage('❌ Bakiyeler yüklenemedi, tekrar deneyin.', 'bot');
    }
  }

  // ── HESAP ÖZETİ ──────────────────────────────────────────────────

  async function hesapOzetiGoster(restoreMusteri, restoreBaslangic, restoreBitis) {
    billablePanelTikla('hesap_ozeti', restoreMusteri ? { musteri: restoreMusteri } : {});
    showTyping();
    let cariler = [];
    try {
      cariler = await carileriGetir(false, true);
    } catch {}
    removeTyping();

    // Geri yüklemede cari zaten seçilmişse direkt işlem geçmişini aç
    if (restoreMusteri) {
      const eslesen = cariler.find(function(c) { return c.ad.toLowerCase().includes(restoreMusteri.toLowerCase()); });
      const cariAdi = eslesen ? eslesen.ad : restoreMusteri;
      (async function() {
        try {
          const params = new URLSearchParams({ sayfa: '1', limit: '25' });
          if (restoreBaslangic) params.set('baslangic', restoreBaslangic);
          if (restoreBitis)     params.set('bitis', restoreBitis);
          const res  = await fetch('/api/islem-gecmisi/' + encodeURIComponent(cariAdi) + '?' + params.toString());
          const data = await res.json();
          if (!data.hata) {
            const wrap = document.createElement('div');
            wrap.style.cssText = 'font-size:13px;';
            const baslikEl = document.createElement('div');
            baslikEl.style.cssText = 'font-weight:500;margin-bottom:10px;font-size:14px;';
            baslikEl.textContent = '📊 ' + (data.cari_adi || cariAdi);
            wrap.appendChild(baslikEl);
            const bakiyeEl = document.createElement('div');
            bakiyeEl.style.cssText = 'margin-bottom:12px;padding:10px 14px;background:var(--accent-lt);border-radius:10px;border-left:3px solid var(--accent);';
            const bakiye = data.bakiye || 0;
            bakiyeEl.innerHTML = '<b>Güncel Bakiye:</b> <span style="color:' + (bakiye > 0 ? 'var(--alacak)' : 'var(--muted)') + ';font-weight:600;">' +
              bakiye.toLocaleString('tr-TR', {minimumFractionDigits:2}) + ' TL</span>';
            wrap.appendChild(bakiyeEl);
            const islemListesi = document.createElement('div');
            wrap.appendChild(islemListesi);
            const islemler = (data.islemler || []).slice(0, 25);
            if (islemler.length === 0) {
              islemListesi.innerHTML = '<div style="color:var(--muted);font-size:12px;">Bu dönemde işlem bulunamadı.</div>';
            } else {
              islemler.forEach(function(is) {
                const row = document.createElement('div');
                row.style.cssText = 'display:flex;justify-content:space-between;font-size:12px;padding:5px 10px;background:var(--bg);border-radius:7px;margin-bottom:4px;';
                const tarih = formatTarih(is.tarih) || '-';
                const tur = is.tur || is.tip || 'İşlem';
                const tutar = parseFloat(is.tutar || 0).toLocaleString('tr-TR', {minimumFractionDigits:0});
                row.innerHTML = '<span>' + safeHtml(tarih) + ' — ' + safeHtml(tur) + '</span><span style="font-weight:600">' + safeHtml(tutar) + ' TL</span>';
                islemListesi.appendChild(row);
              });
            }
            addMessage('', 'bot', wrap);
          }
        } catch {}
      })();
      return;
    }

    const wrap = document.createElement('div');

    // Başlık
    const baslik = document.createElement('div');
    baslik.style.cssText = 'font-size:13px;font-weight:500;color:var(--text);margin-bottom:12px;';
    baslik.textContent = '📊 Hesap Özeti';
    wrap.appendChild(baslik);

    // Cari seçimi
    const cariLabel = document.createElement('div');
    cariLabel.style.cssText = 'font-size:11px;color:var(--muted);margin-bottom:5px;';
    cariLabel.textContent = 'Firma (boş bırakılırsa tümü):';
    wrap.appendChild(cariLabel);

    let secilenCari = null;
    const { wrap: cariWrap, inp: cariInp } = cariSecimDropdown(cariler, function(c) {
      secilenCari = c.ad;
    }, 'Firma adı yazın veya boş bırakın…');
    cariWrap.style.marginBottom = '10px';
    wrap.appendChild(cariWrap);

    // Tarih aralığı
    const tarihLabel = document.createElement('div');
    tarihLabel.style.cssText = 'font-size:11px;color:var(--muted);margin-bottom:5px;';
    tarihLabel.textContent = 'Tarih aralığı (opsiyonel):';
    wrap.appendChild(tarihLabel);

    const tarihRow = document.createElement('div');
    tarihRow.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:12px;';

    const baslangicInp = document.createElement('input');
    baslangicInp.type = 'date';
    baslangicInp.className = 'fiyat-input';
    baslangicInp.style.flex = '1';

    const araLabel = document.createElement('span');
    araLabel.style.cssText = 'font-size:12px;color:var(--muted);';
    araLabel.textContent = '—';

    const bitisInp = document.createElement('input');
    bitisInp.type = 'date';
    bitisInp.className = 'fiyat-input';
    bitisInp.style.flex = '1';
    bitisInp.value = new Date().toISOString().split('T')[0];

    tarihRow.appendChild(baslangicInp);
    tarihRow.appendChild(araLabel);
    tarihRow.appendChild(bitisInp);
    wrap.appendChild(tarihRow);

    // Buton satırı
    const btnRow = document.createElement('div');
    btnRow.className = 'btn-row';

    const sorguBtn = document.createElement('button');
    sorguBtn.className = 'btn-primary';
    sorguBtn.textContent = '📊 Özeti Getir';
    sorguBtn.onclick = async function() {
      sorguBtn.disabled = true;
      sorguBtn.textContent = 'Yükleniyor…';
      try {
        const cari = secilenCari || cariInp.value.trim();
        const baslangic = baslangicInp.value;
        const bitis = bitisInp.value;

        if (cari) {
          // Cari seçilmişse: bakiye + işlem geçmişi
          const params = new URLSearchParams();
          if (baslangic) params.set('baslangic', baslangic);
          if (bitis)     params.set('bitis', bitis);
          params.set('sayfa', '1');
          params.set('limit', '25');

          const res  = await fetch('/api/islem-gecmisi/' + encodeURIComponent(cari) + '?' + params.toString());
          const data = await res.json();

          if (data.hata) { addMessage('❌ ' + data.hata, 'bot'); return; }

          // Özet kartı
          const wrap = document.createElement('div');
          wrap.style.cssText = 'font-size:13px;';

          const baslikEl = document.createElement('div');
          baslikEl.style.cssText = 'font-weight:500;margin-bottom:10px;font-size:14px;';
          baslikEl.textContent = '📊 ' + (data.cari_adi || cari);
          wrap.appendChild(baslikEl);

          const bakiyeEl = document.createElement('div');
          bakiyeEl.style.cssText = 'margin-bottom:12px;padding:10px 14px;background:var(--accent-lt);border-radius:10px;border-left:3px solid var(--accent);';
          const bakiye = data.bakiye || 0;
          bakiyeEl.innerHTML = '<b>Güncel Bakiye:</b> <span style="color:' + (bakiye > 0 ? 'var(--alacak)' : 'var(--muted)') + ';font-weight:600;">' +
            bakiye.toLocaleString('tr-TR', {minimumFractionDigits:2}) + ' TL</span>' +
            (bakiye > 0 ? ' <span style="color:var(--muted);font-size:11px;">(bize borçlu)</span>' : '');
          if (baslangic || bitis) {
            bakiyeEl.innerHTML += '<br><span style="color:var(--muted);font-size:11px;">📅 ' + (baslangic || '—') + ' / ' + (bitis || '—') + '</span>';
          }
          wrap.appendChild(bakiyeEl);

          // İşlem listesi alanı
          const islemListesi = document.createElement('div');
          const dahaFazlaWrap = document.createElement('div');
          wrap.appendChild(islemListesi);
          wrap.appendChild(dahaFazlaWrap);
          addMessage('', 'bot', wrap);
          sohbetPanelKaydet('hesap_ozeti', { musteri: cari, baslangic: baslangic || '', bitis: bitis || '' });

          let mevcutSayfa = 1;
          function islemleriRenderEt(data, temizle) {
            if (temizle) islemListesi.innerHTML = '';
            dahaFazlaWrap.innerHTML = '';

            const islemler = data.islemler || [];
            if (islemler.length === 0 && mevcutSayfa === 1) {
              const bos = document.createElement('div');
              bos.style.cssText = 'color:var(--muted);font-size:12px;';
              bos.textContent = 'Bu dönemde işlem bulunamadı.';
              islemListesi.appendChild(bos);
              return;
            }

            if (mevcutSayfa === 1) {
              const baslik2 = document.createElement('div');
              baslik2.style.cssText = 'font-size:11px;color:var(--muted2);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;';
              baslik2.textContent = 'İşlem Geçmişi (' + (data.toplam_islem || islemler.length) + ' işlem)';
              islemListesi.appendChild(baslik2);
            }

            islemler.forEach(function(i) {
              const row = document.createElement('div');
              row.style.cssText = 'padding:8px 12px;background:var(--bg);border-radius:8px;margin-bottom:4px;display:flex;justify-content:space-between;align-items:center;font-size:12px;';
              const sol = document.createElement('div');
              const tur = i.tur || '-';
              const turEmoji = tur.includes('invoice') || tur.includes('fatura') ? '📄' : tur.includes('payment') || tur.includes('tahsilat') ? '💵' : '📋';
              sol.innerHTML = '<span style="color:var(--muted);margin-right:6px;">' + safeHtml(i.tarih) + '</span>' + safeHtml(turEmoji) + ' ' + safeHtml(i.aciklama || tur);
              const sag = document.createElement('div');
              sag.style.cssText = 'font-weight:600;white-space:nowrap;margin-left:8px;';
              const tutar = i.tutar || 0;
              sag.style.color = tutar >= 0 ? 'var(--alacak)' : '#2a9d5c';
              sag.textContent = (tutar >= 0 ? '+' : '') + tutar.toLocaleString('tr-TR', {minimumFractionDigits:2}) + ' TL';
              row.appendChild(sol); row.appendChild(sag);
              islemListesi.appendChild(row);
            });

            // Daha fazla butonu
            if (data.sonraki_sayfa_var) {
              const dahaBtn = document.createElement('button');
              dahaBtn.className = 'btn-cancel';
              dahaBtn.style.cssText = 'margin-top:6px;width:100%;';
              dahaBtn.textContent = '⬇ Daha Fazla Yükle (' + ((mevcutSayfa * 25)) + '/' + (data.toplam_islem||'?') + ')';
              dahaBtn.onclick = async function() {
                mevcutSayfa++;
                dahaBtn.disabled = true; dahaBtn.textContent = 'Yükleniyor…';
                const p2 = new URLSearchParams();
                if (baslangic) p2.set('baslangic', baslangic);
                if (bitis)     p2.set('bitis', bitis);
                p2.set('sayfa', mevcutSayfa); p2.set('limit', '25');
                try {
                  const r2   = await fetch('/api/islem-gecmisi/' + encodeURIComponent(cari) + '?' + p2.toString());
                  const d2   = await r2.json();
                  islemleriRenderEt(d2, false);
                } catch(e) {
                  dahaBtn.textContent = '❌ Yüklenemedi'; dahaBtn.disabled = false;
                }
              };
              dahaFazlaWrap.appendChild(dahaBtn);
            }
          }

          islemleriRenderEt(data, true);
        } else {
          // Cari seçilmemişse: genel özet
          let url = '/api/hesap-ozeti';
          const params = [];
          if (baslangic) params.push('baslangic=' + baslangic);
          if (bitis) params.push('bitis=' + bitis);
          if (params.length) url += '?' + params.join('&');
          const res = await fetch(url);
          const data = await res.json();
          const satirlar = [];
          if (baslangic || bitis) {
            satirlar.push('📅 Dönem: ' + (baslangic || '—') + ' / ' + (bitis || '—'));
            satirlar.push('');
          }
          satirlar.push('👥 Toplam Cari: ' + (data.toplam_cari || 0));
          satirlar.push('🔴 Toplam Alacak: ' + (data.toplam_alacak || 0).toLocaleString('tr-TR', { minimumFractionDigits: 2 }) + ' TL');
          satirlar.push('🟢 Toplam Borç: ' + (data.toplam_borclu || 0).toLocaleString('tr-TR', { minimumFractionDigits: 2 }) + ' TL');
          addMessage(satirlar.join('\n'), 'bot');
        }
      } catch {
        addMessage('❌ Hesap özeti yüklenemedi, tekrar deneyin.', 'bot');
      } finally {
        sorguBtn.disabled = false;
        sorguBtn.textContent = '📊 Özeti Getir';
      }
    };

    const iptalBtn = document.createElement('button');
    iptalBtn.className = 'btn-cancel';
    iptalBtn.textContent = '✕ İptal';
    iptalBtn.onclick = function() { addMessage('❌ İptal edildi.', 'bot'); };

    btnRow.appendChild(sorguBtn);
    btnRow.appendChild(iptalBtn);
    wrap.appendChild(btnRow);

    addMessage('', 'bot', wrap);
    sohbetPanelKaydet('hesap_ozeti', {});
    setTimeout(function() { cariInp.focus(); }, 100);
  }

  // ── TAHSİLAT FORMU ───────────────────────────────────────────────

