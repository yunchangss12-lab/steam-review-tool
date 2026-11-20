"""核心比对归类逻辑。

把每条历史差评与游戏更新记录（Patch Notes）做匹配，判断差评所反映的问题
是否已经在后续某次更新中被解决，输出三分类：
  - 已解决：差评时间之后存在更新，且更新内容命中该差评的关键词
  - 未解决：差评时间之后没有匹配的更新，或明显仍存在的问题
  - 待确认：无法自动判断（如更新内容含糊、关键词缺失）

匹配策略：
  1. 提取差评文本中的关键词（命中问题词表）
  2. 遍历差评发布时间之后的所有更新记录，做关键词 + 时间窗口匹配
  3. 未命中关键词则归为「待确认」，交由人工判断
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Optional

# 问题关键词表：差评 → 对应的「已解决」信号词（出现在更新记录里）
PROBLEM_KEYWORDS: dict[str, list[str]] = {
    "闪退": ["闪退", "崩溃", "crash", "稳定性"],
    "崩溃": ["崩溃", "闪退", "crash", "稳定性"],
    "卡顿": ["卡顿", "掉帧", "性能", "帧率", "优化", "performance", "fps"],
    "掉帧": ["掉帧", "卡顿", "性能", "帧率", "优化", "performance", "fps"],
    "优化": ["优化", "性能", "performance", "帧率", "fps"],
    "bug": ["bug", "修复", "fix", "修复了"],
    "闪屏": ["闪屏", "画面", "显示", "黑屏", "白屏"],
    "黑屏": ["黑屏", "白屏", "闪屏", "画面", "显示"],
    "存档": ["存档", "存档损坏", "save", "存档丢失"],
    "存档丢失": ["存档", "存档损坏", "save"],
    "汉化": ["汉化", "中文", "本地化", "翻译", "localization"],
    "中文": ["中文", "汉化", "本地化", "翻译", "localization"],
    "翻译": ["翻译", "本地化", "汉化", "localization"],
    "连接": ["连接", "网络", "服务器", "延迟", "掉线", "network", "server"],
    "服务器": ["服务器", "连接", "网络", "延迟", "掉线", "server"],
    "掉线": ["掉线", "连接", "网络", "服务器", "延迟"],
    "延迟": ["延迟", "连接", "网络", "服务器", "ping"],
    "手柄": ["手柄", "控制器", "controller", "gamepad", "摇杆"],
    "控制器": ["控制器", "手柄", "controller", "gamepad"],
    "加载": ["加载", "读取", "loading", "载入"],
    "分辨率": ["分辨率", "画面", "显示", "resolution", "4k"],
    "帧数": ["帧数", "帧率", "fps", "性能", "优化"],
    "反作弊": ["反作弊", "anti-cheat", "作弊", "封禁"],
    "封禁": ["封禁", "反作弊", "ban", "误封"],
    "登录": ["登录", "登陆", "login", "账号"],
    "更新": ["更新", "补丁", "patch", "版本"],
}


@dataclass
class ReviewItem:
    appid: int
    author: str  # 作者 steamid（用于生成评测直链）
    review_text: str
    created_at: int  # unix 秒
    voted_up: bool
    recommendationid: str
    author_name: str = ""  # 作者昵称（用于展示）

    @property
    def created_dt(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.created_at)

    @property
    def display_name(self) -> str:
        """展示用的作者名：优先昵称，否则用 steamid 简写。"""
        return self.author_name or (self.author[:8] + "…" if self.author else "匿名")


@dataclass
class NewsItem:
    title: str
    contents: str
    date: int  # unix 秒
    gid: str

    @property
    def created_dt(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.date)


@dataclass
class NewsMatch:
    """一次「差评关键词 → 补丁内容」的命中记录，供报告展示归类依据。"""

    news: NewsItem
    keyword: str  # 差评中命中的问题关键词
    signal: str  # 补丁中命中的解决信号词
    snippet: str  # 补丁内容中命中处的上下文片段（便于人工复核）


@dataclass
class ClassifiedReview:
    review: ReviewItem
    category: str  # 已解决 / 未解决 / 待确认
    matched_keywords: list[str] = field(default_factory=list)
    matched_news: list[NewsItem] = field(default_factory=list)
    matches: list[NewsMatch] = field(default_factory=list)  # 详细命中记录


def extract_keywords(text: str) -> list[str]:
    """从差评文本中提取命中的问题关键词（去重）。"""
    found: list[str] = []
    for keyword in PROBLEM_KEYWORDS:
        if keyword.lower() in text.lower():
            found.append(keyword)
    return found


def classify_review(
    review: ReviewItem, news_items: list[NewsItem]
) -> ClassifiedReview:
    """对单条差评做归类。

    仅考虑差评发布时间之后、且时间窗口内（默认 90 天）的更新记录，
    避免拿很久以前的更新硬套。
    """
    keywords = extract_keywords(review.review_text)

    # 没有可识别的问题关键词 → 待确认
    if not keywords:
        return ClassifiedReview(review, "待确认", keywords, [])

    # 差评之后 90 天内的更新
    window_end = review.created_at + 90 * 24 * 3600
    later_news = [
        n for n in news_items if review.created_at <= n.date <= window_end
    ]

    # 用关键词对应的「解决信号词」去匹配更新内容，并记录具体命中详情
    matched_news: list[NewsItem] = []
    matches: list[NewsMatch] = []
    # 差评命中的信号词集合（问题关键词 → 对应解决信号词）
    signal_map: dict[str, list[str]] = {
        kw: PROBLEM_KEYWORDS.get(kw, [kw]) for kw in keywords
    }

    for news in later_news:
        haystack = (news.title + " " + news.contents).lower()
        for kw in keywords:
            for sig in signal_map[kw]:
                pos = haystack.find(sig.lower())
                if pos != -1:
                    snippet = _extract_snippet(news.contents, sig)
                    matches.append(NewsMatch(news, kw, sig, snippet))
                    if news not in matched_news:
                        matched_news.append(news)
                    break  # 该关键词命中即可，换下一个关键词

    if matched_news:
        return ClassifiedReview(review, "已解决", keywords, matched_news, matches)

    # 有后续更新但都没命中 → 未解决
    if later_news:
        return ClassifiedReview(review, "未解决", keywords, [])

    # 差评之后没有任何更新 → 待确认（可能问题仍存在，需人工核实）
    return ClassifiedReview(review, "待确认", keywords, [])


def _extract_snippet(contents: str, signal: str, radius: int = 40) -> str:
    """从补丁正文中截取命中信号词附近的一段文字，用于展示归类依据。"""
    if not contents:
        return ""
    low = contents.lower()
    pos = low.find(signal.lower())
    if pos == -1:
        return contents[: radius * 2].strip()
    start = max(0, pos - radius)
    end = min(len(contents), pos + len(signal) + radius)
    snippet = contents[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(contents):
        snippet = snippet + "…"
    return snippet


def classify_all(
    reviews: list[ReviewItem], news_items: list[NewsItem]
) -> list[ClassifiedReview]:
    return [classify_review(r, news_items) for r in reviews]


def summarize(results: list[ClassifiedReview]) -> dict[str, int]:
    counts = {"已解决": 0, "未解决": 0, "待确认": 0}
    for r in results:
        counts[r.category] += 1
    return counts
