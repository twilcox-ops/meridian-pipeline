"""USGS earthquake feed client.

Two fetch modes share the same pagination and retry machinery:

- `fetch_updated_since` — the scheduled-run path. Filters on USGS's
  `updatedafter`, not `starttime`. USGS revises magnitude and location on
  existing events for hours to days after they occur, as more seismograph
  data comes in. Filtering on origin time alone would silently miss those
  revisions the first time a run lands after the revision happens; filtering
  on update time catches new events and corrections in the same pass. This is
  also why the watermark tracks `updated_time_ms`, not `event_time_ms`.

- `fetch_origin_window` — the backfill path. Filters on `starttime`/`endtime`
  (origin time) to reprocess a specific historical window regardless of when
  it was last updated. It runs through the same pagination, parsing, upsert,
  and digest code as the scheduled path — only the query differs.
"""
from __future__ import annotations

import json
from typing import Iterator, Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from . import timeutil

USGS_BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
# USGS doesn't require a User-Agent, but setting one is good citizenship and
# free pagination/rate-limit debugging if you ever need to ask them about it.
DEFAULT_USER_AGENT = "meridian-portfolio-pipeline/1.0 (personal project; update this before deploying)"


class TransientAPIError(Exception):
    """429/5xx from USGS — safe to retry."""


class USGSClient:
    def __init__(
        self,
        base_url: str = USGS_BASE_URL,
        timeout: int = 30,
        page_size: int = 2000,
        min_magnitude: Optional[float] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.page_size = page_size
        self.min_magnitude = min_magnitude
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=20),
        retry=retry_if_exception_type((TransientAPIError, requests.ConnectionError, requests.Timeout)),
    )
    def _get_page(self, params: dict) -> dict:
        response = self.session.get(self.base_url, params=params, timeout=self.timeout)
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientAPIError(f"USGS API returned HTTP {response.status_code}")
        response.raise_for_status()
        return response.json()

    def fetch_updated_since(self, since_ms: int) -> Iterator[dict]:
        yield from self._paginate({"updatedafter": timeutil.ms_to_iso(since_ms)})

    def fetch_origin_window(self, start_ms: int, end_ms: int) -> Iterator[dict]:
        yield from self._paginate(
            {"starttime": timeutil.ms_to_iso(start_ms), "endtime": timeutil.ms_to_iso(end_ms)}
        )

    def _paginate(self, base_params: dict) -> Iterator[dict]:
        offset = 1  # USGS's `offset` param is 1-based
        while True:
            params = {
                "format": "geojson",
                "orderby": "time-asc",
                "limit": self.page_size,
                "offset": offset,
                **base_params,
            }
            if self.min_magnitude is not None:
                params["minmagnitude"] = self.min_magnitude

            payload = self._get_page(params)
            features = payload.get("features", [])
            for feature in features:
                yield _parse_feature(feature)

            if len(features) < self.page_size:
                return
            offset += self.page_size


def _parse_feature(feature: dict) -> dict:
    props = feature.get("properties", {})
    coords = (feature.get("geometry") or {}).get("coordinates") or [None, None, None]
    coords = (list(coords) + [None, None, None])[:3]
    longitude, latitude, depth_km = coords
    return {
        "id": feature["id"],
        "magnitude": props.get("mag"),
        "mag_type": props.get("magType"),
        "place": props.get("place"),
        "event_time_ms": props.get("time"),
        "updated_time_ms": props.get("updated"),
        "longitude": longitude,
        "latitude": latitude,
        "depth_km": depth_km,
        "event_type": props.get("type"),
        "status": props.get("status"),
        "tsunami": bool(props.get("tsunami")),
        "alert_level": props.get("alert"),
        "felt_reports": props.get("felt"),
        "url": props.get("url"),
        "raw_json": json.dumps(feature),
    }
