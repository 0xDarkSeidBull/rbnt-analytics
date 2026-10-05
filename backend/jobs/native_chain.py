"""Native RBNT indexer.

- supply:   Routescan /supply (total + circulating), WRBNT locked native, WRBNT totalSupply
- scan:     forward block scan of top-level txs with value > 0 (native transfers)
- backfill: same scan walking backwards to genesis, in slices
- wraps:    WRBNT Deposit / Withdrawal events (native <-> wrapped flow)
- rich:     native balances for every address the scanner has seen (balancemulti, 20/call)

Limits (shown in UI): internal contract value transfers are not in the transfer
table; richlist is complete only for addresses the scanner has reached so far.
"""
import time

import requests
from web3 import Web3

import db
from sources import routescan
from sources.rpc import w3, get_contract, get_logs_chunked, ERC20_ABI
from config import (RPC_URL, WRBNT, ROUTESCAN_V2, TOPIC_WRBNT_DEPOSIT, TOPIC_WRBNT_WITHDRAWAL,
                    NATIVE_FIRST_WINDOW_BLOCKS, NATIVE_FWD_MAX_BLOCKS, NATIVE_BACK_MAX_BLOCKS,
                    NATIVE_RPC_BATCH, NATIVE_RICH_CALLS)

SCHEMA = """
CREATE TABLE IF NOT EXISTS native_transfers (
  hash TEXT PRIMARY KEY, block INTEGER NOT NULL, ts INTEGER NOT NULL,
  frm TEXT NOT NULL, to_addr TEXT, value_raw TEXT NOT NULL, value_rbnt REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_nt_ts ON native_transfers(ts);
CREATE INDEX IF NOT EXISTS idx_nt_frm ON native_transfers(frm, ts);
CREATE INDEX IF NOT EXISTS idx_nt_to ON native_transfers(to_addr, ts);
CREATE INDEX IF NOT EXISTS idx_nt_val ON native_transfers(value_rbnt);

CREATE TABLE IF NOT EXISTS native_addresses (
  address TEXT PRIMARY KEY, first_block INTEGER, last_block INTEGER,
  last_ts INTEGER, tx_count INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_na_last ON native_addresses(last_ts);

CREATE TABLE IF NOT EXISTS native_wraps (
  tx_hash TEXT NOT NULL, log_index INTEGER NOT NULL, block INTEGER NOT NULL,
  ts INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('wrap','unwrap')),
  wallet TEXT NOT NULL, value_raw TEXT NOT NULL, value_rbnt REAL NOT NULL,
  PRIMARY KEY (tx_hash, log_index));
CREATE INDEX IF NOT EXISTS idx_nw_ts ON native_wraps(ts);
CREATE INDEX IF NOT EXISTS idx_nw_wallet ON native_wraps(wallet);

CREATE TABLE IF NOT EXISTS native_supply_snapshots (
  ts INTEGER PRIMARY KEY, total_rbnt REAL, circulating_rbnt REAL,
  wrbnt_locked_rbnt REAL, wrbnt_supply_rbnt REAL, treasury_native_rbnt REAL,
  price_usd REAL);
"""


def ensure_schema():
    with db._write_lock, db.connect() as c:
        c.executescript(SCHEMA)


def _rbnt(raw):
    return int(raw) / 1e18


# ---------------- supply ----------------

def supply():
    ensure_schema()
    t0 = time.time()
    total = circ = None
    try:
        j = requests.get(ROUTESCAN_V2 + "/supply", timeout=30).json()
        total = float(j.get("totalSupply")) if j.get("totalSupply") is not None else None
        circ = float(j.get("circulatingSupply")) if j.get("circulatingSupply") is not None else None
    except Exception:
        pass
    wa = Web3.to_checksum_address(WRBNT)
    locked = _rbnt(w3().eth.get_balance(wa))
    wsup = _rbnt(get_contract(WRBNT, ERC20_ABI).functions.totalSupply().call())
    tre = 0.0
    for r in db.q("SELECT eth_native_raw FROM treasury"):
        if r["eth_native_raw"]:
            tre += _rbnt(r["eth_native_raw"])
    m = db.one("SELECT price_usd FROM market_snapshots WHERE price_usd IS NOT NULL ORDER BY ts DESC LIMIT 1")
    price = m["price_usd"] if m else None
    db.ex("""INSERT OR REPLACE INTO native_supply_snapshots
             (ts,total_rbnt,circulating_rbnt,wrbnt_locked_rbnt,wrbnt_supply_rbnt,treasury_native_rbnt,price_usd)
             VALUES(?,?,?,?,?,?,?)""", (int(time.time()), total, circ, locked, wsup, tre, price))
    # keep ~90 days of 10-min snapshots
    db.ex("DELETE FROM native_supply_snapshots WHERE ts < ?", (int(time.time()) - 90 * 86400,))
    return 1, f"total={total} circ={circ} locked={locked:.0f}", time.time() - t0


# ---------------- block scanner ----------------

_session = requests.Session()


def _rpc_batch(blocks):
    """eth_getBlockByNumber(full) for many blocks; batch first, sequential fallback."""
    payload = [{"jsonrpc": "2.0", "id": b, "method": "eth_getBlockByNumber",
                "params": [hex(b), True]} for b in blocks]
    for attempt in range(4):
        try:
            r = _session.post(RPC_URL, json=payload, timeout=60)
            j = r.json()
            if isinstance(j, list):
                out = {x["id"]: x.get("result") for x in j}
                if all(out.get(b) for b in blocks):
                    return [out[b] for b in blocks]
            break  # batch not supported or partial: fall back
        except Exception:
            time.sleep(2 * (attempt + 1))
    res = []
    for b in blocks:
        for attempt in range(4):
            try:
                j = _session.post(RPC_URL, json={"jsonrpc": "2.0", "id": b, "method": "eth_getBlockByNumber",
                                                 "params": [hex(b), True]}, timeout=30).json()
                if j.get("result"):
                    res.append(j["result"])
                    break
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
        else:
            raise RuntimeError(f"block {b} unavailable")
    return res


def _ingest(blocks):
    tx_rows, seen = [], {}
    for blk in blocks:
        bn = int(blk["number"], 16)
        ts = int(blk["timestamp"], 16)
        for tx in blk.get("transactions") or []:
            if not isinstance(tx, dict):
                continue
            frm = (tx.get("from") or "").lower()
            to = (tx.get("to") or "").lower() or None
            for a in (frm, to):
                if a:
                    s = seen.setdefault(a, [bn, bn, ts, 0])
                    s[0] = min(s[0], bn); s[1] = max(s[1], bn); s[2] = max(s[2], ts); s[3] += 1
            val = int(tx.get("value") or "0x0", 16)
            if val > 0:
                tx_rows.append((tx["hash"].lower(), bn, ts, frm, to, str(val), val / 1e18))
    if tx_rows:
        db.exmany("""INSERT OR IGNORE INTO native_transfers(hash,block,ts,frm,to_addr,value_raw,value_rbnt)
                     VALUES(?,?,?,?,?,?,?)""", tx_rows)
    if seen:
        db.exmany("""INSERT INTO native_addresses(address,first_block,last_block,last_ts,tx_count)
                     VALUES(?,?,?,?,?) ON CONFLICT(address) DO UPDATE SET
                       first_block=MIN(first_block, excluded.first_block),
                       last_block=MAX(last_block, excluded.last_block),
                       last_ts=MAX(last_ts, excluded.last_ts),
                       tx_count=tx_count+excluded.tx_count""",
                  [(a, v[0], v[1], v[2], v[3]) for a, v in seen.items()])
    return len(tx_rows), len(seen)


def _init_checkpoints(head):
    if db.meta_get("native_fwd_block") is None:
        start = max(1, head - NATIVE_FIRST_WINDOW_BLOCKS)
        db.meta_set("native_fwd_block", start)
        db.meta_set("native_back_block", start - 1)
        db.meta_set("native_scan_origin", start)


def scan_forward():
    ensure_schema()
    t0 = time.time()
    head = w3().eth.block_number
    _init_checkpoints(head)
    lo = int(db.meta_get("native_fwd_block"))
    hi = min(head, lo + NATIVE_FWD_MAX_BLOCKS - 1)
    n_tx = n_addr = 0
    b = lo
    while b <= hi:
        rng = list(range(b, min(b + NATIVE_RPC_BATCH, hi + 1)))
        t, a = _ingest(_rpc_batch(rng))
        n_tx += t; n_addr += a
        b = rng[-1] + 1
        db.meta_set("native_fwd_block", b)
    db.meta_set("native_head_seen", head)
    return n_tx, f"blocks {lo}-{hi} transfers={n_tx} addrs={n_addr} lag={head - hi}", time.time() - t0


def scan_backward():
    ensure_schema()
    t0 = time.time()
    head = w3().eth.block_number
    _init_checkpoints(head)
    hi = int(db.meta_get("native_back_block"))
    if hi < 1:
        return 0, "backfill complete (genesis reached)", 0.0
    lo = max(1, hi - NATIVE_BACK_MAX_BLOCKS + 1)
    n_tx = 0
    b = hi
    while b >= lo:
        rng = list(range(max(lo, b - NATIVE_RPC_BATCH + 1), b + 1))
        t, _ = _ingest(_rpc_batch(rng))
        n_tx += t
        b = rng[0] - 1
        db.meta_set("native_back_block", b)
    return n_tx, f"blocks {lo}-{hi} transfers={n_tx} remaining={max(0, lo - 1)}", time.time() - t0


# ---------------- wrap / unwrap ----------------

_ts_cache = {}


def _ts(bn):
    if bn not in _ts_cache:
        if len(_ts_cache) > 20000:
            _ts_cache.clear()
        _ts_cache[bn] = w3().eth.get_block(bn)["timestamp"]
    return _ts_cache[bn]


def _wrbnt_start_block():
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


# ---------------- richlist ----------------

def _rich_candidates(limit):
    now = int(time.time())
    stale = now - 6 * 3600
    # never-polled first, then most recently active, then stalest
    rows = db.q("""
        SELECT a.address FROM native_addresses a
        LEFT JOIN native_holders h ON h.wallet = a.address
        WHERE h.wallet IS NULL OR h.updated_at < ?
        ORDER BY (h.wallet IS NOT NULL), a.last_ts DESC
        LIMIT ?""", (stale, limit))
    out = [r["address"] for r in rows]
    extra = [WRBNT.lower()] + [r["address"] for r in db.q("SELECT address FROM treasury")]
    for a in extra:
        if a not in out:
            out.insert(0, a)
    return out[:limit]


def richlist():
    ensure_schema()
    t0 = time.time()
    now = int(time.time())
    cands = _rich_candidates(NATIVE_RICH_CALLS * 20)
    n = 0
    for i in range(0, len(cands), 20):
        chunk = cands[i:i + 20]
        try:
            res = routescan.api({"module": "account", "action": "balancemulti",
                                 "address": ",".join(chunk), "tag": "latest"})
        except Exception:
            time.sleep(2)
            continue
        rows = [(r["account"].lower(), str(int(r["balance"])), now) for r in (res or [])
                if str(r.get("balance", "")).isdigit()]
        if rows:
            db.exmany("""INSERT INTO native_holders(wallet,balance_raw,updated_at) VALUES(?,?,?)
                         ON CONFLICT(wallet) DO UPDATE SET balance_raw=excluded.balance_raw,
                           updated_at=excluded.updated_at""", rows)
            n += len(rows)
        time.sleep(0.3)
    total = db.one("SELECT COUNT(*) c FROM native_addresses")["c"]
    return n, f"refreshed={n} seen_addresses={total}", time.time() - t0
