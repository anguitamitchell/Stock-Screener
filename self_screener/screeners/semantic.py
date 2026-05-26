import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from self_screener.models import Holding, ScreenResult
from self_screener.providers.company_research import fetch_company_research


THEME_CONFIG_PATH = Path("config") / "semantic_themes.json"
RELIABLE_RESEARCH_THRESHOLD = 0.65

FALLBACK_RULES = {
    "millennial_spending_power": [
        "AIRBNB", "AMERICAN EXPRESS", "BOOKING", "CHIPOTLE", "COSTCO", "DARDEN",
        "DOMINO", "DOORDASH", "EBAY", "ETSY", "EXPEDIA", "HOME DEPOT", "LAS VEGAS",
        "LOWE", "LULULEMON", "MARRIOTT", "MCDONALD", "NETFLIX", "NIKE", "PAYPAL",
        "RESTAURANT", "ROSS", "STARBUCKS", "TARGET", "TJX", "ULTA", "WALMART",
        "WARNER", "WYNN", "YUM",
    ],
    "labor_supply_advantage": [
        "ACCENTURE", "AUTOMATIC DATA", "CINTAS", "COPART", "EQUIFAX", "FEDEX",
        "FISERV", "FORTIVE", "GARTNER", "HONEYWELL", "IDEX", "INTUIT", "PAYCHEX",
        "PAYCOM", "ROBERT HALF", "ROCKWELL", "ROLLINS", "S&P GLOBAL", "SERVICENOW",
        "SNAP-ON", "TRANSDIGM", "UBER", "UNITED PARCEL", "VERISK", "WASTE MANAGEMENT",
    ],
    "enterprise_ai_leadership": [
        "ADOBE", "ANALOG DEVICES", "ANSYS", "APPLIED MATERIALS", "ARISTA",
        "AUTODESK", "CADENCE", "CLOUDFLARE", "DATADOG", "ELECTRONIC ARTS",
        "FORTINET", "GARTNER", "GEN DIGITAL", "HP INC", "IBM", "KLA", "LAM RESEARCH",
        "MICROCHIP", "MONOLITHIC", "MOTOROLA", "NETAPP", "ON SEMICONDUCTOR",
        "ORACLE", "PALO ALTO", "PTC", "QUALCOMM", "SALESFORCE", "SEAGATE",
        "SYNOPSYS", "TAKE-TWO", "TERADYNE", "TEXAS INSTRUMENTS", "TYLER",
        "WESTERN DIGITAL", "WORKDAY", "ZEBRA",
    ],
    "grid_data_center_demand": [
        "AES", "AIR PRODUCTS", "AKAMAI", "ALBEMARLE", "AMPHENOL", "AON",
        "ATMOS", "BALL", "CARRIER", "CENTERPOINT", "CHENIERE", "CONSOLIDATED EDISON",
        "CORNING", "CROWN CASTLE", "DIGITAL REALTY", "DOMINION", "DOVER", "DTE",
        "EATON", "EDISON", "EMERSON", "ENTERGY", "EQUINIX", "EXELON", "FASTENAL",
        "FIRSTENERGY", "GE VERNOVA", "HUBBELL", "INGERSOLL", "JOHNSON CONTROLS",
        "KEYSIGHT", "L3HARRIS", "LEIDOS", "MARTIN MARIETTA", "NEXTERA", "NRG",
        "OTIS", "PACCAR", "PARKER", "PENTAIR", "PG&E", "PUBLIC SERVICE", "QUANTA",
        "REPUBLIC SERVICES", "ROPER", "SEMPRA", "SHERWIN", "STEEL DYNAMICS",
        "TRANE", "TYLER", "UNITED RENTALS", "VULCAN", "WEC", "XYLEM",
    ],
    "energy_security_tailwinds": [
        "APA", "BAKER HUGHES", "CHEVRON", "CONOCOPHILLIPS", "COTERRA", "DEVON",
        "DIAMONDBACK", "EOG", "EQT", "HALLIBURTON", "HESS", "KINDER MORGAN",
        "MARATHON PETROLEUM", "OCCIDENTAL", "ONEOK", "PHILLIPS 66", "SCHLUMBERGER",
        "TARGA", "VALERO", "WILLIAMS",
    ],
}

RESEARCH_TERMS = {
    "millennial_spending_power": [
        "app", "consumer", "convenience", "digital", "direct-to-consumer", "e-commerce",
        "entertainment", "fitness", "food", "gaming", "hotel", "lifestyle", "loyalty",
        "mobile", "online", "payment", "premium", "restaurant", "retail", "streaming",
        "subscription", "travel", "wellness",
    ],
    "labor_supply_advantage": [
        "artificial intelligence", "automation", "automated", "efficiency", "logistics",
        "machine learning", "managed services", "outsourcing", "productivity", "robotic",
        "software", "staffing", "supply chain", "workflow",
    ],
    "enterprise_ai_leadership": [
        "ai", "analytics", "artificial intelligence", "chip", "cloud", "cybersecurity",
        "data", "database", "developer", "enterprise", "gpu", "infrastructure",
        "machine learning", "model", "networking", "platform", "semiconductor",
        "server", "software",
    ],
    "grid_data_center_demand": [
        "capacity", "data center", "electric", "electrical", "electricity", "equipment",
        "grid", "industrial", "infrastructure", "power", "renewable", "thermal",
        "transmission", "utility",
    ],
    "energy_security_tailwinds": [
        "crude", "drilling", "exploration", "gas", "geopolitical", "lng",
        "natural gas", "oil", "pipeline", "production", "refining", "reserves",
        "shale", "upstream",
    ],
}

STRONG_RESEARCH_TERMS = {
    "millennial_spending_power": {
        "e-commerce", "online", "premium", "retail", "streaming", "subscription", "travel", "wellness",
    },
    "labor_supply_advantage": {
        "artificial intelligence", "automation", "machine learning", "productivity", "robotic", "workflow",
    },
    "enterprise_ai_leadership": {
        "ai", "artificial intelligence", "cloud", "cybersecurity", "enterprise", "gpu",
        "machine learning", "semiconductor", "software",
    },
    "grid_data_center_demand": {
        "data center", "electric", "electrical", "grid", "infrastructure", "power", "transmission", "utility",
    },
    "energy_security_tailwinds": {
        "crude", "drilling", "lng", "natural gas", "oil", "pipeline", "production", "refining",
    },
}

THEME_DISQUALIFIERS = {
    "enterprise_ai_leadership": [
        "cloudy",
    ],
    "grid_data_center_demand": [
        "power drink",
        "powerade",
        "power sports",
    ],
    "energy_security_tailwinds": [
        "energy drink",
        "energy drinks",
        "beverage",
        "beverages",
        "non-alcoholic",
    ],
}

THEME_REQUIRED_TERMS = {
    "enterprise_ai_leadership": {
        "ai",
        "artificial intelligence",
        "cloud",
        "cybersecurity",
        "enterprise",
        "gpu",
        "machine learning",
        "semiconductor",
        "software",
    },
    "grid_data_center_demand": {
        "data center",
        "electric",
        "electrical",
        "electricity",
        "grid",
        "infrastructure",
        "power",
        "transmission",
        "utility",
    },
    "energy_security_tailwinds": {
        "crude",
        "drilling",
        "exploration",
        "gas",
        "lng",
        "natural gas",
        "oil",
        "pipeline",
        "production",
        "refining",
        "reserves",
        "shale",
        "upstream",
    },
}

STRENGTH_MULTIPLIERS = {
    "strong": 1.0,
    "moderate": 0.72,
    "weak": 0.42,
}

EXPOSURE_MULTIPLIERS = {
    "direct": 1.0,
    "indirect": 0.72,
    "weak": 0.42,
}

DIRECT_EXPOSURE_TERMS = {
    "millennial_spending_power": {
        "e-commerce", "entertainment", "fitness", "gaming", "hotel", "lifestyle", "loyalty",
        "mobile", "online", "premium", "restaurant", "retail", "streaming", "subscription",
        "travel", "wellness",
    },
    "labor_supply_advantage": {
        "artificial intelligence", "automation", "automated", "machine learning", "productivity",
        "robotic", "staffing", "workflow",
    },
    "enterprise_ai_leadership": {
        "ai", "artificial intelligence", "cloud", "cybersecurity", "enterprise", "gpu",
        "machine learning", "semiconductor", "server", "software",
    },
    "grid_data_center_demand": {
        "data center", "electric", "electrical", "electricity", "grid", "power",
        "transmission", "utility",
    },
    "energy_security_tailwinds": {
        "crude", "drilling", "exploration", "gas", "lng", "natural gas", "oil",
        "pipeline", "production", "refining", "reserves", "shale", "upstream",
    },
}


def screen_holding(holding: Holding, refresh: bool = False) -> ScreenResult:
    config = _load_theme_config()
    company = config.get("companies", {}).get(holding.ticker)

    if not company:
        research_result = _research_screen(holding, config, refresh=refresh)
        if research_result:
            return research_result
        return _fallback_screen(holding, config)

    themes = config.get("themes", {})
    theme_exposures = _curated_theme_exposures(company.get("themes", []))
    matched_theme_keys = list(theme_exposures)
    score = float(
        company.get(
            "score",
            sum(
                float(themes.get(key, {}).get("weight", 0))
                * EXPOSURE_MULTIPLIERS.get(theme_exposures.get(key, "direct"), 1.0)
                for key in matched_theme_keys
            ),
        )
    )
    theme_labels = [
        f"{themes.get(key, {}).get('label', key)} ({theme_exposures.get(key, 'direct')})"
        for key in matched_theme_keys
    ]
    label = _label_for_score(score)

    return ScreenResult(
        score=round(score, 1),
        label=label,
        reasons=[
            f"Matched themes: {', '.join(theme_labels) if theme_labels else 'none'}.",
            company.get("notes", "No research note provided yet."),
        ],
        confidence=float(company.get("confidence", 1.0)),
        research_status=company.get("research_status", "curated"),
        source=company.get("source", "Local curated semantic config"),
        exposures=theme_exposures,
    )


def _load_theme_config() -> Dict:
    if not THEME_CONFIG_PATH.exists():
        return {"themes": {}, "companies": {}}
    return json.loads(THEME_CONFIG_PATH.read_text(encoding="utf-8"))


def _curated_theme_exposures(raw_themes) -> Dict[str, str]:
    if isinstance(raw_themes, dict):
        return {
            str(theme): _normalize_exposure(exposure)
            for theme, exposure in raw_themes.items()
        }
    return {str(theme): "direct" for theme in raw_themes or []}


def _normalize_exposure(value) -> str:
    normalized = str(value or "direct").lower()
    if normalized in EXPOSURE_MULTIPLIERS:
        return normalized
    return "direct"


def build_research_evidence(holding: Holding, refresh: bool = False) -> Dict:
    config = _load_theme_config()
    themes = config.get("themes", {})
    research = fetch_company_research(holding.ticker, holding.name, refresh=refresh)
    if research.error:
        return {
            "ticker": holding.ticker,
            "name": holding.name,
            "error": research.error,
            "source": research.source,
            "matches": [],
            "confidence": 0.0,
            "research_status": "unreliable",
        }

    matches = _research_theme_matches(holding, research)
    guardrails = _research_guardrails(holding, research)
    confidence = _research_confidence(research.summary, matches)
    return {
        "ticker": holding.ticker,
        "name": research.name or holding.name,
        "sector": research.sector,
        "industry": research.industry,
        "summary": _compact_summary(research.summary, limit=700),
        "source": research.source,
        "matches": [
            {
                "theme_key": theme_key,
                "theme_label": themes.get(theme_key, {}).get("label", theme_key),
                "terms": detail["terms"],
                "strength": detail["strength"],
                "exposure": detail["exposure"],
            }
            for theme_key, detail in matches.items()
        ],
        "guardrails": [
            {
                "theme_key": item["theme_key"],
                "theme_label": themes.get(item["theme_key"], {}).get("label", item["theme_key"]),
                "terms": item["terms"],
                "reason": item["reason"],
            }
            for item in guardrails
        ],
        "confidence": confidence,
        "research_status": "auto_approved" if confidence >= RELIABLE_RESEARCH_THRESHOLD else "unreliable",
    }


def _fallback_screen(holding: Holding, config: Dict) -> ScreenResult:
    themes = config.get("themes", {})
    matched_theme_keys = _fallback_themes(holding)
    if not matched_theme_keys:
        return ScreenResult(
            score=15.0,
            label="research not reliable",
            reasons=[
                "No curated mapping or fallback theme match yet.",
                f"Auto-research could not confidently map {holding.ticker} to your themes.",
            ],
            confidence=0.15,
            research_status="unreliable",
            source="Local auto-research fallback",
        )

    raw_score = sum(float(themes.get(key, {}).get("weight", 0)) for key in matched_theme_keys)
    score = min(70.0, raw_score + 12.0)
    theme_labels = [themes.get(key, {}).get("label", key) for key in matched_theme_keys]
    confidence = _fallback_confidence(matched_theme_keys)
    is_reliable = confidence >= RELIABLE_RESEARCH_THRESHOLD

    return ScreenResult(
        score=round(score, 1),
        label="auto-researched" if is_reliable else "research not reliable",
        reasons=[
            f"Fallback themes: {', '.join(theme_labels)}.",
            (
                "Auto-approved from local keyword evidence; review when convenient."
                if is_reliable
                else "Research is not reliable enough yet; review before trusting the semantic score."
            ),
        ],
        confidence=confidence,
        research_status="auto_approved" if is_reliable else "unreliable",
        source="Local auto-research fallback",
    )


def _research_screen(holding: Holding, config: Dict, refresh: bool = False) -> Optional[ScreenResult]:
    research = fetch_company_research(holding.ticker, holding.name, refresh=refresh)
    if research.error:
        return None

    matches = _research_theme_matches(holding, research)
    if not matches:
        summary = _compact_summary(research.summary)
        return ScreenResult(
            score=18.0,
            label="research not reliable",
            reasons=[
                "No research theme match found.",
                (
                    f"Profile pull: sector={research.sector or 'unknown'}; "
                    f"industry={research.industry or 'unknown'}; {summary}"
                ),
            ],
            confidence=0.25,
            research_status="unreliable",
            source=research.source,
        )

    themes = config.get("themes", {})
    matched_theme_keys = list(matches)
    raw_score = _weighted_research_score(matches, themes)
    evidence_points = _evidence_points(matches)
    score = min(88.0, raw_score + min(12.0, evidence_points * 1.8))
    confidence = _research_confidence(research.summary, matches)
    is_reliable = confidence >= RELIABLE_RESEARCH_THRESHOLD
    theme_labels = [themes.get(key, {}).get("label", key) for key in matched_theme_keys]
    evidence = _format_evidence(matches, themes)
    summary = _compact_summary(research.summary)

    return ScreenResult(
        score=round(score, 1),
        label="auto-researched" if is_reliable else "research not reliable",
        reasons=[
            f"Matched themes: {', '.join(theme_labels)}.",
            (
                f"Profile pull: sector={research.sector or 'unknown'}; "
                f"industry={research.industry or 'unknown'}; evidence: {evidence}. {summary}"
            ),
        ],
        confidence=confidence,
        research_status="auto_approved" if is_reliable else "unreliable",
        source=research.source,
    )


def _research_theme_matches(holding: Holding, research) -> Dict[str, Dict]:
    text = _research_text(holding, research)
    matches = {}
    for theme_key, terms in RESEARCH_TERMS.items():
        found_terms = [term for term in terms if _term_matches(term, text)]
        if found_terms and _theme_context_allowed(theme_key, text, found_terms):
            matches[theme_key] = {
                "terms": found_terms[:6],
                "strength": _theme_strength(theme_key, found_terms),
                "exposure": _theme_exposure(theme_key, found_terms),
            }
    return matches


def _research_guardrails(holding: Holding, research) -> List[Dict]:
    text = _research_text(holding, research)
    blocked = []
    for theme_key, terms in RESEARCH_TERMS.items():
        disqualifiers = [term for term in THEME_DISQUALIFIERS.get(theme_key, []) if term in text]
        found_terms = [term for term in terms if _term_matches(term, text)]
        if not found_terms and not disqualifiers:
            continue
        reason = _theme_block_reason(theme_key, text, found_terms)
        if not reason and disqualifiers:
            reason = f"Blocked by context: {', '.join(disqualifiers[:3])}."
        if reason:
            blocked.append(
                {
                    "theme_key": theme_key,
                    "terms": (found_terms or disqualifiers)[:6],
                    "reason": reason,
                }
            )
    return blocked


def _research_text(holding: Holding, research) -> str:
    return " ".join(
        [
            holding.ticker,
            holding.name,
            research.name,
            research.sector,
            research.industry,
            research.summary,
        ]
    ).lower()


def _term_matches(term: str, text: str) -> bool:
    if term.replace(" ", "").isalpha() and " " not in term:
        return re.search(rf"\b{re.escape(term)}\b", text) is not None
    return term in text


def _theme_context_allowed(theme_key: str, text: str, found_terms: List[str]) -> bool:
    return _theme_block_reason(theme_key, text, found_terms) == ""


def _theme_block_reason(theme_key: str, text: str, found_terms: List[str]) -> str:
    disqualifiers = THEME_DISQUALIFIERS.get(theme_key, [])
    triggered = [disqualifier for disqualifier in disqualifiers if disqualifier in text]
    if triggered:
        return f"Blocked by context: {', '.join(triggered[:3])}."
    required_terms = THEME_REQUIRED_TERMS.get(theme_key)
    if required_terms and not any(term in required_terms for term in found_terms):
        return "Blocked because only broad/ambiguous terms matched, without required theme context."
    return ""


def _theme_strength(theme_key: str, terms: List[str]) -> str:
    strong_terms = STRONG_RESEARCH_TERMS.get(theme_key, set())
    strong_hits = sum(1 for term in terms if term in strong_terms)
    if strong_hits >= 2 or len(terms) >= 5:
        return "strong"
    if strong_hits == 1 or len(terms) >= 3:
        return "moderate"
    return "weak"


def _theme_exposure(theme_key: str, terms: List[str]) -> str:
    direct_terms = DIRECT_EXPOSURE_TERMS.get(theme_key, set())
    direct_hits = sum(1 for term in terms if term in direct_terms)
    if direct_hits >= 2 or (direct_hits >= 1 and len(terms) >= 3):
        return "direct"
    if direct_hits >= 1:
        return "indirect"
    return "weak"


def _weighted_research_score(matches: Dict[str, Dict], themes: Dict) -> float:
    score = 0.0
    for theme_key, detail in matches.items():
        theme_weight = float(themes.get(theme_key, {}).get("weight", 0))
        strength_multiplier = STRENGTH_MULTIPLIERS.get(detail["strength"], 0.42)
        exposure_multiplier = EXPOSURE_MULTIPLIERS.get(detail.get("exposure", "weak"), 0.42)
        score += theme_weight * min(strength_multiplier, exposure_multiplier)
    return score


def _evidence_points(matches: Dict[str, Dict]) -> float:
    strength_values = {"strong": 3.0, "moderate": 2.0, "weak": 0.75}
    exposure_values = {"direct": 3.0, "indirect": 1.8, "weak": 0.75}
    return sum(
        min(
            strength_values.get(detail["strength"], 0.75),
            exposure_values.get(detail.get("exposure", "weak"), 0.75),
        )
        for detail in matches.values()
    )


def _research_confidence(summary: str, matches: Dict[str, Dict]) -> float:
    confidence = 0.35
    if summary and len(summary) >= 180:
        confidence += 0.18
    strong_count = sum(1 for detail in matches.values() if detail["strength"] == "strong")
    moderate_count = sum(1 for detail in matches.values() if detail["strength"] == "moderate")
    weak_count = sum(1 for detail in matches.values() if detail["strength"] == "weak")
    direct_count = sum(1 for detail in matches.values() if detail.get("exposure") == "direct")
    indirect_count = sum(1 for detail in matches.values() if detail.get("exposure") == "indirect")
    if strong_count >= 1:
        confidence += 0.18
    elif moderate_count >= 1:
        confidence += 0.10
    if len(matches) >= 2:
        confidence += 0.18
    if direct_count >= 1:
        confidence += 0.08
    elif indirect_count >= 1:
        confidence += 0.03
    evidence_count = sum(len(detail["terms"]) for detail in matches.values())
    if evidence_count >= 6:
        confidence += 0.16
    elif evidence_count >= 3:
        confidence += 0.09
    if weak_count and not strong_count and moderate_count <= 1:
        confidence -= 0.08
    return round(min(confidence, 0.92), 2)


def _format_evidence(matches: Dict[str, List[str]], themes: Dict) -> str:
    parts = []
    for theme_key, detail in matches.items():
        label = themes.get(theme_key, {}).get("label", theme_key)
        parts.append(f"{label}: {detail['exposure']} / {detail['strength']} ({', '.join(detail['terms'][:3])})")
    return "; ".join(parts)


def _compact_summary(summary: str, limit: int = 280) -> str:
    cleaned = " ".join((summary or "").split())
    if not cleaned:
        return "No business summary was returned."
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0] + "..."


def _fallback_themes(holding: Holding) -> List[str]:
    text = f"{holding.ticker} {holding.name}".upper()
    matches = []
    for theme_key, keywords in FALLBACK_RULES.items():
        if any(keyword in text for keyword in keywords):
            matches.append(theme_key)
    return matches


def _fallback_confidence(theme_keys: List[str]) -> float:
    if len(theme_keys) >= 2:
        return 0.72
    if len(theme_keys) == 1:
        return 0.52
    return 0.15


def _label_for_score(score: float) -> str:
    if score >= 75:
        return "narrative leader"
    if score >= 55:
        return "aligned"
    if score >= 30:
        return "partial fit"
    return "low fit"
