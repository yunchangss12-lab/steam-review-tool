"""Excel 导出模块。

把归类结果导出为 .xlsx 表格，按类别分 sheet（已解决 / 未解决 / 待确认），
每个 sheet 列出该类别下每条差评的详细信息，便于在 Excel 里筛选、排序、
以及针对性整理开发者回复。

依赖 openpyxl（已在打包环境中可用）。
"""

import datetime as dt
import os

from .classifier import ClassifiedReview


def _fmt_time(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _safe_filename(s: str, maxlen: int = 40) -> str:
    if not s:
        return ""
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, "_")
    s = s.strip().strip(".").strip()
    return " ".join(s.split())[:maxlen]


# 表头
HEADERS = ["序号", "差评日期", "玩家", "差评内容", "归类", "解决依据 / 判定理由", "推荐回复文案", "评测链接"]


def _review_url(appid: int, author_steamid: str) -> str:
    if author_steamid:
        return f"https://steamcommunity.com/profiles/{author_steamid}/recommended/{appid}/"
    return f"https://steamcommunity.com/app/{appid}/reviews/"


def export_excel(
    results: list[ClassifiedReview],
    title: str,
    range_label: str = "",
    lang_label: str = "",
    output_dir: str = "reports",
) -> str:
    """把归类结果导出为 Excel，按类别分 sheet。返回导出文件路径。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    os.makedirs(output_dir, exist_ok=True)

    wb = Workbook()
    # 移除默认 sheet，改为按类别创建
    wb.remove(wb.active)

    # 样式
    header_fill = PatternFill(start_color="1e9b50", end_color="1e9b50", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    wrap = Alignment(vertical="top", wrap_text=True)

    # 类别 → 填充色（sheet 页签可用色略深）
    cat_color = {
        "已解决": "2e7d32",  # 绿
        "未解决": "d32f2f",  # 红
        "情绪化评论": "7b1fa2",  # 紫
        "本地化质量反馈": "1565c0",  # 蓝
        "待确认": "f57f17",  # 橙
    }

    # 每个类别的行数据
    categories = ["已解决", "未解决", "情绪化评论", "本地化质量反馈", "待确认"]
    for cat in categories:
        group = [r for r in results if r.category == cat]
        ws = wb.create_sheet(title=cat)
        ws.sheet_properties.tabColor = cat_color[cat]

        # 写表头
        for col, h in enumerate(HEADERS, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # 写数据
        for i, r in enumerate(group, 1):
            rev = r.review
            # 解决依据 / 判定理由
            if r.category == "已解决" and r.matches:
                reasons = []
                for m in r.matches:
                    nt = m.news.title.strip()
                    if m.reason:
                        reasons.append(f"[{nt}] {m.reason}")
                    else:
                        reasons.append(f"[{nt}]")
                reason_text = "\n".join(reasons)
            elif r.reason:
                reason_text = r.reason
            else:
                reason_text = ""

            row_vals = [
                i,
                _fmt_time(rev.created_at),
                rev.display_name,
                rev.review_text.strip(),
                cat,
                reason_text,
                r.reply_suggestion,  # 推荐回复文案（未解决为空）
                _review_url(rev.appid, rev.author),
            ]
            for col, val in enumerate(row_vals, 1):
                cell = ws.cell(row=i + 1, column=col, value=val)
                cell.alignment = wrap

    # 列宽
    col_widths = [6, 12, 14, 60, 8, 50, 50, 40]
    for ws in wb.worksheets:
        for idx, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(idx)].width = w
        ws.freeze_panes = "A2"  # 冻结表头

    # 组装文件名：{游戏名}_{语种}_{时间段}_{日期}_{差评总数}.xlsx
    ts = dt.datetime.now().strftime("%Y%m%d")
    parts = [_safe_filename(title)]
    if lang_label:
        parts.append(_safe_filename(lang_label))
    if range_label:
        parts.append(_safe_filename(range_label))
    parts.append(ts)
    parts.append(str(len(results)))
    name = "_".join(p for p in parts if p)

    path = os.path.join(output_dir, f"{name}.xlsx")
    wb.save(path)
    return path
