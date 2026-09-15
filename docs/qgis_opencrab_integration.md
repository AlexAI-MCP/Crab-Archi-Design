# QGIS → OpenCrab → Crab Archi Design

Crab Archi Design can retain local QGIS context alongside a community-design
project. The integration is deliberately evidence-first:

```text
QGIS MCP (read-only) → QGIS snapshot → GIS context sidecar
                         ↓                 ↓
                    OpenCrab request ← design handoff → deterministic SVG engine
```

The sidecar gives OpenCrab and the design handoff the site CRS, canvas extent,
layer metadata, QGIS analysis provenance, and human observations. It does not
add a raster tile to the source SVG, does not alter QGIS, and does not turn GIS
coordinates into SVG coordinates automatically.

## 1. Capture QGIS context

Start QGIS, enable **QGIS MCP**, and run its local server. Then capture only
metadata from the localhost plugin:

```bash
crab-archi-design --project-root projects qgis-capture \
  --project-id uijeongbu-site \
  --max-layers 50 \
  --include-preview
```

This calls only `get_qgis_info`, `get_project_info`, `get_canvas_extent`,
`get_layers`, and `get_layer_info` on `127.0.0.1:9876`; `--include-preview`
adds the read-only `get_canvas_screenshot` call. If the QGIS plugin uses an
authentication token, place it in `QGIS_MCP_TOKEN`; the command can use it but
never writes it to a project artifact.

The result is `projects/uijeongbu-site/gis/qgis_snapshot_001.json`. With
`--include-preview`, a matching `qgis_preview_001.png` is also retained as a
GIS review sidecar. It lets a reviewer see the QGIS terrain/around-site context
that informed the request, but is never inserted into a source or final SVG.
If QGIS is busy and cannot return the image, the metadata capture still
succeeds and records the preview as `unavailable` for review. Layer sources are
scrubbed for token/password-like values.

## 2. Attach it as advisory GIS context

```bash
crab-archi-design --project-root projects gis-context-attach \
  --project-id uijeongbu-site \
  --snapshot projects/uijeongbu-site/gis/qgis_snapshot_001.json \
  --summary "의정부 대상지 주변 지형·접근·현황 레이어" \
  --observation "북측 산지와 경사 조건은 배치 검토의 근거로 사용" \
  --confidence 0.85
```

Optional QGIS processing output can be retained with `--analysis-file`, for
example a saved slope, viewshed, flood, road-access, or parcel analysis JSON.
The resulting `gis_context_###.json` and `gis_context_manifest.json` stay in
the project's `gis/` directory and are included in a normal export package.

## 3. Ground OpenCrab retrieval in the context

`opencrab-request` automatically includes the latest GIS summary if present:

```bash
crab-archi-design --project-root projects opencrab-request \
  --project-id uijeongbu-site \
  --intent "지형과 보행 접근을 고려한 커뮤니티 동선·외부공간 전략을 제안"
```

Run the recommended OpenCrab tool call, attach its result with
`opencrab-sync`, then use `design-handoff` and the existing deterministic
same-layer flow. The handoff cites both the OpenCrab evidence and GIS sidecar.

## Safety boundary

- GIS is **advisory context** by default. It cannot create a final SVG overlay,
  mutate SVG geometry, or choose mutable zones.
- Converting a GIS boundary, slope, setback, or hazard result into a solver
  constraint requires a reviewed georeference mapping between the source SVG
  and GIS CRS plus an explicit human confirmation.
- `community_shell`, parking, columns, cores, ramps, stairs, egress, and
  protected geometry remain protected by the existing deterministic gates.
- Use QGIS screenshots and raster layers only in the QGIS/GIS sidecar or review
  material, never as a final native-SVG drawing layer.

The initial bridge is metadata-first so it works with a local QGIS install and
does not add heavyweight GIS runtime dependencies to Crab Archi Design. A later
phase can add a reviewed control-point transform, GeoJSON checksum/provenance,
and human-approved GIS constraint proposals.
