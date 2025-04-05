"""
使用长桥数据训练预测模型

利用长桥API获取历史数据，训练机器学习模型进行股价预测
"""
import os
import sys
import logging
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from typing import Dict, List, Tuple, Any, Optional
import pickle
import json
from sklearn.preprocessing import MinMaxScaler
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

# 添加项目根目录到系统路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# 导入长桥API客户端
from longbridge_quant.api_client.client import LongPortClient
from longbridge_quant.data_engine.historical import HistoricalDataLoader
from longport.openapi import Period, AdjustType

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("model_training")

# 模型保存目录
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/models'))
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/training_data'))

# 确保目录存在
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

def fetch_historical_data(symbols: List[str], 
                          lookback_days: int = 365, 
                          period: str = 'day',
                          adjusted: bool = True) -> Dict[str, pd.DataFrame]:
    """
    从长桥API获取历史数据
    
    参数:
        symbols (List[str]): 股票代码列表
        lookback_days (int): 回溯天数
        period (str): 周期类型，day/week/month
        adjusted (bool): 是否使用复权数据
    
    返回:
        Dict[str, pd.DataFrame]: 股票代码到DataFrame的映射
    """
    logger.info(f"获取{len(symbols)}个股票的历史数据，回溯{lookback_days}天")
    
    # 创建长桥客户端
    client = LongPortClient()
    data_loader = HistoricalDataLoader(client)
    
    # 设置日期范围
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=lookback_days)
    
    # 设置周期类型
    period_map = {
        'day': Period.Day,
        'week': Period.Week,
        'month': Period.Month,
        '60m': Period.Min_60,
        '30m': Period.Min_30,
        '15m': Period.Min_15,
        '5m': Period.Min_5,
        '1m': Period.Min_1
    }
    longport_period = period_map.get(period, Period.Day)
    
    # 设置复权类型
    adjust_type = AdjustType.ForwardAdjust if adjusted else AdjustType.NoAdjust
    
    # 获取所有股票的历史数据
    result = {}
    for symbol in symbols:
        try:
            logger.info(f"获取 {symbol} 的历史数据")
            df = data_loader.get_bars(
                symbol=symbol,
                period=longport_period,
                start_date=start_date,
                end_date=end_date,
                adjust_type=adjust_type,
                use_cache=True
            )
            
            if df.empty:
                logger.warning(f"未获取到 {symbol} 的有效数据")
                continue
                
            # 设置日期索引
            if 'timestamp' in df.columns:
                df['date'] = pd.to_datetime(df['timestamp']).dt.date
                df = df.set_index('date')
                
            # 添加到结果
            result[symbol] = df
            logger.info(f"成功获取 {symbol} 的历史数据，共 {len(df)} 条记录")
            
            # 保存数据到CSV
            save_data_to_csv(symbol, df)
            
        except Exception as e:
            logger.error(f"获取 {symbol} 的历史数据时出错: {e}")
    
    return result

def save_data_to_csv(symbol: str, df: pd.DataFrame):
    """
    保存数据到CSV文件
    """
    # 替换符号中的特殊字符
    safe_symbol = symbol.replace(".", "_")
    file_path = os.path.join(DATA_DIR, f"{safe_symbol}.csv")
    
    try:
        df.to_csv(file_path)
        logger.info(f"成功保存 {symbol} 的数据到 {file_path}")
    except Exception as e:
        logger.error(f"保存 {symbol} 的数据时出错: {e}")

def load_data_from_csv(symbol: str) -> pd.DataFrame:
    """
    从CSV文件加载数据
    """
    # 替换符号中的特殊字符
    safe_symbol = symbol.replace(".", "_")
    file_path = os.path.join(DATA_DIR, f"{safe_symbol}.csv")
    
    if not os.path.exists(file_path):
        logger.warning(f"未找到 {symbol} 的数据文件: {file_path}")
        return pd.DataFrame()
    
    try:
        df = pd.read_csv(file_path, index_col=0, parse_dates=True)
        logger.info(f"成功加载 {symbol} 的数据，共 {len(df)} 条记录")
        return df
    except Exception as e:
        logger.error(f"加载 {symbol} 的数据时出错: {e}")
        return pd.DataFrame()

def generate_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    生成特征
    """
    if df.empty:
        logger.warning("无法为空数据生成特征")
        return df
    
    # 转换Decimal类型为float类型，避免后续计算错误
    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'turnover']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].astype(float)
    
    # 计算收益率
    df['return_1d'] = df['close'].pct_change()
    df['return_2d'] = df['close'].pct_change(2)
    df['return_5d'] = df['close'].pct_change(5)
    df['return_10d'] = df['close'].pct_change(10)
    df['return_20d'] = df['close'].pct_change(20)
    
    # 计算技术指标
    # 移动平均线
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma10'] = df['close'].rolling(window=10).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()
    df['ma60'] = df['close'].rolling(window=60).mean()
    
    # 波动率
    df['volatility_5d'] = df['return_1d'].rolling(window=5).std()
    df['volatility_10d'] = df['return_1d'].rolling(window=10).std()
    df['volatility_20d'] = df['return_1d'].rolling(window=20).std()
    
    # 相对强弱指标 (RSI)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['rsi_14'] = 100 - (100 / (1 + rs))
    
    # MACD
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    
    # 布林带
    df['ma20_std'] = df['close'].rolling(window=20).std()
    df['bollinger_upper'] = df['ma20'] + (df['ma20_std'] * 2)
    df['bollinger_lower'] = df['ma20'] - (df['ma20_std'] * 2)
    df['bollinger_pct'] = (df['close'] - df['bollinger_lower']) / (df['bollinger_upper'] - df['bollinger_lower'])
    
    # 成交量指标
    df['volume_ma5'] = df['volume'].rolling(window=5).mean()
    df['volume_ma10'] = df['volume'].rolling(window=10).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma5']
    
    # 加入目标变量：未来N天的收盘价变动百分比
    df['target_1d'] = df['close'].shift(-1) / df['close'] - 1  # 明天的收益率
    df['target_5d'] = df['close'].shift(-5) / df['close'] - 1  # 5天后的收益率
    df['target_10d'] = df['close'].shift(-10) / df['close'] - 1  # 10天后的收益率
    
    # 去除含有NaN的行
    df = df.dropna()
    
    return df

def prepare_train_test_data(df: pd.DataFrame, target_column: str = 'target_1d', test_size: float = 0.2) -> Tuple:
    """
    准备训练和测试数据
    """
    if df.empty:
        return None, None, None, None, None
    
    # 选择特征
    features = [
        'open', 'high', 'low', 'close', 'volume',
        'ma5', 'ma10', 'ma20', 'ma60',
        'return_1d', 'return_5d', 'return_10d',
        'volatility_5d', 'volatility_10d', 'volatility_20d',
        'rsi_14', 'macd', 'macd_signal', 'macd_hist',
        'bollinger_upper', 'bollinger_lower', 'bollinger_pct',
        'volume_ma5', 'volume_ma10', 'volume_ratio'
    ]
    
    # 提取特征和目标
    X = df[features]
    y = df[target_column]
    
    # 特征标准化
    scaler = MinMaxScaler()
    X_scaled = scaler.fit_transform(X)
    
    # 分割训练集和测试集
    X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=test_size, shuffle=False)
    
    return X_train, X_test, y_train, y_test, scaler

def train_random_forest_model(X_train, y_train, n_estimators=100, max_depth=None):
    """
    训练随机森林模型
    """
    model = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        random_state=42,
        n_jobs=-1
    )
    
    model.fit(X_train, y_train)
    return model

def evaluate_model(model, X_test, y_test):
    """
    评估模型性能
    """
    # 预测
    y_pred = model.predict(X_test)
    
    # 计算评估指标
    mse = mean_squared_error(y_test, y_pred)
    rmse = np.sqrt(mse)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)
    
    # 打印结果
    logger.info(f"模型评估结果:")
    logger.info(f"  MSE: {mse:.6f}")
    logger.info(f"  RMSE: {rmse:.6f}")
    logger.info(f"  MAE: {mae:.6f}")
    logger.info(f"  R²: {r2:.6f}")
    
    # 返回指标
    return {
        'mse': mse,
        'rmse': rmse,
        'mae': mae,
        'r2': r2,
        'y_test': y_test.tolist(),
        'y_pred': y_pred.tolist()
    }

def save_model(model, symbol: str, target: str, scaler, metrics, features=None):
    """
    保存模型和相关信息
    """
    # 创建以股票代码命名的目录
    safe_symbol = symbol.replace(".", "_")
    model_dir = os.path.join(MODEL_DIR, safe_symbol)
    os.makedirs(model_dir, exist_ok=True)
    
    # 构建文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{safe_symbol}_{target}_{timestamp}"
    
    # 保存模型
    model_path = os.path.join(model_dir, f"{base_filename}.pkl")
    with open(model_path, 'wb') as f:
        pickle.dump(model, f)
        
    # 保存缩放器
    scaler_path = os.path.join(model_dir, f"{base_filename}_scaler.pkl")
    with open(scaler_path, 'wb') as f:
        pickle.dump(scaler, f)
        
    # 保存特征列表和指标
    metadata = {
        'symbol': symbol,
        'target': target,
        'created_at': datetime.now().isoformat(),
        'features': features,
        'metrics': metrics
    }
    
    metadata_path = os.path.join(model_dir, f"{base_filename}_metadata.json")
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
        
    logger.info(f"模型和相关数据已保存到 {model_dir}")
    return model_dir

def load_model(model_path: str, scaler_path: str, metadata_path: str):
    """
    加载模型和相关信息
    """
    # 打印实际的文件路径，用于调试
    logger.info(f"尝试加载模型: {model_path}")
    logger.info(f"尝试加载缩放器: {scaler_path}")
    logger.info(f"尝试加载元数据: {metadata_path}")
    
    # 检查scaler路径中是否有重复的'scaler'文字，如果有，修复它
    if '_scaler_scaler' in scaler_path:
        logger.warning(f"检测到scaler路径错误，尝试修复: {scaler_path}")
        corrected_path = scaler_path.replace('_scaler_scaler', '_scaler')
        logger.info(f"修复后的路径: {corrected_path}")
        scaler_path = corrected_path
    
    # 首先检查文件是否存在
    for path, file_type in [(model_path, '模型'), (scaler_path, '缩放器'), (metadata_path, '元数据')]:
        if not os.path.exists(path):
            logger.error(f"{file_type}文件不存在: {path}")
            return None, None, None
            
    try:
        # 加载模型
        with open(model_path, 'rb') as f:
            model = pickle.load(f)
            
        # 加载缩放器
        with open(scaler_path, 'rb') as f:
            scaler = pickle.load(f)
            
        # 加载元数据
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)
            
        logger.info(f"成功加载模型: {model_path}")
        return model, scaler, metadata
    except Exception as e:
        logger.error(f"加载模型时出错: {e}")
        return None, None, None

def plot_prediction_vs_actual(y_test, y_pred, symbol, target):
    """
    绘制预测值与实际值的对比图
    """
    plt.figure(figsize=(12, 6))
    plt.plot(y_test.values, label='实际值', color='blue')
    plt.plot(y_pred, label='预测值', color='red', linestyle='--')
    plt.title(f'{symbol} - {target} 预测')
    plt.xlabel('样本')
    plt.ylabel('价格变动比例')
    plt.legend()
    plt.grid(True)
    
    # 计算相关系数
    corr = np.corrcoef(y_test.values, y_pred)[0, 1]
    plt.annotate(f'相关系数: {corr:.4f}', xy=(0.05, 0.95), xycoords='axes fraction')
    
    # 保存图片
    safe_symbol = symbol.replace(".", "_")
    img_dir = os.path.join(MODEL_DIR, safe_symbol, 'plots')
    os.makedirs(img_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_path = os.path.join(img_dir, f"{safe_symbol}_{target}_{timestamp}.png")
    plt.savefig(img_path)
    logger.info(f"预测图保存到: {img_path}")
    
    plt.close()

def train_model_for_symbol(symbol: str, target: str = 'target_1d', force_fetch: bool = False):
    """
    为指定股票训练模型
    """
    # 尝试从本地加载数据
    df = load_data_from_csv(symbol)
    
    # 如果没有数据或强制获取，从API获取
    if df.empty or force_fetch:
        data_dict = fetch_historical_data([symbol], lookback_days=365*2)
        if symbol in data_dict:
            df = data_dict[symbol]
        else:
            logger.error(f"无法获取 {symbol} 的数据")
            return None
    
    # 生成特征
    df = generate_features(df)
    
    if df.empty:
        logger.error(f"生成特征后数据为空")
        return None
    
    # 准备训练数据
    X_train, X_test, y_train, y_test, scaler = prepare_train_test_data(df, target_column=target)
    
    if X_train is None:
        logger.error(f"准备训练数据时出错")
        return None
    
    # 训练模型
    logger.info(f"开始训练 {symbol} 的 {target} 预测模型")
    model = train_random_forest_model(X_train, y_train, n_estimators=100, max_depth=10)
    
    # 评估模型
    metrics = evaluate_model(model, X_test, y_test)
    
    # 特征重要性
    feature_names = [
        'open', 'high', 'low', 'close', 'volume',
        'ma5', 'ma10', 'ma20', 'ma60',
        'return_1d', 'return_5d', 'return_10d',
        'volatility_5d', 'volatility_10d', 'volatility_20d',
        'rsi_14', 'macd', 'macd_signal', 'macd_hist',
        'bollinger_upper', 'bollinger_lower', 'bollinger_pct',
        'volume_ma5', 'volume_ma10', 'volume_ratio'
    ]
    
    feature_importance = sorted(zip(feature_names, model.feature_importances_), 
                              key=lambda x: x[1], reverse=True)
    
    logger.info("特征重要性排名:")
    for feature, importance in feature_importance[:10]:  # 只展示前10个
        logger.info(f"  {feature}: {importance:.4f}")
    
    # 保存模型
    save_model(model, symbol, target, scaler, metrics, feature_names)
    
    # 绘制预测图
    y_pred = model.predict(X_test)
    plot_prediction_vs_actual(y_test, y_pred, symbol, target)
    
    return model

def predict_next_movement(symbol: str, days: int = 1, model_dir: str = None):
    """
    预测下一个交易日的价格变动
    """
    # 加载最新数据
    df = load_data_from_csv(symbol)
    
    if df.empty:
        # 获取最新数据
        data_dict = fetch_historical_data([symbol], lookback_days=60)
        if symbol in data_dict:
            df = data_dict[symbol]
        else:
            logger.error(f"无法获取 {symbol} 的数据")
            return None
    
    # 生成特征
    df = generate_features(df)
    
    if df.empty:
        logger.error(f"生成特征后数据为空")
        return None
    
    # 根据预测天数选择目标
    if days == 1:
        target = 'target_1d'
    elif days == 5:
        target = 'target_5d'
    elif days == 10:
        target = 'target_10d'
    else:
        logger.error(f"不支持的预测天数: {days}")
        return None
    
    # 查找最新模型
    safe_symbol = symbol.replace(".", "_")
    if model_dir is None:
        model_dir = os.path.join(MODEL_DIR, safe_symbol)
    
    if not os.path.exists(model_dir):
        logger.error(f"未找到 {symbol} 的模型目录")
        return None
    
    # 查找最新的模型文件，排除scaler和metadata文件
    model_files = [f for f in os.listdir(model_dir) if f.endswith('.pkl') and target in f and not ('_scaler.pkl' in f or '_metadata.json' in f)]
    if not model_files:
        logger.error(f"未找到 {symbol} 的 {target} 模型")
        return None
    
    # 按文件名排序，获取最新的模型
    latest_model_file = sorted(model_files)[-1]
    model_path = os.path.join(model_dir, latest_model_file)
    
    # 获取相应的scaler和metadata文件
    base_name = latest_model_file.rsplit('.', 1)[0]
    scaler_path = os.path.join(model_dir, f"{base_name}_scaler.pkl")
    metadata_path = os.path.join(model_dir, f"{base_name}_metadata.json")
    
    # 加载模型
    model, scaler, metadata = load_model(model_path, scaler_path, metadata_path)
    
    if model is None:
        logger.error(f"加载模型失败")
        return None
    
    # 准备最新的特征数据
    features = metadata.get('features', [
        'open', 'high', 'low', 'close', 'volume',
        'ma5', 'ma10', 'ma20', 'ma60',
        'return_1d', 'return_5d', 'return_10d',
        'volatility_5d', 'volatility_10d', 'volatility_20d',
        'rsi_14', 'macd', 'macd_signal', 'macd_hist',
        'bollinger_upper', 'bollinger_lower', 'bollinger_pct',
        'volume_ma5', 'volume_ma10', 'volume_ratio'
    ])
    
    latest_data = df.dropna().iloc[-1][features].values.reshape(1, -1)
    
    # 预测
    scaled_data = scaler.transform(latest_data)
    prediction = model.predict(scaled_data)[0]
    
    # 获取最新价格
    latest_price = df.iloc[-1]['close']
    predicted_price = latest_price * (1 + prediction)
    
    # 返回预测结果
    result = {
        'symbol': symbol,
        'date': df.index[-1].strftime('%Y-%m-%d'),
        'current_price': latest_price,
        'predicted_change_pct': prediction * 100,  # 转为百分比
        'predicted_price': predicted_price,
        'days': days
    }
    
    logger.info(f"预测结果: {symbol} 在 {days} 天后的预测价格变动为 {prediction*100:.2f}%")
    logger.info(f"当前价格: {latest_price:.2f}, 预测价格: {predicted_price:.2f}")
    
    return result

def parse_args():
    """
    解析命令行参数
    """
    parser = argparse.ArgumentParser(description="股票价格预测模型训练工具")
    
    parser.add_argument("--symbol", type=str, default="700.HK", 
                        help="股票代码，默认为腾讯控股(700.HK)")
    parser.add_argument("--target", type=str, choices=['1d', '5d', '10d'], default='1d',
                        help="预测目标，可选1天/5天/10天后")
    parser.add_argument("--mode", type=str, choices=['train', 'predict'], default='train',
                        help="模式：训练或预测")
    parser.add_argument("--force-fetch", action="store_true", 
                        help="强制从API获取最新数据")
    
    return parser.parse_args()

def main():
    """
    主函数
    """
    args = parse_args()
    
    # 设置目标字段
    target_map = {
        '1d': 'target_1d',
        '5d': 'target_5d',
        '10d': 'target_10d'
    }
    target = target_map[args.target]
    days = int(args.target.replace('d', ''))
    
    # 根据模式执行相应操作
    if args.mode == 'train':
        logger.info(f"开始训练 {args.symbol} 的 {args.target} 预测模型")
        train_model_for_symbol(args.symbol, target, args.force_fetch)
    else:
        logger.info(f"预测 {args.symbol} 在未来 {args.target} 的价格变动")
        result = predict_next_movement(args.symbol, days)
        if result:
            print(json.dumps(result, indent=2))
    
if __name__ == "__main__":
    main() 