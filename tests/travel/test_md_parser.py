"""``travel/md_parser.py`` 的单元测试。

口径（与项目其它测试一致）：

* **喂样本字符串，不喂磁盘上的真文件** —— 解析器是纯函数，这是它的设计前提；
* **每个断言都问一句「改成错的会红吗」**，写不出反例的断言就不写；
* 反例集中在最后一组，命名统一 ``test_loud_*`` / ``test_negative_*``，
  它们守的是「宁可报错，不要静默空解析」这条底线。
"""

from __future__ import annotations

import pytest

from openbiliclaw.travel.md_parser import (
    TravelMarkdownError,
    parse_amount,
    parse_checklist,
    parse_days,
    parse_document,
    parse_expenses,
    parse_flights,
    parse_hotels,
    parse_members,
    parse_meta,
    parse_outline,
)

SAMPLE_MD = """# 测试北疆线路 — 精确时间游玩计划（最终定稿 · V3 含白哈巴）

> 出行时间：2026年10月2日 - 10月3日（2天1晚）
> 人数：3人（2大1小）
> 用车：14座中巴
> 更新时间：2026-09-15

---

## 一、航班信息（已出票）

### 去程（10月2日）

| 航班 | 航线 | 时间 | 乘机人 | 票价 |
|------|------|------|--------|------|
| HU7851 | 深圳→乌鲁木齐 | 06:40-12:30 | 张三（1人） | ¥2,118 |
| HU7832 | 重庆→乌鲁木齐 | 08:45-12:45 | 李四、田嘉和、赵六（3人） | ¥3,544 |

### 返程

| 日期 | 航班 | 航线 | 时间 | 乘机人 | 票价 |
|------|------|------|------|--------|------|
| 10-03 | HU7446 | 乌鲁木齐→太原 | 21:00-01:00+1 | 张三、李四（2人） | 含在去程¥6600内 |

## 三、每日精确行程

### Day 1（10月2日 周五）集合日

| 时间 | 行程 | 备注 |
|------|------|------|
| 06:40 | 张三从深圳起飞 | 宝安机场T3 |

**住宿**：测试酒店（5钻）
**餐饮**：午餐自理
**温馨提示**：第一天以适应为主。

### Day 2（10月3日 周六）返程日 ★新增

| 时间 | 行程 | 备注 |
|------|------|------|
| 10:00 | 国际大巴扎 | |

**住宿**：无
**餐饮**：早餐（酒店）
**车程**：约120km/2h

## 五、费用明细

### 团费（实际成交价）
- **团队总价：¥32,600**（订单号ABC123）
- 比行程表报价¥44,592优惠¥11,992

### 自费住宿（禾木1晚）
- 别墅：¥4,078
- 亲子房：¥2,428
- **合计：¥6,506**

### 机票（全部已出票）
| 航线 | 航班 | 人数 | 乘机人 | 费用 |
|------|------|------|--------|------|
| 深圳→乌鲁木齐（10-02） | HU7851 | 1人 | 张三 | ¥2,118 |
| **机票合计** | | **3人** | | **¥5,662** |

## 六、住宿安排（V2 更新）

| 日期 | 晚次 | 城市 | 酒店 | 等级 | 人数 |
|------|------|------|------|------|------|
| 10-02 | 第1晚 | 乌鲁木齐 | 亚朵S | 5钻 | 3人 |
| 10-03 | 第2晚 | 禾木 | 诺瓦木屋 | 特色木屋（**自费¥6,506**） | 3人 |

## 七、同行成员（3人）

| # | 姓名 | 关系 | 备注 |
|---|------|------|------|
| 1 | 张三 | 本人 | 组织者 |
| 2 | 李四 | 配偶 | |
| 3 | 王五 | 孩子（8岁） | 儿童票 |

## 八、重要提醒

### 物品准备
- **证件**：身份证（必带）、医保卡
- **衣物**：羽绒服、帽子
"""


# ---------------------------------------------------------------------------
# 元信息与大纲
# ---------------------------------------------------------------------------


def test_parse_meta_reads_quote_block() -> None:
    meta = parse_meta(SAMPLE_MD)
    assert meta["start_date"] == "2026-10-02"
    assert meta["end_date"] == "2026-10-03"
    assert meta["people_count"] == 3
    assert meta["vehicle"] == "14座中巴"
    # H1 的破折号后面是文档种类，不属于行程名
    assert meta["title"] == "测试北疆线路（最终定稿）"


def test_parse_outline_keeps_own_lines_only() -> None:
    """``## 六、住宿安排`` 的直属行不能被后面的 ``###`` 章节污染。

    鉴别力：若 ``Section.lines`` 里混进子章节，表格解析会把别的表的行也算进来，
    行数会 > 4。这是整棵树最容易写错的地方（最初版本就是天真地「到下一个 ## 为止」）。
    """
    roots = parse_outline(SAMPLE_MD)
    # H1 是根，七个二级章节挂在它下面
    assert len(roots) == 1 and roots[0].level == 1
    hotel = next(s for s in roots[0].children if "住宿" in s.title)
    assert hotel.children == ()
    day_sections = next(s for s in roots[0].children if "每日" in s.title)
    assert [s.title for s in day_sections.children] == [
        "Day 1（10月2日 周五）集合日",
        "Day 2（10月3日 周六）返程日 ★新增",
    ]
    table_rows = [line for line in hotel.lines if line.strip().startswith("|")]
    assert len(table_rows) == 4  # 表头 + 分隔 + 2 行数据


def test_heading_decoration_does_not_break_section_lookup() -> None:
    """标题加装饰后缀（V2 更新 / ⚠️ / 换名字）仍要找得到章节。"""
    renamed = SAMPLE_MD.replace("## 六、住宿安排（V2 更新）", "## 六、住宿安排（V9 修订）⚠️")
    assert len(parse_hotels(renamed)) == 2


# ---------------------------------------------------------------------------
# 每日行程
# ---------------------------------------------------------------------------


def test_parse_days_builds_one_row_per_day_with_date_and_title() -> None:
    days = parse_days(SAMPLE_MD)
    assert [d["day_number"] for d in days] == [1, 2]
    assert days[0]["date"] == "2026-10-02"
    assert days[0]["title"] == "集合日"
    assert days[1]["date"] == "2026-10-03"
    # Day 2 标题里的 ★新增 是 md 的一部分，保留原样（不去猜作者意图）
    assert days[1]["title"] == "返程日 ★新增"


def test_parse_days_maps_bullet_kv_lines_to_columns() -> None:
    first, second = parse_days(SAMPLE_MD)
    assert first["accommodation"] == "测试酒店（5钻）"
    assert first["meals"] == "午餐自理"
    assert first["notes"] == "第一天以适应为主。"
    assert second["transport"] == "约120km/2h"
    assert second["accommodation"] == "无"


# ---------------------------------------------------------------------------
# 航班
# ---------------------------------------------------------------------------


def test_parse_flights_handles_two_header_shapes() -> None:
    """去程表没有「日期」列、返程表有 —— 按列名取值而不是按下标。"""
    flights = parse_flights(SAMPLE_MD)
    assert len(flights) == 3
    assert flights[0]["flight_type"] == "去程"
    assert flights[2]["flight_type"] == "返程"
    assert flights[0]["departure_city"] == "深圳"
    assert flights[0]["arrival_city"] == "乌鲁木齐"
    assert flights[0]["departure_time"] == "2026-10-02 06:40"
    assert flights[0]["arrival_time"] == "2026-10-02 12:30"
    assert flights[0]["airline"] == "海南航空"


def test_flight_arrival_crossing_midnight_lands_on_next_day() -> None:
    """``21:00-01:00+1`` 的落地日期必须 +1 天。

    鉴别力：不处理 → 到达时间变成出发当天 01:00，比起飞还早，
    任何按时间排序的前端都会把返程航班排到最前。
    """
    returning = parse_flights(SAMPLE_MD)[2]
    assert returning["departure_time"] == "2026-10-03 21:00"
    assert returning["arrival_time"] == "2026-10-04 01:00"


def test_passenger_names_are_not_split_on_the_he_character() -> None:
    """``李四、田嘉和、赵六`` 里的「和」是人名的一部分，不能当分隔符。"""
    outbound = parse_flights(SAMPLE_MD)[1]
    assert outbound["passengers"] == "李四,田嘉和,赵六"
    assert outbound["passenger_count"] == 3


def test_flight_price_that_is_included_elsewhere_is_not_a_price() -> None:
    """``含在去程¥6600内`` 表示「这笔不另收钱」，不是 6600。

    鉴别力：贪心匹配会把这个单元格读成 6600，机票总额凭空多一倍。
    """
    assert parse_flights(SAMPLE_MD)[2]["price"] is None
    assert parse_amount("¥2,118") == 2118.0
    assert parse_amount("含在去程¥6600内") is None
    assert parse_amount("-") is None


# ---------------------------------------------------------------------------
# 住宿 / 成员
# ---------------------------------------------------------------------------


def test_parse_hotels_derives_night_number_and_self_paid_flag() -> None:
    hotels = parse_hotels(SAMPLE_MD)
    assert [h["day_number"] for h in hotels] == [1, 2]
    assert hotels[0]["city"] == "乌鲁木齐"
    assert hotels[0]["guest_count"] == 3
    assert hotels[0]["included_in_tour"] == 1
    # 「自费」只写在等级单元格里 → 用它反推
    assert hotels[1]["included_in_tour"] == 0


def test_parse_members_extracts_age_from_relation_cell() -> None:
    members = parse_members(SAMPLE_MD)
    assert [m["name"] for m in members] == ["张三", "李四", "王五"]
    assert members[2]["age"] == 8
    assert members[0]["age"] is None
    assert members[1]["notes"] is None
    # id_card 不在 md 里，解析器**不能**凭空产出这个键（生成器据此判断「不碰」）
    assert "id_card" not in members[0]


# ---------------------------------------------------------------------------
# 费用 / 清单
# ---------------------------------------------------------------------------


def test_parse_expenses_skips_subtotal_rows() -> None:
    """``**合计：¥6,506**`` 是小计，落库会重复计数。"""
    expenses = parse_expenses(SAMPLE_MD)
    assert [(e["category"], e["item"], e["amount"]) for e in expenses] == [
        ("团费", "团队总价", 32600.0),
        ("自费住宿", "别墅", 4078.0),
        ("自费住宿", "亲子房", 2428.0),
        ("机票", "深圳→乌鲁木齐（10-02） HU7851", 2118.0),
    ]


def test_parse_checklist_expands_one_md_line_into_items() -> None:
    items = parse_checklist(SAMPLE_MD)
    assert [(i["category"], i["item"]) for i in items] == [
        ("证件", "身份证"),
        ("证件", "医保卡"),
        ("衣物", "羽绒服"),
        ("衣物", "帽子"),
    ]
    # 解析产物里绝不能出现 owner/done —— 那两列只属于 db
    assert all("done" not in i and "owner" not in i for i in items)


def test_parse_document_respects_requested_sections() -> None:
    document = parse_document(SAMPLE_MD, ("hotels",))
    assert set(document) == {"meta", "hotels"}
    assert len(document["hotels"]) == 2


# ---------------------------------------------------------------------------
# 反例：宁可报错，不要静默空解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("section_missing_md", "parser"),
    [
        (SAMPLE_MD.replace("## 六、住宿安排（V2 更新）", "## 六、住宿"), parse_hotels),
        (SAMPLE_MD.replace("## 七、同行成员（3人）", "## 七、同行的人"), parse_members),
        (SAMPLE_MD.replace("## 一、航班信息（已出票）", "## 一、机票信息"), parse_flights),
    ],
)
def test_loud_when_section_title_changes(section_missing_md: str, parser: object) -> None:
    """章节改名后必须报错。

    鉴别力：这是在防最坏的一类失败 —— 标题改了 → 静默解析出 0 行 →
    生成器以为「没有要同步的」→ 页面继续显示旧数据，且没有任何人知道。
    """
    with pytest.raises(TravelMarkdownError):
        parser(section_missing_md)  # type: ignore[operator]


def test_loud_when_day_heading_has_no_date() -> None:
    """标题有括号但没有日期 —— 要么改成能解析的，要么报错，不许生成 date=None 的行。"""
    broken = SAMPLE_MD.replace("### Day 1（10月2日 周五）集合日", "### Day 1（周五）集合日")
    with pytest.raises(TravelMarkdownError, match="日期"):
        parse_days(broken)


def test_loud_on_unknown_section_name() -> None:
    with pytest.raises(TravelMarkdownError, match="未知章节"):
        parse_document(SAMPLE_MD, ("membersss",))


def test_loud_when_checklist_subsection_gone() -> None:
    broken = SAMPLE_MD.replace("### 物品准备", "### 带上这些东西")
    with pytest.raises(TravelMarkdownError, match="物品准备"):
        parse_checklist(broken)


def test_loud_when_malformed_time_range() -> None:
    broken = SAMPLE_MD.replace("| 06:40-12:30 |", "| 0640~1230 |")
    with pytest.raises(TravelMarkdownError, match="时刻"):
        parse_flights(broken)
