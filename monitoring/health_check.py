"""
健康检查模块
提供系统健康状态监控和报告
"""

import asyncio
import psutil
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum


class HealthStatus(Enum):
    """健康状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ComponentHealth:
    """组件健康状态"""
    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    message: str = ""
    last_check: Optional[datetime] = None
    response_time_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemMetrics:
    """系统指标"""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_available_mb: float = 0.0
    disk_percent: float = 0.0
    disk_free_gb: float = 0.0
    open_files: int = 0
    threads: int = 0
    uptime_seconds: float = 0.0


class HealthCheck:
    """
    健康检查类
    
    功能：
    1. 系统资源监控 (CPU, 内存, 磁盘)
    2. 组件健康检查
    3. 自定义健康检查注册
    4. 健康报告生成
    """
    
    def __init__(self, logger=None):
        """
        初始化健康检查
        
        Args:
            logger: 日志记录器
        """
        self.logger = logger or logging.getLogger(__name__)
        self.start_time = datetime.now()
        
        # 组件健康状态
        self.components: Dict[str, ComponentHealth] = {}
        
        # 自定义健康检查函数
        self._health_checks: Dict[str, Callable] = {}
        
        # 阈值配置
        self.thresholds = {
            'cpu_warning': 80.0,
            'cpu_critical': 95.0,
            'memory_warning': 80.0,
            'memory_critical': 95.0,
            'disk_warning': 85.0,
            'disk_critical': 95.0,
            'response_time_warning_ms': 1000,
            'response_time_critical_ms': 5000
        }
        
        self.logger.info("健康检查模块初始化完成")
    
    def register_check(self, name: str, check_func: Callable) -> None:
        """
        注册自定义健康检查
        
        Args:
            name: 检查名称
            check_func: 检查函数，返回 (status, message, details)
        """
        self._health_checks[name] = check_func
        self.components[name] = ComponentHealth(name=name)
        self.logger.debug(f"注册健康检查: {name}")
    
    async def check_component(self, name: str) -> ComponentHealth:
        """
        执行单个组件的健康检查
        
        Args:
            name: 组件名称
            
        Returns:
            ComponentHealth
        """
        if name not in self._health_checks:
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNKNOWN,
                message="未找到检查函数"
            )
        
        start_time = datetime.now()
        
        try:
            check_func = self._health_checks[name]
            
            # 支持同步和异步函数
            if asyncio.iscoroutinefunction(check_func):
                result = await check_func()
            else:
                result = check_func()
            
            response_time = (datetime.now() - start_time).total_seconds() * 1000
            
            if isinstance(result, tuple) and len(result) >= 2:
                status, message = result[0], result[1]
                details = result[2] if len(result) > 2 else {}
            else:
                status = HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY
                message = "检查通过" if result else "检查失败"
                details = {}
            
            # 检查响应时间
            if response_time > self.thresholds['response_time_critical_ms']:
                status = HealthStatus.UNHEALTHY
                message = f"响应时间过长: {response_time:.0f}ms"
            elif response_time > self.thresholds['response_time_warning_ms']:
                if status == HealthStatus.HEALTHY:
                    status = HealthStatus.DEGRADED
                message = f"{message} (响应慢: {response_time:.0f}ms)"
            
            component = ComponentHealth(
                name=name,
                status=status,
                message=message,
                last_check=datetime.now(),
                response_time_ms=response_time,
                details=details
            )
            
        except Exception as e:
            response_time = (datetime.now() - start_time).total_seconds() * 1000
            component = ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=f"检查异常: {str(e)}",
                last_check=datetime.now(),
                response_time_ms=response_time
            )
            self.logger.error(f"健康检查 {name} 失败: {e}")
        
        self.components[name] = component
        return component
    
    async def check_all(self) -> Dict[str, ComponentHealth]:
        """
        执行所有组件的健康检查
        
        Returns:
            所有组件的健康状态
        """
        tasks = [self.check_component(name) for name in self._health_checks.keys()]
        await asyncio.gather(*tasks, return_exceptions=True)
        return self.components
    
    def get_system_metrics(self) -> SystemMetrics:
        """
        获取系统指标
        
        Returns:
            SystemMetrics
        """
        try:
            process = psutil.Process()
            
            # CPU
            cpu_percent = psutil.cpu_percent(interval=0.1)
            
            # 内存
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_used_mb = memory.used / (1024 * 1024)
            memory_available_mb = memory.available / (1024 * 1024)
            
            # 磁盘
            disk = psutil.disk_usage('/')
            disk_percent = disk.percent
            disk_free_gb = disk.free / (1024 * 1024 * 1024)
            
            # 进程信息
            open_files = len(process.open_files())
            threads = process.num_threads()
            
            # 运行时间
            uptime_seconds = (datetime.now() - self.start_time).total_seconds()
            
            return SystemMetrics(
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                memory_used_mb=memory_used_mb,
                memory_available_mb=memory_available_mb,
                disk_percent=disk_percent,
                disk_free_gb=disk_free_gb,
                open_files=open_files,
                threads=threads,
                uptime_seconds=uptime_seconds
            )
            
        except Exception as e:
            self.logger.error(f"获取系统指标失败: {e}")
            return SystemMetrics()
    
    def get_system_health(self) -> HealthStatus:
        """
        根据系统指标判断系统健康状态
        
        Returns:
            HealthStatus
        """
        metrics = self.get_system_metrics()
        
        # 检查关键指标
        if (metrics.cpu_percent > self.thresholds['cpu_critical'] or
            metrics.memory_percent > self.thresholds['memory_critical'] or
            metrics.disk_percent > self.thresholds['disk_critical']):
            return HealthStatus.UNHEALTHY
        
        if (metrics.cpu_percent > self.thresholds['cpu_warning'] or
            metrics.memory_percent > self.thresholds['memory_warning'] or
            metrics.disk_percent > self.thresholds['disk_warning']):
            return HealthStatus.DEGRADED
        
        return HealthStatus.HEALTHY
    
    def get_overall_status(self) -> HealthStatus:
        """
        获取整体健康状态
        
        Returns:
            HealthStatus
        """
        system_health = self.get_system_health()
        
        # 如果系统不健康，直接返回
        if system_health == HealthStatus.UNHEALTHY:
            return HealthStatus.UNHEALTHY
        
        # 检查所有组件
        component_statuses = [c.status for c in self.components.values()]
        
        if not component_statuses:
            return system_health
        
        if any(s == HealthStatus.UNHEALTHY for s in component_statuses):
            return HealthStatus.UNHEALTHY
        
        if any(s == HealthStatus.DEGRADED for s in component_statuses):
            return HealthStatus.DEGRADED
        
        if system_health == HealthStatus.DEGRADED:
            return HealthStatus.DEGRADED
        
        return HealthStatus.HEALTHY
    
    async def get_health_report(self) -> Dict[str, Any]:
        """
        生成完整的健康报告
        
        Returns:
            健康报告字典
        """
        # 执行所有检查
        await self.check_all()
        
        # 获取系统指标
        metrics = self.get_system_metrics()
        
        # 获取整体状态
        overall_status = self.get_overall_status()
        
        return {
            'status': overall_status.value,
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': metrics.uptime_seconds,
            'uptime_human': self._format_uptime(metrics.uptime_seconds),
            'system': {
                'status': self.get_system_health().value,
                'cpu_percent': metrics.cpu_percent,
                'memory_percent': metrics.memory_percent,
                'memory_used_mb': round(metrics.memory_used_mb, 2),
                'memory_available_mb': round(metrics.memory_available_mb, 2),
                'disk_percent': metrics.disk_percent,
                'disk_free_gb': round(metrics.disk_free_gb, 2),
                'open_files': metrics.open_files,
                'threads': metrics.threads
            },
            'components': {
                name: {
                    'status': comp.status.value,
                    'message': comp.message,
                    'response_time_ms': round(comp.response_time_ms, 2),
                    'last_check': comp.last_check.isoformat() if comp.last_check else None,
                    'details': comp.details
                }
                for name, comp in self.components.items()
            }
        }
    
    def _format_uptime(self, seconds: float) -> str:
        """格式化运行时间"""
        td = timedelta(seconds=int(seconds))
        days = td.days
        hours, remainder = divmod(td.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if days > 0:
            return f"{days}天 {hours}时 {minutes}分"
        elif hours > 0:
            return f"{hours}时 {minutes}分 {seconds}秒"
        elif minutes > 0:
            return f"{minutes}分 {seconds}秒"
        else:
            return f"{seconds}秒"
    
    def get_summary(self) -> str:
        """获取健康检查摘要"""
        metrics = self.get_system_metrics()
        overall = self.get_overall_status()
        
        status_icon = {
            HealthStatus.HEALTHY: "✅",
            HealthStatus.DEGRADED: "⚠️",
            HealthStatus.UNHEALTHY: "❌",
            HealthStatus.UNKNOWN: "❓"
        }
        
        lines = ["🏥 系统健康状态:"]
        lines.append(f"   整体状态: {status_icon.get(overall, '❓')} {overall.value}")
        lines.append(f"   运行时间: {self._format_uptime(metrics.uptime_seconds)}")
        lines.append(f"   CPU使用率: {metrics.cpu_percent:.1f}%")
        lines.append(f"   内存使用率: {metrics.memory_percent:.1f}%")
        lines.append(f"   磁盘使用率: {metrics.disk_percent:.1f}%")
        lines.append(f"   打开文件数: {metrics.open_files}")
        lines.append(f"   线程数: {metrics.threads}")
        
        if self.components:
            lines.append("\n   组件状态:")
            for name, comp in self.components.items():
                icon = status_icon.get(comp.status, '❓')
                lines.append(f"      {icon} {name}: {comp.status.value} - {comp.message}")
        
        return "\n".join(lines)


def create_health_check(logger=None) -> HealthCheck:
    """
    工厂函数：创建健康检查实例
    
    Args:
        logger: 日志记录器
        
    Returns:
        HealthCheck 实例
    """
    return HealthCheck(logger)
