"""wraps speed-up: batch block timestamps (50 per RPC call) + 5k-block slices."""
import os, sys
B = os.path.dirname(os.path.abspath(__file__))
p = os.path.join(B, "jobs/native_chain.py")
s = open(p).read()
if "_prefetch_ts" in s:
    sys.exit("skip  already patched")

helper = '''def _prefetch_ts(blocks):
    """Fill _ts_cache for many blocks with batched header calls (50 per request)."""
    need = sorted({b for b in blocks if b not in _ts_cache})
    for i in range(0, len(need), 50):
        chunk = need[i:i + 50]
        payload = [{"jsonrpc": "2.0", "id": b, "method": "eth_getBlockByNumber",
                    "params": [hex(b), False]} for b in chunk]
        try:
            j = _session.post(RPC_URL, json=payload, timeout=60).json()
            if isinstance(j, list):
                for x in j:
                    if x.get("result"):
                        _ts_cache[x["id"]] = int(x["result"]["timestamp"], 16)
        except Exception:
            pass  # _ts() falls back to single calls for anything missing


'''
anchor = "def wraps("
old_loop = '''        rows = []
        for lg in logs:
            kind = "wrap"'''
new_loop = '''        if len(_ts_cache) > 20000:
            _ts_cache.clear()
        _prefetch_ts([lg["blockNumber"] for lg in logs])
        rows = []
        for lg in logs:
            kind = "wrap"'''
for a, n in ((anchor, 1), (old_loop, 1), ("def wraps(budget_secs=240, slice_blocks=20000)", 1)):
    if s.count(a) != n:
        sys.exit(f"FAIL anchor x{s.count(a)}: {a[:50]!r}")
s = s.replace(anchor, helper + anchor, 1)
s = s.replace(old_loop, new_loop)
s = s.replace("def wraps(budget_secs=240, slice_blocks=20000)", "def wraps(budget_secs=240, slice_blocks=5000)")
open(p, "w").write(s)
print("ok    native_chain.py wraps speed-up")
