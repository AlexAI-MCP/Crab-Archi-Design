# MCP and OAuth Integration

Crab Archi Design is designed to be driven by Codex, an MCP wrapper, or an OAuth-backed SaaS worker through local exec calls.

The integration boundary is:

```text
Codex / MCP wrapper / OAuth worker
  -> crab-archi-design mcp-manifest
  -> crab-archi-design-mcp --stdio
  -> run_job or selected CLI tool calls
  -> project JSON artifacts
  -> export-package ZIP
  -> verify-package / doctor
```

## Tool Manifest

Generate the machine-readable catalog:

```bash
crab-archi-design mcp-manifest \
  --output integrations/crab_archi_design_mcp_manifest.json
```

Generate runtime configuration for MCP clients and OAuth workers:

```bash
crab-archi-design mcp-config \
  --output integrations/crab_archi_design_mcp_config.json \
  --project-root projects

crab-archi-design mcp-smoke \
  --config integrations/crab_archi_design_mcp_config.json \
  --strict
```

The manifest records:

- CLI subcommands that should be exposed as MCP tools.
- Required and optional arguments for each tool.
- Expected output artifacts.
- Required gates such as `opencrab_evidence_verified`, `latest_alternative_native_svg`, and `package_verify_pass`.
- Recommended command sequences for first-run design, revision loops, and OpenCrab-first manual workflows.
- Security policy for source SVG packaging, native SVG output, raster overlays, and secrets.

`mcp-config` also exposes two worker-oriented sequences: `handoff_sequence` for first-run job execution and `revision_sequence` for existing-project `revision-run` execution followed by package export, verification, and doctor checks. Run `validate-job --strict` before `run-job` when a worker receives user-authored JSON.

## Recommended MCP Tool Mapping

Expose each manifest `tools[].id` as an MCP tool that shells out to:

```text
crab-archi-design --project-root <project_root> <cli_subcommand> ...
```

Use the first stdout line as the primary artifact path unless the tool prints JSON only.

## Built-in Stdio Bridge

The package installs a dependency-free stdio MCP bridge:

```bash
crab-archi-design-mcp --stdio
```

It supports the MCP JSON-RPC methods:

```text
initialize
notifications/initialized
tools/list
tools/call
ping
```

`tools/list` is generated from `crab-archi-design mcp-manifest`, so the CLI manifest and MCP server stay aligned. `tools/call` maps each tool id back to the corresponding CLI subcommand and returns both text content and `structuredContent` with command, stdout, stderr, return code, and primary artifact path.

`mcp-config` emits a Codex-style block such as:

```json
{
  "mcpServers": {
    "crab-archi-design": {
      "command": "crab-archi-design-mcp",
      "args": ["--stdio"],
      "cwd": "/path/to/Crab-Archi-Design",
      "env": {
        "CRAB_ARCHI_PROJECT_ROOT": "projects",
        "PYTHONPATH": "/path/to/Crab-Archi-Design/src"
      }
    }
  }
}
```

The stdio bridge uses `CRAB_ARCHI_PROJECT_ROOT` as the default project root when a tool call does not pass `project_root` explicitly.

Use `mcp-smoke` before handing a config to Codex, an MCP wrapper, or an OAuth worker. It starts the configured stdio process, sends `initialize`, `notifications/initialized`, and `tools/list`, then writes a `mcp_smoke_report_###.json` report with the available tool names and gate checks.

Example client messages:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"example","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"doctor","arguments":{"project_root":"projects","project_id":"demo","strict":true}}}
```

The most important tools are:

```text
run_job
validate_job
workflow_run
revision_run
opencrab_sync
topology_build
prompt_edit
sketch_intent
constraint_attach
edit_brief
design_handoff
apply_edit
review_panel
project_status
export_package
verify_package
doctor
doodle_editor
```

## OAuth Worker Pattern

An OAuth or SaaS worker should not mutate SVG directly.

Use this pattern:

1. Receive user-owned SVG, standards, doodle JSON, and prompt through the product UI.
2. Store them in a sandboxed job directory.
3. Call OpenCrab MCP and save the MCP result JSON.
4. Load the generated `mcp-config` and start `crab-archi-design-mcp --stdio` in the sandbox.
5. Run `mcp-smoke --strict`.
6. Write a `crab-archi-design-job-spec-v1` JSON file.
7. Run `validate-job --job <job.json> --strict`.
8. Run `run-job --job <job.json> --strict`.
9. Upload only the validated ZIP or selected JSON/SVG artifacts from the job report.

Example job spec:

```json
{
  "schema": "crab-archi-design-job-spec-v1",
  "project_root": "projects",
  "project_id": "demo",
  "source_svg": "/path/to/original.svg",
  "households": 900,
  "standards": ["/path/to/area_standard.csv"],
  "ontology_pack": "community_svg_topology_ontology_v2",
  "opencrab_result_file": ["/path/to/opencrab_mcp_result.json"],
  "constraint_sketch": "/path/to/constraint_sketch.json",
  "prompt": "Improve the greenery lounge and fitness connection while preserving protected geometry.",
  "engine_adapter": "layout-svg-engine",
  "skip_preview": true,
  "export_package": true,
  "verify_package": true,
  "doctor": true,
  "strict": true
}
```

By default, `export-package` excludes the original source SVG. Include it only with `--include-source-svg` when the receiving environment is allowed to hold proprietary drawings.

## Codex Exec Pattern

Codex can use the same manifest without a custom server:

```bash
crab-archi-design mcp-manifest
crab-archi-design mcp-config --project-root projects
crab-archi-design mcp-smoke --strict
crab-archi-design validate-job --job examples/job_spec_sample.json --strict
crab-archi-design run-job --job examples/job_spec_sample.json --strict
```

For first-run debugging, Codex can still call the lower-level manual sequence: `workflow-run`, `export-package`, `verify-package --strict`, and `doctor --strict`. For repeated design edits on an existing project, call `revision-run` with `--text`, `--sketch`, or `--constraint-sketch`, then export and verify the package again.

The LLM should produce structured intent and handoff artifacts. Native SVG mutation should remain in deterministic adapters such as `layout-svg-engine`, `reference-svg-engine`, or a project-specific room-envelope solver.

## OpenCrab Requirement

OpenCrab MCP is mandatory before a final candidate is accepted.

The manifest advertises:

```text
opencrab.required = true
opencrab.evidence_gate = opencrab_evidence_verified
```

An MCP or OAuth integration should reject final alternatives unless `doctor` confirms the OpenCrab evidence gate and native SVG candidate gates are passing.
