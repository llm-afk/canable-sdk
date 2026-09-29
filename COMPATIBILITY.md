# 兼容性

## 固件

支持 Elmue Candlelight 固件 >= `0x260618`，且要求 Elmue 能力位。
传统 gs_usb 和 SLCAN 不在支持范围内。协议来源见 [UPSTREAM.md](UPSTREAM.md)。

## Python 入口

发行包为 `canable-sdk`，推荐导入名与命令行入口为 `canable`。
`canable25` 导入、子模块和命令行入口作为兼容别名提供，与 `canable` 共享实现及类型。
`receive()` 是 `recv_event()` 的别名，返回完整 Event。

依赖声明应使用 `canable-sdk>=0.1.4,<0.2`。
Python 导入别名不能满足名为 `canable25` 的 pip 发行包依赖。
安装环境已有 `canable25` 发行包时，应先卸载该包，再安装 `canable-sdk`，
避免两个发行包管理相同模块文件。

启动脚本优先使用 `CANABLE_PYTHON`，也接受 `CANABLE25_PYTHON`。
