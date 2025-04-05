#!/usr/bin/env python3
"""
长桥API完整测试脚本 - 包含正确的认证和API调用
"""

import os
import time
import json
import logging
import requests
from dotenv import load_dotenv
import hashlib
import hmac
import base64
from datetime import datetime, timezone
import urllib.parse

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('longport_api')

# 加载环境变量
load_dotenv()

# API凭证 - 更新了键名称以匹配.env文件
APP_KEY = os.getenv('LONG_PORT_APP_KEY')
APP_SECRET = os.getenv('LONG_PORT_APP_SECRET')
ACCESS_TOKEN = os.getenv('LONG_PORT_ACCESS_TOKEN')

# 检查是否禁用SSL验证
DISABLE_SSL_VERIFY = os.getenv('LONGPORT_DISABLE_SSL_VERIFY', 'false').lower() == 'true'

# API基础URL
BASE_URLS = [
    "https://open-api.longportapp.com",
    "https://api-gateway.longportapp.com"
]

# 签名生成函数
def generate_signature(http_method, url_path, payload="", content_type=""):
    # 获取当前的UTC时间戳
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    
    # 解析URL路径
    parsed_url = urllib.parse.urlparse(url_path)
    path = parsed_url.path
    query = parsed_url.query
    
    # 构建签名字符串
    sign_str = f"{timestamp}{http_method.upper()}{path}"
    if query:
        sign_str += f"?{query}"
    
    # 添加内容类型和内容
    if content_type:
        sign_str += content_type
    if payload:
        sign_str += payload
        
    # 使用HMAC-SHA256生成签名
    signature = hmac.new(
        APP_SECRET.encode('utf-8'),
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).digest()
    
    # Base64编码
    signature_b64 = base64.b64encode(signature).decode('utf-8')
    
    return {
        'X-Api-Key': APP_KEY,
        'X-Api-Signature': signature_b64,
        'X-Api-Timestamp': str(timestamp),
        'Authorization': f'Bearer {ACCESS_TOKEN}'
    }

# API请求函数
def make_api_request(base_url, endpoint, method="GET", payload=None, content_type="application/json"):
    url = f"{base_url}{endpoint}"
    
    # 准备请求头
    headers = generate_signature(method, endpoint, json.dumps(payload) if payload else "", content_type)
    headers['Content-Type'] = content_type
    headers['Accept'] = 'application/json'
    
    logger.info(f"请求URL: {url}")
    logger.info(f"请求方法: {method}")
    logger.info(f"请求头: {headers}")
    if payload:
        logger.info(f"请求内容: {payload}")
    
    try:
        start_time = time.time()
        
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=10, verify=not DISABLE_SSL_VERIFY)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=payload, timeout=10, verify=not DISABLE_SSL_VERIFY)
        else:
            logger.error(f"不支持的请求方法: {method}")
            return None
        
        elapsed = time.time() - start_time
        
        logger.info(f"响应状态码: {response.status_code} (用时: {elapsed:.2f}秒)")
        logger.info(f"响应头: {dict(response.headers)}")
        
        try:
            response_data = response.json()
            logger.info(f"响应内容 (JSON): {json.dumps(response_data, indent=2)}")
        except json.JSONDecodeError:
            logger.info(f"响应内容 (非JSON): {response.text[:500]}...")
        
        return response
    
    except requests.exceptions.RequestException as e:
        logger.error(f"请求失败: {str(e)}")
        return None

# 测试API端点
def test_api_endpoints():
    # 检查是否设置了凭证
    if not APP_KEY or not APP_SECRET or not ACCESS_TOKEN:
        logger.error("缺少API凭证。请在.env文件中设置LONG_PORT_APP_KEY，LONG_PORT_APP_SECRET和LONG_PORT_ACCESS_TOKEN")
        return
    
    logger.info("===== 开始测试长桥API =====")
    logger.info(f"SSL验证已{'禁用' if DISABLE_SSL_VERIFY else '启用'}")
    
    endpoints = [
        # 行情API
        "/v1/hrmarket/trade-session",                # 获取各市场交易时段
        "/v1/quote/static-info",                     # 获取标的基础信息
        "/v1/quote/stocks",                          # 获取标的行情
        
        # 交易API
        "/v1/trade/order/history",                   # 获取历史订单
        "/v1/trade/order/today",                     # 获取当日订单
        "/v1/account/balance",                       # 获取账户资产
    ]
    
    # 请求参数示例
    payloads = {
        "/v1/quote/static-info": {
            "symbols": ["700.HK", "AAPL.US"]
        },
        "/v1/quote/stocks": {
            "symbols": ["700.HK", "AAPL.US"]
        }
    }
    
    # 测试每个基础URL
    for base_url in BASE_URLS:
        logger.info(f"\n===== 测试基础URL: {base_url} =====")
        
        # 测试每个端点
        for endpoint in endpoints:
            logger.info(f"\n----- 测试端点: {endpoint} -----")
            
            # 确定HTTP方法和请求内容
            method = "POST" if endpoint in payloads else "GET"
            payload = payloads.get(endpoint) if method == "POST" else None
            
            # 发送请求
            make_api_request(base_url, endpoint, method, payload)
            
            # 添加延迟以避免频率限制
            time.sleep(1)

if __name__ == "__main__":
    test_api_endpoints() 