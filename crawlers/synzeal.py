"""SynZeal connector for public product availability research."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.synzeal.com"
SEARCH_URL = f"{BASE_URL}/product/search"
AUTOCOMPLETE_URL = f"{BASE_URL}/catalog/searchtermautocomplete"
PRODUCT_PAGE_PREFIX = f"{BASE_URL}/en/"
DEFAULT_TIMEOUT = 15
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class SynZealParsingError(RuntimeError):
    """Raised when SynZeal returns a page that cannot be parsed reliably."""


@dataclass
class SynZealCandidate:
    """A public product candidate returned by SynZeal search endpoints."""

    product_name: str
    product_url: str
    catalogue_number: Optional[str] = None
    cas_number: Optional[str] = None


class SynZealConnector:
    """Public SynZeal product search connector."""

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
        """Search SynZeal for a CAS number and return public details when found."""

        cas_number = self._normalize_cas(cas_number)
        if not cas_number:
            return self._error_result(
                cas_number,
                error_type="invalid_cas",
                message="CAS number is required.",
            )
        if not self._is_valid_cas(cas_number):
            return self._error_result(
                cas_number,
                error_type="invalid_cas",
                message="CAS number must have a valid format and checksum.",
            )

        try:
            candidates = self._find_candidates(cas_number)
            exact_match = self._select_exact_match(cas_number, candidates)

            if not exact_match:
                return self._not_found_result(cas_number)

            product_data = self._fetch_product_page(exact_match.product_url)
            product_data.setdefault("cas_number", exact_match.cas_number or cas_number)
            product_data.setdefault("product_name", exact_match.product_name)
            product_data.setdefault("product_url", exact_match.product_url)
            product_data.setdefault("catalogue_number", exact_match.catalogue_number)

            return {
                "source": "SynZeal",
                "query_cas": cas_number,
                "found": True,
                "exact_match": True,
                "product_name": product_data.get("product_name"),
                "product_url": product_data.get("product_url"),
                "catalogue_number": product_data.get("catalogue_number"),
                "cas_number": product_data.get("cas_number"),
                "molecular_formula": product_data.get("molecular_formula"),
                "molecular_weight": product_data.get("molecular_weight"),
                "availability": product_data.get("availability"),
                "availability_raw": product_data.get("availability_raw"),
                "shipping_condition": product_data.get("shipping_condition"),
                "country_of_origin": product_data.get("country_of_origin"),
                "smiles": product_data.get("smiles"),
            }
        except requests.Timeout as exc:
            return self._error_result(cas_number, "timeout", "SynZeal request timed out.", exc)
        except requests.ConnectionError as exc:
            return self._error_result(cas_number, "connection_error", "Unable to connect to SynZeal.", exc)
        except requests.HTTPError as exc:
            return self._error_result(cas_number, "http_error", "SynZeal returned an HTTP error.", exc)
        except SynZealParsingError as exc:
            return self._error_result(cas_number, "parsing_failure", str(exc), exc)
        except ValueError as exc:
            return self._error_result(cas_number, "parsing_failure", "Unable to parse SynZeal response.", exc)

    def _find_candidates(self, cas_number: str) -> List[SynZealCandidate]:
        candidates: List[SynZealCandidate] = []
        seen_urls = set()

        for item in self._autocomplete(cas_number):
            candidate = self._candidate_from_autocomplete_item(item)
            if not candidate or candidate.product_url in seen_urls:
                continue
            seen_urls.add(candidate.product_url)
            candidates.append(candidate)

        for item in self._search_results(cas_number):
            candidate = self._candidate_from_search_card(item)
            if not candidate or candidate.product_url in seen_urls:
                continue
            seen_urls.add(candidate.product_url)
            candidates.append(candidate)

        return [candidate for candidate in candidates if candidate.cas_number == cas_number]

    def _autocomplete(self, term: str) -> List[Dict[str, Any]]:
        response = self._get(AUTOCOMPLETE_URL, params={"term": term})
        data = response.json()
        if isinstance(data, list):
            return data
        return []

    def _search_results(self, query: str) -> List[Dict[str, Any]]:
        try:
            response = self._get(SEARCH_URL, params={"q": query})
        except requests.RequestException:
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        results: List[Dict[str, Any]] = []

        for card in soup.select(".product-item, .product-grid-item, .item-box, .product-box, .featured-products-bottom > div"):
            text = " ".join(card.get_text(" ", strip=True).split())
            link = card.select_one("a[href*='/en/']")
            if not link:
                continue
            results.append(
                {
                    "text": text,
                    "url": urljoin(BASE_URL, link.get("href", "")),
                }
            )

        if results:
            return results

        for link in soup.select("a[href*='/en/']"):
            text = " ".join(link.get_text(" ", strip=True).split())
            href = link.get("href", "")
            if not text or not href:
                continue
            if href.startswith("javascript:"):
                continue
            results.append({"text": text, "url": urljoin(BASE_URL, href)})

        return results

    def _candidate_from_autocomplete_item(self, item: Dict[str, Any]) -> Optional[SynZealCandidate]:
        label = str(item.get("label") or "").strip()
        product_url = self._absolute_url(str(item.get("producturl") or ""))
        if not label or not product_url:
            return None

        product_name, cas_number = self._split_label_and_cas(label)
        catalogue_number = self._extract_catalogue_number(label)
        return SynZealCandidate(
            product_name=product_name,
            product_url=product_url,
            catalogue_number=catalogue_number,
            cas_number=cas_number,
        )

    def _candidate_from_search_card(self, item: Dict[str, Any]) -> Optional[SynZealCandidate]:
        text = str(item.get("text") or "").strip()
        product_url = self._absolute_url(str(item.get("url") or ""))
        if not text or not product_url:
            return None

        cas_number = self._extract_cas(text)
        product_name = self._extract_product_name_from_text(text)
        catalogue_number = self._extract_catalogue_number(text)
        return SynZealCandidate(
            product_name=product_name,
            product_url=product_url,
            catalogue_number=catalogue_number,
            cas_number=cas_number,
        )

    def _select_exact_match(self, cas_number: str, candidates: List[SynZealCandidate]) -> Optional[SynZealCandidate]:
        for candidate in candidates:
            if candidate.cas_number == cas_number:
                return candidate
        return None

    def _fetch_product_page(self, product_url: str) -> Dict[str, Any]:
        response = self._get(product_url)
        soup = BeautifulSoup(response.text, "html.parser")

        title = self._first_text(soup, ["h2", ".product-name", ".product-title", "h1"])
        summary_block = self._find_product_summary_block(soup)
        summary_text = self._text_from_node(summary_block)
        availability_raw = self._extract_dom_label_value(
            soup,
            ["Inv. Status", "Availability", "Stock"],
        ) or self._extract_table_value(soup, ["Inv. Status", "Availability", "Stock"])
        shipping_condition = self._extract_dom_label_value(
            summary_block,
            ["Shipping Condition", "Shipping Temperature"],
        ) or self._extract_field_from_text(
            summary_text,
            ["Shipping Condition", "Shipping Temperature"],
            ["Country of Origin", "Smiles", "Usage Note", "CHNS Ratio", "Ratio (%)", "Product Overview", "Description", "Technical Data", "Related Products", "Disclaimer"],
        )
        country_of_origin = self._extract_dom_label_value(
            summary_block,
            ["Country of Origin"],
        ) or self._extract_field_from_text(
            summary_text,
            ["Country of Origin"],
            ["Smiles", "Usage Note", "CHNS Ratio", "Ratio (%)", "Product Overview", "Description", "Technical Data", "Related Products", "Disclaimer"],
        )
        smiles = self._extract_dom_label_value(
            summary_block,
            ["Smiles"],
        ) or self._extract_field_from_text(
            summary_text,
            ["Smiles"],
            ["CHNS Ratio", "Ratio (%)", "Usage Note", "Product Overview", "Description", "Technical Data", "Related Products", "Disclaimer"],
        )
        details_text = " ".join(soup.get_text(" ", strip=True).split())

        result: Dict[str, Any] = {
            "product_name": title,
            "product_url": product_url,
            "catalogue_number": self._extract_table_value(soup, ["SZ CAT No", "Catalog No", "Catalogue No", "Cat No"]),
            "cas_number": self._extract_table_value(soup, ["CAS No", "CAS"]),
            "molecular_formula": self._extract_table_value(soup, ["Mol.F.", "Molecular Formula"]),
            "molecular_weight": self._extract_table_value(soup, ["Mol.Wt.", "Molecular Weight"]),
            "availability_raw": availability_raw,
            "availability": self._normalize_availability(availability_raw),
            "shipping_condition": shipping_condition,
            "country_of_origin": country_of_origin,
            "smiles": smiles,
        }

        if not result["cas_number"]:
            result["cas_number"] = self._extract_cas(details_text)
        if not result["product_name"]:
            result["product_name"] = self._extract_product_name_from_text(details_text)

        if not result["product_name"] and not result["cas_number"] and not result["catalogue_number"]:
            raise SynZealParsingError("Unable to extract public product details from the SynZeal product page.")

        return result

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
        if "in stock" in text:
            return "IN_STOCK"
        if "synthesis on demand" in text or "under synthesis" in text:
            return "SYNTHESIS_ON_DEMAND"
        if "out of stock" in text or "not available" in text or "unavailable" in text:
            return "OUT_OF_STOCK"
        return "UNKNOWN"

    @staticmethod
    def _absolute_url(url: str) -> str:
        if not url:
            return ""
        return urljoin(BASE_URL, url)

    @staticmethod
    def _split_label_and_cas(label: str) -> tuple[str, Optional[str]]:
        cas_match = re.search(r"\b(\d{2,7}-\d{2}-\d)\b", label)
        if cas_match:
            cas_number = cas_match.group(1)
            product_name = label[: cas_match.start()].strip(" -–|\n\t")
            return product_name or label, cas_number
        return label, None

    @staticmethod
    def _extract_cas(text: str) -> Optional[str]:
        match = re.search(r"\b(\d{2,7}-\d{2}-\d)\b", text)
        return match.group(1) if match else None

    @staticmethod
    def _extract_catalogue_number(text: str) -> Optional[str]:
        match = re.search(r"\bSZ-[A-Z0-9-]+\b", text)
        return match.group(0) if match else None

    @staticmethod
    def _extract_product_name_from_text(text: str) -> Optional[str]:
        if not text:
            return None
        if "|" in text:
            return text.split("|")[0].strip()
        if " - " in text:
            return text.split(" - ")[0].strip()
        if "SZ CAT No" in text:
            return text.split("SZ CAT No")[0].strip()
        return text.strip()

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
    def _first_text_from_text(text: str, labels: List[str], stop_labels: List[str]) -> Optional[str]:
        return SynZealConnector._extract_field_from_text(text, labels, stop_labels)

    @staticmethod
    def _find_product_summary_block(soup: BeautifulSoup) -> Any:
        for element in soup.find_all(["table", "section", "div", "dl"]):
            text = " ".join(element.get_text(" ", strip=True).split())
            if "Inv. Status" in text and "Mol.Wt." in text:
                return element
            if "Shipping Condition" in text and "Smiles" in text:
                return element
        return soup

    @staticmethod
    def _text_from_node(node: Any) -> str:
        if node is None:
            return ""
        return " ".join(node.get_text(" ", strip=True).split())

    @staticmethod
    def _extract_dom_label_value(node: Any, labels: List[str]) -> Optional[str]:
        if node is None or not hasattr(node, "find_all"):
            return None

        for row in node.find_all(["tr", "li", "div", "p", "span", "dt"]):
            cells = row.find_all(["th", "td", "dt", "dd"])
            if len(cells) >= 2:
                key = " ".join(cells[0].get_text(" ", strip=True).split()).rstrip(":")
                value = " ".join(cells[1].get_text(" ", strip=True).split())
                if key in labels and value:
                    return value

            direct_text = " ".join(row.get_text(" ", strip=True).split())
            for label in labels:
                if direct_text.startswith(label):
                    value = direct_text[len(label):].lstrip(": -").strip()
                    if value:
                        value = SynZealConnector._truncate_at_known_label(
                            value,
                            ["Inv. Status", "Shipping Condition", "Shipping Temperature", "Country of Origin", "Smiles", "CHNS Ratio", "Ratio (%)", "Usage Note", "Product Overview", "Description", "Technical Data", "Related Products", "Disclaimer"],
                        )
                        return value or None

        return None

    @staticmethod
    def _truncate_at_known_label(text: str, stop_labels: List[str]) -> str:
        lower_text = text.lower()
        stop_index = len(text)
        for stop_label in stop_labels:
            index = lower_text.find(stop_label.lower())
            if index != -1 and index < stop_index:
                stop_index = index
        return text[:stop_index].strip(" :-")

    def _not_found_result(self, cas_number: str) -> Dict[str, Any]:
        return {
            "source": "SynZeal",
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
    def _extract_field_from_text(text: str, labels: List[str], stop_labels: List[str]) -> Optional[str]:
        if not text:
            return None
        lower_text = text.lower()
        label_index = None
        matched_label = None

        for label in labels:
            index = lower_text.find(label.lower())
            if index != -1 and (label_index is None or index < label_index):
                label_index = index
                matched_label = label

        if label_index is None or matched_label is None:
            return None

        remainder = text[label_index + len(matched_label):].lstrip(" :")
        stop_index = len(remainder)
        lower_remainder = remainder.lower()

        for stop_label in stop_labels:
            index = lower_remainder.find(stop_label.lower())
            if index != -1 and index < stop_index:
                stop_index = index

        value = remainder[:stop_index].strip(" :")
        return value or None

    @staticmethod
    def _candidate_to_dict(candidate: SynZealCandidate) -> Dict[str, Any]:
        return {
            "product_name": candidate.product_name,
            "product_url": candidate.product_url,
            "catalogue_number": candidate.catalogue_number,
            "cas_number": candidate.cas_number,
        }

    @staticmethod
    def _error_result(cas_number: str, error_type: str, message: str, exception: Optional[Exception] = None) -> Dict[str, Any]:
        error: Dict[str, Any] = {
            "type": error_type,
            "message": message,
        }
        if isinstance(exception, requests.HTTPError) and exception.response is not None:
            error["status_code"] = exception.response.status_code
        return {
            "source": "SynZeal",
            "query_cas": cas_number,
            "found": False,
            "exact_match": False,
            "error": error,
        }

    @staticmethod
    def _extract_table_value(soup: BeautifulSoup, labels: List[str]) -> Optional[str]:
        tables = soup.find_all("table")
        for table in tables:
            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])
                if len(cells) < 2:
                    continue
                key = " ".join(cells[0].get_text(" ", strip=True).split()).rstrip(":")
                value = " ".join(cells[1].get_text(" ", strip=True).split())
                if key in labels and value:
                    return value
        return None


def search_synzeal(cas_number: str) -> Dict[str, Any]:
    """Convenience wrapper for the SynZeal connector."""

    return SynZealConnector().search(cas_number)


__all__ = ["SynZealConnector", "search_synzeal"]
