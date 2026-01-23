"""
数据归一化器
统一的归一化逻辑，确保训练和预测时使用一致的归一化方法
"""

import numpy as np
import pandas as pd
import pickle
import os
import logging
from typing import Dict, List, Tuple, Optional, Union
from datetime import datetime


class DataNormalizer:
    """
    统一的数据归一化器
    
    解决的问题：
    1. 训练时（historical.py）和预测时（signals.py）归一化方法不一致
    2. 没有保存归一化参数，导致每次预测使用不同的参数
    
    方案：
    1. 训练时拟合并保存归一化参数（每个特征的min/max）
    2. 预测时加载保存的参数进行归一化
    3. 支持滑动窗口归一化（避免未来数据泄露）
    """
    
    def __init__(self, scaler_path: str = "./models/scaler.pkl", logger=None):
        """
        初始化数据归一化器
        
        Args:
            scaler_path: 归一化参数保存路径
            logger: 日志记录器
        """
        self.scaler_path = scaler_path
        self.logger = logger or logging.getLogger(__name__)
        
        # 特征归一化参数
        # {feature_name: {'min': float, 'max': float, 'range': float}}
        self.feature_params: Dict[str, Dict[str, float]] = {}
        
        # 是否已拟合
        self.is_fitted = False
        
        # 拟合时的特征列名
        self.feature_names: List[str] = []
        
        # 拟合时的统计信息
        self.fit_info = {
            'fit_time': None,
            'sample_count': 0,
            'feature_count': 0
        }
        
        # 尝试加载已保存的参数
        if os.path.exists(scaler_path):
            self.load()
    
    def fit(self, data: Union[np.ndarray, pd.DataFrame], feature_names: Optional[List[str]] = None):
        """
        拟合归一化参数
        
        使用训练数据计算每个特征的全局min/max值
        
        Args:
            data: 训练数据，shape=(样本数, 特征数) 或 DataFrame
            feature_names: 特征列名列表
        """
        if isinstance(data, pd.DataFrame):
            if feature_names is None:
                feature_names = data.columns.tolist()
            data = data[feature_names].values
        
        if feature_names is None:
            feature_names = [f"feature_{i}" for i in range(data.shape[1])]
        
        self.feature_names = feature_names
        self.feature_params = {}
        
        for i, name in enumerate(feature_names):
            col_data = data[:, i]
            min_val = float(np.nanmin(col_data))
            max_val = float(np.nanmax(col_data))
            range_val = max_val - min_val
            
            self.feature_params[name] = {
                'min': min_val,
                'max': max_val,
                'range': range_val if range_val > 0 else 1.0,  # 避免除零
                'index': i
            }
            
            self.logger.debug(f"特征 {name}: min={min_val:.4f}, max={max_val:.4f}, range={range_val:.4f}")
        
        self.is_fitted = True
        self.fit_info = {
            'fit_time': datetime.now().isoformat(),
            'sample_count': len(data),
            'feature_count': len(feature_names)
        }
        
        # 自动保存
        self.save()
        
        self.logger.info(f"归一化器拟合完成: {len(feature_names)}个特征, {len(data)}个样本")
    
    def transform(self, data: Union[np.ndarray, pd.DataFrame], 
                  feature_names: Optional[List[str]] = None) -> np.ndarray:
        """
        使用保存的参数进行归一化
        
        Args:
            data: 要归一化的数据
            feature_names: 特征列名列表（如果与拟合时不同需指定）
            
        Returns:
            归一化后的数据
        """
        if isinstance(data, pd.DataFrame):
            if feature_names is None:
                feature_names = data.columns.tolist()
            data = data[feature_names].values
        
        if feature_names is None:
            feature_names = self.feature_names
        
        normalized = np.zeros_like(data, dtype=np.float32)
        
        for i, name in enumerate(feature_names):
            if name in self.feature_params:
                params = self.feature_params[name]
                min_val = params['min']
                range_val = params['range']
                
                # Min-Max 归一化到 [0, 1]
                normalized[:, i] = (data[:, i] - min_val) / range_val
                
                # 裁剪到 [0, 1] 范围（处理超出训练范围的数据）
                normalized[:, i] = np.clip(normalized[:, i], 0, 1)
            else:
                # 未知特征使用局部归一化（带警告）
                self.logger.warning(f"特征 {name} 不在拟合参数中，使用局部归一化")
                col_min = data[:, i].min()
                col_max = data[:, i].max()
                if col_max > col_min:
                    normalized[:, i] = (data[:, i] - col_min) / (col_max - col_min)
                else:
                    normalized[:, i] = 0.5
        
        return normalized
    
    def fit_transform(self, data: Union[np.ndarray, pd.DataFrame],
                      feature_names: Optional[List[str]] = None) -> np.ndarray:
        """
        拟合并转换数据
        
        Args:
            data: 训练数据
            feature_names: 特征列名列表
            
        Returns:
            归一化后的数据
        """
        self.fit(data, feature_names)
        return self.transform(data, feature_names)
    
    def transform_window(self, window_data: np.ndarray, 
                         feature_names: Optional[List[str]] = None,
                         use_global_params: bool = True) -> np.ndarray:
        """
        归一化单个时间窗口的数据
        
        这个方法专门用于预测时的数据归一化，保证与训练时一致
        
        Args:
            window_data: 时间窗口数据，shape=(时间步, 特征数)
            feature_names: 特征列名列表
            use_global_params: 是否使用全局参数（True）还是窗口内参数（False）
            
        Returns:
            归一化后的数据
        """
        if feature_names is None:
            feature_names = self.feature_names
        
        if use_global_params and self.is_fitted:
            # 使用全局参数（推荐，与训练一致）
            return self.transform(window_data, feature_names)
        else:
            # 使用窗口内参数（后备方案）
            normalized = np.zeros_like(window_data, dtype=np.float32)
            
            for i in range(window_data.shape[1]):
                col_min = window_data[:, i].min()
                col_max = window_data[:, i].max()
                
                if col_max > col_min:
                    normalized[:, i] = (window_data[:, i] - col_min) / (col_max - col_min)
                else:
                    normalized[:, i] = 0.5
            
            return normalized
    
    def inverse_transform(self, normalized_data: np.ndarray,
                          feature_names: Optional[List[str]] = None) -> np.ndarray:
        """
        反归一化
        
        Args:
            normalized_data: 归一化后的数据
            feature_names: 特征列名列表
            
        Returns:
            原始尺度的数据
        """
        if feature_names is None:
            feature_names = self.feature_names
        
        original = np.zeros_like(normalized_data, dtype=np.float32)
        
        for i, name in enumerate(feature_names):
            if name in self.feature_params:
                params = self.feature_params[name]
                min_val = params['min']
                range_val = params['range']
                
                original[:, i] = normalized_data[:, i] * range_val + min_val
            else:
                # 无法反归一化
                original[:, i] = normalized_data[:, i]
                self.logger.warning(f"无法反归一化特征 {name}")
        
        return original
    
    def save(self, path: Optional[str] = None):
        """
        保存归一化参数
        
        Args:
            path: 保存路径，如果为None则使用默认路径
        """
        save_path = path or self.scaler_path
        
        # 确保目录存在
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        
        save_data = {
            'feature_params': self.feature_params,
            'feature_names': self.feature_names,
            'fit_info': self.fit_info,
            'version': '1.0'
        }
        
        with open(save_path, 'wb') as f:
            pickle.dump(save_data, f)
        
        self.logger.info(f"归一化参数已保存到: {save_path}")
    
    def load(self, path: Optional[str] = None) -> bool:
        """
        加载归一化参数
        
        Args:
            path: 加载路径，如果为None则使用默认路径
            
        Returns:
            是否成功加载
        """
        load_path = path or self.scaler_path
        
        if not os.path.exists(load_path):
            self.logger.warning(f"归一化参数文件不存在: {load_path}")
            return False
        
        try:
            with open(load_path, 'rb') as f:
                save_data = pickle.load(f)
            
            self.feature_params = save_data.get('feature_params', {})
            self.feature_names = save_data.get('feature_names', [])
            self.fit_info = save_data.get('fit_info', {})
            self.is_fitted = len(self.feature_params) > 0
            
            self.logger.info(f"归一化参数已加载: {len(self.feature_params)}个特征, "
                           f"拟合时间: {self.fit_info.get('fit_time', 'unknown')}")
            return True
            
        except Exception as e:
            self.logger.error(f"加载归一化参数失败: {e}")
            return False
    
    def get_feature_info(self, feature_name: str) -> Optional[Dict]:
        """
        获取指定特征的归一化参数
        
        Args:
            feature_name: 特征名称
            
        Returns:
            特征参数字典
        """
        return self.feature_params.get(feature_name)
    
    def get_summary(self) -> str:
        """
        获取归一化器状态摘要
        
        Returns:
            格式化的摘要字符串
        """
        lines = ["📊 数据归一化器状态:"]
        lines.append(f"   已拟合: {'是' if self.is_fitted else '否'}")
        lines.append(f"   参数文件: {self.scaler_path}")
        
        if self.is_fitted:
            lines.append(f"   拟合时间: {self.fit_info.get('fit_time', 'unknown')}")
            lines.append(f"   样本数量: {self.fit_info.get('sample_count', 0)}")
            lines.append(f"   特征数量: {len(self.feature_params)}")
            lines.append("   特征参数:")
            for name, params in self.feature_params.items():
                lines.append(f"     - {name}: min={params['min']:.4f}, max={params['max']:.4f}")
        
        return "\n".join(lines)
    
    def update_params(self, new_data: Union[np.ndarray, pd.DataFrame],
                      feature_names: Optional[List[str]] = None,
                      update_ratio: float = 0.1):
        """
        增量更新归一化参数（用于在线学习）
        
        使用指数移动平均更新min/max值
        
        Args:
            new_data: 新数据
            feature_names: 特征列名列表
            update_ratio: 更新比例（0-1之间）
        """
        if isinstance(new_data, pd.DataFrame):
            if feature_names is None:
                feature_names = new_data.columns.tolist()
            new_data = new_data[feature_names].values
        
        if feature_names is None:
            feature_names = self.feature_names
        
        for i, name in enumerate(feature_names):
            if name in self.feature_params:
                new_min = float(np.nanmin(new_data[:, i]))
                new_max = float(np.nanmax(new_data[:, i]))
                
                old_params = self.feature_params[name]
                
                # 指数移动平均更新
                updated_min = old_params['min'] * (1 - update_ratio) + new_min * update_ratio
                updated_max = old_params['max'] * (1 - update_ratio) + new_max * update_ratio
                
                # 扩展范围而不是缩小（更安全）
                updated_min = min(updated_min, new_min)
                updated_max = max(updated_max, new_max)
                
                self.feature_params[name] = {
                    'min': updated_min,
                    'max': updated_max,
                    'range': updated_max - updated_min if updated_max > updated_min else 1.0,
                    'index': i
                }
        
        self.fit_info['last_update'] = datetime.now().isoformat()
        self.save()
        
        self.logger.info(f"归一化参数已增量更新: {len(feature_names)}个特征")


# 全局单例实例
_default_normalizer: Optional[DataNormalizer] = None


def get_default_normalizer(scaler_path: str = "./models/scaler.pkl") -> DataNormalizer:
    """
    获取默认的归一化器实例（单例模式）
    
    Args:
        scaler_path: 归一化参数保存路径
        
    Returns:
        DataNormalizer实例
    """
    global _default_normalizer
    
    if _default_normalizer is None:
        _default_normalizer = DataNormalizer(scaler_path)
    
    return _default_normalizer
