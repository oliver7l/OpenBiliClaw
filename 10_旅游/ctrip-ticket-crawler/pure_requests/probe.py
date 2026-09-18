"""Probe: characterize the 432 WhaleGuard block and check for a curl binary."""
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

url = "https://flights.ctrip.com/online/list/oneway-ckg-yty?depdate=2026-09-01"
r = requests.get(url, headers={"User-Agent": UA}, timeout=30)

print("=== 432 WhaleGuard block characterization ===")
print("URL            :", url)
print("status code    :", r.status_code)
print("reason         :", r.reason)
print("response headers:")
for k, v in r.headers.items():
    print(f"  {k}: {v}")
print("full body (repr):", repr(r.text))
print("full body (len) :", len(r.text))
