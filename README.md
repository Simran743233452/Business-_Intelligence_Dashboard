# AI Business Dashboard

An intelligent business issue monitoring system that leverages Claude AI to analyze business problems and automatically logs them to Google Sheets. This project uses the Model Context Protocol (MCP) to expose business operations as tools accessible to Claude.

## Overview

The AI Business Dashboard automates the process of:
1. **Analyzing** business issues using Claude's advanced AI capabilities
2. **Structuring** issues into a standardized format
3. **Tracking** issues in Google Sheets for team visibility
4. **Managing** business operations through an MCP-enabled interface

## Features

- 🤖 **Claude AI Integration**: Uses Claude Sonnet 4 for intelligent issue analysis
- 📊 **Google Sheets Integration**: Seamlessly logs issues to a shared spreadsheet
- 🔌 **MCP Server**: Exposes business operations as callable tools
- 📋 **Structured Data**: Automatically converts issues into standardized JSON format
- ✅ **Connection Testing**: Built-in utilities to verify API connections

## Project Structure

```
ai-business-dashboard/
├── claude_summary.py       # Sample script for AI-powered issue analysis
├── mcp_server.py           # MCP server: tools + transport entry point
├── summary_parser.py       # Deterministic parser for the monitoring summary template
├── sheets_service.py       # Reusable Google Sheets read/write/upsert helpers
├── auth_middleware.py      # Bearer-token auth for the streamable-http /mcp endpoint
├── test_sheets.py          # Test script for verifying the Sheets connection
├── test_process_summary.py # Local test for process_monitoring_summary()
├── test_auth.py            # Local test for bearer-token authentication
├── requirements.txt        # Pinned Python dependencies
├── render.yaml              # Render Blueprint (non-secret deployment config only)
├── credentials.json         # Google service account credentials (gitignored)
├── .env                    # ANTHROPIC_API_KEY (gitignored)
└── README.md               # This file
```

## Files Description

### `claude_summary.py`
Analyzes business issues using Claude AI and converts the response into structured JSON format.

**Key Features:**
- Sends business issues to Claude for analysis
- Parses Claude's response into JSON
- Extracts fields: section, site, issue, count, days_open, action_taken, next_action, status, notes

### `mcp_server.py`
Sets up an MCP server that exposes Google Sheets operations as callable tools,
and the entry point that starts it (locally over stdio today; over remote
HTTP once Phase 2 is deployed).

**Tools Available:**
- `test_connection()`: Verifies the MCP server is operational
- `read_sheet()`: Retrieves all data from the Monitoring sheet
- `add_issue()`: Adds a new business issue to the Monitoring sheet
- `analyze_sheet()`: Generates basic business insights from the Monitoring sheet
- `get_high_priority_issues()`: Lists high-priority open issues
- `update_issue()`: Updates an existing issue in the Monitoring sheet
- `process_monitoring_summary(summary)`: **Main workflow tool.** Parses a
  complete raw SUNTROP SOLAR Plant Monitoring Summary and writes each
  section to its own tab (Daily Summary, Needs Attention, Actions Taken,
  What's Needed Next, Service Pattern Watch). Safe to re-run on the same
  summary without duplicating rows.

Also exposes a plain `GET /health` HTTP endpoint (only reachable when
running under the HTTP transport - see "Running the MCP Server" below).

### `summary_parser.py`
Deterministic (no LLM) text parser that turns a raw Plant Monitoring
Summary into a structured dict. Used by `process_monitoring_summary`.

### `sheets_service.py`
Reusable Google Sheets helpers (tab creation, reading, append-if-not-duplicate,
upsert-by-key) shared by `process_monitoring_summary` so the tool itself
stays thin.

### `auth_middleware.py`
Bearer-token auth for the streamable-http `/mcp` endpoint - a small ASGI
middleware, not the MCP SDK's full OAuth system. See "Authentication" below.

### `test_sheets.py`
Utility script to test Google Sheets API connectivity and verify authentication.

### `test_process_summary.py`
Local test for `process_monitoring_summary()` using a realistic sample
summary - lets you verify the parser and Sheets writes without Claude Pro.

### `test_auth.py`
Local test proving `/health` works without a token, `/mcp` rejects missing/
wrong tokens, and `/mcp` accepts the correct one.

### `credentials.json`
Google Cloud service account credentials for accessing Google Sheets API.

## Prerequisites

- Python 3.8+
- Google Cloud project with Sheets API enabled
- Anthropic API key (for Claude access)
- Service account credentials for Google Sheets

## Installation

1. **Clone or download this project**
   ```bash
   cd ai-business-dashboard
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up Google Sheets credentials**
   - Create a service account in Google Cloud Console
   - Download the service account JSON file
   - Save it as `credentials.json` in the project directory

4. **Set up environment variables**
   - Create a `.env` file in the project directory
   - Add your Anthropic API key:
     ```
     ANTHROPIC_API_KEY=your_api_key_here
     ```

## Configuration

### Google Sheets Setup

Update the following in `mcp_server.py` and `test_sheets.py`:

```python
SPREADSHEET_ID = "your_spreadsheet_id_here"
SHEET_NAME = "Monitoring"
```

The Monitoring sheet should have these columns:
- A: Date
- B: Section
- C: Site
- D: Issue
- E: Count
- F: Days Open
- G: Action Taken
- H: Next Action
- I: Status
- J: Notes

## Usage

### Run the main analysis script

```bash
python claude_summary.py
```

This script will:
1. Send a business issue to Claude for analysis
2. Receive a structured JSON response
3. Parse the response into a Python dictionary

### Test Google Sheets connection

```bash
python test_sheets.py
```

This will verify that your credentials are valid and your spreadsheet is accessible.

### Use the MCP Server

The MCP server can be integrated with Claude or other AI applications to provide business operations tools:

```python
from mcp_server import mcp

# The server exposes these tools:
# - test_connection(message: str) -> str
# - read_sheet() -> str
# - add_issue(...) -> str
# - analyze_sheet() -> str
# - get_high_priority_issues() -> str
# - update_issue(...) -> str
# - process_monitoring_summary(summary: str) -> str
```

## Running the MCP Server

The transport is selected at runtime with the `MCP_TRANSPORT` environment
variable, so the same `mcp_server.py` works for local development today and
a remote deployment later - no code changes needed to switch.

### Local development (stdio) - default, used by MCP Inspector

This is unchanged from Phase 1: no environment variable needed.

```bash
python mcp_server.py
```

To inspect/test interactively with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python mcp_server.py
```

### Remote HTTP (Streamable HTTP) - for a Claude Pro custom connector

Not deployed anywhere yet - this is the command that will be run by
whatever hosting service is chosen in Phase 2. It starts the same server
over HTTP instead of stdio, on `0.0.0.0` so it's reachable from outside its
process/container. `MCP_AUTH_TOKEN` is required in this mode (see
"Authentication" below) - the process refuses to start without it:

```bash
export MCP_AUTH_TOKEN=some-long-random-secret   # see "Authentication" below
MCP_TRANSPORT=streamable-http python mcp_server.py
```

Endpoints exposed in this mode:
- `POST /mcp` - the MCP Streamable HTTP endpoint (what Claude Pro's "Add
  custom connector" would be pointed at, as `https://<your-host>/mcp`).
  **Requires** a valid `Authorization: Bearer <token>` header.
- `GET /health` - plain JSON liveness check (`{"status": "ok", ...}`) for
  the hosting platform's health checks / uptime monitoring. **Does not**
  require authentication, by design (see below).

Environment variables for HTTP mode:

| Variable        | Default | Purpose                                                              |
|-----------------|---------|------------------------------------------------------------------------|
| `MCP_TRANSPORT` | `stdio` | Set to `streamable-http` to run the remote HTTP server instead        |
| `MCP_AUTH_TOKEN` | *(none - required)* | Shared secret required on every `/mcp` request. Server refuses to start in HTTP mode without it. Never hard-coded - see "Authentication" below. |
| `PORT`          | `8000`  | Port to bind - read first since most hosting platforms inject this   |
| `MCP_PORT`      | `8000`  | Manual port override, used only if `PORT` isn't set                   |
| `MCP_HOST`      | `0.0.0.0` | Host/interface to bind in HTTP mode                                 |
| `GOOGLE_APPLICATION_CREDENTIALS` | `credentials.json` | Path to the Google service-account key file. See "Deploying on Render" below for how this is set on a host where the file isn't committed to the repo. |

`.env` (`ANTHROPIC_API_KEY`) is only used by the local sample script
`claude_summary.py` - the MCP server itself never reads it and does not
need it to run, locally or deployed.

## Authentication

The `/mcp` endpoint is protected by a simple bearer-token check - a shared
secret, not OAuth. This server has exactly one intended caller (a Claude
Pro custom connector) holding one token, so a static-secret check is the
right amount of protection; the MCP SDK's full OAuth 2.1 `auth` system
(issuer URLs, protected-resource metadata, etc.) would be unnecessary
complexity for this. It's implemented in
[`auth_middleware.py`](auth_middleware.py) as a small ASGI middleware
wrapped around the server's own Streamable HTTP app, so it only ever
applies in HTTP mode - stdio/MCP Inspector is completely unaffected and
needs no token.

### How it works

- Every request to `/mcp` must include `Authorization: Bearer <MCP_AUTH_TOKEN>`.
- Missing header, wrong scheme (not `Bearer `), or a token that doesn't
  match → `401 Unauthorized` with a generic `{"error": "unauthorized"}`
  body. The response never reveals whether a token was present, what was
  expected, or anything else that would help someone guess it.
- The comparison uses `hmac.compare_digest` (constant-time), so response
  timing can't leak how many characters of a guessed token were correct.
- `GET /health` is explicitly exempt, so hosting-platform health checks
  keep working without credentials.
- `MCP_AUTH_TOKEN` is read from the environment only - it is never
  hard-coded, logged, or echoed back in any response. It also never needs
  to reach `credentials.json`/Google Sheets code at all; it's checked
  purely at the HTTP layer before a request ever reaches an MCP tool.
- If `MCP_TRANSPORT=streamable-http` is set but `MCP_AUTH_TOKEN` is not,
  the server raises an error and refuses to start, rather than ever
  silently serving the endpoint unauthenticated.

### Setting `MCP_AUTH_TOKEN` locally

Generate a random token and export it before starting the server in HTTP
mode (only needed for HTTP mode - plain `python mcp_server.py` for
stdio/MCP Inspector needs nothing):

```bash
# Generate a random token (any long random string works)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Use it for this session
export MCP_AUTH_TOKEN=<paste the generated token>
MCP_TRANSPORT=streamable-http python mcp_server.py
```

Then call it with the matching header, e.g.:

```bash
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}'
```

### Configuring `MCP_AUTH_TOKEN` on Render

Add it as a regular environment variable (not a Secret File - it's a
plain string, not a multi-line key file like `credentials.json`):

1. Generate a token the same way as above (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), somewhere local - not in a Render text box that might get logged elsewhere.
2. Render dashboard → the Web Service → **Environment** → **Environment Variables** → add `MCP_AUTH_TOKEN` with that value.
3. Save. Render redeploys the service with it set.
4. Configure Claude Pro's custom connector (see below) with the same token.

If the token is ever rotated, update it in both places (Render's env var
and Claude Pro's connector config) together - a mismatch simply results in
`401 Unauthorized` until they match again.

### Claude Custom Connector URL format

Once deployed, in Claude Pro → Settings → Connectors → "Add custom
connector":

- **URL:** `https://<your-service>.onrender.com/mcp`
- **Authentication:** Bearer token, set to the same value as `MCP_AUTH_TOKEN`
  on Render.

(Not usable yet - nothing has been deployed. This documents the eventual
format for when Render deployment happens.)

## Deploying on Render

**Status: prepared, not deployed.** This section documents the exact
settings to use; nothing has been deployed yet.

### Do any code changes need to happen for Render?

Yes, one was required and is already in place: credential loading in
`mcp_server.py` now reads the key file path from
`GOOGLE_APPLICATION_CREDENTIALS` (defaulting to `credentials.json`, so
local behavior is unchanged) instead of a hard-coded `"credentials.json"`.
Without this, the process would crash on startup on Render, since
`credentials.json` is intentionally never committed to the repo.
`PORT`/`0.0.0.0` binding for HTTP mode was already in place from the
transport work in the previous phase - no change needed there.

### Render Web Service settings

| Setting | Value |
|---|---|
| Environment | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python mcp_server.py` |
| Health Check Path | `/health` |

These are also captured non-secretly in [`render.yaml`](render.yaml), which
can be used with Render's "New +" → "Blueprint" flow, or you can enter the
same values manually when creating a Web Service from the repo. Either way,
nothing in `render.yaml` deploys anything by itself - it's just config.

### Required environment variables (set in the Render dashboard)

| Variable | Value | Notes |
|---|---|---|
| `MCP_TRANSPORT` | `streamable-http` | Required - without it the server would try to start in stdio mode, which doesn't make sense on a web service. |
| `MCP_AUTH_TOKEN` | *(a generated random secret)* | Required - the server refuses to start in HTTP mode without it. See "Authentication" above for how to generate and set it. Use a regular environment variable, not a Secret File. |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/etc/secrets/credentials.json` | Required - path to the Secret File described below. |
| `PORT` | *(leave unset)* | Render injects this automatically for web services; the code already reads it. Do not set it manually. |

`MCP_HOST` does not need to be set - it already defaults to `0.0.0.0` in
code, which is required for Render's proxy to reach the container.

### Providing Google service-account credentials securely on Render

**Do not** paste the private key into a regular environment variable, and
never commit `credentials.json` to git (it stays gitignored). Use Render's
**Secret Files** feature instead - it's built for exactly this:

1. In the Render dashboard, open the Web Service → **Environment** → **Secret Files**.
2. Add a new secret file:
   - **Filename:** `credentials.json`
   - **Contents:** paste the full contents of your existing local
     `credentials.json` (the service account key JSON).
3. Render mounts it read-only inside the container at
   `/etc/secrets/credentials.json` - which is exactly the path set in the
   `GOOGLE_APPLICATION_CREDENTIALS` environment variable above.
4. Nothing else changes - `mcp_server.py` reads that path the same way it
   reads the local `credentials.json` file today.

Secret Files are not stored in git, are not shown in build logs, and are
separate from the regular environment variable list - this keeps the
private key out of anywhere it could be accidentally exposed (repo history,
CI logs, a leaked `.env` export, etc.).

### Will `/health` and `/mcp` work behind Render?

Yes, as-is. Render's proxy forwards HTTP requests to whatever port the
container binds via the `PORT` env var, over both paths - `GET /health`
for Render's own health checks (already wired via `healthCheckPath` above)
and `POST /mcp` for the actual MCP Streamable HTTP protocol Claude Pro's
"Add custom connector" would call, as `https://<your-service>.onrender.com/mcp`.
Both were verified locally by starting the server with `PORT` set and
confirming the process binds `0.0.0.0` and responds on that port.

### Security concerns to be aware of before actually deploying

- **The MCP endpoint is now authenticated** (bearer token via
  `MCP_AUTH_TOKEN` - see "Authentication" above), which closes the earlier
  gap where anyone with the Render URL could call every tool. Two things
  still worth being deliberate about: use a long, random token (the
  `secrets.token_urlsafe(32)` command above is enough), and treat that
  token itself as a credential - don't paste it anywhere it could be
  logged or shared beyond Render's env vars and Claude Pro's connector
  config.
- **Least-privilege service account.** Make sure the service account tied
  to `credentials.json` only has access to the one spreadsheet it needs
  (share the sheet with its `client_email` specifically) rather than
  broader Google Workspace/Drive scope.
- **Rotate the key if it's ever exposed.** If the service-account JSON is
  ever pasted somewhere other than Render's Secret Files (a chat, a public
  gist, a committed file), treat it as compromised and generate a new key
  in Google Cloud Console, then update the Secret File.
- **Free-tier cold starts.** Render's free web services spin down after
  inactivity; the first request after idling (including a health check or
  the first tool call from Claude) can take up to ~30-60s while it spins
  back up. Not a security issue, but worth knowing before assuming a
  connector call failed.

## Data Structure

Issues are structured in the following JSON format:

```json
{
  "section": "Marketing",
  "site": "Bengaluru",
  "issue": "Marketing campaign conversion dropped significantly",
  "count": 0,
  "days_open": 0,
  "action_taken": "Investigation initiated",
  "next_action": "Review campaign metrics",
  "status": "Open",
  "notes": "Requires immediate attention"
}
```

## Environment Variables

Create a `.env` file with the following:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

## Dependencies

- `anthropic`: Anthropic Python SDK for Claude API access
- `google-api-python-client`: Google Sheets API client
- `google-auth-oauthlib`: Google authentication
- `google-auth-httplib2`: HTTP adapter for Google auth
- `python-dotenv`: Environment variable management
- `mcp`: Model Context Protocol implementation

## Troubleshooting

### Authentication Errors
- Verify `credentials.json` is in the project directory
- Ensure the service account has Sheets API access
- Check that your Google Cloud project has the Sheets API enabled

### API Key Issues
- Verify your `ANTHROPIC_API_KEY` in the `.env` file
- Ensure the API key is valid and has not expired

### Sheet Access Errors
- Verify the `SPREADSHEET_ID` is correct
- Ensure the service account email has been granted access to the spreadsheet
- Check that the sheet named "Monitoring" exists

## Future Enhancements

- Add issue categorization and priority levels
- Implement real-time notifications for critical issues
- Add dashboard UI for visualization
- Integrate with additional business systems
- Add issue resolution tracking and analytics

## License

[Add your license information here]

## Support

[Add support contact information here]
