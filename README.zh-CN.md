# c2c-img：让 Claude 像原生一样生图

[English](README.md) | **简体中文**

同时装了 Codex 和 Claude Code 的用户，可以用这个插件让 Claude 直接调用 Codex 内置的 `image_gen` 来生成或编辑图片。它走 ChatGPT 登录态，**不需要 `OPENAI_API_KEY`**。

对用户来说，体验是：说一句“画一张……”，片刻后图片直接出现在对话里；再说“亮一点”“换成竖版”，会在上一张图的基础上修改。整个过程不会看到 Codex、脚本或 prompt。

| 中文海报（文字零错误） | 改图：只把黎明换成极光夜 | 透明贴纸（`-t -s 512x512 -f webp`） |
|---|---|---|
| ![poster](docs/images/poster-zh.jpg) | ![before](docs/images/edit-before.jpg) → ![after](docs/images/edit-after.jpg) | ![sticker](docs/images/sticker-transparent.png) |

## 安装

前提：已安装 Codex（ChatGPT 桌面 App 自带，或 `npm i -g @openai/codex`），并用 ChatGPT 账号完成 `codex login`；Python 3.9 以上。

**方式一：作为插件安装（推荐）**。在 Claude Code 里执行：

```
/plugin marketplace add actionow-ai/c2c_img
/plugin install c2c-img@c2c-img
```

本地开发时也可以直接用路径：`/plugin marketplace add /path/to/c2c_img`。

**方式二：手动安装 skill**

```bash
ln -s "$PWD/skills/c2c-img" ~/.claude/skills/c2c-img   # 在仓库根目录执行
```

在 Windows 上，把 `skills\c2c-img` 目录复制到 `%USERPROFILE%\.claude\skills\` 下。

装好后可以先自检，这一步不会生图：

```bash
python3 skills/c2c-img/scripts/c2c_img.py --doctor
```

**可选**：`pip install pillow` 可以启用 webp 输出，在 Linux 和 Windows 上还能启用 `--size` 和 jpg。macOS 自带的 `sips` 已经能处理缩放、png 和 jpg。

## 能力

| 能力 | 用法 | 实测（macOS，真实 Codex） |
|---|---|---|
| 文生图 | `"prompt"` | 约 42–53s，受服务端波动影响；画质与在 Codex 里直接生图相同 |
| 改图或参考图 | `-i img.png` | 约 43–46s；只改指定内容，其余保持不变 |
| 透明背景 | `-t` | 输出真实 alpha；与 `-s`/`-f webp` 组合时 alpha 保留 |
| 精确尺寸 | `-s 1200x628` | 按最接近的比例生成，再缩放并居中裁剪 |
| 格式 | `-f jpg/webp`，或由 `-o` 的扩展名推断 | jpg 横幅 154KB；webp 贴纸 58KB |
| 多个变体 / 批量 | `-n 3` / `--batch -` | 并行生成 |
| 中文文字 | 在 prompt 里写 `Text (verbatim): "…"` | 多次测试都一字不差 |
| 自检 / 停止常驻进程 | `--doctor` / `--stop-daemon` | |

默认行为：
- 未指定 `-o` 时存到 `~/Pictures/c2c_img/<时间>-<slug>.png`。
- 从不覆盖已有文件，会自动加上 `-v2`、`-v3`。
- Codex 那边的原始文件会被移走，不会留下副本。

## 两套引擎

```
                         ┌── daemon（默认）─────────────────────────────────────────┐
Claude ─Bash─▶ c2c_img.py ─▶ 127.0.0.1 + token ─▶ 常驻进程 ─▶ codex app-server（常驻）
                         │     保持常驻，空闲 15 分钟后自动退出；codex 或脚本更新后自动替换
                         │
                         └── exec（回退）── codex exec --ephemeral（精简模式）每张图一个进程
```

| | daemon | exec |
|---|---|---|
| 调度开销（只回复 OK 的基准，中位数） | **6.6s** | 13.9s |
| 拿到图片路径的方式 | `item/completed` 事件里的 `savedPath` | 轮询 `generated_images/<thread>/` |
| 错误信息 | 结构化字段（`usageLimitExceeded` + `resetsAt`、`serverOverloaded`……） | 靠匹配错误文本 |
| 配置 | 用户配置，但关闭 17 个无关工具、MCP 服务器和 notify 钩子 | `--ignore-user-config` 加同样的 17 项禁用 |
| 出图后立即结束 | `turn/interrupt` | 结束进程组 |

**回退链**：
- 常驻进程启动失败或通信出错时，立即改用 exec。
- 常驻模式下模型或登录类错误，改用精简模式的 exec 重试。
- 精简模式的 exec 失败时，再用完整配置的 exec 重试一次。

额度耗尽、内容被拒、超时这三类不回退，直接报告。

**模型容量已满**时，会用 `model/list` 里的另一个模型重试。这里的模型只负责调用一次生图工具，图片由另一个模型生成，所以换模型不影响画质。

## 平台

| 平台 | 状态 |
|---|---|
| macOS | 用真实 Codex 实测，共约 25 张图 |
| Linux / Windows | 代码已适配：进程组、`taskkill`、跨平台文件锁、`%LOCALAPPDATA%`/`XDG_CACHE_HOME`、stdin/stdout 统一用 UTF-8、`.cmd` 形式的 CLI。已配好 CI 矩阵，**推到 GitHub 后才会实际运行** |

## 测试

```bash
python3 tests/test_offline.py
```

```bash
python3 tests/test_engines.py
```

两组测试都不需要 Codex 登录，不消耗额度：
- `test_offline.py`：12 项，覆盖工具函数、错误分类、后处理和参数校验。
- `test_engines.py`：10 项，用 `tests/fake_codex.py` 模拟 Codex 的 `exec` 和 `app-server` 协议，端到端测试两套引擎：出图后提前结束、原图被移走、常驻进程复用和升级替换、并发批量、额度耗尽（附重置时间）、容量已满时换模型、内容被拒、常驻进程起不来时快速回退、中文编码、防覆盖命名。

`.github/workflows/test.yml` 在 macOS、Ubuntu 和 Windows 上，分别用 Python 3.9 和 3.12，把上面两组测试各跑一遍，并覆盖有无 Pillow 两种环境。

插件结构已通过 `claude plugin validate`，只剩一条提示缺少 author 的警告。在隔离的配置目录里走完了“添加 marketplace → 安装 → 加载”的流程。常驻上下文成本约 **208 token**，触发时加载约 1.4k。

## 性能演进（单张新图，真实 Codex）

| 版本 | 单张 | 每张的 Codex token |
|---|---|---|
| 初版 | 72–80s | ~32k |
| 出图即停 + 精简 exec | 37–42s | ~15k |
| 再关闭 17 个工具 | ~42s | ~12.5k |
| 常驻 app-server | 调度开销再省约 7s（实际总耗时受服务端波动影响，42–53s） | ~13k（会话之间命中缓存） |

理论下限约 26s（纯生图时间）。

## 局限

- 依赖 Codex 的实验性接口：app-server 协议标注为 experimental，`exec --json` 事件和 `generated_images/` 目录结构也没有公开承诺。两套引擎互为兜底；Codex 升级后先跑 `--doctor` 和一张冒烟测试图。
- 常驻模式无法完全忽略用户配置，这是 app-server 的限制。已经关掉 MCP、notify 和 17 个工具，但模型设置仍沿用用户配置。
- Linux 和 Windows 只经过代码适配和假 Codex 测试，还没有在真实 Codex 上验证。
- 粘贴进对话、没有文件路径的图片无法作为 `-i` 输入。
- **还没做“能否正确触发”的评测**：需要登录态的 `claude` CLI，可以用 `claude plugin eval` 来跑。

## 目录

```
.claude-plugin/
  plugin.json                插件清单（c2c-img 0.4.0，作者 actionow，MIT）
  marketplace.json           本仓库同时是一个只含一个插件的 marketplace
skills/c2c-img/
  SKILL.md                   触发描述 + 原生感工作流
  references/prompting.md    prompt 方法论（按需加载）
  scripts/c2c_img.py         执行器（只用标准库，Python 3.9 以上，macOS/Linux/Windows）
tests/
  test_offline.py            离线单测
  test_engines.py            引擎集成测试
  fake_codex.py              模拟的 Codex CLI（exec + app-server）
  outputs/                   真实测试产物（已加入 gitignore）
.github/workflows/test.yml   三平台 CI
```

## 环境变量

| 变量 | 作用 |
|---|---|
| `C2C_CODEX_BIN` | 指定 codex 可执行文件 |
| `C2C_OUT_DIR` | 默认输出目录（默认 `~/Pictures/c2c_img`） |
| `C2C_DAEMON=0` | 禁用常驻模式，始终使用 exec |
| `C2C_DAEMON_IDLE` | 常驻进程的空闲退出时间，单位秒（默认 900） |
| `C2C_TIMEOUT` | 单张超时，单位秒（默认 300） |
| `C2C_MODEL` | 指定调度模型 |
| `C2C_USE_USER_CONFIG=1` | exec 引擎加载完整用户配置 |

## 许可证

[MIT](LICENSE) © 2026 actionow
