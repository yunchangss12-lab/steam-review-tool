"""Steam 数据获取模块。

数据源：
  1. IUserReviewsService/GetAppReviews（官方，无需 Key）：
     - 分页拉取指定 AppID 的评测，可按语言/好评差评过滤。
     - 官方替代已移除的 /appreviews 旧接口。
  2. Storefront appdetails（公开，无需 Key）：
     - 拉取游戏基本信息。
  3. Steamworks 新闻接口（需 Web API Key）：
     - GetNewsForApp：拉取游戏更新记录 / Patch Notes。

全部为只读请求，不修改任何 Steam 侧数据。
"""

import json
import time
from typing import Optional

import requests

# 社区内部评测接口：能拉到「全量历史评测」，不受官方接口 30 天窗口限制。
# 这是商店/社区页面实际用来加载评测列表的接口（Revadike/InternalSteamWebAPI 文档）。
REVIEWS_API = "https://store.steampowered.com/ajaxappreviews/{appid}"
# 官方 Service 接口：作为兜底，但它只返回最近 30 天（day_range_used=30），
# 无法取到历史全量，故仅作降级方案。
REVIEWS_API_FALLBACK = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
APPDETAIL_API = "https://store.steampowered.com/api/appdetails"
NEWS_API = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"

HEADERS = {
    "User-Agent": "SteamReviewTool/1.0 (developer support tool)",
    "Accept": "application/json, text/plain, */*",
}


class SteamClient:
    def __init__(
        self,
        api_key: str = "",
        timeout: int = 12,
        rate_limit: float = 1.0,
        max_retries: int = 2,
    ):
        self.api_key = api_key
        self.timeout = timeout
        self.rate_limit = rate_limit  # 请求间隔，避免触发限流
        self.max_retries = max_retries  # 网络抖动时自动重试次数
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.time()

    def _get(self, url: str, **kwargs) -> requests.Response:
        """带重试与「代理/直连双通道」的 GET 请求。

        策略：每个请求先走系统代理（trust_env=True），失败后回退直连
        （trust_env=False）。这样无论用户开没开代理软件都能连上 Steam：
        - 开了代理 → 走代理成功
        - 没开/代理失效 → 回退直连成功
        - 均失败 → 抛异常（由上层统一捕获，避免闪退）

        同时指数退避重试，缓解国内访问 Steam API 的间歇性超时。
        """
        last_exc: Exception | None = None
        # 允许调用方显式传入基础超时，否则用实例默认值
        base_timeout = kwargs.pop("timeout", self.timeout)
        # 通道顺序：先代理，后直连
        for use_proxy in (True, False):
            session = requests.Session()
            session.trust_env = use_proxy
            for attempt in range(self.max_retries):
                try:
                    resp = session.get(
                        url,
                        headers=HEADERS,
                        timeout=base_timeout * (attempt + 1),
                        **kwargs,
                    )
                    resp.raise_for_status()
                    return resp
                except (
                    requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.InvalidSchema,
                ) as e:
                    last_exc = e
                    if attempt < self.max_retries - 1:
                        time.sleep(1.5 * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    def get_reviews(
        self,
        appid: int,
        language: str = "all",
        review_type: str = "negative",
        num_per_page: int = 100,
        max_pages: int = 200,
        date_start: Optional[int] = None,
        date_end: Optional[int] = None,
    ) -> list[dict]:
        """分页拉取评测（社区内部 ajaxappreviews 接口，全量历史）。

        review_type: "negative"（差评）/ "positive"（好评）/ "all"
        返回按时间倒序的评测列表（最新在前）。

        date_start / date_end: Unix 时间戳，限定评测创建时间范围（含边界）。
        两者都为 None 时抓全量历史；只给 date_start 表示「从该时间至今」；
        只给 date_end 表示「从最早到该时间」。内部转成接口的
        start_date/end_date/date_range_type=include 参数。

        为什么不用官方 GetAppReviews：
          - 官方接口 filter=recent 会硬性 day_range_used=30，只返回最近 30 天，
            历史差评（游戏可能有几千条）根本拉不到。
          - 社区内部接口 /ajaxappreviews/:appid 是商店/社区页面实际使用的接口，
            能通过 cursor 翻页拿到「全量历史评测」，无 30 天窗口限制，
            且支持 start_date/end_date 精确时间范围过滤。
        """
        reviews: list[dict] = []
        cursor = "*"
        url = REVIEWS_API.format(appid=appid)

        for _ in range(max_pages):
            params = {
                "json": 1,
                "review_type": review_type,  # negative / positive / all（字符串）
                "purchase_type": "all",
                "language": language,
                "num_per_page": num_per_page,
                "cursor": cursor,
                "filter": "recent",  # 按创建时间倒序，可完整遍历到空列表
            }
            # 时间范围过滤（仅当至少有一个时间边界时启用）
            # 注意：接口要求 start_date 与 end_date 必须成对出现才生效，
            # 只传其一会被忽略。因此缺省一侧时补全边界：
            #   start 缺失 -> 0（最早）；end 缺失 -> 当前时间（最新）。
            if date_start is not None or date_end is not None:
                params["date_range_type"] = "include"
                params["start_date"] = int(date_start) if date_start is not None else 0
                params["end_date"] = int(date_end) if date_end is not None else int(time.time())

            self._throttle()
            try:
                resp = self._get(url, params=params)
                data = resp.json()
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.RequestException,
                ValueError,
            ):
                # 翻页过程中偶发超时：保留已抓到的部分，终止翻页，避免前功尽弃
                break
            page_reviews = data.get("reviews", [])
            reviews.extend(page_reviews)
            new_cursor = data.get("cursor", "")
            # 终止条件：无更多数据 / cursor 不再变化 / 返回不足一页
            if not page_reviews or new_cursor == cursor or not new_cursor:
                break
            cursor = new_cursor
        return reviews

    def get_app_details(self, appid: int) -> Optional[dict]:
        """拉取游戏基本信息（商店 appdetails 接口）。

        这个接口偶尔在国内网络下超时/不通，但它不是核心数据——
        失败时返回 None（由上层降级为「AppID xxxx」作标题），
        绝不让它拖垮整个抓取流程。
        """
        params = {"appids": appid, "l": "schinese", "cc": "cn"}
        self._throttle()
        try:
            # 商店接口用更短的超时，避免长时间卡死等待
            resp = self._get(APPDETAIL_API, params=params, timeout=self.timeout)
            data = resp.json()
            if data and str(appid) in data and data[str(appid)].get("success"):
                return data[str(appid)]["data"]
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.RequestException,
            ValueError,
        ):
            # 游戏信息获取失败不致命：标题降级为 AppID，差评/更新记录照常抓
            pass
        return None

    def get_news(self, appid: int, count: int = 50, maxlength: int = 2000) -> list[dict]:
        """拉取更新记录 / Patch Notes。需要 API Key。

        失败时返回空列表（归类仍可基于差评文本自身的信号词进行），
        不让更新记录接口的抖动中断整个流程。
        """
        if not self.api_key:
            return []
        params = {
            "appid": appid,
            "count": count,
            "maxlength": maxlength,
            "format": "json",
        }
        self._throttle()
        try:
            resp = self._get(NEWS_API, params=params)
            data = resp.json()
            items = (
                data.get("appnews", {}).get("newsitems", [])
                if data.get("appnews")
                else []
            )
            return items
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.RequestException,
            ValueError,
        ):
            return []
