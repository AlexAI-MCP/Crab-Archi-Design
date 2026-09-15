"""Command-line entry point for CrabCADParser."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crab_archi_design.cad_parser import inspect_pack, run_batch
from crab_archi_design.cad_reconstruction import reconstruct_from_ir_file


def _progress(event: dict[str, object]) -> None:
    if event.get("stage") == "file":
        label = f"[{event.get('index')}/{event.get('total')}] {event.get('status')} {event.get('relative_path')}"
        if event.get("error"):
            label += f" — {event['error']}"
        print(label, flush=True)
    elif event.get("stage") == "complete":
        print(json.dumps(event, ensure_ascii=False), flush=True)


def command_run(args: argparse.Namespace) -> None:
    input_dir = Path(args.input_dir).expanduser()
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else input_dir.parent / f"{input_dir.name}_crabcadparser"
    result = run_batch(
        input_dir,
        output_dir,
        recursive=not args.no_recursive,
        converter=args.converter,
        output_version=args.output_version,
        timeout_seconds=args.timeout,
        max_files=args.max_files,
        resume=not args.no_resume,
        pack_title=args.pack_title,
        progress=_progress,
    )
    receipt = result["receipt"]
    print(json.dumps({
        "status": "pass" if receipt["statistics"]["failed"] == 0 else "review_required",
        "receipt": receipt["batch_id"],
        "receipt_path": str(output_dir / "batch_receipt.json"),
        "pack_dir": receipt["pack_dir"],
        "pack_zip": receipt["pack_zip"],
        "statistics": receipt["statistics"],
    }, ensure_ascii=False))


def command_gui(_args: argparse.Namespace) -> None:
    from crab_archi_design.cad_gui import launch

    launch()


def command_inspect(args: argparse.Namespace) -> None:
    print(json.dumps(inspect_pack(Path(args.pack_dir).expanduser()), ensure_ascii=False, indent=2))


def command_payload(args: argparse.Namespace) -> None:
    payload = Path(args.pack_dir).expanduser() / "opencrab" / "ingest_payloads.jsonl"
    if not payload.exists():
        raise SystemExit(f"Missing OpenCrab ingest payload: {payload}")
    print(payload.resolve())
    print(payload.read_text(encoding="utf-8"), end="")


def command_reconstruct(args: argparse.Namespace) -> None:
    result = reconstruct_from_ir_file(
        Path(args.ir_json).expanduser(),
        Path(args.output_dxf).expanduser(),
        mode=args.mode,
        report_path=Path(args.report).expanduser() if args.report else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crabcadparser", description="Parse DWG/DXF folders into OpenCrab-ready ontology packs.")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Parse a DWG/DXF directory and build an OpenCrab Pack v1 bundle.")
    run.add_argument("input_dir", help="Directory containing DWG/DXF files.")
    run.add_argument("--output-dir", help="Output batch directory. Defaults beside input_dir.")
    run.add_argument("--converter", help="Explicit ODAFileConverter executable path.")
    run.add_argument("--output-version", default="ACAD2000", help="ODA output version, default ACAD2000.")
    run.add_argument("--timeout", type=int, default=120, help="Per-file ODA timeout in seconds.")
    run.add_argument("--max-files", type=int, help="Optional bounded file count for a pilot run.")
    run.add_argument("--pack-title", help="OpenCrab pack title.")
    run.add_argument("--no-recursive", action="store_true", help="Do not scan nested directories.")
    run.add_argument("--no-resume", action="store_true", help="Reparse files even if a matching IR already exists.")
    run.set_defaults(func=command_run)

    gui = sub.add_parser("gui", help="Open the native folder-selection GUI.")
    gui.set_defaults(func=command_gui)

    inspect = sub.add_parser("inspect", help="Inspect a generated OpenCrab pack.")
    inspect.add_argument("pack_dir")
    inspect.set_defaults(func=command_inspect)

    payload = sub.add_parser("opencrab-payload", help="Print the OpenCrab ingest payload JSONL path and contents.")
    payload.add_argument("pack_dir")
    payload.set_defaults(func=command_payload)

    reconstruct = sub.add_parser("reconstruct", help="Replay a CAD IR JSON into a new DXF and report round-trip fidelity.")
    reconstruct.add_argument("ir_json", help="Path to a documents/*.json CAD IR file.")
    reconstruct.add_argument("output_dxf", help="Output DXF path.")
    reconstruct.add_argument("--mode", choices=("structured", "source"), default="structured", help="Structured IR replay or exact canonical DXF copy.")
    reconstruct.add_argument("--report", help="Optional JSON report path.")
    reconstruct.set_defaults(func=command_reconstruct)
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        command_gui(argparse.Namespace())
        return
    args = build_parser().parse_args(argv)
    if not getattr(args, "command", None):
        build_parser().print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
