# DeepSeek-TUI：把 DeepSeek 变成终端里的编程Agent

- 链接: https://zhuanlan.zhihu.com/p/2037323356601594908
- 发布: 2026-05-12 00:09:14
- 赞同: 0 | 评论: 0

---

最近 DeepSeek-TUI 这个项目挺火，它有点像是专门适配DeepSeek的Agent。让 DeepSeek 直接进入你的工作目录，读代码、改文件、执行命令、看 Git 状态、调用工具，甚至调度子 Agent 完成更复杂的开发任务。

![](https://pic2.zhimg.com/v2-0a73c024aff99b29e07166ce4dc93f9d_1440w.jpg)

它的官方地址在这里：

- GitHub 链接： [https://github.com/Hmbown/DeepSeek-TUI/tree/main](https://github.com/Hmbown/DeepSeek-TUI/tree/main)

简单说，其实就是让DeepSeek辅助开发者进行项目开发。在项目 README 里对它的定义也很直接：它是一个运行在终端中的 coding agent，支持读取和编辑文件、运行 shell 命令、搜索网页、管理 Git，并通过键盘驱动的 TUI 界面协调子 Agent。

![](https://picx.zhimg.com/v2-babd3a9a8f8b76a2993e7aa56e5640d3_1440w.jpg)

其实我们很容易就想到，这个DeepSeek-TUI 有点像现在的Claude Code、OpenCode。它们代表的就是“终端编程 Agent”这条路线。

当然，这三个产品定位有点不一样

**Claude Code = 最完整的商业级工程 Agent。**

**OpenCode = 开源、多模型、可自托管的 Agent 容器。**

**DeepSeek-TUI = 围绕 DeepSeek 模型做的终端编程客户端。**

![](https://pica.zhimg.com/v2-f211ee5f5d49ba94ccc8f0cf5b40155e_1440w.jpg)

DeepSeek-TUI 的特点是“更像一个 DeepSeek 版 Claude Code”。它支持读写本地文件、跑 shell、管理 git、web 搜索、apply patch、子 Agent、MCP，并且有 Plan、Agent、YOLO 三种模式；它还强调 DeepSeek V4、长上下文和 prefix-cache 成本统计。也就是说，它不是单纯聊天 CLI，而是在补齐“本地工程执行器”这一层。

Claude Code 最大优势是 **模型和产品一体化** 。它不是“随便接一个模型再包一层 shell”，而是 Anthropic 官方围绕 Claude 做的 agentic coding system：能读代码库、改文件、运行命令、跑测试、接 MCP、用 hooks / skills / subagents，并且现在已经覆盖终端、IDE、桌面、Web 等入口。

OpenCode 的核心优势是 **开放和多模型** 。它不是押注某一个模型，而是提供一个开源 Agent 外壳，可以接 Claude、GPT、Gemini、本地模型以及大量 provider；它还支持 LSP、多会话、分享 session、GitHub Copilot / ChatGPT 账号接入等能力。

## 安装教程

安装方式也比较面向开发者。它提供 npm、Cargo、Homebrew 和直接下载二进制等方式。安装只需要根据你自己的机器环境选择安装即可

**DeepSeek-TUI 提供了 5 种安装方式** ，你可以根据机器环境选择一种。

**npm 安装：适合已经有 Node.js 的机器**

```text
npm install -g deepseek-tui
```

**Cargo 安装：适合 Rust 环境**

```text
cargo install deepseek-tui-cli--locked
cargo install deepseek-tui--locked
```

**Homebrew 安装：适合 macOS**

```text
brew tap Hmbown/deepseek-tui
brew install deepseek-tui
```

**直接下载：适合无 npm / cargo / brew 的机器**

```text
https://github.com/Hmbown/DeepSeek-TUI/releases
```

去 GitHub Releases 页面直接下载对应系统的二进制文件。

**Docker 运行：适合不想污染本机环境**

```text
docker run --rm -it \
  -e DEEPSEEK_API_KEY \
  -v "$PWD:/workspace" \
  ghcr.io/hmbown/deepseek-tui:latest
```

安装完成后，首次启动会要求填 API Key

第一次运行：

```text
deepseek
```

它会提示你输入 DeepSeek API Key。输入后会保存到：

```text
~/.deepseek/config.toml
```

以后你在任何目录运行 `deepseek` ，都不需要重复输入 Key。

最后就可以看到具体的启动界面了~

![](https://picx.zhimg.com/v2-20df631b9e6de2eb588bd99dc54849ad_1440w.jpg)

我觉得 DeepSeek-TUI 的出现，代表一个很明确的趋势：AI 编程工具现在真的慢慢的变成一个落地的具体场景。模型本身很重要，但更重要的是它能不能进入开发者每天使用的终端、仓库、脚本、日志、测试和部署流程里。谁能把这些环节打通，谁就更接近真正可用的 AI 工程助手。

当然，DeepSeek-TUI 目前更适合喜欢终端工作流、愿意让 AI 深度参与代码修改的开发者。它不是官方 DeepSeek 产品，项目 README 也明确说明与 DeepSeek Inc. 没有关联，并采用 MIT License。

但从产品形态上看，它很值得关注。对于习惯 Claude Code、OpenCode、OpenClaw 这类工具的人来说，DeepSeek-TUI 可能会成为 DeepSeek 生态里最值得尝试的终端入口之一。