/* 面试安排视图模型（VM）— 纯函数，无 DOM、无 HTML。
 *
 * 为什么单独抽出：`/api/interview/study/schedule` 返回的是**投递域原始行**
 * （自由文本 + 迁移 001 回填的结构化列混在一起），日期切分/倒计时/阶段分组
 * 这类逻辑如果埋在 `interview.js` 的 IIFE 里，就只能靠 grep 源码去"测"，
 * 那类断言没有鉴别力。这里抽成可被 node 直接 require 的纯模块，
 * 由 `tests/desktop/test_interview_schedule_view_model.py` 真跑真断言。
 *
 * 加载方式：桌面端是传统 <script>（非 ESM），故用 UMD 包装——
 * 浏览器挂到 window.OBCScheduleView，node 测试走 module.exports。
 *
 * 契约来源：`interview/schedule.py` 迁移 001 的 stage 枚举
 * （候选/已投递/待面/面试中/谈薪中/已结束/已终止）。
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.OBCScheduleView = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // 展示顺序；阶段分布小标签按此排序。
  var STAGE_ORDER = ["待面", "面试中", "谈薪中", "已投递", "候选", "已结束", "已终止"];

  // 视为「仍在推进」的阶段——只有这些才可能进「待进行」区。
  var ACTIVE_STAGES = ["待面", "面试中", "谈薪中"];

  var STAGE_TONE = {
    待面: "upcoming",
    面试中: "progress",
    谈薪中: "offer",
    已投递: "idle",
    候选: "idle",
    已结束: "closed",
    已终止: "closed",
  };

  function isIsoDate(value) {
    return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
  }

  function todayIso(now) {
    var d = now instanceof Date ? now : new Date();
    return (
      d.getFullYear() +
      "-" +
      String(d.getMonth() + 1).padStart(2, "0") +
      "-" +
      String(d.getDate()).padStart(2, "0")
    );
  }

  function shiftIso(iso, days) {
    var p = iso.split("-").map(Number);
    var d = new Date(p[0], p[1] - 1, p[2]);
    d.setDate(d.getDate() + days);
    return todayIso(d);
  }

  /** 两个 ISO 日期相差天数（b - a），纯字符串日期算法，不带时区。 */
  function daysBetween(aIso, bIso) {
    if (!isIsoDate(aIso) || !isIsoDate(bIso)) return null;
    var a = aIso.split("-").map(Number);
    var b = bIso.split("-").map(Number);
    var ms = Date.UTC(b[0], b[1] - 1, b[2]) - Date.UTC(a[0], a[1] - 1, a[2]);
    return Math.round(ms / 86400000);
  }

  /**
   * 取结构化时间里的 (日期, 时刻)。
   *
   * 优先用后端回填的 `interview_start_at`（ISO 前缀，可字符串比较）；
   * 为空时**才**回退去自由文本 `interview_at` 抠日期。
   * 绝不用空白切分——'2026-08下旬~09初' / '未约面' 切出来是垃圾。
   */
  function scheduleDateParts(start, rawAt) {
    var s = typeof start === "string" ? start.trim() : "";
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
      return {
        datePart: s.slice(0, 10),
        timePart: s.length > 10 ? s.slice(11, 16) : "",
      };
    }
    var m = /(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}:\d{2}))?/.exec(
      typeof rawAt === "string" ? rawAt : ""
    );
    return m ? { datePart: m[1], timePart: m[2] || "" } : { datePart: "", timePart: "" };
  }

  /** 倒计时文案与色调。已过/无日期返回 null（不显示倒计时）。 */
  function countdown(datePart, today) {
    if (!isIsoDate(datePart)) return null;
    var diff = daysBetween(today || todayIso(), datePart);
    if (diff === null) return null;
    if (diff === 0) return { text: "今天", tone: "soon" };
    if (diff === 1) return { text: "明天", tone: "soon" };
    if (diff > 1) return { text: diff + " 天后", tone: "future" };
    return null;
  }

  function stageTone(stage) {
    return STAGE_TONE[stage] || "idle";
  }

  /** 单行 → 展示项。字段缺失一律降级为可见但中性的值，不抛错。 */
  function buildItem(job, today) {
    var row = job || {};
    var parts = scheduleDateParts(row.interview_start_at, row.interview_at);
    var days = isIsoDate(parts.datePart) ? daysBetween(today, parts.datePart) : null;
    return {
      company: row.company || "",
      role: row.role || "",
      direction: row.direction || "",
      note: row.note || "",
      status: row.status || "",
      stage: row.stage || "",
      stageTone: stageTone(row.stage || ""),
      roundNote: row.round_note || "",
      datePart: parts.datePart,
      day: parts.datePart ? String(Number(parts.datePart.slice(8, 10))) : "?",
      month: parts.datePart ? Number(parts.datePart.slice(5, 7)) + "月" : "待定",
      time: parts.timePart,
      daysUntil: days,
      countdown: countdown(parts.datePart, today),
      isUpcoming: row.is_upcoming === true || row.is_upcoming === 1,
    };
  }

  /**
   * 后端 schedule 响应 → 渲染用 VM。
   *
   * @param {object} data  `{upcoming, history, total, upcoming_count, stage_counts, structured}`
   * @param {string} [today]  注入「今天」（ISO），便于测试确定性
   */
  function buildScheduleViewModel(data, today) {
    var src = data || {};
    var day = isIsoDate(today) ? today : todayIso();
    var upcoming = Array.isArray(src.upcoming) ? src.upcoming : [];
    var history = Array.isArray(src.history) ? src.history : [];
    var counts = src.stage_counts || {};

    var chips = STAGE_ORDER.filter(function (k) {
      return counts[k] > 0;
    }).map(function (k) {
      return { stage: k, count: counts[k], tone: stageTone(k) };
    });

    var sections = [];
    if (upcoming.length) {
      sections.push({
        key: "upcoming",
        title: "⏳ 待进行",
        items: upcoming.map(function (j) {
          return buildItem(j, day);
        }),
      });
    }
    if (history.length) {
      sections.push({
        key: "history",
        title: "🗂 历史与候选",
        items: history.map(function (j) {
          return buildItem(j, day);
        }),
      });
    }

    return {
      total:
        typeof src.total === "number" ? src.total : upcoming.length + history.length,
      upcomingCount:
        typeof src.upcoming_count === "number"
          ? src.upcoming_count
          : upcoming.length,
      structured: src.structured === true,
      chips: chips,
      sections: sections,
      isEmpty: upcoming.length + history.length === 0,
    };
  }

  return {
    STAGE_ORDER: STAGE_ORDER,
    ACTIVE_STAGES: ACTIVE_STAGES,
    isIsoDate: isIsoDate,
    todayIso: todayIso,
    shiftIso: shiftIso,
    daysBetween: daysBetween,
    scheduleDateParts: scheduleDateParts,
    countdown: countdown,
    stageTone: stageTone,
    buildItem: buildItem,
    buildScheduleViewModel: buildScheduleViewModel,
  };
});
