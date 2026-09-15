/* Crab Archi Design Three.js derivative viewer.
 *
 * This module reads the browser's current SVG DOM and creates a separate 3D
 * review scene. It never writes geometry back to the SVG. Camera cuts are sent
 * to the localhost canvas server as PNG + provenance metadata.
 */

const app = window.CrabCanvas;
const $ = id => document.getElementById(id);

let THREE;
let OrbitControls;
try {
  THREE = await import('three');
  ({ OrbitControls } = await import('three/addons/controls/OrbitControls.js'));
} catch (error) {
  $('threeStatus').textContent = 'Three.js 로드 실패 — 네트워크 연결을 확인하세요';
  app.log('3D 로드 실패: ' + error);
  throw error;
}

const state = {
  renderer: null,
  scene: null,
  camera: null,
  controls: null,
  model: null,
  initialized: false,
  sourceRevision: -1,
  selected: null,
  selectedObjectKey: null,
  selectionFootprint: null,
  reviewHeightOverrides: new Map(),
  loadRetry: null,
  wallMeshes: [],
  columnMeshes: [],
  furnitureMeshes: [],
  parkingMeshes: [],
  buildingMeshes: [],
  landscapeMeshes: [],
  civilMeshes: [],
  terrainMeshes: [],
  allMeshes: [],
  objectCount: 0,
  segmentCount: 0,
  wallCount: 0,
  heightOverrideCount: 0,
  columnCount: 0,
  furnitureCount: 0,
  parkingCount: 0,
  evParkingCount: 0,
  parkingLineCount: 0,
  buildingCount: 0,
  landscapeCount: 0,
  civilCount: 0,
  terrainCount: 0,
  terrainFlatCount: 0,
  representationCounts: new Map(),
  planOverlayCount: 0,
  planOverlayTruncated: false,
  objectSelectTotal: 0,
  objectSelectShown: 0,
  cappedChannels: new Set(),
  focusBox: null,
  recognitionMode: 'unavailable',
  scaleMode: 'unavailable',
  unitsToMeters: 1,
};

const finishPresets = {
  plaster: { color: '#d8d4ca', roughness: 0.82, metalness: 0.0, opacity: 1.0 },
  concrete: { color: '#8c8f91', roughness: 0.94, metalness: 0.0, opacity: 1.0 },
  wood: { color: '#9a6a43', roughness: 0.7, metalness: 0.0, opacity: 1.0 },
  metal: { color: '#66717d', roughness: 0.28, metalness: 0.82, opacity: 1.0 },
  glass: { color: '#8bd4e8', roughness: 0.12, metalness: 0.0, opacity: 0.34 },
  custom: { color: '#d8d4ca', roughness: 0.72, metalness: 0.0, opacity: 1.0 },
};

// Flattened site plans can contain tens of thousands of repeated green or civil
// primitives. Keep each review channel bounded independently and report the cap.
const channelCaps = Object.freeze({ building: 1600, landscape: 1400, civil: 1800, terrain: 900 });

function numberValue(id, fallback) {
  const value = Number($(id).value);
  return Number.isFinite(value) && value >= 0 ? value : fallback;
}

// HEIGHT_CONTROL_START
function normalizeReviewHeightMm(value, fallback = 2700) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return Math.max(10, Math.round(fallback));
  return Math.max(10, Math.min(200000, Math.round(numeric)));
}

function floorHeightTargetMm(floors, floorHeightMm) {
  const safeFloors = Math.max(1, Math.min(200, Math.round(Number(floors) || 1)));
  const safeFloorHeight = Math.max(300, Math.min(10000, Math.round(Number(floorHeightMm) || 2700)));
  return { floors: safeFloors, floorHeightMm: safeFloorHeight, heightMm: safeFloors * safeFloorHeight };
}

function heightScaleFactor(baseHeightMm, targetHeightMm) {
  const safeBase = Math.max(1, Number(baseHeightMm) || 1);
  return normalizeReviewHeightMm(targetHeightMm, safeBase) / safeBase;
}

function floorGuideLevels(floors, maximumGuides = 40) {
  const safeFloors = Math.max(1, Math.min(200, Math.round(Number(floors) || 1)));
  const safeMaximum = Math.max(2, Math.round(Number(maximumGuides) || 40));
  if (safeFloors <= safeMaximum) return Array.from({ length: safeFloors }, (_, index) => index);
  const step = Math.ceil((safeFloors - 1) / (safeMaximum - 1));
  const levels = [];
  for (let level = 0; level < safeFloors; level += step) levels.push(level);
  if (levels[levels.length - 1] !== safeFloors - 1) levels.push(safeFloors - 1);
  return levels;
}

function perspectiveFitDistance(width, height, depth, fovDegrees = 42, aspect = 1, padding = 1.35) {
  const safeWidth = Math.max(0.01, Number(width) || 0.01);
  const safeHeight = Math.max(0.01, Number(height) || 0.01);
  const safeDepth = Math.max(0.01, Number(depth) || 0.01);
  const safeFov = Math.max(10, Math.min(120, Number(fovDegrees) || 42)) * Math.PI / 180;
  const safeAspect = Math.max(0.2, Number(aspect) || 1);
  const halfHeightDistance = safeHeight / (2 * Math.tan(safeFov / 2));
  const halfWidthDistance = safeWidth / (2 * Math.tan(safeFov / 2) * safeAspect);
  return Math.max(halfHeightDistance, halfWidthDistance, safeDepth, 1) *
    Math.max(1.05, Number(padding) || 1.35);
}
// HEIGHT_CONTROL_END

function elementHeightMeters(element, fallback) {
  const heightMm = Number(element.getAttribute('data-height-mm'));
  return Number.isFinite(heightMm) && heightMm > 0 ? heightMm / 1000 : fallback;
}

function settings() {
  return {
    wall_height_mm: numberValue('threeWallHeight', 2700),
    wall_thickness_mm: numberValue('threeWallThickness', 150),
    slab_thickness_mm: numberValue('threeSlab', 150),
    furniture_height_mm: numberValue('threeFurnitureHeight', 750),
    finish: $('threeFinish').value,
    finish_color: $('threeColor').value,
    units_to_meters: state.unitsToMeters,
    scale_mode: state.scaleMode,
    source_revision: state.sourceRevision,
    object_count: state.objectCount,
    segment_count: state.segmentCount,
    wall_count: state.wallCount,
    height_override_count: state.heightOverrideCount,
    column_count: state.columnCount,
    furniture_count: state.furnitureCount,
    parking_count: state.parkingCount,
    ev_parking_count: state.evParkingCount,
    parking_line_count: state.parkingLineCount,
    building_count: state.buildingCount,
    landscape_count: state.landscapeCount,
    civil_count: state.civilCount,
    terrain_count: state.terrainCount,
    terrain_flat_count: state.terrainFlatCount,
    representation_counts: Object.fromEntries(state.representationCounts),
    native_plan_overlay: {
      segment_count: state.planOverlayCount,
      truncated: state.planOverlayTruncated,
    },
    height_object_list: {
      total: state.objectSelectTotal,
      shown: state.objectSelectShown,
    },
    review_height_overrides: Object.fromEntries(state.reviewHeightOverrides),
    selected_object_key: state.selectedObjectKey,
    channel_caps: Object.fromEntries(Object.entries(channelCaps).map(([channel, cap]) =>
      [channel, { cap, reached: state.cappedChannels.has(channel) }])),
    focus_bbox_svg: state.focusBox,
    recognition_mode: state.recognitionMode,
    visible_categories: {
      walls: $('threeShowWalls').checked,
      columns: $('threeShowColumns').checked,
      furniture: $('threeShowFurniture').checked,
      parking: $('threeShowParking').checked,
      building: $('threeShowBuilding').checked,
      landscape: $('threeShowLandscape').checked,
      civil: $('threeShowCivil').checked,
      terrain: $('threeShowTerrain').checked,
      plan_overlay: $('threeShowOverlay').checked,
      selected_plan: $('threeShowSelectionPlan').checked,
    },
  };
}

function init() {
  if (state.initialized) return;
  const canvas = $('threeCanvas');
  state.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  state.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  state.renderer.outputColorSpace = THREE.SRGBColorSpace;
  state.renderer.toneMapping = THREE.ACESFilmicToneMapping;
  state.renderer.toneMappingExposure = 1.0;
  state.renderer.shadowMap.enabled = true;
  state.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  state.scene = new THREE.Scene();
  state.scene.background = new THREE.Color('#111821');
  state.camera = new THREE.PerspectiveCamera(42, 1, 0.01, 2000);
  state.controls = new OrbitControls(state.camera, canvas);
  state.controls.enableDamping = true;
  state.controls.dampingFactor = 0.08;
  state.controls.enablePan = true;
  state.controls.screenSpacePanning = true;
  state.controls.minDistance = 0.1;
  state.controls.maxDistance = 1000;
  updateInteractionMode();

  const hemi = new THREE.HemisphereLight(0xddeeff, 0x38404a, 1.8);
  state.scene.add(hemi);
  const key = new THREE.DirectionalLight(0xffffff, 3.4);
  key.position.set(18, 28, 12);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  state.scene.add(key);
  const fill = new THREE.DirectionalLight(0x88aaff, 1.1);
  fill.position.set(-16, 10, -20);
  state.scene.add(fill);

  const grid = new THREE.GridHelper(100, 100, 0x3a4757, 0x202a36);
  grid.position.y = -0.001;
  grid.name = 'review-grid';
  state.scene.add(grid);

  new ResizeObserver(resize).observe($('threePane'));
  canvas.addEventListener('pointerdown', updateInteractionMode, true);
  canvas.addEventListener('pointerdown', selectObject);
  state.initialized = true;
  resize();
  animate();
}

function resize() {
  if (!state.initialized || $('threePane').classList.contains('hidden')) return;
  const canvas = $('threeCanvas');
  const width = Math.max(1, canvas.clientWidth);
  const height = Math.max(1, canvas.clientHeight);
  if (canvas.width !== Math.round(width * state.renderer.getPixelRatio()) ||
      canvas.height !== Math.round(height * state.renderer.getPixelRatio())) {
    state.renderer.setSize(width, height, false);
    state.camera.aspect = width / height;
    state.camera.updateProjectionMatrix();
  }
}

function animate() {
  requestAnimationFrame(animate);
  if (!state.initialized || $('threePane').classList.contains('hidden')) return;
  state.controls.update();
  state.renderer.render(state.scene, state.camera);
}

function disposeModel() {
  if (!state.model) return;
  clearSelectionFootprint();
  state.model.traverse(object => {
    if (object.geometry) object.geometry.dispose();
    if (object.material) {
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach(material => {
        if (material.map) material.map.dispose();
        material.dispose();
      });
    }
  });
  state.scene.remove(state.model);
  state.model = null;
  state.wallMeshes = [];
  state.columnMeshes = [];
  state.furnitureMeshes = [];
  state.parkingMeshes = [];
  state.buildingMeshes = [];
  state.landscapeMeshes = [];
  state.civilMeshes = [];
  state.terrainMeshes = [];
  state.allMeshes = [];
  state.selected = null;
  state.selectedObjectKey = null;
}

function clearSelectionFootprint() {
  const footprint = state.selectionFootprint;
  if (!footprint) return;
  footprint.traverse(object => {
    if (object.geometry) object.geometry.dispose();
    if (object.material) object.material.dispose();
  });
  footprint.parent?.remove(footprint);
  state.selectionFootprint = null;
}

function roleText(element) {
  const values = [];
  let node = element;
  while (node && typeof node.getAttribute === 'function') {
    values.push(
      node.getAttribute('data-role'),
      node.getAttribute('data-crab-program-role'),
      node.getAttribute('data-crab-solver-projected-role'),
      node.getAttribute('data-zone-mode'),
      node.getAttribute('id'),
      node.getAttribute('class'),
    );
    if (node.localName === 'svg') break;
    node = node.parentElement;
  }
  return values.filter(Boolean).join(' ').toLowerCase();
}

function semanticAttribute(element, name, { includeSvgRoot = true } = {}) {
  let node = element;
  while (node && typeof node.getAttribute === 'function') {
    // Illustrator commonly writes a generic data-name such as "Layer 1" on
    // the root SVG.  It is document metadata, not an inherited element role,
    // and must not suppress the conservative green landscape fallback.
    if (!includeSvgRoot && node.localName === 'svg') break;
    const value = node.getAttribute(name);
    if (value && value.trim()) return value.trim();
    if (node.localName === 'svg') break;
    node = node.parentElement;
  }
  return '';
}

function semanticRoleText(element) {
  const values = [];
  let node = element;
  while (node && typeof node.getAttribute === 'function') {
    values.push(node.getAttribute('data-role'), node.getAttribute('data-crab-program-role'),
      node.getAttribute('data-crab-solver-projected-role'));
    if (node.localName === 'svg') break;
    node = node.parentElement;
  }
  return values.filter(Boolean).join(' ');
}

function semanticRecord(element, {
  repeated = 0, closed = false, siteDominant = false, siteBuilding = false, siteStructure = false,
  aspect = 0, sizeRatio = 0,
} = {}) {
  return {
    role: semanticRoleText(element),
    discipline: semanticAttribute(element, 'data-discipline'),
    symbol: semanticAttribute(element, 'data-symbol'),
    name: semanticAttribute(element, 'data-name', { includeSvgRoot: false }),
    fill: styleValue(element, 'fill', ''),
    stroke: styleValue(element, 'stroke', ''),
    repeated,
    closed,
    siteDominant,
    siteBuilding,
    siteStructure,
    aspect,
    sizeRatio,
    tag: element.localName || '',
  };
}

function styleValue(element, name, fallback = '') {
  let node = element;
  let hasClass = false;
  while (node && typeof node.getAttribute === 'function') {
    hasClass = hasClass || Boolean(node.getAttribute('class'));
    const direct = node.getAttribute(name);
    if (direct !== null && direct !== '' && direct.trim().toLowerCase() !== 'inherit') return direct.trim();
    const inline = node.getAttribute('style') || '';
    const entries = inline.split(';');
    for (const entry of entries) {
      const split = entry.indexOf(':');
      if (split > 0 && entry.slice(0, split).trim() === name) {
        const value = entry.slice(split + 1).trim();
        if (value.toLowerCase() !== 'inherit') return value;
      }
    }
    if (node.localName === 'svg') break;
    node = node.parentElement;
  }
  if (hasClass && typeof getComputedStyle === 'function') {
    const computed = getComputedStyle(element).getPropertyValue(name).trim();
    if (computed) return computed;
  }
  return fallback;
}

function numericStyle(element, name, fallback = 0) {
  const value = Number.parseFloat(styleValue(element, name, String(fallback)));
  return Number.isFinite(value) ? value : fallback;
}

function numericAttribute(element, name, fallback = 0) {
  const value = Number.parseFloat(element.getAttribute(name));
  return Number.isFinite(value) ? value : fallback;
}

function rawPointList(element) {
  if (element.points && typeof element.points.length === 'number') {
    return Array.from(element.points).map(point => ({ x: Number(point.x), y: Number(point.y) }));
  }
  const values = (element.getAttribute('points') || '').trim().split(/[\s,]+/)
    .map(value => Number.parseFloat(value)).filter(Number.isFinite);
  const points = [];
  for (let index = 1; index < values.length; index += 2) {
    points.push({ x: values[index - 1], y: values[index] });
  }
  return points;
}

function hiddenOrAnnotation(element) {
  const role = roleText(element);
  if (role.includes('zone') || role.includes('cleanup-mask') || role.includes('corridor-axis') ||
      role.includes('door-opening') || role.includes('dimension') || role.includes('annotation')) return true;
  let node = element;
  while (node && typeof node.getAttribute === 'function') {
    const inline = (node.getAttribute('style') || '').replace(/\s+/g, '').toLowerCase();
    if (node.getAttribute('display') === 'none' || node.getAttribute('visibility') === 'hidden' ||
        node.getAttribute('opacity') === '0' || inline.includes('display:none') ||
        inline.includes('visibility:hidden') || inline.includes('opacity:0')) return true;
    const id = (node.id || '').toLowerCase();
    if (id === 'crab_zones' || id.includes('annotation') || id.includes('dimension')) return true;
    if (node.localName === 'svg') break;
    node = node.parentElement;
  }
  return false;
}

function rawElementBox(element) {
  const tag = element.localName;
  const finiteBox = (x0, y0, x1, y1) => {
    const values = [x0, y0, x1, y1].map(Number);
    if (!values.every(Number.isFinite)) return null;
    return { x0: Math.min(values[0], values[2]), y0: Math.min(values[1], values[3]),
      x1: Math.max(values[0], values[2]), y1: Math.max(values[1], values[3]) };
  };
  if (tag === 'line') return finiteBox(element.getAttribute('x1'), element.getAttribute('y1'),
    element.getAttribute('x2'), element.getAttribute('y2'));
  if (tag === 'rect') {
    const x = numericAttribute(element, 'x'), y = numericAttribute(element, 'y');
    return finiteBox(x, y, x + numericAttribute(element, 'width'), y + numericAttribute(element, 'height'));
  }
  if (tag === 'circle' || tag === 'ellipse') {
    const cx = numericAttribute(element, 'cx'), cy = numericAttribute(element, 'cy');
    const rx = numericAttribute(element, tag === 'circle' ? 'r' : 'rx');
    const ry = numericAttribute(element, tag === 'circle' ? 'r' : 'ry', rx);
    return finiteBox(cx - rx, cy - ry, cx + rx, cy + ry);
  }
  if (tag === 'polyline' || tag === 'polygon') {
    const points = rawPointList(element);
    if (!points.length) return null;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const point of points) {
      x0 = Math.min(x0, point.x); y0 = Math.min(y0, point.y);
      x1 = Math.max(x1, point.x); y1 = Math.max(y1, point.y);
    }
    return finiteBox(x0, y0, x1, y1);
  }
  if (tag === 'path') {
    try {
      const box = element.getBBox();
      return finiteBox(box.x, box.y, box.x + box.width, box.y + box.height);
    } catch (_) { return null; }
  }
  return null;
}

function boxWidth(box) { return box ? box.x1 - box.x0 : 0; }
function boxHeight(box) { return box ? box.y1 - box.y0 : 0; }
function boxCenter(box) { return { x: (box.x0 + box.x1) / 2, y: (box.y0 + box.y1) / 2 }; }
function boxInside(inner, outer, margin = 0) {
  const center = boxCenter(inner);
  return center.x >= outer.x0 - margin && center.x <= outer.x1 + margin &&
    center.y >= outer.y0 - margin && center.y <= outer.y1 + margin;
}

function quantile(values, fraction) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.max(0, Math.min(sorted.length - 1, Math.round((sorted.length - 1) * fraction)));
  return sorted[index];
}

function repeatedShapeKey(element, box, maxDim) {
  const step = Math.max(1, maxDim / 1000);
  const round = value => Math.round(value / step);
  return `${element.localName}:${round(boxWidth(box))}:${round(boxHeight(box))}`;
}

function isClosedElement(element, points = null) {
  if (['polygon', 'circle', 'ellipse', 'rect'].includes(element.localName)) return true;
  if (element.localName === 'path') return /[zZ]\s*$/.test(element.getAttribute('d') || '');
  if (element.localName === 'polyline') {
    const values = points || elementPoints(app.getSvgRoot(), element);
    return values.length > 2 && Math.hypot(values[0].x - values[values.length - 1].x,
      values[0].y - values[values.length - 1].y) <= 1.5;
  }
  return false;
}

function localToRoot(root, element, x, y) {
  if (typeof root.createSVGPoint !== 'function' || typeof element.getScreenCTM !== 'function' ||
      typeof root.getScreenCTM !== 'function') return { x: Number(x), y: Number(y) };
  const local = root.createSVGPoint();
  local.x = Number(x); local.y = Number(y);
  const elementMatrix = element.getScreenCTM();
  const rootMatrix = root.getScreenCTM();
  if (!elementMatrix || !rootMatrix) return { x: Number(x), y: Number(y) };
  const screen = local.matrixTransform(elementMatrix);
  const world = screen.matrixTransform(rootMatrix.inverse());
  return { x: world.x, y: world.y };
}

function elementBox(root, element) {
  const box = rawElementBox(element);
  if (!box) return null;
  let transformed = false;
  let node = element;
  while (node && typeof node.getAttribute === 'function') {
    const transform = (node.getAttribute('transform') || '').trim();
    if (transform && transform.toLowerCase() !== 'none') { transformed = true; break; }
    if (node === root || node.localName === 'svg') break;
    node = node.parentElement;
  }
  if (!transformed) return box;
  const corners = [
    localToRoot(root, element, box.x0, box.y0), localToRoot(root, element, box.x1, box.y0),
    localToRoot(root, element, box.x1, box.y1), localToRoot(root, element, box.x0, box.y1),
  ];
  return {
    x0: Math.min(...corners.map(point => point.x)), y0: Math.min(...corners.map(point => point.y)),
    x1: Math.max(...corners.map(point => point.x)), y1: Math.max(...corners.map(point => point.y)),
  };
}

function elementPoints(root, element, maxPoints = 48) {
  const tag = element.localName;
  if (tag === 'line') {
    return [
      localToRoot(root, element, element.getAttribute('x1'), element.getAttribute('y1')),
      localToRoot(root, element, element.getAttribute('x2'), element.getAttribute('y2')),
    ];
  }
  if (tag === 'polyline' || tag === 'polygon') {
    const points = rawPointList(element).map(point => localToRoot(root, element, point.x, point.y));
    if (tag === 'polygon' && points.length > 2) points.push({ ...points[0] });
    return points;
  }
  if (tag === 'rect') {
    const x = numericAttribute(element, 'x'), y = numericAttribute(element, 'y');
    const w = numericAttribute(element, 'width'), h = numericAttribute(element, 'height');
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]].map(
      point => localToRoot(root, element, point[0], point[1]));
  }
  if (tag === 'path' && typeof element.getTotalLength === 'function') {
    let length;
    try { length = element.getTotalLength(); } catch (_) { return []; }
    if (!Number.isFinite(length) || length <= 0) return [];
    const count = Math.max(2, Math.min(maxPoints, Math.ceil(length / 18) + 1));
    const points = [];
    for (let index = 0; index < count; index += 1) {
      const point = element.getPointAtLength(length * index / (count - 1));
      points.push(localToRoot(root, element, point.x, point.y));
    }
    return points;
  }
  return [];
}

function makeMaterial(kind = 'wall') {
  const selectedFinish = kind === 'glass' ? 'glass' : $('threeFinish').value;
  const preset = { ...(finishPresets[selectedFinish] || finishPresets.plaster) };
  if (selectedFinish === 'custom') preset.color = $('threeColor').value;
  if (kind === 'floor') Object.assign(preset, { color: '#202933', roughness: 0.94, opacity: 1.0 });
  if (kind === 'column') Object.assign(preset, { color: '#c3c8cd', roughness: 0.76, opacity: 1.0 });
  if (kind === 'furniture') Object.assign(preset, { color: '#9a7651', roughness: 0.68, metalness: 0.02, opacity: 1.0 });
  if (kind === 'parking') Object.assign(preset, { color: '#f1f5f9', roughness: 0.56, metalness: 0.08, opacity: 1.0 });
  if (kind === 'ev-parking') Object.assign(preset, { color: '#38bdf8', roughness: 0.42, metalness: 0.14, opacity: 1.0 });
  if (kind === 'building' || kind === 'building-mass') Object.assign(preset, { color: '#cbd5e1', roughness: 0.72, metalness: 0.02, opacity: 1.0 });
  if (kind === 'building-roof') Object.assign(preset, { color: '#94a3b8', roughness: 0.78, metalness: 0.02, opacity: 1.0 });
  if (kind === 'structure-linear') Object.assign(preset, { color: '#a8b0ba', roughness: 0.82, metalness: 0.08, opacity: 1.0 });
  if (kind === 'landscape') Object.assign(preset, { color: '#4d8d58', roughness: 0.88, metalness: 0.0, opacity: 1.0 });
  if (kind === 'tree-canopy') Object.assign(preset, { color: '#3f7f4e', roughness: 0.92, metalness: 0.0, opacity: 1.0 });
  if (kind === 'tree-canopy-light') Object.assign(preset, { color: '#5c995a', roughness: 0.92, metalness: 0.0, opacity: 1.0 });
  if (kind === 'tree-trunk') Object.assign(preset, { color: '#6b4f35', roughness: 0.96, metalness: 0.0, opacity: 1.0 });
  if (kind === 'shrub') Object.assign(preset, { color: '#5f9b57', roughness: 0.94, metalness: 0.0, opacity: 1.0 });
  if (kind === 'hedge') Object.assign(preset, { color: '#47794b', roughness: 0.94, metalness: 0.0, opacity: 1.0 });
  if (kind === 'planting' || kind === 'planting-line') Object.assign(preset, { color: '#739c5a', roughness: 0.98, metalness: 0.0, opacity: 1.0 });
  if (kind === 'water') Object.assign(preset, { color: '#4f9fc4', roughness: 0.24, metalness: 0.0, opacity: 0.78 });
  if (kind === 'hardscape') Object.assign(preset, { color: '#9c9284', roughness: 0.9, metalness: 0.0, opacity: 1.0 });
  if (kind === 'road') Object.assign(preset, { color: '#5f6670', roughness: 0.96, metalness: 0.0, opacity: 1.0 });
  if (kind === 'civil-line' || kind === 'civil-surface' || kind === 'civil-utility') {
    Object.assign(preset, { color: '#b9824b', roughness: 0.88, metalness: 0.0, opacity: 1.0 });
  }
  if (kind === 'civil') Object.assign(preset, { color: '#d19a58', roughness: 0.8, metalness: 0.0, opacity: 1.0 });
  if (kind === 'terrain') Object.assign(preset, { color: '#8b6f4d', roughness: 0.96, metalness: 0.0, opacity: 0.82 });
  if (kind === 'terrain-contour' || kind === 'terrain-surface') {
    Object.assign(preset, { color: '#8b6f4d', roughness: 0.96, metalness: 0.0, opacity: 0.82 });
  }
  const material = new THREE.MeshPhysicalMaterial({
    color: preset.color,
    roughness: preset.roughness,
    metalness: preset.metalness,
    transparent: preset.opacity < 1,
    opacity: preset.opacity,
    depthWrite: preset.opacity >= 0.9,
    side: THREE.DoubleSide,
  });
  material.userData.baseEmissive = kind === 'ev-parking' ? '#075985' : '#000000';
  if (kind === 'ev-parking') {
    material.emissive.set(material.userData.baseEmissive);
    material.emissiveIntensity = 0.35;
  }
  return material;
}

function planWorldPoints(points, options, y = 0.028) {
  return (points || []).map(point => new THREE.Vector3(
    (point.x - options.centerX) * options.unitsToMeters,
    y,
    (point.y - options.centerY) * options.unitsToMeters,
  ));
}

function prepareHeightMesh(mesh, {
  heightMeters,
  heightAxis = 'y',
  planPoints = [],
  closed = false,
  subtype = '',
  confidence = 'derived',
  heightEditable = true,
  floorEditable = false,
  heightTransform = 'centered',
  groundY = 0,
  objectKey = '',
  baseFloorCount = 1,
  floorHeightMm = 2700,
  heightSource = 'derived',
} = {}) {
  const baseHeightMm = normalizeReviewHeightMm((Number(heightMeters) || 0.01) * 1000, 10);
  const cid = mesh.userData.cid;
  mesh.userData.objectKey = objectKey || (cid ? `cid:${cid}` : `mesh:${mesh.uuid}`);
  mesh.userData.baseHeightMm = baseHeightMm;
  mesh.userData.heightAxis = heightAxis;
  mesh.userData.baseAxisScale = mesh.scale[heightAxis];
  mesh.userData.basePositionY = mesh.position.y;
  mesh.userData.heightTransform = heightTransform;
  mesh.userData.groundY = groundY;
  mesh.userData.planPoints = planPoints;
  mesh.userData.planClosed = Boolean(closed);
  mesh.userData.subtype = subtype || mesh.userData.role || 'object';
  mesh.userData.confidence = confidence;
  mesh.userData.heightEditable = Boolean(heightEditable);
  mesh.userData.floorEditable = Boolean(floorEditable);
  mesh.userData.baseFloorCount = Math.max(1, Math.round(Number(baseFloorCount) || 1));
  mesh.userData.floorHeightMm = Math.max(300, Math.round(Number(floorHeightMm) || 2700));
  mesh.userData.heightSource = heightSource;
}

function registerMesh(mesh, channel = null) {
  state.allMeshes.push(mesh);
  if (channel && state[`${channel}Meshes`]) state[`${channel}Meshes`].push(mesh);
}

function channelHasCapacity(channel) {
  const meshes = state[`${channel}Meshes`];
  if (!meshes || meshes.length < channelCaps[channel]) return true;
  state.cappedChannels.add(channel);
  return false;
}

function addWallSegment(group, a, b, options) {
  const dx = (b.x - a.x) * options.unitsToMeters;
  const dz = (b.y - a.y) * options.unitsToMeters;
  const length = Math.hypot(dx, dz);
  if (length < Math.max(options.thickness * 0.35, 0.015) || state.segmentCount >= (options.maxSegments || 4200) ||
      (options.channel && !channelHasCapacity(options.channel))) return;
  if (options.seenSegments) {
    const snap = point => `${Math.round(point.x * 4)}:${Math.round(point.y * 4)}`;
    const endpoints = [snap(a), snap(b)].sort();
    const key = `${options.kind}:${endpoints[0]}:${endpoints[1]}`;
    if (options.seenSegments.has(key)) return;
    options.seenSegments.add(key);
  }
  const geometry = new THREE.BoxGeometry(length, options.height, options.thickness);
  const material = makeMaterial(options.kind);
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.set(
    (((a.x + b.x) / 2) - options.centerX) * options.unitsToMeters,
    options.height / 2,
    (((a.y + b.y) / 2) - options.centerY) * options.unitsToMeters,
  );
  mesh.rotation.y = -Math.atan2(dz, dx);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData = {
    cid: options.cid,
    role: options.kind,
    channel: options.channel || '',
    heightMm: Math.round(options.height * 1000),
    derivedFromSvg: true,
  };
  prepareHeightMesh(mesh, {
    heightMeters: options.height,
    heightAxis: 'y',
    planPoints: planWorldPoints([a, b], options),
    subtype: options.subtype || options.kind,
    confidence: options.confidence || 'derived',
    heightEditable: options.heightEditable !== false,
    floorEditable: Boolean(options.floorEditable),
    baseFloorCount: options.baseFloorCount || 1,
    floorHeightMm: options.floorHeightMm || 2700,
    heightSource: options.heightSource || 'derived',
  });
  group.add(mesh);
  if (options.channel) registerMesh(mesh, options.channel);
  else {
    state.wallMeshes.push(mesh);
    registerMesh(mesh);
  }
  state.segmentCount += 1;
}

function addParkingMarking(group, a, b, options) {
  const dx = (b.x - a.x) * options.unitsToMeters;
  const dz = (b.y - a.y) * options.unitsToMeters;
  const length = Math.hypot(dx, dz);
  if (length < 0.01) return false;
  if (options.seenSegments) {
    const snap = point => `${Math.round(point.x * 4)}:${Math.round(point.y * 4)}`;
    const endpoints = [snap(a), snap(b)].sort();
    const key = `${options.kind}:${endpoints[0]}:${endpoints[1]}`;
    if (options.seenSegments.has(key)) return false;
    options.seenSegments.add(key);
  }
  const geometry = new THREE.BoxGeometry(length, options.height, options.thickness);
  const mesh = new THREE.Mesh(geometry, makeMaterial(options.kind));
  mesh.position.set(
    (((a.x + b.x) / 2) - options.centerX) * options.unitsToMeters,
    options.height / 2 + 0.006,
    (((a.y + b.y) / 2) - options.centerY) * options.unitsToMeters,
  );
  mesh.rotation.y = -Math.atan2(dz, dx);
  mesh.castShadow = false;
  mesh.receiveShadow = true;
  mesh.userData = { cid: options.cid, role: options.kind, derivedFromSvg: true };
  prepareHeightMesh(mesh, {
    heightMeters: options.height,
    heightAxis: 'y',
    planPoints: planWorldPoints([a, b], options),
    subtype: options.kind,
    heightEditable: false,
    floorEditable: false,
  });
  group.add(mesh);
  state.parkingMeshes.push(mesh);
  registerMesh(mesh);
  return true;
}

function disposeReviewObject(object) {
  if (!object) return;
  object.traverse(child => {
    if (child.geometry) child.geometry.dispose();
    if (child.material) {
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      materials.forEach(material => material.dispose());
    }
  });
  object.parent?.remove(object);
}

function setBuildingStoreyBands(mesh, floors) {
  disposeReviewObject(mesh?.userData?.storeyBandGroup);
  if (!mesh || mesh.userData.subtype !== 'building-mass') return;
  const points = mesh.userData.planPoints || [];
  if (points.length < 3) return;
  const safeFloors = Math.max(1, Math.min(80, Math.round(Number(floors) || 1)));
  const baseHeightM = Number(mesh.userData.baseHeightMm) / 1000;
  const levels = floorGuideLevels(safeFloors, 18).filter(level => level > 0);
  const group = new THREE.Group();
  group.name = 'building-storey-bands';
  for (const level of levels) {
    const vertices = points.map(point =>
      new THREE.Vector3(point.x, -point.z, baseHeightM * level / safeFloors + 0.004));
    const geometry = new THREE.BufferGeometry().setFromPoints(vertices);
    const material = new THREE.LineBasicMaterial({
      color: '#5f6f7f', transparent: true, opacity: 0.68, depthWrite: false,
    });
    const band = new THREE.LineLoop(geometry, material);
    band.renderOrder = 3;
    band.userData = {
      cid: mesh.userData.cid,
      objectKey: mesh.userData.objectKey,
      role: 'building-storey-band',
      derivedFromSvg: true,
    };
    group.add(band);
  }
  mesh.add(group);
  mesh.userData.storeyBandGroup = group;
  mesh.userData.currentFloorCount = safeFloors;
}

function addPlanPrism(group, points, height, options, kind) {
  const cleaned = points.slice();
  if (cleaned.length > 2 && Math.hypot(cleaned[0].x - cleaned[cleaned.length - 1].x,
    cleaned[0].y - cleaned[cleaned.length - 1].y) < 1.5) cleaned.pop();
  if (cleaned.length < 3) return null;
  const shape = new THREE.Shape();
  cleaned.forEach((point, index) => {
    const x = (point.x - options.centerX) * options.unitsToMeters;
    const y = -(point.y - options.centerY) * options.unitsToMeters;
    if (index === 0) shape.moveTo(x, y); else shape.lineTo(x, y);
  });
  shape.closePath();
  let geometry;
  try {
    geometry = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false, curveSegments: 3 });
  } catch (_) { return null; }
  const mesh = new THREE.Mesh(geometry, makeMaterial(kind));
  const semanticColor = representationColor(options.subtype || kind, options.cid);
  if (semanticColor) mesh.material.color.set(semanticColor);
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = options.yOffset === undefined ? 0.006 : options.yOffset;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData = { cid: options.cid, role: kind, channel: options.channel || '', derivedFromSvg: true };
  prepareHeightMesh(mesh, {
    heightMeters: height,
    heightAxis: 'z',
    planPoints: planWorldPoints(cleaned, options, (options.yOffset === undefined ? 0.006 : options.yOffset) + 0.028),
    closed: true,
    subtype: options.subtype || kind,
    confidence: options.confidence || 'derived',
    heightEditable: options.heightEditable !== false,
    floorEditable: Boolean(options.floorEditable),
    baseFloorCount: options.baseFloorCount || 1,
    floorHeightMm: options.floorHeightMm || 2700,
    heightSource: options.heightSource || 'derived',
  });
  if (kind === 'building-mass') {
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry, 28),
      new THREE.LineBasicMaterial({ color: '#64748b', transparent: true, opacity: 0.46 }),
    );
    edges.renderOrder = 1;
    edges.userData = {
      cid: mesh.userData.cid,
      objectKey: mesh.userData.objectKey,
      role: 'building-edge',
      derivedFromSvg: true,
    };
    mesh.add(edges);
    const roof = new THREE.Mesh(new THREE.ShapeGeometry(shape), makeMaterial('building-roof'));
    roof.position.z = height + 0.008;
    roof.renderOrder = 2;
    roof.userData = {
      cid: mesh.userData.cid,
      objectKey: mesh.userData.objectKey,
      role: 'building-roof-cap',
      derivedFromSvg: true,
    };
    mesh.add(roof);
    setBuildingStoreyBands(mesh, options.baseFloorCount || 1);
  }
  group.add(mesh);
  registerMesh(mesh, options.channel);
  if (kind === 'column') state.columnMeshes.push(mesh);
  if (kind === 'furniture') state.furnitureMeshes.push(mesh);
  return mesh;
}

function addColumn(group, root, element, options, points = null) {
  let geometry;
  let center;
  if (element.localName === 'circle' || element.localName === 'ellipse') {
    const cx = numericAttribute(element, 'cx'), cy = numericAttribute(element, 'cy');
    const radius = numericAttribute(element, element.localName === 'circle' ? 'r' : 'rx');
    const radiusY = numericAttribute(element, element.localName === 'circle' ? 'r' : 'ry', radius);
    center = localToRoot(root, element, cx, cy);
    const edge = localToRoot(root, element, cx + radius, cy);
    const edgeY = localToRoot(root, element, cx, cy + radiusY);
    const radiusM = Math.max(0.03, Math.hypot(edge.x - center.x, edge.y - center.y) * options.unitsToMeters);
    geometry = new THREE.CylinderGeometry(radiusM, radiusM, options.height, 24);
    geometry.scale(1, 1, Math.max(0.05, Math.hypot(edgeY.x - center.x, edgeY.y - center.y) * options.unitsToMeters) / radiusM);
  } else if (points && isClosedElement(element, points)) {
    return addPlanPrism(group, points, options.height, { ...options, cid: element.getAttribute('data-cid') }, 'column');
  } else {
    const box = element.getBBox();
    center = localToRoot(root, element, box.x + box.width / 2, box.y + box.height / 2);
    const corner = localToRoot(root, element, box.x + box.width, box.y + box.height);
    const width = Math.max(0.04, Math.abs(corner.x - center.x) * 2 * options.unitsToMeters);
    const depth = Math.max(0.04, Math.abs(corner.y - center.y) * 2 * options.unitsToMeters);
    geometry = new THREE.BoxGeometry(width, options.height, depth);
  }
  const mesh = new THREE.Mesh(geometry, makeMaterial('column'));
  mesh.position.set((center.x - options.centerX) * options.unitsToMeters, options.height / 2,
                    (center.y - options.centerY) * options.unitsToMeters);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData = { cid: element.getAttribute('data-cid'), role: 'column', derivedFromSvg: true };
  prepareHeightMesh(mesh, { heightMeters: options.height, heightAxis: 'y' });
  group.add(mesh);
  state.allMeshes.push(mesh);
  state.columnMeshes.push(mesh);
  return mesh;
}

function addFurniture(group, root, element, options, points = null) {
  if (element.localName === 'circle' || element.localName === 'ellipse') {
    const furnitureOptions = { ...options, height: options.furnitureHeight };
    const mesh = addColumn(group, root, element, furnitureOptions, points);
    if (!mesh) return null;
    const index = state.columnMeshes.indexOf(mesh);
    if (index >= 0) state.columnMeshes.splice(index, 1);
    mesh.material.dispose();
    mesh.material = makeMaterial('furniture');
    mesh.userData.role = 'furniture';
    state.furnitureMeshes.push(mesh);
    return mesh;
  }
  const outline = points || elementPoints(root, element, 28);
  return addPlanPrism(group, outline, options.furnitureHeight,
    { ...options, cid: element.getAttribute('data-cid') }, 'furniture');
}

function addFlatSegment(group, a, b, options) {
  const dx = (b.x - a.x) * options.unitsToMeters;
  const dz = (b.y - a.y) * options.unitsToMeters;
  const length = Math.hypot(dx, dz);
  if (length < 0.01 || !channelHasCapacity(options.channel)) return false;
  const geometry = new THREE.BoxGeometry(length, options.height, options.thickness);
  const mesh = new THREE.Mesh(geometry, makeMaterial(options.kind));
  mesh.position.set(
    (((a.x + b.x) / 2) - options.centerX) * options.unitsToMeters,
    options.yOffset + options.height / 2,
    (((a.y + b.y) / 2) - options.centerY) * options.unitsToMeters,
  );
  mesh.rotation.y = -Math.atan2(dz, dx);
  mesh.castShadow = false;
  mesh.receiveShadow = true;
  mesh.userData = { cid: options.cid, role: options.kind, channel: options.channel, derivedFromSvg: true };
  prepareHeightMesh(mesh, {
    heightMeters: options.height,
    heightAxis: 'y',
    planPoints: planWorldPoints([a, b], options, options.yOffset + 0.028),
    subtype: options.subtype || options.kind,
    confidence: options.confidence || 'derived',
    heightEditable: Boolean(options.heightEditable),
    floorEditable: false,
  });
  group.add(mesh);
  registerMesh(mesh, options.channel);
  return true;
}

function numericSemanticAttribute(element, names) {
  for (const name of names) {
    const raw = semanticAttribute(element, name);
    if (!raw) continue;
    const value = Number(raw);
    if (Number.isFinite(value)) return value;
  }
  return null;
}

function ovalPlanPoints(box, count = 16) {
  const center = boxCenter(box);
  const radiusX = boxWidth(box) / 2;
  const radiusY = boxHeight(box) / 2;
  return Array.from({ length: count }, (_, index) => {
    const angle = Math.PI * 2 * index / count;
    return { x: center.x + Math.cos(angle) * radiusX, y: center.y + Math.sin(angle) * radiusY };
  });
}

function addPlantVolume(group, root, element, representation, options) {
  if (!channelHasCapacity('landscape')) return null;
  const box = elementBox(root, element);
  if (!box) return null;
  const center = boxCenter(box);
  const radius = Math.max(0.08, Math.max(boxWidth(box), boxHeight(box)) * options.unitsToMeters / 2);
  const subtype = representation.subtype;
  const presentation = vegetationPresentation({
    subtype,
    radiusMeters: radius,
    explicitHeightMm: element.getAttribute('data-height-mm'),
  });
  const totalHeight = presentation.heightMeters;
  const trunkHeight = totalHeight * (subtype === 'tree' ? 0.48 : 0.3);
  const trunkRadius = Math.max(subtype === 'tree' ? 0.06 : 0.035,
    radius * (subtype === 'tree' ? 0.11 : 0.24));
  const trunk = new THREE.Mesh(
    new THREE.CylinderGeometry(trunkRadius, trunkRadius * 1.08, trunkHeight, 10),
    makeMaterial('tree-trunk'),
  );
  const groundY = 0.008;
  trunk.position.set(
    (center.x - options.centerX) * options.unitsToMeters,
    groundY + trunkHeight / 2,
    (center.y - options.centerY) * options.unitsToMeters,
  );
  trunk.castShadow = true;
  trunk.receiveShadow = true;
  trunk.userData = {
    cid: element.getAttribute('data-cid'),
    role: subtype,
    channel: 'landscape',
    derivedFromSvg: true,
  };
  prepareHeightMesh(trunk, {
    heightMeters: totalHeight,
    heightAxis: 'y',
    planPoints: planWorldPoints(ovalPlanPoints(box), options),
    closed: true,
    subtype,
    confidence: representation.confidence,
    heightEditable: true,
    floorEditable: false,
    heightTransform: 'ground-scale',
    groundY,
    heightSource: presentation.heightSource,
  });
  const crownRadius = presentation.crownRadius;
  const lobeSpecs = subtype === 'tree' ? [
    { x: 0, y: totalHeight * 0.7 - trunkHeight / 2, z: 0, scale: 1 },
    { x: -crownRadius * 0.52, y: totalHeight * 0.61 - trunkHeight / 2, z: crownRadius * 0.18, scale: 0.72 },
    { x: crownRadius * 0.5, y: totalHeight * 0.64 - trunkHeight / 2, z: -crownRadius * 0.2, scale: 0.68 },
  ] : [
    { x: -crownRadius * 0.22, y: totalHeight * 0.58 - trunkHeight / 2, z: 0, scale: 0.9 },
    { x: crownRadius * 0.28, y: totalHeight * 0.54 - trunkHeight / 2, z: crownRadius * 0.08, scale: 0.76 },
  ];
  for (let index = 0; index < lobeSpecs.length; index += 1) {
    const spec = lobeSpecs[index];
    const crown = new THREE.Mesh(
      new THREE.SphereGeometry(crownRadius * spec.scale, 14, 10),
      makeMaterial(subtype === 'tree' && index > 0 ? 'tree-canopy-light' :
        subtype === 'tree' ? 'tree-canopy' : 'shrub'),
    );
    const crownColor = representationColor(subtype, `${trunk.userData.cid}:${index}`);
    if (crownColor) crown.material.color.set(crownColor);
    crown.scale.y = subtype === 'tree' ? 0.88 : 0.68;
    crown.position.set(spec.x, spec.y, spec.z);
    crown.castShadow = true;
    crown.receiveShadow = true;
    crown.userData = {
      cid: trunk.userData.cid,
      role: subtype,
      subtype,
      objectKey: trunk.userData.objectKey,
      channel: 'landscape',
      derivedFromSvg: true,
    };
    trunk.add(crown);
  }
  group.add(trunk);
  registerMesh(trunk, 'landscape');
  return trunk;
}

function addDisciplineElement(group, root, element, channel, options, representation) {
  if (!channelHasCapacity(channel)) return { created: false, flatTerrain: false, capped: true };
  const tag = element.localName;
  const cid = element.getAttribute('data-cid');
  const points = elementPoints(root, element, 32);
  const subtype = representation?.subtype || channel;
  const confidence = representation?.confidence || 'derived';
  const capabilities = representationCapabilities(subtype, channel);
  const elevationMm = channel === 'terrain' ?
    numericSemanticAttribute(element, ['data-elevation-mm', 'data-z-mm']) : null;
  const yOffset = elevationMm === null ? 0.008 : elevationMm / 1000;
  const flatHeight = channel === 'terrain' ? 0.012 : channel === 'civil' ? 0.025 : 0.05;
  const closed = isClosedElement(element, points);
  const buildingBox = channel === 'building' ? elementBox(root, element) : null;
  const buildingView = channel === 'building' && subtype === 'building-mass' ? buildingPresentation({
    explicitHeightMm: numericSemanticAttribute(element, ['data-height-mm']),
    explicitFloors: numericSemanticAttribute(element, ['data-floors', 'data-storeys', 'data-stories']),
    floorHeightMm: numericSemanticAttribute(element, ['data-floor-height-mm', 'data-storey-height-mm']),
    longSideMeters: buildingBox ?
      Math.max(boxWidth(buildingBox), boxHeight(buildingBox)) * options.unitsToMeters : 0,
  }) : null;
  let created = false;

  if (subtype === 'tree' || subtype === 'shrub') {
    const mesh = addPlantVolume(group, root, element, representation, options);
    return { created: Boolean(mesh), flatTerrain: false, capped: state.cappedChannels.has(channel), subtype };
  }

  if (subtype === 'hedge') {
    const height = elementHeightMeters(element, 1.15);
    if (closed && points.length >= 3) {
      const mesh = addPlanPrism(group, points, height, {
        ...options, cid, channel, yOffset, subtype, confidence,
        heightEditable: true, floorEditable: false,
      }, 'hedge');
      return { created: Boolean(mesh), flatTerrain: false, capped: state.cappedChannels.has(channel), subtype };
    }
    for (let index = 1; index < points.length; index += 1) {
      const before = state.segmentCount;
      addWallSegment(group, points[index - 1], points[index], {
        ...options, cid, kind: 'hedge', channel: 'landscape', height,
        thickness: Math.max(0.22, options.thickness * 1.4),
        subtype, confidence, heightEditable: true, floorEditable: false,
      });
      created = created || state.segmentCount > before;
    }
    return { created, flatTerrain: false, capped: state.cappedChannels.has(channel), subtype };
  }

  if (channel === 'building' && points.length >= 2 && !closed) {
    const height = buildingView ? buildingView.heightMm / 1000 : elementHeightMeters(element, options.height);
    for (let index = 1; index < points.length; index += 1) {
      const before = state.segmentCount;
      addWallSegment(group, points[index - 1], points[index], {
        ...options, cid, kind: subtype, channel: 'building', height,
        subtype, confidence, heightEditable: capabilities.heightEditable,
        floorEditable: capabilities.floorEditable,
        baseFloorCount: buildingView?.floors || 1,
        floorHeightMm: buildingView?.floorHeightMm || 2700,
        heightSource: buildingView?.heightSource || 'derived',
      });
      created = created || state.segmentCount > before;
    }
    return { created, flatTerrain: false, capped: state.cappedChannels.has(channel), subtype };
  }

  if ((tag === 'circle' || tag === 'ellipse') && channel !== 'building') {
    const box = elementBox(root, element);
    if (!box) return { created: false, flatTerrain: false, capped: false };
    const center = boxCenter(box);
    const radius = Math.max(0.02, Math.max(boxWidth(box), boxHeight(box)) * options.unitsToMeters / 2);
    const height = flatHeight;
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, height, 20), makeMaterial(subtype));
    mesh.position.set((center.x - options.centerX) * options.unitsToMeters, yOffset + height / 2,
      (center.y - options.centerY) * options.unitsToMeters);
    mesh.castShadow = channel === 'landscape';
    mesh.receiveShadow = true;
    mesh.userData = { cid, role: subtype, channel, derivedFromSvg: true };
    prepareHeightMesh(mesh, {
      heightMeters: height, heightAxis: 'y', subtype, confidence,
      heightEditable: capabilities.heightEditable, floorEditable: capabilities.floorEditable,
    });
    group.add(mesh);
    registerMesh(mesh, channel);
    return { created: true, flatTerrain: channel === 'terrain' && elevationMm === null, capped: false, subtype };
  }

  if (closed && points.length >= 3) {
    const height = buildingView ? buildingView.heightMm / 1000 :
      subtype === 'building-roof' ? 0.16 :
      channel === 'building' ? elementHeightMeters(element, options.height) : flatHeight;
    const mesh = addPlanPrism(group, points, height, {
      ...options, cid, channel, yOffset, subtype, confidence,
      heightEditable: capabilities.heightEditable, floorEditable: capabilities.floorEditable,
      baseFloorCount: buildingView?.floors || 1,
      floorHeightMm: buildingView?.floorHeightMm || 2700,
      heightSource: buildingView?.heightSource || 'derived',
    }, subtype);
    return { created: Boolean(mesh), flatTerrain: Boolean(mesh) && channel === 'terrain' && elevationMm === null,
      capped: state.cappedChannels.has(channel), subtype };
  }

  if (points.length >= 2) {
    for (let index = 1; index < points.length; index += 1) {
      created = addFlatSegment(group, points[index - 1], points[index], {
        ...options, cid, kind: subtype, subtype, confidence, channel, height: flatHeight,
        heightEditable: capabilities.heightEditable,
        thickness: channel === 'terrain' ? 0.018 : Math.max(0.024, options.thickness * 0.22), yOffset,
      }) || created;
    }
  }
  return { created, flatTerrain: created && channel === 'terrain' && elevationMm === null,
    capped: state.cappedChannels.has(channel), subtype };
}

function setMeshesVisible(meshes, visible) {
  for (const mesh of meshes) mesh.visible = visible;
}

// Visibility is deliberately separate from recognition: toggles and presets do
// not reclassify or recount the SVG; they only show/hide the already-built mesh channels.
function syncCategoryVisibility() {
  setMeshesVisible(state.wallMeshes, $('threeShowWalls').checked);
  setMeshesVisible(state.columnMeshes, $('threeShowColumns').checked);
  setMeshesVisible(state.furnitureMeshes, $('threeShowFurniture').checked);
  setMeshesVisible(state.parkingMeshes, $('threeShowParking').checked);
  setMeshesVisible(state.buildingMeshes, $('threeShowBuilding').checked);
  setMeshesVisible(state.landscapeMeshes, $('threeShowLandscape').checked);
  setMeshesVisible(state.civilMeshes, $('threeShowCivil').checked);
  setMeshesVisible(state.terrainMeshes, $('threeShowTerrain').checked);
}

function deriveFocusBox(root, graphics, viewBox) {
  const maxDim = Math.max(viewBox.width, viewBox.height, 1);
  const seeds = [];
  const anchorLines = [];
  for (const element of graphics) {
    const role = roleText(element);
    const structural = /wall|partition|column|glazing|shell|boundary/.test(role) ||
      numericStyle(element, 'stroke-width', 0) >= 1.5;
    if (!structural || hiddenOrAnnotation(element)) continue;
    const box = elementBox(root, element);
    if (!box) continue;
    const width = boxWidth(box), height = boxHeight(box), span = Math.max(width, height);
    if (element.localName === 'line' && span >= maxDim * 0.08 && span <= maxDim * 0.65) {
      anchorLines.push(box);
    }
    if (span < maxDim * 0.0005 || span > maxDim * 0.3) continue;
    seeds.push(box);
  }
  if (seeds.length < 8) return { x0: viewBox.x, y0: viewBox.y, x1: viewBox.x + viewBox.width, y1: viewBox.y + viewBox.height };
  const centers = seeds.map(boxCenter);
  const centerWindow = {
    x0: quantile(centers.map(point => point.x), 0.015),
    y0: quantile(centers.map(point => point.y), 0.015),
    x1: quantile(centers.map(point => point.x), 0.985),
    y1: quantile(centers.map(point => point.y), 0.985),
  };
  const kept = seeds.filter(box => boxInside(box, centerWindow));
  if (!kept.length) return centerWindow;
  const raw = {
    x0: Math.min(...kept.map(box => box.x0)), y0: Math.min(...kept.map(box => box.y0)),
    x1: Math.max(...kept.map(box => box.x1)), y1: Math.max(...kept.map(box => box.y1)),
  };
  const pad = Math.max(raw.x1 - raw.x0, raw.y1 - raw.y0) * 0.025;
  const focus = {
    x0: Math.max(viewBox.x, raw.x0 - pad), y0: Math.max(viewBox.y, raw.y0 - pad),
    x1: Math.min(viewBox.x + viewBox.width, raw.x1 + pad),
    y1: Math.min(viewBox.y + viewBox.height, raw.y1 + pad),
  };
  // Flattened CAD sheets often put dimension grids and title blocks around the
  // plan. Long structural axes give a more reliable horizontal footprint; use
  // them only to trim the inferred review window, never to mutate source data.
  const keptAnchors = anchorLines.filter(box => boxInside(box, focus));
  if (keptAnchors.length < 4) return focus;
  const anchorBox = {
    x0: Math.min(...keptAnchors.map(box => box.x0)),
    y0: Math.min(...keptAnchors.map(box => box.y0)),
    x1: Math.max(...keptAnchors.map(box => box.x1)),
    y1: Math.max(...keptAnchors.map(box => box.y1)),
  };
  if (boxWidth(anchorBox) < boxWidth(focus) * 0.25) return focus;
  const anchorScoped = seeds.map(boxCenter).filter(point =>
    point.x >= anchorBox.x0 && point.x <= anchorBox.x1 && point.y >= focus.y0 && point.y <= focus.y1);
  if (anchorScoped.length < 20) return focus;
  const y0 = quantile(anchorScoped.map(point => point.y), 0.01);
  const y1 = quantile(anchorScoped.map(point => point.y), 0.99);
  const refinedPad = Math.max(boxWidth(anchorBox), y1 - y0) * 0.015;
  return {
    x0: Math.max(viewBox.x, anchorBox.x0 - refinedPad),
    y0: Math.max(viewBox.y, y0 - refinedPad),
    x1: Math.min(viewBox.x + viewBox.width, anchorBox.x1 + refinedPad),
    y1: Math.min(viewBox.y + viewBox.height, y1 + refinedPad),
  };
}

function shapeFrequency(root, graphics, focusBox, maxDim) {
  const counts = new Map();
  for (const element of graphics) {
    if (!['circle', 'ellipse', 'polygon', 'polyline', 'rect'].includes(element.localName)) continue;
    const box = elementBox(root, element);
    if (!box || !boxInside(box, focusBox)) continue;
    const size = Math.max(boxWidth(box), boxHeight(box));
    if (size < maxDim * 0.0006 || size > maxDim * 0.04) continue;
    const key = repeatedShapeKey(element, box, maxDim);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return counts;
}

function labelCenters(root) {
  const centers = [];
  for (const element of root.querySelectorAll('text')) {
    const x = numericAttribute(element, 'x', NaN), y = numericAttribute(element, 'y', NaN);
    if (Number.isFinite(x) && Number.isFinite(y)) centers.push(localToRoot(root, element, x, y));
  }
  return centers;
}

function hasCenteredLabel(box, labels) {
  const center = boxCenter(box);
  const radius = Math.max(10, Math.max(boxWidth(box), boxHeight(box)) * 0.7);
  return labels.some(point => Math.hypot(point.x - center.x, point.y - center.y) <= radius);
}

function visibleStroke(element) {
  const stroke = styleValue(element, 'stroke', 'black').toLowerCase();
  return stroke !== 'none' && stroke !== 'transparent' && stroke !== 'white' && stroke !== '#fff' && stroke !== '#ffffff';
}

// MULTIDISCIPLINE_CLASSIFIER_START
function greenTone(value) {
  const text = String(value || '').trim().toLowerCase();
  if (/green|lime|olive|forest|seagreen|chartreuse/.test(text)) return true;
  const hex = text.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    const raw = hex[1].length === 3 ? hex[1].split('').map(part => part + part).join('') : hex[1];
    const red = Number.parseInt(raw.slice(0, 2), 16), green = Number.parseInt(raw.slice(2, 4), 16);
    const blue = Number.parseInt(raw.slice(4, 6), 16);
    return green >= 70 && green >= red * 1.06 && green >= blue * 1.25 && green - blue >= 25;
  }
  const rgb = text.match(/^rgba?\(\s*([\d.]+)[,\s]+\s*([\d.]+)[,\s]+\s*([\d.]+)/);
  return Boolean(rgb) && Number(rgb[2]) >= 70 && Number(rgb[2]) >= Number(rgb[1]) * 1.06 &&
    Number(rgb[2]) >= Number(rgb[3]) * 1.25 && Number(rgb[2]) - Number(rgb[3]) >= 25;
}

function neutralTone(value, maxChannel = 255) {
  const text = String(value || '').trim().toLowerCase();
  if (/^(?:black|gray|grey|dimgray|dimgrey|darkgray|darkgrey|silver|white)$/.test(text)) {
    return maxChannel >= (text === 'white' ? 255 : text === 'silver' ? 192 : text === 'black' ? 0 : 169);
  }
  let channels = null;
  const hex = text.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    const raw = hex[1].length === 3 ? hex[1].split('').map(part => part + part).join('') : hex[1];
    channels = [0, 2, 4].map(index => Number.parseInt(raw.slice(index, index + 2), 16));
  }
  const rgb = text.match(/^rgba?\(\s*([\d.]+)[,\s]+\s*([\d.]+)[,\s]+\s*([\d.]+)/);
  if (rgb) channels = rgb.slice(1, 4).map(Number);
  if (!channels || channels.some(channel => !Number.isFinite(channel))) return false;
  return Math.max(...channels) <= maxChannel && Math.max(...channels) - Math.min(...channels) <= 24;
}

// Pure marker: explicit element/group semantics win. Untagged site content is
// accepted only when deterministic geometry inference has already established
// a building/structure, or when its own paint is unambiguously green.
function classifyMultiDiscipline(record = {}) {
  const channelFor = value => {
    const semantic = String(value || '').toLowerCase();
    if (/\bterrain\b|elevation|\btopo(?:graphy)?\b|grade(?:line)?|landform/.test(semantic)) return 'terrain';
    if (/\bbuilding\b|\bstructure\b|structural|foundation|retaining|superstructure|exterior|facade|façade|\broof\b|massing|envelope/.test(semantic)) return 'building';
    if (/\bcivil\b|road|driveway|curb|drain(?:age)?|manhole|catch[ _-]?basin|gutter|utility|site[-_ ]?work|contour/.test(semantic)) return 'civil';
    if (/\blandscape\b|tree|shrub|lawn|plant(?:ing)?|paving|water|bioswale|garden|site\b/.test(semantic)) return 'landscape';
    return null;
  };
  // Metadata is resolved from an element outward through parent groups. A more
  // specific data field wins if a source happens to contain conflicting labels.
  for (const value of [record.discipline, record.role, record.symbol, record.name]) {
    const channel = channelFor(value);
    if (channel) return channel;
  }
  const explicit = Boolean(record.discipline || record.role || record.symbol || record.name);
  if (explicit) return null;
  const green = greenTone(record.fill) || greenTone(record.stroke);
  if (green && (Number(record.repeated) >= 2 || Boolean(record.closed))) return 'landscape';
  if (record.siteBuilding || record.siteStructure) return 'building';
  return null;
}

function allowInferredFurniture(record = {}) {
  return !record.siteDominant && !record.siteStructureContext;
}

function classifySiteStructureGeometry(record = {}) {
  const sceneMax = Math.max(1, Number(record.sceneMax) || 1);
  const strokeWidth = Number(record.strokeWidth) || 0;
  const strokeThreshold = Number(record.strokeThreshold) || Infinity;
  if (!record.siteDominant || record.green || !record.neutralStroke || strokeWidth < strokeThreshold) return null;
  const size = Number(record.size) || 0;
  const shortSide = Number(record.shortSide) || 0;
  const aspect = Number(record.aspect) || Infinity;
  const pointCount = Number(record.pointCount) || 0;
  if (record.closed && size >= sceneMax * 0.055 && size <= sceneMax * 0.38 &&
      shortSide >= sceneMax * 0.012 && aspect <= 12 && pointCount >= 8) return 'building';
  if (!record.closed && size >= sceneMax * 0.07 && aspect >= 8 && pointCount >= 2) return 'structure';
  return null;
}

function classifyDisciplineRepresentation(record = {}) {
  const channel = record.channel || classifyMultiDiscipline(record);
  const semantic = [record.discipline, record.role, record.symbol, record.name]
    .filter(Boolean).join(' ').toLowerCase();
  const explicit = Boolean(semantic);
  const result = subtype => ({ channel, subtype, confidence: explicit ? 'explicit' : 'inferred' });
  if (channel === 'building') {
    if (/\broof\b|canopy/.test(semantic)) return result('building-roof');
    if (record.siteStructure || /retaining|foundation|\bstructure\b|structural/.test(semantic)) {
      return result('structure-linear');
    }
    return result('building-mass');
  }
  if (channel === 'landscape') {
    if (/water|pond|stream|bioswale|wetland/.test(semantic)) return result('water');
    if (/paving|hardscape|plaza|deck|terrace/.test(semantic)) return result('hardscape');
    if (/hedge|screening/.test(semantic)) return result('hedge');
    if (/shrub|bush/.test(semantic)) return result('shrub');
    if (/\btree\b|arbor|canopy/.test(semantic)) return result('tree');
    if (/lawn|plant(?:ing)?|garden|groundcover|meadow/.test(semantic)) return result('planting');
    const compactVegetation = record.closed && Number(record.sizeRatio) >= 0.00025 &&
      Number(record.sizeRatio) <= 0.018 && Number(record.aspect) <= 2.5;
    if (compactVegetation) return result(Number(record.repeated) >= 2 ? 'tree' : 'shrub');
    if (record.closed) return result('planting');
    if (Number(record.aspect) >= 7) return result('hedge');
    return result('planting-line');
  }
  if (channel === 'civil') {
    if (/road|driveway|street|lane/.test(semantic)) return result('road');
    if (/paving|hardscape|plaza|sidewalk|walkway/.test(semantic)) return result('hardscape');
    if (/curb|drain|gutter|manhole|utility|catch[ _-]?basin/.test(semantic)) return result('civil-utility');
    return result(record.closed ? 'civil-surface' : 'civil-line');
  }
  if (channel === 'terrain') return result(record.closed ? 'terrain-surface' : 'terrain-contour');
  return { channel: null, subtype: 'unclassified-2d', confidence: 'unknown' };
}

function representationCapabilities(subtype, role = '') {
  const kind = String(subtype || role || '').toLowerCase();
  const floorEditable = kind === 'building-mass';
  const heightEditable = floorEditable || [
    'structure-linear', 'tree', 'shrub', 'hedge', 'wall', 'glass', 'column', 'furniture',
  ].includes(kind);
  return { heightEditable, floorEditable };
}

function representationLabel(subtype, fallback = '객체') {
  const labels = {
    'building-mass': '건축 매스',
    'building-roof': '지붕',
    'structure-linear': '외부 구조물',
    tree: '수목',
    shrub: '관목',
    hedge: '생울타리',
    planting: '식재면',
    'planting-line': '식재선',
    water: '수공간',
    hardscape: '포장',
    road: '도로',
    'civil-utility': '토목 시설',
    'civil-surface': '토목 면',
    'civil-line': '토목 선',
    'terrain-surface': '지형면',
    'terrain-contour': '등고선',
    wall: '벽',
    glass: '유리벽',
    column: '기둥',
    furniture: '가구',
    parking: '주차',
    'ev-parking': '전기차 주차',
  };
  return labels[subtype] || fallback;
}

function stableVisualIndex(value, length) {
  const text = String(value || '');
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash >>> 0) % Math.max(1, length);
}

function buildingPresentation(record = {}) {
  const floorHeightMm = Math.max(2400, Math.min(5000, Number(record.floorHeightMm) || 3000));
  const explicitHeightMm = Number(record.explicitHeightMm);
  const explicitFloors = Number(record.explicitFloors);
  let floors;
  let heightMm;
  let heightSource;
  if (Number.isFinite(explicitFloors) && explicitFloors > 0) {
    floors = Math.max(1, Math.min(80, Math.round(explicitFloors)));
    heightMm = Number.isFinite(explicitHeightMm) && explicitHeightMm > 0 ?
      explicitHeightMm : floors * floorHeightMm;
    heightSource = Number.isFinite(explicitHeightMm) && explicitHeightMm > 0 ? 'svg-height-and-floors' : 'svg-floors';
  } else if (Number.isFinite(explicitHeightMm) && explicitHeightMm > 0) {
    heightMm = explicitHeightMm;
    floors = Math.max(1, Math.min(80, Math.round(heightMm / floorHeightMm)));
    heightSource = 'svg-height';
  } else {
    const longSideMeters = Math.max(0, Number(record.longSideMeters) || 0);
    floors = Math.max(2, Math.min(12, Math.round(longSideMeters / 3.2)));
    heightMm = floors * floorHeightMm;
    heightSource = 'footprint-inferred';
  }
  return {
    floors,
    floorHeightMm,
    heightMm: Math.max(10, Math.min(200000, Math.round(Number(heightMm) || floors * floorHeightMm))),
    heightSource,
  };
}

function vegetationPresentation(record = {}) {
  const subtype = String(record.subtype || 'shrub');
  const radius = Math.max(0.05, Number(record.radiusMeters) || 0.05);
  const explicitHeightMm = Number(record.explicitHeightMm);
  const inferredHeight = subtype === 'tree' ?
    Math.max(4.5, Math.min(13, radius * 5.4)) :
    Math.max(0.8, Math.min(2.4, radius * 1.8));
  const heightMeters = Number.isFinite(explicitHeightMm) && explicitHeightMm > 0 ?
    explicitHeightMm / 1000 : inferredHeight;
  const crownRadius = subtype === 'tree' ?
    Math.max(0.75, Math.min(2.8, Math.max(radius * 1.25, heightMeters * 0.2))) :
    Math.max(0.34, Math.min(1.4, Math.max(radius * 1.12, heightMeters * 0.38)));
  return {
    heightMeters,
    crownRadius,
    heightSource: Number.isFinite(explicitHeightMm) && explicitHeightMm > 0 ? 'svg-height' : 'geometry-inferred',
  };
}

function representationColor(subtype, cid = '') {
  const palettes = {
    'building-mass': ['#d9d3c7', '#c8d1d8', '#d8c7b6', '#c4cbc3', '#d2c8ce'],
    planting: ['#6f9955', '#789f5d', '#648d50', '#809f62'],
    'planting-line': ['#658b4f', '#72975a', '#5c8249'],
    tree: ['#2f7545', '#3b824c', '#28703f', '#478b52'],
    shrub: ['#4f8d4d', '#5d9854', '#478346', '#6a9d59'],
    hardscape: ['#a39a8e', '#958d82', '#aaa194'],
  };
  const palette = palettes[subtype];
  return palette ? palette[stableVisualIndex(cid, palette.length)] : null;
}
// MULTIDISCIPLINE_CLASSIFIER_END

function detectSiteDominantDrawing(graphics, threshold = 160) {
  let greenSignals = 0;
  for (const element of graphics) {
    if (greenTone(styleValue(element, 'fill', '')) || greenTone(styleValue(element, 'stroke', ''))) {
      greenSignals += 1;
      if (greenSignals >= threshold) return true;
    }
  }
  return false;
}

function deriveSiteFocusBox(root, graphics, viewBox) {
  const maxDim = Math.max(viewBox.width, viewBox.height, 1);
  const boxes = [];
  for (const element of graphics) {
    if (!greenTone(styleValue(element, 'fill', '')) && !greenTone(styleValue(element, 'stroke', ''))) continue;
    const box = elementBox(root, element);
    if (!box) continue;
    const span = Math.max(boxWidth(box), boxHeight(box));
    if (span < maxDim * 0.0002 || span > maxDim * 0.7) continue;
    boxes.push(box);
  }
  if (boxes.length < 8) return viewBox;
  const centers = boxes.map(boxCenter);
  const centerWindow = {
    x0: quantile(centers.map(point => point.x), 0.005),
    y0: quantile(centers.map(point => point.y), 0.005),
    x1: quantile(centers.map(point => point.x), 0.995),
    y1: quantile(centers.map(point => point.y), 0.995),
  };
  const kept = boxes.filter(box => boxInside(box, centerWindow));
  if (!kept.length) return viewBox;
  const raw = {
    x0: Math.min(...kept.map(box => box.x0)), y0: Math.min(...kept.map(box => box.y0)),
    x1: Math.max(...kept.map(box => box.x1)), y1: Math.max(...kept.map(box => box.y1)),
  };
  const pad = Math.max(boxWidth(raw), boxHeight(raw), 1) * 0.035;
  const focus = {
    x0: Math.max(viewBox.x, raw.x0 - pad), y0: Math.max(viewBox.y, raw.y0 - pad),
    x1: Math.min(viewBox.x + viewBox.width, raw.x1 + pad),
    y1: Math.min(viewBox.y + viewBox.height, raw.y1 + pad),
  };
  if (boxWidth(focus) < viewBox.width * 0.05 || boxHeight(focus) < viewBox.height * 0.05) return viewBox;
  return focus;
}

function siteGeometryPointCount(element) {
  if (element.localName === 'line') return 2;
  if (element.localName === 'polyline' || element.localName === 'polygon') return rawPointList(element).length;
  if (element.localName === 'rect') return 4;
  if (element.localName === 'path') return ((element.getAttribute('d') || '').match(/[a-z]/gi) || []).length;
  return 0;
}

function siteBoxesNear(a, b, tolerance) {
  return Math.abs(a.x0 - b.x0) <= tolerance && Math.abs(a.y0 - b.y0) <= tolerance &&
    Math.abs(a.x1 - b.x1) <= tolerance && Math.abs(a.y1 - b.y1) <= tolerance;
}

function sitePointInPolygon(point, polygon) {
  let inside = false;
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
    const a = polygon[index], b = polygon[previous];
    const crosses = (a.y > point.y) !== (b.y > point.y) &&
      point.x < ((b.x - a.x) * (point.y - a.y)) / ((b.y - a.y) || Number.EPSILON) + a.x;
    if (crosses) inside = !inside;
  }
  return inside;
}

function siteElementPolygon(root, element) {
  if (element.localName === 'rect') return elementPoints(root, element, 5);
  if (element.localName === 'polygon' || element.localName === 'polyline') {
    return rawPointList(element).map(point => localToRoot(root, element, point.x, point.y));
  }
  if (element.localName === 'path') return elementPoints(root, element, 64);
  return [];
}

// Illustrator/site-plan exports frequently lose layer names while retaining a
// reliable visual hierarchy: building envelopes are large, closed, complex,
// neutral outlines drawn in the heaviest few percent of strokes. Detect those
// envelopes relative to the current sheet, deduplicate export copies, and keep
// their internal detail from falling through to the compact-furniture heuristic.
function inferSiteStructures(root, graphics, focusBox, sceneMax) {
  const records = [];
  const strokeWidths = [];
  const allowedTags = new Set(['line', 'polyline', 'polygon', 'path', 'rect']);
  for (const element of graphics) {
    if (!allowedTags.has(element.localName) || hiddenOrAnnotation(element) || !visibleStroke(element)) continue;
    const stroke = styleValue(element, 'stroke', '');
    if (!neutralTone(stroke, 205) || greenTone(stroke) || greenTone(styleValue(element, 'fill', ''))) continue;
    const box = elementBox(root, element);
    if (!box || !boxInside(box, focusBox)) continue;
    const strokeWidth = numericStyle(element, 'stroke-width', 0);
    if (!(strokeWidth > 0)) continue;
    strokeWidths.push(strokeWidth);
    const width = boxWidth(box), height = boxHeight(box);
    const size = Math.max(width, height);
    const aspect = size / Math.max(0.1, Math.min(width, height));
    const rawPoints = element.localName === 'polyline' || element.localName === 'polygon' ? rawPointList(element) : null;
    records.push({
      element, box, strokeWidth, size, aspect,
      pointCount: siteGeometryPointCount(element),
      closed: isClosedElement(element, rawPoints),
    });
  }
  if (strokeWidths.length < 12) return {
    buildings: new Set(), structures: new Set(), members: new Set(), footprints: [], strokeThreshold: Infinity,
  };

  const strokeThreshold = Math.max(Number.EPSILON, quantile(strokeWidths, 0.985) * 0.96);
  const candidateRecords = records.map(record => ({
    ...record,
    siteKind: classifySiteStructureGeometry({
      siteDominant: true,
      green: false,
      neutralStroke: true,
      strokeWidth: record.strokeWidth,
      strokeThreshold,
      sceneMax,
      size: record.size,
      shortSide: Math.min(boxWidth(record.box), boxHeight(record.box)),
      aspect: record.aspect,
      pointCount: record.pointCount,
      closed: record.closed,
    }),
  })).filter(record => record.siteKind).sort((a, b) => Number(b.closed) - Number(a.closed) ||
    b.strokeWidth - a.strokeWidth ||
    b.pointCount - a.pointCount);

  const tolerance = Math.max(0.1, sceneMax * 0.0018);
  const selected = [];
  const members = new Set(candidateRecords.map(record => record.element));
  for (const record of candidateRecords) {
    if (selected.some(other => other.closed === record.closed && siteBoxesNear(other.box, record.box, tolerance))) continue;
    selected.push(record);
  }
  const buildings = new Set(selected.filter(record => record.siteKind === 'building').map(record => record.element));
  const structures = new Set(selected.filter(record => record.siteKind === 'structure').map(record => record.element));
  const footprints = selected.filter(record => record.siteKind === 'building').map(record => ({
    box: record.box,
    polygon: siteElementPolygon(root, record.element),
  }));
  return { buildings, structures, members, footprints, strokeThreshold };
}

function insideSiteBuilding(box, inference) {
  if (!box || !inference || !inference.footprints.length) return false;
  const center = boxCenter(box);
  return inference.footprints.some(footprint => {
    if (!boxInside({ x0: center.x, y0: center.y, x1: center.x, y1: center.y }, footprint.box)) return false;
    return footprint.polygon.length < 3 || sitePointInPolygon(center, footprint.polygon);
  });
}

// PARKING_CLASSIFIER_START
function parkingDistance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function parkingPointInside(point, box, tolerance) {
  return point.x >= box.x0 - tolerance && point.x <= box.x1 + tolerance &&
    point.y >= box.y0 - tolerance && point.y <= box.y1 + tolerance;
}

function parkingLineRecord(line, axisTolerance) {
  if (!line || !line.cid || !line.a || !line.b) return null;
  const length = parkingDistance(line.a, line.b);
  if (!Number.isFinite(length) || length <= 0) return null;
  const dx = line.b.x - line.a.x;
  const dy = line.b.y - line.a.y;
  if (Math.abs(dy) <= axisTolerance) return { ...line, axis: 'horizontal', length,
    x0: Math.min(line.a.x, line.b.x), x1: Math.max(line.a.x, line.b.x), y: (line.a.y + line.b.y) / 2 };
  if (Math.abs(dx) <= axisTolerance) return { ...line, axis: 'vertical', length,
    x: (line.a.x + line.b.x) / 2, y0: Math.min(line.a.y, line.b.y), y1: Math.max(line.a.y, line.b.y) };
  return { ...line, axis: 'diagonal', length };
}

function parkingKey(value, step) {
  return Math.round(value / step);
}

function parkingVerticalConnector(byX, x, y0, y1, expectedLength, tolerance) {
  const key = parkingKey(x, tolerance);
  for (let offset = -1; offset <= 1; offset += 1) {
    for (const line of byX.get(key + offset) || []) {
      if (Math.abs(line.x - x) > tolerance || line.y0 > y0 + tolerance || line.y1 < y1 - tolerance ||
          Math.abs(line.length - expectedLength) > tolerance * 3) continue;
      return line;
    }
  }
  return null;
}

function parkingDiagonals(diagonals, box, tolerance) {
  return diagonals.filter(line => parkingPointInside(line.a, box, tolerance) && parkingPointInside(line.b, box, tolerance))
    .map(line => line.cid);
}

// Finds repeated, short rectangular parking bays from transformed SVG lines.
// A single box never becomes parking: it must have a similarly-sized row neighbor.
function detectParkingBays(lines, { sceneMax = 1000 } = {}) {
  const safeSceneMax = Math.max(1, Number(sceneMax) || 1);
  const axisTolerance = Math.max(0.5, safeSceneMax * 0.00075);
  const connectorTolerance = Math.max(0.75, safeSceneMax * 0.0015);
  const minSpan = Math.max(3, safeSceneMax * 0.003);
  const maxSpan = Math.max(minSpan * 4, safeSceneMax * 0.1);
  const records = lines.map(line => parkingLineRecord(line, axisTolerance)).filter(Boolean);
  const horizontals = records.filter(line => line.axis === 'horizontal' && line.length >= minSpan && line.length <= maxSpan);
  const verticals = records.filter(line => line.axis === 'vertical' && line.length >= minSpan && line.length <= maxSpan);
  const diagonals = records.filter(line => line.axis === 'diagonal' && line.length <= maxSpan * 1.5);
  const verticalByX = new Map();
  for (const line of verticals) {
    const key = parkingKey(line.x, connectorTolerance);
    const bucket = verticalByX.get(key) || [];
    bucket.push(line);
    verticalByX.set(key, bucket);
  }
  const lengthStep = Math.max(axisTolerance * 2, safeSceneMax * 0.002);
  const horizontalBuckets = new Map();
  for (const line of horizontals) {
    const key = parkingKey(line.length, lengthStep);
    const bucket = horizontalBuckets.get(key) || [];
    bucket.push(line);
    horizontalBuckets.set(key, bucket);
  }
  const candidates = new Map();
  for (const [bucketKey, ownLines] of horizontalBuckets) {
    const peers = [...ownLines, ...(horizontalBuckets.get(bucketKey + 1) || [])]
      .sort((a, b) => a.y - b.y || a.x0 - b.x0);
    for (let index = 0; index < peers.length; index += 1) {
      const top = peers[index];
      for (let next = index + 1; next < peers.length; next += 1) {
        const bottom = peers[next];
        const height = bottom.y - top.y;
        if (height > maxSpan) break;
        if (height < minSpan || Math.abs(top.length - bottom.length) > lengthStep ||
            Math.abs(top.x0 - bottom.x0) > connectorTolerance || Math.abs(top.x1 - bottom.x1) > connectorTolerance) continue;
        const left = parkingVerticalConnector(verticalByX, (top.x0 + bottom.x0) / 2, top.y, bottom.y, height, connectorTolerance);
        const right = parkingVerticalConnector(verticalByX, (top.x1 + bottom.x1) / 2, top.y, bottom.y, height, connectorTolerance);
        if (!left || !right) continue;
        const width = (top.length + bottom.length) / 2;
        const aspect = Math.max(width, height) / Math.max(0.1, Math.min(width, height));
        if (aspect > 3.5) continue;
        const cids = [top.cid, bottom.cid, left.cid, right.cid].sort();
        const key = cids.join('|');
        if (candidates.has(key)) continue;
        const box = { x0: Math.min(top.x0, bottom.x0), y0: top.y, x1: Math.max(top.x1, bottom.x1), y1: bottom.y };
        candidates.set(key, { cids, box, width, height,
          center: { x: (box.x0 + box.x1) / 2, y: (box.y0 + box.y1) / 2 },
          diagonalCids: parkingDiagonals(diagonals, box, connectorTolerance), rowNeighbors: 0, similarCount: 0 });
      }
    }
  }
  const boxes = [...candidates.values()];
  const dimensionTolerance = Math.max(connectorTolerance * 2, safeSceneMax * 0.004);
  for (let index = 0; index < boxes.length; index += 1) {
    for (let next = index + 1; next < boxes.length; next += 1) {
      const a = boxes[index], b = boxes[next];
      if (Math.abs(a.width - b.width) > dimensionTolerance || Math.abs(a.height - b.height) > dimensionTolerance) continue;
      a.similarCount += 1;
      b.similarCount += 1;
      const dx = Math.abs(a.center.x - b.center.x), dy = Math.abs(a.center.y - b.center.y);
      const shortSide = Math.min(a.width, a.height), longSide = Math.max(a.width, a.height);
      const alignedX = dx <= Math.max(dimensionTolerance, shortSide * 0.35) && dy >= shortSide * 0.5 && dy <= longSide * 3.5;
      const alignedY = dy <= Math.max(dimensionTolerance, shortSide * 0.35) && dx >= shortSide * 0.5 && dx <= longSide * 3.5;
      if (alignedX || alignedY) { a.rowNeighbors += 1; b.rowNeighbors += 1; }
    }
  }
  const bays = boxes.filter(box => box.rowNeighbors > 0 && (box.similarCount >= 2 || box.diagonalCids.length > 0));
  const lineCids = new Set();
  for (const bay of bays) {
    bay.cids.forEach(cid => lineCids.add(cid));
    bay.diagonalCids.forEach(cid => lineCids.add(cid));
  }
  return { bays, lineCids };
}
// PARKING_CLASSIFIER_END

function parkingLineRecords(root, graphics, focusBox) {
  const records = [];
  for (const element of graphics) {
    if (element.localName !== 'line' || hiddenOrAnnotation(element) || !visibleStroke(element) ||
        numericStyle(element, 'stroke-width', 0) < 0.8) continue;
    const role = roleText(element);
    if (/wall|partition|column|core|ramp|stair|egress|shell|boundary/.test(role)) continue;
    const box = elementBox(root, element);
    if (!box || !boxInside(box, focusBox)) continue;
    const points = elementPoints(root, element, 2);
    if (points.length !== 2) continue;
    records.push({ cid: element.getAttribute('data-cid'), a: points[0], b: points[1], role });
  }
  return records;
}

function textLabels(root) {
  const labels = [];
  for (const element of root.querySelectorAll('text')) {
    const text = (element.textContent || '').replace(/\s+/g, ' ').trim();
    const x = numericAttribute(element, 'x', NaN), y = numericAttribute(element, 'y', NaN);
    if (!text || !Number.isFinite(x) || !Number.isFinite(y)) continue;
    labels.push({ text: text.toLowerCase(), point: localToRoot(root, element, x, y) });
  }
  return labels;
}

function isEvParkingBay(bay, lineByCid, labels, compactSymbols) {
  const roleIsEv = [...bay.cids, ...bay.diagonalCids].some(cid =>
    /\bev\b|electric|charging|charger|전기|충전/.test(lineByCid.get(cid)?.role || ''));
  if (roleIsEv) return true;
  const size = Math.max(bay.width, bay.height);
  const radius = Math.max(12, size * 1.7);
  if (labels.some(label => /\bev\b|electric|charging|charger|전기|충전/.test(label.text) &&
    Math.hypot(label.point.x - bay.center.x, label.point.y - bay.center.y) <= radius)) return true;
  // A compact circular marker inside a bay with a diagonal is the deterministic
  // symbol fallback.  Without both signals the bay remains ordinary parking.
  const shortSide = Math.min(bay.width, bay.height);
  return bay.diagonalCids.length > 0 && compactSymbols.some(symbol =>
    symbol.center.x >= bay.box.x0 && symbol.center.x <= bay.box.x1 &&
    symbol.center.y >= bay.box.y0 && symbol.center.y <= bay.box.y1 &&
    symbol.size >= shortSide * 0.08 && symbol.size <= shortSide * 0.8);
}

function lineSupportsBox(box, lines, orientation) {
  const center = boxCenter(box);
  const tolerance = Math.max(5, Math.min(18, Math.max(boxWidth(box), boxHeight(box)) * 0.6));
  return lines.some(line => {
    const lineCenter = boxCenter(line);
    if (orientation === 'horizontal') {
      return center.x >= line.x0 - tolerance && center.x <= line.x1 + tolerance &&
        Math.abs(lineCenter.y - center.y) <= tolerance;
    }
    return center.y >= line.y0 - tolerance && center.y <= line.y1 + tolerance &&
      Math.abs(lineCenter.x - center.x) <= tolerance;
  });
}

// NATIVE_PLAN_OVERLAY_START
function nativeOverlaySampleIndices(total, maximum) {
  const safeTotal = Math.max(0, Math.round(Number(total) || 0));
  const safeMaximum = Math.max(1, Math.round(Number(maximum) || 1));
  if (safeTotal <= safeMaximum) return Array.from({ length: safeTotal }, (_, index) => index);
  const step = safeTotal / safeMaximum;
  return Array.from({ length: safeMaximum }, (_, index) => Math.min(safeTotal - 1, Math.floor(index * step)));
}
// NATIVE_PLAN_OVERLAY_END

function overlayPoints(root, element) {
  if (element.localName === 'circle' || element.localName === 'ellipse') {
    const box = elementBox(root, element);
    if (!box) return [];
    const points = ovalPlanPoints(box, 16);
    points.push({ ...points[0] });
    return points;
  }
  return elementPoints(root, element, 18);
}

function addPlanOverlay(root, graphics, sceneBox, options, model) {
  state.planOverlayCount = 0;
  state.planOverlayTruncated = false;
  if (!$('threeShowOverlay').checked || !model || state.model !== model) return;
  const maximumElements = 12000;
  const maximumSegments = 30000;
  const prioritized = [];
  const ordinary = [];
  let eligibleCount = 0;
  const ordinaryStride = Math.max(1, Math.floor(graphics.length / 20000));
  for (let index = 0; index < graphics.length; index += 1) {
    const element = graphics[index];
    if (hiddenOrAnnotation(element)) continue;
    const box = elementBox(root, element);
    if (!box || !boxInside(box, sceneBox)) continue;
    eligibleCount += 1;
    const semantic = semanticRecord(element);
    const important = Boolean(semantic.discipline || semantic.role || semantic.symbol || semantic.name ||
      element.hasAttribute('data-height-mm') || numericStyle(element, 'stroke-width', 0) >= 1.5);
    if (important && prioritized.length < 6000) prioritized.push(element);
    else if (index % ordinaryStride === 0 && ordinary.length < 18000) ordinary.push(element);
  }
  const remaining = Math.max(0, maximumElements - prioritized.length);
  const sampledOrdinary = nativeOverlaySampleIndices(ordinary.length, remaining).map(index => ordinary[index]);
  const candidates = [...prioritized, ...sampledOrdinary];
  state.planOverlayTruncated = eligibleCount > candidates.length;
  const buckets = new Map([
    ['plan', []], ['building', []], ['landscape', []], ['civil', []], ['terrain', []],
  ]);
  let segmentCount = 0;
  for (const element of candidates) {
    const points = overlayPoints(root, element);
    if (points.length < 2) continue;
    const channel = classifyMultiDiscipline(semanticRecord(element, {
      closed: isClosedElement(element, points),
    })) || 'plan';
    const target = buckets.get(channel) || buckets.get('plan');
    for (let index = 1; index < points.length && segmentCount < maximumSegments; index += 1) {
      target.push(...planWorldPoints([points[index - 1], points[index]], options, 0.082));
      segmentCount += 1;
    }
    if (segmentCount >= maximumSegments) {
      state.planOverlayTruncated = true;
      break;
    }
  }
  const styles = {
    plan: ['#c4d7e6', 0.62],
    building: ['#eef4f8', 0.78],
    landscape: ['#b5dfa4', 0.7],
    civil: ['#efbd7f', 0.72],
    terrain: ['#cbb08c', 0.66],
  };
  for (const [channel, vertices] of buckets) {
    if (!vertices.length) continue;
    const [color, opacity] = styles[channel];
    const geometry = new THREE.BufferGeometry().setFromPoints(vertices);
    const material = new THREE.LineBasicMaterial({
      color, transparent: true, opacity, depthWrite: false, depthTest: true,
    });
    const overlay = new THREE.LineSegments(geometry, material);
    overlay.renderOrder = 2;
    overlay.userData = {
      role: 'native-vector-plan-overlay',
      channel,
      derivedFromSvg: true,
      rasterOverlay: false,
    };
    model.add(overlay);
  }
  state.planOverlayCount = segmentCount;
}

function rebuild() {
  init();
  const root = app.getSvgRoot();
  if (!root) {
    $('threeStatus').textContent = 'SVG 로드 대기 중…';
    clearTimeout(state.loadRetry);
    if (!$('threePane').classList.contains('hidden')) state.loadRetry = setTimeout(rebuild, 300);
    return;
  }
  clearTimeout(state.loadRetry);
  $('threeStatus').textContent = 'SVG에서 건축 · 조경 · 토목 · 지형과 실내 요소 인식 중…';
  disposeModel();
  state.model = new THREE.Group();
  state.model.name = 'svg-derived-review-model';
  state.scene.add(state.model);
  state.segmentCount = 0;
  state.objectCount = 0;
  state.wallCount = 0;
  state.heightOverrideCount = 0;
  state.columnCount = 0;
  state.furnitureCount = 0;
  state.parkingCount = 0;
  state.evParkingCount = 0;
  state.parkingLineCount = 0;
  state.buildingCount = 0;
  state.landscapeCount = 0;
  state.civilCount = 0;
  state.terrainCount = 0;
  state.terrainFlatCount = 0;
  state.representationCounts = new Map();
  state.planOverlayCount = 0;
  state.planOverlayTruncated = false;
  state.objectSelectTotal = 0;
  state.objectSelectShown = 0;
  state.cappedChannels = new Set();

  const vb = root.viewBox && root.viewBox.baseVal;
  const viewBox = vb && vb.width > 0 ? { x: vb.x, y: vb.y, width: vb.width, height: vb.height } :
    { x: 0, y: 0, width: Number(root.getAttribute('width') || 1000), height: Number(root.getAttribute('height') || 1000) };
  const graphics = Array.from(root.querySelectorAll('line,polyline,polygon,path,rect,circle,ellipse'));
  graphics.sort((a, b) => Number(b.hasAttribute('data-height-mm')) - Number(a.hasAttribute('data-height-mm')));
  const semanticNodes = root.querySelectorAll(
    '[data-role],[data-discipline],[data-symbol],[data-name],[data-crab-program-role],[data-crab-solver-projected-role]');
  const semantic = Array.from(semanticNodes).some(element => {
    const record = semanticRecord(element);
    const text = [roleText(element), record.discipline, record.role, record.symbol, record.name]
      .filter(Boolean).join(' ').toLowerCase();
    return /wall|partition|column|glazing|shell|boundary|furniture|furnishing|chair|table|sofa|equipment|building|structure|structural|foundation|retaining|exterior|roof|facade|landscape|civil|terrain/.test(text);
  });
  const siteDominant = !semantic && detectSiteDominantDrawing(graphics);
  state.recognitionMode = semantic ? 'hybrid_semantic_and_inference' :
    siteDominant ? 'flattened_site_dominant_inference' : 'flattened_svg_inference';
  const focusBox = siteDominant ? deriveSiteFocusBox(root, graphics, viewBox) :
    deriveFocusBox(root, graphics, viewBox);
  state.focusBox = { x0: focusBox.x0, y0: focusBox.y0, x1: focusBox.x1, y1: focusBox.y1 };
  const sceneWidth = Math.max(1, focusBox.x1 - focusBox.x0);
  const sceneHeight = Math.max(1, focusBox.y1 - focusBox.y0);
  const sceneMax = Math.max(sceneWidth, sceneHeight);
  const centerX = (focusBox.x0 + focusBox.x1) / 2;
  const centerY = (focusBox.y0 + focusBox.y1) / 2;
  const mmPerUnit = Number(root.getAttribute('data-crab-mm-per-unit'));
  const unitsToMeters = Number.isFinite(mmPerUnit) && mmPerUnit > 0 ? mmPerUnit / 1000 : 45 / sceneMax;
  state.unitsToMeters = unitsToMeters;
  state.scaleMode = Number.isFinite(mmPerUnit) && mmPerUnit > 0 ?
    'svg_confirmed_mm_per_unit' : 'preview_normalized_focus_to_45m';
  const wallHeight = numberValue('threeWallHeight', 2700) / 1000;
  const wallThickness = Math.max(0.02, numberValue('threeWallThickness', 150) / 1000);
  const slabThickness = numberValue('threeSlab', 150) / 1000;
  const furnitureHeight = numberValue('threeFurnitureHeight', 750) / 1000;
  const baseOptions = {
    centerX, centerY, unitsToMeters, height: wallHeight, thickness: wallThickness,
    furnitureHeight, maxSegments: 4200, seenSegments: new Set(),
  };

  if (slabThickness > 0) {
    const floor = new THREE.Mesh(
      new THREE.BoxGeometry(sceneWidth * unitsToMeters, slabThickness, sceneHeight * unitsToMeters),
      makeMaterial('floor'),
    );
    floor.position.y = -slabThickness / 2;
    floor.receiveShadow = true;
    floor.userData = { cid: null, role: 'floor', derivedFromSvg: true };
    state.model.add(floor);
    state.allMeshes.push(floor);
  }

  const shapeCounts = shapeFrequency(root, graphics, focusBox, sceneMax);
  const siteStructures = siteDominant ? inferSiteStructures(root, graphics, focusBox, sceneMax) : {
    buildings: new Set(), structures: new Set(), members: new Set(), footprints: [], strokeThreshold: Infinity,
  };
  const labels = labelCenters(root);
  const parkingRecords = parkingLineRecords(root, graphics, focusBox);
  const parkingRecognition = detectParkingBays(parkingRecords, { sceneMax });
  const parkingLineByCid = new Map(parkingRecords.map(record => [record.cid, record]));
  const compactSymbols = graphics.filter(element => ['circle', 'ellipse'].includes(element.localName))
    .map(element => elementBox(root, element)).filter(Boolean).map(box => ({
      center: boxCenter(box), size: Math.max(boxWidth(box), boxHeight(box)),
    }));
  const semanticLabels = textLabels(root);
  const parkingKinds = new Map();
  for (const bay of parkingRecognition.bays) {
    const kind = isEvParkingBay(bay, parkingLineByCid, semanticLabels, compactSymbols) ? 'ev-parking' : 'parking';
    if (kind === 'ev-parking') state.evParkingCount += 1;
    for (const parkingCid of [...bay.cids, ...bay.diagonalCids]) {
      if (kind === 'ev-parking' || !parkingKinds.has(parkingCid)) parkingKinds.set(parkingCid, kind);
    }
  }
  state.parkingCount = parkingRecognition.bays.length;
  const wallMinLength = Math.max(3, sceneMax * 0.006);
  const furnitureMinSize = Math.max(5, sceneMax * 0.005);
  const furnitureMaxSize = sceneMax * 0.04;
  const columnMinSize = Math.max(5, sceneMax * 0.0035);
  const columnMaxSize = sceneMax * 0.025;
  const horizontalSupports = [];
  const verticalSupports = [];

  for (const element of graphics) {
    if (element.localName !== 'line' || numericStyle(element, 'stroke-width', 0) < 1.5 ||
        !visibleStroke(element) || hiddenOrAnnotation(element)) continue;
    const box = elementBox(root, element);
    if (!box || !boxInside(box, focusBox)) continue;
    const length = Math.hypot(boxWidth(box), boxHeight(box));
    if (length < wallMinLength) continue;
    if (boxHeight(box) <= Math.max(2, boxWidth(box) * 0.05)) horizontalSupports.push(box);
    if (boxWidth(box) <= Math.max(2, boxHeight(box) * 0.05)) verticalSupports.push(box);
  }

  for (const element of graphics) {
    if (hiddenOrAnnotation(element)) continue;
    const role = roleText(element);
    const cid = element.getAttribute('data-cid');
    const tag = element.localName;
    const box = elementBox(root, element);
    if (!box || !boxInside(box, focusBox)) continue;
    const width = boxWidth(box), height = boxHeight(box);
    const size = Math.max(width, height);
    const aspect = size / Math.max(0.1, Math.min(width, height));
    const strokeWidth = numericStyle(element, 'stroke-width', 0);
    const repeated = shapeCounts.get(repeatedShapeKey(element, box, sceneMax)) || 0;
    const rawClosedPoints = tag === 'polyline' || tag === 'polygon' ? rawPointList(element) : null;
    const closed = isClosedElement(element, rawClosedPoints);
    const siteBuilding = siteStructures.buildings.has(element);
    const siteStructure = siteStructures.structures.has(element);
    const siteStructureContext = siteStructures.members.has(element) || insideSiteBuilding(box, siteStructures);
    const disciplineRecord = semanticRecord(element, {
      repeated, closed, siteDominant, siteBuilding, siteStructure,
      aspect, sizeRatio: size / sceneMax,
    });
    const channel = classifyMultiDiscipline(disciplineRecord);
    if (channel) {
      try {
        const representation = classifyDisciplineRepresentation({ ...disciplineRecord, channel });
        const result = addDisciplineElement(state.model, root, element, channel, baseOptions, representation);
        if (result.created) {
          state[`${channel}Count`] += 1;
          state.representationCounts.set(result.subtype,
            (state.representationCounts.get(result.subtype) || 0) + 1);
          if (result.flatTerrain) state.terrainFlatCount += 1;
        }
      } catch (_) {}
      continue;
    }
    const parkingKind = parkingKinds.get(cid);
    if (parkingKind) {
      const points = elementPoints(root, element, 2);
      if (points.length === 2 && addParkingMarking(state.model, points[0], points[1], {
        centerX, centerY, unitsToMeters, cid, kind: parkingKind,
        height: Math.max(0.006, wallThickness * 0.08),
        thickness: Math.max(0.018, wallThickness * 0.18),
        seenSegments: baseOptions.parkingSeenSegments || (baseOptions.parkingSeenSegments = new Set()),
      })) state.parkingLineCount += 1;
      continue;
    }
    let isColumn = role.includes('column');
    let isFurniture = /furniture|furnishing|chair|table|sofa|equipment/.test(role);
    const isGlass = role.includes('glazing') || role.includes('glass');
    let isWall = /wall|partition|shell|boundary/.test(role) || isGlass;
    const hasSemanticClassification = isColumn || isFurniture || isWall;

    if (!hasSemanticClassification) {
      const outlined = visibleStroke(element) && ['none', 'transparent', ''].includes(
        styleValue(element, 'fill', 'none').toLowerCase());
      const compactShape = ['polygon', 'polyline', 'rect', 'circle', 'ellipse'].includes(tag);
      const columnShapeCandidate = outlined && compactShape && repeated >= 2 &&
        size >= columnMinSize && size <= columnMaxSize && aspect <= 2.75;
      const hasBothStructuralAxes = columnShapeCandidate &&
        lineSupportsBox(box, horizontalSupports, 'horizontal') &&
        lineSupportsBox(box, verticalSupports, 'vertical');
      isColumn = columnShapeCandidate && hasBothStructuralAxes;

      if (strokeWidth >= 1.5 && visibleStroke(element)) {
        if (tag === 'line') isWall = size >= wallMinLength;
        else if (tag === 'path' || tag === 'polyline') {
          const rawPoints = tag === 'polyline' ? rawPointList(element) : null;
          isWall = size >= wallMinLength * 1.5 && !isClosedElement(element, rawPoints);
        } else if ((tag === 'polygon' || tag === 'rect') && !isColumn) {
          isWall = size >= wallMinLength * 1.6 && aspect >= 5;
        }
      }

      isFurniture = !isColumn && !isWall && outlined && compactShape && repeated >= 2 &&
        size >= furnitureMinSize && size <= furnitureMaxSize && aspect <= 6 &&
        !hasCenteredLabel(box, labels) && allowInferredFurniture({ siteDominant, siteStructureContext });
    }

    if (isColumn) {
      try {
        const points = ['polygon', 'polyline', 'rect'].includes(tag) ? elementPoints(root, element, 32) : null;
        if (addColumn(state.model, root, element, baseOptions, points)) state.columnCount += 1;
      } catch (_) {}
      continue;
    }
    if (isFurniture) {
      if (state.furnitureCount >= 1400) continue;
      try {
        const points = ['polygon', 'polyline', 'rect'].includes(tag) ? elementPoints(root, element, 32) : null;
        if (addFurniture(state.model, root, element, baseOptions, points)) state.furnitureCount += 1;
      } catch (_) {}
      continue;
    }
    if (!isWall ||
        !['line', 'polyline', 'polygon', 'path', 'rect'].includes(tag)) continue;
    const points = elementPoints(root, element);
    if (points.length < 2) continue;
    const before = state.segmentCount;
    const explicitHeightMm = Number(element.getAttribute('data-height-mm'));
    const hasHeightOverride = Number.isFinite(explicitHeightMm) && explicitHeightMm > 0;
    const options = {
      ...baseOptions,
      cid,
      kind: isGlass ? 'glass' : 'wall',
      height: elementHeightMeters(element, baseOptions.height),
    };
    for (let index = 1; index < points.length; index += 1) {
      addWallSegment(state.model, points[index - 1], points[index], options);
    }
    if (state.segmentCount > before) {
      state.wallCount += 1;
      if (hasHeightOverride) state.heightOverrideCount += 1;
    }
  }

  state.sourceRevision = app.getRevision();
  state.objectCount = state.wallCount + state.columnCount + state.furnitureCount + state.parkingCount +
    state.buildingCount + state.landscapeCount + state.civilCount + state.terrainCount;
  reapplyReviewHeightOverrides();
  const currentModel = state.model;
  addPlanOverlay(root, graphics, focusBox, baseOptions, currentModel);
  syncCategoryVisibility();
  refreshObjectSelect();
  fitCamera('perspective');
  syncSelectionFromCanvas();
  const modeLabel = semantic ? '역할 태그 + 무태그 추론' :
    siteDominant ? '평탄화 대지 다분야 추론' : '평탄화 추론';
  const capLabel = state.cappedChannels.size ? ` · 상한 ${[...state.cappedChannels].map(channel => `${channel} ${channelCaps[channel]}`).join(', ')}` : '';
  const scaleLabel = state.scaleMode === 'svg_confirmed_mm_per_unit' ?
    '실척 스케일' : '⚠ 스케일 미확정(45m 보기 정규화)';
  const representationLabelText = [...state.representationCounts.entries()]
    .sort((a, b) => b[1] - a[1]).slice(0, 5)
    .map(([subtype, count]) => `${representationLabel(subtype, subtype)} ${count}`).join(' · ');
  const overlayLabel = `도면패턴 ${state.planOverlayCount}${state.planOverlayTruncated ? '+' : ''}`;
  $('threeStatus').textContent = `rev ${state.sourceRevision} · 건축 ${state.buildingCount} · 조경 ${state.landscapeCount} · 토목 ${state.civilCount} · 지형 ${state.terrainCount}(평탄 ${state.terrainFlatCount}) · 벽 ${state.wallCount} · 기둥 ${state.columnCount} · 가구 ${state.furnitureCount} · 주차 ${state.parkingCount}(EV ${state.evParkingCount}) · SVG높이 ${state.heightOverrideCount} · 뷰어높이 ${state.reviewHeightOverrides.size} · ${overlayLabel} · ${scaleLabel} · ${modeLabel}${representationLabelText ? ` · ${representationLabelText}` : ''}${capLabel}`;
  app.log(`3D 정합 완료: 건축 ${state.buildingCount} · 조경 ${state.landscapeCount} · 토목 ${state.civilCount} · 지형 ${state.terrainCount}(평탄 ${state.terrainFlatCount}) · 벽 ${state.wallCount}개/${state.segmentCount} 세그먼트 · ` +
    `기둥 ${state.columnCount}개 · 가구 ${state.furnitureCount}개 · 주차 ${state.parkingCount}개(EV ${state.evParkingCount}) · ${modeLabel}${capLabel}` +
    (state.segmentCount >= baseOptions.maxSegments ? ' (벽 안전 상한 적용)' : ''));
}

function modelBounds() {
  if (!state.model) return null;
  const box = new THREE.Box3().setFromObject(state.model);
  return box.isEmpty() ? null : box;
}

function fitCamera(mode = 'perspective') {
  const box = modelBounds();
  if (!box) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const distance = Math.max(size.x, size.z, size.y * 2, 3);
  const siteContext = state.recognitionMode === 'flattened_site_dominant_inference';
  if (mode === 'top') state.camera.position.set(center.x, center.y + distance * 1.55, center.z + 0.001);
  else if (mode === 'eye') state.camera.position.set(center.x - distance * 0.9, 1.65, center.z + distance * 0.65);
  else if (siteContext) state.camera.position.set(
    center.x + distance * 0.7,
    center.y + distance * 0.58,
    center.z + distance * 0.7,
  );
  else state.camera.position.set(center.x + distance * 0.9, center.y + distance * 0.95, center.z + distance * 0.9);
  state.controls.target.copy(mode === 'eye' ? new THREE.Vector3(center.x, 1.25, center.z) : center);
  state.camera.near = Math.max(0.01, distance / 1000);
  state.camera.far = Math.max(200, distance * 20);
  state.camera.updateProjectionMatrix();
  state.controls.update();
}

function clearSelectionHighlight() {
  const meshes = state.selectedObjectKey ? meshesForObjectKey(state.selectedObjectKey) : [state.selected];
  for (const mesh of meshes) {
    if (!mesh?.material?.emissive) continue;
    mesh.material.emissive.set(mesh.material.userData.baseEmissive || '#000000');
    mesh.material.emissiveIntensity = 1;
  }
}

function objectKeyForMesh(mesh) {
  return mesh?.userData?.objectKey || null;
}

function meshesForObjectKey(objectKey) {
  if (!objectKey) return [];
  return state.allMeshes.filter(mesh => objectKeyForMesh(mesh) === objectKey);
}

function resetMeshReviewHeight(mesh) {
  const axis = mesh?.userData?.heightAxis;
  if (!axis || !mesh.userData.baseHeightMm) return;
  mesh.scale[axis] = Number(mesh.userData.baseAxisScale) || 1;
  if (axis === 'y') mesh.position.y = mesh.userData.basePositionY;
}

function applyMeshReviewHeight(mesh, targetHeightMm) {
  const axis = mesh?.userData?.heightAxis;
  const baseHeightMm = Number(mesh?.userData?.baseHeightMm);
  if (!axis || !Number.isFinite(baseHeightMm) || baseHeightMm <= 0) return;
  const factor = heightScaleFactor(baseHeightMm, targetHeightMm);
  mesh.scale[axis] = (Number(mesh.userData.baseAxisScale) || 1) * factor;
  if (axis === 'y') {
    const baseY = Number(mesh.userData.basePositionY) || 0;
    if (mesh.userData.heightTransform === 'ground-scale') {
      const groundY = Number(mesh.userData.groundY) || 0;
      mesh.position.y = groundY + (baseY - groundY) * factor;
    } else {
      mesh.position.y = baseY - baseHeightMm / 2000 +
        normalizeReviewHeightMm(targetHeightMm, baseHeightMm) / 2000;
    }
  }
}

function selectedObjectMeshes() {
  return meshesForObjectKey(state.selectedObjectKey);
}

function selectedObjectRecord() {
  const meshes = selectedObjectMeshes();
  if (!meshes.length) return null;
  const first = meshes[0];
  const baseHeightMm = normalizeReviewHeightMm(first.userData.baseHeightMm, 2700);
  const override = state.reviewHeightOverrides.get(state.selectedObjectKey);
  const heightEditable = meshes.some(mesh => mesh.userData.heightEditable);
  const floorEditable = meshes.some(mesh => mesh.userData.floorEditable);
  const defaultFloorHeightMm = Number(first.userData.floorHeightMm) || numberValue('threeWallHeight', 2700);
  const defaultFloors = floorEditable ? Math.max(1, Number(first.userData.baseFloorCount) || 1) :
    Math.max(1, Math.round((override?.heightMm || baseHeightMm) / defaultFloorHeightMm));
  return {
    key: state.selectedObjectKey,
    meshes,
    cid: first.userData.cid || '',
    role: first.userData.role || 'object',
    subtype: first.userData.subtype || first.userData.role || 'object',
    confidence: first.userData.confidence || 'derived',
    heightSource: first.userData.heightSource || 'derived',
    heightEditable,
    floorEditable,
    baseHeightMm,
    heightMm: override?.heightMm || baseHeightMm,
    floors: override?.floors || defaultFloors,
    floorHeightMm: override?.floorHeightMm || defaultFloorHeightMm,
  };
}

function selectedObjectBounds(record = selectedObjectRecord()) {
  if (!record?.meshes?.length) return null;
  const bounds = new THREE.Box3();
  let found = false;
  for (const mesh of record.meshes) {
    if (!mesh?.geometry) continue;
    if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
    if (!mesh.geometry.boundingBox) continue;
    mesh.updateMatrixWorld(true);
    bounds.union(mesh.geometry.boundingBox.clone().applyMatrix4(mesh.matrixWorld));
    found = true;
  }
  return found && !bounds.isEmpty() ? bounds : null;
}

function focusSelectedObject(record = selectedObjectRecord()) {
  if (!state.camera || !state.controls) return false;
  const bounds = selectedObjectBounds(record);
  if (!bounds) return false;
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const direction = state.camera.position.clone().sub(state.controls.target);
  if (direction.lengthSq() < 0.0001) direction.set(1, 0.8, 1);
  direction.normalize();
  const distance = perspectiveFitDistance(
    Math.max(size.x, size.z),
    size.y,
    Math.min(size.x, size.z),
    state.camera.fov,
    state.camera.aspect,
    1.55,
  );
  state.camera.position.copy(center).addScaledVector(direction, distance);
  state.controls.target.copy(center);
  state.camera.near = Math.max(0.01, distance / 1000);
  state.camera.far = Math.max(200, distance * 20);
  state.camera.updateProjectionMatrix();
  state.controls.update();
  return true;
}

function setHeightApplyState(message, applied = false) {
  const status = $('threeHeightApplyState');
  if (!status) return;
  status.textContent = message;
  status.classList.toggle('applied', applied);
}

function lineForPlanPoints(points, closed, y) {
  if (!points || points.length < 2) return null;
  const vertices = points.map(point => new THREE.Vector3(point.x, y, point.z));
  const geometry = new THREE.BufferGeometry().setFromPoints(vertices);
  const material = new THREE.LineBasicMaterial({ color: '#38bdf8', transparent: true, opacity: 0.92, depthTest: false });
  const line = closed ? new THREE.LineLoop(geometry, material) : new THREE.Line(geometry, material);
  line.renderOrder = 4;
  return line;
}

function fallbackPlanPoints(mesh) {
  const box = new THREE.Box3().setFromObject(mesh);
  if (box.isEmpty()) return [];
  return [
    new THREE.Vector3(box.min.x, 0, box.min.z), new THREE.Vector3(box.max.x, 0, box.min.z),
    new THREE.Vector3(box.max.x, 0, box.max.z), new THREE.Vector3(box.min.x, 0, box.max.z),
  ];
}

function refreshSelectionFootprint() {
  clearSelectionFootprint();
  const record = selectedObjectRecord();
  if (!record || !state.model) return;
  const group = new THREE.Group();
  group.name = 'selected-object-plan-footprint';
  const override = state.reviewHeightOverrides.get(record.key);
  const levels = record.floorEditable && override?.mode === 'floors' ?
    floorGuideLevels(override.floors, 40) : [0];
  const floorHeightM = (override?.floorHeightMm || record.floorHeightMm) / 1000;
  const seenPlans = new Set();
  let planCount = 0;
  for (const mesh of record.meshes) {
    const points = mesh.userData.planPoints?.length ? mesh.userData.planPoints : fallbackPlanPoints(mesh);
    const closed = mesh.userData.planPoints?.length ? Boolean(mesh.userData.planClosed) : true;
    const signature = points.map(point => `${point.x.toFixed(3)}:${point.z.toFixed(3)}`).join('|');
    if (!signature || seenPlans.has(signature) || planCount >= 48) continue;
    seenPlans.add(signature);
    planCount += 1;
    for (const level of levels) {
      const y = 0.035 + level * floorHeightM;
      const line = lineForPlanPoints(points, closed, y);
      if (line) group.add(line);
    }
  }
  const bounds = selectedObjectBounds(record);
  if (bounds) {
    const helper = new THREE.Box3Helper(bounds, '#38bdf8');
    helper.name = 'selected-object-height-bounds';
    helper.material.depthTest = false;
    helper.material.transparent = true;
    helper.material.opacity = 0.95;
    helper.renderOrder = 6;
    group.add(helper);
  }
  group.visible = $('threeShowSelectionPlan').checked;
  state.model.add(group);
  state.selectionFootprint = group;
}

function syncSelectionHeightControls() {
  const record = selectedObjectRecord();
  const floorControls = ['threeSelectedFloors', 'threeSelectedFloorHeight', 'btn3dApplyFloors'];
  const heightControls = ['threeSelectedHeight', 'btn3dApplyHeight'];
  for (const id of floorControls) $(id).disabled = !record?.floorEditable;
  for (const id of heightControls) $(id).disabled = !record?.heightEditable;
  $('btn3dResetHeight').disabled = !record?.heightEditable ||
    !state.reviewHeightOverrides.has(record?.key);
  if (!record) {
    $('threeObjectSelect').value = '';
    $('threeSelection').textContent = '선택: 전체 벽';
    const listNote = state.objectSelectTotal > state.objectSelectShown ?
      ` · 목록 ${state.objectSelectShown}/${state.objectSelectTotal}, 나머지는 2D·3D에서 선택` : '';
    $('threeHeightSelectionHint').textContent = `2D·3D·목록에서 높이 객체 선택${listNote}`;
    setHeightApplyState('객체 선택 후 숫자 변경 시 자동 적용');
    return;
  }
  $('threeSelectedFloors').value = String(record.floors);
  $('threeSelectedFloorHeight').value = String(record.floorHeightMm);
  $('threeSelectedHeight').value = String(record.heightMm);
  $('threeObjectSelect').value = record.key;
  const typeLabel = representationLabel(record.subtype, record.role);
  const label = record.cid ? `${typeLabel} ${record.cid}` : typeLabel;
  const editLabel = record.floorEditable ? '층수·높이 편집' :
    record.heightEditable ? '높이 편집' : '2D 기준면(높이 편집 안 함)';
  const sourceLabel = record.heightSource === 'footprint-inferred' ? '발자국 기반 가높이' :
    record.heightSource === 'geometry-inferred' ? '형상 기반 가높이' :
    record.heightSource.startsWith('svg-') ? 'SVG 근거 높이' : '뷰어 기본 높이';
  $('threeSelection').textContent = `선택: ${label} · ${editLabel}${record.heightEditable ? ` · ${record.heightMm.toLocaleString()}mm` : ''}`;
  $('threeHeightSelectionHint').textContent =
    `${label} · ${record.confidence === 'explicit' ? '명시 분류' : '추론 분류'} · ${sourceLabel} · ${editLabel}`;
  const override = state.reviewHeightOverrides.get(record.key);
  setHeightApplyState(override ?
    `적용됨 · ${record.heightMm.toLocaleString()}mm · 선택 객체로 화면 이동` :
    '숫자 변경 시 자동 적용 · Enter 가능', Boolean(override));
}

function syncReviewOverrideCount() {
  const status = $('threeStatus');
  status.textContent = status.textContent.replace(/뷰어높이 \d+/, `뷰어높이 ${state.reviewHeightOverrides.size}`);
}

function applyReviewHeight(objectKey, override, note) {
  const meshes = meshesForObjectKey(objectKey).filter(mesh => mesh.userData.heightEditable);
  if (!meshes.length) return false;
  const heightMm = normalizeReviewHeightMm(override.heightMm, meshes[0].userData.baseHeightMm);
  const previous = state.reviewHeightOverrides.get(objectKey);
  const changed = !previous || previous.mode !== override.mode || previous.heightMm !== heightMm ||
    previous.floors !== override.floors || previous.floorHeightMm !== override.floorHeightMm;
  state.reviewHeightOverrides.set(objectKey, { ...override, heightMm });
  for (const mesh of meshes) {
    applyMeshReviewHeight(mesh, heightMm);
    if (mesh.userData.subtype === 'building-mass') {
      setBuildingStoreyBands(mesh, override.mode === 'floors' ?
        override.floors : mesh.userData.baseFloorCount);
    }
    mesh.updateMatrixWorld(true);
  }
  state.model?.updateMatrixWorld(true);
  syncReviewOverrideCount();
  refreshSelectionFootprint();
  syncSelectionHeightControls();
  focusSelectedObject();
  if (changed) {
    app.log(`3D ${note}: ${meshes[0].userData.role || 'object'} ${meshes[0].userData.cid || ''} → ${heightMm}mm`);
  }
  return true;
}

function applySelectedFloors() {
  const record = selectedObjectRecord();
  if (!record?.floorEditable) return;
  const target = floorHeightTargetMm($('threeSelectedFloors').value, $('threeSelectedFloorHeight').value);
  applyReviewHeight(record.key, { ...target, mode: 'floors' }, `${target.floors}층 × ${target.floorHeightMm}mm`);
}

function applySelectedHeight() {
  const record = selectedObjectRecord();
  if (!record?.heightEditable) return;
  applyReviewHeight(record.key, {
    heightMm: normalizeReviewHeightMm($('threeSelectedHeight').value, record.heightMm),
    floors: null,
    floorHeightMm: null,
    mode: 'direct',
  }, '직접 높이');
}

function resetSelectedHeight() {
  const record = selectedObjectRecord();
  if (!record?.heightEditable) return;
  state.reviewHeightOverrides.delete(record.key);
  for (const mesh of record.meshes) {
    resetMeshReviewHeight(mesh);
    if (mesh.userData.subtype === 'building-mass') {
      setBuildingStoreyBands(mesh, mesh.userData.baseFloorCount);
    }
  }
  syncReviewOverrideCount();
  refreshSelectionFootprint();
  syncSelectionHeightControls();
  focusSelectedObject();
  app.log(`3D 높이 초기화: ${record.role} ${record.cid || ''}`);
}

function reapplyReviewHeightOverrides() {
  for (const [objectKey, override] of state.reviewHeightOverrides) {
    const meshes = meshesForObjectKey(objectKey).filter(mesh => mesh.userData.heightEditable);
    if (!meshes.length) continue;
    for (const mesh of meshes) {
      applyMeshReviewHeight(mesh, override.heightMm);
      if (mesh.userData.subtype === 'building-mass') {
        setBuildingStoreyBands(mesh, override.mode === 'floors' ?
          override.floors : mesh.userData.baseFloorCount);
      }
    }
  }
}

function interactionMode() {
  return app.getInteractionMode ? app.getInteractionMode() : { pan: false };
}

function updateInteractionMode() {
  if (!state.controls) return;
  const pan = !!interactionMode().pan;
  state.controls.mouseButtons.LEFT = pan ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
  $('threeCanvas').classList.toggle('pan-mode', pan);
}

// SELECTION_BRIDGE_START
function firstMeshForSelectedCids(cids, meshes) {
  const wanted = new Set(Array.isArray(cids) ? cids.filter(Boolean).map(String) : []);
  if (!wanted.size) return null;
  return meshes.find(mesh => wanted.has(String(mesh?.userData?.cid || ''))) || null;
}
// SELECTION_BRIDGE_END

function setSelectedMesh(mesh, mirrorToCanvas = false) {
  clearSelectionHighlight();
  state.selected = mesh || null;
  state.selectedObjectKey = objectKeyForMesh(state.selected);
  if (state.selected && state.selectedObjectKey) {
    for (const target of meshesForObjectKey(state.selectedObjectKey)) {
      if (!target.material?.emissive) continue;
      target.material.emissive.set('#164e63');
      target.material.emissiveIntensity = 0.75;
    }
    const cid = state.selected.userData.cid;
    if (mirrorToCanvas && cid) app.selectCid(cid);
  }
  refreshSelectionFootprint();
  syncSelectionHeightControls();
  return Boolean(state.selectedObjectKey);
}

function refreshObjectSelect() {
  const select = $('threeObjectSelect');
  const previous = state.selectedObjectKey || select.value;
  const records = new Map();
  for (const mesh of state.allMeshes) {
    const key = objectKeyForMesh(mesh);
    if (!key || records.has(key) || !mesh.userData.heightEditable) continue;
    records.set(key, {
      key,
      cid: String(mesh.userData.cid || ''),
      role: String(mesh.userData.role || 'object'),
      subtype: String(mesh.userData.subtype || mesh.userData.role || 'object'),
      floorEditable: Boolean(mesh.userData.floorEditable),
    });
  }
  const roleOrder = new Map([
    ['building-mass', 0], ['structure-linear', 1], ['wall', 2], ['glass', 3],
    ['column', 4], ['tree', 5], ['shrub', 6], ['hedge', 7], ['furniture', 8],
  ]);
  const options = [new Option('3D 객체 선택…', '')];
  const sorted = [...records.values()].sort((a, b) =>
    (roleOrder.get(a.subtype) ?? 99) - (roleOrder.get(b.subtype) ?? 99) ||
    a.cid.localeCompare(b.cid, undefined, { numeric: true }));
  const maximumOptions = 500;
  let shown = sorted.slice(0, maximumOptions);
  const selectedRecord = records.get(previous);
  if (selectedRecord && !shown.some(record => record.key === previous)) {
    shown = [...shown.slice(0, Math.max(0, maximumOptions - 1)), selectedRecord];
  }
  for (const record of shown) {
    const typeLabel = representationLabel(record.subtype, record.role);
    const capabilityLabel = record.floorEditable ? '층수+높이' : '높이';
    options.push(new Option(`${typeLabel} · ${record.cid || record.key} · ${capabilityLabel}`, record.key));
  }
  if (records.size > shown.length) {
    const note = new Option(`그 외 ${records.size - shown.length}개 — 2D·3D에서 선택`, '');
    note.disabled = true;
    options.push(note);
  }
  select.replaceChildren(...options);
  if (records.has(previous)) select.value = previous;
  state.objectSelectTotal = records.size;
  state.objectSelectShown = shown.length;
}

function selectObjectKey(objectKey, mirrorToCanvas = true) {
  const mesh = meshesForObjectKey(objectKey)[0] || null;
  return setSelectedMesh(mesh, mirrorToCanvas);
}

function syncSelectionFromCanvas(cids = app.getSelectedCids()) {
  if (!state.model) return false;
  const selectedCids = Array.isArray(cids) ? cids : [];
  const matched = firstMeshForSelectedCids(selectedCids, state.allMeshes);
  const didSelect = setSelectedMesh(matched, false);
  if (!didSelect && selectedCids.length) {
    $('threeSelection').textContent = `선택: ${selectedCids[0]} · 3D 높이 대상 아님`;
    $('threeHeightSelectionHint').textContent = '선택 요소가 현재 3D 모델에 없음';
  }
  return didSelect;
}

function selectObject(event) {
  if (!state.model || event.button !== 0 || interactionMode().pan) return;
  const rect = $('threeCanvas').getBoundingClientRect();
  const pointer = new THREE.Vector2(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  );
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(pointer, state.camera);
  const hit = raycaster.intersectObjects(state.allMeshes, true)[0];
  setSelectedMesh(hit ? hit.object : null, true);
}

function applyFinish() {
  const name = $('threeFinish').value;
  const preset = { ...(finishPresets[name] || finishPresets.custom) };
  if (name === 'custom') preset.color = $('threeColor').value;
  else $('threeColor').value = preset.color;
  const targets = state.selectedObjectKey ? meshesForObjectKey(state.selectedObjectKey) : state.wallMeshes;
  clearSelectionHighlight();
  for (const mesh of targets) {
    mesh.traverse(object => {
      const materials = object.material ? (Array.isArray(object.material) ? object.material : [object.material]) : [];
      for (const material of materials) {
        if (material.color) material.color.set(preset.color);
        if ('roughness' in material) material.roughness = preset.roughness;
        if ('metalness' in material) material.metalness = preset.metalness;
        material.transparent = preset.opacity < 1;
        material.opacity = preset.opacity;
        material.depthWrite = preset.opacity >= 0.9;
        material.needsUpdate = true;
      }
    });
  }
  if (state.selectedObjectKey) setSelectedMesh(state.selected, false);
  $('threeSelection').textContent = `마감 적용: ${name} · ${targets.length}개`;
}

function cameraMetadata() {
  return {
    projection: 'perspective',
    position: state.camera.position.toArray().map(value => Number(value.toFixed(6))),
    target: state.controls.target.toArray().map(value => Number(value.toFixed(6))),
    up: state.camera.up.toArray().map(value => Number(value.toFixed(6))),
    fov_degrees: state.camera.fov,
    aspect: Number(state.camera.aspect.toFixed(6)),
    near: state.camera.near,
    far: state.camera.far,
  };
}

function projectId() {
  const source = app.getSourcePath() || 'canvas-session';
  const name = source.split('/').pop().replace(/\.svg$/i, '');
  return (name || 'canvas-session').replace(/[^A-Za-z0-9_.-]+/g, '-').slice(0, 80);
}

function defaultRenderPrompt() {
  return '이 카메라 구도와 공간 배치를 정확히 유지하고, 건축 시각화 품질의 재질·간접광·그림자·사람 눈높이 스케일을 적용해 사실적으로 렌더링해줘. 벽, 기둥, 개구부 위치는 변경하지 마.';
}

function captureDataUrl() {
  state.renderer.render(state.scene, state.camera);
  return $('threeCanvas').toDataURL('image/png');
}

async function sendCut() {
  if (!state.model || state.sourceRevision !== app.getRevision()) rebuild();
  if (!state.model || state.sourceRevision !== app.getRevision()) {
    app.log('3D 컷 전달 중단: SVG revision과 3D 모델 revision이 다릅니다');
    return;
  }
  const prompt = $('threeRenderPrompt').value.trim() || defaultRenderPrompt();
  $('btn3dSend').disabled = true;
  try {
    const result = await app.api('/api/3d/capture', {
      image_data_url: captureDataUrl(),
      project_id: projectId(),
      model_source_revision: state.sourceRevision,
      camera: cameraMetadata(),
      settings: settings(),
      render_prompt: prompt,
      queue_to_agent: true,
    });
    if (result.status === 'ok') {
      const label = result.queued_to_agent === false ? '3D 컷 다운로드 완료' : 'Codex 대기열에 추가됨';
      app.log(label + ': ' + result.capture_path);
      $('threeStatus').textContent = `${label} · source rev ${result.source_revision ?? state.sourceRevision}`;
    } else app.log('3D 컷 전달 오류: ' + result.error);
  } catch (error) {
    app.log('3D 컷 전달 실패: ' + error);
  } finally {
    $('btn3dSend').disabled = false;
  }
}

async function copyCapture() {
  try {
    const blob = await new Promise(resolve => $('threeCanvas').toBlob(resolve, 'image/png'));
    if (!blob || !navigator.clipboard || typeof ClipboardItem === 'undefined') throw new Error('이미지 클립보드를 지원하지 않는 브라우저입니다');
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    app.log('📋 현재 3D 컷 이미지를 복사했습니다 — Codex, GPT, Nano Banana 입력창에 붙여넣으세요');
  } catch (error) {
    app.log('캡처 복사 실패: ' + error.message);
  }
}

async function copyPrompt() {
  const prompt = $('threeRenderPrompt').value.trim() || defaultRenderPrompt();
  try {
    await navigator.clipboard.writeText(prompt);
    app.log('📋 렌더 요청 문구를 복사했습니다');
  } catch (error) {
    app.log('렌더 요청 복사 실패: ' + error.message);
  }
}

function bindHeightInputs(ids, apply) {
  let timer = null;
  const commit = () => {
    clearTimeout(timer);
    timer = null;
    apply();
  };
  for (const id of ids) {
    $(id).addEventListener('input', () => {
      setHeightApplyState('입력 중… 잠시 후 자동 적용');
      clearTimeout(timer);
      timer = setTimeout(commit, 360);
    });
    $(id).addEventListener('change', commit);
    $(id).addEventListener('keydown', event => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      commit();
    });
  }
  return commit;
}

const commitSelectedFloors = bindHeightInputs(
  ['threeSelectedFloors', 'threeSelectedFloorHeight'],
  applySelectedFloors,
);
const commitSelectedHeight = bindHeightInputs(['threeSelectedHeight'], applySelectedHeight);

$('btn3d').onclick = () => {
  const hidden = !$('threePane').classList.contains('hidden');
  $('threePane').classList.toggle('hidden', hidden);
  $('btn3d').classList.toggle('active', !hidden);
  if (!hidden) { init(); resize(); rebuild(); }
};
$('btn3dRebuild').onclick = rebuild;
$('btn3dApplyFinish').onclick = applyFinish;
$('btn3dApplyFloors').onclick = commitSelectedFloors;
$('btn3dApplyHeight').onclick = commitSelectedHeight;
$('btn3dResetHeight').onclick = resetSelectedHeight;
$('threeObjectSelect').addEventListener('change', event => {
  selectObjectKey(event.target.value, true);
});
$('btn3dPerspective').onclick = () => fitCamera('perspective');
$('btn3dTop').onclick = () => fitCamera('top');
$('btn3dEye').onclick = () => fitCamera('eye');
$('btn3dFit').onclick = () => fitCamera('perspective');
$('btn3dSend').onclick = sendCut;
$('btn3dCopy').onclick = copyCapture;
$('btn3dCopyPrompt').onclick = copyPrompt;
$('threeShowSelectionPlan').addEventListener('change', () => {
  if (state.selectionFootprint) state.selectionFootprint.visible = $('threeShowSelectionPlan').checked;
});
const threeCategoryIds = [
  'threeShowWalls', 'threeShowColumns', 'threeShowFurniture', 'threeShowParking',
  'threeShowBuilding', 'threeShowLandscape', 'threeShowCivil', 'threeShowTerrain',
];
const threeViewPresets = {
  all: new Set(threeCategoryIds),
  building: new Set(['threeShowBuilding']),
  landscape: new Set(['threeShowLandscape']),
  'civil-terrain': new Set(['threeShowCivil', 'threeShowTerrain']),
  interior: new Set(['threeShowWalls', 'threeShowColumns', 'threeShowFurniture', 'threeShowParking']),
};
function applyViewPreset(name) {
  const visible = threeViewPresets[name];
  if (!visible) return;
  for (const id of threeCategoryIds) $(id).checked = visible.has(id);
  syncCategoryVisibility();
}
$('threeViewPreset').addEventListener('change', event => applyViewPreset(event.target.value));
for (const id of ['threeWallHeight', 'threeWallThickness', 'threeSlab', 'threeFurnitureHeight', 'threeShowOverlay']) {
  $(id).addEventListener('change', () => {
    if (!$('threePane').classList.contains('hidden')) rebuild();
  });
}
for (const id of threeCategoryIds) {
  $(id).addEventListener('change', () => {
    $('threeViewPreset').value = 'manual';
    syncCategoryVisibility();
  });
}
let rebuildTimer = null;
window.addEventListener('crab-canvas-revision', event => {
  const revision = Number(event.detail && event.detail.revision);
  if ($('threePane').classList.contains('hidden') || !state.initialized || revision === state.sourceRevision) return;
  clearTimeout(rebuildTimer);
  rebuildTimer = setTimeout(rebuild, 180);
});
window.addEventListener('crab-canvas-selection', event => {
  syncSelectionFromCanvas(event.detail?.cids);
});
window.addEventListener('crab-canvas-interaction-mode', updateInteractionMode);
