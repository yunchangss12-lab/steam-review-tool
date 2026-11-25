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
    "通常补丁不会「修复」，应判为「未解决」。\n"
    "4. 只有当你能够明确指出「补丁第 X 条的哪句话修复了差评里的哪个具体问题」时，才判「已解决」。\n"
    "5. 差评是「纯情绪发泄/辱骂/嘲讽，没有任何实质问题描述」（例如「垃圾游戏」「做的是个狗把」「纯纯的废物」），"
    "应单独判为「情绪化评论」——这类差评没有可对应的具体问题，无需去补丁里找修复。\n"
    "6. 只有当差评确实描述了某个具体问题、但补丁描述含糊/拿不准是否修复时，才判「待确认」。\n"
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

    def _chat(self, user_content: str, system_prompt: str = "") -> str:
        """调用大模型对话接口，返回模型文本；带重试。

        system_prompt: 为空则用默认归类提示词，否则用传入的自定义提示词。
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
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

    def _chat_with_system(self, system_prompt: str, user_content: str) -> str:
        """用自定义 system prompt 调用对话接口（兼容旧调用方式）。"""
        return self._chat(user_content, system_prompt=system_prompt)

    def classify_batch(
        self,
        reviews: list[dict],
        news: list[dict],
        check_localization: bool = False,
    ) -> list[dict]:
        """批量判定一批差评。

        reviews: [{"id": int, "text": str, "date": str}]
        news:    [{"idx": int, "title": str, "date": str, "contents": str}]
        check_localization: 是否额外启用「本地化质量反馈」判断（可选）。
        返回:    [{"id": int, "verdict": "已解决|未解决|待确认|情绪化评论|本地化质量反馈",
                  "news_idx": int|-1, "reason": str}]
        """
        news_block = "\n".join(
            f"【补丁 {n['idx']}】日期 {n['date']}｜标题：{n['title']}\n内容：{n['contents'][:800]}"
            for n in news
        ) or "（无任何更新记录）"

        review_block = "\n".join(
            f"【差评 {r['id']}】日期 {r['date']}｜内容：{r['text']}" for r in reviews
        )

        verdict_enum = "已解决|未解决|待确认|情绪化评论"
        localization_rule = ""
        if check_localization:
            verdict_enum += "|本地化质量反馈"
            localization_rule = (
                "7. 若差评抱怨的是「翻译/本地化质量」问题（例如：翻译错误、错译、漏译、"
                "文本显示乱码/方框、某语言内容缺失或不完整、术语翻译不统一、语句不通顺/机翻感、"
                "界面文字未翻译仍显示英文等），应单独判为「本地化质量反馈」——"
                "这类问题与游戏本身的 bug 修复无关，不要归入已解决/未解决/待确认。\n"
            )

        user_content = (
            "以下是某游戏的全部更新记录（Patch Note）与若干条差评。\n"
            "请逐条判断每条差评反映的具体问题，是否已被某条补丁【真正修复】。\n\n"
            f"===== 更新记录 =====\n{news_block}\n\n"
            f"===== 待判定的差评 =====\n{review_block}\n\n"
            "请输出一个 JSON 对象，格式如下：\n"
            '{"results": ['
            '{"id": 差评id, "verdict": "' + verdict_enum + '", '
            '"news_idx": 命中的补丁idx或-1, '
            '"reason": "一句话说明：差评的具体问题，以及补丁里对应的具体修复内容（情绪化评论则说明为何判定为情绪发泄；本地化质量反馈则说明具体是哪种翻译问题）"}]}'
        )

        if localization_rule:
            # 把本地化规则插入系统提示词（在第 6 条规则之后）
            sys_prompt = SYSTEM_PROMPT.replace(
                "输出格式：必须只输出一个 JSON 对象，不要输出任何多余文字或代码块标记。",
                localization_rule + "输出格式：必须只输出一个 JSON 对象，不要输出任何多余文字或代码块标记。",
            )
            raw = self._chat_with_system(sys_prompt, user_content)
        else:
            raw = self._chat(user_content)
        parsed = _safe_json(raw)
        if not parsed or "results" not in parsed:
            return []
        return parsed["results"]

    def generate_reply(
        self,
        review_text: str,
        category: str,
        reason: str = "",
        cases_text: str = "",
        system_prompt: str = "",
    ) -> str:
        """为单条差评生成一条推荐回复文案。

        仅对「已解决」「情绪化评论」「待确认」生成；「未解决」（设计/数值等
        主观问题）不生成回复（调用方应跳过）。
        - 已解决 → 告知已修复
        - 情绪化评论 → 安抚为主，不引导反馈 bug
        - 待确认 → 安抚 + 引导反馈

        category: 已解决 / 未解决 / 情绪化评论 / 待确认
        reason:   归类时大模型给出的判定理由（已解决=具体修复内容，待确认=判定理由）
        cases_text:   自定义回复案例库文本；为空则用内置默认案例库。
        system_prompt: 自定义系统提示词；为空则用内置默认。
        返回回复文案文本；失败返回空串。
        """
        # 未解决不生成回复
        if category == "未解决":
            return ""

        from .reply_templates import build_reply_cases_text, REPLY_SYSTEM_PROMPT

        if not cases_text:
            cases_text = build_reply_cases_text()
        if not system_prompt:
            system_prompt = REPLY_SYSTEM_PROMPT

        # 根据类别定制指令
        if category == "已解决":
            instruction = (
                "这条差评反映的问题已经被某次游戏更新修复了。"
                "请据此撰写回复：先致歉，再告知问题已在更新中修复、感谢反馈。"
                "可结合下面给出的「判定理由」中提到的具体修复内容来措辞。"
            )
        elif category == "情绪化评论":
            instruction = (
                "这条差评是纯情绪发泄/辱骂，没有实质问题描述。"
                "请据此撰写回复：以安抚情绪为主，态度诚恳、表达歉意与理解，"
                "感谢玩家关注并邀请继续游玩/提出建议即可。"
                "【不要】主动让玩家反馈 bug、不要引导提交问题详情、不要提及客服排查。"
            )
        elif category == "本地化质量反馈":
            instruction = (
                "这条差评反映的是翻译/本地化质量问题（如错译、漏译、乱码、语言不完整等）。"
                "请据此撰写回复：先感谢玩家指出问题、表达歉意，再表示会把问题转达给本地化团队"
                "尽快修正，并欢迎继续反馈其他翻译问题。"
                "语气诚恳、专业，体现对本地化质量的重视。"
            )
        else:  # 待确认
            instruction = (
                "这条差评反映的问题暂时无法确认是否已解决。"
                "请据此撰写回复：先致歉/安抚，再表示已收到反馈、会尽快排查，"
                "并引导玩家通过官方渠道反馈详情。"
            )

        user_content = (
            f"{instruction}\n\n"
            f"===== 历史回复案例（请严格参照其语气和措辞风格）=====\n{cases_text}\n\n"
            f"===== 待回复的差评 =====\n{review_text}\n\n"
            f"===== 归类判定理由（供参考）=====\n{reason or '（无）'}\n\n"
            "请直接输出回复文案正文，不要任何解释或标记。"
        )

        # 用自定义 system prompt 调用（覆盖默认的归类 prompt）
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
            "stream": False,
            "max_tokens": 1024,
        }
        try:
            resp = requests.post(
                self.chat_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 402:
                raise InsufficientBalanceError(
                    f"大模型账户余额不足（HTTP 402），请检查 {self.base_url} 对应账户的余额后重试"
                )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except InsufficientBalanceError:
            raise
        except Exception:
            return ""


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
