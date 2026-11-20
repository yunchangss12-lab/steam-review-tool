# AGENTS.md — 项目协作与版本管理约定（通用模板）

> 本文件约束所有参与本仓库的人与 AI 助手。动手前先读一遍，尤其「分支约束」与「发布与 tag」。
> 完全不懂 git 的话，把下面「给 AI 助手的额外须知」里的内容念给 AI 听，让 AI 帮你操作即可。

## 分支约束

- 使用 git 进行项目分支管理
- `main` 分支为主要分支，只存放**已发布上线**的代码；一切大版本开发都要以 main 分支为基础，迁移出新分支
- 长期集成分支为 `develop`：日常开发先合到这里，攒够一批（一个版本）再合进 `main` 上线
- 开发分支命名格式为 `feature/branchName`，从 `develop` 拉出，做完合回 `develop`
- 修复功能命名格式为 `fixbug/fixName`，从 `develop` 拉出，做完合回 `develop`；线上紧急故障用 `fixbug/hotfixName` 从 `main` 拉出，修完同时合回 `main` 和 `develop`
- 默认版本号为 `1.0.0`：每次开辟新功能，版本号加 `0.1`；每次修复功能，版本号加 `0.0.1`
- 分支路径为单向合并，禁止把 `main` 分支合并到开发与修复分支
- 每次把开发分支合并到 `main` 发布上线后，标记对应 tag，tag 为版本号
- 禁止将 `develop` 或 `origin/develop` 合并到功能分支
- 为解决与 `develop` 的冲突，只做必要的功能代码调整，并使用模拟合并验证；不得通过引入 `develop` 的整段历史消除冲突

## 分支流向

```
feature/xxx  ─┐
              ├─→ develop ─→ main ─→ tag vX.Y.Z
fixbug/xxx   ─┘        ↑
                       └── fixbug/hotfixName（线上急修：从 main 拉出，修完 main 和 develop 都要合）
```

- **单向**：只允许 `feature/*`、`fixbug/*` → `develop` → `main`。
- 想让功能分支跟上最新代码，用 `git rebase develop`，**不要** `git merge main/develop` 进功能分支。
- 合并统一用 `--no-ff`，保留合并点，方便回溯「哪个分支进了哪个版本」。
- 功能分支合并后即可删除（本地 + 远程），不留长期分支。

## 版本号

- 唯一真源：仓库根目录的 **`VERSION`** 文件（单行，如 `1.1.0`）。
- 递增规则：
  - 新功能：`1.0.0` → `1.1.0` → `1.2.0`（中间位 +1）
  - 修 bug：`1.1.0` → `1.1.1` → `1.1.2`（末位 +1）
- **版本号只在发布时递增**，位置固定写在 `develop` 上（发布提交 `chore(release): x.y.z`），随合并进入 `main`。`feature/*`、`fixbug/*` 里不要碰 `VERSION`，否则会冲突。

## 提交信息

格式：`type(scope): 摘要`

- `type`：`feat` 新功能 / `fix` 修复 / `chore` 杂务 / `docs` 文档 / `refactor` 重构 / `perf` 性能
- `scope` 用模块名（可自行增删）
- 例：`feat(files): 支持在版本内更新字数分析`、`fix(files): 全选框重渲染后复位`

## 发布与 tag

1. 功能分支合回集成分支：`git switch develop && git merge --no-ff feature/xxx`
2. **在 `develop` 上递增版本号**：改 `VERSION` → `git commit -m "chore(release): 1.1.0"` → `git push origin develop`
   - **必须在 `develop` 上改，不能在 `main` 上改。** 因为禁止 `main` 回流开发分支，在 `main` 上递增的版本号永远回不到 `develop`，下次合并 `VERSION` 必然冲突。
3. 发布：`git switch main && git merge --no-ff develop -m "merge: release 1.1.0"`
4. **部署上线（必须用户明确确认）**：只有用户明确说「确认线上发布」这类指令时才执行这一步，默认**只推 GitHub、不动线上**。
5. 打 tag 并推送：`git tag -a v1.1.0 -m "v1.1.0"` → `git push origin main --tags`

> tag 统一带 `v` 前缀（`v1.1.0`），与 `VERSION` 里的 `1.1.0` 一一对应。

## 不进 git 的内容

见 `.gitignore`。建议至少排除：

- 客户/敏感资料目录（含客户数据）——不入库
- 打包产物与二进制（`*.exe`、`*.dll` 等大文件）
- 运行态与临时文件、本地缓存、`__pycache__/`、`.venv/` 等

## 给 AI 助手的额外须知

- **自动托管版本管理（面向不懂 git 的协作者）**：项目使用者大多是**不会敲 git 命令的同事**。AI 助手应**主动、静默地**替他们完成分支创建、提交、推送到 GitHub，不要让他们自己敲命令、也不要甩命令给他们抄。具体：
  - 用户发起一个任务时，AI 主动判断是否要从 `develop` 拉新分支；干完一段活，AI 主动 `commit` 并 `push` 到 GitHub，完成后用一句大白话告诉用户「已保存备份」。
  - 当一个版本攒够（一批功能/修复做完）时，AI **主动询问用户**「这一版是否发布上线？」，由用户决定；用户未明确说「发布/上线」前，一律只推 GitHub、不动线上。
  - 全程用通俗语言跟用户沟通（说「存好了」「要发布吗」这类人话），不要展示 git 命令细节，除非用户主动要学。
- **线上发布默认关闭**：改完代码只推到 GitHub，**不要自作主张部署线上**。只有用户明确说「确认线上发布」之类的指令，才执行发布步骤，并在完成后回报线上复核结果。
- **版本号自己按规则判定，不用反问用户**：新功能 `+0.1`、修 bug `+0.0.1`；不确定是功能还是修复时，按改动性质就近归类并在汇报里说明理由。
- 开工前先 `git status` 确认为干净状态、`git pull` 取最新；**永远不在 `main` 上直接改代码**。
