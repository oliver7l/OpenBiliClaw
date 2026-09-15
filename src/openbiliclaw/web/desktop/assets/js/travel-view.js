/* 旅行（✈️ 旅行 tab）视图模型 — 纯函数，无 DOM、无 HTML。
 *
 * 为什么单独抽出：`/api/travel/flights` 返回的是**采集域原始字段**
 * （lowest_price / lowest_departure / child_fare / vs_baseline.{diff,pct}），
 * 此前 `app.js` 直接按 `a.price` / `a.drop` / `r.departure_time` / `r.child_price`
 * 取值，字段名早已漂移，导致降价提醒、航班时刻、儿童价**恒渲染 undefined**。
 * 这类漂移埋在 IIFE 里只能靠 grep 源码去"测"，没有鉴别力；抽成可被 node
 * 直接 require 的纯模块后，由 `tests/desktop/test_travel_view_model.py` 真跑。
 *
 * 加载方式：桌面端是传统 <script>（非 ESM），故用 UMD 包装——
 * 浏览器挂 window.OBCTravelView，node 测试走 module.exports。
 *
 * 契约来源：`openbiliclaw/travel/routes.py` 的 get_flights()。
 * 后端字段改名时必须同步改这里和上面那个测试文件。
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.OBCTravelView = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /** 千分位金额；非数字 → null（调用方据此决定是否渲染元素）。 */
  function formatMoney(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return null;
    return n.toLocaleString("zh-CN");
  }

  /**
   * 把 '2026-10-02 21:00:00' / '2026-10-02T21:00:00' 压成 '21:00'。
   *
   * 后端 lowest_departure 存的是**完整时间戳**（采集器原始格式），
   * 直接塞进卡片会把「10-02 21:00:00」整串糊上去。
   * 拿不到时刻时返回空串——由调用方决定是否渲染占位。
   */
  function clockOf(raw) {
    if (typeof raw !== "string") return "";
    var m = /(\d{1,2}):(\d{2})/.exec(raw);
    if (!m) return "";
    return String(m[1]).padStart(2, "0") + ":" + m[2];
  }

  /** rejectNullish：把 null/undefined/'' 归一成 ''，避免渲染出 "undefined"。 */
  function text(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  /**
   * 单条航线 → 渲染形状。返回值里**不含 HTML**，只含已经算好的展示串，
   * HTML 拼装仍留在 app.js。
   */
  function routeViewModel(route) {
    var r = route || {};
    var vs = r.vs_baseline || {};

    return {
      key: text(r.route),
      routeLabel: (text(r.dep_city) || text(r.dep)) + " → " + (text(r.arr_city) || text(r.arr)),
      depCity: text(r.dep_city) || text(r.dep),
      arrCity: text(r.arr_city) || text(r.arr),
      date: text(r.date),

      // 后端字段是 lowest_price（不是 price）
      priceValue: Number.isFinite(Number(r.lowest_price)) ? Number(r.lowest_price) : null,
      priceText: formatMoney(r.lowest_price),
      hasPrice: r.lowest_price !== null && r.lowest_price !== undefined && r.lowest_price !== "",

      // 后端字段是 lowest_flight / lowest_departure（不是 flight / departure_time）
      flightNo: text(r.lowest_flight),
      departure: clockOf(r.lowest_departure),
      airline: text(r.lowest_airline),

      // 后端字段是 child_fare（不是 child_price）
      childValue: Number.isFinite(Number(r.child_fare)) ? Number(r.child_fare) : null,
      childText: formatMoney(r.child_fare),
      hasChild: r.child_fare !== null && r.child_fare !== undefined && r.child_fare !== "",

      baseline: Number.isFinite(Number(r.baseline)) ? Number(r.baseline) : null,
      dropValue: Number.isFinite(Number(vs.diff)) ? Number(vs.diff) : null,
      dropPct: Number.isFinite(Number(vs.pct)) ? Number(vs.pct) : null,
      isAlert: vs.alert === true,

      flightCount: Number(r.flight_count) || 0,
      error: text(r.error),
      success: r.success === true,
    };
  }

  /**
   * 降价提醒文案。
   *
   * 旧实现读 `a.price` / `a.drop` / `a.flight`（三个后端都不存在的字段），
   * 渲染出来是「TYN-URC 2026-10-02 降价至 ¥undefined（undefined），较基线降 ¥undefined」。
   *
   * 接受**任一形态**：已归一化的 VM，或后端原始 route_info。
   * （判别式是 `isAlert` 是否为布尔——VM 一定有，原始 route 一定没有。）
   */
  function alertMessage(routeOrVm) {
    var vm =
      routeOrVm && typeof routeOrVm.isAlert === "boolean" ? routeOrVm : routeViewModel(routeOrVm);
    if (!vm.isAlert) return "";
    var parts = ["🔥 " + vm.routeLabel];
    if (vm.date) parts.push(vm.date);
    parts.push("降价至 ¥" + (vm.priceText || "—"));
    if (vm.flightNo) parts.push("（" + vm.flightNo + "）");
    if (vm.dropValue !== null) {
      parts.push("，较基线降 ¥" + formatMoney(vm.dropValue));
      if (vm.dropPct !== null) parts.push("（-" + vm.dropPct + "%）");
    }
    return parts.join(" ");
  }

  /** GET /api/travel/flights 的响应 → 视图模型。 */
  function buildFlightsViewModel(data) {
    var d = data || {};
    var routes = (d.routes || []).map(routeViewModel);
    return {
      routes: routes,
      alerts: routes.filter(function (r) {
        return r.isAlert;
      }),
      updatedAt: d.updated_at === undefined ? null : d.updated_at,
      feeNote: text(d.fee_note),
      hasData: routes.length > 0,
    };
  }

  return {
    formatMoney: formatMoney,
    clockOf: clockOf,
    routeViewModel: routeViewModel,
    alertMessage: alertMessage,
    buildFlightsViewModel: buildFlightsViewModel,
  };
});
