# CrabCADParser

`CrabCADParser`는 DWG/DXF 원본 폴더를 선택해 도면 인벤토리, CAD 엔티티, 레이어, 블록, 텍스트, 치수, 공간 후보, 건축적 역할 힌트, 파일 해시와 출처를 추출하고 OpenCrab Pack v1로 내보내는 로컬 오픈소스 브리지다.

## 현재 지원 범위

- DXF: `ezdxf`로 직접 파싱한다.
- DWG: 원본을 변경하지 않고 로컬 ODA File Converter를 통해 임시 DXF로 변환한 뒤 같은 파서로 읽는다.
- 대상: `.dwg`, `.dxf`; 하위 폴더 재귀 검색, 파일별 진행률, 재실행 시 동일 해시 IR 재사용.
- 결과: `cad_ir/`, `converted_dxf/`, `opencrab_pack/`, `batch_receipt.json`, `*.zip`.
- GUI: Crab command-deck 스타일의 로컬 대시보드에서 입력 폴더와 출력 폴더를 선택한다.
- CAD IR v2: 원본 좌표·곡선 파라미터·폴리라인 폭/bulge·시각 속성·블록 내부 엔티티·레이아웃·주요 DXF 테이블을 보존한다.
- 재구성: 구조화된 IR을 새 DXF로 replay하거나 canonical DXF를 exact fallback으로 복사하고 round-trip fidelity를 계산한다.
- MCP: 로컬 배치 실행·상태 확인·팩 검사·OpenCrab ingest payload 조회·DXF 재구성을 stdio MCP로 노출한다.

## 설치

```bash
cd /Users/alex/Work/Crab-Archi-Design
python3 -m venv .venv
.venv/bin/pip install -e .
```

DWG를 처리하려면 ODA File Converter를 별도로 설치하고 실행 파일 경로를 지정한다. 자동 검색 위치는 `ODA_FILE_CONVERTER` 환경변수, PATH의 `ODAFileConverter`, macOS의 `/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter` 순서다. 변환기가 없으면 DXF는 계속 처리하지만 DWG 행은 `converter_missing`으로 명시적으로 남는다.

## GUI

```bash
.venv/bin/crabcadparser gui
```

화면에서는 아래 세 번만 누르면 된다.

1. `도면 폴더 선택`을 눌러 원본 DWG/DXF가 들어 있는 폴더를 고른다.
2. `결과 폴더 선택`을 누르거나 자동으로 잡힌 결과 폴더를 그대로 둔다.
3. 큰 `파싱 시작` 버튼을 누른다. 완료되면 `결과 열기`로 생성된 팩 폴더를 연다.

`하위 폴더까지 찾기`는 기본으로 켜져 있어 수백 개 도면을 한 번에 찾는다. `ODA 변환기 경로`는 DWG용 변환기를 자동으로 찾지 못했을 때만 입력하면 되고, DXF만 처리할 때는 비워 둬도 된다. 먼저 5~10개 파일로 시험하려면 CLI의 `--max-files` 옵션을 사용하고, GUI의 `이미 처리한 파일 재사용`은 기본값 그대로 두면 중단 후 재실행이 빠르다.

## CLI

```bash
.venv/bin/crabcadparser run /path/to/cad-source \
  --output-dir /path/to/cad-source_crabcadparser \
  --converter /Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter \
  --pack-title "Project CAD Ontology" \
  --max-files 10
```

실제 전체 배치에서는 `--max-files`를 생략한다. 결과의 `batch_receipt.json`을 먼저 확인하고 `opencrab_pack/quality/report.json`의 `status`가 `pass`인지 검토한다.

## 재구성 가능한 CAD IR

도면을 다시 설계할 수 있는 수준으로 사용하려면 단순한 `bbox`나 역할 힌트만 읽어서는 안 된다. 각 `documents/*.json`에는 다음 정보가 남는다.

- 선·호·원·타원·스플라인의 원본 파라미터와 metric용 sampled points
- LWPOLYLINE의 꼭짓점, 폭, bulge, 폐합, elevation, extrusion
- 블록 정의와 블록 내부 엔티티, INSERT의 삽입점·회전·X/Y/Z 스케일
- 레이어·linetype·text style·dim style·layout·header snapshot
- 엔티티의 handle, raw DXF attributes, 시각 속성, source hash

배치가 끝나면 canonical DXF도 `opencrab_pack/sources/`에 보존된다. 구조화된 재생과 exact fallback은 다음처럼 실행한다.

```bash
.venv/bin/crabcadparser reconstruct \
  /path/to/opencrab_pack/documents/cad-document_xxx.json \
  /path/to/reconstructed.dxf \
  --mode structured \
  --report /path/to/roundtrip.json
```

`roundtrip.json`은 엔티티 수·타입·레이어·블록·텍스트·바운딩박스와 구조화된 signature 일치율을 보고한다. HATCH 경계, DIMENSION 생성 그래픽, 프록시/XREF처럼 IR만으로 완전 재생하기 어려운 객체는 canonical DXF를 exact fallback으로 사용한다.

## OpenCrab 연동 방식

생성 팩은 OpenCrab Pack v1 파일 계약을 따른다.

```text
opencrab_pack/
  manifest.json
  graph/nodes.jsonl
  graph/edges.jsonl
  evidence/index.jsonl
  quality/report.json
  neo4j/import.cypher
  neo4j/opencrab_ingest.jsonl
  neo4j/export_status.json
  documents/*.json
  opencrab/ingest_payloads.jsonl
```

`opencrab/ingest_payloads.jsonl`에는 도면별로 `opencrab_ingest_text`에 제출할 수 있는 명시적 payload가 들어간다. CrabCADParser는 인증정보를 저장하지 않고 원격 OpenCrab 쓰기를 몰래 실행하지 않는다. 사용자가 팩 QA를 확인한 다음 연결된 OpenCrab MCP 클라이언트에서 각 payload를 제출한다. 이 경계 덕분에 수백 개 파일의 대량 쓰기를 검토·재시도·감사할 수 있다.

```bash
.venv/bin/crabcadparser opencrab-payload /path/to/cad-source_crabcadparser/opencrab_pack
```

MCP 서버를 등록할 때는 다음 실행 파일을 사용한다.

```bash
.venv/bin/crabcadparser-mcp --stdio
```

도구는 `cad_batch_run`, `cad_batch_status`, `cad_pack_inspect`, `cad_opencrab_ingest_payload`, `cad_reconstruct_dxf` 다섯 가지다. `cad_opencrab_ingest_payload`는 기본적으로 도면 본문을 제외한 메타데이터만 반환하며, 실제 제출 전 `include_content=true`를 명시해야 한다.

## 온톨로지 데이터 모델

그래프에는 `cad_batch`, `cad_drawing`, `cad_layer`, `cad_block`, `cad_entity`, `cad_space`, `cad_text` 노드와 `CONTAINS_DRAWING`, `HAS_LAYER`, `HAS_ENTITY`, `HAS_SPACE`, `LABELS_SPACE`, `CONTAINS_SPACE` 등의 관계가 생성된다. 각 노드는 원본 상대경로, SHA-256, DXF handle 또는 추출 단위 ID, `evidence_ref`를 가진다. 인식 결과는 확정된 BIM 의미가 아니라 `role_hint`와 `confidence`로 표현되며, 레이어명·텍스트·기하 규칙으로부터 나온 추론임을 보존한다.

## 검증 원칙

파서의 완료는 세 단계로 확인한다.

1. 파일별 `batch_receipt.json`에서 발견·변환·파싱·실패 수를 확인한다.
2. 팩의 `quality/report.json`과 `opencrab-pack-qa`로 노드·엣지·evidence 연결, 필수 파일, ZIP 구조를 검사한다.
3. OpenCrab에 제출한 뒤에는 반환된 pack/ingest receipt와 자연어 질의 결과를 별도로 확인한다. 로컬 팩 생성 성공만으로 원격 인제스트 완료라고 주장하지 않는다.

현재 구현은 재구성 가능한 기하/출처 기반을 제공한다. 블록별 실제 의미, 회사별 레이어 표준, 도면 간 외부참조(XREF), PDF 래스터 도면의 OCR, HATCH/DIMENSION/프록시 객체의 완전한 구조화, 완전한 BIM 객체 분류는 후속 어댑터/규칙팩 영역이다.
