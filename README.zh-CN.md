# phonectl-mcp

[English](https://github.com/jiefeng-ai/phonectl-mcp/blob/main/README.md)

一个 MCP 服务：让 AI agent 通过 USB 线看见并操作一台真实的 iPhone，能截图、读屏幕文字、点按、滑动、打字、按硬件按钮、启动 App、读写剪贴板。**仅支持 macOS。**

<p align="center"><img src="https://raw.githubusercontent.com/jiefeng-ai/phonectl-mcp/main/docs/demo.gif" width="358" alt="脚本通过 phonectl-mcp 在 BOSS 直聘里投递一个岗位：用 read_text 读屏、用 tap 点按"></p>
<p align="center"><sub>脚本通过 phonectl-mcp 在招聘 App 里投递一个岗位：<code>read_text</code> 找按钮，<code>tap</code> 点下去。个人信息已打码，2 倍速。</sub></p>

手机上不用装任何东西，也不用越狱。它用的是 iOS 对已信任 Mac 开放的开发者服务（Xcode 用的就是这套），点按和打字走 iOS 27 开放的 USB 输入通道。

## 需要什么

- **一台 Mac**：USB 隧道和读屏文字识别（Apple Vision）都只有 macOS 上有。
- **iOS 27 或以上的 iPhone**：点按、滑动、打字需要 iOS 27。iOS 17–26 上截图和启动 App 可能能用，但没有测试过。
- **一根能传数据的 USB 线**：只能充电的线不行。
- **手机打开「开发者模式」，并信任这台 Mac**：这两步 `phonectl doctor` 会一步步告诉你怎么做。
- **[uv](https://docs.astral.sh/uv/)**。

已在 iPhone 17 Pro（iOS 27.2）+ macOS 27 上测试。

## 安装

```sh
uv tool install phonectl-mcp
phonectl install-service
phonectl doctor
```

- `install-service`：把服务注册为开机自启（launchd），监听 `http://127.0.0.1:47321/mcp`；在 `~/.config/phonectl/token` 生成访问令牌，并打印下面的客户端配置。
- `doctor`：从数据线到服务，逐段检查连接，哪一步不通就告诉你怎么修。缺开发者磁盘镜像时，加 `--fix` 会自动装上。

### 接入客户端

Claude Code：

```sh
claude mcp add --scope user --transport http phone http://127.0.0.1:47321/mcp \
  --header "Authorization: Bearer $(cat ~/.config/phonectl/token)"
```

其他支持 streamable HTTP 的客户端，填上面的地址，再加请求头 `Authorization: Bearer <令牌>` 即可。

### 为什么全机只跑一份服务

手机和 Mac 之间同一时间只保留一条控制连接，两个程序各连各的会互相踢掉对方。所以由一份常驻服务独占手机，所有 agent 和脚本都连这份服务。你自己写的脚本，也可以用任意 MCP 客户端库调用同样的工具。

注意，这只解决连接共用的问题，服务管不了两个 agent 同时点屏幕。同一时间请只让一方操作。

## 工具

坐标单位是 iOS 的 point，和服务返回的截图像素一一对应（iPhone 17 Pro 是 402×874）。

| 工具 | 作用 |
| --- | --- |
| `screenshot(native=False)` | 截图。`native=True` 返回原分辨率 PNG，给脚本用 |
| `read_text()` | 以 JSON 返回屏幕上的文字和位置（单位 point）。只需要找按钮时，比看图省得多 |
| `tap(x, y)`、`long_press(x, y, duration_ms)` | 点按、长按 |
| `swipe(x1, y1, x2, y2, duration_ms)` | 滑动。要往下翻，就向上滑 |
| `type_text(text)` | 往当前输入框打字。ASCII 字符逐键输入；中文和 emoji 经剪贴板粘贴，会覆盖剪贴板原有内容 |
| `press_key(key)` | 按键或组合键，如 `enter`、`backspace`、`cmd+a` |
| `press_button(name)` | 按硬件按钮：`home`、`lock`、`volume-up`、`volume-down`、`mute`、`siri` |
| `launch_app(bundle_id, restart=False)`、`list_apps(query)` | 启动 App，查 App 的 bundle id |
| `get_clipboard()`、`set_clipboard(text)` | 读写手机剪贴板 |
| `status()` | 报告服务状态和手机连接 |

操作手机的工具（`tap`、`long_press`、`swipe`、`type_text`、`press_key`、`press_button`、`launch_app`）操作完会返回一张截图，传 `screenshot=False` 可以不要。手机不会确认点按是否生效，请看这张截图判断点没点中。

## 配置

在运行 `phonectl install-service` 之前设好下面的环境变量，安装时会写进服务配置。

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `PHONECTL_UDID` | 第一台 USB 连接的 iPhone | 连了多台手机时，指定用哪一台 |
| `PHONECTL_PORT` | `47321` | HTTP 端口，只监听 127.0.0.1 |
| `PHONECTL_TOKEN_FILE` | `~/.config/phonectl/token` | 令牌文件的位置 |
| `PHONECTL_SCALE` | `3` | 每个 point 对应几个像素。iPhone SE、XR、11 填 2。它只影响截图大小，设错了也不会点错位置 |
| `PHONECTL_OCR_LANGS` | `zh-Hans,en-US` | `read_text` 的识别语言，按优先级排列 |
| `PHONECTL_SETTLE` | `0.6` | 操作后等多少秒再截图 |
| `PHONECTL_TOUCH_IDLE` | `30` | 多少秒没有输入后关闭到手机的输入通道。下次输入会重新打开，约 0.6 秒 |
| `PHONECTL_CALL_TIMEOUT` | `15` | 等手机响应多少秒算失败（不含手势本身的耗时） |
| `PHONECTL_LOG` | `WARNING` | 日志级别（安装成服务时是 `INFO`）。日志在 `~/Library/Logs/phonectl*.log` |

其他命令：
- `phonectl restart`：重启服务，比如用 `uv tool upgrade phonectl-mcp` 升级之后；
- `phonectl uninstall-service`：卸载服务；
- 直接运行 `phonectl-mcp`（不带参数）：以 stdio 方式服务单个客户端，用于调试。

## 排查问题

先跑 `phonectl doctor`，它会告诉你断在哪一段。

| 现象 | 处理 |
| --- | --- |
| 找不到 iPhone | 换一根能传数据的线，解锁手机后重插 |
| 提示 Mac 未被信任 | 解锁手机，重插线，在手机上点「信任」 |
| 设置里找不到「开发者模式」 | 手机连着 Mac 时打开一次 Xcode，之后「设置 › 隐私与安全性」里会出现这个开关 |
| 截图失败 | 运行 `phonectl doctor --fix`，装上开发者磁盘镜像 |
| 点了没反应 | 点按需要 iOS 27 及以上；另外确认屏幕亮着、没锁屏 |
| 客户端连不上 | 运行 `phonectl restart`，再看 `~/Library/Logs/phonectl.error.log` |

## 安全

- 服务只监听 `127.0.0.1`，不带令牌的请求一律拒绝。
- 接入的 agent 能做你用手指能做的任何事：发消息、付款、删数据。请给它尽量明确、范围小的指令，并在它操作时看着屏幕。
- 使用风险自负。用它自动操作第三方 App，可能违反这些 App 的用户协议。

## 开发

```sh
git clone https://github.com/jiefeng-ai/phonectl-mcp
cd phonectl-mcp
uv sync
uv run pytest
```

想让服务直接运行你本地的代码，在仓库目录里执行 `uv run phonectl install-service`。

## 协议

MIT
