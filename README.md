# Antigravity CLI 极简状态栏 HUD (`agy-statusline`)

专为 Google Antigravity CLI (`agy`) 打造的轻量 2 行终端底部 HUD，纯 Python 3 标准库零依赖（单次执行 ~33ms），默认叠加在官方状态栏正下方，不丢失官方快捷键和状态提示。

## 效果预览

```text
上下文 ██░░░░░░░░ 18.8% (197k/1M) │ 缓存 96% (181k) 约23:41过期
限额(Gemini) 5h剩87% (1时44分后重置) │ 周剩85% (5天16时后重置)
```

切到 Claude / GPT 等第三方模型时，自动切换对应缓存窗口与 3P 限额池：

```text
上下文 ████████░░ 75.0% (150k/200k) │ 缓存 93% (140k) 22:44过期
限额(3P) 5h剩40% (3时29分后重置) │ 周剩12% (6天23时后重置)
```

## 核心特性

- **实时上下文进度条**：10 格进度条 + 百分比 + 精确 Token 用量（`<70%` 绿、`70%~84%` 黄、`≥85%` 红），数值与 `/context` 面板对齐。
- **Prompt 缓存命中率 + 过期时间点**：
  - 实时显示上轮缓存命中率与缓存读取 Token（`≥70%` 绿、`30%~69%` 黄、冷启动/未命中醒目提示）。
  - 显示**绝对过期时刻**而非会冻僵的倒计时（`agy` 状态栏为事件驱动，空闲期间不会每秒重绘；对照系统时钟即可零心算判断缓存是否过期）：
    - **Claude**：显式 5 分钟 TTL → `HH:MM过期` / `已过期`
    - **Gemini 4 Neon**：隐式前缀缓存（实测参考热窗约 60 分钟）→ `约HH:MM过期` / `约已过期`
    - **Gemini 4 Hydrogen**：隐式前缀缓存（实测约 5 分钟后易冷）→ `约HH:MM易冷` / `约已过期`
  - 自动兼容修复 `agy` v1.5.15 传入 `transcript_path` 遗漏 `-cli` 导致时间戳缺失的问题。
  - 支持 `/compact` 压缩后的 `CHECKPOINT` 事件识别：压缩完成后立即更新估算上下文占用并显示 `缓存 0% (已重置)`，避免继续残留压缩前的旧百分比与旧缓存数据。
- **独立 5h + 周剩余限额行**：
  - 零额外网络请求，直接读取 `agy` 原生 `quota` 字段，按当前模型族系严格隔离匹配 `Gemini` 或 `3P` 池；对应模型池缺失、模型未识别或 API Key 模式无订阅额度时自动隐藏第二行，绝不跨池回退误导。
  - 同时展示 5h 与周刷新倒计时（如 `1时44分后重置`、`5天16时后重置`，窄屏自动精简为 `1时44分`、`5天16时`；`>40%` 绿、`16%~40%` 黄、`≤15%` 红）。
- **CJK 精准列宽自适应**：按终端真实中文列宽自动分级精简，即使缩到 48 列极窄分屏也绝不折行撑乱输入框。

## 一键安装 / 升级

### 方式一：一行命令安装（推荐，无需登录 GitHub）

```bash
curl -fsSL https://raw.githubusercontent.com/lyezhucc/agy-statusline/main/install.sh | bash
```

- 自动下载脚本到 `~/.gemini/antigravity-cli/statusline.py`
- 自动备份并安全合并 `statusLine` 到 `~/.gemini/antigravity-cli/settings.json`（保留 `0600` 权限与原有全部配置，默认 `stack_with_default: true`）
- 后续发布新版时，再次运行同一行命令即可原地升级

如果你不想保留官方默认底栏、希望只显示本 HUD，可传 `--replace`：

```bash
curl -fsSL https://raw.githubusercontent.com/lyezhucc/agy-statusline/main/install.sh | bash -s -- --replace
```

### 方式二：本地克隆安装

```bash
git clone https://github.com/lyezhucc/agy-statusline.git
cd agy-statusline
./install.sh
```

## 生效与常用命令

- **新开的 `agy` 终端**：自动生效。
- **已经开着的 `agy` 终端**：在输入框中运行一次即可立即刷出：
  ```text
  /statusline ~/.gemini/antigravity-cli/statusline.py
  ```
- **临时关闭 / 重新开启**：在 `agy` 中运行 `/statusline off` 或 `/statusline on`。

## 卸载

```bash
curl -fsSL https://raw.githubusercontent.com/lyezhucc/agy-statusline/main/install.sh | bash -s -- --uninstall
```

或直接在 `agy` 中输入 `/statusline reset`。

## 运行要求与本地测试

- macOS / Linux + Python 3.10+ + Antigravity CLI (`agy`) 1.1+（已在 `1.5.15` 实机验收）
- 运行单元测试：

```bash
python3 -m unittest discover -v
```

## 配套工具

如果你在 **Orca** 中同时使用多个 Antigravity 账号，推荐搭配使用：

- **[orca-agy-account-switcher](https://github.com/lyezhucc/orca-agy-account-switcher)** — 反重力多账号额度总览、防后台回跳与跨工作区终端无损原地一键换号。

## License

MIT
