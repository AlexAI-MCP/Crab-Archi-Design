"""Preview the allowlisted public build with the production security headers."""

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        config = json.loads((ROOT / "vercel.json").read_text())
        for item in config["headers"][0]["headers"]:
            self.send_header(item["key"], item["value"])
        super().end_headers()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8785)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 partial(Handler, directory=str(ROOT / "dist/web")))
    print(f"http://127.0.0.1:{args.port}/", flush=True)
    server.serve_forever()
