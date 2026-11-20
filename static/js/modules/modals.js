/**
 * ImmoMetrica - Modals Module
 * Handles Lightbox photo gallery, LOI & doc-checklist generator, API key settings & URL importer modal.
 */
window.immoModalsModule = {
  // Multi-Provider AI API Key State (OneProvider & Google Gemini gleichzeitig)
  activeAiProvider: 'oneprovider', // 'oneprovider' | 'gemini'
  oneproviderKeyInput: '',
  geminiKeyInput: '',
  configuredOneProviderMasked: '',
  configuredGeminiMasked: '',
  hasOneProviderKey: false,
  hasGeminiKey: false,
  showTokenPricing: false,

  geminiApiKey: localStorage.getItem('immometrica_gemini_api_key') || '',
  showApiKeyModal: false,
  apiKeyInput: '',
  apiKeySaving: false,
  apiKeyFeedback: null,
  hasServerApiKey: false,
  isOneProvider: true,
  oneproviderBalance: null,
  oneproviderBalanceFormatted: '',
  oneproviderCurrency: 'USD',
  refreshingBalance: false,
  pendingPropertyIdForAnalysis: null,

  get hasActiveApiKey() {
    if (this.activeAiProvider === 'oneprovider') return Boolean(this.hasOneProviderKey);
    if (this.activeAiProvider === 'gemini') return Boolean(this.hasGeminiKey);
    return Boolean(this.geminiApiKey || this.hasServerApiKey);
  },

  get headerApiKeyLabel() {
    if (this.activeAiProvider === 'oneprovider' && this.hasOneProviderKey) {
      return 'OneProvider: Aktiv';
    }
    if (this.activeAiProvider === 'gemini' && this.hasGeminiKey) {
      return 'Gemini: Aktiv';
    }
    if (this.geminiApiKey || this.hasServerApiKey) {
      return this.isOneProvider ? 'OneProvider: Aktiv' : 'Gemini: Aktiv';
    }
    return '🔑 KI API-Key';
  },

  // Photo Lightbox Modal State
  lightboxOpen: false,
  lightboxPhotos: [],
  lightboxIndex: 0,

  // Kaufangebot / Letter of Intent (LOI) & Unterlagen-Anfrage State
  offerData: {
    property: null,
    step: 'docs',
    buyerName: 'Max Mustermann',
    buyerEmail: 'investor@beispiel.de',
    buyerPhone: '+49 170 12345678',
    recipientEmail: '',
    offeredPrice: 0,
    originalPrice: 0,
    discount: 0,
    validDays: 14,
    docChecklist: [],
    docRequestSubject: '',
    docRequestText: '',
    loiText: ''
  },

  // URL Import Modal State
  urlImportModalOpen: false,
  importUrlInput: '',
  isImportingUrl: false,
  importError: '',

  async openApiKeyModal(pendingPropId = null) {
    this.pendingPropertyIdForAnalysis = pendingPropId;
    this.apiKeyFeedback = null;
    this.oneproviderKeyInput = '';
    this.geminiKeyInput = '';
    this.showApiKeyModal = true;
    await this.checkSavedApiKey();
    if (this.hasOneProviderKey) {
      this.refreshOneProviderBalance();
    }
    setTimeout(() => {
      if (window.lucide) lucide.createIcons();
    }, 100);
  },

  async checkSavedApiKey() {
    try {
      const res = await fetch('/api/settings/gemini-key');
      const data = await res.json();
      this.hasServerApiKey = Boolean(data.has_key);
      this.activeAiProvider = data.active_provider || (data.is_oneprovider ? 'oneprovider' : 'gemini');
      this.isOneProvider = (this.activeAiProvider === 'oneprovider');

      if (data.oneprovider) {
        this.hasOneProviderKey = Boolean(data.oneprovider.has_key);
        this.configuredOneProviderMasked = data.oneprovider.masked_key || '';
      }
      if (data.gemini) {
        this.hasGeminiKey = Boolean(data.gemini.has_key);
        this.configuredGeminiMasked = data.gemini.masked_key || '';
      }
      if (data.masked_key && !data.masked_key.includes('...') && !data.masked_key.includes('***')) {
        this.geminiApiKey = data.masked_key;
      }
    } catch (e) {
      console.debug('Failed to check server API key status:', e);
    }
  },

  async selectActiveProvider(provider) {
    if (!['oneprovider', 'gemini'].includes(provider)) return;
    this.activeAiProvider = provider;
    this.isOneProvider = (provider === 'oneprovider');

    const hasKey = (provider === 'oneprovider') ? this.hasOneProviderKey : this.hasGeminiKey;
    if (hasKey) {
      try {
        const res = await fetch('/api/settings/active-provider', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ active_provider: provider })
        });
        const data = await res.json();
        if (data.success) {
          if (typeof this.showToast === 'function') {
            this.showToast(data.message || `Aktiver Anbieter umgestellt auf ${provider}`, 'success');
          }
          if (provider === 'oneprovider') {
            this.refreshOneProviderBalance();
          }
        }
      } catch (err) {
        console.debug('Failed to set active provider:', err);
      }
    }
    setTimeout(() => { if (window.lucide) lucide.createIcons(); }, 50);
  },

  async refreshOneProviderBalance() {
    this.refreshingBalance = true;
    try {
      const res = await fetch('/api/settings/oneprovider-balance');
      const data = await res.json();
      if (data.success) {
        this.oneproviderBalance = data.balance;
        this.oneproviderBalanceFormatted = data.formatted;
        this.oneproviderCurrency = data.currency || 'USD';
      }
    } catch (e) {
      console.debug('Failed to refresh OneProvider balance:', e);
    } finally {
      this.refreshingBalance = false;
      setTimeout(() => { if (window.lucide) lucide.createIcons(); }, 50);
    }
  },

  async saveApiKey() {
    const oneVal = (this.oneproviderKeyInput || '').trim();
    const gemVal = (this.geminiKeyInput || '').trim();
    const legacyVal = (this.apiKeyInput || '').trim();

    if (!oneVal && !gemVal && !legacyVal && !this.hasOneProviderKey && !this.hasGeminiKey) {
      this.apiKeyFeedback = { type: 'error', text: 'Bitte gib mindestens einen API-Key (OneProvider oder Google Gemini) ein.' };
      return;
    }

    this.apiKeySaving = true;
    this.apiKeyFeedback = null;

    try {
      const payload = {
        active_provider: this.activeAiProvider
      };
      if (oneVal) payload.oneprovider_key = oneVal;
      if (gemVal) payload.gemini_key = gemVal;
      if (legacyVal && !oneVal && !gemVal) payload.api_key = legacyVal;

      const res = await fetch('/api/settings/gemini-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();

      if (data.success) {
        await this.checkSavedApiKey();
        this.oneproviderKeyInput = '';
        this.geminiKeyInput = '';
        this.apiKeyInput = '';

        if (this.hasOneProviderKey) {
          await this.refreshOneProviderBalance();
        }

        const balText = (this.activeAiProvider === 'oneprovider' && this.oneproviderBalanceFormatted) 
          ? ` (Guthaben: ${this.oneproviderBalanceFormatted})` : '';

        this.apiKeyFeedback = { 
          type: 'success', 
          text: (data.message || 'API-Key(s) erfolgreich gespeichert!') + balText 
        };

        if (typeof this.showToast === 'function') {
          this.showToast(data.message || 'KI API-Key Konfiguration erfolgreich aktualisiert!', 'success');
        }

        setTimeout(() => {
          this.showApiKeyModal = false;
          if (this.pendingPropertyIdForAnalysis) {
            const pId = this.pendingPropertyIdForAnalysis;
            this.pendingPropertyIdForAnalysis = null;
            this.triggerImageAnalysis(pId);
          }
        }, 900);
      } else {
        this.apiKeyFeedback = { type: 'error', text: data.error || 'Fehler beim Validieren des API-Keys.' };
      }
    } catch (err) {
      this.apiKeyFeedback = { type: 'error', text: 'Netzwerkfehler: ' + err.message };
    } finally {
      this.apiKeySaving = false;
      setTimeout(() => { if (window.lucide) lucide.createIcons(); }, 50);
    }
  },

  async removeKey(provider = 'all') {
    try {
      const url = provider === 'all' ? '/api/settings/gemini-key' : `/api/settings/gemini-key?provider=${provider}`;
      await fetch(url, { method: 'DELETE' });
      await this.checkSavedApiKey();
      if (provider === 'oneprovider' || provider === 'all') {
        this.oneproviderKeyInput = '';
        this.configuredOneProviderMasked = '';
        this.oneproviderBalance = null;
        this.oneproviderBalanceFormatted = '';
      }
      if (provider === 'gemini' || provider === 'all') {
        this.geminiKeyInput = '';
        this.configuredGeminiMasked = '';
      }
      const label = provider === 'oneprovider' ? 'OneProvider-Key' : (provider === 'gemini' ? 'Gemini-Key' : 'API-Keys');
      this.apiKeyFeedback = { type: 'info', text: `${label} wurde entfernt.` };
      if (typeof this.showToast === 'function') {
        this.showToast(`${label} erfolgreich entfernt.`, 'info');
      }
    } catch (e) {
      console.debug('Failed to remove API key:', e);
    }
    setTimeout(() => { if (window.lucide) lucide.createIcons(); }, 50);
  },

  async removeApiKey() {
    return this.removeKey('all');
  },

  refinePhotoTag(tag, description, findings, isBlueprint = false) {
    const t = String(tag || '').trim();
    const d = String(description || '').trim().toLowerCase();
    const fStr = Array.isArray(findings) ? findings.join(' ').toLowerCase() : '';

    // 1. Grundrisse und Baupläne STRENG priorisieren (auch wenn versehentlich ein alter/falscher Tag hinterlegt war)
    if (isBlueprint || /grundriss|bauzeichnung|schnittzeichnung|architekturplan|aufteilungsplan|flächenberechnung|etagenplan|raumaufteilung|2d-grundriss|3d-grundriss|bauplan|wohnungsplan/i.test(d) || /grundriss|bauzeichnung|aufteilungsplan|raumaufteilung/i.test(fStr) || /grundriss|bauzeichnung|aufteilungsplan/i.test(t)) {
      return '📐 Bauzeichnung / Grundriss';
    }

    // 2. Bevorzuge bereits bereinigte Tags vom Python-Backend
    const genericTags = ['Objektansicht', 'Objektfoto', 'Foto aus Exposé-Galerie', '🛋️ Wohnbereich / Zimmer', '📷 Exposé-Foto', ''];
    if (t && !genericTags.includes(t)) {
      return t;
    }

    // 3. Schlanker Fallback für Altdaten ohne Backend-Bereinigung
    if (/unternehmenslogo|makler-einblend|werbefolie|marketinggrafik|kontaktdatenblatt|energieausweis/i.test(d)) {
      return '📄 Dokument / Info';
    }
    if (/fassade|außenansicht|gebäudeansicht|mehrfamilienhaus|straßenansicht|gebäudefront/i.test(d) && !/im wohnzimmer|im bad|in der küche/i.test(d)) {
      return '🏢 Fassade / Außenansicht';
    }
    if (/garten|innenhof|hinterhof|rasenfläche|außenanlagen/i.test(d)) {
      return '🌳 Garten / Hof / Außenanlagen';
    }
    if (/garage|stellplatz|tiefgarage|carport/i.test(d)) {
      return '🚗 Garage / Stellplatz';
    }
    if (/balkon|loggia|terrasse|dachterrasse/i.test(d) && !/wohn|schlaf/i.test(d.slice(0, 15))) {
      return '🌿 Balkon / Loggia';
    }
    if (/blick durch den flur|blick durch den offenen flur|wohnungsflur|flurbereich|dielenbereich/i.test(d) && !/duschkabine|badewanne|wand-wc/i.test(d)) {
      return '🚪 Flur / Eingangsbereich';
    }
    if (/badezimmer|duschbad|wannenbad|tageslichtbad|gäste-wc|wc|sanitär/i.test(d) && !/küche/i.test(d)) {
      return '🛁 Badezimmer & Sanitär';
    }
    if (/küche|einbauküche|ebk|küchenzeile/i.test(d) && !/bad/i.test(d)) {
      return '🍳 Küche / EBK';
    }
    if (/flur|diele|eingangsbereich|treppenhaus/i.test(d)) {
      return '🚪 Flur / Eingangsbereich';
    }
    if (/schlafzimmer|kinderzimmer|arbeitszimmer|gästezimmer/i.test(d)) {
      return '🛏️ Schlafzimmer / Zimmer';
    }
    if (/wohnzimmer|wohn- und ess|wohnraum|wohnstudio/i.test(d)) {
      return '🛋️ Wohnbereich / Wohnzimmer';
    }
    if (/keller|kellerabteil|abstellraum|speisekammer/i.test(d)) {
      return '📦 Keller / Abstellraum';
    }
    return t || '📷 Exposé-Foto';
  },

  openPhotoLightbox(photos, index = 0) {
    if (!photos || !photos.length) return;
    this.lightboxPhotos = photos.map((p, i) => ({
      index: p.index || (i + 1),
      url: String(p.url || '').replace('rule=$_1.AUTO', 'rule=$_59.AUTO'),
      tag: this.refinePhotoTag(p.tag, p.description, p.findings, p.is_blueprint),
      description: p.description || 'Keine detaillierte Beschreibung vorhanden.',
      quality_score: p.quality_score,
      is_blueprint: Boolean(p.is_blueprint),
      findings: Array.isArray(p.findings) ? p.findings : [],
      width: p.width,
      height: p.height
    }));
    this.lightboxIndex = Math.max(0, Math.min(Number(index) || 0, this.lightboxPhotos.length - 1));
    this.lightboxOpen = true;
    setTimeout(() => {
      if (window.lucide) window.lucide.createIcons();
    }, 40);
  },

  openMainPhotoLightbox(prop) {
    if (!prop) return;
    if (prop.image_analysis && Array.isArray(prop.image_analysis.analyzed_photos) && prop.image_analysis.analyzed_photos.length > 0) {
      this.openPhotoLightbox(prop.image_analysis.analyzed_photos, 0);
    } else if (prop.image_url) {
      this.openPhotoLightbox([{
        index: 1,
        url: prop.image_url,
        tag: prop.property_type || 'Titelbild',
        description: prop.title + (prop.address ? ` (${prop.address})` : ''),
        findings: []
      }], 0);
    }
  },

  openPhotoByNumber(photoNum) {
    const photos = this.selectedProperty?.image_analysis?.analyzed_photos;
    if (!Array.isArray(photos) || !photos.length) return;
    const idx = photos.findIndex(p => Number(p.index) === Number(photoNum));
    this.openPhotoLightbox(photos, idx >= 0 ? idx : Math.max(0, Number(photoNum) - 1));
  },

  closePhotoLightbox() {
    this.lightboxOpen = false;
  },

  nextLightboxPhoto() {
    if (!this.lightboxPhotos.length) return;
    this.lightboxIndex = (this.lightboxIndex + 1) % this.lightboxPhotos.length;
  },

  prevLightboxPhoto() {
    if (!this.lightboxPhotos.length) return;
    this.lightboxIndex = (this.lightboxIndex - 1 + this.lightboxPhotos.length) % this.lightboxPhotos.length;
  },

  getCurrentLightboxPhoto() {
    if (!this.lightboxPhotos || !this.lightboxPhotos.length) return null;
    return this.lightboxPhotos[this.lightboxIndex] || null;
  },

  get currentLightboxPhoto() {
    return this.getCurrentLightboxPhoto();
  },

  openOfferModal(property, step = 'docs') {
    if (!property || !property.ai_analysis) return;
    const analysis = property.ai_analysis;
    const origPrice = Number(property.purchase_price) || 0;
    const targetPrice = Number(analysis.negotiation_target_price) || origPrice;
    const discount = origPrice - targetPrice;

    this.offerData.property = property;
    this.offerData.step = (step === 'loi') ? 'loi' : 'docs';
    this.offerData.originalPrice = origPrice;
    this.offerData.offeredPrice = targetPrice;
    this.offerData.discount = Math.max(0, discount);
    this.offerData.docChecklist = this.buildDocChecklist(property);

    // Nutze die vom Backend generierte Vorlage, falls vorhanden
    if (property.ai_analysis?.document_request?.subject) {
      this.offerData.docRequestSubject = property.ai_analysis.document_request.subject;
    }
    if (property.ai_analysis?.document_request?.email_text) {
      this.offerData.docRequestText = property.ai_analysis.document_request.email_text;
    } else {
      this.generateDocRequestEmail();
    }
    this.generateLoiText();
    this.offerModalOpen = true;
    setTimeout(() => {
      if (window.lucide) window.lucide.createIcons();
    }, 50);
  },

  buildDocChecklist(prop) {
    // 1. Bevorzuge die vom Backend fertig generierte Unterlagen-Checkliste aus build_missing_documents_request
    const backendDocs = prop?.ai_analysis?.missing_documents 
      || prop?.ai_analysis?.doc_checklist 
      || prop?.missing_documents 
      || prop?.doc_checklist;

    if (Array.isArray(backendDocs) && backendDocs.length > 0) {
      return backendDocs.map(d => ({
        id: d.id,
        label: d.label,
        reason: d.reason,
        priority: d.priority || 'MEDIUM',
        checked: d.checked !== undefined ? Boolean(d.checked) : true,
        emailLine: d.emailLine || d.email_line || d.label
      }));
    }

    // 2. Schlanker Fallback für Altdaten ohne Backend-Checkliste
    const analysis = prop?.ai_analysis || {};
    const insights = analysis.insights || {};
    const metrics = analysis.metrics || {};
    const imgAnalysis = analysis.image_analysis || prop?.image_analysis || {};
    const propType = String(insights.corrected_property_type || prop?.property_type || '').toLowerCase();
    const isHouse = ['haus', 'mehrfamilien'].some(k => propType.includes(k)) && !propType.includes('wohnung');

    const docs = [];
    const isRented = Boolean(metrics.is_rented || prop?.is_rented || insights.is_rented);
    if (isRented) {
      docs.push({
        id: 'lease',
        label: 'Aktueller Mietvertrag & Ist-Kaltmiete (inkl. Nebenkosten & Kaution)',
        reason: 'Vertragsprüfung',
        priority: 'HIGH',
        checked: true,
        emailLine: 'Aktueller Mietvertrag (Nettokaltmiete, Betriebskostenvorauszahlung, Kaution)'
      });
    }

    if (!isHouse) {
      docs.push({
        id: 'hausgeld_plan',
        label: 'Wirtschaftsplan & letzte 2–3 Hausgeldabrechnungen',
        reason: 'Bewirtschaftung',
        priority: 'HIGH',
        checked: true,
        emailLine: 'Aktueller Wirtschaftsplan sowie die letzten 2–3 Hausgeldabrechnungen'
      });
      docs.push({
        id: 'weg_protocols',
        label: 'WEG-Protokolle (letzte 3 Jahre) & Stand Instandhaltungsrücklage',
        reason: 'WEG-Prüfung',
        priority: 'MEDIUM',
        checked: true,
        emailLine: 'Protokolle der letzten 3 Eigentümerversammlungen sowie Stand der Instandhaltungsrücklage'
      });
      docs.push({
        id: 'teilungserklaerung',
        label: 'Teilungserklärung, Aufteilungsplan & Grundbuchauszug',
        reason: 'Standard-Ankauf',
        priority: 'MEDIUM',
        checked: true,
        emailLine: 'Teilungserklärung inkl. Aufteilungsplan sowie ein aktueller Grundbuchauszug'
      });
    } else {
      docs.push({
        id: 'house_docs',
        label: 'Aktueller Grundbuchauszug, Flurkarte & Gebäudeversicherung',
        reason: 'Standard-Ankauf',
        priority: 'HIGH',
        checked: true,
        emailLine: 'Aktueller Grundbuchauszug, amtliche Flurkarte sowie Wohngebäudeversicherung'
      });
    }

    docs.push({
      id: 'energy_cert',
      label: 'Gültiger Energieausweis (§ 80 GEG)',
      reason: 'GEG-Check',
      priority: 'MEDIUM',
      checked: true,
      emailLine: 'Gültiger Energieausweis (Bedarfs- oder Verbrauchsausweis gemäß § 80 GEG)'
    });

    const hasBlueprint = (imgAnalysis.analyzed_photos || []).some(p => Boolean(p.is_blueprint));
    docs.push({
      id: 'floorplan',
      label: 'Maßstabsgerechter Grundriss & Wohnflächenberechnung',
      reason: hasBlueprint ? 'Bankenprüfung' : 'FEHLT IN GALERIE',
      priority: hasBlueprint ? 'LOW' : 'MEDIUM',
      checked: !hasBlueprint,
      emailLine: 'Maßstabsgerechter Grundriss sowie offizielle Wohnflächenberechnung'
    });

    return docs;
  },

  generateDocRequestEmail() {
    const prop = this.offerData.property;
    if (!prop) return;
    const loc = `${prop.postal_code || ''} ${prop.city || ''}`.trim();
    const selectedLines = (this.offerData.docChecklist || [])
      .filter(d => d.checked)
      .map((d, idx) => `${idx + 1}. ${d.emailLine || d.email_line || d.label}`)
      .join('\n');

    this.offerData.docRequestSubject = `Objektunterlagen & Vorabanfrage: ${prop.title}${loc ? ` (${loc})` : ''}`;
    this.offerData.docRequestText =
`Sehr geehrte Damen und Herren,

vielen Dank für Ihr Immobilienangebot „${prop.title}“${loc ? ` in ${loc}` : ''} (ca. ${this.formatNumber(prop.living_space, 0)} m² Wohnfläche, Angebotspreis ${this.formatCurrency(this.offerData.originalPrice)}).
Das Objekt passt sehr gut in mein Ankaufsprofil – eine entsprechende Finanzierungsbestätigung bzw. ein Eigenkapitalnachweis liegt bereits vor.

Bevor ich Ihnen im nächsten Schritt ein konkretes Kaufangebot (Letter of Intent) übermittle, bitte ich zur abschließenden kaufmännischen Prüfung vorab um Übersendung folgender noch offener Objektunterlagen:

${selectedLines || '- Alle wesentlichen Objekt-, Miet- und WEG-Unterlagen'}

Digitale Scans oder kurze Auskünfte per E-Mail genügen mir zunächst vollkommen. Nach Sichtung der Unterlagen komme ich sehr kurzfristig zwecks Besichtigung bzw. konkretem Kaufangebot auf Sie zu.

Vielen Dank vorab für Ihre Unterstützung!

Mit freundlichen Grüßen
${this.offerData.buyerName}
Telefon: ${this.offerData.buyerPhone}
E-Mail: ${this.offerData.buyerEmail}`;
  },

  syncOfferEmails() {
    this.generateDocRequestEmail();
    this.generateLoiText();
  },

  copyDocRequestEmail() {
    if (!this.offerData.docRequestText) return;
    const fullText = `Betreff: ${this.offerData.docRequestSubject}\n\n${this.offerData.docRequestText}`;
    navigator.clipboard.writeText(fullText);
    alert('Unterlagen-Anfrage (inkl. Betreff) in die Zwischenablage kopiert!\nNach Erhalt der Unterlagen kannst du direkt in Schritt 2 den Letter of Intent versenden.');
  },

  buildOfferMailtoHref(mode = 'docs') {
    const to = encodeURIComponent((this.offerData.recipientEmail || '').trim());
    if (mode === 'loi') {
      const subj = encodeURIComponent(`Verbindliches Kaufangebot (LOI): ${this.offerData.property?.title || 'Immobilie'}`);
      const body = encodeURIComponent(this.offerData.loiText || '');
      return `mailto:${to}?subject=${subj}&body=${body}`;
    }
    const subj = encodeURIComponent(this.offerData.docRequestSubject || 'Anfrage Objektunterlagen');
    const body = encodeURIComponent(this.offerData.docRequestText || '');
    return `mailto:${to}?subject=${subj}&body=${body}`;
  },

  generateLoiText() {
    const prop = this.offerData.property;
    if (!prop) return;
    const analysis = prop.ai_analysis || {};
    const capex = analysis.capex || { total_capex: 0, items: [] };
    const insights = analysis.insights || {};

    let reasonPoints = [];
    if (capex.total_capex > 0) {
      const topItems = (capex.items || []).slice(0, 3).map(i => i.title).join(', ');
      reasonPoints.push(`- Notwendiger Investitions- & Sanierungsbedarf (CaPEx ca. ${this.formatCurrency(capex.total_capex)} nach aktuellem Baukostenindex für ${topItems || 'Instandsetzungsmaßnahmen'})`);
    }
    if (insights.no_reserve_found) {
      reasonPoints.push('- Fehlende Instandhaltungsrücklage der Eigentümergemeinschaft (0 € Rücklage gebildet; akutes Sonderumlagenrisiko für das Gemeinschaftseigentum)');
    }
    if (insights.energy_class && ['F', 'G', 'H'].includes(insights.energy_class)) {
      reasonPoints.push(`- Gebäudeenergiegesetz (GEG) & Energieklasse ${insights.energy_class} (künftige Dämmpflichten & CO2-Kostenbeteiligung des Vermieters)`);
    }
    if (reasonPoints.length === 0) {
      reasonPoints.push('- Auswertung der aktuellen Marktpreisspanne und Zinskonditionen für vergleichbare Eigentumswohnungen in der Mikrolage');
    }

    this.offerData.loiText = 
`Sehr geehrte Damen und Herren,
sehr geehrte Eigentümer,

hiermit bekunde ich mein verbindliches Kaufinteresse (Letter of Intent) für Ihre Immobilie:

Objekt: ${prop.title}
Standort: ${prop.address || ''}, ${prop.postal_code || ''} ${prop.city}
Wohnfläche: ca. ${prop.living_space} m² (${prop.rooms || 2} Zimmer)

Angebotener Kaufpreis: ${this.formatCurrency(this.offerData.offeredPrice)}
(Angebotspreis im Inserat: ${this.formatCurrency(this.offerData.originalPrice)})

Begründung des Kaufpreisangebots:
Auf Basis unserer gründlichen kaufmännischen und bautechnischen Vorprüfung setzt sich dieses Angebot aus folgenden sachlichen Faktoren zusammen:
${reasonPoints.join('\n')}

Konditionen & Abwicklung:
1. Eine vorläufige Finanzierungsbestätigung bzw. ein Eigenkapitalnachweis liegt vor und kann unverzüglich übermittelt werden.
2. Ich bin bereit, den notariellen Kaufvertrag innerhalb von 14 bis 21 Werktagen nach Erhalt der vollständigen Objektunterlagen (Teilungserklärung, Grundbuchauszug, WEG-Protokolle) zu beurkunden.
3. Die Übergabe und Kaufpreiszahlung kann flexibel nach Absprache mit Ihnen erfolgen.

Dieses Angebot halte ich bis zum ${new Date(Date.now() + (this.offerData.validDays || 14) * 24 * 60 * 60 * 1000).toLocaleDateString('de-DE')} verbindlich aufrecht.

Ich freue mich über Ihre Rückmeldung und stehe für eine Besichtigung oder ein persönliches Abstimmungsgespräch sehr gerne zur Verfügung.

Mit freundlichen Grüßen
${this.offerData.buyerName}
Telefon: ${this.offerData.buyerPhone}
E-Mail: ${this.offerData.buyerEmail}`;
  },

  copyLoiText() {
    if (!this.offerData.loiText) return;
    navigator.clipboard.writeText(this.offerData.loiText);
    alert('Kaufangebot (Letter of Intent) in die Zwischenablage kopiert!\nDu kannst es jetzt direkt als Nachricht oder E-Mail an den Verkäufer/Makler senden.');
  },

  // 1-Klick Gutachten Drucken / PDF Export
  printAiReport(property) {
    window.print();
  },

  openUrlImportModal() {
    this.importUrlInput = '';
    this.importError = '';
    this.urlImportModalOpen = true;
    this.$nextTick(() => {
      if (window.lucide) window.lucide.createIcons();
    });
  },

  async submitUrlImport() {
    const url = (this.importUrlInput || '').trim();
    if (!url || !url.startsWith('http')) {
      this.importError = 'Bitte gib eine gültige URL (http:// oder https://) ein.';
      return;
    }
    this.isImportingUrl = true;
    this.importError = '';
    try {
      const res = await fetch('/api/properties/import-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Import fehlgeschlagen');
      }
      this.urlImportModalOpen = false;
      await this.fetchProperties();
      await this.fetchBoard();
      if (data.property_id) {
        await this.openDetailModalById(data.property_id);
      }
      alert(data.message || 'Exposé erfolgreich importiert und per KI analysiert!');
    } catch (err) {
      this.importError = 'Fehler beim Importieren: ' + err.message;
    } finally {
      this.isImportingUrl = false;
    }
  }
};
