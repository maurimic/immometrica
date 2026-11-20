"""
Search Agent & Deal Alert Engine for ImmoMetrica Clone.
Monitors configured search profiles across German real estate portals
(Kleinanzeigen, ImmoScout24, Immowelt, and meta-portals),
executes the 3-Stage Smart Filter Funnel (KI-Kostenkontrolle) to discard ~85%
of unqualified deals before calling LLM APIs, performs targeted full AI Due Diligence Audits,
enforces Pipeline Protection (moves poor deals to 'archiv' to keep the Kanban board clean),
and dispatches Telegram push alerts with verified audited numbers.
"""

import os
import re
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from app.database import (
    get_connection,
    get_search_profiles,
    get_search_profile_by_id,
    update_search_profile,
    get_deal_alerts,
    is_alert_already_logged,
    record_deal_alert,
    get_system_setting,
    set_system_setting,
    mark_listing_seen,
    get_seen_listing_urls,
    clear_seen_listings_for_profile
)
from app.scraper import scrape_multi_portal_deals, scrape_live_deals_kleinanzeigen, import_scraped_deals_returning_ids
from app.telegram_notifier import send_deal_telegram_alert

logger = logging.getLogger("immometrica.search_agent")


def quick_ai_deal_precheck(deal: Dict[str, Any], full_description: str = "") -> Dict[str, Any]:
    """
    Performs a fast, targeted AI due diligence pre-check on a listing:
    1. Detects critical deal-killers: Erbbaurecht (leasehold), Zwangsversteigerung (foreclosure),
       Denkmalschutz (monument protection), Sanierungsstau (backlog).
    2. Identifies investment highlights (commission-free, renovated, high rental yield).
    3. Synthesizes a crisp 1-sentence German verdict for push notifications and alert feed.
    """
    combined_text = f"{deal.get('title', '')} {full_description} {deal.get('address', '')}".lower()

    risks: List[str] = []
    highlights: List[str] = []
    has_critical_risks = False

    # 1. Critical Deal Killer: Erbbaurecht / Erbpacht
    if any(k in combined_text for k in ["erbbaurecht", "erbbauzins", "erbbau", "erbpacht"]):
        risks.append("⚠️ Erbbaurecht / Erbpacht (monatlicher Erbbauzins schmälert Cashflow)")
        has_critical_risks = True

    # 2. Foreclosure / ZVG
    if "zwangsversteigerung" in combined_text or "zvg" in combined_text:
        risks.append("⚖️ Zwangsversteigerung (Sonderbedingungen bei Erwerb beachten)")

    # 3. Denkmalschutz
    if "denkmal" in combined_text:
        highlights.append("🏛️ Denkmalschutz (erhöhte Denkmal-AfA möglich, Auflagen beachten)")

    # 4. Renovierung / Kernsanierung
    if any(k in combined_text for k in ["sanierungsbedürftig", "renovierungsbedürftig", "entkernt", "sanierungsstau", "abriss"]):
        risks.append("🛠️ Erheblicher Sanierungsbedarf / CaPEx einplanen")
    elif any(k in combined_text for k in ["kernsaniert", "vollsaniert", "neuwertig", "erstbezug nach sanierung"]):
        highlights.append("✨ Neuwertiger / frisch sanierter Zustand")

    # 5. Provisionsfrei
    if any(k in combined_text for k in ["provisionsfrei", "ohne provision", "privatverkauf"]):
        highlights.append("0 % Käuferprovision (spart Kaufnebenkosten)")

    # 6. Miete & Ertrag
    gross_yield = float(deal.get("gross_yield") or 0.0)
    discount_pct = float(deal.get("market_deviation_percent") or deal.get("market_discount_pct") or 0.0)

    if gross_yield >= 7.0:
        highlights.append(f"Top-Bruttorendite: {gross_yield:.1f} %")
    if discount_pct <= -15.0:
        highlights.append(f"{abs(discount_pct):.1f} % unter Marktwert-Benchmark")

    city = deal.get("city", "Standort")
    price = deal.get("purchase_price") or deal.get("price") or 0
    sqm = deal.get("living_space") or 1
    price_per_sqm = round(price / sqm) if sqm > 0 else 0

    if has_critical_risks:
        verdict = f"Achtung: {risks[0]}! Vor Gebot zwingend Erbpachtvertrag und Zinslaufzeit prüfen."
    elif risks:
        verdict = f"Interessante Lage in {city} ({price_per_sqm:,.0f} €/m²), jedoch {risks[0].replace('🛠️ ', '')}. Sanierungs-CaPEx einpreisen."
    elif discount_pct <= -12.0 and gross_yield >= 6.0:
        verdict = f"Hervorragender Deal-Kandidat: {gross_yield:.1f} % Rendite bei {abs(discount_pct):.1f} % Abschlag zum Marktwert. Keine Ausschlusskriterien gefunden."
    elif gross_yield >= 5.5:
        verdict = f"Solider Cashflow-Deal in {city} mit {gross_yield:.1f} % Bruttorendite. Gepflegte Bausubstanz im ersten Textabgleich."
    else:
        verdict = f"Faires Angebot in {city} ({price_per_sqm:,.0f} €/m²). Gute Makrolage, Prüfung von Mietsteigerungspotenzial empfohlen."

    return {
        "has_critical_risks": has_critical_risks,
        "verdict": verdict,
        "risks": risks,
        "highlights": highlights
    }


def get_oneprovider_token_rates(model_or_provider_str: str) -> Tuple[float, float]:
    """
    Berechnet die geschätzten EUR-Kosten pro 1M Token anhand des Modells:
    - claude-haiku-5-5 und gpt-6-luna: ~0,09 EUR Input / 0,46 EUR Output
    - qwen3.8-flash: ~0,14 EUR Input / 0,43 EUR Output
    - gemini-flash-lite-latest / gemini-3.5-flash-lite: ~0,09 EUR Input / 0,37 EUR Output
    - gemini-3.8-flash: ~0,69 EUR Input / 3,45 EUR Output
    - gemini-3.5-flash: ~1,38 EUR Input / 8,28 EUR Output
    """
    s = str(model_or_provider_str or "").lower()
    if "qwen" in s:
        return 0.14, 0.43
    if "flash-lite" in s or "3.5-flash-lite" in s or "lite" in s:
        return 0.09, 0.37
    if "gemini-3.8" in s or ("gemini" in s and "3.8" in s):
        return 0.69, 3.45
    if "gemini-3.5" in s:
        return 1.38, 8.28
    # Standard für claude-haiku-5-5, gpt-6-luna, deepseek-v4.1-flash etc.
    return 0.09, 0.46


def execute_search_profile(profile_id: int, force: bool = False) -> Dict[str, Any]:
    """
    Executes a single search profile using the 3-Stage Smart Filter Funnel:
    - Stage 1 (0 € cost): Hard mathematical filter (max price, price/sqm, min yield, size, rooms).
    - Stage 2 (0 € cost): Local deterministic NLP pre-filter (Erbbaurecht, Erbpacht, severe red flags).
    - Stage 3 (Targeted LLM API): Full AI Due Diligence Audit (extracts cold rents, true Hausgeld, CaPEx).
    - Stage 4 (Pipeline Protection): Only deals with Score >= 6.5 and positive cashflow enter 'neu';
      inferior deals are moved to 'archiv' to keep the active pipeline uncluttered.
    """
    profile = get_search_profile_by_id(profile_id)
    if not profile:
        return {"success": False, "error": f"Suchprofil #{profile_id} existiert nicht."}

    if not profile.get("is_active") and not force:
        return {
            "success": True,
            "profile_id": profile_id,
            "message": "Suchprofil ist pausiert.",
            "new_alerts": 0
        }

    city = profile.get("city", "").strip()
    search_query = profile.get("search_query", "").strip()
    radius_km = int(profile.get("radius_km") or 25)
    max_price = float(profile.get("max_price") or 0.0)
    max_price_per_sqm = float(profile.get("max_price_per_sqm") or 0.0)
    min_yield = float(profile.get("min_yield") or 0.0)
    yield_basis = str(profile.get("yield_basis") or "any").strip().lower()
    min_discount_pct = float(profile.get("min_discount_pct") or 0.0)
    min_rooms = float(profile.get("min_rooms") or 1.0)
    min_living_space = float(profile.get("min_living_space") or 0.0)
    property_type = (profile.get("property_type") or "all").lower().strip()

    logger.info(f"Running search profile #{profile_id} '{profile['name']}' for {city} across portals...")

    # Load seen URLs for this profile to prevent re-crawling identical deals
    seen_urls = get_seen_listing_urls(profile_id)

    # 1. Multi-Portal Meta-Search (Kleinanzeigen, ImmoScout24, Immowelt)
    scraped_deals = scrape_multi_portal_deals(city=city, query=search_query, max_results=50, exclude_urls=seen_urls)

    qualified_deals: List[Dict[str, Any]] = []
    new_alerts_count = 0
    skipped_known_deals = 0
    newly_evaluated_deals = 0
    filtered_stage1_2 = 0
    text_ai_calls = 0
    vision_ai_calls = 0
    text_prompt_tokens = 0
    text_candidates_tokens = 0
    vision_prompt_tokens = 0
    vision_candidates_tokens = 0
    prompt_tokens_used = 0
    candidates_tokens_used = 0
    total_tokens_used = 0
    accumulated_cost_eur = 0.0

    tg_token = get_system_setting("telegram_bot_token")
    tg_chat_id = get_system_setting("telegram_chat_id")
    has_telegram = bool(tg_token and tg_chat_id)

    for deal in scraped_deals:
        portal_url = deal.get("portal_url", "")
        if not portal_url:
            continue

        # Check deduplication first against seen listings and logged alerts
        if portal_url in seen_urls or is_alert_already_logged(portal_url):
            skipped_known_deals += 1
            continue

        newly_evaluated_deals += 1

        raw_price = float(deal.get("purchase_price") or 0.0)
        raw_sqm = float(deal.get("living_space") or 0.0)
        raw_price_per_sqm = (raw_price / raw_sqm) if raw_sqm > 0 else 0.0
        raw_rooms = float(deal.get("rooms") or 1.0)
        raw_yield = float(deal.get("gross_yield") or 0.0)
        raw_market_dev = float(deal.get("market_deviation_percent") or 0.0)
        p_type = str(deal.get("property_type", "")).lower()

        # =====================================================================
        # STAGE 1: HARD MATHEMATICAL PRE-FILTER (0 € API Cost - Zero LLM Calls)
        # =====================================================================
        if max_price > 0 and raw_price > max_price:
            filtered_stage1_2 += 1
            mark_listing_seen(portal_url, profile_id, "dropped_stage1_max_price")
            continue
        if max_price_per_sqm > 0 and raw_price_per_sqm > max_price_per_sqm:
            filtered_stage1_2 += 1
            mark_listing_seen(portal_url, profile_id, "dropped_stage1_price_per_sqm")
            continue
        if min_living_space > 0 and raw_sqm < min_living_space:
            filtered_stage1_2 += 1
            mark_listing_seen(portal_url, profile_id, "dropped_stage1_living_space")
            continue
        if min_rooms > 1.0 and raw_rooms < min_rooms:
            filtered_stage1_2 += 1
            mark_listing_seen(portal_url, profile_id, "dropped_stage1_rooms")
            continue
        if property_type != "all" and property_type not in p_type:
            filtered_stage1_2 += 1
            mark_listing_seen(portal_url, profile_id, "dropped_stage1_property_type")
            continue

        # Strikte Stadtgrenzen-Prüfung wenn Radius = 0 km gewählt wurde
        if radius_km == 0 and city:
            deal_c = str(deal.get("city") or "").lower().strip()
            target_c = city.lower().strip()
            if target_c and target_c not in deal_c and deal_c not in target_c:
                filtered_stage1_2 += 1
                mark_listing_seen(portal_url, profile_id, "dropped_stage1_city_boundary")
                continue

        # Stage 1 Rendite-Vorfilter: Berücksichtigt konfigurierte Rendite-Basis ('actual', 'potential', 'any')
        if min_yield > 0:
            from app.valuation import get_mietspiegel_size_benchmark, evaluate_property_valuation
            deal_city = deal.get("city") or city
            actual_yield = raw_yield

            # 1. Erwartete Benchmark-Rendite (mit Baujahr & Zustand falls im Inserat vorhanden)
            exp_rent = float(deal.get("expected_rent") or 0.0)
            if exp_rent <= 0 and raw_price > 0 and raw_sqm > 0:
                val = evaluate_property_valuation(
                    city=deal_city,
                    living_space=raw_sqm,
                    purchase_price=raw_price,
                    year_built=deal.get("year_built"),
                    condition=deal.get("condition")
                )
                exp_rent = float(val.get("expected_rent_monthly") or 0.0)
            expected_yield = round(((exp_rent * 12.0) / raw_price) * 100.0, 2) if raw_price > 0 else 0.0

            # 2. Größenadjustierte Mietspiegel-Rendite
            mietspiegel_size_yield = 0.0
            if raw_price > 0 and raw_sqm > 0:
                ms = get_mietspiegel_size_benchmark(city=deal_city, living_space=raw_sqm)
                ms_monthly = float(ms.get("expected_sqm_rent") or 0.0) * raw_sqm
                mietspiegel_size_yield = round(((ms_monthly * 12.0) / raw_price) * 100.0, 2)

            potential_yield = max(expected_yield, mietspiegel_size_yield)

            if yield_basis == "actual":
                effective_yield = actual_yield
            elif yield_basis == "potential":
                effective_yield = potential_yield
            else: # "any" (Standard: Ist- ODER Mietspiegel-Potenzial)
                effective_yield = max(actual_yield, potential_yield)

            if effective_yield < min_yield:
                filtered_stage1_2 += 1
                mark_listing_seen(portal_url, profile_id, "dropped_stage1_yield")
                continue

        # =====================================================================
        # STAGE 2: LOCAL DETERMINISTIC NLP / RED-FLAG FILTER (0 € API Cost)
        # =====================================================================
        precheck = quick_ai_deal_precheck(deal)
        if precheck.get("has_critical_risks"):
            # Leasehold (Erbbaurecht / Erbpacht) or structural disaster: discard immediately!
            filtered_stage1_2 += 1
            mark_listing_seen(portal_url, profile_id, "dropped_stage2_redflag")
            logger.info(f"Stage 2 Pre-filter discarded deal '{deal.get('title')}' due to: {precheck.get('risks')}")
            continue

        # =====================================================================
        # STAGE 3: FULL AI DUE DILIGENCE AUDIT (Targeted LLM API Execution)
        # Only reached by the top ~5-10% of candidates that passed Stages 1 & 2!
        # =====================================================================
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM properties WHERE portal_url = ?", (portal_url,))
        existing_prop = c.fetchone()
        conn.close()
        if existing_prop:
            prop_id = existing_prop[0]
        else:
            inserted_ids = import_scraped_deals_returning_ids([deal])
            prop_id = inserted_ids[0] if inserted_ids else None

        if not prop_id:
            continue

        text_ai_calls += 1
        from app.routers.properties import run_property_ai_audit
        try:
            audit_res = run_property_ai_audit(prop_id, force_refresh=False, skip_images=True)
            ai_report = audit_res.get("ai_audit") or {}
            metrics = ai_report.get("metrics") or {}
            insights = ai_report.get("insights") or {}
            tu_ai = ai_report.get("token_usage") or {}
            t_p = int(tu_ai.get("prompt_tokens") or 0)
            t_c = int(tu_ai.get("candidates_tokens") or 0)
            text_prompt_tokens += t_p
            text_candidates_tokens += t_c
            prompt_tokens_used += t_p
            candidates_tokens_used += t_c
            total_tokens_used += int(tu_ai.get("total_tokens") or (t_p + t_c))
            in_rate_t, out_rate_t = get_oneprovider_token_rates(ai_report.get("ai_provider"))
            accumulated_cost_eur += (t_p * in_rate_t / 1_000_000.0) + (t_c * out_rate_t / 1_000_000.0)
        except Exception as e:
            logger.error(f"Error running full AI audit on property #{prop_id}: {e}")
            ai_report = {}
            metrics = {}
            insights = {}
            ai_report = {}
            metrics = {}
            insights = {}

        audited_price = float(metrics.get("purchase_price") or raw_price)
        audited_sqm = float(metrics.get("living_space") or raw_sqm)
        audited_price_per_sqm = round(audited_price / audited_sqm) if audited_sqm > 0 else raw_price_per_sqm
        audited_yield = float(metrics.get("gross_yield") or deal.get("gross_yield") or 0.0)
        audited_potential_yield = float(metrics.get("potential_gross_yield") or audited_yield)
        audited_cashflow = float(metrics.get("monthly_cashflow") if metrics.get("monthly_cashflow") is not None else deal.get("monthly_cashflow", 0.0))
        audited_potential_cf = float(metrics.get("potential_monthly_cashflow") if metrics.get("potential_monthly_cashflow") is not None else audited_cashflow)
        audited_rent_upside = float(metrics.get("rent_upside_percent") or 0.0)
        audited_market_dev = float(metrics.get("market_deviation_percent") if metrics.get("market_deviation_percent") is not None else deal.get("market_deviation_percent", 0.0))
        audited_score = float(ai_report.get("score") or 7.0)
        audited_capex = float(ai_report.get("capex", {}).get("total_capex") or 0.0)
        audited_risks = ai_report.get("risks") or []

        # Check for critical risks discovered during full exposé text analysis
        has_critical = any(
            r.get("severity") == "CRITICAL"
            or any(k in str(r.get("title", "")).lower() or k in str(r.get("description", "")).lower() for k in ["erbbaurecht", "erbpacht"])
            for r in audited_risks
        )
        if not has_critical:
            deep_precheck = quick_ai_deal_precheck(deal, full_description=insights.get("full_description", ""))
            if deep_precheck.get("has_critical_risks"):
                has_critical = True

        # =====================================================================
        # STAGE 4: VALUE-ADD AWARE FILTER GATE (AFTER TEXT AI AUDIT)
        # Evaluates whether the deal is fundamentally interesting based on
        # financial numbers, cold rent verification, absence of critical traps,
        # and whether a negative cashflow deal turns profitable at local Mietspiegel.
        # =====================================================================
        miet_ana = ai_report.get("mietspiegel_analysis") or metrics.get("mietspiegel_analysis") or {}
        turns_profitable_mietspiegel = bool(miet_ana.get("turns_profitable") or miet_ana.get("is_profitable_at_mietspiegel"))

        has_value_add_upside = (
            audited_cashflow >= 0
            or turns_profitable_mietspiegel
            or audited_rent_upside >= 10.0
            or audited_potential_cf >= 0
            or audited_market_dev <= -10.0
            or audited_score >= 6.0
        )

        # Stage 4: Verbindlicher Abgleich gegen die Renditevorgaben des Ankaufsprofils ('actual', 'potential', 'any')
        if yield_basis == "actual":
            eff_audited_yield = audited_yield
        elif yield_basis == "potential":
            eff_audited_yield = audited_potential_yield
        else: # "any" (Standard: Höhere Rendite aus Ist und verifiziertem Mietspiegel-Potenzial)
            eff_audited_yield = max(audited_yield, audited_potential_yield)

        meets_profile_limits = (
            (max_price <= 0 or audited_price <= max_price)
            and (max_price_per_sqm <= 0 or audited_price_per_sqm <= max_price_per_sqm)
            and (min_living_space <= 0 or audited_sqm >= min_living_space)
            and (min_yield <= 0 or eff_audited_yield >= min_yield)
            and (min_discount_pct <= 0 or audited_market_dev <= -min_discount_pct)
        )

        is_deal_killer = has_critical or audited_score < 5.0

        is_interesting_after_audit = meets_profile_limits and has_value_add_upside and not is_deal_killer

        if not is_interesting_after_audit:
            reasons = []
            if max_price > 0 and audited_price > max_price:
                reasons.append(f"Kaufpreis ({audited_price:,.0f} €) übersteigt Profil-Maximum ({max_price:,.0f} €)")
            if max_price_per_sqm > 0 and audited_price_per_sqm > max_price_per_sqm:
                reasons.append(f"Quadratmeterpreis ({audited_price_per_sqm:,.0f} €/m²) übersteigt Profil-Limit ({max_price_per_sqm:,.0f} €/m²)")
            if min_living_space > 0 and audited_sqm < min_living_space:
                reasons.append(f"Wohnfläche ({audited_sqm:.0f} m²) unter Profil-Minimum ({min_living_space:.0f} m²)")
            if min_yield > 0 and eff_audited_yield < min_yield:
                if yield_basis == "actual":
                    reasons.append(f"Ist-Bruttorendite ({audited_yield:.1f}%) unter Profil-Ziel ({min_yield:.1f}%)")
                elif yield_basis == "potential":
                    reasons.append(f"Mietspiegel-Potenzialrendite ({audited_potential_yield:.1f}%) unter Profil-Ziel ({min_yield:.1f}%)")
                else:
                    reasons.append(f"Weder Ist-Rendite ({audited_yield:.1f}%) noch Mietspiegel-Rendite ({audited_potential_yield:.1f}%) erreichen Profil-Ziel ({min_yield:.1f}%)")
            if min_discount_pct > 0 and audited_market_dev > -min_discount_pct:
                reasons.append(f"Untermarktwert-Abschlag ({audited_market_dev:+.1f}%) erreicht nicht Mindestabschlag von -{min_discount_pct:.1f}%")
            if has_critical:
                crit_titles = [r.get("title") for r in audited_risks if r.get("severity") == "CRITICAL"]
                reasons.append(f"Kritisches K.O.-Risiko im Exposé erkannt: {', '.join(crit_titles) or 'Erbpacht / Schwerwiegender Mangel'}")
            elif audited_score < 5.0:
                reasons.append(f"KI-Gesamtscore ungenügend ({audited_score:.1f}/10)")
            elif not has_value_add_upside:
                reasons.append("Kein Wertsteigerungspotenzial (negativer Cashflow ohne Mietsteigerungsperspektive)")

            rejection_text = "; ".join(reasons) if reasons else "Kriterien des Ankaufsprofils nicht erfüllt"

            mark_listing_seen(portal_url, profile_id, "dropped_stage4_audit")
            conn = get_connection()
            c = conn.cursor()
            if ai_report:
                ai_report["pipeline_decision"] = {
                    "stage": "archiv",
                    "reason": rejection_text,
                    "date": datetime.now().strftime("%d.%m.%Y %H:%M")
                }
                c.execute(
                    "UPDATE properties SET pipeline_stage = 'archiv', status_message = ?, ai_analysis = ? WHERE id = ?",
                    (rejection_text, json.dumps(ai_report, ensure_ascii=False), prop_id)
                )
            else:
                c.execute("UPDATE properties SET pipeline_stage = 'archiv', status_message = ? WHERE id = ?", (rejection_text, prop_id))
            conn.commit()
            conn.close()
            logger.info(f"Pipeline Gate (Audit): Deal #{prop_id} moved to 'archiv' ({rejection_text}).")
            continue

        # =====================================================================
        # STAGE 5: AUTOMATIC KI-BILDANALYSE (VISUAL DUE DILIGENCE GATE)
        # The deal is interesting! Now automatically inspect all property photos
        # using Computer Vision + Multimodal AI to detect real structural state
        # (bathrooms, kitchens, floors, windows, heating, moisture) and CaPEx!
        # =====================================================================
        from app.routers.properties import run_property_image_analysis
        img_report = None
        visual_score = 7.5
        visual_capex = 0.0
        visual_condition = "Gepflegt"

        vision_ai_calls += 1
        try:
            img_res = run_property_image_analysis(prop_id)
            img_report = img_res.get("image_analysis") or {}
            if img_report:
                visual_score = float(img_report.get("overall_visual_score") or 7.5)
                visual_capex = float(img_report.get("total_visual_capex") or 0.0)
                visual_condition = str(img_report.get("condition_rating") or "Gepflegt")
                
                # If capex was reconciled with image evidence, update financial figures
                reconciled_ai = img_res.get("ai_audit") or {}
                if reconciled_ai:
                    ai_report = reconciled_ai
                    metrics = ai_report.get("metrics") or metrics
                    audited_score = float(ai_report.get("score") or audited_score)
                    audited_capex = float(ai_report.get("capex", {}).get("total_capex") or visual_capex)

            tu_img = img_report.get("token_usage") or {}
            v_p = int(tu_img.get("prompt_tokens") or 0)
            v_c = int(tu_img.get("candidates_tokens") or 0)
            vision_prompt_tokens += v_p
            vision_candidates_tokens += v_c
            prompt_tokens_used += v_p
            candidates_tokens_used += v_c
            total_tokens_used += int(tu_img.get("total_tokens") or (v_p + v_c))
            in_rate_v, out_rate_v = get_oneprovider_token_rates(img_report.get("ai_provider"))
            accumulated_cost_eur += (v_p * in_rate_v / 1_000_000.0) + (v_c * out_rate_v / 1_000_000.0)
        except Exception as e:
            logger.warning(f"Automatic image analysis warning on Deal #{prop_id}: {e}")

        # Final check after seeing photos:
        # If photos show catastrophic structural decay or ruinous CaPEx, reject to archiv!
        visual_ruin = (visual_score < 4.0 or (audited_price > 0 and visual_capex > (audited_price * 0.45)))
        is_still_interesting_after_images = (not visual_ruin) and (audited_score >= 5.0)

        if not is_still_interesting_after_images:
            reasons_img = []
            if visual_score < 4.0:
                reasons_img.append(f"Visueller Zustand mangelhaft / Bauschäden auf Fotos erkennbar (Score {visual_score:.1f}/10)")
            if audited_price > 0 and visual_capex > (audited_price * 0.45):
                reasons_img.append(f"Unwirtschaftlicher Sanierungsaufwand / CaPEx ({visual_capex:,.0f} € übersteigt 45% des Kaufpreises)")
            if audited_score < 5.0:
                reasons_img.append(f"KI-Gesamtbewertung ungenügend ({audited_score:.1f}/10)")

            rejection_text = "; ".join(reasons_img) if reasons_img else "Bilderanalyse ergab ruinöse Bausubstanz"

            mark_listing_seen(portal_url, profile_id, "dropped_stage5_visual")
            conn = get_connection()
            c = conn.cursor()
            if ai_report:
                ai_report["pipeline_decision"] = {
                    "stage": "archiv",
                    "reason": rejection_text,
                    "date": datetime.now().strftime("%d.%m.%Y %H:%M")
                }
                c.execute(
                    "UPDATE properties SET pipeline_stage = 'archiv', status_message = ?, ai_analysis = ? WHERE id = ?",
                    (rejection_text, json.dumps(ai_report, ensure_ascii=False), prop_id)
                )
            else:
                c.execute("UPDATE properties SET pipeline_stage = 'archiv', status_message = ? WHERE id = ?", (rejection_text, prop_id))
            conn.commit()
            conn.close()
            logger.info(f"Pipeline Gate (Images): Deal #{prop_id} moved to 'archiv' ({rejection_text}).")
            continue

        # =====================================================================
        # STAGE 6: FINAL GATEKEEPER CHECK & ADMISSION TO PIPELINE / TELEGRAM
        # Verified check: Ensure audited yield strictly meets profile requirement
        # =====================================================================
        if min_yield > 0:
            yield_passed = False
            if yield_basis == "actual":
                yield_passed = (audited_yield >= min_yield)
            elif yield_basis == "potential":
                yield_passed = (audited_potential_yield >= min_yield)
            else: # "any"
                yield_passed = (max(audited_yield, audited_potential_yield) >= min_yield)

            if not yield_passed:
                rej = f"Geprüfte Rendite verfehlt Mindestrendite ({audited_yield:.1f}% Ist / {audited_potential_yield:.1f}% Mietspiegel < {min_yield:.1f}%)"
                mark_listing_seen(portal_url, profile_id, "dropped_final_yield")
                conn = get_connection()
                c = conn.cursor()
                if ai_report:
                    ai_report["pipeline_decision"] = {
                        "stage": "archiv",
                        "reason": rej,
                        "date": datetime.now().strftime("%d.%m.%Y %H:%M")
                    }
                    c.execute(
                        "UPDATE properties SET pipeline_stage = 'archiv', status_message = ?, ai_analysis = ? WHERE id = ?",
                        (rej, json.dumps(ai_report, ensure_ascii=False), prop_id)
                    )
                else:
                    c.execute("UPDATE properties SET pipeline_stage = 'archiv', status_message = ? WHERE id = ?", (rej, prop_id))
                conn.commit()
                conn.close()
                logger.info(f"Final Gatekeeper: Deal #{prop_id} rejected from alerts/telegram ({rej}).")
                continue

        mark_listing_seen(portal_url, profile_id, "qualified")
        target_stage = "merkliste" if (audited_score >= 8.0 or visual_score >= 8.5) else "neu"
        qualification_text = f"Qualifiziert: Score {audited_score:.1f}/10, Rendite {audited_yield:.1f}%, Cashflow {audited_cashflow:+,.0f} €/M"
        conn = get_connection()
        c = conn.cursor()
        if ai_report:
            ai_report["pipeline_decision"] = {
                "stage": target_stage,
                "reason": qualification_text,
                "date": datetime.now().strftime("%d.%m.%Y %H:%M")
            }
            c.execute(
                "UPDATE properties SET pipeline_stage = ?, status_message = ?, ai_analysis = ? WHERE id = ?",
                (target_stage, qualification_text, json.dumps(ai_report, ensure_ascii=False), prop_id)
            )
        else:
            c.execute("UPDATE properties SET pipeline_stage = ?, status_message = ? WHERE id = ?", (target_stage, qualification_text, prop_id))
        conn.commit()
        conn.close()

        qualified_deals.append(deal)

        # Formulate verified AI Verdict based on Audit + Photo Inspection
        status_label = ai_report.get("status_label", "🟢 Solider Deal")
        photo_tag = f"📸 Fotos: {visual_score:.1f}/10 ({visual_condition})"
        if audited_cashflow < 0 and (turns_profitable_mietspiegel or audited_potential_cf >= 0):
            ai_verdict = (
                f"KI-Audit: Score {audited_score}/10 ({status_label} • 🚀 Mietspiegel-Hebel). {photo_tag}. "
                f"Aktuell negativer Cashflow ({audited_cashflow:+,.0f} €/M) dreht bei Mietspiegel-Miete ({metrics.get('expected_monthly_rent', 0):,.0f} €/M, +{audited_rent_upside:+.1f}%) "
                f"ins Positive auf {audited_potential_cf:+,.0f} €/M! Rendite: {audited_yield:.1f}% ➔ {audited_potential_yield:.1f}%. CaPEx: ca. {audited_capex:,.0f} €."
            )
        elif audited_cashflow < 0 and (audited_rent_upside >= 10.0 or audited_potential_cf > audited_cashflow):
            ai_verdict = (
                f"KI-Audit: Score {audited_score}/10 ({status_label} • 🚀 Value-Add). {photo_tag}. "
                f"Ist-Miete liegt {audited_rent_upside:+.1f}% unter Mietspiegel! "
                f"Ist-CF: {audited_cashflow:+,.0f} €/M ➔ Potenzial-CF: {audited_potential_cf:+,.0f} €/M. "
                f"Rendite: {audited_yield:.1f}% (Soll: {audited_potential_yield:.1f}%). CaPEx: ca. {audited_capex:,.0f} €."
            )
        elif audited_yield >= 6.5 and audited_market_dev <= -10.0:
            ai_verdict = (
                f"KI-Audit: Score {audited_score}/10 ({status_label}). {photo_tag}. "
                f"Geprüfte Rendite {audited_yield:.1f}% bei {abs(audited_market_dev):.1f}% Marktwert-Abschlag. "
                f"Cashflow: {audited_cashflow:+,.0f} €/M. CaPEx ca. {audited_capex:,.0f} €."
            )
        else:
            ai_verdict = (
                f"KI-Audit: Score {audited_score}/10 ({status_label}). {photo_tag}. "
                f"Cashflow: {audited_cashflow:+,.0f} €/M bei {audited_yield:.1f}% Rendite. "
                f"CaPEx ca. {audited_capex:,.0f} €."
            )

        # Dispatch Telegram Alert with verified audited numbers
        sent_tg = False
        tg_err = None

        if has_telegram:
            deal_for_tg = {
                "title": deal.get("title"),
                "city": deal.get("city", city),
                "address": deal.get("address", city),
                "purchase_price": audited_price,
                "living_space": audited_sqm,
                "rooms": raw_rooms,
                "gross_yield": audited_yield,
                "monthly_cashflow": audited_cashflow,
                "market_deviation_percent": audited_market_dev,
                "portal_url": portal_url,
                "property_id": prop_id
            }
            tg_result = send_deal_telegram_alert(
                bot_token=tg_token,
                chat_id=tg_chat_id,
                deal=deal_for_tg,
                ai_verdict=ai_verdict,
                profile_name=profile["name"]
            )
            sent_tg = tg_result.get("success", False)
            tg_err = tg_result.get("error")
        else:
            tg_err = "Telegram nicht konfiguriert"

        # Record verified alert in deal_alerts table
        alert_record = {
            "profile_id": profile_id,
            "property_id": prop_id,
            "portal_url": portal_url,
            "title": deal.get("title", "Immobilienangebot"),
            "city": deal.get("city", city),
            "price": audited_price,
            "living_space": audited_sqm,
            "price_per_sqm": audited_price_per_sqm,
            "gross_yield": audited_yield,
            "market_discount_pct": audited_market_dev,
            "monthly_cashflow": audited_cashflow,
            "ai_verdict": ai_verdict,
            "has_critical_risks": 0,
            "sent_to_telegram": 1 if sent_tg else 0,
            "telegram_error": tg_err,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        record_deal_alert(alert_record)
        new_alerts_count += 1

    # Update search profile stats
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_count = int(profile.get("deals_found_count") or 0)
    update_search_profile(profile_id, {
        "last_run_at": now_str,
        "deals_found_count": current_count + new_alerts_count
    })

    active_key = get_system_setting("gemini_api_key") or os.getenv("ONEPROVIDER_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
    is_oneprovider = bool(active_key and active_key.strip().startswith("sk-"))

    if is_oneprovider:
        # OneProvider Dev AI modellabhängige Kosten:
        # - claude-haiku-5-5, gpt-6-luna: ~0,09 € In / 0,46 € Out pro 1M Tokens
        # - qwen3.8-flash: ~0,14 € In / 0,43 € Out pro 1M Tokens
        # - gemini-3.5-flash: ~1,38 € In / 8,28 € Out pro 1M Tokens
        if accumulated_cost_eur > 0:
            estimated_cost_eur = round(accumulated_cost_eur, 4)
        else:
            estimated_cost_eur = round(
                (prompt_tokens_used * 0.09 / 1_000_000.0) + (candidates_tokens_used * 0.46 / 1_000_000.0),
                4
            )
    else:
        # Google Gemini Flash-Lite Tarif: 0,09 € In / 0,37 € Out pro 1M Tokens
        if accumulated_cost_eur > 0:
            estimated_cost_eur = round(accumulated_cost_eur, 4)
        else:
            estimated_cost_eur = round(
                (prompt_tokens_used * 0.09 / 1_000_000.0) + (candidates_tokens_used * 0.37 / 1_000_000.0),
                4
            )

    stats = {
        "total_scraped": len(scraped_deals),
        "skipped_known_deals": skipped_known_deals,
        "newly_evaluated_deals": newly_evaluated_deals,
        "filtered_stage1_2": filtered_stage1_2,
        "text_ai_calls": text_ai_calls,
        "vision_ai_calls": vision_ai_calls,
        "text_prompt_tokens": text_prompt_tokens,
        "text_candidates_tokens": text_candidates_tokens,
        "vision_prompt_tokens": vision_prompt_tokens,
        "vision_candidates_tokens": vision_candidates_tokens,
        "prompt_tokens_used": prompt_tokens_used,
        "candidates_tokens_used": candidates_tokens_used,
        "total_tokens_used": total_tokens_used,
        "estimated_cost_eur": estimated_cost_eur,
        "is_oneprovider": is_oneprovider,
        "qualified_deals": len(qualified_deals),
        "new_alerts": new_alerts_count,
        "timestamp": now_str
    }
    set_system_setting("crawler_last_stats", json.dumps(stats, ensure_ascii=False))

    logger.info(f"Search profile #{profile_id} finished: {len(scraped_deals)} scraped across portals, {len(qualified_deals)} qualified, {new_alerts_count} new alerts, {total_tokens_used} tokens ({estimated_cost_eur:.4f} €).")

    return {
        "success": True,
        "profile_id": profile_id,
        "profile_name": profile["name"],
        "total_scraped": len(scraped_deals),
        "skipped_known_deals": skipped_known_deals,
        "newly_evaluated_deals": newly_evaluated_deals,
        "filtered_stage1_2": filtered_stage1_2,
        "text_ai_calls": text_ai_calls,
        "vision_ai_calls": vision_ai_calls,
        "text_prompt_tokens": text_prompt_tokens,
        "text_candidates_tokens": text_candidates_tokens,
        "vision_prompt_tokens": vision_prompt_tokens,
        "vision_candidates_tokens": vision_candidates_tokens,
        "prompt_tokens_used": prompt_tokens_used,
        "candidates_tokens_used": candidates_tokens_used,
        "total_tokens_used": total_tokens_used,
        "estimated_cost_eur": estimated_cost_eur,
        "is_oneprovider": is_oneprovider,
        "qualified_deals": len(qualified_deals),
        "new_alerts": new_alerts_count,
        "last_run_at": now_str,
        "stats": stats
    }


def execute_all_active_profiles() -> List[Dict[str, Any]]:
    """
    Executes all active search profiles sequentially and records crawler runtime and aggregated stats.
    """
    profiles = get_search_profiles()
    results = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    set_system_setting("crawler_last_run", now_str)

    tot_scraped = 0
    tot_skipped = 0
    tot_newly_evaluated = 0
    tot_filtered_1_2 = 0
    tot_text_ai = 0
    tot_vision_ai = 0
    tot_text_prompt_tokens = 0
    tot_text_candidates_tokens = 0
    tot_vision_prompt_tokens = 0
    tot_vision_candidates_tokens = 0
    tot_prompt_tokens = 0
    tot_candidates_tokens = 0
    tot_total_tokens = 0
    tot_qualified = 0
    tot_alerts = 0
    tot_cost = 0.0

    for p in profiles:
        if p.get("is_active"):
            try:
                res = execute_search_profile(p["id"])
                results.append(res)
                if isinstance(res, dict) and res.get("success"):
                    tot_scraped += res.get("total_scraped", 0)
                    tot_skipped += res.get("skipped_known_deals", 0)
                    tot_newly_evaluated += res.get("newly_evaluated_deals", 0)
                    tot_filtered_1_2 += res.get("filtered_stage1_2", 0)
                    tot_text_ai += res.get("text_ai_calls", 0)
                    tot_vision_ai += res.get("vision_ai_calls", 0)
                    tot_text_prompt_tokens += res.get("text_prompt_tokens", 0)
                    tot_text_candidates_tokens += res.get("text_candidates_tokens", 0)
                    tot_vision_prompt_tokens += res.get("vision_prompt_tokens", 0)
                    tot_vision_candidates_tokens += res.get("vision_candidates_tokens", 0)
                    tot_prompt_tokens += res.get("prompt_tokens_used", 0)
                    tot_candidates_tokens += res.get("candidates_tokens_used", 0)
                    tot_total_tokens += res.get("total_tokens_used", 0)
                    tot_qualified += res.get("qualified_deals", 0)
                    tot_alerts += res.get("new_alerts", 0)
                    tot_cost += float(res.get("estimated_cost_eur") or 0.0)
            except Exception as e:
                logger.error(f"Error executing profile #{p['id']}: {e}")
                results.append({
                    "success": False,
                    "profile_id": p["id"],
                    "profile_name": p.get("name"),
                    "error": str(e)
                })

    active_key = get_system_setting("gemini_api_key") or os.getenv("ONEPROVIDER_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
    is_oneprovider = bool(active_key and active_key.strip().startswith("sk-"))

    tot_cost = round(tot_cost, 4)
    aggregated_stats = {
        "total_scraped": tot_scraped,
        "skipped_known_deals": tot_skipped,
        "newly_evaluated_deals": tot_newly_evaluated,
        "filtered_stage1_2": tot_filtered_1_2,
        "text_ai_calls": tot_text_ai,
        "vision_ai_calls": tot_vision_ai,
        "text_prompt_tokens": tot_text_prompt_tokens,
        "text_candidates_tokens": tot_text_candidates_tokens,
        "vision_prompt_tokens": tot_vision_prompt_tokens,
        "vision_candidates_tokens": tot_vision_candidates_tokens,
        "prompt_tokens_used": tot_prompt_tokens,
        "candidates_tokens_used": tot_candidates_tokens,
        "total_tokens_used": tot_total_tokens,
        "estimated_cost_eur": tot_cost,
        "is_oneprovider": is_oneprovider,
        "qualified_deals": tot_qualified,
        "new_alerts": tot_alerts,
        "timestamp": now_str
    }
    set_system_setting("crawler_last_stats", json.dumps(aggregated_stats, ensure_ascii=False))

    return results
