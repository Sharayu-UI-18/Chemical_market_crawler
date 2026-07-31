"""Aggregation helpers for combining connector results."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from crawlers.anantlabs import search_anantlabs
from crawlers.clearsynth import search_clearsynth
from crawlers.simsonpharma import search_simsonpharma
from crawlers.synzeal import search_synzeal
from crawlers.axios import search_axios



SUPPORTED_SOURCES = {
    "synzeal": search_synzeal,
    "anantlabs": search_anantlabs,
    "clearsynth": search_clearsynth,
    "simsonpharma": search_simsonpharma,
    "axios": search_axios
}


def check_market_availability(cas_number: str) -> Dict[str, Any]:
    """Check supported sources for a CAS number and aggregate the results."""

    query_cas = _normalize_cas(cas_number)
    results: Dict[str, Dict[str, Any]] = {}

    for source_name, search_fn in SUPPORTED_SOURCES.items():
        results[source_name] = _safe_search(source_name, search_fn, query_cas)

    sources_checked = list(SUPPORTED_SOURCES.keys())
    sources_found = [source for source, result in results.items() if bool(result.get("found"))]
    source_presence_ratio = len(sources_found) / len(sources_checked) if sources_checked else 0.0

    in_stock_count = 0
    synthesis_on_demand_count = 0
    out_of_stock_count = 0
    unknown_status_count = 0

    for result in results.values():
        status = _normalize_availability(result.get("availability"))
        if status == "IN_STOCK":
            in_stock_count += 1
        elif status == "SYNTHESIS_ON_DEMAND":
            synthesis_on_demand_count += 1
        elif status == "OUT_OF_STOCK":
            out_of_stock_count += 1
        else:
            unknown_status_count += 1

    availability_score = calculate_availability_score(
        sources_checked=len(sources_checked),
        sources_found=len(sources_found),
        in_stock_count=in_stock_count,
    )

    return {
        "query_cas": query_cas,
        "sources_checked": sources_checked,
        "sources_found": sources_found,
        "source_presence_ratio": source_presence_ratio,
        "in_stock_count": in_stock_count,
        "synthesis_on_demand_count": synthesis_on_demand_count,
        "out_of_stock_count": out_of_stock_count,
        "unknown_status_count": unknown_status_count,
        "availability_score": availability_score,
        "availability_label": score_to_label(availability_score),
        "results": results,
    }


def calculate_availability_score(*, sources_checked: int, sources_found: int, in_stock_count: int) -> int:
    """Compute the prototype availability score on a 0-100 scale."""

    if sources_checked <= 0:
        return 0

    source_presence_ratio = sources_found / sources_checked
    in_stock_ratio = in_stock_count / sources_checked
    score = 100 * ((0.5 * source_presence_ratio) + (0.5 * in_stock_ratio))
    return max(0, min(100, round(score)))


def score_to_label(score: int) -> str:
    """Convert the prototype score into a simple qualitative label."""

    if score <= 30:
        return "LOW"
    if score <= 70:
        return "MODERATE"
    return "HIGH"


def _safe_search(source_name: str, search_fn: Callable[[str], Dict[str, Any]], cas_number: str) -> Dict[str, Any]:
    try:
        result = search_fn(cas_number)
        if isinstance(result, dict):
            return result
        return _structured_error(source_name, cas_number, "unexpected_result", "Connector returned a non-dictionary result.")
    except Exception as exc:  # pragma: no cover - defensive boundary for connector failures
        return _structured_error(source_name, cas_number, "connector_error", str(exc))


def _structured_error(source_name: str, cas_number: str, error_type: str, message: str) -> Dict[str, Any]:
    return {
        "source": source_name,
        "query_cas": cas_number,
        "found": False,
        "exact_match": False,
        "product_name": None,
        "product_url": None,
        "catalogue_number": None,
        "cas_number": None,
        "molecular_formula": None,
        "molecular_weight": None,
        "availability": None,
        "availability_raw": None,
        "shipping_condition": None,
        "country_of_origin": None,
        "smiles": None,
        "error": {
            "type": error_type,
            "message": message,
        },
    }


def _normalize_cas(value: str) -> str:
    return str(value or "").strip()


def _normalize_availability(value: Optional[str]) -> str:
    text = " ".join(str(value or "").split()).upper()
    if not text:
        return "UNKNOWN"
    if text == "IN_STOCK":
        return "IN_STOCK"
    if text == "SYNTHESIS_ON_DEMAND":
        return "SYNTHESIS_ON_DEMAND"
    if text == "OUT_OF_STOCK":
        return "OUT_OF_STOCK"
    return "UNKNOWN"


__all__ = [
    "check_market_availability",
    "calculate_availability_score",
    "score_to_label",
]