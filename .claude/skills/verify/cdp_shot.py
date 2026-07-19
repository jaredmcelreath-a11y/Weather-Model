"""Screenshot a page via Chrome DevTools Protocol, waiting for real render.

Usage: python3 cdp_shot.py <url> <out.png> [wait_text] [extra_wait_s] [WxH]
Launches its own headless Chrome on a debug port, polls the DOM until
wait_text appears (or 45s), waits extra_wait_s more for charts, screenshots
the full page, and exits.
"""
import base64
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333

url, out = sys.argv[1], sys.argv[2]
wait_text = sys.argv[3] if len(sys.argv) > 3 else None
extra = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
W, H = (int(x) for x in (sys.argv[5] if len(sys.argv) > 5 else "1400x2600").split("x"))

proc = subprocess.Popen(
    [CHROME, "--headless", "--disable-gpu", f"--remote-debugging-port={PORT}",
     "--remote-allow-origins=*",
     "--no-first-run", "--user-data-dir=/tmp/cdp-profile-501",
     f"--window-size={W},{H}", "about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    ws_url = None
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen(
                f"http://localhost:{PORT}/json", timeout=2))
            page = [t for t in tabs if t.get("type") == "page"]
            if page:
                ws_url = page[0]["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
        time.sleep(0.3)
    assert ws_url, "no debuggable page"

    ws = websocket.create_connection(ws_url, timeout=60)
    mid = [0]

    def send(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method,
                            "params": params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == mid[0]:
                return msg.get("result", {})

    send("Page.enable")
    send("Emulation.setDeviceMetricsOverride",
         {"width": W, "height": H, "deviceScaleFactor": 1,
          "mobile": W <= 640})
    send("Page.navigate", {"url": url})

    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            r = send("Runtime.evaluate",
                     {"expression": "document.body.innerText.length",
                      "returnByValue": True})
            n = r.get("result", {}).get("value", 0)
            if wait_text:
                r2 = send("Runtime.evaluate",
                          {"expression":
                           f"document.body.innerText.includes({wait_text!r})",
                           "returnByValue": True})
                if r2.get("result", {}).get("value"):
                    break
            elif n > 500:
                break
        except Exception:
            pass
    time.sleep(extra)
    shot = send("Page.captureScreenshot", {"format": "png",
                                           "captureBeyondViewport": True})
    with open(out, "wb") as fh:
        fh.write(base64.b64decode(shot["data"]))
    txt = send("Runtime.evaluate",
               {"expression": "document.body.innerText.slice(0, 3000)",
                "returnByValue": True})
    print(txt.get("result", {}).get("value", "")[:3000])
finally:
    proc.terminate()
