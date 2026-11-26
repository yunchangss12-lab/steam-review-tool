# Steam 差评 × 更新记录 比对归类工具

输入一个 Steam 游戏的 AppID，自动抓取该游戏的**历史差评**与**更新记录（Patch Notes）**，借助大模型做语义比对，把每条差评归类为 **已解决 / 未解决 / 情绪化评论 / 本地化质量反馈 / 待确认**，并可选生成**推荐回复文案**，最终输出一份可在浏览器中按类别筛选的 **HTML 报告** 和一份按类别分 Sheet 的 **Excel 表格**。

> 面向 Steam 游戏开发者 / 社区运营：一眼看清「哪些差评所反映的问题已经被后续补丁修掉了」，从而有针对性、低成本地回复玩家评测，提升好评率。

---

## ✨ 功能特性

- **历史差评抓取**：抓取游戏全量差评，支持按 **时间范围** 筛选；**语种自动加载**游戏支持的全部语言（也可选「全部语言」无差别抓取）。
- **更新记录比对**：抓取 Patch Notes，自动过滤掉节日祝福、销量庆祝、展会宣传等营销贴，只保留真正的更新/补丁记录。
- **大模型语义归类**：把「差评反映的具体问题」与「补丁修的具体内容」交给大模型做语义匹配，而非关键词硬匹配，显著降低误判。
  - 🟢 **已解决** —— 差评描述的具体问题，能被某条补丁中的具体修复内容对应上
  - 🔴 **未解决** —— 主观/设计类问题，或补丁未针对该问题修复（不生成推荐回复）
  - 💬 **情绪化评论** —— 纯情绪发泄/辱骂，无实质问题描述（安抚为主，不引导反馈 bug）
  - 🔵 **本地化质量反馈** —— 差评指向翻译 / 本地化质量问题（引导转达本地化团队）
  - 🟡 **待确认** —— 有具体问题但补丁含糊、或大模型判定失败，留人工复核
- **两个独立分析开关**：「语义归类（差评↔补丁修复）」与「本地化质量判断（翻译问题）」可分别启停，自由组合，单独跑其一可节省 token。
- **推荐回复生成**：参照回复案例库，为已解决/待确认的差评自动生成回复文案，且**支持在界面中直接编辑**。
- **自定义案例库**：可导入自己的回复案例文件（`xlsx` / `csv` / `md` / `txt` / `docx`，格式不限），替换默认案例库。
- **双格式导出**：HTML 报告（顶部统计 + 按类别筛选按钮 + 跳转评测页回复直链）+ Excel 表格（按类别分 Sheet）。
- **本地运行、数据不上传**：所有数据与 API Key 仅保存在本地，无任何远程上报。

---

## 📦 快速开始

### 方式一：EXE 图形界面（推荐，无需 Python 环境）

1. 从 [Releases](../../releases) 下载 `Steam差评比对工具.exe`（或运行安装包 `Steam差评比对工具_安装包.exe`）。
2. 双击启动，输入游戏的 **AppID**（商店页网址末尾那串数字，例如 Dota 2 为 `https://store.steampowered.com/app/570/`，AppID 即 `570`）。
3. 选择评测语言、时间范围，点击「开始抓取」。
4. 抓取 + AI 分析完成后，会弹出是否生成推荐回复文案的提示（可导入自己的案例库）。
5. 报告和 Excel 自动保存到程序目录的 `reports/` 文件夹。

### 方式二：命令行（开发者 / 进阶）

```bash
git clone git@github.com:yunchangss12-lab/steam-review-tool.git
cd steam-review-tool
pip install -r requirements.txt

python -m steam_review_tool.main \
  --appid 570 \
  --lang schinese \
  --start 2026-01-01 \
  --end 2026-06-30 \
  --format html
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `--appid` | Steam 游戏 AppID（必填） | — |
| `--lang` | 评测语言：`schinese` / `english` / `all` 等 | `all` |
| `--start` | 起始日期 `YYYY-MM-DD`（含） | 不限 |
| `--end` | 结束日期 `YYYY-MM-DD`（含） | 不限 |
| `--format` | 报告格式：`html` / `md` | `html` |
| `--outdir` | 报告输出目录 | `reports` |
| `--no-classify` | 禁用语义归类（差评↔补丁修复），差评标为待确认 | 否 |
| `--localization` | 额外启用本地化质量判断（独立流程，只看差评文本） | 否 |

---

## 🔑 配置 API Key（可选，但建议配置）

> 不配置也能用：差评抓取走的是官方公开接口，无需 Key。但「更新记录比对」需要 Steam Key，「语义归类 + 推荐回复」需要大模型 Key。

在程序同目录（源码运行即项目根目录）新建 `config.local.json`：

```json
{
  "steam_web_api_key": "你的 Steam Key",
  "deepseek_api_key": "你的 DeepSeek Key",
  "llm_model": "deepseek-chat",
  "llm_base_url": "https://api.deepseek.com"
}
```

| 字段 | 作用 | 是否必须 |
|------|------|---------|
| `steam_web_api_key` | 抓取更新记录（Patch Notes） | 建议配置，否则无法比对 |
| `deepseek_api_key` | 大模型语义归类 + 生成回复文案 | 建议配置，否则降级为保守的本地归类 |
| `llm_model` | 大模型名称 | 默认即可 |
| `llm_base_url` | 大模型接口地址 | 默认 DeepSeek，可换成任意 OpenAI 兼容接口 |

> 图形界面内也可以直接填写 **Steam Web API Key**、大模型的 **Base URL / API Key / 模型名**，填写后自动保存，无需手改配置文件。

### 申请 Key

- **Steam Web API Key**：<https://steamcommunity.com/dev/apikey>（域名栏填你的任意主页即可，Steam 只做形式记录）。
- **DeepSeek API Key**：<https://platform.deepseek.com>（注册后充值少量余额，在「API Keys」页面创建）。

> ⚠️ `config.local.json` 已被 `.gitignore` 排除，**不会**上传到 GitHub，请勿分享给他人。

---

## 🧠 归类逻辑

1. **本地粗筛**：判断差评是否有「实质问题」——纯情绪/无内容的差评直接进入「情绪化评论」或「待确认」。
2. **补丁预筛**：只保留「更新日志 / 补丁」类新闻，过滤营销贴（全部时间的补丁都参与匹配，不限时间段）。
3. **大模型精判**：把「差评具体问题 + 全部补丁正文」交给 DeepSeek，要求给出「问题 ↔ 修复」的具体对应关系，据此判定已解决 / 未解决 / 待确认。
4. **本地化质量判断（独立可选）**：只依据差评文本本身，判断其是否指向翻译 / 本地化质量问题，命中则归入「本地化质量反馈」。该流程与语义归类完全分离，可单独开启，无需重复跑一遍归类。
5. **降级兜底**：大模型不可用（无 Key / 调用失败）时，降级为保守的本地规则——**宁可判「待确认」，绝不因笼统的 bug/修复 字眼误判「已解决」**。

> 为什么不关键词硬匹配？「bug」「fix」这类词几乎每条补丁都有，毫无区分度，会导致海量误判。只有语义层面理解「差评说的具体问题」和「补丁修的具体内容」是否一致，才能得到可信的「已解决」结论。

---

## 🗂 项目结构

```
steam-review-tool/
├── gui.py                      # 图形界面入口（tkinter）
├── launcher.py                 # 双击启动器（处理代理/编码/闪退兜底）
├── installer.py                # 自包含安装器源码
├── steam_review_tool/          # 核心包
│   ├── steam_client.py         # Steam 数据获取（差评 + 更新记录）
│   ├── classifier.py           # 五分类归类（含本地化判断）+ 回复生成编排
│   ├── llm_client.py           # DeepSeek 调用（语义归类 + 生成回复）
│   ├── reply_templates.py      # 默认回复案例库（few-shot）
│   ├── reply_cases_loader.py   # 通用案例解析器（xlsx/csv/md/txt/docx）
│   ├── reporter.py             # HTML / Markdown 报告生成
│   ├── excel_exporter.py       # Excel 导出（按类别分 Sheet）
│   ├── config.py               # 配置加载（API Key / LLM 配置）
│   └── main.py                 # 命令行入口
├── installer/                  # 安装包资源（config 模板 + 使用说明）
├── steam_review_tool.spec      # 主程序 PyInstaller 打包配置
├── installer.spec              # 安装器打包配置
├── requirements.txt
├── VERSION
└── LICENSE
```

---

## 🔨 本地打包（可选）

生成单文件 EXE 需要 **官方 Python**（带 `tkinter`），并安装 PyInstaller：

```bash
pip install pyinstaller pysocks requests
python -m PyInstaller steam_review_tool.spec --noconfirm
```

产物位于 `dist/Steam差评比对工具.exe`。

> 已知坑：请勿使用缺少 `tkinter` 的精简版 Python 打包，否则运行时会报 `ModuleNotFoundError: No module named 'tkinter'`。

---

## 🛡 隐私与安全

- **API Key**：仅存于本地 `config.local.json`，已 `.gitignore` 排除，不入库、不上传。
- **抓取数据**：差评与更新记录仅写入本地 `reports/`、`data/` 目录，同样被 `.gitignore` 排除。
- **无遥测**：程序不向任何第三方上报使用数据。

---

## 🤝 贡献

欢迎提交 Issue 与 Pull Request。提交前请确认：

- 不要提交任何真实 API Key 或抓取到的数据（保持 `.gitignore` 规则）。
- 新功能 / 修复请走独立分支，版本号变更遵循 `VERSION` 文件约定。

详见 [AGENTS.md](AGENTS.md) 中的协作约定。

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。
