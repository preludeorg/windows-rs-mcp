"""Configuration management for the Windows RS MCP server."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Server configuration loaded from environment variables."""

    # Playwright settings
    skip_playwright_install: bool
    playwright_browser: str

    # Timeouts (in milliseconds)
    search_timeout: int
    page_load_timeout: int
    element_timeout: int
    results_timeout: int

    # Cache settings
    cache_ttl: int  # seconds
    enable_cache: bool

    # URLs
    windows_docs_base_url: str
    features_url: str
    search_index_url: str

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables with sensible defaults."""
        return cls(
            # Playwright settings
            skip_playwright_install=os.getenv(
                "MCP_SKIP_PLAYWRIGHT_INSTALL", "false"
            ).lower()
            in ("true", "1", "yes"),
            playwright_browser=os.getenv("MCP_PLAYWRIGHT_BROWSER", "chromium"),
            # Timeouts
            search_timeout=int(os.getenv("MCP_SEARCH_TIMEOUT", "2000")),
            page_load_timeout=int(os.getenv("MCP_PAGE_LOAD_TIMEOUT", "10000")),
            element_timeout=int(os.getenv("MCP_ELEMENT_TIMEOUT", "5000")),
            results_timeout=int(os.getenv("MCP_RESULTS_TIMEOUT", "5000")),
            # Cache settings
            cache_ttl=int(os.getenv("MCP_CACHE_TTL", "300")),  # 5 minutes default
            enable_cache=os.getenv("MCP_ENABLE_CACHE", "true").lower()
            in ("true", "1", "yes"),
            # URLs
            windows_docs_base_url=os.getenv(
                "MCP_WINDOWS_DOCS_URL",
                "https://microsoft.github.io/windows-docs-rs/doc/windows/index.html",
            ),
            features_url=os.getenv(
                "MCP_FEATURES_URL", "https://microsoft.github.io/windows-rs/features"
            ),
            search_index_url=os.getenv(
                "MCP_SEARCH_INDEX_URL",
                "https://microsoft.github.io/windows-docs-rs/doc/search-index.js",
            ),
        )
