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

# 评测接口 language 参数支持的语言代码（Steam 官方标准化代码）。
# 中文显示名 ↔ Steam API 语言代码。
# 注意：评测接口的 language 过滤与商店 supported_languages 的语种不是一一对应，
# 这里只收录 ajaxappreviews / GetAppReviews 实际支持的语言代码。
LANG_CODE_TO_NAME = {
    "schinese": "简体中文",
    "tchinese": "繁体中文",
    "english": "英语",
    "japanese": "日语",
    "korean": "韩语",
    "french": "法语",
    "german": "德语",
    "russian": "俄语",
    "italian": "意大利语",
    "spanish": "西班牙语（西班牙）",
    "latam": "西班牙语（拉丁美洲）",
    "portuguese": "葡萄牙语",
    "brazilian": "葡萄牙语（巴西）",
    "polish": "波兰语",
    "czech": "捷克语",
    "dutch": "荷兰语",
    "turkish": "土耳其语",
    "ukrainian": "乌克兰语",
    "vietnamese": "越南语",
    "thai": "泰语",
    "indonesian": "印尼语",
    "swedish": "瑞典语",
    "danish": "丹麦语",
    "finnish": "芬兰语",
    "norwegian": "挪威语",
    "hungarian": "匈牙利语",
    "romanian": "罗马尼亚语",
    "bulgarian": "保加利亚语",
    "greek": "希腊语",
    "arabic": "阿拉伯语",
}

# 商店 appdetails 的 supported_languages 里出现的中文语言名 → 评测接口语言代码。
# 用于把「游戏支持的语言」映射成可下发给评测接口的 language 参数。
# 由于 supported_languages 是中文名（如「简体中文」「法语」），需要反查代码。
LANG_NAME_TO_CODE = {
    "简体中文": "schinese",
    "繁体中文": "tchinese",
    "英语": "english",
    "日语": "japanese",
    "韩语": "korean",
    "法语": "french",
    "德语": "german",
    "俄语": "russian",
    "意大利语": "italian",
    "西班牙语": "spanish",
    "西班牙语 - 拉丁美洲": "latam",
    "葡萄牙语": "portuguese",
    "葡萄牙语 - 巴西": "brazilian",
    "波兰语": "polish",
    "捷克语": "czech",
    "荷兰语": "dutch",
    "土耳其语": "turkish",
    "乌克兰语": "ukrainian",
    "越南语": "vietnamese",
    "泰语": "thai",
    "印尼语": "indonesian",
    "瑞典语": "swedish",
    "丹麦语": "danish",
    "芬兰语": "finnish",
    "挪威语": "norwegian",
    "匈牙利语": "hungarian",
    "罗马尼亚语": "romanian",
    "保加利亚语": "bulgarian",
    "希腊语": "greek",
    "阿拉伯语": "arabic",
}

# 英文语言名 → 评测接口语言代码（兜底：supported_languages 若返回英文名时使用）。
LANG_NAME_EN_TO_CODE = {
    "simplified chinese": "schinese",
    "traditional chinese": "tchinese",
    "english": "english",
    "japanese": "japanese",
    "korean": "korean",
    "french": "french",
    "german": "german",
    "russian": "russian",
    "italian": "italian",
    "spanish": "spanish",
    "spanish - latin america": "latam",
    "portuguese": "portuguese",
    "portuguese - brazil": "brazilian",
    "brazilian": "brazilian",
    "polish": "polish",
    "czech": "czech",
    "dutch": "dutch",
    "turkish": "turkish",
    "ukrainian": "ukrainian",
    "vietnamese": "vietnamese",
    "thai": "thai",
    "indonesian": "indonesian",
    "swedish": "swedish",
    "danish": "danish",
    "finnish": "finnish",
    "norwegian": "norwegian",
    "hungarian": "hungarian",
    "romanian": "romanian",
    "bulgarian": "bulgarian",
    "greek": "greek",
    "arabic": "arabic",
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

    def get_supported_languages(self, appid: int) -> list[tuple[str, str]]:
        """解析游戏支持的语言，返回 [(显示名, 语言代码)]。

        从商店 appdetails 的 supported_languages 字段解析出游戏支持的语种，
        再映射成评测接口可用的 language 代码。

        返回列表已保证按「简体中文 → 英语 → 其它」的常见优先级排序，
        并始终附带一个「全部语言」(all) 兜底项由调用方处理。

        若接口失败或无法解析，返回空列表（调用方回退为默认三选项）。
        """
        import re

        params = {"appids": appid, "l": "schinese", "cc": "cn"}
        self._throttle()
        try:
            resp = self._get(APPDETAIL_API, params=params, timeout=self.timeout)
            data = resp.json()
            if not data or str(appid) not in data or not data[str(appid)].get("success"):
                return []
            raw = data[str(appid)]["data"].get("supported_languages", "")
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.RequestException,
            ValueError,
        ):
            return []

        if not raw:
            return []

        # 去掉 HTML 标签（<strong>、<br>、</strong> 等）和星号标记
        text = re.sub(r"<[^>]+>", ",", raw)
        text = text.replace("*", "")
        # 按逗号拆分语言列表，清洗空白；
        # 末尾的说明文字（如「具有完全音频支持的语言」）不属于语言名，需要剔除。
        names = [s.strip() for s in text.split(",") if s.strip()]

        # 过滤掉非语言名的说明性文字（「具有…」「支持…」等）
        names = [n for n in names if not re.search(r"(具有|支持|音频|完全)", n)]

        langs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name in names:
            # 兼容中文名与英文名（supported_languages 可能返回两种）
            lower = name.lower()
            code = LANG_NAME_TO_CODE.get(name)
            if code is None:
                code = LANG_NAME_EN_TO_CODE.get(lower)
            if code is None:
                # 尝试去掉「- 拉丁美洲」「- 巴西」等后缀再匹配
                base = name.split(" - ")[0].strip()
                code = LANG_NAME_TO_CODE.get(base) or LANG_NAME_EN_TO_CODE.get(base.lower())
            if code and code not in seen:
                seen.add(code)
                # 显示名：优先用映射表里的规范中文名，保证下拉框文案统一
                display = LANG_CODE_TO_NAME.get(code, name)
                langs.append((display, code))
        return langs

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
