"""Send concurrent checkout requests. Usage: python loadtest.py [num_requests] [concurrency]"""
import sys, json, threading, urllib.request, time

N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
C = int(sys.argv[2]) if len(sys.argv) > 2 else 10
URL = "http://127.0.0.1:8000/checkout"
res = []
lock = threading.Lock()

def worker(k):
    for _ in range(k):
        t = time.time()
        try:
            r = urllib.request.urlopen(urllib.request.Request(URL, method="POST"), timeout=15)
            code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:
            code = 0
        with lock:
            res.append((code, round((time.time() - t) * 1000)))

ts = [threading.Thread(target=worker, args=(N // C,)) for _ in range(C)]
[t.start() for t in ts]; [t.join() for t in ts]
ok = [m for c, m in res if c == 200]
print(f"requests={len(res)} ok={len(ok)} errors={len(res)-len(ok)}")
if ok: print(f"avg ok latency={sum(ok)//len(ok)}ms")
print(json.dumps(json.loads(urllib.request.urlopen("http://127.0.0.1:8000/metrics").read())))
