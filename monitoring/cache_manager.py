"""
智能缓存管理器
提供多层缓存、TTL管理和自动清理
"""

import time
import hashlib
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable, TypeVar, Generic
from dataclasses import dataclass, field
from collections import OrderedDict
from functools import wraps


T = TypeVar('T')


@dataclass
class CacheEntry:
    """缓存条目"""
    key: str
    value: Any
    created_at: datetime
    expires_at: Optional[datetime]
    access_count: int = 0
    last_accessed: Optional[datetime] = None
    size_bytes: int = 0


class LRUCache(Generic[T]):
    """
    LRU (最近最少使用) 缓存
    线程安全实现
    """
    
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        """
        初始化 LRU 缓存
        
        Args:
            max_size: 最大条目数
            ttl_seconds: 默认TTL (秒)
        """
        self.max_size = max_size
        self.default_ttl = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        
        # 统计
        self.hits = 0
        self.misses = 0
    
    def get(self, key: str) -> Optional[T]:
        """获取缓存值"""
        with self._lock:
            if key not in self._cache:
                self.misses += 1
                return None
            
            entry = self._cache[key]
            
            # 检查是否过期
            if entry.expires_at and datetime.now() > entry.expires_at:
                del self._cache[key]
                self.misses += 1
                return None
            
            # 更新访问信息
            entry.access_count += 1
            entry.last_accessed = datetime.now()
            
            # 移动到末尾 (最近访问)
            self._cache.move_to_end(key)
            
            self.hits += 1
            return entry.value
    
    def set(self, key: str, value: T, ttl_seconds: int = None) -> None:
        """设置缓存值"""
        with self._lock:
            ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
            expires_at = datetime.now() + timedelta(seconds=ttl) if ttl > 0 else None
            
            # 估算大小
            try:
                import sys
                size_bytes = sys.getsizeof(value)
            except Exception:
                size_bytes = 0
            
            entry = CacheEntry(
                key=key,
                value=value,
                created_at=datetime.now(),
                expires_at=expires_at,
                size_bytes=size_bytes
            )
            
            # 如果key已存在，先删除
            if key in self._cache:
                del self._cache[key]
            
            # 检查容量
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)  # 删除最老的
            
            self._cache[key] = entry
    
    def delete(self, key: str) -> bool:
        """删除缓存"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    def clear(self) -> int:
        """清空缓存"""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count
    
    def cleanup_expired(self) -> int:
        """清理过期条目"""
        with self._lock:
            now = datetime.now()
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.expires_at and now > entry.expires_at
            ]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            total_size = sum(e.size_bytes for e in self._cache.values())
            hit_rate = self.hits / (self.hits + self.misses) if (self.hits + self.misses) > 0 else 0
            
            return {
                'size': len(self._cache),
                'max_size': self.max_size,
                'hits': self.hits,
                'misses': self.misses,
                'hit_rate': hit_rate,
                'total_size_mb': total_size / (1024 * 1024)
            }


class CacheManager:
    """
    智能缓存管理器
    
    功能：
    1. 多命名空间缓存
    2. TTL 管理
    3. 自动过期清理
    4. 缓存统计
    5. 装饰器支持
    """
    
    def __init__(self, config=None, logger=None):
        """
        初始化缓存管理器
        
        Args:
            config: 配置对象
            logger: 日志记录器
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # 多命名空间缓存
        self._caches: Dict[str, LRUCache] = {}
        
        # 默认配置
        self.default_max_size = 1000
        self.default_ttl = 3600  # 1小时
        
        # 自动清理配置
        self.auto_cleanup_interval = 300  # 5分钟
        self._last_cleanup = datetime.now()
        
        # 创建默认缓存
        self._caches['default'] = LRUCache(self.default_max_size, self.default_ttl)
        
        self.logger.info("缓存管理器初始化完成")
    
    def get_cache(self, namespace: str = 'default') -> LRUCache:
        """
        获取指定命名空间的缓存
        
        Args:
            namespace: 命名空间
            
        Returns:
            LRUCache
        """
        if namespace not in self._caches:
            self._caches[namespace] = LRUCache(self.default_max_size, self.default_ttl)
        return self._caches[namespace]
    
    def create_cache(self, namespace: str, max_size: int = None, ttl_seconds: int = None) -> LRUCache:
        """
        创建新的缓存命名空间
        
        Args:
            namespace: 命名空间
            max_size: 最大条目数
            ttl_seconds: 默认TTL
            
        Returns:
            LRUCache
        """
        cache = LRUCache(
            max_size=max_size or self.default_max_size,
            ttl_seconds=ttl_seconds or self.default_ttl
        )
        self._caches[namespace] = cache
        self.logger.debug(f"创建缓存命名空间: {namespace}")
        return cache
    
    def get(self, key: str, namespace: str = 'default') -> Optional[Any]:
        """获取缓存值"""
        self._maybe_cleanup()
        return self.get_cache(namespace).get(key)
    
    def set(self, key: str, value: Any, ttl_seconds: int = None, namespace: str = 'default') -> None:
        """设置缓存值"""
        self.get_cache(namespace).set(key, value, ttl_seconds)
    
    def delete(self, key: str, namespace: str = 'default') -> bool:
        """删除缓存"""
        return self.get_cache(namespace).delete(key)
    
    def clear(self, namespace: str = None) -> int:
        """
        清空缓存
        
        Args:
            namespace: 命名空间，None表示清空所有
            
        Returns:
            清理的条目数
        """
        if namespace:
            return self.get_cache(namespace).clear()
        else:
            total = 0
            for cache in self._caches.values():
                total += cache.clear()
            return total
    
    def _maybe_cleanup(self) -> None:
        """检查是否需要自动清理"""
        if datetime.now() - self._last_cleanup > timedelta(seconds=self.auto_cleanup_interval):
            self.cleanup_all()
            self._last_cleanup = datetime.now()
    
    def cleanup_all(self) -> int:
        """清理所有命名空间的过期条目"""
        total = 0
        for namespace, cache in self._caches.items():
            cleaned = cache.cleanup_expired()
            if cleaned > 0:
                self.logger.debug(f"清理过期缓存 [{namespace}]: {cleaned}条")
            total += cleaned
        return total
    
    def cached(self, namespace: str = 'default', ttl_seconds: int = None, key_func: Callable = None):
        """
        缓存装饰器
        
        Args:
            namespace: 缓存命名空间
            ttl_seconds: TTL
            key_func: 自定义key生成函数
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                # 生成缓存key
                if key_func:
                    cache_key = key_func(*args, **kwargs)
                else:
                    key_parts = [func.__name__]
                    key_parts.extend(str(arg) for arg in args)
                    key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
                    cache_key = hashlib.md5(':'.join(key_parts).encode()).hexdigest()
                
                # 尝试从缓存获取
                cached_value = self.get(cache_key, namespace)
                if cached_value is not None:
                    return cached_value
                
                # 执行函数
                result = func(*args, **kwargs)
                
                # 存入缓存
                self.set(cache_key, result, ttl_seconds, namespace)
                
                return result
            
            return wrapper
        return decorator
    
    def async_cached(self, namespace: str = 'default', ttl_seconds: int = None, key_func: Callable = None):
        """
        异步缓存装饰器
        
        Args:
            namespace: 缓存命名空间
            ttl_seconds: TTL
            key_func: 自定义key生成函数
        """
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # 生成缓存key
                if key_func:
                    cache_key = key_func(*args, **kwargs)
                else:
                    key_parts = [func.__name__]
                    key_parts.extend(str(arg) for arg in args)
                    key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
                    cache_key = hashlib.md5(':'.join(key_parts).encode()).hexdigest()
                
                # 尝试从缓存获取
                cached_value = self.get(cache_key, namespace)
                if cached_value is not None:
                    return cached_value
                
                # 执行函数
                result = await func(*args, **kwargs)
                
                # 存入缓存
                self.set(cache_key, result, ttl_seconds, namespace)
                
                return result
            
            return wrapper
        return decorator
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有命名空间的统计"""
        return {
            namespace: cache.get_stats()
            for namespace, cache in self._caches.items()
        }
    
    def get_summary(self) -> str:
        """获取缓存管理器摘要"""
        all_stats = self.get_all_stats()
        
        lines = ["📦 缓存管理器状态:"]
        lines.append(f"   命名空间数: {len(self._caches)}")
        
        total_entries = 0
        total_hits = 0
        total_misses = 0
        
        for namespace, stats in all_stats.items():
            total_entries += stats['size']
            total_hits += stats['hits']
            total_misses += stats['misses']
            
            hit_rate_pct = stats['hit_rate'] * 100
            lines.append(f"\n   [{namespace}]:")
            lines.append(f"      条目数: {stats['size']}/{stats['max_size']}")
            lines.append(f"      命中率: {hit_rate_pct:.1f}%")
            lines.append(f"      命中/未命中: {stats['hits']}/{stats['misses']}")
        
        overall_hit_rate = total_hits / (total_hits + total_misses) if (total_hits + total_misses) > 0 else 0
        lines.insert(2, f"   总条目数: {total_entries}")
        lines.insert(3, f"   总体命中率: {overall_hit_rate*100:.1f}%")
        
        return "\n".join(lines)


# 全局缓存管理器实例
_global_cache_manager: Optional[CacheManager] = None


def get_cache_manager(config=None, logger=None) -> CacheManager:
    """
    获取全局缓存管理器实例
    
    Args:
        config: 配置对象
        logger: 日志记录器
        
    Returns:
        CacheManager 实例
    """
    global _global_cache_manager
    if _global_cache_manager is None:
        _global_cache_manager = CacheManager(config, logger)
    return _global_cache_manager


def create_cache_manager(config=None, logger=None) -> CacheManager:
    """
    工厂函数：创建新的缓存管理器
    
    Args:
        config: 配置对象
        logger: 日志记录器
        
    Returns:
        CacheManager 实例
    """
    return CacheManager(config, logger)
