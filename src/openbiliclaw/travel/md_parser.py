"""旅游「最终定稿」Markdown → 结构化事实（纯解析器）。

设计契约（改动前请先读 ``docs/modules/travel.md`` §真值源）:

1. **纯函数、零副作用**：入参是 ``str``，不读文件、不连 sqlite、不访问网络。
   这样 pytest 可以直接喂一段 md 样本字符串，不必准备磁盘上的定稿文件。
2. **结构匹配而非字符串全等**：章节靠「层级 + 标题正则」定位，表格靠「表头列名」
   取值。理由是现实中的标题会长装饰尾巴（``★新增``、``（V2 更新）``、``⚠️``），
   一旦按全等匹配，作者改标题就会**静默空解析**——比报错更糟。
3. **缺章节必须报错**：找不到必需章节抛 :class:`TravelMarkdownError`，
   绝不返回空列表。空列表会让「同步了个寂寞」看起来像成功。

解析产物 dict 的 value 一律用列名 → 值的扁平 ``dict[str, Any]``，键名与
``data/travel.db`` 的列名一一对应（``trip_days`` / ``trip_flights`` / ...），
生成器脚本可以直接把某个 key 的缺席理解为「这一列不要碰」。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ALL_SECTIONS",
    "DEFAULT_SECTIONS",
    "Section",
    "TravelMarkdownError",
    "cell",
    "parse_checklist",
    "parse_days",
    "parse_document",
    "parse_expenses",
    "parse_flights",
    "parse_hotels",
    "parse_markdown_table",
    "parse_members",
    "parse_meta",
    "parse_outline",
    "require_cell",
    "strip_md",
]


class TravelMarkdownError(ValueError):
    """Markdown 结构不符合预期。

    刻意继承 ``ValueError`` 而不是返回空列表：调用方（生成器）必须显式处理，
    避免「标题改了 → 解析出 0 天 → 静默写入 0 行」这种最难查的失败模式。
    """


# ---------------------------------------------------------------------------
# 大纲（标题树）
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# 逻辑章节名 → 匹配标题用的正则。**不要写全等标题**，装饰后缀随时会变。
SECTION_PATTERNS: dict[str, str] = {
    "flights": r"航班信息",
    "days": r"每日(精确)?行程",
    "hotels": r"住宿安排",
    "members": r"同行成员",
    "expenses": r"费用明细",
    "reminders": r"重要提醒",
}

# 8 subgroups
_SUBSECTION_PATTERNS: dict[str, str] = {
    "group_fee": r"团费",
    "self_paid_hotel": r"自费住宿",
    "tickets": r"机票",
    "checklist": r"物品准备",
}

ALL_SECTIONS: tuple[str, ...] = (
    "trip",
    "days",
    "flights",
    "hotels",
    "members",
    "expenses",
    "checklist",
)

#: 默认参与 md → db 单向生成的表。
#: 不含 ``trip`` / ``expenses`` / ``checklist``，理由见 docs/modules/travel.md §同步范围：
#: * ``trip`` —— md 里没有 destination 等手填字段，只补不生成；
#: * ``expenses`` —— db 现有 ``(category, item)`` 键本身不唯一（CZ2312 两笔同 item），
#:   自动 upsert 会张冠李戴；
#: * ``checklist`` —— md 只有粗粒度一行，db 是细粒度 + owner/done 状态，直接生成会
#:   在人工清单旁制造一堆近似重复项。三者都可用 ``--include`` 显式开启。
DEFAULT_SECTIONS: tuple[str, ...] = ("days", "flights", "hotels", "members")

_AIRLINES: dict[str, str] = {
    "CA": "中国国际航空",
    "CZ": "中国南方航空",
    "HU": "海南航空",
    "MU": "中国东方航空",
    "ZH": "深圳航空",
    "MF": "厦门航空",
    "SC": "山东航空",
    "3U": "四川航空",
    "GS": "天津航空",
    "JD": "首都航空",
}


@dataclass
class Section:
    """一级大纲节点。

    ``lines`` 只含**直属**内容（已剔除子章节的行），所以 ``## 六、住宿安排``
    的 ``lines`` 不会被后面的 ``###`` 章节污染。
    """

    level: int
    title: str
    lines: tuple[str, ...] = ()
    children: tuple[Section, ...] = ()

    def children_matching(self, pattern: str) -> Iterator[Section]:
        """按正则迭代子章节（不改标题也不影响，前提是还认得出关键词）。"""
        for child in self.children:
            if re.search(pattern, child.title):
                yield child

    def child(self, pattern: str) -> Section | None:
        return next(self.children_matching(pattern), None)


@dataclass
class _Node:
    level: int
    title: str
    start: int
    end: int
    children: list[_Node] = field(default_factory=list)


def parse_outline(md: str) -> tuple[Section, ...]:
    """把 Markdown 解析成顶层标题树 ``tuple[Section, ...]``。"""
    lines = md.splitlines()
    nodes = _flat_nodes(lines)
    roots, _ = _assemble(nodes, 0, min((n.level for n in nodes), default=1))
    return tuple(_to_section(lines, n) for n in roots)


def _flat_nodes(lines: list[str]) -> list[_Node]:
    hits: list[tuple[int, str, int]] = []
    for idx, raw in enumerate(lines):
        matched = _HEADING_RE.match(raw)
        if matched:
            hits.append((len(matched.group(1)), matched.group(2).strip(), idx))

    nodes: list[_Node] = []
    for position, (level, title, start) in enumerate(hits):
        end = next(
            (hits[other][2] for other in range(position + 1, len(hits)) if hits[other][0] <= level),
            len(lines),
        )
        nodes.append(_Node(level=level, title=title, start=start, end=end))
    return nodes


def _assemble(nodes: list[_Node], pos: int, level: int) -> tuple[list[_Node], int]:
    """把扁平标题列表折成树。遇到跳级（## 后直接 ####）挂到上一个兄弟的孩子里。"""
    out: list[_Node] = []
    while pos < len(nodes) and nodes[pos].level >= level:
        if nodes[pos].level > level:
            deeper, pos = _assemble(nodes, pos, nodes[pos].level)
            if out:
                out[-1].children.extend(deeper)
            continue
        node = nodes[pos]
        children, nxt = _assemble(nodes, pos + 1, node.level + 1)
        node.children = children
        out.append(node)
        pos = nxt
    return out, pos


def _to_section(lines: list[str], node: _Node) -> Section:
    own: list[str] = []
    cursor = node.start + 1
    for child in node.children:
        own.extend(lines[cursor : child.start])
        cursor = child.end
    own.extend(lines[cursor : node.end])
    return Section(
        level=node.level,
        title=node.title,
        lines=tuple(own),
        children=tuple(_to_section(lines, c) for c in node.children),
    )


def find_section(roots: Iterable[Section], pattern: str, *, level: int = 2) -> Section:
    """按正则找章节；找不到抛 :class:`TravelMarkdownError`（不返回 None）。"""
    for candidate in _walk(roots):
        if candidate.level == level and re.search(pattern, candidate.title):
            return candidate
    raise TravelMarkdownError(f"找不到匹配 {pattern!r} 的 {level} 级章节")


def _walk(sections: Iterable[Section]) -> Iterator[Section]:
    for item in sections:
        yield item
        yield from _walk(item.children)


# ---------------------------------------------------------------------------
# 行 / 表格 / 标量 的小工具
# ---------------------------------------------------------------------------


def strip_md(text: str) -> str:
    """去掉粗体、行内代码、链接语法和首尾空白，保留纯文本。"""
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text.strip())
    return cleaned.replace("**", "").replace("`", "").strip()


def _norm(text: str) -> str:
    """表头归一化：去 md 标记 + 去所有空白（`序号` / `序 号` 应视为同一列）。"""
    return re.sub(r"\s+", "", strip_md(text))


def parse_markdown_table(lines: Iterable[str]) -> list[dict[str, str]]:
    """解析一段 Markdown 表格。

    返回按**表头列名**索引的行 dict；跳过分隔行（``|---|---|``）与空行。
    表头为空时会把原 row 拼回去，而不是抛错（有些表格用错位行做备注）。
    """
    rows: list[list[str]] = []
    for raw in lines:
        line = raw.strip()
        if not line.startswith("|"):
            if rows:
                break  # 表格结束
            continue
        rows.append([c.strip() for c in line.strip("|").split("|")])

    usable = [r for r in rows if not _is_separator(r)]
    if len(usable) < 2:
        return []

    header = [_norm(c) for c in usable[0]]
    parsed: list[dict[str, str]] = []
    for row in usable[1:]:
        cells = list(row) + [""] * (len(header) - len(row))
        item: dict[str, str] = {}
        for name, value in zip(header, cells, strict=False):
            if name and value:
                item[name] = strip_md(value)
        if item:
            parsed.append(item)
    return parsed


def _is_separator(cells: list[str]) -> bool:
    stripped = [c.strip() for c in cells if c.strip()]
    if not stripped:
        return True
    return all(re.fullmatch(r":?-{2,}:?", c) for c in stripped)


def cell(row: dict[str, str], *names: str) -> str:
    """按表头别名取值，取不到返回空串（判定空用 ``require_cell``）。"""
    for name in names:
        wanted = _norm(name)
        for key, value in row.items():
            if _norm(key) == wanted:
                return value
    return ""


def require_cell(row: dict[str, str], *names: str) -> str:
    value = cell(row, *names)
    if not value:
        raise TravelMarkdownError(f"表格缺少列 {'/'.join(names)}：{row!r}")
    return value


_MONEY_RE = re.compile(r"^¥?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*$")


def parse_amount(text: str) -> float | None:
    """解析金额。只在**单元格整体就是一个金额**时才认。

    ``¥2,118`` → 2118.0；``含在去程¥6600内`` → ``None``（它表示「已含」，
    不是这笔的价格）；``-`` / 空 → ``None``。

    第二种是关键反例：贪心匹配会读出 6600，把往返已含的 HU7446 写成 6600，
    费用汇总直接翻倍。所以这里用 ``fullmatch`` 而不是 ``search``。
    """
    matched = _MONEY_RE.fullmatch(re.sub(r",", "", strip_md(text)))
    if not matched:
        return None
    return float(matched.group(1).replace(",", ""))


_DATE_RE = re.compile(r"(?:(\d{4})年)?(\d{1,2})[月\-/](\d{1,2})日?")


def _parse_md_date(text: str, base_year: int, base_month: int) -> str | None:
    """把 ``10-02`` / ``10月2日`` / ``2026年10月2日`` 归一成 ``YYYY-MM-DD``。

    年份缺失时用 *base_year*；若月份小于 *base_month* 视为跨年 +1
    （跨年团是真实存在的场景，不想在那一刻才发现）。
    """
    matched = _DATE_RE.search(strip_md(text))
    if not matched:
        return None
    year = int(matched.group(1)) if matched.group(1) else base_year
    month, day = int(matched.group(2)), int(matched.group(3))
    if matched.group(1) is None and month < base_month:
        year += 1
    return f"{year:04d}-{month:02d}-{day:02d}"


_COUNT_RE = re.compile(r"(\d+)\s*人")
_AGE_RE = re.compile(r"(\d{1,3})\s*岁")


def _parse_count(*texts: str) -> int | None:
    """从 ``（4人）`` / ``11人`` 里取人数。"""
    for text in texts:
        matched = _COUNT_RE.search(strip_md(text))
        if matched:
            return int(matched.group(1))
    return None


def _split_people(text: str) -> list[str]:
    """``周贤英、童先海、童言、童力（4人）`` → 4 个姓名（去掉人数后缀）。

    ⚠️ 分隔符**只在顿号/逗号/斜杠上切**，不能切「和」——``田嘉和`` 是一个人的名字
    （前六字符里有「和」），早期版本用 ``[、,，/]|和`` 把ta割成了 ``田嘉``。
    """
    cleaned = re.sub(r"[（(][^）)]*[）)]\s*$", "", strip_md(text))
    return [p.strip() for p in re.split(r"[、,，/]", cleaned) if p.strip()]


# ---------------------------------------------------------------------------
# 各章节解析
# ---------------------------------------------------------------------------


def parse_meta(md: str) -> dict[str, Any]:
    """解析文首的 ``>`` 元信息块（出行时间 / 人数 / 用车）与 H1 标题。"""
    title = ""
    for line in md.splitlines():
        matched = _HEADING_RE.match(line)
        if matched and len(matched.group(1)) == 1:
            title = _clean_title(matched.group(2))
            break

    start_date = end_date = None
    people_count: int | None = None
    vehicle = ""
    quote = "\n".join(line for line in md.splitlines() if line.lstrip().startswith(">"))

    period = re.search(
        r"出行时间[:：]\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*[-–—~]\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日",
        quote,
    )
    base_year = 1970
    base_month = 1
    if period:
        base_year = int(period.group(1))
        base_month = int(period.group(2))
        start_date = f"{base_year:04d}-{base_month:02d}-{int(period.group(3)):02d}"
        end_year = int(period.group(4)) if period.group(4) else base_year
        end_month = int(period.group(5))
        if period.group(4) is None and end_month < base_month:
            end_year += 1
        end_date = f"{end_year:04d}-{end_month:02d}-{int(period.group(6)):02d}"

    people = re.search(r"人数[:：]\s*(\d+)\s*人", quote)
    if people:
        people_count = int(people.group(1))
    vehicle_match = re.search(r"用车[:：]\s*(.+)", quote)
    if vehicle_match:
        vehicle = strip_md(vehicle_match.group(1))

    return {
        "title": title,
        "start_date": start_date,
        "end_date": end_date,
        "people_count": people_count,
        "vehicle": vehicle,
        "base_year": base_year,
        "base_month": base_month,
    }


def _clean_title(raw: str) -> str:
    """去掉 H1 里的版本说明尾巴，留住正题。

    ``# 国庆北疆金秋8日游 — 精确时间游玩计划（最终定稿 · V2 含白哈巴）``
    → ``国庆北疆金秋8日游（最终定稿）``：破折号后面是文档种类，不入 title；
    「最终定稿」这个版本标记则保留（db 里原本就叫这个名字）。
    """
    head = re.split(r"\s+[—–-]\s+", strip_md(raw), maxsplit=1)[0].strip()
    if "最终定稿" in raw and "最终定稿" not in head:
        head = f"{head}（最终定稿）"
    return head


_DAY_HEADING_RE = re.compile(r"^Day\s*(\d+)\s*[（(]([^）)]+)[）)]\s*(.*)$")
_KV_LINE_RE = re.compile(r"^\*\*(.+?)\*\*\s*[:：]\s*(.+)$")
_DAY_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "住宿": ("accommodation",),
    "餐饮": ("meals",),
    "用餐": ("meals",),
    "车程": ("transport",),
    "交通": ("transport",),
    "温馨提示": ("notes",),
    "提示": ("notes",),
    "住宿安排": ("accommodation",),
}


def parse_days(md: str) -> list[dict[str, Any]]:
    """解析「三、每日精确行程」的 8 个 ``### Day N`` 小节。

    ``date`` / ``title`` 来自标题（**结构性字段，始终以 md 为准**）；
    ``住宿/餐饮/车程/温馨提示`` 来自小节末尾的加粗行；
    ``description`` 由分钟级表格自动摘要 —— 生成器默认**只在原值为空时**写入，
    以免覆盖人工精炼文案（``--force-description`` 可强制）。
    """
    meta = parse_meta(md)
    roots = parse_outline(md)
    section = find_section(roots, SECTION_PATTERNS["days"], level=2)

    days: list[dict[str, Any]] = []
    for child in section.children_matching(r"^Day\s*\d+"):
        matched = _DAY_HEADING_RE.match(child.title.strip())
        if not matched:
            raise TravelMarkdownError(f"无法识别的 Day 标题：{child.title!r}")
        day_number = int(matched.group(1))
        date = _parse_md_date(matched.group(2), meta["base_year"], meta["base_month"])
        if date is None:
            raise TravelMarkdownError(f"Day {day_number} 标题里没有可解析的日期：{child.title!r}")

        row: dict[str, Any] = {
            "day_number": day_number,
            "date": date,
            "title": strip_md(matched.group(3)) or None,
        }
        timeline: list[str] = []
        for line in child.lines:
            kv = _KV_LINE_RE.match(line.strip())
            if kv:
                aliases = _DAY_FIELD_ALIASES.get(strip_md(kv.group(1)))
                if aliases:
                    row[aliases[0]] = strip_md(kv.group(2))
        for tl in parse_markdown_table(child.lines):
            what = cell(tl, "行程", "安排", "内容")
            when = cell(tl, "时间", "时刻")
            if what:
                timeline.append(f"{when} {what}".strip())
        row["description"] = _summarize_timeline(timeline)
        days.append(row)

    if not days:
        raise TravelMarkdownError("「每日行程」章节下没有任何 Day 小节")
    return days


def _summarize_timeline(entries: list[str]) -> str:
    """把分钟级表格压成一句话摘要。

    不做 NLP，只是去重 + 截断：它的定位是「新插入的 Day 不至于留空」，
    不是要与人工精炼文案打平。超过 300 字就砍，避免把整张时刻表塞进
    ``description``。
    """
    seen: list[str] = []
    for entry in entries:
        key = re.sub(r"^\d{1,2}:\d{2}[-–—]?\d{0,2}:?\d{0,2}\s*", "", entry)
        if key and key not in seen:
            seen.append(key)
    text = "、".join(seen)
    return text[:300]


def parse_flights(md: str) -> list[dict[str, Any]]:
    """解析「一、航班信息」去程/返程两个子表（表头列数不同，按列名取）。"""
    meta = parse_meta(md)
    roots = parse_outline(md)
    section = find_section(roots, SECTION_PATTERNS["flights"], level=2)

    flights: list[dict[str, Any]] = []
    for child in section.children:
        rows = parse_markdown_table(child.lines)
        if not rows:
            continue
        title = child.title
        default_md_date = strip_md(title)
        for raw_row in rows:
            flight_no = cell(raw_row, "航班", "航班号")
            if not flight_no or flight_no.upper().startswith("合计"):
                continue
            route = require_cell(raw_row, "航线", "航段")
            departure_city, arrival_city = _split_route(route)
            day = cell(raw_row, "日期") or default_md_date
            date = _parse_md_date(day, meta["base_year"], meta["base_month"])
            if date is None:
                raise TravelMarkdownError(f"航班 {flight_no} 缺少可解析日期：{raw_row!r}")

            depart_local, arrive_local, plus_day = _split_clock(require_cell(raw_row, "时间", "时刻"))
            passengers_raw = cell(raw_row, "乘机人", "乘客")
            people = _split_people(passengers_raw)
            count = _parse_count(passengers_raw, cell(raw_row, "人数")) or len(people)

            flights.append(
                {
                    "flight_type": _flight_type(title, route),
                    "flight_no": flight_no,
                    "airline": _AIRLINES.get(flight_no[:2].upper()),
                    "departure_city": departure_city,
                    "arrival_city": arrival_city,
                    "departure_time": f"{date} {depart_local}",
                    "arrival_time": _shift_days(date, arrive_local, plus_day, depart_local),
                    "passengers": ",".join(people),
                    "passenger_count": count,
                    "price": parse_amount(cell(raw_row, "票价", "价格", "费用")),
                    "order_no": None,
                }
            )

    if not flights:
        raise TravelMarkdownError("「航班信息」章节解析出 0 条航班")
    return flights


def _split_route(route: str) -> tuple[str, str]:
    cleaned = strip_md(route)
    for sep in ("→", "->", "➔", "至"):
        if sep in cleaned:
            left, _, right = cleaned.partition(sep)
            return left.strip(), right.strip()
    raise TravelMarkdownError(f"航线缺少出发/到达分隔：{route!r}")


_TIME_RANGE_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*[-–—~]\s*(\d{1,2}):(\d{2})\s*(\+\s*1)?$")


def _split_clock(text: str) -> tuple[str, str, bool]:
    """``21:00-01:00+1`` → ``("21:00", "01:00", True)``。"""
    matched = _TIME_RANGE_RE.match(strip_md(text))
    if not matched:
        raise TravelMarkdownError(f"无法解析的时刻区间：{text!r}")
    depart = f"{int(matched.group(1)):02d}:{matched.group(2)}"
    arrive = f"{int(matched.group(3)):02d}:{matched.group(4)}"
    return depart, arrive, bool(matched.group(5))


def _shift_days(date: str, clock: str, plus_day: bool, depart_clock: str) -> str:
    """到达时间：显式 +1 或跨零点（到达早于出发）都算次日。"""
    from datetime import date as _date
    from datetime import timedelta

    cross_midnight = clock <= depart_clock
    year, month, day = (int(p) for p in date.split("-"))
    base = _date(year, month, day)
    if plus_day or cross_midnight:
        base = base + timedelta(days=1)
    return f"{base.isoformat()} {clock}"


def _flight_type(title: str, route: str) -> str:
    if re.search(r"去程", title):
        return "去程"
    if re.search(r"返程", title):
        return "返程"
    # 子标题没写去/返（只写日期）时按航线方向兜底：飞向乌鲁木齐=去程
    _, arrival = _split_route(route)
    return "去程" if arrival == "乌鲁木齐" else "返程"


def parse_hotels(md: str) -> list[dict[str, Any]]:
    """解析「六、住宿安排」表（日期 / 晚次 / 城市 / 酒店 / 等级 / 人数）。"""
    meta = parse_meta(md)
    roots = parse_outline(md)
    section = find_section(roots, SECTION_PATTERNS["hotels"], level=2)

    hotels: list[dict[str, Any]] = []
    for raw_row in parse_markdown_table(section.lines):
        night = cell(raw_row, "晚次", "第几晚")
        number = re.search(r"(\d+)", night)
        name = cell(raw_row, "酒店", "住宿")
        if not number or not name:
            continue
        date = _parse_md_date(cell(raw_row, "日期"), meta["base_year"], meta["base_month"])
        rating = strip_md(cell(raw_row, "等级"))
        guests = _parse_count(cell(raw_row, "人数"))
        hotels.append(
            {
                "day_number": int(number.group(1)),
                "date": date,
                "city": strip_md(cell(raw_row, "城市")) or None,
                "hotel_name": name,
                "star_rating": rating or None,
                # md 里「自费」只写在等级单元格 → 用它反推是否含在团费里
                "included_in_tour": 0 if "自费" in rating else 1,
                "guest_count": guests,
            }
        )

    if not hotels:
        raise TravelMarkdownError("「住宿安排」章节解析出 0 条住宿")
    return hotels


def parse_members(md: str) -> list[dict[str, Any]]:
    """解析「七、同行成员」表（含从「（14岁）」里抽年龄）。"""
    roots = parse_outline(md)
    section = find_section(roots, SECTION_PATTERNS["members"], level=2)

    members: list[dict[str, Any]] = []
    for raw_row in parse_markdown_table(section.lines):
        name = cell(raw_row, "姓名", "成员")
        if not name:
            continue
        relation = strip_md(cell(raw_row, "关系"))
        notes = strip_md(cell(raw_row, "备注")) or None
        age_match = _AGE_RE.search(f"{relation} {notes or ''}")
        members.append(
            {
                "name": name,
                "relation": relation or None,
                "age": int(age_match.group(1)) if age_match else None,
                "notes": notes,
            }
        )

    if not members:
        raise TravelMarkdownError("「同行成员」章节解析出 0 位成员")
    return members


def parse_expenses(md: str) -> list[dict[str, Any]]:
    """解析「五、费用明细」。

    ⚠️ 仅供 ``--include expenses`` 使用 —— db 现有 ``(category, item)``
    并不唯一（CZ2312 的「爸爸 1 人」「三嬢+三姑爷 2 人」两笔 item 同名），
    盲目 upsert 会把其中一笔的钱改到另一笔头上。
    """
    roots = parse_outline(md)
    section = find_section(roots, SECTION_PATTERNS["expenses"], level=2)

    expenses: list[dict[str, Any]] = []
    for child in section.children:
        category = strip_md(re.sub(r"[（(].*?[）)]", "", child.title))
        if re.search(_SUBSECTION_PATTERNS["group_fee"], child.title):
            expenses.extend(_parse_group_fee(category, child.lines))
        elif re.search(_SUBSECTION_PATTERNS["self_paid_hotel"], child.title):
            expenses.extend(_parse_self_paid(category, child.lines))
        elif re.search(_SUBSECTION_PATTERNS["tickets"], child.title):
            expenses.extend(_parse_ticket_rows(category, child.lines))

    if not expenses:
        raise TravelMarkdownError("「费用明细」章节解析出 0 笔费用")
    return expenses


def _parse_group_fee(category: str, lines: Iterable[str]) -> list[dict[str, Any]]:
    for line in lines:
        text = strip_md(line.lstrip("- ").strip())
        if text.startswith("**团队总价") or text.startswith("团队总价"):
            amount = _inline_amount(text)
            item = strip_md(text.split("：")[0].split(":")[0]) or "团费"
            return [{"category": category, "item": item, "amount": amount, "detail": text}]
    return []


def _inline_amount(text: str) -> float | None:
    """从段落里挖出紧跟 ¥ 的数字。

    这里是 ``search`` 不是 ``fullmatch``：费用段的金额藏在
    ``- **团队总价：¥32,600**（订单号…）`` 这种句子里，不可能是纯金额单元格。
    """
    matched = re.search(r"¥\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text)
    return float(matched.group(1).replace(",", "")) if matched else None


def _parse_self_paid(category: str, lines: Iterable[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in lines:
        text = strip_md(line.strip().lstrip("- ").strip())
        if not text or text.startswith("合计") or "合计" in text[:3]:
            continue  # 小计行，落库会重复计数
        if "：" not in text and ":" not in text:
            continue
        item = strip_md(re.split(r"[:：]", text, maxsplit=1)[0])
        amount = _inline_amount(text)
        if item and amount is not None:
            out.append({"category": category, "item": item, "amount": amount, "detail": text})
    return out


def _parse_ticket_rows(category: str, lines: Iterable[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw_row in parse_markdown_table(lines):
        route = cell(raw_row, "航线", "航段")
        flight_no = cell(raw_row, "航班", "航班号")
        people = cell(raw_row, "乘机人")
        amount = parse_amount(cell(raw_row, "费用", "票价", "价格", "金额"))
        if not route and not flight_no:
            continue
        item = " ".join(p for p in (strip_md(route), flight_no) if p).strip()
        if not item or item.startswith("合计") or "合计" in item:
            continue
        detail = " · ".join(
            p for p in (f"{strip_md(people)}{cell(raw_row, '人数')}", strip_md(cell(raw_row, "备注"))) if p
        )
        out.append({"category": category, "item": item, "amount": amount, "detail": detail or None})
    return out


def parse_checklist(md: str) -> list[dict[str, Any]]:
    """解析「八、重要提醒 → 物品准备」列表。

    md 一行是一个类别（``- **证件**：身份证、医保卡、边防证``），db 是细粒度
    逐项 + ``owner``/``done``。生成器对这张表**只插不改**，插之前想清楚会不会
    在人工清单旁制造近似重复项（默认不同步，需 ``--include checklist``）。
    """
    roots = parse_outline(md)
    section = find_section(roots, SECTION_PATTERNS["reminders"], level=2)
    child = section.child(_SUBSECTION_PATTERNS["checklist"])
    if child is None:
        raise TravelMarkdownError("「重要提醒」下找不到「物品准备」小节")

    items: list[dict[str, Any]] = []
    for line in child.lines:
        text = line.strip()
        if not text.startswith("- "):
            continue
        body = strip_md(text[2:])
        if "：" not in body and ":" not in body:
            continue
        category = strip_md(re.split(r"[:：]", body, maxsplit=1)[0])
        tail = re.split(r"[:：]", body, maxsplit=1)[1]
        for piece in re.split(r"[、,，]", tail):
            item = strip_md(re.sub(r"[（(][^）)]*[）)]\s*$", "", piece))
            if item:
                items.append({"category": category, "item": item})
    if not items:
        raise TravelMarkdownError("「物品准备」解析出 0 条清单")
    return items


def parse_document(md: str, sections: Iterable[str] = DEFAULT_SECTIONS) -> dict[str, Any]:
    """一次性解析出若干张表的事实。

    返回 ``{"meta": {...}, "trip": {...}, "days": [...], ...}``。
    未知章节名直接抛错——拼错 section 名时想立刻知道，而不是拿到空结果。
    """
    wanted = tuple(sections)
    unknown = [name for name in wanted if name not in ALL_SECTIONS]
    if unknown:
        raise TravelMarkdownError(f"未知章节：{unknown}，可用：{list(ALL_SECTIONS)}")

    meta = parse_meta(md)
    result: dict[str, Any] = {"meta": meta}
    if "trip" in wanted:
        result["trip"] = {
            "title": meta["title"],
            "start_date": meta["start_date"],
            "end_date": meta["end_date"],
            "people_count": meta["people_count"],
            "notes": f"用车：{meta['vehicle']}" if meta["vehicle"] else None,
        }
    if "days" in wanted:
        result["days"] = parse_days(md)
    if "flights" in wanted:
        result["flights"] = parse_flights(md)
    if "hotels" in wanted:
        result["hotels"] = parse_hotels(md)
    if "members" in wanted:
        result["members"] = parse_members(md)
    if "expenses" in wanted:
        result["expenses"] = parse_expenses(md)
    if "checklist" in wanted:
        result["checklist"] = parse_checklist(md)
    return result
