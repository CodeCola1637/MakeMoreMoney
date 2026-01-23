"""
strategy 模块
包含交易策略相关组件
"""

from strategy.signals import Signal, SignalType, SignalGenerator
from strategy.signal_filter import SignalFilter
from strategy.data_normalizer import DataNormalizer, get_default_normalizer
from strategy.correlation_filter import CorrelationFilter
from strategy.attention_lstm import AttentionLSTM, create_attention_lstm
from strategy.feature_engineer import FeatureEngineer, create_feature_engineer
from strategy.strategy_ensemble import StrategyEnsemble, EnsembleMethod
from strategy.technical_strategy import TechnicalStrategy
from strategy.portfolio_manager import PortfolioManager
from strategy.profit_stop_manager import ProfitStopManager

__all__ = [
    'Signal',
    'SignalType',
    'SignalGenerator',
    'SignalFilter',
    'DataNormalizer',
    'get_default_normalizer',
    'CorrelationFilter',
    'AttentionLSTM',
    'create_attention_lstm',
    'FeatureEngineer',
    'create_feature_engineer',
    'StrategyEnsemble',
    'EnsembleMethod',
    'TechnicalStrategy',
    'PortfolioManager',
    'ProfitStopManager'
]
