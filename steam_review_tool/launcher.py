"""EXE 双击启动入口（无需命令行）。

打包成 EXE 后，用户双击即可运行：
  1. 提示输入 Steam AppID
  2. 可选输入语言（默认 schinese）
  3. 自动抓取、比对、生成 HTML 报告并打开
"""

import os
import sys
import webbrowser

from .config import ensure_config_hint
from .classifier import ReviewItem, NewsItem, classify_all
from .config import get_api_key
from .reporter import write_report
from .steam_client import SteamClient


def _ask_int(prompt: str, default: int | None = None) -> int | None:
    while True:
        try:
            raw = input(prompt).strip()
            if not raw and default is not None:
                return default
            return int(raw)
        except (ValueError, EOFError):
            print("  输入无效，请输入数字。")


def run() -> int:
    print("=" * 50)
    print("  Steam 差评 × 更新记录 比对归类工具")
    print("=" * 50)

    ensure_config_hint()

    appid = _ask_int("\n请输入 Steam 游戏 AppID（游戏商店页 URL 末尾那串数字）：")
    if appid is None:
        print("未输入 AppID，退出。")
        return 1

    lang = input("评测语言（回车默认 schinese，输入 all 取全部语言）：").strip() or "schinese"
    pages_raw = input("抓取差评页数（每页 100 条，回车默认 10 页）：").strip()
    try:
        pages = int(pages_raw) if pages_raw else 10
    except ValueError:
        pages = 10

    client = SteamClient(api_key=get_api_key())

    print(f"\n[1/4] 获取游戏信息（AppID {appid}）...")
    details = client.get_app_details(appid)
    title = details.get("name", f"AppID {appid}") if details else f"AppID {appid}"
    print(f"      游戏：{title}")

    print(f"[2/4] 抓取历史差评（最多 {pages * 100} 条）...")
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
    print(f"      共获取 {len(reviews)} 条差评")

    print("[3/4] 抓取更新记录（Patch Notes）...")
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
    print(f"      共获取 {len(news)} 条更新记录")

    print("[4/4] 比对归类...")
    results = classify_all(reviews, news)
    path = write_report(results, appid, title, "reports", "html")

    print(f"\n✓ 完成！报告已生成：{path}")
    print("  正在打开报告...")
    abs_path = os.path.abspath(path)
    webbrowser.open("file://" + abs_path)
    print("\n  按回车键退出。")
    try:
        input()
    except EOFError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(run())
