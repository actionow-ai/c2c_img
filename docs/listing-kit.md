# c2c-img 上架材料包 / Listing kit

复制粘贴用。所有平台尽量使用同一套名称和描述，便于搜索、形成品牌识别。

## 基本信息 / Basics

| 字段 | 值 |
|---|---|
| Name / slug | `c2c-img` |
| Display name | c2c-img: Codex image generation for Claude Code |
| Repository | https://github.com/actionow-ai/c2c_img |
| Author | actionow |
| License | MIT |
| Version | 0.4.0 |
| Category | Creative & Media / Image Generation（创意与媒体 / 图像生成） |
| Tags | claude-code, claude-skills, agent-skills, image-generation, codex, gpt-image, chatgpt, text-to-image, image-editing, 生图, AI绘图 |
| Works with | Claude Code (plugin or skill). Any Agent Skills–compatible agent that can run shell commands |
| Requirements | Codex CLI signed in with a ChatGPT account (`codex login`), Python 3.9+ |

## 一句话介绍 / Tagline

- **EN (≤80 chars):** Native-feeling image generation for Claude Code, powered by your Codex (ChatGPT) login.
- **中文：** 让 Claude Code 像原生一样生图：调用本地 Codex 的 image_gen，无需 API Key。

## 简短描述 / Short description

**EN (~200 chars):** Lets Claude Code generate and edit images through the local Codex CLI's built-in image_gen, using your ChatGPT login instead of an API key. It supports text-to-image, edits, transparent backgrounds, exact sizes and batches.

**中文：** 让 Claude Code 通过本地 Codex CLI 内置的 image_gen 生成和编辑图片，使用 ChatGPT 登录态，无需 API Key。支持文生图、改图、透明背景、精确尺寸和批量生成。

## 详细描述 / Long description

**EN**

c2c-img gives Claude Code built-in image generation by borrowing the `image_gen` tool from your local Codex CLI. It runs on your existing ChatGPT login, so there's no API key or extra bill.

- **Feels native:** ask "draw a…" and the image appears in the chat. Claude writes the prompt, checks the result, and fixes obvious defects before showing it. Say "make it brighter" to edit the last image.
- **Full toolkit:** text-to-image, reference-based edits, real transparent backgrounds, exact pixel sizes (`-s 1200x630`), png, jpg and webp output, variants, and parallel batches.
- **Accurate text:** renders Chinese and English text in posters and signs verbatim.
- **Fast and robust:** a warm Codex app-server daemon saves about 7 s per call. A lean `codex exec` fallback takes over when needed. Errors are structured: quota with reset time, capacity with a retry on another model, and refusals.
- **Safe by default:** Codex runs read-only in an empty temp directory with shell and browser tools disabled. Your files are never overwritten.
- **Cross-platform:** macOS, Linux and Windows, stdlib-only Python. CI covers 3 operating systems × 2 Python versions.

**中文**

c2c-img 借用本地 Codex CLI 内置的 `image_gen`，给 Claude Code 装上“原生”生图能力。它使用你现有的 ChatGPT 登录，不需要 API Key，也不会额外计费。

- **原生体验：** 说一句“画一张……”，图片就会出现在对话里。Claude 自己写 prompt、检查结果，明显有缺陷时先修好再给你看。说“再亮一点”，会在上一张图上修改。
- **功能齐全：** 文生图、参考图编辑、真实透明背景、精确尺寸（`-s 1200x630`）、png/jpg/webp 输出、多变体、并行批量。
- **文字准确：** 海报、招牌里的中英文文字一字不差。
- **快速稳健：** 常驻的 Codex app-server 每次调用约省 7 秒；必要时自动回退到精简模式的 `codex exec`。错误信息结构化：额度耗尽附重置时间，容量已满时换模型重试，内容被拒也会明确说明。
- **默认安全：** Codex 在只读沙箱和临时空目录中运行，禁用 shell 和浏览器工具；你的文件永远不会被覆盖。
- **跨平台：** 支持 macOS、Linux、Windows，只用 Python 标准库；CI 覆盖 3 个系统 × 2 个 Python 版本。

## 安装命令 / Install

```
/plugin marketplace add actionow-ai/c2c_img
/plugin install c2c-img@c2c-img
```

```bash
npx skills add actionow-ai/c2c_img -g -a claude-code
```

## 截图 / Screenshots

- https://raw.githubusercontent.com/actionow-ai/c2c_img/main/docs/images/poster-zh.jpg
- https://raw.githubusercontent.com/actionow-ai/c2c_img/main/docs/images/edit-before.jpg
- https://raw.githubusercontent.com/actionow-ai/c2c_img/main/docs/images/edit-after.jpg
- https://raw.githubusercontent.com/actionow-ai/c2c_img/main/docs/images/reading-nook.jpg
- https://raw.githubusercontent.com/actionow-ai/c2c_img/main/docs/images/sticker-transparent.png
- https://raw.githubusercontent.com/actionow-ai/c2c_img/main/docs/images/banner-1200x628.jpg

## 平台清单 / Where to list

| 平台 | 地址 | 方式 | 状态 / 备注 |
|---|---|---|---|
| ClawHub（OpenClaw） | https://clawhub.ai | CLI：`clawhub skill publish` | 由 Claude 代为发布 |
| skills.sh（Vercel） | https://skills.sh | 有人执行 `npx skills add` 后按安装量自动上榜 | 无需提交；仓库结构已验证兼容 |
| SkillsMP | https://skillsmp.com | 自动爬取 GitHub 上的 SKILL.md | 无需提交；GitHub topics 已加 |
| skilldb 等聚合站 | https://github.com/AmazingAng/skilldb | 聚合 skills.sh、SkillsMP、ClawHub | 自动 |
| **Anthropic 目录**（claude.ai） | https://claude.ai/directory/manage | 开发者门户提交，**需要付费的 claude.ai 账号** | 你来提交；先读 [提交前检查清单](https://claude.com/docs/plugins/pre-submission-checklist) |
| SkillsClaude | https://skillsclaude.org | Submit：从 GitHub 读取并打信任分 | 你来提交 |
| Claude Skills Info | https://claudeskills.info | Submit 页：名称、描述、仓库 URL | 你来提交 |
| MCP Market（Skills） | https://mcpmarket.com/tools/skills | 网页提交（入口未核实） | 你来提交 |
| Developers Digest Skills Marketplace | https://www.developersdigest.tech | 表单，会对 SKILL.md 做 lint（已通过 skills-ref 校验） | 你来提交 |
| Agent Skill Club | https://www.agentskill.club | 网页提交（未核实） | 你来提交 |
| skillhub.club | https://www.skillhub.club | 网页提交（未核实） | 你来提交 |
| 腾讯 SkillHub | https://skillhub.cn | 基于 OpenClaw 生态，可能同步 ClawHub（未核实） | ClawHub 发布后去搜一下 |
| Skill Hub 中国 | https://skill-cn.com | 网页提交（未核实） | 你来提交 |
| 虾技市场 | https://skillcast.cn | 用户提交，精选审核 | 你来提交 |
| AI产品库 SkillHub | https://skills.aiproducthub.cn | 网页提交（未核实） | 你来提交 |

## 以后再提交 / Later（有门槛，且必须由真人提交）

| 清单 | 门槛 | 最早可提交时间 |
|---|---|---|
| [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code) | 只能由真人通过网页 issue 表单提交，不接受 PR 或 gh CLI；仓库满 14 天且仍在活跃开发，或 ≥100 star；一次只推荐一个资源 | **2026-10-14** 起 |
| [travisvn/awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills) | ≥10 star；**禁止 AI 辅助的 PR** | 达到 10 star 后，由你手写 PR |
| [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) | 需要“真实的社区使用量”，不收新 skill | 有一定安装量以后 |
| [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills) | PR；要求基于真实使用、有文档和例子；归入 Creative & Media | 随时，建议积累一些用户反馈后再提 |
