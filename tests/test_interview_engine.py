"""Unit tests for the interview module engine (openbiliclaw.interview.engine)."""

from __future__ import annotations

import csv
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from openbiliclaw.interview.engine import (
    DEFAULT_INTERVIEW_ROOT,
    ENV_INTERVIEW_ROOT,
    InterviewEngine,
    resolve_root,
)

#: 引擎数据表 CSV 表头（与 _系统_知识库引擎/数据 一致）
CSV_HEADERS: dict[str, list[str]] = {
    "01_岗位表.csv": [
        "公司",
        "岗位",
        "面试时间",
        "状态",
        "主打方向",
        "备战目录",
        "简历版本",
        "备注",
    ],
    "02_项目表.csv": ["项目名", "公司", "技术栈", "核心数字", "来源", "可讲要点"],
    "03_真实数字表.csv": ["数字", "口径", "公司/项目", "来源", "入库时间"],
    "04_面试日志.csv": ["日期", "公司", "轮次", "面试官角色", "被问要点", "复盘", "复盘文档"],
    "05_面试题索引.csv": ["题目", "方向", "公司", "答案位置"],
    "06_全库文件索引.csv": [
        "路径",
        "层",
        "子层",
        "类型",
        "文件名",
        "扩展名",
        "大小KB",
        "修改日期",
    ],
}


def _write_csv(path: Path, name: str, rows: list[dict[str, str]]) -> None:
    """按标准表头写入一张引擎数据表。"""
    target = path / name
    with target.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS[name])
        writer.writeheader()
        writer.writerows(rows)


def build_kb(root: Path, *, with_db: bool = True) -> Path:
    """构造一个最小可用的三层求职知识库，返回 root。"""
    engine_dir = root / "_系统_知识库引擎"
    data_dir = engine_dir / "数据"
    data_dir.mkdir(parents=True)

    _write_csv(
        data_dir,
        "01_岗位表.csv",
        [
            {
                "公司": "测试公司",
                "岗位": "推荐算法工程师",
                "面试时间": "2026-09-09",
                "状态": "待面",
                "主打方向": "推荐全链路/广告算法",
                "备战目录": "03_岗位弹药库/测试公司-推荐算法-面试准备/",
                "简历版本": "基础版",
                "备注": "两轮面试",
            }
        ],
    )
    _write_csv(
        data_dir,
        "02_项目表.csv",
        [
            {
                "项目名": "测试项目A",
                "公司": "测试公司",
                "技术栈": "python/sql",
                "核心数字": "+21% ecpm",
                "来源": "原始资料",
                "可讲要点": "归因/分层",
            }
        ],
    )
    _write_csv(
        data_dir,
        "03_真实数字表.csv",
        [
            {
                "数字": "+21%",
                "口径": "ecpm提升",
                "公司/项目": "测试公司/测试项目A",
                "来源": "答辩文档",
                "入库时间": "2026-09-01",
            },
            {
                "数字": "1.04",
                "口径": "ARPU",
                "公司/项目": "OPPO/多位置校准",
                "来源": "答辩文档",
                "入库时间": "2026-09-01",
            },
        ],
    )
    _write_csv(
        data_dir,
        "04_面试日志.csv",
        [
            {
                "日期": "2026-09-01",
                "公司": "测试公司",
                "轮次": "一面",
                "面试官角色": "面试官A",
                "被问要点": "召回",
                "复盘": "已复盘",
                "复盘文档": "",
            },
        ],
    )
    _write_csv(
        data_dir,
        "05_面试题索引.csv",
        [
            {
                "题目": "oCPX 机制是什么",
                "方向": "广告算法",
                "公司": "测试公司",
                "答案位置": "02_方向知识库/广告算法/README.md",
            },
            {
                "题目": "如何做因果推断",
                "方向": "数据科学",
                "公司": "跨岗位",
                "答案位置": "02_方向知识库/数据科学/06_因果推断方法论.md",
            },
        ],
    )
    _write_csv(
        data_dir,
        "06_全库文件索引.csv",
        [
            {
                "路径": "02_方向知识库/推荐系统/README.md",
                "层": "02_方向知识库",
                "子层": "推荐系统",
                "类型": "md",
                "文件名": "README.md",
                "扩展名": ".md",
                "大小KB": "1",
                "修改日期": "2026-09-01",
            },
            {
                "路径": "03_岗位弹药库/测试公司-推荐算法-面试准备/02_面试备战资料/攻略.md",
                "层": "03_岗位弹药库",
                "子层": "测试公司",
                "类型": "md",
                "文件名": "攻略.md",
                "扩展名": ".md",
                "大小KB": "1",
                "修改日期": "2026-09-01",
            },
        ],
    )

    if with_db:
        conn = sqlite3.connect(data_dir / "knowledge.db")
        conn.execute(
            """
            CREATE TABLE file_index (
                路径 TEXT, 层 TEXT, 子层 TEXT, 类型 TEXT,
                文件名 TEXT, 扩展名 TEXT, 大小KB TEXT, 修改日期 TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO file_index (路径,层,子层,类型,文件名) VALUES (?,?,?,?,?)",
            [
                (
                    "02_方向知识库/推荐系统/README.md",
                    "02_方向知识库",
                    "推荐系统",
                    "md",
                    "README.md",
                ),
                (
                    "03_岗位弹药库/测试公司-推荐算法-面试准备/02_面试备战资料/攻略.md",
                    "03_岗位弹药库",
                    "测试公司",
                    "md",
                    "攻略.md",
                ),
            ],
        )
        conn.commit()
        conn.close()

    # 三层内容文件
    (root / "02_方向知识库").mkdir(parents=True)
    (root / "02_方向知识库" / "推荐系统").mkdir()
    (root / "02_方向知识库" / "推荐系统" / "README.md").write_text(
        "召回是推荐系统的第一步，oCPX 属于广告出价机制。", encoding="utf-8"
    )
    (root / "02_方向知识库" / "广告算法").mkdir()
    (root / "02_方向知识库" / "广告算法" / "README.md").write_text(
        "oCPX 机制：按转化目标出价。", encoding="utf-8"
    )
    pos_dir = root / "03_岗位弹药库" / "测试公司-推荐算法-面试准备"
    (pos_dir / "02_面试备战资料").mkdir(parents=True)
    (pos_dir / "02_面试备战资料" / "攻略.md").write_text(
        "面试攻略：ARPU 口径要熟。", encoding="utf-8"
    )
    # 岗位定制弹药：速成包（速记卡 + 预测题库）
    (pos_dir / "03_速成包").mkdir()
    (pos_dir / "03_速成包" / "测试公司推荐算法_面试前速记卡.md").write_text(
        "主打推荐全链路：召回→粗排→精排，数字先查真实数字表。", encoding="utf-8"
    )
    (pos_dir / "03_速成包" / "测试公司技术面_预测题库.md").write_text(
        "预测题库：两轮面试要点 + 手撕 SQL。", encoding="utf-8"
    )
    # 系统层规范
    spec_dir = root / "_系统_知识库引擎" / "规范"
    spec_dir.mkdir(parents=True)
    (spec_dir / "岗位匹配评估.md").write_text(
        "岗位匹配评估：五维打分 + deal-breaker 检查。", encoding="utf-8"
    )
    (root / "01_原始资料库" / "解码文本").mkdir(parents=True)
    (root / "01_原始资料库" / "解码文本" / "测试项目A.txt").write_text(
        "测试项目A：ecpm +21%。", encoding="utf-8"
    )
    # 过往工作资料（工作资料_腾讯）
    work_dir = root / "01_原始资料库" / "工作资料_腾讯"
    work_dir.mkdir(parents=True)
    (work_dir / "微视账号推荐_工作笔记.md").write_text(
        "工作笔记：微视账号推荐冷启动实践。", encoding="utf-8"
    )
    return root


@pytest.fixture()
def kb(tmp_path: Path) -> Path:
    return build_kb(tmp_path / "kb")


def test_resolve_root_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_INTERVIEW_ROOT, raising=False)
    # 配置优先于默认路径
    assert resolve_root("/tmp/自定义") == "/tmp/自定义"
    # 环境变量覆盖默认路径
    monkeypatch.setenv(ENV_INTERVIEW_ROOT, "/tmp/env-root")
    assert resolve_root("") == "/tmp/env-root"
    assert resolve_root("/tmp/自定义") == "/tmp/自定义"
    # 全空时回退默认路径
    monkeypatch.delenv(ENV_INTERVIEW_ROOT, raising=False)
    assert resolve_root("") == DEFAULT_INTERVIEW_ROOT


def test_unconfigured_root(tmp_path: Path) -> None:
    eng = InterviewEngine(tmp_path / "not-exists")
    assert not eng.is_configured
    assert eng.overview()["configured"] is False
    with pytest.raises(ValueError):
        eng.search("关键词")


def test_jobs(kb: Path) -> None:
    eng = InterviewEngine(kb)
    assert len(eng.jobs()) == 1
    assert eng.jobs("测试公司")[0]["岗位"] == "推荐算法工程师"
    assert eng.jobs("不存在公司") == []


def test_search_hits_across_layers(kb: Path) -> None:
    eng = InterviewEngine(kb)
    hits = eng.search("oCPX")
    paths = {h["path"] for h in hits}
    # 02_方向知识库 两个文档都命中，且路径相对 root
    assert "02_方向知识库/推荐系统/README.md" in paths
    assert "02_方向知识库/广告算法/README.md" in paths
    for h in hits:
        assert h["line"] >= 1
        assert h["snippet"]
    # 解码文本可命中
    assert any("测试项目A.txt" in h["path"] for h in eng.search("ecpm"))
    # 大小写不敏感 + 每文件只报第一行
    hits_arpu = eng.search("arpu")
    assert len(hits_arpu) == 1
    assert hits_arpu[0]["snippet"].startswith("面试攻略")
    # 关键词为空返回空
    assert eng.search("  ") == []


def test_numbers_and_projects(kb: Path) -> None:
    eng = InterviewEngine(kb)
    assert len(eng.numbers()) == 2
    assert len(eng.numbers("ARPU")) == 1
    assert len(eng.projects()) == 1
    assert eng.projects("测试项目A")[0]["核心数字"] == "+21% ecpm"
    assert eng.projects("无") == []


def test_questions_by_company(kb: Path) -> None:
    eng = InterviewEngine(kb)
    rows = eng.questions("测试公司")
    # 命中本公司题目 + 跨岗位题目
    assert len(rows) == 2
    assert any(r["题目"] == "oCPX 机制是什么" for r in rows)
    assert any(r["公司"] == "跨岗位" for r in rows)


def test_directions(kb: Path) -> None:
    eng = InterviewEngine(kb)
    dirs = eng.directions()
    names = [d["direction"] for d in dirs]
    assert "推荐系统" in names and "广告算法" in names
    rec = next(d for d in dirs if d["direction"] == "推荐系统")
    assert rec["docs"] == ["README.md"]


def test_index_from_db(kb: Path) -> None:
    eng = InterviewEngine(kb)
    rows = eng.index(keyword="README")
    assert len(rows) == 1
    assert rows[0]["路径"].startswith("02_方向知识库")
    # 多命中：路径含「推荐」的共 2 个文件
    assert len(eng.index(keyword="推荐")) == 2
    # 按层过滤
    assert eng.index(layer="03")[0]["层"] == "03_岗位弹药库"
    assert eng.index(keyword="不存在") == []


def test_index_csv_fallback(tmp_path: Path) -> None:
    root = build_kb(tmp_path / "kb", with_db=False)
    eng = InterviewEngine(root)
    rows = eng.index(keyword="README")
    assert len(rows) == 1
    assert rows[0]["文件名"] == "README.md"
    # 层过滤同样生效
    assert eng.index(layer="03")[0]["路径"].startswith("03_岗位弹药库")


def test_add_log_appends_only(kb: Path) -> None:
    eng = InterviewEngine(kb)
    before = eng.logs()
    assert len(before) == 1
    new_id = eng.add_log("测试公司", "二面", "问 AB 实验")
    assert new_id == 2
    after = eng.logs()
    assert len(after) == 2
    # 追加行位于最后（logs 倒序返回时在最前）
    assert after[0]["公司"] == "测试公司"
    assert after[0]["轮次"] == "二面"
    assert after[0]["被问要点"] == "问 AB 实验"
    assert after[0]["日期"] == date.today().isoformat()
    assert after[0]["复盘"] == "待复盘"
    # 历史行未被修改
    assert after[1]["复盘"] == "已复盘"


def test_speed_card(kb: Path) -> None:
    eng = InterviewEngine(kb)
    card = eng.speed_card("测试公司")
    assert card["job"]["公司"] == "测试公司"
    assert len(card["numbers"]) == 1  # 公司命中 1 条
    assert card["projects"][0]["项目名"] == "测试项目A"
    assert len(card["questions"]) == 2
    with pytest.raises(KeyError):
        eng.speed_card("不存在公司")


def test_speed_card_includes_prep(kb: Path) -> None:
    """速记卡应包含岗位目录下用户定制的速成包与备战资料弹药。"""
    eng = InterviewEngine(kb)
    prep = eng.speed_card("测试公司")["prep"]
    assert prep["dir"] and prep["dir"].endswith("测试公司-推荐算法-面试准备")
    assert "主打推荐全链路" in prep["quick_card"]  # 速记卡文件全文已纳入
    names = {p["name"] for p in prep["quick_pack"]}
    assert "测试公司技术面_预测题库.md" in names
    assert any("速记卡" in n for n in names)
    assert prep["prep_docs"][0]["name"] == "攻略.md"


def test_search_covers_work_and_specs(kb: Path) -> None:
    """全文检索应覆盖过往工作资料与系统层规范。"""
    eng = InterviewEngine(kb)
    assert any("工作资料_腾讯" in h["path"] for h in eng.search("微视账号推荐"))
    assert any("_系统_知识库引擎/规范" in h["path"] for h in eng.search("五维打分"))


def test_overview_specs(kb: Path) -> None:
    """总览应列出系统层规范文档。"""
    eng = InterviewEngine(kb)
    assert "岗位匹配评估.md" in eng.overview()["specs"]


def test_scaffold_creates_and_idempotent(kb: Path) -> None:
    eng = InterviewEngine(kb)
    result = eng.scaffold("新公司", "数据科学")
    assert result["name"] == "新公司-数据科学-面试准备"
    assert sorted(result["created"]) == ["01_岗位与公司信息", "02_面试备战资料", "03_速成包"]
    target = Path(result["path"])
    assert target.is_dir()
    assert (target / "01_岗位与公司信息").is_dir()
    # 幂等：再次调用不覆盖，全部列为已存在
    again = eng.scaffold("新公司", "数据科学")
    assert again["created"] == []
    assert sorted(again["existing"]) == ["01_岗位与公司信息", "02_面试备战资料", "03_速成包"]
    with pytest.raises(ValueError):
        eng.scaffold("", "岗位")


def test_overview(kb: Path) -> None:
    eng = InterviewEngine(kb)
    ov = eng.overview()
    assert ov["configured"] is True
    assert len(ov["jobs"]) == 1
    assert ov["number_count"] == 2
    assert ov["question_count"] == 2
    assert ov["log_count"] == 1
    assert "推荐系统" in ov["directions"]
