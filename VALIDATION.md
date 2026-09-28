# 验证记录

日期：2026-09-28。源码依据：上游 `e862f6a`、固件版本 `0x260803`。
本次未修改上游固件，也未烧录适配器。

## 初版离线验证（连接设备前）

- Windows x64、Python 3.12.14，标准库 unittest：52 项通过。
- 固定协议向量：经典 CAN、FD/BRS/扩展 ID、RTR、Tx Echo、错误、混合 Rx Blob。
- 畸形数据：截断、非法长度、尾随字节、嵌套 Blob、2000 组固定种子随机输入。
- 位时序：160 MHz / 60 MHz 能力范围、精确波特率、不可能的波特率拒绝。
- 固件固定 64 字节拷贝边界、2048 字节批量上限、64 个待完成发送背压。
- 模拟设备：USB 成功但固件失败、控制/反馈串行化、多通道路由、串号变化、旧版本拒绝。
- 生命周期：关闭时先结束接收线程、故障传播、主机队列满、发送完成与 USB 提交区分。
- FD→经典 CAN 的内部环回复位命令序列、维护前真实通道状态检查、DFU 前读线程退出。
- Windows ctypes 结构体尺寸/偏移验证。
- 实际调用 Windows SetupAPI 枚举目标 GUID：返回 `[]`。此项仅验证本机枚举调用可执行，
  **没有访问到适配器的控制/收发端点**。
- 打包生成 `dist/canable25-0.1.0-py3-none-any.whl`，在独立目标目录安装并检查导入/CLI。

## 本次真机内部环回：通过

连接设备后验证，Windows x64 / Python 3.12.14。设备实际报告：

- 序列号：`20903498384550052`
- 产品：`Candlelight 2.5 - Multiboard`
- 板型信息：`STM32G431` / `Multiboard`，MCU Device ID `0x468`
- 固件 `0x260803`，硬件 `0x0200`，能力位 `0xE53B`，单通道、160 MHz CAN 时钟
- CAN 接口使用系统注册 GUID `D62F2386-83BC-4AB7-9E4A-2856D8E8DA56`

最终完整一轮 **24 组通过，共 1024 帧接收、1024 个 Tx Event**，内容、顺序及 marker 均匹配，
记录的主机丢弃数均为 0，主测试阶段查询的 Tx/Rx 错误计数均为 0。

- 经典标准/扩展帧，0、1、8 字节；标准/扩展 RTR，DLC 0、8。
- CAN FD 全部 16 种合法长度，标准/扩展 ID，BRS 开/关；仲裁 1 Mbit/s、数据 5 Mbit/s。
- 精确 64 字节及 2048 字节 USB OUT，验证结束边界，没有多余帧/重复 marker。
- 63 帧经典批量 ×10 轮、28 帧 FD 64 字节批量 ×10 轮，包含 8 位 marker 回绕。
- FD 已配置未启动→经典 500 kbit/s 配置，真实内部环回通过。
- 5 次正常关闭、重新打开、配置和收发；每次结束释放句柄。

原始报告：[hardware-loopback-20260928.json](validation/hardware-loopback-20260928.json)。
可复跑脚本：[hardware_loopback.py](examples/hardware_loopback.py)。
SDK 修复后版本为 **0.1.1**，**57 项离线回归测试通过**。
已生成并独立安装 `dist/canable25-0.1.1-py3-none-any.whl`，安装版只读枚举也成功找到该 CAN 接口。
测试结束后的只读查询为 `state=4 (Stopped), rx_errors=0, tx_errors=0`，USB 句柄已释放。

### 测试发现及处理

1. 初版只枚举固件默认 GUID，遗漏被已有驱动改过 GUID 的 CAN 接口。
   修复为只读查询该 VID/PID 绑定 WinUSB 的实际接口 GUID，再经 SetupAPI 过滤在场设备。
2. 64 字节 USB OUT 返回成功，但固件没有结束接收，3 秒内没有 Rx/Tx Event。
   增加按长度选择 `SHORT_PACKET_TERMINATE`；2048 字节满缓冲区不补 ZLP。
   [修复前记录](validation/hardware-loopback-20260928-before-zlp-fix.json)。
3. 批量占满 64 槽时，固件报告 `APP_CanTxOverflow=0x04`。SDK 将未决发送/单批上限设为 63，
   保留一槽；修复后 63 帧连续批量通过。
   [修复前记录](validation/hardware-loopback-20260928-before-queue-fix.json)。
4. 溢出后关闭并重开 USB，收到上一轮的残留 Rx 数据。
   [残留记录](validation/hardware-loopback-20260928-stale-after-failure.json)。
   这印证了跨会话不能只凭 8 位 marker 判断发送完成。本次在确认 CAN 已停止后显式排空旧 USB 数据，
   然后重新执行完整测试；常规使用要求异常后重新插拔适配器，不自动重连/重放。
   此限制没有被描述为已经彻底修复。

没有刷固件、改 BOOT0、写用户 Flash 或进入 DFU，也没有运行外部正常发送模式。
本次未进行最大吞吐或长时间压力测试，不从短时环回结果推导性能上限。

## 尚未完成的验收

| 测试 | 需要验证的证据 |
|---|---|
| 双适配器互通 | 真实收发、ACK、终端电阻、线缆及波特率一致 |
| 高负载 | 队列/固件溢出、不同帧长和速率下持续吞吐/延迟；不预设性能数字 |
| 异常 | 无 ACK、Bus Off、拔线时 pending read/write、挂起/恢复、接收者停滞 |
| 多设备/多通道 | 整台独占、不同 USB 接口、通道并发控制与收发、桥接 |
| 长时间 | 微秒计时回绕、重启归零、毫秒计时回绕、持续日志与资源占用 |
| 维护 | 用户段读写、BOOT0、DFU 进入/重新插拔，使用匹配的测试板另行执行 |
| 兼容 | 实际目标 Windows/Python 版本、不同 MCU/板型、不同 USB 主控 |

离线测试不证明目标硬件上可稳定运行，不证明 CAN 收发、实时性或电机控制正确。
异常发送结果不明时禁止自动重放；上位业务协议负责确认目标状态。

## 复现

仓库根目录：

```powershell
.\sdk.ps1 test
.\sdk.ps1 list
```

安装了构建工具时：

```powershell
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir ./dist
```

本次内部环回不能证明外部收发器、电气连接、远端 ACK 或电机业务协议正确。

## 桌面独立项目复验

目录：`C:\Users\32196\Desktop\canable25-sdk`。独立项目无需原固件仓库即可运行。

- 源文件复制完整性已检查，非预期修改的文件 SHA256 一致。
- 路径与构建说明已改为独立项目根目录，增加 UPSTREAM.md 记录固件来源。
- 独立目录重新执行 57 项离线测试：通过。
- 独立目录完整真机内部环回复跑：24 组、1024 Rx、1024 Tx Event，全通过。
  报告：[standalone-loopback-20260928.json](validation/standalone-loopback-20260928.json)。
- 完善 sdk.ps1：自动筛选兼容 Python；构建时避开系统旧 setuptools，使用已有合格环境。
- 使用 sdk.ps1 build 独立生成 0.1.1 wheel，未升级系统 Python 或依赖。
- wheel 安装到独立验证目录，以系统 Python 3.11 执行同一套 57 项测试：通过。
- 安装版 SDK 只读查询设备最终状态：Stopped，Tx/Rx 错误计数均为 0。
- 源码、文档、示例、测试、许可说明、原始验证记录提交到本地 Git；dist/build/cache 不入库。