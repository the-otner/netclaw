# Aruba Central MCP Server — Deployment Guide

**Running NetClaw's Aruba Central integration with a locally-hosted open-source LLM**

This guide walks you through deploying the Aruba Central MCP server alongside a private, locally-hosted large language model (LLM) on your own server. No data leaves your network — the LLM runs on-premises and talks to Aruba Central via the HPE GreenLake API Gateway.

---

## Architecture Overview

```
Your Network
┌────────────────────────────────────────────────────────┐
│                                                        │
│  ┌─────────────────┐     MCP (stdio/JSON-RPC)         │
│  │  LLM (Ollama /  │ ◄──────────────────────────────► │
│  │  LM Studio /    │                                  │
│  │  llama.cpp)     │   ┌──────────────────────────┐   │
│  └────────┬────────┘   │ Aruba Central MCP Server  │   │
│           │            │ mcp-servers/aruba-central │   │
│           │ OpenAI API │ -mcp/server.py             │   │
│           │            └────────────┬─────────────┘   │
│  ┌────────▼────────┐               │ HTTPS REST       │
│  │  MCP Client     │               │ Bearer Token     │
│  │ (Claude Desktop │               ▼                  │
│  │  / Open Inter-  │    ┌──────────────────────────┐  │
│  │  preter / CLI)  │    │  HPE GreenLake API        │  │
│  └─────────────────┘    │  Gateway (Aruba Central)  │  │
│                         └──────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

**How it works:**

1. The LLM server (Ollama, LM Studio, or llama.cpp) runs locally — no data sent to OpenAI or Anthropic
2. An MCP client bridges your LLM to the Aruba Central MCP server via the Model Context Protocol
3. The MCP server translates tool calls into Aruba Central REST API requests
4. Results flow back to the LLM for analysis and natural language responses

---

## Prerequisites

### Hardware Requirements

For the LLM server (heavier models = better tool-calling accuracy):

| Model Size | RAM | CPU | GPU (optional but recommended) |
|-----------|-----|-----|-------------------------------|
| 7B parameters (fast, basic) | 8 GB | 8-core modern CPU | Any 8 GB VRAM GPU |
| 13B parameters (balanced) | 16 GB | 12-core CPU | 12 GB VRAM GPU (RTX 3080) |
| 70B parameters (recommended) | 48 GB | 16-core CPU | 2× A100 or 2× RTX 4090 |
| 70B quantized (Q4, practical) | 40 GB RAM | 16-core CPU | 24 GB VRAM GPU (RTX 3090/4090) |

> **Recommendation for network engineers:** Use a 70B quantized model (Q4_K_M) if you have a workstation with 40+ GB RAM. Models like Llama 3.1 70B and DeepSeek-Coder-V2 have strong tool-calling capabilities needed for reliable MCP integration.

### Software Requirements

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | 3.12+ | Aruba Central MCP server |
| uv | latest | Python dependency management |
| git | any | Clone NetClaw repository |
| Docker | optional | Containerized LLM deployment |
| Node.js | 18+ | Required for some MCP clients (Claude Desktop, mcp-client-cli) |

Install Python 3.12 and uv:

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y python3.12 python3.12-pip git curl

# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

### Network Requirements

- **Outbound HTTPS** (port 443) to your regional Aruba Central API Gateway:
  - US-East: `apigw-prod2.central.arubanetworks.com`
  - US-West: `apigw-uswest4.central.arubanetworks.com`
  - EU: `apigw-eucentral3.central.arubanetworks.com`
  - APAC: `apigw-apnortheast.central.arubanetworks.com`
  - Canada: `apigw-cacentral.central.arubanetworks.com`
- **Local network access** for the LLM server (loopback or LAN)
- DNS resolution for the API Gateway FQDN

Verify connectivity before proceeding:

```bash
curl -v https://apigw-prod2.central.arubanetworks.com/monitoring/v2/switches \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" | head -20
```

---

## Step 1: Set Up the Local LLM Server

Choose one of the three options below. **Ollama is recommended** for ease of use.

---

### Option A: Ollama (Recommended)

Ollama is the easiest way to run local LLMs and has excellent tool-calling support.

#### Install Ollama

```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh

# macOS
brew install ollama

# Or download from https://ollama.com/download
```

#### Pull a Tool-Capable Model

```bash
# Best balance of quality and speed for 24 GB VRAM GPU:
ollama pull llama3.1:70b-instruct-q4_K_M

# For 8 GB VRAM (faster but less accurate tool-calling):
ollama pull llama3.1:8b

# DeepSeek Coder V2 (excellent for technical/networking tasks):
ollama pull deepseek-coder-v2:16b

# Mixtral (strong multi-step reasoning):
ollama pull mixtral:8x7b
```

> **Model selection for network engineering:** Llama 3.1 70B or DeepSeek-Coder-V2 are recommended. Smaller models (7B-8B) may struggle with complex multi-tool reasoning chains (e.g., "check health, then run ping, then analyze routing table").

#### Start the Ollama Server

```bash
# Start with default settings (localhost:11434)
ollama serve

# Or run as a systemd service (auto-start on boot):
sudo systemctl enable ollama
sudo systemctl start ollama
```

#### Verify Ollama is Running

```bash
curl http://localhost:11434/api/tags
# Expected: {"models":[{"name":"llama3.1:70b-instruct-q4_K_M",...}]}

# Test inference:
ollama run llama3.1:70b-instruct-q4_K_M "What is BGP?"
```

---

### Option B: LM Studio

LM Studio provides a GUI for model management and an OpenAI-compatible local API.

1. **Download LM Studio** from [lmstudio.ai](https://lmstudio.ai) for your OS (Windows/macOS/Linux)

2. **Load a model** — in LM Studio, go to the **Discover** tab and download:
   - `Meta Llama 3.1 70B Instruct` (Q4_K_M — requires ~40 GB RAM)
   - `Mixtral 8x22B Instruct` (Q4 — requires ~50 GB RAM, exceptional reasoning)
   - `Qwen2.5-Coder 32B` (Q4 — excellent for technical tasks, requires ~20 GB RAM)

3. **Start the Local Server** — go to the **Local Server** tab and click **Start Server**
   - The server starts on `http://localhost:1234` (OpenAI-compatible API)
   - Port is configurable in LM Studio settings

4. **Verify:**
   ```bash
   curl http://localhost:1234/v1/models
   # Expected: {"object":"list","data":[{"id":"meta-llama-3.1-70b-instruct",...}]}
   ```

---

### Option C: llama.cpp Server

For maximum control and performance, build llama.cpp from source.

#### Build llama.cpp

```bash
# Install build dependencies
sudo apt install -y build-essential cmake libopenblas-dev

# Clone and build (CPU + BLAS optimized)
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp

# For CUDA GPU acceleration:
cmake -B build -DLLAMA_CUDA=ON
cmake --build build --config Release -j$(nproc)

# For CPU-only:
cmake -B build
cmake --build build --config Release -j$(nproc)
```

#### Download a GGUF Model

```bash
# Install huggingface-hub CLI
pip install huggingface-hub

# Download Llama 3.1 70B Q4_K_M (good quality/size balance)
huggingface-cli download \
  bartowski/Meta-Llama-3.1-70B-Instruct-GGUF \
  Meta-Llama-3.1-70B-Instruct-Q4_K_M.gguf \
  --local-dir ./models/

# Or DeepSeek-Coder-V2 (recommended for technical tasks)
huggingface-cli download \
  bartowski/DeepSeek-Coder-V2-Instruct-GGUF \
  DeepSeek-Coder-V2-Instruct-Q4_K_M.gguf \
  --local-dir ./models/
```

#### Start the llama.cpp Server

```bash
# CPU-only (OpenAI-compatible API on port 8080)
./build/bin/llama-server \
  --model models/Meta-Llama-3.1-70B-Instruct-Q4_K_M.gguf \
  --host 0.0.0.0 \
  --port 8080 \
  --ctx-size 8192 \
  --n-gpu-layers 0

# With GPU acceleration (offload all layers to GPU):
./build/bin/llama-server \
  --model models/Meta-Llama-3.1-70B-Instruct-Q4_K_M.gguf \
  --host 0.0.0.0 \
  --port 8080 \
  --ctx-size 8192 \
  --n-gpu-layers 99

# Verify:
curl http://localhost:8080/v1/models
```

---

## Step 2: Get Aruba Central GreenLake Credentials

### Log in to HPE GreenLake

1. Navigate to [https://common.cloud.hpe.com](https://common.cloud.hpe.com)
2. Sign in with your HPE GreenLake account credentials
3. Select your **Account/Tenant**

### Navigate to the API Gateway

1. From the top navigation, click **Account Home** (top-right user menu)
2. Select **API Gateway** from the left sidebar

   > If you don't see API Gateway, ensure your account has **API Access** permissions. Contact your HPE GreenLake administrator.

### Create a System App (if not already created)

1. Under **System Apps & Tokens**, click **+ Add App**
2. Enter a name (e.g., `netclaw-mcp`)
3. Select your **Customer / Tenant**
4. Click **Create**

### Generate an OAuth2 Token

1. Select your System App from the list
2. Click **Generate Token**
3. Copy the **Access Token** — this is your `ARUBA_CENTRAL_TOKEN`

   > **Important:** Tokens expire after ~2 hours. For long-running deployments, implement token refresh using the `refresh_token` field. See [Aruba Central OAuth2 documentation](https://developer.arubanetworks.com/aruba-central/docs/oauth-workflow).

### Identify Your API Gateway Base URL

Choose the URL for your region:

| Region | API Gateway URL |
|--------|----------------|
| US-East (default) | `https://apigw-prod2.central.arubanetworks.com` |
| US-West | `https://apigw-uswest4.central.arubanetworks.com` |
| EU Central | `https://apigw-eucentral3.central.arubanetworks.com` |
| AP Northeast | `https://apigw-apnortheast.central.arubanetworks.com` |
| Canada | `https://apigw-cacentral.central.arubanetworks.com` |

Your region is shown in the API Gateway URL when you log in to HPE GreenLake. If unsure, US-East (`apigw-prod2`) is the default for US customers.

---

## Step 3: Clone and Configure NetClaw

### Clone the Repository

```bash
git clone https://github.com/automateyournetwork/netclaw.git
cd netclaw
```

### Configure Aruba Central Credentials

```bash
# Create the .env file for the Aruba Central MCP server
cp mcp-servers/aruba-central-mcp/.env.example mcp-servers/aruba-central-mcp/.env
nano mcp-servers/aruba-central-mcp/.env
```

Edit the `.env` file with your credentials:

```bash
# mcp-servers/aruba-central-mcp/.env
ARUBA_CENTRAL_BASE_URL=https://apigw-prod2.central.arubanetworks.com
ARUBA_CENTRAL_TOKEN=your_access_token_here
```

### Install Dependencies

```bash
# Using uv (recommended)
cd mcp-servers/aruba-central-mcp
uv pip install -r requirements.txt

# Or using pip directly
pip3 install -r mcp-servers/aruba-central-mcp/requirements.txt
```

---

## Step 4: Configure MCP Client to Connect LLM to MCP Server

The **Model Context Protocol (MCP)** is an open standard that lets LLMs call external tools. The flow is:

```
LLM ↔ MCP Client ↔ MCP Server ↔ Aruba Central REST API
```

Choose the MCP client that matches your LLM setup:

---

### Option A: Claude Desktop (if using Claude)

If you have Claude Desktop installed and want to use Anthropic Claude instead of a local LLM:

Edit `~/.config/claude/claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "aruba-central": {
      "command": "uv",
      "args": [
        "run",
        "--with", "fastmcp",
        "fastmcp", "run",
        "/path/to/netclaw/mcp-servers/aruba-central-mcp/server.py"
      ],
      "env": {
        "ARUBA_CENTRAL_BASE_URL": "https://apigw-prod2.central.arubanetworks.com",
        "ARUBA_CENTRAL_TOKEN": "your_access_token_here"
      }
    }
  }
}
```

Restart Claude Desktop. You should see "aruba-central" in the MCP tools panel.

---

### Option B: mcp-client-cli (Command-Line MCP Client)

`mcp-client-cli` is an open-source CLI that connects any OpenAI-compatible LLM to MCP servers.

```bash
# Install
pip install mcp-client-cli

# Configure: create ~/.config/mcp-client-cli/config.json
mkdir -p ~/.config/mcp-client-cli
cat > ~/.config/mcp-client-cli/config.json << 'EOF'
{
  "llm": {
    "provider": "openai",
    "model": "llama3.1:70b-instruct-q4_K_M",
    "base_url": "http://localhost:11434/v1",
    "api_key": "ollama"
  },
  "mcp_servers": {
    "aruba-central": {
      "command": "uv",
      "args": [
        "run", "--with", "fastmcp",
        "fastmcp", "run",
        "/path/to/netclaw/mcp-servers/aruba-central-mcp/server.py"
      ],
      "env": {
        "ARUBA_CENTRAL_BASE_URL": "https://apigw-prod2.central.arubanetworks.com",
        "ARUBA_CENTRAL_TOKEN": "your_access_token_here"
      }
    }
  }
}
EOF

# For LM Studio (port 1234):
# Change "base_url" to "http://localhost:1234/v1"
# For llama.cpp (port 8080):
# Change "base_url" to "http://localhost:8080/v1"

# Start a chat session
mcp-chat
```

---

### Option C: Custom Python Script

For maximum flexibility, use this Python script that bridges your local LLM with the Aruba Central MCP server:

```python
#!/usr/bin/env python3
"""
NetClaw Aruba Central — Local LLM + MCP Bridge
Connects a local Ollama/LM Studio/llama.cpp LLM to the Aruba Central MCP server.

Usage:
  pip install openai mcp
  python aruba_chat.py
"""

import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

# ── Configuration ──────────────────────────────────────────────────────────

# Local LLM endpoint (choose one):
LLM_BASE_URL = "http://localhost:11434/v1"   # Ollama
# LLM_BASE_URL = "http://localhost:1234/v1"  # LM Studio
# LLM_BASE_URL = "http://localhost:8080/v1"  # llama.cpp

LLM_MODEL = "llama3.1:70b-instruct-q4_K_M"  # Adjust to your pulled model
LLM_API_KEY = "ollama"                         # Any non-empty string for local LLMs

MCP_SERVER_SCRIPT = "/path/to/netclaw/mcp-servers/aruba-central-mcp/server.py"
MCP_ENV = {
    "ARUBA_CENTRAL_BASE_URL": "https://apigw-prod2.central.arubanetworks.com",
    "ARUBA_CENTRAL_TOKEN": "your_access_token_here",
}

# ── Main loop ───────────────────────────────────────────────────────────────

async def chat_loop():
    client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

    server_params = StdioServerParameters(
        command="uv",
        args=["run", "--with", "fastmcp", "fastmcp", "run", MCP_SERVER_SCRIPT],
        env=MCP_ENV,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Convert MCP tools to OpenAI function format
            mcp_tools = await session.list_tools()
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.inputSchema,
                    },
                }
                for t in mcp_tools.tools
            ]

            print(f"Connected to Aruba Central MCP server ({len(tools)} tools available)")
            print("Type your question or 'quit' to exit.\n")

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are NetClaw, a CCIE-level AI network engineer. "
                        "You have access to tools for monitoring and troubleshooting "
                        "HPE Aruba Networking Central. Always verify device state before "
                        "making recommendations. Record findings for the audit trail."
                    ),
                }
            ]

            while True:
                user_input = input("You: ").strip()
                if user_input.lower() in ("quit", "exit", "q"):
                    break
                if not user_input:
                    continue

                messages.append({"role": "user", "content": user_input})

                # Agentic loop — keep calling LLM until no more tool calls
                while True:
                    response = await client.chat.completions.create(
                        model=LLM_MODEL,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                    )
                    msg = response.choices[0].message
                    messages.append(msg)

                    if not msg.tool_calls:
                        print(f"\nNetClaw: {msg.content}\n")
                        break

                    # Execute tool calls via MCP
                    for call in msg.tool_calls:
                        args = json.loads(call.function.arguments)
                        print(f"  [tool] {call.function.name}({args})")
                        result = await session.call_tool(call.function.name, args)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": result.content[0].text if result.content else "{}",
                        })


if __name__ == "__main__":
    asyncio.run(chat_loop())
```

Save as `aruba_chat.py`, install dependencies, and run:

```bash
pip install openai mcp
python aruba_chat.py
```

---

### Option D: Open Interpreter

[Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) supports local LLMs and MCP servers:

```bash
# Install Open Interpreter
pip install open-interpreter

# Configure for Ollama + Aruba Central MCP
cat > ~/.config/open-interpreter/config.yaml << 'EOF'
llm:
  model: ollama/llama3.1:70b-instruct-q4_K_M
  api_base: http://localhost:11434

mcp_servers:
  - command: uv
    args:
      - run
      - --with
      - fastmcp
      - fastmcp
      - run
      - /path/to/netclaw/mcp-servers/aruba-central-mcp/server.py
    env:
      ARUBA_CENTRAL_BASE_URL: https://apigw-prod2.central.arubanetworks.com
      ARUBA_CENTRAL_TOKEN: your_access_token_here
EOF

interpreter
```

---

## Step 5: Run the Aruba Central MCP Server

You can run the MCP server directly for testing (the MCP client will start it automatically in production):

```bash
cd netclaw/mcp-servers/aruba-central-mcp

# Run with uv (resolves dependencies automatically)
uv run --with fastmcp fastmcp run server.py

# Or with pip-installed dependencies
python server.py
```

The server starts in stdio mode — it reads JSON-RPC messages from stdin and writes responses to stdout. You will see:

```
2026-03-10 10:00:00 [aruba-central-mcp] INFO Aruba Central HTTP client initialised — base_url=https://apigw-prod2.central.arubanetworks.com
```

Press `Ctrl+C` to stop the server. In production, the MCP client manages the server lifecycle automatically.

---

## Step 6: Test the Integration

### Verify LLM Can Call Tools

With your MCP client running, try these test prompts to verify end-to-end functionality:

#### Basic Inventory Test

```
List all my Aruba devices and their status
```

Expected behavior: LLM calls `aruba_get_devices`, returns device list with model, serial, status.

#### Device Health Test

```
Check the health of device with serial CN12345678
```

Expected behavior: LLM calls `aruba_get_device_health(serial="CN12345678")`, returns CPU, memory, uptime.

#### Events Test

```
Show me all critical events from the last 24 hours
```

Expected behavior: LLM calls `aruba_get_events(severity="critical")`, returns event list with descriptions.

#### Routing Health Test

```
Run a BGP health check on my gateways
```

Expected behavior: LLM calls `aruba_get_devices(device_type="gateway")` then `aruba_get_bgp_neighbors(serial)` for each gateway serial.

#### Security Audit Test

```
Audit the ACLs on device XYZ789
```

Expected behavior: LLM calls `aruba_get_acls(serial="XYZ789")`, analyzes rules, reports findings.

#### Full Health Check

```
Give me a complete health overview of my Aruba network:
- List all devices and flag any that are down
- Check CPU/memory on all gateways
- Show any critical or major events
- Check BGP neighbors on all gateways
```

Expected behavior: Multi-step tool chain: `aruba_get_devices` → `aruba_get_device_health` per gateway → `aruba_get_events` → `aruba_get_bgp_neighbors` per gateway.

### Troubleshooting Test Prompts

```
# Connectivity troubleshoot
Run a ping from gateway CN12345678 to 8.8.8.8

# Path analysis
Trace the route from gateway CN12345678 to 10.0.1.1

# Routing table check
Show me the routing table on gateway CN12345678 and check for a default route

# OSPF check
Check OSPF neighbor state on gateway CN12345678

# Security audit
Check the firewall policies and ACLs on gateway CN12345678 for any permit-any rules

# Firmware compliance
Which of my devices are running non-compliant firmware versions?
```

---

## Step 7: Security Considerations for Private Server

### Protect Credentials

```bash
# Never commit .env files
echo "mcp-servers/aruba-central-mcp/.env" >> .gitignore
echo ".env" >> .gitignore

# Restrict file permissions
chmod 600 mcp-servers/aruba-central-mcp/.env

# Store token in system keychain instead of file (macOS):
security add-generic-password -a aruba-central -s netclaw -w "your_token"
# Retrieve: security find-generic-password -a aruba-central -s netclaw -w
```

### Restrict LLM Server Access

If the LLM server runs on a shared host, restrict who can reach it:

```bash
# Option A: Bind to localhost only (already default for Ollama)
# Ollama default: http://127.0.0.1:11434 — only accessible from local machine

# Option B: Use iptables to restrict access
sudo iptables -A INPUT -p tcp --dport 11434 -s 10.0.0.0/8 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 11434 -j DROP

# Option C: Use SSH tunneling to access a remote LLM server securely
ssh -L 11434:localhost:11434 user@llm-server.example.com
# Then connect your MCP client to http://localhost:11434
```

### Enable HTTPS for Network-Exposed LLM Server

If the LLM server must be accessible across the network (not just localhost):

```bash
# Option A: nginx reverse proxy with TLS
sudo apt install nginx certbot python3-certbot-nginx

# /etc/nginx/sites-available/ollama
# server {
#   listen 443 ssl;
#   server_name llm.example.com;
#   ssl_certificate /etc/letsencrypt/live/llm.example.com/fullchain.pem;
#   ssl_certificate_key /etc/letsencrypt/live/llm.example.com/privkey.pem;
#   location / { proxy_pass http://127.0.0.1:11434; }
# }

# Option B: Ollama built-in TLS (add --tls-cert and --tls-key flags when available)
```

### Rotate Aruba Central Tokens Regularly

Aruba Central access tokens expire after ~2 hours. For automated workflows:

```bash
# Automate token refresh with a cron job:
# 1. Store your client_id, client_secret, and refresh_token securely
# 2. Create a refresh script:

cat > /usr/local/bin/refresh-aruba-token.sh << 'EOF'
#!/bin/bash
RESPONSE=$(curl -s -X POST "https://apigw-prod2.central.arubanetworks.com/oauth2/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=refresh_token" \
  -d "client_id=${ARUBA_CLIENT_ID}" \
  -d "client_secret=${ARUBA_CLIENT_SECRET}" \
  -d "refresh_token=${ARUBA_REFRESH_TOKEN}")

NEW_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
# Update .env file
sed -i "s/^ARUBA_CENTRAL_TOKEN=.*/ARUBA_CENTRAL_TOKEN=$NEW_TOKEN/" \
  /path/to/netclaw/mcp-servers/aruba-central-mcp/.env
EOF

chmod +x /usr/local/bin/refresh-aruba-token.sh

# Cron job: refresh every 90 minutes (before 2-hour expiry)
# crontab -e
# */90 * * * * /usr/local/bin/refresh-aruba-token.sh
```

### Audit with GAIT

Every interaction with Aruba Central through NetClaw is automatically tracked in the GAIT audit trail:

```bash
# View today's audit log
cat memory/$(date +%Y-%m-%d).md

# View all Aruba Central tool calls
grep "aruba_" memory/*.md
```

---

## Troubleshooting

### Error: `ARUBA_CENTRAL_BASE_URL is not set`

The MCP server cannot find the environment variable.

```bash
# Check your .env file
cat mcp-servers/aruba-central-mcp/.env

# Or export directly for testing
export ARUBA_CENTRAL_BASE_URL=https://apigw-prod2.central.arubanetworks.com
export ARUBA_CENTRAL_TOKEN=your_token
python mcp-servers/aruba-central-mcp/server.py
```

### Error: `Authentication failed (401)`

Your Aruba Central token has expired or is invalid.

```bash
# Verify your token is valid
curl -s "https://apigw-prod2.central.arubanetworks.com/monitoring/v2/switches?limit=1" \
  -H "Authorization: Bearer YOUR_TOKEN" | python3 -m json.tool

# If you see {"code":401,"description":"Unauthorized"}, generate a new token
# via HPE GreenLake API Gateway → System Apps & Tokens → Generate Token
```

### Error: `Rate limited (429)`

You are exceeding the Aruba Central API rate limits.

The MCP server automatically retries on 429 (up to 3 attempts with backoff). If rate limiting persists:

- Reduce parallel requests in fleet-wide queries
- Space out monitoring checks (poll every 5 minutes, not every 30 seconds)
- Contact HPE to increase your API rate limits if needed

### Error: `Connection failed` / `SSL handshake`

```bash
# Test DNS resolution
nslookup apigw-prod2.central.arubanetworks.com

# Test TLS connection
openssl s_client -connect apigw-prod2.central.arubanetworks.com:443 -brief

# Check proxy settings (if behind a corporate proxy)
export HTTPS_PROXY=http://proxy.example.com:8080
```

### Error: `Resource not found (404)` for device serial

The serial number does not exist in your Aruba Central account, or the device type is wrong (e.g., using switch endpoint for a gateway).

```bash
# List all devices to find the correct serial
# Ask NetClaw: "List all my Aruba gateways and their serial numbers"
```

### LLM Not Calling Tools

If the LLM responds to queries without calling any MCP tools:

1. **Model is too small** — use a 13B+ model; 7B models often ignore tool calls
2. **System prompt issue** — add explicit instruction: *"Use the available tools to answer questions about Aruba devices"*
3. **Model doesn't support tool calling** — not all GGUF models include instruction-tuning for function calling; use `-instruct` variants

### High Latency / Slow Responses

- Reduce context window if using large RIB/LSDB responses: set `limit=20` in routing queries
- Use pagination (`offset` parameter) for large device fleets
- Consider running a faster (smaller) model for quick lookups and the larger model for analysis
