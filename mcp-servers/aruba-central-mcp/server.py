#!/usr/bin/env python3
"""
Aruba Central MCP Server — HPE Aruba Networking Central API for NetClaw

Exposes 16 tools via FastMCP/stdio for Aruba Networking Central management:
  Device Inventory & Health (5): aruba_get_devices, aruba_get_device_health,
                                  aruba_get_interfaces, aruba_get_clients,
                                  aruba_get_events
  Routing Analysis (4):          aruba_get_bgp_neighbors, aruba_get_bgp_routes,
                                  aruba_get_ospf_neighbors, aruba_get_ospf_lsdb
  Troubleshooting (3):           aruba_run_ping, aruba_run_traceroute,
                                  aruba_get_routing_table
  Security Audit (4):            aruba_get_acls, aruba_get_aaa_config,
                                  aruba_get_firewall_policies,
                                  aruba_get_firmware_compliance

Authentication: Bearer token (ARUBA_CENTRAL_TOKEN env var)
Base URL: ARUBA_CENTRAL_BASE_URL env var
  e.g. https://apigw-prod2.central.arubanetworks.com   (US-East)
       https://apigw-uswest4.central.arubanetworks.com  (US-West)
       https://apigw-eucentral3.central.arubanetworks.com (EU)
       https://apigw-apnortheast.central.arubanetworks.com (APAC)
       https://apigw-cacentral.central.arubanetworks.com  (Canada)
"""

import asyncio
import atexit
import ipaddress
import json
import logging
import os
import re
from typing import Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("ARUBA_CENTRAL_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("ARUBA_CENTRAL_TOKEN", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("aruba-central-mcp")

# ---------------------------------------------------------------------------
# HTTP client — lazy-initialised, shared across requests
# ---------------------------------------------------------------------------
_client: Optional[httpx.AsyncClient] = None

_MAX_RETRIES = 3
_RETRY_BACKOFF = [1, 2, 4]  # seconds


def _get_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first call."""
    global _client
    if _client is None:
        if not BASE_URL:
            raise RuntimeError(
                "ARUBA_CENTRAL_BASE_URL is not set. "
                "Export it or add it to .env (e.g. https://apigw-prod2.central.arubanetworks.com)."
            )
        if not TOKEN:
            raise RuntimeError(
                "ARUBA_CENTRAL_TOKEN is not set. "
                "Export it or add it to .env (HPE GreenLake OAuth2 access_token)."
            )
        _client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )
        logger.info("Aruba Central HTTP client initialised — base_url=%s", BASE_URL)
    return _client


async def _close_client() -> None:
    """Close the shared HTTP client and release connections."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("Aruba Central HTTP client closed")


def _sync_close_client() -> None:
    """Synchronous atexit handler — cleanly closes the async client."""
    global _client
    if _client is not None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_close_client())
            else:
                loop.run_until_complete(_close_client())
        except Exception:
            pass


atexit.register(_sync_close_client)


# ---------------------------------------------------------------------------
# Input validators
# ---------------------------------------------------------------------------
_SERIAL_RE = re.compile(r"^[A-Z0-9]{4,20}$", re.IGNORECASE)


def _validate_serial(serial: str) -> Optional[str]:
    """Return an error message if the serial is invalid, else None."""
    if not serial or not serial.strip():
        return "Serial number cannot be empty."
    if not _SERIAL_RE.match(serial.strip()):
        return (
            f"Invalid serial number format: '{serial}'. "
            "Expected alphanumeric, 4–20 characters (e.g. 'CN12345678')."
        )
    return None


def _validate_cidr(prefix: str) -> Optional[str]:
    """Return an error message if the prefix is not a valid CIDR, else None."""
    try:
        ipaddress.ip_network(prefix, strict=False)
        return None
    except ValueError as exc:
        return f"Invalid CIDR prefix '{prefix}': {exc}"


def _validate_ip(addr: str) -> Optional[str]:
    """Return an error message if addr is not a valid IP address, else None."""
    try:
        ipaddress.ip_address(addr)
        return None
    except ValueError:
        return f"Invalid IP address: '{addr}'"


# ---------------------------------------------------------------------------
# HTTP helpers with retry on 429 / transient failures
# ---------------------------------------------------------------------------

def _http_error_json(exc: httpx.HTTPStatusError) -> str:
    """Convert an HTTPStatusError into a JSON error string."""
    status = exc.response.status_code
    if status == 401:
        return json.dumps({"error": "Authentication failed (401). Check ARUBA_CENTRAL_TOKEN."})
    if status == 403:
        return json.dumps({"error": "Access denied (403). Token lacks the required scope/permissions."})
    if status == 404:
        return json.dumps({"error": "Resource not found (404). Check the device serial number or API path."})
    if status == 429:
        return json.dumps({"error": "Rate limited (429). All retry attempts exhausted. Try again later."})
    body = exc.response.text[:300] if exc.response.text else ""
    return json.dumps({"error": f"HTTP {status}: {body}"})


async def _request(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
) -> dict:
    """Send an HTTP request with retry logic for 429 and transient errors.

    Args:
        method: 'GET' or 'POST'
        path: API path (appended to BASE_URL)
        params: Query parameters for GET
        body: JSON body for POST
    """
    client = _get_client()
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        try:
            if method == "GET":
                resp = await client.get(path, params=params or {})
            else:
                resp = await client.post(path, json=body or {})

            if resp.status_code == 429:
                retry_after = int(
                    resp.headers.get(
                        "Retry-After",
                        _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)],
                    )
                )
                logger.warning(
                    "Rate limited (429). Retrying in %ds (attempt %d/%d)",
                    retry_after,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                await asyncio.sleep(retry_after)
                continue

            resp.raise_for_status()
            return resp.json()

        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                logger.warning(
                    "Transient error (%s), retrying in %ds (attempt %d/%d): %s",
                    type(exc).__name__,
                    delay,
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                )
                await asyncio.sleep(delay)
                continue
            raise

        except httpx.HTTPStatusError:
            raise  # propagate 4xx/5xx immediately (except 429 handled above)

    # If we exhausted retries on 429:
    if last_exc:
        raise last_exc
    raise RuntimeError("Request failed after max retries")


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------
mcp = FastMCP("aruba-central-mcp")


# ── Device Inventory & Health ───────────────────────────────────────────────

@mcp.tool()
async def aruba_get_devices(
    device_type: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> str:
    """List devices managed in Aruba Central with model, serial, version, and status.

    Args:
        device_type: Filter by device type — 'all', 'switch', 'ap', or 'gateway'. Default: 'all'.
        limit: Maximum number of devices to return (1–1000). Default: 100.
        offset: Pagination offset. Default: 0.
    """
    limit = min(max(1, limit), 1000)
    offset = max(0, offset)

    valid_types = ("all", "switch", "ap", "gateway")
    if device_type not in valid_types:
        return json.dumps(
            {"error": f"Invalid device_type '{device_type}'. Must be one of: {', '.join(valid_types)}."}
        )

    path_map = {
        "all": "/platform/device_inventory/v1/devices",
        "switch": "/monitoring/v2/switches",
        "ap": "/monitoring/v2/aps",
        "gateway": "/monitoring/v2/gateways",
    }

    try:
        data = await _request("GET", path_map[device_type], params={"limit": limit, "offset": offset})
        # Aruba Central returns items under different keys depending on endpoint
        devices = (
            data.get("devices")
            or data.get("switches")
            or data.get("aps")
            or data.get("gateways")
            or []
        )
        total = data.get("total", len(devices))
        logger.info("aruba_get_devices: returned %d devices (total=%s)", len(devices), total)
        return json.dumps(
            {"devices": devices, "count": len(devices), "total": total, "offset": offset},
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_devices")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_device_health(serial: str) -> str:
    """Get CPU utilization, memory utilization, and operational status for a device.

    Args:
        serial: Device serial number (e.g. 'CN12345678').
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})

    serial = serial.strip().upper()
    try:
        # Try switch endpoint first; if 404, try gateway
        try:
            data = await _request("GET", f"/monitoring/v2/switches/{serial}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                data = await _request("GET", f"/monitoring/v2/gateways/{serial}")
            else:
                raise

        health = {
            "serial": serial,
            "name": data.get("name") or data.get("hostname", ""),
            "model": data.get("model", ""),
            "status": data.get("status", ""),
            "firmware_version": data.get("firmware_version", ""),
            "cpu_utilization": data.get("cpu_utilization"),
            "mem_utilization": data.get("mem_utilization"),
            "uptime_seconds": data.get("uptime_seconds"),
            "ip_address": data.get("ip_address", ""),
            "site": data.get("site", ""),
            "group_name": data.get("group_name", ""),
        }
        logger.info("aruba_get_device_health: serial=%s status=%s cpu=%s mem=%s",
                    serial, health["status"], health["cpu_utilization"], health["mem_utilization"])
        return json.dumps({"health": health}, indent=2)
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_device_health")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_interfaces(serial: str, limit: int = 100, offset: int = 0) -> str:
    """Get interface/port status, speed, duplex, and error counters for a switch.

    Args:
        serial: Switch serial number (e.g. 'CN12345678').
        limit: Maximum number of ports to return (1–500). Default: 100.
        offset: Pagination offset. Default: 0.
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})

    serial = serial.strip().upper()
    limit = min(max(1, limit), 500)
    offset = max(0, offset)

    try:
        data = await _request(
            "GET",
            f"/monitoring/v1/switches/{serial}/ports",
            params={"limit": limit, "offset": offset},
        )
        ports = data.get("ports", data.get("interfaces", []))
        total = data.get("total", len(ports))
        logger.info("aruba_get_interfaces: serial=%s returned %d ports", serial, len(ports))
        return json.dumps(
            {"serial": serial, "ports": ports, "count": len(ports), "total": total},
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_interfaces")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_clients(
    serial: Optional[str] = None,
    client_type: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> str:
    """List connected clients (wired and wireless) with IP, MAC, VLAN, and signal info.

    Args:
        serial: Optional device serial to filter clients by AP/switch. Default: all devices.
        client_type: Filter by type — 'all', 'wired', or 'wireless'. Default: 'all'.
        limit: Maximum number of clients to return (1–1000). Default: 100.
        offset: Pagination offset. Default: 0.
    """
    if serial:
        err = _validate_serial(serial)
        if err:
            return json.dumps({"error": err})
        serial = serial.strip().upper()

    valid_types = ("all", "wired", "wireless")
    if client_type not in valid_types:
        return json.dumps(
            {"error": f"Invalid client_type '{client_type}'. Must be one of: {', '.join(valid_types)}."}
        )

    limit = min(max(1, limit), 1000)
    offset = max(0, offset)

    params: dict = {"limit": limit, "offset": offset}
    if serial:
        params["serial"] = serial
    if client_type != "all":
        params["client_type"] = client_type

    try:
        data = await _request("GET", "/monitoring/v2/clients", params=params)
        clients = data.get("clients", [])
        total = data.get("total", len(clients))
        logger.info("aruba_get_clients: returned %d clients (total=%s)", len(clients), total)
        return json.dumps(
            {"clients": clients, "count": len(clients), "total": total, "offset": offset},
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_clients")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_events(
    severity: str = "all",
    device_type: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> str:
    """Retrieve events and alerts from Aruba Central with severity and device details.

    Args:
        severity: Filter by severity — 'all', 'critical', 'major', 'minor', 'info'. Default: 'all'.
        device_type: Filter by device type — 'all', 'switch', 'ap', 'gateway'. Default: 'all'.
        limit: Maximum number of events to return (1–1000). Default: 100.
        offset: Pagination offset. Default: 0.
    """
    valid_severities = ("all", "critical", "major", "minor", "info")
    valid_types = ("all", "switch", "ap", "gateway")

    if severity not in valid_severities:
        return json.dumps(
            {"error": f"Invalid severity '{severity}'. Must be one of: {', '.join(valid_severities)}."}
        )
    if device_type not in valid_types:
        return json.dumps(
            {"error": f"Invalid device_type '{device_type}'. Must be one of: {', '.join(valid_types)}."}
        )

    limit = min(max(1, limit), 1000)
    offset = max(0, offset)

    params: dict = {"limit": limit, "offset": offset}
    if severity != "all":
        params["severity"] = severity
    if device_type != "all":
        params["device_type"] = device_type

    try:
        data = await _request("GET", "/monitoring/v1/logs/events", params=params)
        events = data.get("events", [])
        total = data.get("total", len(events))
        logger.info("aruba_get_events: returned %d events (severity=%s)", len(events), severity)
        return json.dumps(
            {"events": events, "count": len(events), "total": total, "offset": offset},
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_events")
        return json.dumps({"error": f"Unexpected error: {exc}"})


# ── Routing Analysis ────────────────────────────────────────────────────────

@mcp.tool()
async def aruba_get_bgp_neighbors(serial: str) -> str:
    """Get BGP peer table for an Aruba gateway — neighbor IPs, AS numbers, state, and prefix counts.

    Args:
        serial: Gateway serial number (e.g. 'CN12345678').
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})

    serial = serial.strip().upper()
    try:
        data = await _request("GET", f"/monitoring/v1/gateways/{serial}/bgp_peers")
        peers = data.get("bgp_peers", data.get("peers", []))
        logger.info("aruba_get_bgp_neighbors: serial=%s returned %d peers", serial, len(peers))
        return json.dumps(
            {"serial": serial, "bgp_peers": peers, "count": len(peers)},
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_bgp_neighbors")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_bgp_routes(
    serial: str,
    prefix: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    """Get the BGP RIB for an Aruba gateway — prefixes, next-hops, AS paths, and attributes.

    Supports 11-step BGP best-path analysis: prefer highest LOCAL_PREF, lowest AS_PATH length,
    origin code (i > e > ?), lowest MED, eBGP over iBGP, lowest IGP metric, oldest route,
    lowest router ID, lowest neighbor IP.

    Args:
        serial: Gateway serial number (e.g. 'CN12345678').
        prefix: Optional CIDR prefix to filter (e.g. '10.0.0.0/8'). Default: all BGP routes.
        limit: Maximum routes to return (1–1000). Default: 100.
        offset: Pagination offset. Default: 0.
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})
    if prefix:
        err = _validate_cidr(prefix)
        if err:
            return json.dumps({"error": err})

    serial = serial.strip().upper()
    limit = min(max(1, limit), 1000)
    offset = max(0, offset)

    params: dict = {"protocol": "bgp", "limit": limit, "offset": offset}
    if prefix:
        params["prefix"] = prefix

    try:
        data = await _request("GET", f"/monitoring/v1/gateways/{serial}/ip_routes", params=params)
        routes = data.get("routes", data.get("ip_routes", []))
        total = data.get("total", len(routes))
        logger.info("aruba_get_bgp_routes: serial=%s returned %d routes", serial, len(routes))
        return json.dumps(
            {"serial": serial, "routes": routes, "count": len(routes), "total": total, "offset": offset},
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_bgp_routes")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_ospf_neighbors(serial: str) -> str:
    """Get OSPF neighbor table for an Aruba gateway — neighbor IDs, states, areas, and interfaces.

    Args:
        serial: Gateway serial number (e.g. 'CN12345678').
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})

    serial = serial.strip().upper()
    try:
        data = await _request("GET", f"/monitoring/v1/gateways/{serial}/ospf_neighbors")
        neighbors = data.get("ospf_neighbors", data.get("neighbors", []))
        logger.info("aruba_get_ospf_neighbors: serial=%s returned %d neighbors", serial, len(neighbors))
        return json.dumps(
            {"serial": serial, "ospf_neighbors": neighbors, "count": len(neighbors)},
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_ospf_neighbors")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_ospf_lsdb(serial: str, area_id: Optional[str] = None) -> str:
    """Get the OSPF Link State Database (LSDB) for an Aruba gateway.

    Returns LSA types (Router-LSA type 1, Network-LSA type 2, Summary-LSA type 3/4,
    AS External-LSA type 5, NSSA External-LSA type 7) useful for topology analysis.

    Args:
        serial: Gateway serial number (e.g. 'CN12345678').
        area_id: Optional OSPF area ID to filter (e.g. '0.0.0.0' for area 0). Default: all areas.
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})

    serial = serial.strip().upper()
    params: dict = {}
    if area_id:
        params["area_id"] = area_id

    try:
        data = await _request(
            "GET",
            f"/monitoring/v1/gateways/{serial}/ospf_lsdb",
            params=params,
        )
        lsdb = data.get("ospf_lsdb", data.get("lsdb", []))
        logger.info("aruba_get_ospf_lsdb: serial=%s returned %d LSAs", serial, len(lsdb))
        return json.dumps(
            {"serial": serial, "lsdb": lsdb, "count": len(lsdb)},
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_ospf_lsdb")
        return json.dumps({"error": f"Unexpected error: {exc}"})


# ── Troubleshooting ────────────────────────────────────────────────────────

@mcp.tool()
async def aruba_run_ping(
    serial: str,
    destination: str,
    count: int = 5,
) -> str:
    """Run an on-device ping from an Aruba device to a destination IP.

    Args:
        serial: Device serial number to run ping from (e.g. 'CN12345678').
        destination: Destination IP address or hostname to ping.
        count: Number of ICMP echo requests (1–100). Default: 5.
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})

    serial = serial.strip().upper()
    count = min(max(1, count), 100)

    # Accept both IP and hostname for destination
    if not destination or not destination.strip():
        return json.dumps({"error": "Destination IP/hostname cannot be empty."})

    try:
        data = await _request(
            "POST",
            "/troubleshooting/v1/ping",
            body={"serial": serial, "destination": destination.strip(), "count": count},
        )
        logger.info("aruba_run_ping: serial=%s -> %s", serial, destination)
        return json.dumps({"serial": serial, "destination": destination, "result": data}, indent=2)
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_run_ping")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_run_traceroute(serial: str, destination: str) -> str:
    """Run an on-device traceroute from an Aruba device to a destination IP.

    Args:
        serial: Device serial number to run traceroute from (e.g. 'CN12345678').
        destination: Destination IP address or hostname.
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})

    serial = serial.strip().upper()

    if not destination or not destination.strip():
        return json.dumps({"error": "Destination IP/hostname cannot be empty."})

    try:
        data = await _request(
            "POST",
            "/troubleshooting/v1/traceroute",
            body={"serial": serial, "destination": destination.strip()},
        )
        logger.info("aruba_run_traceroute: serial=%s -> %s", serial, destination)
        return json.dumps({"serial": serial, "destination": destination, "result": data}, indent=2)
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_run_traceroute")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_routing_table(
    serial: str,
    protocol: Optional[str] = None,
    prefix: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> str:
    """Get the full routing table for an Aruba gateway — all routes with next-hop and protocol.

    Args:
        serial: Gateway serial number (e.g. 'CN12345678').
        protocol: Optional filter by protocol — 'bgp', 'ospf', 'static', 'connected'. Default: all.
        prefix: Optional CIDR prefix to look up (e.g. '192.168.1.0/24'). Default: all routes.
        limit: Maximum routes to return (1–1000). Default: 200.
        offset: Pagination offset. Default: 0.
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})
    if prefix:
        err = _validate_cidr(prefix)
        if err:
            return json.dumps({"error": err})

    serial = serial.strip().upper()
    limit = min(max(1, limit), 1000)
    offset = max(0, offset)

    valid_protocols = ("bgp", "ospf", "static", "connected")
    if protocol and protocol not in valid_protocols:
        return json.dumps(
            {"error": f"Invalid protocol '{protocol}'. Must be one of: {', '.join(valid_protocols)}."}
        )

    params: dict = {"limit": limit, "offset": offset}
    if protocol:
        params["protocol"] = protocol
    if prefix:
        params["prefix"] = prefix

    try:
        data = await _request("GET", f"/monitoring/v1/gateways/{serial}/ip_routes", params=params)
        routes = data.get("routes", data.get("ip_routes", []))
        total = data.get("total", len(routes))
        logger.info("aruba_get_routing_table: serial=%s returned %d routes", serial, len(routes))
        return json.dumps(
            {"serial": serial, "routes": routes, "count": len(routes), "total": total, "offset": offset},
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_routing_table")
        return json.dumps({"error": f"Unexpected error: {exc}"})


# ── Security Audit ──────────────────────────────────────────────────────────

@mcp.tool()
async def aruba_get_acls(serial: str) -> str:
    """Get ACL (Access Control List) configuration for an Aruba gateway.

    Returns named ACLs with rules, source/destination networks, ports, and permit/deny actions.
    Use for security posture review, CIS benchmark analysis, and change auditing.

    Args:
        serial: Gateway serial number (e.g. 'CN12345678').
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})

    serial = serial.strip().upper()
    try:
        data = await _request("GET", f"/monitoring/v1/gateways/{serial}/acl_config")
        acls = data.get("acl_config", data.get("acls", []))
        logger.info("aruba_get_acls: serial=%s returned %d ACLs", serial, len(acls))
        return json.dumps({"serial": serial, "acls": acls, "count": len(acls)}, indent=2)
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_acls")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_aaa_config(serial: str) -> str:
    """Get AAA (Authentication, Authorization, Accounting) configuration for an Aruba device.

    Returns RADIUS servers, TACACS+ servers, dot1x settings, MAC authentication,
    and server group configurations. Use for AAA posture audit and ISE NAD verification.

    Args:
        serial: Device serial number (e.g. 'CN12345678').
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})

    serial = serial.strip().upper()
    try:
        data = await _request("GET", f"/configuration/v1/devices/{serial}/aaa")
        aaa = data.get("aaa", data)
        logger.info("aruba_get_aaa_config: serial=%s", serial)
        return json.dumps({"serial": serial, "aaa_config": aaa}, indent=2)
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_aaa_config")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_firewall_policies(serial: str) -> str:
    """Get firewall policies and security zones for an Aruba gateway.

    Returns stateful firewall rules, zone-based policies, application IDs, and session limits.
    Use for firewall posture audit and compliance reviews.

    Args:
        serial: Gateway serial number (e.g. 'CN12345678').
    """
    err = _validate_serial(serial)
    if err:
        return json.dumps({"error": err})

    serial = serial.strip().upper()
    try:
        data = await _request("GET", f"/monitoring/v1/gateways/{serial}/firewall_policy")
        policies = data.get("firewall_policy", data.get("policies", []))
        logger.info("aruba_get_firewall_policies: serial=%s returned %d policies", serial, len(policies) if isinstance(policies, list) else 1)
        return json.dumps({"serial": serial, "firewall_policies": policies}, indent=2)
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_firewall_policies")
        return json.dumps({"error": f"Unexpected error: {exc}"})


@mcp.tool()
async def aruba_get_firmware_compliance(
    device_type: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> str:
    """Get firmware versions and compliance status for all Aruba devices.

    Cross-reference running firmware versions against recommended/required versions
    to identify devices running outdated or vulnerable software. Use with nvd-cve
    skill to correlate firmware versions against known CVEs.

    Args:
        device_type: Filter by type — 'all', 'switch', 'ap', 'gateway'. Default: 'all'.
        limit: Maximum devices to return (1–1000). Default: 100.
        offset: Pagination offset. Default: 0.
    """
    valid_types = ("all", "switch", "ap", "gateway")
    if device_type not in valid_types:
        return json.dumps(
            {"error": f"Invalid device_type '{device_type}'. Must be one of: {', '.join(valid_types)}."}
        )

    limit = min(max(1, limit), 1000)
    offset = max(0, offset)

    params: dict = {"limit": limit, "offset": offset}
    if device_type != "all":
        params["device_type"] = device_type

    try:
        data = await _request("GET", "/firmware/v1/devices", params=params)
        devices = data.get("devices", [])
        total = data.get("total", len(devices))
        logger.info(
            "aruba_get_firmware_compliance: returned %d devices (total=%s)", len(devices), total
        )
        return json.dumps(
            {"devices": devices, "count": len(devices), "total": total, "offset": offset},
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        return _http_error_json(exc)
    except httpx.ConnectError as exc:
        return json.dumps({"error": f"Connection failed: {exc}. Check ARUBA_CENTRAL_BASE_URL."})
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out after 30 seconds."})
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error in aruba_get_firmware_compliance")
        return json.dumps({"error": f"Unexpected error: {exc}"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
