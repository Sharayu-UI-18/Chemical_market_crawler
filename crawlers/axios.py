"""Axios Research connector for public product availability research.

Site investigation notes (see module docstring at bottom for the full
write-up of how this was determined):

* https://www.axios-research.com/search/<CAS>/ is a Next.js page
  ("meta-og:site_name: NextSSS") that renders NOTHING on the server for
  search — fetching it with plain requests returns HTTP 200 with the
  literal body text "Search Results / Redirecting to search results...".
  There is no embedded __NEXT_DATA__ payload with results, and no public
  JSON/XHR/GraphQL endpoint was found that returns search results
  directly (no /api/search, no /_next/data/*/search/<cas>.json, no
  discoverable Algolia/GraphQL keys). The results are populated
  entirely client-side after the initial paint, which matches the
  symptom reported: HTTP 200 with an HTML body that has no product
  links.
* Individual product pages (e.g.
  https://www.axios-research.com/products/venlafaxine-ep-impurity-h)
  ARE fully server-rendered and contain the product name, catalogue
  number, CAS #, alternate CAS #, molecular formula, molecular weight
  and inventory status as plain text in the initial HTML — no
  JavaScript needed to read them.
* Product URLs are NOT consistently prefixed with /products/ — some
  live at /products/<slug>/ and others at /<slug>/ directly. This
  connector does not guess the slug; it always discovers the URL from
  the rendered search page.

Given that split, this connector uses Playwright ONLY for the one step
that genuinely requires JavaScript (loading /search/<CAS>/ and reading
the links it renders), then falls back to plain requests +
BeautifulSoup for every product page it visits, matching the
architecture of the other connectors in this project.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised when playwright isn't installed
    sync_playwright = None  # type: ignore[assignment]

    class PlaywrightTimeoutError(Exception):  # type: ignore[no-redef]
        """Stand-in so the rest of the module can still import cleanly."""


BASE_URL = "https://www.axios-research.com"
SEARCH_URL_TEMPLATE = f"{BASE_URL}/search/{{cas}}/"
DEFAULT_TIMEOUT = 15
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_PAGE_TIMEOUT_MS = 20000
MAX_CANDIDATES_TO_VERIFY = 12
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Paths that are always site chrome/navigation, never a product. Anything
# under these is skipped when scanning the rendered search page for
# candidate product links.
NAV_PATH_PREFIXES = (
    "",
    "/",
    "/aboutus",
    "/about-us",
    "/services",
    "/support",
    "/contact-us",
    "/contactus",
    "/career",
    "/careers",
    "/cart",
    "/search",
    "/blogs",
    "/blog",
    "/terms-and-conditions",
    "/sample-certificate-of-analysis",
    "/privacy-policy",
)
NAV_TEXT = {
    "home", "about us", "products", "services", "support", "contact",
    "careers", "career", "quote cart", "search", "login", "blogs", "blog",
    "en", "pt", "refresh data", "newsletter", "sign up",
    "terms and conditions", "sample certificate of analysis",
}

AVAILABILITY_MAP = {
    "in stock": "IN_STOCK",
    "out of stock": "OUT_OF_STOCK",
    "not available": "OUT_OF_STOCK",
    "unavailable": "OUT_OF_STOCK",
    "synthesis on demand": "SYNTHESIS_ON_DEMAND",
    "under synthesis": "SYNTHESIS_ON_DEMAND",
    "made to order": "SYNTHESIS_ON_DEMAND",
    "on request": "SYNTHESIS_ON_DEMAND",
    # "Enquire" is what Axios shows for items with no fixed on-hand
    # inventory that still need a custom quote/production run.
    "enquire": "SYNTHESIS_ON_DEMAND",
    "enquire now": "SYNTHESIS_ON_DEMAND",
    "limited stock": "IN_STOCK",
}


class AxiosParsingError(RuntimeError):
    """Raised when Axios Research returns a page that cannot be parsed reliably."""


class AxiosPlaywrightUnavailableError(RuntimeError):
    """Raised when Playwright (required for search) isn't installed/available."""


@dataclass
class AxiosProductData:
    """Everything this connector can extract from a single product page."""

    product_name: Optional[str] = None
    catalogue_number: Optional[str] = None
    cas_number: Optional[str] = None
    alternate_cas_number: Optional[str] = None
    molecular_formula: Optional[str] = None
    molecular_weight: Optional[str] = None
    availability_raw: Optional[str] = None


class AxiosConnector:
    """Public Axios Research product search connector."""

    def __init__(
        self,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        session: Optional[requests.Session] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        headless: bool = True,
        page_timeout_ms: int = DEFAULT_PAGE_TIMEOUT_MS,
        max_candidates_to_verify: int = MAX_CANDIDATES_TO_VERIFY,
        debug: bool = True,
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
        self.user_agent = user_agent
        self.headless = headless
        self.page_timeout_ms = page_timeout_ms
        self.max_candidates_to_verify = max_candidates_to_verify
        self.debug = debug

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, cas_number: str) -> Dict[str, Any]:
        """Search Axios Research for a CAS number and return public details when found."""

        cas_number = self._normalize_cas(cas_number)
        if not cas_number:
            return self._error_result(cas_number, "invalid_cas", "CAS number is required.")
        if not self._is_valid_cas(cas_number):
            return self._error_result(
                cas_number, "invalid_cas", "CAS number must have a valid format and checksum."
            )

        try:
            candidate_urls = self._find_candidate_urls(cas_number)
            matched_url, product_data = self._verify_candidates(cas_number, candidate_urls)

            if not matched_url or not product_data:
                self._log("selected product URL: none")
                self._log("extracted CAS: none")
                self._log("extracted availability: none")
                return self._not_found_result(cas_number)

            self._log(f"selected product URL: {matched_url}")
            self._log(f"extracted CAS: {product_data.cas_number}")
            self._log(f"extracted availability: {product_data.availability_raw}")

            return {
                "source": "axios",
                "query_cas": cas_number,
                "found": True,
                "exact_match": True,
                "product_name": product_data.product_name,
                "product_url": matched_url,
                "catalogue_number": product_data.catalogue_number,
                "cas_number": product_data.cas_number or cas_number,
                "molecular_formula": product_data.molecular_formula,
                "molecular_weight": product_data.molecular_weight,
                "availability": self._normalize_availability(product_data.availability_raw),
                "availability_raw": product_data.availability_raw,
                "shipping_condition": None,
                "country_of_origin": None,
                "smiles": None,
                "error": None,
            }
        except AxiosPlaywrightUnavailableError as exc:
            return self._error_result(cas_number, "playwright_unavailable", str(exc), exc)
        except requests.Timeout as exc:
            return self._error_result(cas_number, "timeout", "Axios Research request timed out.", exc)
        except requests.ConnectionError as exc:
            return self._error_result(cas_number, "connection_error", "Unable to connect to Axios Research.", exc)
        except requests.HTTPError as exc:
            return self._error_result(cas_number, "http_error", "Axios Research returned an HTTP error.", exc)
        except AxiosParsingError as exc:
            return self._error_result(cas_number, "parsing_failure", str(exc), exc)
        except PlaywrightTimeoutError as exc:  # pragma: no cover - network dependent
            return self._error_result(cas_number, "timeout", "Axios Research search page timed out rendering.", exc)

    # ------------------------------------------------------------------
    # Search (Playwright — the search results page is client-rendered)
    # ------------------------------------------------------------------

    def _find_candidate_urls(self, cas_number: str) -> List[str]:
        search_url = SEARCH_URL_TEMPLATE.format(cas=cas_number)
        self._log(f"search URL: {search_url}")

        if sync_playwright is None:
            raise AxiosPlaywrightUnavailableError(
                "Playwright is not installed. Axios Research renders search results "
                "client-side (plain requests return HTTP 200 with no product links), "
                "so search requires a real browser. Install with "
                "`pip install playwright` and `playwright install chromium`."
            )

        html = ""
        status_code: Optional[int] = None

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            try:
                page = browser.new_page(user_agent=self.user_agent)
                response = page.goto(search_url, timeout=self.page_timeout_ms, wait_until="domcontentloaded")
                status_code = response.status if response else None
                self._log(f"HTTP status: {status_code}")
                self._log("JavaScript rendering required: True (search results populate client-side)")

                # Let the SPA settle after its data fetch. networkidle can hang
                # forever on pages with polling/analytics, so treat a timeout
                # here as "good enough, keep going" rather than a failure.
                try:
                    page.wait_for_load_state("networkidle", timeout=self.page_timeout_ms)
                except PlaywrightTimeoutError:
                    pass

                try:
                    page.wait_for_selector("main a[href], a[href*='/products/']", timeout=8000)
                except PlaywrightTimeoutError:
                    pass

                html = page.content()
            finally:
                browser.close()

        self._log(
            "API endpoint discovered: none (no public JSON/XHR/GraphQL endpoint found; "
            "results are rendered directly into the DOM client-side)"
        )

        return self._extract_candidate_urls_from_html(html, cas_number)

    def _extract_candidate_urls_from_html(self, html: str, cas_number: str) -> List[str]:
        soup = BeautifulSoup(html or "", "html.parser")

        prioritized: List[str] = []
        others: List[str] = []
        seen = set()

        for link in soup.find_all("a", href=True):
            href = link["href"].strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue

            absolute_url = urljoin(BASE_URL, href)
            if absolute_url in seen or self._is_nav_link(absolute_url, link):
                continue
            seen.add(absolute_url)

            text = " ".join(link.get_text(" ", strip=True).split())
            if cas_number in text or cas_number in href:
                prioritized.append(absolute_url)
            else:
                others.append(absolute_url)

        # Product links first (most likely relevant), then anything else the
        # page rendered, capped so a single search can't fetch the whole site.
        ordered = prioritized + [u for u in others if u not in prioritized]
        return ordered[: self.max_candidates_to_verify]

    @staticmethod
    def _is_nav_link(absolute_url: str, link: Any) -> bool:
        path = urlparse(absolute_url).path.rstrip("/").lower()
        if path in NAV_PATH_PREFIXES:
            return True
        for prefix in NAV_PATH_PREFIXES:
            if prefix and (path == prefix or path.startswith(prefix + "/")):
                return True
        if "/images/" in path or "/cdn-cgi/" in path:
            return True
        text = " ".join(link.get_text(" ", strip=True).split()).lower()
        if text in NAV_TEXT:
            return True
        return False

    # ------------------------------------------------------------------
    # Product page (plain requests + BeautifulSoup — these ARE server rendered)
    # ------------------------------------------------------------------

    def _verify_candidates(
        self, cas_number: str, candidate_urls: List[str]
    ) -> "tuple[Optional[str], Optional[AxiosProductData]]":
        for url in candidate_urls:
            try:
                data = self._fetch_product_page(url)
            except (AxiosParsingError, requests.RequestException):
                continue

            if data.cas_number == cas_number or data.alternate_cas_number == cas_number:
                return url, data

        return None, None

    def _fetch_product_page(self, product_url: str) -> AxiosProductData:
        response = self._get(product_url)
        soup = BeautifulSoup(response.text, "html.parser")
        text = " ".join(soup.get_text(" ", strip=True).split())

        product_name = self._extract_product_name(soup)
        catalogue_number = self._extract_field_from_text(
            text, ["Catalogue #"], ["CAS #", "Alternate CAS #", "Molecular Formula"]
        )
        cas_number = self._extract_field_from_text(
            text, ["CAS #"], ["Alternate CAS #", "Molecular Formula", "Molecular weight"]
        )
        alternate_cas_number = self._extract_field_from_text(
            text, ["Alternate CAS #"], ["Molecular Formula", "Molecular weight"]
        )
        molecular_formula = self._extract_field_from_text(
            text, ["Molecular Formula"], ["Molecular weight", "H.S. code", "Inventory Status"]
        )
        molecular_weight = self._extract_field_from_text(
            text, ["Molecular weight", "Molecular Weight"], ["H.S. code", "Inventory Status", "Request Quote"]
        )
        availability_raw = self._extract_field_from_text(
            text, ["Inventory Status"], ["Request Quote", "Synonyms", "Description"]
        )

        if alternate_cas_number and alternate_cas_number.strip().upper() in {"NA", "N/A", "-", ""}:
            alternate_cas_number = None
        if cas_number and cas_number.strip().upper() in {"NA", "N/A", "-", ""}:
            cas_number = None

        if not product_name and not cas_number and not catalogue_number:
            raise AxiosParsingError(
                f"Unable to extract public product details from the Axios Research product page: {product_url}"
            )

        return AxiosProductData(
            product_name=product_name,
            catalogue_number=catalogue_number,
            cas_number=cas_number,
            alternate_cas_number=alternate_cas_number,
            molecular_formula=molecular_formula,
            molecular_weight=molecular_weight,
            availability_raw=availability_raw,
        )

    def _extract_product_name(self, soup: BeautifulSoup) -> Optional[str]:
        headings = [
            " ".join(h.get_text(" ", strip=True).split())
            for h in soup.find_all("h1")
        ]
        headings = [h for h in headings if h and h.lower() != "products"]
        if headings:
            return headings[-1]

        if soup.title and soup.title.string:
            title = " ".join(soup.title.string.split())
            for separator in (" - CAS", " | Axios Research"):
                if separator in title:
                    title = title.split(separator)[0].strip()
            return title or None

        return None

    def _get(self, url: str) -> requests.Response:
        time.sleep(self.delay_seconds)
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response

    def _log(self, message: str) -> None:
        if self.debug:
            print(f"[axios] {message}")

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------

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
    def _extract_cas(text: str) -> Optional[str]:
        match = re.search(r"\b(\d{2,7}-\d{2}-\d)\b", text)
        return match.group(1) if match else None

    @staticmethod
    def _normalize_availability(value: Optional[str]) -> str:
        text = " ".join(str(value or "").split()).lower()
        if not text:
            return "UNKNOWN"
        for phrase, normalized in AVAILABILITY_MAP.items():
            if phrase in text:
                return normalized
        return "UNKNOWN"

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

        remainder = text[label_index + len(matched_label):].lstrip(" :#")
        stop_index = len(remainder)
        lower_remainder = remainder.lower()

        for stop_label in stop_labels:
            index = lower_remainder.find(stop_label.lower())
            if index != -1 and index < stop_index:
                stop_index = index

        value = remainder[:stop_index].strip(" :#")
        return value or None

    def _not_found_result(self, cas_number: str) -> Dict[str, Any]:
        return {
            "source": "axios",
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
            "error": None,
        }

    def _error_result(
        self, cas_number: str, error_type: str, message: str, exception: Optional[Exception] = None
    ) -> Dict[str, Any]:
        error: Dict[str, Any] = {"type": error_type, "message": message}
        if isinstance(exception, requests.HTTPError) and exception.response is not None:
            error["status_code"] = exception.response.status_code

        return {
            "source": "axios",
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
            "error": error,
        }


def search_axios(cas_number: str) -> Dict[str, Any]:
    """Convenience wrapper for the Axios Research connector."""

    return AxiosConnector().search(cas_number)


__all__ = ["AxiosConnector", "search_axios", "AxiosPlaywrightUnavailableError", "AxiosParsingError"]
