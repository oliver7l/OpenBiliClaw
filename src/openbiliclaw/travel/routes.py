"""Travel budget API routes — flight prices, budget document, and itinerary.

Endpoints:
- GET /api/travel/flights    — lowest tax-inclusive price per monitored route
- GET /api/travel/doc        — raw budget markdown (for in-app rendering)
- GET /api/travel/overview   — structured summary (plans, totals, per-person)
- GET /api/travel/itinerary  — trip itinerary, members, and checklist from travel.db

The data directory is configured via ``[travel] data_path`` in config.toml.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from openbiliclaw.config import _project_root

logger = logging.getLogger(__name__)

# Airport code → Chinese label
CITY_LABELS: dict[str, str] = {
    "TYN": "太原",
    "SZX": "深圳",
    "CKG": "重庆",
    "URC": "乌鲁木齐",
    "CAN": "广州",
}

# Baseline tax-inclusive adult prices (¥) for alert comparison
BASELINES: dict[str, int] = {
    "TYN-URC": 670,
    "SZX-URC": 2030,
    "CKG-URC": 790,
    "URC-TYN": 975,
    "URC-SZX": 1920,
    "URC-CKG": 1070,
}

AIRPORT_FEE = 50
FUEL_FEE = 70


def build_travel_router(*, data_path: str, budget_doc: str, flights_json: str) -> APIRouter:
    """Create the travel router with resolved data paths."""
    router = APIRouter(prefix="/api/travel", tags=["travel"])

    base = Path(data_path).expanduser() if data_path else None

    def _resolve(rel: str) -> Path | None:
        if base is None:
            return None
        p = base / rel
        return p if p.exists() else None

    def _tax_inclusive(flight: dict[str, Any]) -> int:
        """Compute adult tax-inclusive price from a flight record."""
        price = int(flight.get("adult_price", 0) or 0)
        if not flight.get("free_airport_fee"):
            price += AIRPORT_FEE
        if not flight.get("free_fuel_fee"):
            price += FUEL_FEE
        return price

    @router.get("/flights")
    def get_flights() -> dict[str, Any]:
        """Return lowest tax-inclusive price for each monitored route."""
        path = _resolve(flights_json) if base else None
        if path is None:
            raise HTTPException(status_code=404, detail="旅行数据目录未配置或机票文件不存在")

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.exception("Failed to read flights JSON")
            raise HTTPException(status_code=500, detail=f"读取机票数据失败: {exc}") from exc

        routes: list[dict[str, Any]] = []
        for item in raw:
            dep = item.get("dep", "")
            arr = item.get("arr", "")
            date = item.get("date", "")
            key = f"{dep}-{arr}"
            success = item.get("success", False)
            flights = item.get("flights", []) or []

            route_info: dict[str, Any] = {
                "route": key,
                "dep": dep,
                "arr": arr,
                "dep_city": CITY_LABELS.get(dep, dep),
                "arr_city": CITY_LABELS.get(arr, arr),
                "date": date,
                "success": success,
                "baseline": BASELINES.get(key),
                "flight_count": len(flights),
            }

            if success and flights:
                # Pick lowest tax-inclusive adult price
                best = min(flights, key=_tax_inclusive)
                lowest = _tax_inclusive(best)
                route_info.update(
                    lowest_price=lowest,
                    lowest_flight=best.get("itinerary_id", ""),
                    lowest_departure=best.get("departure_time", ""),
                    lowest_arrival=best.get("arrival_time", ""),
                    lowest_airline=(best.get("segments") or [{}])[0].get("airline_name", ""),
                    adult_fare=int(best.get("adult_price", 0) or 0),
                    child_fare=float(best.get("child_price", 0) or 0),
                    baggage_kg=best.get("baggage_kg", 0),
                    seat_count=best.get("ticket_count", 0),
                )
                baseline = BASELINES.get(key)
                if baseline:
                    diff = baseline - lowest
                    pct = round(diff / baseline * 100, 1) if baseline else 0
                    route_info["vs_baseline"] = {
                        "diff": diff,
                        "pct": pct,
                        "alert": diff >= 200 or pct >= 10,
                    }
                # Top 3 cheapest for reference
                sorted_flights = sorted(flights, key=_tax_inclusive)[:3]
                route_info["top3"] = [
                    {
                        "flight": f.get("itinerary_id", ""),
                        "departure": f.get("departure_time", ""),
                        "arrival": f.get("arrival_time", ""),
                        "price_tax_inclusive": _tax_inclusive(f),
                        "adult_fare": int(f.get("adult_price", 0) or 0),
                        "airline": (f.get("segments") or [{}])[0].get("airline_name", ""),
                    }
                    for f in sorted_flights
                ]
            else:
                route_info["error"] = item.get("error", "查询失败")

            routes.append(route_info)

        alerts = [r for r in routes if r.get("vs_baseline", {}).get("alert")]
        return {
            "updated_at": path.stat().st_mtime if path else None,
            "source": str(path),
            "routes": routes,
            "alerts": alerts,
            "fee_note": f"含税=票面+机建{AIRPORT_FEE}+燃油{FUEL_FEE}；儿童=票面5折+燃油半价",
        }

    @router.get("/documents")
    def list_documents() -> dict[str, Any]:
        """List all budget markdown documents in the travel data directory."""
        if base is None or not base.exists():
            return {"docs": []}
        docs: list[dict[str, Any]] = []
        for p in sorted(base.glob("*.md")):
            try:
                docs.append(
                    {
                        "name": p.name,
                        "stem": p.stem,
                        "updated_at": p.stat().st_mtime,
                        "size": p.stat().st_size,
                    }
                )
            except OSError:
                continue
        return {"docs": docs}

    @router.get("/doc")
    def get_budget_doc(doc: str = "") -> dict[str, Any]:
        """Return the raw budget markdown document (optional ?doc=<filename>)."""
        name = (doc or budget_doc).lstrip("/")
        path = (base / name) if base else None
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="旅行数据目录未配置或预算文档不存在")
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to read budget doc")
            raise HTTPException(status_code=500, detail=f"读取预算文档失败: {exc}") from exc
        return {
            "title": path.stem,
            "name": path.name,
            "content": content,
            "updated_at": path.stat().st_mtime,
            "source": str(path),
        }

    @router.get("/overview")
    def get_overview(doc: str = "") -> dict[str, Any]:
        """Structured budget summary extracted from the markdown doc (?doc=<filename>)."""
        name = (doc or budget_doc).lstrip("/")
        path = (base / name) if base else None
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="旅行数据目录未配置或预算文档不存在")

        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"读取预算文档失败: {exc}") from exc

        # Extract total budget table
        totals: list[dict[str, str]] = []
        in_total = False
        for line in text.splitlines():
            if "全款预算" in line or "总预算" in line:
                in_total = True
                continue
            if in_total:
                if line.startswith("|") and "---" not in line and "项目" not in line:
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    if len(cells) >= 2 and cells[0]:
                        note = cells[2] if len(cells) > 2 else ""
                        totals.append({"item": cells[0], "amount": cells[1], "note": note})
                elif (
                    line.startswith("##")
                    or line.startswith("---")
                    or (line.startswith(">") and totals)
                ):
                    in_total = False

        # Extract three-plan comparison
        plans: list[dict[str, str]] = []
        in_plans = False
        for line in text.splitlines():
            if "三方案汇总" in line:
                in_plans = True
                continue
            if in_plans:
                if line.startswith("|") and "---" not in line and "项目" not in line:
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    if len(cells) >= 4 and cells[0]:
                        plans.append(
                            {
                                "item": cells[0],
                                "plan_a": cells[1],
                                "plan_b": cells[2],
                                "plan_c": cells[3],
                            }
                        )
                elif line.startswith("##") or line.startswith("---"):
                    in_plans = False

        return {
            "title": "新疆旅行预算概览",
            "totals": totals,
            "plans": plans,
            "doc_updated_at": path.stat().st_mtime,
        }

    @router.get("/itinerary")
    def get_itinerary() -> dict[str, Any]:
        """Return trip itinerary, members, and checklist from travel.db."""
        db_path = (_project_root() / "data" / "travel.db")
        if not db_path.exists():
            raise HTTPException(status_code=404, detail="旅行数据库不存在")

        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            # Get latest trip
            c.execute("SELECT * FROM trips ORDER BY id DESC LIMIT 1")
            trip_row = c.fetchone()
            if not trip_row:
                conn.close()
                return {"trip": None, "days": [], "members": [], "checklist": []}

            trip = dict(trip_row)
            trip_id = trip["id"]

            # Get days
            c.execute(
                "SELECT * FROM trip_days WHERE trip_id = ? ORDER BY day_number",
                (trip_id,),
            )
            days = [dict(r) for r in c.fetchall()]

            # Get members
            c.execute(
                "SELECT id, name, relation, age, notes FROM trip_members WHERE trip_id = ? ORDER BY id",
                (trip_id,),
            )
            members = [dict(r) for r in c.fetchall()]

            # Get checklist grouped by category
            c.execute(
                "SELECT id, category, item, owner, done, notes "
                "FROM trip_checklist WHERE trip_id = ? ORDER BY category, id",
                (trip_id,),
            )
            checklist = [dict(r) for r in c.fetchall()]

            conn.close()
            return {
                "trip": trip,
                "days": days,
                "members": members,
                "checklist": checklist,
            }
        except Exception as exc:
            logger.exception("Failed to read itinerary")
            raise HTTPException(status_code=500, detail=f"读取行程数据失败: {exc}") from exc

    @router.get("/expenses")
    def get_expenses() -> dict[str, Any]:
        """Return trip expenses from trip_expenses table."""
        db_path = (_project_root() / "data" / "travel.db")
        if not db_path.exists():
            raise HTTPException(status_code=404, detail="旅行数据库不存在")

        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            # Get latest trip
            c.execute("SELECT id, title, budget, people_count FROM trips ORDER BY id DESC LIMIT 1")
            trip_row = c.fetchone()
            if not trip_row:
                conn.close()
                return {"trip": None, "expenses": [], "summary": {}}

            trip = dict(trip_row)
            trip_id = trip["id"]

            # Get all expenses
            c.execute(
                "SELECT id, category, item, detail, amount, created_at "
                "FROM trip_expenses WHERE trip_id = ? ORDER BY category, id",
                (trip_id,),
            )
            expenses = [dict(r) for r in c.fetchall()]

            # Summary by category
            c.execute(
                "SELECT category, SUM(amount) as total, COUNT(*) as count "
                "FROM trip_expenses WHERE trip_id = ? GROUP BY category ORDER BY total DESC",
                (trip_id,),
            )
            by_category = [dict(r) for r in c.fetchall()]

            total = sum(e["amount"] for e in expenses)
            people = trip.get("people_count", 1) or 1

            conn.close()
            return {
                "trip": trip,
                "expenses": expenses,
                "summary": {
                    "by_category": by_category,
                    "total": total,
                    "per_person": round(total / people, 0) if people else 0,
                    "people_count": people,
                },
            }
        except Exception as exc:
            logger.exception("Failed to read expenses")
            raise HTTPException(status_code=500, detail=f"读取费用数据失败: {exc}") from exc

    @router.get("/flights-detail")
    def get_flights_detail() -> dict[str, Any]:
        """Return detailed flight information from trip_flights table."""
        db_path = (_project_root() / "data" / "travel.db")
        if not db_path.exists():
            raise HTTPException(status_code=404, detail="旅行数据库不存在")

        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            c.execute("SELECT id, title FROM trips ORDER BY id DESC LIMIT 1")
            trip_row = c.fetchone()
            if not trip_row:
                conn.close()
                return {"trip": None, "departures": [], "returns": []}

            trip = dict(trip_row)
            trip_id = trip["id"]

            # 去程
            c.execute(
                "SELECT * FROM trip_flights WHERE trip_id = ? AND flight_type = '去程' ORDER BY departure_time",
                (trip_id,),
            )
            departures = [dict(r) for r in c.fetchall()]

            # 返程
            c.execute(
                "SELECT * FROM trip_flights WHERE trip_id = ? AND flight_type = '返程' ORDER BY departure_time",
                (trip_id,),
            )
            returns = [dict(r) for r in c.fetchall()]

            total_price = sum(f["price"] or 0 for f in departures + returns)

            conn.close()
            return {
                "trip": trip,
                "departures": departures,
                "returns": returns,
                "summary": {
                    "departure_count": len(departures),
                    "return_count": len(returns),
                    "total_price": total_price,
                },
            }
        except Exception as exc:
            logger.exception("Failed to read flights")
            raise HTTPException(status_code=500, detail=f"读取航班数据失败: {exc}") from exc

    @router.get("/hotels")
    def get_hotels() -> dict[str, Any]:
        """Return hotel information from trip_hotels table."""
        db_path = (_project_root() / "data" / "travel.db")
        if not db_path.exists():
            raise HTTPException(status_code=404, detail="旅行数据库不存在")

        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            c.execute("SELECT id, title FROM trips ORDER BY id DESC LIMIT 1")
            trip_row = c.fetchone()
            if not trip_row:
                conn.close()
                return {"trip": None, "hotels": []}

            trip = dict(trip_row)
            trip_id = trip["id"]

            c.execute(
                "SELECT * FROM trip_hotels WHERE trip_id = ? ORDER BY day_number",
                (trip_id,),
            )
            hotels = [dict(r) for r in c.fetchall()]

            included_count = sum(1 for h in hotels if h["included_in_tour"] == 1)
            self_paid_count = sum(1 for h in hotels if h["included_in_tour"] == 0)
            self_paid_total = sum(h["price"] or 0 for h in hotels if h["included_in_tour"] == 0)

            conn.close()
            return {
                "trip": trip,
                "hotels": hotels,
                "summary": {
                    "total_nights": len(hotels),
                    "included_in_tour": included_count,
                    "self_paid": self_paid_count,
                    "self_paid_total": self_paid_total,
                },
            }
        except Exception as exc:
            logger.exception("Failed to read hotels")
            raise HTTPException(status_code=500, detail=f"读取住宿数据失败: {exc}") from exc

    return router
