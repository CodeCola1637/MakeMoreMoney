#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Union, Any
import time
import tensorflow as tf
from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
from sklearn.model_selection import train_test_split
import matplotlib.pyplot as plt

from utils import ConfigLoader, setup_logger
from data_loader.historical import HistoricalDataLoader
from longport.openapi import Period, AdjustType

class LSTMModelTrainer:
    """LSTM模型训练器，负责训练和评估预测模型"""
    
    def __init__(self, config_loader: ConfigLoader, data_loader: HistoricalDataLoader):
        """
        初始化LSTM模型训练器
        
        Args:
            config_loader: 配置加载器
            data_loader: 历史数据加载器
        """
        self.config = config_loader
        self.data_loader = data_loader
        self.logger = setup_logger(
            "lstm_trainer", 
            self.config.get("logging.level", "INFO"),
            self.config.get("logging.file")
        )
        
        # 模型参数
        self.lookback_period = self.config.get("strategy.lookback_period", 30)
        self.model_path = self.config.get("strategy.model_path", "./models/lstm_model.h5")
        
        # 训练参数
        self.epochs = self.config.get("strategy.training.epochs", 100)
        self.batch_size = self.config.get("strategy.training.batch_size", 32)
        self.test_size = self.config.get("strategy.training.test_size", 0.2)
        
        # 特征列
        self.feature_cols = self.config.get("strategy.training.features", 
                                            ["close", "volume", "high", "low"])
        
        # 创建模型目录
        model_dir = os.path.dirname(self.model_path)
        if not os.path.exists(model_dir):
            os.makedirs(model_dir)
            
        # 已加载的模型
        self.model = None
    
    async def load_training_data(self, symbols: List[str], period: str = "Day", 
                                count: int = 300, adjust_type: str = "NoAdjust") -> Dict[str, pd.DataFrame]:
        """
        加载训练数据
        
        Args:
            symbols: 股票代码列表
            period: K线周期
            count: K线数量
            adjust_type: 复权类型
            
        Returns:
            训练数据字典
        """
        self.logger.info(f"加载训练数据: {symbols}")
        
        # 获取历史K线数据
        try:
            # 调用get_multiple_candlesticks并等待结果
            data_dict = await self.data_loader.get_multiple_candlesticks(
                symbols, 
                period, 
                count, 
                getattr(AdjustType, adjust_type)
            )
            
            # 检查每个股票的数据
            for symbol, df in data_dict.items():
                if df.empty:
                    self.logger.warning(f"股票 {symbol} 的历史数据为空")
                else:
                    self.logger.info(f"成功加载 {symbol} 的历史数据，共 {len(df)} 条记录")
            
            return data_dict
        except Exception as e:
            self.logger.error(f"加载训练数据失败: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def prepare_train_data(self, data_dict: Dict[str, pd.DataFrame]) -> Tuple[np.ndarray, np.ndarray]:
        """
        准备训练数据
        
        Args:
            data_dict: K线数据字典
            
        Returns:
            X_train, y_train: 训练数据
        """
        all_X = []
        all_y = []
        
        for symbol, df in data_dict.items():
            if df.empty:
                self.logger.warning(f"股票 {symbol} 数据为空，跳过")
                continue
                
            try:
                X, y = self.data_loader.prepare_feature_data(
                    df, 
                    self.lookback_period, 
                    target_col="close"
                )
                
                if len(X) > 0:
                    all_X.append(X)
                    all_y.append(y)
                    self.logger.info(f"股票 {symbol} 准备了 {len(X)} 个训练样本")
            except Exception as e:
                self.logger.error(f"准备 {symbol} 的训练数据时出错: {e}")
                
        if not all_X:
            raise ValueError("没有可用的训练数据")
            
        # 合并所有数据
        X_combined = np.vstack(all_X)
        y_combined = np.concatenate(all_y)
        
        return X_combined, y_combined
    
    def build_model(self, input_shape: Tuple[int, int]) -> Sequential:
        """
        构建LSTM模型
        
        Args:
            input_shape: 输入数据形状 (时间步, 特征数)
            
        Returns:
            构建好的模型
        """
        model = Sequential([
            LSTM(units=50, return_sequences=True, input_shape=input_shape),
            Dropout(0.2),
            LSTM(units=50, return_sequences=False),
            Dropout(0.2),
            Dense(units=25),
            Dense(units=1)
        ])
        
        model.compile(
            optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
            loss="mean_squared_error"
        )
        
        return model
    
    async def train_model(self, symbols: Optional[List[str]] = None, force_retrain: bool = False):
        """
        训练模型
        
        Args:
            symbols: 用于训练的股票代码列表，如果为None则使用配置中的股票
            force_retrain: 是否强制重新训练，即使模型文件已存在
            
        Returns:
            训练好的模型
        """
        # 检查是否需要重新训练
        if os.path.exists(self.model_path) and not force_retrain:
            self.logger.info(f"加载已有模型: {self.model_path}")
            self.model = load_model(self.model_path)
            return self.model
            
        if symbols is None:
            symbols = self.config.get("quote.symbols", [])
            
        if not symbols:
            raise ValueError("没有指定训练股票")
            
        self.logger.info(f"准备训练模型，使用股票: {symbols}")
        
        try:
            # 加载训练数据 - 添加await
            data_dict = await self.load_training_data(symbols)
            
            # 准备训练数据
            X, y = self.prepare_train_data(data_dict)
            
            # 划分训练集和测试集
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=self.test_size, shuffle=False
            )
            
            self.logger.info(f"训练数据形状: {X_train.shape}, 测试数据形状: {X_test.shape}")
            
            # 构建模型
            input_shape = (X_train.shape[1], X_train.shape[2])
            model = self.build_model(input_shape)
            
            # 设置早停回调
            early_stopping = EarlyStopping(
                monitor="val_loss",
                patience=10,
                restore_best_weights=True
            )
            
            # 设置模型检查点
            checkpoint = ModelCheckpoint(
                self.model_path,
                monitor="val_loss",
                save_best_only=True,
                save_weights_only=False,
                verbose=1
            )
            
            # 训练模型
            self.logger.info("开始训练模型...")
            history = model.fit(
                X_train, y_train,
                epochs=self.epochs,
                batch_size=self.batch_size,
                validation_data=(X_test, y_test),
                callbacks=[early_stopping, checkpoint],
                verbose=1
            )
            
            # 保存训练历史
            self._save_training_history(history)
            
            # 评估模型
            test_loss = model.evaluate(X_test, y_test, verbose=0)
            self.logger.info(f"测试集损失: {test_loss}")
            
            # 保存模型
            self.model = model
            
            return model
        except Exception as e:
            self.logger.error(f"训练模型失败: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        使用模型进行预测
        
        Args:
            X: 输入特征数据，形状为 (样本数, 时间步, 特征数)
            
        Returns:
            预测结果，形状为 (样本数,)
        """
        if self.model is None:
            if os.path.exists(self.model_path):
                self.logger.info(f"加载模型: {self.model_path}")
                self.model = load_model(self.model_path)
            else:
                raise ValueError("模型未训练，无法进行预测")
                
        return self.model.predict(X).flatten()
    
    async def predict_next(self, symbol: str, period: str = "Day") -> Dict[str, Any]:
        """
        预测下一个交易日的价格变化
        
        Args:
            symbol: 股票代码
            period: K线周期
            
        Returns:
            预测结果字典
        """
        try:
            # 获取历史K线数据 - 添加await
            df = await self.data_loader.get_candlesticks(
                symbol, 
                period, 
                self.lookback_period + 10,  # 多获取一些数据
                AdjustType.NoAdjust
            )
            
            if df.empty:
                self.logger.warning(f"{symbol} 历史数据为空")
                return {"symbol": symbol, "error": "无法获取K线数据"}
                
            # 准备特征数据
            try:
                X, _ = self.data_loader.prepare_feature_data(
                    df, 
                    self.lookback_period, 
                    target_col="close"
                )
                
                if len(X) == 0:
                    self.logger.warning(f"{symbol} 特征数据为空")
                    return {"symbol": symbol, "error": "无法准备预测特征"}
                    
                # 获取最新的一组特征
                latest_features = X[-1].reshape(1, X.shape[1], X.shape[2])
                
                # 预测
                pred = self.predict(latest_features)[0]
                
                # 获取最新价格
                latest_price = float(df.iloc[-1]["close"])
                
                # 预测价格
                predicted_change_pct = pred * 100  # 转换为百分比
                predicted_price = latest_price * (1 + pred)
                
                self.logger.info(f"{symbol} 预测变化: {predicted_change_pct:.2f}%, 价格: {predicted_price:.2f}")
                
                result = {
                    "symbol": symbol,
                    "timestamp": df.iloc[-1]["timestamp"],
                    "latest_price": latest_price,
                    "predicted_change_pct": predicted_change_pct,
                    "predicted_price": predicted_price,
                    "signal": "BUY" if pred > 0.01 else ("SELL" if pred < -0.01 else "HOLD")
                }
                
                return result
            except Exception as e:
                self.logger.error(f"预测 {symbol} 时出错: {e}")
                import traceback
                traceback.print_exc()
                return {"symbol": symbol, "error": str(e)}
        except Exception as e:
            self.logger.error(f"获取 {symbol} 历史数据失败: {e}")
            import traceback
            traceback.print_exc()
            return {"symbol": symbol, "error": str(e)}
    
    def _save_training_history(self, history):
        """保存训练历史到图表"""
        plt.figure(figsize=(12, 4))
        
        # 绘制损失曲线
        plt.subplot(1, 2, 1)
        plt.plot(history.history["loss"], label="Training Loss")
        plt.plot(history.history["val_loss"], label="Validation Loss")
        plt.title("Model Loss")
        plt.xlabel("Epoch")
        plt.ylabel("Loss")
        plt.legend()
        
        # 保存图表
        history_dir = os.path.join(os.path.dirname(self.model_path), "history")
        if not os.path.exists(history_dir):
            os.makedirs(history_dir)
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        plt.savefig(os.path.join(history_dir, f"training_history_{timestamp}.png"))
        self.logger.info(f"训练历史已保存到图表")
        plt.close()
