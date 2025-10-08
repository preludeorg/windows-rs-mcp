import argparse
import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from windows_rs_mcp.browser_manager import BrowserManager
from windows_rs_mcp.cache import CacheManager
from windows_rs_mcp.config import Config
from windows_rs_mcp.search_index import SearchIndexClient
from windows_rs_mcp.service import SearchResult, SearchService

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global instances (initialized in lifespan)
_browser_manager: BrowserManager | None = None
_search_service: SearchService | None = None
_cache_manager: CacheManager | None = None
_search_index_client: SearchIndexClient | None = None
_cleanup_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(server) -> AsyncIterator[None]:
    """Lifespan context manager for server startup and shutdown.

    This manages the browser instance lifecycle, ensuring the browser
    is created once at startup and properly cleaned up at shutdown.
    """
    global \
        _browser_manager, \
        _search_service, \
        _cache_manager, \
        _search_index_client, \
        _cleanup_task

    # Load configuration
    config = Config.from_env()
    logger.info("Starting Windows RS MCP Server...")

    try:
        # Initialize cache manager
        _cache_manager = CacheManager(ttl=config.cache_ttl, enabled=config.enable_cache)
        logger.info(
            f"Cache initialized (enabled={config.enable_cache}, ttl={config.cache_ttl}s)"
        )

        # Initialize and load search index
        _search_index_client = SearchIndexClient(
            index_url=config.search_index_url, cache_ttl=config.cache_ttl
        )
        await _search_index_client.load()
        logger.info(
            f"Search index loaded ({_search_index_client.item_count} items indexed)"
        )

        # Initialize browser manager
        _browser_manager = BrowserManager(config)
        await _browser_manager.start()

        # Initialize search service
        _search_service = SearchService(
            _browser_manager, config, _cache_manager, _search_index_client
        )
        logger.info("Search service initialized")

        # Start background cache cleanup task
        _cleanup_task = asyncio.create_task(_periodic_cache_cleanup())

        logger.info("Server initialization complete")

        yield  # Server runs here

    finally:
        # Cleanup
        logger.info("Shutting down server...")

        if _cleanup_task:
            _cleanup_task.cancel()
            try:
                await _cleanup_task
            except asyncio.CancelledError:
                pass

        if _browser_manager:
            await _browser_manager.stop()

        if _cache_manager:
            await _cache_manager.clear_all()

        logger.info("Server shutdown complete")


async def _periodic_cache_cleanup() -> None:
    """Periodically clean up expired cache entries."""
    try:
        while True:
            await asyncio.sleep(60)  # Run every minute
            if _cache_manager:
                removed = await _cache_manager.cleanup_expired_all()
                if removed > 0:
                    logger.info(f"Cleaned up {removed} expired cache entries")
    except asyncio.CancelledError:
        logger.info("Cache cleanup task cancelled")
        raise


# Create the MCP server instance with lifespan
mcp = FastMCP("Windows Crate Docs Search", lifespan=lifespan)

@mcp.tool()
async def search_windows_api(query: str) -> SearchResult:
    """Search the Rust Windows crate API documentation (https://microsoft.github.io/windows-docs-rs/).

    An example query is "CreateFile", "VirtualAlloc", "FILE_DISPOSITION_INFORMATION".
    You can't search for multiple objects at once, e.g. "CreateFile VirtualAlloc" will not work.

    Args:
        query: The search term for a single object in the Windows API documentation (e.g., function name, struct name).

    Returns:
        A SearchResult object containing an optional exact match documentation
        and a list of related API functions found in the search results.
    """
    if _search_service is None:
        raise RuntimeError("Search service not initialized")

    return await _search_service.search_api(query)


def main():
    """Main entry point to run the MCP server."""
    parser = argparse.ArgumentParser(description="Windows RS MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport type (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind SSE server to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port for SSE server (default: 8000)"
    )

    args = parser.parse_args()

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

    if args.transport == "sse":
        print(f"Starting MCP server over SSE on {args.host}:{args.port}...")
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        print("Starting MCP server over stdio...")
        mcp.run()


if __name__ == "__main__":
    main()
