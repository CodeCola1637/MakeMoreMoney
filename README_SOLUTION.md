# 长桥API连接问题解决方案

经过多次测试，我们发现当前环境存在以下网络连接问题：

1. DNS污染：`openapi.longportapp.com`被错误解析到Facebook的服务器(31.13.88.26)
2. TLS连接问题：即使使用正确的IP地址，也无法建立TLS连接

## 解决方案

### 方案1：使用VPN或代理服务器（推荐）

这是最可靠的解决方案，可以完全绕过网络环境限制：

1. 安装可靠的VPN或代理服务器（如Clash、V2Ray等）
2. 配置全局代理或仅为Python程序配置代理
3. 在`.env`文件中设置代理：
   ```
   HTTP_PROXY=http://127.0.0.1:7890
   HTTPS_PROXY=http://127.0.0.1:7890
   ```
   (请根据您的实际代理配置修改端口号)

### 方案2：修改hosts文件

1. 编辑hosts文件：
   ```bash
   sudo nano /etc/hosts
   ```
2. 添加以下内容：
   ```
   88.191.249.182 openapi.longportapp.com
   88.191.249.182 openapi-quote.longportapp.com
   ```
3. 保存并退出
4. 刷新DNS缓存：
   ```bash
   sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder
   ```

### 方案3：使用Docker模拟环境

如果您希望在本地进行开发和测试，可以考虑使用Docker：

1. 修改`docker-compose.yml`文件添加代理设置：
   ```yaml
   services:
     app:
       build: .
       environment:
         - HTTP_PROXY=http://host.docker.internal:7890
         - HTTPS_PROXY=http://host.docker.internal:7890
   ```

2. 在Dockerfile中配置证书信任：
   ```dockerfile
   # 安装证书
   RUN apt-get update && apt-get install -y ca-certificates
   ```

### 方案4：联系长桥API支持

这是长期解决方案的最佳选择：

1. 联系长桥API技术支持，获取最新的：
   - API服务器地址
   - 连接方法和建议
   - 是否有专用的国内/国际线路

## 临时解决方案：使用本地模拟数据

在解决网络问题之前，您可以继续使用`test_local.py`脚本进行本地开发和测试：

```bash
python3 test_local.py
```

这将使用模拟数据执行双均线策略回测，让您可以继续开发算法逻辑。

## 其他建议

1. 检查您的API凭证是否已过期
2. 确认API访问权限和限制
3. 考虑使用长桥提供的其他API SDK或标准库 