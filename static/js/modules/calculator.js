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
    selected_term_years: 10,
    repayment_rate: 2.0,
    additional_costs: 50, // Hausgeld / Instandhaltung nicht umlegbar
    hausgeld_total: 0,
    renovation_costs: 0,
    commission_percent: 3.57,
    buy_costs_from_equity: true,
    manual_state_override: false,
    mortgage_bank_name: '',
    is_live_quote: false,
    drklein_calculator_url: '',
    is_fetching_drklein: false,
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
    const rawRent = prop.current_rent > 0 ? prop.current_rent : prop.expected_rent;
    this.calc.rent = rawRent ? Math.round(Number(rawRent) * 100) / 100 : 0;
    this.calc.expected_rent = Number(Number(prop.expected_rent || prop.ai_analysis?.metrics?.expected_monthly_rent || 0).toFixed(2));
    this.calc.current_rent = Number(Number(prop.current_rent || prop.ai_analysis?.metrics?.current_monthly_rent || 0).toFixed(2));
    this.calc.is_rented = Boolean(prop.is_rented || prop.ai_analysis?.metrics?.is_rented);
    this.calc.mietspiegel_analysis = prop.ai_analysis?.metrics?.mietspiegel_analysis || prop.ai_analysis?.mietspiegel_analysis || null;
    this.calc.living_space = prop.living_space;
    this.calc.commission_percent = prop.commission_percent !== undefined ? prop.commission_percent : 3.57;

    // Use consistent live interest rate matching the AI audit
    if (prop.ai_analysis?.metrics?.applied_interest_rate) {
      this.calc.interest_rate = Number(prop.ai_analysis.metrics.applied_interest_rate);
      this.calc.mortgage_bank_name = prop.ai_analysis.metrics.mortgage_bank_name || '';
      this.calc.is_live_quote = Boolean(prop.ai_analysis.metrics.is_drklein_live_quote);
      this.calc.drklein_calculator_url = prop.ai_analysis.metrics.drklein_calculator_url || '';
      if (prop.ai_analysis.metrics.rates_by_term && Array.isArray(prop.ai_analysis.metrics.rates_by_term) && prop.ai_analysis.metrics.rates_by_term.length > 0) {
        this.liveRates.rates_by_term = prop.ai_analysis.metrics.rates_by_term;
      }
    } else if (this.liveRates?.default_nominal_rate) {
      this.calc.interest_rate = Number(this.liveRates.default_nominal_rate);
      this.fetchTailoredObjectRate();
    }

    const hgExplicit = prop.ai_analysis?.insights?.has_explicit_hausgeld;
    const hgVal = Number(prop.ai_analysis?.insights?.hausgeld || 0);
    const hgNonRec = Number(prop.ai_analysis?.insights?.hausgeld_non_recoverable || 0);
    this.calc.hausgeld_total = (hgExplicit && hgVal > 0) ? Math.round(hgVal * 100) / 100 : 0;
    if (prop.ai_analysis?.metrics?.additional_costs !== undefined && prop.ai_analysis?.metrics?.additional_costs !== null) {
      this.calc.additional_costs = Math.round(Number(prop.ai_analysis.metrics.additional_costs) * 100) / 100;
    } else if (hgExplicit && hgVal > 0) {
      this.calc.additional_costs = hgNonRec > 0 ? Math.round(hgNonRec * 100) / 100 : Math.round(hgVal * 0.35 * 100) / 100;
    } else {
      this.calc.additional_costs = 50;
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
      this.calc.rent = Math.round(targetRent * 100) / 100;
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
    this.calc.selected_term_years = term.years;
    if (term.bank_name) {
      this.calc.mortgage_bank_name = term.bank_name;
    }
    this.calc.user_has_modified_interest = true;
    this.runCalculator();
  },

  setMietspiegelRent(amount) {
    if (!amount || amount <= 0) return;
    this.calc.rent = Math.round(Number(amount) * 100) / 100;
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
      if (this.calc.result) {
        this.calc.drklein_calculator_url = this.getDrKleinUrl();
        if (this.calc.result.mortgage_bank_name) {
          this.calc.mortgage_bank_name = this.calc.result.mortgage_bank_name;
          this.calc.is_live_quote = Boolean(this.calc.result.is_drklein_live_quote);
        }
        if (this.calc.result.rates_by_term && Array.isArray(this.calc.result.rates_by_term) && this.calc.result.rates_by_term.length > 0) {
          this.liveRates.rates_by_term = this.calc.result.rates_by_term;
        }
      }
    } catch (err) {
      console.error('Calculator error:', err);
    }
  },

  setEquityType(type) {
    if (this.calc.equity_type === type) return;
    const p = Math.max(0, Number(this.calc.price) || 0);
    const val = Number(this.calc.equity_value) || 0;
    if (type === 'amount') {
      // Convert percent to Euro amount
      this.calc.equity_value = p > 0 ? Math.round(p * (val / 100)) : val;
      this.calc.equity_type = 'amount';
    } else {
      // Convert Euro amount to percent
      this.calc.equity_value = p > 0 ? Math.round((val / p) * 1000) / 10 : 20;
      this.calc.equity_type = 'percent';
    }
    this.runCalculator();
    this.fetchTailoredObjectRate(true);
  },

  handleEquityInput() {
    const val = Number(this.calc.equity_value) || 0;
    // Auto-detect if user typed a nominal euro amount (e.g. 15000 or > 100) while in percent mode
    if (this.calc.equity_type === 'percent' && val > 100) {
      this.calc.equity_type = 'amount';
    }
    this.runCalculator();
  },

  getDrKleinUrl() {
    const p = Math.max(10000, Math.round(Number(this.calc.price) || 0));
    const taxRate = Number(this.calc.tax_rate || 6.5);
    const commissionPct = Number(this.calc.commission_percent || 0);
    const capex = Math.max(0, Math.round(Number(this.calc.renovation_costs) || 0));

    // Exact user-specified equity in EUR (on purchase price)
    let eqEur = 0;
    if (this.calc.equity_type === 'percent') {
      const pct = Number(this.calc.equity_value) || 0;
      eqEur = Math.round(p * (pct / 100));
    } else {
      eqEur = Math.round(Number(this.calc.equity_value) || 0);
    }
    eqEur = Math.max(0, eqEur);

    // Dr. Klein Form Nebenkosten: GrESt + 2.0% Notar/Grundbuch
    const drkleinNebenkostenEur = Math.round(p * ((taxRate + 2.0) / 100));
    const drkleinTotalCost = p + drkleinNebenkostenEur + capex;

    // Full project financing need including broker
    const fullNebenkostenPct = taxRate + 2.0 + commissionPct;
    const fullNebenkostenEur = Math.round(p * (fullNebenkostenPct / 100));
    const totalFinancingNeed = p + fullNebenkostenEur + capex;

    // Target loan amount matching calculator settings
    let loanAmount = 0;
    if (this.calc.result?.loan_amount && Number(this.calc.result.loan_amount) > 0) {
      loanAmount = Math.round(Number(this.calc.result.loan_amount));
    } else if (this.calc.buy_costs_from_equity) {
      loanAmount = Math.max(5000, Math.round((p - eqEur) + capex));
    } else {
      loanAmount = Math.max(5000, Math.round(totalFinancingNeed - eqEur));
    }

    // On Dr. Klein: Darlehensbetrag = Gesamtkosten - Eigenkapital.
    // To match loanAmount 100%, pass eigenkapital = drkleinTotalCost - loanAmount.
    const drkleinEquity = Math.max(0, drkleinTotalCost - loanAmount);

    const plz = (this.calc.postal_code || '').replace(/\D/g, '').slice(0, 5);
    const plzParam = plz ? `&plz=${plz}` : '';
    const tilg = Math.max(1, Math.min(10, Math.round(Number(this.calc.repayment_rate || 2))));
    const termYears = this.calc.selected_term_years || 10;

    let url = `https://www.drklein.de/bauzinsen-rechner.html?kaufpreis=${p}&eigenkapital=${drkleinEquity}&darlehensbetrag=${loanAmount}${plzParam}&sollzinsbindung=${termYears}&tilgungssatz=${tilg}`;
    if (capex > 0) {
      url += `&modernisierungskosten=${capex}`;
    }
    if (commissionPct > 0) {
      url += `&maklerprovisionPct=${commissionPct}&maklerprovision=${commissionPct}`;
    }
    url += `#construction-interest-calculator`;
    return url;
  },

  getDrKleinUrlForProperty(prop) {
    if (!prop) return 'https://www.drklein.de/bauzinsen-rechner.html#construction-interest-calculator';
    const p = Math.max(10000, Math.round(Number(prop.purchase_price) || 0));
    const taxRate = Number(prop.ai_analysis?.metrics?.grunderwerbsteuer_rate || 6.5);
    const commissionPct = Number(prop.commission_percent !== undefined ? prop.commission_percent : (prop.ai_analysis?.metrics?.commission_percent || 3.57));
    const capex = Math.max(0, Math.round(
      Number((prop.image_analysis && prop.image_analysis.total_visual_capex) ||
             (prop.ai_analysis && prop.ai_analysis.capex && prop.ai_analysis.capex.total_capex) || 0)
    ));
    const eqPct = Number(prop.ai_analysis?.metrics?.equity_value || 20);
    const eqEur = Math.round(p * (eqPct / 100));

    // Dr. Klein Form Nebenkosten: GrESt + 2.0% Notar/Grundbuch
    const drkleinNebenkostenEur = Math.round(p * ((taxRate + 2.0) / 100));
    const drkleinTotalCost = p + drkleinNebenkostenEur + capex;

    let loanAmount = 0;
    if (prop.ai_analysis?.metrics?.loan_amount && Number(prop.ai_analysis.metrics.loan_amount) > 0) {
      loanAmount = Math.round(Number(prop.ai_analysis.metrics.loan_amount));
    } else {
      loanAmount = Math.max(5000, Math.round((p - eqEur) + capex));
    }

    const drkleinEquity = Math.max(0, drkleinTotalCost - loanAmount);

    const plz = (prop.postal_code || '').replace(/\D/g, '').slice(0, 5);
    const plzParam = plz ? `&plz=${plz}` : '';
    const tilg = Math.max(1, Math.min(10, Math.round(Number(prop.ai_analysis?.metrics?.applied_repayment_rate || 2))));
    const termYears = Number(prop.ai_analysis?.metrics?.term_years || 10);

    let url = `https://www.drklein.de/bauzinsen-rechner.html?kaufpreis=${p}&eigenkapital=${drkleinEquity}&darlehensbetrag=${loanAmount}${plzParam}&sollzinsbindung=${termYears}&tilgungssatz=${tilg}`;
    if (capex > 0) url += `&modernisierungskosten=${capex}`;
    if (commissionPct > 0) url += `&maklerprovisionPct=${commissionPct}&maklerprovision=${commissionPct}`;
    url += `#construction-interest-calculator`;
    return url;
  },

  async fetchTailoredObjectRate(forceApply = false) {
    if (!this.calc.price || this.calc.price <= 0) return;
    try {
      this.calc.is_fetching_drklein = true;
      const params = new URLSearchParams({
        price: this.calc.price,
        equity_value: this.calc.equity_value,
        equity_type: this.calc.equity_type,
        term_years: this.calc.selected_term_years || 10,
        repayment_rate: this.calc.repayment_rate || 2.0,
        renovation_costs: this.calc.renovation_costs || 0,
        commission_percent: this.calc.commission_percent || 3.57,
        buy_costs_from_equity: this.calc.buy_costs_from_equity ? 'true' : 'false'
      });
      if (this.calc.postal_code) params.append('postal_code', this.calc.postal_code);
      const res = await fetch(`/api/calculator/object-rate?${params.toString()}`);
      if (res.ok) {
        const quote = await res.json();
        if (quote) {
          if (quote.rates_by_term && Array.isArray(quote.rates_by_term) && quote.rates_by_term.length > 0) {
            this.liveRates.rates_by_term = quote.rates_by_term;
          }
          if (quote.nominal_rate) {
            if (forceApply || !this.calc.user_has_modified_interest) {
              this.calc.interest_rate = Number(quote.nominal_rate);
            }
            this.calc.mortgage_bank_name = quote.bank_name || '';
            this.calc.is_live_quote = Boolean(quote.is_live_quote);
            this.calc.drklein_calculator_url = quote.calculator_url || this.getDrKleinUrl();
            if (forceApply) {
              this.runCalculator();
            }
          }
        }
      }
    } catch (e) {
      console.warn('Could not fetch tailored Dr. Klein rate:', e);
    } finally {
      this.calc.is_fetching_drklein = false;
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
      const rawRent = this.selectedProperty.current_rent > 0 ? this.selectedProperty.current_rent : this.selectedProperty.expected_rent;
      this.calc.rent = rawRent ? Math.round(Number(rawRent) * 100) / 100 : 0;
      this.calc.expected_rent = Number(Number(this.selectedProperty.expected_rent || this.selectedProperty.ai_analysis?.metrics?.expected_monthly_rent || 0).toFixed(2));
      this.calc.current_rent = Number(Number(this.selectedProperty.current_rent || this.selectedProperty.ai_analysis?.metrics?.current_monthly_rent || 0).toFixed(2));
      this.calc.is_rented = Boolean(this.selectedProperty.is_rented || this.selectedProperty.ai_analysis?.metrics?.is_rented);
      this.calc.mietspiegel_analysis = this.selectedProperty.ai_analysis?.metrics?.mietspiegel_analysis || this.selectedProperty.ai_analysis?.mietspiegel_analysis || null;
      this.calc.living_space = this.selectedProperty.living_space;
      this.calc.commission_percent = this.selectedProperty.commission_percent !== undefined ? this.selectedProperty.commission_percent : 3.57;
      if (this.selectedProperty.ai_analysis?.metrics?.applied_interest_rate) {
        this.calc.interest_rate = Number(this.selectedProperty.ai_analysis.metrics.applied_interest_rate);
        this.calc.mortgage_bank_name = this.selectedProperty.ai_analysis.metrics.mortgage_bank_name || '';
        this.calc.is_live_quote = Boolean(this.selectedProperty.ai_analysis.metrics.is_drklein_live_quote);
        this.calc.drklein_calculator_url = this.selectedProperty.ai_analysis.metrics.drklein_calculator_url || '';
        if (this.selectedProperty.ai_analysis.metrics.rates_by_term && Array.isArray(this.selectedProperty.ai_analysis.metrics.rates_by_term) && this.selectedProperty.ai_analysis.metrics.rates_by_term.length > 0) {
          this.liveRates.rates_by_term = this.selectedProperty.ai_analysis.metrics.rates_by_term;
        }
      }
      const hgExplicit = this.selectedProperty.ai_analysis?.insights?.has_explicit_hausgeld;
      const hgVal = Number(this.selectedProperty.ai_analysis?.insights?.hausgeld || 0);
      const hgNonRec = Number(this.selectedProperty.ai_analysis?.insights?.hausgeld_non_recoverable || 0);
      this.calc.hausgeld_total = (hgExplicit && hgVal > 0) ? Math.round(hgVal * 100) / 100 : 0;
      if (this.selectedProperty.ai_analysis?.metrics?.additional_costs !== undefined && this.selectedProperty.ai_analysis?.metrics?.additional_costs !== null) {
        this.calc.additional_costs = Math.round(Number(this.selectedProperty.ai_analysis.metrics.additional_costs) * 100) / 100;
      } else if (hgExplicit && hgVal > 0) {
        this.calc.additional_costs = hgNonRec > 0 ? Math.round(hgNonRec * 100) / 100 : Math.round(hgVal * 0.35 * 100) / 100;
      } else {
        this.calc.additional_costs = 50;
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
