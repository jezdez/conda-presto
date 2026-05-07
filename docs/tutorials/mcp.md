# MCP integration

conda-presto exposes a [Model Context Protocol](https://modelcontextprotocol.io/)
endpoint when running with the server feature. AI agents and coding
assistants can discover and call solve tools through the standard MCP
Streamable HTTP transport.

## Discovery

The MCP server metadata is available at the well-known endpoint:

```bash
curl -sS "$CONDA_PRESTO_URL/.well-known/mcp-server.json"
```

The MCP transport endpoint itself is at `/mcp`. Point your MCP client
at this URL to connect.

## Available tools

Tools are the primary way agents interact with conda-presto. Each
tool maps to an underlying HTTP API endpoint.

| Tool | Description |
|---|---|
| `resolve` | Resolve inline package specs to fully pinned packages. Accepts specs, channels, platforms, and an optional output format. Maps to `GET /resolve`. |
| `resolve_file` | Resolve an environment file (or inline file content) to pinned packages. Accepts a file body, content type, and query parameters. Maps to `POST /resolve`. |
| `parse_file` | Extract specs and channels from a file without solving. Useful for inspecting what an environment file contains before committing to a solve. Maps to `POST /parse`. |

### Example: resolve tool

An agent calling the `resolve` tool might send:

```json
{
  "specs": ["python=3.12", "numpy"],
  "channels": ["conda-forge"],
  "platforms": ["linux-64"]
}
```

The response is the same JSON array the HTTP API returns: one entry
per platform with full package metadata.

### Example: parse_file tool

The `parse_file` tool is handy for agents that need to understand
what an environment file asks for before deciding whether to solve:

```json
{
  "file": "channels:\n  - conda-forge\ndependencies:\n  - numpy\n  - pandas\n",
  "filename": "environment.yml"
}
```

Returns:

```json
{
  "specs": ["numpy", "pandas"],
  "channels": ["conda-forge"]
}
```

## Available resources

Resources provide read-only reference data that agents can query
without triggering a solve.

| Resource | Description |
|---|---|
| `formats` | List of supported output format names (maps to `GET /formats`) |
| `platforms` | List of known conda platform subdirs (maps to `GET /platforms`) |
| `version` | Version info for conda-presto and its dependencies (maps to `GET /version`) |
| `health` | Liveness probe (maps to `GET /health`) |

## Client configuration

To use conda-presto as an MCP server in your agent or IDE, add it
to your MCP client configuration. The exact syntax depends on your
client, but the key fields are the transport type and the URL:

```json
{
  "mcpServers": {
    "conda-presto": {
      "transport": "streamable-http",
      "url": "https://your-presto-instance.example.com/mcp"
    }
  }
}
```

The server uses [litestar-mcp](https://github.com/cofin/litestar-mcp)
to expose the MCP endpoint alongside the REST API, so both are
available on the same host and port.
