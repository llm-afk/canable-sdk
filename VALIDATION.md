# 验证

## 离线与安装验证

SDK 0.1.5：源码及独立安装包各通过 83 项测试。
覆盖协议编解码、合法帧长度、位时序、USB 边界、固件版本检查、异常传播、
发送回执、接收模式互斥、资源生命周期及兼容命名空间。
配置测试覆盖 USB 命令顺序、参数预检查、不支持的硬件能力及初始化失败资源释放。
新旧命令行入口均通过安装检查。报告见 [安装验证](validation/configuration-0.1.5.json)。

离线测试不证明外部 CAN 互通、持续吞吐或实时控制性能。

## 实机内部环回

验证日期：2026-09-29；SDK：0.1.3；Windows x64。

| 项目 | 设备信息 |
|---|---|
| 产品 | Candlelight 2.5 - Multiboard |
| MCU | STM32G431 |
| 序列号 | 20903498384550052 |
| 固件 / 硬件 | 0x260803 / 0x0200 |
| 能力位 | 0xE53B |
| 通道 / CAN 时钟 | 1 / 160 MHz |

完整环回套件通过 24 组测试，共 1024 帧接收、1024 个 Tx Event。
覆盖经典标准/扩展帧、RTR、FD 全部合法长度、BRS、64/2048 字节 USB 边界、
63 帧经典批量、28 帧 FD 批量、FD 到经典 CAN 配置切换及重复打开/关闭。

应用连接接口另通过 40 帧环回，覆盖帧与事件两种接收方式。
测试结束控制器为 Stopped，Tx/Rx 错误计数为 0，句柄已释放。

- [完整环回报告](validation/hardware-loopback-0.1.3-20260929.json)
- [应用接口环回报告](validation/facade-loopback-0.1.3-20260929.json)
- [测试脚本](examples/hardware_loopback.py)

内部环回不验证外部收发器、线缆、远端 ACK 或电机业务协议。

## 传输约束

- 单批及未决发送最多 63 帧，USB 批量缓冲区上限为 2048 字节。
- 64 字节 USB 包边界通过 SHORT_PACKET_TERMINATE 结束传输；2048 字节满缓冲区不补 ZLP。
- 异常中断后应重新插拔适配器，再执行完整测试。重开 USB 句柄不能保证清除设备残留数据。
- 发送结果不确定时不自动重放；业务层负责确认远端状态。

## 待验证范围

| 范围 | 验证内容 |
|---|---|
| 外部总线 | 双适配器收发、ACK、终端电阻、线缆 |
| 性能 | 持续吞吐、延迟、1 kHz 控制时序、长时间资源占用 |
| 故障 | 无 ACK、Bus Off、拔线、挂起/恢复、接收停滞 |
| 并发 | 多适配器、多通道、桥接 |
| 维护 | 用户数据区、BOOT0、DFU |
| 平台 | 其他 Windows/Python 版本、MCU、板型和 USB 主控 |

## 执行测试

在项目根目录运行：

```powershell
.\sdk.ps1 test
.\sdk.ps1 build
.\sdk.ps1 list
.\sdk.ps1 loopback-suite --serial YOUR_SERIAL --rounds 10 --output .\validation\loopback.json
```

loopback-suite 会配置并启停内部环回。测试报告应记录 SDK 与固件版本、设备信息、
速率、帧数、错误和丢弃计数。原始测试报告保存在 `validation/`，按各报告中的版本和配置解释。
