"""IP-to-country web API.

A small FastAPI service that resolves an IPv4/IPv6 address to its country
using the offline geoip2fast dataset (no external calls, no rate limits).

Each successful lookup is also recorded to PostgreSQL (timestamp, ip, country)
on a background task, so the database write never delays — or breaks — the API
response.
"""
from __future__ import annotations

import ipaddress
import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import BackgroundTasks, FastAPI, HTTPException
from geoip2fast import GeoIP2Fast
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

logger = logging.getLogger("ip_country_api")

# Load the geolocation database once at startup; it is reused for every request.
geoip = GeoIP2Fast()

# Domain metric: how many lookups resolved to each country. Cardinality is
# bounded (~250 country codes + a few private/reserved markers), so a label per
# country_code is safe. HTTP request count/latency are added by Instrumentator.
LOOKUPS = Counter(
    "ip_country_lookups_total",
    "Total IP-to-country lookups, labelled by resolved country code.",
    ["country_code"],
)

# Connection params come from the standard PG* env vars (PGHOST/PGPORT/PGUSER/
# PGPASSWORD/PGDATABASE) set in the Deployment — asyncpg reads them automatically.
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ip_lookups (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip           TEXT NOT NULL,
    country_code TEXT,
    country_name TEXT
);
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Open a small async pool and ensure the table exists. A database problem
    # must NOT take the API down, so any failure here is swallowed and request
    # logging silently degrades to a no-op until the next restart.
    app.state.pool = None
    try:
        # server_settings.timezone makes THIS app's sessions render timestamps in
        # Tehran time (+03:30) immediately.
        app.state.pool = await asyncpg.create_pool(
            min_size=1,
            max_size=5,
            timeout=5,
            server_settings={"timezone": "Asia/Tehran"},
        )
        async with app.state.pool.acquire() as conn:
            await conn.execute(CREATE_TABLE)
            # Persist Tehran as the database default so every client (e.g. psql)
            # also sees +03:30, not just this app. Applies to new sessions; needs
            # appuser to own appdb (it does — the chart creates it that way).
            db = await conn.fetchval("SELECT current_database()")
            await conn.execute(f'ALTER DATABASE "{db}" SET timezone TO \'Asia/Tehran\'')
        logger.info("connected to postgres; ip_lookups table ready (tz=Asia/Tehran)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("postgres unavailable, request logging disabled: %s", exc)
    try:
        yield
    finally:
        if app.state.pool is not None:
            await app.state.pool.close()


app = FastAPI(
    title="IP Country API",
    description="Resolve an IP address to its country, fully offline.",
    version="1.0.0",
    lifespan=lifespan,
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


async def _log_request(ip: str, country_code: str, country_name: str) -> None:
    # Fire-and-forget insert run as a background task. Tolerant of DB outages:
    # a failed write is logged, never raised, so the client already has its
    # response regardless.
    pool = app.state.pool
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO ip_lookups (ip, country_code, country_name) "
                "VALUES ($1, $2, $3)",
                ip,
                country_code,
                country_name,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to log request to postgres: %s", exc)


@app.get("/health")
def health() -> dict[str, str]:
    # Liveness/readiness probe target.
    return {"status": "ok"}


@app.get("/country/{ip}", response_model=CountryResponse)
async def country_by_path(ip: str, background_tasks: BackgroundTasks) -> CountryResponse:
    """Look up a country using a path parameter: /country/8.8.8.8"""
    resp = _lookup(ip)
    background_tasks.add_task(_log_request, resp.ip, resp.country_code, resp.country_name)
    return resp


@app.get("/country", response_model=CountryResponse)
async def country_by_query(ip: str, background_tasks: BackgroundTasks) -> CountryResponse:
    """Look up a country using a query parameter: /country?ip=8.8.8.8"""
    resp = _lookup(ip)
    background_tasks.add_task(_log_request, resp.ip, resp.country_code, resp.country_name)
    return resp
