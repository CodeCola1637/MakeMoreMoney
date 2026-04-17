"""Read-only position and account balance queries extracted from OrderManager."""

import time
import traceback
from typing import Optional, List, Dict

_BALANCE_CACHE_TTL = 30  # seconds
_MARGIN_CACHE_TTL = 60  # seconds


class PositionService:
    """Handles read-only position and balance queries via trade_ctx."""

    def __init__(self, manager):
        self._mgr = manager
        self._balance_cache: Optional[float] = None
        self._balance_cache_time: float = 0.0
        self._margin_cache: Optional[Dict] = None
        self._margin_cache_time: float = 0.0

    @property
    def logger(self):
        return self._mgr.logger

    @property
    def trade_ctx(self):
        return self._mgr.trade_ctx

    @property
    def config(self):
        return self._mgr.config

    def get_account_balance(self):
        """获取账户余额 — 带 30s TTL 缓存，避免热路径中重复 API 调用"""
        now = time.time()
        if self._balance_cache is not None and (now - self._balance_cache_time) < _BALANCE_CACHE_TTL:
            return self._balance_cache

        result = self._fetch_account_balance()
        if result > 0:
            self._balance_cache = result
            self._balance_cache_time = now
        return result

    def get_margin_info(self) -> Dict:
        """获取保证金信息 — 带 60s TTL 缓存"""
        now = time.time()
        if self._margin_cache is not None and (now - self._margin_cache_time) < _MARGIN_CACHE_TTL:
            return self._margin_cache

        result = self._fetch_margin_info()
        if result:
            self._margin_cache = result
            self._margin_cache_time = now
        return result

    def _fetch_margin_info(self) -> Dict:
        """从券商 API 获取保证金/杠杆状态"""
        default = {
            "net_assets": 0.0, "total_cash": 0.0, "buy_power": 0.0,
            "init_margin": 0.0, "maintenance_margin": 0.0,
            "margin_call": 0.0, "risk_level": 0,
            "max_finance_amount": 0.0, "remaining_finance_amount": 0.0,
            "position_value": 0.0, "leverage": 0.0, "margin_ratio": 0.0,
            "available": True,
        }
        try:
            resp = self.trade_ctx.account_balance()
            items = resp if isinstance(resp, list) else [resp]

            for item in items:
                net_assets = float(getattr(item, 'net_assets', 0) or 0)
                if net_assets <= 0:
                    continue
                total_cash = float(getattr(item, 'total_cash', 0) or 0)
                buy_power = float(getattr(item, 'buy_power', 0) or 0)
                init_margin = float(getattr(item, 'init_margin', 0) or 0)
                maint_margin = float(getattr(item, 'maintenance_margin', 0) or 0)
                margin_call_val = float(getattr(item, 'margin_call', 0) or 0)
                risk_level = int(getattr(item, 'risk_level', 0) or 0)
                max_finance = float(getattr(item, 'max_finance_amount', 0) or 0)
                remain_finance = float(getattr(item, 'remaining_finance_amount', 0) or 0)

                position_value = net_assets - total_cash
                leverage = position_value / net_assets if net_assets > 0 else 0
                margin_ratio = (net_assets / position_value * 100) if position_value > 0 else 100.0

                info = {
                    "net_assets": net_assets,
                    "total_cash": total_cash,
                    "buy_power": buy_power,
                    "init_margin": init_margin,
                    "maintenance_margin": maint_margin,
                    "margin_call": margin_call_val,
                    "risk_level": risk_level,
                    "max_finance_amount": max_finance,
                    "remaining_finance_amount": remain_finance,
                    "position_value": position_value,
                    "leverage": leverage,
                    "margin_ratio": margin_ratio,
                    "available": True,
                }
                self.logger.debug(
                    f"保证金信息: 净资产={net_assets:.0f}, 杠杆={leverage:.2f}x, "
                    f"维持保证金={maint_margin:.0f}, 风险等级={risk_level}"
                )
                return info

            self.logger.warning("account_balance 中无有效 net_assets 数据")
            return default
        except Exception as e:
            self.logger.error(f"获取保证金信息失败: {e}")
            return default

    def _fetch_account_balance(self) -> float:
        """实际调用 broker API 获取余额 — 优先 buy_power，回退到 available_cash 求和"""
        try:
            balance_response = self.trade_ctx.account_balance()

            self.logger.debug(f"账户余额对象类型: {type(balance_response)}")

            if isinstance(balance_response, list):
                self.logger.debug(f"账户余额是列表格式，包含 {len(balance_response)} 项")

                for item in balance_response:
                    buy_power = float(getattr(item, 'buy_power', 0) or 0)
                    if buy_power > 0:
                        self.logger.info(f"使用购买力作为余额: {buy_power:.2f}")
                        return buy_power

                total_available = 0.0
                for item in balance_response:
                    if hasattr(item, 'cash_infos') and item.cash_infos:
                        for cash_info in item.cash_infos:
                            if hasattr(cash_info, 'available_cash'):
                                self.logger.info(f"获取到{cash_info.currency}可用资金: {cash_info.available_cash}")
                                if cash_info.currency == "USD":
                                    total_available += float(cash_info.available_cash) * 7.8
                                elif cash_info.currency == "HKD":
                                    total_available += float(cash_info.available_cash)
                                else:
                                    total_available += float(cash_info.available_cash)

                self.logger.info(f"账户总可用资金(回退): {total_available}")
                return total_available

            buy_power = float(getattr(balance_response, 'buy_power', 0) or 0)
            if buy_power > 0:
                self.logger.info(f"使用购买力作为余额: {buy_power:.2f}")
                return buy_power

            if hasattr(balance_response, 'cash_infos') and balance_response.cash_infos:
                total_available = 0.0
                for cash_info in balance_response.cash_infos:
                    if hasattr(cash_info, 'available_cash'):
                        self.logger.info(f"获取到{cash_info.currency}可用资金: {cash_info.available_cash}")
                        if cash_info.currency == "USD":
                            total_available += float(cash_info.available_cash) * 7.8
                        elif cash_info.currency == "HKD":
                            total_available += float(cash_info.available_cash)
                        else:
                            total_available += float(cash_info.available_cash)

                self.logger.info(f"账户总可用资金: {total_available}")
                return total_available
            elif hasattr(balance_response, 'list'):
                balances = balance_response.list
                total_available = 0.0

                for balance in balances:
                    if hasattr(balance, 'available'):
                        self.logger.info(f"获取到{balance.currency}可用资金: {balance.available}")
                        if balance.currency == "USD":
                            total_available += float(balance.available) * 7.8
                        elif balance.currency == "HKD":
                            total_available += float(balance.available)
                        else:
                            total_available += float(balance.available)

                self.logger.info(f"账户总可用资金: {total_available}")
                return total_available
            elif hasattr(balance_response, 'cash') and isinstance(balance_response.cash, dict):
                total_available = 0.0
                for currency, info in balance_response.cash.items():
                    if hasattr(info, 'available'):
                        self.logger.info(f"获取到{currency}可用资金: {info.available}")
                        total_available += float(info.available)

                self.logger.info(f"账户总可用资金: {total_available}")
                return total_available
            else:
                attrs = dir(balance_response)
                self.logger.warning(f"无法识别的账户余额格式，对象属性: {attrs}")

                if hasattr(balance_response, 'net_assets'):
                    self.logger.info(f"使用net_assets作为可用资金: {balance_response.net_assets}")
                    return float(balance_response.net_assets)
                elif hasattr(balance_response, 'total_cash'):
                    self.logger.info(f"使用total_cash作为可用资金: {balance_response.total_cash}")
                    return float(balance_response.total_cash)

                self.logger.error("无法获取账户可用资金，返回默认值")
                return 100000.0
        except Exception as e:
            self.logger.error(f"获取账户余额失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return 0.0

    def get_positions(self, symbol: str = None):
        """获取当前持仓"""
        try:
            positions_response = self.trade_ctx.stock_positions()

            positions = []
            if hasattr(positions_response, 'channels') and positions_response.channels:
                for channel in positions_response.channels:
                    if hasattr(channel, 'positions') and channel.positions:
                        positions.extend(channel.positions)

            position_count = len(positions) if positions else 0
            self.logger.info(f"成功获取持仓, 共{position_count}个")

            if positions:
                for pos in positions:
                    self.logger.debug(f"持仓: {pos.symbol}, 数量: {pos.quantity}, 可用: {pos.available_quantity}")

            if symbol and positions:
                filtered_positions = [p for p in positions if p.symbol.upper() == symbol.upper()]
                self.logger.debug(f"过滤持仓 {symbol}, 结果: {len(filtered_positions)}个")
                return filtered_positions

            return positions
        except Exception as e:
            self.logger.error(f"获取持仓失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return []

    async def get_position(self, symbol: str):
        """获取指定股票的持仓"""
        try:
            positions = self.get_positions(symbol)
            if positions and len(positions) > 0:
                return positions[0]
            return None
        except Exception as e:
            self.logger.error(f"获取{symbol}持仓失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    def is_enough_balance(self, cost: float, symbol: str = None) -> bool:
        """检查账户余额是否足够支付交易成本（优先使用购买力）"""
        try:
            balance_response = self.trade_ctx.account_balance()

            if isinstance(balance_response, list):
                for item in balance_response:
                    buy_power = float(getattr(item, 'buy_power', 0) or 0)
                    if buy_power > 0:
                        if buy_power >= cost:
                            self.logger.info(f"购买力充足: {buy_power:.2f} >= {cost:.2f}")
                            return True
                        else:
                            self.logger.warning(f"购买力不足: {buy_power:.2f} < {cost:.2f}")
                            return False

                target_currency = "USD" if (symbol and '.US' in symbol) else "HKD"
                for item in balance_response:
                    if hasattr(item, 'cash_infos') and item.cash_infos:
                        for cash_info in item.cash_infos:
                            if getattr(cash_info, 'currency', '') == target_currency:
                                available = float(getattr(cash_info, 'available_cash', 0) or 0)
                                if available >= cost:
                                    self.logger.info(f"{target_currency}可用现金充足: {available:.2f} >= {cost:.2f}")
                                    return True
                                else:
                                    self.logger.warning(f"{target_currency}可用现金不足: {available:.2f} < {cost:.2f}")
                                    return False

            total_available = self.get_account_balance()
            if total_available >= cost:
                self.logger.info(f"总账户余额充足: {total_available:.2f} >= {cost:.2f}")
                return True

            self.logger.warning(f"总账户余额不足: {total_available:.2f} < {cost:.2f}")
            return False

        except Exception as e:
            self.logger.error(f"检查账户余额时出错: {str(e)}")
            return False

    def get_account_info(self):
        """获取账户信息"""
        try:
            total_cash = self.get_account_balance()
            positions = self.get_positions()
            return {
                'total_cash': total_cash,
                'total_market_value': 0.0,
                'positions_count': len(positions) if positions else 0,
                'currency': 'USD',
                'positions': positions or []
            }
        except Exception as e:
            self.logger.error(f"获取账户信息失败: {e}")
            return self._create_default_account_info()

    @staticmethod
    def _create_default_account_info():
        return {
            'total_cash': 0.0,
            'total_market_value': 0.0,
            'positions_count': 0,
            'currency': 'USD',
            'balances': [],
            'positions': []
        }
