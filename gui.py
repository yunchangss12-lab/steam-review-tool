"""图形界面（GUI）入口。

基于 tkinter 的桌面窗口，用户输入 AppID / 语言 / 页数后点击「开始抓取」，
后台线程执行抓取与比对，进度和结果实时显示在窗口里，避免界面卡死。

本文件放在项目根目录，作为 PyInstaller 的 GUI 入口脚本。
"""

import os
import queue
import sys
import threading
import traceback
import webbrowser

from steam_review_tool.classifier import (
    NewsItem,
    ReviewItem,
    classify_all,
    summarize,
    generate_replies,
)
from steam_review_tool.config import (
    get_api_key,
    get_deepseek_key,
    get_llm_model,
    get_llm_base_url,
    save_llm_config,
)
from steam_review_tool.llm_client import LLMClassifier
from steam_review_tool.reporter import write_report
from steam_review_tool.reply_cases_loader import (
    ReplyCaseSource,
    cases_to_text,
    load_reply_cases,
)
from steam_review_tool.steam_client import SteamClient


# ---------- 抓取任务（后台线程） ----------

def _fetch_task(
    appid: int,
    lang: str,
    date_start: int | None,
    date_end: int | None,
    outdir: str,
    llm_config: dict,
    range_label: str,
    lang_label: str,
    do_classify: bool,
    do_localization: bool,
    msg_q: queue.Queue,
) -> None:
    """在后台线程里执行完整抓取+分析，进度经 msg_q 回传主线程。

    llm_config: {"enabled": bool, "api_key": str, "base_url": str, "model": str}
    range_label: 时间段文案（用于日志与文件名）。
    lang_label: 语种文案（用于文件名）。
    do_classify:    是否启用「语义归类」（差评 ↔ 补丁修复）。
    do_localization: 是否启用「本地化质量判断」（翻译问题，独立流程）。
    """
    try:
        client = SteamClient(api_key=get_api_key())

        msg_q.put(("log", f"[1/4] 获取游戏信息（AppID {appid}）..."))
        details = client.get_app_details(appid)
        if details:
            title = details.get("name", f"AppID {appid}")
            msg_q.put(("log", f"      游戏：{title}"))
        else:
            title = f"AppID {appid}"
            msg_q.put(("log", f"      游戏信息获取失败（网络超时/不可达），将以 AppID 作为标题继续"))

        # 时间范围描述（用于日志）
        import datetime as _dt
        _fmt = lambda ts: _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "不限"
        if date_start is None and date_end is None:
            rng_desc = "全部时间"
        else:
            rng_desc = f"{_fmt(date_start)} ~ {_fmt(date_end)}"

        msg_q.put(("log", f"[2/4] 抓取差评（时间范围：{rng_desc}）..."))
        raw_reviews = client.get_reviews(
            appid,
            language=lang,
            review_type="negative",
            num_per_page=100,
            max_pages=200,
            date_start=date_start,
            date_end=date_end,
        )
        reviews = [
            ReviewItem(
                appid=appid,
                author=r.get("author", {}).get("steamid", "匿名"),
                author_name=r.get("author", {}).get("personaname", ""),
                review_text=r.get("review", ""),
                created_at=int(r.get("timestamp_created", 0)),
                voted_up=bool(r.get("voted_up", False)),
                recommendationid=r.get("recommendationid", ""),
            )
            for r in raw_reviews
        ]
        msg_q.put(("log", f"      共获取 {len(reviews)} 条差评"))

        msg_q.put(("log", "[3/4] 抓取更新记录（Patch Notes）..."))
        raw_news = client.get_news(appid, count=50)
        news = [
            NewsItem(
                title=n.get("title", ""),
                contents=n.get("contents", ""),
                date=int(n.get("date", 0)),
                gid=n.get("gid", ""),
            )
            for n in raw_news
        ]
        msg_q.put(("log", f"      共获取 {len(news)} 条更新记录"))

        msg_q.put(("log", "[4/4] 大模型分析..."))
        llm = None
        if llm_config.get("enabled") and llm_config.get("api_key"):
            llm = LLMClassifier(
                api_key=llm_config["api_key"],
                base_url=llm_config.get("base_url") or "https://api.deepseek.com",
                model=llm_config.get("model") or "deepseek-chat",
            )
            msg_q.put(("log", f"      大模型就绪（{llm.model}）"))
        else:
            msg_q.put(("log", "      ⚠ 未配置大模型（API Key 为空），两个分析功能都无法执行。"))
            msg_q.put(("log", "      （请在下方填写 API Key，并勾选需要的分析功能）"))

        # 分别提示两个独立功能的执行情况
        if do_classify:
            if llm is not None:
                msg_q.put(("log", "      ✓ 语义归类：分析差评问题 ↔ 补丁修复的对应关系"))
            else:
                msg_q.put(("log", "      ✗ 语义归类：未启用（无大模型），差评将标为「待确认」"))
        if do_localization:
            if llm is not None:
                msg_q.put(("log", "      ✓ 本地化质量判断：独立分析差评是否涉及翻译/本地化问题"))
            else:
                msg_q.put(("log", "      ✗ 本地化质量判断：未启用（无大模型），无法识别本地化问题"))
        if not do_classify and not do_localization:
            msg_q.put(("log", "      ⚠ 未选择任何分析功能，差评将全部标为「待确认」。"))

        results = classify_all(
            reviews, news, llm=llm,
            do_classify=do_classify, do_localization=do_localization,
        )

        path = write_report(
            results, appid, title, outdir, "html",
            range_label=range_label, lang_label=lang_label,
        )

        counts = summarize(results)
        msg_q.put(("done", {
            "title": title,
            "path": os.path.abspath(path),
            "counts": counts,
            "total": len(results),
            "results": results,  # 供导出 Excel / 生成回复使用
            "range_label": range_label,
            "lang_label": lang_label,
            "appid": appid,
            "llm_config": llm_config,
        }))
    except Exception as e:  # noqa: BLE001
        msg_q.put(("error", f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"))


# ---------- 生成推荐回复任务（后台线程） ----------

def _generate_reply_task(
    results: list,
    llm_config: dict,
    case_source: ReplyCaseSource,
    msg_q: queue.Queue,
) -> None:
    """后台生成推荐回复文案。

    用 case_source（默认库或用户导入的案例）作为 few-shot 参考。
    结果写回 results 里每个 ClassifiedReview.reply_suggestion。
    """
    try:
        llm = None
        if llm_config.get("enabled") and llm_config.get("api_key"):
            llm = LLMClassifier(
                api_key=llm_config["api_key"],
                base_url=llm_config.get("base_url") or "https://api.deepseek.com",
                model=llm_config.get("model") or "deepseek-chat",
            )
        if llm is None or not llm.available:
            msg_q.put(("error", "未启用大模型或缺少 API Key，无法生成推荐回复。"))
            return

        # 组装案例文本
        cases_text = ""
        if case_source is not None and not case_source.is_empty:
            if case_source.is_structured:
                cases_text = cases_to_text(case_source.cases)
            else:
                cases_text = case_source.raw_text
            msg_q.put(("log", f"      使用自定义回复案例库：{case_source.source_name}"))
        else:
            msg_q.put(("log", "      使用内置默认回复案例库"))

        reply_count = generate_replies(results, llm, cases_text=cases_text)
        msg_q.put(("done_reply", {
            "count": reply_count,
            "results": results,
        }))
    except Exception as e:  # noqa: BLE001
        msg_q.put(("error", f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"))


# ---------- 时间范围解析 ----------

def _resolve_date_range(
    range_choice: str, start_str: str, end_str: str
) -> tuple[int | None, int | None]:
    """把「时间段选择 + 自定义日期」解析成 (date_start, date_end) Unix 时间戳。

    返回 (None, None) 表示「全部时间」（不加时间过滤）。
    """
    import datetime as _dt
    import time as _time

    now = int(_time.time())
    day = 86400

    preset_days = {
        "最近7天": 7,
        "最近30天": 30,
        "最近90天": 90,
        "最近半年": 182,
        "最近一年": 365,
    }

    if range_choice in preset_days:
        return now - preset_days[range_choice] * day, now

    if range_choice == "全部时间":
        return None, None

    # 自定义日期
    def _parse(s: str) -> int | None:
        s = s.strip()
        if not s:
            return None
        try:
            return int(_dt.datetime.strptime(s, "%Y-%m-%d").timestamp())
        except ValueError:
            return None

    ds = _parse(start_str)
    de = _parse(end_str)
    # 结束日期若填了，取当天末尾（23:59:59）
    if de is not None:
        de = de + day - 1
    return ds, de


# ---------- 语言下拉框辅助 ----------

def _lang_display_to_code(display: str) -> str:
    """把语言下拉框的显示名解析成评测接口的 language 代码。

    显示名有两种格式：
      - 「中文名（代码）」如「简体中文（schinese）」
      - 「中文名·代码」如「西班牙语（拉丁美洲）·latam」（名称本身带括号时用 · 分隔）
    「全部语言」特殊处理返回 "all"（文案形如「全部语言（all·不区分语种）」）。
    """
    if display == "全部语言" or "（all" in display:
        return "all"
    # 优先按 · 分隔（名称带括号的情形）
    if "·" in display:
        return display.rsplit("·", 1)[1].strip()
    # 再按全角括号提取
    if "（" in display and display.endswith("）"):
        return display[display.rindex("（") + 1 : -1]
    # 兜底：无法解析时按常见默认值
    return "schinese"


def _lang_display_to_label(display: str) -> str:
    """从语言下拉框的显示名提取「语种中文名」，用于文件名。

    显示名格式如「简体中文（schinese）」「西班牙语（拉丁美洲）·latam」「全部语言（all·不区分语种）」，
    这里取括号/分隔符前面的中文名部分。
    """
    if not display:
        return ""
    # 全部语言 → 标签「全部语言」
    if display == "全部语言" or "（all" in display:
        return "全部语言"
    # 按 · 分隔（名称本身带括号的情形，如「西班牙语（拉丁美洲）·latam」）
    if "·" in display:
        return display.rsplit("·", 1)[0].strip()
    # 按全角括号分隔（如「简体中文（schinese）」）
    if "（" in display:
        return display[: display.index("（")].strip()
    return display.strip()


def _build_lang_values(langs: list[tuple[str, str]]) -> list[str]:
    """把 [(显示名, 代码)] 拼成下拉框 values 列表，末尾固定带「全部语言」。

    若 langs 为空，返回默认三选项。

    显示名若本身已带括号（如「西班牙语（拉丁美洲）」），
    则直接拼接代码，避免出现双重括号的冗余观感。

    「全部语言」用特殊文案强调其「无差别抓取所有语言差评」的语义。
    """
    values = []
    for name, code in langs:
        if "（" in name:
            values.append(f"{name}·{code}")
        else:
            values.append(f"{name}（{code}）")
    values.append("全部语言（all·不区分语种）")
    return values


# ---------- 主窗口 ----------

def build_app() -> "tk.Tk":
    import tkinter as tk
    from tkinter import scrolledtext, ttk

    root = tk.Tk()
    root.title("Steam 差评 × 更新记录 比对归类工具")
    root.geometry("720x620")
    root.minsize(640, 520)

    # 主题配色（浅色）
    bg = "#f5f6f7"
    card = "#ffffff"
    accent = "#1e9b50"  # Steam 绿
    fg = "#1a1a1a"
    sub = "#666666"
    root.configure(bg=bg)

    # 字体
    title_font = ("Microsoft YaHei", 16, "bold")
    label_font = ("Microsoft YaHei", 10)
    btn_font = ("Microsoft YaHei", 11, "bold")
    mono_font = ("Consolas", 10)

    # 顶部标题
    header = tk.Frame(root, bg=accent)
    header.pack(fill="x")
    tk.Label(
        header, text="Steam 差评 × 更新记录 比对归类工具",
        bg=accent, fg="white", font=title_font, padx=16, pady=14,
    ).pack(anchor="w")

    # 输入卡片
    form = tk.Frame(root, bg=card)
    form.pack(fill="x", padx=16, pady=14)
    form.columnconfigure(1, weight=1)

    def _label(parent, text, row):
        tk.Label(parent, text=text, bg=card, fg=fg, font=label_font).grid(
            row=row, column=0, sticky="w", padx=(16, 8), pady=6
        )

    _label(form, "游戏 AppID：", 0)
    appid_var = tk.StringVar(value="2445690")
    appid_entry = tk.Entry(form, textvariable=appid_var, font=label_font, width=20)
    appid_entry.grid(row=0, column=1, sticky="w", pady=6)

    _label(form, "评测语言：", 1)
    lang_var = tk.StringVar(value="简体中文（schinese）")
    lang_combo = ttk.Combobox(
        form, textvariable=lang_var, font=label_font, width=26, state="readonly",
        values=["简体中文（schinese）", "英语（english）", "全部语言（all·不区分语种）"],
    )
    lang_combo.current(0)
    lang_combo.grid(row=1, column=1, sticky="w", pady=6)

    # 醒目提示：语言列表会按 AppID 自动刷新（带图标 + 强调色）
    lang_hint_var = tk.StringVar(value="💡 输入 AppID 后按回车（或点击下方按钮）自动加载该游戏支持的语种")
    lang_hint = tk.Label(
        form, textvariable=lang_hint_var,
        bg=card, fg="#b26a00", font=("Microsoft YaHei", 8, "bold"),
    )
    lang_hint.grid(row=2, column=1, columnspan=2, sticky="w", padx=(0, 8), pady=(0, 2))

    # 「刷新语种」按钮：给用户明确的主动触发入口
    refresh_lang_btn = tk.Button(
        form, text="↻ 刷新语种", bg="#e8f0ea", fg="#1e9b50",
        font=("Microsoft YaHei", 9, "bold"), relief="flat", cursor="hand2",
        padx=10, pady=2,
    )
    refresh_lang_btn.grid(row=1, column=2, sticky="w", padx=8, pady=6)

    _label(form, "时间范围：", 3)
    range_var = tk.StringVar(value="最近30天")
    range_combo = ttk.Combobox(
        form, textvariable=range_var, font=label_font, width=17, state="readonly",
        values=["最近7天", "最近30天", "最近90天", "最近半年", "最近一年", "全部时间", "自定义日期"],
    )
    range_combo.current(1)
    range_combo.grid(row=3, column=1, sticky="w", pady=6)

    tk.Label(
        form,
        text="按时间段筛选差评，可针对性查看某版本更新前后的反馈",
        bg=card, fg=sub, font=("Microsoft YaHei", 8),
    ).grid(row=4, column=1, sticky="w", padx=(0, 16), pady=(0, 8))

    # 自定义日期行（默认隐藏，选「自定义日期」时显示）
    date_frame = tk.Frame(form, bg=card)
    date_frame.grid(row=5, column=0, columnspan=2, sticky="w", padx=(16, 16), pady=(0, 10))
    tk.Label(date_frame, text="起：", bg=card, fg=fg, font=label_font).pack(side="left")
    date_start_var = tk.StringVar(value="")
    date_start_entry = tk.Entry(date_frame, textvariable=date_start_var, font=label_font, width=12)
    date_start_entry.pack(side="left", padx=(0, 12))
    tk.Label(date_frame, text="止：", bg=card, fg=fg, font=label_font).pack(side="left")
    date_end_var = tk.StringVar(value="")
    date_end_entry = tk.Entry(date_frame, textvariable=date_end_var, font=label_font, width=12)
    date_end_entry.pack(side="left")
    tk.Label(
        date_frame, text="（格式 YYYY-MM-DD，可留空=不限）",
        bg=card, fg=sub, font=("Microsoft YaHei", 8),
    ).pack(side="left", padx=10)
    date_frame.grid_remove()  # 默认隐藏

    def _on_range_change(_event=None):
        if range_var.get() == "自定义日期":
            date_frame.grid()
        else:
            date_frame.grid_remove()

    range_combo.bind("<<ComboboxSelected>>", _on_range_change)

    # ---------- 大模型（可选）配置区 ----------
    # 大模型配置（API Key / Base URL / 模型名）是「基础设施」，两个分析功能共用。
    # 下面两个勾选框是「独立功能开关」，可自由组合：
    #   ① 语义归类（差评↔补丁修复）  ② 本地化质量判断（翻译问题）
    llm_card = tk.Frame(root, bg=card)
    llm_card.pack(fill="x", padx=16, pady=(0, 8))

    llm_header = tk.Frame(llm_card, bg=card)
    llm_header.pack(fill="x", padx=16, pady=(10, 4))

    # 功能开关①：语义归类（差评 ↔ 补丁修复）
    do_classify_var = tk.BooleanVar(value=bool(get_deepseek_key()))
    classify_check = tk.Checkbutton(
        llm_header, text="启用语义归类（分析差评问题 ↔ 补丁修复的对应关系）",
        variable=do_classify_var, bg=card, fg=fg, font=label_font,
        activebackground=card, anchor="w", cursor="hand2",
    )
    classify_check.pack(side="left")

    llm_fields = tk.Frame(llm_card, bg=card)
    llm_fields.pack(fill="x", padx=16, pady=(0, 10))

    def _llm_label(parent, text, row):
        tk.Label(parent, text=text, bg=card, fg=sub, font=("Microsoft YaHei", 9)).grid(
            row=row, column=0, sticky="w", pady=3
        )

    _llm_label(llm_fields, "Base URL：", 0)
    llm_base_url_var = tk.StringVar(value=get_llm_base_url())
    tk.Entry(llm_fields, textvariable=llm_base_url_var, font=("Consolas", 9), width=46).grid(
        row=0, column=1, sticky="w", pady=3
    )

    _llm_label(llm_fields, "API Key：", 1)
    llm_api_key_var = tk.StringVar(value=get_deepseek_key())
    tk.Entry(llm_fields, textvariable=llm_api_key_var, font=("Consolas", 9), width=46, show="").grid(
        row=1, column=1, sticky="w", pady=3
    )

    _llm_label(llm_fields, "模型名：", 2)
    llm_model_var = tk.StringVar(value=get_llm_model())
    tk.Entry(llm_fields, textvariable=llm_model_var, font=("Consolas", 9), width=46).grid(
        row=2, column=1, sticky="w", pady=3
    )

    tk.Label(
        llm_fields,
        text="支持 OpenAI 兼容接口（DeepSeek/Kimi/通义/Ollama 等）；留空 API Key 则两个分析功能都不可用。填写会自动保存。",
        bg=card, fg=sub, font=("Microsoft YaHei", 8),
    ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))

    # 功能开关②：本地化质量判断（独立于语义归类，可单独开启）
    loc_card = tk.Frame(root, bg=card)
    loc_card.pack(fill="x", padx=16, pady=(0, 8))
    do_localization_var = tk.BooleanVar(value=False)

    loc_check = tk.Checkbutton(
        loc_card,
        text="启用本地化质量判断（独立分析差评是否与翻译/本地化质量相关，归入「本地化质量反馈」分类）",
        variable=do_localization_var, bg=card, fg=fg, font=label_font,
        activebackground=card, anchor="w", cursor="hand2",
    )
    loc_check.pack(fill="x", padx=16, pady=10)

    # 按钮区
    btn_row = tk.Frame(root, bg=bg)
    btn_row.pack(fill="x", padx=16, pady=(0, 8))
    start_btn = tk.Button(
        btn_row, text="开始抓取", bg=accent, fg="white",
        font=btn_font, padx=28, pady=6, relief="flat", cursor="hand2",
    )
    start_btn.pack(side="left")
    open_btn = tk.Button(
        btn_row, text="打开报告", bg="#ffffff", fg=fg,
        font=btn_font, padx=20, pady=6, relief="flat", cursor="hand2",
        state="disabled",
    )
    open_btn.pack(side="left", padx=10)
    export_btn = tk.Button(
        btn_row, text="导出 Excel", bg="#ffffff", fg=fg,
        font=btn_font, padx=20, pady=6, relief="flat", cursor="hand2",
        state="disabled",
    )
    export_btn.pack(side="left")

    # 状态标签
    status_var = tk.StringVar(value="就绪。请填写 AppID 后点击「开始抓取」。")
    tk.Label(root, textvariable=status_var, bg=bg, fg=sub, font=label_font, anchor="w").pack(
        fill="x", padx=18
    )

    # 进度条
    progress = ttk.Progressbar(root, mode="indeterminate")
    progress.pack(fill="x", padx=18, pady=(6, 4))

    # 日志输出区
    log_frame = tk.Frame(root, bg=card)
    log_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))
    log_text = scrolledtext.ScrolledText(
        log_frame, font=mono_font, bg="#fbfcfd", fg="#222222",
        wrap="word", state="disabled", padx=10, pady=10, borderwidth=0,
    )
    log_text.pack(fill="both", expand=True)

    def _log(msg: str) -> None:
        log_text.configure(state="normal")
        log_text.insert("end", msg + "\n")
        log_text.see("end")
        log_text.configure(state="disabled")

    # 结果缓存（供「打开报告」「导出 Excel」使用）
    result_path: list[str] = []
    result_data: list = []  # 保存 (title, range_label, results) 供导出

    # 记录上次已加载语言列表的 AppID，用于去重
    _loaded_lang_appid: list[int] = [-1]

    def _refresh_languages() -> None:
        """按当前 AppID 拉取游戏支持语种，动态填充语言下拉框。

        成功：下拉框改为「游戏支持语种 + 全部语言」，并尽量保留当前选择；
        失败：回退为默认三选项，并提示。

        通过「上次已加载的 AppID」做去重：AppID 未变时跳过请求，
        避免每次失焦都重复拉取。
        """
        raw = appid_var.get().strip()
        if not raw.isdigit():
            lang_hint_var.set("⚠ 请先填写有效的 AppID（纯数字），再刷新语种")
            return
        appid = int(raw)
        # 去重：同一 AppID 已加载过则跳过
        if appid == _loaded_lang_appid[0]:
            lang_hint_var.set("✓ 已加载该游戏支持的语种，可在下拉框选择")
            return

        lang_hint_var.set("⏳ 正在获取该游戏支持的语种...")
        status_var.set("正在获取游戏支持的语种...")
        try:
            client = SteamClient(api_key=get_api_key())
            langs = client.get_supported_languages(appid)
        except Exception as e:  # noqa: BLE001
            langs = []
            _log(f"\n[提示] 获取游戏支持语种失败：{e}")

        _loaded_lang_appid[0] = appid
        if langs:
            lang_combo["values"] = _build_lang_values(langs)
            # 尽量保留当前选择；若不在新列表里则切到第一项
            cur = lang_var.get()
            if cur not in lang_combo["values"]:
                lang_combo.current(0)
            _log(f"\n✓ 已获取 {len(langs)} 种游戏支持语种，请在下拉框选择。")
            lang_hint_var.set(f"✓ 已加载 {len(langs)} 种语种；如需抓取所有语言的差评，请选末尾「全部语言」")
            status_var.set(f"已加载游戏支持语种（{len(langs)} 种）。")
        else:
            lang_combo["values"] = ["简体中文（schinese）", "英语（english）", "全部语言（all·不区分语种）"]
            lang_combo.current(0)
            _log("\n[提示] 未能解析游戏支持语种，保留默认语言选项。")
            lang_hint_var.set("⚠ 未能解析语种，保留默认选项（含「全部语言」可抓取所有语言）")
            status_var.set("未能获取游戏支持语种，保留默认选项。")

    # 绑定「刷新语种」按钮（明确的主动触发入口）
    refresh_lang_btn.configure(command=_refresh_languages)
    # AppID 输入框失焦时自动刷新语言列表
    appid_entry.bind("<FocusOut>", lambda _e: _refresh_languages())
    # 回车也触发（用户输入 AppID 后直接按回车）
    appid_entry.bind("<Return>", lambda _e: _refresh_languages())

    def _on_start() -> None:
        raw = appid_var.get().strip()
        if not raw.isdigit():
            status_var.set("⚠ AppID 必须是数字，请检查输入。")
            return
        appid = int(raw)

        # 语言下拉框的显示名 → 语言代码。显示名含代码（如「简体中文（schinese）」），
        # 这里只取括号里的代码作为接口参数；「全部语言」特殊处理为 "all"。
        lang = _lang_display_to_code(lang_var.get())
        # 语种中文名（用于文件名）
        lang_label = _lang_display_to_label(lang_var.get())

        # 两个独立功能开关：语义归类 + 本地化质量判断（可自由组合）
        do_classify = bool(do_classify_var.get())
        do_localization = bool(do_localization_var.get())

        # 解析时间范围
        if range_var.get() == "自定义日期":
            # 校验日期格式（若填写则必须是 YYYY-MM-DD）
            import datetime as _dt
            for _label_txt, _val in (("起", date_start_var.get()), ("止", date_end_var.get())):
                _v = _val.strip()
                if _v:
                    try:
                        _dt.datetime.strptime(_v, "%Y-%m-%d")
                    except ValueError:
                        status_var.set(f"⚠ 日期格式错误：{_label_txt}日期「{_v}」应为 YYYY-MM-DD。")
                        return
        date_start, date_end = _resolve_date_range(range_var.get(), date_start_var.get(), date_end_var.get())

        # 生成时间段文案（用于文件名）
        range_choice = range_var.get()
        if range_choice == "自定义日期":
            ds = date_start_var.get().strip() or "不限"
            de = date_end_var.get().strip() or "不限"
            range_label = f"{ds}至{de}"
        else:
            range_label = range_choice

        # 收集大模型配置。只要两个功能开关中任意一个开启，就需要大模型。
        # （语义归类和本地化判断共用同一套 API Key / Base URL / 模型）
        llm_needed = do_classify or do_localization
        llm_config = {
            "enabled": llm_needed,
            "api_key": llm_api_key_var.get().strip() if llm_needed else "",
            "base_url": llm_base_url_var.get().strip(),
            "model": llm_model_var.get().strip(),
        }
        # 若需要大模型且有 Key，把配置保存到 config.local.json（下次启动自动回填）
        if llm_needed and llm_config["api_key"]:
            save_llm_config(
                llm_config["api_key"],
                base_url=llm_config["base_url"],
                model=llm_config["model"],
            )

        result_path.clear()
        result_data.clear()
        open_btn.configure(state="disabled")
        export_btn.configure(state="disabled")
        start_btn.configure(state="disabled")
        status_var.set("正在抓取，请稍候（网络不稳时会自动重试）...")
        progress.start(10)

        msg_q: queue.Queue = queue.Queue()
        t = threading.Thread(
            target=_fetch_task,
            args=(appid, lang, date_start, date_end, "reports", llm_config, range_label, lang_label, do_classify, do_localization, msg_q),
            daemon=True,
        )
        t.start()

        def _poll() -> None:
            try:
                while True:
                    kind, payload = msg_q.get_nowait()
                    if kind == "log":
                        _log(payload)
                    elif kind == "done":
                        progress.stop()
                        start_btn.configure(state="normal")
                        open_btn.configure(state="normal")
                        export_btn.configure(state="normal")
                        result_path.append(payload["path"])
                        result_data.append({
                            "title": payload["title"],
                            "range_label": payload.get("range_label", ""),
                            "lang_label": payload.get("lang_label", ""),
                            "results": payload["results"],
                            "appid": payload.get("appid"),
                            "llm_config": payload.get("llm_config", {}),
                        })
                        c = payload["counts"]
                        _log("")
                        _log("=" * 50)
                        _log(f"✓ 完成！游戏：{payload['title']}")
                        _log(f"  已解决 {c['已解决']} 条 · 未解决 {c['未解决']} 条 · 情绪化评论 {c.get('情绪化评论', 0)} 条 · 本地化质量反馈 {c.get('本地化质量反馈', 0)} 条 · 待确认 {c['待确认']} 条 · 合计 {payload['total']} 条")
                        _log(f"  报告文件：{payload['path']}")
                        _log("  点击「打开报告」在浏览器查看，或「导出 Excel」保存为表格。")
                        status_var.set("完成！可点击「打开报告」或「导出 Excel」。")
                        # 抓取+分析完成后，弹出对话框询问是否生成推荐回复
                        _ask_generate_reply(payload)
                        return
                    elif kind == "error":
                        progress.stop()
                        start_btn.configure(state="normal")
                        status_var.set("出错，详情见下方日志。")
                        _log("\n[出错] 抓取失败：\n" + payload)
                        return
            except queue.Empty:
                pass
            root.after(200, _poll)

        root.after(200, _poll)

    def _on_open() -> None:
        if result_path:
            webbrowser.open("file://" + result_path[0])

    def _on_export() -> None:
        if not result_data:
            status_var.set("⚠ 暂无结果，请先「开始抓取」。")
            return
        from steam_review_tool.excel_exporter import export_excel
        d = result_data[-1]
        try:
            path = export_excel(
                d["results"], d["title"], d["range_label"],
                lang_label=d.get("lang_label", ""), output_dir="reports",
            )
            status_var.set(f"已导出 Excel：{path}")
            _log(f"\n✓ 已导出 Excel：{path}")
        except Exception as e:  # noqa: BLE001
            status_var.set(f"⚠ 导出失败：{e}")
            _log(f"\n[出错] 导出 Excel 失败：{e}")

    # ---------- 生成推荐回复 & 编辑 ----------

    def _refresh_report() -> None:
        """根据最新的 results 重新生成 HTML 报告并更新缓存。"""
        if not result_data:
            return
        d = result_data[-1]
        try:
            path = write_report(
                d["results"], d["appid"], d["title"], "reports", "html",
                range_label=d["range_label"], lang_label=d.get("lang_label", ""),
            )
            result_path.clear()
            result_path.append(os.path.abspath(path))
            _log(f"\n✓ 报告已更新：{path}")
            return path
        except Exception as e:  # noqa: BLE001
            _log(f"\n[出错] 刷新报告失败：{e}")
            return None

    def _ask_generate_reply(payload: dict) -> None:
        """抓取+分析完成后，弹出对话框询问是否生成推荐回复。"""
        import tkinter as tk
        from tkinter import filedialog, messagebox

        results = payload["results"]
        # 判断是否有可生成回复的条目（非「未解决」）
        genable = [r for r in results if r.category != "未解决"]
        if not genable:
            _log("  （无「已解决/情绪化评论/本地化质量反馈/待确认」的差评，跳过生成推荐回复）")
            return

        # 记录是否已启用大模型
        llm_config = payload.get("llm_config", {})
        has_llm = bool(llm_config.get("enabled") and llm_config.get("api_key"))

        dlg = tk.Toplevel(root)
        dlg.title("生成推荐回复")
        dlg.geometry("520x300")
        dlg.resizable(False, False)
        dlg.configure(bg=card)
        dlg.transient(root)
        dlg.grab_set()

        tk.Label(
            dlg,
            text=f"抓取与分析已完成，共 {len(genable)} 条差评可生成推荐回复文案。",
            bg=card, fg=fg, font=label_font, justify="left", wraplength=480,
        ).pack(anchor="w", padx=20, pady=(20, 4))

        if not has_llm:
            tk.Label(
                dlg,
                text="⚠ 当前未启用大模型，将无法生成回复。\n请先勾选界面「大模型(可选)」并填写 API Key。",
                bg=card, fg="#c0392b", font=label_font, justify="left", wraplength=480,
            ).pack(anchor="w", padx=20, pady=(4, 0))

        tk.Label(
            dlg,
            text="回复案例库：",
            bg=card, fg=fg, font=label_font,
        ).pack(anchor="w", padx=20, pady=(16, 4))

        case_var = tk.StringVar(value="默认")
        opt_frame = tk.Frame(dlg, bg=card)
        opt_frame.pack(anchor="w", padx=20)
        tk.Radiobutton(
            opt_frame, text="使用内置默认案例库", variable=case_var, value="默认",
            bg=card, fg=fg, font=label_font, activebackground=card, anchor="w",
        ).pack(anchor="w")
        tk.Radiobutton(
            opt_frame, text="导入自定义回复案例文件", variable=case_var, value="导入",
            bg=card, fg=fg, font=label_font, activebackground=card, anchor="w",
        ).pack(anchor="w")

        imported_file: list[str] = []

        def _pick_file():
            p = filedialog.askopenfilename(
                title="选择回复案例文件",
                filetypes=[
                    ("所有支持格式", "*.xlsx *.xls *.csv *.md *.txt *.docx"),
                    ("Excel", "*.xlsx *.xls"),
                    ("CSV", "*.csv"),
                    ("Markdown", "*.md"),
                    ("文本", "*.txt"),
                    ("Word", "*.docx"),
                    ("所有文件", "*.*"),
                ],
            )
            if p:
                imported_file.clear()
                imported_file.append(p)
                import_btn.configure(text=os.path.basename(p))

        import_btn = tk.Button(
            opt_frame, text="选择文件…", bg="#e8f0ea", fg=fg, font=("Microsoft YaHei", 9),
            relief="flat", cursor="hand2", command=_pick_file,
        )
        import_btn.pack(anchor="w", padx=(0, 0), pady=(4, 0))

        # 按钮
        btn_frame = tk.Frame(dlg, bg=card)
        btn_frame.pack(side="bottom", fill="x", padx=20, pady=16)

        def _do_generate():
            # 确定案例来源
            case_source: ReplyCaseSource | None = None
            if case_var.get() == "导入":
                if not imported_file:
                    messagebox.showwarning("提示", "请先选择一个回复案例文件。", parent=dlg)
                    return
                case_source = load_reply_cases(imported_file[0])
                if case_source.is_empty:
                    messagebox.showerror(
                        "解析失败",
                        "无法从该文件解析出回复案例（可能是空文件或格式不支持）。\n请改用 xlsx/csv/md/txt/docx 格式。",
                        parent=dlg,
                    )
                    return
            dlg.destroy()

            status_var.set("正在生成推荐回复，请稍候...")
            progress.start(10)
            _log("\n正在生成推荐回复文案...")

            msg_q2: queue.Queue = queue.Queue()
            t2 = threading.Thread(
                target=_generate_reply_task,
                args=(results, payload.get("llm_config", {}), case_source, msg_q2),
                daemon=True,
            )
            t2.start()

            def _poll_reply():
                try:
                    while True:
                        kind2, pl2 = msg_q2.get_nowait()
                        if kind2 == "log":
                            _log(pl2)
                        elif kind2 == "done_reply":
                            progress.stop()
                            _log(f"✓ 已生成 {pl2['count']} 条推荐回复")
                            status_var.set("推荐回复已生成，可打开报告查看或导出 Excel。")
                            _refresh_report()
                            _edit_replies_window()
                            return
                        elif kind2 == "error":
                            progress.stop()
                            status_var.set("生成回复出错，详情见日志。")
                            _log("\n[出错] 生成推荐回复失败：\n" + pl2)
                            return
                except queue.Empty:
                    pass
                root.after(200, _poll_reply)

            root.after(200, _poll_reply)

        def _skip():
            dlg.destroy()
            status_var.set("已跳过生成推荐回复。")

        tk.Button(
            btn_frame, text="暂不生成", bg="#ffffff", fg=fg, font=label_font,
            padx=16, pady=6, relief="flat", cursor="hand2", command=_skip,
        ).pack(side="right", padx=6)
        tk.Button(
            btn_frame, text="生成推荐回复", bg=accent, fg="white", font=btn_font,
            padx=20, pady=6, relief="flat", cursor="hand2", command=_do_generate,
        ).pack(side="right")

    def _edit_replies_window() -> None:
        """弹出可编辑的回复窗口，让用户逐条修改推荐回复文案。"""
        import tkinter as tk

        if not result_data:
            return
        d = result_data[-1]
        results = d["results"]
        # 只展示有回复文案的条目（未解决无回复，不展示）
        edit_items = [r for r in results if r.reply_suggestion]
        if not edit_items:
            _log("  （没有已生成的回复文案，无需编辑）")
            return

        win = tk.Toplevel(root)
        win.title("编辑推荐回复文案")
        win.geometry("760x640")
        win.configure(bg=bg)
        win.transient(root)

        tk.Label(
            win, text="可直接修改每条回复文案，点击「保存并更新报告」后生效。",
            bg=bg, fg=sub, font=label_font,
        ).pack(anchor="w", padx=16, pady=(12, 4))

        # 可滚动容器
        canvas = tk.Canvas(win, bg=bg, highlightthickness=0)
        scrollbar = tk.Scrollbar(win, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True, padx=(16, 0), pady=(0, 8))

        inner = tk.Frame(canvas, bg=bg)
        canvas.create_window((0, 0), window=inner, anchor="nw")

        # 保存每个条目对应的编辑文本框
        entry_vars: list[tk.Text] = []

        for r in edit_items:
            card_f = tk.Frame(inner, bg=card)
            card_f.pack(fill="x", padx=(0, 12), pady=6)

            head = tk.Frame(card_f, bg=card)
            head.pack(fill="x", padx=12, pady=(10, 2))
            # 差评摘要 + 类别标签
            summary = r.review.review_text.strip().replace("\n", " ")[:60]
            tag_color = {"已解决": "#1e9b50", "未解决": "#c0392b", "情绪化评论": "#7b1fa2", "本地化质量反馈": "#1565c0", "待确认": "#e67e22"}.get(r.category, "#666")
            tk.Label(
                head, text=f"[{r.category}] {summary}", bg=card, fg=fg,
                font=("Microsoft YaHei", 9, "bold"), anchor="w",
            ).pack(side="left")
            tk.Label(
                head, text=r.category, bg=tag_color, fg="white",
                font=("Microsoft YaHei", 8), padx=6, pady=1,
            ).pack(side="right")

            txt = tk.Text(card_f, font=("Microsoft YaHei", 10), wrap="word",
                          height=4, padx=10, pady=8, relief="solid", borderwidth=1)
            txt.insert("1.0", r.reply_suggestion)
            txt.pack(fill="x", padx=12, pady=(0, 10))
            entry_vars.append((r, txt))

        def _inner_configure(e=None):
            inner.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))

        inner.bind("<Configure>", _inner_configure)

        # 底部按钮
        bottom = tk.Frame(win, bg=bg)
        bottom.pack(fill="x", padx=16, pady=12)

        def _save():
            for r, txt in entry_vars:
                r.reply_suggestion = txt.get("1.0", "end").strip()
            _refresh_report()
            _log("✓ 回复文案已更新并保存到报告。")
            status_var.set("回复文案已更新。")
            win.destroy()

        tk.Button(
            bottom, text="保存并更新报告", bg=accent, fg="white", font=btn_font,
            padx=20, pady=6, relief="flat", cursor="hand2", command=_save,
        ).pack(side="right")
        tk.Button(
            bottom, text="取消", bg="#ffffff", fg=fg, font=label_font,
            padx=16, pady=6, relief="flat", cursor="hand2", command=win.destroy,
        ).pack(side="right", padx=6)

    start_btn.configure(command=_on_start)
    open_btn.configure(command=_on_open)
    export_btn.configure(command=_on_export)

    return root


def main() -> int:
    try:
        root = build_app()
        root.mainloop()
        return 0
    except Exception:
        traceback.print_exc()
        try:
            input("按回车键退出...")
        except (EOFError, KeyboardInterrupt):
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
