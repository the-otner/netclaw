---
name: aruba-central-monitoring
description: "HPE Aruba Networking Central device health monitoring — fleet-wide device inventory, CPU/memory health, interface status, client tracking, events and alerts (5 tools)"
user-invocable: true
metadata:
  { "openclaw": { "requires": { "bins": ["uv"], "env": ["ARUBA_CENTRAL_BASE_URL", "ARUBA_CENTRAL_TOKEN"] } } }
---

# HPE Aruba Networking Central — Device Health Monitoring

Monitor your entire Aruba network estate through the Aruba Central MCP server. Query fleet-wide device inventory (switches, APs, gateways), retrieve per-device CPU and memory health, inspect interface/port status and error counters, track connected clients, and retrieve events and alerts with severity filtering.

## MCP Server

| Field | Value |
|-------|-------|
| **Location** | `mcp-servers/aruba-central-mcp/server.py` (bundled with NetClaw) |
| **Transport** | stdio (FastMCP) |
| **Python** | 3.12+ |
| **Protocol** | HTTPS REST → Aruba Central API Gateway |
| **Dependencies** | `fastmcp`, `httpx`, `python-dotenv` |
| **Install** | `pip install -r mcp-servers/aruba-central-mcp/requirements.txt` |
| **Entry Point** | `uv run --with fastmcp fastmcp run mcp-servers/aruba-central-mcp/server.py` |
| **Retry Logic** | Automatic retry on 429 (rate limit) and transient connection errors (3 attempts) |

## Authentication

Aruba Central uses OAuth2 Bearer token authentication via the HPE GreenLake API Gateway:

1. Log in to **HPE GreenLake** (`https://common.cloud.hpe.com`)
2. Navigate to **Account Home → API Gateway**
3. Under **System Apps & Tokens**, create or select a System App
4. Generate an **Access Token** (valid for 2 hours by default)
5. Note your **API Gateway base URL** for your region (US-East, EU, APAC, etc.)
6. Store credentials in `.env`: `ARUBA_CENTRAL_BASE_URL` and `ARUBA_CENTRAL_TOKEN`

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ARUBA_CENTRAL_BASE_URL` | Yes | API Gateway base URL (e.g., `https://apigw-prod2.central.arubanetworks.com`) |
| `ARUBA_CENTRAL_TOKEN` | Yes | OAuth2 access_token from HPE GreenLake API Gateway |

---

## Tools (5)

### Device Inventory (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_get_devices` | `device_type` (all/switch/ap/gateway), `limit` (default 100), `offset` (default 0) | List all managed devices with model, serial, firmware version, IP, site, group, and operational status |

### Device Health (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_get_device_health` | `serial` (required) | Get CPU utilization (%), memory utilization (%), uptime, status, firmware version, and site/group for a specific device by serial number |

### Interface Monitoring (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_get_interfaces` | `serial` (required), `limit` (default 100), `offset` (default 0) | Get all port/interface status, speed, duplex, VLAN, and error/drop counters for a switch |

### Client Tracking (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_get_clients` | `serial` (optional), `client_type` (all/wired/wireless), `limit` (default 100), `offset` (default 0) | List connected clients with MAC, IP, VLAN, AP/switch association, signal strength, and connection type |

### Event Monitoring (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_get_events` | `severity` (all/critical/major/minor/info), `device_type` (all/switch/ap/gateway), `limit` (default 100), `offset` (default 0) | Retrieve events and alerts with severity, device serial, description, and timestamp |

---

## Aruba Central API Endpoints

| Method | Endpoint | Tool |
|--------|----------|------|
| GET | `/platform/device_inventory/v1/devices` | `aruba_get_devices` (all types) |
| GET | `/monitoring/v2/switches` | `aruba_get_devices` (switch filter) |
| GET | `/monitoring/v2/aps` | `aruba_get_devices` (ap filter) |
| GET | `/monitoring/v2/gateways` | `aruba_get_devices` (gateway filter) |
| GET | `/monitoring/v2/switches/{serial}` | `aruba_get_device_health` |
| GET | `/monitoring/v2/gateways/{serial}` | `aruba_get_device_health` (gateway fallback) |
| GET | `/monitoring/v1/switches/{serial}/ports` | `aruba_get_interfaces` |
| GET | `/monitoring/v2/clients` | `aruba_get_clients` |
| GET | `/monitoring/v1/logs/events` | `aruba_get_events` |

---

## Workflows

### 1. Aruba Fleet Discovery
```
aruba_get_devices(device_type="all") → inventory all managed devices (switches, APs, gateways)
→ Group by site and device type
→ Cross-reference with NetBox/Nautobot → flag discrepancies (missing, mismatched model/serial)
→ GAIT
```

### 2. Device Health Dashboard
```
aruba_get_devices(device_type="all") → identify all devices
→ aruba_get_device_health(serial) for each device → collect CPU, memory, status
→ Flag devices: status != "Up", cpu_utilization > 80%, mem_utilization > 85%
→ Severity-sort findings → GAIT
```

### 3. Interface Audit
```
aruba_get_devices(device_type="switch") → get switch inventory
→ aruba_get_interfaces(serial) for target switches
→ Identify: admin-down ports, error counters > threshold, speed mismatches, duplex mismatches
→ Cross-reference with NetBox cabling data → flag unexpected link states
→ GAIT
```

### 4. Client Capacity Check
```
aruba_get_clients(client_type="wireless") → count wireless clients per AP
→ aruba_get_clients(client_type="wired") → count wired clients per switch
→ Flag APs/switches approaching client capacity thresholds
→ Cross-reference signal strength for poor-RSSI clients (< -75 dBm)
→ GAIT
```

### 5. Event Triage
```
aruba_get_events(severity="critical") → pull all critical events
→ Group by device serial and event type
→ Correlate with aruba_get_device_health for affected devices
→ Cross-reference with nvd-cve for firmware CVEs on affected devices
→ Escalate per severity matrix → GAIT
```

### 6. Aruba Network Health Check
```
aruba_get_devices(device_type="all") → baseline inventory
→ aruba_get_events(severity="critical") + aruba_get_events(severity="major")
→ aruba_get_device_health(serial) for devices with active alerts
→ aruba_get_interfaces(serial) for devices with interface-related events
→ Consolidate findings → severity-sort → GAIT
```

---

## Integration with Other Skills

| Skill | Integration |
|-------|-------------|
| **aruba-central-troubleshoot** | Escalate from health alerts to on-device ping/traceroute for L3 connectivity issues |
| **aruba-central-routing** | Escalate from gateway health alerts to BGP/OSPF neighbor and routing table analysis |
| **aruba-central-security** | Correlate device health with ACL, AAA, and firewall policy audits |
| **netbox-reconcile** | Cross-reference Aruba Central device inventory (model, serial, IP) against NetBox DCIM/IPAM |
| **nautobot-sot** | Validate Aruba device IP addresses and prefixes in Nautobot source of truth |
| **nvd-cve** | Scan firmware versions from `aruba_get_devices` against NVD CVE database |
| **servicenow-change-workflow** | All remediation actions gated behind ServiceNow Change Requests |
| **gait-session-tracking** | Every inventory query, health check, and event pull must be logged in GAIT |
| **slack-network-alerts** | Format critical/major Aruba events for `#netclaw-alerts` Slack channel |
| **pyats-network** | Aruba Central MCP for HPE Aruba fleet; pyATS MCP for Cisco devices — unified monitoring |

---

## Guardrails

- **Always call `aruba_get_devices` first** — verify Aruba Central is reachable and devices are managed before deeper queries
- **Check events before changes** — call `aruba_get_events(severity="critical")` to identify active issues before any configuration operations
- **Validate serial numbers** — device serial must be alphanumeric, 4–20 characters (e.g. `CN12345678`)
- **Respect pagination limits** — use `limit` (max 1000) and `offset` for large fleets; do not attempt to fetch all devices in a single call if fleet exceeds 1000
- **Token expiry awareness** — Aruba Central tokens expire after ~2 hours; if 401 errors occur, refresh the token and update `ARUBA_CENTRAL_TOKEN`
- **Rate limiting** — the MCP server retries on 429 automatically (up to 3 attempts); if rate limiting persists, slow down fleet-wide parallel queries
- **Cross-reference inventory** — always compare Aruba Central device data with NetBox/Nautobot to detect source-of-truth drift
- **Record in GAIT** — every inventory query, health check, interface audit, client count, and event pull must be logged
