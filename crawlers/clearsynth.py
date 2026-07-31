"""Clearsynth connector for public product availability research."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://clearsynth.com"
SEARCH_URL = f"{BASE_URL}/search1"
DEFAULT_TIMEOUT = 15
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ClearsynthParsingError(RuntimeError):
    """Raised when Clearsynth returns a page that cannot be parsed reliably."""


@dataclass
class ClearsynthCandidate:
    """A public product candidate returned by the Clearsynth search results page."""

    product_name: Optional[str]
    product_url: str
    catalogue_number: Optional[str] = None
    cas_number: Optional[str] = None
    molecular_formula: Optional[str] = None
    molecular_weight: Optional[str] = None
    availability_raw: Optional[str] = None


class ClearsynthConnector:
    """Public Clearsynth product search connector."""

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
        """Search Clearsynth for a CAS number and return public details when found."""

        query_cas = self._normalize_cas(cas_number)
        if not query_cas:
            return self._error_result(
                query_cas=query_cas,
                error_type="invalid_cas",
                message="CAS number is required.",
            )

        if not self._is_valid_cas(query_cas):
            return self._error_result(
                query_cas=query_cas,
                error_type="invalid_cas",
                message="CAS number must have a valid format and checksum.",
            )

        try:
            candidates = self._search_candidates(query_cas)
        except requests.Timeout as exc:
            return self._error_result(query_cas, "timeout", "Clearsynth request timed out.", exc)
        except requests.ConnectionError as exc:
            return self._error_result(query_cas, "connection_error", "Unable to connect to Clearsynth.", exc)
        except requests.HTTPError as exc:
            return self._error_result(query_cas, "http_error", "Clearsynth returned an HTTP error.", exc)
        except ClearsynthParsingError as exc:
            return self._error_result(query_cas, "parsing_failure", str(exc), exc)

        exact_candidate = self._select_exact_match(query_cas, candidates)
        if not exact_candidate:
            return self._not_found_result(query_cas)

        try:
            product_data = self._fetch_product_page(exact_candidate.product_url)
        except requests.Timeout as exc:
            return self._error_result(query_cas, "timeout", "Clearsynth request timed out.", exc)
        except requests.ConnectionError as exc:
            return self._error_result(query_cas, "connection_error", "Unable to connect to Clearsynth.", exc)
        except requests.HTTPError as exc:
            return self._error_result(query_cas, "http_error", "Clearsynth returned an HTTP error.", exc)
        except ClearsynthParsingError as exc:
            return self._error_result(query_cas, "parsing_failure", str(exc), exc)

        verified_cas = self._normalize_cas(product_data.get("cas_number"))
        if verified_cas != query_cas:
            return self._not_found_result(query_cas)

        availability_raw = self._first_nonempty(
            exact_candidate.availability_raw,
            product_data.get("availability_raw"),
        )

        return {
            "source": "Clearsynth",
            "query_cas": query_cas,
            "found": True,
            "exact_match": True,
            "product_name": self._first_nonempty(product_data.get("product_name"), exact_candidate.product_name),
            "product_url": product_data.get("product_url") or exact_candidate.product_url,
            "catalogue_number": self._first_nonempty(product_data.get("catalogue_number"), exact_candidate.catalogue_number),
            "cas_number": verified_cas,
            "molecular_formula": self._first_nonempty(product_data.get("molecular_formula"), exact_candidate.molecular_formula),
            "molecular_weight": self._first_nonempty(product_data.get("molecular_weight"), exact_candidate.molecular_weight),
            "availability": self._normalize_availability(availability_raw),
            "availability_raw": availability_raw,
            "shipping_condition": product_data.get("shipping_condition"),
            "country_of_origin": product_data.get("country_of_origin"),
            "smiles": product_data.get("smiles"),
        }

    def _search_candidates(self, cas_number: str) -> List[ClearsynthCandidate]:
        response = self._get(SEARCH_URL, params={"s": cas_number, "page": 1, "pagesize": 24})
        soup = BeautifulSoup(response.text, "html.parser")

        cards = soup.select("div.card.product-card-click")
        if not cards:
            if "No products found" in self._clean_text(soup.get_text(" ", strip=True)):
                return []
            raise ClearsynthParsingError("Clearsynth search results did not contain any product cards.")

        candidates: List[ClearsynthCandidate] = []
        seen_urls = set()

        for card in cards:
            product_link = card.select_one("h2.product-title a[href*='/product/']") or card.select_one(
                "a[href*='/product/']"
            )
            if not product_link:
                continue

            product_url = urljoin(BASE_URL, product_link.get("href", ""))
            if not product_url or product_url in seen_urls:
                continue

            attributes = self._card_action_attributes(card)
            product_name = self._clean_text(product_link.get_text(" ", strip=True)) or None
            catalogue_number = self._first_nonempty(
                attributes.get("data-catnumber"),
                self._spec_value(card, "CAT No."),
            )
            cas_value = self._normalize_cas(
                self._first_nonempty(
                    attributes.get("data-casnumber"),
                    self._spec_value(card, "CAS No."),
                )
            )
            molecular_formula = self._first_nonempty(
                self._compact_formula(attributes.get("data-molecularformula")),
                self._compact_formula(self._spec_value(card, "Formula")),
            )
            molecular_weight = self._first_nonempty(
                self._clean_text(attributes.get("data-molecularweight")),
                self._clean_text(self._spec_value(card, "Mol. Wt.")),
            )
            availability_raw = self._first_nonempty(
                self._clean_text(attributes.get("data-stockstatus")),
                self._card_stock_status(card),
            )

            seen_urls.add(product_url)
            candidates.append(
                ClearsynthCandidate(
                    product_name=product_name,
                    product_url=product_url,
                    catalogue_number=catalogue_number,
                    cas_number=self._strip_cas_suffix(cas_value),
                    molecular_formula=molecular_formula,
                    molecular_weight=molecular_weight,
                    availability_raw=availability_raw,
                )
            )

        return candidates

    def _fetch_product_page(self, product_url: str) -> Dict[str, Any]:
        response = self._get(product_url)
        soup = BeautifulSoup(response.text, "html.parser")

        fields = self._product_spec_fields(soup)
        product_name = self._first_nonempty(fields.get("product name"), self._first_text(soup, ["h1", "h2"]))
        cas_number = self._strip_cas_suffix(self._normalize_cas(fields.get("cas no.")))
        catalogue_number = self._first_nonempty(fields.get("cat no."), fields.get("product code"))
        molecular_formula = self._compact_formula(fields.get("molecular formula"))
        molecular_weight = self._clean_text(fields.get("molecular weight"))
        shipping_condition = self._first_nonempty(fields.get("storage condition"), None)
        country_of_origin = self._first_nonempty(fields.get("country of origin"), None)
        availability_raw = self._extract_stock_status(soup)

        if not product_name and not cas_number and not catalogue_number:
            raise ClearsynthParsingError("Unable to extract public product details from the Clearsynth product page.")

        return {
            "product_name": product_name,
            "product_url": product_url,
            "catalogue_number": catalogue_number,
            "cas_number": cas_number,
            "molecular_formula": molecular_formula,
            "molecular_weight": molecular_weight,
            "availability_raw": availability_raw,
            "shipping_condition": shipping_condition,
            "country_of_origin": country_of_origin,
            "smiles": None,
        }

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        time.sleep(self.delay_seconds)
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response

    @staticmethod
    def _card_action_attributes(card: Any) -> Dict[str, str]:
        button = card.select_one("button[data-producturl]")
        if not button:
            return {}
        return {key.lower(): value for key, value in button.attrs.items() if isinstance(value, str)}

    @staticmethod
    def _card_stock_status(card: Any) -> Optional[str]:
        for chip in card.select(".tag-row .chip"):
            text = ClearsynthConnector._clean_text(chip.get_text(" ", strip=True))
            if text and text.lower() != "best match":
                return text
        return None

    @staticmethod
    def _product_spec_fields(soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for row in soup.select(".cs-spec-table .cs-spec-row"):
            label = row.select_one(".cs-spec-left")
            value = row.select_one(".cs-spec-right")
            if not label or not value:
                continue
            key = ClearsynthConnector._clean_text(label.get_text(" ", strip=True)).lower().rstrip(":")
            fields[key] = ClearsynthConnector._clean_text(value.get_text(" ", strip=True))
        return fields

    @staticmethod
    def _extract_stock_status(soup: BeautifulSoup) -> Optional[str]:
        for node in soup.select("a.stock-enq.cs-stock-enquiry-link, .cs-stock-title a.stock-enq"):
            text = ClearsynthConnector._clean_text(node.get_text(" ", strip=True))
            if text:
                return text.replace("Stock Status:", "").strip() or text
        return None

    @staticmethod
    def _spec_value(card: Any, label: str) -> Optional[str]:
        normalized_label = ClearsynthConnector._clean_text(label).lower().rstrip(":")
        for spec in card.select("div.spec"):
            key = spec.select_one("b")
            value = spec.select_one("span")
            if not key or not value:
                continue
            key_text = ClearsynthConnector._clean_text(key.get_text(" ", strip=True)).lower().rstrip(":")
            if key_text == normalized_label:
                return ClearsynthConnector._clean_text(value.get_text(" ", strip=True))
        return None

    @staticmethod
    def _select_exact_match(cas_number: str, candidates: List[ClearsynthCandidate]) -> Optional[ClearsynthCandidate]:
        for candidate in candidates:
            if candidate.cas_number == cas_number:
                return candidate
        return None

    @staticmethod
    def _normalize_cas(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _strip_cas_suffix(value: str) -> str:
        match = re.match(r"^(\d{2,7}-\d{2}-\d)\b", value)
        if match:
            return match.group(1)
        return value

    @staticmethod
    def _compact_formula(value: Any) -> Optional[str]:
        cleaned = ClearsynthConnector._clean_text(value or "")
        if not cleaned or cleaned.upper() == "NA":
            return None
        return cleaned.replace(" ", "")

    @staticmethod
    def _normalize_availability(value: Optional[str]) -> str:
        text = " ".join(str(value or "").split()).lower()
        if not text:
            return "UNKNOWN"
        if "in stock" in text:
            return "IN_STOCK"
        if "custom synthesis" in text:
            return "SYNTHESIS_ON_DEMAND"
        if "out of stock" in text:
            return "OUT_OF_STOCK"
        return "UNKNOWN"

    @staticmethod
    def _first_text(soup: BeautifulSoup, selectors: List[str]) -> Optional[str]:
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = ClearsynthConnector._clean_text(node.get_text(" ", strip=True))
                if text:
                    return text
        return None

    @staticmethod
    def _first_nonempty(*values: Optional[str]) -> Optional[str]:
        for value in values:
            text = ClearsynthConnector._clean_text(value or "")
            if text and text.upper() != "NA":
                return text
        return None

    @staticmethod
    def _clean_text(value: Any) -> str:
        return " ".join(str(value or "").split())

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
    def _not_found_result(cas_number: str) -> Dict[str, Any]:
        return {
            "source": "Clearsynth",
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
    def _error_result(
        query_cas: str,
        error_type: str,
        message: str,
        exception: Optional[Exception] = None,
    ) -> Dict[str, Any]:
        error: Dict[str, Any] = {
            "type": error_type,
            "message": message,
        }
        if isinstance(exception, requests.HTTPError) and exception.response is not None:
            error["status_code"] = exception.response.status_code
        return {
            "source": "Clearsynth",
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


def search_clearsynth(cas_number: str) -> Dict[str, Any]:
    """Convenience wrapper for the Clearsynth connector."""

    return ClearsynthConnector().search(cas_number)


__all__ = ["ClearsynthConnector", "search_clearsynth"]