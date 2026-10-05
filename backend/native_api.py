"""Read endpoints for the native RBNT section (/api/native/*)."""
import re
import time

from fastapi import APIRouter, HTTPException

import db
from config import WRBNT, STAKING, NATIVE_WHALE_RBNT
from jobs.native_chain import ensure_schema

router = APIRouter(prefix="/api/native")


def _labels():
    m = {WRBNT.lower(): ("WRBNT contract (wrap backing)", "protocol"),
         STAKING.lower(): ("Staking contract", "protocol")}
    for r in db.q("SELECT address FROM managers"):
        m[r["address"]] = ("Vesting manager", "protocol")
    for r in db.q("SELECT address, label FROM pools"):
        m[r["address"]] = (f"DEX pool {r['label']}", "dex")
    for r in db.q("SELECT address, exchange FROM cex_clusters"):
        m[r["address"]] = (f"{r['exchange']} (clustered)", "cex")
    for r in db.q("SELECT address, exchange FROM cex_anchors"):
        m[r["address"]] = (f"{r['exchange']} hot wallet", "cex")
    for r in db.q("SELECT address, label FROM treasury"):
        m[r["address"]] = (r["label"], "treasury")
    return m


def _lab(m, a):
    return m.get((a or "").lower(), (None, None))


def _latest_supply():
    return db.one("SELECT * FROM native_supply_snapshots ORDER BY ts DESC LIMIT 1")


@router.get("/overview")
def overview():
    ensure_schema()
    now = int(time.time())
    d1, d7 = now - 86400, now - 7 * 86400
    sup = _latest_supply()
    t24 = db.one("""SELECT COUNT(*) n, COALESCE(SUM(value_rbnt),0) vol FROM native_transfers WHERE ts>=?""", (d1,))
    act = db.one("""SELECT COUNT(*) n FROM (SELECT frm a FROM native_transfers WHERE ts>=?
                    UNION SELECT to_addr FROM native_transfers WHERE ts>=? AND to_addr IS NOT NULL)""", (d1, d1))
    wh24 = db.one("SELECT COUNT(*) n FROM native_transfers WHERE ts>=? AND value_rbnt>=?", (d1, NATIVE_WHALE_RBNT))
    w = {}
    for since, key in ((d1, "24h"), (d7, "7d")):
        r = db.one("""SELECT COALESCE(SUM(CASE WHEN kind='wrap' THEN value_rbnt END),0) wrap,
                             COALESCE(SUM(CASE WHEN kind='unwrap' THEN value_rbnt END),0) unwrap
                      FROM native_wraps WHERE ts>=?""", (since,))
        w[key] = {"wrap": r["wrap"], "unwrap": r["unwrap"], "net": r["wrap"] - r["unwrap"]}
    fwd = db.meta_get("native_fwd_block")
    back = db.meta_get("native_back_block")
    head = db.meta_get("native_head_seen")
    wrapb = db.meta_get("native_wrap_block")
    coverage = None
    if fwd and back and head:
        scanned = int(fwd) - max(0, int(back))
        coverage = {"from_block": int(back) + 1, "to_block": int(fwd) - 1, "head": int(head),
                    "pct": round(100 * scanned / max(1, int(head)), 2),
                    "wraps_scanned_to": int(wrapb) - 1 if wrapb else None}
    counts = {
        "transfers": db.one("SELECT COUNT(*) c FROM native_transfers")["c"],
        "addresses": db.one("SELECT COUNT(*) c FROM native_addresses")["c"],
        "wrap_events": db.one("SELECT COUNT(*) c FROM native_wraps")["c"],
        "balances": db.one("SELECT COUNT(*) c FROM native_holders")["c"],
    }
    hist = [dict(r) for r in db.q("""
        SELECT ts, circulating_rbnt, wrbnt_locked_rbnt, treasury_native_rbnt FROM native_supply_snapshots
        WHERE ts IN (SELECT MAX(ts) FROM native_supply_snapshots GROUP BY ts/86400)
        ORDER BY ts DESC LIMIT 30""")][::-1]
    return {"supply": sup, "supply_daily": hist,
            "last24h": {"transfers": t24["n"], "volume_rbnt": t24["vol"], "active_addresses": act["n"],
                        "whale_transfers": wh24["n"]},
            "wraps": w, "coverage": coverage, "counts": counts,
            "whale_threshold": NATIVE_WHALE_RBNT, "updated_at": sup["ts"] if sup else None}


@router.get("/daily")
def daily(days: int = 30):
    ensure_schema()
    days = max(7, min(days, 180))
    since = int(time.time()) - days * 86400
    tx = {r["day"]: dict(r) for r in db.q("""
        SELECT date(ts,'unixepoch') day, COUNT(*) transfers, SUM(value_rbnt) volume,
               COUNT(DISTINCT frm) senders
        FROM native_transfers WHERE ts>=? GROUP BY day""", (since,))}
    wr = {r["day"]: dict(r) for r in db.q("""
        SELECT date(ts,'unixepoch') day,
               SUM(CASE WHEN kind='wrap' THEN value_rbnt ELSE 0 END) wrap,
               SUM(CASE WHEN kind='unwrap' THEN value_rbnt ELSE 0 END) unwrap
        FROM native_wraps WHERE ts>=? GROUP BY day""", (since,))}
    out = []
    for day in sorted(set(tx) | set(wr)):
        a, b = tx.get(day, {}), wr.get(day, {})
        out.append({"day": day, "transfers": a.get("transfers", 0), "volume": a.get("volume") or 0,
                    "senders": a.get("senders", 0), "wrap": b.get("wrap") or 0, "unwrap": b.get("unwrap") or 0})
    return {"days": out}


@router.get("/richlist")
def richlist(limit: int = 300):
    ensure_schema()
    limit = max(10, min(limit, 1000))
    lab = _labels()
    sup = _latest_supply()
    circ = (sup or {}).get("circulating_rbnt")
    total = (sup or {}).get("total_rbnt") or 1e10
    rows = db.q("""SELECT h.wallet, h.balance_raw, h.updated_at, a.last_ts, a.tx_count
                   FROM native_holders h LEFT JOIN native_addresses a ON a.address=h.wallet
                   WHERE h.balance_raw != '0'
                   ORDER BY length(h.balance_raw) DESC, h.balance_raw DESC LIMIT ?""", (limit,))
    out = []
    for i, r in enumerate(rows, 1):
        bal = int(r["balance_raw"]) / 1e18
        name, kind = _lab(lab, r["wallet"])
        out.append({"rank": i, "wallet": r["wallet"], "balance_raw": r["balance_raw"],
                    "pct_total": round(100 * bal / total, 4),
                    "label": name, "kind": kind, "last_active": r["last_ts"],
                    "tx_count": r["tx_count"], "updated_at": r["updated_at"]})
    return {"holders": out, "circulating_rbnt": circ, "total_rbnt": total,
            "note": "ranked among every address the block scanner has reached; complete once backfill hits genesis"}


@router.get("/whales")
def whales(min_rbnt: float = 0, days: int = 30, limit: int = 300):
    ensure_schema()
    min_rbnt = min_rbnt or NATIVE_WHALE_RBNT
    since = int(time.time()) - max(1, min(days, 365)) * 86400
    lab = _labels()
    rows = db.q("""SELECT * FROM native_transfers WHERE ts>=? AND value_rbnt>=?
                   ORDER BY ts DESC LIMIT ?""", (since, min_rbnt, max(10, min(limit, 1000))))
    out = []
    for r in rows:
        fl, fk = _lab(lab, r["frm"])
        tl, tk = _lab(lab, r["to_addr"])
        out.append({**dict(r), "from_label": fl, "from_kind": fk, "to_label": tl, "to_kind": tk})
    wraps = [dict(r) for r in db.q("""SELECT * FROM native_wraps WHERE ts>=? AND value_rbnt>=?
                                     ORDER BY ts DESC LIMIT 200""", (since, min_rbnt / 10))]
    return {"transfers": out, "large_wraps": wraps, "min_rbnt": min_rbnt}


@router.get("/flows")
def flows(days: int = 7):
    """Native in/out per labeled entity (treasury, CEX, DEX, protocol) over the window."""
    ensure_schema()
    since = int(time.time()) - max(1, min(days, 90)) * 86400
    lab = _labels()
    if not lab:
        return {"entities": []}
    addrs = list(lab.keys())
    ph = ",".join("?" for _ in addrs)
    ins = {r["a"]: r for r in db.q(f"""SELECT to_addr a, COUNT(*) n, SUM(value_rbnt) v FROM native_transfers
                                       WHERE ts>=? AND to_addr IN ({ph}) GROUP BY to_addr""", (since, *addrs))}
    outs = {r["a"]: r for r in db.q(f"""SELECT frm a, COUNT(*) n, SUM(value_rbnt) v FROM native_transfers
                                        WHERE ts>=? AND frm IN ({ph}) GROUP BY frm""", (since, *addrs))}
    res = []
    for a in set(ins) | set(outs):
        i, o = ins.get(a), outs.get(a)
        name, kind = lab[a]
        vin, vout = (i["v"] if i else 0) or 0, (o["v"] if o else 0) or 0
        res.append({"address": a, "label": name, "kind": kind,
                    "in_rbnt": vin, "in_txs": i["n"] if i else 0,
                    "out_rbnt": vout, "out_txs": o["n"] if o else 0, "net_rbnt": vin - vout})
    res.sort(key=lambda x: -(x["in_rbnt"] + x["out_rbnt"]))
    return {"entities": res, "days": days}


@router.get("/wallet/{address}")
def wallet(address: str):
    ensure_schema()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        raise HTTPException(400, "address must be a 42-char hex string")
    a = address.lower()
    lab = _labels()
    tx = [dict(r) for r in db.q("""SELECT * FROM native_transfers WHERE frm=? OR to_addr=?
                                   ORDER BY ts DESC LIMIT 100""", (a, a))]
    for r in tx:
        r["direction"] = "out" if r["frm"] == a else "in"
        r["counterparty"] = r["to_addr"] if r["direction"] == "out" else r["frm"]
        r["counterparty_label"] = _lab(lab, r["counterparty"])[0]
    agg = db.one("""SELECT COALESCE(SUM(CASE WHEN to_addr=? THEN value_rbnt END),0) vin,
                           COALESCE(SUM(CASE WHEN frm=? THEN value_rbnt END),0) vout,
                           COUNT(*) n FROM native_transfers WHERE frm=? OR to_addr=?""", (a, a, a, a))
    wr = [dict(r) for r in db.q("SELECT * FROM native_wraps WHERE wallet=? ORDER BY ts DESC LIMIT 50", (a,))]
    seen = db.one("SELECT * FROM native_addresses WHERE address=?", (a,))
    return {"address": a, "transfers": tx, "wraps": wr, "seen": seen,
            "totals": {"in_rbnt": agg["vin"], "out_rbnt": agg["vout"], "count": agg["n"]},
            "label": _lab(lab, a)[0]}
