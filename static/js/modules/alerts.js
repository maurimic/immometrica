/**
 * ImmoMetrica - Alerts Module
 * Search profiles, multi-portal crawler triggers, live deal alerts & Telegram notifications.
 */
window.immoAlertsModule = {
  // Search Profiles & Deal Alerts State
  searchProfiles: [],
  alertFeed: [],
  alertSettings: {
    telegram_configured: false,
    telegram_masked_token: null,
    telegram_chat_id: '',
    crawler_interval_minutes: 30,
    crawler_active: false,
    crawler_last_run: null,
    crawler_is_busy: false
  },
  telegramInputToken: '',
  telegramInputChatId: '',
  telegramTesting: false,
  telegramTestResult: null,
  savingAlertSettings: false,
  scanningProfiles: false,
  runningProfileId: null,
  lastScanStats: null,
  searchProfileModalOpen: false,
  editingProfile: null,
  profileFormData: {
    id: null,
    name: '',
    city: '',
    postal_code: '',
    radius_km: 25,
    max_price: '',
    max_price_per_sqm: '',
    min_yield: '',
    yield_basis: 'any',
    min_discount_pct: '',
    min_rooms: 1,
    min_living_space: '',
    property_type: 'all',
    search_query: '',
    is_active: true
  },
  alertFilterProfileId: '',

  async loadAlertsData() {
    try {
      const [profRes, feedRes, settRes] = await Promise.all([
        fetch('/api/alerts/profiles').then(r => r.json()),
        fetch('/api/alerts/feed').then(r => r.json()),
        fetch('/api/alerts/settings').then(r => r.json())
      ]);
      if (profRes && profRes.success) this.searchProfiles = profRes.profiles || [];
      if (feedRes && feedRes.success) this.alertFeed = feedRes.alerts || [];
      if (settRes) {
        this.alertSettings = settRes;
        this.telegramInputChatId = String(settRes.telegram_chat_id || '').trim();
        if (settRes.crawler_last_stats) {
          this.lastScanStats = settRes.crawler_last_stats;
        }
      }
    } catch (e) {
      console.error('Error loading alerts data:', e);
    }
  },

  openNewProfileModal() {
    this.editingProfile = null;
    this.profileFormData = {
      id: null,
      name: '',
      city: 'Dortmund',
      postal_code: '',
      radius_km: 25,
      max_price: '',
      max_price_per_sqm: '',
      min_yield: 6.0,
      yield_basis: 'any',
      min_discount_pct: 10.0,
      min_rooms: 1,
      min_living_space: 40,
      property_type: 'all',
      search_query: '',
      is_active: true
    };
    this.searchProfileModalOpen = true;
    setTimeout(() => {
      if (window.lucide) window.lucide.createIcons();
    }, 50);
  },

  openEditProfileModal(profile) {
    this.editingProfile = profile;
    this.profileFormData = {
      id: profile.id,
      name: profile.name,
      city: profile.city,
      postal_code: profile.postal_code || '',
      radius_km: profile.radius_km || 25,
      max_price: profile.max_price || '',
      max_price_per_sqm: profile.max_price_per_sqm || '',
      min_yield: profile.min_yield || '',
      yield_basis: profile.yield_basis || 'any',
      min_discount_pct: profile.min_discount_pct || '',
      min_rooms: profile.min_rooms || 1,
      min_living_space: profile.min_living_space || '',
      property_type: profile.property_type || 'all',
      search_query: profile.search_query || '',
      is_active: Boolean(profile.is_active)
    };
    this.searchProfileModalOpen = true;
    setTimeout(() => {
      if (window.lucide) window.lucide.createIcons();
    }, 50);
  },

  async saveSearchProfileForm() {
    if (!this.profileFormData.name || !this.profileFormData.city) {
      alert('Bitte gib mindestens einen Namen und eine Zielstadt ein.');
      return;
    }
    try {
      const payload = {
        name: String(this.profileFormData.name || '').trim(),
        city: String(this.profileFormData.city || '').trim(),
        postal_code: String(this.profileFormData.postal_code || '').trim(),
        radius_km: Number(this.profileFormData.radius_km) || 25,
        max_price: this.profileFormData.max_price !== '' && !isNaN(Number(this.profileFormData.max_price)) ? Number(this.profileFormData.max_price) : 0,
        max_price_per_sqm: this.profileFormData.max_price_per_sqm !== '' && !isNaN(Number(this.profileFormData.max_price_per_sqm)) ? Number(this.profileFormData.max_price_per_sqm) : 0,
        min_yield: this.profileFormData.min_yield !== '' && !isNaN(Number(this.profileFormData.min_yield)) ? Number(this.profileFormData.min_yield) : 0,
        yield_basis: String(this.profileFormData.yield_basis || 'any').trim().toLowerCase(),
        min_discount_pct: this.profileFormData.min_discount_pct !== '' && !isNaN(Number(this.profileFormData.min_discount_pct)) ? Number(this.profileFormData.min_discount_pct) : 0,
        min_rooms: Number(this.profileFormData.min_rooms) || 1,
        min_living_space: this.profileFormData.min_living_space !== '' && !isNaN(Number(this.profileFormData.min_living_space)) ? Number(this.profileFormData.min_living_space) : 0,
        property_type: this.profileFormData.property_type || 'all',
        search_query: String(this.profileFormData.search_query || '').trim(),
        is_active: Boolean(this.profileFormData.is_active)
      };

      let res, data;
      if (this.editingProfile && this.editingProfile.id) {
        res = await fetch(`/api/alerts/profiles/${this.editingProfile.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
      } else {
        res = await fetch('/api/alerts/profiles', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
      }

      const resText = await res.text();
      try {
        data = JSON.parse(resText);
      } catch {
        throw new Error(resText || `Serverfehler (${res.status})`);
      }

      if (!res.ok || !data.success) {
        throw new Error(data.detail || data.message || `Fehler beim Speichern (${res.status})`);
      }

      this.searchProfileModalOpen = false;
      await this.loadAlertsData();
      setTimeout(() => {
        if (window.lucide) window.lucide.createIcons();
      }, 50);
    } catch (e) {
      alert('Fehler beim Speichern des Suchprofils: ' + e.message);
    }
  },

  async toggleProfileActive(profile) {
    const newActive = !profile.is_active;
    profile.is_active = newActive ? 1 : 0;
    try {
      await fetch(`/api/alerts/profiles/${profile.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: newActive })
      });
    } catch (e) {
      console.error('Error toggling profile:', e);
      profile.is_active = !newActive ? 1 : 0;
    }
  },

  async deleteProfile(profile) {
    if (!confirm(`Möchtest du das Suchprofil "${profile.name}" wirklich unwiderruflich löschen?`)) {
      return;
    }
    try {
      await fetch(`/api/alerts/profiles/${profile.id}`, { method: 'DELETE' });
      await this.loadAlertsData();
      setTimeout(() => {
        if (window.lucide) window.lucide.createIcons();
      }, 50);
    } catch (e) {
      alert('Fehler beim Löschen des Profils: ' + e.message);
    }
  },

  async runSearchProfiles(profileId = null) {
    this.scanningProfiles = true;
    this.runningProfileId = profileId;
    try {
      const url = profileId ? `/api/alerts/profiles/run?profile_id=${profileId}` : '/api/alerts/profiles/run';
      const res = await fetch(url, { method: 'POST' });
      let data;
      const rawText = await res.text();
      try {
        data = JSON.parse(rawText);
      } catch (err) {
        throw new Error(res.ok ? 'Ungültige Server-Antwort (kein JSON)' : `Serverfehler (${res.status}): ${rawText.slice(0, 150)}`);
      }
      if (!res.ok) {
        throw new Error(data.detail || data.error || `Serverfehler (${res.status})`);
      }
      if (data.stats) {
        this.lastScanStats = data.stats;
      } else if (data.results && data.results[0] && data.results[0].stats) {
        this.lastScanStats = data.results[0].stats;
      }
      await this.loadAlertsData();
      await this.fetchProperties();
      if (this.isOneProvider) {
        await this.refreshOneProviderBalance();
      }
      setTimeout(() => {
        if (window.lucide) window.lucide.createIcons();
      }, 50);
      if (data.success) {
        alert(`✅ Suchlauf abgeschlossen: ${data.message}`);
      } else {
        alert(`⚠️ Suchlauf beendet: ${data.detail || data.error || 'Fehler'}`);
      }
    } catch (e) {
      alert('Fehler beim Ausführen des Suchlaufs: ' + e.message);
    } finally {
      this.scanningProfiles = false;
      this.runningProfileId = null;
    }
  },

  async saveAlertSettingsForm() {
    this.savingAlertSettings = true;
    try {
      const cleanChatId = String(this.telegramInputChatId || '').trim();
      const payload = {
        telegram_chat_id: cleanChatId,
        crawler_interval_minutes: Number(this.alertSettings.crawler_interval_minutes),
        crawler_active: Boolean(this.alertSettings.crawler_active)
      };
      if (this.telegramInputToken && this.telegramInputToken.trim()) {
        payload.telegram_bot_token = this.telegramInputToken.trim();
      }
      const res = await fetch('/api/alerts/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.success) {
        this.telegramInputToken = '';
        this.alertSettings.telegram_chat_id = cleanChatId;
        this.telegramInputChatId = cleanChatId;
        await this.loadAlertsData();
        alert(`✅ Einstellungen gespeichert!\nChat-ID: ${cleanChatId || '(keine angegeben)'}`);
      } else {
        alert('Fehler: ' + (data.detail || 'Konnte Einstellungen nicht speichern.'));
      }
    } catch (e) {
      alert('Fehler beim Speichern: ' + e.message);
    } finally {
      this.savingAlertSettings = false;
    }
  },

  async testTelegramConnection() {
    this.telegramTesting = true;
    this.telegramTestResult = null;
    try {
      const payload = {
        telegram_chat_id: String(this.telegramInputChatId || '').trim()
      };
      if (this.telegramInputToken && this.telegramInputToken.trim()) {
        payload.telegram_bot_token = this.telegramInputToken.trim();
      }
      const res = await fetch('/api/alerts/telegram/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      this.telegramTestResult = data;
    } catch (e) {
      this.telegramTestResult = { success: false, error: 'Netzwerkfehler: ' + e.message };
    } finally {
      this.telegramTesting = false;
    }
  },

  async openDealFromAlert(alertItem) {
    if (!alertItem) return;

    const propId = Number(alertItem.property_id);
    if (propId) {
      try {
        const chk = await fetch(`/api/properties/${propId}`);
        if (chk.ok) {
          await this.openDetailModalById(propId);
          return;
        }
      } catch (e) {
        console.warn('Property ID lookup failed:', e);
      }
    }

    // Check if property is in client-side properties list
    const prop = this.properties.find(p => (alertItem.property_id && p.id === Number(alertItem.property_id)) || (alertItem.portal_url && p.portal_url === alertItem.portal_url));
    if (prop && prop.id) {
      await this.openDetailModal(prop);
      return;
    }

    // Try resolving by portal_url from server DB
    if (alertItem.portal_url) {
      try {
        const res = await fetch(`/api/properties/by-url?url=${encodeURIComponent(alertItem.portal_url)}`);
        if (res.ok) {
          const data = await res.json();
          if (data && data.id) {
            await this.openDetailModal(data);
            return;
          }
        }
      } catch (e) {
        console.warn('Could not resolve deal by URL:', e);
      }

      // Auto-import into pipeline so user gets full AI audit immediately!
      try {
        const impRes = await fetch('/api/properties/import-url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: alertItem.portal_url })
        });
        if (impRes.ok) {
          const impData = await impRes.json();
          if (impData.property && impData.property.id) {
            await this.fetchProperties();
            await this.openDetailModal(impData.property);
            return;
          }
        }
      } catch (e) {
        console.warn('Auto-import from alert failed:', e);
      }

      window.open(alertItem.portal_url, '_blank');
    }
  },

  async deleteDealAlert(alertId) {
    if (!alertId) return;
    try {
      const res = await fetch(`/api/alerts/feed/${alertId}`, { method: 'DELETE' });
      let data = {};
      try { data = await res.json(); } catch (_) {}
      if (res.ok) {
        this.alertFeed = (this.alertFeed || []).filter(a => a.id !== alertId);
        setTimeout(() => {
          if (window.lucide) window.lucide.createIcons();
        }, 50);
      } else {
        alert('Fehler beim Löschen des Alert-Eintrags: ' + (data.detail || res.statusText));
      }
    } catch (e) {
      alert('Verbindungsfehler beim Löschen des Alerts: ' + e.message);
    }
  },

  async clearAllDealAlerts(profileId = null) {
    const activeFilter = profileId || this.alertFilterProfileId;
    const targetScope = activeFilter 
      ? 'für das ausgewählte Profil' 
      : 'aus dem gesamten Deal-Alert-Feed';
    const count = activeFilter 
      ? (this.alertFeed || []).filter(a => String(a.profile_id) === String(activeFilter)).length 
      : (this.alertFeed || []).length;
    
    if (count === 0) {
      alert('Es sind keine Einträge zum Löschen vorhanden.');
      return;
    }

    if (!confirm(`Möchtest du wirklich alle ${count} Einträge ${targetScope} unwiderruflich löschen?`)) {
      return;
    }

    try {
      const url = activeFilter ? `/api/alerts/feed?profile_id=${activeFilter}` : '/api/alerts/feed';
      const res = await fetch(url, { method: 'DELETE' });
      let data = {};
      try { data = await res.json(); } catch (_) {}
      if (res.ok) {
        if (activeFilter) {
          this.alertFeed = (this.alertFeed || []).filter(a => String(a.profile_id) !== String(activeFilter));
        } else {
          this.alertFeed = [];
        }
        setTimeout(() => {
          if (window.lucide) window.lucide.createIcons();
        }, 50);
      } else {
        alert('Fehler beim Leeren des Feeds: ' + (data.detail || res.statusText));
      }
    } catch (e) {
      alert('Verbindungsfehler beim Leeren des Feeds: ' + e.message);
    }
  },

  formatCostEur(val) {
    if (val === undefined || val === null) return '0,0000 €';
    return Number(val).toLocaleString('de-DE', { minimumFractionDigits: 4, maximumFractionDigits: 4 }) + ' €';
  },

  formatAlertTimestamp(ts) {
    if (!ts) return '';
    const str = String(ts).trim();
    const parts = str.split(' ');
    if (parts.length === 2) {
      const dParts = parts[0].split('-');
      const tParts = parts[1].split(':');
      if (dParts.length === 3 && tParts.length >= 2) {
        return `${dParts[2]}.${dParts[1]}.${dParts[0]} um ${tParts[0]}:${tParts[1]} Uhr`;
      }
    }
    return str.substring(0, 16);
  },

  formatScanTimestamp(ts) {
    if (!ts) return '';
    const str = String(ts).trim();
    const parts = str.split(' ');
    if (parts.length === 2) {
      const dParts = parts[0].split('-');
      const tParts = parts[1].split(':');
      if (dParts.length === 3 && tParts.length >= 2) {
        return `${dParts[2]}.${dParts[1]}. um ${tParts[0]}:${tParts[1]} Uhr`;
      }
    }
    return str.substring(0, 16);
  }
};
