/**
 * ImmoMetrica - Calculator Module
 * Cashflow calculator, AfA tax optimization, live mortgage benchmark rates & CaPEx transfer.
 */
window.immoCalculatorModule = {
  // Cashflow Calculator State (automatically derives Bundesland & Grunderwerbsteuer from property data)
  calc: {
    title: 'Objektkalkulation',
    address: '',
    postal_code: '45127',
    city: 'Essen',
    state: 'Nordrhein-Westfalen',
    tax_rate: 6.5,
    price: 250000,
    rent: 1100,
    expected_rent: 0,
    current_rent: 0,
    is_rented: false,
    mietspiegel_analysis: null,
    user_has_modified_interest: false,
    living_space: 72,
    equity_value: 20,
    equity_type: 'percent', // 'percent' or 'amount'
    interest_rate: 3.93,
    repayment_rate: 2.0,
    additional_costs: 45, // Hausgeld / Instandhaltung nicht umlegbar
    hausgeld_total: 0,
    renovation_costs: 0,
    commission_percent: 3.57,
    buy_costs_from_equity: true,
    manual_state_override: false,
    // AfA & Steuer-Parameter
    include_afa: false,
    building_share_percent: 80,
    depreciation_mode: 'standard',
    depreciation_years: 50,
    marginal_tax_rate: 42,
    result: null
  },

  // Live Mortgage Interest Rate State (Bauzinsen online Benchmark)
  liveRates: {
    default_nominal_rate: 3.93,
    default_effective_rate: 4.03,
    default_term_years: 10,
    as_of_date: '',
    source_name: 'Dr. Klein (Tagesaktuell)',
    rates_by_term: [
      { years: 5, nominal_rate: 3.97, effective_rate: 4.09, label: '5 Jahre' },
      { years: 10, nominal_rate: 3.93, effective_rate: 4.03, label: '10 Jahre (Standard)', is_default: true },
      { years: 15, nominal_rate: 4.09, effective_rate: 4.19, label: '15 Jahre' },
      { years: 20, nominal_rate: 4.18, effective_rate: 4.28, label: '20 Jahre' },
      { years: 30, nominal_rate: 4.44, effective_rate: 4.55, label: '30 Jahre' }
    ],
    loading: false
  },

  async sendToCalculator(prop) {
    this.calc.title = prop.title;
    this.calc.address = prop.address || '';
    this.calc.postal_code = prop.postal_code || '';
    this.calc.city = prop.city || '';
    this.calc.state = prop.state || '';
    this.calc.price = prop.purchase_price;
    this.calc.rent = prop.current_rent > 0 ? prop.current_rent : prop.expected_rent;
    this.calc.expected_rent = Number(prop.expected_rent || prop.ai_analysis?.metrics?.expected_monthly_rent || 0);
    this.calc.current_rent = Number(prop.current_rent || prop.ai_analysis?.metrics?.current_monthly_rent || 0);
    this.calc.is_rented = Boolean(prop.is_rented || prop.ai_analysis?.metrics?.is_rented);
    this.calc.mietspiegel_analysis = prop.ai_analysis?.metrics?.mietspiegel_analysis || prop.ai_analysis?.mietspiegel_analysis || null;
    this.calc.living_space = prop.living_space;
    this.calc.commission_percent = prop.commission_percent !== undefined ? prop.commission_percent : 3.57;

    // Use consistent live interest rate matching the AI audit
    if (prop.ai_analysis?.metrics?.applied_interest_rate) {
      this.calc.interest_rate = Number(prop.ai_analysis.metrics.applied_interest_rate);
    } else if (this.liveRates?.default_nominal_rate) {
      this.calc.interest_rate = Number(this.liveRates.default_nominal_rate);
    }

    const hgExplicit = prop.ai_analysis?.insights?.has_explicit_hausgeld;
    const hgVal = Number(prop.ai_analysis?.insights?.hausgeld || 0);
    const hgNonRec = Number(prop.ai_analysis?.insights?.hausgeld_non_recoverable || 0);
    this.calc.hausgeld_total = (hgExplicit && hgVal > 0) ? Math.round(hgVal) : 0;
    if (hgExplicit && hgVal > 0) {
      this.calc.additional_costs = hgNonRec > 0 ? Math.round(hgNonRec) : Math.round(hgVal * 0.35);
    } else {
      this.calc.additional_costs = 45;
    }
    this.calc.manual_state_override = false;

    // Transfer CaPEx / Sanierungskosten if available
    const capex = (prop.image_analysis && prop.image_analysis.total_visual_capex) ||
                  (prop.ai_analysis && prop.ai_analysis.capex && prop.ai_analysis.capex.total_capex) ||
                  0;
    this.calc.renovation_costs = Math.round(Number(capex) || 0);

    // Auto-resolve Grunderwerbsteuer directly from property data without user prompt
    await this.resolveLocationTax();

    // Ensure the modal view was pushed to history so browser Back returns directly to it
    if (this.detailModalOpen && this.selectedProperty) {
      this.syncUrl(true);
    }
    this.detailModalOpen = false;
    this.activeTab = 'calculator';
    this.runCalculator();
  },

  async sendToCalculatorWithMietspiegel(prop) {
    if (!prop) return;
    await this.sendToCalculator(prop);
    const targetRent = Number(prop.expected_rent || prop.ai_analysis?.metrics?.expected_monthly_rent || 0);
    if (targetRent > 0) {
      this.calc.rent = Math.round(targetRent);
      this.runCalculator();
    }
    this.activeTab = 'calculator';
    window.scrollTo({ top: 0, behavior: 'smooth' });
  },

  sendToCalculatorById(propertyId) {
    const prop = this.properties.find(p => p.id === propertyId);
    if (prop) {
      this.sendToCalculator(prop);
    }
  },

  async fetchLiveMortgageRates(forceRefresh = false) {
    try {
      this.liveRates.loading = true;
      const res = await fetch(`/api/calculator/interest-rates${forceRefresh ? '?force_refresh=true' : ''}`);
      if (res.ok) {
        const data = await res.json();
        this.liveRates = { ...this.liveRates, ...data, loading: false };
        if (data.default_nominal_rate && (!this.calc.user_has_modified_interest || !this.calc.interest_rate)) {
          this.calc.interest_rate = Number(data.default_nominal_rate);
          this.runCalculator();
        }
      }
    } catch (err) {
      console.warn('Could not fetch live mortgage rates:', err);
    } finally {
      this.liveRates.loading = false;
    }
  },

  setMortgageTerm(term) {
    if (!term || !term.nominal_rate) return;
    this.calc.interest_rate = Number(term.nominal_rate);
    this.calc.user_has_modified_interest = true;
    this.runCalculator();
  },

  setMietspiegelRent(amount) {
    if (!amount || amount <= 0) return;
    this.calc.rent = Math.round(Number(amount));
    this.runCalculator();
  },

  async resolveLocationTax() {
    try {
      const params = new URLSearchParams();
      if (this.calc.postal_code) params.append('postal_code', this.calc.postal_code);
      if (this.calc.city) params.append('city', this.calc.city);
      if (this.calc.state) params.append('state', this.calc.state);

      const res = await fetch(`/api/calculator/resolve-geo?${params.toString()}`);
      const geo = await res.json();
      if (geo.state) {
        this.calc.state = geo.state;
        this.calc.tax_rate = geo.tax_rate;
      }
    } catch (err) {
      console.error('Failed to resolve geo tax:', err);
    }
  },

  setDepreciationPreset(mode, years) {
    this.calc.depreciation_mode = mode;
    this.calc.depreciation_years = years;
    this.runCalculator();
  },

  async runCalculator() {
    try {
      const res = await fetch('/api/calculator/calculate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          price: Number(this.calc.price),
          rent: Number(this.calc.rent),
          equity_value: Number(this.calc.equity_value),
          equity_type: this.calc.equity_type,
          interest_rate: Number(this.calc.interest_rate),
          repayment_rate: Number(this.calc.repayment_rate),
          additional_costs: Number(this.calc.additional_costs),
          renovation_costs: Number(this.calc.renovation_costs),
          buy_costs_from_equity: this.calc.buy_costs_from_equity,
          state: this.calc.state,
          postal_code: this.calc.postal_code,
          city: this.calc.city,
          commission_percent: Number(this.calc.commission_percent),
          living_space: Number(this.calc.living_space),
          include_afa: Boolean(this.calc.include_afa),
          building_share_percent: Number(this.calc.building_share_percent) || 80,
          depreciation_years: Number(this.calc.depreciation_years) || 50,
          marginal_tax_rate: Number(this.calc.marginal_tax_rate) || 42
        })
      });
      this.calc.result = await res.json();
    } catch (err) {
      console.error('Calculator error:', err);
    }
  },

  applyVisualCapexToCalculator(capexAmount) {
    if (this.selectedProperty) {
      // Sync full property parameters into calculator
      this.calc.title = this.selectedProperty.title;
      this.calc.address = this.selectedProperty.address || '';
      this.calc.postal_code = this.selectedProperty.postal_code || '';
      this.calc.city = this.selectedProperty.city || '';
      this.calc.state = this.selectedProperty.state || '';
      this.calc.price = this.selectedProperty.purchase_price;
      this.calc.rent = this.selectedProperty.current_rent > 0 ? this.selectedProperty.current_rent : this.selectedProperty.expected_rent;
      this.calc.expected_rent = Number(this.selectedProperty.expected_rent || this.selectedProperty.ai_analysis?.metrics?.expected_monthly_rent || 0);
      this.calc.current_rent = Number(this.selectedProperty.current_rent || this.selectedProperty.ai_analysis?.metrics?.current_monthly_rent || 0);
      this.calc.is_rented = Boolean(this.selectedProperty.is_rented || this.selectedProperty.ai_analysis?.metrics?.is_rented);
      this.calc.mietspiegel_analysis = this.selectedProperty.ai_analysis?.metrics?.mietspiegel_analysis || this.selectedProperty.ai_analysis?.mietspiegel_analysis || null;
      this.calc.living_space = this.selectedProperty.living_space;
      this.calc.commission_percent = this.selectedProperty.commission_percent !== undefined ? this.selectedProperty.commission_percent : 3.57;
      const hgExplicit = this.selectedProperty.ai_analysis?.insights?.has_explicit_hausgeld;
      const hgVal = Number(this.selectedProperty.ai_analysis?.insights?.hausgeld || 0);
      const hgNonRec = Number(this.selectedProperty.ai_analysis?.insights?.hausgeld_non_recoverable || 0);
      this.calc.hausgeld_total = (hgExplicit && hgVal > 0) ? Math.round(hgVal) : 0;
      if (hgExplicit && hgVal > 0) {
        this.calc.additional_costs = hgNonRec > 0 ? Math.round(hgNonRec) : Math.round(hgVal * 0.35);
      } else {
        this.calc.additional_costs = 45;
      }
      this.calc.manual_state_override = false;
      this.resolveLocationTax();
    }

    let capexNum = Math.round(Number(capexAmount) || 0);
    if (capexNum <= 0 && this.selectedProperty) {
      if (this.selectedProperty.image_analysis && Number(this.selectedProperty.image_analysis.total_visual_capex) > 0) {
        capexNum = Math.round(Number(this.selectedProperty.image_analysis.total_visual_capex));
      } else if (this.selectedProperty.ai_analysis && this.selectedProperty.ai_analysis.capex && Number(this.selectedProperty.ai_analysis.capex.total_capex) > 0) {
        capexNum = Math.round(Number(this.selectedProperty.ai_analysis.capex.total_capex));
      }
    }

    this.calc.renovation_costs = capexNum;
    this.runCalculator();
    this.activeTab = 'calculator';
    this.detailModalOpen = false;
    window.scrollTo({ top: 0, behavior: 'smooth' });
    if (capexNum > 0) {
      alert(`Die kalkulierten Sanierungskosten (${capexNum.toLocaleString('de-DE')} €) wurden in den Cashflow-Rechner übernommen und neu durchgerechnet!`);
    } else {
      alert('Das Objekt wurde in den Cashflow-Rechner übernommen (Sanierungskosten: 0 €).');
    }
  }
};
