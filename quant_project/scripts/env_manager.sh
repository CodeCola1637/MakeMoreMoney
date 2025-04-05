#!/bin/bash

# 量化交易系统环境管理脚本
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CONFIG_DIR="$PROJECT_ROOT/configs"
LOG_DIR="$PROJECT_ROOT/shared_data/logs"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

# 颜色代码
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 创建所有环境
create_all_environments() {
    log_info "开始创建所有 Conda 环境..."
    
    # 遍历所有 yml 文件
    for yml_file in "$CONFIG_DIR"/*_env.yml; do
        env_name=$(basename "$yml_file" | sed 's/\.yml$//')
        log_info "创建环境: $env_name 从配置文件 $(basename "$yml_file")"
        
        # 创建环境
        conda env create -f "$yml_file" || {
            log_error "创建环境 $env_name 失败"
            continue
        }
        
        log_success "环境 $env_name 创建成功"
    done
    
    log_success "所有环境创建完成"
}

# 删除所有环境
remove_all_environments() {
    log_info "删除所有已创建的 Conda 环境..."
    
    # 根据配置文件获取环境名称
    for yml_file in "$CONFIG_DIR"/*_env.yml; do
        env_name=$(grep "^name:" "$yml_file" | cut -d' ' -f2)
        if [ -n "$env_name" ]; then
            log_info "删除环境: $env_name"
            conda env remove -n "$env_name" || log_error "删除环境 $env_name 失败"
        fi
    done
    
    log_success "环境清理完成"
}

# 启动数据服务
start_data_service() {
    log_info "启动数据服务层..."
    env_name="data_service_env"
    
    # 检查服务是否已在运行
    if [ -f "$LOG_DIR/${env_name}.pid" ]; then
        pid=$(cat "$LOG_DIR/${env_name}.pid")
        if ps -p "$pid" > /dev/null; then
            log_warning "数据服务已在运行 (PID: $pid)"
            return 0
        else
            log_warning "删除过期的 PID 文件"
            rm "$LOG_DIR/${env_name}.pid"
        fi
    fi
    
    # 启动服务
    log_info "使用 $env_name 环境启动数据服务..."
    (conda run -n "$env_name" python "$PROJECT_ROOT/data_service/api_server.py" > "$LOG_DIR/${env_name}_$(date +%Y%m%d).log" 2>&1) &
    
    # 保存 PID
    echo $! > "$LOG_DIR/${env_name}.pid"
    log_success "数据服务已启动，PID: $(cat "$LOG_DIR/${env_name}.pid")"
}

# 启动策略研究服务
start_strategy_service() {
    log_info "启动策略研究服务..."
    env_name="strategy_research_env"
    
    # 检查服务是否已在运行
    if [ -f "$LOG_DIR/${env_name}.pid" ]; then
        pid=$(cat "$LOG_DIR/${env_name}.pid")
        if ps -p "$pid" > /dev/null; then
            log_warning "策略研究服务已在运行 (PID: $pid)"
            return 0
        else
            log_warning "删除过期的 PID 文件"
            rm "$LOG_DIR/${env_name}.pid"
        fi
    fi
    
    # 启动服务
    log_info "使用 $env_name 环境启动策略研究服务..."
    (conda run -n "$env_name" jupyter lab --ip=0.0.0.0 --no-browser --notebook-dir="$PROJECT_ROOT/strategy_research" > "$LOG_DIR/${env_name}_$(date +%Y%m%d).log" 2>&1) &
    
    # 保存 PID
    echo $! > "$LOG_DIR/${env_name}.pid"
    log_success "策略研究服务已启动，PID: $(cat "$LOG_DIR/${env_name}.pid")"
}

# 启动交易执行服务
start_trading_service() {
    log_info "启动交易执行服务..."
    env_name="trading_execution_env"
    
    # 检查服务是否已在运行
    if [ -f "$LOG_DIR/${env_name}.pid" ]; then
        pid=$(cat "$LOG_DIR/${env_name}.pid")
        if ps -p "$pid" > /dev/null; then
            log_warning "交易执行服务已在运行 (PID: $pid)"
            return 0
        else
            log_warning "删除过期的 PID 文件"
            rm "$LOG_DIR/${env_name}.pid"
        fi
    fi
    
    # 启动服务
    log_info "使用 $env_name 环境启动交易执行服务..."
    (conda run -n "$env_name" python "$PROJECT_ROOT/trading_execution/trading_server.py" > "$LOG_DIR/${env_name}_$(date +%Y%m%d).log" 2>&1) &
    
    # 保存 PID
    echo $! > "$LOG_DIR/${env_name}.pid"
    log_success "交易执行服务已启动，PID: $(cat "$LOG_DIR/${env_name}.pid")"
}

# 停止所有服务
stop_all_services() {
    log_info "停止所有运行中的服务..."
    
    # 遍历 PID 文件并停止服务
    for pid_file in "$LOG_DIR"/*.pid; do
        if [ -f "$pid_file" ]; then
            service_name=$(basename "$pid_file" .pid)
            pid=$(cat "$pid_file")
            log_info "停止服务: $service_name (PID: $pid)"
            
            # 尝试优雅关闭
            kill "$pid" 2>/dev/null || {
                log_warning "无法正常停止服务 $service_name, 尝试强制停止"
                kill -9 "$pid" 2>/dev/null || log_error "无法停止服务 $service_name"
            }
            
            rm "$pid_file"
        fi
    done
    
    log_success "所有服务已停止"
}

# 检查服务状态
check_service_status() {
    log_info "检查服务状态..."
    
    # 检查是否有运行中的服务
    found_services=false
    
    # 遍历 PID 文件并检查服务状态
    for pid_file in "$LOG_DIR"/*.pid; do
        if [ -f "$pid_file" ]; then
            found_services=true
            service_name=$(basename "$pid_file" .pid)
            pid=$(cat "$pid_file")
            
            if ps -p "$pid" > /dev/null; then
                log_success "服务 $service_name 正在运行 (PID: $pid)"
            else
                log_error "服务 $service_name 已崩溃 (PID: $pid 不存在)"
                log_info "查看日志: $LOG_DIR/${service_name}_*.log"
            fi
        fi
    done
    
    if [ "$found_services" = false ]; then
        log_warning "未发现运行中的服务"
    fi
}

# 导出环境配置
export_environments() {
    log_info "导出当前环境配置..."
    
    # 创建导出目录
    export_dir="$PROJECT_ROOT/configs/exports"
    mkdir -p "$export_dir"
    
    # 导出各环境配置
    for env_name in data_service_env strategy_research_env trading_execution_env ml_models_env; do
        if conda env list | grep -q "$env_name"; then
            log_info "导出 $env_name 环境配置"
            conda env export -n "$env_name" > "$export_dir/${env_name}_$(date +%Y%m%d).yml"
            log_success "$env_name 环境配置已导出至 $export_dir/${env_name}_$(date +%Y%m%d).yml"
        else
            log_warning "环境 $env_name 不存在，无法导出"
        fi
    done
}

# 显示使用帮助
show_help() {
    echo -e "${BLUE}量化交易系统环境管理脚本${NC}"
    echo
    echo "用法: $0 [命令]"
    echo
    echo "可用命令:"
    echo "  create-env     创建所有环境"
    echo "  remove-env     删除所有环境"
    echo "  start-data     启动数据服务"
    echo "  start-strategy 启动策略研究服务"
    echo "  start-trading  启动交易执行服务"
    echo "  start-all      启动所有服务"
    echo "  stop-all       停止所有服务"
    echo "  status         检查服务状态"
    echo "  export-env     导出当前环境配置"
    echo "  help           显示此帮助信息"
    echo
}

# 主命令处理
case "$1" in
    create-env)
        create_all_environments
        ;;
    remove-env)
        remove_all_environments
        ;;
    start-data)
        start_data_service
        ;;
    start-strategy)
        start_strategy_service
        ;;
    start-trading)
        start_trading_service
        ;;
    start-all)
        start_data_service
        sleep 5
        start_strategy_service
        sleep 3
        start_trading_service
        ;;
    stop-all)
        stop_all_services
        ;;
    status)
        check_service_status
        ;;
    export-env)
        export_environments
        ;;
    help|--help|-h|*)
        show_help
        ;;
esac

exit 0 