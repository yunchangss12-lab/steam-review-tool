"""核心比对归类逻辑。

把每条历史差评与游戏更新记录（Patch Notes）做语义匹配，判断差评所反映的
具体问题是否已经在后续某次更新中被真正修复，输出三分类：
  - 已解决：差评描述的具体问题，能被某条补丁中的具体修复内容对应上
  - 未解决：差评反映的是主观/设计类问题，或补丁没有针对该问题的修复
  - 待确认：无法判断（差评无实质内容 / 补丁描述含糊 / 大模型不可用）

匹配策略（语义驱动，不再是关键词硬匹配）：
  1. 本地粗筛：判断差评是否有「实质问题」（纯情绪/无内容直接待确认）。
  2. 大模型精判：把「差评具体问题 + 全部补丁正文」交给 DeepSeek，
     要求其给出「问题 ↔ 修复」的具体对应关系，据此判定已解决/未解决/待确认。
     全部补丁都参与匹配（不限时间段）。
  3. 大模型不可用（无 Key / 调用失败）时，降级为保守的本地规则：
     宁可判「待确认」，绝不因笼统的 bug/修复 字眼误判「已解决」。

为什么不用关键词硬匹配：
  - 「bug」「修复」「fix」这类词几乎每条补丁都有，无区分度，会导致海量误判。
  - 只有语义层面理解「差评说的具体问题」和「补丁修的具体内容」是否一致，
    才能得到可信的「已解决」结论。
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from .llm_client import LLMClassifier, build_news_dicts, build_review_dicts

# 补丁预筛关键词：只保留「更新日志/补丁」类新闻作为候选，过滤节日祝福、
# 销量庆祝、展会宣传、合作公告等与 bug 修复无关的营销贴。
# 注意：这里只做「内容相关性」过滤，不做「时间段」过滤（全部时间都参与）。
PATCH_TITLE_HINTS = (
    "update notes",
    "update",
    "patch",
    "bug fix",
    "hotfix",
    "hot fix",
    "更新",
    "补丁",
    "修复",
    "维护",
    "优化",
    "平衡性",
)


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
    """一次「差评问题 → 补丁修复」的语义对应记录，供报告展示归类依据。

    与旧版不同，这里不再记录「关键词 + 信号词」的硬命中，而是记录大模型
    给出的「具体问题 ↔ 具体修复」对应关系。
    """

    news: NewsItem
    reason: str = ""  # 大模型给出的一句话判定理由


@dataclass
class ClassifiedReview:
    review: ReviewItem
    category: str  # 已解决 / 未解决 / 待确认
    matched_news: list[NewsItem] = field(default_factory=list)
    matches: list[NewsMatch] = field(default_factory=list)  # 详细命中记录
    reason: str = ""  # 判定理由（大模型给出，供报告展示）
    reply_suggestion: str = ""  # 推荐回复文案（大模型生成，未解决为空）


def _filter_patch_news(news_items: list[NewsItem]) -> list[NewsItem]:
    """预筛补丁：只保留标题含「更新/补丁/修复」等关键字的新闻。

    目的是过滤掉节日祝福、销量庆祝、展会宣传、合作公告等与 bug 修复无关的
    营销贴，避免喂给大模型造成干扰与浪费。时间段不做限制（全部参与）。
    """
    filtered: list[NewsItem] = []
    for n in news_items:
        title_low = n.title.lower()
        if any(hint in title_low for hint in PATCH_TITLE_HINTS):
            filtered.append(n)
    # 若预筛后为空（极少数游戏标题不规范），回退为全部
    return filtered if filtered else list(news_items)


def classify_all(
    reviews: list[ReviewItem],
    news_items: list[NewsItem],
    llm: Optional[LLMClassifier] = None,
    batch_size: int = 10,
) -> list[ClassifiedReview]:
    """对全部差评做归类。

    llm: 已初始化的大模型分类器。若为 None 或不可用，则走保守本地降级。
    news_items 全部参与匹配（不限时间段——补丁修复不区分差评前后）。
    """
    # 排序补丁（升序），便于后续按差评时间过滤
    news_sorted = sorted(news_items, key=lambda n: n.date)
    # 预筛：过滤与修复无关的营销贴（不按时间过滤）
    news_candidates = _filter_patch_news(news_sorted)

    # 若大模型可用，走语义精判
    if llm is not None and llm.available:
        try:
            return _classify_with_llm(reviews, news_candidates, llm, batch_size)
        except Exception as e:
            # 大模型调用失败 → 降级到保守本地规则，并附上失败原因
            return _classify_fallback(reviews, news_candidates, reason=str(e))

    return _classify_fallback(reviews, news_candidates)


def _classify_with_llm(
    reviews: list[ReviewItem],
    news_sorted: list[NewsItem],
    llm: LLMClassifier,
    batch_size: int,
) -> list[ClassifiedReview]:
    """大模型语义精判（分批调用，避免单次请求过长）。"""
    review_dicts = build_review_dicts(reviews)
    news_dicts = build_news_dicts(news_sorted)
    # news_dicts 的 idx 对应 news_sorted 的下标

    verdicts: dict[int, dict] = {}

    for start in range(0, len(review_dicts), batch_size):
        batch = review_dicts[start:start + batch_size]
        try:
            results = llm.classify_batch(batch, news_dicts)
        except Exception:
            results = []
        if not results:
            # 该批调用失败：这批全部落为待确认
            for r in batch:
                verdicts[r["id"]] = {
                    "verdict": "待确认",
                    "news_idx": -1,
                    "reason": "（大模型判定失败，需人工复核）",
                }
            continue
        for res in results:
            rid = res.get("id")
            if rid is None:
                continue
            verdicts[int(rid)] = {
                "verdict": str(res.get("verdict", "待确认")),
                "news_idx": res.get("news_idx", -1),
                "reason": str(res.get("reason", "")),
            }

    # 汇总为 ClassifiedReview
    out: list[ClassifiedReview] = []
    for i, review in enumerate(reviews):
        v = verdicts.get(i)
        if v is None:
            out.append(ClassifiedReview(review, "待确认", [], [], ""))
            continue
        category = v["verdict"] if v["verdict"] in ("已解决", "未解决", "待确认") else "待确认"
        matched_news: list[NewsItem] = []
        matches: list[NewsMatch] = []
        news_idx = v.get("news_idx")
        if category == "已解决" and isinstance(news_idx, int) and 0 <= news_idx < len(news_sorted):
            n = news_sorted[news_idx]
            matched_news.append(n)
            matches.append(NewsMatch(news=n, reason=v["reason"]))
        out.append(ClassifiedReview(review, category, matched_news, matches, v["reason"]))
    return out


def _classify_fallback(
    reviews: list[ReviewItem], news_sorted: list[NewsItem], reason: str = ""
) -> list[ClassifiedReview]:
    """大模型不可用时的保守降级：一律判「待确认」，绝不误判「已解决」。

    这样做的原因：本地关键词匹配（尤其是 bug/修复 这类词）不可靠，
    与其给出大量错误的「已解决」，不如保守地交给人工。
    """
    msg = reason or "（未配置大模型或调用失败，需人工复核）"
    out: list[ClassifiedReview] = []
    for review in reviews:
        # 纯情绪/无实质内容的差评也归「待确认」
        out.append(ClassifiedReview(review, "待确认", [], [], msg))
    return out


def summarize(results: list[ClassifiedReview]) -> dict[str, int]:
    counts = {"已解决": 0, "未解决": 0, "待确认": 0}
    for r in results:
        counts[r.category] += 1
    return counts


def generate_replies(
    results: list[ClassifiedReview],
    llm: Optional[LLMClassifier],
    cases_text: str = "",
    system_prompt: str = "",
) -> int:
    """为归类结果生成推荐回复文案。

    只对「已解决」和「待确认」的差评生成；「未解决」（设计/数值等主观问题）
    不生成（保持 reply_suggestion 为空）。

    cases_text:    自定义回复案例库文本；为空则用内置默认案例库。
    system_prompt: 自定义系统提示词；为空则用内置默认。

    逐条调用大模型，生成结果写回每个 ClassifiedReview.reply_suggestion。
    返回成功生成的条数。
    """
    if llm is None or not llm.available:
        return 0

    count = 0
    for r in results:
        if r.category == "未解决":
            continue  # 未解决不生成回复
        try:
            reply = llm.generate_reply(
                r.review.review_text,
                r.category,
                r.reason,
                cases_text=cases_text,
                system_prompt=system_prompt,
            )
            if reply:
                r.reply_suggestion = reply
                count += 1
        except Exception:
            # 单条失败不影响整体，继续下一条
            continue
    return count
