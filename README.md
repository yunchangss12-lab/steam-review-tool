"""Steam 差评 × 更新记录 比对归类工具

输入一个 Steam 游戏 AppID，抓取：
  1. 历史差评（Storefront API，无需 Key）
  2. 游戏更新记录 / Patch Notes（Steamworks 新闻接口，需 Web API Key）
然后按时间与关键词比对，把差评归类为「已解决 / 未解决 / 待确认」，
生成一份便于针对性写开发者回复的清单报告。
"""
