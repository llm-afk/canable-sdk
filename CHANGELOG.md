# 版本记录

## 0.1.4

- 发行包：`canable-sdk`；Python 模块与命令行入口：`canable`。
- 提供 `canable25` 兼容命名空间，详见 [兼容性说明](COMPATIBILITY.md)。
- 源码及安装包各通过 75 项离线测试，覆盖模块身份与命令行入口。

## 0.1.3

- 应用接口：list_devices、recv、recv_event、info、read_status、identify。
- 帧接收与事件接收采用互斥模式；一个连接允许一个接收消费者。
- 错误和未知事件通过 CanEventError 提供。
- 内部环回覆盖经典 CAN、CAN FD、批量传输及连接生命周期，见 [验证记录](VALIDATION.md)。

## 0.1.2

- Connection/open_can 提供连接配置、启动和上下文资源管理。
- Device/Channel 提供通道控制、事件流及 TxReceipt。
- 62 项离线测试通过；安装验证见 [报告](validation/application-api-0.1.2.json)。
