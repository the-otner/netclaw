---
name: aruba-central-security
description: "HPE Aruba Networking Central security audit — ACL review, AAA/RADIUS/TACACS+ config, firewall policy analysis, firmware compliance and CVE scanning (4 tools)"
user-invocable: true
metadata:
  { "openclaw": { "requires": { "bins": ["uv"], "env": ["ARUBA_CENTRAL_BASE_URL", "ARUBA_CENTRAL_TOKEN"] } } }
---

# HPE Aruba Networking Central — Security Audit

Audit the security posture of your Aruba network estate. Review Access Control Lists (ACLs), inspect AAA authentication servers (RADIUS and TACACS+) for ISE NAD verification, analyze stateful firewall policies, and audit firmware compliance across the fleet for CVE exposure.

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

## Tools (4)

### Access Control (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_get_acls` | `serial` (required) | Get ACL configuration for an Aruba gateway — named ACLs with rules, source/destination networks, protocol, port ranges, and permit/deny actions |

### Authentication Audit (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_get_aaa_config` | `serial` (required) | Get AAA configuration — RADIUS servers (IP, port, shared-secret present/absent), TACACS+ servers, dot1x authentication, MAC authentication, server-group assignments, and CoA configuration |

### Firewall Policy (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_get_firewall_policies` | `serial` (required) | Get stateful firewall policies and security zones for an Aruba gateway — zone-based rules, application-layer inspection, session limits, and deny rules |

### Firmware Compliance (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `aruba_get_firmware_compliance` | `device_type` (all/switch/ap/gateway), `limit` (default 100), `offset` (default 0) | Get firmware versions for all devices and their compliance status — current version, recommended version, compliance state (compliant/non-compliant) |

---

## Aruba Central API Endpoints

| Method | Endpoint | Tool |
|--------|----------|------|
| GET | `/monitoring/v1/gateways/{serial}/acl_config` | `aruba_get_acls` |
| GET | `/configuration/v1/devices/{serial}/aaa` | `aruba_get_aaa_config` |
| GET | `/monitoring/v1/gateways/{serial}/firewall_policy` | `aruba_get_firewall_policies` |
| GET | `/firmware/v1/devices` | `aruba_get_firmware_compliance` |

---

## Security Audit Workflows

### 1. ACL Security Review
```
aruba_get_acls(serial) → retrieve all named ACLs on the gateway
→ Analyze each ACL for:
   - Overly permissive rules (e.g., "permit any any")
   - Missing explicit deny at the end of critical ACLs
   - Rules allowing traffic on known risky ports (Telnet/23, FTP/21, SNMP/161)
   - ACL ordering issues (deny before permit for critical traffic)
→ Compare against organizational security policy baseline
→ servicenow-change-workflow → gate any ACL modifications
→ GAIT
```

### 2. AAA / ISE NAD Verification
```
aruba_get_aaa_config(serial) → retrieve AAA configuration
→ Verify RADIUS servers match authorized ISE policy service nodes (PSNs)
→ Check: correct RADIUS IP addresses, correct authentication port (1812), correct accounting port (1813)
→ Verify: shared-secret is configured (non-empty), server group assignments
→ Verify: CoA (Change of Authorization) is enabled for ISE posture enforcement
→ Cross-reference with ise-posture-audit skill for ISE NAD device list
→ GAIT
```

### 3. Firewall Policy Audit
```
aruba_get_firewall_policies(serial) → retrieve all firewall policies
→ Review zone-based policies:
   - Verify trust/untrust zone separation is correct
   - Check for "permit any" between security zones
   - Identify missing application-layer inspection rules
   - Check session limits (too high = DoS risk, too low = legitimate traffic drops)
→ Correlate with aruba_get_acls for interface-level ACL + firewall policy overlap
→ GAIT
```

### 4. Firmware CVE Scanning
```
aruba_get_firmware_compliance(device_type="all") → get all device firmware versions
→ Group devices by firmware version
→ For each unique firmware version: nvd-cve skill → search NVD for "Aruba" + version
→ Identify critical (CVSS >= 9.0) and high (CVSS >= 7.0) CVEs affecting running firmware
→ aruba_get_events(severity="critical") → correlate with active exploit indicators
→ Prioritize by: CVSS score, exploit availability, device role (gateway > AP > switch)
→ Create ServiceNow CR for firmware upgrades on affected devices
→ GAIT
```

### 5. CIS Benchmark Audit
```
aruba_get_aaa_config(serial) → verify:
  CIS 1.1: TACACS+ or RADIUS authentication for admin access (no local-only auth)
  CIS 1.2: Enable/privilege mode authentication configured
  CIS 2.1: SSH enabled (Telnet disabled)
  CIS 3.1: NTP configured and synchronized
→ aruba_get_acls(serial) → verify:
  CIS 4.1: Management ACL restricts access to authorized admin IPs
  CIS 4.2: SNMP ACL restricts read/write access
→ aruba_get_firewall_policies(serial) → verify:
  CIS 5.1: Stateful inspection enabled
  CIS 5.2: DoS protection thresholds set
→ GAIT
```

### 6. Security Posture Baseline
```
aruba_get_devices(device_type="all") → fleet inventory
→ aruba_get_firmware_compliance(device_type="all") → identify non-compliant firmware
→ For each gateway serial:
   → aruba_get_aaa_config(serial) → AAA posture
   → aruba_get_acls(serial) → ACL posture
   → aruba_get_firewall_policies(serial) → firewall posture
→ Score each device against CIS benchmarks and organizational policy
→ Generate prioritized remediation plan → servicenow-change-workflow
→ GAIT
```

### 7. Post-Change Security Verification
```
ServiceNow CR completed
→ aruba_get_acls(serial) → verify ACL changes applied correctly
→ aruba_get_aaa_config(serial) → verify AAA changes (new RADIUS server, etc.)
→ aruba_get_firewall_policies(serial) → verify firewall policy changes
→ aruba_run_ping + aruba_run_traceroute → verify legitimate traffic still flows
→ aruba_get_events(severity="critical") → check for new alerts post-change
→ Update ServiceNow CR with verification results → close CR
→ GAIT
```

---

## Integration with Other Skills

| Skill | Integration |
|-------|-------------|
| **aruba-central-monitoring** | Start with device health and events; escalate to security audit when security events fire |
| **aruba-central-troubleshoot** | After ACL/firewall changes, use ping/traceroute to verify traffic flow not broken |
| **aruba-central-routing** | Correlate routing anomalies with firewall policy and ACL misconfiguration |
| **nvd-cve** | Scan Aruba firmware versions from `aruba_get_firmware_compliance` against NVD CVE database |
| **ise-posture-audit** | Cross-reference Aruba AAA RADIUS server IPs with ISE PSN list for NAD verification |
| **ise-incident-response** | Correlate Aruba MAC authentication failures with ISE endpoint quarantine actions |
| **netbox-reconcile** | Verify Aruba device management IPs and segments match NetBox IPAM data |
| **servicenow-change-workflow** | All ACL, AAA, and firewall policy changes require ServiceNow CR in Implement state |
| **gait-session-tracking** | Every ACL review, AAA audit, firewall query, and firmware compliance check must be logged |

---

## Guardrails

- **Security audits are read-only** — all four tools only read configuration; no changes are applied via this MCP server
- **Gate all changes behind ServiceNow** — any remediation (ACL modification, RADIUS server change, firmware upgrade) requires a ServiceNow CR in Implement state before execution
- **Validate serial numbers** — device serial must be alphanumeric, 4–20 characters (e.g. `CN12345678`)
- **Never log shared secrets** — AAA configurations may contain RADIUS/TACACS shared secrets; do not log or display raw secret values in Slack channels
- **Firmware upgrades require maintenance window** — firmware upgrades can cause brief device outages; always schedule with a CR and announce maintenance window
- **CVE severity triage** — prioritize CVEs by CVSS score AND device role: gateway CVEs are more critical than AP/switch CVEs due to network boundary exposure
- **ISE NAD verification is bidirectional** — verify RADIUS config on Aruba side AND verify Aruba device is registered as a NAD in ISE; both must match
- **ACL analysis requires context** — an ACL rule is only meaningful in the context of its applied interface and direction (in/out); always note the ACL application point
- **Record in GAIT** — every ACL review, AAA audit, firewall query, and firmware compliance check must be logged
