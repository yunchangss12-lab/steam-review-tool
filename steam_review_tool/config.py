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


def ensure_config_hint() -> None:
    """提示用户如何配置 API Key（不泄露任何实际密钥）。"""
    if get_api_key():
        return
    print(
        "\n[提示] 未检测到 Steam Web API Key（可选）。\n"
        "  使用更新记录（Patch Notes）比对功能需要它。\n"
        f"  请在程序同目录创建 {CONFIG_FILENAME}，内容形如：\n"
        '  {"steam_web_api_key": "你的KEY"}\n'
        "  申请地址：https://steamcommunity.com/dev/apikey\n"
    )
