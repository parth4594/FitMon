"""HTTP client for the Hevy REST API (spec 05).

The only file in this project that imports `requests`. All auth, pagination
parameters, retry/backoff, and rate-limit handling for the Hevy API live
here — src/ingestion/ingest_hevy_api.py never calls `requests` directly, it
only calls the functions below.
"""
import logging
import time

import requests

from src.config.settings import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hevyapp.com/v1"
PAGE_SIZE = 10          # always use max
REQUEST_DELAY = 1.0     # seconds between paginated requests
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds; retry waits 2^attempt seconds
TIMEOUT_SECONDS = 10


class HevyAPIError(RuntimeError):
    """Raised when a Hevy API request fails (immediately for 401, or after
    MAX_RETRIES for everything else). `status_code` lets the caller record a
    `failure_code` in meta.ingestion_log.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def get_headers() -> dict:
    return {"api-key": settings.hevy_api_key}


def make_request(url: str, params: dict) -> dict:
    """Make a single logical GET request, retrying transient failures.

    - 200: returns the parsed JSON body.
    - 401: raises immediately, no retry — a bad API key won't fix itself.
    - 429: waits the `Retry-After` header value if present, else exponential
      backoff, then retries (up to MAX_RETRIES).
    - Other 4xx: raises immediately — likely a bug in request construction.
    - 5xx: exponential backoff, then retries (up to MAX_RETRIES).
    - After MAX_RETRIES exhausted: raises HevyAPIError with the last status
      code, for the caller to log to meta.ingestion_log.
    """
    last_status: int | None = None

    for attempt in range(MAX_RETRIES + 1):
        response = requests.get(
            url, headers=get_headers(), params=params, timeout=TIMEOUT_SECONDS
        )

        if response.status_code == 200:
            return response.json()

        last_status = response.status_code

        if response.status_code == 401:
            raise HevyAPIError(
                "Hevy API rejected the API key (401 Unauthorized)", status_code=401
            )

        if response.status_code == 429:
            if attempt >= MAX_RETRIES:
                break
            retry_after = response.headers.get("Retry-After")
            wait_seconds = (
                float(retry_after) if retry_after else RETRY_BACKOFF_BASE**attempt
            )
            logger.warning(
                "Hevy API rate-limited (429) on %s, retrying in %.1fs (attempt %d/%d)",
                url,
                wait_seconds,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(wait_seconds)
            continue

        if 400 <= response.status_code < 500:
            raise HevyAPIError(
                f"Hevy API request failed with client error {response.status_code}: {url}",
                status_code=response.status_code,
            )

        # 5xx — exponential backoff retry
        if attempt >= MAX_RETRIES:
            break
        wait_seconds = RETRY_BACKOFF_BASE**attempt
        logger.warning(
            "Hevy API request to %s failed (status %d), retrying in %ds (attempt %d/%d)",
            url,
            response.status_code,
            wait_seconds,
            attempt + 1,
            MAX_RETRIES,
        )
        time.sleep(wait_seconds)

    raise HevyAPIError(
        f"Hevy API request failed after {MAX_RETRIES} retries "
        f"(last status {last_status}): {url}",
        status_code=last_status,
    )


def fetch_workouts_page(page: int) -> dict:
    """GET /v1/workouts?page=<page>&pageSize=10 — full response dict
    including `page`, `page_count`, `workouts`.
    """
    return make_request(f"{BASE_URL}/workouts", {"page": page, "pageSize": PAGE_SIZE})


def fetch_workout_events_page(since: str, page: int) -> dict:
    """GET /v1/workouts/events?since=<since>&page=<page>&pageSize=10 — full
    response dict including `page`, `page_count`, `events`. `since` is an
    ISO 8601 timestamp string.
    """
    return make_request(
        f"{BASE_URL}/workouts/events",
        {"since": since, "page": page, "pageSize": PAGE_SIZE},
    )


def verify_connection() -> bool:
    """GET /v1/user/info. Returns True on 200, raises HevyAPIError otherwise."""
    response = requests.get(
        f"{BASE_URL}/user/info", headers=get_headers(), timeout=TIMEOUT_SECONDS
    )
    if response.status_code == 200:
        return True
    raise HevyAPIError(
        f"Hevy API connection test failed with status {response.status_code}",
        status_code=response.status_code,
    )
