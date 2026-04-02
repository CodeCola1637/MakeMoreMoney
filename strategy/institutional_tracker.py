#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
机构交易跟踪模块

通过 SEC EDGAR 公开数据跟踪：
1. 13F 机构持仓变动 - 跟踪顶级对冲基金和机构季度持仓变化
2. Form 4 内部人交易 - 跟踪公司高管买卖操作（2天内披露，最及时）
3. 基于机构行为生成交易信号
"""

import asyncio
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from utils import setup_logger


# ============================================================
# 数据类定义
# ============================================================

class InstitutionAction(Enum):
    NEW_POSITION = "new_position"
    INCREASED = "increased"
    DECREASED = "decreased"
    SOLD_OUT = "sold_out"
    UNCHANGED = "unchanged"


@dataclass
class InstitutionalHolding:
    """机构持仓记录"""
    symbol: str
    name: str
    cusip: str
    shares: int
    value_thousands: float
    institution: str
    filing_date: str
    action: InstitutionAction = InstitutionAction.UNCHANGED
    shares_change: int = 0
    shares_change_pct: float = 0.0


@dataclass
class InsiderTransaction:
    """内部人交易记录"""
    symbol: str
    insider_name: str
    role: str
    transaction_type: str   # P=Purchase, S=Sale, A=Award
    shares: int
    price: float
    total_value: float
    transaction_date: str
    filing_date: str
    acquired_disposed: str  # A=Acquired, D=Disposed


@dataclass
class InstitutionalSignal:
    """机构信号"""
    symbol: str
    signal_type: str        # BUY, SELL
    confidence: float
    sources: List[str] = field(default_factory=list)
    reason: str = ""
    institutional_score: float = 0.0
    insider_buy_count: int = 0
    insider_sell_count: int = 0
    institutions_buying: int = 0
    institutions_selling: int = 0
    filing_date: str = ""


# ============================================================
# CUSIP -> Ticker 映射（覆盖主要美股）
# ============================================================

CUSIP_TO_TICKER = {
    '037833100': 'AAPL',    # Apple
    '594918104': 'MSFT',    # Microsoft
    '02079K305': 'GOOGL',   # Alphabet Class A
    '02079K107': 'GOOG',    # Alphabet Class C
    '67066G104': 'NVDA',    # NVIDIA
    '88160R101': 'TSLA',    # Tesla
    '023135106': 'AMZN',    # Amazon
    '30303M102': 'META',    # Meta Platforms
    '084670702': 'BRK.B',   # Berkshire Hathaway B
    '46625H100': 'JPM',     # JPMorgan Chase
    '92826C839': 'V',       # Visa
    '17275R102': 'CSCO',    # Cisco
    '478160104': 'JNJ',     # Johnson & Johnson
    '91324P102': 'UNH',     # UnitedHealth
    '742718109': 'PG',      # Procter & Gamble
    '375558103': 'GILD',    # Gilead Sciences
    '500754106': 'KO',      # Coca-Cola
    '713448108': 'PEP',     # PepsiCo
    '931142103': 'WMT',     # Walmart
    '172967424': 'C',       # Citigroup
    '060505104': 'BAC',     # Bank of America
    '38141G104': 'GS',      # Goldman Sachs
    '585515104': 'MER',     # Merrill (part of BAC)
    '617446448': 'MS',      # Morgan Stanley
    '254709108': 'DIS',     # Walt Disney
    '00724F101': 'ADBE',    # Adobe
    '79466L302': 'CRM',     # Salesforce
    '44919P508': 'INTC',    # Intel
    '00206R102': 'T',       # AT&T
    '922908363': 'VZ',      # Verizon
    '46120E602': 'INTU',    # Intuit
    '68389X105': 'ORCL',    # Oracle
    '053015103': 'AVGO',    # Broadcom
    '00507V109': 'ACGL',    # Arch Capital
    '532457108': 'LLY',     # Eli Lilly
    '58933Y105': 'MRK',     # Merck
    '718172109': 'PFE',     # Pfizer
    '00287Y109': 'ABBV',    # AbbVie
    '02209S103': 'AMAT',    # Applied Materials
    '46625H100': 'JPM',     # JPMorgan
    '125523100': 'COP',     # ConocoPhillips
    '20825C104': 'COST',    # Costco
    '571903202': 'MCD',     # McDonald's
    '548661107': 'LOW',     # Lowe's
    '427866108': 'HD',      # Home Depot
    '98978V103': 'ZM',      # Zoom
    '90184L102': 'UBER',    # Uber
    '00971T101': 'AFRM',    # Affirm
    '824348106': 'SHOP',    # Shopify
    '126408103': 'CME',     # CME Group
    '87612E106': 'TGT',     # Target
    'G8232Q166': 'SPOT',    # Spotify
    '345838106': 'F',       # Ford
    '37045V100': 'GM',      # General Motors
    '46625H100': 'JPM',     # JPMorgan
}

# Company name -> ticker 模糊匹配
COMPANY_NAME_TO_TICKER = {
    'APPLE INC': 'AAPL',
    'MICROSOFT CORP': 'MSFT',
    'ALPHABET INC': 'GOOGL',
    'NVIDIA CORP': 'NVDA',
    'TESLA INC': 'TSLA',
    'AMAZON COM INC': 'AMZN',
    'AMAZON.COM INC': 'AMZN',
    'META PLATFORMS INC': 'META',
    'FACEBOOK INC': 'META',
    'JPMORGAN CHASE': 'JPM',
    'JPMORGAN CHASE & CO': 'JPM',
    'BANK OF AMERICA': 'BAC',
    'BANK AMER CORP': 'BAC',
    'GOLDMAN SACHS': 'GS',
    'GOLDMAN SACHS GRP INC': 'GS',
    'MORGAN STANLEY': 'MS',
    'VISA INC': 'V',
    'JOHNSON & JOHNSON': 'JNJ',
    'UNITEDHEALTH GROUP': 'UNH',
    'PROCTER & GAMBLE': 'PG',
    'COCA COLA CO': 'KO',
    'WALMART INC': 'WMT',
    'WALT DISNEY CO': 'DIS',
    'SALESFORCE INC': 'CRM',
    'INTEL CORP': 'INTC',
    'ORACLE CORP': 'ORCL',
    'BROADCOM INC': 'AVGO',
    'ELI LILLY & CO': 'LLY',
    'MERCK & CO INC': 'MRK',
    'PFIZER INC': 'PFE',
    'ABBVIE INC': 'ABBV',
    'COSTCO WHOLESALE': 'COST',
    'HOME DEPOT INC': 'HD',
    'BERKSHIRE HATHAWAY': 'BRK.B',
    'ZOOM VIDEO COMMUNICATIONS': 'ZM',
    'UBER TECHNOLOGIES INC': 'UBER',
    'SHOPIFY INC': 'SHOP',
    'TARGET CORP': 'TGT',
    'FORD MOTOR CO': 'F',
    'GENERAL MOTORS CO': 'GM',
    'CITIGROUP INC': 'C',
    'ADOBE INC': 'ADBE',
    'ADVANCED MICRO DEVICES': 'AMD',
    'QUALCOMM INC': 'QCOM',
    'NETFLIX INC': 'NFLX',
    'PAYPAL HLDGS INC': 'PYPL',
    'COINBASE GLOBAL INC': 'COIN',
    'PALANTIR TECHNOLOGIES': 'PLTR',
    'SNOWFLAKE INC': 'SNOW',
    'CROWDSTRIKE HOLDINGS': 'CRWD',
    'DATADOG INC': 'DDOG',
    'SERVICENOW INC': 'NOW',
}


# 顶级机构列表 (CIK numbers)
TOP_INSTITUTIONS = {
    'berkshire_hathaway': {
        'cik': '0001067983',
        'name': 'Berkshire Hathaway (Warren Buffett)',
        'weight': 1.5,   # 巴菲特信号加权
    },
    'bridgewater': {
        'cik': '0001350694',
        'name': 'Bridgewater Associates (Ray Dalio)',
        'weight': 1.3,
    },
    'renaissance': {
        'cik': '0001037389',
        'name': 'Renaissance Technologies (Jim Simons)',
        'weight': 1.4,
    },
    'citadel': {
        'cik': '0001423053',
        'name': 'Citadel Advisors (Ken Griffin)',
        'weight': 1.2,
    },
    'two_sigma': {
        'cik': '0001179392',
        'name': 'Two Sigma Investments',
        'weight': 1.2,
    },
    'point72': {
        'cik': '0001603466',
        'name': 'Point72 (Steve Cohen)',
        'weight': 1.3,
    },
    'blackrock': {
        'cik': '0001364742',
        'name': 'BlackRock',
        'weight': 1.0,
    },
    'jpmorgan': {
        'cik': '0000019617',
        'name': 'JPMorgan Chase',
        'weight': 1.1,
    },
    'goldman_sachs': {
        'cik': '0000886982',
        'name': 'Goldman Sachs',
        'weight': 1.1,
    },
    'morgan_stanley': {
        'cik': '0000895421',
        'name': 'Morgan Stanley',
        'weight': 1.1,
    },
    'soros': {
        'cik': '0001029160',
        'name': 'Soros Fund Management',
        'weight': 1.3,
    },
    'appaloosa': {
        'cik': '0001656456',
        'name': 'Appaloosa Management (David Tepper)',
        'weight': 1.2,
    },
}


# ============================================================
# SEC EDGAR API 客户端
# ============================================================

class SECEdgarClient:
    """SEC EDGAR API 客户端"""

    DATA_URL = "https://data.sec.gov"
    ARCHIVES_URL = "https://www.sec.gov/Archives"
    SEARCH_URL = "https://efts.sec.gov/LATEST"
    RATE_LIMIT_INTERVAL = 0.12  # SEC要求不超过10请求/秒

    def __init__(self, user_agent: str, logger: logging.Logger):
        self.user_agent = user_agent
        self.logger = logger
        self._last_request_time = 0

    async def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.RATE_LIMIT_INTERVAL:
            await asyncio.sleep(self.RATE_LIMIT_INTERVAL - elapsed)
        self._last_request_time = time.time()

    async def _fetch(self, url: str) -> Optional[str]:
        """带速率限制的 HTTP GET（自动处理 gzip）"""
        await self._rate_limit()
        headers = {
            'User-Agent': self.user_agent,
            'Accept-Encoding': 'gzip, deflate',
            'Accept': 'application/json, text/html, application/xml, text/xml, */*',
        }
        try:
            def _do_request():
                import gzip as _gzip
                req = Request(url, headers=headers)
                with urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    encoding = resp.headers.get('Content-Encoding', '')
                    if encoding == 'gzip':
                        raw = _gzip.decompress(raw)
                    return raw.decode('utf-8', errors='replace')

            return await asyncio.to_thread(_do_request)
        except HTTPError as e:
            self.logger.warning(f"SEC EDGAR HTTP错误: {url} -> {e.code}")
            return None
        except (URLError, Exception) as e:
            self.logger.warning(f"SEC EDGAR请求失败: {url} -> {e}")
            return None

    async def get_company_filings(self, cik: str) -> Optional[Dict]:
        """获取公司/机构的所有提交记录"""
        cik_padded = cik.lstrip('0').zfill(10)
        url = f"{self.DATA_URL}/submissions/CIK{cik_padded}.json"
        text = await self._fetch(url)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                self.logger.error(f"无法解析JSON: {url}")
        return None

    async def get_13f_holdings(self, cik: str, institution_name: str) -> List[InstitutionalHolding]:
        """获取机构最新 13F 持仓"""
        filings_data = await self.get_company_filings(cik)
        if not filings_data:
            return []

        recent = filings_data.get('filings', {}).get('recent', {})
        if not recent:
            return []

        forms = recent.get('form', [])
        accessions = recent.get('accessionNumber', [])
        filing_dates = recent.get('filingDate', [])
        primary_docs = recent.get('primaryDocument', [])

        # 找到最新的 13F-HR 提交
        for i, form in enumerate(forms):
            if form in ('13F-HR', '13F-HR/A'):
                accession = accessions[i]
                filing_date = filing_dates[i]
                accession_clean = accession.replace('-', '')
                cik_clean = cik.lstrip('0')

                # 获取提交的文件索引
                index_url = f"{self.ARCHIVES_URL}/edgar/data/{cik_clean}/{accession_clean}/index.json"
                index_text = await self._fetch(index_url)
                if not index_text:
                    continue

                try:
                    index_data = json.loads(index_text)
                except json.JSONDecodeError:
                    continue

                # 找到 information table XML 文件
                infotable_file = None
                for item in index_data.get('directory', {}).get('item', []):
                    name = item.get('name', '').lower()
                    if 'infotable' in name and name.endswith('.xml'):
                        infotable_file = item['name']
                        break

                if not infotable_file:
                    # 尝试其他命名方式
                    for item in index_data.get('directory', {}).get('item', []):
                        name = item.get('name', '').lower()
                        if ('information' in name or '13f' in name) and name.endswith('.xml'):
                            infotable_file = item['name']
                            break

                if not infotable_file:
                    self.logger.warning(f"未找到 13F 信息表: {institution_name}")
                    continue

                # 下载并解析 information table
                table_url = f"{self.ARCHIVES_URL}/edgar/data/{cik_clean}/{accession_clean}/{infotable_file}"
                xml_text = await self._fetch(table_url)
                if not xml_text:
                    continue

                raw_holdings = self._parse_13f_xml(xml_text, institution_name, filing_date)
                holdings = self._aggregate_holdings(raw_holdings)
                self.logger.info(f"解析 {institution_name} 13F: {len(holdings)} 只持仓 "
                               f"(合并前{len(raw_holdings)}), 提交日期: {filing_date}")
                return holdings

        return []

    def _parse_13f_xml(self, xml_text: str, institution_name: str, filing_date: str) -> List[InstitutionalHolding]:
        """解析 13F XML 信息表"""
        holdings = []
        try:
            root = ET.fromstring(xml_text)

            # 处理命名空间
            ns = ''
            for prefix, uri in [('', '')]:
                pass
            # 自动检测命名空间
            tag = root.tag
            if '{' in tag:
                ns = tag[:tag.index('}') + 1]

            for info in root.iter(f'{ns}infoTable'):
                try:
                    name = self._get_xml_text(info, f'{ns}nameOfIssuer', '')
                    cusip = self._get_xml_text(info, f'{ns}cusip', '')
                    value = self._get_xml_float(info, f'{ns}value', 0)

                    # 获取股数 - 处理不同的XML结构
                    shares = 0
                    shramt_elem = info.find(f'{ns}shrsOrPrnAmt')
                    if shramt_elem is not None:
                        shares = self._get_xml_int(shramt_elem, f'{ns}sshPrnamt', 0)
                    if shares == 0:
                        shares = self._get_xml_int(info, f'{ns}sshPrnamt', 0)

                    # CUSIP -> 股票代码
                    ticker = CUSIP_TO_TICKER.get(cusip, '')
                    if not ticker:
                        ticker = self._match_company_name(name)

                    if ticker:
                        holdings.append(InstitutionalHolding(
                            symbol=f"{ticker}.US",
                            name=name,
                            cusip=cusip,
                            shares=shares,
                            value_thousands=value,
                            institution=institution_name,
                            filing_date=filing_date,
                        ))
                except Exception as e:
                    continue

        except ET.ParseError as e:
            self.logger.error(f"XML解析错误: {e}")

        return holdings

    async def get_insider_transactions(self, cik: str, days: int = 30) -> List[InsiderTransaction]:
        """获取公司最近的 Form 4 内部人交易"""
        filings_data = await self.get_company_filings(cik)
        if not filings_data:
            return []

        recent = filings_data.get('filings', {}).get('recent', {})
        forms = recent.get('form', [])
        accessions = recent.get('accessionNumber', [])
        filing_dates = recent.get('filingDate', [])
        primary_docs = recent.get('primaryDocument', [])

        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        transactions = []
        parsed_count = 0
        max_parse = 15  # 限制解析数量避免过多API调用

        for i, form in enumerate(forms):
            if form not in ('4', '4/A'):
                continue
            if filing_dates[i] < cutoff_date:
                break
            if parsed_count >= max_parse:
                break

            accession = accessions[i]
            accession_clean = accession.replace('-', '')
            cik_clean = cik.lstrip('0')
            doc = primary_docs[i] if i < len(primary_docs) else ''

            # 去掉 XSLT 路径前缀（如 "xslF345X05/"）
            if '/' in doc:
                doc = doc.split('/')[-1]

            if not doc.endswith('.xml'):
                doc = doc.replace('.htm', '.xml').replace('.html', '.xml')

            doc_url = f"{self.ARCHIVES_URL}/edgar/data/{cik_clean}/{accession_clean}/{doc}"
            xml_text = await self._fetch(doc_url)
            if not xml_text:
                continue

            txns = self._parse_form4_xml(xml_text, filing_dates[i])
            transactions.extend(txns)
            parsed_count += 1

        return transactions

    def _parse_form4_xml(self, xml_text: str, filing_date: str) -> List[InsiderTransaction]:
        """解析 Form 4 XML"""
        transactions = []
        try:
            root = ET.fromstring(xml_text)

            # 获取股票代码
            issuer = root.find('.//issuerTradingSymbol')
            symbol = issuer.text.strip() if issuer is not None and issuer.text else ''
            if not symbol:
                return []

            # 获取内部人信息
            owner_name_elem = root.find('.//rptOwnerName')
            owner_name = owner_name_elem.text.strip() if owner_name_elem is not None and owner_name_elem.text else 'Unknown'

            # 获取职位
            role = self._extract_insider_role(root)

            # 解析非衍生品交易
            for txn in root.iter('nonDerivativeTransaction'):
                try:
                    txn_date = self._get_xml_text(txn, './/transactionDate/value', '')
                    txn_code = self._get_xml_text(txn, './/transactionCoding/transactionCode', '')
                    shares = self._get_xml_float(txn, './/transactionAmounts/transactionShares/value', 0)
                    price = self._get_xml_float(txn, './/transactionAmounts/transactionPricePerShare/value', 0)
                    acq_disp = self._get_xml_text(txn, './/transactionAmounts/transactionAcquiredDisposedCode/value', '')

                    # 只关注实际的买入(P)和卖出(S)，忽略期权行使(A/M)等
                    if txn_code not in ('P', 'S'):
                        continue

                    total_value = shares * price if price > 0 else 0

                    transactions.append(InsiderTransaction(
                        symbol=f"{symbol}.US",
                        insider_name=owner_name,
                        role=role,
                        transaction_type=txn_code,
                        shares=int(shares),
                        price=price,
                        total_value=total_value,
                        transaction_date=txn_date,
                        filing_date=filing_date,
                        acquired_disposed=acq_disp,
                    ))
                except Exception:
                    continue

        except ET.ParseError:
            pass

        return transactions

    def _extract_insider_role(self, root) -> str:
        """提取内部人职位"""
        rel = root.find('.//reportingOwnerRelationship')
        if rel is None:
            return 'Other'
        title = rel.find('officerTitle')
        if title is not None and title.text:
            return title.text.strip()
        if self._get_xml_text(rel, 'isDirector', '0') == '1':
            return 'Director'
        if self._get_xml_text(rel, 'isTenPercentOwner', '0') == '1':
            return '10% Owner'
        if self._get_xml_text(rel, 'isOfficer', '0') == '1':
            return 'Officer'
        return 'Other'

    @staticmethod
    def _get_xml_text(elem, path: str, default: str = '') -> str:
        child = elem.find(path)
        return child.text.strip() if child is not None and child.text else default

    @staticmethod
    def _get_xml_float(elem, path: str, default: float = 0.0) -> float:
        child = elem.find(path)
        if child is not None and child.text:
            try:
                return float(child.text.strip().replace(',', ''))
            except ValueError:
                pass
        return default

    @staticmethod
    def _get_xml_int(elem, path: str, default: int = 0) -> int:
        child = elem.find(path)
        if child is not None and child.text:
            try:
                return int(float(child.text.strip().replace(',', '')))
            except ValueError:
                pass
        return default

    @staticmethod
    def _aggregate_holdings(holdings: List[InstitutionalHolding]) -> List[InstitutionalHolding]:
        """合并同一股票的多个持仓条目"""
        aggregated: Dict[str, InstitutionalHolding] = {}
        for h in holdings:
            if h.symbol in aggregated:
                existing = aggregated[h.symbol]
                existing.shares += h.shares
                existing.value_thousands += h.value_thousands
            else:
                aggregated[h.symbol] = InstitutionalHolding(
                    symbol=h.symbol, name=h.name, cusip=h.cusip,
                    shares=h.shares, value_thousands=h.value_thousands,
                    institution=h.institution, filing_date=h.filing_date,
                )
        return list(aggregated.values())

    @staticmethod
    def _match_company_name(name: str) -> str:
        """通过公司名模糊匹配股票代码"""
        name_upper = name.upper().strip()
        if name_upper in COMPANY_NAME_TO_TICKER:
            return COMPANY_NAME_TO_TICKER[name_upper]
        # 尝试部分匹配
        for key, ticker in COMPANY_NAME_TO_TICKER.items():
            if key in name_upper or name_upper in key:
                return ticker
        return ''


# ============================================================
# SEC 公司 CIK 查询（ticker -> CIK 映射）
# ============================================================

# 常见股票的 CIK 预映射，避免每次查询
TICKER_TO_CIK = {
    'AAPL': '0000320193',
    'MSFT': '0000789019',
    'GOOGL': '0001652044',
    'GOOG': '0001652044',
    'AMZN': '0001018724',
    'NVDA': '0001045810',
    'TSLA': '0001318605',
    'META': '0001326801',
    'JPM': '0000019617',
    'V': '0001403161',
    'JNJ': '0000200406',
    'UNH': '0000731766',
    'HD': '0000354950',
    'PG': '0000080424',
    'BAC': '0000070858',
    'GS': '0000886982',
    'MS': '0000895421',
    'DIS': '0001744489',
    'NFLX': '0001065280',
    'ADBE': '0000796343',
    'CRM': '0001108524',
    'INTC': '0000050863',
    'AMD': '0000002488',
    'QCOM': '0000804328',
    'COST': '0000909832',
    'KO': '0000021344',
    'PEP': '0000077476',
    'WMT': '0000104169',
    'MCD': '0000789019',
    'ABBV': '0001551152',
    'MRK': '0000310158',
    'LLY': '0000059478',
    'PFE': '0000078003',
    'ZM': '0001585521',
    'UBER': '0001543151',
    'COIN': '0001679788',
    'PLTR': '0001321655',
    'SNOW': '0001640147',
    'CRWD': '0001535527',
}


# ============================================================
# 机构交易跟踪器
# ============================================================

class InstitutionalTracker:
    """机构交易跟踪器"""

    CACHE_DIR = "data_cache/institutional"

    def __init__(self, config, logger: logging.Logger = None):
        self.config = config
        self.logger = logger or setup_logger("institutional_tracker",
                                             config.get("logging.level", "INFO"),
                                             config.get("logging.file"))

        user_email = config.get("institutional.user_email", "research@example.com")
        self.edgar_client = SECEdgarClient(
            user_agent=f"MakeMoreMoney Research {user_email}",
            logger=self.logger,
        )

        # 配置
        self.enabled = config.get("institutional.enable", True)
        self.insider_days = config.get("institutional.insider_days", 30)
        self.min_transaction_value = config.get("institutional.min_transaction_value", 100000)
        self.insider_buy_boost = config.get("institutional.insider_buy_confidence_boost", 0.15)
        self.min_institutions_for_signal = config.get("institutional.min_institutions_buying", 2)

        # 选择要跟踪的机构
        tracked = config.get("institutional.tracked_institutions", list(TOP_INSTITUTIONS.keys()))
        self.tracked_institutions = {k: v for k, v in TOP_INSTITUTIONS.items() if k in tracked}

        # 缓存
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        self._holdings_cache: Dict[str, List[InstitutionalHolding]] = {}
        self._prev_holdings_cache: Dict[str, List[InstitutionalHolding]] = {}
        self._insider_cache: Dict[str, List[InsiderTransaction]] = {}
        self._last_13f_scan = None
        self._last_insider_scan = None
        self._load_cache()

        self.logger.info(f"机构跟踪器初始化完成: 跟踪 {len(self.tracked_institutions)} 家机构")

    # ----------------------------------------------------------
    # 13F 机构持仓跟踪
    # ----------------------------------------------------------

    async def scan_13f_holdings(self) -> Dict[str, List[InstitutionalHolding]]:
        """扫描所有跟踪机构的最新 13F 持仓"""
        self.logger.info("开始扫描机构 13F 持仓...")
        all_holdings = {}

        for inst_key, inst_info in self.tracked_institutions.items():
            try:
                holdings = await self.edgar_client.get_13f_holdings(
                    inst_info['cik'], inst_info['name']
                )
                if holdings:
                    all_holdings[inst_key] = holdings
                    self.logger.info(f"  {inst_info['name']}: {len(holdings)} 只持仓")
                else:
                    self.logger.warning(f"  {inst_info['name']}: 未获取到持仓数据")

                await asyncio.sleep(1)  # 礼貌的请求间隔
            except Exception as e:
                self.logger.error(f"扫描 {inst_info['name']} 失败: {e}")

        # 保存旧持仓用于对比
        self._prev_holdings_cache = self._holdings_cache.copy()
        self._holdings_cache = all_holdings
        self._last_13f_scan = datetime.now()
        self._save_cache()

        return all_holdings

    def detect_13f_changes(self) -> Dict[str, List[InstitutionalHolding]]:
        """对比前后两次 13F 持仓，检测变动"""
        changes = {}

        for inst_key, current_holdings in self._holdings_cache.items():
            prev_holdings = self._prev_holdings_cache.get(inst_key, [])

            prev_map = {h.symbol: h for h in prev_holdings}
            curr_map = {h.symbol: h for h in current_holdings}

            inst_changes = []
            # 检查新增和变化
            for symbol, curr in curr_map.items():
                prev = prev_map.get(symbol)
                if prev is None:
                    curr.action = InstitutionAction.NEW_POSITION
                    curr.shares_change = curr.shares
                    curr.shares_change_pct = 100.0
                    inst_changes.append(curr)
                elif curr.shares != prev.shares:
                    change = curr.shares - prev.shares
                    curr.shares_change = change
                    curr.shares_change_pct = (change / prev.shares * 100) if prev.shares > 0 else 100
                    curr.action = InstitutionAction.INCREASED if change > 0 else InstitutionAction.DECREASED
                    inst_changes.append(curr)

            # 检查清仓
            for symbol, prev in prev_map.items():
                if symbol not in curr_map:
                    sold = InstitutionalHolding(
                        symbol=symbol,
                        name=prev.name,
                        cusip=prev.cusip,
                        shares=0,
                        value_thousands=0,
                        institution=prev.institution,
                        filing_date=prev.filing_date,
                        action=InstitutionAction.SOLD_OUT,
                        shares_change=-prev.shares,
                        shares_change_pct=-100.0,
                    )
                    inst_changes.append(sold)

            if inst_changes:
                changes[inst_key] = inst_changes

        return changes

    # ----------------------------------------------------------
    # Form 4 内部人交易跟踪
    # ----------------------------------------------------------

    async def scan_insider_trades(self, symbols: List[str]) -> Dict[str, List[InsiderTransaction]]:
        """扫描指定股票的内部人交易"""
        self.logger.info(f"开始扫描 {len(symbols)} 只股票的内部人交易...")
        all_transactions = {}

        for symbol in symbols:
            ticker = symbol.replace('.US', '')
            cik = TICKER_TO_CIK.get(ticker)
            if not cik:
                self.logger.debug(f"未找到 {ticker} 的CIK，跳过")
                continue

            try:
                txns = await self.edgar_client.get_insider_transactions(cik, self.insider_days)
                if txns:
                    all_transactions[symbol] = txns
                    buys = sum(1 for t in txns if t.transaction_type == 'P')
                    sells = sum(1 for t in txns if t.transaction_type == 'S')
                    self.logger.info(f"  {symbol}: {len(txns)} 笔内部人交易 (买入:{buys}, 卖出:{sells})")

                await asyncio.sleep(1)
            except Exception as e:
                self.logger.error(f"扫描 {symbol} 内部人交易失败: {e}")

        self._insider_cache = all_transactions
        self._last_insider_scan = datetime.now()
        self._save_cache()

        return all_transactions

    # ----------------------------------------------------------
    # 信号生成
    # ----------------------------------------------------------

    async def generate_signals(self, watch_symbols: List[str] = None) -> List[InstitutionalSignal]:
        """综合 13F 和内部人交易数据生成信号"""
        signals = []

        # 1. 基于 13F 持仓变动生成信号
        signals.extend(self._generate_13f_signals(watch_symbols))

        # 2. 基于内部人交易生成信号
        signals.extend(self._generate_insider_signals(watch_symbols))

        # 3. 综合评分
        signals = self._consolidate_signals(signals)

        if signals:
            self.logger.info(f"生成 {len(signals)} 个机构信号:")
            for sig in signals:
                self.logger.info(f"  {sig.symbol}: {sig.signal_type}, 置信度={sig.confidence:.2f}, "
                               f"原因={sig.reason}")

        return signals

    def _generate_13f_signals(self, watch_symbols: List[str] = None) -> List[InstitutionalSignal]:
        """基于 13F 变动生成信号"""
        signals = []
        symbol_scores: Dict[str, Dict] = {}

        for inst_key, holdings in self._holdings_cache.items():
            inst_weight = self.tracked_institutions.get(inst_key, {}).get('weight', 1.0)
            inst_name = self.tracked_institutions.get(inst_key, {}).get('name', inst_key)

            for h in holdings:
                if watch_symbols and h.symbol not in watch_symbols:
                    continue
                if h.action == InstitutionAction.UNCHANGED:
                    continue

                if h.symbol not in symbol_scores:
                    symbol_scores[h.symbol] = {
                        'buy_score': 0, 'sell_score': 0,
                        'buy_sources': [], 'sell_sources': [],
                        'inst_buying': 0, 'inst_selling': 0,
                    }

                score = symbol_scores[h.symbol]
                if h.action in (InstitutionAction.NEW_POSITION, InstitutionAction.INCREASED):
                    weight = inst_weight
                    if h.action == InstitutionAction.NEW_POSITION:
                        weight *= 1.5  # 新建仓信号更强
                    score['buy_score'] += weight
                    score['buy_sources'].append(f"{inst_name}(+{h.shares_change_pct:.0f}%)")
                    score['inst_buying'] += 1
                elif h.action in (InstitutionAction.DECREASED, InstitutionAction.SOLD_OUT):
                    weight = inst_weight
                    if h.action == InstitutionAction.SOLD_OUT:
                        weight *= 1.5
                    score['sell_score'] += weight
                    score['sell_sources'].append(f"{inst_name}({h.shares_change_pct:.0f}%)")
                    score['inst_selling'] += 1

        # 收集每个 symbol 最新的 filing_date
        symbol_filing_dates: Dict[str, str] = {}
        for inst_key, holdings in self._holdings_cache.items():
            for h in holdings:
                if h.filing_date:
                    prev = symbol_filing_dates.get(h.symbol, "")
                    if h.filing_date > prev:
                        symbol_filing_dates[h.symbol] = h.filing_date

        for symbol, score in symbol_scores.items():
            net_score = score['buy_score'] - score['sell_score']
            total_institutions = score['inst_buying'] + score['inst_selling']

            if abs(net_score) < 1.0 or total_institutions < self.min_institutions_for_signal:
                continue

            fd = symbol_filing_dates.get(symbol, "")
            if net_score > 0:
                confidence = min(0.9, 0.3 + net_score * 0.1)
                signals.append(InstitutionalSignal(
                    symbol=symbol,
                    signal_type='BUY',
                    confidence=confidence,
                    sources=score['buy_sources'],
                    reason=f"13F: {score['inst_buying']}家机构增持",
                    institutional_score=net_score,
                    institutions_buying=score['inst_buying'],
                    institutions_selling=score['inst_selling'],
                    filing_date=fd,
                ))
            else:
                confidence = min(0.9, 0.3 + abs(net_score) * 0.1)
                signals.append(InstitutionalSignal(
                    symbol=symbol,
                    signal_type='SELL',
                    confidence=confidence,
                    sources=score['sell_sources'],
                    reason=f"13F: {score['inst_selling']}家机构减持",
                    institutional_score=net_score,
                    institutions_buying=score['inst_buying'],
                    institutions_selling=score['inst_selling'],
                    filing_date=fd,
                ))

        return signals

    def _generate_insider_signals(self, watch_symbols: List[str] = None) -> List[InstitutionalSignal]:
        """基于内部人交易生成信号"""
        signals = []

        for symbol, txns in self._insider_cache.items():
            if watch_symbols and symbol not in watch_symbols:
                continue

            buys = [t for t in txns
                    if t.transaction_type == 'P' and t.total_value >= self.min_transaction_value]
            sells = [t for t in txns
                     if t.transaction_type == 'S' and t.total_value >= self.min_transaction_value]

            total_buy_value = sum(t.total_value for t in buys)
            total_sell_value = sum(t.total_value for t in sells)

            latest_filing = max((t.filing_date for t in txns if t.filing_date), default="")

            if buys and total_buy_value > total_sell_value:
                buy_names = [f"{t.insider_name}({t.role})" for t in buys[:3]]
                confidence = min(0.85, 0.4 + len(buys) * 0.1 + (total_buy_value / 1_000_000) * 0.05)

                signals.append(InstitutionalSignal(
                    symbol=symbol,
                    signal_type='BUY',
                    confidence=confidence,
                    sources=buy_names,
                    reason=f"内部人买入: {len(buys)}笔, 总额${total_buy_value:,.0f}",
                    insider_buy_count=len(buys),
                    insider_sell_count=len(sells),
                    filing_date=latest_filing,
                ))

            elif sells and len(sells) >= 3 and total_sell_value > total_buy_value * 3:
                sell_names = [f"{t.insider_name}({t.role})" for t in sells[:3]]
                confidence = min(0.6, 0.2 + len(sells) * 0.05)

                signals.append(InstitutionalSignal(
                    symbol=symbol,
                    signal_type='SELL',
                    confidence=confidence,
                    sources=sell_names,
                    reason=f"内部人集中卖出: {len(sells)}笔, 总额${total_sell_value:,.0f}",
                    insider_buy_count=len(buys),
                    insider_sell_count=len(sells),
                    filing_date=latest_filing,
                ))

        return signals

    def _consolidate_signals(self, signals: List[InstitutionalSignal]) -> List[InstitutionalSignal]:
        """合并同一股票的多个信号源"""
        consolidated: Dict[str, InstitutionalSignal] = {}

        for sig in signals:
            key = f"{sig.symbol}_{sig.signal_type}"
            if key in consolidated:
                existing = consolidated[key]
                existing.confidence = min(0.95, existing.confidence + sig.confidence * 0.3)
                existing.sources.extend(sig.sources)
                existing.reason += f"; {sig.reason}"
                existing.insider_buy_count += sig.insider_buy_count
                existing.insider_sell_count += sig.insider_sell_count
                existing.institutions_buying += sig.institutions_buying
                existing.institutions_selling += sig.institutions_selling
            else:
                consolidated[key] = sig

        return sorted(consolidated.values(), key=lambda s: s.confidence, reverse=True)

    # ----------------------------------------------------------
    # 完整扫描周期
    # ----------------------------------------------------------

    async def run_scan_cycle(self, watch_symbols: List[str] = None) -> List[InstitutionalSignal]:
        """运行完整的扫描周期"""
        if not self.enabled:
            return []

        self.logger.info("=" * 50)
        self.logger.info("🏦 机构交易跟踪扫描开始...")
        start = time.time()

        # 13F 扫描（较慢，每天最多扫描一次）
        should_scan_13f = (
            self._last_13f_scan is None or
            (datetime.now() - self._last_13f_scan).total_seconds() > 86400
        )
        if should_scan_13f:
            await self.scan_13f_holdings()

        # 内部人交易扫描
        us_symbols = watch_symbols or []
        us_symbols = [s for s in us_symbols if '.US' in s]
        if us_symbols:
            await self.scan_insider_trades(us_symbols)

        # 生成信号
        signals = await self.generate_signals(watch_symbols)

        elapsed = time.time() - start
        self.logger.info(f"🏦 扫描完成: 耗时 {elapsed:.1f}秒, 生成 {len(signals)} 个信号")
        self.logger.info("=" * 50)

        return signals

    # ----------------------------------------------------------
    # 获取摘要信息
    # ----------------------------------------------------------

    def get_summary(self) -> str:
        """获取当前跟踪状态摘要"""
        lines = ["📊 机构交易跟踪摘要:"]
        lines.append(f"  跟踪机构: {len(self.tracked_institutions)} 家")
        lines.append(f"  13F 持仓数据: {sum(len(h) for h in self._holdings_cache.values())} 只")
        lines.append(f"  内部人交易: {sum(len(t) for t in self._insider_cache.values())} 笔")

        if self._last_13f_scan:
            lines.append(f"  上次 13F 扫描: {self._last_13f_scan.strftime('%Y-%m-%d %H:%M')}")
        if self._last_insider_scan:
            lines.append(f"  上次内部人扫描: {self._last_insider_scan.strftime('%Y-%m-%d %H:%M')}")

        # 热门持仓
        symbol_count: Dict[str, int] = {}
        for holdings in self._holdings_cache.values():
            for h in holdings:
                symbol_count[h.symbol] = symbol_count.get(h.symbol, 0) + 1

        if symbol_count:
            top = sorted(symbol_count.items(), key=lambda x: x[1], reverse=True)[:10]
            lines.append("  🔥 机构热门持仓:")
            for sym, count in top:
                lines.append(f"    {sym}: {count} 家机构持有")

        return '\n'.join(lines)

    def get_institutional_holdings_for_symbol(self, symbol: str) -> List[Dict]:
        """获取某只股票被哪些机构持有"""
        result = []
        for inst_key, holdings in self._holdings_cache.items():
            for h in holdings:
                if h.symbol == symbol:
                    inst_info = self.tracked_institutions.get(inst_key, {})
                    result.append({
                        'institution': inst_info.get('name', inst_key),
                        'shares': h.shares,
                        'value_thousands': h.value_thousands,
                        'action': h.action.value,
                        'shares_change': h.shares_change,
                        'shares_change_pct': h.shares_change_pct,
                        'filing_date': h.filing_date,
                    })
        return result

    # ----------------------------------------------------------
    # 持久化缓存
    # ----------------------------------------------------------

    def _save_cache(self):
        """保存缓存数据"""
        try:
            cache_data = {
                'last_13f_scan': self._last_13f_scan.isoformat() if self._last_13f_scan else None,
                'last_insider_scan': self._last_insider_scan.isoformat() if self._last_insider_scan else None,
                'holdings': {},
                'prev_holdings': {},
                'insider_trades': {},
            }
            for inst, holdings in self._holdings_cache.items():
                cache_data['holdings'][inst] = [
                    {'symbol': h.symbol, 'name': h.name, 'cusip': h.cusip,
                     'shares': h.shares, 'value_thousands': h.value_thousands,
                     'institution': h.institution, 'filing_date': h.filing_date,
                     'action': h.action.value, 'shares_change': h.shares_change,
                     'shares_change_pct': h.shares_change_pct}
                    for h in holdings
                ]
            for inst, holdings in self._prev_holdings_cache.items():
                cache_data['prev_holdings'][inst] = [
                    {'symbol': h.symbol, 'name': h.name, 'cusip': h.cusip,
                     'shares': h.shares, 'value_thousands': h.value_thousands,
                     'institution': h.institution, 'filing_date': h.filing_date}
                    for h in holdings
                ]
            for symbol, txns in self._insider_cache.items():
                cache_data['insider_trades'][symbol] = [
                    {'symbol': t.symbol, 'insider_name': t.insider_name,
                     'role': t.role, 'transaction_type': t.transaction_type,
                     'shares': t.shares, 'price': t.price,
                     'total_value': t.total_value,
                     'transaction_date': t.transaction_date,
                     'filing_date': t.filing_date,
                     'acquired_disposed': t.acquired_disposed}
                    for t in txns
                ]

            with open(os.path.join(self.CACHE_DIR, 'tracker_cache.json'), 'w') as f:
                json.dump(cache_data, f, indent=2)

        except Exception as e:
            self.logger.error(f"保存缓存失败: {e}")

    def _load_cache(self):
        """加载缓存数据"""
        cache_file = os.path.join(self.CACHE_DIR, 'tracker_cache.json')
        if not os.path.exists(cache_file):
            return

        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)

            if data.get('last_13f_scan'):
                self._last_13f_scan = datetime.fromisoformat(data['last_13f_scan'])
            if data.get('last_insider_scan'):
                self._last_insider_scan = datetime.fromisoformat(data['last_insider_scan'])

            for inst, items in data.get('holdings', {}).items():
                self._holdings_cache[inst] = [
                    InstitutionalHolding(
                        symbol=h['symbol'], name=h['name'], cusip=h['cusip'],
                        shares=h['shares'], value_thousands=h['value_thousands'],
                        institution=h['institution'], filing_date=h['filing_date'],
                        action=InstitutionAction(h.get('action', 'unchanged')),
                        shares_change=h.get('shares_change', 0),
                        shares_change_pct=h.get('shares_change_pct', 0),
                    ) for h in items
                ]

            for inst, items in data.get('prev_holdings', {}).items():
                self._prev_holdings_cache[inst] = [
                    InstitutionalHolding(
                        symbol=h['symbol'], name=h['name'], cusip=h['cusip'],
                        shares=h['shares'], value_thousands=h['value_thousands'],
                        institution=h['institution'], filing_date=h['filing_date'],
                    ) for h in items
                ]

            for symbol, items in data.get('insider_trades', {}).items():
                self._insider_cache[symbol] = [
                    InsiderTransaction(
                        symbol=t['symbol'], insider_name=t['insider_name'],
                        role=t['role'], transaction_type=t['transaction_type'],
                        shares=t['shares'], price=t['price'],
                        total_value=t['total_value'],
                        transaction_date=t['transaction_date'],
                        filing_date=t['filing_date'],
                        acquired_disposed=t['acquired_disposed'],
                    ) for t in items
                ]

            self.logger.info(f"加载缓存: {len(self._holdings_cache)} 家机构持仓, "
                           f"{len(self._insider_cache)} 只股票内部人交易")
        except Exception as e:
            self.logger.warning(f"加载缓存失败: {e}")
