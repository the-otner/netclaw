---
name: aruba-central-troubleshoot
description: "HPE Aruba Networking Central OSI-layer troubleshooting — on-device ping, on-device traceroute, full routing table analysis (3 tools)"
user-invocable: true
metadata:
  { "openclaw": { "requires": { "bins": ["uv"], "env": ["ARUBA_CENTRAL_BASE_URL", "ARUBA_CENTRAL_TOKEN"] } } }
---

# HPE Aruba Networking Central — OSI-Layer Troubleshooting

Diagnose connectivity, routing, and performance issues on Aruba networks using OSI-layer methodology. Execute on-device ping and traceroute directly from Aruba devices via the Central API, and retrieve the full routing table for Layer 3 path analysis.

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

## Authentication

| Variable | Required | Purpose |
|----------|----------|---------|
| `ARUBA_CENTRAL_BASE_URL` | Yes | API Gateway base URL (e.g., `https://apigw-prod2.central.arubanetworks.com`) |
| `ARUBA_CENTRAL_TOKEN` | Yes | OAuth2 access_token from HPE GreenLake API Gateway |

---

## Tools (3)

### Connectivity Testing (2 tools)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_run_ping` | `serial` (required), `destination` (required), `count` (default 5, max 100) | Run ICMP ping from an Aruba device to a destination IP/hostname — returns RTT min/avg/max and packet loss |
| `aruba_run_traceroute` | `serial` (required), `destination` (required) | Run hop-by-hop traceroute from an Aruba device to a destination — returns each hop IP, RTT, and FQDN |

### Routing Analysis (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_get_routing_table` | `serial` (required), `protocol` (optional: bgp/ospf/static/connected), `prefix` (optional CIDR), `limit` (default 200), `offset` (default 0) | Get the full IP routing table for an Aruba gateway with next-hop, metric, and protocol for each prefix |

---

## Aruba Central API Endpoints

| Method | Endpoint | Tool |
|--------|----------|------|
| POST | `/troubleshooting/v1/ping` | `aruba_run_ping` |
| POST | `/troubleshooting/v1/traceroute` | `aruba_run_traceroute` |
| GET | `/monitoring/v1/gateways/{serial}/ip_routes` | `aruba_get_routing_table` |

---

## OSI-Layer Troubleshooting Methodology

### L1 — Physical
```
aruba_get_device_health(serial) → check device status (Up/Down)
→ aruba_get_interfaces(serial) → check admin/oper state, link speed, error counters
→ Identify: interface down, excessive CRC errors, duplex mismatches
```

### L2 — Data Link
```
aruba_get_interfaces(serial) → check VLAN membership, STP state, port security
→ aruba_get_clients(serial) → verify MAC addresses learned on correct ports
→ Identify: VLAN mismatch, STP blocking, MAC flooding indicators
```

### L3 — Network
```
aruba_run_ping(serial, destination) → verify basic IP reachability
→ aruba_get_routing_table(serial) → check if destination prefix is present
→ aruba_get_routing_table(serial, protocol="static") → check for missing static routes
→ Identify: missing route, wrong next-hop, black-hole route
```

### L3 — Path Analysis
```
aruba_run_traceroute(serial, destination) → identify where path fails
→ aruba_get_routing_table(serial) → correlate routing table with hop-by-hop path
→ Identify: asymmetric routing, sub-optimal path, unexpected next-hop at each hop
```

### L4–L7 — Transport / Application
```
aruba_run_ping(serial, destination, count=20) → check for intermittent packet loss
→ aruba_get_firewall_policies(serial) → check if ACL/firewall is blocking traffic
→ aruba_get_acls(serial) → verify ACL permits application traffic on required ports
→ Identify: ACL deny, firewall block, application port mismatch
```

---

## Workflows

### 1. Basic Connectivity Verification
```
aruba_run_ping(serial, destination) → verify ICMP reachability
→ If ping fails: aruba_get_routing_table(serial) → check for route to destination
→ If route missing: aruba_get_bgp_neighbors(serial) / aruba_get_ospf_neighbors(serial)
  → check if routing protocol adjacencies are up
→ If route present but ping fails: aruba_get_acls(serial) / aruba_get_firewall_policies(serial)
  → check if traffic is being blocked
→ GAIT
```

### 2. Path Tracing and Hop Analysis
```
aruba_run_traceroute(serial, destination) → trace full path
→ Identify first hop that does not respond (timeout = *) — this is the likely failure point
→ aruba_get_routing_table(serial) → verify expected path matches actual hops
→ For each intermediate Aruba gateway in the path: aruba_run_ping(gateway_serial, destination)
  → isolate which segment is broken
→ GAIT
```

### 3. Routing Table Audit
```
aruba_get_routing_table(serial, protocol="bgp") → check BGP-learned routes
→ aruba_get_routing_table(serial, protocol="ospf") → check OSPF-learned routes
→ aruba_get_routing_table(serial, protocol="static") → audit static routes for correctness
→ aruba_get_routing_table(serial, protocol="connected") → verify connected prefixes
→ Check for: duplicate prefixes across protocols (recursive routing), default route presence,
  unexpected next-hops, missing critical prefixes
→ GAIT
```

### 4. Application Connectivity Troubleshoot
```
User reports: "Cannot reach application at 10.1.2.3 from site XYZ"
Step 1 (L3): aruba_run_ping(gateway_serial, "10.1.2.3") → verify L3 reachability
Step 2 (L3): aruba_get_routing_table(gateway_serial, prefix="10.1.2.3/32")
             → confirm route exists with correct next-hop
Step 3 (L4): aruba_get_acls(gateway_serial) → check for permit/deny on app port
Step 4 (L4): aruba_get_firewall_policies(gateway_serial) → check stateful firewall
Step 5 (L7): If all above pass → escalate to application team / packet capture
→ GAIT
```

### 5. Multi-Site Path Verification
```
For each Aruba gateway serial in affected sites:
  → aruba_run_ping(serial, hub_gateway_ip) → verify spoke-to-hub reachability
  → aruba_run_traceroute(serial, hub_gateway_ip) → capture hop count and RTT
  → aruba_get_routing_table(serial, prefix=hub_prefix) → verify route to hub
→ Compare results across sites → identify asymmetric RTT, inconsistent hop counts
→ GAIT
```

---

## Integration with Other Skills

| Skill | Integration |
|-------|-------------|
| **aruba-central-monitoring** | Start here for health checks; escalate to troubleshoot when health alerts fire |
| **aruba-central-routing** | Escalate from routing table gaps to full BGP/OSPF neighbor and LSDB analysis |
| **aruba-central-security** | Escalate from reachability failure to ACL, firewall policy, and AAA audit |
| **pyats-network** | For deeper IOS-XE/NX-OS troubleshooting on the upstream network path |
| **servicenow-change-workflow** | Gate any corrective configuration changes behind a ServiceNow CR |
| **gait-session-tracking** | All ping/traceroute executions and routing table queries must be logged in GAIT |
| **te-network-monitoring** | Correlate Aruba on-device traceroute with ThousandEyes synthetic path data |
| **grafana-observability** | Cross-reference Grafana metrics (packet loss, RTT) with on-device troubleshooting results |

---

## Guardrails

- **Always run `aruba_run_ping` before `aruba_run_traceroute`** — confirm basic reachability before spending time on hop-by-hop trace
- **Never guess the failure layer** — follow the OSI methodology top-down (L1 → L2 → L3 → L4-7); do not skip layers
- **Validate serial numbers** — device serial must be alphanumeric, 4–20 characters (e.g. `CN12345678`)
- **Limit ping count** — use `count=5` for quick checks; use `count=20` for intermittent loss detection; never exceed 100
- **Validate CIDR prefixes** — when using `prefix` parameter in `aruba_get_routing_table`, provide valid CIDR notation (e.g. `10.0.0.0/24`)
- **On-device troubleshooting is read-only** — ping and traceroute do not change device state; no ServiceNow CR required
- **Rate limiting awareness** — avoid running concurrent on-device troubleshooting across many devices simultaneously; space out requests
- **Escalate to ServiceNow** — any corrective actions (route additions, ACL changes) require a ServiceNow CR in Implement state
- **Record in GAIT** — every connectivity test, path trace, and routing table query must be logged
