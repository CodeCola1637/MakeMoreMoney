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

# 全局配置
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/models'))
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/training_data'))
PRED_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/predictions'))

# 确保目录存在
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PRED_DIR, exist_ok=True)

def fetch_historical_data(symbols: List[str], 
                          lookback_days: int = 365*3, 
                          period: str = 'day',
                          adjusted: bool = True) -> Dict[str, pd.DataFrame]:
    """
    从长桥API获取历史数据
    
    参数:
        symbols (List[str]): 股票代码列表
        lookback_days (int): 回溯天数，默认3年
        period (str): 周期类型，day/week/month
        adjusted (bool): 是否使用复权数据
    
    返回:
        Dict[str, pd.DataFrame]: 股票代码到DataFrame的映射
    """
    logger.info(f"获取{len(symbols)}个股票的历史数据，回溯{lookback_days}天 (约{lookback_days/365:.1f}年)")
    
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
            logger.info(f"获取 {symbol} 的历史数据，从 {start_date} 到 {end_date}")
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
    生成特征 - 增强版，适应数据量较小的情况
    """
    if df.empty:
        logger.warning("无法为空数据生成特征")
        return df
    
    original_length = len(df)
    logger.info(f"开始特征工程，原始数据长度: {original_length}")
    
    # 转换Decimal类型为float类型，避免后续计算错误
    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'turnover']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].astype(float)
    
    # 1. 价格特征 - 根据数据长度调整窗口
    # 计算收益率
    df['return_1d'] = df['close'].pct_change()
    
    # 仅在数据量足够的情况下计算较长时间收益率
    if len(df) >= 3:
        df['return_2d'] = df['close'].pct_change(2)
    if len(df) >= 6:
        df['return_5d'] = df['close'].pct_change(5)
    if len(df) >= 11:
        df['return_10d'] = df['close'].pct_change(10)
    if len(df) >= 21:
        df['return_20d'] = df['close'].pct_change(20)
    
    # 价格与移动平均的关系 - 动态调整窗口
    if len(df) >= 5:
        df['ma5'] = df['close'].rolling(window=min(5, len(df))).mean()
        df['price_rel_ma5'] = df['close'] / df['ma5'] - 1
    
    if len(df) >= 10:
        df['ma10'] = df['close'].rolling(window=10).mean()
        df['price_rel_ma10'] = df['close'] / df['ma10'] - 1
        
        # 仅在有足够数据时计算均线交叉信号
        if 'ma5' in df.columns:
            df['ma_5_10_cross'] = np.where(df['ma5'] > df['ma10'], 1, -1)
    
    if len(df) >= 20:
        df['ma20'] = df['close'].rolling(window=20).mean()
        
        # 添加相关衍生指标
        if 'ma5' in df.columns:
            df['ma_5_20_cross'] = np.where(df['ma5'] > df['ma20'], 1, -1)
            df['trend_strength'] = abs(df['ma5'] / df['ma20'] - 1) * 100
    
    # 价格区间特征
    df['daily_range'] = (df['high'] - df['low']) / df['close']
    if 'close' in df.columns and len(df) > 1:
        df['gap_up'] = (df['open'] - df['close'].shift(1)) / df['close'].shift(1)
    
    # 2. 波动率特征 - 根据数据量调整窗口
    min_window_size = min(5, len(df) - 1)
    if min_window_size >= 2 and 'return_1d' in df.columns:  # 确保至少有2个数据点计算标准差
        df['volatility_5d'] = df['return_1d'].rolling(window=min_window_size).std()
    
    if len(df) >= 10 and 'return_1d' in df.columns:
        df['volatility_10d'] = df['return_1d'].rolling(window=10).std()
    
    # 3. 技术指标 - 只在数据足够的情况下添加
    # RSI
    if len(df) >= 7:  # 确保至少有足够数据计算短期RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=min(6, len(df)-1)).mean()
        avg_loss = loss.rolling(window=min(6, len(df)-1)).mean()
        rs = avg_gain / avg_loss
        df['rsi_6'] = 100 - (100 / (1 + rs))
    
    # 加入目标变量：未来N天的收盘价变动百分比
    df['target_1d'] = df['close'].shift(-1) / df['close'] - 1  # 明天的收益率
    if len(df) >= 6:
        df['target_5d'] = df['close'].shift(-5) / df['close'] - 1  # 5天后的收益率
    if len(df) >= 11:
        df['target_10d'] = df['close'].shift(-10) / df['close'] - 1  # 10天后的收益率
    
    # 去除含有NaN的行
    df_clean = df.dropna()
    logger.info(f"特征生成后数据长度: {len(df_clean)}, 减少了 {original_length - len(df_clean)} 行")
    
    return df_clean

def prepare_train_test_data(df: pd.DataFrame, target_column: str = 'target_1d', test_size: float = 0.2, 
                        feature_selection: bool = True) -> Tuple:
    """
    准备训练和测试数据
    
    参数:
        df: 包含特征和目标的DataFrame
        target_column: 目标列名
        test_size: 测试集比例
        feature_selection: 是否进行特征选择
    """
    if df.empty:
        return None, None, None, None, None, None
    
    # 排除目标变量
    exclude_cols = ['target_1d', 'target_5d', 'target_10d']
    
    # 只选择数值型列作为特征
    numeric_cols = df.select_dtypes(include=['number']).columns
    base_features = [col for col in numeric_cols if col not in exclude_cols]
    
    logger.info(f"共找到 {len(base_features)} 个数值型特征")
    
    # 移除高度相关的特征
    if feature_selection and len(base_features) > 10:
        # 计算特征相关性矩阵
        corr_matrix = df[base_features].corr().abs()
        
        # 找出高度相关的特征对
        upper = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
        to_drop = [column for column in upper.columns if any(upper[column] > 0.95)]
        
        logger.info(f"移除 {len(to_drop)} 个高度相关的特征: {to_drop}")
        features = [f for f in base_features if f not in to_drop]
    else:
        features = base_features
    
    # 最终特征列表
    logger.info(f"使用 {len(features)} 个特征进行训练")
    if len(features) > 10:
        logger.info(f"前10个特征: {features[:10]}")
    else:
        logger.info(f"特征: {features}")
    
    # 处理特征和目标
    X = df[features]
    y = df[target_column]
    
    # 特征标准化
    scaler = MinMaxScaler()
    X_scaled = scaler.fit_transform(X)
    
    # 分割训练集和测试集 - 使用时间序列分割更合适
    split_idx = int(len(df) * (1 - test_size))
    X_train, X_test = X_scaled[:split_idx], X_scaled[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
    
    logger.info(f"训练集大小: {X_train.shape}, 测试集大小: {X_test.shape}")
    
    return X_train, X_test, y_train, y_test, scaler, features

def train_random_forest_model(X_train, y_train, tune_hyperparams=True, n_estimators=100, max_depth=None):
    """
    训练随机森林模型，可选使用网格搜索和交叉验证进行超参数优化
    
    参数:
        X_train: 训练特征
        y_train: 训练目标
        tune_hyperparams: 是否进行超参数调优
        n_estimators: 树的数量（如不调参时使用）
        max_depth: 树的最大深度（如不调参时使用）
    """
    from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
    
    if tune_hyperparams:
        # 定义参数网格
        param_grid = {
            'n_estimators': [50, 100, 200],
            'max_depth': [None, 10, 20, 30],
            'min_samples_split': [2, 5, 10],
            'min_samples_leaf': [1, 2, 4],
            'max_features': ['sqrt', 'log2', None]
        }
        
        # 创建基础模型
        base_model = RandomForestRegressor(random_state=42, n_jobs=-1)
        
        # 创建时间序列交叉验证对象
        tscv = TimeSeriesSplit(n_splits=5)
        
        # 创建网格搜索
        grid_search = GridSearchCV(
            estimator=base_model,
            param_grid=param_grid,
            cv=tscv,
            n_jobs=-1,
            scoring='neg_mean_squared_error',
            verbose=1
        )
        
        # 执行网格搜索
        logger.info("开始网格搜索超参数优化...")
        grid_search.fit(X_train, y_train)
        
        # 获取最佳参数和模型
        best_params = grid_search.best_params_
        logger.info(f"最佳参数: {best_params}")
        
        model = grid_search.best_estimator_
    else:
        # 使用指定参数创建模型
        model = RandomForestRegressor(
            n_estimators=n_estimators,
            max_depth=max_depth,
            random_state=42,
            n_jobs=-1
        )
        model.fit(X_train, y_train)
    
    return model

def train_model_with_multiple_algorithms(X_train, X_test, y_train, y_test):
    """
    使用多种算法训练模型并比较性能（仅使用sklearn中的模型）
    """
    from sklearn.ensemble import GradientBoostingRegressor, AdaBoostRegressor, ExtraTreesRegressor
    from sklearn.svm import SVR
    from sklearn.linear_model import ElasticNet, Ridge, Lasso
    from sklearn.neural_network import MLPRegressor
    
    # 定义要测试的模型
    models = {
        'RandomForest': RandomForestRegressor(n_estimators=100, random_state=42),
        'GradientBoosting': GradientBoostingRegressor(n_estimators=100, random_state=42),
        'ExtraTrees': ExtraTreesRegressor(n_estimators=100, random_state=42),
        'AdaBoost': AdaBoostRegressor(n_estimators=50, random_state=42),
        'ElasticNet': ElasticNet(random_state=42, alpha=0.1),
        'Ridge': Ridge(alpha=1.0, random_state=42),
        'Lasso': Lasso(alpha=0.1, random_state=42),
        'SVR': SVR(kernel='rbf', C=1.0, gamma='scale'),
        'MLP': MLPRegressor(hidden_layer_sizes=(50, 25), max_iter=1000, random_state=42)
    }
    
    # 评估每个模型
    results = {}
    best_model = None
    best_score = float('-inf')
    
    for name, model in models.items():
        try:
            logger.info(f"训练 {name} 模型...")
            model.fit(X_train, y_train)
            
            # 预测和评估
            y_pred = model.predict(X_test)
            mse = mean_squared_error(y_test, y_pred)
            rmse = np.sqrt(mse)
            mae = mean_absolute_error(y_test, y_pred)
            r2 = r2_score(y_test, y_pred)
            
            # 保存结果
            results[name] = {
                'mse': mse,
                'rmse': rmse,
                'mae': mae,
                'r2': r2,
                'model': model
            }
            
            logger.info(f"{name} - RMSE: {rmse:.6f}, R²: {r2:.6f}")
            
            # 更新最佳模型
            if r2 > best_score:
                best_score = r2
                best_model = model
                
        except Exception as e:
            logger.error(f"{name} 训练失败: {e}")
    
    # 输出所有模型的比较结果
    logger.info("\n模型性能比较:")
    logger.info("-" * 50)
    logger.info(f"{'模型名称':<15} {'RMSE':<10} {'MAE':<10} {'R²':<10}")
    logger.info("-" * 50)
    
    for name, result in results.items():
        logger.info(f"{name:<15} {result['rmse']:<10.6f} {result['mae']:<10.6f} {result['r2']:<10.6f}")
    
    logger.info("-" * 50)
    
    if results:
        best_model_name = max(results.items(), key=lambda x: x[1]['r2'])[0]
        logger.info(f"最佳模型: {best_model_name}")
    else:
        logger.error("没有成功训练任何模型")
    
    return best_model, results

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

def train_model_for_symbol(symbol: str, target: str = 'target_1d', force_fetch: bool = False, 
                     use_multiple_models: bool = True, tune_hyperparams: bool = True, lookback_days: int = 365*3):
    """
    为指定股票训练模型
    
    参数:
        symbol: 股票代码
        target: 目标列名
        force_fetch: 是否强制获取新数据
        use_multiple_models: 是否使用多种算法进行比较
        tune_hyperparams: 是否进行超参数调优
        lookback_days: 回溯天数
    """
    # 尝试从本地加载数据
    df = load_data_from_csv(symbol)
    
    # 如果没有数据或强制获取，从API获取3年数据
    if df.empty or force_fetch:
        data_dict = fetch_historical_data([symbol], lookback_days=lookback_days)
        if symbol in data_dict:
            df = data_dict[symbol]
        else:
            logger.error(f"无法获取 {symbol} 的数据")
            return None
    
    # 检查数据量是否足够
    if len(df) < 200:
        logger.warning(f"{symbol} 的数据量不足 ({len(df)} 行)，建议获取更多数据")
        
    # 生成增强特征
    logger.info("生成增强特征...")
    df = generate_features(df)
    
    if df.empty:
        logger.error("生成特征后数据为空")
        return None
    
    # 准备训练数据
    logger.info("准备训练和测试数据...")
    train_data = prepare_train_test_data(df, target_column=target, feature_selection=True)
    
    if train_data is None or len(train_data) < 6:
        logger.error("准备训练数据时出错")
        return None
    
    X_train, X_test, y_train, y_test, scaler, features = train_data
    
    # 训练模型
    logger.info(f"开始训练 {symbol} 的 {target} 预测模型")
    
    if use_multiple_models:
        # 使用多种算法训练并比较
        model, model_results = train_model_with_multiple_algorithms(X_train, X_test, y_train, y_test)
        
        # 获取最佳模型名称
        best_model_name = max(model_results.items(), key=lambda x: x[1]['r2'])[0]
        best_metrics = model_results[best_model_name]
        
        # 获取评估指标
        metrics = {
            'mse': best_metrics['mse'],
            'rmse': best_metrics['rmse'],
            'mae': best_metrics['mae'],
            'r2': best_metrics['r2'],
            'y_test': y_test.tolist(),
            'y_pred': model.predict(X_test).tolist()
        }
    else:
        # 只训练随机森林模型
        model = train_random_forest_model(X_train, y_train, tune_hyperparams=tune_hyperparams)
        
        # 评估模型
        y_pred = model.predict(X_test)
        mse = mean_squared_error(y_test, y_pred)
        metrics = {
            'mse': mse,
            'rmse': np.sqrt(mse),
            'mae': mean_absolute_error(y_test, y_pred),
            'r2': r2_score(y_test, y_pred),
            'y_test': y_test.tolist(),
            'y_pred': y_pred.tolist()
        }
    
    # 如果是随机森林，计算特征重要性
    if hasattr(model, 'feature_importances_'):
        feature_importance = sorted(zip(features, model.feature_importances_), 
                                  key=lambda x: x[1], reverse=True)
        
        logger.info("特征重要性排名 (前20个):")
        for feature, importance in feature_importance[:20]:
            logger.info(f"  {feature}: {importance:.4f}")
    
    # 保存模型
    save_model(model, symbol, target, scaler, metrics, features)
    
    # 绘制预测图
    y_pred = model.predict(X_test)
    plot_prediction_vs_actual(y_test, y_pred, symbol, target)
    
    return model

def predict_next_movement(symbol: str, days: int = 1, model_dir: str = None, force_fetch: bool = True, new_model: bool = False):
    """
    预测下一个交易日的价格变动
    
    参数:
        symbol: 股票代码
        days: 预测天数
        model_dir: 模型目录
        force_fetch: 是否强制获取最新数据
        new_model: 是否重新训练新模型（用于当特征不匹配时）
    """
    if force_fetch:
        # 强制获取最新数据
        logger.info(f"强制获取 {symbol} 的最新数据...")
        # 预测时增加回溯天数，确保有足够的数据计算技术指标
        data_dict = fetch_historical_data([symbol], lookback_days=180)
        if symbol in data_dict:
            df = data_dict[symbol]
        else:
            logger.error(f"无法获取 {symbol} 的最新数据")
            return None
    else:
        # 加载本地数据
        df = load_data_from_csv(symbol)
        
        if df.empty:
            # 如果本地没有数据，获取最新数据
            data_dict = fetch_historical_data([symbol], lookback_days=180)
            if symbol in data_dict:
                df = data_dict[symbol]
            else:
                logger.error(f"无法获取 {symbol} 的数据")
                return None
    
    # 确保日期是正确的 - 检查df的索引格式
    if isinstance(df.index, pd.DatetimeIndex):
        latest_date = df.index[-1]
        latest_date_str = latest_date.strftime('%Y-%m-%d')
    else:
        try:
            # 尝试将日期列转换为日期类型
            if 'date' in df.columns:
                latest_date = pd.to_datetime(df['date'].iloc[-1])
                latest_date_str = latest_date.strftime('%Y-%m-%d')
            elif 'timestamp' in df.columns:
                latest_date = pd.to_datetime(df['timestamp'].iloc[-1])
                latest_date_str = latest_date.strftime('%Y-%m-%d')
            else:
                # 如果没有明确的日期列，使用当前日期
                latest_date = datetime.now()
                latest_date_str = latest_date.strftime('%Y-%m-%d')
        except:
            # 如果转换失败，使用当前日期
            latest_date = datetime.now()
            latest_date_str = latest_date.strftime('%Y-%m-%d')
    
    # 保存原始数据中的最新价格，确保使用正确的价格进行预测
    if 'close' in df.columns:
        # 确保价格是float类型
        latest_raw_price = float(df['close'].iloc[-1])
        logger.info(f"原始数据中的最新价格: {latest_raw_price}")
    else:
        logger.error("数据中没有close列，无法获取价格")
        return None
    
    logger.info(f"数据最新日期: {latest_date_str}")
    logger.info(f"数据总量: {len(df)} 条")
    
    # 生成特征
    logger.info("生成预测所需特征...")
    df_features = generate_features(df)
    
    if df_features.empty:
        logger.error(f"生成特征后数据为空，尝试使用简化特征重新生成")
        # 尝试使用简化特征再次生成
        df_features = generate_simplified_features(df)
        
        if df_features.empty:
            logger.error("即使使用简化特征也无法生成有效数据，无法进行预测")
            return None
    
    logger.info(f"特征生成完成，可用数据: {len(df_features)} 条")
    
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
    
    # 如果选择重新训练新模型，直接训练并预测
    if new_model:
        logger.info("选择重新训练新模型，使用当前特征集...")
        
        # 1. 准备训练数据 - 过滤不需要的列
        # 只保留数值型特征
        numeric_columns = df_features.select_dtypes(include=['number']).columns.tolist()
        
        # 确保目标变量在列表中
        if target not in numeric_columns and target in df_features.columns:
            numeric_columns.append(target)
            
        logger.info(f"筛选出 {len(numeric_columns)} 个数值型特征")
        
        # 排除目标变量中不需要的列
        targets_to_exclude = []
        for col in numeric_columns:
            if col.startswith('target_') and col != target:
                targets_to_exclude.append(col)
        
        # 创建特征数据集和目标变量
        feature_columns = [col for col in numeric_columns if col not in targets_to_exclude and col != target]
        X_data = df_features[feature_columns]
        y_data = df_features[target]
        
        logger.info(f"特征数量: {X_data.shape[1]}")
        logger.info(f"特征列表: {feature_columns[:10]}...")
        
        # 2. 保存特征和最新数据供预测使用
        latest_data = X_data.iloc[-1].copy()
        
        # 3. 数据分割
        test_size = 0.2
        train_size = int(len(X_data) * (1 - test_size))
        
        X_train = X_data.iloc[:train_size]
        X_test = X_data.iloc[train_size:]
        y_train = y_data.iloc[:train_size]
        y_test = y_data.iloc[train_size:]
        
        logger.info(f"训练集大小: {X_train.shape}, 测试集大小: {X_test.shape}")
        
        # 4. 数据缩放
        scaler = MinMaxScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        # 5. 训练模型
        logger.info("使用随机森林算法训练模型...")
        model = RandomForestRegressor(n_estimators=100, random_state=42)
        model.fit(X_train_scaled, y_train)
        
        # 6. 评估模型
        y_pred = model.predict(X_test_scaled)
        
        mse = mean_squared_error(y_test, y_pred)
        metrics = {
            'mse': mse,
            'rmse': np.sqrt(mse),
            'mae': mean_absolute_error(y_test, y_pred),
            'r2': r2_score(y_test, y_pred)
        }
        
        logger.info(f"模型评估结果: RMSE={metrics['rmse']:.6f}, MAE={metrics['mae']:.6f}, R²={metrics['r2']:.6f}")
        
        # 如果模型表现不好，记录警告
        if metrics['r2'] < 0.1:
            logger.warning(f"模型预测性能较差 (R²={metrics['r2']:.6f})，预测结果可能不可靠")
        
        # 7. 使用模型进行预测
        try:
            # 准备最新数据
            latest_features = latest_data.values.reshape(1, -1)
            
            # 缩放数据
            latest_scaled = scaler.transform(latest_features)
            
            # 预测
            prediction = model.predict(latest_scaled)[0]
            
            # 使用原始数据中的最新价格，而不是特征生成后的价格
            latest_price = latest_raw_price  # 已经确保是float类型
            predicted_price = latest_price * (1 + prediction)
            
            # 获取交易日期 - 预测的是下一个交易日
            next_trading_date = datetime.now() + timedelta(days=1)  # 简单假设下一个交易日是明天
            next_date_str = next_trading_date.strftime('%Y-%m-%d')
            
            # 返回预测结果
            result = {
                'symbol': symbol,
                'current_date': latest_date_str,
                'predict_date': next_date_str,
                'current_price': float(latest_price),
                'predicted_change_pct': float(prediction * 100),  # 转为百分比
                'predicted_price': float(predicted_price),
                'days': days,
                'model': 'newly_trained',
                'metrics': {
                    'rmse': float(metrics['rmse']),
                    'r2': float(metrics['r2'])
                }
            }
            
            logger.info(f"预测结果: {symbol} 在 {days} 天后的预测价格变动为 {prediction*100:.2f}%")
            logger.info(f"当前价格: {latest_price:.2f}, 预测价格: {predicted_price:.2f}")
            
            return result
        except Exception as e:
            logger.error(f"使用新训练模型预测过程中出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
    features = metadata.get('features', [])
    if not features:
        logger.error("模型元数据中没有特征信息")
        return None
    
    # 确保所有所需特征都在数据中
    missing_features = [f for f in features if f not in df_features.columns]
    if missing_features:
        logger.warning(f"数据中缺少以下特征: {missing_features}")
        logger.warning(f"特征不匹配！模型需要 {len(features)} 个特征，但当前数据只有 {len(df_features.columns) - len(missing_features)} 个匹配特征")
        
        # 如果特征差异过大，建议重新训练
        if len(missing_features) > len(features) * 0.3:  # 如果缺失超过30%的特征
            logger.warning("特征差异过大，建议使用 --new-model 选项重新训练模型")
            return predict_next_movement(symbol, days, model_dir, force_fetch, new_model=True)
        
        logger.warning("使用现有特征进行预测...")
        features = [f for f in features if f in df_features.columns]
        
    if not features:
        logger.error("没有可用特征进行预测")
        return None
    
    try:
        logger.info(f"使用以下特征进行预测: {features}")
        latest_data = df_features.dropna().iloc[-1][features].values.reshape(1, -1)
        
        # 预测
        scaled_data = scaler.transform(latest_data)
        prediction = model.predict(scaled_data)[0]
        
        # 使用原始数据中的最新价格，而不是特征数据
        latest_price = latest_raw_price  # 已经确保是float类型
        predicted_price = latest_price * (1 + prediction)
        
        # 获取最新日期和预测日期
        next_trading_date = datetime.now() + timedelta(days=1)  # 简单假设下一个交易日是明天
        next_date_str = next_trading_date.strftime('%Y-%m-%d')
        
        # 返回预测结果
        result = {
            'symbol': symbol,
            'current_date': latest_date_str,
            'predict_date': next_date_str,
            'current_price': float(latest_price),
            'predicted_change_pct': float(prediction * 100),  # 转为百分比
            'predicted_price': float(predicted_price),
            'days': days,
            'model': 'existing'
        }
        
        logger.info(f"预测结果: {symbol} 在 {days} 天后的预测价格变动为 {prediction*100:.2f}%")
        logger.info(f"当前价格: {latest_price:.2f}, 预测价格: {predicted_price:.2f}")
        
        return result
    except Exception as e:
        logger.error(f"预测过程中出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        # 如果是因为特征维度不匹配导致的错误，尝试重新训练模型
        if "features, but" in str(e) and "features as input" in str(e):
            logger.warning("特征维度不匹配，尝试重新训练模型...")
            return predict_next_movement(symbol, days, model_dir, force_fetch, new_model=True)
        
        return None

def generate_simplified_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    生成简化版特征，适用于数据量较小的情况
    """
    if df.empty:
        logger.warning("无法为空数据生成特征")
        return df
    
    # 转换Decimal类型为float类型，避免后续计算错误
    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'turnover']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].astype(float)
    
    # 1. 核心价格特征 - 使用较小的窗口
    # 计算收益率
    df['return_1d'] = df['close'].pct_change()
    
    # 较短时间的移动平均线
    if len(df) >= 5:
        df['ma5'] = df['close'].rolling(window=5).mean()
        df['price_rel_ma5'] = df['close'] / df['ma5'] - 1
    
    # 2. 仅计算短期波动率
    if len(df) >= 5:
        df['volatility_5d'] = df['return_1d'].rolling(window=5).std()
    
    # 3. 最少的技术指标
    if len(df) >= 6:
        # 短期RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=6).mean()
        avg_loss = loss.rolling(window=6).mean()
        rs = avg_gain / avg_loss
        df['rsi_6'] = 100 - (100 / (1 + rs))
    
    # 简化的价格区间指标
    df['daily_range'] = (df['high'] - df['low']) / df['close']
    
    # 加入目标变量：未来N天的收盘价变动百分比
    df['target_1d'] = df['close'].shift(-1) / df['close'] - 1  # 明天的收益率
    
    # 保留原始价格数据
    df = df.dropna()
    
    return df

def parse_args():
    """
    解析命令行参数
    """
    parser = argparse.ArgumentParser(description='股票价格预测模型训练与预测')
    parser.add_argument('--symbol', type=str, help='股票代码')
    parser.add_argument('--target', type=str, default='1d', help='预测目标天数 (1d, 5d, 10d)')
    parser.add_argument('--mode', type=str, default='train', help='模式: train 或 predict')
    parser.add_argument('--force-fetch', action='store_true', help='强制获取最新数据')
    parser.add_argument('--multi-model', action='store_true', help='使用多种模型进行比较')
    parser.add_argument('--new-model', action='store_true', help='为预测重新训练新模型（适用于特征不匹配的情况）')
    
    return parser.parse_args()

def main():
    """
    主函数
    """
    args = parse_args()
    symbol = args.symbol
    
    if symbol is None:
        logger.error("请提供股票代码，例如: --symbol 700.HK")
        return
    
    # 检查模式
    if args.mode == 'train':
        # 训练模式
        if args.target == '1d':
            target_column = 'target_1d'
        elif args.target == '5d':
            target_column = 'target_5d'
        elif args.target == '10d':
            target_column = 'target_10d'
        else:
            logger.error(f"不支持的预测目标: {args.target}")
            return
        
        logger.info(f"开始为 {symbol} 训练 {args.target} 预测模型")
        logger.info(f"使用增强的特征工程和{'多种算法比较' if args.multi_model else '单一模型'}")
        
        # 设置回溯天数为3年
        lookback_years = 3
        lookback_days = lookback_years * 365
        logger.info(f"数据回溯 {lookback_years} 年, 超参数调优: 关闭")
        
        # 获取历史数据
        data_dict = {}
        if args.force_fetch:
            logger.info(f"强制获取 {symbol} 的历史数据...")
            data_dict = fetch_historical_data([symbol], lookback_days=lookback_days)
        else:
            # 加载本地数据
            df = load_data_from_csv(symbol)
            if not df.empty:
                data_dict[symbol] = df
                logger.info(f"成功加载 {symbol} 的本地数据，共 {len(df)} 条记录")
            else:
                logger.info(f"未找到 {symbol} 的本地数据，获取历史数据...")
                data_dict = fetch_historical_data([symbol], lookback_days=lookback_days)
        
        if symbol in data_dict:
            df = data_dict[symbol]
            logger.info(f"成功加载 {symbol} 的历史数据，共 {len(df)} 条记录")
            
            # 生成特征
            df = generate_features(df)
            
            if df.empty:
                logger.error(f"生成特征后数据为空")
                return
                
            # 准备训练数据
            logger.info("准备训练和测试数据...")
            train_data = prepare_train_test_data(df, target_column=target_column, feature_selection=True)
            
            if train_data is None or len(train_data) < 6:
                logger.error("准备训练数据时出错")
                return
                
            X_train, X_test, y_train, y_test, scaler, features = train_data
            
            # 训练模型
            if args.multi_model:
                # 使用多种算法训练并比较
                model, model_results = train_model_with_multiple_algorithms(X_train, X_test, y_train, y_test)
                
                # 获取最佳模型
                if model is not None:
                    # 获取最佳模型的预测
                    y_pred = model.predict(X_test)
                    metrics = {
                        'mse': mean_squared_error(y_test, y_pred),
                        'rmse': np.sqrt(mean_squared_error(y_test, y_pred)),
                        'mae': mean_absolute_error(y_test, y_pred),
                        'r2': r2_score(y_test, y_pred),
                        'y_test': y_test.tolist(),
                        'y_pred': y_pred.tolist()
                    }
                    
                    # 保存模型
                    save_model(model, symbol, target_column, scaler, metrics, features)
                    
                    # 绘制预测图
                    plot_prediction_vs_actual(y_test, y_pred, symbol, target_column)
            else:
                # 使用随机森林算法
                model = train_random_forest_model(X_train, y_train, tune_hyperparams=False)
                
                # 评估模型
                y_pred = model.predict(X_test)
                metrics = {
                    'mse': mean_squared_error(y_test, y_pred),
                    'rmse': np.sqrt(mean_squared_error(y_test, y_pred)),
                    'mae': mean_absolute_error(y_test, y_pred),
                    'r2': r2_score(y_test, y_pred),
                    'y_test': y_test.tolist(),
                    'y_pred': y_pred.tolist()
                }
                
                # 保存模型
                save_model(model, symbol, target_column, scaler, metrics, features)
                
                # 绘制预测图
                plot_prediction_vs_actual(y_test, y_pred, symbol, target_column)
                
        else:
            logger.error(f"无法获取 {symbol} 的历史数据")
            return
    
    elif args.mode == 'predict':
        # 预测模式
        days = 1
        if args.target == '1d':
            days = 1
        elif args.target == '5d':
            days = 5
        elif args.target == '10d':
            days = 10
        else:
            logger.error(f"不支持的预测目标: {args.target}")
            return
            
        logger.info(f"预测 {symbol} 在未来 {args.target} 的价格变动")
        result = predict_next_movement(symbol, days, force_fetch=args.force_fetch, new_model=args.new_model)
        
        if result:
            movement = result['predicted_change_pct']
            if movement > 0:
                suggestion = "买入 📈"
            elif movement < 0:
                suggestion = "卖出 📉"
            else:
                suggestion = "持有 ↔️"
                
            logger.info(f"投资建议: {suggestion}")
            
            # 保存预测结果
            safe_symbol = symbol.replace(".", "_")
            pred_dir = os.path.join(PRED_DIR, safe_symbol)
            os.makedirs(pred_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            pred_file = os.path.join(pred_dir, f"{safe_symbol}_pred_{args.target}_{timestamp}.json")
            
            with open(pred_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
                
            logger.info(f"预测结果已保存至: {pred_file}")
        else:
            logger.error("预测失败")
    
    else:
        logger.error(f"不支持的模式: {args.mode}")
        return

if __name__ == "__main__":
    main() 