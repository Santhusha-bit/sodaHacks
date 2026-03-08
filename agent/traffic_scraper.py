"""
traffic_scraper.py — Selenium-based traffic incident scraper.

Scrapes Google Maps Traffic layer and 511.org for real-time road hazard info
near the configured location. Runs headless Chrome so it works on any OS.

The scraper outputs a list of TrafficAlert objects which the hazard classifier
uses to generate voice warnings for the driver.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    logger.warning("[Scraper] selenium/webdriver-manager not installed — running in mock mode.")
    SELENIUM_AVAILABLE = False


@dataclass
class TrafficAlert:
    severity: str           # "low" | "medium" | "high"
    description: str        # Human-readable incident description
    location: str           # Street/area name
    source: str             # "google_maps" | "511" | "mock"

    @property
    def is_high_severity(self) -> bool:
        return self.severity == "high"

    def to_speech(self) -> str:
        return f"Traffic alert: {self.description} near {self.location}."


def _make_chrome_options() -> "Options":
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--log-level=3")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    return opts


class TrafficScraper:
    """
    Scrapes traffic incidents for a given location.
    Creates a new headless Chrome session per scrape to avoid stale state.
    """

    def __init__(self, location: str, mock: bool = False):
        self.location = location
        self.mock = mock or not SELENIUM_AVAILABLE
        self._cached_alerts: list[TrafficAlert] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def get_alerts(self) -> list[TrafficAlert]:
        """
        Fetch current traffic alerts. Returns cached list on failure.
        """
        if self.mock:
            return self._mock_alerts()

        alerts = []
        try:
            alerts = self._scrape_google_maps()
            if not alerts:
                alerts = self._scrape_511()
        except Exception as e:
            logger.error(f"[Scraper] Fetch failed: {e} — returning cached data")
            alerts = self._cached_alerts

        self._cached_alerts = alerts
        return alerts

    def get_summary_speech(self, alerts: list[TrafficAlert]) -> Optional[str]:
        """Convert alert list to a natural speech summary."""
        if not alerts:
            return None
        high = [a for a in alerts if a.is_high_severity]
        if high:
            top = high[0]
            return f"Warning. {top.to_speech()}"
        medium = [a for a in alerts if a.severity == "medium"]
        if medium:
            return f"Traffic advisory. {medium[0].to_speech()}"
        return f"Minor delays reported near {alerts[0].location}."

    # ── Scrapers ──────────────────────────────────────────────────────────────

    def _scrape_google_maps(self) -> list[TrafficAlert]:
        """Scrape Google Maps for traffic incidents near location."""
        alerts = []
        location_query = self.location.replace(" ", "+").replace(",", "")
        url = f"https://www.google.com/maps/search/traffic+incidents+near+{location_query}"

        driver = self._make_driver()
        try:
            driver.get(url)
            wait = WebDriverWait(driver, 10)

            # Look for incident/delay result cards
            try:
                results = wait.until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "[data-result-index]"))
                )
                for el in results[:5]:
                    text = el.text.strip()
                    if text and any(kw in text.lower() for kw in
                                    ["accident", "closure", "delay", "construction", "incident", "crash"]):
                        severity = "high" if any(kw in text.lower() for kw in ["accident", "crash", "closure"]) \
                            else "medium"
                        alerts.append(TrafficAlert(
                            severity=severity,
                            description=text[:120],
                            location=self.location,
                            source="google_maps",
                        ))
            except Exception:
                logger.debug("[Scraper] No incident cards found on Google Maps")
        finally:
            driver.quit()

        logger.info(f"[Scraper] Google Maps: {len(alerts)} alerts found")
        return alerts

    def _scrape_511(self) -> list[TrafficAlert]:
        """Scrape 511.org as a fallback traffic data source."""
        alerts = []
        url = "https://511.org/traffic/map"

        driver = self._make_driver()
        try:
            driver.get(url)
            time.sleep(3)  # Allow JS to load map data

            # Try to find incident markers/tooltips
            try:
                incident_els = driver.find_elements(By.CSS_SELECTOR, ".incident-item, .event-item, [class*='incident']")
                for el in incident_els[:5]:
                    text = el.text.strip()
                    if text:
                        alerts.append(TrafficAlert(
                            severity="medium",
                            description=text[:120],
                            location=self.location,
                            source="511",
                        ))
            except Exception:
                logger.debug("[Scraper] No incident elements found on 511.org")
        finally:
            driver.quit()

        logger.info(f"[Scraper] 511.org: {len(alerts)} alerts found")
        return alerts

    def _make_driver(self) -> "webdriver.Chrome":
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=_make_chrome_options())

    def _mock_alerts(self) -> list[TrafficAlert]:
        """Return fake alerts for testing."""
        import random
        scenarios = [
            TrafficAlert("high", "Multi-vehicle accident blocking lanes", "Market St & 5th", "mock"),
            TrafficAlert("medium", "Construction causing delays, expect 10-minute slowdown", "Highway 101 North", "mock"),
            TrafficAlert("low", "Minor fender bender, right shoulder", "Bay Bridge approach", "mock"),
        ]
        num = random.randint(0, 2)
        results = scenarios[:num]
        logger.info(f"[MockScraper] 🚧 Returning {len(results)} mock traffic alerts")
        return results
