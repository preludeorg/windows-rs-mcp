# Windows Crate Docs Search MCP

This project provides a Claude Desktop MCP server that allows searching the Rust `windows` crate API documentation hosted at [microsoft.github.io/windows-docs-rs](https://microsoft.github.io/windows-docs-rs/).

It uses Playwright to interact with the documentation website's search functionality and the features table.

## Features

*   **Search Windows API:** Use the `search_windows_api` tool to find functions, structs, enums, etc., within the `windows` crate docs.
*   **Get Documentation:** Retrieve detailed documentation for exact matches, including description, signature, and the required Cargo feature (best effort).
*   **Find Related Items:** List related API items found during the search.

## Usage

This package requires Playwright browsers to be installed. The server attempts to install the default browser (`chromium`) automatically on the first run. You can skip this by setting the `MCP_SKIP_PLAYWRIGHT_INSTALL` environment variable.

### Running the MCP Server

**Using `uvx`:**

The simplest way to run the server after installation is using `uvx`:

```bash
uvx windows-rs-mcp
```

**Running Locally with `uv`:**

If you have cloned the repository, you can run the server directly using `uv run` from the project directory:

```bash
# Navigate to the project root directory
# cd /path/to/windows-rs-mcp  (Linux/macOS)
# cd C:\path\to\your\project\windows-rs-mcp (Windows)

# Run the server
uv run windows-rs-mcp
```

### Configuring Claude Desktop

To use this MCP with Claude Desktop, add the following configuration to your Claude Desktop settings:

**Option 1: Using `uvx` (Recommended)**

```json
{
  "mcpServers": {
    "windows-docs": {
      "command": "uvx",
      "args": [
        "windows-rs-mcp"
      ]
    }
  }
}
```

**Option 2: Using `uv run` (For local source)**

Make sure to replace the placeholder path in `args` with the actual absolute path to your project directory.

```json
{
  "mcpServers": {
    "windows-docs": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\path\\to\\your\\project\\windows-rs-mcp", // <-- Replace with your path
        "run",
        "windows-rs-mcp"
      ]
    }
  }
}
```

After configuring, restart Claude Desktop. You should then be able to use the `search_windows_api` tool.

### Configuring Cursor

Open `.cursor/mcp.json` and add the MCP server

```json
{
  "mcpServers": {
    "windows-rs-mcp": {
      "command": "uvx",
      "args": ["windows-rs-mcp"]
    }
  }
}
```

## Environment Variables

*   `MCP_SKIP_PLAYWRIGHT_INSTALL`: Set to `true`, `1`, or `yes` to skip the automatic Playwright browser installation check.
*   `MCP_PLAYWRIGHT_BROWSER`: Specifies the browser Playwright should install (defaults to `chromium`). Other options include `firefox` and `webkit`.
