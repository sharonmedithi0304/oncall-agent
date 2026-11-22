import json, time, urllib.request
from diagnose import diagnose

BASE = "http://127.0.0.1:8000"

def post(path):
    req = urllib.request.Request(BASE + path, method="POST")
    return json.loads(urllib.request.urlopen(req).read())

def probe(n=10):
    lat, err = [], 0
    for _ in range(n):
        t = time.time()
        try:
            urllib.request.urlopen(urllib.request.Request(BASE + "/checkout", method="POST"), timeout=15)
            lat.append(round((time.time() - t) * 1000))
        except Exception:
            err += 1
    lat.sort()
    p95 = lat[max(int(len(lat) * 0.95) - 1, 0)] if lat else None
    return {"p95_ms": p95, "errors": err}

if __name__ == "__main__":
    print("Probing broken service...")
    before = probe()
    print("BEFORE:", before)
    ev, d = diagnose()
    print(json.dumps(d, indent=2))
    if input("\nApprove fix (roll back pool fault)? [y/N] ").strip().lower() == "y":
        post("/fault/pool?on=false")
        after = probe()
        print("BEFORE:", before)
        print("AFTER: ", after)
        ok = after["errors"] == 0 and (after["p95_ms"] or 9999) < 500
        print("RECOVERED" if ok else "NOT RECOVERED")