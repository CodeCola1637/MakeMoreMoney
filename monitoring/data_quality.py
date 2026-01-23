"""
数据质量监控模块
负责检测和报告数据质量问题
"""

import numpy as np
import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict


class QualityLevel(Enum):
    """数据质量等级"""
    GOOD = "good"
    WARNING = "warning"
    POOR = "poor"
    CRITICAL = "critical"


@dataclass
class QualityIssue:
    """数据质量问题"""
    issue_type: str
    severity: QualityLevel
    description: str
    affected_rows: int = 0
    affected_columns: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class QualityReport:
    """数据质量报告"""
    symbol: str
    timestamp: datetime
    overall_level: QualityLevel
    completeness_score: float  # 0-1
    consistency_score: float   # 0-1
    freshness_score: float     # 0-1
    accuracy_score: float      # 0-1
    issues: List[QualityIssue] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


class DataQualityMonitor:
    """
    数据质量监控器
    
    功能：
    1. 数据完整性检查
    2. 数据一致性检查
    3. 数据新鲜度检查
    4. 数据准确性检查
    5. 异常值检测
    """
    
    def __init__(self, config=None, logger=None):
        """
        初始化数据质量监控器
        
        Args:
            config: 配置对象
            logger: 日志记录器
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # 质量报告历史
        self.reports: Dict[str, List[QualityReport]] = defaultdict(list)
        self.max_reports_per_symbol = 100
        
        # 阈值配置
        self.thresholds = {
            'missing_ratio_warning': 0.05,    # 5%缺失警告
            'missing_ratio_critical': 0.20,   # 20%缺失严重
            'stale_minutes_warning': 30,      # 30分钟数据过旧警告
            'stale_minutes_critical': 60,     # 60分钟数据过旧严重
            'price_change_warning': 0.10,     # 10%价格变化警告
            'price_change_critical': 0.20,    # 20%价格变化严重
            'volume_spike_threshold': 5.0,    # 成交量异常倍数
            'zscore_threshold': 3.0           # Z-Score异常阈值
        }
        
        self.logger.info("数据质量监控器初始化完成")
    
    def check_dataframe(self, df: pd.DataFrame, symbol: str = "unknown") -> QualityReport:
        """
        检查DataFrame数据质量
        
        Args:
            df: 数据DataFrame
            symbol: 股票代码
            
        Returns:
            QualityReport
        """
        issues = []
        metrics = {}
        
        # 1. 完整性检查
        completeness_score, completeness_issues = self._check_completeness(df)
        issues.extend(completeness_issues)
        metrics['completeness'] = {
            'score': completeness_score,
            'missing_counts': df.isna().sum().to_dict()
        }
        
        # 2. 一致性检查
        consistency_score, consistency_issues = self._check_consistency(df)
        issues.extend(consistency_issues)
        metrics['consistency'] = {
            'score': consistency_score
        }
        
        # 3. 新鲜度检查
        freshness_score, freshness_issues = self._check_freshness(df)
        issues.extend(freshness_issues)
        metrics['freshness'] = {
            'score': freshness_score
        }
        
        # 4. 准确性检查 (异常值)
        accuracy_score, accuracy_issues = self._check_accuracy(df)
        issues.extend(accuracy_issues)
        metrics['accuracy'] = {
            'score': accuracy_score
        }
        
        # 计算综合得分
        overall_score = (completeness_score + consistency_score + freshness_score + accuracy_score) / 4
        
        if overall_score >= 0.9:
            overall_level = QualityLevel.GOOD
        elif overall_score >= 0.7:
            overall_level = QualityLevel.WARNING
        elif overall_score >= 0.5:
            overall_level = QualityLevel.POOR
        else:
            overall_level = QualityLevel.CRITICAL
        
        report = QualityReport(
            symbol=symbol,
            timestamp=datetime.now(),
            overall_level=overall_level,
            completeness_score=completeness_score,
            consistency_score=consistency_score,
            freshness_score=freshness_score,
            accuracy_score=accuracy_score,
            issues=issues,
            metrics=metrics
        )
        
        # 保存报告
        self._save_report(symbol, report)
        
        return report
    
    def _check_completeness(self, df: pd.DataFrame) -> Tuple[float, List[QualityIssue]]:
        """检查数据完整性"""
        issues = []
        
        if df.empty:
            issues.append(QualityIssue(
                issue_type="empty_data",
                severity=QualityLevel.CRITICAL,
                description="数据为空"
            ))
            return 0.0, issues
        
        total_cells = df.size
        missing_cells = df.isna().sum().sum()
        missing_ratio = missing_cells / total_cells if total_cells > 0 else 0
        
        # 检查各列缺失情况
        for col in df.columns:
            col_missing = df[col].isna().sum()
            col_ratio = col_missing / len(df) if len(df) > 0 else 0
            
            if col_ratio > self.thresholds['missing_ratio_critical']:
                issues.append(QualityIssue(
                    issue_type="high_missing",
                    severity=QualityLevel.CRITICAL,
                    description=f"列 {col} 缺失率过高: {col_ratio:.1%}",
                    affected_rows=col_missing,
                    affected_columns=[col]
                ))
            elif col_ratio > self.thresholds['missing_ratio_warning']:
                issues.append(QualityIssue(
                    issue_type="missing_values",
                    severity=QualityLevel.WARNING,
                    description=f"列 {col} 存在缺失: {col_ratio:.1%}",
                    affected_rows=col_missing,
                    affected_columns=[col]
                ))
        
        score = 1.0 - missing_ratio
        return max(0, min(1, score)), issues
    
    def _check_consistency(self, df: pd.DataFrame) -> Tuple[float, List[QualityIssue]]:
        """检查数据一致性"""
        issues = []
        score = 1.0
        
        # 检查OHLC一致性
        if all(col in df.columns for col in ['open', 'high', 'low', 'close']):
            # high >= low
            invalid_hl = df[df['high'] < df['low']]
            if len(invalid_hl) > 0:
                issues.append(QualityIssue(
                    issue_type="ohlc_inconsistent",
                    severity=QualityLevel.CRITICAL,
                    description=f"存在 high < low 的记录: {len(invalid_hl)}行",
                    affected_rows=len(invalid_hl),
                    affected_columns=['high', 'low']
                ))
                score -= 0.2
            
            # high >= open, close
            invalid_high = df[(df['high'] < df['open']) | (df['high'] < df['close'])]
            if len(invalid_high) > 0:
                issues.append(QualityIssue(
                    issue_type="ohlc_inconsistent",
                    severity=QualityLevel.WARNING,
                    description=f"存在 high 不是最高价的记录: {len(invalid_high)}行",
                    affected_rows=len(invalid_high),
                    affected_columns=['high', 'open', 'close']
                ))
                score -= 0.1
            
            # low <= open, close
            invalid_low = df[(df['low'] > df['open']) | (df['low'] > df['close'])]
            if len(invalid_low) > 0:
                issues.append(QualityIssue(
                    issue_type="ohlc_inconsistent",
                    severity=QualityLevel.WARNING,
                    description=f"存在 low 不是最低价的记录: {len(invalid_low)}行",
                    affected_rows=len(invalid_low),
                    affected_columns=['low', 'open', 'close']
                ))
                score -= 0.1
        
        # 检查成交量
        if 'volume' in df.columns:
            negative_volume = df[df['volume'] < 0]
            if len(negative_volume) > 0:
                issues.append(QualityIssue(
                    issue_type="negative_volume",
                    severity=QualityLevel.CRITICAL,
                    description=f"存在负成交量: {len(negative_volume)}行",
                    affected_rows=len(negative_volume),
                    affected_columns=['volume']
                ))
                score -= 0.2
        
        return max(0, min(1, score)), issues
    
    def _check_freshness(self, df: pd.DataFrame) -> Tuple[float, List[QualityIssue]]:
        """检查数据新鲜度"""
        issues = []
        score = 1.0
        
        # 查找时间列
        time_col = None
        for col in ['timestamp', 'date', 'datetime', 'time']:
            if col in df.columns:
                time_col = col
                break
        
        if time_col is None:
            # 尝试使用索引
            if hasattr(df.index, 'max') and hasattr(df.index[0], 'date'):
                latest_time = df.index.max()
            else:
                return score, issues
        else:
            try:
                latest_time = pd.to_datetime(df[time_col]).max()
            except Exception:
                return score, issues
        
        # 计算数据延迟
        now = datetime.now()
        if hasattr(latest_time, 'to_pydatetime'):
            latest_time = latest_time.to_pydatetime()
        
        # 处理时区问题
        if hasattr(latest_time, 'tzinfo') and latest_time.tzinfo is not None:
            latest_time = latest_time.replace(tzinfo=None)
        
        delay_minutes = (now - latest_time).total_seconds() / 60
        
        if delay_minutes > self.thresholds['stale_minutes_critical']:
            issues.append(QualityIssue(
                issue_type="stale_data",
                severity=QualityLevel.CRITICAL,
                description=f"数据严重过期: {delay_minutes:.0f}分钟前"
            ))
            score = 0.3
        elif delay_minutes > self.thresholds['stale_minutes_warning']:
            issues.append(QualityIssue(
                issue_type="stale_data",
                severity=QualityLevel.WARNING,
                description=f"数据较旧: {delay_minutes:.0f}分钟前"
            ))
            score = 0.7
        
        return score, issues
    
    def _check_accuracy(self, df: pd.DataFrame) -> Tuple[float, List[QualityIssue]]:
        """检查数据准确性（异常值检测）"""
        issues = []
        score = 1.0
        
        # 检查价格列的异常值
        price_cols = ['open', 'high', 'low', 'close']
        for col in price_cols:
            if col not in df.columns:
                continue
            
            series = df[col].dropna()
            if len(series) < 10:
                continue
            
            # Z-Score检测
            mean = series.mean()
            std = series.std()
            if std > 0:
                zscore = abs(series - mean) / std
                outliers = zscore[zscore > self.thresholds['zscore_threshold']]
                
                if len(outliers) > 0:
                    issues.append(QualityIssue(
                        issue_type="price_outlier",
                        severity=QualityLevel.WARNING,
                        description=f"列 {col} 存在异常值: {len(outliers)}个",
                        affected_rows=len(outliers),
                        affected_columns=[col]
                    ))
                    score -= 0.05 * len(outliers) / len(series)
            
            # 价格剧烈变化检测
            pct_change = series.pct_change().abs()
            large_changes = pct_change[pct_change > self.thresholds['price_change_critical']]
            if len(large_changes) > 0:
                issues.append(QualityIssue(
                    issue_type="price_spike",
                    severity=QualityLevel.WARNING,
                    description=f"列 {col} 存在剧烈价格变化: {len(large_changes)}次",
                    affected_rows=len(large_changes),
                    affected_columns=[col]
                ))
                score -= 0.1
        
        # 检查成交量异常
        if 'volume' in df.columns:
            volume = df['volume'].dropna()
            if len(volume) > 10:
                mean_volume = volume.mean()
                if mean_volume > 0:
                    volume_ratio = volume / mean_volume
                    spikes = volume_ratio[volume_ratio > self.thresholds['volume_spike_threshold']]
                    if len(spikes) > 0:
                        issues.append(QualityIssue(
                            issue_type="volume_spike",
                            severity=QualityLevel.WARNING,
                            description=f"存在成交量异常飙升: {len(spikes)}次",
                            affected_rows=len(spikes),
                            affected_columns=['volume']
                        ))
        
        return max(0, min(1, score)), issues
    
    def _save_report(self, symbol: str, report: QualityReport) -> None:
        """保存质量报告"""
        self.reports[symbol].append(report)
        
        # 限制报告数量
        if len(self.reports[symbol]) > self.max_reports_per_symbol:
            self.reports[symbol] = self.reports[symbol][-self.max_reports_per_symbol:]
    
    def get_latest_report(self, symbol: str) -> Optional[QualityReport]:
        """获取最新的质量报告"""
        if symbol in self.reports and self.reports[symbol]:
            return self.reports[symbol][-1]
        return None
    
    def get_quality_trend(self, symbol: str, lookback: int = 10) -> Dict[str, List[float]]:
        """获取质量趋势"""
        if symbol not in self.reports:
            return {}
        
        recent = self.reports[symbol][-lookback:]
        
        return {
            'completeness': [r.completeness_score for r in recent],
            'consistency': [r.consistency_score for r in recent],
            'freshness': [r.freshness_score for r in recent],
            'accuracy': [r.accuracy_score for r in recent],
            'timestamps': [r.timestamp.isoformat() for r in recent]
        }
    
    def get_all_symbols_summary(self) -> Dict[str, Dict]:
        """获取所有股票的质量摘要"""
        summary = {}
        for symbol in self.reports.keys():
            latest = self.get_latest_report(symbol)
            if latest:
                summary[symbol] = {
                    'level': latest.overall_level.value,
                    'completeness': latest.completeness_score,
                    'consistency': latest.consistency_score,
                    'freshness': latest.freshness_score,
                    'accuracy': latest.accuracy_score,
                    'issues_count': len(latest.issues),
                    'last_check': latest.timestamp.isoformat()
                }
        return summary
    
    def get_summary(self) -> str:
        """获取数据质量监控摘要"""
        level_icons = {
            QualityLevel.GOOD: '✅',
            QualityLevel.WARNING: '⚠️',
            QualityLevel.POOR: '🔶',
            QualityLevel.CRITICAL: '❌'
        }
        
        lines = ["📊 数据质量监控状态:"]
        lines.append(f"   监控股票数: {len(self.reports)}")
        
        if self.reports:
            # 统计各等级数量
            level_counts = defaultdict(int)
            total_issues = 0
            
            for symbol in self.reports:
                latest = self.get_latest_report(symbol)
                if latest:
                    level_counts[latest.overall_level] += 1
                    total_issues += len(latest.issues)
            
            lines.append(f"   总问题数: {total_issues}")
            lines.append("\n   质量分布:")
            for level in [QualityLevel.GOOD, QualityLevel.WARNING, QualityLevel.POOR, QualityLevel.CRITICAL]:
                count = level_counts[level]
                if count > 0:
                    icon = level_icons.get(level, '❓')
                    lines.append(f"      {icon} {level.value}: {count}个股票")
            
            # 显示有问题的股票
            problem_symbols = []
            for symbol in self.reports:
                latest = self.get_latest_report(symbol)
                if latest and latest.overall_level in [QualityLevel.POOR, QualityLevel.CRITICAL]:
                    problem_symbols.append(symbol)
            
            if problem_symbols:
                lines.append(f"\n   ⚠️ 需关注: {', '.join(problem_symbols)}")
        
        return "\n".join(lines)


def create_data_quality_monitor(config=None, logger=None) -> DataQualityMonitor:
    """
    工厂函数：创建数据质量监控器
    
    Args:
        config: 配置对象
        logger: 日志记录器
        
    Returns:
        DataQualityMonitor 实例
    """
    return DataQualityMonitor(config, logger)
