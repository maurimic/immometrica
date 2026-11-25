"""
Settings router for ImmoMetrica Clone.
Manages user preferences, external API keys (Google Gemini & OneProvider Dev AI), and system configuration.
"""

import os
import requests
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Body
from app.database import get_system_setting, set_system_setting, delete_system_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = logging.getLogger("immometrica.settings")


def mask_key(k: Optional[str]) -> Optional[str]:
    if not k:
        return None
    k = k.strip()
    if len(k) <= 8:
        return "***"
    if k.startswith("sk-"):
        return f"{k[:6]}...{k[-4:]}"
    return f"{k[:5]}...{k[-4:]}"


def get_stored_keys():
    """Liest die gespeicherten OneProvider- und Gemini-Keys sowie den aktiven Provider aus."""
    one_key = get_system_setting("oneprovider_api_key") or os.environ.get("ONEPROVIDER_API_KEY")
    gem_key = get_system_setting("google_gemini_api_key") or os.environ.get("GEMINI_API_KEY")
    legacy = get_system_setting("gemini_api_key")
    if not one_key and legacy and legacy.strip().startswith("sk-"):
        one_key = legacy.strip()
    if not gem_key and legacy and not legacy.strip().startswith("sk-"):
        gem_key = legacy.strip()

    active_prov = get_system_setting("active_ai_provider")
    if not active_prov:
        if one_key:
            active_prov = "oneprovider"
        elif gem_key:
            active_prov = "gemini"
        else:
            active_prov = "oneprovider"

    return one_key, gem_key, active_prov


def verify_oneprovider_key(key: str) -> tuple:
    test_url = "https://api.oneprovider.dev/v1/models"
    headers = {"Authorization": f"Bearer {key.strip()}"}
    try:
        resp = requests.get(test_url, headers=headers, timeout=7.0)
        if resp.status_code == 200:
            return True, ""
        try:
            err_data = resp.json()
            return False, err_data.get("error", {}).get("message", f"HTTP {resp.status_code}")
        except Exception:
            return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, f"Verbindungsfehler: {str(e)}"


def verify_gemini_key(key: str) -> tuple:
    from app.network_security import sanitize_error_message
    test_url = "https://generativelanguage.googleapis.com/v1beta/models"
    try:
        resp = requests.get(test_url, headers={"x-goog-api-key": key.strip()}, timeout=7.0)
        if resp.status_code == 200:
            return True, ""
        try:
            err_data = resp.json()
            return False, sanitize_error_message(err_data.get("error", {}).get("message", f"HTTP {resp.status_code}"))
        except Exception:
            return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, f"Verbindungsfehler: {sanitize_error_message(e)}"


@router.get("/gemini-key")
def get_gemini_key_status():
    """
    Liefert den Status beider konfigurierter API-Keys (OneProvider & Google Gemini)
    und gibt an, welcher Provider aktuell für Text- und Bildanalyse aktiv ist.
    """
    one_key, gem_key, active_prov = get_stored_keys()
    active_key = one_key if active_prov == "oneprovider" else gem_key
    is_oneprovider = (active_prov == "oneprovider")
    provider_name = "OneProvider Dev AI" if is_oneprovider else "Google Gemini Multimodal AI"

    return {
        "has_key": bool(active_key and active_key.strip()),
        "active_provider": active_prov,
        "is_oneprovider": is_oneprovider,
        "provider": provider_name,
        "masked_key": mask_key(active_key),
        "oneprovider": {
            "has_key": bool(one_key and one_key.strip()),
            "masked_key": mask_key(one_key),
            "model_info": "Claude Haiku 5.5 (Text & Vision)"
        },
        "gemini": {
            "has_key": bool(gem_key and gem_key.strip()),
            "masked_key": mask_key(gem_key),
            "model_info": "Gemini 3.5 / Flash (Kostenloser Tier)"
        }
    }


@router.post("/gemini-key")
def save_and_verify_gemini_key(payload: Dict[str, Any] = Body(...)):
    """
    Speichert und verifiziert Google Gemini- und/oder OneProvider-Keys.
    Ermöglicht die gleichzeitige Eingabe beider Keys und die Auswahl des aktiven Anbieters.
    """
    one_input = payload.get("oneprovider_key", "").strip()
    gem_input = payload.get("gemini_key", "").strip()
    legacy_input = payload.get("api_key", "").strip()
    active_input = payload.get("active_provider", "").strip().lower()
    force_save = payload.get("force", False)

    # Legacy-Kompatibilität: wenn nur 'api_key' übergeben wird
    if legacy_input and not one_input and not gem_input:
        if legacy_input.startswith("sk-"):
            one_input = legacy_input
            if not active_input:
                active_input = "oneprovider"
        else:
            gem_input = legacy_input
            if not active_input:
                active_input = "gemini"

    current_one, current_gem, current_active = get_stored_keys()
    updated_one = current_one
    updated_gem = current_gem
    messages = []

    # 1. OneProvider Key verifizieren & speichern, falls neu angegeben
    if one_input:
        if not one_input.startswith("sk-"):
            raise HTTPException(status_code=400, detail="OneProvider API-Key muss mit 'sk-' beginnen.")
        ok, err = verify_oneprovider_key(one_input)
        if not ok and not force_save:
            return {
                "success": False,
                "error": f"Ungültiger OneProvider API-Key ({err}). Bitte prüfe deinen Key auf oneprovider.dev.",
                "provider": "oneprovider"
            }
        set_system_setting("oneprovider_api_key", one_input)
        os.environ["ONEPROVIDER_API_KEY"] = one_input
        updated_one = one_input
        messages.append("OneProvider-Key verifiziert")

    # 2. Google Gemini Key verifizieren & speichern, falls neu angegeben
    if gem_input:
        ok, err = verify_gemini_key(gem_input)
        if not ok and not force_save:
            return {
                "success": False,
                "error": f"Ungültiger Google Gemini API-Key ({err}). Bitte prüfe den Key in Google AI Studio.",
                "provider": "gemini"
            }
        set_system_setting("google_gemini_api_key", gem_input)
        os.environ["GEMINI_API_KEY"] = gem_input
        updated_gem = gem_input
        messages.append("Gemini-Key verifiziert")

    # 3. Aktiven Provider festlegen
    if active_input in ["oneprovider", "gemini"]:
        selected_prov = active_input
    elif one_input:
        selected_prov = "oneprovider"
    elif gem_input:
        selected_prov = "gemini"
    else:
        selected_prov = current_active

    set_system_setting("active_ai_provider", selected_prov)

    # 4. Aktiven Key spiegeln für 100% Abwärtskompatibilität
    active_key = updated_one if selected_prov == "oneprovider" else updated_gem
    if active_key:
        set_system_setting("gemini_api_key", active_key)
        if selected_prov == "oneprovider":
            os.environ["ONEPROVIDER_API_KEY"] = active_key
            os.environ["GEMINI_API_KEY"] = active_key
        else:
            os.environ["GEMINI_API_KEY"] = active_key
            if "ONEPROVIDER_API_KEY" in os.environ and not updated_one:
                del os.environ["ONEPROVIDER_API_KEY"]

    msg = " & ".join(messages) if messages else "Einstellungen aktualisiert"
    prov_label = "OneProvider (Claude Haiku 5.5)" if selected_prov == "oneprovider" else "Google Gemini"
    full_msg = f"{msg}! Aktiver Provider: {prov_label}."

    prov_name = "OneProvider Dev AI" if (selected_prov == "oneprovider") else "Google Gemini Multimodal AI"
    return {
        "success": True,
        "message": full_msg,
        "provider": prov_name,
        "active_provider": selected_prov,
        "is_oneprovider": (selected_prov == "oneprovider"),
        "has_key": bool(active_key),
        "masked_key": mask_key(active_key),
        "oneprovider": {
            "has_key": bool(updated_one),
            "masked_key": mask_key(updated_one)
        },
        "gemini": {
            "has_key": bool(updated_gem),
            "masked_key": mask_key(updated_gem)
        }
    }


@router.post("/active-provider")
def set_active_provider(payload: Dict[str, Any] = Body(...)):
    """
    Schaltet den aktiven KI-Provider zwischen OneProvider und Google Gemini um,
    ohne dass Keys neu eingegeben werden müssen.
    """
    provider = payload.get("active_provider", "").strip().lower()
    if provider not in ["oneprovider", "gemini"]:
        raise HTTPException(status_code=400, detail="Ungültiger Provider. Erlaubt: 'oneprovider' oder 'gemini'.")

    one_key, gem_key, _ = get_stored_keys()

    if provider == "oneprovider" and not one_key:
        raise HTTPException(status_code=400, detail="Kein OneProvider API-Key hinterlegt. Bitte zuerst einen OneProvider-Key eingeben.")
    if provider == "gemini" and not gem_key:
        raise HTTPException(status_code=400, detail="Kein Google Gemini API-Key hinterlegt. Bitte zuerst einen Gemini-Key eingeben.")

    set_system_setting("active_ai_provider", provider)
    active_key = one_key if provider == "oneprovider" else gem_key
    set_system_setting("gemini_api_key", active_key)

    if provider == "oneprovider":
        os.environ["ONEPROVIDER_API_KEY"] = active_key
        os.environ["GEMINI_API_KEY"] = active_key
    else:
        os.environ["GEMINI_API_KEY"] = active_key

    prov_label = "OneProvider (Claude Haiku 5.5)" if provider == "oneprovider" else "Google Gemini"
    return {
        "success": True,
        "active_provider": provider,
        "is_oneprovider": (provider == "oneprovider"),
        "message": f"Aktiver Anbieter auf {prov_label} umgestellt!"
    }


@router.get("/oneprovider-balance")
def get_oneprovider_balance():
    """
    Abfrage des OneProvider-Kontostands.
    Fragt bei konfiguriertem OneProvider-Key die Balance ab:
    Primär: https://dashboard.oneprovider.dev/v1/dashboard/balance
    Fallback: https://api.oneprovider.dev/v1/dashboard/balance
    Ausschließlich Header: 'Authorization: Bearer <sk-key>' (niemals x-api-key).
    """
    one_key, _, _ = get_stored_keys()
    if not one_key or not one_key.strip().startswith("sk-"):
        return {
            "success": False,
            "error": "Kein OneProvider API-Key hinterlegt"
        }
    raw_key = one_key.strip()
    headers = {"Authorization": f"Bearer {raw_key}"}

    urls = [
        "https://dashboard.oneprovider.dev/v1/dashboard/balance",
        "https://api.oneprovider.dev/v1/dashboard/balance"
    ]

    resp_data = None
    last_err = "Verbindungsfehler"
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=6.0)
            if r.status_code == 200:
                resp_data = r.json()
                break
            else:
                last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)

    if resp_data is None:
        return {
            "success": False,
            "error": f"Konnte Kontostand nicht abfragen ({last_err})"
        }

    # Flexible Extraktion von balance_usd, balance, credits, etc.
    target_obj = resp_data
    if isinstance(resp_data, dict) and "data" in resp_data and isinstance(resp_data["data"], dict):
        target_obj = resp_data["data"]

    balance_val = None
    matched_field = ""
    for f_name in ["balance_usd", "balance", "credits", "remaining", "amount", "credit", "total_balance", "available_balance"]:
        if isinstance(target_obj, dict) and f_name in target_obj and target_obj[f_name] is not None:
            try:
                balance_val = float(target_obj[f_name])
                matched_field = f_name
                break
            except (ValueError, TypeError):
                pass

    if balance_val is not None:
        currency = "USD"
        if "eur" in matched_field.lower():
            currency = "EUR"
        elif isinstance(target_obj, dict) and "currency" in target_obj:
            currency = str(target_obj.get("currency") or "USD").upper()
        curr_symbol = "€" if currency in ["EUR", "€"] else "$"
        curr_code = "EUR" if currency in ["EUR", "€"] else "USD"
        formatted = f"{balance_val:.2f} $" if curr_symbol == "$" else f"{balance_val:.2f} €"

        return {
            "success": True,
            "balance": round(balance_val, 2),
            "currency": curr_code,
            "formatted": formatted,
            "is_active": target_obj.get("is_active", True) if isinstance(target_obj, dict) else True,
            "expires_at": target_obj.get("expires_at") if isinstance(target_obj, dict) else None,
            "last_synced_at": target_obj.get("last_synced_at") if isinstance(target_obj, dict) else None
        }

    return {
        "success": False,
        "error": "Kontostand-Feld in API-Antwort nicht gefunden",
        "raw": resp_data
    }


@router.delete("/gemini-key")
def remove_gemini_key(provider: Optional[str] = None):
    """
    Löscht den konfigurierten OneProvider-, Gemini- oder beide API-Keys.
    """
    prov = (provider or "").strip().lower()
    one_key, gem_key, active_prov = get_stored_keys()

    if prov == "oneprovider":
        delete_system_setting("oneprovider_api_key")
        os.environ.pop("ONEPROVIDER_API_KEY", None)
        if active_prov == "oneprovider":
            if gem_key:
                set_system_setting("active_ai_provider", "gemini")
                set_system_setting("gemini_api_key", gem_key)
                os.environ["GEMINI_API_KEY"] = gem_key
            else:
                delete_system_setting("gemini_api_key")
                os.environ.pop("GEMINI_API_KEY", None)
        return {"success": True, "message": "OneProvider API-Key entfernt.", "has_key": bool(gem_key)}

    if prov == "gemini":
        delete_system_setting("google_gemini_api_key")
        os.environ.pop("GEMINI_API_KEY", None)
        if active_prov == "gemini":
            if one_key:
                set_system_setting("active_ai_provider", "oneprovider")
                set_system_setting("gemini_api_key", one_key)
                os.environ["ONEPROVIDER_API_KEY"] = one_key
                os.environ["GEMINI_API_KEY"] = one_key
            else:
                delete_system_setting("gemini_api_key")
        return {"success": True, "message": "Google Gemini API-Key entfernt.", "has_key": bool(one_key)}

    # Beide löschen
    delete_system_setting("oneprovider_api_key")
    delete_system_setting("google_gemini_api_key")
    delete_system_setting("gemini_api_key")
    os.environ.pop("ONEPROVIDER_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)
    return {
        "success": True,
        "message": "Alle KI API-Keys erfolgreich entfernt.",
        "has_key": False
    }
