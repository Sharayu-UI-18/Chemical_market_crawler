"""Simson Pharma connector for public product availability research."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.simsonpharma.com"
AUTOCOMPLETE_URL = f"{BASE_URL}/autocompletesearch/ajaxsearch"
DEFAULT_TIMEOUT = 15
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class SimsonPharmaParsingError(RuntimeError):
    """Raised when Simson Pharma returns a page that cannot be parsed reliably."""


@dataclass
class SimsonPharmaCandidate:
    """A public product candidate returned by the Simson Pharma search page."""

    product_name: str
    product_url: str
    catalogue_number: Optional[str] = None
    cas_number: Optional[str] = None
    molecular_formula: Optional[str] = None
    molecular_weight: Optional[str] = None
    availability_raw: Optional[str] = None


class SimsonPharmaConnector:
    """Public Simson Pharma product search connector."""

    def __init__(
        self,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        session: Optional[requests.Session] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.timeout = timeout
        self.delay_seconds = delay_seconds
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
            }
        )

    def search(self, cas_number: str) -> Dict[str, Any]:
        """Search Simson Pharma for a CAS number and return public details when found."""

        cas_number = self._normalize_cas(cas_number)
        if not cas_number:
            return self._error_result(
                query_cas=cas_number,
                error_type="invalid_cas",
                message="CAS number is required.",
            )

        if not self._is_valid_cas(cas_number):
            return self._error_result(
                query_cas=cas_number,
                error_type="invalid_cas",
                message="CAS number must have a valid format and checksum.",
            )

        try:
            candidates = self._search_candidates(cas_number)
        except requests.Timeout as exc:
            return self._error_result(cas_number, "timeout", "Simson Pharma request timed out.", exc)
        except requests.ConnectionError as exc:
            return self._error_result(cas_number, "connection_error", "Unable to connect to Simson Pharma.", exc)
        except requests.HTTPError as exc:
            return self._error_result(cas_number, "http_error", "Simson Pharma returned an HTTP error.", exc)
        except SimsonPharmaParsingError as exc:
            return self._error_result(cas_number, "parsing_failure", str(exc), exc)

        exact_candidate = self._select_exact_match(cas_number, candidates)
        if not exact_candidate:
            return self._not_found_result(cas_number)

        try:
            product_data = self._fetch_product_page(exact_candidate.product_url)
        except requests.Timeout as exc:
            return self._error_result(cas_number, "timeout", "Simson Pharma request timed out.", exc)
        except requests.ConnectionError as exc:
            return self._error_result(cas_number, "connection_error", "Unable to connect to Simson Pharma.", exc)
        except requests.HTTPError as exc:
            return self._error_result(cas_number, "http_error", "Simson Pharma returned an HTTP error.", exc)
        except SimsonPharmaParsingError as exc:
            return self._error_result(cas_number, "parsing_failure", str(exc), exc)

        if self._strip_cas_suffix(self._normalize_cas(product_data.get("cas_number"))) != cas_number:
            return self._not_found_result(cas_number)

        return {
            "source": "Simson Pharma",
            "query_cas": cas_number,
            "found": True,
            "exact_match": True,
            "product_name": product_data.get("product_name"),
            "product_url": product_data.get("product_url"),
            "catalogue_number": product_data.get("catalogue_number"),
            "cas_number": product_data.get("cas_number"),
            "molecular_formula": product_data.get("molecular_formula"),
            "molecular_weight": product_data.get("molecular_weight"),
            "availability": self._normalize_availability(product_data.get("availability_raw")),
            "availability_raw": product_data.get("availability_raw"),
            "shipping_condition": product_data.get("shipping_condition"),
            "country_of_origin": product_data.get("country_of_origin"),
            "smiles": product_data.get("smiles"),
        }

    def _search_candidates(self, cas_number: str) -> List[SimsonPharmaCandidate]:
        response = self._get(AUTOCOMPLETE_URL, params={"q": cas_number})
        try:
            payload = response.json()
        except ValueError as exc:
            raise SimsonPharmaParsingError("Simson Pharma autocomplete did not return valid JSON.") from exc

        candidates: List[SimsonPharmaCandidate] = []
        seen_urls = set()

        for item in payload:
            product_slug = self._clean_text(item.get("value", ""))
            if not product_slug:
                continue

            product_url = urljoin(BASE_URL, f"/product/{product_slug}")
            if not product_url or product_url in seen_urls:
                continue

            product_name = self._clean_text(item.get("label", "")) or None
            cas_value = self._strip_cas_suffix(self._normalize_cas(item.get("casno", "")))

            seen_urls.add(product_url)
            candidates.append(
                SimsonPharmaCandidate(
                    product_name=product_name,
                    product_url=product_url,
                    cas_number=None if cas_value in {"", "NA"} else cas_value,
                )
            )

        return candidates

    def _fetch_product_page(self, product_url: str) -> Dict[str, Any]:
        response = self._get(product_url)
        soup = BeautifulSoup(response.text, "html.parser")

        title = self._first_text(soup, ["h1", "h2", ".product-title", ".product-name"]) or self._page_title(soup)
        table = self._find_summary_table(soup)

        catalogue_number = self._extract_table_value(table, ["CAT. No", "Cat. No", "CAT No", "Catalog No"])
        cas_number = self._extract_table_value(table, ["CAS. No", "CAS No", "CAS No.", "CAS"])
        molecular_formula = self._extract_table_value(table, ["Mol. F", "Mol. Formula", "Mol.F.", "Mol. F."])
        molecular_weight = self._extract_table_value(table, ["Mol. Wt", "Mol. Weight", "Mol.W.", "Mol. W."])
        availability_raw = self._extract_table_value(table, ["Stock Status", "Availability", "Stock"])

        if not title and not catalogue_number and not cas_number:
            raise SimsonPharmaParsingError("Unable to extract public product details from the Simson Pharma product page.")

        return {
            "product_name": title,
            "product_url": product_url,
            "catalogue_number": self._clean_none(catalogue_number),
            "cas_number": self._strip_cas_suffix(self._clean_none(cas_number) or ""),
            "molecular_formula": self._clean_none(molecular_formula),
            "molecular_weight": self._clean_none(molecular_weight),
            "availability_raw": self._clean_none(availability_raw),
            "shipping_condition": None,
            "country_of_origin": None,
            "smiles": None,
        }

    def _get(self, url: str, params: Optional[Dict[str, str]] = None) -> requests.Response:
        time.sleep(self.delay_seconds)
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response

    @staticmethod
    def _normalize_cas(value: str) -> str:
        return str(value or "").strip()

    @staticmethod
    def _strip_cas_suffix(value: str) -> str:
        match = re.match(r"^(\d{2,7}-\d{2}-\d)\b", value)
        if match:
            return match.group(1)
        return value

    @staticmethod
    def _is_valid_cas(value: str) -> bool:
        match = re.fullmatch(r"(\d{2,7})-(\d{2})-(\d)", value)
        if not match:
            return False
        digits = match.group(1) + match.group(2)
        checksum = 0
        for index, digit in enumerate(reversed(digits), start=1):
            checksum += index * int(digit)
        return checksum % 10 == int(match.group(3))

    @staticmethod
    def _normalize_availability(value: Optional[str]) -> str:
        text = " ".join(str(value or "").split()).lower()
        if not text:
            return "UNKNOWN"
        if "in stock" in text or "instock" in text:
            return "IN_STOCK"
        if "under synthesis" in text or "custom synthesis" in text:
            return "SYNTHESIS_ON_DEMAND"
        if "out of stock" in text:
            return "OUT_OF_STOCK"
        return "UNKNOWN"

    @staticmethod
    def _select_exact_match(cas_number: str, candidates: List[SimsonPharmaCandidate]) -> Optional[SimsonPharmaCandidate]:
        for candidate in candidates:
            if candidate.cas_number == cas_number:
                return candidate
        return None

    @staticmethod
    def _extract_card_product_name(card: Any) -> Optional[str]:
        for selector in ["h1", "h2", "h3", "h4", "a[href^='/product/']", "a[href^='https://www.simsonpharma.com/product/']"]:
            element = card.select_one(selector)
            if element:
                text = " ".join(element.get_text(" ", strip=True).split())
                if text and text.lower() not in {"view", "product view"}:
                    return text
        text = " ".join(card.get_text(" ", strip=True).split())
        for marker in ["Cat. No", "CAT. No", "Cas. No", "CAS. No", "Mol. F", "Mol. W", "Stock Status"]:
            index = text.find(marker)
            if index != -1:
                return text[:index].strip()
        return text or None

    @staticmethod
    def _find_summary_table(soup: BeautifulSoup) -> Any:
        for table in soup.find_all("table"):
            text = " ".join(table.get_text(" ", strip=True).split())
            if "CAS. No" in text and "Mol. F" in text:
                return table
        return soup

    @staticmethod
    def _extract_table_value(node: Any, labels: List[str]) -> Optional[str]:
        if node is None or not hasattr(node, "find_all"):
            return None

        normalized_labels = {
            SimsonPharmaConnector._clean_text(label).lower().rstrip(":").strip() for label in labels
        }
        for row in node.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            key = SimsonPharmaConnector._clean_text(cells[0].get_text(" ", strip=True)).lower().rstrip(":").strip()
            value = SimsonPharmaConnector._clean_text(cells[1].get_text(" ", strip=True))
            if key in normalized_labels and value:
                return value
        return None

    @staticmethod
    def _first_text(soup: BeautifulSoup, selectors: List[str]) -> Optional[str]:
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                text = " ".join(element.get_text(" ", strip=True).split())
                if text:
                    return text
        return None

    @staticmethod
    def _page_title(soup: BeautifulSoup) -> Optional[str]:
        if soup.title:
            text = " ".join(soup.title.get_text(" ", strip=True).split())
            if text:
                return text
        return None

    @staticmethod
    def _extract_pattern_value(text: str, pattern: str) -> Optional[str]:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        value = match.group(1).strip()
        return value or None

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(str(value or "").split())

    @staticmethod
    def _clean_none(value: Optional[str]) -> Optional[str]:
        cleaned = SimsonPharmaConnector._clean_text(value or "")
        if not cleaned or cleaned in {"—", "-"}:
            return None
        return cleaned

    @staticmethod
    def _not_found_result(cas_number: str) -> Dict[str, Any]:
        return {
            "source": "Simson Pharma",
            "query_cas": cas_number,
            "found": False,
            "exact_match": False,
            "product_name": None,
            "product_url": None,
            "catalogue_number": None,
            "cas_number": cas_number,
            "molecular_formula": None,
            "molecular_weight": None,
            "availability": "UNKNOWN",
            "availability_raw": None,
            "shipping_condition": None,
            "country_of_origin": None,
            "smiles": None,
        }

    @staticmethod
    def _error_result(query_cas: str, error_type: str, message: str, exception: Optional[Exception] = None) -> Dict[str, Any]:
        error: Dict[str, Any] = {
            "type": error_type,
            "message": message,
        }
        if isinstance(exception, requests.HTTPError) and exception.response is not None:
            error["status_code"] = exception.response.status_code
        return {
            "source": "Simson Pharma",
            "query_cas": query_cas,
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
            "error": error,
        }


def search_simsonpharma(cas_number: str) -> Dict[str, Any]:
    """Convenience wrapper for the Simson Pharma connector."""

    return SimsonPharmaConnector().search(cas_number)


__all__ = ["SimsonPharmaConnector", "search_simsonpharma"]
