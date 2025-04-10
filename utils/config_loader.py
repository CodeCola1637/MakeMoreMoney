class ConfigLoader:
    """配置加载器"""
    
    def __init__(self, config_file: str = "config.yaml"):
        """
        初始化配置加载器
        
        Args:
            config_file: 配置文件路径
        """
        self.config_file = config_file
        self.config = {}
        self.load_config()
        
    def load_config(self) -> None:
        """加载配置文件"""
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f)
            self.logger.info(f"成功加载配置文件: {self.config_file}")
        except Exception as e:
            self.logger.error(f"加载配置文件失败: {str(e)}")
            raise
            
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置项
        
        Args:
            key: 配置键，支持点号分隔的多级键
            default: 默认值
            
        Returns:
            配置值
        """
        try:
            # 分割多级键
            keys = key.split(".")
            value = self.config
            
            # 逐级获取值
            for k in keys:
                if isinstance(value, dict):
                    value = value.get(k, default)
                else:
                    return default
                    
            return value
        except Exception as e:
            self.logger.error(f"获取配置项失败: {key}, 错误: {str(e)}")
            return default 