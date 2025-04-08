import asyncio
import types
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from playwright.async_api import (
    Browser,
    Page,
    Playwright,
    async_playwright,
)
from pydantic import BaseModel, Field

# Constants
WINDOWS_DOCS_BASE_URL = (
    "https://microsoft.github.io/windows-docs-rs/doc/windows/index.html"
)
FEATURES_URL = "https://microsoft.github.io/windows-rs/features"
SEARCH_TIMEOUT = 2000
PAGE_LOAD_TIMEOUT = 10000
ELEMENT_TIMEOUT = 5000
RESULTS_TIMEOUT = 5000


@dataclass(frozen=True)
class WindowsApiItem:
    type: str
    full_name: str


@dataclass(frozen=True)
class DocPage:
    title: str
    description: str | None
    signature: str | None
    content: str


class WindowsDocError(Exception):
    pass


class DocumentationNotFoundError(WindowsDocError):
    pass


class SearchResultsError(WindowsDocError):
    pass


class WindowsFeatureError(Exception):
    pass


class FeatureNotFoundError(WindowsFeatureError):
    pass


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


class WindowsDocsClient:
    def __init__(self) -> None:
        self._browser: Browser | None = None
        self._playwright: Playwright | None = None
        self._features_page: Page | None = None
        self._docs_page: Page | None = None

        # Caches
        self._doc_cache: dict[str, DocPage] = {}
        self._feature_cache: dict[str, str | None] = {}
        self._search_cache: dict[str, tuple[list[WindowsApiItem], float]] = {}
        self._cache_ttl = 300  # 5 minutes in seconds
        self._last_search_time = 0.0

    async def __aenter__(self) -> "WindowsDocsClient":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]
        )

        # Initialize both pages in parallel
        self._docs_page, self._features_page = await asyncio.gather(
            self._browser.new_page(), self._browser.new_page()
        )

        # Load initial pages in parallel
        if self._docs_page and self._features_page:
            await asyncio.gather(
                self._docs_page.goto(WINDOWS_DOCS_BASE_URL),
                self._features_page.goto(FEATURES_URL),
            )
        else:
            raise WindowsDocError("Pages not initialized properly")

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    @staticmethod
    def _is_exact_match(search_term: str, api_path: str) -> bool:
        api_name = api_path.split("::")[-1]
        return search_term == api_name

    async def get_search_results(self) -> tuple[str | None, list[str]]:
        if not self._docs_page:
            raise WindowsDocError("Documentation page not initialized")

        try:
            results_element = self._docs_page.locator(".search-results.active")
            paths_elements = self._docs_page.locator("div.path")

            results_text = await results_element.text_content()
            paths = await paths_elements.all_text_contents()

            return results_text, [p.strip() for p in paths if p.strip()]
        except Exception as e:
            raise SearchResultsError(
                f"Failed to process search results: {str(e)}"
            ) from e

    async def _find_matching_row(self, api_path: str) -> str | None:
        if api_path in self._feature_cache:
            return self._feature_cache[api_path]

        if not self._features_page:
            raise WindowsFeatureError("Features page not initialized")

        try:
            search_input = self._features_page.locator("input.fui-Input__input")
            await search_input.fill(api_path)
            await self._features_page.wait_for_timeout(SEARCH_TIMEOUT)

            rows = self._features_page.locator(
                ".fui-DataGridRow:not(.fui-DataGridHeader .fui-DataGridRow)"
            )
            count = await rows.count()

            feature = None
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
                        feature = feature.strip().strip('"') if feature else None
                        break

            self._feature_cache[api_path] = feature
            return feature

        except Exception as e:
            raise WindowsFeatureError(f"Error finding feature: {str(e)}") from e

    @staticmethod
    def _convert_doc_to_feature_path(doc_path: str) -> str:
        if doc_path.startswith("windows::"):
            doc_path = doc_path[9:]
        return doc_path

    async def _get_doc_page_content(self, api_path: str) -> DocPage:
        """Get documentation page content using search with correct URL handling."""
        if api_path in self._doc_cache:
            return self._doc_cache[api_path]

        if not self._docs_page:
            raise WindowsDocError("Documentation page not initialized")

        try:
            # Use search to get the right URL
            base_url = "https://microsoft.github.io/windows-docs-rs/doc"
            search_url = (
                f"{base_url}/windows/index.html?search={api_path.split('::')[-1]}"
            )

            await self._docs_page.goto(search_url)
            await self._docs_page.wait_for_selector(".search-results.active")

            # Find the correct link
            links = await self._docs_page.locator(".search-results a").all()
            target_url = None

            for link in links:
                path = await link.locator(".path").text_content()
                if path and path.strip() == api_path:
                    href = await link.get_attribute("href")
                    if href:
                        # Construct correct URL with 'windows' namespace
                        if href.startswith(".."):
                            target_url = (
                                f"{base_url}/windows/{href.replace('../windows/', '')}"
                            )
                        elif href.startswith("/"):
                            target_url = f"https://microsoft.github.io{href}"
                        else:
                            target_url = href
                        break

            if not target_url:
                raise DocumentationNotFoundError(
                    f"Could not find documentation for {api_path}"
                )

            response = await self._docs_page.goto(
                target_url, wait_until="domcontentloaded"
            )

            if not response or response.status != 200:
                raise DocumentationNotFoundError(
                    f"Failed to load page: {response.status if response else 'No response'}"
                )

            # Verify page structure
            structure = await self._docs_page.evaluate("""() => {
                return {
                    hasContent: !!document.querySelector('#main-content'),
                    hasTitle: !!document.querySelector('h1'),
                    url: window.location.href
                }
            }""")

            if not structure["hasContent"]:
                raise DocumentationNotFoundError(
                    "Page loaded but #main-content not found"
                )

            # Extract content
            title, content, signature, description = await asyncio.gather(
                self._docs_page.title(),
                self._docs_page.locator("#main-content").text_content(),
                self._get_optional_content(".item-decl"),
                self._get_optional_content(".docblock"),
            )

            doc_page = DocPage(
                title=title.strip(),
                description=description.strip() if description else None,
                signature=signature.strip() if signature else None,
                content=content.strip() if content else "",
            )

            self._doc_cache[api_path] = doc_page
            return doc_page

        except Exception as e:
            if isinstance(e, DocumentationNotFoundError):
                raise
            raise DocumentationNotFoundError(f"Failed to get documentation: {str(e)}")

    async def _get_optional_content(self, selector: str) -> str | None:
        """Helper method to safely extract optional content."""
        try:
            if not self._docs_page:
                raise WindowsDocError("Documentation page not initialized")
            elements = await self._docs_page.locator(selector).all()
            if not elements:
                return None

            # Try to get text content from the first element
            content = await elements[0].text_content()
            return content if content else None
        except Exception:
            return None

    async def search_windows_docs(self, search_term: str) -> list[WindowsApiItem]:
        if not self._docs_page:
            raise WindowsDocError("Documentation page not initialized")

        try:
            await self._docs_page.goto(WINDOWS_DOCS_BASE_URL)
            search_input = await self._docs_page.wait_for_selector(".search-input")
            if search_input:
                await search_input.fill(search_term)
            else:
                raise WindowsDocError("Search input element not found")
            await search_input.press("Enter")
            await self._docs_page.wait_for_timeout(SEARCH_TIMEOUT)

            results_text, paths = await self.get_search_results()

            if not results_text:
                return []

            items: list[WindowsApiItem] = []
            current_text = results_text

            for path in paths:
                path_pos = current_text.find(path)
                if path_pos == -1:
                    continue

                type_text = current_text[:path_pos].strip().split()[-1]
                items.append(WindowsApiItem(type=type_text, full_name=path))
                current_text = current_text[path_pos + len(path) :]

            return items

        except Exception as e:
            raise WindowsDocError(f"Search failed: {str(e)}") from e

    async def _get_api_details(self, api_path: str) -> ApiDocumentation:
        # Run documentation and feature lookups in parallel
        doc_page, feature = await asyncio.gather(
            self._get_doc_page_content(api_path),
            self._find_matching_row(self._convert_doc_to_feature_path(api_path)),
        )

        if not feature:
            # Fallback to module path if feature not found
            module_path = "::".join(
                self._convert_doc_to_feature_path(api_path).split("::")[:-1]
            )
            feature = "_".join(module_path.split("::"))

        return ApiDocumentation(
            title=doc_page.title,
            description=doc_page.description,
            signature=doc_page.signature,
            content=doc_page.content,
            feature=feature,
        )

    async def search_api(self, search_term: str, max_related: int = 5) -> SearchResult:
        try:
            results = await self.search_windows_docs(search_term)

            if not results:
                return SearchResult(related_functions=[])

            related_functions: list[RelatedApiFunction] = []
            exact_match = None

            # Process results and initiate exact match lookup early if found
            exact_match_task = None
            for item in results[:max_related]:
                related_functions.append(
                    RelatedApiFunction(type=item.type, name=item.full_name)
                )

                if exact_match_task is None and self._is_exact_match(
                    search_term, item.full_name
                ):
                    exact_match_task = asyncio.create_task(
                        self._get_api_details(item.full_name)
                    )

            # Wait for exact match lookup if it was initiated
            if exact_match_task:
                try:
                    exact_match = await exact_match_task
                except (DocumentationNotFoundError, FeatureNotFoundError):
                    pass

            return SearchResult(
                exact_match=exact_match,
                related_functions=related_functions,
            )

        except Exception as e:
            if "timeout" in str(e).lower():
                return SearchResult(related_functions=[])
            raise DocumentationNotFoundError(f"Search failed: {str(e)}")


@asynccontextmanager
async def windows_docs_client() -> AsyncIterator[WindowsDocsClient]:
    """Context manager for creating and managing a WindowsDocsClient instance."""
    client = WindowsDocsClient()
    try:
        async with client:
            yield client
    finally:
        pass


__all__ = [
    "windows_docs_client",
    "SearchResult",
    "RelatedApiFunction",
    "ApiDocumentation",
]
