# Steam 差评 × 更新记录 比对归类工具

输入一个 Steam 游戏 AppID，自动抓取**历史差评**和**游戏更新记录（Patch Notes）**，按时间与关键词比对，把差评归类为 **已解决 / 未解决 / 待确认**，生成一份便于针对性写开发者回复的 HTML 清单。

---

## 一、怎么用（EXE 版）

1. 双击 `Steam差评比对工具.exe`（打包后位于 `dist/` 目录）。
2. 按提示输入游戏的 **AppID**——就是游戏商店页网址末尾那串数字，例如 Dota 2 是 `https://store.steampowered.com/app/570/`，AppID 就是 `570`。
3. 回车后选择语言、页数，工具会自动抓取、比对，生成 HTML 报告并**自动用浏览器打开**。
4. 报告顶部有「已解决 / 未解决 / 待确认」的统计和筛选按钮，点一下就能只看某一类。

### 命令行版（进阶）

```bash
python -m steam_review_tool.main --appid 570 --lang schinese --pages 20 --format html
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `--appid` | Steam 游戏 AppID（必填） | — |
| `--lang` | 评测语言（`schinese` / `english` / `all`） | `all` |
| `--pages` | 抓取差评页数（每页 100 条） | `20` |
| `--format` | 报告格式（`html` / `md`） | `html` |
| `--outdir` | 报告输出目录 | `reports` |

---

## 二、配置 Steam Web API Key（可选，用于抓更新记录）

> 不配置也能用——差评抓取走的是**官方公开接口，无需 Key**。但「更新记录比对」这一核心功能需要 Key 才能拉取 Patch Notes。

1. 到 <https://steamcommunity.com/dev/apikey> 申请（域名栏填你的 GitHub 主页 `https://github.com/你的用户名` 即可，Steam 只做形式记录）。
2. 在**程序同目录**（EXE 旁边）新建一个文件，命名为 `config.local.json`，内容：

```json
{"steam_web_api_key": "把这里换成你的KEY"}
```

3. 保存后重新运行即可。

> ⚠️ 该文件已被 `.gitignore` 排除，**不会**上传到 GitHub，请勿泄露给他人。

---

## 三、数据保密说明

- **API Key**：保存在本地 `config.local.json`，不入库、不上传。
- **抓取到的差评 / 更新数据**：全部写入本地 `reports/`、`data/` 目录，已加入 `.gitignore`，**不会**进入 Git 仓库或 GitHub。
- GitHub 仓库为**私有（Private）**，只有你自己可见。

---

## 四、归类逻辑

1. 提取每条差评文本中的**问题关键词**（闪退、卡顿、优化、存档、汉化、服务器…见 `classifier.py` 的 `PROBLEM_KEYWORDS`）。
2. 只匹配**差评发布之后 90 天内**的更新记录（避免拿陈年更新硬套）。
3. 命中关键词对应「解决信号词」→ **已解决**；有后续更新但没命中 → **未解决**；无关键词或差评后无更新 → **待确认**（留人工判断）。

> 归类结果仅供参考，写开发者回复前建议点报告里的「前往评测页回复」链接人工复核。

---

## 五、开发者须知（版本管理）

本项目遵循根目录 `AGENTS.md` 的协作约定：
- 分支：`main`（仅已发布）← `develop`（集成）← `feature/*` / `fixbug/*`。
- 版本号唯一真源在 `VERSION` 文件，新功能 +0.1、修 bug +0.0.1。
- 打包产物（`dist/`、`build/`、`*.exe`）与敏感数据不入库。

本地重新打包：

```bash
python -m PyInstaller steam_review_tool.spec --noconfirm
```
