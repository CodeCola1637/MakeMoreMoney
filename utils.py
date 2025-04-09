#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import yaml
import logging
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, Any, Optional

class ConfigLoader:
    """配置加载器，用于加载配置文件和环境变量"""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化配置加载器
        
        Args:
            config_path: 配置文件路径
        """
        # 加载.env文件
        load_dotenv()
        
        # 加载配置文件
        self.config_path = config_path
        self.config = self._load_config()
        
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

def setup_logger(name: str, level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """
    设置日志记录器
    
    Args:
        name: 日志记录器名称
        level: 日志级别
        log_file: 日志文件路径，如果为None则只输出到控制台
        
    Returns:
        配置好的日志记录器
    """
    # 创建日志记录器
    logger = logging.getLogger(name)
    
    # 设置日志级别
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    logger.setLevel(level_map.get(level.upper(), logging.INFO))
    
    # 创建格式化器
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 如果指定了日志文件，创建文件处理器
    if log_file:
        # 确保日志目录存在
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger 

def setup_longport_env():
    """设置长桥API所需的环境变量"""
    logger = logging.getLogger("longport_env")
    
    # 映射环境变量
    mapping = {
        "LONG_PORT_APP_KEY": "LONGPORT_APP_KEY",
        "LONG_PORT_APP_SECRET": "LONGPORT_APP_SECRET",
        "LONG_PORT_ACCESS_TOKEN": "LONGPORT_ACCESS_TOKEN",
    }
    
    # 设置超时
    os.environ["LONGBRIDGE_NETWORK_TIMEOUT"] = "60"
    
    # 复制环境变量
    for src, dst in mapping.items():
        if os.getenv(src):
            os.environ[dst] = os.getenv(src)
            logger.debug(f"设置环境变量 {dst} 来自 {src}")
        else:
            logger.warning(f"未找到环境变量 {src}")
    
    # 验证设置
    all_set = all(os.getenv(env) for env in ["LONGPORT_APP_KEY", "LONGPORT_APP_SECRET", "LONGPORT_ACCESS_TOKEN"])
    if all_set:
        logger.info("长桥API环境变量已设置")
    else:
        logger.warning("长桥API环境变量未完全设置，可能导致API连接问题")
    
    return all_set 