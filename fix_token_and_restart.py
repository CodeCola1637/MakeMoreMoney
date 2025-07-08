#!/usr/bin/env python3
"""
快速修复Token并重启交易系统
使用方法: python fix_token_and_restart.py NEW_TOKEN_HERE
"""

import sys
import os
import time
import subprocess
from datetime import datetime

def update_env_file(new_token):
    """更新.env文件中的token"""
    try:
        # 备份当前.env文件
        backup_name = f".env.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        subprocess.run(['cp', '.env', backup_name], check=True)
        print(f"✅ 已备份.env文件到 {backup_name}")
        
        # 读取当前.env文件
        with open('.env', 'r') as f:
            content = f.read()
        
        # 更新所有相关的token行
        lines = content.split('\n')
        updated_lines = []
        
        for line in lines:
            if line.startswith('LB_ACCESS_TOKEN='):
                updated_lines.append(f'LB_ACCESS_TOKEN={new_token}')
                print("✅ 更新了 LB_ACCESS_TOKEN")
            elif line.startswith('LONGPORT_ACCESS_TOKEN='):
                updated_lines.append(f'LONGPORT_ACCESS_TOKEN={new_token}')
                print("✅ 更新了 LONGPORT_ACCESS_TOKEN")
            else:
                updated_lines.append(line)
        
        # 写回文件
        with open('.env', 'w') as f:
            f.write('\n'.join(updated_lines))
        
        print("✅ .env文件更新完成")
        return True
        
    except Exception as e:
        print(f"❌ 更新.env文件失败: {e}")
        return False

def stop_trading_system():
    """停止交易系统"""
    try:
        print("🛑 正在停止交易系统...")
        result = subprocess.run(['pkill', '-f', 'python.*main.py'], 
                              capture_output=True, text=True)
        time.sleep(3)  # 等待进程完全停止
        print("✅ 交易系统已停止")
        return True
    except Exception as e:
        print(f"❌ 停止系统失败: {e}")
        return False

def start_trading_system():
    """启动交易系统"""
    try:
        print("🚀 正在启动交易系统...")
        
        # 在后台启动
        process = subprocess.Popen(['python', 'main.py'], 
                                 stdout=subprocess.PIPE, 
                                 stderr=subprocess.PIPE)
        
        time.sleep(5)  # 等待系统启动
        
        # 检查进程是否还在运行
        if process.poll() is None:
            print(f"✅ 交易系统已启动 (PID: {process.pid})")
            return True
        else:
            print("❌ 交易系统启动失败")
            return False
            
    except Exception as e:
        print(f"❌ 启动系统失败: {e}")
        return False

def verify_token(token):
    """验证新token"""
    try:
        # 简单验证token格式
        if not token or len(token) < 100:
            print("❌ Token格式无效（太短）")
            return False
        
        if not token.startswith('m_eyJ'):
            print("❌ Token格式无效（应该以m_eyJ开头）")
            return False
        
        parts = token.split('.')
        if len(parts) != 3:
            print("❌ Token格式无效（不是有效的JWT格式）")
            return False
        
        print("✅ Token格式验证通过")
        return True
        
    except Exception as e:
        print(f"❌ Token验证失败: {e}")
        return False

def check_system_status():
    """检查系统状态"""
    try:
        print("\n📊 检查系统状态...")
        
        # 检查进程
        result = subprocess.run(['pgrep', '-f', 'python.*main.py'], 
                              capture_output=True, text=True)
        
        if result.stdout.strip():
            print(f"✅ 系统进程运行中 (PID: {result.stdout.strip()})")
        else:
            print("❌ 系统进程未运行")
            return False
        
        # 检查最新日志
        time.sleep(10)  # 等待系统生成日志
        
        try:
            result = subprocess.run(['tail', '-10', 'logs/trading.log'], 
                                  capture_output=True, text=True)
            
            recent_logs = result.stdout
            if 'token expired' in recent_logs:
                print("❌ 系统仍然有token过期错误")
                return False
            elif 'ERROR' in recent_logs:
                print("⚠️ 发现其他错误，请检查日志")
                print("最新日志:")
                print(recent_logs[-500:])  # 显示最后500字符
            else:
                print("✅ 系统运行正常，未发现明显错误")
        
        except Exception:
            print("⚠️ 无法检查日志文件")
        
        return True
        
    except Exception as e:
        print(f"❌ 检查系统状态失败: {e}")
        return False

def main():
    """主函数"""
    print("🔧 长桥API Token修复工具")
    print("=" * 50)
    
    # 检查参数
    if len(sys.argv) != 2:
        print("❌ 使用方法: python fix_token_and_restart.py NEW_TOKEN_HERE")
        print("示例: python fix_token_and_restart.py m_eyJhbGciOiJSUzI1NiIs...")
        return False
    
    new_token = sys.argv[1]
    
    # 验证新token
    if not verify_token(new_token):
        return False
    
    print(f"🔍 新Token前缀: {new_token[:50]}...")
    
    # 确认操作
    confirm = input("\n⚠️ 确认要更新Token并重启系统吗？(y/n): ")
    if confirm.lower() != 'y':
        print("❌ 操作已取消")
        return False
    
    # 执行修复步骤
    success = True
    
    # 1. 停止系统
    if not stop_trading_system():
        success = False
    
    # 2. 更新token
    if success and not update_env_file(new_token):
        success = False
    
    # 3. 启动系统
    if success and not start_trading_system():
        success = False
    
    # 4. 检查状态
    if success:
        check_system_status()
    
    if success:
        print("\n" + "=" * 50)
        print("🎉 修复完成！")
        print("✅ Token已更新")
        print("✅ 系统已重启")
        print("💡 建议监控几分钟确保系统正常运行")
        print("📋 可以运行: python check_token.py 验证修复效果")
    else:
        print("\n" + "=" * 50)
        print("❌ 修复过程中出现错误")
        print("🔧 请手动检查并修复问题")
    
    return success

if __name__ == "__main__":
    main() 