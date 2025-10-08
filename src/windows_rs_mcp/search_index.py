"""Search index client for fast API lookups without browser automation.

This module downloads and parses the rustdoc search index, enabling
sub-millisecond searches instead of multi-second browser automation.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchIndexItem:
    """An item from the search index."""

    type: str  # "struct", "fn", "enum", etc.
    path: str  # Full path like "windows::Win32::Storage::FileSystem"
    name: str  # Item name like "CreateFileA"
    full_path: str  # Complete path: path::name

    @property
    def display_type(self) -> str:
        """Get display-friendly type name."""
        type_map = {
            "fn": "function",
            "struct": "struct",
            "enum": "enum",
            "type": "type",
            "trait": "trait",
            "mod": "module",
            "constant": "constant",
            "static": "static",
        }
        return type_map.get(self.type, self.type)


class SearchIndexClient:
    """Client for searching the rustdoc search index."""

    def __init__(self, index_url: str, cache_ttl: int = 3600):
        """Initialize the search index client.

        Args:
            index_url: URL to the search-index.js file
            cache_ttl: Time to live for cached index in seconds
        """
        self._index_url = index_url
        self._cache_ttl = cache_ttl
        self._index_data: dict | None = None
        self._items_by_name: dict[str, list[SearchIndexItem]] = {}
        self._all_items: list[SearchIndexItem] = []
        self._loaded = False

    async def load(self) -> None:
        """Load and parse the search index.

        Raises:
            httpx.HTTPError: If download fails
            ValueError: If parsing fails
        """
        if self._loaded:
            logger.warning("Search index already loaded")
            return

        logger.info(f"Loading search index from {self._index_url}")
        start = asyncio.get_event_loop().time()

        try:
            # Download the index
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(self._index_url, follow_redirects=True)
                response.raise_for_status()
                content = response.text

            # Parse the JavaScript wrapper
            # Format: var searchIndex = new Map(JSON.parse('[...]'));
            match = re.search(r"JSON\.parse\('(.+?)'\)", content, re.DOTALL)
            if not match:
                raise ValueError("Could not find JSON data in search index")

            json_str = match.group(1)
            # Unescape
            json_str = json_str.replace("\\'", "'").replace("\\\\", "\\")

            # Parse JSON
            crates_list = json.loads(json_str)

            # Find the windows crate
            for crate_entry in crates_list:
                if crate_entry[0] == "windows":
                    self._index_data = crate_entry[1]
                    break

            if not self._index_data:
                raise ValueError("Could not find 'windows' crate in search index")

            # Build search structures
            self._build_search_index()

            self._loaded = True
            elapsed = asyncio.get_event_loop().time() - start
            logger.info(
                f"Search index loaded successfully in {elapsed:.2f}s "
                f"({len(self._all_items)} items indexed)"
            )

        except Exception as e:
            logger.error(f"Failed to load search index: {e}")
            raise

    def _build_search_index(self) -> None:
        """Build efficient search structures from the raw index data."""
        if not self._index_data:
            return

        # The rustdoc index structure is complex. We'll parse the main parts:
        # - "n" contains item names
        # - "q" contains parent paths
        # - "t" contains type information
        # - "p" contains path information

        names = self._index_data.get("n", [])
        types = self._index_data.get("t", "")
        paths = self._index_data.get("q", [])
        parent_idx = self._index_data.get("i", [])

        # Build a path lookup
        path_list = ["windows"]  # Root is always "windows"
        if "p" in self._index_data:
            # "p" contains path segment definitions
            for p in self._index_data["p"]:
                if isinstance(p, list) and len(p) >= 2:
                    path_list.append("::".join(str(x) for x in p))

        # Process items
        for idx, name in enumerate(names):
            if idx >= len(types):
                break

            type_char = types[idx]

            # Map type characters to readable names
            # This is a simplified mapping - rustdoc uses various type codes
            type_map = {
                "t": "struct",
                "f": "fn",
                "e": "enum",
                "s": "static",
                "c": "constant",
                "T": "trait",
                "m": "mod",
                "y": "type",
                "p": "primitive",
            }
            item_type = type_map.get(type_char, type_char)

            # Get parent path
            parent_path = "windows"
            if idx < len(parent_idx):
                parent_id = parent_idx[idx]
                # parent_id can be int or string, convert to int
                try:
                    parent_id_int = (
                        int(parent_id) if isinstance(parent_id, str) else parent_id
                    )
                    if 0 <= parent_id_int < len(paths):
                        path_parts = paths[parent_id_int]
                        if isinstance(path_parts, list):
                            parent_path = "windows::" + "::".join(
                                str(p) for p in path_parts
                            )
                        elif isinstance(path_parts, str):
                            parent_path = f"windows::{path_parts}"
                except (ValueError, TypeError):
                    # If conversion fails, use default parent_path
                    pass

            full_path = f"{parent_path}::{name}"

            item = SearchIndexItem(
                type=item_type, path=parent_path, name=name, full_path=full_path
            )

            self._all_items.append(item)

            # Index by name (case-insensitive)
            name_lower = name.lower()
            if name_lower not in self._items_by_name:
                self._items_by_name[name_lower] = []
            self._items_by_name[name_lower].append(item)

        logger.info(
            f"Built search index: {len(self._all_items)} total items, "
            f"{len(self._items_by_name)} unique names"
        )

    def search(self, query: str, max_results: int = 10) -> list[SearchIndexItem]:
        """Search for items matching the query.

        Args:
            query: Search term (case-insensitive)
            max_results: Maximum number of results to return

        Returns:
            List of matching items, sorted by relevance

        Raises:
            RuntimeError: If index not loaded
        """
        if not self._loaded:
            raise RuntimeError("Search index not loaded. Call load() first.")

        query_lower = query.lower()
        results: list[tuple[int, SearchIndexItem]] = []

        # Exact name matches first
        if query_lower in self._items_by_name:
            for item in self._items_by_name[query_lower]:
                results.append((0, item))  # Priority 0 = exact match

        # Prefix matches
        if len(results) < max_results:
            for name_lower, items in self._items_by_name.items():
                if name_lower.startswith(query_lower) and name_lower != query_lower:
                    for item in items:
                        results.append((1, item))  # Priority 1 = prefix match

        # Substring matches
        if len(results) < max_results:
            for name_lower, items in self._items_by_name.items():
                if (
                    query_lower in name_lower
                    and not name_lower.startswith(query_lower)
                    and name_lower != query_lower
                ):
                    for item in items:
                        results.append((2, item))  # Priority 2 = substring match

        # Sort by priority, then by name length (shorter = more relevant)
        results.sort(key=lambda x: (x[0], len(x[1].name), x[1].name))

        # Return just the items (without priority scores)
        return [item for _, item in results[:max_results]]

    def get_by_full_path(self, full_path: str) -> SearchIndexItem | None:
        """Get an item by its full path.

        Args:
            full_path: Full path like "windows::Win32::Storage::FileSystem::CreateFileA"

        Returns:
            SearchIndexItem if found, None otherwise
        """
        if not self._loaded:
            raise RuntimeError("Search index not loaded. Call load() first.")

        for item in self._all_items:
            if item.full_path == full_path:
                return item

        return None

    @property
    def is_loaded(self) -> bool:
        """Check if index is loaded.

        Returns:
            True if loaded, False otherwise
        """
        return self._loaded

    @property
    def item_count(self) -> int:
        """Get total number of indexed items.

        Returns:
            Number of items in index
        """
        return len(self._all_items)
