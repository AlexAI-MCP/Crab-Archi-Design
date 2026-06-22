#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-.quickstart-projects}"
PROJECT_ID="${2:-quickstart-demo}"

if command -v crab-archi-design >/dev/null 2>&1; then
  CRAB_ARCHI_CLI=(crab-archi-design)
else
  export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
  CRAB_ARCHI_CLI=(python3 -m crab_archi_design.cli)
fi

cd "$ROOT_DIR"

PROJECT_ROOT="$OUT_DIR/projects"
JOB_DIR="$OUT_DIR/job_specs"
DIAGNOSTIC_DIR="$OUT_DIR/diagnostics"
JOB_REPORT_DIR="$OUT_DIR/job_reports"
JOB_PATH="$JOB_DIR/${PROJECT_ID}_job.json"

mkdir -p "$JOB_DIR" "$DIAGNOSTIC_DIR" "$JOB_REPORT_DIR"
rm -rf "$PROJECT_ROOT/$PROJECT_ID"
rm -f "$JOB_PATH" "${JOB_PATH%.json}.md"

echo "==> Creating job spec and review brief"
CREATE_STDOUT="$OUT_DIR/create_job.stdout"
"${CRAB_ARCHI_CLI[@]}" --project-root "$PROJECT_ROOT" create-job \
  --project-id "$PROJECT_ID" \
  --source-svg examples/original_sample.svg \
  --households 900 \
  --standards examples/area_standard_sample.csv \
  --ontology-pack community_svg_topology_ontology_v2 \
  --opencrab-result-file examples/opencrab_mcp_result_sample.json \
  --opencrab-source-tool opencrab_search_documents \
  --constraint-sketch examples/constraint_sketch_sample.json \
  --prompt "Improve the greenery lounge hierarchy while preserving protected geometry." \
  --engine-adapter layout-svg-engine \
  --output "$JOB_PATH" \
  --validate \
  --validation-output-dir "$DIAGNOSTIC_DIR" \
  --strict-validation \
  --skip-preview \
  --brief \
  --force | tee "$CREATE_STDOUT"

echo "==> Validating job spec"
"${CRAB_ARCHI_CLI[@]}" validate-job \
  --job "$JOB_PATH" \
  --output-dir "$DIAGNOSTIC_DIR" \
  --strict

echo "==> Running full job"
RUN_STDOUT="$OUT_DIR/run_job.stdout"
"${CRAB_ARCHI_CLI[@]}" run-job \
  --job "$JOB_PATH" \
  --output-dir "$JOB_REPORT_DIR" \
  --strict | tee "$RUN_STDOUT"

ZIP_PATH="$PROJECT_ROOT/$PROJECT_ID/exports/${PROJECT_ID}_export_001.zip"

echo "==> Running release audit"
AUDIT_STDOUT="$OUT_DIR/release_audit.stdout"
"${CRAB_ARCHI_CLI[@]}" --project-root "$PROJECT_ROOT" release-audit \
  --project-id "$PROJECT_ID" \
  --zip "$ZIP_PATH" \
  --strict | tee "$AUDIT_STDOUT"

echo "==> Quickstart complete"
echo "Job spec: $JOB_PATH"
echo "Job brief: ${JOB_PATH%.json}.md"
echo "Job report: $(sed -n '1p' "$RUN_STDOUT")"
echo "Release audit: $(sed -n '1p' "$AUDIT_STDOUT")"
