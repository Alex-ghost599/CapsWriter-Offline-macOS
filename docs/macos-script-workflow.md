# macOS 脚本启动、停止与双 Mac 麦克风

本入口适用于已有 Python 3.12 `.venv`、冻结依赖和离线模型的 Apple Silicon Mac。它直接运行源码，不依赖外层 GUI 启动器；不自动安装依赖、下载模型、调整 VPN 或更改隐私权限。打包的 `dist/CapsWriter.app` 可继续用于文件转录。

## 获取与本机准备

使用包含本页及三个 `scripts/` 管理脚本的完整分支或提交，不要只复制 `.command` 文件。旧 `v2.6.0-macos.2` tag 不包含本入口。本次提交合并前，可在全新目录获取：

```sh
git clone --branch feat--macos-script-microphone --single-branch \
  https://github.com/Alex-ghost599/CapsWriter-Offline-macOS.git \
  "$HOME/CapsWriter-Offline-macOS"
cd "$HOME/CapsWriter-Offline-macOS"
uv python install 3.12
uv sync --all-groups --frozen
uv run --frozen --no-sync python scripts/download_macos_models.py
```

已有安装请先保留自己的修改和数据，再合并目标提交；不要覆盖 `.venv`、模型或个人录音。`uv`、FFmpeg 等先按 [macOS 安装说明](macos-setup.md) 准备。首次使用的系统权限由用户按实际启动宿主与客户端处理，已有 App 授权不保证 Terminal 源码链路也有权限；不要自动重置 TCC 或为通用解释器扩大权限。

## 启动与选择

在 Finder 双击仓库根目录 `CapsWriter.command`，由系统 Terminal 启动：

- **1 远端麦克风**：先经 SSH 打开远端 SonoBus，再打开本机 SonoBus，自动请求加入同一组，然后启动 Caps；识别设备固定为 `BlackHole 2ch`。
- **2 本机麦克风**：使用当前系统默认输入并打印设备名；不启动、重配或关闭 SonoBus，不修改系统默认音频。

选择 1 会自动应用：

| 位置 | 顶部 MUTE（输入静音） | Send Mute（发送静音） |
| --- | --- | --- |
| 本机接收端 | On / `1.0` | On / `1.0` |
| 远端麦克风端 | Off / `0.0` | Off / `0.0` |

两端已有专用 SonoBus 都会先停止再重开，以加载设置；重要界面调整应先正常退出保存。脚本保留其他保存配置，并在静音配置变化时备份；不会强制重设 Monitor、Record 等开关。远端原生保存配置和启用预设同步更新，避免仅写预设而实际仍静音。正常情况下不需手点这些静音开关。

显示“后台输入已就绪”后，把光标放在输入框，按住本机右 Shift 超过 0.3 秒说话，句尾留约 1 秒再松开。关闭 Terminal 窗口会保留后台。

已有 Caps 后台的输入设备或模式不同、未知或处于仅服务模式时，脚本拒绝静默切换：先执行联合停止，再重新选择。重复同模式启动会复用 Caps 后台，但选择 1 仍会重启两端 SonoBus。

命令行等价入口（在仓库根目录）：

```sh
./CapsWriter.command start --microphone remote
./CapsWriter.command start --microphone local
./CapsWriter.command status
./CapsWriter.command start --server-only
./CapsWriter-stop.command
```

## 停止和失败处理

`CapsWriter-stop.command` 先停止本入口管理的 Caps 客户端/服务，再检查并关闭两端精确路径的专用 SonoBus，包括手动打开的实例。SonoBus 进程身份按用户、路径、PID 和启动时间核对；先 SIGTERM，必要时有界 SIGKILL。确认远端退出后，将其原生保存配置恢复为双静音 `1.0`。运行期间远端保存配置为双解除静音；联合停止成功后再手动打开会保持静音。此入口不停止 VPN、UU 或其他 Python/音频进程。

SSH 明确在连接建立前失败时最多重试一次；执行后断连或回执丢失不自动重试。远端不可达时仍清理本机并返回非零，不能因此声称远端已停或已恢复静音。连接恢复后再次运行停止入口核实。启动中途失败会回退本次新开的实例，包括由本次重启产生的实例；清理身份或回执不明时保留未知状态。

状态、控制 socket 和管理日志在 `~/Library/Application Support/CapsWriter-Offline/script-runtime/`；其中 `launcher.log`、`client.log` 和 `server.log` 可用于当次排错，可能包含个人转录，不要公开上传。热词、录音与日记默认位于上一级应用数据目录，显式 `CAPSWRITER_HOME` 会保留。

## 确认真正连接和解除静音

Caps 的 `status`/ready 与“已请求加入组”只说明启动状态，不能代替音频验收。在本机 SonoBus 检查：

1. 同一组内存在本机接收端和远端麦克风端两名成员。
2. 本机顶部 MUTE、Send Mute 均 On，接收远端的 MUTE 为 Off。
3. 出现 **Other side is muted** 表示远端发送仍静音；看到实际接收声道、流量和活动电平才有实时音频证据。

2026-10-17 两 Mac 实测：修复前远端显示 `0ch / Other side is muted`；同步原生启动配置后显示 `1ch / 96 kbps` 并有活动电平。远端停止后双静音恢复，再次启动重新发送。此记录证明当时的通道，不能代替新机器的设备、权限和真人右 Shift 上屏验收。

## 其他机器复用前需核对

当前远端模式带有一套两 Mac 的默认值，没有通用设备自动发现或配置界面：

| 对象 | 当前默认值 / 核对位置 |
| --- | --- |
| SonoBus App | 两端均为各自 `~/Applications/SonoBus.app` |
| SSH 目标 | `scripts/capswriter_entry.py` 中的 alias `macmini2`；本机 SSH 配置须能非交互登录正确主机 |
| 远端身份保护 | `scripts/sonobus_endpoint.py` 要求 ComputerName `Alex_Mac_mini_2`、home `/Users/alex`；换远端时先人工核实并调整 |
| 远端输入 / 本机输出 | 已保存的 `UGREEN CM564 USB Audio` / `BlackHole 2ch`；SonoBus 音频路由由用户预先设置 |
| 组服务器 / 组名 | `sonobus_endpoint.py` 的 `SERVER`、`GROUP`；默认 `10.10.0.5:10999` 和专用组 |
| 显示名 | `sonobus_endpoint.py` 的 `macmini2-Mic`、`Current-Mac-Receive`；同时使用多接收端前另设唯一显示名并验证 |

继续使用原 macmini2 作为麦克风端时，另一台接收 Mac 仍需安装 SonoBus、BlackHole，配置上述路由和 SSH；此脚本不会部署远端软件。仅用选择 2 时无需这套远端设施。克隆或更新仓库不会安装本机 Codex skill；后续 agent 可直接读取本页。

## 文件转录

文件转录使用独立任务副本和数据目录，确认后可复用既有 Caps 服务；只停止本次任务自己创建的服务。不要用联合停止脚本清理文件任务，它会同时中断日常实时输入和两端 SonoBus。不要为文件转录启动远端麦克风或请求录音/键盘权限。
