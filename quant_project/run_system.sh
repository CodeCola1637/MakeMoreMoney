#!/bin/bash
# 启动量化交易系统的各个组件

# 确保脚本中断时能清理所有子进程
trap "kill 0" EXIT

# 配置
API_SERVER_PORT=8002
INTERFACE_TYPE=${1:-mock}  # 默认使用模拟接口，可通过参数指定

# 颜色配置
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 创建日志目录
mkdir -p logs

# 显示启动信息
echo -e "${GREEN}================== 启动量化交易系统 ==================${NC}"
echo -e "${BLUE}接口类型:${NC} $INTERFACE_TYPE"
echo -e "${BLUE}API端口:${NC} $API_SERVER_PORT"
echo -e "${BLUE}日志目录:${NC} logs"
echo -e "${GREEN}===================================================${NC}"

# 激活虚拟环境（如果存在）
if [ -d "./venv" ]; then
    echo -e "${BLUE}激活虚拟环境...${NC}"
    source ./venv/bin/activate
fi

# 启动API服务器
echo -e "${YELLOW}启动交易API服务器...${NC}"
python -m quant_project.trading_execution.api_server --port $API_SERVER_PORT --interface $INTERFACE_TYPE > logs/api_server.log 2>&1 &
API_PID=$!
echo -e "${GREEN}API服务器已启动 (PID: $API_PID)${NC}"

# 等待API服务器启动
echo -e "${YELLOW}等待API服务器启动...${NC}"
sleep 5

# 检查API服务器是否正常启动
if kill -0 $API_PID 2>/dev/null; then
    echo -e "${GREEN}API服务器正常运行中${NC}"
else
    echo -e "${RED}API服务器启动失败，请检查logs/api_server.log${NC}"
    exit 1
fi

# 提示如何启动策略
echo -e "\n${BLUE}系统已就绪！可以通过以下命令启动策略:${NC}"
echo -e "${GREEN}python -m quant_project.strategy_research.run_dual_ma --symbol 700.HK --fast 5 --slow 20${NC}"

# 保持脚本运行
echo -e "\n${YELLOW}按 Ctrl+C 停止系统...${NC}"
wait 