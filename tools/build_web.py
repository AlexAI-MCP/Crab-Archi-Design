"""Build an explicit public asset allowlist; never copy repository/project trees."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import zipfile
import py_compile
from pathlib import Path

import defusedxml

ROOT = Path(__file__).resolve().parents[1]


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    html = (ROOT / "tools/canvas.html").read_text()
    html = html.replace("<head>", '<head>\n<script src="/web_bridge.js"></script>')
    (output / "index.html").write_text(html)
    for name in ("canvas_3d.js", "web_bridge.js", "web_worker.js", "web.css"):
        shutil.copyfile(ROOT / "tools" / name, output / name)
    shutil.copyfile(ROOT / "examples/original_sample.svg", output / "sample.svg")
    with zipfile.ZipFile(output / "cad_runtime.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("crab_archi_design/__init__.py", "")
        for name in ("canvas_document.py", "canvas_symbols.py", "web_runtime.py"):
            py_compile.compile(str(ROOT / "src/crab_archi_design" / name), doraise=True)
            bundle.write(ROOT / "src/crab_archi_design" / name, "crab_archi_design/" + name)
        for path in Path(defusedxml.__file__).parent.glob("*.py"):
            bundle.write(path, "defusedxml/" + path.name)
    from importlib.metadata import distribution
    metadata = distribution("defusedxml")
    license_file = next((p for p in metadata.files or [] if str(p).endswith("LICENSE")), None)
    if license_file:
        shutil.copyfile(metadata.locate_file(license_file), output / "defusedxml-LICENSE.txt")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (output / "version.json").write_text(json.dumps({
        "name": "Crab Archi Design", "commit": commit,
        "runtime": "browser-pyodide", "agent_connected": False,
    }) + "\n")
    print(f"Built public canvas: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist/web")
    parser.add_argument("--vercel", action="store_true")
    args = parser.parse_args()
    build(args.output)
    if args.vercel:
        target = ROOT / ".vercel/output"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(args.output, target / "static", dirs_exist_ok=True)
        config = json.loads((ROOT / "vercel.json").read_text())
        headers = {item["key"]: item["value"] for item in config["headers"][0]["headers"]}
        (target / "config.json").write_text(json.dumps({"version": 3, "routes": [
            {"src": "/(.*)", "headers": headers, "continue": True},
            {"src": "/version.json", "headers": {"Cache-Control": "no-store"}, "continue": True},
            {"handle": "filesystem"},
        ]}, indent=2) + "\n")
