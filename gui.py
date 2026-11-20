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

from steam_review_tool.classifier import NewsItem, ReviewItem, classify_all, summarize
from steam_review_tool.config import get_api_key
from steam_review_tool.reporter import write_report
from steam_review_tool.steam_client import SteamClient


# ---------- 抓取任务（后台线程） ----------

def _fetch_task(
    appid: int,
    lang: str,
    date_start: int | None,
    date_end: int | None,
    outdir: str,
    msg_q: queue.Queue,
) -> None:
    """在后台线程里执行完整抓取+比对，进度经 msg_q 回传主线程。"""
    try:
        client = SteamClient(api_key=get_api_key())

        msg_q.put(("log", f"[1/4] 获取游戏信息（AppID {appid}）..."))
        details = client.get_app_details(appid)
        title = details.get("name", f"AppID {appid}") if details else f"AppID {appid}"
        msg_q.put(("log", f"      游戏：{title}"))

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

        msg_q.put(("log", "[4/4] 比对归类..."))
        results = classify_all(reviews, news)
        path = write_report(results, appid, title, outdir, "html")

        counts = summarize(results)
        msg_q.put(("done", {
            "title": title,
            "path": os.path.abspath(path),
            "counts": counts,
            "total": len(results),
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
    tk.Entry(form, textvariable=appid_var, font=label_font, width=20).grid(
        row=0, column=1, sticky="w", pady=6
    )

    _label(form, "评测语言：", 1)
    lang_var = tk.StringVar(value="schinese")
    lang_combo = ttk.Combobox(
        form, textvariable=lang_var, font=label_font, width=17, state="readonly",
        values=["schinese（简体中文）", "english（英语）", "all（全部语言）"],
    )
    lang_combo.current(0)
    lang_combo.grid(row=1, column=1, sticky="w", pady=6)

    _label(form, "时间范围：", 2)
    range_var = tk.StringVar(value="最近30天")
    range_combo = ttk.Combobox(
        form, textvariable=range_var, font=label_font, width=17, state="readonly",
        values=["最近7天", "最近30天", "最近90天", "最近半年", "最近一年", "全部时间", "自定义日期"],
    )
    range_combo.current(1)
    range_combo.grid(row=2, column=1, sticky="w", pady=6)

    tk.Label(
        form,
        text="按时间段筛选差评，可针对性查看某版本更新前后的反馈",
        bg=card, fg=sub, font=("Microsoft YaHei", 8),
    ).grid(row=3, column=1, sticky="w", padx=(0, 16), pady=(0, 8))

    # 自定义日期行（默认隐藏，选「自定义日期」时显示）
    date_frame = tk.Frame(form, bg=card)
    date_frame.grid(row=4, column=0, columnspan=2, sticky="w", padx=(16, 16), pady=(0, 10))
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

    # 结果缓存（供「打开报告」使用）
    result_path: list[str] = []

    def _on_start() -> None:
        raw = appid_var.get().strip()
        if not raw.isdigit():
            status_var.set("⚠ AppID 必须是数字，请检查输入。")
            return
        appid = int(raw)

        lang_map = {
            "schinese（简体中文）": "schinese",
            "english（英语）": "english",
            "all（全部语言）": "all",
        }
        lang = lang_map.get(lang_var.get(), "schinese")

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

        result_path.clear()
        open_btn.configure(state="disabled")
        start_btn.configure(state="disabled")
        status_var.set("正在抓取，请稍候（网络不稳时会自动重试）...")
        progress.start(10)

        msg_q: queue.Queue = queue.Queue()
        t = threading.Thread(
            target=_fetch_task,
            args=(appid, lang, date_start, date_end, "reports", msg_q),
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
                        result_path.append(payload["path"])
                        c = payload["counts"]
                        _log("")
                        _log("=" * 50)
                        _log(f"✓ 完成！游戏：{payload['title']}")
                        _log(f"  已解决 {c['已解决']} 条 · 未解决 {c['未解决']} 条 · 待确认 {c['待确认']} 条 · 合计 {payload['total']} 条")
                        _log(f"  报告文件：{payload['path']}")
                        _log("  点击下方「打开报告」在浏览器查看，按类别筛选后针对性写开发者回复。")
                        status_var.set("完成！可点击「打开报告」查看结果。")
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

    start_btn.configure(command=_on_start)
    open_btn.configure(command=_on_open)

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
