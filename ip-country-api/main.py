"""IP-to-country web API.

A small FastAPI service that resolves an IPv4/IPv6 address to its country
using the offline geoip2fast dataset (no external calls, no rate limits).
"""
from __future__ import annotations

import ipaddress

from fastapi import FastAPI, HTTPException
from geoip2fast import GeoIP2Fast
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

# Load the geolocation database once at startup; it is reused for every request.
geoip = GeoIP2Fast()

app = FastAPI(
    title="IP Country API",
    description="Resolve an IP address to its country, fully offline.",
    version="1.0.0",
)

# Domain metric: how many lookups resolved to each country. Cardinality is
# bounded (~250 country codes + a few private/reserved markers), so a label per
# country_code is safe. HTTP request count/latency are added by Instrumentator.
LOOKUPS = Counter(
    "ip_country_lookups_total",
    "Total IP-to-country lookups, labelled by resolved country code.",
    ["country_code"],
)

# Auto-instrument every request (http_request_duration_seconds, _requests_total,
# in-progress, etc.) and expose them at GET /metrics in Prometheus text format.
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


class CountryResponse(BaseModel):
    ip: str
    country_code: str
    country_name: str
    is_private: bool


def _lookup(ip: str) -> CountryResponse:
    # Validate the input so we return a clean 400 instead of a vague error.
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"'{ip}' is not a valid IP address")

    result = geoip.lookup(ip)
    country_code = result.country_code or "unknown"
    LOOKUPS.labels(country_code=country_code).inc()
    return CountryResponse(
        ip=ip,
        country_code=result.country_code or "",
        country_name=result.country_name or "Unknown",
        is_private=parsed.is_private,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/country/{ip}", response_model=CountryResponse)
def country_by_path(ip: str) -> CountryResponse:
    """Look up a country using a path parameter: /country/8.8.8.8"""
    return _lookup(ip)


@app.get("/country", response_model=CountryResponse)
def country_by_query(ip: str) -> CountryResponse:
    """Look up a country using a query parameter: /country?ip=8.8.8.8"""
    return _lookup(ip)
