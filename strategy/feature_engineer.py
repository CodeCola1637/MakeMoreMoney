"""
特征工程模块
负责生成和管理各种技术指标和衍生特征
"""

import numpy as np
import pandas as pd
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class FeatureEngineer:
    """
    特征工程类
    
    生成丰富的技术指标和衍生特征，用于增强模型预测能力
    
    特征类别：
    1. 价格特征 - 收益率、价格比率等
    2. 动量指标 - RSI、MACD、ROC等
    3. 波动性指标 - ATR、布林带宽度等
    4. 趋势指标 - SMA、EMA、ADX等
    5. 量价指标 - OBV、成交量变化等
    6. 统计特征 - 滚动统计量
    """
    
    def __init__(self, config=None, logger=None):
        """
        初始化特征工程类
        
        Args:
            config: 配置对象（可选）
            logger: 日志记录器（可选）
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # 特征配置
        self.short_period = 5
        self.medium_period = 14
        self.long_period = 30
        self.ema_periods = [5, 10, 20, 50]
        self.sma_periods = [5, 10, 20, 50]
        
        # 生成的特征列表
        self.generated_features: List[str] = []
        
        self.logger.info("特征工程模块初始化完成")
    
    def generate_all_features(self, df: pd.DataFrame, 
                               include_volume: bool = True) -> pd.DataFrame:
        """
        生成所有特征
        
        Args:
            df: 原始数据 DataFrame (需包含 open, high, low, close, volume)
            include_volume: 是否包含成交量相关特征
            
        Returns:
            包含所有特征的 DataFrame
        """
        result = df.copy()
        
        # 确保必要的列存在
        required_cols = ['open', 'high', 'low', 'close']
        if include_volume:
            required_cols.append('volume')
        
        for col in required_cols:
            if col not in result.columns:
                self.logger.warning(f"缺少必要列: {col}")
                if col == 'volume':
                    include_volume = False
        
        # 1. 价格特征
        result = self._add_price_features(result)
        
        # 2. 动量指标
        result = self._add_momentum_indicators(result)
        
        # 3. 波动性指标
        result = self._add_volatility_indicators(result)
        
        # 4. 趋势指标
        result = self._add_trend_indicators(result)
        
        # 5. 量价指标
        if include_volume:
            result = self._add_volume_indicators(result)
        
        # 6. 统计特征
        result = self._add_statistical_features(result)
        
        # 7. 时间特征
        result = self._add_time_features(result)
        
        # 更新生成的特征列表
        self.generated_features = [
            col for col in result.columns 
            if col not in df.columns
        ]
        
        self.logger.info(f"共生成 {len(self.generated_features)} 个特征")
        
        return result
    
    def _add_price_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加价格相关特征"""
        
        # 收益率
        df['returns'] = df['close'].pct_change()
        df['log_returns'] = np.log(df['close'] / df['close'].shift(1))
        
        # 价格比率
        df['hl_ratio'] = df['high'] / df['low']
        df['co_ratio'] = df['close'] / df['open']
        df['hc_ratio'] = df['high'] / df['close']
        df['lc_ratio'] = df['low'] / df['close']
        
        # 价格差
        df['hl_spread'] = df['high'] - df['low']
        df['co_spread'] = df['close'] - df['open']
        
        # 真实波幅 (True Range)
        df['true_range'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift(1)),
                abs(df['low'] - df['close'].shift(1))
            )
        )
        
        # 累积收益
        df['cum_returns_5'] = df['returns'].rolling(5).sum()
        df['cum_returns_10'] = df['returns'].rolling(10).sum()
        
        return df
    
    def _add_momentum_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加动量指标"""
        
        # RSI (Relative Strength Index)
        for period in [7, 14, 21]:
            df[f'rsi_{period}'] = self._calculate_rsi(df['close'], period)
        
        # MACD
        macd, signal, hist = self._calculate_macd(df['close'])
        df['macd'] = macd
        df['macd_signal'] = signal
        df['macd_hist'] = hist
        
        # ROC (Rate of Change)
        for period in [5, 10, 20]:
            df[f'roc_{period}'] = ((df['close'] - df['close'].shift(period)) / 
                                   df['close'].shift(period)) * 100
        
        # 动量
        for period in [5, 10, 20]:
            df[f'momentum_{period}'] = df['close'] - df['close'].shift(period)
        
        # Stochastic Oscillator
        stoch_k, stoch_d = self._calculate_stochastic(df, 14, 3)
        df['stoch_k'] = stoch_k
        df['stoch_d'] = stoch_d
        
        # Williams %R
        df['williams_r'] = self._calculate_williams_r(df, 14)
        
        # CCI (Commodity Channel Index)
        df['cci'] = self._calculate_cci(df, 20)
        
        return df
    
    def _add_volatility_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加波动性指标"""
        
        # ATR (Average True Range)
        for period in [7, 14, 21]:
            df[f'atr_{period}'] = df['true_range'].rolling(period).mean()
        
        # 波动率 (标准差)
        for period in [5, 10, 20]:
            df[f'volatility_{period}'] = df['returns'].rolling(period).std()
        
        # 布林带
        for period in [20]:
            sma = df['close'].rolling(period).mean()
            std = df['close'].rolling(period).std()
            df[f'bb_upper_{period}'] = sma + 2 * std
            df[f'bb_lower_{period}'] = sma - 2 * std
            df[f'bb_width_{period}'] = (df[f'bb_upper_{period}'] - df[f'bb_lower_{period}']) / sma
            df[f'bb_position_{period}'] = (df['close'] - df[f'bb_lower_{period}']) / (df[f'bb_upper_{period}'] - df[f'bb_lower_{period}'])
        
        # 价格振幅
        df['price_range'] = (df['high'] - df['low']) / df['close']
        
        # Keltner Channel
        ema20 = df['close'].ewm(span=20).mean()
        atr10 = df['true_range'].rolling(10).mean()
        df['keltner_upper'] = ema20 + 2 * atr10
        df['keltner_lower'] = ema20 - 2 * atr10
        
        return df
    
    def _add_trend_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加趋势指标"""
        
        # 简单移动平均 (SMA)
        for period in self.sma_periods:
            df[f'sma_{period}'] = df['close'].rolling(period).mean()
        
        # 指数移动平均 (EMA)
        for period in self.ema_periods:
            df[f'ema_{period}'] = df['close'].ewm(span=period).mean()
        
        # 均线交叉信号
        df['sma_cross_5_20'] = (df['sma_5'] > df['sma_20']).astype(int)
        df['sma_cross_10_50'] = (df['sma_10'] > df['sma_50']).astype(int)
        df['ema_cross_5_20'] = (df['ema_5'] > df['ema_20']).astype(int)
        
        # 价格相对于均线的位置
        df['price_to_sma_20'] = df['close'] / df['sma_20']
        df['price_to_ema_20'] = df['close'] / df['ema_20']
        
        # 趋势强度 (简化版ADX)
        df['trend_strength'] = abs(df['ema_5'] - df['ema_20']) / df['ema_20']
        
        # 趋势方向
        df['trend_direction'] = np.sign(df['ema_5'] - df['ema_20'])
        
        # 均线斜率
        df['sma_20_slope'] = (df['sma_20'] - df['sma_20'].shift(5)) / df['sma_20'].shift(5)
        df['ema_20_slope'] = (df['ema_20'] - df['ema_20'].shift(5)) / df['ema_20'].shift(5)
        
        return df
    
    def _add_volume_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加量价指标"""
        
        # 成交量变化率
        df['volume_change'] = df['volume'].pct_change()
        
        # 成交量移动平均
        for period in [5, 10, 20]:
            df[f'volume_sma_{period}'] = df['volume'].rolling(period).mean()
        
        # 相对成交量
        df['relative_volume'] = df['volume'] / df['volume_sma_20']
        
        # OBV (On-Balance Volume)
        df['obv'] = self._calculate_obv(df)
        df['obv_sma_10'] = df['obv'].rolling(10).mean()
        
        # 量价相关性
        df['price_volume_corr'] = df['close'].rolling(20).corr(df['volume'])
        
        # 成交量加权价格
        df['vwap'] = (df['close'] * df['volume']).rolling(20).sum() / df['volume'].rolling(20).sum()
        df['price_to_vwap'] = df['close'] / df['vwap']
        
        # 力量指数
        df['force_index'] = df['close'].diff() * df['volume']
        df['force_index_ema'] = df['force_index'].ewm(span=13).mean()
        
        return df
    
    def _add_statistical_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加统计特征"""
        
        # 滚动统计量
        for period in [5, 10, 20]:
            # 均值
            df[f'close_mean_{period}'] = df['close'].rolling(period).mean()
            # 标准差
            df[f'close_std_{period}'] = df['close'].rolling(period).std()
            # 偏度
            df[f'returns_skew_{period}'] = df['returns'].rolling(period).skew()
            # 峰度
            df[f'returns_kurt_{period}'] = df['returns'].rolling(period).kurt()
            # 最大值
            df[f'close_max_{period}'] = df['close'].rolling(period).max()
            # 最小值
            df[f'close_min_{period}'] = df['close'].rolling(period).min()
        
        # Z-Score
        df['zscore_20'] = (df['close'] - df['close_mean_20']) / df['close_std_20']
        
        # 百分位排名
        df['percentile_20'] = df['close'].rolling(20).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )
        
        return df
    
    def _add_time_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加时间特征"""
        
        try:
            if 'timestamp' in df.columns:
                timestamps = pd.to_datetime(df['timestamp'])
                
                # 星期几 (0=周一, 6=周日)
                df['day_of_week'] = timestamps.dt.dayofweek
                
                # 月份
                df['month'] = timestamps.dt.month
                
                # 一周中的位置 (周一=0, 周五=4)
                df['week_position'] = df['day_of_week'] / 4.0
                
                # 是否是周一/周五
                df['is_monday'] = (df['day_of_week'] == 0).astype(int)
                df['is_friday'] = (df['day_of_week'] == 4).astype(int)
                
                # 月初/月末
                df['is_month_start'] = (timestamps.dt.day <= 5).astype(int)
                df['is_month_end'] = (timestamps.dt.day >= 25).astype(int)
            elif hasattr(df.index, 'dayofweek'):
                # 如果索引是DatetimeIndex
                df['day_of_week'] = df.index.dayofweek
                df['month'] = df.index.month
                df['week_position'] = df['day_of_week'] / 4.0
                df['is_monday'] = (df['day_of_week'] == 0).astype(int)
                df['is_friday'] = (df['day_of_week'] == 4).astype(int)
                df['is_month_start'] = (df.index.day <= 5).astype(int)
                df['is_month_end'] = (df.index.day >= 25).astype(int)
        except Exception as e:
            self.logger.warning(f"添加时间特征失败: {e}")
        
        return df
    
    # ============ 辅助计算函数 ============
    
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """计算 RSI"""
        delta = prices.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_macd(self, prices: pd.Series, 
                        fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """计算 MACD"""
        ema_fast = prices.ewm(span=fast).mean()
        ema_slow = prices.ewm(span=slow).mean()
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=signal).mean()
        histogram = macd - signal_line
        
        return macd, signal_line, histogram
    
    def _calculate_stochastic(self, df: pd.DataFrame, 
                              k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
        """计算随机振荡器"""
        low_min = df['low'].rolling(k_period).min()
        high_max = df['high'].rolling(k_period).max()
        
        stoch_k = 100 * (df['close'] - low_min) / (high_max - low_min)
        stoch_d = stoch_k.rolling(d_period).mean()
        
        return stoch_k, stoch_d
    
    def _calculate_williams_r(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """计算威廉指标"""
        high_max = df['high'].rolling(period).max()
        low_min = df['low'].rolling(period).min()
        
        williams_r = -100 * (high_max - df['close']) / (high_max - low_min)
        
        return williams_r
    
    def _calculate_cci(self, df: pd.DataFrame, period: int = 20) -> pd.Series:
        """计算CCI"""
        tp = (df['high'] + df['low'] + df['close']) / 3
        sma_tp = tp.rolling(period).mean()
        mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        
        cci = (tp - sma_tp) / (0.015 * mad)
        
        return cci
    
    def _calculate_obv(self, df: pd.DataFrame) -> pd.Series:
        """计算OBV"""
        obv = np.where(df['close'] > df['close'].shift(1), df['volume'],
                       np.where(df['close'] < df['close'].shift(1), -df['volume'], 0))
        return pd.Series(obv, index=df.index).cumsum()
    
    def get_feature_names(self) -> List[str]:
        """获取生成的特征名列表"""
        return self.generated_features.copy()
    
    def select_features(self, df: pd.DataFrame, 
                        feature_list: List[str]) -> pd.DataFrame:
        """
        选择特定的特征
        
        Args:
            df: 包含特征的 DataFrame
            feature_list: 需要的特征列表
            
        Returns:
            只包含指定特征的 DataFrame
        """
        available = [f for f in feature_list if f in df.columns]
        missing = [f for f in feature_list if f not in df.columns]
        
        if missing:
            self.logger.warning(f"缺少以下特征: {missing}")
        
        return df[available].copy()
    
    def get_feature_summary(self) -> Dict:
        """获取特征工程摘要"""
        return {
            'total_features': len(self.generated_features),
            'feature_categories': {
                'price': [f for f in self.generated_features if 'returns' in f or 'ratio' in f or 'spread' in f],
                'momentum': [f for f in self.generated_features if 'rsi' in f or 'macd' in f or 'roc' in f or 'momentum' in f],
                'volatility': [f for f in self.generated_features if 'atr' in f or 'volatility' in f or 'bb_' in f],
                'trend': [f for f in self.generated_features if 'sma' in f or 'ema' in f or 'trend' in f],
                'volume': [f for f in self.generated_features if 'volume' in f or 'obv' in f or 'vwap' in f],
                'statistical': [f for f in self.generated_features if 'mean' in f or 'std' in f or 'skew' in f or 'kurt' in f],
                'time': [f for f in self.generated_features if 'day' in f or 'month' in f or 'week' in f]
            },
            'feature_list': self.generated_features
        }


def create_feature_engineer(config=None, logger=None) -> FeatureEngineer:
    """
    工厂函数：创建特征工程实例
    
    Args:
        config: 配置对象
        logger: 日志记录器
        
    Returns:
        FeatureEngineer 实例
    """
    return FeatureEngineer(config, logger)
