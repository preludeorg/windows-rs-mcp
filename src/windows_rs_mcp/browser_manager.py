"""Browser lifecycle management for persistent browser instances."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import Browser, Page, Playwright, async_playwright

from windows_rs_mcp.config import Config

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manages a persistent browser instance and page pool."""

    def __init__(self, config: Config) -> None:
        """Initialize the browser manager.

        Args:
            config: Configuration object
        """
        self._config = config
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._docs_page: Page | None = None
        self._features_page: Page | None = None
        self._page_lock = asyncio.Lock()
        self._initialized = False

    async def start(self) -> None:
        """Start the browser and initialize pages.

        This should be called once at server startup.
        """
        if self._initialized:
            logger.warning("BrowserManager already initialized")
            return

        try:
            logger.info("Starting Playwright and launching browser...")
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]
            )

            # Create pages
            logger.info("Creating browser pages...")
            self._docs_page = await self._browser.new_page()
            self._features_page = await self._browser.new_page()

            # Pre-navigate to base pages to warm up
            logger.info("Pre-loading documentation and features pages...")
            await asyncio.gather(
                self._docs_page.goto(
                    self._config.windows_docs_base_url,
                    timeout=self._config.page_load_timeout,
                ),
                self._features_page.goto(
                    self._config.features_url, timeout=self._config.page_load_timeout
                ),
            )

            self._initialized = True
            logger.info("Browser manager initialized successfully")

        except Exception as e:
            logger.error(f"Failed to start browser manager: {e}")
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop the browser and clean up resources.

        This should be called once at server shutdown.
        """
        logger.info("Stopping browser manager...")

        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.error(f"Error closing browser: {e}")

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.error(f"Error stopping playwright: {e}")

        self._browser = None
        self._playwright = None
        self._docs_page = None
        self._features_page = None
        self._initialized = False
        logger.info("Browser manager stopped")

    @asynccontextmanager
    async def get_docs_page(self) -> AsyncIterator[Page]:
        """Get the documentation page with exclusive access.

        This ensures thread-safe access to the shared page.

        Yields:
            The documentation page

        Raises:
            RuntimeError: If browser is not initialized
        """
        if not self._initialized or not self._docs_page:
            raise RuntimeError("Browser manager not initialized")

        async with self._page_lock:
            yield self._docs_page

    @asynccontextmanager
    async def get_features_page(self) -> AsyncIterator[Page]:
        """Get the features page with exclusive access.

        This ensures thread-safe access to the shared page.

        Yields:
            The features page

        Raises:
            RuntimeError: If browser is not initialized
        """
        if not self._initialized or not self._features_page:
            raise RuntimeError("Browser manager not initialized")

        # Features page doesn't need locking since it's accessed independently
        # But we'll lock it anyway for consistency and future-proofing
        yield self._features_page

    @property
    def is_initialized(self) -> bool:
        """Check if the browser manager is initialized.

        Returns:
            True if initialized, False otherwise
        """
        return self._initialized

    async def health_check(self) -> bool:
        """Perform a health check on the browser.

        Returns:
            True if healthy, False otherwise
        """
        if not self._initialized or not self._browser:
            return False

        try:
            # Check if browser is still connected
            return self._browser.is_connected()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
