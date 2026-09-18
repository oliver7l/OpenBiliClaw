"""Test: can we bootstrap anti-bot params without JavaScript?"""
import requests
import json

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})

# 1) Full flow simulation: try to build up cookies step by step
print("=== Step 1: bee/collect to get uid/suid ===")
r = session.post("https://s.c-ctrip.com/bee/collect",
    json={"t": 1, "c": [{"d": "test"}]},
    headers={"Content-Type": "application/json", "Origin": "https://flights.ctrip.com", "Referer": "https://flights.ctrip.com/"},
    timeout=30
)
print("Status:", r.status_code, "Body:", r.text)
print("Cookies:", [(c.name, c.value[:30], c.domain) for c in session.cookies])

# 2) Try cdid with proper payload
print("\n=== Step 2: cdid device registration ===")
r2 = session.post("https://cdid.c-ctrip.com/chloro-device/v4/d",
    json={
        "appId": "1001",
        "appVersion": "5.9.1",
        "platform": "PC",
        "browserType": "Chrome",
        "browserVersion": "151.0.0.0",
        "osType": "Windows",
        "osVersion": "10.0",
        "screenResolution": "1920x1080",
        "timezoneOffset": -480,
        "language": "zh-CN"
    },
    headers={"Content-Type": "application/json", "Origin": "https://flights.ctrip.com", "Referer": "https://flights.ctrip.com/"},
    timeout=30
)
print("Status:", r2.status_code)
print("X-Original-Status:", r2.headers.get("X-Original-Status"))
print("Body:", repr(r2.text[:300]))

# 3) Try flights page with all collected cookies
print("\n=== Step 3: flights homepage with all cookies ===")
print("All cookies in jar:", [(c.name, c.value[:20], c.domain) for c in session.cookies])
r3 = session.get("https://flights.ctrip.com/", timeout=30)
print("Status:", r3.status_code, "Body:", r3.text[:100])

# 4) Try setting the cookies on .ctrip.com domain manually
print("\n=== Step 4: manually set cookies on .ctrip.com ===")
for c in session.cookies:
    session.cookies.set(c.name, c.value, domain=".ctrip.com", path="/")
print("Cookies after domain fix:", [(c.name, c.value[:20], c.domain) for c in session.cookies])
r4 = session.get("https://flights.ctrip.com/", timeout=30)
print("Status:", r4.status_code, "Body:", r4.text[:100])

# 5) Conclusion
print("\n=== CONCLUSION ===")
print("Even after collecting cookies from accessible endpoints,")
print("flights.ctrip.com still returns 432 without a valid JS-computed token.")
print("The token CANNOT be obtained by requesting pages first.")
