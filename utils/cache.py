"""
LRU Cache implementation with TTL and size limits
Thread-safe caching for API responses
"""
import time
import threading
from collections import OrderedDict
from typing import Any, Optional, Tuple
from datetime import datetime


class LRUCache:
    """
    Thread-safe LRU (Least Recently Used) Cache with TTL (Time To Live)

    Features:
    - Maximum size limit (evicts least recently used items)
    - TTL per item (automatic expiration)
    - Thread-safe operations
    - Hit/miss statistics
    - Memory-efficient

    Example:
        cache = LRUCache(max_size=1000, default_ttl=60)
        cache.set("key", "value", ttl=30)
        value = cache.get("key")
    """

    def __init__(self, max_size: int = 1000, default_ttl: int = 60):
        """
        Initialize LRU cache

        Args:
            max_size: Maximum number of items in cache
            default_ttl: Default time-to-live in seconds
        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, Tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()

        # Statistics
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            expiry_time, value = self._cache[key]

            # Check if expired
            if time.time() > expiry_time:
                del self._cache[key]
                self._misses += 1
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set value in cache

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if not specified)
        """
        with self._lock:
            ttl = ttl if ttl is not None else self.default_ttl
            expiry_time = time.time() + ttl

            # Update existing key or add new
            if key in self._cache:
                del self._cache[key]

            self._cache[key] = (expiry_time, value)
            self._cache.move_to_end(key)

            # Evict LRU items if over max size
            while len(self._cache) > self.max_size:
                self._evict_lru()

    def _evict_lru(self) -> None:
        """Evict least recently used item (must be called with lock held)"""
        if self._cache:
            self._cache.popitem(last=False)
            self._evictions += 1

    def clear(self) -> None:
        """Clear all cache entries"""
        with self._lock:
            self._cache.clear()

    def delete(self, key: str) -> bool:
        """
        Delete specific key from cache

        Args:
            key: Cache key to delete

        Returns:
            True if key was found and deleted, False otherwise
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def cleanup_expired(self) -> int:
        """
        Remove all expired entries

        Returns:
            Number of entries removed
        """
        with self._lock:
            current_time = time.time()
            expired_keys = [
                key for key, (expiry, _) in self._cache.items()
                if current_time > expiry
            ]

            for key in expired_keys:
                del self._cache[key]

            return len(expired_keys)

    def size(self) -> int:
        """Get current cache size"""
        with self._lock:
            return len(self._cache)

    def stats(self) -> dict:
        """
        Get cache statistics

        Returns:
            Dictionary with hit rate, miss rate, size, etc.
        """
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0

            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate": f"{hit_rate:.2f}%",
                "total_requests": total_requests,
            }

    def reset_stats(self) -> None:
        """Reset statistics counters"""
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0


# ============================================================
# PRE-CONFIGURED CACHE INSTANCES
# ============================================================

# Prediction cache: 20 second TTL, max 1000 stops
prediction_cache = LRUCache(max_size=1000, default_ttl=20)

# Schedule cache: 60 second TTL, max 500 queries
schedule_cache = LRUCache(max_size=500, default_ttl=60)

# Route/Stop metadata cache: 5 minute TTL, max 200 items
metadata_cache = LRUCache(max_size=200, default_ttl=300)
