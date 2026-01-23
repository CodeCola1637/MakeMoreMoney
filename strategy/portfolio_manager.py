"""
投资组合管理器
负责智能仓位管理、动态配比和风险控制
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

from utils import ConfigLoader, setup_logger

# 延迟导入以避免循环依赖
def _get_correlation_filter():
    from strategy.correlation_filter import CorrelationFilter
    return CorrelationFilter


class AllocationStrategy(Enum):
    """配置策略类型"""
    EQUAL_WEIGHT = "equal_weight"  # 等权重配置
    MARKET_CAP_WEIGHT = "market_cap_weight"  # 市值加权
    SIGNAL_STRENGTH_WEIGHT = "signal_strength_weight"  # 信号强度加权
    RISK_PARITY = "risk_parity"  # 风险平价
    MOMENTUM_WEIGHT = "momentum_weight"  # 动量加权


@dataclass
class PositionTarget:
    """目标仓位"""
    symbol: str
    target_weight: float  # 目标权重 (0-1)
    target_value: float   # 目标金额
    target_quantity: int  # 目标股数
    current_quantity: int = 0  # 当前股数
    current_value: float = 0.0  # 当前市值
    deviation: float = 0.0      # 偏离度
    action: str = "HOLD"        # 建议动作: BUY/SELL/HOLD
    priority: int = 0           # 调整优先级


@dataclass
class PortfolioStatus:
    """投资组合状态"""
    total_value: float          # 总市值
    cash_available: float       # 可用现金
    total_equity: float         # 总权益
    positions: Dict[str, PositionTarget]  # 持仓目标
    allocation_deviation: float # 配置偏离度
    rebalance_needed: bool      # 是否需要再平衡
    last_update: datetime       # 最后更新时间


class PortfolioManager:
    """投资组合管理器"""
    
    def __init__(self, config: ConfigLoader, order_manager, realtime_mgr):
        """
        初始化投资组合管理器
        
        Args:
            config: 配置加载器
            order_manager: 订单管理器  
            realtime_mgr: 实时数据管理器
        """
        self.config = config
        self.order_manager = order_manager
        self.realtime_mgr = realtime_mgr
        
        # 设置日志
        self.logger = setup_logger(
            "portfolio_manager",
            self.config.get("logging.level", "INFO"),
            self.config.get("logging.file")
        )
        
        # 投资组合配置
        self.allocation_strategy = AllocationStrategy(
            self.config.get("portfolio.allocation_strategy", "signal_strength_weight")
        )
        
        # 风险控制参数
        self.max_position_weight = float(self.config.get("portfolio.max_position_weight", 0.3))  # 单个仓位最大权重30%
        self.min_position_weight = float(self.config.get("portfolio.min_position_weight", 0.05)) # 单个仓位最小权重5%
        self.rebalance_threshold = float(self.config.get("portfolio.rebalance_threshold", 0.1))  # 再平衡阈值10%
        self.cash_reserve_ratio = float(self.config.get("portfolio.cash_reserve_ratio", 0.1))    # 现金储备比例10%
        
        # 动态调整参数
        self.rebalance_frequency = self.config.get("portfolio.rebalance_frequency", 3600)  # 再平衡频率(秒)
        self.signal_weight_factor = float(self.config.get("portfolio.signal_weight_factor", 2.0))  # 信号权重因子
        
        # 投资组合状态
        self.portfolio_status: Optional[PortfolioStatus] = None
        self.target_symbols: List[str] = []
        self.last_rebalance_time = datetime.now()
        
        # 价格缓存
        self.price_cache: Dict[str, float] = {}
        self.price_update_time: Dict[str, datetime] = {}
        
        # 🔧 相关性过滤器 - 用于风险分散检查
        self.correlation_filter = None  # 延迟初始化
        self._correlation_filter_initialized = False
        
    async def initialize(self, symbols: List[str], historical_loader=None):
        """
        初始化投资组合
        
        Args:
            symbols: 目标股票列表
            historical_loader: 历史数据加载器（可选，用于初始化相关性过滤器）
        """
        try:
            self.logger.info(f"初始化投资组合管理器，目标股票: {symbols}")
            self.target_symbols = symbols
            
            # 🔧 初始化相关性过滤器
            if historical_loader and not self._correlation_filter_initialized:
                try:
                    CorrelationFilter = _get_correlation_filter()
                    self.correlation_filter = CorrelationFilter(self.config, historical_loader, self.logger)
                    self._correlation_filter_initialized = True
                    self.logger.info("✅ 相关性过滤器初始化完成")
                    
                    # 异步更新相关性矩阵
                    asyncio.create_task(self._update_correlation_matrix())
                except Exception as e:
                    self.logger.warning(f"初始化相关性过滤器失败: {e}")
            
            # 获取初始投资组合状态
            await self.update_portfolio_status()
            
            self.logger.info("投资组合管理器初始化完成")
            return True
            
        except Exception as e:
            self.logger.error(f"初始化投资组合管理器失败: {e}")
            return False
    
    async def _update_correlation_matrix(self):
        """更新相关性矩阵"""
        if self.correlation_filter and self.target_symbols:
            try:
                await self.correlation_filter.update_correlation_matrix(self.target_symbols)
                self.logger.info("相关性矩阵更新完成")
            except Exception as e:
                self.logger.error(f"更新相关性矩阵失败: {e}")
    
    async def update_portfolio_status(self) -> PortfolioStatus:
        """更新投资组合状态"""
        try:
            self.logger.debug("更新投资组合状态...")
            
            # 获取账户余额和持仓
            account_balance = self.order_manager.get_account_balance()
            positions = self.order_manager.get_positions()
            
            # 获取实时价格
            await self._update_prices()
            
            # 计算投资组合价值
            total_value = 0.0
            current_positions = {}
            
            # 处理当前持仓
            for position in positions:
                symbol = position.symbol
                quantity = float(position.quantity)
                current_price = self.price_cache.get(symbol, float(position.cost_price))
                current_value = quantity * current_price
                
                current_positions[symbol] = PositionTarget(
                    symbol=symbol,
                    target_weight=0.0,  # 稍后计算
                    target_value=0.0,   # 稍后计算
                    target_quantity=0,  # 稍后计算
                    current_quantity=int(quantity),
                    current_value=current_value
                )
                
                total_value += current_value
                self.logger.debug(f"持仓: {symbol}, 数量: {quantity}, 价格: {current_price}, 市值: {current_value}")
            
            # 添加目标股票（如果还没有持仓）
            for symbol in self.target_symbols:
                if symbol not in current_positions:
                    current_positions[symbol] = PositionTarget(
                        symbol=symbol,
                        target_weight=0.0,
                        target_value=0.0,
                        target_quantity=0,
                        current_quantity=0,
                        current_value=0.0
                    )
            
            # 计算目标配置
            await self._calculate_target_allocation(current_positions, account_balance, total_value)
            
            # 创建投资组合状态
            self.portfolio_status = PortfolioStatus(
                total_value=total_value,
                cash_available=float(account_balance) if account_balance > 0 else 0.0,
                total_equity=total_value + max(0.0, float(account_balance)),
                positions=current_positions,
                allocation_deviation=self._calculate_deviation(current_positions),
                rebalance_needed=self._check_rebalance_needed(current_positions),
                last_update=datetime.now()
            )
            
            self.logger.info(f"投资组合状态更新: 总市值={total_value:.2f}, 可用现金={self.portfolio_status.cash_available:.2f}, "
                           f"配置偏离度={self.portfolio_status.allocation_deviation:.2%}, 需要再平衡={self.portfolio_status.rebalance_needed}")
            
            return self.portfolio_status
            
        except Exception as e:
            self.logger.error(f"更新投资组合状态失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            raise
    
    async def _update_prices(self):
        """更新价格缓存"""
        try:
            for symbol in self.target_symbols:
                # 检查价格是否需要更新（5分钟内的价格认为有效）
                if (symbol in self.price_update_time and 
                    datetime.now() - self.price_update_time[symbol] < timedelta(minutes=5)):
                    continue
                
                # 获取实时价格
                try:
                    quotes = await self.realtime_mgr.get_quote([symbol])
                    if quotes and symbol in quotes:
                        price = float(quotes[symbol].last_done)
                        self.price_cache[symbol] = price
                        self.price_update_time[symbol] = datetime.now()
                        self.logger.debug(f"更新价格: {symbol} = {price}")
                except Exception as e:
                    self.logger.warning(f"获取{symbol}实时价格失败: {e}")
                    
        except Exception as e:
            self.logger.error(f"更新价格缓存失败: {e}")
    
    async def _calculate_target_allocation(self, positions: Dict[str, PositionTarget], 
                                         cash_available: float, total_value: float):
        """计算目标配置"""
        try:
            total_equity = total_value + max(0.0, cash_available)
            available_for_investment = total_equity * (1 - self.cash_reserve_ratio)
            
            self.logger.debug(f"总权益: {total_equity}, 可投资金额: {available_for_investment}")
            
            if self.allocation_strategy == AllocationStrategy.EQUAL_WEIGHT:
                # 等权重分配
                target_weight = 1.0 / len(self.target_symbols)
                for symbol in self.target_symbols:
                    if symbol in positions:
                        positions[symbol].target_weight = target_weight
                        positions[symbol].target_value = available_for_investment * target_weight
                        
            elif self.allocation_strategy == AllocationStrategy.SIGNAL_STRENGTH_WEIGHT:
                # 基于信号强度的权重分配
                await self._calculate_signal_based_weights(positions, available_for_investment)
                
            else:
                # 默认等权重
                target_weight = 1.0 / len(self.target_symbols)
                for symbol in self.target_symbols:
                    if symbol in positions:
                        positions[symbol].target_weight = target_weight
                        positions[symbol].target_value = available_for_investment * target_weight
            
            # 计算目标股数和调整建议
            for symbol, position in positions.items():
                if symbol in self.price_cache and self.price_cache[symbol] > 0:
                    price = self.price_cache[symbol]
                    position.target_quantity = int(position.target_value / price)
                    position.deviation = abs(position.current_value - position.target_value) / max(position.target_value, 1.0)
                    
                    # 确定调整动作
                    quantity_diff = position.target_quantity - position.current_quantity
                    if abs(quantity_diff) >= 1:  # 至少1股的差异才调整
                        if quantity_diff > 0:
                            position.action = "BUY"
                            position.priority = int(position.deviation * 100)
                        else:
                            position.action = "SELL"
                            position.priority = int(position.deviation * 100)
                    else:
                        position.action = "HOLD"
                        position.priority = 0
                        
                    self.logger.debug(f"目标配置 {symbol}: 权重={position.target_weight:.2%}, "
                                    f"目标市值={position.target_value:.2f}, 目标股数={position.target_quantity}, "
                                    f"当前股数={position.current_quantity}, 动作={position.action}")
                        
        except Exception as e:
            self.logger.error(f"计算目标配置失败: {e}")
            raise
    
    async def _calculate_signal_based_weights(self, positions: Dict[str, PositionTarget], 
                                            available_for_investment: float):
        """基于信号强度计算权重"""
        try:
            # 这里可以集成信号生成器的信号强度
            # 临时使用等权重，后续可以根据实际信号强度调整
            
            signal_strengths = {}
            total_strength = 0.0
            
            for symbol in self.target_symbols:
                # 临时使用随机权重模拟信号强度，实际应该从信号生成器获取
                strength = 1.0  # 可以从信号生成器的置信度获取
                signal_strengths[symbol] = strength
                total_strength += strength
            
            # 根据信号强度分配权重
            for symbol in self.target_symbols:
                if symbol in positions and total_strength > 0:
                    base_weight = signal_strengths[symbol] / total_strength
                    # 应用权重限制
                    weight = max(self.min_position_weight, 
                               min(self.max_position_weight, base_weight))
                    
                    positions[symbol].target_weight = weight
                    positions[symbol].target_value = available_for_investment * weight
                    
                    self.logger.debug(f"信号权重 {symbol}: 强度={signal_strengths[symbol]}, 权重={weight:.2%}")
                    
        except Exception as e:
            self.logger.error(f"计算信号权重失败: {e}")
            # 回退到等权重
            target_weight = 1.0 / len(self.target_symbols)
            for symbol in self.target_symbols:
                if symbol in positions:
                    positions[symbol].target_weight = target_weight
                    positions[symbol].target_value = available_for_investment * target_weight
    
    def _calculate_deviation(self, positions: Dict[str, PositionTarget]) -> float:
        """计算配置偏离度"""
        try:
            total_deviation = 0.0
            count = 0
            
            for position in positions.values():
                if position.target_value > 0:
                    deviation = abs(position.current_value - position.target_value) / position.target_value
                    total_deviation += deviation
                    count += 1
            
            return total_deviation / max(count, 1)
            
        except Exception as e:
            self.logger.error(f"计算偏离度失败: {e}")
            return 0.0
    
    def _check_rebalance_needed(self, positions: Dict[str, PositionTarget]) -> bool:
        """检查是否需要再平衡"""
        try:
            # 检查时间条件
            time_since_last_rebalance = datetime.now() - self.last_rebalance_time
            if time_since_last_rebalance.total_seconds() < self.rebalance_frequency:
                return False
            
            # 检查偏离度条件
            for position in positions.values():
                if position.deviation > self.rebalance_threshold:
                    return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查再平衡条件失败: {e}")
            return False
    
    async def generate_rebalance_orders(self) -> List[Dict]:
        """生成再平衡订单"""
        try:
            if not self.portfolio_status or not self.portfolio_status.rebalance_needed:
                return []
            
            orders = []
            
            # 按优先级排序
            sorted_positions = sorted(
                self.portfolio_status.positions.values(),
                key=lambda x: x.priority,
                reverse=True
            )
            
            for position in sorted_positions:
                if position.action in ["BUY", "SELL"] and position.priority > 0:
                    quantity_diff = position.target_quantity - position.current_quantity
                    
                    if abs(quantity_diff) >= 1:
                        order = {
                            "symbol": position.symbol,
                            "action": position.action,
                            "quantity": abs(quantity_diff),
                            "price": self.price_cache.get(position.symbol, 0.0),
                            "priority": position.priority,
                            "reason": f"再平衡调整，目标权重: {position.target_weight:.2%}"
                        }
                        orders.append(order)
                        
                        self.logger.info(f"生成再平衡订单: {order}")
            
            return orders
            
        except Exception as e:
            self.logger.error(f"生成再平衡订单失败: {e}")
            return []
    
    async def execute_rebalance(self) -> bool:
        """执行投资组合再平衡"""
        try:
            self.logger.info("开始执行投资组合再平衡...")
            
            # 更新投资组合状态
            await self.update_portfolio_status()
            
            if not self.portfolio_status.rebalance_needed:
                self.logger.info("无需进行再平衡")
                return True
            
            # 生成再平衡订单
            orders = await self.generate_rebalance_orders()
            
            if not orders:
                self.logger.info("没有生成再平衡订单")
                return True
            
            # 执行订单
            success_count = 0
            for order in orders:
                try:
                    # 使用order_manager执行订单
                    result = await self.order_manager.submit_buy_order(
                        symbol=order["symbol"],
                        price=order["price"],
                        quantity=order["quantity"],
                        strategy_name="portfolio_rebalance"
                    ) if order["action"] == "BUY" else await self.order_manager.submit_sell_order(
                        symbol=order["symbol"],
                        price=order["price"],
                        quantity=order["quantity"],
                        strategy_name="portfolio_rebalance"
                    )
                    
                    if result and not result.is_rejected():
                        success_count += 1
                        self.logger.info(f"再平衡订单执行成功: {order}")
                    else:
                        self.logger.warning(f"再平衡订单执行失败: {order}, 结果: {result}")
                        
                except Exception as e:
                    self.logger.error(f"执行再平衡订单失败: {order}, 错误: {e}")
            
            self.last_rebalance_time = datetime.now()
            
            self.logger.info(f"再平衡执行完成，成功订单: {success_count}/{len(orders)}")
            return success_count > 0
            
        except Exception as e:
            self.logger.error(f"执行投资组合再平衡失败: {e}")
            return False
    
    def get_position_suggestion(self, symbol: str, signal_confidence: float) -> Tuple[str, int]:
        """
        根据信号强度和当前投资组合状态给出仓位建议
        
        Args:
            symbol: 股票代码
            signal_confidence: 信号置信度
            
        Returns:
            (建议动作, 建议数量)
        """
        try:
            if not self.portfolio_status or symbol not in self.portfolio_status.positions:
                return "HOLD", 0
            
            position = self.portfolio_status.positions[symbol]
            current_price = self.price_cache.get(symbol, 0.0)
            
            if current_price <= 0:
                return "HOLD", 0
            
            # 基于信号强度调整目标权重
            signal_factor = min(2.0, max(0.5, signal_confidence * self.signal_weight_factor))
            adjusted_target_value = position.target_value * signal_factor
            adjusted_target_quantity = int(adjusted_target_value / current_price)
            
            quantity_diff = adjusted_target_quantity - position.current_quantity
            
            if abs(quantity_diff) >= 1:
                action = "BUY" if quantity_diff > 0 else "SELL"
                quantity = abs(quantity_diff)
                
                # 检查买入时的资金约束
                if action == "BUY":
                    # 获取当前可用资金（考虑负余额的情况）
                    cash_available = self.portfolio_status.cash_available
                    required_amount = quantity * current_price
                    
                    # 🔧 关键修复：严格的负余额检查
                    if cash_available <= 0:
                        self.logger.warning(f"资金不足或为负，拒绝买入建议: {symbol}, 可用资金: {cash_available:.2f}")
                        return "HOLD", 0
                    
                    # 🔧 应用严格的资金限制 (基于position_pct配置)
                    total_equity = self.portfolio_status.total_equity
                    max_position_pct = 2.0  # 从配置读取，这里硬编码为安全起见
                    max_trade_value = abs(total_equity) * (max_position_pct / 100.0) if total_equity != 0 else 0
                    max_safe_quantity = int(max_trade_value / current_price) if current_price > 0 else 0
                    
                    self.logger.info(f"投资组合风控 {symbol}: 总权益={total_equity:.2f}, 限制={max_position_pct}%, "
                                   f"最大安全金额={max_trade_value:.2f}, 原始建议={quantity}股, 限制后={max_safe_quantity}股")
                    
                    # 应用更严格的限制
                    quantity = min(quantity, max_safe_quantity)
                    
                    # 如果总权益为负，使用超保守策略
                    if total_equity < 0:
                        # 计算可用于投资的极小比例（0.1%的绝对值）
                        ultra_conservative_value = abs(total_equity) * 0.001  
                        ultra_conservative_quantity = max(1, int(ultra_conservative_value / current_price))
                        
                        quantity = min(quantity, ultra_conservative_quantity, 3)  # 最多3股
                        self.logger.warning(f"总权益为负，使用超保守策略: {symbol}, 权益={total_equity:.2f}, "
                                          f"超保守数量={quantity}股")
                        
                        if quantity <= 0:
                            self.logger.warning(f"超保守策略下仍无法交易: {symbol}")
                            return "HOLD", 0
                    
                    # 再次检查可用资金
                    elif required_amount > cash_available:
                        # 根据可用资金调整买入数量
                        max_affordable_quantity = int(cash_available / current_price)
                        if max_affordable_quantity > 0:
                            quantity = min(max_affordable_quantity, quantity)
                            self.logger.info(f"资金约束调整买入数量: {symbol}, 原计划: {abs(quantity_diff)}, "
                                           f"调整后: {quantity}, 可用资金: {cash_available:.2f}")
                        else:
                            self.logger.warning(f"资金不足买入1股，跳过: {symbol}, 可用资金: {cash_available:.2f}, "
                                              f"单股价格: {current_price:.2f}")
                            return "HOLD", 0
                
                # 🔧 最终安全检查：确保建议数量合理
                if quantity <= 0:
                    return "HOLD", 0
                
                # 限制最大建议数量（防止异常大单）
                MAX_SUGGESTION = 10  # 单次最多建议买入10股
                if quantity > MAX_SUGGESTION:
                    self.logger.warning(f"建议数量过大，限制为{MAX_SUGGESTION}股: {symbol}, 原建议={quantity}")
                    quantity = MAX_SUGGESTION
                
                self.logger.info(f"最终仓位建议 {symbol}: 信号置信度={signal_confidence:.2f}, "
                                f"调整因子={signal_factor:.2f}, 当前={position.current_quantity}, "
                                f"目标={adjusted_target_quantity}, 建议={action} {quantity}")
                
                return action, quantity
            
            return "HOLD", 0
            
        except Exception as e:
            self.logger.error(f"生成仓位建议失败: {e}")
            return "HOLD", 0
    
    def get_portfolio_summary(self) -> Dict:
        """获取投资组合摘要"""
        try:
            if not self.portfolio_status:
                return {}
            
            summary = {
                "total_value": self.portfolio_status.total_value,
                "cash_available": self.portfolio_status.cash_available,
                "total_equity": self.portfolio_status.total_equity,
                "allocation_deviation": self.portfolio_status.allocation_deviation,
                "rebalance_needed": self.portfolio_status.rebalance_needed,
                "positions": {}
            }
            
            for symbol, position in self.portfolio_status.positions.items():
                summary["positions"][symbol] = {
                    "current_quantity": position.current_quantity,
                    "current_value": position.current_value,
                    "target_weight": position.target_weight,
                    "target_value": position.target_value,
                    "target_quantity": position.target_quantity,
                    "deviation": position.deviation,
                    "action": position.action
                }
            
            return summary
            
        except Exception as e:
            self.logger.error(f"获取投资组合摘要失败: {e}")
            return {}
    
    def check_correlation(self, new_symbol: str) -> Tuple[bool, str]:
        """
        检查新股票与现有持仓的相关性
        
        Args:
            new_symbol: 新股票代码
            
        Returns:
            Tuple[bool, str]: (是否通过检查, 消息)
        """
        if self.correlation_filter is None:
            return True, "相关性过滤器未初始化，允许交易"
        
        # 获取当前持仓的股票列表
        current_positions = []
        if self.portfolio_status and self.portfolio_status.positions:
            current_positions = [
                symbol for symbol, pos in self.portfolio_status.positions.items()
                if pos.current_quantity > 0
            ]
        
        return self.correlation_filter.check_correlation(new_symbol, current_positions)
    
    def get_portfolio_correlation_stats(self) -> Dict:
        """
        获取投资组合的相关性统计
        
        Returns:
            相关性统计字典
        """
        if self.correlation_filter is None:
            return {'error': '相关性过滤器未初始化'}
        
        # 获取当前持仓的股票列表
        current_positions = []
        if self.portfolio_status and self.portfolio_status.positions:
            current_positions = [
                symbol for symbol, pos in self.portfolio_status.positions.items()
                if pos.current_quantity > 0
            ]
        
        return self.correlation_filter.get_portfolio_correlation(current_positions)
    
    async def refresh_correlation_matrix(self):
        """刷新相关性矩阵"""
        if self.correlation_filter:
            await self.correlation_filter.update_correlation_matrix(self.target_symbols, force=True)
            self.logger.info("相关性矩阵已刷新")