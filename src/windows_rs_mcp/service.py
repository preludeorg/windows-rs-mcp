"""Business logic layer for Windows documentation search."""

import logging

from pydantic import BaseModel, Field

from windows_rs_mcp.browser_manager import BrowserManager
from windows_rs_mcp.cache import CacheManager
from windows_rs_mcp.config import Config
from windows_rs_mcp.scraper import (
    DocPage,
    DocsScraper,
    DocumentationNotFoundError,
    FeatureNotFoundError,
)
from windows_rs_mcp.search_index import SearchIndexClient

logger = logging.getLogger(__name__)


class RelatedApiFunction(BaseModel):
    """Model representing a related API function."""

    type: str = Field(description="Type of the API item (function, struct, etc)")
    name: str = Field(description="Full path of the API item")


class ApiDocumentation(BaseModel):
    """Model representing the API documentation."""

    title: str = Field(description="Title of the API documentation")
    description: str | None = Field(
        description="Description from the documentation block", default=None
    )
    signature: str | None = Field(
        description="Type signature or declaration", default=None
    )
    content: str = Field(description="Main content of the documentation")
    feature: str | None = Field(
        description="Required Cargo feature for this API", default=None
    )


class SearchResult(BaseModel):
    """Model representing the search results."""

    exact_match: ApiDocumentation | None = Field(
        description="Documentation for exact API match if found", default=None
    )
    related_functions: list[RelatedApiFunction] = Field(
        description="List of related API functions", default_factory=list
    )


class SearchService:
    """High-level service for searching Windows documentation."""

    def __init__(
        self,
        browser_manager: BrowserManager,
        config: Config,
        cache: CacheManager,
        search_index: SearchIndexClient,
    ) -> None:
        """Initialize the search service.

        Args:
            browser_manager: Browser manager instance
            config: Configuration object
            cache: Cache manager instance
            search_index: Search index client for fast lookups
        """
        self._browser_manager = browser_manager
        self._config = config
        self._cache = cache
        self._search_index = search_index
        self._scraper = DocsScraper(config)

    async def search_api(self, search_term: str, max_related: int = 5) -> SearchResult:
        """Search the Windows API documentation.

        Args:
            search_term: The search term (e.g., "CreateFile", "VirtualAlloc")
            max_related: Maximum number of related functions to return

        Returns:
            SearchResult with exact match and related functions

        Raises:
            Exception: If search fails
        """
        try:
            # Check cache for search results
            cache_key = f"search:{search_term}:{max_related}"
            cached_result = await self._cache.search_cache.get(cache_key)
            if cached_result is not None:
                logger.info(f"Cache hit for search: {search_term}")
                return cached_result

            # Search using the features page (gives full namespace paths)
            async with self._browser_manager.get_features_page() as page:
                search_results = await self._scraper.search_features_page(
                    page, search_term, max_results=max_related
                )

            if not search_results:
                empty_result = SearchResult(related_functions=[])
                await self._cache.search_cache.set(cache_key, empty_result)
                return empty_result

            # Build related functions list and find exact match
            related_functions: list[RelatedApiFunction] = []
            exact_match_result: dict | None = None

            for result_item in search_results:
                related_functions.append(
                    RelatedApiFunction(
                        type=result_item["type"], name=result_item["full_path"]
                    )
                )

                # Check for exact name match (first result is usually exact match)
                if (
                    exact_match_result is None
                    and result_item["name"].lower() == search_term.lower()
                ):
                    exact_match_result = result_item

            # Get exact match documentation if found
            exact_match = None
            if exact_match_result:
                try:
                    # Determine item type by checking the docs page
                    full_path = exact_match_result["full_path"]
                    item_type = await self._determine_item_type(full_path)
                    exact_match_result["type"] = item_type

                    # Update the related functions with the correct type
                    for i, func in enumerate(related_functions):
                        if func.name == full_path:
                            related_functions[i] = RelatedApiFunction(
                                type=item_type, name=full_path
                            )
                            break

                    # Get documentation
                    exact_match = await self._get_api_details(
                        full_path, exact_match_result["feature"], item_type
                    )
                except (DocumentationNotFoundError, FeatureNotFoundError) as e:
                    logger.warning(
                        f"Failed to get details for exact match {full_path}: {e}"
                    )

            result = SearchResult(
                exact_match=exact_match, related_functions=related_functions
            )

            # Cache the result
            await self._cache.search_cache.set(cache_key, result)

            return result

        except Exception as e:
            logger.error(f"Search failed for '{search_term}': {e}")
            raise

    async def _determine_item_type(self, full_path: str) -> str:
        """Determine the item type by trying to load docs with different type prefixes.

        Args:
            full_path: Full API path (e.g., windows::Win32::System::Memory::VirtualAlloc)

        Returns:
            Item type ("fn", "struct", "constant", etc.)
        """
        # Try types in order of likelihood
        types_to_try = ["fn", "struct", "type", "constant", "enum", "union", "trait"]

        for item_type in types_to_try:
            try:
                url = self._scraper.full_path_to_doc_url(full_path, item_type)
                # Try a quick HEAD request to see if the page exists
                async with self._browser_manager.get_docs_page() as page:
                    response = await page.goto(
                        url, timeout=5000, wait_until="domcontentloaded"
                    )
                    if response and response.status == 200:
                        # Check if it's actually a valid docs page
                        has_content = await page.locator("#main-content").count() > 0
                        if has_content:
                            logger.info(
                                f"Determined type '{item_type}' for {full_path}"
                            )
                            return item_type
            except Exception:
                continue

        # Default to function
        logger.warning(f"Could not determine type for {full_path}, defaulting to 'fn'")
        return "fn"

    async def _get_api_details(
        self, api_path: str, feature: str | None = None, item_type: str = "fn"
    ) -> ApiDocumentation:
        """Get detailed documentation for an API.

        Args:
            api_path: Full API path
            feature: Cargo feature (if already known)
            item_type: Type of item (fn, struct, etc.)

        Returns:
            ApiDocumentation with details

        Raises:
            DocumentationNotFoundError: If documentation cannot be found
        """
        # Check doc cache
        doc_cache_key = f"doc:{api_path}:{item_type}"
        cached_doc = await self._cache.doc_cache.get(doc_cache_key)
        if cached_doc is not None:
            logger.info(f"Cache hit for documentation: {api_path}")
            return cached_doc

        # Get documentation page
        doc_page = await self._get_doc_page_direct(api_path, item_type)

        # Get feature if not provided
        if not feature:
            try:
                feature = await self._get_feature_for_api(api_path)
            except Exception as e:
                logger.warning(f"Could not get feature for {api_path}: {e}")
                # Fallback feature generation
                module_path = "::".join(
                    self._scraper.convert_doc_to_feature_path(api_path).split("::")[:-1]
                )
                feature = "_".join(module_path.split("::"))

        api_doc = ApiDocumentation(
            title=doc_page.title,
            description=doc_page.description,
            signature=doc_page.signature,
            content=doc_page.content,
            feature=feature,
        )

        # Cache the result
        await self._cache.doc_cache.set(doc_cache_key, api_doc)

        return api_doc

    async def _get_doc_page_direct(self, api_path: str, item_type: str) -> DocPage:
        """Get documentation page content directly by URL.

        Args:
            api_path: Full API path
            item_type: Type of item (fn, struct, etc.)

        Returns:
            DocPage content

        Raises:
            DocumentationNotFoundError: If documentation cannot be found
        """
        cache_key = f"docpage:{api_path}:{item_type}"
        cached = await self._cache.doc_cache.get(cache_key)
        if cached is not None:
            return cached

        # Generate URL directly
        url = self._scraper.full_path_to_doc_url(api_path, item_type)

        async with self._browser_manager.get_docs_page() as page:
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self._config.page_load_timeout,
            )

            if not response or response.status != 200:
                raise DocumentationNotFoundError(
                    f"Failed to load page: {response.status if response else 'No response'}"
                )

            # Verify page loaded correctly
            has_content = await page.locator("#main-content").count() > 0
            if not has_content:
                raise DocumentationNotFoundError(
                    "Page loaded but #main-content not found"
                )

            # Extract content
            doc_page = await self._scraper._extract_doc_content(page)

        await self._cache.doc_cache.set(cache_key, doc_page)
        return doc_page

    async def _get_doc_page(self, api_path: str) -> DocPage:
        """Get documentation page content with caching.

        Args:
            api_path: Full API path

        Returns:
            DocPage content
        """
        cache_key = f"docpage:{api_path}"
        cached = await self._cache.doc_cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._browser_manager.get_docs_page() as page:
            doc_page = await self._scraper.get_doc_page_content(page, api_path)

        await self._cache.doc_cache.set(cache_key, doc_page)
        return doc_page

    async def _get_feature_for_api(self, api_path: str) -> str | None:
        """Get the Cargo feature for an API with caching.

        Args:
            api_path: Full API path

        Returns:
            Feature name or None
        """
        feature_path = self._scraper.convert_doc_to_feature_path(api_path)
        cache_key = f"feature:{feature_path}"

        cached = await self._cache.feature_cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._browser_manager.get_features_page() as page:
            feature = await self._scraper.find_feature_for_api(page, feature_path)

        await self._cache.feature_cache.set(cache_key, feature)
        return feature
