"""
ctrip_flight.py - Pure-requests crawler for flights.ctrip.com flight search.

What IS reproducible without a browser:
  - transactionID generation (uuid4 hex, 32 chars)
  - sign header = md5(transactionID + depCity + arrCity + depDate)
  - JSON request body structure
  - All static HTTP headers (User-Agent, Content-Type, etc.)

What is NOT reproducible without executing JavaScript:
  - token header      (WhaleGuard anti-bot token, computed by client-side JS)
  - w-payload-source  (anti-bot payload, computed by client-side JS)
  - anti-bot cookies set by JS

Result: a pure-requests call gets HTTP 432 (WhaleGuard block).
The module documents this and shows exactly what a valid request looks like.
"""

import hashlib
import json
import uuid
import requests


# 1. Reproducible crypto

def compute_sign(transaction_id: str, dep: str, arr: str, dep_date: str) -> str:
    """Compute the reproducible 'sign' header.

    Verified against a live browser trace:
        md5(transactionID + departureCityCode + arrivalCityCode + departureDate)
    """
    src = transaction_id + dep + arr + dep_date
    return hashlib.md5(src.encode("utf-8")).hexdigest()


def generate_transaction_id() -> str:
    """Generate a 32-char hex transactionID (uuid4 hex)."""
    return uuid.uuid4().hex


# 2. City metadata (extend as needed)

# IATA code -> (city name CN, city id, province id, time zone offset minutes)
_CITY_INFO = {
    "CKG": ("重庆", 4, 4, 480),
    "YTY": ("扬州", 15, 15, 480),
    "SHA": ("上海", 2, 2, 480),
    "PVG": ("上海", 2, 2, 480),
    "PEK": ("北京", 1, 1, 480),
    "PKX": ("北京", 1, 1, 480),
    "CAN": ("广州", 3, 3, 480),
    "SZX": ("深圳", 5, 5, 480),
    "CTU": ("成都", 6, 6, 480),
    "HGH": ("杭州", 7, 7, 480),
    "WUH": ("武汉", 8, 8, 480),
    "XIY": ("西安", 9, 9, 480),
    "NKG": ("南京", 10, 10, 480),
    "XMN": ("厦门", 11, 11, 480),
    "KMG": ("昆明", 12, 12, 480),
    "DLC": ("大连", 13, 13, 480),
    "TAO": ("青岛", 14, 14, 480),
}

_DEFAULT_CITY = ("未知", 0, 0, 480)


def _city(code: str):
    """Return (name, city_id, province_id, tz_offset) for an IATA code."""
    return _CITY_INFO.get(code.upper(), _DEFAULT_CITY)


# 3. Request body builder

def build_search_body(
    transaction_id: str,
    dep: str,
    arr: str,
    dep_date: str,
    adult: int = 1,
    child: int = 0,
    infant: int = 0,
    cabin: str = "Y_S",
) -> dict:
    """Build the JSON body for the batchSearch API."""
    dep_name, dep_city_id, dep_prov_id, dep_tz = _city(dep)
    arr_name, arr_city_id, arr_prov_id, arr_tz = _city(arr)

    return {
        "adultCount": adult,
        "childCount": child,
        "infantCount": infant,
        "flightWay": "S",
        "cabin": cabin,
        "scope": "d",
        "segmentNo": 1,
        "transactionID": transaction_id,
        "flightSegments": [
            {
                "departureCityCode": dep.upper(),
                "arrivalCityCode": arr.upper(),
                "departureCityName": dep_name,
                "arrivalCityName": arr_name,
                "departureDate": dep_date,
                "departureCountryId": 1,
                "departureCountryName": "中国",
                "departureCountryCode": "CN",
                "departureProvinceId": dep_prov_id,
                "departureCityId": dep_city_id,
                "arrivalCountryId": 1,
                "arrivalCountryName": "中国",
                "arrivalCountryCode": "CN",
                "arrivalProvinceId": arr_prov_id,
                "arrivalCityId": arr_city_id,
                "departureCityTimeZone": dep_tz,
                "arrivalCityTimeZone": arr_tz,
                "timeZone": dep_tz,
            }
        ],
        "directFlight": False,
        "extGlobalSwitches": {
            "useAllRecommendSwitch": True,
            "unfoldPriceListSwitch": True,
        },
        "noRecommend": False,
        "extensionAttributes": {
            "LoggingSampling": False,
            "isFlightIntlNewUser": False,
        },
    }


# 4. Header builder

def build_headers(
    transaction_id: str,
    dep: str,
    arr: str,
    dep_date: str,
) -> dict:
    """Build the HTTP headers for the batchSearch API.

    NOTE: The 'token' and 'w-payload-source' headers are WhaleGuard anti-bot
    values computed by client-side JavaScript. They CANNOT be reproduced in a
    pure-requests approach. Without them the server returns HTTP 432.
    We include every reproducible header here.
    """
    sign = compute_sign(transaction_id, dep, arr, dep_date)
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) "
            "Gecko/20100101 Firefox/154.0"
        ),
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": (
            f"https://flights.ctrip.com/online/list/oneway-{dep.lower()}-{arr.lower()}"
            f"?depdate={dep_date}"
        ),
        "Content-Type": "application/json;charset=utf-8",
        "Origin": "https://flights.ctrip.com",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Cache-Control": "no-cache",
        "sign": sign,
        "transactionID": transaction_id,
        "scope": "d",
    }


# 5. Attempt the request (for demonstration / debugging)

SEARCH_URL = (
    "https://flights.ctrip.com/international/search/api/search/batchSearch"
    "?v=0.014012161395707823"
)


def attempt_batch_search(
    dep: str,
    arr: str,
    dep_date: str = "2026-09-01",
    token: str | None = None,
    cookies: dict | None = None,
) -> dict:
    """Attempt the batchSearch POST and return a result summary.

    Without a valid 'token' (WhaleGuard anti-bot token from the browser),
    the server returns HTTP 432. Pass token=... to include it.
    """
    tx = generate_transaction_id()
    headers = build_headers(tx, dep, arr, dep_date)
    body = build_search_body(tx, dep, arr, dep_date)

    if token:
        headers["token"] = token

    session = requests.Session()
    if cookies:
        session.cookies.update(cookies)

    try:
        resp = session.post(SEARCH_URL, headers=headers, json=body, timeout=30)
        result = {
            "status_code": resp.status_code,
            "reason": resp.reason,
            "content_type": resp.headers.get("Content-Type", ""),
        }
        try:
            result["json"] = resp.json()
        except Exception:
            result["body_preview"] = resp.text[:500]
        return result
    except Exception as e:
        return {"error": str(e)}


# 6. CLI demo

if __name__ == "__main__":
    print("=== ctrip_flight.py demo ===\n")
    tx = generate_transaction_id()
    print("transactionID:", tx)
    print("sign         :", compute_sign(tx, "CKG", "YTY", "2026-09-01"))
    print()
    body = build_search_body(tx, "CKG", "YTY", "2026-09-01")
    print("body:")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print()
    hdrs = build_headers(tx, "CKG", "YTY", "2026-09-01")
    print("headers (reproducible subset):")
    for k, v in hdrs.items():
        print(f"  {k}: {v}")
    print()
    print("--- Live request attempt (no anti-bot token, expect HTTP 432) ---")
    res = attempt_batch_search("CKG", "YTY")
    print(json.dumps(res, ensure_ascii=False, indent=2))
