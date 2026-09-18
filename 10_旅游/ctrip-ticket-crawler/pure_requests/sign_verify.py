"""Verify the sign computation against the live browser trace captured by Playwright."""
import hashlib
import uuid

# --- Values captured from the live browser trace (Playwright) ---
tx_live = "8992e1c46f7942da9066ad5a71ca5f0c"
dep_live = "CKG"
arr_live = "YTY"
date_live = "2026-09-01"
sign_expected = "e0415e976e9a3972fd0afd39a923897b"

src = tx_live + dep_live + arr_live + date_live
sign_computed = hashlib.md5(src.encode("utf-8")).hexdigest()

print("=== Sign verification against live browser trace ===")
print("source string   :", src)
print("computed sign   :", sign_computed)
print("expected sign   :", sign_expected)
print("MATCH           :", sign_computed == sign_expected)
print()

# --- Show how to generate transactionID (32 hex chars, uuid4 hex) ---
tx_gen = uuid.uuid4().hex
print("generated transactionID (uuid4 hex):", tx_gen, "len:", len(tx_gen))
sign_gen = hashlib.md5((tx_gen + dep_live + arr_live + date_live).encode("utf-8")).hexdigest()
print("sign for generated tx            :", sign_gen)
print()

# --- The JSON request body (from project file 'a' / GlobalSearchCriteria) ---
body = {
    "adultCount": 1,
    "childCount": 0,
    "infantCount": 0,
    "flightWay": "S",
    "cabin": "Y_S",
    "scope": "d",
    "segmentNo": 1,
    "transactionID": tx_gen,
    "flightSegments": [
        {
            "departureCityCode": dep_live,
            "arrivalCityCode": arr_live,
            "departureCityName": "重庆",
            "arrivalCityName": "扬州",
            "departureDate": date_live,
            "departureCountryId": 1,
            "departureCountryName": "中国",
            "departureCountryCode": "CN",
            "departureProvinceId": 4,
            "departureCityId": 4,
            "arrivalCountryId": 1,
            "arrivalCountryName": "中国",
            "arrivalCountryCode": "CN",
            "arrivalProvinceId": 15,
            "arrivalCityId": 15,
            "departureCityTimeZone": 480,
            "arrivalCityTimeZone": 480,
            "timeZone": 480,
        }
    ],
    "directFlight": False,
    "extGlobalSwitches": {"useAllRecommendSwitch": True, "unfoldPriceListSwitch": True},
    "noRecommend": False,
    "extensionAttributes": {"LoggingSampling": False, "isFlightIntlNewUser": False},
}
print("request body (transactionID replaced with generated):")
import json
print(json.dumps(body, ensure_ascii=False, indent=2))
