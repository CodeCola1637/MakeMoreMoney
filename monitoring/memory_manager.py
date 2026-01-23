"""
内存管理器
负责监控和优化内存使用
"""

import gc
import sys
import weakref
import logging
import psutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class MemorySnapshot:
    """内存快照"""
    timestamp: datetime
    process_memory_mb: float
    system_memory_percent: float
    gc_stats: Dict[str, int]
    top_objects: List[Dict[str, Any]] = field(default_factory=list)


class MemoryManager:
    """
    内存管理器
    
    功能：
    1. 内存使用监控
    2. 内存泄漏检测
    3. 自动垃圾回收
    4. 缓存清理触发
    """
    
    def __init__(self, config=None, logger=None):
        """
        初始化内存管理器
        
        Args:
            config: 配置对象
            logger: 日志记录器
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # 配置参数
        self.memory_warning_threshold = 80.0  # 内存警告阈值 (%)
        self.memory_critical_threshold = 90.0  # 内存临界阈值 (%)
        self.auto_gc_threshold = 85.0  # 自动GC阈值 (%)
        self.snapshot_interval = 60  # 快照间隔 (秒)
        self.max_snapshots = 100  # 最大快照数量
        
        # 内存快照历史
        self.snapshots: List[MemorySnapshot] = []
        self.last_snapshot_time: Optional[datetime] = None
        
        # 缓存清理回调
        self._cache_cleaners: List[Callable] = []
        
        # 对象追踪
        self._tracked_objects: weakref.WeakSet = weakref.WeakSet()
        
        # GC统计
        self.gc_count = 0
        self.total_freed_mb = 0.0
        
        self.logger.info("内存管理器初始化完成")
    
    def register_cache_cleaner(self, cleaner: Callable) -> None:
        """
        注册缓存清理函数
        
        Args:
            cleaner: 清理函数
        """
        self._cache_cleaners.append(cleaner)
        self.logger.debug(f"注册缓存清理器: {cleaner.__name__}")
    
    def track_object(self, obj: Any) -> None:
        """
        追踪对象（使用弱引用）
        
        Args:
            obj: 要追踪的对象
        """
        self._tracked_objects.add(obj)
    
    def get_memory_usage(self) -> Dict[str, float]:
        """
        获取当前内存使用情况
        
        Returns:
            内存使用字典
        """
        try:
            process = psutil.Process()
            process_memory = process.memory_info()
            system_memory = psutil.virtual_memory()
            
            return {
                'process_rss_mb': process_memory.rss / (1024 * 1024),
                'process_vms_mb': process_memory.vms / (1024 * 1024),
                'system_total_mb': system_memory.total / (1024 * 1024),
                'system_available_mb': system_memory.available / (1024 * 1024),
                'system_percent': system_memory.percent,
                'process_percent': process.memory_percent()
            }
        except Exception as e:
            self.logger.error(f"获取内存使用失败: {e}")
            return {}
    
    def take_snapshot(self) -> MemorySnapshot:
        """
        拍摄内存快照
        
        Returns:
            MemorySnapshot
        """
        memory = self.get_memory_usage()
        gc_stats = gc.get_stats()
        
        snapshot = MemorySnapshot(
            timestamp=datetime.now(),
            process_memory_mb=memory.get('process_rss_mb', 0),
            system_memory_percent=memory.get('system_percent', 0),
            gc_stats={
                'collections': sum(s.get('collections', 0) for s in gc_stats),
                'collected': sum(s.get('collected', 0) for s in gc_stats),
                'uncollectable': sum(s.get('uncollectable', 0) for s in gc_stats)
            },
            top_objects=self._get_top_objects(10)
        )
        
        self.snapshots.append(snapshot)
        self.last_snapshot_time = snapshot.timestamp
        
        # 限制快照数量
        if len(self.snapshots) > self.max_snapshots:
            self.snapshots = self.snapshots[-self.max_snapshots:]
        
        return snapshot
    
    def _get_top_objects(self, n: int = 10) -> List[Dict[str, Any]]:
        """
        获取内存占用最大的对象类型
        
        Args:
            n: 返回数量
            
        Returns:
            对象统计列表
        """
        try:
            type_counts = defaultdict(lambda: {'count': 0, 'size': 0})
            
            for obj in gc.get_objects():
                try:
                    obj_type = type(obj).__name__
                    obj_size = sys.getsizeof(obj)
                    type_counts[obj_type]['count'] += 1
                    type_counts[obj_type]['size'] += obj_size
                except Exception:
                    continue
            
            # 按大小排序
            sorted_types = sorted(
                type_counts.items(),
                key=lambda x: x[1]['size'],
                reverse=True
            )[:n]
            
            return [
                {
                    'type': t,
                    'count': stats['count'],
                    'size_mb': stats['size'] / (1024 * 1024)
                }
                for t, stats in sorted_types
            ]
            
        except Exception as e:
            self.logger.debug(f"获取对象统计失败: {e}")
            return []
    
    def check_memory(self) -> Dict[str, Any]:
        """
        检查内存状态
        
        Returns:
            内存状态字典
        """
        memory = self.get_memory_usage()
        system_percent = memory.get('system_percent', 0)
        
        status = 'healthy'
        message = '内存使用正常'
        
        if system_percent > self.memory_critical_threshold:
            status = 'critical'
            message = f'内存使用临界: {system_percent:.1f}%'
        elif system_percent > self.memory_warning_threshold:
            status = 'warning'
            message = f'内存使用较高: {system_percent:.1f}%'
        
        return {
            'status': status,
            'message': message,
            'memory': memory,
            'should_gc': system_percent > self.auto_gc_threshold
        }
    
    def force_gc(self, full: bool = False) -> Dict[str, Any]:
        """
        强制垃圾回收
        
        Args:
            full: 是否执行完整GC
            
        Returns:
            GC结果
        """
        before_memory = self.get_memory_usage()
        before_rss = before_memory.get('process_rss_mb', 0)
        
        # 执行GC
        if full:
            # 完整GC - 执行所有代
            collected_0 = gc.collect(0)
            collected_1 = gc.collect(1)
            collected_2 = gc.collect(2)
            total_collected = collected_0 + collected_1 + collected_2
        else:
            # 快速GC - 只执行年轻代
            total_collected = gc.collect(0)
        
        after_memory = self.get_memory_usage()
        after_rss = after_memory.get('process_rss_mb', 0)
        freed_mb = before_rss - after_rss
        
        self.gc_count += 1
        if freed_mb > 0:
            self.total_freed_mb += freed_mb
        
        self.logger.info(f"GC完成: 回收{total_collected}个对象, 释放{freed_mb:.2f}MB")
        
        return {
            'collected_objects': total_collected,
            'freed_mb': freed_mb,
            'before_rss_mb': before_rss,
            'after_rss_mb': after_rss
        }
    
    def cleanup_caches(self) -> int:
        """
        清理所有注册的缓存
        
        Returns:
            清理的缓存数量
        """
        cleaned = 0
        for cleaner in self._cache_cleaners:
            try:
                cleaner()
                cleaned += 1
            except Exception as e:
                self.logger.error(f"缓存清理失败 ({cleaner.__name__}): {e}")
        
        self.logger.info(f"清理了 {cleaned} 个缓存")
        return cleaned
    
    def auto_optimize(self) -> Dict[str, Any]:
        """
        自动内存优化
        
        Returns:
            优化结果
        """
        check = self.check_memory()
        result = {
            'memory_status': check['status'],
            'actions': []
        }
        
        if check['should_gc']:
            gc_result = self.force_gc(full=True)
            result['actions'].append({
                'action': 'gc',
                'freed_mb': gc_result['freed_mb']
            })
            
            # 如果GC后仍然高，清理缓存
            after_check = self.check_memory()
            if after_check['should_gc']:
                cache_cleaned = self.cleanup_caches()
                result['actions'].append({
                    'action': 'cache_cleanup',
                    'cleaned': cache_cleaned
                })
                
                # 再次GC
                gc_result2 = self.force_gc(full=True)
                result['actions'].append({
                    'action': 'gc_after_cleanup',
                    'freed_mb': gc_result2['freed_mb']
                })
        
        return result
    
    def detect_memory_leak(self, window_minutes: int = 30) -> Optional[Dict[str, Any]]:
        """
        检测内存泄漏
        
        Args:
            window_minutes: 分析窗口 (分钟)
            
        Returns:
            泄漏检测结果
        """
        if len(self.snapshots) < 5:
            return None
        
        cutoff = datetime.now() - timedelta(minutes=window_minutes)
        recent_snapshots = [s for s in self.snapshots if s.timestamp > cutoff]
        
        if len(recent_snapshots) < 3:
            return None
        
        # 计算内存增长趋势
        memory_values = [s.process_memory_mb for s in recent_snapshots]
        
        # 简单线性回归计算斜率
        n = len(memory_values)
        x = list(range(n))
        x_mean = sum(x) / n
        y_mean = sum(memory_values) / n
        
        numerator = sum((x[i] - x_mean) * (memory_values[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            return None
        
        slope = numerator / denominator
        
        # 判断是否有泄漏趋势
        is_leaking = slope > 0.5  # 每个快照增加0.5MB以上
        
        return {
            'is_leaking': is_leaking,
            'growth_rate_mb_per_snapshot': slope,
            'current_memory_mb': memory_values[-1],
            'initial_memory_mb': memory_values[0],
            'total_growth_mb': memory_values[-1] - memory_values[0],
            'snapshot_count': len(recent_snapshots)
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取内存管理统计
        
        Returns:
            统计信息
        """
        memory = self.get_memory_usage()
        
        return {
            'current_memory': memory,
            'gc_count': self.gc_count,
            'total_freed_mb': self.total_freed_mb,
            'snapshot_count': len(self.snapshots),
            'tracked_objects': len(self._tracked_objects),
            'registered_cleaners': len(self._cache_cleaners)
        }
    
    def get_summary(self) -> str:
        """获取内存管理器摘要"""
        memory = self.get_memory_usage()
        check = self.check_memory()
        leak_check = self.detect_memory_leak()
        
        status_icon = {
            'healthy': '✅',
            'warning': '⚠️',
            'critical': '❌'
        }
        
        lines = ["💾 内存管理器状态:"]
        lines.append(f"   状态: {status_icon.get(check['status'], '❓')} {check['message']}")
        lines.append(f"   进程内存: {memory.get('process_rss_mb', 0):.1f} MB")
        lines.append(f"   系统内存: {memory.get('system_percent', 0):.1f}%")
        lines.append(f"   GC次数: {self.gc_count}")
        lines.append(f"   累计释放: {self.total_freed_mb:.1f} MB")
        lines.append(f"   快照数: {len(self.snapshots)}")
        
        if leak_check:
            leak_icon = '⚠️' if leak_check['is_leaking'] else '✅'
            lines.append(f"   内存泄漏检测: {leak_icon} {'疑似泄漏' if leak_check['is_leaking'] else '正常'}")
            if leak_check['is_leaking']:
                lines.append(f"      增长率: {leak_check['growth_rate_mb_per_snapshot']:.2f} MB/快照")
        
        return "\n".join(lines)


def create_memory_manager(config=None, logger=None) -> MemoryManager:
    """
    工厂函数：创建内存管理器
    
    Args:
        config: 配置对象
        logger: 日志记录器
        
    Returns:
        MemoryManager 实例
    """
    return MemoryManager(config, logger)
