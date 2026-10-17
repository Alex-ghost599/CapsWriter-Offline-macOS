# CapsWriter-Offline-macOS

![demo](assets/demo.png)

> [!NOTE]
> 本仓库基于 [@HaujetZhao/CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline)
> 进行 macOS 可用适配；上游仍以 Windows 为主要支持平台。

> **Windows 按住 CapsLock，macOS 按住右 Shift；说话后松开就上屏。**

**CapsWriter-Offline** 是一个**完全离线**语音输入工具，支持 Windows；本 fork 另提供
Apple Silicon macOS 手动构建与运行适配。

## macOS 日常脚本入口

已有环境可使用仓库根目录的 `CapsWriter.command` 和 `CapsWriter-stop.command`，支持本机输入及 SonoBus 双 Mac 远端麦克风；远端模式自动设置本机双静音、远端双解除静音，停止后恢复远端静音。详见 [脚本启停、自动静音与其他机器复用](docs/macos-script-workflow.md)。此入口需要包含脚本的完整分支或提交，下面旧 Release 的安装包不包含这些新文件。

## macOS 源码安装实验版

[`v2.6.0-macos.2`](https://github.com/Alex-ghost599/CapsWriter-Offline-macOS/releases/tag/v2.6.0-macos.2)
提供可审查的源码安装脚本，不包含预编译 App 或模型。需要 Apple Silicon Mac、网络连接、
[Homebrew](https://brew.sh) 和约 5 GB 可用空间。

### 1. 下载、校验并安装

```bash
INSTALLER_DIR="$(mktemp -d "${TMPDIR:-/tmp}/capswriter-installer.XXXXXX")"
cd "$INSTALLER_DIR"
BASE_URL=https://github.com/Alex-ghost599/CapsWriter-Offline-macOS/releases/download/v2.6.0-macos.2
curl -fLO "$BASE_URL/install-macos.sh"
curl -fLO "$BASE_URL/SHA256SUMS"
shasum -a 256 -c SHA256SUMS
printf '%s  %s\n' \
  'b8d6f8fc861ae64c46a10e0cd9b224a18fe39323a6d4ba29673995cdf2e57b77' \
  'install-macos.sh' | shasum -a 256 -c -
bash install-macos.sh
```

两次校验都必须显示 `install-macos.sh: OK`。脚本会固定安装该 tag，以 `uv` 管理 Python 3.12
和冻结依赖，默认校验下载模型并在本机构建 `dist/CapsWriter.app`。默认目录是
`~/CapsWriter-Offline-macOS`；只要该路径已经存在，
安装器就会在 Homebrew 前停止，请用 `--install-dir <新路径>` 另选目录，不要删除或覆盖旧目录。
使用自定义目录时，也要把后文启动命令中的默认目录替换成该路径。用 `--skip-models` 可暂不
下载约 530 MB 模型，但在补下载前不能完成真实听写验收。

### 2. 启动本地服务和客户端

安装器不会自动启动程序。先在一个终端保持服务端运行：

```bash
cd ~/CapsWriter-Offline-macOS
CAPSWRITER_SERVER_BIND=127.0.0.1 CAPSWRITER_SERVER_PORT=6016 \
  CAPSWRITER_MODEL_TYPE=paraformer uv run python start_server.py
```

再开一个终端启动客户端：

```bash
open ~/CapsWriter-Offline-macOS/dist/CapsWriter.app
```

### 3. 授权并完成第一次真人验收

在“系统设置 > 隐私与安全性”中为最终的 `CapsWriter.app` 开启麦克风、辅助功能和输入监控，
然后退出并重新打开 App。打开 TextEdit，把光标放入文档，由本人按住右 Shift 说话并松开；
只有识别文本实际写入前台输入框，才算第一次安装完成。测试句不要包含姓名、账号或工作内容等
敏感信息，因为录音、日志和转写结果默认会保存在本机。每台 Mac 都要分别安装和授权。

完整参数、模型延后下载、配置和排错见 [macOS 安装与运行](docs/macos-setup.md)；安全边界、
失败续接和逐项验收见 [Agent 安装执行规约](docs/agent-macos-install.md)。

### 4. 交给本机 Agent 协助安装

在目标 Mac 上打开具备终端访问能力的 Codex、Claude Code 或其他 agent，把下面整句话复制给它。
agent 会先读取[安装执行规约](docs/agent-macos-install.md)，执行可自动化步骤，并在 Homebrew、
系统权限和真人发声等环节请求你确认：

> 请先完整阅读这份安装执行规约：<https://github.com/Alex-ghost599/CapsWriter-Offline-macOS/blob/develop/docs/agent-macos-install.md>；然后严格按文档在这台 Apple Silicon Mac 上协助我安装和配置 CapsWriter-Offline-macOS：你可以执行必要的终端命令并验证结果，但不得删除或重置已有目录、不得跳过 SHA-256 校验，未经我明确同意不得跳过模型，安装 Homebrew、修改配置或需要 macOS 隐私权限时先征得我确认，最后由我完成真人右 Shift 录音并确认文本实际写入前台应用。

## ✨ 核心特性

-   **语音输入**：按住 `CapsLock键` 或 `鼠标侧键X2` 说话，松开即输入，超低延迟，默认去除末尾逗句号。支持对讲机模式和单击录音模式。
-   **文件转录**：音视频文件往客户端 exe 一丢，字幕 (`.srt`)、文本 (`.txt`)、时间戳 (`.json`) 统统都有。
-   **数字 ITN**：自动将「十五六个」转为「15~16个」，支持各种复杂数字格式。
-   **热词替换**：在 `hot.txt` 记下偏僻词，通过音素模糊匹配，相似度大于阈值则强制替换。
-   **正则替换**：在 `hot-rule.txt` 用正则或简单等号规则，精准强制替换。
-   **LLM 角色**：预置了润色、小助理等角色，当识别结果的开头匹配任一角色名字时，将交由该角色处理。
-   **托盘菜单**：右键托盘图标即可添加热词、复制结果、清除LLM记忆。
-   **C/S 架构**：服务端与客户端分离，虽然 Win7 老电脑跑不了服务端模型，但最少能用客户端输入。
-   **日记归档**：按日期保存你的每一句语音及其识别结果。
-   **录音保存**：所有语音均保存为本地音频文件，隐私安全，永不丢失。

**CapsWriter-Offline** 的精髓在于：**完全离线**（不受网络限制）、**响应极快**、**高准确率** 且 **高度自定义**。我追求的是一种「如臂使指」的流畅感，让它成为一个专属的一体化输入利器。无需安装，一个U盘就能带走，随插随用，保密电脑也能用。

以下为支持的模型：

| 引擎名 | 准确性 | 速度 | 格式 | 显卡加速 |
|------|-------|------|------|---------|
| Paraformer | ★★★☆☆ | ★★★★★ | ONNX | ❌ |
| SenseVoice-Small | ★★★☆☆ | ★★★★★ | ONNX | ✅ |
| Fun-ASR-Nano | ★★★★☆ | ★★★★☆ | ONNX + GGUF | ✅ |
| Qwen3-ASR | ★★★★★ | ★★★☆☆ | ONNX + GGUF | ✅ |


性能参考（20s 音频转录延迟）：

| 模型 | CPU U9-285H | GPU RTX5050 |
|------|------------|------------|
| Paraformer | 0.6s | - |
| SenseVoice-Small | 0.6s | 0.15s |
| Fun-ASR-Nano | 2.0s | 0.5s |
| Qwen3-ASR-1.7B | 4.0s | 1.0s |

详细功能说明请参考 [`docs/`](docs/) 目录：
- [macOS 安装与运行](docs/macos-setup.md) — uv 环境、模型校验、应用构建和 TCC 权限
- [Agent 安装执行规约](docs/agent-macos-install.md) — 供本机 agent 执行安装、权限引导和真人验收
- [环境依赖安装说明](docs/环境依赖安装说明.md) — VC++ 运行库、FFmpeg 安装
- [热词功能如何使用](docs/热词功能如何使用.md) — 热词替换、规则替换、自定义短语
- [角色功能如何使用](docs/角色功能如何使用.md) — LLM 角色配置、输出模式、创建新角色
- [识别语言如何配置](docs/识别语言如何配置.md) — 各引擎语言支持范围与配置方法
- [文件转录功能如何使用](docs/文件转录功能如何使用.md) — 拖拽转字幕、时间戳对齐
- [显卡加速的若干问题](docs/显卡加速的若干问题.md) — DirectML、Vulkan 加速配置
- [模型下载的若干问题](docs/模型下载的若干问题.md) — 引擎选择、模型下载、目录结构
- [常见问题](docs/常见问题.md) — FAQ
- [更新日志](docs/CHANGELOG.md) 


## 💻 平台支持

Windows 10/11（64 位）仍是上游主要支持平台。

- **Linux**：暂无环境进行测试和打包，无法保证兼容性。
- **macOS**：本 fork 已在 Apple Silicon 上使用 USB 物理麦克风和真人说话验证
  Paraformer 服务端、右 Shift 全局热键、TextEdit/Chrome 上屏及剪贴板恢复。当前需要按
  [macOS 安装与运行](docs/macos-setup.md) 从源码构建，尚未使用 Developer ID 签名或公证发布。

[LazyTyper](https://lazytyper.com/) 和 [闪电说](https://shandianshuo.cn/) 也是很优秀的作品，都有离线引擎，都支持 Windows Linux 与 MacOS，并都有漂亮的图形化页面，推荐使用。

CapsWriter 的特别之处在于追求：

- 无感输入
- 完全离线，不受网络约束
- 低延迟，尽量做到硬件极限的最快速度
- 高度自定义的热词系统


## 🎬 快速开始

Apple Silicon macOS 请使用上面的 Release 源码安装入口，或按
[macOS 安装与运行](docs/macos-setup.md) 手动操作。以下步骤为 Windows 发行包流程。

1.  **准备环境**：确保安装了 [VC++ 运行库](https://learn.microsoft.com/zh-cn/cpp/windows/latest-supported-vc-redist)。若要使用文件转录功能，还需安装 [ffmpeg](https://ffmpeg.org/download.html) 并确保其在系统 PATH 中。
2.  **下载解压**：下载 [Latest Release](https://github.com/HaujetZhao/CapsWriter-Offline/releases/latest) 里的软件本体，再到 [Models Release](https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models) 下载模型压缩包，将模型解压，放入 `models` 文件夹中对应模型的文件夹里。
3.  **启动服务**：双击 `start_server.exe`，**它会自动最小化到托盘菜单**。
4.  **启动听写**：双击 `start_client.exe`，**它会自动最小化到托盘菜单**。
5.  **开始录音**：按住 `CapsLock键` 或 `鼠标侧键X2` 就可以说话了！


## ⚙️ 个性化配置

所有的设置都在根目录的 `config_server.py` 和 `config_client.py` 里，可直接编辑。
macOS 默认仅配置右 Shift（`shift_r`）作为录音键。源码或终端启动时可通过
`CAPSWRITER_HOTKEY=f10` 临时改为其他单键；当前不支持 `Option+Space` 这类组合键。


## 🛠️ 常见问题


**Q: 为什么按了没反应？**  
A: 请确认 `start_client.exe` 的黑窗口还在运行。若想在管理员权限运行的程序中输入，也需以管理员权限运行客户端。

**Q: 为什么识别结果没字？**  
A: 到 `年/月/assets` 文件夹中检查录音文件，看是不是没有录到音；听听录音效果，是不是麦克风太差，建议使用桌面 USB 麦克风；检查麦克风权限。

**Q: 想要隐藏黑窗口？**  
A: 点击托盘菜单即可隐藏黑窗口。

**Q: 如何开机启动？**  
A: `Win+R` 输入 `shell:startup` 打开启动文件夹，将服务端、客户端的快捷方式放进去即可。

更多问题请参阅 [docs/常见问题.md](docs/常见问题.md)。


## 🚀 上游作者的其他项目

| 项目名称 | 说明 | 体验地址 |
| :--- | :--- | :--- |
| [**IME_Indicator**](https://github.com/HaujetZhao/IME_Indicator) | Windows 输入法中英状态指示器 | [下载即用](https://github.com/HaujetZhao/IME_Indicator/releases/latest/download/IME-Indicator.exe) |
| [**Rust-Tray**](https://github.com/HaujetZhao/Rust-Tray) | 将控制台最小化到托盘图标的工具 | [下载即用](https://github.com/HaujetZhao/Rust-Tray/releases/latest/download/Tray.exe) |
| [**Gallery-Viewer**](https://github.com/HaujetZhao/Gallery-Viewer-HTML) | 网页端图库查看器，纯 HTML 实现 | [点击即用](https://haujetzhao.github.io/Gallery-Viewer-HTML/) |
| [**全景图片查看器**](https://github.com/HaujetZhao/Panorama-Viewer-HTML) | 单个网页实现全景照片、视频查看 | [点击即用](https://haujetzhao.github.io/Panorama-Viewer-HTML/) |
| [**图标生成器**](https://github.com/HaujetZhao/Font-Awesome-Icon-Generator-HTML) | 使用 Font-Awesome 生成网站 Icon | [点击即用](https://haujetzhao.github.io/Font-Awesome-Icon-Generator-HTML/) |
| [**五笔编码反查**](https://github.com/HaujetZhao/wubi86-revert-query) | 86 五笔编码在线反查 | [点击即用](https://haujetzhao.github.io/wubi86-revert-query/) |
| [**快捷键映射图**](https://github.com/HaujetZhao/ShortcutMapper_Chinese) | 可视化、交互式的快捷键映射图 (中文版) | [点击即用](https://haujetzhao.github.io/ShortcutMapper_Chinese/) |


## ❤️ 致谢

本项目基于以下优秀的开源项目：

-   [Sherpa-ONNX](https://github.com/k2-fsa/sherpa-onnx)
-   [FunASR](https://github.com/alibaba-damo-academy/FunASR)

上游作者感谢 Google Antigravity、Anthropic Claude、GLM、DeepSeek 对项目开发的帮助。

同时感谢支持上游项目并帮助其持续开发的捐助者。


如需支持原作者，可使用上游保留的赞助入口：


![sponsor](assets/sponsor.jpg)	

## 许可证

本仓库沿用上游的 [MIT License](LICENSE)。允许使用、复制、修改和再分发；分发时须保留
`LICENSE` 中的原版权声明与许可声明。
