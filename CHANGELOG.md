# 0.1.4

- 项目、桌面主目录和 GitHub 仓库统一命名为 canable-sdk。
- Python 主入口改为 canable，保留 canable25 导入、子模块及 CLI 兼容入口。
- 固件支持范围和通信协议不变；历史验证文件保留原始版本与路径。
- 发行包名变更需要下游更新依赖声明；仅 Python 导入保持兼容。

# 0.1.3

Added list_devices(), recv(), recv_event(), info, read_status(), identify(). Frame and event receive modes cannot mix; a second concurrent consumer is rejected. Error/unknown events remain visible through CanEventError. Legacy receive/Device/Channel contracts retained. 72 tests passed from installed wheel; no hardware test this turn.

# 0.1.2

新增 Connection/open_can：打开、配置、启动、上下文退出释放。
原 Device/Channel、事件流和 TxReceipt 语义保持不变。
没有新增隐藏接收线程或电机协议逻辑。

62 项离线测试通过；与 mc_t 0.2.0 wheel 一起安装后再次通过测试。
记录：validation/application-api-0.1.2.json。
本轮未运行硬件；原有硬件内部环回记录属于此前版本。
