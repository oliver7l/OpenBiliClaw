"""周末玩法引擎。

把多源上下文聚合成一份带「为什么适合你」理由的周末计划：

- 日记情绪（data/diary.db，已回填的 mood_score）——决定 出门/宅家 权重
- 豆瓣想看/想读（data/douban.db status='wish'）——宅家方案弹药库
- 灵魂画像周末模式（obc_soul ContextMode.weekend_patterns，可选，目前多为空）
- 本地活动种子（WeekendStore.weekend_spots）——出门方案候选

默认纯规则生成（不依赖 LLM，可离线、可测试）；``use_llm`` 开启时
把规则方案交给 LLM 润色文案，失败自动回退规则结果。
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from openbiliclaw.storage.database import open_db_conn

from .models import PlanMode, PlanOption, WeekendPlan
from .store import WeekendStore

# 你活动半径内的优先区域（来自日记画像：宝安/南山为主，福田也在 30min 圈）
PRIORITY_DISTRICTS = ("宝安", "南山", "福田")
# 适合「带娃/家庭」的标签/人群关键词
FAMILY_HINTS = ("带娃", "亲子", "家庭")
# 适合「累/想躺平」的标签/人群关键词
REST_HINTS = ("累的人", "躺平", "独处", "独处缝隙")


class WeekendEngine:
    """离线优先的周末计划生成器。"""

    def __init__(
        self,
        store: WeekendStore,
        *,
        diary_db: str = "data/diary.db",
        douban_db: str = "data/douban.db",
        llm_service: Any | None = None,
        use_llm: bool = False,
    ) -> None:
        self.store = store
        self.diary_db = diary_db
        self.douban_db = douban_db
        self.llm_service = llm_service
        self.use_llm = use_llm

    # ── 上下文采集 ───────────────────────────────────────────────
    def recent_mood(self, days: int = 14) -> dict[str, Any]:
        """最近 N 天日记情绪均值与主导 mood。"""
        conn = open_db_conn(self.diary_db)
        try:
            since = (date.today() - timedelta(days=days)).isoformat()
            rows = conn.execute(
                "SELECT mood, mood_score FROM diary_entries WHERE entry_date >= ? AND mood_score IS NOT NULL",
                (since,),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return {"avg": None, "count": 0, "dominant": None}
        scores = [float(r["mood_score"]) for r in rows]
        avg = sum(scores) / len(scores)
        dominant = Counter(r["mood"] for r in rows).most_common(1)[0][0]
        return {"avg": round(avg, 3), "count": len(scores), "dominant": dominant}

    def douban_wish(self, limit_per: int = 6) -> dict[str, list[str]]:
        """豆瓣想看/想读清单（宅家方案弹药）。"""
        conn = open_db_conn(self.douban_db)
        try:
            out: dict[str, list[str]] = {"book": [], "movie": []}
            for cat, key in (("book", "book"), ("movie", "movie")):
                rows = conn.execute(
                    "SELECT name FROM douban_items "
                    "WHERE category = ? AND status = 'wish' "
                    "ORDER BY CASE rating WHEN '' THEN 0 ELSE CAST(rating AS REAL) END DESC "
                    "LIMIT ?",
                    (cat, limit_per),
                ).fetchall()
                out[key] = [r["name"] for r in rows]
        finally:
            conn.close()
        return out

    def soul_weekend_patterns(self) -> str | None:
        """灵魂画像里的周末模式描述（可选；目前大多为空）。"""
        try:
            from obc_soul.profile import SoulProfile

            profile = SoulProfile()
            ctx = getattr(profile, "context", None)
            if ctx is None:
                return None
            val = getattr(ctx, "weekend_patterns", "")
            return val or None
        except Exception:
            return None

    # ── 模式判定 ─────────────────────────────────────────────────
    @staticmethod
    def decide_mode(requested: str, mood: dict[str, Any]) -> str:
        """auto 时总是返回 mixed（出门+宅家都给，情绪只影响文案/能量，不剔除方向）。"""
        if requested != PlanMode.AUTO.value:
            return requested
        return "mixed"

    # ── 方案构建 ─────────────────────────────────────────────────
    def _score_spot(self, s: Any) -> int:
        sc = 0
        if s.district in PRIORITY_DISTRICTS:
            sc += 2
        if any(h in s.suitable_for for h in FAMILY_HINTS):
            sc += 2
        if s.free:
            sc += 1
        if any(h in s.suitable_for for h in REST_HINTS):
            sc += 1
        if s.detail:
            sc += 1
        return sc

    def _group_spots(self) -> dict[str, list[Any]]:
        """按人群/主题把活动分组，便于生成不同方向的出门方案。"""
        fam: list[Any] = []
        rest: list[Any] = []
        culture: list[Any] = []
        for s in self.store.list_spots():
            sf = s.suitable_for
            if any(h in sf for h in FAMILY_HINTS):
                fam.append(s)
            if any(h in sf for h in REST_HINTS):
                rest.append(s)
            if s.category == "culture":
                culture.append(s)
        return {"family": fam, "rest": rest, "culture": culture}

    def _mk_outdoor(self, title: str, spots: list[Any], mood: dict[str, Any], *, energy: str) -> PlanOption:
        picks = sorted(spots, key=self._score_spot, reverse=True)[:3]
        avg = mood.get("avg")
        if avg is not None and avg < 0.1:
            why = "这周你情绪基线偏低，挑的都是车程 30 分钟内、能随时撤退的近郊点，带娃放电但不折腾，累了随时能回家。"
        else:
            why = "离家近、带娃友好，开车/地铁都能到，周末轻松不内耗。"
        actions = [f"{s.title}｜{s.location}｜{s.time_text}｜{s.transit}" for s in picks]
        return PlanOption(
            title=title,
            mode="outdoor",
            why=why,
            energy=energy,
            actions=actions,
            spot_ids=[s.id for s in picks],
        )

    def _outdoor_options(self, mood: dict[str, Any]) -> list[PlanOption]:
        g = self._group_spots()
        avg = mood.get("avg")
        energy = "low" if (avg is not None and avg < 0.1) else "medium"
        opts: list[PlanOption] = []
        if g["family"]:
            opts.append(self._mk_outdoor("轻松出门 · 带娃放电", g["family"], mood, energy=energy))
        if g["rest"]:
            opts.append(self._mk_outdoor("中途偷闲 · 躺平独处", g["rest"], mood, energy="low"))
        if g["culture"]:
            opts.append(self._mk_outdoor("文化深度 · 逛古村", g["culture"], mood, energy=energy))
        if not opts:
            all_spots = self.store.list_spots()
            if all_spots:
                opts.append(self._mk_outdoor("附近逛逛", all_spots, mood, energy=energy))
        return opts

    def _indoor_options(self, douban: dict[str, list[str]], mood: dict[str, Any]) -> list[PlanOption]:
        opts: list[PlanOption] = []
        books = douban.get("book", [])[:3]
        movies = douban.get("movie", [])[:2]
        avg = mood.get("avg")
        rest_note = (
            "这周明显累了——最省力的方案：瘫着把想看的书和电影消化掉，不动脑子。"
            if (avg is not None and avg < 0.1)
            else ""
        )
        if books:
            why = "宅家优先：从你豆瓣想读里挑了这几本，不费体力，累了就瘫着看完。" + rest_note
            opts.append(
                PlanOption(
                    title="宅家充电 · 读书",
                    mode="indoor",
                    why=why,
                    energy="low",
                    actions=["读：" + " / ".join(books)],
                    douban_picks=books,
                )
            )
        if movies:
            why = "不想动脑子时，从想看清单挑几部叙事/人物向的片子，窝沙发刷完。" + rest_note
            opts.append(
                PlanOption(
                    title="宅家充电 · 观影",
                    mode="indoor",
                    why=why,
                    energy="low",
                    actions=["看：" + " / ".join(movies)],
                    douban_picks=movies,
                )
            )
        if not opts:
            opts.append(
                PlanOption(
                    title="宅家充电 · 放空",
                    mode="indoor",
                    why="什么也不安排，睡到自然醒，给自己一个完全空白的周末。",
                    energy="low",
                    actions=["睡懒觉 / 发呆 / 随便翻翻"],
                )
            )
        return opts

    def build_options(self, mode: str, mood: dict[str, Any], douban: dict[str, list[str]]) -> list[PlanOption]:
        if mode == PlanMode.OUTDOOR.value:
            return self._outdoor_options(mood)[:3]
        if mode == PlanMode.INDOOR.value:
            return self._indoor_options(douban, mood)[:3]
        # mixed/auto：出门 + 宅家组合，凑满 3 个
        outdoor = self._outdoor_options(mood)
        indoor = self._indoor_options(douban, mood)
        out: list[PlanOption] = []
        if outdoor:
            out.append(outdoor[0])
        out.extend(indoor[:2])
        for extra in outdoor[1:]:
            if len(out) >= 3:
                break
            out.append(extra)
        for extra in indoor[2:]:
            if len(out) >= 3:
                break
            out.append(extra)
        if not out:
            out.append(
                PlanOption(
                    title="完全放空",
                    mode="indoor",
                    why="没有任何数据，给自己一个空白周末吧。",
                    energy="low",
                    actions=["睡懒觉 / 发呆"],
                )
            )
        return out[:3]

    # ── 生成 ─────────────────────────────────────────────────────
    @staticmethod
    def saturday_of_week(d: date | None = None) -> str:
        """返回包含 d（默认今天）的那个周六的 ISO 日期。

        以周一为一周起点：周一~周六都映射到所在周的周六；周日则回退到
        刚过去的那个周六（即本周末刚结束）。
        """
        d = d or date.today()
        # weekday(): 周一=0 … 周六=5 周日=6；周日回退到刚过去的周六
        offset = -1 if d.weekday() == 6 else 5 - d.weekday()
        return (d + timedelta(days=offset)).isoformat()

    def mood_basis_text(self, mood: dict[str, Any]) -> str:
        avg = mood.get("avg")
        if avg is None:
            return "近 14 天无情绪记录，按默认偏好生成。"
        tone = "情绪偏高、有精力" if avg >= 0.15 else "情绪偏低、偏累" if avg < 0.0 else "情绪平稳"
        return f"近 14 天日记情绪均值 {avg}（{mood['count']} 篇，{tone}），主导心情「{mood['dominant']}」。"

    def generate(self, mode: str = PlanMode.AUTO.value, week_of: str | None = None) -> WeekendPlan:
        week_of = week_of or self.saturday_of_week()
        mood = self.recent_mood()
        douban = self.douban_wish()
        final_mode = self.decide_mode(mode, mood)
        options = self.build_options(final_mode, mood, douban)
        plan = WeekendPlan(
            week_of=week_of,
            mode=final_mode,
            mood_basis=self.mood_basis_text(mood),
            options=options,
            source="local",
        )
        if self.use_llm and self.llm_service is not None:
            plan = self._polish_with_llm(plan) or plan
        self.store.save_plan(plan)
        return plan

    def _polish_with_llm(self, plan: WeekendPlan) -> WeekendPlan | None:
        """可选：用 LLM 润色方案 why/actions 文案，失败返回 None（回退规则）。"""
        try:
            soul = self.soul_weekend_patterns()
            prompt = (
                "你是用户的周末私人规划师。基于以下上下文，把周末方案文案写得更像"
                "「懂他的人」说的白话，保留事实（地点/交通/清单）不要编造。\n"
                f"情绪依据：{plan.mood_basis}\n"
                f"灵魂画像周末模式：{soul or '（无）'}\n"
                f"现有方案：{[o.to_dict() for o in plan.options]}\n"
                "返回同样的 JSON 结构，只润色 title/why/actions 的中文表达。"
            )
            result = self.llm_service.complete_structured_task(
                prompt=prompt,
                schema={"options": "list[dict]"},
            )
            opts = result.get("options") if isinstance(result, dict) else None
            if not opts:
                return None
            plan.options = [PlanOption.from_dict(o) for o in opts[:3]]
            return plan
        except Exception:
            return None

    # ── 周五定时触发 ─────────────────────────────────────────────
    @staticmethod
    def is_friday_push_window(now: datetime | None = None, *, hour: int = 20) -> bool:
        """是否为周五 18:00-23:00 的主动推送窗口。"""
        now = now or datetime.now()
        return now.weekday() == 4 and hour <= now.hour < hour + 3

    def should_generate_for_friday(self, now: datetime | None = None, *, hour: int = 20) -> bool:
        """周五窗口内、且本周尚未生成过 → 触发。"""
        now = now or datetime.now()
        if not self.is_friday_push_window(now, hour=hour):
            return False
        return not self.store.plan_exists_for_week(self.saturday_of_week(now.date()))

    def generate_for_friday(self, now: datetime | None = None) -> WeekendPlan | None:
        """周五触发入口：满足条件才生成，否则返回 None。"""
        if not self.should_generate_for_friday(now):
            return None
        plan = self.generate(mode=PlanMode.AUTO.value, week_of=self.saturday_of_week((now or datetime.now()).date()))
        plan.source = "friday_push"
        self.store.save_plan(plan)
        return plan
