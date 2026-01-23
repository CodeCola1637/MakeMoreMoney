#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import yaml
import logging
import threading
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, Any, Optional
from logging.handlers import RotatingFileHandler


class ConfigLoader:
    """
    配置加载器（单例模式）
    用于加载配置文件和环境变量，确保全局配置一致性
    """
    
    _instance: Optional['ConfigLoader'] = None
    _lock: threading.Lock = threading.Lock()
    _initialized: bool = False
    
    def __new__(cls, config_path: str = "config.yaml"):
        """
        单例模式实现
        
        Args:
            config_path: 配置文件路径（仅首次创建时有效）
        """
        if cls._instance is None:
            with cls._lock:
                # 双重检查锁定
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化配置加载器（仅首次调用时执行）
        
        Args:
            config_path: 配置文件路径
        """
        # 防止重复初始化
        if ConfigLoader._initialized:
            return
            
        with ConfigLoader._lock:
            if ConfigLoader._initialized:
                return
                
            # 加载.env文件
            load_dotenv()
            
            # 加载配置文件
            self.config_path = config_path
            self.config = self._load_config()
            
            ConfigLoader._initialized = True
    
    @classmethod
    def get_instance(cls, config_path: str = "config.yaml") -> 'ConfigLoader':
        """
        获取ConfigLoader单例实例
        
        Args:
            config_path: 配置文件路径（仅首次创建时有效）
            
        Returns:
            ConfigLoader实例
        """
        return cls(config_path)
    
    @classmethod
    def reset_instance(cls):
        """
        重置单例实例（仅用于测试）
        """
        with cls._lock:
            cls._instance = None
            cls._initialized = False
        
    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件并处理环境变量替换"""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"配置文件 {self.config_path} 不存在")
            
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            
        # 递归处理环境变量替换
        return self._process_env_vars(config)
    
    def _process_env_vars(self, config: Any) -> Any:
        """递归处理配置中的环境变量替换"""
        if isinstance(config, dict):
            return {k: self._process_env_vars(v) for k, v in config.items()}
        elif isinstance(config, list):
            return [self._process_env_vars(item) for item in config]
        elif isinstance(config, str) and config.startswith("${") and config.endswith("}"):
            # 提取环境变量名
            env_var = config[2:-1]
            # 获取环境变量值，如果不存在则保留原值
            return os.environ.get(env_var, config)
        else:
            return config
    
    def get_config(self) -> Dict[str, Any]:
        """获取完整配置"""
        return self.config
    
    def get(self, key: str, default: Optional[Any] = None) -> Any:
        """
        获取指定键的配置值
        
        Args:
            key: 配置键，支持点号分隔的路径，如 "api.app_key"
            default: 默认值，当键不存在时返回
            
        Returns:
            配置值或默认值
        """
        keys = key.split(".")
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
                
        return value
        
    def update_config(self, key: str, value: Any) -> bool:
        """
        更新指定键的配置值
        
        Args:
            key: 配置键，支持点号分隔的路径，如 "quote.use_mock_data"
            value: 新的配置值
            
        Returns:
            更新是否成功
        """
        keys = key.split(".")
        config = self.config
        
        # 定位到最后一级的父节点
        for i in range(len(keys) - 1):
            k = keys[i]
            if isinstance(config, dict) and k in config:
                config = config[k]
            else:
                return False
        
        # 更新最后一个键的值
        last_key = keys[-1]
        if isinstance(config, dict) and last_key in config:
            config[last_key] = value
            return True
        
        return False

class LoggingManager:
    """
    统一日志管理器（单例模式）
    避免重复创建日志处理器，确保日志配置一致性
    """
    
    _instance: Optional['LoggingManager'] = None
    _lock: threading.Lock = threading.Lock()
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if LoggingManager._initialized:
            return
            
        with LoggingManager._lock:
            if LoggingManager._initialized:
                return
                
            self._configured_loggers: Dict[str, logging.Logger] = {}
            self._root_configured = False
            self._default_level = logging.INFO
            self._default_log_file = "./logs/trading.log"
            self._formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            
            LoggingManager._initialized = True
    
    @classmethod
    def get_instance(cls) -> 'LoggingManager':
        """获取LoggingManager单例实例"""
        return cls()
    
    def setup_root_logging(self, config: Optional['ConfigLoader'] = None):
        """
        设置根日志记录器（仅执行一次）
        
        Args:
            config: 配置加载器实例
        """
        if self._root_configured:
            return
            
        with self._lock:
            if self._root_configured:
                return
                
            # 从配置获取参数
            if config:
                level_str = config.get("logging.level", "INFO")
                log_file = config.get("logging.file", self._default_log_file)
            else:
                level_str = "INFO"
                log_file = self._default_log_file
                
            level_map = {
                "DEBUG": logging.DEBUG,
                "INFO": logging.INFO,
                "WARNING": logging.WARNING,
                "ERROR": logging.ERROR,
                "CRITICAL": logging.CRITICAL
            }
            level = level_map.get(level_str.upper(), logging.INFO)
            self._default_level = level
            self._default_log_file = log_file
            
            # 配置根日志记录器
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.DEBUG)  # 根日志器设为DEBUG，由处理器控制级别
            
            # 避免重复添加处理器
            if not root_logger.handlers:
                # 控制台处理器
                console_handler = logging.StreamHandler()
                console_handler.setLevel(level)
                console_handler.setFormatter(self._formatter)
                root_logger.addHandler(console_handler)
                
                # 文件处理器 - 滚动日志
                if log_file:
                    log_dir = os.path.dirname(log_file)
                    if log_dir and not os.path.exists(log_dir):
                        os.makedirs(log_dir)
                        
                    file_handler = RotatingFileHandler(
                        log_file,
                        maxBytes=10*1024*1024,  # 10MB
                        backupCount=5,
                        encoding="utf-8"
                    )
                    file_handler.setLevel(logging.DEBUG)  # 文件记录所有日志
                    file_handler.setFormatter(self._formatter)
                    root_logger.addHandler(file_handler)
            
            self._root_configured = True
    
    def get_logger(self, name: str, level: Optional[str] = None) -> logging.Logger:
        """
        获取或创建日志记录器
        
        Args:
            name: 日志记录器名称
            level: 日志级别（可选）
            
        Returns:
            配置好的日志记录器
        """
        # 如果已经配置过，直接返回
        if name in self._configured_loggers:
            return self._configured_loggers[name]
            
        # 获取或创建日志记录器
        logger = logging.getLogger(name)
        
        # 设置级别
        if level:
            level_map = {
                "DEBUG": logging.DEBUG,
                "INFO": logging.INFO,
                "WARNING": logging.WARNING,
                "ERROR": logging.ERROR,
                "CRITICAL": logging.CRITICAL
            }
            logger.setLevel(level_map.get(level.upper(), self._default_level))
        else:
            logger.setLevel(self._default_level)
        
        # 禁用传播到父日志器的重复日志
        # 注意：如果根日志器已配置，子日志器会自动继承处理器
        # logger.propagate = True  # 保持默认True，使用根日志器的处理器
        
        self._configured_loggers[name] = logger
        return logger
    
    @classmethod
    def reset_instance(cls):
        """重置单例实例（仅用于测试）"""
        with cls._lock:
            cls._instance = None
            cls._initialized = False


# 全局日志管理器获取函数
def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """
    获取日志记录器（推荐使用）
    
    Args:
        name: 日志记录器名称
        level: 日志级别（可选）
        
    Returns:
        配置好的日志记录器
    """
    return LoggingManager.get_instance().get_logger(name, level)


def setup_logging(config: Optional['ConfigLoader'] = None):
    """
    设置全局日志系统（应在程序启动时调用一次）
    
    Args:
        config: 配置加载器实例
    """
    LoggingManager.get_instance().setup_root_logging(config)


def setup_logger(name: str, level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """
    设置日志记录器（向后兼容接口）
    
    Args:
        name: 日志记录器名称
        level: 日志级别
        log_file: 日志文件路径（已废弃，统一使用根配置）
        
    Returns:
        配置好的日志记录器
    """
    # 确保根日志器已配置
    logging_mgr = LoggingManager.get_instance()
    if not logging_mgr._root_configured:
        # 如果根日志器未配置且提供了log_file，临时配置
        if log_file:
            logging_mgr._default_log_file = log_file
        logging_mgr.setup_root_logging()
    
    return logging_mgr.get_logger(name, level)

def setup_longport_env(config_loader: Optional['ConfigLoader'] = None):
    """
    设置长桥API所需的环境变量
    
    Args:
        config_loader: 可选的配置加载器，如果提供则从配置文件读取凭证
    """
    logger = logging.getLogger("longport_env")
    
    # 设置超时
    os.environ["LONGBRIDGE_NETWORK_TIMEOUT"] = "60"
    
    # 首先尝试从配置文件加载
    if config_loader is None:
        try:
            config_loader = ConfigLoader()
        except Exception as e:
            logger.warning(f"无法加载配置文件: {e}")
    
    if config_loader:
        # 从配置文件读取API凭证
        app_key = config_loader.get("api.app_key")
        app_secret = config_loader.get("api.app_secret")
        access_token = config_loader.get("api.access_token")
        
        # 设置环境变量（如果配置文件中有值且不是环境变量占位符）
        if app_key and not app_key.startswith("${"):
            os.environ["LONGPORT_APP_KEY"] = app_key
            logger.info("从配置文件加载 LONGPORT_APP_KEY")
        if app_secret and not app_secret.startswith("${"):
            os.environ["LONGPORT_APP_SECRET"] = app_secret
            logger.info("从配置文件加载 LONGPORT_APP_SECRET")
        if access_token and not access_token.startswith("${"):
            os.environ["LONGPORT_ACCESS_TOKEN"] = access_token
            logger.info("从配置文件加载 LONGPORT_ACCESS_TOKEN")
    
    # 映射旧的环境变量名称到新名称
    mapping = {
        "LONG_PORT_APP_KEY": "LONGPORT_APP_KEY",
        "LONG_PORT_APP_SECRET": "LONGPORT_APP_SECRET",
        "LONG_PORT_ACCESS_TOKEN": "LONGPORT_ACCESS_TOKEN",
        "LB_APP_KEY": "LONGPORT_APP_KEY",
        "LB_APP_SECRET": "LONGPORT_APP_SECRET",
        "LB_ACCESS_TOKEN": "LONGPORT_ACCESS_TOKEN"
    }
    
    # 复制环境变量（如果目标环境变量未设置）
    for src, dst in mapping.items():
        if os.getenv(src) and not os.getenv(dst):
            os.environ[dst] = os.getenv(src)
            logger.debug(f"设置环境变量 {dst} 来自 {src}")
    
    # 验证设置
    all_set = all(os.getenv(env) for env in ["LONGPORT_APP_KEY", "LONGPORT_APP_SECRET", "LONGPORT_ACCESS_TOKEN"])
    if all_set:
        logger.info("长桥API环境变量已设置完成")
        # 打印部分信息用于调试（隐藏敏感部分）
        app_key = os.getenv("LONGPORT_APP_KEY", "")
        access_token = os.getenv("LONGPORT_ACCESS_TOKEN", "")
        logger.debug(f"APP_KEY: {app_key[:8]}...{app_key[-4:]}" if len(app_key) > 12 else f"APP_KEY: {app_key}")
        logger.debug(f"ACCESS_TOKEN: {access_token[:20]}..." if len(access_token) > 20 else f"ACCESS_TOKEN: {access_token}")
    else:
        missing = [env for env in ["LONGPORT_APP_KEY", "LONGPORT_APP_SECRET", "LONGPORT_ACCESS_TOKEN"] if not os.getenv(env)]
        logger.warning(f"长桥API环境变量未完全设置，缺少: {missing}")
    
    return all_set 