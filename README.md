# CANable 2.5 Windows Python SDK

独立、零第三方运行时依赖的 Python 包，基于 Windows 自带 WinUSB，通过 `ctypes` 调用。
第一版实现上游 CANable 固件的 **Candlelight / Elmue 扩展协议**。SLCAN 是后续独立后端，目前未实现。
当前版本 0.1.1。已经过离线验证及一台 STM32G431 Multiboard 的内部环回验收；
外部 CAN 总线互通、性能、长时间运行仍待验收。

## 支持范围

- Windows 原生 USB；Python 3.10+。当前验证环境是 Windows x64 / Python 3.12。
- 协议依据：上游固件 `e862f6a`（见 [UPSTREAM.md](UPSTREAM.md)），固件版本 `0x260803`。
- 要求固件版本 >= `0x260803`、Elmue 能力位；更高版本按能力探测，并不表示全部已实测兼容。
- STM32G431 / G473 / G0B1 的时钟和位时序范围从设备读取，不把 160 MHz 写死。
- 按真实 USB 序列号选择设备；支持多设备、多通道；接口由描述符发现。
- 经典 CAN、CAN FD、BRS、11/29 位 ID、RTR、批量收发、硬件时间戳。
- 普通、只听、内部/外部环回、单次发送；错误状态、负载报告、过滤和跨通道桥接。
- 板型信息、LED 识别、软件终端电阻（按硬件能力）、BOOT0 状态、用户数据区。
- 显式进入 DFU；**不包含 DFU 镜像解析、下载、刷写和校验器**。
- 不自动重连、不重放未确认发送，不内置 CANopen、MIT 或任何电机业务协议。

传统 gs_usb/旧版 Candlelight 不会被悄悄降级使用；不符合版本/能力要求会明确拒绝。
本包名称为 `canable25`，避免与现有 `python-can`、`gs_usb`、`candle-api` 包混淆。

## 安装与直接运行

在本仓库根目录，已有 Python 时：

```powershell
python -m pip install .
python -m canable25 list
python -m canable25 info --serial YOUR_SERIAL
```

也可以不安装，使用自动查找 Python 的入口：

```powershell
.\sdk.ps1 list
.\sdk.ps1 info --serial YOUR_SERIAL
.\sdk.ps1 test
```

`sdk.ps1` 支持用 `CANABLE25_PYTHON` 指定解释器，否则查 PATH、Codex Python 和常见用户安装路径。所有候选都会检查 Python >=3.10；`build` 还会检查 setuptools>=77 和 wheel，自动跳过不满足条件的环境。
它仅临时修改当前脚本的 `PYTHONPATH`，不修改系统 PATH。找不到时提示安装 Python。

`list` / `info` 只读取 USB 描述符、固件版本和能力，**不会 reset、start CAN 或切换 DFU**。
无设备时输出 `[]`。忙碌/无权限的接口保留 `path` 与 `error`，不会被当成“没插设备”静默忽略。
枚举会只读查询该 VID/PID 的 WinUSB 注册 GUID，兼容 INF/Zadig 改过 GUID 的 Windows 安装，
不修改驱动或注册表。本次真机使用的 CAN GUID 为 `D62F2386-83BC-4AB7-9E4A-2856D8E8DA56`。
`info` 只读取无需激活 Elmue 的字段；更详细的板型通过配置后 `device.board_info()` 查询。

## 最小用法

```python
from canable25 import Device, Frame

with Device.open(serial="YOUR_SERIAL") as device:
    channel = device.channel(0)
    channel.configure(bitrate=1_000_000, data_bitrate=5_000_000)
    print(channel.nominal_timing, channel.data_timing)
    print(device.board_info())
    channel.start()

    receipt = channel.send(Frame(0x123, bytes(range(12)), fd=True, brs=True))
    completion = receipt.wait(timeout=1.0)
    print(completion)

    event = channel.receive(timeout=0.5)
    if event and event.kind == "frame":
        print(event.frame.arbitration_id, event.frame.data)
```

此示例会真实发 CAN 帧。`examples/receive.py` 为只听示例，`examples/send_once.py` 为显式单帧发送。

设备身份与生命周期：

1. `discover()` 只读发现。
2. `Device.open()` 独占同一物理设备的所有 CAN USB 接口，仅打开 USB 句柄。
3. `device.channel(n)` 获取通道对象，不开始 CAN。
4. `configure()` 停止该通道、初始化配置、计算并设置位时序，最后保持停止。
5. `start()` 才进入用户指定的总线模式。
6. `stop()` 清除配置，下一次启动必须重新 `configure()`。
7. `close()` 停止通道、取消并等待 USB 读完成、退出线程，最后释放句柄。可重复调用。

**固件兼容细节：** 当前固件对“已经关闭”的通道执行 reset 不会清除先前设置的 FD 位时序。
为保证 FD→经典 CAN 的重新配置不会残留，`configure()` 会在内部进行一次“内部环回启动→停止”，
不提交任何帧，也不对外发送 CAN 或 ACK；随后设置最终配置。它会改变控制器状态，所以 `configure()`
不是只读 API。内部环回能力是本版要求之一。该兼容路径已在本次 STM32G431 Multiboard 上验证。

`configure()` 会清除本通道之前的过滤器、桥接配置和负载报告间隔；按下面顺序配置：

```python
channel.configure(bitrate=500_000)
channel.add_filter(0x180, 0x7F0)
channel.set_bus_load_interval(1000)
channel.start(mode="listen_only")
```

## 接口与契约

| 对象 | 接口 | 说明 |
|---|---|---|
| 全局 | `discover(include_dfu=False)` | 只读枚举，返回 `DeviceInfo` 列表 |
| Device | `open(serial=...)` / `channel(index)` | 自动选择只允许恰好一台设备；多台必须指定 SN |
| Device | `board_info()` / `boot_pin_enabled()` | configure 激活协议后查询 |
| Device | `read_user_data()` / `write_user_data(bytes)` | 仅保留用户段 0，最多 2000 字节；空数据擦除 |
| Device | `disable_boot_pin()` | 明确的持久化 Option Bytes 操作，正常连接不会调用 |
| Device | `enter_dfu()` | 所有通道停止后调用；返回请求状态，不代表刷写成功 |
| Channel | `configure(...)` | 精确匹配波特率，选择最接近目标采样点的合法时序 |
| Channel | `start(mode=..., one_shot=False)` | `normal/listen_only/internal_loopback/external_loopback` |
| Channel | `send(frame, echo=True)` | 返回 `TxReceipt`，默认请求固件 Tx Event |
| Channel | `send_many(frames, echo=True)` | 一个 USB 传输；1–63 帧且总长度 <=2048 字节 |
| Channel | `receive(timeout=1.0)` | 返回下一事件；超时返回 `None`，关闭/故障抛异常 |
| Channel | `identify(enabled)` | LED 定位设备 |
| Channel | `set/get_termination()` | 无硬件能力立即抛 `UnsupportedError` |
| Channel | `error_state()` | 返回原始状态及 Tx/Rx 错误计数 |
| Channel | `set_bus_load_interval(milliseconds)` | 0 关闭；100–10000 ms，100 ms 步进 |
| Channel | `add_filter()/clear_filters()` | 停止时配置，硬件总共最多 8 个 host filters |
| Channel | `set_bridge_filter()/clear_bridge_filters()` | 停止时配置，20 个槽，另一个通道为目标 |
| Channel | `stop()/close()` | 清理通道；存在未决发送时不能安全原样重用 |

过滤语义为 `(received_id & mask) == (filter_id & mask)`。
固件一旦配置过滤器，只配置标准帧过滤器会同时挡住未匹配的扩展帧，反之亦然。
桥接目标必须另外配置并启动；不要在上位机和固件同时转发相同的帧，避免环路。
维护 API 会查询所有通道的真实固件状态，要求全部停止，不仅检查 SDK 本地变量。

### 帧、批量与发送完成

- `Frame` 是不可变对象，复制传入的 bytes-like 数据，避免调用者之后修改发送内容。
- CAN FD 长度仅允许 0–8、12、16、20、24、32、48、64；不会默默补齐，调用者显式补零。
- `Frame(id, remote=True, remote_dlc=8)` 表示 RTR，`data` 必须为空；FD 不允许 RTR。
- `esi` 为接收属性，发送时拒绝由应用强制指定。
- 数据速率不得低于仲裁速率；带 BRS 的帧要求数据速率严格更高，避免固件静默清除 BRS。
- `send_many` 不自动拆包，避免部分已提交却给出“整批失败”的错误印象。64 字节 FD 单批最多 28 帧。
- 混合长度批量还会校验固件的固定 64 字节拷贝边界；即使报文总长小于 2048，也可能要求拆包。
- `receipt.submitted_ns` 仅说明 USB 写完成；`receipt.wait()` 返回固件 Tx Event。
- 正常自动重发模式、单次发送模式、内部环回的 Tx Event 证据不同；它从不代表电机执行或应用层回复。
- `wait()` 超时不撤回 CAN 帧、不重试、不释放 marker。最多允许 63 个待完成 receipt，随后明确背压。
  真机测试中用满固件的 64 槽池会触发溢出报告，SDK 因此保留一个空槽。
- `echo=False` 使用 marker 0，只报告 USB 提交；接收不到发送完成，也无法用 receipt 做背压。
- 批量不是事务，USB 错误时部分帧可能已经发送。`SendUncertain` 不能简单 catch 后重发。
- marker 只有 8 位，不提供跨复位、跨进程或异常重连的 exactly-once 语义。
  异常断开或遗留发送无法确定时，先重新上电适配器，再建立新会话；同时由业务协议确认目标实际状态。

### 接收、时间戳与线程

`receive()` 返回 `Event`，`kind` 可为 `frame/tx_echo/error/debug/bus_load/unknown`。
不把错误帧混成 CAN 数据，不在 SDK 内打印调试消息，不静默丢弃未知消息；原始 `raw` 始终保留。

`device_timestamp_us` 是固件原始 32 位微秒值，约 71.6 分钟回绕，首次启动 CAN 时也可能归零。
不将它伪装成 Windows 墙钟或跨会话连续时间。`host_timestamp_ns` 是 `perf_counter_ns()` 的读完成时间；
同一 USB Blob 中的事件共享主机时间。需要精密时间对齐时，应在业务层保存会话边界及同步证据。

每个通道一个后台读线程，IN 读保持 pending；`receive(timeout)` 只等待主机队列，不取消 USB 读。
对 overlapped 读，完成字节数在完成后通过 WinUSB 获取，参见
[Microsoft WinUsb_ReadPipe 文档](https://learn.microsoft.com/en-us/windows/win32/api/winusb/nf-winusb-winusb_readpipe)。

OUT 长度是 USB 最大包长的整数倍且小于固件 2048 字节接收缓冲区时，开启短包终止（补 ZLP）；
恰好 2048 字节时关闭它，因为固件已经收满并重新准备接收。64 和 2048 字节边界均已真机验证。
WinUSB 策略含义见 [Microsoft 管道策略文档](https://learn.microsoft.com/zh-cn/windows-hardware/drivers/usbcon/winusb-functions-for-pipe-policy-modification)。

默认事件队列容量 4096，可用 `device.channel(0, queue_size=8192)` 修改首次创建容量。
Tx echo 和调试事件也占队列，长期测试必须持续消费。队列满会计数并抛 `QueueOverflow`，不会静默覆盖旧帧。
后台读/解析故障会传给前台和未完成 receipt。固件报告丢帧/发送错误时保留错误事件，并禁止继续提交。

生命周期和维护调用由一个拥有者线程串行执行；允许同时运行发送线程与一个事件消费线程。
多发送线程在通道内串行化。设备级控制命令与 `GetLastError` 是不可拆分的临界区。
不要在同一物理适配器上混用本 SDK 与其他程序或旧 GS 协议。关闭时若读线程未退出，保留句柄并报错，
不强制释放仍在使用的缓冲区。无自动热插拔重连，重新发现必须由上层明确决定。

## CLI

```powershell
# 只读
.\sdk.ps1 list
.\sdk.ps1 info --serial YOUR_SERIAL

# 以下明确配置控制器：只听，不发 ACK；对端仍需其他正常节点提供 ACK
.\sdk.ps1 monitor --serial YOUR_SERIAL --bitrate 1000000 --data-bitrate 5000000 --seconds 10

# 内部环回，不向外部 CAN 总线发测试帧
.\sdk.ps1 loopback --serial YOUR_SERIAL --id 0x123 --data 01020304

# 真实发送一帧
.\sdk.ps1 send --serial YOUR_SERIAL --id 0x123 --data 01020304
```

FD 帧另加 `--fd`，需要 BRS 时再加 `--brs`，同时必须指定 `--data-bitrate`。
例如 8 Mbit/s 的采样点应结合板卡实测选择；SDK 不把作者某块板的结果当作所有板的保证。

## 目录与后续复用

```text
src/canable25/
  models.py       帧、事件、设备信息，不带电机语义
  interfaces.py   业务代码依赖的 CanChannel Protocol
  protocol.py     纯编解码和位时序计算
  winusb.py       Windows API、USB 传输和句柄
  device.py       设备归属、通道管理、控制事务和维护
  channel.py      生命周期、队列、收发与完成事件
  __main__.py     显式 CLI
tests/            标准库 unittest，不需要设备
examples/         接收与发送脚本
```

以后增加 SLCAN：保留 `Frame/Event/CanChannel` 契约，新增串口传输与 ASCII 编解码及会话实现。
不支持的能力明确报错；不把“串口命令成功”伪装成硬件发送完成。CANopen/MIT/产测报告作为上层包。
本版没有依赖 python-can；之后可单独实现 python-can 适配层，无需把电机协议塞入传输 SDK。

## 验证

```powershell
.\sdk.ps1 test
```

本次内部环回验收可按原参数复跑（会配置/启停内部环回，不向外部 CAN 发帧）：

```powershell
.\sdk.ps1 loopback-suite --serial 20903498384550052 --rounds 10 --output .\validation\hardware-loopback-repeat.json
```

若上一次测试异常中断，应先重新插拔设备再完整复跑；单纯关开 USB 句柄不会清空固件的全部旧数据。

详见 [VALIDATION.md](VALIDATION.md)。真机验收应分别记录固件版本、板卡、USB 控制器、CAN 速率、
帧长度、吞吐/延迟、丢帧计数，不能仅凭内部环回判断线缆、收发器、ACK 或多节点互通正常。
原始来源及许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 独立构建

在本项目根目录执行 `.\sdk.ps1 build`，输出到 `dist/`。构建会使用已经具备所需构建工具的 Python，不升级系统环境。完整来源基线见 [UPSTREAM.md](UPSTREAM.md)。
