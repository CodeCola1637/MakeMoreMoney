"""
异步任务管理器
负责管理和协调所有后台任务，提供弹性任务包装和健康监控
"""

import asyncio
import logging
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Callable, Optional, Any, Coroutine
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESTARTING = "restarting"


@dataclass
class TaskInfo:
    """任务信息"""
    name: str
    status: TaskStatus = TaskStatus.PENDING
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    run_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    last_error_time: Optional[datetime] = None
    restart_count: int = 0
    max_restarts: int = 3
    restart_delay: float = 5.0
    is_critical: bool = False
    task_ref: Optional[asyncio.Task] = None


class TaskManager:
    """
    异步任务管理器
    
    功能：
    1. 统一管理所有后台任务
    2. 弹性任务包装 - 自动重启失败任务
    3. 任务健康监控
    4. 优雅关闭
    """
    
    def __init__(self, logger=None):
        """
        初始化任务管理器
        
        Args:
            logger: 日志记录器
        """
        self.logger = logger or logging.getLogger(__name__)
        self.tasks: Dict[str, TaskInfo] = {}
        self.is_running = False
        self._shutdown_event = asyncio.Event()
        
        self.logger.info("任务管理器初始化完成")
    
    def create_resilient_task(
        self,
        name: str,
        coro_func: Callable[[], Coroutine],
        max_restarts: int = 3,
        restart_delay: float = 5.0,
        is_critical: bool = False
    ) -> asyncio.Task:
        """
        创建弹性任务
        
        Args:
            name: 任务名称
            coro_func: 协程工厂函数（每次调用返回新协程）
            max_restarts: 最大重启次数
            restart_delay: 重启延迟（秒）
            is_critical: 是否为关键任务
            
        Returns:
            asyncio.Task
        """
        task_info = TaskInfo(
            name=name,
            max_restarts=max_restarts,
            restart_delay=restart_delay,
            is_critical=is_critical
        )
        self.tasks[name] = task_info
        
        async def resilient_wrapper():
            """弹性任务包装器"""
            while not self._shutdown_event.is_set():
                try:
                    task_info.status = TaskStatus.RUNNING
                    task_info.start_time = datetime.now()
                    task_info.run_count += 1
                    
                    self.logger.info(f"[TaskManager] 启动任务: {name} (第{task_info.run_count}次)")
                    
                    # 执行任务
                    await coro_func()
                    
                    # 正常完成
                    task_info.status = TaskStatus.COMPLETED
                    task_info.end_time = datetime.now()
                    self.logger.info(f"[TaskManager] 任务完成: {name}")
                    break
                    
                except asyncio.CancelledError:
                    task_info.status = TaskStatus.CANCELLED
                    task_info.end_time = datetime.now()
                    self.logger.info(f"[TaskManager] 任务已取消: {name}")
                    raise
                    
                except Exception as e:
                    task_info.error_count += 1
                    task_info.last_error = str(e)
                    task_info.last_error_time = datetime.now()
                    
                    self.logger.error(f"[TaskManager] 任务 {name} 发生错误: {e}")
                    self.logger.debug(traceback.format_exc())
                    
                    # 检查是否可以重启
                    if task_info.restart_count < task_info.max_restarts:
                        task_info.restart_count += 1
                        task_info.status = TaskStatus.RESTARTING
                        
                        self.logger.warning(
                            f"[TaskManager] 任务 {name} 将在 {restart_delay}秒后重启 "
                            f"(重启 {task_info.restart_count}/{max_restarts})"
                        )
                        
                        await asyncio.sleep(restart_delay)
                        continue
                    else:
                        task_info.status = TaskStatus.FAILED
                        task_info.end_time = datetime.now()
                        
                        self.logger.error(
                            f"[TaskManager] 任务 {name} 已达到最大重启次数 ({max_restarts})，停止重试"
                        )
                        
                        if is_critical:
                            self.logger.critical(f"[TaskManager] 关键任务 {name} 失败！")
                        
                        break
        
        task = asyncio.create_task(resilient_wrapper(), name=f"resilient_{name}")
        task_info.task_ref = task
        
        return task
    
    def create_periodic_task(
        self,
        name: str,
        coro_func: Callable[[], Coroutine],
        interval: float,
        max_restarts: int = 10,
        is_critical: bool = False
    ) -> asyncio.Task:
        """
        创建周期性任务
        
        Args:
            name: 任务名称
            coro_func: 协程工厂函数
            interval: 执行间隔（秒）
            max_restarts: 最大重启次数
            is_critical: 是否为关键任务
            
        Returns:
            asyncio.Task
        """
        async def periodic_wrapper():
            while not self._shutdown_event.is_set():
                try:
                    await coro_func()
                except Exception as e:
                    self.logger.error(f"[TaskManager] 周期任务 {name} 执行出错: {e}")
                
                # 等待下一个周期或关闭信号
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=interval
                    )
                    break  # 收到关闭信号
                except asyncio.TimeoutError:
                    continue  # 正常超时，继续下一轮
        
        return self.create_resilient_task(
            name=name,
            coro_func=periodic_wrapper,
            max_restarts=max_restarts,
            restart_delay=interval,
            is_critical=is_critical
        )
    
    async def start(self):
        """启动任务管理器"""
        self.is_running = True
        self._shutdown_event.clear()
        self.logger.info("[TaskManager] 任务管理器已启动")
    
    async def shutdown(self, timeout: float = 30.0):
        """
        优雅关闭所有任务
        
        Args:
            timeout: 关闭超时时间（秒）
        """
        self.logger.info("[TaskManager] 开始关闭任务管理器...")
        self._shutdown_event.set()
        self.is_running = False
        
        # 等待所有任务完成或超时
        running_tasks = [
            info.task_ref for info in self.tasks.values()
            if info.task_ref and not info.task_ref.done()
        ]
        
        if running_tasks:
            self.logger.info(f"[TaskManager] 等待 {len(running_tasks)} 个任务完成...")
            
            done, pending = await asyncio.wait(
                running_tasks,
                timeout=timeout
            )
            
            # 取消超时未完成的任务
            for task in pending:
                task.cancel()
                self.logger.warning(f"[TaskManager] 强制取消任务: {task.get_name()}")
            
            # 等待取消完成
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        
        self.logger.info("[TaskManager] 任务管理器已关闭")
    
    def cancel_task(self, name: str) -> bool:
        """
        取消指定任务
        
        Args:
            name: 任务名称
            
        Returns:
            是否成功取消
        """
        if name not in self.tasks:
            self.logger.warning(f"[TaskManager] 任务不存在: {name}")
            return False
        
        task_info = self.tasks[name]
        if task_info.task_ref and not task_info.task_ref.done():
            task_info.task_ref.cancel()
            task_info.status = TaskStatus.CANCELLED
            self.logger.info(f"[TaskManager] 已取消任务: {name}")
            return True
        
        return False
    
    def get_task_status(self, name: str) -> Optional[TaskInfo]:
        """获取任务状态"""
        return self.tasks.get(name)
    
    def get_all_status(self) -> Dict[str, Dict]:
        """获取所有任务状态"""
        result = {}
        for name, info in self.tasks.items():
            result[name] = {
                'status': info.status.value,
                'run_count': info.run_count,
                'error_count': info.error_count,
                'restart_count': info.restart_count,
                'is_critical': info.is_critical,
                'start_time': info.start_time.isoformat() if info.start_time else None,
                'last_error': info.last_error,
                'is_running': info.task_ref and not info.task_ref.done() if info.task_ref else False
            }
        return result
    
    def get_health_report(self) -> Dict[str, Any]:
        """
        获取健康报告
        
        Returns:
            健康报告字典
        """
        total_tasks = len(self.tasks)
        running_tasks = sum(1 for info in self.tasks.values() if info.status == TaskStatus.RUNNING)
        failed_tasks = sum(1 for info in self.tasks.values() if info.status == TaskStatus.FAILED)
        total_errors = sum(info.error_count for info in self.tasks.values())
        
        critical_failed = [
            name for name, info in self.tasks.items()
            if info.is_critical and info.status == TaskStatus.FAILED
        ]
        
        is_healthy = len(critical_failed) == 0 and failed_tasks < total_tasks / 2
        
        return {
            'is_healthy': is_healthy,
            'is_running': self.is_running,
            'total_tasks': total_tasks,
            'running_tasks': running_tasks,
            'failed_tasks': failed_tasks,
            'total_errors': total_errors,
            'critical_failed': critical_failed,
            'task_details': self.get_all_status()
        }
    
    def get_summary(self) -> str:
        """获取任务管理器摘要"""
        health = self.get_health_report()
        
        lines = ["🔧 任务管理器状态:"]
        lines.append(f"   运行状态: {'运行中' if health['is_running'] else '已停止'}")
        lines.append(f"   健康状态: {'✅ 健康' if health['is_healthy'] else '⚠️ 异常'}")
        lines.append(f"   总任务数: {health['total_tasks']}")
        lines.append(f"   运行中: {health['running_tasks']}")
        lines.append(f"   失败: {health['failed_tasks']}")
        lines.append(f"   总错误数: {health['total_errors']}")
        
        if health['critical_failed']:
            lines.append(f"   ⚠️ 关键任务失败: {', '.join(health['critical_failed'])}")
        
        lines.append("\n   任务详情:")
        for name, details in health['task_details'].items():
            status_icon = {
                'running': '🟢',
                'completed': '✅',
                'failed': '❌',
                'cancelled': '⏹️',
                'restarting': '🔄',
                'pending': '⏳'
            }.get(details['status'], '❓')
            
            lines.append(f"      {status_icon} {name}: {details['status']} "
                        f"(运行{details['run_count']}次, 错误{details['error_count']}次)")
        
        return "\n".join(lines)


def create_task_manager(logger=None) -> TaskManager:
    """
    工厂函数：创建任务管理器
    
    Args:
        logger: 日志记录器
        
    Returns:
        TaskManager 实例
    """
    return TaskManager(logger)
