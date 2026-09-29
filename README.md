# CANable SDK 0.1.4

常用接口收敛为：列设备、打开连接、发送、接收、读状态。
基于 Windows WinUSB，Python >=3.10，无第三方运行时依赖。
面向当前 Elmue Candlelight 扩展固件，SLCAN 尚未实现。

## 名称与安装

项目 / GitHub 仓库：`canable-sdk`；Python 导入与命令：`canable`。
从本地目录安装：`python -m pip install .`；命令行枚举：`python -m canable list`。
支持范围仍是上述 Elmue Candlelight 固件，并不因改名而支持所有 CANable 固件。

旧 `import canable25`、子模块导入和 `python -m canable25` 保留兼容入口，
与新入口使用同一套实现。新项目请使用 `canable`。
发行包名称已经变为 `canable-sdk`：下游 pyproject/requirements 的
`canable25` 依赖需要改为 `canable-sdk>=0.1.4,<0.2`；
导入兼容不等于 pip 发行包依赖兼容。旧发行包已安装时，建议先卸载旧包再安装本目录。
本机保留旧文件夹名的目录链接，以兼容已有项目的路径引用。

## 常用接口

| 接口 | 用途 |
|---|---|
| list_devices() | 只读枚举，返回设备/通道身份和能力 |
| open_can(...) | 打开、配置并启动指定通道 |
| can.info | 读取已缓存的设备信息，不发 USB 查询 |
| can.send(frame) | 提交一帧，返回发送回执 |
| can.send_many(frames) | 一次 USB 批量提交，返回回执列表 |
| can.recv(timeout) | 只返回 CAN Frame，超时为 None |
| can.read_status() | 查询控制器状态及收发错误计数，不发 CAN 帧 |
| can.identify() | LED 定位设备 |
| can.close() | 停止并释放适配器，不控制远端节点 |

list_devices 每个 CAN 接口返回一条 DeviceInfo；多通道设备共享序列号。
忙碌/访问失败的接口保留 error 信息，不会静默当作未连接。
只有一个可用适配器时可省略 serial；多设备必须指定。
open_can 默认经典 CAN 1 Mbps；使用 FD 时显式设置 data_bitrate。

## 常见收发流程

```python
from canable import list_devices, open_can, Frame, CanEventError

print(list_devices())

with open_can(bitrate=1_000_000, data_bitrate=4_000_000) as can:
    print(can.info)
    # 由应用确定要发送的帧后执行：
    # receipt = can.send(Frame(0x123, bytes([1, 2]), fd=True, brs=True))

    try:
        frame = can.recv(timeout=0.1)
        if frame is not None:
            print(frame.arbitration_id, frame.data)
    except CanEventError as exc:
        print(exc.event)  # 保留原始错误/未知事件

    print(can.read_status())
```

正常模式会参与总线 ACK；仅监听用 open_can(mode="listen_only", ...)。
经典 CAN 最多 8 字节；FD 支持合法 DLC 长度至 64 字节；不隐式补齐。
支持标准/扩展 ID、FD/BRS 和经典 CAN RTR。具体格式见 ADVANCED.md。

## 发送到底完成了什么

send 返回：USB 提交完成，返回 TxReceipt；不代表远端应用收到或执行。
receipt.wait(timeout) 等待适配器的 Tx Event；仍不是远端应用应答。
不需要同步等回执时直接继续程序，底层会自动匹配 Tx Event。
回执和接收队列独立处理，recv 过滤 Tx echo 不会破坏 receipt。

send_many 单批最多 63 帧且受 2048 字节缓冲区约束，不自动拆包，不自动重试。
USB 出错时可能已经发出部分帧，会明确报告不确定，不能盲目重放。
长时间运行需要持续接收事件，避免有限队列积压。

## 选择一种接收方式

普通应用用 recv()：
- 返回 Frame 或 None。
- 错误/未知事件抛 CanEventError，原事件在 exception.event。
- Tx echo 已由底层送到对应回执，不再作为 CAN 帧返回。
- 最近调试和负载事件保存在 last_debug_event、last_bus_load_event；
  这是最新值，不是完整诊断历史。

需要时间戳、完整错误/调试/负载流时，从一开始用 recv_event()：
```python
event = can.recv_event(timeout=0.1)
if event is not None and event.kind == "frame":
    print(event.frame, event.device_timestamp_us, event.host_timestamp_ns)
```

同一连接首次接收时选定模式，此后 recv 与 recv_event 禁止混用。
旧 receive() 保留为 recv_event() 的兼容别名，仍返回 Event。
同一连接同时只允许一个接收消费者，竞争调用明确报错，不互相抢事件。
不要绕过连接直接读 can.channel.receive() 后再使用上面的接口。

recv(0) 是非阻塞轮询；正数为秒；None 无限等待。
recv 返回的 Frame 不包含事件时间戳，精确时间记录请使用 recv_event。
传输异常、关闭、队列溢出仍以原异常抛出，不包装成空帧。

## 高级与维护能力

过滤、桥接、终端电阻、总线负载配置、用户数据区、BOOT0 和 DFU 入口保留在
原 Device/Channel API；见 ADVANCED.md。普通程序不必理解这些接口。
需要停止时配置的功能，应使用 Device.open → channel.configure → 配置 → start 流程，
而不是已经启动的 open_can 简化入口。
维护操作仍要求所有通道停止；enter_dfu 只是进入模式，不负责刷写。

本轮未增加发送优先级、业务应答匹配、自动重连、自动周期发送等策略。
该 SDK 只提供通用 CAN 帧通信，不解释帧中业务数据。

## 运行与验证

```powershell
.\sdk.ps1 list
.\sdk.ps1 info --serial YOUR_SERIAL
.\sdk.ps1 test
.\sdk.ps1 build
```

可用 CANABLE_PYTHON 指定 Python。安装用 python -m pip install .。
本轮只做离线接口与安装验证，未操作实机。
此前内部环回记录属于历史验收，不能代替新版外部总线/性能验证。
