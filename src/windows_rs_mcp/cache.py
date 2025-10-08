"""Caching layer with TTL support for the windows-rs MCP server."""

import asyncio
import time
from typing import Any, TypeVar

T = TypeVar("T")


class CacheEntry[T]:
    """A cache entry with timestamp for TTL checking."""

    def __init__(self, value: T, timestamp: float) -> None:
        """Initialize cache entry.

        Args:
            value: The cached value
            timestamp: When the value was cached
        """
        self.value = value
        self.timestamp = timestamp

    def is_expired(self, ttl: int) -> bool:
        """Check if this cache entry has expired.

        Args:
            ttl: Time to live in seconds

        Returns:
            True if expired, False otherwise
        """
        return time.time() - self.timestamp > ttl


class AsyncCache[T]:
    """Thread-safe async cache with TTL support."""

    def __init__(self, ttl: int, enabled: bool = True) -> None:
        """Initialize the cache.

        Args:
            ttl: Time to live for cache entries in seconds
            enabled: Whether caching is enabled
        """
        self._cache: dict[str, CacheEntry[T]] = {}
        self._ttl = ttl
        self._enabled = enabled
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        """Get a value from the cache.

        Args:
            key: Cache key

        Returns:
            Cached value if found and not expired, None otherwise
        """
        if not self._enabled:
            return None

        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            if entry.is_expired(self._ttl):
                del self._cache[key]
                return None

            return entry.value

    async def set(self, key: str, value: T) -> None:
        """Set a value in the cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        if not self._enabled:
            return

        async with self._lock:
            self._cache[key] = CacheEntry(value, time.time())

    async def clear(self) -> None:
        """Clear all entries from the cache."""
        async with self._lock:
            self._cache.clear()

    async def cleanup_expired(self) -> int:
        """Remove expired entries from the cache.

        Returns:
            Number of entries removed
        """
        if not self._enabled:
            return 0

        async with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items() if entry.is_expired(self._ttl)
            ]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)

    def size(self) -> int:
        """Get the current cache size.

        Returns:
            Number of entries in the cache
        """
        return len(self._cache)


class CacheManager:
    """Manages multiple typed caches for different data types."""

    def __init__(self, ttl: int, enabled: bool = True) -> None:
        """Initialize cache manager.

        Args:
            ttl: Default TTL for all caches in seconds
            enabled: Whether caching is enabled globally
        """
        self.doc_cache: AsyncCache[Any] = AsyncCache(ttl, enabled)
        self.feature_cache: AsyncCache[str | None] = AsyncCache(ttl, enabled)
        self.search_cache: AsyncCache[Any] = AsyncCache(ttl, enabled)

    async def clear_all(self) -> None:
        """Clear all caches."""
        await asyncio.gather(
            self.doc_cache.clear(),
            self.feature_cache.clear(),
            self.search_cache.clear(),
        )

    async def cleanup_expired_all(self) -> int:
        """Clean up expired entries from all caches.

        Returns:
            Total number of entries removed
        """
        results = await asyncio.gather(
            self.doc_cache.cleanup_expired(),
            self.feature_cache.cleanup_expired(),
            self.search_cache.cleanup_expired(),
        )
        return sum(results)
