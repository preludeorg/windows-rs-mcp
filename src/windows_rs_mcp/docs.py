"""Backward compatibility module - re-exports models from the new architecture.

This module maintains backward compatibility for any code that imported
from the old docs.py module. New code should import directly from the
appropriate modules (service, scraper, etc.).

The old WindowsDocsClient class has been removed. Use the new architecture
via the SearchService in the main __init__.py module instead.
"""

# Re-export models for backward compatibility
from windows_rs_mcp.scraper import (
    DocPage,
    DocumentationNotFoundError,
    FeatureNotFoundError,
    SearchResultsError,
    WindowsDocError,
    WindowsFeatureError,
)
from windows_rs_mcp.search_index import SearchIndexItem as WindowsApiItem
from windows_rs_mcp.service import (
    ApiDocumentation,
    RelatedApiFunction,
    SearchResult,
)

__all__ = [
    "SearchResult",
    "RelatedApiFunction",
    "ApiDocumentation",
    "WindowsApiItem",
    "DocPage",
    "WindowsDocError",
    "DocumentationNotFoundError",
    "SearchResultsError",
    "WindowsFeatureError",
    "FeatureNotFoundError",
]
