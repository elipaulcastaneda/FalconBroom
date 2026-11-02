"""Dev helper: attach to a WebView2 debug port and report page state/console errors.

Start the app with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222
"""
import asyncio
import json
import sys
import urllib.request

import websockets

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9222


async def main():
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
    for t in targets:
        print("target:", t.get("type"), t.get("url"))
    page = next(t for t in targets if t.get("type") == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
        mid = 0

        async def call(method, params=None):
            nonlocal mid
            mid += 1
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    return msg.get("result")

        res = await call(
            "Runtime.evaluate",
            {
                "expression": "JSON.stringify({url: location.href, rootChildren: document.getElementById('root') && document.getElementById('root').children.length, bodyText: document.body.innerText.slice(0,300), backend: window.__FALCONBROOM_BACKEND_URL, early: window.__fb_early_network_failures})",
                "returnByValue": True,
            },
        )
        print(res["result"].get("value"))


asyncio.run(main())
