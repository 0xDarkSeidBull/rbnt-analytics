"""Fix 1: chart tooltips leaking text (unescaped quotes in data-tip).
Fix 2: wraps job - start at WRBNT creation block, checkpoint every slice, time budget."""
import os, re, sys

B = os.path.dirname(os.path.abspath(__file__))

jp = os.path.join(B, "static/js/app.js")
js = open(jp).read()
old = 'data-tip="<b>${esc(d.label)}</b><br>${tipLines}"'
new = 'data-tip="${esc(`<b>${esc(d.label)}</b><br>${tipLines}`)}"'
if new in js:
    print("skip  app.js tooltip (already fixed)")
elif js.count(old) == 2:
    open(jp, "w").write(js.replace(old, new))
    print("ok    app.js tooltip")
else:
    sys.exit(f"FAIL  app.js tooltip anchor found {js.count(old)}x")

np_ = os.path.join(B, "jobs/native_chain.py")
src = open(np_).read()
if "_wrbnt_start_block" in src:
    print("skip  native_chain.py wraps (already fixed)")
else:
    a = src.index("def wraps(")
    b = src.index("# ---------------- richlist")
    new_fn = '''def _wrbnt_start_block():
    """First block worth scanning: WRBNT contract creation (falls back to 1)."""
    try:
        res = routescan.api({"module": "contract", "action": "getcontractcreation",
                             "contractaddresses": WRBNT})
        r0 = res[0]
        if r0.get("blockNumber"):
            return int(r0["blockNumber"])
        return int(w3().eth.get_transaction_receipt(r0["txHash"])["blockNumber"])
    except Exception:
        return 1


def _hex0x(v):
    s = ("0x" + v.hex()) if isinstance(v, (bytes, bytearray)) else str(v)
    s = s if s.startswith("0x") else "0x" + s
    return s.replace("0x0x", "0x").lower()


def wraps(budget_secs=240, slice_blocks=20000):
    ensure_schema()
    t0 = time.time()
    head = w3().eth.block_number
    safe = head - 1600  # node serves logs for finalized range only
    cp = db.meta_get("native_wrap_block")
    lo = int(cp) if cp else _wrbnt_start_block()
    start, n = lo, 0
    while lo <= safe and time.time() - t0 < budget_secs:
        hi = min(safe, lo + slice_blocks - 1)
        logs = get_logs_chunked(WRBNT.lower(), [[TOPIC_WRBNT_DEPOSIT, TOPIC_WRBNT_WITHDRAWAL]], lo, hi)
        rows = []
        for lg in logs:
            kind = "wrap" if _hex0x(lg["topics"][0]) == TOPIC_WRBNT_DEPOSIT else "unwrap"
            wallet = "0x" + _hex0x(lg["topics"][1])[-40:]
            val = int.from_bytes(bytes(lg["data"]), "big")
            rows.append((_hex0x(lg["transactionHash"]), lg["logIndex"], lg["blockNumber"],
                         _ts(lg["blockNumber"]), kind, wallet, str(val), val / 1e18))
        if rows:
            db.exmany("""INSERT OR IGNORE INTO native_wraps(tx_hash,log_index,block,ts,kind,wallet,value_raw,value_rbnt)
                         VALUES(?,?,?,?,?,?,?,?)""", rows)
        n += len(rows)
        db.meta_set("native_wrap_block", hi + 1)  # checkpoint every slice
        lo = hi + 1
    return n, f"blocks {start}-{lo - 1} events={n} remaining={max(0, safe - lo + 1)}", time.time() - t0


'''
    open(np_, "w").write(src[:a] + new_fn + src[b:])
    print("ok    native_chain.py wraps")
print("done")
