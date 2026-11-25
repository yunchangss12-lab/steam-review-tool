"""报告生成：把归类结果输出为易读的清单，便于针对性写开发者回复。

支持两种输出：
  1. HTML 报告（默认，双击即可在浏览器查看，含筛选）
  2. 纯文本 Markdown 报告（便于复制粘贴）
"""

import datetime as dt
import html
import os
from typing import Optional

from .classifier import ClassifiedReview, ReviewItem, summarize


def _fmt_time(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _review_url(appid: int, author_steamid: str) -> str:
    """生成 Steam 单条评测的直链，直接定位到该玩家对该游戏的评测。

    格式：https://steamcommunity.com/profiles/{steamid}/recommended/{appid}/
    这能精确跳转到该玩家写的这条评测（而非评测列表页），省去手动筛选。
    """
    if author_steamid:
        return f"https://steamcommunity.com/profiles/{author_steamid}/recommended/{appid}/"
    # 兜底：没有 steamid 时跳到评测列表页
    return f"https://steamcommunity.com/app/{appid}/reviews/"


def build_markdown(results: list[ClassifiedReview], appid: int) -> str:
    counts = summarize(results)
    lines: list[str] = []
    lines.append(f"# Steam 差评比对报告（AppID: {appid}）")
    lines.append(f"\n> 生成时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(
        f"\n**统计**：已解决 {counts['已解决']} 条 · 未解决 {counts['未解决']} 条 · "
        f"情绪化评论 {counts['情绪化评论']} 条 · 待确认 {counts['待确认']} 条 · "
        f"本地化质量反馈 {counts['本地化质量反馈']} 条 · 合计 {len(results)} 条\n"
    )

    order = ["已解决", "未解决", "情绪化评论", "本地化质量反馈", "待确认"]
    for cat in order:
        group = [r for r in results if r.category == cat]
        if not group:
            continue
        lines.append(f"\n## {cat}（{len(group)} 条）\n")
        for i, r in enumerate(group, 1):
            rev = r.review
            text = rev.review_text.strip().replace("\n", " ")[:200]
            lines.append(f"### {i}. {_fmt_time(rev.created_at)} · 玩家 {rev.display_name}")
            lines.append(f"> {text}")
            # 已解决：展示大模型给出的「问题↔修复」对应关系（解决依据）
            for m in r.matches:
                news_title = m.news.title.strip()
                lines.append(f"- 解决依据：[{news_title}]")
                if m.reason:
                    lines.append(f"  - {m.reason}")
            # 未解决/待确认/本地化质量反馈：展示判定理由（如有）
            if r.reason and cat != "已解决":
                lines.append(f"- 判定：{r.reason}")
            # 推荐回复文案（未解决不生成）
            if r.reply_suggestion:
                lines.append(f"- 推荐回复：{r.reply_suggestion}")
            lines.append(f"- 评测链接：{_review_url(appid, rev.author)}\n")
    return "\n".join(lines)


def build_html(results: list[ClassifiedReview], appid: int, title: str) -> str:
    counts = summarize(results)
    css = """
    :root{--red:#d32f2f;--green:#2e7d32;--amber:#f57f17;--purple:#7b1fa2;--blue:#1565c0;--bg:#f7f7f8;--card:#fff;--line:#e5e5e5}
    *{box-sizing:border-box}
    body{margin:0;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:#222}
    .wrap{max-width:960px;margin:0 auto;padding:24px 20px 60px}
    h1{font-size:22px;margin:0 0 6px}
    .meta{color:#777;font-size:13px;margin-bottom:16px}
    .stats{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}
    .stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 20px;min-width:120px}
    .stat b{display:block;font-size:26px}
    .stat span{font-size:13px;color:#777}
    .solved b{color:var(--green)}.unsolved b{color:var(--red)}.pending b{color:var(--amber)}.emotional b{color:var(--purple)}.localization b{color:var(--blue)}
    .filters{display:flex;gap:8px;margin-bottom:18px}
    .filters button{border:1px solid var(--line);background:#fff;border-radius:20px;padding:6px 14px;cursor:pointer;font-size:13px}
    .filters button.active{background:#222;color:#fff}
    .group{margin-top:22px}
    .group h2{font-size:17px;border-left:4px solid #222;padding-left:10px}
    .item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:12px}
    .item .head{display:flex;justify-content:space-between;gap:10px;font-size:13px;color:#777;margin-bottom:6px;flex-wrap:wrap}
    .item .txt{font-size:14px;line-height:1.6;white-space:pre-wrap}
    .item .kw{margin-top:8px;font-size:12px;color:#555}
    .item .news{margin-top:8px;font-size:12px;color:#555}
    .item .news .nhead{font-weight:600;color:#333}
    .item .news .snip{display:block;background:#f2f7f3;border-left:3px solid var(--green);padding:6px 10px;margin-top:4px;border-radius:4px;color:#444;line-height:1.5;font-size:12px}
    .item .reply{margin-top:10px;font-size:13px;color:#1a1a1a;background:#fff8e1;border:1px solid #ffe0a3;border-left:3px solid #f5a623;padding:8px 12px;border-radius:6px;line-height:1.6}
    .item .reply .rhead{font-weight:700;color:#b26a00;display:block;margin-bottom:3px;font-size:12px}
    .tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;color:#fff}
    .tag.solved{background:var(--green)}.tag.unsolved{background:var(--red)}.tag.pending{background:var(--amber)}.tag.emotional{background:var(--purple)}.tag.localization{background:var(--blue)}
    .link{font-size:12px;color:#1976d2;text-decoration:none}
    .empty{color:#999;font-size:14px}
    """
    esc = html.escape
    rows = []
    for r in results:
        rev = r.review
        txt = esc(rev.review_text.strip())
        tag_cls = {
            "已解决": "solved",
            "未解决": "unsolved",
            "待确认": "pending",
            "情绪化评论": "emotional",
            "本地化质量反馈": "localization",
        }[r.category]
        url = _review_url(appid, rev.author)

        # 已解决：展示大模型给出的「问题↔修复」对应关系（解决依据）
        news_blocks = ""
        if r.matches:
            blocks = []
            for m in r.matches:
                ntitle = esc(m.news.title.strip())
                reason = esc(m.reason.strip()) if m.reason else ""
                blocks.append(
                    f"<div class='news'>"
                    f"<span class='nhead'>解决依据：{ntitle}</span>"
                    + (f"<span class='snip'>{reason}</span>" if reason else "")
                    + "</div>"
                )
            news_blocks = "".join(blocks)
        elif r.reason and r.category != "已解决":
            # 未解决/待确认：展示判定理由
            reason = esc(r.reason.strip())
            news_blocks = (
                f"<div class='news'><span class='snip'>{reason}</span></div>"
            )

        # 推荐回复文案（未解决不生成）
        reply_block = ""
        if r.reply_suggestion:
            reply_block = (
                f"<div class='reply'>"
                f"<span class='rhead'>💬 推荐回复文案</span>"
                f"{esc(r.reply_suggestion)}"
                f"</div>"
            )

        rows.append(
            f"""
        <div class="item" data-cat="{r.category}">
          <div class="head">
            <span>{esc(_fmt_time(rev.created_at))} · 玩家 {esc(rev.display_name)}</span>
            <span class="tag {tag_cls}">{esc(r.category)}</span>
          </div>
          <div class="txt">{txt}</div>
          {news_blocks}
          {reply_block}
          <div><a class='link' href='{url}' target='_blank' rel='noopener'>前往该条评测回复 ↗</a></div>
        </div>"""
        )

    body = f"""
    <div class="wrap">
      <h1>Steam 差评比对报告 — {esc(title)}</h1>
      <div class="meta">AppID：{appid} · 生成时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
      <div class="stats">
        <div class="stat solved"><b>{counts['已解决']}</b><span>已解决</span></div>
        <div class="stat unsolved"><b>{counts['未解决']}</b><span>未解决</span></div>
        <div class="stat emotional"><b>{counts['情绪化评论']}</b><span>情绪化评论</span></div>
        <div class="stat localization"><b>{counts['本地化质量反馈']}</b><span>本地化质量反馈</span></div>
        <div class="stat pending"><b>{counts['待确认']}</b><span>待确认</span></div>
        <div class="stat"><b>{len(results)}</b><span>合计差评</span></div>
      </div>
      <div class="filters">
        <button class="active" data-f="全部">全部</button>
        <button data-f="已解决">已解决</button>
        <button data-f="未解决">未解决</button>
        <button data-f="情绪化评论">情绪化评论</button>
        <button data-f="本地化质量反馈">本地化质量反馈</button>
        <button data-f="待确认">待确认</button>
      </div>
      <div id="list">{''.join(rows)}</div>
    </div>
    <script>
    const btns=document.querySelectorAll('.filters button');
    const items=document.querySelectorAll('.item');
    btns.forEach(b=>b.addEventListener('click',()=>{{
      btns.forEach(x=>x.classList.remove('active'));b.classList.add('active');
      const f=b.dataset.f;
      items.forEach(it=>{{it.style.display=(f==='全部'||it.dataset.cat===f)?'':'none';}});
    }}));
    </script>
    """
    return f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{css}</style></head><body>{body}</body></html>"


def _safe_filename(s: str, maxlen: int = 40) -> str:
    """把字符串清理为合法的文件名片段（去掉 Windows 非法字符）。"""
    if not s:
        return ""
    # 去掉文件名里不允许的字符
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, "_")
    s = s.strip().strip(".").strip()
    # 去掉多余空格
    s = " ".join(s.split())
    return s[:maxlen]


def write_report(
    results: list[ClassifiedReview],
    appid: int,
    title: str,
    output_dir: str = "reports",
    fmt: str = "html",
    range_label: str = "",
    lang_label: str = "",
) -> str:
    """生成报告文件。

    文件名格式（HTML）：{游戏名}_{语种}_{时间段}_{查询日期}_{差评总数}.html
    range_label: 时间段文案（如「最近30天」「全部时间」），空则省略该段。
    lang_label:   语种文案（如「简体中文」「英语」「全部语言」），空则省略该段。
    """
    os.makedirs(output_dir, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d")
    count = len(results)
    # 组装文件名片段
    parts = [_safe_filename(title)]
    if lang_label:
        parts.append(_safe_filename(lang_label))
    if range_label:
        parts.append(_safe_filename(range_label))
    parts.append(ts)
    parts.append(str(count))
    name = "_".join(p for p in parts if p)

    if fmt == "html":
        path = os.path.join(output_dir, f"{name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_html(results, appid, title))
    else:
        path = os.path.join(output_dir, f"{name}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_markdown(results, appid))
    return path
