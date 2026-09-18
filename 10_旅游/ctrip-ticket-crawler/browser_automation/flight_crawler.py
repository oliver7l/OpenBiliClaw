"""
flight_crawler.py - 纯浏览器自动化爬取携程机票价格（方案 B）

每个查询都使用 Playwright 打开浏览器（事件等待），拦截 batchSearch 响应体，
直接从响应 JSON 中解析航班与价格信息（不依赖 DOM 渲染）。

优点：最稳定，完全模拟真实用户，不受 token/w-payload-source 限制；
     响应体比 DOM 更完整（含舱位代码、行李、共享航班、中转等字段）。
"""

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# 会话建立所需的关键 cookie（由首页 JS 生成，缺少会导致"未找到航班"）
SESSION_KEY_COOKIES = {"_bfa", "FVP"}

# 登录态 Cookie 文件（本地监控用，勿提交/外发）。若存在则注入浏览器，
# 以已登录身份查询，规避携程对匿名查询的 needUserLogin 风控。
COOKIE_FILE = Path(__file__).resolve().parent.parent / "ctrip_cookies.json"


def _load_ctrip_cookies() -> list:
    """从 COOKIE_FILE 读取 Cookie 并转换为 Playwright add_cookies 格式。"""
    if not COOKIE_FILE.exists():
        return []
    try:
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 Cookie 文件失败: %s", e)
        return []
    if isinstance(data, dict):
        data = data.get("cookies", [])
    if not isinstance(data, list):
        return []

    pw_cookies = []
    for c in data:
        if not isinstance(c, dict) or not c.get("name") or not c.get("domain"):
            continue
        item = {
            "name": c["name"],
            "value": c.get("value", ""),
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", False)),
        }
        # session 型 cookie（无 expirationDate）不设 expires，视为会话 cookie
        if c.get("expirationDate"):
            item["expires"] = int(c["expirationDate"])
        ss = c.get("sameSite")
        if ss == "no_restriction":
            item["sameSite"] = "None"
        elif ss == "lax":
            item["sameSite"] = "Lax"
        elif ss == "strict":
            item["sameSite"] = "Strict"
        pw_cookies.append(item)
    return pw_cookies


@dataclass
class SegmentInfo:
    """单个航段信息（中转航班有多个航段）"""
    flight_no: str = ""                # 市场航班号（如 "EU7347"）
    is_shared: bool = False            # 是否共享航班（存在执飞航班号且异于市场航班号即为 True）
    operate_flight_no: str = ""        # 执飞航班号（共享航班时与 flight_no 不同）
    operate_airline_name: str = ""  # 执飞航司名称（如 "四川航空"）
    operate_airline_code: str = ""     # 执飞航司二字码（如 "3U"）
    airline_code: str = ""             # 市场航司二字码（如 "EU"）
    airline_name: str = ""             # 市场航司名称（如 "成都航空"）
    departure_city_code: str = ""      # 出发城市 IATA 代码（如 "CKG"）
    departure_city_name: str = ""      # 出发城市名称（如 "重庆"）
    departure_airport_code: str = ""   # 出发机场 IATA 代码（如 "CKG"）
    departure_airport_name: str = ""   # 出发机场名称（如 "江北国际机场"）
    departure_terminal: str = ""       # 出发航站楼（如 "T3"，可为空）
    departure_time: str = ""           # 出发日期时间 "YYYY-MM-DD HH:MM:SS"
    arrival_city_code: str = ""        # 到达城市 IATA 代码（如 "YTY"）
    arrival_city_name: str = ""        # 到达城市名称（如 "扬州"）
    arrival_airport_code: str = ""     # 到达机场 IATA 代码（如 "YTY"）
    arrival_airport_name: str = ""     # 到达机场名称（如 "扬州泰州机场"）
    arrival_terminal: str = ""         # 到达航站楼（可为空）
    arrival_time: str = ""             # 到达日期时间 "YYYY-MM-DD HH:MM:SS"
    duration: int = 0                  # 该段飞行时长（分钟）
    # aircraft_name: str = ""            # 机型名称（如 "空客320(中)"）

    @classmethod
    def from_flight(cls, f: dict) -> "SegmentInfo":
        flight_no = f.get("flightNo", "")
        operate_flight_no = f.get("operateFlightNo", "")
        return cls(
            flight_no=flight_no,
            is_shared=bool(operate_flight_no) and operate_flight_no != flight_no,
            operate_flight_no=operate_flight_no,
            operate_airline_code=f.get("operateAirlineCode", ""),
            operate_airline_name=f.get("operateAirlineName", ""),
            airline_code=f.get("marketAirlineCode", ""),
            airline_name=f.get("marketAirlineName", ""),
            departure_city_code=f.get("departureCityCode", ""),
            departure_city_name=f.get("departureCityName", ""),
            departure_airport_code=f.get("departureAirportCode", ""),
            departure_airport_name=f.get("departureAirportName", ""),
            departure_terminal=f.get("departureTerminal", ""),
            departure_time=f.get("departureDateTime", ""),
            arrival_city_code=f.get("arrivalCityCode", ""),
            arrival_city_name=f.get("arrivalCityName", ""),
            arrival_airport_code=f.get("arrivalAirportCode", ""),
            arrival_airport_name=f.get("arrivalAirportName", ""),
            arrival_terminal=f.get("arrivalTerminal", ""),
            arrival_time=f.get("arrivalDateTime", ""),
            duration=f.get("duration", 0),
            # aircraft_name=f.get("aircraftName", ""),
        )


@dataclass
class FlightInfo:
    """单个行程（航班方案）信息，价格取该行程 priceList 中最低的一档
    （先剔除含付费 servicePackage 的档位，再依次比较 成人价 → 儿童价 → 婴儿价，
    全部并列时取先出现的）"""
    itinerary_id: str = ""             # 行程唯一标识（各航段航班号用 "-" 拼接，如 "CZ2317-CZ3437"）
    departure_time: str = ""           # 出发日期时间（第一段的 departureDateTime，"YYYY-MM-DD HH:MM:SS"）
    arrival_time: str = ""             # 到达日期时间（最后一段的 arrivalDateTime，中转跨天时为次日）
    transfer_count: int = 0            # 中转次数（0=直飞，1=中转一次）
    cross_days: int = 0                # 跨天数（0=当天到达，1=次日到达）
    duration_total: int = 0            # 总飞行时长（分钟，行程级，含中转等待）
    segments: list = field(default_factory=list)           # SegmentInfo 列表（所有航班信息都在各航段中）

    # ---- 价格与产品信息（均为行程级，来自 priceList 最低档；
    #      实测中转行程两个航段的值完全相同，seatClass 为拼接码 "@Y-Y/T"，
    #      证明这些字段是 flight/行程级别而非 segment 级别） ----
    adult_price: float = 0             # 成人票价（元，整个行程的总价）
    child_price: float = 0             # 儿童票价（元，整个行程的总价）
    infant_price: float = 0            # 婴儿票价（元；priceList 中为 0 时按 child_price 计）
    cabin: str = ""                    # 舱位等级（"Y" 经济 / "C" 公务 / "F" 头等）
    seat_class: str = ""               # 舱位代码（如 "K"、"V"；中转时为拼接码如 "@Y-Y/T"）
    baggage_kg: int = 0                # 免费托运行李重量（公斤）；从 baggageTag 解析，如 "托运行李额20KG"=20、"无免费托运行李额"=0、空=0
    free_airport_fee: bool = False     # 是否免机建费（priceTags 中 FreeSurcharge 且 tag 含"免机建"）
    free_fuel_fee: bool = False        # 是否免燃油（响应体 freeOilFeeAndTax 字段）
    discount_rate: float = 0           # 折扣率（0.37 = 3.7 折；0 表示无标准折扣）
    ticket_count: int = 0              # 剩余票数（0 表示响应中未提供）
    # penalty_tag: str = ""              # 退改签简述标签（如 "退改¥216起"，取第一个 priceUnit 的 penaltyList）

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["segments"] = [s.__dict__ for s in self.segments]
        return d


@dataclass
class SearchResult:
    """搜索结果数据类"""
    dep: str = ""                                   # 查询的出发城市 IATA 代码
    arr: str = ""                                   # 查询的到达城市 IATA 代码
    date: str = ""                                  # 查询的出发日期 "YYYY-MM-DD"
    success: bool = False                           # 查询是否成功（有航班结果且 API 状态正常）
    error: str = ""                                 # 错误信息（成功时为空字符串）
    flights: list = field(default_factory=list)     # FlightInfo 列表（已按条件筛选，如只留直飞）
    raw_response: dict = field(default_factory=dict)  # batchSearch 原始响应体（未处理的完整 JSON）


def _parse_baggage_kg(baggage_tag: str) -> int:
    """
    将托运行李标签解析为免费托运行李重量（公斤）。

    规律：从 "数字KG/KG数" 提取数字；若无匹配（如 "无免费托运行李额"）、
    空字符串则返回 0。兼容 "XXKG"、"X件"（按件计无重量信息时归为 0）等形式。
    例：'托运行李额20KG' -> 20, '托运行李额10KG' -> 10,
        '无免费托运行李额' -> 0, '' -> 0
    """
    if not baggage_tag:
        return 0
    # 匹配 "NNKG"/"NN公斤"（KG 不区分大小写），兼容全角/中文"公斤"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:[Kk][Gg]|公\s*斤)", baggage_tag)
    if m:
        # 可能是小数（如 "7.5KG"），取整
        return int(float(m.group(1)))
    return 0


def _parse_itinerary(itinerary: dict) -> FlightInfo:
    """将一个行程（flightItineraryList 元素）解析为 FlightInfo"""
    info = FlightInfo()

    # ---- 航段信息（所有航班信息都在各航段中） ----
    segments_raw = itinerary.get("flightSegments", [])
    seg = segments_raw[0] if segments_raw else {}
    info.transfer_count = seg.get("transferCount", 0)
    info.cross_days = seg.get("crossDays", 0)
    info.duration_total = seg.get("duration", 0)

    flight_list = seg.get("flightList", [])
    info.segments = [SegmentInfo.from_flight(f) for f in flight_list]
    # 行程唯一标识：各航段航班号用 "-" 拼接
    info.itinerary_id = "-".join(s.flight_no for s in info.segments)
    # 出发/到达时间：第一段出发时间、最后一段到达时间（中转跨天时为次日）
    if info.segments:
        info.departure_time = info.segments[0].departure_time
        info.arrival_time = info.segments[-1].arrival_time

    # ---- 价格：取最低档（依次比较 成人价 → 儿童价 → 婴儿价，全部并列时取先出现的） ----
    # 注意：infantPrice 缺失按 +∞ 处理（排在有值之后），确保并列时优先选有婴儿价信息的档位
    # 注意：先剔除带付费服务包的档位（含 "servicePackage" 且其 price > 0），
    #       例如选座/餐食/行李升级包等增值产品，其 adultPrice 不代表裸票价
    price_list = [
        p for p in itinerary.get("priceList", [])
        if not (p.get("servicePackage") and p["servicePackage"].get("price", 0) > 0)
    ]
    if price_list:
        best = min(
            price_list,
            key=lambda p: (
                p.get("adultPrice", 0),
                p.get("childPrice", 0),
                p.get("infantPrice", float("inf")),
            ),
        )

        # 价格与产品信息均为行程级（priceList 最低档），不绑定到具体航段
        info.adult_price = best.get("adultPrice", 0)
        info.child_price = best.get("childPrice", 0)
        info.infant_price = best.get("infantPrice", 0)
        info.cabin = best.get("cabin", "")
        info.free_fuel_fee = bool(best.get("freeOilFeeAndTax", False))

        # 舱位代码 / 折扣率 / 退改签标签：取第一个 priceUnit 的第一个座位
        seat_list = (
            best.get("priceUnitList", [{}])[0]
            .get("flightSeatList", [{}])
        )
        if seat_list:
            info.seat_class = seat_list[0].get("seatClass", "")
            info.discount_rate = seat_list[0].get("discountRate", 0) or 0
        # 退改签简述：flightSeatList[0].penalty.defaultPenaltyTag（如 "退改¥335起"）
        # if seat_list:
        #     info.penalty_tag = (
        #         seat_list[0].get("penalty", {}).get("defaultPenaltyTag", "") or ""
        #     )

        info.baggage_kg = _parse_baggage_kg(best.get("baggage", {}).get("baggageTag", ""))
        info.ticket_count = best.get("ticketCount", 0) or 0

        # 免机建费：priceTags 中 FreeSurcharge 类型且 tag 含 "免机建"
        for tag in best.get("priceTags", []):
            if tag.get("dtype") == "FreeSurcharge":
                if "免机建" in tag.get("data", {}).get("tag", ""):
                    info.free_airport_fee = True

    return info


class FlightCrawler:
    """携程机票纯浏览器自动化爬虫（响应体解析版）"""

    def __init__(self, headless: bool = True, slow_mo: int = 0):
        self.headless = headless
        self.slow_mo = slow_mo
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self._session_ready = False  # 首页会话（关键 cookie）是否已建立

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """启动浏览器"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
        )
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        # 注入本地登录态 Cookie（存在则注入，帮助规避 needUserLogin 风控）
        injected = _load_ctrip_cookies()
        if injected:
            try:
                self.context.add_cookies(injected)
                logger.info("Injected %d cookies from %s", len(injected), COOKIE_FILE.name)
            except Exception as e:  # noqa: BLE001
                logger.warning("注入 Cookie 失败（忽略，继续匿名查询）: %s", e)
        self.page = self.context.new_page()
        logger.info("Browser started (headless=%s)", self.headless)

    def stop(self):
        """关闭浏览器"""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("Browser stopped")

    def _ensure_session(self, timeout_ms: int = 30000):
        """
        确保首页会话已建立（关键 cookie 生成）。

        首次查询时访问首页并轮询等待 _bfa/FVP cookie 出现（事件驱动，
        通常 1-2 秒即可就绪）；会话已建立时直接跳过。
        """
        if self._session_ready:
            return
        # 若已注入的 Cookie 自带关键会话（_bfa/FVP），无需再访问首页
        names = {c["name"] for c in self.context.cookies()}
        if SESSION_KEY_COOKIES <= names:
            self._session_ready = True
            logger.info("Session cookies present (injected), skip homepage: %s", sorted(SESSION_KEY_COOKIES))
            return
        logger.info("Visiting homepage to establish session...")
        self.page.goto("https://flights.ctrip.com/", wait_until="domcontentloaded", timeout=timeout_ms)
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            names = {c["name"] for c in self.context.cookies()}
            if SESSION_KEY_COOKIES <= names:
                self._session_ready = True
                logger.info("Session established, key cookies: %s", sorted(SESSION_KEY_COOKIES))
                return
            self.page.wait_for_timeout(200)
        raise RuntimeError("Session cookies not established within timeout")

    def search(
        self,
        dep: str,
        arr: str,
        date: str,
        include_transfer: bool = False,
        include_shared: bool = False,
        timeout_ms: int = 30000,
    ) -> SearchResult:
        """
        搜索机票价格（从 batchSearch 响应体解析）

        Args:
            dep: 出发城市 IATA 代码（如 "CKG"）
            arr: 到达城市 IATA 代码（如 "YTY"）
            date: 出发日期（格式：YYYY-MM-DD）
            include_transfer: 是否保留中转航班（False=只保留直飞）
            include_shared: 是否保留共享航班（False=剔除含共享航段的行程）
            timeout_ms: 超时时间（毫秒）

        Returns:
            SearchResult 搜索结果对象
        """
        result = SearchResult(dep=dep, arr=arr, date=date)

        try:
            # 访问主页建立会话/cookies（首次查询时才需要，会话建立后跳过）
            self._ensure_session(timeout_ms)

            # 构建搜索 URL
            url = (
                f"https://flights.ctrip.com/online/list/oneway-{dep.lower()}-{arr.lower()}"
                f"?depdate={date}"
            )

            # 等待 batchSearch 响应到达（事件等待，响应一到立即继续）
            logger.info("Navigating to: %s", url)
            captured = {}

            def _on_response(resp):
                if "batchsearch" in resp.url.lower() and "json" not in captured:
                    try:
                        captured["json"] = resp.json()
                    except Exception as e:
                        captured["err"] = str(e)

            self.page.on("response", _on_response)
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # batchSearch 由页面 JS 加载后发起，轮询等待其在回调中被读取
                deadline = time.time() + timeout_ms / 1000
                while "json" not in captured and "err" not in captured:
                    if time.time() > deadline:
                        break
                    self.page.wait_for_timeout(200)
            finally:
                self.page.remove_listener("response", _on_response)

            body = captured.get("json")
            result.raw_response = body
            if body is None:
                err = captured.get("err", "batchSearch response not captured (timeout)")
                result.error = f"batchSearch response not captured: {err}"
                logger.error(result.error)
                return result

            # 响应成功判断
            if body.get("status") != 0 or body.get("msg") != "success":
                result.error = f"API error: status={body.get('status')}, msg={body.get('msg')}"
                logger.error(result.error)
                return result

            itineraries = body.get("data", {}).get("flightItineraryList", [])
            flights = [_parse_itinerary(it) for it in itineraries]

            # 中转筛选：默认只保留直飞
            if not include_transfer:
                flights = [f for f in flights if f.transfer_count == 0]

            # 共享航班筛选：默认剔除任一航段为共享的行程
            if not include_shared:
                flights = [f for f in flights if not any(s.is_shared for s in f.segments)]

            result.flights = flights
            result.success = len(flights) > 0
            if not result.success:
                result.error = "No flights found"

            logger.info("Found %d flights for %s -> %s (%s)", len(flights), dep, arr, date)

        except Exception as e:
            result.error = str(e)
            logger.error("Search failed: %s", e)

        return result

    def batch_search(
        self,
        queries: list[dict],
        include_transfer: bool = False,
        include_shared: bool = False,
        delay_range: tuple = (2, 5),
    ) -> list:
        """
        批量搜索

        Args:
            queries: 查询列表，每个元素为 dict: {dep, arr, date}
            include_transfer: 是否保留中转航班
            include_shared: 是否保留共享航班（False=剔除含共享航段的行程）
            delay_range: 查询间随机延迟范围（秒）

        Returns:
            SearchResult 列表
        """
        results = []
        for i, q in enumerate(queries):
            logger.info(
                "Batch search %d/%d: %s -> %s (%s)",
                i + 1, len(queries), q["dep"], q["arr"], q["date"]
            )
            result = self.search(
                dep=q["dep"],
                arr=q["arr"],
                date=q["date"],
                include_transfer=include_transfer,
                include_shared=include_shared,
            )
            results.append(result)

            # 随机延迟，避免被检测
            if i < len(queries) - 1:
                delay = random.uniform(*delay_range)
                logger.debug("Waiting %.1f seconds before next search", delay)
                time.sleep(delay)

        return results


def main():
    """CLI 入口"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=== 携程机票纯浏览器自动化爬取（响应体解析版） ===\n")

    queries = [
        {"dep": "CKG", "arr": "YTY", "date": "2026-09-01"},  # 重庆→扬州
        {"dep": "CKG", "arr": "YTY", "date": "2026-09-02"},
        {"dep": "CKG", "arr": "YTY", "date": "2026-09-03"},
        {"dep": "SHA", "arr": "PEK", "date": "2026-09-01"},  # 上海→北京
    ]

    with FlightCrawler(headless=True) as crawler:
        print(f"执行 {len(queries)} 个查询...\n")
        results = crawler.batch_search(queries)

        # 显示结果
        print("\n=== 结果 ===\n")
        for r in results:
            status = "[OK]" if r.success else "[FAIL]"
            print(f"{status} {r.dep} -> {r.arr} ({r.date})")
            if r.success:
                print(f"   找到 {len(r.flights)} 个航班:")
                for f in r.flights[:3]:  # 只显示前 3 个
                    first_seg = f.segments[0]
                    shared = f" (共享:{first_seg.operate_flight_no})" if first_seg.is_shared else ""
                    print(
                        f"   - {first_seg.flight_no}{shared}: 成人¥{f.adult_price} 儿童¥{f.child_price} "
                        f"{f.cabin}/{f.seat_class} 行李{f.baggage_kg}KG "
                        f"[免机建:{f.free_airport_fee} 免燃油:{f.free_fuel_fee}] "
                        f"({first_seg.departure_time} -> {f.segments[-1].arrival_time})"
                    )
                if len(r.flights) > 3:
                    print(f"   ... 还有 {len(r.flights) - 3} 个航班")
            else:
                print(f"   错误: {r.error}")
            print()

        # 保存详细结果到 JSON
        output_path = __file__.replace("flight_crawler.py", "results.json")
        save_data = []
        for r in results:
            save_data.append({
                "dep": r.dep,
                "arr": r.arr,
                "date": r.date,
                "success": r.success,
                "error": r.error,
                "flights": [f.to_dict() for f in r.flights],
            })
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        print(f"详细结果已保存到: {output_path}")


if __name__ == "__main__":
    # 通过 PYTHONIOENCODING=utf-8 环境变量运行可避免 Windows GBK 编码问题
    main()
