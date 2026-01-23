"""
Attention-LSTM 模型
增强的 LSTM 模型架构，带有注意力机制，用于更好的时序特征提取
"""

import numpy as np
import os
import logging
from typing import Optional, Tuple, Dict, Any
from datetime import datetime


# 尝试导入 TensorFlow/Keras
try:
    import tensorflow as tf
    from tensorflow.keras.models import Model, load_model
    from tensorflow.keras.layers import (
        Input, LSTM, Dense, Dropout, BatchNormalization,
        Concatenate, Multiply, Permute, Reshape, Lambda,
        Bidirectional, Layer, Attention
    )
    from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint, ReduceLROnPlateau
    from tensorflow.keras.optimizers import Adam
    from tensorflow.keras.regularizers import l1_l2
    TF_AVAILABLE = True
except ImportError:
    TF_AVAILABLE = False


class SelfAttentionLayer(Layer):
    """自注意力层，用于捕获序列中的长程依赖"""
    
    def __init__(self, units: int = 64, **kwargs):
        super(SelfAttentionLayer, self).__init__(**kwargs)
        self.units = units
    
    def build(self, input_shape):
        self.W_q = self.add_weight(
            name='W_q',
            shape=(input_shape[-1], self.units),
            initializer='glorot_uniform',
            trainable=True
        )
        self.W_k = self.add_weight(
            name='W_k',
            shape=(input_shape[-1], self.units),
            initializer='glorot_uniform',
            trainable=True
        )
        self.W_v = self.add_weight(
            name='W_v',
            shape=(input_shape[-1], self.units),
            initializer='glorot_uniform',
            trainable=True
        )
        super().build(input_shape)
    
    def call(self, x, training=None):
        # Query, Key, Value 计算
        q = tf.matmul(x, self.W_q)  # (batch, seq_len, units)
        k = tf.matmul(x, self.W_k)  # (batch, seq_len, units)
        v = tf.matmul(x, self.W_v)  # (batch, seq_len, units)
        
        # 计算注意力分数
        d_k = tf.cast(tf.shape(k)[-1], tf.float32)
        scores = tf.matmul(q, k, transpose_b=True) / tf.sqrt(d_k)  # (batch, seq_len, seq_len)
        
        # Softmax 获取注意力权重
        attention_weights = tf.nn.softmax(scores, axis=-1)
        
        # 应用注意力权重
        output = tf.matmul(attention_weights, v)  # (batch, seq_len, units)
        
        return output
    
    def get_config(self):
        config = super().get_config()
        config.update({'units': self.units})
        return config


class TemporalAttentionLayer(Layer):
    """时序注意力层，对时间步进行加权"""
    
    def __init__(self, **kwargs):
        super(TemporalAttentionLayer, self).__init__(**kwargs)
    
    def build(self, input_shape):
        self.W = self.add_weight(
            name='attention_weight',
            shape=(input_shape[-1], 1),
            initializer='glorot_uniform',
            trainable=True
        )
        self.b = self.add_weight(
            name='attention_bias',
            shape=(input_shape[1], 1),
            initializer='zeros',
            trainable=True
        )
        super().build(input_shape)
    
    def call(self, x, training=None):
        # x shape: (batch, timesteps, features)
        e = tf.tanh(tf.tensordot(x, self.W, axes=1) + self.b)  # (batch, timesteps, 1)
        attention_weights = tf.nn.softmax(e, axis=1)  # (batch, timesteps, 1)
        
        # 加权求和
        context = x * attention_weights  # (batch, timesteps, features)
        context = tf.reduce_sum(context, axis=1)  # (batch, features)
        
        return context, attention_weights
    
    def get_config(self):
        return super().get_config()


class AttentionLSTM:
    """
    带注意力机制的 LSTM 模型
    
    特性：
    1. 双向 LSTM 层捕获前后文信息
    2. 自注意力机制捕获长程依赖
    3. 时序注意力层对重要时间步加权
    4. 多任务输出（可选：价格预测 + 方向分类）
    """
    
    def __init__(self, config, logger=None):
        """
        初始化 Attention-LSTM 模型
        
        Args:
            config: 配置对象
            logger: 日志记录器
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.model: Optional[Model] = None
        self.is_trained = False
        
        # 模型参数
        self.lookback = config.get("strategy.training.lookback", 30)
        self.num_features = config.get("strategy.training.num_features", 4)
        self.lstm_units = config.get("strategy.model.lstm_units", 64)
        self.attention_units = config.get("strategy.model.attention_units", 32)
        self.dropout_rate = config.get("strategy.model.dropout_rate", 0.3)
        self.learning_rate = config.get("strategy.model.learning_rate", 0.001)
        self.l1_reg = config.get("strategy.model.l1_reg", 0.0001)
        self.l2_reg = config.get("strategy.model.l2_reg", 0.0001)
        
        # 模型文件路径
        self.model_path = config.get("strategy.training.model_path", "./models/attention_lstm.h5")
        
        if not TF_AVAILABLE:
            self.logger.error("TensorFlow 未安装，Attention-LSTM 模型不可用")
        else:
            self.logger.info(f"Attention-LSTM 模型初始化完成 - "
                           f"lookback={self.lookback}, features={self.num_features}, "
                           f"lstm_units={self.lstm_units}, attention_units={self.attention_units}")
    
    def build_model(self, input_shape: Tuple[int, int] = None) -> Model:
        """
        构建 Attention-LSTM 模型
        
        Args:
            input_shape: 输入形状 (timesteps, features)
            
        Returns:
            编译后的 Keras 模型
        """
        if not TF_AVAILABLE:
            raise RuntimeError("TensorFlow 未安装")
        
        if input_shape is None:
            input_shape = (self.lookback, self.num_features)
        
        self.logger.info(f"构建 Attention-LSTM 模型，输入形状: {input_shape}")
        
        # 输入层
        inputs = Input(shape=input_shape, name='input_sequence')
        
        # 第一层 LSTM（双向）
        x = Bidirectional(
            LSTM(
                self.lstm_units,
                return_sequences=True,
                kernel_regularizer=l1_l2(l1=self.l1_reg, l2=self.l2_reg),
                name='lstm_1'
            ),
            name='bidirectional_1'
        )(inputs)
        x = BatchNormalization(name='bn_1')(x)
        x = Dropout(self.dropout_rate, name='dropout_1')(x)
        
        # 自注意力层
        attention_out = SelfAttentionLayer(
            units=self.attention_units,
            name='self_attention'
        )(x)
        
        # 残差连接
        if x.shape[-1] != attention_out.shape[-1]:
            # 如果维度不匹配，使用 Dense 层调整
            x_residual = Dense(self.attention_units, name='residual_proj')(x)
        else:
            x_residual = x
        x = x_residual + attention_out
        
        # 第二层 LSTM
        x = LSTM(
            self.lstm_units // 2,
            return_sequences=True,
            kernel_regularizer=l1_l2(l1=self.l1_reg, l2=self.l2_reg),
            name='lstm_2'
        )(x)
        x = BatchNormalization(name='bn_2')(x)
        x = Dropout(self.dropout_rate, name='dropout_2')(x)
        
        # 时序注意力层
        context, attention_weights = TemporalAttentionLayer(name='temporal_attention')(x)
        
        # 全连接层
        x = Dense(64, activation='relu', name='fc_1')(context)
        x = Dropout(self.dropout_rate / 2, name='dropout_3')(x)
        x = Dense(32, activation='relu', name='fc_2')(x)
        
        # 输出层（价格预测）
        output = Dense(1, activation='linear', name='price_prediction')(x)
        
        # 构建模型
        self.model = Model(inputs=inputs, outputs=output, name='attention_lstm')
        
        # 编译模型
        optimizer = Adam(learning_rate=self.learning_rate)
        self.model.compile(
            optimizer=optimizer,
            loss='mse',
            metrics=['mae']
        )
        
        self.logger.info("模型构建完成")
        self.model.summary(print_fn=lambda x: self.logger.debug(x))
        
        return self.model
    
    def train(self, X_train: np.ndarray, y_train: np.ndarray,
              X_val: np.ndarray = None, y_val: np.ndarray = None,
              epochs: int = 100, batch_size: int = 32) -> Dict[str, Any]:
        """
        训练模型
        
        Args:
            X_train: 训练数据 (samples, timesteps, features)
            y_train: 训练标签
            X_val: 验证数据（可选）
            y_val: 验证标签（可选）
            epochs: 训练轮数
            batch_size: 批次大小
            
        Returns:
            训练历史
        """
        if not TF_AVAILABLE:
            raise RuntimeError("TensorFlow 未安装")
        
        if self.model is None:
            self.build_model(input_shape=(X_train.shape[1], X_train.shape[2]))
        
        self.logger.info(f"开始训练 - epochs={epochs}, batch_size={batch_size}")
        self.logger.info(f"训练数据形状: X={X_train.shape}, y={y_train.shape}")
        
        # 回调函数
        callbacks = [
            EarlyStopping(
                monitor='val_loss' if X_val is not None else 'loss',
                patience=15,
                restore_best_weights=True,
                verbose=1
            ),
            ReduceLROnPlateau(
                monitor='val_loss' if X_val is not None else 'loss',
                factor=0.5,
                patience=5,
                min_lr=1e-6,
                verbose=1
            ),
            ModelCheckpoint(
                filepath=self.model_path,
                monitor='val_loss' if X_val is not None else 'loss',
                save_best_only=True,
                verbose=1
            )
        ]
        
        # 训练
        validation_data = (X_val, y_val) if X_val is not None else None
        history = self.model.fit(
            X_train, y_train,
            epochs=epochs,
            batch_size=batch_size,
            validation_data=validation_data,
            callbacks=callbacks,
            verbose=1
        )
        
        self.is_trained = True
        
        # 计算训练结果
        train_loss = history.history['loss'][-1]
        train_mae = history.history['mae'][-1]
        
        result = {
            'train_loss': train_loss,
            'train_mae': train_mae,
            'epochs_completed': len(history.history['loss']),
            'model_path': self.model_path
        }
        
        if X_val is not None:
            result['val_loss'] = history.history['val_loss'][-1]
            result['val_mae'] = history.history['val_mae'][-1]
        
        self.logger.info(f"训练完成: {result}")
        
        return result
    
    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        预测
        
        Args:
            X: 输入数据 (samples, timesteps, features)
            
        Returns:
            预测结果
        """
        if not TF_AVAILABLE:
            raise RuntimeError("TensorFlow 未安装")
        
        if self.model is None:
            self.load_model()
        
        if self.model is None:
            raise RuntimeError("模型未加载")
        
        return self.model.predict(X, verbose=0)
    
    def predict_with_confidence(self, X: np.ndarray, 
                                 n_samples: int = 10) -> Tuple[np.ndarray, np.ndarray]:
        """
        带置信度的预测（使用 MC Dropout）
        
        Args:
            X: 输入数据
            n_samples: 采样次数
            
        Returns:
            (均值预测, 标准差/置信度)
        """
        if not TF_AVAILABLE:
            raise RuntimeError("TensorFlow 未安装")
        
        if self.model is None:
            self.load_model()
        
        # 使用 MC Dropout 进行多次预测
        predictions = []
        for _ in range(n_samples):
            # 在训练模式下预测（保持 Dropout 激活）
            pred = self.model(X, training=True)
            predictions.append(pred.numpy())
        
        predictions = np.array(predictions)
        mean_prediction = np.mean(predictions, axis=0)
        std_prediction = np.std(predictions, axis=0)
        
        # 置信度 = 1 / (1 + std)
        confidence = 1.0 / (1.0 + std_prediction)
        
        return mean_prediction, confidence
    
    def save_model(self, path: str = None):
        """保存模型"""
        if self.model is None:
            self.logger.warning("模型为空，无法保存")
            return
        
        save_path = path or self.model_path
        
        # 确保目录存在
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        
        self.model.save(save_path)
        self.logger.info(f"模型已保存到: {save_path}")
    
    def load_model(self, path: str = None) -> bool:
        """加载模型"""
        if not TF_AVAILABLE:
            self.logger.error("TensorFlow 未安装")
            return False
        
        load_path = path or self.model_path
        
        if not os.path.exists(load_path):
            self.logger.warning(f"模型文件不存在: {load_path}")
            return False
        
        try:
            self.model = load_model(
                load_path,
                custom_objects={
                    'SelfAttentionLayer': SelfAttentionLayer,
                    'TemporalAttentionLayer': TemporalAttentionLayer
                }
            )
            self.is_trained = True
            self.logger.info(f"模型已加载: {load_path}")
            return True
        except Exception as e:
            self.logger.error(f"加载模型失败: {e}")
            return False
    
    def get_model_summary(self) -> Dict[str, Any]:
        """获取模型摘要"""
        if self.model is None:
            return {'error': '模型未初始化'}
        
        return {
            'name': self.model.name,
            'layers': len(self.model.layers),
            'trainable_params': sum([
                np.prod(v.shape.as_list()) 
                for v in self.model.trainable_variables
            ]),
            'is_trained': self.is_trained,
            'model_path': self.model_path,
            'config': {
                'lookback': self.lookback,
                'num_features': self.num_features,
                'lstm_units': self.lstm_units,
                'attention_units': self.attention_units,
                'dropout_rate': self.dropout_rate
            }
        }


def create_attention_lstm(config, logger=None) -> AttentionLSTM:
    """
    工厂函数：创建 Attention-LSTM 模型
    
    Args:
        config: 配置对象
        logger: 日志记录器
        
    Returns:
        AttentionLSTM 实例
    """
    return AttentionLSTM(config, logger)
