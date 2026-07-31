"""Anant Labs connector for public product availability research."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString

BASE_URL = "https://www.anantlabs.com"
SEARCH_URL = f"{BASE_URL}/en/search"
DEFAULT_TIMEOUT = 15
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

STATUS_MAP = {
    "in stock": "IN_STOCK",
    "under synthesis": "SYNTHESIS_ON_DEMAND",
    "synthesis on demand": "SYNTHESIS_ON_DEMAND",
    "out of stock": "OUT_OF_STOCK",
}


class AnantLabsParsingError(RuntimeError):
    """Raised when Anant Labs returns a page that cannot be parsed reliably."""


@dataclass
class AnantLabsCandidate:
    """A public product candidate returned by the Anant Labs search page."""

    product_name: str
    product_url: str
    catalogue_number: Optional[str] = None
    availability_raw: Optional[str] = None


class AnantLabsConnector:
    """Public Anant Labs product search connector."""

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
        """Search Anant Labs for a CAS number and return public details when found."""

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
            return self._error_result(cas_number, "timeout", "Anant Labs request timed out.", exc)
        except requests.ConnectionError as exc:
            return self._error_result(cas_number, "connection_error", "Unable to connect to Anant Labs.", exc)
        except requests.HTTPError as exc:
            return self._error_result(cas_number, "http_error", "Anant Labs returned an HTTP error.", exc)

        if not candidates:
            return self._not_found_result(cas_number)

        first_fetch_error: Optional[Tuple[str, str, Optional[Exception]]] = None
        parsed_any = False

        for candidate in candidates:
            try:
                product_data = self._fetch_product_page(candidate.product_url)
                parsed_any = True
            except requests.Timeout as exc:
                first_fetch_error = first_fetch_error or ("timeout", "Anant Labs request timed out.", exc)
                continue
            except requests.ConnectionError as exc:
                first_fetch_error = first_fetch_error or ("connection_error", "Unable to connect to Anant Labs.", exc)
                continue
            except requests.HTTPError as exc:
                first_fetch_error = first_fetch_error or ("http_error", "Anant Labs returned an HTTP error.", exc)
                continue
            except AnantLabsParsingError as exc:
                first_fetch_error = first_fetch_error or ("parsing_failure", str(exc), exc)
                continue

            if self._normalize_cas(product_data.get("cas_number")) == cas_number:
                return {
                    "source": "Anant Labs",
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

        if not parsed_any and first_fetch_error is not None:
            error_type, message, exception = first_fetch_error
            return self._error_result(cas_number, error_type, message, exception)

        return self._not_found_result(cas_number)

    def _search_candidates(self, cas_number: str) -> List[AnantLabsCandidate]:
        response = self._get(SEARCH_URL, params={"q": cas_number})
        soup = BeautifulSoup(response.text, "html.parser")
        candidates: List[AnantLabsCandidate] = []
        seen_urls = set()

        for anchor in soup.select('a[href^="/en/molecule/"]'):
            card = anchor.find_parent(lambda tag: tag.name == "div" and tag.get("class") and "group/card" in tag.get("class", []))
            if card is None:
                continue
            href = anchor.get("href")
            if not href:
                continue
            product_url = urljoin(BASE_URL, href)
            if product_url in seen_urls:
                continue

            card_text = self._clean_text(card.get_text(" ", strip=True))
            product_name = self._extract_candidate_name(anchor.get_text(" ", strip=True), card_text)
            catalogue_number = self._extract_catalogue_number(card_text)
            availability_raw = self._extract_status_text(card)

            seen_urls.add(product_url)
            candidates.append(
                AnantLabsCandidate(
                    product_name=product_name,
                    product_url=product_url,
                    catalogue_number=catalogue_number,
                    availability_raw=availability_raw,
                )
            )

        return candidates

    def _fetch_product_page(self, product_url: str) -> Dict[str, Any]:
        response = self._get(product_url)
        soup = BeautifulSoup(response.text, "html.parser")

        title = self._first_text(soup, ["h1", "h2", ".product-title", ".product-name"])
        summary_card = self._find_card_by_hints(soup, ["Enquire Now", "Download MSDS"])
        technical_card = self._find_card_by_hints(soup, ["Technical Data"])

        availability_raw = self._extract_status_text(summary_card) or self._extract_status_text(soup)
        catalogue_number = self._extract_row_value(technical_card, ["Cat. No.", "Cat. No", "Catalogue No", "Catalog No", "Cat No"]) or self._extract_row_value(summary_card, ["Cat. No.", "Cat. No", "Catalogue No", "Catalog No", "Cat No"]) or self._extract_row_value(soup, ["Cat. No.", "Cat. No", "Catalogue No", "Catalog No", "Cat No"])
        cas_number = self._extract_row_value(technical_card, ["CAS", "CAS No", "CAS No."]) or self._extract_row_value(soup, ["CAS", "CAS No", "CAS No."])
        molecular_formula = self._extract_row_value(technical_card, ["Mol. Formula", "Molecular Formula", "Mol.F."]) or self._extract_row_value(soup, ["Mol. Formula", "Molecular Formula", "Mol.F."])
        molecular_weight = self._extract_row_value(technical_card, ["Mol. Weight", "Molecular Weight", "Mol.Wt."]) or self._extract_row_value(soup, ["Mol. Weight", "Molecular Weight", "Mol.Wt."])
        shipping_condition = self._extract_row_value(technical_card, ["Shipping Condition", "Shipping Temperature"]) or self._extract_row_value(soup, ["Shipping Condition", "Shipping Temperature"])
        country_of_origin = self._extract_row_value(technical_card, ["Country of Origin"]) or self._extract_row_value(soup, ["Country of Origin"])
        smiles = self._extract_row_value(technical_card, ["SMILES", "Smiles"]) or self._extract_row_value(soup, ["SMILES", "Smiles"])

        if not title and not catalogue_number and not cas_number:
            raise AnantLabsParsingError("Unable to extract public product details from the Anant Labs product page.")

        return {
            "product_name": title,
            "product_url": product_url,
            "catalogue_number": self._clean_none(catalogue_number),
            "cas_number": self._clean_none(cas_number),
            "molecular_formula": self._clean_none(molecular_formula),
            "molecular_weight": self._clean_none(molecular_weight),
            "availability_raw": self._clean_none(availability_raw),
            "shipping_condition": self._clean_none(shipping_condition),
            "country_of_origin": self._clean_none(country_of_origin),
            "smiles": self._clean_none(smiles),
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
        for needle, normalized in STATUS_MAP.items():
            if needle in text:
                return normalized
        return "UNKNOWN"

    @staticmethod
    def _find_card_by_hints(soup: BeautifulSoup, hints: List[str]) -> Any:
        if soup is None:
            return None
        for card in soup.find_all(lambda tag: tag.name == "div" and tag.get("class") and "group/card" in tag.get("class", [])):
            card_text = " ".join(card.get_text(" ", strip=True).split())
            if all(hint.lower() in card_text.lower() for hint in hints if hint):
                return card
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
    def _extract_candidate_name(anchor_text: str, card_text: str) -> str:
        text = " ".join((anchor_text or card_text or "").split())
        if not text:
            return ""
        for marker in [" In Stock", " Under Synthesis", " Synthesis on demand", " Out of Stock", " Cat. No.", " Cat No."]:
            index = text.find(marker)
            if index != -1:
                return text[:index].strip()
        return text

    @staticmethod
    def _extract_status_text(node: Any) -> Optional[str]:
        if node is None:
            return None
        known_statuses = ["In Stock", "Under Synthesis", "Synthesis on demand", "Out of Stock"]
        for text_node in node.find_all(string=True) if hasattr(node, "find_all") else []:
            text = " ".join(str(text_node).split())
            if text in known_statuses:
                return text
        return None

    @staticmethod
    def _extract_row_value(node: Any, labels: List[str]) -> Optional[str]:
        if node is None or not hasattr(node, "find_all"):
            return None

        normalized_labels = {AnantLabsConnector._clean_text(label).lower() for label in labels}

        for text_node in node.find_all(string=True):
            if not isinstance(text_node, NavigableString):
                continue
            label_text = AnantLabsConnector._clean_text(str(text_node))
            if label_text.lower() not in normalized_labels:
                continue

            row = text_node.parent
            if row is not None and getattr(row, "name", None) in {"span", "dt"} and row.parent is not None:
                row = row.parent
            if row is None:
                continue

            children = [child for child in row.find_all(recursive=False) if getattr(child, "name", None)]
            if len(children) >= 2:
                value = AnantLabsConnector._clean_text(children[1].get_text(" ", strip=True))
                value = value.strip().strip("—").strip()
                if value:
                    return value
                continue

            row_text = AnantLabsConnector._clean_text(row.get_text(" ", strip=True))
            if row_text.lower().startswith(label_text.lower()):
                value = row_text[len(label_text):].lstrip(": -").strip()
                value = AnantLabsConnector._truncate_at_known_label(value, labels)
                if value:
                    return value

        return None

    @staticmethod
    def _truncate_at_known_label(text: str, stop_labels: List[str]) -> str:
        if not text:
            return ""
        lower_text = text.lower()
        stop_index = len(text)
        for label in stop_labels:
            index = lower_text.find(label.lower())
            if index != -1 and index < stop_index:
                stop_index = index
        return text[:stop_index].strip(" :-")

    @staticmethod
    def _extract_catalogue_number(text: str) -> Optional[str]:
        match = re.search(r"\b(?:ANT[A-Z0-9-]+)\b", text)
        return match.group(0) if match else None

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(str(value or "").split())

    @staticmethod
    def _clean_none(value: Optional[str]) -> Optional[str]:
        cleaned = AnantLabsConnector._clean_text(value or "")
        if not cleaned or cleaned in {"—", "-"}:
            return None
        return cleaned

    @staticmethod
    def _not_found_result(cas_number: str) -> Dict[str, Any]:
        return {
            "source": "Anant Labs",
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
            "source": "Anant Labs",
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


def search_anantlabs(cas_number: str) -> Dict[str, Any]:
    """Convenience wrapper for the Anant Labs connector."""

    return AnantLabsConnector().search(cas_number)


__all__ = ["AnantLabsConnector", "search_anantlabs"]
