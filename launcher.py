"""EXE 双击启动入口（无需命令行）。

打包成 EXE 后，用户双击即可运行：
  1. 提示输入 Steam AppID
  2. 可选输入语言（默认 schinese）
  3. 自动抓取、比对、生成 HTML 报告并打开

本文件放在项目根目录，作为 PyInstaller 的入口脚本。
绝对导入 steam_review_tool 包，避免相对导入在打包时失效。
"""

import os
import sys
import traceback
import webbrowser

# Windows 控制台默认 GBK，强制用 UTF-8 输出，避免中文/特殊符号编码崩溃
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from steam_review_tool.classifier import NewsItem, ReviewItem, classify_all
from steam_review_tool.config import (
    ensure_config_hint,
    get_api_key,
    get_deepseek_key,
    get_llm_model,
    get_llm_base_url,
)
from steam_review_tool.llm_client import LLMClassifier
from steam_review_tool.reporter import write_report
from steam_review_tool.steam_client import SteamClient


def _print(msg: str = "") -> None:
    """安全打印，遇到无法编码的字符自动替换，绝不因编码崩溃。"""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


def _safe_input(prompt: str) -> str:
    """安全读取输入，任何 EOF/异常都返回空串，避免闪退。"""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt, OSError):
        return ""


def _ask_int(prompt: str, default: int | None = None) -> int | None:
    while True:
        raw = _safe_input(prompt).strip()
        if not raw:
            if default is not None:
                return default
            print("  未输入，请重新输入。")
            continue
        try:
            return int(raw)
        except ValueError:
            print("  输入无效，请输入数字。")


def run() -> int:
    _print("=" * 52)
    _print("   Steam 差评 × 更新记录 比对归类工具")
    _print("=" * 52)

    ensure_config_hint()

    appid = _ask_int("\n请输入 Steam 游戏 AppID（游戏商店页 URL 末尾的数字）：")
    if appid is None:
        _print("未输入 AppID，程序退出。")
        return 1

    lang = _safe_input("评测语言（回车默认 schinese，输入 all 取全部语言）：").strip() or "schinese"
    pages_raw = _safe_input("抓取差评页数（每页 100 条，回车默认 10 页）：").strip()
    try:
        pages = int(pages_raw) if pages_raw else 10
    except ValueError:
        pages = 10

    client = SteamClient(api_key=get_api_key())

    _print(f"\n[1/4] 获取游戏信息（AppID {appid}）...")
    details = client.get_app_details(appid)
    if details:
        title = details.get("name", f"AppID {appid}")
        _print(f"      游戏：{title}")
    else:
        title = f"AppID {appid}"
        _print("      游戏信息获取失败（网络超时/不可达），将以 AppID 作为标题继续")

    _print(f"[2/4] 抓取历史差评（最多 {pages * 100} 条）...")
    raw_reviews = client.get_reviews(
        appid, language=lang, review_type="negative", num_per_page=100, max_pages=pages
    )
    reviews = [
        ReviewItem(
            appid=appid,
            author=r.get("author", {}).get("steamid", "匿名"),
            review_text=r.get("review", ""),
            created_at=int(r.get("timestamp_created", 0)),
            voted_up=bool(r.get("voted_up", False)),
            recommendationid=r.get("recommendationid", ""),
        )
        for r in raw_reviews
    ]
    _print(f"      共获取 {len(reviews)} 条差评")

    _print("[3/4] 抓取更新记录（Patch Notes）...")
    raw_news = client.get_news(appid, count=50)
    news = [
        NewsItem(
            title=n.get("title", ""),
            contents=n.get("contents", ""),
            date=int(n.get("date", 0)),
            gid=n.get("gid", ""),
        )
        for n in raw_news
    ]
    _print(f"      共获取 {len(news)} 条更新记录")

    _print("[4/4] 语义比对归类（大模型分析差评问题与补丁修复的对应关系）...")
    llm = None
    if get_deepseek_key():
        llm = LLMClassifier(
            api_key=get_deepseek_key(),
            base_url=get_llm_base_url(),
            model=get_llm_model(),
        )
        _print(f"      已启用大模型语义判断（{get_llm_model()}）")
    else:
        _print("      ⚠ 未启用大模型，归类将全部标为「待确认」")
        _print("      （请在 config.local.json 填入 deepseek_api_key）")
    results = classify_all(reviews, news, llm=llm)
    path = write_report(results, appid, title, "reports", "html")

    _print(f"\n完成！报告已生成：{path}")
    _print("  正在打开报告...")
    abs_path = os.path.abspath(path)
    webbrowser.open("file://" + abs_path)
    _print("\n  按回车键退出。")
    _safe_input("")
    return 0


def main() -> int:
    try:
        return run()
    except Exception:
        # 捕获一切异常，打印错误并等待按键，避免双击时闪退看不到错误
        _print("\n[出错] 程序遇到异常，详情如下：")
        traceback.print_exc()
        _print("\n请把上面的错误信息截图反馈。按回车键退出。")
        _safe_input("")
        return 1


if __name__ == "__main__":
    sys.exit(main())
