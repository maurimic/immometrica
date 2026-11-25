/**
 * ImmoMetrica - Map Module (OpenFreeMap + MapLibre GL)
 * Handles interactive map initialization, tile themes, markers, custom popups & camera navigation.
 */
window.immoMapModule = {
  mapInstance: null,
  mapMarkers: [],
  selectedMapPropertyId: null,
  currentMapStyle: null,

  getMapStyle() {
    const isDark = document.documentElement.classList.contains('dark') || 
                   this.theme === 'dark' || 
                   (this.theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    return isDark 
      ? 'https://tiles.openfreemap.org/styles/dark' 
      : 'https://tiles.openfreemap.org/styles/bright';
  },

  // Map initialization (OpenFreeMap + MapLibre GL)
  initMap() {
    setTimeout(() => {
      if (window.lucide) window.lucide.createIcons();
    }, 100);
    const targetStyle = this.getMapStyle();

    if (this.mapInstance) {
      if (this.currentMapStyle !== targetStyle) {
        this.currentMapStyle = targetStyle;
        this.mapInstance.setStyle(targetStyle);
        this.mapInstance.once('styledata', () => {
          this.updateMapMarkers();
        });
      }
      setTimeout(() => {
        this.mapInstance.resize();
        this.updateMapMarkers();
        this.fitAllPropertiesOnMap();
      }, 150);
      return;
    }

    this.$nextTick(() => {
      const container = document.getElementById('map');
      if (!container) return;

      this.currentMapStyle = targetStyle;
      this.mapInstance = new maplibregl.Map({
        container: 'map',
        style: targetStyle,
        center: [10.4515, 51.1657], // Germany center
        zoom: 5.8
      });

      this.mapInstance.addControl(new maplibregl.NavigationControl(), 'top-right');
      
      this.mapInstance.on('load', () => {
        this.mapInstance.resize();
        this.updateMapMarkers();
        setTimeout(() => {
          this.fitAllPropertiesOnMap();
        }, 200);
      });
    });
  },

  updateMapMarkers() {
    // Clear old markers
    if (this.mapMarkers && this.mapMarkers.length) {
      this.mapMarkers.forEach(m => {
        if (m.marker) m.marker.remove();
        else if (m.remove) m.remove();
      });
    }
    this.mapMarkers = [];

    if (!this.mapInstance) return;

    this.properties.forEach(prop => {
      const lat = Number(prop.lat);
      const lng = Number(prop.lng);
      if (isNaN(lat) || isNaN(lng) || !lat || !lng) return;

      const isPos = (prop.monthly_cashflow || 0) >= 0;
      const pinColor = isPos ? '#10b981' : '#0284c7';
      const yieldNum = Number(prop.gross_yield || 0);
      const yieldText = yieldNum.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' %';
      
      // Custom DOM Marker Container
      // CRITICAL: Do NOT put 'relative', 'transform', or positioning classes on `el`.
      // MapLibre's marker engine positions `el` via absolute positioning and inline translate3d.
      const el = document.createElement('div');
      el.className = 'cursor-pointer select-none';
      el.setAttribute('data-prop-id', prop.id);

      // Inner wrapper for group hover and pin badge
      const wrapper = document.createElement('div');
      wrapper.className = 'relative group';

      // Circular visual badge
      const inner = document.createElement('div');
      inner.className = 'w-9 h-9 rounded-full flex items-center justify-center font-bold text-xs text-white shadow-xl border-2 border-white dark:border-slate-800 transition-transform duration-200 hover:scale-125';
      inner.style.backgroundColor = pinColor;
      inner.innerText = yieldText;

      // Hover tooltip (escaped against DOM XSS)
      const tooltip = document.createElement('div');
      tooltip.className = 'hidden group-hover:flex flex-col items-center absolute bottom-full mb-1.5 left-1/2 -translate-x-1/2 pointer-events-none z-50 whitespace-nowrap';
      const safeCity = this.escapeHtml(prop.city || '');
      const safePortal = this.escapeHtml(prop.source_portal || 'Immo');
      const safeTitle = this.escapeHtml(prop.title || '');
      const safePlz = this.escapeHtml(prop.postal_code || '');
      const safeAddress = this.escapeHtml(prop.address || '');
      const safeImgUrl = prop.image_url ? this.safeHttpUrl(prop.image_url) : '#';
      const safePortalUrl = this.safeHttpUrl(prop.portal_url || '');
      const safePropId = Number(prop.id) || 0;

      tooltip.innerHTML = `
        <span class="bg-slate-900/95 text-white text-[11px] font-semibold px-2 py-0.5 rounded shadow-lg">
          ${safeCity} · ${Number(prop.purchase_price || 0).toLocaleString('de-DE')} €
        </span>
        <span class="w-1.5 h-1.5 bg-slate-900/95 rotate-45 -mt-0.5"></span>
      `;

      wrapper.appendChild(inner);
      wrapper.appendChild(tooltip);
      el.appendChild(wrapper);

      const priceFmt = Number(prop.purchase_price || 0).toLocaleString('de-DE') + ' €';
      const cfFmt = this.formatCashflow(prop.monthly_cashflow);
      const cfClass = isPos ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400';
      const rentFmt = Number(prop.current_rent || prop.expected_rent || 0).toLocaleString('de-DE') + ' €/M';
      const spaceFmt = this.formatSqM(prop.living_space);
      
      const imgHtml = (prop.image_url && safeImgUrl !== '#')
        ? `<img src="${safeImgUrl}" alt="Foto" class="w-full h-28 object-cover rounded-md mb-2 cursor-pointer hover:opacity-90 transition-opacity" onclick="window.immoAppInstance.openDetailModalById(${safePropId})">`
        : `<div class="w-full h-20 bg-slate-100 dark:bg-slate-800 rounded-md mb-2 flex items-center justify-center text-slate-400 text-xs cursor-pointer" onclick="window.immoAppInstance.openDetailModalById(${safePropId})">🏢 Details & Foto ansehen</div>`;

      const popupHtml = `
        <div class="map-popup-card text-left max-w-[270px] text-slate-800 dark:text-slate-100">
          ${imgHtml}
          <div class="flex items-center justify-between gap-2 mb-1.5">
            <span class="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-sky-100 dark:bg-sky-950 text-sky-800 dark:text-sky-300 border border-sky-200 dark:border-sky-800 truncate">${safePortal}</span>
            <span class="text-[10px] font-bold px-1.5 py-0.5 rounded ${isPos ? 'bg-emerald-100 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-300' : 'bg-rose-100 dark:bg-rose-950 text-rose-700 dark:text-rose-300'}">${this.escapeHtml(yieldText)} Rendite</span>
          </div>
          <h4 class="font-bold text-xs text-slate-900 dark:text-white line-clamp-2 leading-snug cursor-pointer hover:text-sky-600 dark:hover:text-sky-400 transition-colors" onclick="window.immoAppInstance.openDetailModalById(${safePropId})">
            ${safeTitle}
          </h4>
          <div class="text-[11px] text-slate-500 dark:text-slate-400 mt-1 truncate">
            📍 ${safePlz} ${safeCity}${safeAddress ? ' · ' + safeAddress : ''}
          </div>
          <div class="grid grid-cols-2 gap-1.5 my-2.5 py-1.5 border-y border-slate-200 dark:border-slate-800 text-[11px]">
            <div>
              <span class="text-slate-400 dark:text-slate-500 block text-[10px]">Kaufpreis</span>
              <span class="font-semibold text-slate-900 dark:text-slate-100">${priceFmt}</span>
            </div>
            <div>
              <span class="text-slate-400 dark:text-slate-500 block text-[10px]">Cashflow</span>
              <span class="font-bold ${cfClass}">${cfFmt}</span>
            </div>
            <div>
              <span class="text-slate-400 dark:text-slate-500 block text-[10px]">Kaltmiete</span>
              <span class="font-medium text-slate-700 dark:text-slate-300">${rentFmt}</span>
            </div>
            <div>
              <span class="text-slate-400 dark:text-slate-500 block text-[10px]">Wohnfläche</span>
              <span class="font-medium text-slate-700 dark:text-slate-300">${spaceFmt}</span>
            </div>
          </div>
          <div class="flex flex-col gap-1.5 mt-2">
            <button 
              onclick="window.immoAppInstance.openDetailModalById(${safePropId})" 
              class="w-full py-1.5 px-2 bg-sky-600 hover:bg-sky-700 text-white rounded text-xs font-semibold flex items-center justify-center gap-1.5 shadow-sm transition-colors cursor-pointer">
              <span>🔍 KI-Audit & Details</span>
            </button>
            <div class="flex items-center gap-1.5">
              <button 
                onclick="window.immoAppInstance.sendToCalculatorById(${safePropId})" 
                class="flex-1 py-1 px-2 bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 rounded text-[11px] font-medium transition-colors text-center cursor-pointer">
                🧮 Rechner
              </button>
              <a 
                href="${safePortalUrl}" 
                target="_blank" 
                rel="noopener noreferrer"
                class="flex-1 py-1 px-2 bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 rounded text-[11px] font-medium transition-colors text-center truncate">
                🌐 Inserat
              </a>
            </div>
          </div>
        </div>
      `;

      const popup = new maplibregl.Popup({ offset: 22, maxWidth: '290px', closeButton: true, closeOnClick: false }).setHTML(popupHtml);

      const marker = new maplibregl.Marker({ element: el })
        .setLngLat([lng, lat])
        .setPopup(popup)
        .addTo(this.mapInstance);

      // Clicking pin on map highlights companion card and activates pin glow
      el.addEventListener('click', () => {
        this.selectedMapPropertyId = prop.id;
        const cardEl = document.getElementById(`map-card-${prop.id}`);
        if (cardEl) {
          cardEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
        this.mapMarkers.forEach(m => {
          if (m.inner) m.inner.classList.remove('pin-badge-active');
        });
        inner.classList.add('pin-badge-active');
      });

      this.mapMarkers.push({
        propId: prop.id,
        marker: marker,
        popup: popup,
        el: el,
        inner: inner,
        prop: prop
      });
    });
  },

  flyToProperty(prop) {
    if (!prop) return;
    this.selectedMapPropertyId = prop.id;

    if (!this.mapInstance) {
      this.initMap();
      setTimeout(() => this.flyToProperty(prop), 400);
      return;
    }

    this.mapInstance.resize();

    const lat = Number(prop.lat);
    const lng = Number(prop.lng);

    if (isNaN(lat) || isNaN(lng) || !lat || !lng) {
      console.warn('Property has no valid coordinates:', prop);
      return;
    }

    // Smoothly fly camera to the property with focus
    this.mapInstance.flyTo({
      center: [lng, lat],
      zoom: 15.5,
      speed: 1.4,
      curve: 1.1,
      essential: true
    });

    // Find the marker object
    const target = this.mapMarkers.find(m => m.propId === prop.id);

    // Close all existing popups and reset marker highlights
    this.mapMarkers.forEach(m => {
      if (m.popup && m.popup.isOpen()) {
        m.popup.remove();
      }
      if (m.inner) {
        m.inner.classList.remove('pin-badge-active');
      }
    });

    // Highlight the targeted marker element and open its popup
    if (target) {
      if (target.inner) {
        target.inner.classList.add('pin-badge-active');
      }
      if (target.popup) {
        target.popup.setLngLat([lng, lat]).addTo(this.mapInstance);
      }
    }

    // Also ensure companion card is visible/scrolled in list
    const cardEl = document.getElementById(`map-card-${prop.id}`);
    if (cardEl) {
      cardEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // Scroll the map container into view on smaller displays / mobile
    const mapContainer = document.getElementById('map');
    if (mapContainer && window.innerWidth < 1024) {
      mapContainer.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  },

  showPropertyOnMap(prop) {
    if (!prop) return;
    this.activeTab = 'map';
    this.initMap();
    setTimeout(() => {
      this.flyToProperty(prop);
    }, 350);
  },

  fitAllPropertiesOnMap() {
    if (!this.mapInstance || !this.properties || !this.properties.length) return;
    const validProps = this.properties.filter(p => p.lat && p.lng);
    if (!validProps.length) return;

    const bounds = new maplibregl.LngLatBounds();
    validProps.forEach(p => {
      bounds.extend([Number(p.lng), Number(p.lat)]);
    });
    this.mapInstance.fitBounds(bounds, { padding: 60, maxZoom: 13, duration: 1000 });
  }
};
