"""DeepSeek 大模型语义判断封装。

负责「差评具体问题 ↔ Patch Note 具体修复内容」的语义匹配判定。
核心思路：不再用「bug/修复/fix」这类无区分度的关键词硬匹配（几乎每条
补丁都有 bugfix），而是把每条差评描述的具体问题，交给大模型去和候选
补丁的正文做语义比对，判断该问题是否真的被某条补丁修复了。

设计要点：
  - 直接走 requests 调 DeepSeek 的 OpenAI 兼容接口，不引入 openai SDK
    （减少打包依赖与体积）。
  - 一次请求批量判定多条差评，降低调用次数与成本。
  - 要求模型输出严格 JSON，便于结构化解析与容错降级。
"""

import json
import time
from typing import Any, Optional

import requests

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_CHAT_URL = DEEPSEEK_BASE_URL + "/chat/completions"


class InsufficientBalanceError(Exception):
    """大模型账户余额不足（HTTP 402）。"""

    pass

# 判定系统提示词：强调「必须基于具体问题的语义对应」，禁止用笼统的 bug/修复 词硬判
SYSTEM_PROMPT = (
    "你是一名游戏开发者的客服工具助手，负责判断一条 Steam 差评所反映的具体问题，"
    "是否已经被某条游戏更新记录（Patch Note）真正修复。\n"
    "判定原则（务必严格遵守）：\n"
    "1. 必须分析差评描述的具体问题（例如「换完灵魂刻印后效果消失」「多人联机卡加载」「存档丢失」），"
    "再在补丁正文里寻找针对【同一个具体问题】的修复描述，二者要能对应上才算「已解决」。\n"
    "2. 严禁因为补丁里出现「bug」「修复」「fix」「优化」等笼统字眼就判定已解决——"
    "这类词几乎每条补丁都有，毫无区分度，必须忽略。\n"
    "3. 差评抱怨的是「玩法设计、数值平衡、难度、氪金、画风、社区风气」等主观/设计类问题，"
    "通常补丁不会「修复」，应判为「未解决」或「待确认」。\n"
    "4. 只有当你能够明确指出「补丁第 X 条的哪句话修复了差评里的哪个具体问题」时，才判「已解决」。\n"
    "5. 拿不准、补丁描述含糊、或差评本身没有明确问题（纯情绪发泄/无实质内容）时，判「待确认」。\n"
    "输出格式：必须只输出一个 JSON 对象，不要输出任何多余文字或代码块标记。"
)


def _safe_json(text: str) -> Optional[Any]:
    """从模型返回文本中稳健地解析 JSON（容忍 ```json 包裹、前后杂质）。"""
    if not text:
        return None
    s = text.strip()
    # 去掉可能的代码块标记
    if s.startswith("```"):
        # 找到第一个换行，去掉 ```json 或 ``` 前缀
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        # 去掉结尾的 ```
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 尝试截取第一个 { 到最后一个 } 之间的内容
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


class LLMClassifier:
    """用 OpenAI 兼容的大模型接口对差评做语义归类。

    默认指向 DeepSeek，但 base_url / api_key / model 均可自定义，
    兼容 DeepSeek / OpenAI / Kimi / 通义 / 本地 Ollama 等任何
    OpenAI 兼容接口（只要提供 /chat/completions 端点）。
    """

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = DEEPSEEK_BASE_URL,
        timeout: int = 60,
        max_retries: int = 2,
        max_tokens: int = 4096,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.chat_url = self.base_url + "/chat/completions"
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _chat(self, user_content: str) -> str:
        """调用大模型对话接口，返回模型文本；带重试。"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,  # 判定任务用低温，保证稳定
            "stream": False,
            "max_tokens": self.max_tokens,
        }
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = requests.post(
                    self.chat_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout * (attempt + 1),
                )
                # 402 = 余额不足，重试无意义，直接抛友好错误
                if resp.status_code == 402:
                    raise InsufficientBalanceError(
                        f"大模型账户余额不足（HTTP 402），请检查 {self.base_url} 对应账户的余额后重试"
                    )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except InsufficientBalanceError:
                raise  # 余额不足不重试，直接上抛
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.RequestException,
                KeyError,
                IndexError,
                ValueError,
            ) as e:
                last_exc = e
                if attempt < self.max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    def classify_batch(
        self,
        reviews: list[dict],
        news: list[dict],
    ) -> list[dict]:
        """批量判定一批差评。

        reviews: [{"id": int, "text": str, "date": str}]
        news:    [{"idx": int, "title": str, "date": str, "contents": str}]
        返回:    [{"id": int, "verdict": "已解决|未解决|待确认",
                  "news_idx": int|-1, "reason": str}]
        """
        news_block = "\n".join(
            f"【补丁 {n['idx']}】日期 {n['date']}｜标题：{n['title']}\n内容：{n['contents'][:800]}"
            for n in news
        ) or "（无任何更新记录）"

        review_block = "\n".join(
            f"【差评 {r['id']}】日期 {r['date']}｜内容：{r['text']}" for r in reviews
        )

        user_content = (
            "以下是某游戏的全部更新记录（Patch Note）与若干条差评。\n"
            "请逐条判断每条差评反映的具体问题，是否已被某条补丁【真正修复】。\n\n"
            f"===== 更新记录 =====\n{news_block}\n\n"
            f"===== 待判定的差评 =====\n{review_block}\n\n"
            "请输出一个 JSON 对象，格式如下：\n"
            '{"results": ['
            '{"id": 差评id, "verdict": "已解决|未解决|待确认", '
            '"news_idx": 命中的补丁idx或-1, '
            '"reason": "一句话说明：差评的具体问题，以及补丁里对应的具体修复内容"}]}'
        )
        raw = self._chat(user_content)
        parsed = _safe_json(raw)
        if not parsed or "results" not in parsed:
            return []
        return parsed["results"]


def build_review_dicts(reviews) -> list[dict]:
    """把 ReviewItem 列表转成 LLM 入参格式。"""
    out = []
    for i, r in enumerate(reviews):
        out.append(
            {
                "id": i,
                "text": r.review_text,
                "date": r.created_dt.strftime("%Y-%m-%d"),
            }
        )
    return out


def build_news_dicts(news_items) -> list[dict]:
    """把 NewsItem 列表转成 LLM 入参格式。"""
    out = []
    for i, n in enumerate(news_items):
        out.append(
            {
                "idx": i,
                "title": n.title,
                "date": n.created_dt.strftime("%Y-%m-%d"),
                "contents": n.contents,
            }
        )
    return out
