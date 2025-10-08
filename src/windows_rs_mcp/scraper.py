"""Scraping logic for Windows documentation pages."""

import asyncio
import logging
from dataclasses import dataclass

from playwright.async_api import Page

from windows_rs_mcp.config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocPage:
    """Represents a documentation page content."""

    title: str
    description: str | None
    signature: str | None
    content: str


class WindowsDocError(Exception):
    """Base exception for documentation errors."""

    pass


class DocumentationNotFoundError(WindowsDocError):
    """Raised when documentation cannot be found."""

    pass


class SearchResultsError(WindowsDocError):
    """Raised when search results cannot be processed."""

    pass


class WindowsFeatureError(Exception):
    """Base exception for feature lookup errors."""

    pass


class FeatureNotFoundError(WindowsFeatureError):
    """Raised when feature cannot be found."""

    pass


class DocsScraper:
    """Handles scraping of Windows documentation pages."""

    def __init__(self, config: Config) -> None:
        """Initialize the scraper.

        Args:
            config: Configuration object
        """
        self._config = config

    async def get_doc_page_content(self, page: Page, api_path: str) -> DocPage:
        """Get documentation page content for an API item.

        Args:
            page: Playwright page to use
            api_path: Full API path (e.g., windows::Win32::Foundation::CreateFileA)

        Returns:
            DocPage with the documentation content

        Raises:
            DocumentationNotFoundError: If documentation cannot be found
        """
        try:
            # Use search to find the correct URL
            base_url = "https://microsoft.github.io/windows-docs-rs/doc"
            search_url = (
                f"{base_url}/windows/index.html?search={api_path.split('::')[-1]}"
            )

            await page.goto(search_url, timeout=self._config.page_load_timeout)
            await page.wait_for_selector(
                ".search-results.active", timeout=self._config.results_timeout
            )

            # Find the correct link
            target_url = await self._find_doc_url(page, api_path, base_url)

            if not target_url:
                raise DocumentationNotFoundError(
                    f"Could not find documentation for {api_path}"
                )

            # Navigate to documentation page
            response = await page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=self._config.page_load_timeout,
            )

            if not response or response.status != 200:
                raise DocumentationNotFoundError(
                    f"Failed to load page: {response.status if response else 'No response'}"
                )

            # Verify page loaded correctly
            await self._verify_page_structure(page)

            # Extract content
            return await self._extract_doc_content(page)

        except DocumentationNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to get documentation for {api_path}: {e}")
            raise DocumentationNotFoundError(
                f"Failed to get documentation: {str(e)}"
            ) from e

    async def _find_doc_url(
        self, page: Page, api_path: str, base_url: str
    ) -> str | None:
        """Find the documentation URL for an API path.

        Args:
            page: Playwright page with search results
            api_path: API path to find
            base_url: Base URL for documentation

        Returns:
            Documentation URL or None if not found
        """
        links = await page.locator(".search-results a").all()

        for link in links:
            path = await link.locator(".path").text_content()
            if path and path.strip() == api_path:
                href = await link.get_attribute("href")
                if not href:
                    continue

                # Construct correct URL
                if href.startswith(".."):
                    return f"{base_url}/windows/{href.replace('../windows/', '')}"
                elif href.startswith("/"):
                    return f"https://microsoft.github.io{href}"
                else:
                    return href

        return None

    async def _verify_page_structure(self, page: Page) -> None:
        """Verify the documentation page has expected structure.

        Args:
            page: Playwright page to verify

        Raises:
            DocumentationNotFoundError: If page structure is invalid
        """
        structure = await page.evaluate(
            """() => {
            return {
                hasContent: !!document.querySelector('#main-content'),
                hasTitle: !!document.querySelector('h1'),
                url: window.location.href
            }
        }"""
        )

        if not structure["hasContent"]:
            raise DocumentationNotFoundError("Page loaded but #main-content not found")

    async def _extract_doc_content(self, page: Page) -> DocPage:
        """Extract documentation content from page.

        Args:
            page: Playwright page with documentation

        Returns:
            DocPage with extracted content
        """
        title, content, signature, description = await asyncio.gather(
            page.title(),
            page.locator("#main-content").text_content(),
            self._get_optional_content(page, ".item-decl"),
            self._get_optional_content(page, ".docblock"),
        )

        return DocPage(
            title=title.strip(),
            description=description.strip() if description else None,
            signature=signature.strip() if signature else None,
            content=content.strip() if content else "",
        )

    async def _get_optional_content(self, page: Page, selector: str) -> str | None:
        """Safely extract optional content from page.

        Args:
            page: Playwright page
            selector: CSS selector

        Returns:
            Content text or None if not found
        """
        try:
            elements = await page.locator(selector).all()
            if not elements:
                return None

            content = await elements[0].text_content()
            return content if content else None
        except Exception:
            return None

    async def find_feature_for_api(self, page: Page, api_path: str) -> str | None:
        """Find the Cargo feature required for an API.

        Args:
            page: Playwright page (features page)
            api_path: API path to look up

        Returns:
            Feature name or None if not found

        Raises:
            WindowsFeatureError: If lookup fails
        """
        try:
            # Search in the features table
            search_input = page.locator("input.fui-Input__input")
            await search_input.fill(api_path)
            await page.wait_for_timeout(self._config.search_timeout)

            # Find matching row
            rows = page.locator(
                ".fui-DataGridRow:not(.fui-DataGridHeader .fui-DataGridRow)"
            )
            count = await rows.count()

            for i in range(count):
                row = rows.nth(i)
                cells = row.locator(".fui-DataGridCell")
                name_cell = cells.first

                if await name_cell.count() == 0:
                    continue

                name = await name_cell.evaluate('cell => cell.textContent || ""')
                name = name.strip()

                if name == api_path:
                    feature_cell = cells.nth(1)
                    if await feature_cell.count() > 0:
                        feature = await feature_cell.evaluate(
                            'cell => cell.textContent || ""'
                        )
                        return feature.strip().strip('"') if feature else None

            return None

        except Exception as e:
            logger.error(f"Error finding feature for {api_path}: {e}")
            raise WindowsFeatureError(f"Error finding feature: {str(e)}") from e

    async def search_features_page(
        self, page: Page, search_term: str, max_results: int = 10
    ) -> list[dict[str, str]]:
        """Search the features page for APIs.

        Args:
            page: Playwright page (features page)
            search_term: The search term (e.g., "VirtualAlloc")
            max_results: Maximum number of results to return

        Returns:
            List of dicts with 'name', 'full_path', 'feature', and 'type' keys

        Raises:
            SearchResultsError: If search fails
        """
        try:
            # Find and use the search input box
            search_input = page.locator("input.fui-Input__input")
            input_count = await search_input.count()

            if input_count == 0:
                raise SearchResultsError("Search input not found on features page")

            # Clear and type in the search box
            await search_input.fill(search_term)

            # Wait for results to populate
            await page.wait_for_timeout(self._config.search_timeout)

            # Extract results from the table
            results = []
            rows = page.locator(
                ".fui-DataGridRow:not(.fui-DataGridHeader .fui-DataGridRow)"
            )
            count = await rows.count()

            for i in range(min(count, max_results)):
                row = rows.nth(i)
                cells = row.locator(".fui-DataGridCell")

                if await cells.count() < 2:
                    continue

                # Get name/path from first cell
                name_cell = cells.first
                full_path = await name_cell.text_content()
                full_path = full_path.strip() if full_path else ""

                # Get feature from second cell
                feature_cell = cells.nth(1)
                feature = await feature_cell.text_content()
                feature = feature.strip().strip('"') if feature else ""

                if full_path and feature:
                    # Extract just the name from the full path
                    name = full_path.split("::")[-1]

                    # Determine type based on naming conventions or lookup
                    # We'll need to determine this from the docs page later
                    item_type = "unknown"

                    results.append(
                        {
                            "name": name,
                            "full_path": f"windows::{full_path}",
                            "feature": feature,
                            "type": item_type,
                        }
                    )

            return results

        except Exception as e:
            logger.error(f"Failed to search features page for '{search_term}': {e}")
            raise SearchResultsError(f"Search failed: {str(e)}") from e

    @staticmethod
    def convert_doc_to_feature_path(doc_path: str) -> str:
        """Convert documentation path to feature lookup path.

        Args:
            doc_path: Documentation path (e.g., windows::Win32::Foundation::CreateFileA)

        Returns:
            Feature path (e.g., Win32::Foundation::CreateFileA)
        """
        if doc_path.startswith("windows::"):
            return doc_path[9:]
        return doc_path

    @staticmethod
    def full_path_to_doc_url(full_path: str, item_type: str) -> str:
        """Convert full path to documentation URL.

        Args:
            full_path: Full path (e.g., windows::Win32::System::Memory::VirtualAlloc)
            item_type: Type of item ("fn", "struct", "constant", "type", "union", "enum", etc.)

        Returns:
            Documentation URL
        """
        # Remove windows:: prefix and split
        if full_path.startswith("windows::"):
            path = full_path[9:]
        else:
            path = full_path

        parts = path.split("::")
        name = parts[-1]
        namespace_parts = parts[:-1]

        # Convert :: to / for URL
        namespace_path = "/".join(namespace_parts)

        # Map types to URL prefixes
        type_map = {
            "fn": "fn",
            "function": "fn",
            "struct": "struct",
            "constant": "constant",
            "type": "type",
            "union": "union",
            "enum": "enum",
            "trait": "trait",
            "mod": "index",
            "module": "index",
        }

        type_prefix = type_map.get(item_type.lower(), "fn")

        base_url = "https://microsoft.github.io/windows-docs-rs/doc/windows"
        return f"{base_url}/{namespace_path}/{type_prefix}.{name}.html"
