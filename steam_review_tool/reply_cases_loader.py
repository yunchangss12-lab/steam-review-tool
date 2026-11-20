"""回复案例库导入解析器。

支持用户导入自定义的「差评类型 → 参考回复文案」案例文件，格式不限：
  - Excel (.xlsx / .xls)
  - CSV
  - Markdown 表格 (.md)
  - 纯文本 (.txt)
  - Word (.docx)

解析策略（由结构化到降级）：
  1. 优先尝试解析成结构化对照表（两列：类型 + 回复），得到 list[dict]。
  2. 若解析不出结构化数据，则退化为「整段文本」，把文件全文作为案例
     原文交给大模型自行参考（此时案例库退化为一段 reference text）。
  3. 所有解析都失败时返回空，调用方回退到内置默认案例库。
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReplyCaseSource:
    """一次导入的结果：要么是结构化案例列表，要么是一段原文。"""

    cases: list[dict[str, str]] = field(default_factory=list)
    raw_text: str = ""
    source_name: str = ""  # 文件名（用于日志/报告标注）

    @property
    def is_structured(self) -> bool:
        return bool(self.cases)

    @property
    def is_empty(self) -> bool:
        return not self.cases and not self.raw_text


def _split_rows(text: str) -> list[list[str]]:
    """按行切分，兼容 \r\n / \n。"""
    return [ln for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def _parse_markdown_table(text: str) -> list[dict[str, str]]:
    """尝试把 Markdown 表格解析成「类型 → 回复」对照。

    表头需含「类型/问题/差评」与「回复/文案/内容」中的至少两列。
    """
    lines = _split_rows(text)
    table_lines = [ln for ln in lines if ln.strip().startswith("|")]
    if len(table_lines) < 2:
        return []

    def _cells(ln: str) -> list[str]:
        s = ln.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    header = _cells(table_lines[0])
    # 跳过 |---|---| 分隔行
    body_start = 1
    if body_start < len(table_lines) and all(
        set(c.strip()) <= set("-: ") for c in _cells(table_lines[body_start])
    ):
        body_start = 2

    # 定位「类型」列和「回复」列
    type_idx = reply_idx = None
    for i, h in enumerate(header):
        hl = h.lower()
        if type_idx is None and any(k in hl for k in ("类型", "问题", "差评", "type", "问题描述")):
            type_idx = i
        if reply_idx is None and any(k in hl for k in ("回复", "文案", "内容", "reply", "answer")):
            reply_idx = i
    if type_idx is None or reply_idx is None:
        return []

    out: list[dict[str, str]] = []
    for ln in table_lines[body_start:]:
        cells = _cells(ln)
        if len(cells) <= max(type_idx, reply_idx):
            continue
        t = cells[type_idx].strip()
        r = cells[reply_idx].strip()
        if t and r:
            out.append({"type": t, "reply": r})
    return out


def _parse_csv_content(content: str) -> list[dict[str, str]]:
    """解析 CSV 文本为「类型 → 回复」对照。"""
    reader = csv.reader(io.StringIO(content))
    rows = [r for r in reader]
    if len(rows) < 2:
        return []
    header = [c.strip() for c in rows[0]]
    type_idx = reply_idx = None
    for i, h in enumerate(header):
        hl = h.lower()
        if type_idx is None and any(k in hl for k in ("类型", "问题", "差评", "type", "问题描述")):
            type_idx = i
        if reply_idx is None and any(k in hl for k in ("回复", "文案", "内容", "reply", "answer")):
            reply_idx = i
    if type_idx is None or reply_idx is None:
        return []
    out = []
    for r in rows[1:]:
        if len(r) <= max(type_idx, reply_idx):
            continue
        t = r[type_idx].strip()
        rep = r[reply_idx].strip()
        if t and rep:
            out.append({"type": t, "reply": rep})
    return out


def _parse_docx(path: str) -> str:
    """抽取 .docx 的纯文本（尽量保留段落）。"""
    try:
        import docx  # python-docx，可选依赖
    except ImportError:
        return ""
    try:
        d = docx.Document(path)
        parts = []
        for p in d.paragraphs:
            if p.text.strip():
                parts.append(p.text.strip())
        for t in d.tables:
            for row in t.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    except Exception:
        return ""


def _parse_xlsx(path: str) -> str:
    """抽取 .xlsx/.xls 的单元格文本，重建成 Markdown 表格文本。"""
    try:
        import openpyxl
    except ImportError:
        return ""
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        lines: list[str] = []
        for ws in wb.worksheets:
            lines.append(f"## {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c).strip() for c in row]
                if any(cells):
                    lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)
    except Exception:
        return ""


def load_reply_cases(path: str) -> ReplyCaseSource:
    """从文件加载回复案例库。

    返回 ReplyCaseSource；解析失败时 cases 为空、raw_text 为原文（尽力）。
    """
    if not path or not os.path.isfile(path):
        return ReplyCaseSource(source_name=os.path.basename(path) if path else "")

    name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()

    text = ""
    try:
        if ext in (".xlsx", ".xlsm"):
            text = _parse_xlsx(path)
        elif ext == ".xls":
            text = _parse_xlsx(path)  # 旧 xls 若 openpyxl 不支持则退化为空
        elif ext == ".docx":
            text = _parse_docx(path)
        else:
            # txt / md / csv / 其他文本：尝试多种编码读取
            raw = _read_text(path)
            text = raw
    except Exception:
        text = ""

    if not text:
        # 实在读不出来（如 .xls 老格式），尝试二进制兜底读文本
        try:
            text = _read_text(path)
        except Exception:
            text = ""

    # 结构化解析
    cases: list[dict[str, str]] = []
    if text:
        if ext == ".csv":
            cases = _parse_csv_content(text)
        if not cases:
            cases = _parse_markdown_table(text)

    # 若结构化解析得到少量案例，也保留 raw_text 供补充；否则退化为整段文本
    if not cases and text.strip():
        return ReplyCaseSource(raw_text=text.strip(), source_name=name)

    return ReplyCaseSource(cases=cases, raw_text=text, source_name=name)


def _read_text(path: str) -> str:
    """尝试多种编码读取文本文件。"""
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "utf-16"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception:
            break
    # 最后尝试二进制
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="ignore")


def cases_to_text(cases: list[dict[str, str]]) -> str:
    """把结构化案例列表拼成 few-shot 文本块（与 reply_templates 同格式）。"""
    lines = []
    for c in cases:
        lines.append(f"【差评类型】{c.get('type', '')}\n【参考回复】{c.get('reply', '')}")
    return "\n".join(lines)
