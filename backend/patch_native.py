"""Idempotent patcher: wires native RBNT into poller, API and frontend."""
import os, sys

B = os.path.dirname(os.path.abspath(__file__))


def patch(path, anchor, insert, after=True, marker=None):
    p = os.path.join(B, path)
    s = open(p).read()
    marker = marker or insert.strip().splitlines()[0]
    if marker in s:
        print(f"skip  {path} (already patched)")
        return
    if s.count(anchor) != 1:
        sys.exit(f"FAIL  {path}: anchor found {s.count(anchor)}x -> {anchor!r}")
    s = s.replace(anchor, anchor + insert if after else insert + anchor)
    open(p, "w").write(s)
    print(f"ok    {path}")


# config
cp = os.path.join(B, "config.py")
cs = open(cp).read()
if "ROUTESCAN_V2" not in cs:
    open(cp, "a").write(open(os.path.join(B, "config_native.txt")).read())
    print("ok    config.py")
else:
    print("skip  config.py (already patched)")

# poller: import + jobs + schedule
patch("poller.py", "from jobs import native as j_native\n",
      "from jobs import native_chain as j_native_chain\n")
patch("poller.py", '    "prune": lambda: _prune(),\n',
      '    "native_supply": lambda: j_native_chain.supply(),\n'
      '    "native_scan": lambda: j_native_chain.scan_forward(),\n'
      '    "native_backfill": lambda: j_native_chain.scan_backward(),\n'
      '    "native_wraps": lambda: j_native_chain.wraps(),\n'
      '    "native_rich": lambda: j_native_chain.richlist(),\n')
patch("poller.py", '        "prune": ("holders", 3600),\n',
      '        "native_supply": ("native_supply", 40),\n'
      '        "native_scan": ("native_scan", 25),\n'
      '        "native_backfill": ("native_backfill", 200),\n'
      '        "native_wraps": ("native_wraps", 80),\n'
      '        "native_rich": ("native_rich", 400),\n')

# api: router must be registered before the static "/" mount
patch("app.py", '@app.get("/")\ndef index():',
      'from native_api import router as native_router\napp.include_router(native_router)\n\n\n',
      after=False)

# frontend
patch("static/index.html", '<a href="#/holders" data-route="holders">Holders</a>\n',
      '    <a href="#/native" data-route="native">RBNT Native</a>\n')
patch("static/index.html", '<script src="/js/app.js"></script>\n',
      '<script src="/js/native.js"></script>\n')
# boot after every script (incl. native.js) has registered its routes
jp = os.path.join(B, "static/js/app.js")
js = open(jp).read()
if 'addEventListener("DOMContentLoaded", boot)' in js:
    print("skip  static/js/app.js (already patched)")
elif js.count("\nboot();") == 1:
    open(jp, "w").write(js.replace("\nboot();", '\nwindow.addEventListener("DOMContentLoaded", boot);'))
    print("ok    static/js/app.js")
else:
    sys.exit("FAIL  static/js/app.js: boot() anchor not found")
print("done")
