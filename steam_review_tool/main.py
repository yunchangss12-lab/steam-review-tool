"""命令行主入口。

用法：
    python -m steam_review_tool.main --appid 570 --lang schinese [--start 2026-01-01] [--end 2026-06-30] [--format html]

参数说明：
    --appid   Steam 游戏 AppID（必填）
    --lang    评测语言过滤，默认 all（如 schinese / english）
    --start   起始日期（含），格式 YYYY-MM-DD，默认不限
    --end     结束日期（含），格式 YYYY-MM-DD，默认不限
    --format  报告格式：html（默认）/ md
"""

import argparse
import sys

from .classifier import ReviewItem, NewsItem, classify_all
from .config import get_api_key, ensure_config_hint
from .reporter import write_report
from .steam_client import SteamClient


def _parse_date(s: str) -> int | None:
    """把 YYYY-MM-DD 解析成 Unix 时间戳；空串返回 None。"""
    if not s:
        return None
    import datetime as _dt
    try:
        return int(_dt.datetime.strptime(s, "%Y-%m-%d").timestamp())
    except ValueError:
        raise SystemExit(f"日期格式错误：{s!r}，应为 YYYY-MM-DD")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Steam 差评 × 更新记录比对归类工具")
    parser.add_argument("--appid", type=int, required=True, help="Steam 游戏 AppID")
    parser.add_argument("--lang", default="all", help="评测语言，如 schinese/english")
    parser.add_argument("--start", default="", help="起始日期 YYYY-MM-DD（含，默认不限）")
    parser.add_argument("--end", default="", help="结束日期 YYYY-MM-DD（含，默认不限）")
    parser.add_argument("--format", default="html", choices=["html", "md"], help="报告格式")
    parser.add_argument("--outdir", default="reports", help="报告输出目录")
    args = parser.parse_args(argv)

    date_start = _parse_date(args.start)
    date_end = _parse_date(args.end)
    if date_end is not None:
        date_end += 86400 - 1  # 结束日期取当天末尾

    ensure_config_hint()
    client = SteamClient(api_key=get_api_key())

    print(f"[1/4] 获取游戏信息（AppID {args.appid}）...")
    details = client.get_app_details(args.appid)
    title = details.get("name", f"AppID {args.appid}") if details else f"AppID {args.appid}"
    print(f"      游戏：{title}")

    rng_desc = f"{args.start or '不限'} ~ {args.end or '不限'}"
    print(f"[2/4] 抓取差评（时间范围：{rng_desc}）...")
    raw_reviews = client.get_reviews(
        args.appid,
        language=args.lang,
        review_type="negative",
        num_per_page=100,
        max_pages=200,
        date_start=date_start,
        date_end=date_end,
    )
    reviews = [
        ReviewItem(
            appid=args.appid,
            author=r.get("author", {}).get("steamid", "匿名"),
            author_name=r.get("author", {}).get("personaname", ""),
            review_text=r.get("review", ""),
            created_at=int(r.get("timestamp_created", 0)),
            voted_up=bool(r.get("voted_up", False)),
            recommendationid=r.get("recommendationid", ""),
        )
        for r in raw_reviews
    ]
    print(f"      共获取 {len(reviews)} 条差评")

    print("[3/4] 抓取更新记录（Patch Notes）...")
    raw_news = client.get_news(args.appid, count=50)
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
    path = write_report(results, args.appid, title, args.outdir, args.format)
    print(f"\n✓ 完成！报告已生成：{path}")
    print("  双击 HTML 文件即可在浏览器查看，按类别筛选后针对性写开发者回复。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
