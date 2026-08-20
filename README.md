# managing-zotero

面向 Codex 的安全 Zotero 文献管理 Skill。它通过 Zotero 10 本地 API 完成小范围、可审查的文献检索、去重、Collection 整理、元数据补充、子笔记创建和已批准 PDF 的链接，同时坚持“先预览、后批准、再写入”。

> This repository contains a safety-first Codex Skill for managing Zotero 10 through its local API.

## 主要功能

- 按 Collection、DOI、条目键或关键词进行窄范围只读查询。
- 根据 DOI 和保守的书目信息规则识别新增、复用与冲突条目。
- 为 Collection 和条目写入生成可核对的预览与摘要值。
- 仅在用户明确批准对应预览后申请一次性本地授权并执行写入。
- 写入后重新读取 Zotero，报告实际创建、复用、未变更和失败结果。
- 保留已有个人笔记与可信元数据；Codex 只创建带固定标记的独立子笔记。
- 仅链接已经位于最终、已批准 D 盘目录中的 PDF。
- 提供可选的微波转动光谱文献字段与标签配置。

## 安全边界

本 Skill 的默认行为是只读。它不会：

- 在没有精确预览和明确批准的情况下写入 Zotero；
- 发送 `DELETE` 请求、直接修改 `zotero.sqlite`，或退回 Zotero Web API 写入；
- 自动合并记录、清空标签或覆盖已有个人笔记；
- 打印、保存或重复使用一次性 API 密钥；
- 跟随 HTTP 重定向转发授权信息；
- 上传二进制 PDF，或链接缓存、临时下载及未经批准目录中的文件。

每个写入计划还会绑定 Zotero 实例指纹、对象版本和计划摘要。实例、版本或计划发生变化时，Skill 会停止并要求重新生成预览。

## 环境要求

- Codex
- Zotero 10
- Zotero 正在运行且本地 API 可用
- Python 3.10 或更高版本

运行时代码仅使用 Python 标准库。仓库中的开发校验工具可能使用额外依赖，但不会随 Skill 安装。

## 安装

### 方法一：使用 Codex 安装

将仓库地址交给 Codex，并要求安装其中的 `skill/managing-zotero`：

```text
请从这个仓库安装 managing-zotero Skill：
https://github.com/qiransy/managing-zotero-skill
Skill 路径是 skill/managing-zotero
```

如果仓库是私有的，需要先确保当前 GitHub 连接或 Git 凭据可以访问该仓库。

### 方法二：手动安装

克隆仓库后，将整个 `skill/managing-zotero` 文件夹复制到 Codex Skills 目录：

```text
Windows: %USERPROFILE%\.codex\skills\managing-zotero
macOS/Linux: ~/.codex/skills/managing-zotero
```

安装目录中应直接包含 `SKILL.md`、`agents/`、`scripts/`、`references/` 和 `assets/`。安装完成后新建一个 Codex 对话，使 Skill 被重新发现。

## 快速开始

显式调用：

```text
使用 $managing-zotero 检查本机 Zotero 连接，只读运行，不执行任何写入。
```

检查指定 DOI 是否已经存在：

```text
使用 $managing-zotero 在我的 Zotero 中检查 DOI 10.xxxx/xxxxx 是否重复，只读运行。
```

整理一个新分子体系的文献：

```text
使用 $managing-zotero 为 ethanolamine-water 文献准备一个新的 Collection。
先给我展示 Collection 和条目写入预览，每一步都等我明确批准。
```

当任务明显属于 Zotero 管理时，Codex 也可以自动调用该 Skill；使用 `$managing-zotero` 可以强制指定。

## 写入流程

```text
只读检查
  → 窄范围查重
  → 生成 Collection 预览
  → 用户批准 Collection 摘要
  → 写入并回读确认
  → 生成条目预览
  → 用户批准条目摘要
  → 写入并回读确认
  → 输出脱敏审计记录
```

Collection 和条目是两次独立批准。对一次预览的批准不会自动授权下一次写入；过期摘要、版本变化、Zotero 实例变化或部分失败都不会自动重试。

## 文献研究协作

`managing-zotero` 负责 Zotero 侧的安全整理，不替代文献检索和学术核查。推荐先使用相应研究 Skill：

- `paper-lookup`：查找并核验 DOI、题录与开放全文；
- `literature-review`：系统检索与研究综述；
- `nature-paper-card`：单篇论文深度阅读；
- `citation-verification`：核查引用和标识符；
- `scientific-writing`：生成证据边界明确的学术笔记。

只有经过核验的候选文献才应进入 Zotero 写入预览。

## 微波转动光谱配置

默认配置是通用文献管理。研究任务明确涉及微波转动光谱时，可启用 `microwave-spectroscopy` 配置，用于组织以下信息：

- 分子体系、单体/复合物和构象；
- 实验频率范围、喷嘴和载气等实验条件；
- 转动常数、离心畸变、偶极矩与超精细结构；
- CREST、Gaussian、ORCA、PGOPHER/SPFIT 等计算和指认链条；
- 证据层级、全文核查状态以及理论—实验对应关系。

详细字段见 [`microwave-spectroscopy-schema.md`](skill/managing-zotero/references/profiles/microwave-spectroscopy-schema.md)。

## 开发与验证

运行完整测试：

```powershell
python -m unittest discover -s skill\managing-zotero\scripts\tests -p "test_*.py" -q
```

当前实现包含 88 项自动化测试，覆盖只读探测、授权、预览摘要、版本冲突、实例变化、去重、个人笔记保护、PDF 路径限制、部分失败、脱敏审计和模拟 Zotero 端到端流程。

核心 Skill 指令见 [`SKILL.md`](skill/managing-zotero/SKILL.md)，安全审批协议见 [`safety-and-approval.md`](skill/managing-zotero/references/safety-and-approval.md)。

## 项目结构

```text
skill/managing-zotero/
├── SKILL.md
├── agents/openai.yaml
├── assets/
├── references/
│   └── profiles/
└── scripts/
    └── tests/
```

## 当前状态

该项目处于测试阶段。建议先执行只读操作；若要验证真实写入，请使用专门的测试 Collection、提前备份 Zotero，并逐项核对每次写入预览。
