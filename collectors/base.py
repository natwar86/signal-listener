"""
Shared HTTP fetcher and base collector class.

PoliteFetcher is carried over from V0.1 — polite delays, retries, backoff.
"""

import time
import random
import logging
import requests

log = logging.getLogger("signal-listener")

# Backoff settings
INITIAL_BACKOFF = 30.0
MAX_BACKOFF = 300.0
BACKOFF_MULTIPLIER = 2.0
MAX_RETRIES = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class PoliteFetcher:
    """HTTP client with delays, retries, and exponential backoff."""

    def __init__(self, min_delay: float = 4.0, max_delay: float = 8.0):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._last_request_time = 0.0
        self.min_delay = min_delay
        self.max_delay = max_delay

    def _wait_politely(self):
        elapsed = time.monotonic() - self._last_request_time
        delay = random.uniform(self.min_delay, self.max_delay)
        remaining = delay - elapsed
        if remaining > 0:
            log.debug(f"Waiting {remaining:.1f}s before next request")
            time.sleep(remaining)

    def fetch(self, url: str, method: str = "GET", max_retries: int = MAX_RETRIES, **kwargs) -> requests.Response | None:
        """
        Fetch a URL politely. Returns Response on success, None after
        exhausting all retries.
        """
        backoff = INITIAL_BACKOFF

        for attempt in range(1, max_retries + 1):
            self._wait_politely()
            self._last_request_time = time.monotonic()

            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
            except requests.RequestException as exc:
                log.warning(f"Request error (attempt {attempt}): {exc}")
                if attempt < max_retries:
                    self._backoff_sleep(backoff, reason="request error")
                    backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                continue

            if resp.status_code == 200:
                return resp

            if resp.status_code == 404:
                log.info(f"404 for {url}")
                return None

            if resp.status_code in (429, 503):
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else backoff
                log.warning(f"Rate limited ({resp.status_code}), waiting {wait:.0f}s")
                self._backoff_sleep(wait, reason="rate limit")
                backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                continue

            log.warning(f"HTTP {resp.status_code} for {url} (attempt {attempt})")
            if attempt < max_retries:
                self._backoff_sleep(backoff, reason=f"HTTP {resp.status_code}")
                backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)

        log.error(f"Giving up on {url} after {max_retries} attempts")
        return None

    @staticmethod
    def _backoff_sleep(seconds: float, reason: str = ""):
        jitter = random.uniform(0, seconds * 0.25)
        total = seconds + jitter
        log.info(f"Backoff: sleeping {total:.0f}s ({reason})")
        time.sleep(total)

    def head(self, url: str, **kwargs) -> requests.Response | None:
        """HEAD request — used for store URL resolution."""
        self._wait_politely()
        self._last_request_time = time.monotonic()
        try:
            return self.session.head(url, timeout=15, allow_redirects=True, **kwargs)
        except requests.RequestException:
            return None

    def close(self):
        self.session.close()
