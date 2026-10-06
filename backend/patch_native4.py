"""Holders charts: full address in tooltip, click bar -> wallet page,
tooltip stays on screen, correct units (values are whole tokens, not millions)."""
import os, sys
B = os.path.dirname(os.path.abspath(__file__))
p = os.path.join(B, "static/js/app.js")
s = open(p).read()
if "data-addr=" in s:
    sys.exit("skip  already patched")

R = []
old_tip = '`<b>${esc(d.label)}</b><br>${tipLines}`'
new_tip = '`<b>${esc(d.title || d.label)}</b><br>${tipLines}${d.addr ? "<br><span class=\\"muted\\">click to open wallet</span>" : ""}`'
R.append((old_tip, new_tip, 2))
R.append(('data-tip="${esc(' + new_tip + ')}"></rect>',
          'data-tip="${esc(' + new_tip + ')}"${d.addr ? ` data-addr="${esc(d.addr)}" style="cursor:pointer"` : ""}></rect>', 2))
R.append(('''  tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 240) + "px";
  tip.style.top = (e.clientY + 14) + "px";''',
'''  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + w > window.innerWidth - 8) x = e.clientX - w - 14;
  if (y + h > window.innerHeight - 8) y = e.clientY - h - 14;
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = Math.max(8, y) + "px";''', 1))
R.append(('''label: h.wallet.slice(0, 8), values: { v: Number(BigInt(h.balance_raw) / 10n ** 12n) / 1e6 } }));''',
          '''label: h.wallet.slice(0, 8), title: h.wallet + (h.label ? " - " + h.label : ""), addr: h.wallet, values: { v: Number(BigInt(h.balance_raw) / 10n ** 12n) / 1e6 } }));''', 2))
R.append(('series: [{ key: "v", label: "WRBNT (millions)", color: "#EF5350" }], height: 300 });',
          'series: [{ key: "v", label: "WRBNT", color: "#EF5350" }], height: 300 });', 1))
R.append(('series: [{ key: "v", label: "RBNT native (millions)", color: "#FCD34D" }], height: 300 });',
          'series: [{ key: "v", label: "RBNT native", color: "#FCD34D" }], height: 300 });', 1))

for old, new, n in R:
    if s.count(old) != n:
        sys.exit(f"FAIL anchor x{s.count(old)} (want {n}): {old[:60]!r}")
    s = s.replace(old, new)

s += '''
document.addEventListener("click", e => {
  const el = e.target.closest && e.target.closest(".vbar-hit[data-addr]");
  if (!el) return;
  const tip = document.getElementById("chartTip");
  if (tip) tip.hidden = true;
  location.hash = "#/wallet/" + el.dataset.addr.toLowerCase();
});
'''
open(p, "w").write(s)
print("ok    app.js holders charts")
