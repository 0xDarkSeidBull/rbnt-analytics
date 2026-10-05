/* RBNT Native section - loads after app.js and reuses its helpers */
"use strict";

const fmtR = v => (v === null || v === undefined) ? "-" : (Math.abs(v) >= 1000 ? NF0.format(v) : NF2.format(v));
const kindStatus = k =>
  k === "treasury" ? status("ok", "treasury") :
  k === "cex" ? status("warn", "cex") :
  k === "dex" ? status("neutral", "dex") :
  k === "protocol" ? status("neutral", "protocol") : "";
const nativeFilter = { rich: "", whale: "" };

function labeledAddr(a, label, kind) {
  if (!a) return `<span class="muted">contract creation</span>`;
  return `${addrLink(a)}${label ? `<div class="small muted">${esc(label)} ${kindStatus(kind)}</div>` : ""}`;
}

function richTable(rows, circ) {
  const f = nativeFilter.rich;
  const list = f ? rows.filter(r => r.wallet.includes(f) || (r.label || "").toLowerCase().includes(f)) : rows;
  const { slice, controls } = paginate("nativeRich", list, 25);
  let h = `<div class="tablewrap"><table><thead><tr>
    <th class="num">#</th><th>Wallet</th><th class="num">RBNT native</th><th class="num">% of total</th>
    <th class="num">% of circulating</th><th class="num">Txs seen</th><th>Last active</th></tr></thead><tbody>`;
  for (const r of slice) {
    const bal = Number(BigInt(r.balance_raw) / 10n ** 12n) / 1e6;
    h += `<tr><td class="num">${r.rank}</td><td>${labeledAddr(r.wallet, r.label, r.kind)}</td>
      <td class="num">${fmtRaw(r.balance_raw)}</td><td class="num">${r.pct_total.toFixed(3)}%</td>
      <td class="num">${circ ? (100 * bal / circ).toFixed(3) + "%" : "-"}</td>
      <td class="num">${r.tx_count != null ? NF0.format(r.tx_count) : "-"}</td>
      <td class="muted small">${ago(r.last_active)}</td></tr>`;
  }
  return h + `</tbody></table></div>` + controls;
}

function whaleTable(rows) {
  const f = nativeFilter.whale;
  const list = f ? rows.filter(r => (r.frm + (r.to_addr || "") + (r.from_label || "") + (r.to_label || "")).toLowerCase().includes(f)) : rows;
  const { slice, controls } = paginate("nativeWhale", list, 20);
  let h = `<div class="tablewrap"><table><thead><tr>
    <th>Time</th><th>From</th><th>To</th><th class="num">RBNT</th><th>Tx</th></tr></thead><tbody>`;
  for (const t of slice) {
    h += `<tr><td class="muted small">${fmtTs(t.ts)}</td>
      <td>${labeledAddr(t.frm, t.from_label, t.from_kind)}</td>
      <td>${labeledAddr(t.to_addr, t.to_label, t.to_kind)}</td>
      <td class="num"><strong>${fmtR(t.value_rbnt)}</strong></td>
      <td class="mono small"><a class="accent-link" href="https://redbelly.routescan.io/tx/${t.hash}" target="_blank" rel="noopener">${t.hash.slice(0, 10)}...${t.hash.slice(-6)}</a></td></tr>`;
  }
  return h + `</tbody></table></div>` + controls;
}

async function renderNative() {
  const [ov, daily, rich, wh, fl] = await Promise.all([
    api("/api/native/overview"), api("/api/native/daily?days=30"), api("/api/native/richlist?limit=500"),
    api("/api/native/whales?days=30"), api("/api/native/flows?days=7")]);
  const s = ov.supply || {};
  const circ = s.circulating_rbnt, locked = s.wrbnt_locked_rbnt;
  let html = `<h1>RBNT native</h1>
    <p class="muted">The base coin of Redbelly mainnet, tracked separately from WRBNT. Supply, transfers, wrap flow and a native richlist built from a full block scan.</p>`;
  if (ov.updated_at) html += updatedBanner(ov.updated_at, "supply 10m, blocks 2m, wraps 5m, balances 30m");

  html += `<section class="grid cols-4">
    ${statCard("Total supply", fmtR(s.total_rbnt), "RBNT")}
    ${statCard("Circulating", fmtR(circ), "Routescan supply feed")}
    ${statCard("Locked in WRBNT", fmtR(locked), circ ? (100 * locked / circ).toFixed(1) + "% of circulating" : "")}
    ${statCard("Market cap", s.price_usd && circ ? fmtUsd(s.price_usd * circ) : "-", s.price_usd ? "at " + fmtUsd(s.price_usd) : "price pending")}
  </section>
  <section class="grid cols-4">
    ${statCard("24h native transfers", NF0.format(ov.last24h.transfers))}
    ${statCard("24h volume", fmtR(ov.last24h.volume_rbnt), "RBNT moved")}
    ${statCard("24h active addresses", NF0.format(ov.last24h.active_addresses))}
    ${statCard("7d net wrap", (ov.wraps["7d"].net >= 0 ? "+" : "") + fmtR(ov.wraps["7d"].net),
      `wrapped ${fmtR(ov.wraps["7d"].wrap)} - unwrapped ${fmtR(ov.wraps["7d"].unwrap)}`)}
  </section>`;

  const cov = ov.coverage;
  if (cov) {
    html += `<div class="banner info">Block scan covers ${NF0.format(cov.from_block)} to ${NF0.format(cov.to_block)} of head ${NF0.format(cov.head)} (${cov.pct}% of chain). Backfill walks back to genesis in the background, so the richlist and history grow until it reaches 100%. Wrap events scanned to block ${cov.wraps_scanned_to != null ? NF0.format(cov.wraps_scanned_to) : "-"}. ${NF0.format(ov.counts.addresses)} addresses seen, ${NF0.format(ov.counts.balances)} balances read.</div>`;
  } else {
    html += `<div class="banner info">Block scanner is starting. First data appears within a few minutes.</div>`;
  }

  html += `<section class="grid cols-2">
    <div class="card"><h3>Supply split (millions RBNT)</h3><div id="natSupply"></div></div>
    <div class="card"><h3>Daily wrap vs unwrap - 30d</h3><div id="natWrap"></div></div>
  </section>
  <div class="card"><h3>Daily native volume and transfers - 30d</h3><div id="natVol"></div></div>`;

  html += `<h2>Native richlist</h2>
    <div class="banner">${esc(rich.note)}. WRBNT contract balance is the native backing for every wrapped token, not a single holder.</div>
    ${searchInput("nativeRichSearch", "Filter by address or label", nativeFilter.rich)}
    <div id="natRich">${richTable(rich.holders, circ)}</div>`;

  html += `<h2>Whale transfers - ${NF0.format(wh.min_rbnt)}+ RBNT, 30d</h2>
    ${searchInput("nativeWhaleSearch", "Filter by address or label", nativeFilter.whale)}
    <div id="natWhale">${wh.transfers.length ? whaleTable(wh.transfers) : empty("No whale-size native transfers in the scanned window yet.")}</div>`;

  html += `<h2>Entity flows - native in and out, 7d</h2>`;
  if (fl.entities.length) {
    html += `<div class="tablewrap"><table><thead><tr><th>Entity</th><th>Address</th>
      <th class="num">In</th><th class="num">Out</th><th class="num">Net</th><th class="num">Txs</th></tr></thead><tbody>`;
    for (const e of fl.entities) {
      html += `<tr><td><strong>${esc(e.label)}</strong> ${kindStatus(e.kind)}</td><td>${addrLink(e.address)}</td>
        <td class="num">${fmtR(e.in_rbnt)}</td><td class="num">${fmtR(e.out_rbnt)}</td>
        <td class="num">${status(e.net_rbnt >= 0 ? "ok" : "bad", (e.net_rbnt >= 0 ? "+" : "") + fmtR(e.net_rbnt))}</td>
        <td class="num">${NF0.format(e.in_txs + e.out_txs)}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  } else {
    html += empty("No native movement for labeled treasury, CEX, DEX or protocol wallets in the last 7 days.");
  }
  html += `<div class="banner">Native transfers here are top-level transaction values. Value moved inside contract calls (internal transfers) is not in these totals; wrap and unwrap are tracked through WRBNT events instead.</div>`;
  $view.innerHTML = html;

  /* charts */
  const toM = v => (v || 0) / 1e6;
  if (circ) {
    const tre = s.treasury_native_rbnt || 0;
    setChart("natSupply", renderBars({
      horizontal: true, height: 230,
      data: [
        { label: "Total", values: { v: toM(s.total_rbnt) } },
        { label: "Circulating", values: { v: toM(circ) } },
        { label: "WRBNT lock", values: { v: toM(locked) } },
        { label: "Treasury", values: { v: toM(tre) } },
        { label: "Free float", values: { v: toM(Math.max(0, circ - locked)) } },
      ],
      series: [{ key: "v", label: "RBNT (millions)", color: "#EF5350" }],
    }));
  }
  const days = daily.days;
  if (days.length) {
    setChart("natWrap", renderBars({
      height: 240,
      data: days.map(d => ({ label: d.day.slice(5), values: { wrap: toM(d.wrap), unwrap: toM(d.unwrap) } })),
      series: [{ key: "wrap", label: "Wrapped (M)", color: "#86EFAC" }, { key: "unwrap", label: "Unwrapped (M)", color: "#EF5350" }],
    }));
    setChart("natVol", renderBars({
      height: 260,
      data: days.map(d => ({ label: d.day.slice(5), values: { vol: toM(d.volume), tx: d.transfers } })),
      series: [{ key: "vol", label: "Volume (M RBNT)", color: "#FCD34D" }, { key: "tx", label: "Transfers", color: "#93a4ae" }],
    }));
  } else {
    setChart("natWrap", `<p class="muted small">Waiting for first scan.</p>`);
    setChart("natVol", `<p class="muted small">Waiting for first scan.</p>`);
  }

  /* scoped search + pagers without refetch */
  const reRich = () => { document.getElementById("natRich").innerHTML = richTable(rich.holders, circ); bindPagers(reRich, document.getElementById("natRich")); };
  const reWhale = () => { const el = document.getElementById("natWhale"); if (wh.transfers.length) { el.innerHTML = whaleTable(wh.transfers); bindPagers(reWhale, el); } };
  bindPagers(reRich, document.getElementById("natRich"));
  bindPagers(reWhale, document.getElementById("natWhale"));
  wireSearch("nativeRichSearch", v => { nativeFilter.rich = v; reRich(); }, nativeFilter.rich);
  wireSearch("nativeWhaleSearch", v => { nativeFilter.whale = v; reWhale(); }, nativeFilter.whale);
}

ROUTES.native = { title: "RBNT native", fn: renderNative };

/* wallet page: append native history under the existing profile */
const _renderWalletBase = renderWallet;
renderWallet = async function (address) {
  await _renderWalletBase(address);
  let d;
  try { d = await api("/api/native/wallet/" + address); } catch (e) { return; }
  let h = `<h2>Native RBNT activity</h2>
    <section class="grid cols-3">
      ${statCard("Native received", fmtR(d.totals.in_rbnt), "in scanned blocks")}
      ${statCard("Native sent", fmtR(d.totals.out_rbnt), "in scanned blocks")}
      ${statCard("Native transfers", NF0.format(d.totals.count), d.seen ? "last active " + ago(d.seen.last_ts) : "")}
    </section>`;
  if (d.transfers.length) {
    h += `<div class="tablewrap"><table><thead><tr><th>Time</th><th>Dir</th><th>Counterparty</th><th class="num">RBNT</th><th>Tx</th></tr></thead><tbody>`;
    for (const t of d.transfers) {
      h += `<tr><td class="muted small">${fmtTs(t.ts)}</td>
        <td>${t.direction === "in" ? status("ok", "in") : status("bad", "out")}</td>
        <td>${labeledAddr(t.counterparty, t.counterparty_label)}</td>
        <td class="num">${fmtR(t.value_rbnt)}</td>
        <td class="mono small"><a class="accent-link" href="https://redbelly.routescan.io/tx/${t.hash}" target="_blank" rel="noopener">${t.hash.slice(0, 10)}...</a></td></tr>`;
    }
    h += `</tbody></table></div>`;
  } else {
    h += empty("No native transfers for this wallet in the scanned block range yet.");
  }
  if (d.wraps.length) {
    h += `<h3>Wrap / unwrap</h3><div class="tablewrap"><table><thead><tr><th>Time</th><th>Action</th><th class="num">RBNT</th></tr></thead><tbody>`;
    for (const w of d.wraps) {
      h += `<tr><td class="muted small">${fmtTs(w.ts)}</td><td>${w.kind === "wrap" ? status("ok", "wrap") : status("warn", "unwrap")}</td><td class="num">${fmtR(w.value_rbnt)}</td></tr>`;
    }
    h += `</tbody></table></div>`;
  }
  $view.insertAdjacentHTML("beforeend", h);
};
