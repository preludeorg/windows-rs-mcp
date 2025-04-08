import os
import subprocess
import sys
import typing

from mcp.server.fastmcp import Context, FastMCP

from windows_rs_mcp.docs import (
    ApiDocumentation,
    RelatedApiFunction,
    SearchResult,
    windows_docs_client,
)

# Create the MCP server instance
mcp = FastMCP(
    "Windows Crate Docs Search",
    description="Provides tools to search the Rust Windows crate API documentation.",
    # Add dependencies required by the docs module
    dependencies=["playwright"],
    # Define return types for better schema generation
    response_models={
        "search_windows_api": SearchResult,
        "ApiDocumentation": ApiDocumentation,
        "RelatedApiFunction": RelatedApiFunction,
    },
)


@mcp.tool()
async def search_windows_api(query: str, ctx: Context[typing.Any, typing.Any]) -> SearchResult:
    """Search the Rust Windows crate API documentation (https://microsoft.github.io/windows-docs-rs/).

    An example query is "CreateFile", "VirtualAlloc", "FILE_DISPOSITION_INFORMATION".
    You can't search for multiple objects at once, e.g. "CreateFile VirtualAlloc" will not work.

    Args:
        query: The search term for a single object in the Windows API documentation (e.g., function name, struct name).
        ctx: The MCP context (unused here).

    Returns:
        A SearchResult object containing an optional exact match documentation
        and a list of related API functions found in the search results.
    """
    # The windows_docs_client context manager creates an isolated
    # Playwright browser instance for each call to make sure it's thread safe.
    async with windows_docs_client() as client:
        search_result: SearchResult = await client.search_api(query)
        return search_result


def main():
    """Main entry point to run the MCP server."""
    skip_install = os.getenv("MCP_SKIP_PLAYWRIGHT_INSTALL", "false").lower() in (
        "true",
        "1",
        "yes",
    )

    if not skip_install:
        browser_to_install = os.getenv("MCP_PLAYWRIGHT_BROWSER", "chromium")

        try:
            print(
                f"Ensuring Playwright {browser_to_install} browser is installed..."
                " (Set MCP_SKIP_PLAYWRIGHT_INSTALL=true to skip)"
            )
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", browser_to_install],
                check=True,
            )
            print(f"Browser check complete for {browser_to_install}.")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(
                f"Warning: Failed to automatically install/verify Playwright {browser_to_install} browser: {e}"
            )
            print(
                f"Please ensure Playwright browsers are installed (`playwright install {browser_to_install}`)"
            )
    else:
        print(
            "Skipping Playwright browser installation due to MCP_SKIP_PLAYWRIGHT_INSTALL setting."
        )

    print("Starting MCP server...")
    mcp.run()


if __name__ == "__main__":
    main()