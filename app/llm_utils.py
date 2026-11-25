"""
Shared LLM utilities for ImmoMetrica:
- Robust JSON response parser across Gemini, OneProvider and OpenAI models
- Unified API key resolution honoring active provider settings and env fallbacks
"""

import json
import os
import re
from typing import Any, Dict, Optional

from app.database import get_system_setting


def _looks_masked_or_empty(val: Optional[str]) -> bool:
    if not val:
        return True
    s = val.strip()
    return not s or "..." in s or "***" in s or len(s) < 15


def resolve_primary_llm_key(explicit_api_key: Optional[str] = None) -> Optional[str]:
    """
    Resolves the active LLM API key (OneProvider or Google Gemini) from explicit
    override, SQLite system_settings, or environment variables.
    """
    if not _looks_masked_or_empty(explicit_api_key):
        return explicit_api_key.strip()  # type: ignore[union-attr]

    try:
        active_prov = get_system_setting("active_ai_provider")
        if active_prov == "gemini":
            key = (
                get_system_setting("google_gemini_api_key")
                or get_system_setting("gemini_api_key")
                or os.getenv("GEMINI_API_KEY")
            )
            if key and key.strip():
                return key.strip()
        elif active_prov == "oneprovider":
            key = (
                get_system_setting("oneprovider_api_key")
                or get_system_setting("gemini_api_key")
                or os.getenv("ONEPROVIDER_API_KEY")
                or os.getenv("GEMINI_API_KEY")
            )
            if key and key.strip():
                return key.strip()
        else:
            for s_key in ("gemini_api_key", "oneprovider_api_key", "google_gemini_api_key"):
                db_key = get_system_setting(s_key)
                if db_key and db_key.strip():
                    return db_key.strip()
    except Exception:
        pass

    for env_name in ("ONEPROVIDER_API_KEY", "GEMINI_API_KEY"):
        env_key = os.getenv(env_name)
        if env_key and env_key.strip():
            return env_key.strip()
    return None


def clean_and_parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """
    Robuster JSON-Parser für LLM-Ausgaben (Google Gemini, OpenAI, OneProvider/Claude/Qwen).
    Bereinigt Markdown-Codeblöcke (```json ... ```), führende/nachfolgende Freitexte,
    trailing commas und Token-Truncations am Textende.
    """
    cleaned = raw_text.strip()
    if "```" in cleaned:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if m:
            cleaned = m.group(1).strip()
        else:
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    cleaned_no_trailing = re.sub(r",\s*([\]}])", r"\1", cleaned)
    try:
        return json.loads(cleaned_no_trailing)
    except Exception:
        pass

    for base in (cleaned, cleaned_no_trailing):
        for suffix in ('"}', '"]}', '"}}', '"}]}', "}\n}", '"]}\n}', "}", '"]'):
            try:
                return json.loads(base + suffix)
            except Exception:
                pass

    m_outer = re.search(r"(\{[\s\S]*\})", cleaned)
    if m_outer:
        cand = m_outer.group(1).strip()
        try:
            return json.loads(cand)
        except Exception:
            cand_no_trailing = re.sub(r",\s*([\]}])", r"\1", cand)
            try:
                return json.loads(cand_no_trailing)
            except Exception:
                for suffix in ('"}', '"]}', '"}}', "}\n}"):
                    try:
                        return json.loads(cand_no_trailing + suffix)
                    except Exception:
                        pass

    raise ValueError("Kein valides JSON in LLM-Antwort gefunden")
