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
  loadRetry: null,
  wallMeshes: [],
  columnMeshes: [],
  furnitureMeshes: [],
  allMeshes: [],
  objectCount: 0,
  segmentCount: 0,
  wallCount: 0,
  columnCount: 0,
  furnitureCount: 0,
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

function numberValue(id, fallback) {
  const value = Number($(id).value);
  return Number.isFinite(value) && value >= 0 ? value : fallback;
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
    column_count: state.columnCount,
    furniture_count: state.furnitureCount,
    focus_bbox_svg: state.focusBox,
    recognition_mode: state.recognitionMode,
    visible_categories: {
      walls: $('threeShowWalls').checked,
      columns: $('threeShowColumns').checked,
      furniture: $('threeShowFurniture').checked,
      plan_overlay: $('threeShowOverlay').checked,
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
  state.allMeshes = [];
  state.selected = null;
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
  if (kind === 'floor') Object.assign(preset, { color: '#303943', roughness: 0.92, opacity: 1.0 });
  if (kind === 'column') Object.assign(preset, { color: '#c3c8cd', roughness: 0.76, opacity: 1.0 });
  if (kind === 'furniture') Object.assign(preset, { color: '#9a7651', roughness: 0.68, metalness: 0.02, opacity: 1.0 });
  const material = new THREE.MeshPhysicalMaterial({
    color: preset.color,
    roughness: preset.roughness,
    metalness: preset.metalness,
    transparent: preset.opacity < 1,
    opacity: preset.opacity,
    depthWrite: preset.opacity >= 0.9,
    side: THREE.DoubleSide,
  });
  material.userData.baseEmissive = '#000000';
  return material;
}

function addWallSegment(group, a, b, options) {
  const dx = (b.x - a.x) * options.unitsToMeters;
  const dz = (b.y - a.y) * options.unitsToMeters;
  const length = Math.hypot(dx, dz);
  if (length < Math.max(options.thickness * 0.35, 0.015) || state.segmentCount >= (options.maxSegments || 4200)) return;
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
  mesh.userData = { cid: options.cid, role: options.kind, derivedFromSvg: true };
  group.add(mesh);
  state.wallMeshes.push(mesh);
  state.allMeshes.push(mesh);
  state.segmentCount += 1;
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
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = 0.006;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData = { cid: options.cid, role: kind, derivedFromSvg: true };
  group.add(mesh);
  state.allMeshes.push(mesh);
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

async function addPlanOverlay(sceneBox, unitsToMeters, revision, model) {
  if (!$('threeShowOverlay').checked || !model || state.model !== model) return;
  const bbox = [sceneBox.x0, sceneBox.y0, sceneBox.x1, sceneBox.y1].map(value => Number(value.toFixed(3))).join(',');
  try {
    const response = await fetch(`/api/render?bbox=${encodeURIComponent(bbox)}&px=2048`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    if (revision !== state.sourceRevision || state.model !== model) return;
    const url = URL.createObjectURL(blob);
    const texture = await new Promise((resolve, reject) => {
      new THREE.TextureLoader().load(url, resolve, undefined, reject);
    });
    URL.revokeObjectURL(url);
    if (revision !== state.sourceRevision || state.model !== model) { texture.dispose(); return; }
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = Math.min(8, state.renderer.capabilities.getMaxAnisotropy());
    const geometry = new THREE.PlaneGeometry((sceneBox.x1 - sceneBox.x0) * unitsToMeters,
      (sceneBox.y1 - sceneBox.y0) * unitsToMeters);
    const material = new THREE.MeshBasicMaterial({ map: texture, transparent: true, opacity: 0.72,
      depthWrite: false, side: THREE.DoubleSide });
    const overlay = new THREE.Mesh(geometry, material);
    overlay.rotation.x = -Math.PI / 2;
    overlay.position.y = 0.012;
    overlay.renderOrder = 2;
    overlay.userData = { role: 'plan-overlay', derivedFromSvg: true };
    model.add(overlay);
  } catch (error) {
    app.log('2D 정합선 로드 실패: ' + error.message);
  }
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
  $('threeStatus').textContent = 'SVG에서 벽 · 기둥 · 가구 인식 중…';
  disposeModel();
  state.model = new THREE.Group();
  state.model.name = 'svg-derived-review-model';
  state.scene.add(state.model);
  state.segmentCount = 0;
  state.objectCount = 0;
  state.wallCount = 0;
  state.columnCount = 0;
  state.furnitureCount = 0;

  const vb = root.viewBox && root.viewBox.baseVal;
  const viewBox = vb && vb.width > 0 ? { x: vb.x, y: vb.y, width: vb.width, height: vb.height } :
    { x: 0, y: 0, width: Number(root.getAttribute('width') || 1000), height: Number(root.getAttribute('height') || 1000) };
  const graphics = Array.from(root.querySelectorAll('line,polyline,polygon,path,rect,circle,ellipse'));
  const semanticNodes = root.querySelectorAll(
    '[data-role],[data-crab-program-role],[data-crab-solver-projected-role]');
  const semantic = Array.from(semanticNodes).some(element =>
    /wall|partition|column|glazing|shell|boundary|furniture|furnishing|chair|table|sofa|equipment/.test(roleText(element)));
  state.recognitionMode = semantic ? 'hybrid_semantic_and_inference' : 'flattened_svg_inference';
  const focusBox = deriveFocusBox(root, graphics, viewBox);
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
  const labels = labelCenters(root);
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
        !hasCenteredLabel(box, labels);
    }

    if (isColumn) {
      if (!$('threeShowColumns').checked) continue;
      try {
        const points = ['polygon', 'polyline', 'rect'].includes(tag) ? elementPoints(root, element, 32) : null;
        if (addColumn(state.model, root, element, baseOptions, points)) state.columnCount += 1;
      } catch (_) {}
      continue;
    }
    if (isFurniture) {
      if (!$('threeShowFurniture').checked || state.furnitureCount >= 1400) continue;
      try {
        const points = ['polygon', 'polyline', 'rect'].includes(tag) ? elementPoints(root, element, 32) : null;
        if (addFurniture(state.model, root, element, baseOptions, points)) state.furnitureCount += 1;
      } catch (_) {}
      continue;
    }
    if (!isWall || !$('threeShowWalls').checked ||
        !['line', 'polyline', 'polygon', 'path', 'rect'].includes(tag)) continue;
    const points = elementPoints(root, element);
    if (points.length < 2) continue;
    const before = state.segmentCount;
    const options = { ...baseOptions, cid, kind: isGlass ? 'glass' : 'wall' };
    for (let index = 1; index < points.length; index += 1) {
      addWallSegment(state.model, points[index - 1], points[index], options);
    }
    if (state.segmentCount > before) state.wallCount += 1;
  }

  state.sourceRevision = app.getRevision();
  state.objectCount = state.wallCount + state.columnCount + state.furnitureCount;
  const currentModel = state.model;
  addPlanOverlay(focusBox, unitsToMeters, state.sourceRevision, currentModel);
  fitCamera('perspective');
  const modeLabel = semantic ? '역할 태그 + 무태그 추론' : '평탄화 추론';
  $('threeStatus').textContent = `rev ${state.sourceRevision} · 벽 ${state.wallCount} · 기둥 ${state.columnCount} · 가구 ${state.furnitureCount} · ${modeLabel}`;
  $('threeSelection').textContent = '선택: 전체 벽';
  app.log(`3D 정합 완료: 벽 ${state.wallCount}개/${state.segmentCount} 세그먼트 · ` +
    `기둥 ${state.columnCount}개 · 가구 ${state.furnitureCount}개 · ${modeLabel}` +
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
  if (mode === 'top') state.camera.position.set(center.x, center.y + distance * 1.55, center.z + 0.001);
  else if (mode === 'eye') state.camera.position.set(center.x - distance * 0.9, 1.65, center.z + distance * 0.65);
  else state.camera.position.set(center.x + distance * 0.9, center.y + distance * 0.95, center.z + distance * 0.9);
  state.controls.target.copy(mode === 'eye' ? new THREE.Vector3(center.x, 1.25, center.z) : center);
  state.camera.near = Math.max(0.01, distance / 1000);
  state.camera.far = Math.max(200, distance * 20);
  state.camera.updateProjectionMatrix();
  state.controls.update();
}

function clearSelectionHighlight() {
  if (!state.selected || !state.selected.material || !state.selected.material.emissive) return;
  state.selected.material.emissive.set(state.selected.material.userData.baseEmissive || '#000000');
  state.selected.material.emissiveIntensity = 1;
}

function selectObject(event) {
  if (!state.model || event.button !== 0) return;
  const rect = $('threeCanvas').getBoundingClientRect();
  const pointer = new THREE.Vector2(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  );
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(pointer, state.camera);
  const hit = raycaster.intersectObjects(state.allMeshes, false)[0];
  clearSelectionHighlight();
  state.selected = hit ? hit.object : null;
  if (state.selected && state.selected.material && state.selected.material.emissive) {
    state.selected.material.emissive.set('#164e63');
    state.selected.material.emissiveIntensity = 0.75;
    const cid = state.selected.userData.cid;
    $('threeSelection').textContent = `선택: ${state.selected.userData.role || 'object'} ${cid || ''}`.trim();
    if (cid) app.selectCid(cid);
  } else {
    $('threeSelection').textContent = '선택: 전체 벽';
  }
}

function applyFinish() {
  const name = $('threeFinish').value;
  const preset = { ...(finishPresets[name] || finishPresets.custom) };
  if (name === 'custom') preset.color = $('threeColor').value;
  else $('threeColor').value = preset.color;
  const targets = state.selected && state.selected.userData.role !== 'floor' ? [state.selected] : state.wallMeshes;
  clearSelectionHighlight();
  for (const mesh of targets) {
    const material = mesh.material;
    material.color.set(preset.color);
    material.roughness = preset.roughness;
    material.metalness = preset.metalness;
    material.transparent = preset.opacity < 1;
    material.opacity = preset.opacity;
    material.depthWrite = preset.opacity >= 0.9;
    material.needsUpdate = true;
  }
  state.selected = null;
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
      app.log('📷 3D 컷을 Codex로 전달: ' + result.capture_path);
      $('threeStatus').textContent = `Codex 전달 완료 · source rev ${result.source_revision}`;
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

$('btn3d').onclick = () => {
  const hidden = !$('threePane').classList.contains('hidden');
  $('threePane').classList.toggle('hidden', hidden);
  $('btn3d').classList.toggle('active', !hidden);
  if (!hidden) { init(); resize(); rebuild(); }
};
$('btn3dRebuild').onclick = rebuild;
$('btn3dApplyFinish').onclick = applyFinish;
$('btn3dPerspective').onclick = () => fitCamera('perspective');
$('btn3dTop').onclick = () => fitCamera('top');
$('btn3dEye').onclick = () => fitCamera('eye');
$('btn3dFit').onclick = () => fitCamera('perspective');
$('btn3dSend').onclick = sendCut;
$('btn3dCopy').onclick = copyCapture;
$('btn3dCopyPrompt').onclick = copyPrompt;
for (const id of [
  'threeWallHeight', 'threeWallThickness', 'threeSlab', 'threeFurnitureHeight',
  'threeShowWalls', 'threeShowColumns', 'threeShowFurniture', 'threeShowOverlay',
]) {
  $(id).addEventListener('change', () => { if (!$('threePane').classList.contains('hidden')) rebuild(); });
}
let rebuildTimer = null;
window.addEventListener('crab-canvas-revision', event => {
  const revision = Number(event.detail && event.detail.revision);
  if ($('threePane').classList.contains('hidden') || !state.initialized || revision === state.sourceRevision) return;
  clearTimeout(rebuildTimer);
  rebuildTimer = setTimeout(rebuild, 180);
});
