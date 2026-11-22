document.addEventListener('alpine:init', () => {
  Alpine.data('immoApp', () => {
    const baseApp = {
      // Navigation tabs: 'feed', 'board', 'calculator', 'map', 'alerts', 'info'
      activeTab: (() => {
        try {
          const tab = new URLSearchParams(window.location.search).get('tab');
          if (tab && ['feed', 'board', 'calculator', 'map', 'alerts', 'info'].includes(tab)) {
            return tab;
          }
        } catch (e) {}
        return 'feed';
      })(),
    infoSubTab: 'guide', // 'guide' (Leitfaden/Handbuch) or 'mcp' (Model Context Protocol)
    viewMode: 'table', // 'table' or 'grid'

    // Data state
    properties: [],
    loading: false,
    checkingAll: false,
    isScraping: false,
    showScrapeModal: false,
    scrapeCity: 'Saarlouis',
    scrapeQuery: '',
    scrapeStatusMsg: '',
    boardStages: [],
    selectedProperty: null,
    detailModalOpen: false,
    offerModalOpen: false,
    isModalFullscreen: false,
    modalActiveSection: 'overview',
    currentModalView: 'audit',
    isAuditing: false,
    isAnalyzingImages: false,
    showFullDesc: false,
    theme: localStorage.getItem('immo_theme') || 'system',
    _isPopStateNavigation: false,

    // Kaufangebot / Letter of Intent (LOI) & Unterlagen-Anfrage State

    // Toast notifications
    toast: {
      show: false,
      message: '',
      type: 'info',
      timer: null
    },

    // Portal Health & Canary Alert State
    portalHealth: null,
    portalAlert: null,
    canaryChecking: false,

    // URL Import Modal State
    urlImportModalOpen: false,
    importUrlInput: '',
    isImportingUrl: false,
    importError: '',

    // Filter state
    filters: {
      q: '',
      city: '',
      state: '',
      property_type: 'all',
      min_price: '',
      max_price: '',
      min_yield: '',
      yield_basis: 'any', // 'any' (Ist oder Mietspiegel), 'actual' (nur Ist), 'potential' (nur Mietspiegel)
      positive_cashflow: false,
      undervalued_only: false,
      foreclosure_only: false,
      private_only: false,
      online_only: false, // User requested: check if deals are actually still online
      sort_by: 'gross_yield',
      sort_order: 'desc'
    },

    // Federal states for tax calculation
    states: [
      { name: 'Baden-Württemberg', tax: 5.0 },
      { name: 'Bayern', tax: 3.5 },
      { name: 'Berlin', tax: 6.0 },
      { name: 'Brandenburg', tax: 6.5 },
      { name: 'Bremen', tax: 5.0 },
      { name: 'Hamburg', tax: 5.5 },
      { name: 'Hessen', tax: 6.0 },
      { name: 'Mecklenburg-Vorpommern', tax: 6.0 },
      { name: 'Niedersachsen', tax: 5.0 },
      { name: 'Nordrhein-Westfalen', tax: 6.5 },
      { name: 'Rheinland-Pfalz', tax: 5.0 },
      { name: 'Saarland', tax: 6.5 },
      { name: 'Sachsen', tax: 5.5 },
      { name: 'Sachsen-Anhalt', tax: 5.0 },
      { name: 'Schleswig-Holstein', tax: 6.5 },
      { name: 'Thüringen', tax: 5.0 }
    ],

    // MCP & API State
    mcpToken: '',
    mcpSnippet: '',
    mcpCopied: false,
    mcpRegenerating: false,
    mcpQuotaUsed: 0,
    mcpQuotaLimit: 2000,


    async init() {
      window.immoAppInstance = this;
      window.addEventListener('keydown', (e) => {
        if (!this.lightboxOpen) return;
        if (e.key === 'Escape') {
          this.closePhotoLightbox();
        } else if (e.key === 'ArrowRight') {
          this.nextLightboxPhoto();
        } else if (e.key === 'ArrowLeft') {
          this.prevLightboxPhoto();
        }
      });
      this.applyTheme(this.theme);
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (this.theme === 'system') {
          this.applyTheme('system');
        }
      });

      // Initialize URL Routing & Deep Linking immediately before async data calls
      this.initRouter();

      this.checkSavedApiKey();
      await this.fetchLiveMortgageRates();
      await this.fetchProperties();
      await this.fetchBoard();
      await this.resolveLocationTax();
      this.runCalculator();
      await this.loadAlertsData();
      await this.fetchMcpStatus();
      await this.fetchPortalHealth();
      setInterval(() => this.fetchPortalHealth(), 120000);
      
      // Watch filters
      this.$watch('filters', () => {
        this.fetchProperties();
      }, { deep: true });

      // Automatically sync search keyword into scrapeCity
      this.$watch('filters.q', (val) => {
        if (val && val.trim()) {
          this.scrapeCity = val.trim();
        }
      });

      // Watch calculator price/rent/living_space/rates
      this.$watch('calc.price', () => this.runCalculator());
      this.$watch('calc.rent', () => this.runCalculator());
      this.$watch('calc.living_space', () => this.runCalculator());
      this.$watch('calc.equity_value', () => this.runCalculator());
      this.$watch('calc.equity_type', () => this.runCalculator());
      this.$watch('calc.interest_rate', () => this.runCalculator());
      this.$watch('calc.repayment_rate', () => this.runCalculator());
      this.$watch('calc.additional_costs', () => this.runCalculator());
      this.$watch('calc.renovation_costs', () => this.runCalculator());
      this.$watch('calc.commission_percent', () => this.runCalculator());
      this.$watch('calc.buy_costs_from_equity', () => this.runCalculator());
      this.$watch('calc.include_afa', () => this.runCalculator());
      this.$watch('calc.building_share_percent', () => this.runCalculator());
      this.$watch('calc.depreciation_years', () => this.runCalculator());
      this.$watch('calc.marginal_tax_rate', () => this.runCalculator());
      this.$watch('calc.state', () => {
        const found = this.states.find(s => s.name === this.calc.state);
        if (found) this.calc.tax_rate = found.tax;
        this.runCalculator();
      });

      // Watch activeTab to sync URL parameter and refresh views
      this.$watch('activeTab', async (val) => {
        if (!this._isPopStateNavigation) {
          this.syncUrl(true);
        }
        if (val === 'alerts') {
          if (typeof this.loadAlertsData === 'function') {
            await this.loadAlertsData();
          }
        }
        setTimeout(() => { if (window.lucide) window.lucide.createIcons(); }, 50);
        setTimeout(() => { if (window.lucide) window.lucide.createIcons(); }, 200);
        if (val === 'map' && this.mapInstance) {
          this.updateMapMarkers();
        }
      });
    },

    async fetchProperties() {
      this.loading = true;
      try {
        const params = new URLSearchParams();
        if (this.filters.q) params.append('q', this.filters.q);
        if (this.filters.city) params.append('city', this.filters.city);
        if (this.filters.state) params.append('state', this.filters.state);
        if (this.filters.property_type && this.filters.property_type !== 'all') {
          params.append('property_type', this.filters.property_type);
        }
        if (this.filters.min_price) params.append('min_price', this.filters.min_price);
        if (this.filters.max_price) params.append('max_price', this.filters.max_price);
        if (this.filters.min_yield) {
          params.append('min_yield', this.filters.min_yield);
          params.append('yield_basis', this.filters.yield_basis || 'any');
        }
        if (this.filters.positive_cashflow) params.append('positive_cashflow', 'true');
        if (this.filters.undervalued_only) params.append('undervalued_only', 'true');
        if (this.filters.foreclosure_only) params.append('foreclosure_only', 'true');
        if (this.filters.private_only) params.append('private_only', 'true');
        if (this.filters.online_only) params.append('online_only', 'true');
        params.append('sort_by', this.filters.sort_by);
        params.append('sort_order', this.filters.sort_order);

        const res = await fetch(`/api/properties?${params.toString()}`);
        const data = await res.json();
        this.properties = data.properties || [];

        if (this.activeTab === 'map' && this.mapInstance) {
          this.updateMapMarkers();
        }
        setTimeout(() => lucide.createIcons(), 50);
      } catch (err) {
        console.error('Failed to load properties:', err);
      } finally {
        this.loading = false;
      }
    },

    showToast(message, type = 'info') {
      if (this.toast && this.toast.timer) clearTimeout(this.toast.timer);
      this.toast = {
        show: true,
        message: message,
        type: type,
        timer: setTimeout(() => {
          this.toast.show = false;
        }, 5000)
      };
      this.$nextTick(() => {
        if (window.lucide) window.lucide.createIcons();
      });
    },

    async fetchPortalHealth() {
      try {
        const resp = await fetch('/api/portal-health');
        if (resp.ok) {
          const data = await resp.json();
          this.portalHealth = data;
          this.portalAlert = data.alert && data.alert.active ? data.alert : null;
          this.$nextTick(() => {
            if (window.lucide) window.lucide.createIcons();
          });
        }
      } catch (e) {
        console.error('Error fetching portal health:', e);
      }
    },

    async runCanaryCheck(interactive = true) {
      if (this.canaryChecking) return;
      this.canaryChecking = true;
      if (interactive) {
        this.showToast('Canary-Prüfung aller Portale (Kleinanzeigen, ImmoScout24, Immowelt) läuft...', 'info');
      }
      try {
        const resp = await fetch('/api/portal-health/check', { method: 'POST' });
        if (resp.ok) {
          const data = await resp.json();
          this.portalHealth = data;
          this.portalAlert = data.alert_active ? {
            active: true,
            title: data.overall === 'critical' ? '🚨 Kritischer Portal-Ausfall' : '⚠️ Portal-Warnung: Scraper eingeschränkt',
            message: data.alert_message,
            level: data.overall
          } : null;
          if (interactive) {
            if (data.overall === 'healthy') {
              this.showToast('Alle 3 Portale (Kleinanzeigen, ImmoScout24, Immowelt) sind voll erreichbar & auslesbar!', 'success');
            } else {
              this.showToast(`Portal-Einschränkung erkannt (${data.overall}): ${data.alert_message}`, 'warning');
            }
          }
        } else {
          if (interactive) this.showToast('Fehler bei der Portal-Health-Prüfung', 'error');
        }
      } catch (e) {
        if (interactive) this.showToast('Verbindungsfehler bei der Portal-Prüfung', 'error');
      } finally {
        this.canaryChecking = false;
      }
    },

    async dismissPortalAlert() {
      if (this.portalAlert) {
        this.portalAlert.active = false;
      }
      try {
        await fetch('/api/portal-health/dismiss', { method: 'POST' });
      } catch (e) {
        console.error('Error dismissing portal alert:', e);
      }
    },

    openUrlImportModal() {
      this.urlImportModalOpen = true;
      this.importUrlInput = '';
      this.importError = '';
      this.$nextTick(() => {
        if (window.lucide) window.lucide.createIcons();
      });
    },

    async submitUrlImport() {
      const url = (this.importUrlInput || '').trim();
      if (!url) return;
      this.isImportingUrl = true;
      this.importError = '';
      try {
        const resp = await fetch('/api/properties/import-url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url })
        });
        const data = await resp.json();
        if (resp.ok) {
          this.urlImportModalOpen = false;
          this.importUrlInput = '';
          this.showToast(data.message || 'Inserat erfolgreich importiert!', 'success');
          await this.fetchProperties();
          if (data.property_id) {
            const importedDeal = this.properties.find(p => p.id === data.property_id);
            if (importedDeal) {
              this.openDetailModal(importedDeal, 'audit');
            }
          }
        } else {
          this.importError = data.detail || 'Fehler beim Importieren des Inserats.';
          this.showToast(this.importError, 'error');
        }
      } catch (err) {
        this.importError = 'Netzwerkfehler: ' + err.message;
        this.showToast(this.importError, 'error');
      } finally {
        this.isImportingUrl = false;
      }
    },

    async triggerLiveScrape(targetCity = null) {
      const cityToScrape = (targetCity || this.scrapeCity || this.filters.q || 'Dortmund').trim();
      if (!cityToScrape) {
        this.showToast('Bitte gib eine Stadt ein (z. B. Dortmund, Saarlouis, Nürnberg oder Bochum).', 'warning');
        return;
      }

      // Smart detection: If user entered an exposé link instead of a city, route to URL import
      if (cityToScrape.startsWith('http://') || cityToScrape.startsWith('https://')) {
        this.importUrlInput = cityToScrape;
        return this.submitUrlImport();
      }

      this.isScraping = true;
      this.scrapeCity = cityToScrape;
      
      const payload = {
        city: cityToScrape,
        query: this.scrapeQuery || '',
        limit: 30
      };
      if (this.filters.min_yield !== '' && this.filters.min_yield !== null && this.filters.min_yield !== undefined) {
        payload.min_yield = parseFloat(this.filters.min_yield);
        payload.yield_basis = this.filters.yield_basis || 'any';
      }
      if (this.filters.max_price !== '' && this.filters.max_price !== null && this.filters.max_price !== undefined) {
        payload.max_price = parseFloat(this.filters.max_price);
      }
      if (this.filters.min_price !== '' && this.filters.min_price !== null && this.filters.min_price !== undefined) {
        payload.min_price = parseFloat(this.filters.min_price);
      }

      const yieldInfo = payload.min_yield ? ` (≥ ${payload.min_yield}% Rendite)` : '';
      this.scrapeStatusMsg = `Durchsuche Live-Portale (Kleinanzeigen, Immowelt, ImmoScout24) nach passenden Angeboten in ${cityToScrape}${yieldInfo}...`;

      try {
        const res = await fetch('/api/properties/live-scrape', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.status === 'success') {
          this.scrapeStatusMsg = data.message;
          this.showScrapeModal = false;
          this.filters.q = cityToScrape;
          this.filters.city = '';
          this.filters.state = '';
          if (this.pagination) this.pagination.currentPage = 1;
          await this.fetchProperties();
          await this.fetchBoard();
          if (this.activeTab === 'map' && this.mapInstance) {
            this.updateMapMarkers();
            setTimeout(() => this.fitAllPropertiesOnMap(), 200);
          }
          this.showToast(data.message || `Erfolg: ${data.imported} neue Live-Deals in ${data.city} importiert!`, 'success');
        } else {
          this.showToast('Hinweis: ' + (data.message || 'Keine neuen Angebote gefunden.'), 'info');
        }
      } catch (err) {
        this.showToast('Fehler beim Live-Scraping: ' + err.message, 'error');
      } finally {
        this.isScraping = false;
      }
    },

    getSourcePortalsList(prop) {
      if (!prop) return [];
      if (prop.source_portals) {
        try {
          const parsed = typeof prop.source_portals === 'string' ? JSON.parse(prop.source_portals) : prop.source_portals;
          if (Array.isArray(parsed) && parsed.length > 0) return parsed;
        } catch (e) {}
      }
      if (prop.source_portal) {
        return [{ portal: prop.source_portal, url: prop.portal_url || '#' }];
      }
      return [];
    },

    getPortalBadgeClass(portalName) {
      const p = (portalName || '').toLowerCase();
      if (p.includes('scout')) {
        return 'bg-blue-50 dark:bg-blue-950/80 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800 hover:bg-blue-100';
      } else if (p.includes('immowelt')) {
        return 'bg-teal-50 dark:bg-teal-950/80 text-teal-700 dark:text-teal-300 border-teal-200 dark:border-teal-800 hover:bg-teal-100';
      } else if (p.includes('kleinanzeigen')) {
        return 'bg-amber-50 dark:bg-amber-950/80 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-800 hover:bg-amber-100';
      }
      return 'bg-slate-50 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-700';
    },

    resetFilters() {
      this.filters = {
        q: '',
        city: '',
        state: '',
        property_type: 'all',
        min_price: '',
        max_price: '',
        min_yield: '',
        yield_basis: 'any',
        positive_cashflow: false,
        undervalued_only: false,
        foreclosure_only: false,
        private_only: false,
        online_only: false,
        sort_by: 'gross_yield',
        sort_order: 'desc'
      };
      this.fetchProperties();
    },

    applyTheme(mode) {
      this.theme = mode;
      localStorage.setItem('immo_theme', mode);
      const isDark = mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
      if (isDark) {
        document.documentElement.classList.add('dark');
      } else {
        document.documentElement.classList.remove('dark');
      }

      // Sync map tiles with active theme
      if (this.mapInstance) {
        const targetStyle = this.getMapStyle();
        if (this.currentMapStyle !== targetStyle) {
          this.currentMapStyle = targetStyle;
          this.mapInstance.setStyle(targetStyle);
          this.mapInstance.once('styledata', () => {
            this.updateMapMarkers();
          });
        }
      }
    },

    toggleTheme() {
      if (this.theme === 'system') {
        this.applyTheme('dark');
      } else if (this.theme === 'dark') {
        this.applyTheme('light');
      } else {
        this.applyTheme('system');
      }
    },

    async deleteProperty(propertyId, event = null) {
      if (event) event.stopPropagation();
      if (!confirm(`Möchtest du Deal #${propertyId} wirklich aus der Datenbank löschen?`)) return;
      try {
        const res = await fetch(`/api/properties/${propertyId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.status === 'success') {
          if (this.selectedProperty && this.selectedProperty.id === propertyId) {
            this.detailModalOpen = false;
            this.selectedProperty = null;
          }
          await this.fetchProperties();
          await this.fetchBoard();
        }
      } catch (err) {
        alert('Fehler beim Löschen: ' + err.message);
      }
    },

    async deleteOfflineProperties() {
      const offlineCount = this.properties.filter(p => !p.is_online).length;
      if (offlineCount === 0) {
        alert('Es gibt aktuell keine als offline/deaktiviert markierten Deals in der Datenbank.');
        return;
      }
      if (!confirm(`Möchtest du wirklich alle ${offlineCount} deaktivierten/offline Deals auf einmal aus der Datenbank löschen?`)) return;
      try {
        const res = await fetch('/api/properties/delete-offline', { method: 'POST' });
        const data = await res.json();
        alert(data.message);
        await this.fetchProperties();
        await this.fetchBoard();
      } catch (err) {
        alert('Fehler beim Bereinigen: ' + err.message);
      }
    },

    async fetchBoard() {
      try {
        const res = await fetch('/api/pipeline/board');
        this.boardStages = await res.json();
        setTimeout(() => lucide.createIcons(), 50);
      } catch (err) {
        console.error('Failed to load dealboard:', err);
      }
    },

    // Check individual property online status
    async checkPropertyAvailability(property) {
      property.checking = true;
      try {
        const res = await fetch(`/api/properties/${property.id}/check-availability`, { method: 'POST' });
        const data = await res.json();
        property.is_online = data.is_online ? 1 : 0;
        property.availability_status = data.availability_status;
        property.status_message = data.status_message;
        property.last_checked_at = data.last_checked_at;
        property.live_search_url = data.live_search_url;
        if (data.lat !== undefined && data.lat !== null) property.lat = data.lat;
        if (data.lng !== undefined && data.lng !== null) property.lng = data.lng;
        if (data.address) property.address = data.address;
      } catch (err) {
        console.error('Error verifying property status:', err);
      } finally {
        property.checking = false;
        setTimeout(() => lucide.createIcons(), 50);
      }
    },

    // Check all properties live on portals
    async checkAllAvailability() {
      this.checkingAll = true;
      try {
        const res = await fetch('/api/properties/check-all-availability', { method: 'POST' });
        const result = await res.json();
        await this.fetchProperties();
        alert(`Verfügbarkeits-Prüfung abgeschlossen:\n${result.online_count} von ${result.total_checked} Inseraten sind live erreichbar.\n${result.offline_count} Inserate wurden auf den Portalen deaktiviert oder archiviert.`);
      } catch (err) {
        console.error('Error batch-checking availability:', err);
      } finally {
        this.checkingAll = false;
      }
    },

    async toggleFavorite(property) {
      try {
        const res = await fetch(`/api/properties/${property.id}/toggle-favorite`, { method: 'POST' });
        const data = await res.json();
        property.is_favorite = data.is_favorite;
        setTimeout(() => lucide.createIcons(), 50);
      } catch (err) {
        console.error('Error toggling favorite:', err);
      }
    },

    async openDetailModal(property, view = 'audit', updateHistory = true) {
      if (!property) return;
      try {
        const propId = property.id || property;
        const res = await fetch(`/api/properties/${propId}`);
        if (!res.ok) {
          console.error(`Deal #${propId} konnte nicht geladen werden`);
          return;
        }
        this.selectedProperty = await res.json();
        if (this.selectedProperty.image_analysis && Array.isArray(this.selectedProperty.image_analysis.analyzed_photos)) {
          this.selectedProperty.image_analysis.analyzed_photos.forEach(p => {
            p.tag = this.refinePhotoTag(p.tag, p.description, p.findings, p.is_blueprint);
          });
        }
        this.showFullDesc = false;
        this.detailModalOpen = true;
        this.modalActiveSection = view || 'audit';
        this.currentModalView = view || 'audit';

        this.syncUrl(updateHistory);

        setTimeout(() => {
          if (window.lucide) lucide.createIcons();
          if (view === 'audit') {
            this.scrollToSection('ki-audit-section', false);
          } else if (view === 'gallery') {
            this.scrollToSection('gallery-section', false);
          }
        }, 150);

        // Auto-run AI audit if not yet available
        if (!this.selectedProperty.ai_analysis) {
          this.triggerAiAudit(this.selectedProperty, false);
        } else if (!this.selectedProperty.image_analysis && this.selectedProperty.ai_analysis.image_analysis) {
          this.selectedProperty.image_analysis = this.selectedProperty.ai_analysis.image_analysis;
        }
      } catch (err) {
        console.error('Error fetching property details:', err);
      }
    },

    closeDetailModal() {
      this.detailModalOpen = false;
      this.selectedProperty = null;
      this.isModalFullscreen = false;
      if (!this._isPopStateNavigation) {
        this.syncUrl(true);
      }
    },

    initRouter() {
      const params = new URLSearchParams(window.location.search);
      const tab = params.get('tab');
      if (tab && ['feed', 'board', 'calculator', 'map', 'alerts', 'info'].includes(tab)) {
        this.activeTab = tab;
      }

      const pathParts = window.location.pathname.split('/').filter(Boolean);
      let dealFromPath = null;
      if (pathParts.length >= 2 && (pathParts[0] === 'deal' || pathParts[0] === 'property')) {
        dealFromPath = pathParts[1];
      }

      const dealId = params.get('deal') || params.get('id') || dealFromPath;
      const view = params.get('view') || 'audit';
      if (params.get('fullscreen') === '1' || params.get('fullscreen') === 'true') {
        this.isModalFullscreen = true;
      }

      if (dealId) {
        this.openDealById(dealId, view, false);
      }

      window.addEventListener('popstate', (e) => {
        this.handlePopState();
      });
    },

    syncUrl(push = false) {
      try {
        const url = new URL(window.location.origin + window.location.pathname);
        if (this.detailModalOpen && this.selectedProperty) {
          url.searchParams.set('deal', this.selectedProperty.id);
          if (this.modalActiveSection && this.modalActiveSection !== 'overview') {
            url.searchParams.set('view', this.modalActiveSection);
          }
          if (this.activeTab && this.activeTab !== 'feed') {
            url.searchParams.set('tab', this.activeTab);
          }
          if (this.isModalFullscreen) {
            url.searchParams.set('fullscreen', '1');
          }
          const priceFormatted = this.selectedProperty.purchase_price ? Math.round(this.selectedProperty.purchase_price).toLocaleString('de-DE') + ' €' : '';
          document.title = `${this.selectedProperty.title} (${priceFormatted}) | ImmoMetrica`;
        } else {
          url.searchParams.delete('deal');
          url.searchParams.delete('id');
          url.searchParams.delete('view');
          url.searchParams.delete('fullscreen');
          if (this.activeTab && this.activeTab !== 'feed') {
            url.searchParams.set('tab', this.activeTab);
          }
          document.title = 'ImmoMetrica - Intelligente Immobilien-Analyse';
        }

        const stateObj = { 
          dealId: this.selectedProperty?.id, 
          tab: this.activeTab, 
          view: this.modalActiveSection,
          fullscreen: this.isModalFullscreen
        };

        if (push) {
          window.history.pushState(stateObj, '', url.toString());
        } else {
          window.history.replaceState(stateObj, '', url.toString());
        }
      } catch (e) {
        console.warn('History navigation sync error:', e);
      }
    },

    handlePopState() {
      this._isPopStateNavigation = true;
      try {
        const params = new URLSearchParams(window.location.search);
        const pathParts = window.location.pathname.split('/').filter(Boolean);
        let dealFromPath = null;
        if (pathParts.length >= 2 && (pathParts[0] === 'deal' || pathParts[0] === 'property')) {
          dealFromPath = pathParts[1];
        }

        const dealId = params.get('deal') || params.get('id') || dealFromPath;
        const view = params.get('view') || 'audit';
        const tab = params.get('tab');

        if (tab && ['feed', 'board', 'calculator', 'map', 'alerts', 'info'].includes(tab)) {
          this.activeTab = tab;
        }

        if (dealId) {
          const numId = Number(dealId);
          if (!this.selectedProperty || this.selectedProperty.id !== numId) {
            this.openDealById(numId, view, false);
          } else {
            this.detailModalOpen = true;
            this.modalActiveSection = view;
            if (view === 'audit') {
              this.scrollToSection('ki-audit-section', false);
            } else if (view === 'gallery') {
              this.scrollToSection('gallery-section', false);
            }
          }
        } else if (this.detailModalOpen) {
          this.detailModalOpen = false;
          this.selectedProperty = null;
          this.isModalFullscreen = false;
          document.title = 'ImmoMetrica - Intelligente Immobilien-Analyse';
        }
      } finally {
        setTimeout(() => {
          this._isPopStateNavigation = false;
        }, 80);
      }
    },

    async openDealById(dealId, view = 'audit', updateHistory = false) {
      const numId = Number(dealId);
      const existing = this.properties.find(p => p.id === numId);
      if (existing) {
        await this.openDetailModal(existing, view, updateHistory);
      } else {
        await this.openDetailModal({ id: numId }, view, updateHistory);
      }
    },

    copyDealShareLink(property, view = 'audit') {
      if (!property) return;
      const url = new URL(window.location.origin + window.location.pathname);
      url.searchParams.set('deal', property.id);
      if (view) url.searchParams.set('view', view);
      if (this.activeTab && this.activeTab !== 'feed') url.searchParams.set('tab', this.activeTab);

      const linkStr = url.toString();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(linkStr).then(() => {
          this.showToast('🔗 Link mit geöffnetem KI-Audit kopiert! Bereit zum Teilen oder Öffnen im neuen Tab.', 'success');
        }).catch(() => {
          prompt('Hier ist dein Direktlink zum Teilen:', linkStr);
        });
      } else {
        prompt('Hier ist dein Direktlink zum Teilen:', linkStr);
      }
    },

    scrollToSection(sectionId, updateUrl = true) {
      if (sectionId.includes('audit')) {
        this.modalActiveSection = 'audit';
      } else if (sectionId.includes('gallery')) {
        this.modalActiveSection = 'gallery';
      } else {
        this.modalActiveSection = 'overview';
      }
      if (updateUrl) {
        this.syncUrl(false);
      }
      setTimeout(() => {
        const modalContainer = document.querySelector('.immo-modal-container');
        const target = document.getElementById(sectionId);
        if (target && modalContainer) {
          const topPos = target.offsetTop - modalContainer.offsetTop - 15;
          modalContainer.scrollTo({ top: topPos, behavior: 'smooth' });
        } else if (target) {
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }, 50);
    },

    async triggerAiAudit(property, forceRefresh = false) {
      if (!property) return;
      this.isAuditing = true;
      try {
        const headers = { 'Content-Type': 'application/json' };
        if (this.geminiApiKey && this.geminiApiKey.trim() && !this.geminiApiKey.includes('...') && !this.geminiApiKey.includes('***')) {
          headers['X-Gemini-API-Key'] = this.geminiApiKey.trim();
        }
        const res = await fetch(`/api/properties/${property.id}/ai-audit?force_refresh=${forceRefresh}`, {
          method: 'POST',
          headers
        });
        const data = await res.json();
        if (data.ai_audit) {
          property.ai_analysis = data.ai_audit;
          if (data.image_analysis) {
            property.image_analysis = data.image_analysis;
          }
          if (data.property_updates) {
            Object.assign(property, data.property_updates);
          }
          if (this.selectedProperty && this.selectedProperty.id === property.id) {
            this.selectedProperty.ai_analysis = data.ai_audit;
            if (data.image_analysis) {
              this.selectedProperty.image_analysis = data.image_analysis;
            }
            if (data.property_updates) {
              Object.assign(this.selectedProperty, data.property_updates);
            }
            // Also refresh property description if newly extracted
            if (data.ai_audit.description_length > 0 && !this.selectedProperty.description) {
              const fullRes = await fetch(`/api/properties/${property.id}`);
              const fullData = await fullRes.json();
              this.selectedProperty.description = fullData.description;
            }
          }
          const idx = this.properties.findIndex(p => p.id === property.id);
          if (idx !== -1) {
            this.properties[idx].ai_analysis = data.ai_audit;
            if (data.image_analysis) {
              this.properties[idx].image_analysis = data.image_analysis;
            }
            if (data.property_updates) {
              Object.assign(this.properties[idx], data.property_updates);
            }
          }
          if (this.isOneProvider) {
            this.refreshOneProviderBalance();
          }
        }
      } catch (err) {
        console.error('Error running AI audit:', err);
      } finally {
        this.isAuditing = false;
        setTimeout(() => lucide.createIcons(), 50);
      }
    },

    async updatePropertyPrice(propertyId, newPrice) {
      if (!newPrice || newPrice <= 0) return;
      this.isAuditing = true;
      try {
        const res = await fetch(`/api/properties/${propertyId}/update-price`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ purchase_price: newPrice })
        });
        if (!res.ok) throw new Error('Fehler beim Aktualisieren des Kaufpreises');
        const data = await res.json();
        await this.fetchProperties();
        if (this.selectedProperty && this.selectedProperty.id === propertyId) {
          await this.openDetailModalById(propertyId);
        }
        this.showToast(`Kaufpreis erfolgreich auf ${this.formatCurrency(newPrice)} aktualisiert & neu kalkuliert!`, 'success');
      } catch (err) {
        console.error('Update price error:', err);
        this.showToast('Fehler beim Aktualisieren: ' + err.message, 'error');
      } finally {
        this.isAuditing = false;
        setTimeout(() => lucide.createIcons(), 50);
      }
    },

    formatPropertyAddress(prop) {
      if (!prop) return '';
      const addr = (prop.address || '').trim();
      const city = (prop.city || '').trim();
      const plz = (prop.postal_code || '').trim();
      if (!addr) {
        return `${plz} ${city}`.trim();
      }
      const isPlaceholder = /(straße und hausnummer nicht angegeben|die vollständige adresse|vollständige adresse|straße nicht angegeben|hausnummer nicht angegeben|nicht angegeben|auf anfrage|keine angabe)/i.test(addr);
      if (isPlaceholder) {
        return `${plz} ${city}`.trim();
      }
      const hasCity = Boolean(city && addr.toLowerCase().includes(city.toLowerCase()));
      const hasPlz = Boolean(plz && addr.includes(plz));
      if (hasCity && hasPlz) {
        return addr;
      }
      if (hasCity) {
        return plz && !addr.includes(plz) ? `${plz} ${addr}` : addr;
      }
      return `${addr}, ${plz} ${city}`.trim().replace(/^,\s*/, '');
    },

    getGoogleMapsUrl(prop) {
      if (!prop) return '#';
      if (prop.ai_analysis && prop.ai_analysis.location_analysis && prop.ai_analysis.location_analysis.maps && prop.ai_analysis.location_analysis.maps.direct_url) {
        return prop.ai_analysis.location_analysis.maps.direct_url;
      }
      const addr = (prop.address || '').trim();
      const city = (prop.city || '').trim();
      const plz = (prop.postal_code || '').trim();
      const hasHouseNumber = /\d+/.test(addr);
      const hasStreet = /(straße|strasse|str\b|str\.|weg|allee|gasse|platz|ring|damm|ufer|pfad|chaussee|hellweg|steig|steg|promenade|graben|kamp|reihe)/i.test(addr);
      const isGeneric = /^(auf anfrage|keine angabe|zentrum|innenstadt|stadt|mikrolage|straße und hausnummer nicht angegeben|nicht angegeben)$/i.test(addr) || /(nicht angegeben|auf anfrage|keine angabe|vom anbieter)/i.test(addr);

      if (addr && (hasHouseNumber || hasStreet) && !isGeneric) {
        const fullQuery = (city && addr.toLowerCase().includes(city.toLowerCase()))
          ? addr
          : `${addr}${plz ? ', ' + plz : ''} ${city}`.trim();
        return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(fullQuery.replace(/\s+/g, ' '))}`;
      }
      if (prop.lat && prop.lng && (Math.abs(Number(prop.lat)) > 0.001 || Math.abs(Number(prop.lng)) > 0.001)) {
        return `https://www.google.com/maps/search/?api=1&query=${Number(prop.lat).toFixed(6)},${Number(prop.lng).toFixed(6)}`;
      }
      const fallbackQuery = `${addr && !isGeneric ? addr + ', ' : ''}${plz} ${city}`.trim();
      return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(fallbackQuery || city || 'Deutschland')}`;
    },

    isSearchOverviewUrl(url) {
      if (!url) return false;
      const isSearch = /\/(?:suche|search|s-immobilien|s-wohnung-kaufen|s-haus-kaufen)\b/i.test(url);
      const hasExpose = /\/(?:expose|s-anzeige)\/\w+/i.test(url);
      return isSearch && !hasExpose;
    },

    async triggerImageAnalysis(propertyId) {
      if (!propertyId) return;
      if (!this.geminiApiKey && !this.hasServerApiKey) {
        this.openApiKeyModal(propertyId);
        return;
      }
      this.isAnalyzingImages = true;
      try {
        const keyToSend = (this.geminiApiKey && !this.geminiApiKey.includes('...') && !this.geminiApiKey.includes('***')) 
          ? this.geminiApiKey 
          : undefined;
        const res = await fetch(`/api/properties/${propertyId}/analyze-images`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(keyToSend ? { 'X-Gemini-API-Key': keyToSend } : {})
          },
          body: JSON.stringify({ api_key: keyToSend })
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || 'Fehler bei der KI-Bildanalyse');
        }
        if (data.image_analysis) {
          if (this.selectedProperty && this.selectedProperty.id === propertyId) {
            this.selectedProperty.image_analysis = data.image_analysis;
            if (data.ai_audit) {
              this.selectedProperty.ai_analysis = data.ai_audit;
            } else if (this.selectedProperty.ai_analysis) {
              this.selectedProperty.ai_analysis.image_analysis = data.image_analysis;
            }
          }
          const idx = this.properties.findIndex(p => p.id === propertyId);
          if (idx !== -1) {
            this.properties[idx].image_analysis = data.image_analysis;
            if (data.ai_audit) {
              this.properties[idx].ai_analysis = data.ai_audit;
            }
          }
          if (this.isOneProvider) {
            this.refreshOneProviderBalance();
          }
        }
      } catch (err) {
        console.error('Error analyzing images:', err);
        alert(err.message);
        if (err.message.includes('API-Key') || err.message.includes('400') || err.message.includes('403')) {
          this.openApiKeyModal(propertyId);
        }
      } finally {
        this.isAnalyzingImages = false;
        setTimeout(() => lucide.createIcons(), 50);
      }
    },

    copyDescription(text) {
      if (!text) return;
      navigator.clipboard.writeText(text);
      alert('Vollständige Beschreibung in die Zwischenablage kopiert!');
    },

    // 1-Klick Preset-Filter für gezielte Deal-Suche
    applyPreset(type) {
      this.resetFilters();
      if (type === 'top_yield') {
        this.filters.min_yield = '6.0';
        this.filters.sort_by = 'gross_yield';
      } else if (type === 'positive_cashflow') {
        this.filters.positive_cashflow = true;
        this.filters.sort_by = 'monthly_cashflow';
      } else if (type === 'private') {
        this.filters.private_only = true;
      } else if (type === 'undervalued') {
        this.filters.undervalued_only = true;
        this.filters.sort_by = 'market_deviation';
      } else if (type === 'online') {
        this.filters.online_only = true;
      }
      this.fetchProperties();
    },

    // Kaufangebot / Letter of Intent (LOI) & Vorab-Unterlagen Workflow (2 Schritte)
    async changePropertyStage(propertyId, stageId) {
      await this.moveDeal(propertyId, stageId);
      if (this.selectedProperty && this.selectedProperty.id === propertyId) {
        this.selectedProperty.pipeline_stage = stageId;
      }
      const p = this.properties.find(x => x.id === propertyId);
      if (p) p.pipeline_stage = stageId;
    },

    // Automatically transfer property data to calculator without prompting for Bundesland!
    async moveDeal(propertyId, targetStage) {
      try {
        await fetch('/api/pipeline/move', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ property_id: propertyId, target_stage: targetStage })
        });
        await this.fetchBoard();
      } catch (err) {
        console.error('Error moving deal:', err);
      }
    },

    // Drag and drop handlers for Kanban board
    dragStart(event, property) {
      event.dataTransfer.setData('text/plain', property.id);
      event.target.classList.add('dragging');
    },

    dragEnd(event) {
      event.target.classList.remove('dragging');
    },

    dragOver(event) {
      event.preventDefault();
    },

    async dropDeal(event, targetStageId) {
      event.preventDefault();
      const propertyId = parseInt(event.dataTransfer.getData('text/plain'));
      if (propertyId) {
        await this.moveDeal(propertyId, targetStageId);
      }
    },

    openDetailModalById(propertyId) {
      const idNum = Number(propertyId);
      const prop = this.properties.find(p => p.id === idNum);
      if (prop) {
        this.openDetailModal(prop);
      } else {
        this.openDetailModal({ id: idNum });
      }
    },

    async fetchMcpStatus() {
      try {
        const res = await fetch('/mcp/status');
        if (res.ok) {
          const data = await res.json();
          this.mcpToken = data.token || '';
          this.mcpQuotaUsed = data.quota_used !== undefined ? data.quota_used : 0;
          this.mcpQuotaLimit = data.quota_limit !== undefined ? data.quota_limit : 2000;
          const snippetObj = data.config_json || data.snippet || {
            mcpServers: {
              immometrica: {
                url: 'http://127.0.0.1:8000/mcp/call',
                headers: {
                  Authorization: `Bearer ${this.mcpToken}`
                }
              }
            }
          };
          this.mcpSnippet = JSON.stringify(snippetObj, null, 2);
        }
      } catch (err) {
        console.error('Error fetching MCP status:', err);
      }
    },

    async regenerateMcpToken() {
      if (!confirm('Möchtest Du wirklich einen neuen MCP-API-Token generieren? Der alte Token wird dadurch ungültig.')) return;
      this.mcpRegenerating = true;
      try {
        const res = await fetch('/mcp/regenerate-token', { method: 'POST' });
        if (res.ok) {
          const data = await res.json();
          this.mcpToken = data.token || '';
          this.mcpQuotaUsed = data.quota_used !== undefined ? data.quota_used : 0;
          this.mcpQuotaLimit = data.quota_limit !== undefined ? data.quota_limit : 2000;
          const snippetObj = data.config_json || data.snippet || {
            mcpServers: {
              immometrica: {
                url: 'http://127.0.0.1:8000/mcp/call',
                headers: {
                  Authorization: `Bearer ${this.mcpToken}`
                }
              }
            }
          };
          this.mcpSnippet = JSON.stringify(snippetObj, null, 2);
          this.showToast('Neuer MCP-Token erfolgreich generiert!', 'success');
        } else {
          this.showToast('Fehler beim Generieren des Tokens', 'error');
        }
      } catch (err) {
        console.error('Error regenerating MCP token:', err);
        this.showToast('Fehler: ' + err.message, 'error');
      } finally {
        this.mcpRegenerating = false;
        setTimeout(() => lucide.createIcons(), 50);
      }
    },

    copyMcpSnippet() {
      const snippet = this.mcpSnippet || JSON.stringify({
        "mcpServers": {
          "immometrica": {
            "url": "http://127.0.0.1:8000/mcp/call",
            "headers": {
              "Authorization": `Bearer ${this.mcpToken}`
            }
          }
        }
      }, null, 2);

      navigator.clipboard.writeText(snippet);
      this.mcpCopied = true;
      setTimeout(() => { this.mcpCopied = false; }, 2500);
    },

    exportCsvUrl() {
      const params = new URLSearchParams();
      if (this.filters.state) params.append('state', this.filters.state);
      if (this.filters.city) params.append('city', this.filters.city);
      if (this.filters.online_only) params.append('online_only', 'true');
      return `/api/export/csv?${params.toString()}`;
    },

    formatCurrency(val, includeCents = false) {
      if (val === null || val === undefined || isNaN(val)) return '0 €';
      const num = Number(val);
      return num.toLocaleString('de-DE', {
        style: 'currency',
        currency: 'EUR',
        minimumFractionDigits: includeCents ? 2 : 0,
        maximumFractionDigits: includeCents ? 2 : 0
      });
    },

    formatNumber(val, decimals = 1) {
      if (val === null || val === undefined || isNaN(val)) return '0';
      const num = Number(val);
      return num.toLocaleString('de-DE', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    },

    formatPercent(val, decimals = 2) {
      if (val === null || val === undefined || isNaN(val)) return '0,00 %';
      const num = Number(val);
      return num.toLocaleString('de-DE', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + ' %';
    },

    formatSignedCurrency(val, includeCents = false) {
      if (val === null || val === undefined || isNaN(val)) return '0 €';
      const num = Number(val);
      const sign = num > 0 ? '+' : '';
      return `${sign}${this.formatCurrency(num, includeCents)}`;
    },

    formatSignedPercent(val, decimals = 1) {
      if (val === null || val === undefined || isNaN(val)) return '0,0 %';
      const num = Number(val);
      const sign = num > 0 ? '+' : '';
      return `${sign}${this.formatPercent(num, decimals)}`;
    },

    formatCashflow(val, perMonth = true) {
      if (val === null || val === undefined || isNaN(val)) return '0 €';
      const num = Math.round(Number(val));
      const sign = num > 0 ? '+' : '';
      return `${sign}${num.toLocaleString('de-DE')} €${perMonth ? '/M' : ''}`;
    },

    formatDeviation(val) {
      if (val === null || val === undefined || isNaN(val)) return '0,0 %';
      const num = Number(val);
      const sign = num > 0 ? '+' : '';
      return `${sign}${num.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
    },

    formatSqM(val) {
      if (val === null || val === undefined || isNaN(val)) return '-';
      const num = Number(val);
      const dec = num % 1 === 0 ? 0 : 1;
      return num.toLocaleString('de-DE', { minimumFractionDigits: dec, maximumFractionDigits: dec }) + ' m²';
    },

    formatRooms(val) {
      if (val === null || val === undefined || isNaN(val)) return '-';
      const num = Number(val);
      const dec = num % 1 === 0 ? 0 : 1;
      return num.toLocaleString('de-DE', { minimumFractionDigits: dec, maximumFractionDigits: dec }) + ' Zi.';
    },

    isHouseOrPlot(prop) {
      if (!prop) return false;
      const pType = String(prop.property_type || '').toLowerCase();
      const bType = String(prop.building_type || '').toLowerCase();
      const combined = `${pType} ${bType}`;
      if (combined.includes('wohnung') || combined.includes('paket') || combined.includes('maisonette') || combined.includes('penthouse') || combined.includes('etw')) {
        return false;
      }
      return (
        combined.includes('haus') ||
        combined.includes('grundstück') ||
        combined.includes('grundstueck') ||
        combined.includes('villa') ||
        combined.includes('bungalow') ||
        combined.includes('mfh') ||
        combined.includes('efh')
      );
    },

    formatLandAreaDisplay(prop) {
      if (!prop) return '-';
      if (!this.isHouseOrPlot(prop)) {
        return 'Wohnungseigentum (MEA) / k. A.';
      }
      const area = Number(prop.land_area || prop.parcel_area || 0);
      if (area > 0) {
        return this.formatSqM(area);
      }
      return '–';
    },

    formatParcelInfo(prop) {
      if (!prop) return '–';
      const pid = prop.parcel_id ? String(prop.parcel_id) : '';
      if (pid && !pid.startsWith('KAT-')) {
        return pid;
      }
      if (!this.isHouseOrPlot(prop)) {
        return 'WEG (Miteigentumsanteil)';
      }
      return '–';
    },

    getPipelineDecisionReason(deal) {
      if (!deal) return '';
      if (deal.ai_analysis?.pipeline_decision?.reason) {
        return deal.ai_analysis.pipeline_decision.reason;
      }
      const sm = String(deal.status_message || '');
      const isOnlineStatus = sm.toLowerCase().includes('live erreichbar') || 
                             sm.toLowerCase().includes('aktiv') || 
                             sm.toLowerCase().includes('inserat aktiv') || 
                             sm.toLowerCase().includes('verifiziert');
      if (deal.pipeline_stage === 'archiv') {
        if (!isOnlineStatus && (sm.toLowerCase().includes('gelöscht') || sm.toLowerCase().includes('offline') || sm.toLowerCase().includes('deaktiviert') || sm.toLowerCase().includes('abgelehnt'))) {
          return sm;
        }
        if (deal.monthly_cashflow !== undefined && deal.monthly_cashflow !== null && Number(deal.monthly_cashflow) < 0) {
          const cfFormatted = this.formatCashflow(deal.monthly_cashflow);
          const yld = deal.gross_yield ? `${Number(deal.gross_yield).toFixed(1)} %` : 'gering';
          return `Negativer Cashflow (${cfFormatted}) und Rendite (${yld}) verfehlen das Ankaufsprofil.`;
        }
        return 'Manuell ins Archiv verschoben oder Kriterien des Ankaufsprofils nicht erfüllt.';
      }
      if (!isOnlineStatus && sm) {
        return sm;
      }
      return 'Erfolgreich vorqualifiziert.';
    },

    async logout() {
      if (!confirm('Möchtest du dich wirklich abmelden?')) return;
      try {
        await fetch('/api/auth/logout', { method: 'POST' });
      } catch (e) {
        console.error('Logout error:', e);
      } finally {
        window.location.href = '/login';
      }
    }
  };

  // Merge sub-modules preserving reactive getters and setters
  [window.immoMapModule, window.immoCalculatorModule, window.immoAlertsModule, window.immoModalsModule].forEach(mod => {
    if (mod) {
      Object.defineProperties(baseApp, Object.getOwnPropertyDescriptors(mod));
    }
  });

  return baseApp;
});
});
