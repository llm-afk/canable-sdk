# CANable SDK 0.1.5

面向 CANable USB-CAN 适配器的 Windows Python SDK，提供设备发现、连接管理、CAN 帧收发和控制器状态查询。
基于 Windows WinUSB，Python >=3.10，无第三方运行时依赖。
面向当前 Elmue Candlelight 扩展固件，SLCAN 尚未实现。

## 安装

项目 / GitHub 仓库：`canable-sdk`；Python 导入与命令：`canable`。
在项目根目录执行：

```powershell
python -m pip install .
python -m canable list
```

`python -m pip install .` 将当前目录的 SDK 安装到所选 Python 环境，
安装后应用可在任意目录使用 `from canable import ...`。
也可以将 `.` 换成 SDK 的绝对路径。此操作不安装 USB 驱动，也不烧录适配器固件。
每个 Python 环境单独安装；普通安装后修改 SDK 源码，需要重新安装才能使用修改后的代码。
要求 Elmue Candlelight 固件 >= `0x260618`，且设备支持 Elmue 扩展协议。

应用依赖声明使用 `canable-sdk>=0.1.5,<0.2`。兼容入口与安装注意事项见 [COMPATIBILITY.md](COMPATIBILITY.md)。

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

## 打开连接与配置

`open_can()` 完成设备选择、参数检查、通道配置和启动。退出 `with` 时释放资源。

**所有配置参数都是可选的，省略时使用下表中的默认值。**
仅有一个可用适配器时，最简用法为：

```python
from canable import open_can

with open_can() as can:
    frame = can.recv(timeout=0.1)
    if frame is not None:
        print(hex(frame.arbitration_id), frame.data.hex())
```

此时使用第 0 通道、1 Mbps 经典 CAN、正常收发模式，接收全部 CAN ID。
正常模式会参与总线 ACK。`recv()` 从接收队列取数据，不会发送查询指令。

只需填写与默认值不同的参数：

```python
# 500 kbps 经典 CAN
with open_can(bitrate=500_000) as can:
    frame = can.recv(timeout=0.1)

# CAN FD：1 Mbps 仲裁域、4 Mbps 数据域
with open_can(data_bitrate=4_000_000) as can:
    frame = can.recv(timeout=0.1)

# 多适配器时，通过序列号选择
with open_can(serial="YOUR_SERIAL") as can:
    frame = can.recv(timeout=0.1)
```

**默认配置不会自动识别总线参数。** 波特率、FD 数据速率等必须与所连接的总线一致。
启用 FD 后，发送 FD 帧还需设置 `Frame(..., fd=True)`；使用速率切换时加 `brs=True`。

下面展示多个配置项的组合用法，不要求每次都完整填写：


```python
from canable import open_can, CanFilter

with open_can(
    bitrate=1_000_000,
    data_bitrate=4_000_000,
    sample_point=0.75,
    data_sample_point=0.75,
    mode="normal",
    one_shot=False,
    queue_size=8192,
    filters=[CanFilter(can_id=0x181, mask=0x7FF)],
    termination=None,
    bus_load_interval_ms=1000,
) as can:
    frame = can.recv(timeout=0.1)
```

| 参数 | 默认值 | 含义 |
|---|---|---|
| serial | None | 自动选择唯一可用适配器，多设备时指定序列号 |
| channel | 0 | CAN 通道号 |
| bitrate | 1000000 | 经典 CAN / FD 仲裁域速率，bit/s |
| data_bitrate | None | FD 数据域速率；None 使用经典 CAN |
| sample_point | 0.75 | 仲裁域目标采样点，0 到 1 之间 |
| data_sample_point | 0.75 | FD 数据域目标采样点，0 到 1 之间；仅启用 FD 时使用 |
| mode | normal | normal、listen_only、internal_loopback、external_loopback |
| one_shot | False | True 禁用控制器自动重发 |
| queue_size | 4096 | 主机接收事件队列容量，包含帧、回执与诊断事件 |
| filters | None | 接收过滤规则，最多 8 条；None 或空列表接收全部 ID |
| termination | None | 不改变终端电阻；True/False 要求硬件支持软件控制 |
| bus_load_interval_ms | None | 不显式设置上报周期；0 关闭；100–10000 ms、步长 100 |

`None` 的含义取决于参数：`data_bitrate=None` 表示经典 CAN，
`filters=None` 表示接收全部 ID；`termination=None` 表示不改变终端电阻状态，
`bus_load_interval_ms=None` 表示不主动设置负载上报周期。
需要明确关闭终端电阻时使用 `termination=False`（要求硬件支持），
需要明确关闭负载上报时使用 `bus_load_interval_ms=0`。

过滤规则按 `(received_id & mask) == (can_id & mask)` 匹配，多条规则为“或”关系。
`CanFilter(0x181, 0x7FF)` 精确匹配标准 ID 0x181；
`CanFilter(0x180, 0x7F0)` 匹配标准 ID 0x180–0x18F。
扩展帧规则使用 `extended=True`，ID 和掩码上限为 0x1FFFFFFF。
存在规则时，不匹配的帧不上传主机；标准帧规则不会匹配扩展帧。

过滤规则在通道启动前写入。配置失败不会进入请求的工作模式，SDK 尝试关闭设备并报告异常。
底层位时序配置包含不发送数据的内部环回初始化；这不代表已启动正常 CAN 通信。
队列容量不改变设备发送缓冲区大小。负载事件通过 recv_event() 读取，
或在调用 recv() 消费事件后查看 last_bus_load_event。
硬件不支持软件终端电阻时，请保持 termination=None，使用板上的物理开关或跳线。

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
receive() 是 recv_event() 的别名，返回 Event。
同一连接同时只允许一个接收消费者，竞争调用明确报错，不互相抢事件。
不要绕过连接直接读 can.channel.receive() 后再使用上面的接口。

recv(0) 是非阻塞轮询；正数为秒；None 无限等待。
recv 返回的 Frame 不包含事件时间戳，精确时间记录请使用 recv_event。
传输异常、关闭、队列溢出仍以原异常抛出，不包装成空帧。

## 高级与维护能力

接收过滤、终端电阻和负载周期可通过 open_can 配置。
多通道桥接、用户数据区、BOOT0 和 DFU 使用 Device/Channel API，见 ADVANCED.md。
多通道桥接需要显式管理源通道和目标通道的配置与启动。
维护操作仍要求所有通道停止；enter_dfu 只是进入模式，不负责刷写。

SDK 负责 CAN 帧传输；发送调度、业务应答匹配、重连和周期控制由应用层管理。

## 运行与验证

```powershell
.\sdk.ps1 list
.\sdk.ps1 info --serial YOUR_SERIAL
.\sdk.ps1 test
.\sdk.ps1 build
```

可用 CANABLE_PYTHON 指定 Python。安装用 python -m pip install .。
0.1.5 的源码及安装包均通过 83 项离线测试。实机内部环回记录对应 0.1.3；
外部总线互通与持续性能尚未验证。测试范围与报告见 [VALIDATION.md](VALIDATION.md)。
