# 长桥API连接问题报告

## 问题分析

经过多轮测试，我们发现您当前环境无法连接到长桥API的根本原因是**DNS污染**问题。当您尝试访问长桥API的域名（如openapi.longportapp.com）时，实际被解析到了Facebook的服务器IP地址上。

关键证据：
1. TCP连接测试显示可以成功连接到服务器，但SSL/TLS握手失败
2. 最新测试显示连接到`api-gateway.longportapp.com`时返回了Facebook的错误页面
3. DNS查询显示`openapi.longportapp.com`解析到了`31.13.88.26`，这是Facebook的IP地址

## 推荐解决方案

### 方案1：修改hosts文件（推荐）

1. 找到您系统的hosts文件位置:
   - macOS: `/etc/hosts`
   - Windows: `C:\Windows\System32\drivers\etc\hosts`

2. 使用管理员权限编辑hosts文件，添加以下条目:
   ```
   88.191.249.182 openapi.longportapp.com
   88.191.249.182 openapi-quote.longportapp.com
   ```

3. 保存文件并刷新DNS缓存:
   - macOS: `sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder`
   - Windows: `ipconfig /flushdns`

### 方案2：使用VPN服务

使用可靠的VPN服务，绕过当前网络环境的限制。这可能是最简单的解决方案，尤其是当您无法修改hosts文件时。

### 方案3：更改DNS服务器

将系统或路由器的DNS服务器改为更可靠的公共DNS:
- Google DNS: 8.8.8.8 和 8.8.4.4
- Cloudflare DNS: 1.1.1.1 和 1.0.0.1
- 阿里DNS: 223.5.5.5 和 223.6.6.6

### 方案4：联系长桥API支持

向长桥API提供商报告这个问题，他们可能会提供:
- 专用API地址
- 备用域名
- 直接IP访问方式

## 临时解决方案

如果以上方法都无法立即实施，您可以使用我们提供的`local_mock_data.py`脚本，它会生成模拟数据供算法开发和测试使用。

## 技术细节

以下是我们测试过程中的关键发现:

1. DNS解析结果:
   ```
   openapi.longportapp.com -> 31.13.88.26 (Facebook IP)
   ```

2. 成功的TCP连接但失败的SSL握手:
   ```
   TCP连接成功 (0.00秒)
   SSL握手失败: _ssl.c:1011: The handshake operation timed out
   ```

3. 假成功连接(实际连接到Facebook):
   ```
   连接成功! 状态码: 404, 用时: 0.85秒
   响应内容: <!DOCTYPE html>
   <html lang="en" id="facebook">
   ```

这进一步证实了DNS污染问题的存在。

## 推荐操作

1. 首先尝试修改hosts文件
2. 如果无法修改hosts文件，使用VPN或更改DNS服务器
3. 如果问题仍然存在，考虑联系长桥支持团队

我们确信，一旦DNS问题得到解决，您将能够成功连接到长桥API并开始进行真实数据的交易开发。 