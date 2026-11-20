"""配置加载：负责读取 API Key 等本地敏感配置。

- 配置文件 `config.local.json` 已被 .gitignore 排除，严禁入库。
- 若文件不存在，返回空配置，程序仍可运行（仅使用无需 Key 的 Storefront API）。
"""

import json
import os

CONFIG_FILENAME = "config.local.json"


def _config_path() -> str:
    # 优先取程序所在目录，兼容 PyInstaller 打包后的运行场景
    base = getattr(__import__("sys"), "frozen", False)
    if base:
        root = os.path.dirname(__import__("sys").executable)
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, CONFIG_FILENAME)


def load_config() -> dict:
    path = _config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def get_api_key() -> str:
    return load_config().get("steam_web_api_key", "").strip()


def get_deepseek_key() -> str:
    """大模型 API Key（用于差评 × 更新记录的语义比对归类）。"""
    return load_config().get("deepseek_api_key", "").strip()


def get_llm_model() -> str:
    """语义比对所用的大模型名，默认 deepseek-chat（可自行改为 deepseek-v4-pro 等）。"""
    return load_config().get("llm_model", "deepseek-chat").strip() or "deepseek-chat"


def get_llm_base_url() -> str:
    """大模型接口 base_url（OpenAI 兼容），默认 DeepSeek。"""
    return load_config().get("llm_base_url", "https://api.deepseek.com").strip() or "https://api.deepseek.com"


def save_llm_config(api_key: str, base_url: str = "", model: str = "") -> bool:
    """把大模型配置写回 config.local.json，供下次启动复用。

    返回是否写入成功。仅在配置文件已存在或可创建时写入，失败静默返回 False。
    """
    path = _config_path()
    try:
        cfg = load_config()
        if api_key:
            cfg["deepseek_api_key"] = api_key
        if base_url:
            cfg["llm_base_url"] = base_url
        if model:
            cfg["llm_model"] = model
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def ensure_config_hint() -> None:
    """提示用户如何配置 API Key（不泄露任何实际密钥）。"""
    missing = []
    if not get_api_key():
        missing.append("Steam Web API Key（抓取更新记录用，可选）")
    if not get_deepseek_key():
        missing.append("DeepSeek API Key（差评语义比对归类用，推荐）")
    if not missing:
        return
    hint = "\n[提示] 未检测到以下 Key：\n"
    for m in missing:
        hint += f"  · {m}\n"
    hint += (
        f"  请在程序同目录创建/编辑 {CONFIG_FILENAME}，内容形如：\n"
        '  {"steam_web_api_key": "你的SteamKey", "deepseek_api_key": "你的DeepSeekKey"}\n'
        "  · Steam Key 申请：https://steamcommunity.com/dev/apikey\n"
        "  · DeepSeek Key 申请：https://platform.deepseek.com\n"
    )
    print(hint)
