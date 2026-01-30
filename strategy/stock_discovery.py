"""
股票发现模块
自动发现市场上合适买入的股票，进行观察并建仓买入

功能：
1. 市场扫描 - 扫描港股/美股热门股票
2. 技术筛选 - RSI超卖、MACD金叉、均线突破等
3. 基本面筛选 - 市值、成交量、波动率等
4. 观察列表管理 - 跟踪候选股票
5. 入场时机判断 - 满足条件时生成买入信号
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import numpy as np


class DiscoveryReason(Enum):
    """发现原因"""
    RSI_OVERSOLD = "RSI超卖"
    MACD_GOLDEN_CROSS = "MACD金叉"
    MA_BREAKOUT = "均线突破"
    VOLUME_SURGE = "放量突破"
    PRICE_REVERSAL = "价格反转"
    MOMENTUM_SHIFT = "动量转换"
    SUPPORT_BOUNCE = "支撑位反弹"


@dataclass
class CandidateStock:
    """候选股票"""
    symbol: str
    name: str
    market: str  # HK, US
    discovery_time: datetime
    discovery_reason: DiscoveryReason
    current_price: float
    entry_price: float  # 建议入场价
    stop_loss: float    # 止损价
    target_price: float # 目标价
    confidence: float   # 置信度 0-1
    volume_ratio: float # 成交量比率
    rsi: float = 0.0
    macd_signal: str = ""
    trend: str = ""  # up, down, sideways
    watch_start: datetime = field(default_factory=datetime.now)
    status: str = "watching"  # watching, ready, entered, expired
    notes: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'name': self.name,
            'market': self.market,
            'discovery_reason': self.discovery_reason.value,
            'current_price': self.current_price,
            'entry_price': self.entry_price,
            'stop_loss': self.stop_loss,
            'target_price': self.target_price,
            'confidence': self.confidence,
            'rsi': self.rsi,
            'status': self.status
        }


class StockDiscovery:
    """
    股票发现器
    
    工作流程：
    1. 定期扫描市场（港股/美股热门股票池）
    2. 应用技术筛选条件
    3. 将符合条件的股票加入观察列表
    4. 监控观察列表中的股票
    5. 当满足入场条件时生成买入信号
    """
    
    # 默认股票池
    DEFAULT_HK_POOL = [
        "700.HK", "9988.HK", "1299.HK", "388.HK", "941.HK",
        "3690.HK", "1810.HK", "2318.HK", "1398.HK", "939.HK",
        "5.HK", "1211.HK", "2020.HK", "9618.HK", "2269.HK",
        "1024.HK", "241.HK", "1833.HK", "6618.HK", "9999.HK",
    ]
    
    DEFAULT_US_POOL = [
        "AAPL.US", "GOOGL.US", "MSFT.US", "NVDA.US", "TSLA.US",
        "AMZN.US", "META.US", "AMD.US", "NFLX.US", "CRM.US",
        "COIN.US", "PLTR.US", "SQ.US", "SNOW.US", "DDOG.US",
        "SHOP.US", "UBER.US", "ABNB.US", "ZM.US", "DOCU.US",
    ]
    
    def __init__(self, quote_context, config, logger=None):
        """
        初始化股票发现器
        
        Args:
            quote_context: 行情上下文
            config: 配置对象
            logger: 日志记录器
        """
        self.quote_ctx = quote_context
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # 观察列表
        self.watch_list: Dict[str, CandidateStock] = {}
        
        # 历史数据缓存
        self.price_history: Dict[str, List[dict]] = {}
        
        # 筛选条件配置
        self.min_volume = config.get("discovery.min_volume", 1000000)
        self.max_rsi_oversold = config.get("discovery.rsi_oversold", 30)
        self.min_rsi_overbought = config.get("discovery.rsi_overbought", 70)
        self.min_confidence = config.get("discovery.min_confidence", 0.6)
        self.watch_expiry_hours = config.get("discovery.watch_expiry_hours", 48)
        self.max_watch_list_size = config.get("discovery.max_watch_list", 20)
        
        # 股票池
        self.hk_pool = config.get("discovery.hk_pool", self.DEFAULT_HK_POOL)
        self.us_pool = config.get("discovery.us_pool", self.DEFAULT_US_POOL)
        
        self.logger.info(f"股票发现器初始化完成 - 港股池: {len(self.hk_pool)}只, 美股池: {len(self.us_pool)}只")
    
    async def scan_market(self, market: str = "ALL") -> List[CandidateStock]:
        """
        扫描市场寻找候选股票
        
        Args:
            market: 市场类型 (HK, US, ALL)
            
        Returns:
            List[CandidateStock]: 发现的候选股票列表
        """
        candidates = []
        
        try:
            # 确定扫描的股票池
            if market == "HK":
                pool = self.hk_pool
            elif market == "US":
                pool = self.us_pool
            else:
                pool = self.hk_pool + self.us_pool
            
            self.logger.info(f"开始扫描市场，股票池: {len(pool)}只")
            
            # 批量获取行情
            quotes = await self._get_quotes(pool)
            
            if not quotes:
                self.logger.warning("无法获取行情数据")
                return candidates
            
            # 对每只股票进行分析
            for symbol, quote_data in quotes.items():
                try:
                    candidate = await self._analyze_stock(symbol, quote_data)
                    if candidate:
                        candidates.append(candidate)
                        self.logger.info(f"🎯 发现候选股票: {symbol} - {candidate.discovery_reason.value}, "
                                       f"置信度: {candidate.confidence:.2f}")
                except Exception as e:
                    self.logger.debug(f"分析 {symbol} 失败: {e}")
            
            self.logger.info(f"市场扫描完成，发现 {len(candidates)} 只候选股票")
            
        except Exception as e:
            self.logger.error(f"市场扫描失败: {e}")
        
        return candidates
    
    async def _get_quotes(self, symbols: List[str]) -> Dict[str, dict]:
        """获取股票行情"""
        result = {}
        
        try:
            # 分批获取，避免超时
            batch_size = 20
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i+batch_size]
                try:
                    quotes = self.quote_ctx.quote(batch)
                    for q in quotes:
                        result[q.symbol] = {
                            'last_done': float(q.last_done),
                            'open': float(q.open),
                            'high': float(q.high),
                            'low': float(q.low),
                            'volume': int(q.volume),
                            'turnover': float(q.turnover) if hasattr(q, 'turnover') else 0,
                            'prev_close': float(q.prev_close) if hasattr(q, 'prev_close') else float(q.last_done),
                        }
                except Exception as e:
                    self.logger.warning(f"获取批次行情失败: {e}")
                    
                await asyncio.sleep(0.1)  # 避免请求过快
                
        except Exception as e:
            self.logger.error(f"获取行情失败: {e}")
        
        return result
    
    async def _analyze_stock(self, symbol: str, quote: dict) -> Optional[CandidateStock]:
        """
        分析单只股票是否符合买入条件
        
        Args:
            symbol: 股票代码
            quote: 行情数据
            
        Returns:
            CandidateStock 或 None
        """
        try:
            # 获取历史数据用于技术分析
            history = await self._get_history(symbol)
            
            if not history or len(history) < 20:
                return None
            
            # 计算技术指标
            closes = [h['close'] for h in history[-60:]]
            volumes = [h['volume'] for h in history[-60:]]
            
            current_price = quote['last_done']
            current_volume = quote['volume']
            prev_close = quote['prev_close']
            
            # 计算指标
            rsi = self._calculate_rsi(closes)
            macd, signal, hist = self._calculate_macd(closes)
            sma_20 = np.mean(closes[-20:]) if len(closes) >= 20 else current_price
            sma_50 = np.mean(closes[-50:]) if len(closes) >= 50 else current_price
            avg_volume = np.mean(volumes[-20:]) if len(volumes) >= 20 else current_volume
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
            
            # 价格变动
            price_change = (current_price - prev_close) / prev_close if prev_close > 0 else 0
            
            # 判断趋势
            trend = "sideways"
            if sma_20 > sma_50 * 1.02:
                trend = "up"
            elif sma_20 < sma_50 * 0.98:
                trend = "down"
            
            # 筛选条件评估
            discovery_reason = None
            confidence = 0.0
            
            # 1. RSI 超卖
            if rsi < self.max_rsi_oversold:
                discovery_reason = DiscoveryReason.RSI_OVERSOLD
                confidence = 0.6 + (self.max_rsi_oversold - rsi) / 100
            
            # 2. MACD 金叉
            elif len(hist) >= 2 and hist[-1] > 0 and hist[-2] <= 0:
                discovery_reason = DiscoveryReason.MACD_GOLDEN_CROSS
                confidence = 0.65 + abs(hist[-1]) / 10
            
            # 3. 均线突破
            elif current_price > sma_20 and closes[-2] < sma_20:
                discovery_reason = DiscoveryReason.MA_BREAKOUT
                confidence = 0.6 + (current_price - sma_20) / sma_20
            
            # 4. 放量突破
            elif volume_ratio > 2.0 and price_change > 0.02:
                discovery_reason = DiscoveryReason.VOLUME_SURGE
                confidence = 0.6 + min(volume_ratio / 10, 0.3)
            
            # 5. 价格反转（从下跌转为上涨）
            elif price_change > 0.03 and rsi < 40:
                discovery_reason = DiscoveryReason.PRICE_REVERSAL
                confidence = 0.55 + price_change
            
            # 6. 支撑位反弹
            elif current_price > quote['low'] * 1.02 and quote['low'] < sma_20 * 0.98:
                discovery_reason = DiscoveryReason.SUPPORT_BOUNCE
                confidence = 0.5 + (current_price - quote['low']) / quote['low']
            
            # 没有满足条件
            if not discovery_reason:
                return None
            
            # 置信度调整
            confidence = min(confidence, 1.0)
            
            # 成交量过滤
            if current_volume < self.min_volume * 0.1:  # 成交量过低
                confidence *= 0.7
            
            if confidence < self.min_confidence:
                return None
            
            # 计算入场价、止损和目标价
            entry_price = current_price * 1.005  # 略高于当前价
            stop_loss = min(quote['low'] * 0.98, current_price * 0.95)  # 5%止损
            target_price = current_price * 1.10  # 10%目标
            
            # 确定市场
            market = "HK" if symbol.endswith(".HK") else "US"
            
            # 获取股票名称
            name = self._get_stock_name(symbol)
            
            return CandidateStock(
                symbol=symbol,
                name=name,
                market=market,
                discovery_time=datetime.now(),
                discovery_reason=discovery_reason,
                current_price=current_price,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_price=target_price,
                confidence=confidence,
                volume_ratio=volume_ratio,
                rsi=rsi,
                macd_signal="bullish" if hist[-1] > 0 else "bearish",
                trend=trend,
            )
            
        except Exception as e:
            self.logger.debug(f"分析 {symbol} 时出错: {e}")
            return None
    
    async def _get_history(self, symbol: str, days: int = 60) -> List[dict]:
        """获取历史数据"""
        try:
            # 检查缓存
            cache_key = symbol
            if cache_key in self.price_history:
                cached = self.price_history[cache_key]
                # 检查缓存是否过期（1小时）
                if cached and 'timestamp' in cached[0]:
                    cache_time = cached[0].get('timestamp')
                    if isinstance(cache_time, datetime):
                        if (datetime.now() - cache_time).total_seconds() < 3600:
                            return cached
            
            # 获取K线数据
            from longport.openapi import Period, AdjustType
            from datetime import date
            
            end_date = date.today()
            start_date = end_date - timedelta(days=days + 10)
            
            candlesticks = self.quote_ctx.candlesticks(
                symbol,
                Period.Day,
                days,
                AdjustType.ForwardAdjust
            )
            
            history = []
            for candle in candlesticks:
                history.append({
                    'timestamp': datetime.now(),  # 缓存时间戳
                    'open': float(candle.open),
                    'high': float(candle.high),
                    'low': float(candle.low),
                    'close': float(candle.close),
                    'volume': int(candle.volume),
                })
            
            # 更新缓存
            self.price_history[cache_key] = history
            
            return history
            
        except Exception as e:
            self.logger.debug(f"获取 {symbol} 历史数据失败: {e}")
            return []
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """计算RSI"""
        if len(prices) < period + 1:
            return 50.0
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_macd(self, prices: List[float]) -> Tuple[float, float, List[float]]:
        """计算MACD"""
        if len(prices) < 26:
            return 0.0, 0.0, [0.0]
        
        prices_array = np.array(prices)
        
        # EMA计算
        def ema(data, period):
            alpha = 2 / (period + 1)
            result = np.zeros_like(data)
            result[0] = data[0]
            for i in range(1, len(data)):
                result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
            return result
        
        ema_12 = ema(prices_array, 12)
        ema_26 = ema(prices_array, 26)
        
        macd_line = ema_12 - ema_26
        signal_line = ema(macd_line, 9)
        histogram = macd_line - signal_line
        
        return float(macd_line[-1]), float(signal_line[-1]), histogram.tolist()
    
    def _get_stock_name(self, symbol: str) -> str:
        """获取股票名称"""
        # 常见股票名称映射
        name_map = {
            "700.HK": "腾讯控股",
            "9988.HK": "阿里巴巴",
            "1299.HK": "友邦保险",
            "388.HK": "香港交易所",
            "941.HK": "中国移动",
            "AAPL.US": "Apple",
            "GOOGL.US": "Alphabet",
            "MSFT.US": "Microsoft",
            "NVDA.US": "NVIDIA",
            "TSLA.US": "Tesla",
            "AMZN.US": "Amazon",
            "META.US": "Meta",
            "AMD.US": "AMD",
        }
        return name_map.get(symbol, symbol.split(".")[0])
    
    def add_to_watch_list(self, candidate: CandidateStock) -> bool:
        """
        添加股票到观察列表
        
        Args:
            candidate: 候选股票
            
        Returns:
            bool: 是否成功添加
        """
        try:
            # 检查是否已在观察列表
            if candidate.symbol in self.watch_list:
                # 更新现有记录
                existing = self.watch_list[candidate.symbol]
                if candidate.confidence > existing.confidence:
                    self.watch_list[candidate.symbol] = candidate
                    self.logger.info(f"更新观察列表: {candidate.symbol}, 置信度: {candidate.confidence:.2f}")
                return True
            
            # 检查观察列表大小限制
            if len(self.watch_list) >= self.max_watch_list_size:
                # 移除最旧或置信度最低的
                oldest_symbol = min(self.watch_list.keys(), 
                                   key=lambda s: self.watch_list[s].confidence)
                del self.watch_list[oldest_symbol]
                self.logger.info(f"观察列表已满，移除: {oldest_symbol}")
            
            # 添加到观察列表
            self.watch_list[candidate.symbol] = candidate
            self.logger.info(f"添加到观察列表: {candidate.symbol} - {candidate.discovery_reason.value}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"添加到观察列表失败: {e}")
            return False
    
    async def update_watch_list(self) -> List[CandidateStock]:
        """
        更新观察列表，检查入场机会
        
        Returns:
            List[CandidateStock]: 已准备好入场的股票列表
        """
        ready_to_enter = []
        expired = []
        
        try:
            symbols = list(self.watch_list.keys())
            if not symbols:
                return ready_to_enter
            
            # 获取最新行情
            quotes = await self._get_quotes(symbols)
            
            for symbol, candidate in list(self.watch_list.items()):
                try:
                    # 检查是否过期
                    watch_duration = (datetime.now() - candidate.watch_start).total_seconds() / 3600
                    if watch_duration > self.watch_expiry_hours:
                        expired.append(symbol)
                        continue
                    
                    if symbol not in quotes:
                        continue
                    
                    quote = quotes[symbol]
                    current_price = quote['last_done']
                    
                    # 更新当前价格
                    candidate.current_price = current_price
                    
                    # 检查是否达到止损价
                    if current_price <= candidate.stop_loss:
                        candidate.status = "expired"
                        candidate.notes.append(f"触发止损: {current_price:.2f} <= {candidate.stop_loss:.2f}")
                        expired.append(symbol)
                        continue
                    
                    # 检查是否满足入场条件
                    if self._check_entry_condition(candidate, quote):
                        candidate.status = "ready"
                        ready_to_enter.append(candidate)
                        self.logger.info(f"🎯 入场机会: {symbol} @ {current_price:.2f}")
                    
                except Exception as e:
                    self.logger.debug(f"更新 {symbol} 失败: {e}")
            
            # 清理过期的观察目标
            for symbol in expired:
                if symbol in self.watch_list:
                    del self.watch_list[symbol]
                    self.logger.info(f"移除过期观察目标: {symbol}")
            
        except Exception as e:
            self.logger.error(f"更新观察列表失败: {e}")
        
        return ready_to_enter
    
    def _check_entry_condition(self, candidate: CandidateStock, quote: dict) -> bool:
        """
        检查是否满足入场条件
        
        Args:
            candidate: 候选股票
            quote: 最新行情
            
        Returns:
            bool: 是否满足入场条件
        """
        current_price = quote['last_done']
        
        # 条件1: 价格接近或低于建议入场价
        if current_price <= candidate.entry_price:
            return True
        
        # 条件2: 价格突破并回调
        if candidate.discovery_reason == DiscoveryReason.MA_BREAKOUT:
            if current_price < candidate.current_price * 0.99:  # 小幅回调
                return True
        
        # 条件3: RSI从超卖区域反弹
        if candidate.discovery_reason == DiscoveryReason.RSI_OVERSOLD:
            if current_price > quote.get('prev_close', current_price) * 1.01:
                return True
        
        return False
    
    def get_watch_list_summary(self) -> str:
        """获取观察列表摘要"""
        if not self.watch_list:
            return "观察列表为空"
        
        lines = ["📋 观察列表:"]
        for symbol, candidate in self.watch_list.items():
            status_icon = {
                "watching": "👀",
                "ready": "🎯",
                "entered": "✅",
                "expired": "❌"
            }.get(candidate.status, "❓")
            
            lines.append(
                f"  {status_icon} {symbol} ({candidate.name}): "
                f"现价={candidate.current_price:.2f}, "
                f"入场={candidate.entry_price:.2f}, "
                f"止损={candidate.stop_loss:.2f}, "
                f"目标={candidate.target_price:.2f}, "
                f"置信度={candidate.confidence:.2f}"
            )
        
        return "\n".join(lines)
    
    async def run_discovery_cycle(self) -> List[CandidateStock]:
        """
        运行一个完整的发现周期
        
        Returns:
            List[CandidateStock]: 准备入场的股票
        """
        # 1. 扫描市场
        new_candidates = await self.scan_market()
        
        # 2. 添加到观察列表
        for candidate in new_candidates:
            self.add_to_watch_list(candidate)
        
        # 3. 更新观察列表并检查入场机会
        ready_stocks = await self.update_watch_list()
        
        # 4. 记录状态
        self.logger.info(f"发现周期完成: 新发现 {len(new_candidates)} 只, "
                        f"观察中 {len(self.watch_list)} 只, "
                        f"准备入场 {len(ready_stocks)} 只")
        
        return ready_stocks
