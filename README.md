# IPv6 地址变化邮件通知

这个项目检测指定网卡的 IPv6 地址变化，并通过 SMTP over SSL 发送通知。邮件正文包含设备标记、主机名、IPv4 地址和 IPv6 地址。

## 入口

统一实现是 `ipv6_monitor.py`。以下文件保留为兼容入口，并调用同一实现：

- `ipv6send.py`：树莓派现有 cron 可继续调用。
- `sendipv6address.py`：旧手工入口可继续调用。

`iv6.py` 是历史实现，不应再加入定时任务。

## 配置

`sendmail.json` 是运行时配置示例。至少需要按设备修改：

- `device_name`：邮件中的设备标记。
- `interfaces`：要监控的网卡；树莓派使用 `eth0`，Termux 手机使用 `wlan0`。
- `ifconfig_path`：设备上的 `ifconfig` 路径。
- `state_file`：IPv6 基线文件。
- SMTP 主机、账号、授权码、发件人和收件人。

运行时配置应限制为 `600`，不要将真实 SMTP 授权码提交到公共仓库。`sendmail.json` 中的值只是示例占位符。

示例运行时配置：

```json
{
  "device_name": "树莓派",
  "interfaces": ["eth0"],
  "ifconfig_path": "/usr/sbin/ifconfig",
  "state_file": "local_ipv6_old.json",
  "smtp_host": "smtp.example.com",
  "smtp_port": 465,
  "smtp_user": "sender@example.com",
  "smtp_password": "smtp-app-password",
  "sender": "sender@example.com",
  "subject": "IPv6 地址发生变化",
  "recipients": ["recipient@example.com"]
}
```

也可以通过 `IPV6_MONITOR_CONFIG` 指定配置文件路径。相对状态文件路径相对于脚本目录解析。

## 检测行为

- 只使用全局 IPv6 地址集合判断是否变化；IPv4 只作为邮件正文信息，不会单独触发通知。
- 首次运行只建立 IPv6 基线，不发送邮件。
- IPv6 未变化时不发送邮件。
- 没有全局 IPv6 时保留旧基线。
- IPv6 变化时，只有 SMTP 发送成功后才原子更新基线。
- SMTP 失败会保留旧基线，下一次运行可以重试。
- 不再把动态正文写回配置文件。

## 邮件正文

正文格式如下：

```text
设备标记: 树莓派
主机名: raspberrypi
检测时间: 2026-01-01T12:00:00+08:00

IPv4 地址:
  eth0: 192.168.2.60

IPv6 地址:
  eth0: 2408:...
```

## 树莓派 cron

```cron
0 0,8,12,16,20 * * * /usr/bin/python3 /home/pi/sendipv6address/ipv6send.py
```

部署后检查：

```sh
chmod 700 /home/pi/sendipv6address/ipv6_monitor.py
chmod 700 /home/pi/sendipv6address/ipv6send.py
chmod 600 /home/pi/sendipv6address/sendmail.json
/usr/bin/python3 -m py_compile /home/pi/sendipv6address/ipv6_monitor.py
/usr/bin/python3 /home/pi/sendipv6address/ipv6send.py
```

## Termux 手机

手机使用 `wlan0` 和 Termux 的 `ifconfig`：

```json
{
  "device_name": "荣耀 BND-AL10",
  "interfaces": ["wlan0"],
  "ifconfig_path": "/data/data/com.termux/files/usr/bin/ifconfig",
  "state_file": "ipv6_state.json"
}
```

配置文件中的 SMTP 字段与树莓派相同。部署后设置权限并注册原有任务：

```sh
chmod 700 "$HOME/ipv6-monitor/ipv6_monitor.py"
chmod 600 "$HOME/ipv6-monitor/config.json"
termux-job-scheduler \
  --script "$HOME/ipv6-monitor/ipv6_monitor.py" \
  --job-id 2601 \
  --period-ms 900000 \
  --network any \
  --battery-not-low false \
  --persisted true
```
