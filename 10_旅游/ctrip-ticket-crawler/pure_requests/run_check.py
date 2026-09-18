"""Runner: syntax-check modules, verify sign, and run the pure-requests attempt."""
import json
import os
import sys
import py_compile

# Ensure the project root is on sys.path so `pure_requests` is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from pure_requests import ctrip_flight
from pure_requests.ctrip_flight import compute_sign, generate_transaction_id

# 1) syntax check
py_compile.compile(os.path.join(_HERE, "ctrip_flight.py"), doraise=True)
py_compile.compile(os.path.join(_HERE, "sign_verify.py"), doraise=True)
print("SYNTAX OK: ctrip_flight.py, sign_verify.py")

# 2) sign verification against the live browser trace
tx = "8992e1c46f7942da9066ad5a71ca5f0c"
got = compute_sign(tx, "CKG", "YTY", "2026-09-01")
print(f"sign for live trace : {got}  (expected e0415e976e9a3972fd0afd39a923897b)  MATCH={got == 'e0415e976e9a3972fd0afd39a923897b'}")

# 3) show the request that WOULD be sent (reproducible part)
gen_tx = generate_transaction_id()
hdrs = ctrip_flight.build_headers(gen_tx, "CKG", "YTY", "2026-09-01")
body = ctrip_flight.build_search_body(gen_tx, "CKG", "YTY", "2026-09-01")
print("\nReproducible transactionID:", gen_tx)
print("Reproducible sign        :", compute_sign(gen_tx, "CKG", "YTY", "2026-09-01"))
print("Reproducible headers (no token):")
for k, v in hdrs.items():
    print(f"  {k}: {v}")
print("Reproducible body keys  :", list(body.keys()))
print("  flightSegments[0]     :", json.dumps(body["flightSegments"][0], ensure_ascii=False))

# 4) attempt the actual pure-requests call
print("\n--- Attempting pure-requests batchSearch (no anti-bot token) ---")
res = ctrip_flight.attempt_batch_search("CKG", "YTY")
print(json.dumps(res, ensure_ascii=False, indent=2))
