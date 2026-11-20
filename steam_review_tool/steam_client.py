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

REVIEWS_API = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
APPDETAIL_API = "https://store.steampowered.com/api/appdetails"
NEWS_API = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"

HEADERS = {"User-Agent": "SteamReviewTool/1.0 (developer support tool)"}


class SteamClient:
    def __init__(
        self,
        api_key: str = "",
        timeout: int = 20,
        rate_limit: float = 1.0,
        max_retries: int = 3,
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
        """带重试的 GET 请求。

        - trust_env=False：忽略系统/环境变量里的代理配置，直接连接 Steam。
          原因：Steam API 直连稳定可用，而用户机器上可能残留无效的
          HTTP/SOCKS 代理配置（如 Clash/v2ray 等），走代理反而会导致
          "Missing dependencies for SOCKS support" 或超时。
        - 指数退避重试：国内访问 api.steampowered.com 常有间歇性超时。
        """
        session = requests.Session()
        session.trust_env = False  # 关键：忽略环境代理，直连
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = session.get(
                    url,
                    headers=HEADERS,
                    timeout=self.timeout * (attempt + 1),
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
        max_pages: int = 20,
    ) -> list[dict]:
        """分页拉取评测（官方 IUserReviewsService 接口）。

        review_type: "negative"（差评）/ "positive"（好评）/ "all"
        返回按时间倒序的评测列表（最新在前）。

        说明：
        - filter 用 recent（按创建时间倒序），这样配合 cursor 分页能取到
          「最新一批差评」，而不是按好评度排序。
        - languages 用 [language]；"all" 表示所有语言。
        - Service 接口需把参数整体放进 input_json。
        """
        reviews: list[dict] = []
        cursor = "*"
        # review_type 枚举（官方 EUserReviewsReviewType）：
        #   0 = all, 1 = positive(好评), 2 = negative(差评)
        rt_map = {"negative": 2, "positive": 1, "all": 0}
        review_type_id = rt_map.get(review_type, 2)

        for _ in range(max_pages):
            payload = {
                "json": 1,
                "appid": appid,
                "filter": "recent",
                "languages": ["all"] if language == "all" else [language],
                "review_type": review_type_id,
                "purchase_type": "all",
                "num_per_page": num_per_page,
                "cursor": cursor,
            }
            self._throttle()
            resp = self._get(
                REVIEWS_API,
                params={"input_json": json.dumps(payload)},
            )
            data = resp.json()
            resp_body = data.get("response", {})
            reviews.extend(resp_body.get("reviews", []))
            cursor = resp_body.get("cursor", "")
            if not cursor or cursor == "*" or len(resp_body.get("reviews", [])) < num_per_page:
                break
        return reviews

    def get_app_details(self, appid: int) -> Optional[dict]:
        params = {"appids": appid, "l": "schinese", "cc": "cn"}
        self._throttle()
        resp = self._get(APPDETAIL_API, params=params)
        data = resp.json()
        if data and str(appid) in data and data[str(appid)].get("success"):
            return data[str(appid)]["data"]
        return None

    def get_news(self, appid: int, count: int = 50, maxlength: int = 2000) -> list[dict]:
        """拉取更新记录 / Patch Notes。需要 API Key。"""
        if not self.api_key:
            return []
        params = {
            "appid": appid,
            "count": count,
            "maxlength": maxlength,
            "format": "json",
        }
        self._throttle()
        resp = self._get(NEWS_API, params=params)
        data = resp.json()
        items = (
            data.get("appnews", {}).get("newsitems", [])
            if data.get("appnews")
            else []
        )
        return items
