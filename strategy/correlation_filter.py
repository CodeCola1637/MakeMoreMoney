"""
相关性过滤器
实现持仓相关性检查，避免过度集中的风险敞口
"""

import numpy as np
import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict


class CorrelationFilter:
    """
    相关性过滤器 - 风险分散
    
    功能：
    1. 计算股票间的收益率相关性矩阵
    2. 检查新仓位与现有持仓的相关性
    3. 拒绝与现有持仓高度相关的新交易
    4. 支持动态更新相关性矩阵
    """
    
    def __init__(self, config, historical_loader, logger=None):
        """
        初始化相关性过滤器
        
        Args:
            config: 配置对象
            historical_loader: 历史数据加载器
            logger: 日志记录器
        """
        self.config = config
        self.historical_loader = historical_loader
        self.logger = logger or logging.getLogger(__name__)
        
        # 配置参数
        self.max_correlation = config.get("execution.risk_control.max_correlation", 0.7)
        self.lookback_days = config.get("strategy.correlation_lookback_days", 60)
        self.update_interval_hours = config.get("strategy.correlation_update_interval", 24)
        
        # 相关性矩阵
        self.correlation_matrix: Optional[pd.DataFrame] = None
        self.last_update: Optional[datetime] = None
        self.symbols_in_matrix: List[str] = []
        
        # 缓存的收益率数据
        self.returns_cache: Dict[str, pd.Series] = {}
        
        self.logger.info(f"相关性过滤器初始化完成 - 最大相关性: {self.max_correlation}, "
                        f"回看天数: {self.lookback_days}, "
                        f"更新间隔: {self.update_interval_hours}小时")
    
    async def update_correlation_matrix(self, symbols: List[str], force: bool = False):
        """
        更新相关性矩阵
        
        Args:
            symbols: 股票代码列表
            force: 是否强制更新（忽略更新间隔）
        """
        # 检查是否需要更新
        if not force and self.last_update:
            time_since_update = datetime.now() - self.last_update
            if time_since_update.total_seconds() < self.update_interval_hours * 3600:
                self.logger.debug("相关性矩阵仍在有效期内，跳过更新")
                return
        
        self.logger.info(f"开始更新相关性矩阵: {symbols}")
        
        returns_data = {}
        
        for symbol in symbols:
            try:
                # 获取历史数据
                df = await self.historical_loader.get_candlesticks(
                    symbol=symbol,
                    count=self.lookback_days + 10  # 多获取一些以确保有足够数据
                )
                
                if df is not None and not df.empty and len(df) >= 20:
                    # 计算日收益率
                    returns = df['close'].pct_change().dropna()
                    returns_data[symbol] = returns
                    self.returns_cache[symbol] = returns
                    self.logger.debug(f"获取 {symbol} 收益率数据: {len(returns)}天")
                else:
                    self.logger.warning(f"股票 {symbol} 数据不足，跳过")
                    
            except Exception as e:
                self.logger.error(f"获取 {symbol} 历史数据失败: {e}")
        
        if len(returns_data) >= 2:
            # 构建收益率DataFrame
            returns_df = pd.DataFrame(returns_data)
            
            # 对齐数据（取交集）
            returns_df = returns_df.dropna()
            
            if len(returns_df) >= 10:
                # 计算相关性矩阵
                self.correlation_matrix = returns_df.corr()
                self.symbols_in_matrix = list(returns_df.columns)
                self.last_update = datetime.now()
                
                self.logger.info(f"相关性矩阵更新完成: {len(self.symbols_in_matrix)}只股票, "
                               f"{len(returns_df)}天数据")
                
                # 记录高相关性对
                self._log_high_correlations()
            else:
                self.logger.warning(f"对齐后数据不足: {len(returns_df)}天")
        else:
            self.logger.warning(f"有效股票数据不足: {len(returns_data)}只")
    
    def _log_high_correlations(self):
        """记录高相关性的股票对"""
        if self.correlation_matrix is None:
            return
        
        high_corr_pairs = []
        symbols = self.correlation_matrix.columns
        
        for i, sym1 in enumerate(symbols):
            for j, sym2 in enumerate(symbols):
                if i < j:  # 只检查上三角
                    corr = self.correlation_matrix.loc[sym1, sym2]
                    if abs(corr) > self.max_correlation:
                        high_corr_pairs.append((sym1, sym2, corr))
        
        if high_corr_pairs:
            self.logger.info(f"发现 {len(high_corr_pairs)} 对高相关性股票:")
            for sym1, sym2, corr in high_corr_pairs:
                self.logger.info(f"   {sym1} <-> {sym2}: {corr:.3f}")
    
    def check_correlation(self, new_symbol: str, current_positions: List[str]) -> Tuple[bool, str]:
        """
        检查新仓位与现有持仓的相关性
        
        Args:
            new_symbol: 新股票代码
            current_positions: 当前持仓的股票代码列表
            
        Returns:
            Tuple[bool, str]: (是否通过检查, 消息)
        """
        # 如果没有相关性矩阵，允许交易
        if self.correlation_matrix is None:
            return True, "相关性矩阵未初始化，允许交易"
        
        # 如果新股票不在矩阵中，允许交易
        if new_symbol not in self.correlation_matrix.columns:
            return True, f"股票 {new_symbol} 不在相关性矩阵中，允许交易"
        
        # 如果没有现有持仓，允许交易
        if not current_positions:
            return True, "没有现有持仓，允许交易"
        
        # 检查与每个现有持仓的相关性
        high_correlations = []
        
        for existing_symbol in current_positions:
            if existing_symbol in self.correlation_matrix.columns:
                corr = abs(self.correlation_matrix.loc[new_symbol, existing_symbol])
                
                if corr > self.max_correlation:
                    high_correlations.append((existing_symbol, corr))
        
        if high_correlations:
            details = ", ".join([f"{s}:{c:.2f}" for s, c in high_correlations])
            return False, f"与现有持仓相关性过高: {details} (阈值: {self.max_correlation})"
        
        # 通过检查
        if current_positions:
            # 计算与所有现有持仓的平均相关性
            correlations = []
            for existing_symbol in current_positions:
                if existing_symbol in self.correlation_matrix.columns:
                    corr = self.correlation_matrix.loc[new_symbol, existing_symbol]
                    correlations.append(corr)
            
            if correlations:
                avg_corr = np.mean(correlations)
                return True, f"相关性检查通过: 平均相关性 {avg_corr:.2f}"
        
        return True, "相关性检查通过"
    
    def get_correlation(self, symbol1: str, symbol2: str) -> Optional[float]:
        """
        获取两只股票之间的相关性
        
        Args:
            symbol1: 第一只股票代码
            symbol2: 第二只股票代码
            
        Returns:
            相关系数，如果无法计算则返回None
        """
        if self.correlation_matrix is None:
            return None
        
        if symbol1 not in self.correlation_matrix.columns:
            return None
        
        if symbol2 not in self.correlation_matrix.columns:
            return None
        
        return float(self.correlation_matrix.loc[symbol1, symbol2])
    
    def get_portfolio_correlation(self, symbols: List[str]) -> Dict:
        """
        获取投资组合的相关性统计
        
        Args:
            symbols: 投资组合中的股票代码列表
            
        Returns:
            相关性统计信息
        """
        if self.correlation_matrix is None or len(symbols) < 2:
            return {
                'average_correlation': None,
                'max_correlation': None,
                'min_correlation': None,
                'high_correlation_pairs': []
            }
        
        correlations = []
        high_corr_pairs = []
        
        for i, sym1 in enumerate(symbols):
            for j, sym2 in enumerate(symbols):
                if i < j and sym1 in self.correlation_matrix.columns and sym2 in self.correlation_matrix.columns:
                    corr = self.correlation_matrix.loc[sym1, sym2]
                    correlations.append(corr)
                    
                    if abs(corr) > self.max_correlation:
                        high_corr_pairs.append({
                            'symbol1': sym1,
                            'symbol2': sym2,
                            'correlation': corr
                        })
        
        if not correlations:
            return {
                'average_correlation': None,
                'max_correlation': None,
                'min_correlation': None,
                'high_correlation_pairs': []
            }
        
        return {
            'average_correlation': float(np.mean(correlations)),
            'max_correlation': float(np.max(correlations)),
            'min_correlation': float(np.min(correlations)),
            'high_correlation_pairs': high_corr_pairs
        }
    
    def suggest_diversification(self, current_positions: List[str], 
                                candidates: List[str]) -> List[Tuple[str, float]]:
        """
        建议多样化的股票
        
        找出与当前持仓相关性最低的候选股票
        
        Args:
            current_positions: 当前持仓的股票代码列表
            candidates: 候选股票代码列表
            
        Returns:
            排序后的候选股票列表 [(symbol, avg_correlation), ...]
        """
        if self.correlation_matrix is None or not current_positions:
            return [(s, 0.0) for s in candidates]
        
        diversification_scores = []
        
        for candidate in candidates:
            if candidate in self.correlation_matrix.columns:
                correlations = []
                for pos in current_positions:
                    if pos in self.correlation_matrix.columns:
                        corr = abs(self.correlation_matrix.loc[candidate, pos])
                        correlations.append(corr)
                
                if correlations:
                    avg_corr = np.mean(correlations)
                    diversification_scores.append((candidate, avg_corr))
                else:
                    diversification_scores.append((candidate, 0.0))
            else:
                diversification_scores.append((candidate, 0.0))
        
        # 按相关性从低到高排序（低相关性 = 高多样化）
        diversification_scores.sort(key=lambda x: x[1])
        
        return diversification_scores
    
    def is_matrix_stale(self) -> bool:
        """
        检查相关性矩阵是否过期
        
        Returns:
            是否过期
        """
        if self.last_update is None:
            return True
        
        time_since_update = datetime.now() - self.last_update
        return time_since_update.total_seconds() > self.update_interval_hours * 3600
    
    def get_summary(self) -> str:
        """
        获取相关性过滤器状态摘要
        
        Returns:
            格式化的摘要字符串
        """
        lines = ["📈 相关性过滤器状态:"]
        lines.append(f"   最大相关性阈值: {self.max_correlation}")
        lines.append(f"   回看天数: {self.lookback_days}")
        lines.append(f"   更新间隔: {self.update_interval_hours}小时")
        
        if self.correlation_matrix is not None:
            lines.append(f"   矩阵股票数: {len(self.symbols_in_matrix)}")
            lines.append(f"   股票列表: {', '.join(self.symbols_in_matrix)}")
            lines.append(f"   最后更新: {self.last_update.strftime('%Y-%m-%d %H:%M:%S') if self.last_update else 'N/A'}")
            lines.append(f"   是否过期: {'是' if self.is_matrix_stale() else '否'}")
            
            # 显示相关性矩阵摘要
            if len(self.symbols_in_matrix) > 1:
                avg_corr = self.correlation_matrix.values[np.triu_indices_from(
                    self.correlation_matrix.values, k=1)].mean()
                lines.append(f"   平均相关性: {avg_corr:.3f}")
        else:
            lines.append("   相关性矩阵: 未初始化")
        
        return "\n".join(lines)
    
    def get_matrix_as_dict(self) -> Optional[Dict]:
        """
        获取相关性矩阵的字典形式
        
        Returns:
            相关性矩阵字典
        """
        if self.correlation_matrix is None:
            return None
        
        return self.correlation_matrix.to_dict()
