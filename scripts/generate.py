#!/usr/bin/env python3
"""主页 SVG 生成器：Catppuccin（Mocha 深色 / Latte 浅色）× kitty 标签页窗口。

每个面板都是同一套窗口外壳，只是激活的标签页不同：
  1 fastfetch 自我介绍 · 2 btop 统计 · 3 skyline 3D 贡献图 · 4 ls ~/code 项目 · 5 snake 贪吃蛇

用法：GH_TOKEN=<token> python scripts/generate.py --out dist [--snake snake.svg]
依赖：fonttools、brotli（字体子集化后以 woff2 内嵌，<img> 里的 SVG 无法加载外部字体）
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import io
import json
import os
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
FONTS = {
    400: HERE / "fonts/MapleMono-CN-Regular.base.woff2",
    700: HERE / "fonts/MapleMono-CN-Bold.base.woff2",
}

W = 860          # 面板宽度，README 里按 100% 缩放
PAD = 28         # 面板内边距
BAR = 36         # 标签栏高度
TABS = ["fastfetch", "btop", "skyline", "ls ~/code", "snake"]

# Catppuccin 官方色板：https://catppuccin.com/palette
MOCHA = dict(
    rosewater="#f5e0dc", flamingo="#f2cdcd", pink="#f5c2e7", mauve="#cba6f7", red="#f38ba8",
    maroon="#eba0ac", peach="#fab387", yellow="#f9e2af", green="#a6e3a1", teal="#94e2d5",
    sky="#89dceb", sapphire="#74c7ec", blue="#89b4fa", lavender="#b4befe", text="#cdd6f4",
    subtext1="#bac2de", subtext0="#a6adc8", overlay2="#9399b2", overlay1="#7f849c",
    overlay0="#6c7086", surface2="#585b70", surface1="#45475a", surface0="#313244",
    base="#1e1e2e", mantle="#181825", crust="#11111b",
)
LATTE = dict(
    rosewater="#dc8a78", flamingo="#dd7878", pink="#ea76cb", mauve="#8839ef", red="#d20f39",
    maroon="#e64553", peach="#fe640b", yellow="#df8e1d", green="#40a02b", teal="#179299",
    sky="#04a5e5", sapphire="#209fb5", blue="#1e66f5", lavender="#7287fd", text="#4c4f69",
    subtext1="#5c5f77", subtext0="#6c6f85", overlay2="#7c7f93", overlay1="#8c8fa1",
    overlay0="#9ca0b0", surface2="#acb0be", surface1="#bcc0cc", surface0="#ccd0da",
    base="#eff1f5", mantle="#e6e9ef", crust="#dce0e8",
)
THEMES = {"dark": MOCHA, "light": LATTE}
LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}


def mix(p: dict, key: str, t: float, base: str = "base") -> str:
    """把色板里的 key 按比例 t 混进底色，用来派生贡献等级色。"""
    a, b = p[key], p[base]
    return "#" + "".join(f"{round(int(b[k:k + 2], 16) + (int(a[k:k + 2], 16) - int(b[k:k + 2], 16)) * t):02x}"
                         for k in (1, 3, 5))


def shade(color: str, f: float) -> str:
    return "#" + "".join(f"{round(int(color[k:k + 2], 16) * f):02x}" for k in (1, 3, 5))


def is_light(p: dict) -> bool:
    return p is LATTE


def soft(p: dict, key: str, t: float = .72) -> str:
    """大面积填充用的强调色：深色主题原样用；浅色主题 Latte 太饱和，混进底色柔化。
    文字仍用原色保证对比度，只有色块走这里。"""
    return mix(p, key, t) if is_light(p) else p[key]


def level_colors(p: dict) -> list[str]:
    """贡献 0~4 级配色：贪吃蛇和 3D 图共用。浅色主题整体浅一档。"""
    steps = (.25, .45, .65, .85) if is_light(p) else (.35, .6, .8, 1)
    return [p["surface0"], *(mix(p, "mauve", t) for t in steps)]


# 数据系列的取色顺序；语言颜色在所有面板里保持一致
SERIES = ["mauve", "blue", "green", "peach", "pink", "teal", "yellow"]

ARCH = r"""                   -`
                  .o+`
                 `ooo/
                `+oooo:
               `+oooooo:
               -+oooooo+:
             `/:-:++oooo+:
            `/++++/+++++++:
           `/++++++++++++++:
          `/+++ooooooooooooo/`
         ./ooosssso++osssssso+`
        .oossssso-````/ossssss+`
       -osssssso.      :ssssssso.
      :osssssss/        osssso+++.
     /ossssssss/        +ssssooo/-
   `/ossssso+/:-        -:/+osssso+-
  `+sso+:-`                 `.-/+oso:
 `++:.                           `-/+/
 .`                                 `/""".split("\n")


# ───────────────────────── 字体与排版 ─────────────────────────

_metrics = TTFont(FONTS[400])
_cmap, _hmtx, _upm = _metrics.getBestCmap(), _metrics["hmtx"], _metrics["head"].unitsPerEm


def tw(s: str, size: float) -> float:
    """按字体真实步进计算文字宽度（等宽：ASCII 0.6em，汉字 1.2em）。"""
    total = 0
    for ch in s:
        g = _cmap.get(ord(ch))
        total += _hmtx[g][0] if g else (1200 if unicodedata.east_asian_width(ch) in "WF" else 600)
    return total / _upm * size


def font_face(weight: int, chars: set[str]) -> str:
    f = TTFont(FONTS[weight])
    o = subset.Options()
    o.flavor, o.layout_features, o.hinting, o.name_IDs = "woff2", [], False, [1, 2]
    s = subset.Subsetter(o)
    s.populate(text="".join(sorted(chars | {" "})))
    s.subset(f)
    buf = io.BytesIO()
    f.flavor = "woff2"
    f.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"@font-face{{font-family:RX;font-weight:{weight};src:url(data:font/woff2;base64,{b64}) format('woff2')}}"


def wrap(s: str, size: float, width: float, max_lines: int) -> list[str]:
    """按词换行：英文单词整体不拆，中文逐字可断；超出行数用省略号收尾。"""
    tokens = re.findall(r"[A-Za-z0-9_\-./+#]+|\s+|.", s)
    lines, cur = [], ""
    for i, tok in enumerate(tokens):
        if tw(cur + tok, size) <= width or not cur.strip():
            cur += tok
            continue
        lines.append(cur.rstrip())
        cur = tok.lstrip()
        if len(lines) == max_lines:
            last = lines[-1]
            while last and tw(last + "…", size) > width:
                last = last[:-1]
            lines[-1] = last.rstrip() + "…"
            return lines
    return lines + [cur] if cur.strip() else lines


# ───────────────────────── SVG 画布 ─────────────────────────

class Svg:
    def __init__(self, h: float, label: str, p: dict):
        self.h, self.label, self.p = h, label, p
        self.parts: list[str] = []
        self.defs: list[str] = []
        self.glyphs: dict[int, set[str]] = {400: set(), 700: set()}

    def c(self, key: str) -> str:
        return self.p.get(key, key)

    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x: float, y: float, runs, *, size: float = 13, anchor: str = "start") -> None:
        """runs: 字符串，或 [(文字, 颜色, 字重[, 字号])] 列表；一个 <text> 里多段 tspan。"""
        if isinstance(runs, str):
            runs = [(runs, "text", 400)]
        spans = []
        for run in runs:
            t, fill, weight = run[:3]
            fs = f' font-size="{run[3]}"' if len(run) > 3 else ""
            fw = ' font-weight="700"' if weight == 700 else ""
            self.glyphs[weight].update(t)
            spans.append(f'<tspan fill="{self.c(fill)}"{fw}{fs}>{html.escape(t)}</tspan>')
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}"{a}>{"".join(spans)}</text>')

    def chrome(self, active: int, right: str) -> None:
        p, h = self.p, self.h
        self.defs.append(f'<clipPath id="rx-win"><rect width="{W}" height="{h}" rx="12"/></clipPath>')
        self.add(f'<g clip-path="url(#rx-win)"><rect width="{W}" height="{h}" fill="{p["base"]}"/>'
                 f'<rect width="{W}" height="{BAR}" fill="{p["mantle"]}"/></g>')
        self.add(f'<line x1="0" y1="{BAR}" x2="{W}" y2="{BAR}" stroke="{p["surface0"]}"/>')
        x = 12
        for i, name in enumerate(TABS, 1):
            label, on = f" {i} {name} ", i == active
            w = tw(label, 12) + 8
            # 激活标签：深色主题实心紫底；浅色主题淡紫底 + 紫字，避免白底上一块刺眼的饱和色
            if on and is_light(p):
                fill, alpha, ink = mix(p, "mauve", .14), 1, "mauve"
            elif on:
                fill, alpha, ink = p["mauve"], 1, "crust"
            else:
                fill, alpha, ink = p["surface0"], .55, "overlay1"
            self.add(f'<rect x="{x}" y="8" width="{w:.1f}" height="20" rx="5" fill="{fill}" fill-opacity="{alpha}"/>')
            self.text(x + 4, 22.5, [(label, ink, 700 if on else 400)], size=12)
            x += w + 6
        self.text(W - 16, 22.5, [(right, "overlay1", 400)], size=12, anchor="end")
        self.add(f'<rect x=".5" y=".5" width="{W - 1}" height="{h - 1}" rx="11.5" fill="none" stroke="{p["surface0"]}"/>')

    def render(self) -> str:
        faces = "".join(font_face(w, g) for w, g in self.glyphs.items() if g)
        css = (
            f"{faces}"
            "text{font-family:RX,'Maple Mono NF CN','JetBrains Mono',ui-monospace,monospace;white-space:pre}"
            ".rx-cur{animation:rx-blink 1.1s step-end infinite}"
            "@keyframes rx-blink{50%{opacity:0}}"
            "@media (prefers-reduced-motion:reduce){.rx-cur{animation:none}}"
        )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{self.h}" viewBox="0 0 {W} {self.h}" '
            f'role="img" aria-label="{html.escape(self.label)}" xml:space="preserve">'
            f"<title>{html.escape(self.label)}</title><style>{css}</style>"
            f"<defs>{''.join(self.defs)}</defs>{''.join(self.parts)}</svg>"
        )


# ───────────────────────── 数据 ─────────────────────────

def gql(query: str, **variables) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {os.environ['GH_TOKEN']}", "User-Agent": "profile-gen"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


CAL = "contributionCalendar{totalContributions weeks{contributionDays{date contributionCount contributionLevel}}}"


def fetch(login: str) -> dict:
    u = gql(f"""query($login:String!){{user(login:$login){{createdAt
      contributionsCollection{{{CAL}}}
      repositories(ownerAffiliations:OWNER,isFork:false,first:100){{nodes{{name
        languages(first:10,orderBy:{{field:SIZE,direction:DESC}}){{edges{{size node{{name}}}}}}}}}}}}}}""",
            login=login)["user"]

    # 历年日历拼起来算 streak 与累计贡献（单次查询最多跨一年）
    days: dict[str, int] = {}
    created = dt.datetime.fromisoformat(u["createdAt"].replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)
    start = created
    while start < now:
        end = min(start + dt.timedelta(days=365), now)
        cal = gql(f"""query($login:String!,$from:DateTime!,$to:DateTime!){{user(login:$login){{
          contributionsCollection(from:$from,to:$to){{{CAL}}}}}}}""",
                  login=login, **{"from": start.isoformat(), "to": end.isoformat()})
        for wk in cal["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]:
            for d in wk["contributionDays"]:
                days[d["date"]] = d["contributionCount"]
        start = end

    year = u["contributionsCollection"]["contributionCalendar"]
    weeks = [sum(d["contributionCount"] for d in wk["contributionDays"]) for wk in year["weeks"]][-52:]
    week_starts = [wk["contributionDays"][0]["date"] for wk in year["weeks"]][-52:]
    grid = [[(d["contributionCount"], LEVELS[d["contributionLevel"]], d["date"]) for d in wk["contributionDays"]]
            for wk in year["weeks"]]

    langs: Counter[str] = Counter()
    for repo in u["repositories"]["nodes"]:
        for e in repo["languages"]["edges"]:
            if e["node"]["name"] not in CONFIG["exclude_languages"]:
                langs[e["node"]["name"]] += e["size"]

    featured = []
    for name in CONFIG["featured"]:
        r = gql("""query($o:String!,$n:String!){repository(owner:$o,name:$n){name description isPrivate
          stargazerCount forkCount pushedAt primaryLanguage{name}}}""", o=login, n=name)["repository"]
        if r and not r["isPrivate"]:       # 私有仓库永远不上主页
            featured.append(r)

    return dict(created=created, days=days, year_total=year["totalContributions"],
                weeks=weeks, week_starts=week_starts, grid=grid, langs=langs, featured=featured)


def streaks(days: dict[str, int]) -> tuple[tuple[int, str, str], tuple[int, str]]:
    today = dt.date.today().isoformat()
    dates = sorted(d for d in days if d <= today)
    best, run, run_start, best_range = 0, 0, "", ("", "")
    for d in dates:
        if days[d] > 0:
            run_start = d if run == 0 else run_start
            run += 1
            if run > best:
                best, best_range = run, (run_start, d)
        else:
            run = 0
    # 当前连续：今天还没提交不算断，从昨天往回数
    cur, i = 0, len(dates) - 1
    if i >= 0 and days[dates[i]] == 0:
        i -= 1
    while i >= 0 and days[dates[i]] > 0:
        cur, i = cur + 1, i - 1
    cur_start = dates[i + 1] if cur else ""
    return (best, *best_range), (cur, cur_start)


def lang_colors(langs: Counter) -> dict[str, str]:
    return {name: SERIES[i % len(SERIES)] for i, (name, _) in enumerate(langs.most_common())}


# ───────────────────────── 面板 ─────────────────────────

def hero(p: dict, data: dict) -> str:
    info = [tuple(row) for row in CONFIG["info"]]
    age = (dt.datetime.now(dt.timezone.utc) - data["created"]).days
    info.append(("", "Commits", f"近一年 {data['year_total']:,} 次贡献"))
    info.append(("", "Uptime", f"在 GitHub 上 {age // 365} 年 {age % 365 // 30} 个月"))

    top, step = BAR + 46, 23
    h = top + len(info) * step + 92
    s = Svg(h, f"{CONFIG['user']}@{CONFIG['host']} — fastfetch", p)
    s.chrome(1, f" {CONFIG['login']}")

    # Arch 标志：蓝 → 紫对角渐变
    lh, fs = 14, 11.5
    ly = BAR + (h - BAR - len(ARCH) * lh) / 2 + 10
    s.defs.append(f'<linearGradient id="rx-arch" gradientUnits="userSpaceOnUse" x1="{PAD}" y1="{ly}" '
                  f'x2="{PAD + 270}" y2="{ly + len(ARCH) * lh}"><stop offset="0" stop-color="{p["blue"]}"/>'
                  f'<stop offset="1" stop-color="{p["mauve"]}"/></linearGradient>')
    for i, line in enumerate(ARCH):
        s.text(PAD + 6, ly + i * lh, [(line, "url(#rx-arch)", 700)], size=fs)

    x = 318
    head = f"{CONFIG['user']}@{CONFIG['host']}"
    s.text(x, top, [(CONFIG["user"], "mauve", 700), ("@", "overlay1", 400), (CONFIG["host"], "blue", 700)], size=17)
    s.add(f'<line x1="{x}" y1="{top + 11}" x2="{x + tw(head, 17):.1f}" y2="{top + 11}" stroke="{p["surface1"]}" stroke-width="1.5"/>')

    y = top + 36
    for i, (icon, key, val) in enumerate(info):
        s.text(x, y + i * step, [(f"{icon}  ", "blue", 400), (f"{key:<8}", "blue", 700), (val, "text", 400)],
               size=13.5)

    y += len(info) * step - 4
    for i, key in enumerate(["red", "peach", "yellow", "green", "teal", "blue", "mauve", "pink"]):
        s.add(f'<rect x="{x + i * 30}" y="{y}" width="24" height="12" rx="3" fill="{p[key]}"/>')
    y += 44
    s.text(x, y, [("❯ ", "green", 700), ("echo \"欢迎来玩\"", "subtext0", 400)], size=13.5)
    cx = x + tw("❯ echo \"欢迎来玩\" ", 13.5)
    s.add(f'<rect x="{cx:.1f}" y="{y - 12}" width="8" height="16" rx="1" fill="{p["text"]}" class="rx-cur"/>')
    return s.render()


def stats(p: dict, data: dict) -> str:
    h = 344
    s = Svg(h, "GitHub 统计：贡献、连续提交、每周活跃与语言占比", p)
    s.chrome(2, f"updated {dt.date.today():%Y-%m-%d}")

    (best, b0, b1), (cur, c0) = streaks(data["days"])
    md = lambda d: d[5:] if d else "--"
    metrics = [
        ("mauve", "近一年贡献", f"{data['year_total']:,}", "", "过去 365 天"),
        ("blue", "最长连续", str(best), " 天", f"{md(b0)} → {md(b1)}"),
        ("green", "当前连续", str(cur), " 天", f"自 {md(c0)} 起" if cur else "今天就是新的开始"),
        ("peach", "累计贡献", f"{sum(data['days'].values()):,}", "", f"自 {data['created']:%Y-%m} 加入"),
    ]
    cw = (W - 2 * PAD) / len(metrics)
    for i, (color, label, value, unit, note) in enumerate(metrics):
        x = PAD + i * cw + (0 if i == 0 else 22)
        if i:
            s.add(f'<line x1="{PAD + i * cw:.1f}" y1="{BAR + 28}" x2="{PAD + i * cw:.1f}" y2="{BAR + 104}" stroke="{p["surface0"]}"/>')
        s.add(f'<rect x="{x:.1f}" y="{BAR + 29}" width="3" height="12" rx="1.5" fill="{p[color]}"/>')
        s.text(x + 10, BAR + 39.5, [(label, "subtext0", 400)], size=12)
        s.text(x, BAR + 78, [(value, "text", 700, 30), (unit, "subtext0", 400, 13)], size=30)
        s.text(x, BAR + 100, [(note, "overlay1", 400)], size=11.5)

    s.add(f'<line x1="{PAD}" y1="{BAR + 128}" x2="{W - PAD}" y2="{BAR + 128}" stroke="{p["surface0"]}"/>')

    # 每周活跃柱状图
    top = BAR + 156
    cx0, cw_ = PAD, 500
    weeks, starts = data["weeks"], data["week_starts"]
    peak = max(weeks) or 1
    s.add(f'<rect x="{cx0}" y="{top - 10}" width="3" height="12" rx="1.5" fill="{p["mauve"]}"/>')
    s.text(cx0 + 10, top, [("每周活跃 · 近 52 周", "subtext0", 400)], size=12)
    s.text(cx0 + cw_, top, [(f"峰值 {peak} / 周", "overlay1", 400)], size=11.5, anchor="end")
    ch, base = 92, top + 22 + 92
    s.defs.append(f'<linearGradient id="rx-bar" gradientUnits="userSpaceOnUse" x1="0" y1="{base - ch}" x2="0" y2="{base}">'
                  f'<stop offset="0" stop-color="{soft(p, "mauve", .78)}"/><stop offset="1" stop-color="{soft(p, "blue", .5)}"/></linearGradient>')
    slot = cw_ / len(weeks)
    for i, v in enumerate(weeks):
        bx = cx0 + i * slot + slot * 0.18
        if v:
            bh = max(3, v / peak * ch)
            s.add(f'<rect x="{bx:.1f}" y="{base - bh:.1f}" width="{slot * 0.64:.1f}" height="{bh:.1f}" rx="2" '
                  f'fill="url(#rx-bar)"/>')
        else:
            s.add(f'<rect x="{bx:.1f}" y="{base - 2}" width="{slot * 0.64:.1f}" height="2" rx="1" fill="{p["surface1"]}"/>')
    s.add(f'<line x1="{cx0}" y1="{base + 0.5}" x2="{cx0 + cw_}" y2="{base + 0.5}" stroke="{p["surface0"]}"/>')
    last = -9
    for i, d in enumerate(starts):
        if i == 0 or d[5:7] != starts[i - 1][5:7]:
            if i - last >= 3:
                s.text(cx0 + i * slot, base + 17, [(f"{int(d[5:7])}月", "overlay1", 400)], size=10.5)
                last = i

    # 语言占比
    lx, lw = cx0 + cw_ + 40, W - PAD - (cx0 + cw_ + 40)
    s.add(f'<rect x="{lx}" y="{top - 10}" width="3" height="12" rx="1.5" fill="{p["blue"]}"/>')
    s.text(lx + 10, top, [("语言占比", "subtext0", 400)], size=12)
    langs = data["langs"]
    total = sum(langs.values()) or 1
    colors = lang_colors(langs)
    rows = langs.most_common(5)
    if len(langs) > 5:
        rows = rows[:4] + [("其他", total - sum(v for _, v in rows[:4]))]
    s.defs.append(f'<clipPath id="rx-lang"><rect x="{lx}" y="{top + 16}" width="{lw}" height="10" rx="5"/></clipPath>')
    bx, segs = lx, []
    for name, v in rows:
        w = v / total * lw
        segs.append(f'<rect x="{bx:.1f}" y="{top + 16}" width="{w + 0.5:.1f}" height="10" fill="{p[colors.get(name, "overlay0")]}"/>')
        bx += w
    s.add(f'<g clip-path="url(#rx-lang)">{"".join(segs)}</g>')
    for i, (name, v) in enumerate(rows):
        y = top + 52 + i * 21
        s.add(f'<circle cx="{lx + 5}" cy="{y - 4.5}" r="4" fill="{p[colors.get(name, "overlay0")]}"/>')
        s.text(lx + 16, y, [(name, "text", 400)], size=12.5)
        s.text(W - PAD, y, [(f"{v / total * 100:.1f}%", "subtext0", 400)], size=12.5, anchor="end")
    return s.render()


def skyline(p: dict, data: dict) -> str:
    """等轴测 3D 贡献图：一天一根柱子，顶面用贡献等级色，两个侧面压暗出立体感。"""
    grid = data["grid"]
    u, v = (12.6, 3.3), (-6.6, 5.0)          # 周方向（右下缓坡）、星期方向（左下陡坡）
    gap, hmax = 0.84, 118                     # 柱子占格比例、最高柱高度
    peak_count, _, peak_date = max((c for wk in grid for c in wk), default=(1, 0, ""))
    peak = peak_count or 1
    ox = (W - (len(grid) * u[0] - 7 * v[0])) / 2 - 7 * v[0]
    oy = BAR + 40 + hmax
    h = round(oy + len(grid) * u[1] + 7 * v[1] + 40)
    s = Svg(h, "3D 贡献图：近一年每天的贡献量", p)
    s.chrome(3, f"{grid[0][0][2]} → {grid[-1][-1][2]}")
    lv = level_colors(p)
    side_l, side_r = (0.87, 0.94) if is_light(p) else (0.70, 0.85)

    cells = []
    for w, wk in enumerate(grid):
        for d, (count, level, _) in enumerate(wk):
            ax = ox + w * u[0] + d * v[0]
            ay = oy + w * u[1] + d * v[1]
            cells.append((ay + (u[1] + v[1]) * gap, ax, ay, count, level))
    cells.sort()                               # 屏幕上靠下的柱子后画，才能挡住后面的

    pt = lambda x, y: f"{x:.1f},{y:.1f}"
    for _, ax, ay, count, level in cells:
        top = lv[level]
        ht = 2.0 if count == 0 else 4 + (count / peak) ** 0.6 * hmax
        A = (ax, ay)
        B = (ax + u[0] * gap, ay + u[1] * gap)
        D = (ax + v[0] * gap, ay + v[1] * gap)
        C = (B[0] + v[0] * gap, B[1] + v[1] * gap)
        up = lambda q: (q[0], q[1] - ht)
        # 左侧面 D-C、右侧面 C-B（底边在轮廓下沿，所以可见）
        s.add(f'<polygon points="{pt(*D)} {pt(*C)} {pt(*up(C))} {pt(*up(D))}" fill="{shade(top, side_l)}"/>')
        s.add(f'<polygon points="{pt(*C)} {pt(*B)} {pt(*up(B))} {pt(*up(C))}" fill="{shade(top, side_r)}"/>')
        s.add(f'<polygon points="{pt(*up(A))} {pt(*up(B))} {pt(*up(C))} {pt(*up(D))}" fill="{top}"/>')

    # 左上角：与统计面板同款的小标题；右下角：图例
    s.add(f'<rect x="{PAD}" y="{BAR + 18}" width="3" height="12" rx="1.5" fill="{p["mauve"]}"/>')
    s.text(PAD + 10, BAR + 28.5, [("每日贡献 · 一天一根柱子", "subtext0", 400)], size=12)
    s.text(PAD, BAR + 50, [(f"最忙的一天 {peak_date} · {peak_count} 次", "overlay1", 400)], size=11.5)
    lx = W - PAD - 5 * 16 - tw("多", 11.5) - 6
    s.text(lx - 6, h - 22, [("少", "overlay1", 400)], size=11.5, anchor="end")
    for i, c in enumerate(lv):
        s.add(f'<rect x="{lx + i * 16:.1f}" y="{h - 32}" width="12" height="12" rx="2.5" fill="{c}"/>')
    s.text(lx + 5 * 16 + 2, h - 22, [("多", "overlay1", 400)], size=11.5)
    return s.render()


def projects(p: dict, data: dict) -> str:
    repos = data["featured"]
    cols, gap, ch = 2, 16, 116
    cw = (W - 2 * PAD - gap * (cols - 1)) / cols
    nrows = (len(repos) + cols - 1) // cols
    h = BAR + 26 + nrows * (ch + gap) - gap + 28
    s = Svg(h, "精选项目：" + "、".join(r["name"] for r in repos), p)
    s.chrome(4, f"~/code · {len(repos)} repos")
    colors = lang_colors(data["langs"])
    for i, r in enumerate(repos):
        x, y = PAD + (i % cols) * (cw + gap), BAR + 26 + (i // cols) * (ch + gap)
        s.add(f'<rect x="{x:.1f}" y="{y}" width="{cw:.1f}" height="{ch}" rx="10" fill="{p["mantle"]}" '
              f'stroke="{p["surface0"]}"/>')
        s.text(x + 18, y + 30, [("  ", "blue", 400), (r["name"], "text", 700)], size=15)
        s.text(x + cw - 18, y + 30, [(f" {r['stargazerCount']}    {r['forkCount']}", "overlay1", 400)],
               size=12.5, anchor="end")
        desc = CONFIG.get("descriptions", {}).get(r["name"]) or r["description"] or ""
        for j, line in enumerate(wrap(desc, 12.5, cw - 36, 2)):
            s.text(x + 18, y + 56 + j * 19, [(line, "subtext0", 400)], size=12.5)
        if lang := (r["primaryLanguage"] or {}).get("name"):
            s.add(f'<circle cx="{x + 23}" cy="{y + ch - 22}" r="4" fill="{p[colors.get(lang, "overlay0")]}"/>')
            s.text(x + 33, y + ch - 17.5, [(lang, "subtext0", 400)], size=12)
        s.text(x + cw - 18, y + ch - 17.5, [(f"更新于 {r['pushedAt'][:10]}", "overlay1", 400)], size=11.5, anchor="end")
    return s.render()


def snake(p: dict, data: dict, src: str) -> str:
    """把 snk 生成的贪吃蛇嵌进同款窗口，并把它的 CSS 变量换成 Catppuccin。"""
    lv = level_colors(p)
    colors = {"--cb": "#0000", "--cs": p["green"], "--ce": lv[0], "--c0": lv[0],
              "--c1": lv[1], "--c2": lv[2], "--c3": lv[3], "--c4": lv[4]}
    for k, v in colors.items():
        src = re.sub(rf"{k}:[^;}}]+", f"{k}:{v}", src)
    m = re.search(r'viewBox="([^"]+)"', src)
    if not m:
        raise ValueError("snk 输出里没有 viewBox")
    vb = [float(v) for v in m[1].split()]
    iw = W - 2 * PAD
    ih = iw * vb[3] / vb[2]
    h = BAR + 16 + ih + 8
    s = Svg(round(h), "贪吃蛇吃掉贡献图的动画", p)
    s.chrome(5, f"{data['year_total']:,} contributions eaten")
    src = re.sub(r"<svg\b[^>]*?>", lambda m: re.sub(r'\s(width|height)="[^"]*"', "", m[0])[:-1]
                 + f' x="{PAD}" y="{BAR + 16}" width="{iw}" height="{ih:.1f}">', src, count=1)
    s.add(re.sub(r"<\?xml[^>]*\?>", "", src))
    return s.render()


# ───────────────────────── 入口 ─────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--snake", help="snk 生成的 svg（任意配色，会被重新上色）")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = fetch(CONFIG["login"])
    snake_src = Path(args.snake).read_text(encoding="utf-8") if args.snake else None
    for mode, p in THEMES.items():
        (out / f"hero-{mode}.svg").write_text(hero(p, data), encoding="utf-8")
        (out / f"stats-{mode}.svg").write_text(stats(p, data), encoding="utf-8")
        (out / f"skyline-{mode}.svg").write_text(skyline(p, data), encoding="utf-8")
        (out / f"projects-{mode}.svg").write_text(projects(p, data), encoding="utf-8")
        if snake_src:
            (out / f"snake-{mode}.svg").write_text(snake(p, data, snake_src), encoding="utf-8")
    for f in sorted(out.glob("*.svg")):
        ET.parse(f)                     # 坏掉的 SVG 在 CI 里直接失败，别推上线
        print(f"{f.name:28} {f.stat().st_size / 1024:7.1f} KB")


if __name__ == "__main__":
    main()
