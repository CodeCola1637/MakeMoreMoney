#!/bin/bash

# 设置颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== 量化交易系统启动脚本 ===${NC}"

# 检查虚拟环境是否存在
if [ -d "venv" ]; then
    echo -e "${GREEN}激活虚拟环境...${NC}"
    source venv/bin/activate
else
    echo -e "${YELLOW}未找到虚拟环境，使用系统 Python${NC}"
fi

# 检查 .env 文件是否存在
if [ -f ".env" ]; then
    echo -e "${GREEN}加载环境变量...${NC}"
    # 从 .env 文件加载环境变量
    set -a
    source .env
    set +a
else
    echo -e "${RED}错误: .env 文件不存在！请先创建 .env 文件并设置 LongBridge API 的环境变量${NC}"
    echo -e "需要设置以下环境变量:"
    echo -e "  LONGPORT_APP_KEY=您的应用密钥"
    echo -e "  LONGPORT_APP_SECRET=您的应用密码"
    echo -e "  LONGPORT_ACCESS_TOKEN=您的访问令牌"
    exit 1
fi

# 检查日志目录是否存在
if [ ! -d "logs" ]; then
    echo -e "${GREEN}创建日志目录...${NC}"
    mkdir -p logs
fi

# 检查数据库目录是否存在
if [ ! -d "databases" ]; then
    echo -e "${GREEN}创建数据库目录...${NC}"
    mkdir -p databases
fi

# 运行参数
SYMBOLS="700.HK"
TRAIN=""
NO_MOCK=""

# 检查命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --symbols)
            shift
            SYMBOLS="$1"
            shift
            ;;
        --train)
            TRAIN="--train"
            shift
            ;;
        --no-mock)
            NO_MOCK="--no-mock"
            shift
            ;;
        *)
            echo -e "${RED}未知参数: $1${NC}"
            shift
            ;;
    esac
done

# 运行主程序
echo -e "${GREEN}启动量化交易系统...${NC}"
echo -e "${GREEN}交易标的: ${SYMBOLS}${NC}"
if [ -n "$TRAIN" ]; then
    echo -e "${GREEN}将在启动时训练模型${NC}"
fi
if [ -n "$NO_MOCK" ]; then
    echo -e "${GREEN}禁用本地模拟数据${NC}"
fi

python main.py --symbols $SYMBOLS $TRAIN $NO_MOCK

# 检查退出状态
EXIT_STATUS=$?
if [ $EXIT_STATUS -ne 0 ]; then
    echo -e "${RED}程序异常退出，错误码: $EXIT_STATUS${NC}"
    echo -e "${YELLOW}请检查日志文件以了解详细信息${NC}"
else
    echo -e "${GREEN}程序正常退出${NC}"
fi

# 如果使用了虚拟环境，则退出
if [ -d "venv" ]; then
    deactivate
fi

exit $EXIT_STATUS 