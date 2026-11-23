"use strict";

const get = id => document.getElementById(id);
const canvas = get("cloud");
const ctx = canvas.getContext("2d");
const target = get("target");
const orbit = get("orbit");
const topView = get("top");
const navigationButtons = [orbit, topView, get("zoom-in"), get("zoom-out"), get("reset-view")];
const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });
const mode = get("mode");
const connection = get("connection");
const command = get("command");
const execution = get("execution");
const stop = get("stop");
const folderInput = get("photo-folder");
const chooseFolder = get("pick-photos");
const reconstruct = get("reconstruct");
const reconstructionStatus = get("reconstruction-status");
const reconstructionResult = get("reconstruction-result");
const gsOption = get("train-gs");
const gsButton = get("gaussians");
const gsContainer = get("gs-viewer");
const gsStatus = get("gs-status");
const gsOverlay = get("gs-overlay");
const gsTargets = get("gs-targets");
const gsTargetOutlines = get("gs-target-outlines");
const svgNamespace = "http://www.w3.org/2000/svg";
const depart = get("depart");
const startConfirmed = get("start-confirmed");
const targetSearch = get("target-search");
const targetKeyword = get("target-keyword");
const targetSearchStatus = get("target-search-status");
const targetList = get("target-list");
const stages = {
  uploading: "正在接收照片",
  starting: "正在启动重建",
  features: "正在提取特征",
  matching: "正在匹配照片",
  mapping: "正在恢复相机位姿和三维点",
  export: "正在导出点云",
  gaussians: "正在训练实景 GS",
};

const colors = { car: "#44d9ed", cup: "#83b7ff", route: "#71efdb" };
const stateLabels = {
  idle: "等待指令", running: "处理中", stopping: "正在停车",
  stopped: "已停车", completed: "动作指令执行结束", previewed: "预演完成", error: "执行失败",
};
const commandLabels = { 22: "前进", 23: "后退", 24: "停车" };
const connectionLabels = {
  online: "小车已连接", offline: "小车未连接", connecting: "正在连接小车", not_polled: "未启用小车轮询",
  usb: "小车已通过 USB 连接",
};

let scene;
let model;
let selected = false;
let lastCommand;
let waitingForStop = false;
let yaw = 0;
let elevation = 0;
let zoom = 1;
let view = "orbit";
let drag;
let sceneRequest = 0;
let photos = [];
let folderName = "";
let submitting = false;
let currentJob;
let latestResult;
let gsAvailable = false;
let gsViewer;
let gsRequest = 0;
let gsTimeout;
let gatewayStatus;
let departing = false;
let activeTarget;
let activeRoute;
let gsMarkers = [];
let sceneCloud;

const subtract = (a, b) => a.map((value, axis) => value - b[axis]);
const dot = (a, b) => a.reduce((sum, value, axis) => sum + value * b[axis], 0);
const cross = (a, b) => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const unit = vector => {
  const length = Math.hypot(...vector);
  if (length === 0) throw new Error("场景坐标轴无效");
  return vector.map(value => value / length);
};
const limit = (value, low, high) => Math.min(high, Math.max(low, value));

function parseCloud(buffer, semantic) {
  const decoder = new TextDecoder();
  const header = decoder.decode(new Uint8Array(buffer, 0, Math.min(4096, buffer.byteLength)));
  const end = /^end_header\r?\n/m.exec(header);
  const vertex = /^element vertex (\d+)\r?$/m.exec(header);
  const count = Number(vertex?.[1]);
  if (!header.startsWith("ply") || !end || !count) throw new Error("PLY 点云为空或格式无效");
  const offset = end.index + end[0].length;
  const binary = /^format binary_little_endian 1\.0\r?$/m.test(header);
  const ascii = /^format ascii 1\.0\r?$/m.test(header);
  if (!binary && !ascii) throw new Error("请使用带 XYZ RGB 的 COLMAP PLY");
  const data = new DataView(buffer, offset);
  const lines = ascii ? decoder.decode(data).trim().split(/\r?\n/) : [];
  if (binary ? data.byteLength < 15 * count : lines.length < count) throw new Error("PLY 点云不完整");

  // Limit browser drawing work while keeping the original PLY available for download.
  const stride = Math.max(1, Math.ceil(count / 25000));
  const points = [];
  for (let index = 0; index < count; index += stride) {
    const row = index * 15;
    const values = binary ? [
      data.getFloat32(row, true), data.getFloat32(row + 4, true), data.getFloat32(row + 8, true),
      data.getUint8(row + 12), data.getUint8(row + 13), data.getUint8(row + 14),
    ] : lines[index].trim().split(/\s+/).map(Number);
    if (values.length !== 6 || !values.every(Number.isFinite)) throw new Error("PLY 点坐标或颜色无效");
    const [x, y, z, r, g, b] = values;
    let label = null;
    if (semantic && r === 255 && g === 115 && b === 20) label = "cup";
    if (semantic && r === 0 && g === 210 && b === 245) label = "car";
    points.push({ xyz: [x, y, z], label, color: label ? colors[label] : "rgb(" + [r, g, b].join(",") + ")" });
  }
  return { points, count };
}

function buildModel(points, currentScene) {
  const geometry = currentScene.geometry;
  const origin = geometry?.start || [0, 0, 0];
  const up = unit(geometry?.up || currentScene.view_up);
  const right = geometry ? subtract(geometry.front, origin) : currentScene.view_right;
  const side = unit(cross(up, right));
  const forward = cross(side, up);
  const local = xyz => {
    const relative = subtract(xyz, origin);
    return [dot(relative, forward), dot(relative, side), dot(relative, up)];
  };

  const markers = geometry ? Object.fromEntries(
    ["start", "front", "stop", "target"].map(key => [key, local(geometry[key])])
  ) : null;
  const position = points.map(point => ({ ...point, xyz: local(point.xyz) }));
  // Frame the navigation area. Distant background points are still visible when zooming out.
  const focus = markers
    ? [...Object.values(markers), ...position.filter(point => point.label).map(point => point.xyz)]
    : position.map(point => point.xyz);
  const min = [...focus[0]];
  const max = [...focus[0]];
  for (const xyz of focus) {
    for (let axis = 0; axis < 3; axis++) {
      min[axis] = Math.min(min[axis], xyz[axis]);
      max[axis] = Math.max(max[axis], xyz[axis]);
    }
  }
  return {
    points: position, markers, min, max,
    center: min.map((value, axis) => (value + max[axis]) / 2),
    span: Math.max(.01, Math.hypot(...subtract(max, min))),
  };
}

function line(a, b, color, width = 1, dash = []) {
  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.setLineDash(dash);
  ctx.moveTo(a[0], a[1]);
  ctx.lineTo(b[0], b[1]);
  ctx.stroke();
  ctx.setLineDash([]);
}

function circle([x, y], radius, color, outline = false) {
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, 2 * Math.PI);
  if (outline) {
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = color;
    ctx.stroke();
  } else {
    ctx.fillStyle = color;
    ctx.fill();
  }
}

function label([x, y], text, color, width, height) {
  ctx.font = "12px system-ui, Microsoft YaHei, sans-serif";
  const badge = ctx.measureText(text).width + 16;
  const left = limit(x - badge / 2, 5, width - badge - 5);
  const baseline = limit(y, 27, height - 11);
  ctx.fillStyle = "#0a1725ed";
  ctx.fillRect(left, baseline - 16, badge, 23);
  ctx.fillStyle = color;
  ctx.fillText(text, left + 8, baseline);
}

function draw() {
  if (!model) return;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (width < 1 || height < 1) return;
  const ratio = Math.min(devicePixelRatio || 1, 2);
  const pixelWidth = Math.round(width * ratio);
  const pixelHeight = Math.round(height * ratio);
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const { center, markers, min, max, points, span } = model;
  const scale = Math.min(width * .86, height * 1.32) / span * zoom;
  const sin = Math.sin(yaw);
  const cos = Math.cos(yaw);
  const rise = Math.sin(elevation);
  const depth = Math.cos(elevation);
  const project = xyz => {
    const [x, y, z] = subtract(xyz, center);
    const side = x * sin + y * cos;
    return [
      width / 2 + (x * cos - y * sin) * scale,
      height / 2 - (z * depth + side * rise) * scale,
      side * depth - z * rise,
    ];
  };

  const floor = min[2] - span * .04;
  const inset = span * .08;
  for (let i = 0; i <= 12; i++) {
    const x = min[0] - inset + (max[0] - min[0] + 2 * inset) * i / 12;
    const y = min[1] - inset + (max[1] - min[1] + 2 * inset) * i / 12;
    line(project([x, min[1] - inset, floor]), project([x, max[1] + inset, floor]), "#274259a8");
    line(project([min[0] - inset, y, floor]), project([max[0] + inset, y, floor]), "#274259a8");
  }

  const projected = points.map(point => ({ ...point, screen: project(point.xyz) }));
  projected.sort((a, b) => b.screen[2] - a.screen[2]);
  projected.forEach(({ screen, color, label: semantic }) => {
    ctx.globalAlpha = semantic === "cup" && !selected ? .63 : semantic ? .94 : .9;
    circle(screen, semantic === "cup" && selected ? 3.3 : semantic ? 2.5 : 2, color);
  });
  ctx.globalAlpha = 1;
  if (!markers) return;

  const start = project(markers.start);
  const front = project(markers.front);
  const end = project(markers.stop);
  const cup = project(markers.target);
  if (selected) {
    line(start, end, colors.route, 2.5, [7, 5]);
    circle(end, 6, colors.route, true);
    circle(cup, 12, colors.cup, true);
    label([end[0], end[1] + 24], "计划停车点", colors.route, width, height);
  }

  const angle = Math.atan2(front[1] - start[1], front[0] - start[0]);
  line(start, front, colors.car, 2.1);
  for (const offset of [-.57, .57]) {
    line(front, [front[0] - 9 * Math.cos(angle + offset), front[1] - 9 * Math.sin(angle + offset)], colors.car, 2.1);
  }
  circle(start, 4.5, colors.car);
  const from = gatewayStatus?.navigation?.source;
  label([start[0], start[1] - 13], selected && from && from !== "start" ? "预计小车位置" : "拍摄时起点", colors.car, width, height);
  if (activeTarget) label([cup[0], cup[1] - 15], activeTarget.name, colors.cup, width, height);
}

function selectView(next) {
  gsRequest += 1;
  clearTimeout(gsTimeout);
  gsViewer?.destroy();
  gsViewer = undefined;
  gsContainer.hidden = true;
  gsOverlay.hidden = true;
  gsStatus.hidden = true;
  canvas.hidden = false;
  get("cloud-zoom").hidden = false;
  get("recon-legend").textContent = "原色点云 · 尺度未标定";
  gsButton.setAttribute("aria-pressed", "false");
  view = next;
  const orientation = model?.markers ? Math.PI : 0;
  yaw = next === "top" ? orientation : orientation - .28;
  elevation = next === "top" ? Math.PI / 2 : .72;
  zoom = .95;
  orbit.setAttribute("aria-pressed", String(next === "orbit"));
  topView.setAttribute("aria-pressed", String(next === "top"));
  draw();
}

function convexSilhouette(points) {
  if (points.length < 3) return [];
  const sorted = points.sort((a, b) => a.x - b.x || a.y - b.y);
  const side = (a, b, c) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  const edge = order => {
    const chain = [];
    for (const point of order) {
      while (chain.length > 1 && side(chain[chain.length - 2], chain[chain.length - 1], point) <= 0) chain.pop();
      chain.push(point);
    }
    return chain.slice(0, -1);
  };
  return [...edge(sorted), ...edge(sorted.reverse())];
}

function renderGsMarkers(items) {
  gsTargets.replaceChildren();
  gsTargetOutlines.replaceChildren();
  gsMarkers = items.filter(item => item.gs_navigation).map(item => {
    const group = document.createElement("div");
    const hit = document.createElement("button");
    const outline = document.createElementNS(svgNamespace, "polygon");
    const choose = () => {
      targetSearchStatus.hidden = true;
      selectTarget(item, true);
    };
    group.className = "gs-target";
    group.dataset.targetId = item.id;
    group.hidden = true;
    hit.type = "button";
    hit.className = "gs-target-hit";
    hit.setAttribute("aria-label", item.name);
    outline.setAttribute("class", "gs-target-outline");
    outline.setAttribute("hidden", "");
    hit.addEventListener("click", choose);
    hit.addEventListener("pointerenter", () => outline.classList.add("highlighted"));
    hit.addEventListener("pointerleave", () => outline.classList.remove("highlighted"));
    hit.addEventListener("focus", () => outline.classList.add("highlighted"));
    hit.addEventListener("blur", () => outline.classList.remove("highlighted"));
    group.append(hit);
    gsTargets.append(group);
    gsTargetOutlines.append(outline);
    return { item, group, hit, outline };
  });
}

function drawGsMarker(marker, project, width, height) {
  const { item, group, hit, outline: shape } = marker;
  const navigation = item.gs_navigation;
  const position = project(navigation.geometry.target);
  group.style.zIndex = item.id === activeTarget?.id ? "2" : "1";
  hit.setAttribute("aria-pressed", String(item.id === activeTarget?.id));
  shape.classList.toggle("selected", item.id === activeTarget?.id);
  const vertices = navigation.cup_outline || navigation.cup_points;
  hit.classList.toggle("location-only", !vertices);
  if (!vertices) {
    const visible = position && position.x >= 0 && position.x <= width && position.y >= 0 && position.y <= height;
    group.hidden = !visible;
    shape.toggleAttribute("hidden", true);
    if (visible) {
      Object.assign(hit.style, { left: position.x - 10 + "px", top: position.y - 10 + "px",
        width: "20px", height: "20px", clipPath: "circle(50%)" });
    }
    return;
  }
  const points = vertices.map(project).filter(Boolean);
  const outline = convexSilhouette(points);
  const x = outline.map(point => point.x);
  const y = outline.map(point => point.y);
  const left = Math.min(...x);
  const right = Math.max(...x);
  const top = Math.min(...y);
  const bottom = Math.max(...y);
  const w = right - left;
  const h = bottom - top;
  const visible = outline.length >= 3 && w > 1 && h > 1 && right >= 0 && left <= width && bottom >= 0 && top <= height;
  group.hidden = !visible;
  shape.toggleAttribute("hidden", !visible);
  if (!visible) return;
  shape.setAttribute("points", outline.map(point => point.x.toFixed(1) + "," + point.y.toFixed(1)).join(" "));
  const clip = outline.map(point => ((point.x - left) / w * 100).toFixed(2) + "% "
    + ((point.y - top) / h * 100).toFixed(2) + "%").join(", ");
  Object.assign(hit.style, { left: left + "px", top: top + "px", width: w + "px", height: h + "px",
    clipPath: "polygon(" + clip + ")" });
}

function redrawGsNavigation() {
  const camera = gsViewer?.app.root.findByName("camera")?.camera;
  if (!camera || !gsViewer.state.loaded) return;
  gsOverlay.hidden = gsMarkers.length === 0;
  const width = gsOverlay.clientWidth;
  const height = gsOverlay.clientHeight;
  if (!width || !height) return;
  const origin = camera.entity.getPosition();
  const forward = camera.entity.forward;
  const project = ([x, y, z]) => {
    const depth = (x - origin.x) * forward.x + (y - origin.y) * forward.y + (z - origin.z) * forward.z;
    if (depth <= camera.nearClip) return null;
    const point = camera.worldToScreen({ x, y, z }, undefined, width, height);
    return Number.isFinite(point.x) && Number.isFinite(point.y) ? point : null;
  };
  for (const marker of gsMarkers) drawGsMarker(marker, project, width, height);
  const route = get("gs-route-overlay");
  const geometry = activeRoute?.gs_geometry || activeTarget?.gs_navigation?.geometry;
  if (!selected || !geometry?.stop) {
    route.toggleAttribute("hidden", true);
    return;
  }
  const start = project(geometry.start);
  const end = project(geometry.stop);
  const front = project(geometry.front);
  const routeVisible = start && end && front;
  route.toggleAttribute("hidden", !routeVisible);
  if (!routeVisible) return;
  const set = (id, attributes) => {
    for (const [key, value] of Object.entries(attributes)) get(id).setAttribute(key, value);
  };
  set("gs-heading", { x1: start.x, y1: start.y, x2: front.x, y2: front.y });
  set("gs-path", { x1: start.x, y1: start.y, x2: end.x, y2: end.y });
  set("gs-start", { cx: start.x, cy: start.y });
  set("gs-end", { cx: end.x, cy: end.y });
  set("gs-end-label", { x: end.x + 9, y: end.y + 19 });
}

async function showGaussians() {
  if (!scene?.gs_url || view === "gaussians") return;
  const current = ++gsRequest;
  const { gs_url, gs_camera, gs_navigation } = scene;
  view = "gaussians";
  canvas.hidden = true;
  get("cloud-zoom").hidden = true;
  get("scene-error").hidden = true;
  gsContainer.hidden = false;
  get("recon-legend").textContent = gs_navigation ? "实景 GS · 搜索或点击目标" : "实景 GS · 尺度未标定";
  gsStatus.hidden = false;
  gsStatus.textContent = "正在加载实景 GS…";
  orbit.setAttribute("aria-pressed", "false");
  topView.setAttribute("aria-pressed", "false");
  gsButton.setAttribute("aria-pressed", "true");
  try {
    const { createViewer } = await import("/vendor/supersplat/index.js");
    if (current !== gsRequest) return;
    const viewer = await createViewer({
      container: gsContainer,
      contentUrl: gs_url,
      settings: {
        version: 2,
        tonemapping: "none",
        highPrecisionRendering: false,
        background: { color: [0.03, 0.07, 0.11] },
        animTracks: [],
        cameras: [{ initial: gsMarkers.length ? { ...gs_camera, fov: Math.max(75, gs_camera.fov) } : gs_camera }],
        annotations: [],
        startMode: "default",
      },
      renderer: "webgl",
      nofx: true,
      noanim: true,
    });
    if (current !== gsRequest) {
      viewer.destroy();
      return;
    }
    gsViewer = viewer;
    const showLoaded = () => {
      if (current !== gsRequest) return;
      clearTimeout(gsTimeout);
      gsStatus.hidden = true;
      if (gsMarkers.length) {
        viewer.app.on("postrender", redrawGsNavigation);
        redrawGsNavigation();
      }
    };
    if (viewer.state.loaded) {
      showLoaded();
    } else {
      viewer.events.once("loaded:changed", showLoaded);
      gsTimeout = setTimeout(() => {
        gsStatus.textContent = "实景加载超时。可切回三维点云后重试。";
      }, 45000);
    }
  } catch {
    if (current === gsRequest) gsStatus.textContent = "无法打开实景 GS，请确认浏览器已启用 WebGL。";
  }
}

gsButton.addEventListener("click", showGaussians);

function changeZoom(factor) {
  zoom = limit(zoom * factor, .20, 5);
  draw();
}

function rotate(dx, dy) {
  if (view !== "orbit") return;
  yaw += dx * .009;
  elevation = limit(elevation + dy * .008, .08, 1.55);
  draw();
}

orbit.addEventListener("click", () => selectView("orbit"));
topView.addEventListener("click", () => selectView("top"));
get("reset-view").addEventListener("click", () => selectView(view));
get("zoom-in").addEventListener("click", () => changeZoom(1.22));
get("zoom-out").addEventListener("click", () => changeZoom(1 / 1.22));
canvas.addEventListener("wheel", event => {
  if (!model) return;
  event.preventDefault();
  changeZoom(event.deltaY < 0 ? 1.12 : 1 / 1.12);
}, { passive: false });
canvas.addEventListener("pointerdown", event => {
  if (!model || !event.isPrimary || event.button !== 0 || view !== "orbit") return;
  canvas.setPointerCapture(event.pointerId);
  drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
});
canvas.addEventListener("pointermove", event => {
  if (drag?.id !== event.pointerId) return;
  rotate(event.clientX - drag.x, event.clientY - drag.y);
  drag.x = event.clientX;
  drag.y = event.clientY;
});
canvas.addEventListener("lostpointercapture", () => { drag = null; });
canvas.addEventListener("keydown", event => {
  if (!model) return;
  const keys = { ArrowUp: [0, -12], ArrowDown: [0, 12], ArrowLeft: [-12, 0], ArrowRight: [12, 0] };
  if (event.key === "+" || event.key === "=") changeZoom(1.22);
  else if (event.key === "-") changeZoom(1 / 1.22);
  else if (keys[event.key]) rotate(...keys[event.key]);
  else return;
  event.preventDefault();
});
new ResizeObserver(draw).observe(canvas);

function showRoute() {
  if (!activeRoute) return;
  selected = true;
  target.setAttribute("aria-pressed", "true");
  updateDeparture();
  get("target-action").textContent = "路线已展开";
  get("route-empty").hidden = true;
  get("route").hidden = false;
  draw();
  redrawGsNavigation();
}

function selectTarget(item, preview) {
  activeTarget = item;
  const navigation = scene.web_navigation_configured ? gatewayStatus?.navigation : null;
  activeRoute = item && !item.unavailable_reason
    ? (navigation ? navigation.routes[item.id] : item)
    : null;
  selected = false;
  startConfirmed.checked = false;
  get("depart-status").hidden = true;
  get("route").hidden = true;
  get("route-empty").hidden = false;
  target.hidden = !item || targetList.childElementCount > 0;
  for (const button of targetList.children) {
    button.setAttribute("aria-pressed", String(button.dataset.targetId === item?.id));
  }
  target.setAttribute("aria-pressed", "false");
  get("route-steps").replaceChildren();
  get("target-action").textContent = "查看路线 ›";
  get("departure").hidden = !item?.web_ready || !activeRoute;
  get("route-empty").textContent = scene.geometry ? "选择目标查看转向、距离和计划停车点。" : "完成标注后可生成路线。";
  if (scene.geometry) {
    // Keep the scene's coordinate basis and replace only the target and route markers.
    const { points } = sceneCloud;
    let modelPoints = points;
    if (item?.id !== "blue_cup") {
      modelPoints = points.map(point => point.label === "cup" ? { ...point, label: null, color: "#68788b" } : point);
    }
    const geometry = { ...scene.geometry, ...item?.geometry, ...activeRoute?.geometry };
    model = buildModel(modelPoints, { ...scene, geometry });
  }
  if (item) {
    get("route-empty").textContent = item.unavailable_reason || "选择目标查看转向、距离和计划停车点。";
    get("target-name").textContent = item.name;
  }
  if (activeRoute) {
    if (activeRoute.turn_deg) addStep(activeRoute.turn_deg > 0 ? "左转" : "右转", number.format(Math.abs(activeRoute.turn_deg)) + "°");
    if (activeRoute.distance_cm) addStep("前进", number.format(activeRoute.distance_cm) + " cm");
    if (item.push_cm != null) addStep("终点", "接触后再推进 " + number.format(item.push_cm) + " cm");
    else addStep("停车", item.name + "旁" + (item.stand_off_cm ? " · 留 " + item.stand_off_cm + " cm" : ""));
  }
  if (item && !item.unavailable_reason && !activeRoute && navigation) {
    get("route-empty").textContent = navigation.source === "unknown"
      ? "当前位置未确认。请把小车摆回拍摄时的起点，再点「重置起点」。"
      : "小车已在此目标的计划停车点，请选择下一站。";
  }
  if (preview && activeRoute) showRoute();
  else {
    updateDeparture();
    draw();
    redrawGsNavigation();
  }
}

const normalized = value => value.normalize("NFKC").trim().toLowerCase().replace(/[\s_]+/g, "");
function renderTargetButtons(items) {
  targetList.replaceChildren();
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button target-choice";
    button.dataset.targetId = item.id;
    button.textContent = item.name;
    button.setAttribute("aria-pressed", "false");
    if (item.unavailable_reason) {
      const detail = document.createElement("small");
      detail.textContent = "可定位";
      button.append(detail);
    }
    button.addEventListener("click", () => {
      targetSearchStatus.hidden = true;
      selectTarget(item, true);
    });
    targetList.append(button);
  }
}
targetSearch.addEventListener("submit", event => {
  event.preventDefault();
  if (!scene?.targets?.length) return;
  const query = normalized(targetKeyword.value);
  targetSearchStatus.hidden = !query;
  if (!query) { selectTarget(scene.targets[0], false); return; }
  const item = scene.targets.find(entry => [entry.name, ...entry.keywords].some(word => normalized(word) === query));
  targetSearchStatus.textContent = item ? "已找到 " + item.name : "未找到匹配目标。可以直接选择下方物品。";
  selectTarget(item, true);
});

target.addEventListener("click", showRoute);

function addStep(action, value) {
  const item = document.createElement("li");
  const label = document.createElement("span");
  const amount = document.createElement("strong");
  label.textContent = action;
  amount.textContent = value;
  item.append(label, amount);
  get("route-steps").append(item);
}

async function request(url, options = {}, timeout = 2500) {
  const response = await fetch(url, { ...options, cache: "no-store", signal: AbortSignal.timeout(timeout) });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || "HTTP " + response.status);
  }
  return response;
}

async function loadScene(result) {
  const revision = ++sceneRequest;
  const message = get("scene-error");
  message.hidden = true;
  try {
    const nextScene = result || await (await request("/scene")).json();
    const response = await request(result ? result.cloud_url : nextScene.point_cloud_url, {}, 15000);
    const { points, count } = parseCloud(await response.arrayBuffer(), Boolean(nextScene.geometry));
    const nextModel = buildModel(points, nextScene);
    if (revision !== sceneRequest) return;
    scene = nextScene;
    sceneCloud = { points };
    model = nextModel;
    selected = false;
    startConfirmed.checked = false;
    get("depart-status").hidden = true;
    get("scene-name").textContent = scene.name;
    get("scene-info").textContent = result ? result.registered + " / " + result.photos + " 张照片已注册 · " + count + " 个重建点" : count + " 个重建点";
    get("route-steps").replaceChildren();
    get("route").hidden = true;
    const emptyRoute = get("route-empty");
    emptyRoute.hidden = false;
    emptyRoute.textContent = result ? "完成标注后可生成路线。" : "选择目标查看转向、距离和计划停车点。";
    target.setAttribute("aria-pressed", "false");
    get("target-action").textContent = "查看路线 ›";
    target.hidden = Boolean(result);
    target.disabled = Boolean(result);
    get("no-scene-target").hidden = !result;
    get("view-navigation").hidden = !result;
    get("recon-legend").hidden = !result && !scene.gs_url;
    gsButton.hidden = !scene.gs_url;
    targetSearch.hidden = !scene.targets?.length;
    renderTargetButtons(scene.targets || []);
    renderGsMarkers(scene.targets || []);
    targetSearchStatus.hidden = true;
    targetKeyword.value = "";
    document.querySelectorAll("[data-route-legend]").forEach(item => { item.hidden = Boolean(result); });
    selectTarget(scene.targets?.[0], false);
    for (const button of navigationButtons) button.disabled = false;
    selectView("orbit");
    if (scene.gs_url) showGaussians();
    if (scene.voice_navigation_configured && lastCommand?.blueCup) showRoute();
    updateNavigationPosition();
  } catch (error) {
    if (revision !== sceneRequest) return;
    if (!model) get("scene-info").textContent = "加载失败";
    message.textContent = "无法加载场景 · " + error.message;
    message.hidden = false;
  }
}

function renderStatus(status) {
  const previousRevision = gatewayStatus?.navigation?.revision;
  gatewayStatus = status;
  if (scene?.web_navigation_configured && status.navigation?.revision !== previousRevision && activeTarget) {
    selectTarget(activeTarget, selected);
  }
  connection.textContent = connectionLabels[status.car_connection] || "后端已连接";
  connection.dataset.state = status.car_connection || "unknown";
  mode.hidden = false;
  mode.dataset.mode = status.mode;
  mode.textContent = status.mode === "live" ? "实车" : "预演";
  const blueCup = status.blue_cup_id != null && status.last_command_id === status.blue_cup_id;
  const newVoiceCup = blueCup && !lastCommand?.blueCup;
  lastCommand = { blueCup };
  if (newVoiceCup && model && scene.voice_navigation_configured) selectTarget(scene.targets[0], true);
  const id = status.last_command_id;
  const title = blueCup ? "去蓝杯" : commandLabels[id];
  const destination = scene?.targets?.find(item => item.id === status.last_target)?.name;
  command.textContent = id == null ? (destination ? "网页 · 去" + destination : "—")
    : title ? title + " · " + id : "ID " + id;
  get("voice-config").textContent = scene?.cloud_url
    ? "重建结果可查看。语音导航使用后端配置的路线。"
    : scene?.voice_navigation_configured
    ? "说「去蓝杯」触发导航 · ID " + status.blue_cup_id
    : status.blue_cup_id == null ? "待配置「去蓝杯」指令 ID。" : "当前语音指令使用另一份路线文件。";
  execution.textContent = stateLabels[status.state] || status.state;
  if (status.state === "stopped" && status.mode === "preview") execution.textContent = "预演已停止";
  const error = get("gateway-error");
  error.textContent = status.error || "";
  error.hidden = !status.error;
  updateDeparture();
  updateNavigationPosition();

  if (waitingForStop && id === 24 && (status.state === "stopped" || status.state === "error")) {
    waitingForStop = false;
    const feedback = get("stop-feedback");
    feedback.textContent = status.state === "error" ? "后端报告停车失败。" :
      status.mode === "live" ? "后端已完成停车请求。" : "预演停车已确认。";
  }
}

async function pollStatus() {
  try {
    renderStatus(await (await request("/status")).json());
  } catch {
    gatewayStatus = null;
    updateDeparture();
    connection.textContent = "后端未连接";
    connection.dataset.state = "offline";
    mode.hidden = true;
    execution.textContent = "状态不可用";
  } finally {
    setTimeout(pollStatus, 750);
  }
}

function updateDeparture() {
  const live = gatewayStatus?.mode === "live";
  const unavailable = live && activeTarget?.web_ready && !activeRoute?.live_ready;
  get("confirm-start").hidden = !live || unavailable;
  const explanation = get("departure-availability");
  explanation.hidden = !unavailable;
  explanation.textContent = "实车出发前需要标定此路线的转向和直行速度。";
  depart.textContent = live ? "出发" : "预演出发";
  depart.disabled = !gatewayStatus || !activeRoute || !activeTarget?.web_ready || unavailable || departing || ["running", "stopping"].includes(gatewayStatus.state)
    || (live && (!startConfirmed.checked || ["offline", "connecting"].includes(gatewayStatus.car_connection)));
}

function updateNavigationPosition() {
  const position = get("navigation-position");
  const navigation = scene?.web_navigation_configured ? gatewayStatus?.navigation : null;
  position.hidden = !navigation;
  if (!navigation) return;
  const { source } = navigation;
  const name = scene.targets.find(item => item.id === source)?.name;
  get("navigation-source").textContent = name ? "预计当前位置 · " + name + "的计划停车点"
    : source === "start" ? "当前位置 · 拍摄时起点" : "当前位置 · 待确认";
  get("route-note").textContent = name ? "这段路线从上一站的预计停车位置出发。"
    : "路线从拍摄时的起点出发。小车没有实时定位。";
  get("confirm-start-text").textContent = name ? "已核对小车位置、朝向和沿途空间"
    : "小车已摆回拍摄时的起点并对准车头";
  get("navigation-reset").disabled = ["running", "stopping"].includes(gatewayStatus.state);
}

get("navigation-reset").addEventListener("click", async () => {
  if (gatewayStatus?.mode === "live" && !window.confirm("确认小车已摆回拍摄时的起点，并对准原车头方向？")) return;
  const feedback = get("navigation-message");
  try {
    await request("/navigation/reset", { method: "POST", headers: { "X-Car-Navigate": "reset" } });
    renderStatus(await (await request("/status")).json());
    feedback.hidden = true;
  } catch (error) {
    feedback.textContent = error.message;
    feedback.hidden = false;
  }
});

startConfirmed.addEventListener("change", updateDeparture);
async function departForTarget() {
  if (!selected || !activeTarget?.web_ready || depart.disabled) return;
  const targetId = activeTarget.id;
  const revision = gatewayStatus.navigation?.revision;
  departing = true;
  startConfirmed.checked = false;
  updateDeparture();
  const feedback = get("depart-status");
  feedback.hidden = false;
  feedback.textContent = "正在提交路线…";
  try {
    await request("/navigate/" + targetId.replaceAll("_", "-"), {
      method: "POST", headers: { "X-Car-Navigate": targetId, "X-Car-Route-Revision": String(revision ?? "") },
    });
    feedback.textContent = gatewayStatus?.mode === "live" ? "路线已提交，查看执行状态。" : "预演已提交。";
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    departing = false;
    updateDeparture();
  }
}

depart.addEventListener("click", departForTarget);

stop.addEventListener("click", async () => {
  stop.disabled = true;
  const feedback = get("stop-feedback");
  feedback.textContent = "正在发送停车请求…";
  feedback.hidden = false;
  try {
    await request("/stop", { method: "POST" });
    waitingForStop = true;
    feedback.textContent = "后端已接收停车请求，等待执行结果。";
  } catch {
    waitingForStop = false;
    feedback.textContent = "停车请求发送失败，请检查后端连接。";
  } finally {
    stop.disabled = false;
  }
});

function updateReconstructionControls() {
  const busy = submitting || ["uploading", "running"].includes(currentJob?.state);
  reconstruct.disabled = busy || photos.length < 2;
  chooseFolder.disabled = busy;
  gsOption.disabled = !gsAvailable || busy;
}

function displayReconstructionStatus(message, error = false) {
  reconstructionStatus.hidden = !message;
  reconstructionStatus.classList.toggle("error", error);
  reconstructionStatus.textContent = message;
}

function renderJob(job) {
  const prior = currentJob;
  currentJob = job;
  if (job.gs_available !== undefined) {
    if (job.gs_available && !gsAvailable) gsOption.checked = true;
    gsAvailable = job.gs_available;
    get("gs-option-note").hidden = gsAvailable;
  }
  if (job.state === "completed") {
    const device = job.sift_backend === "cpu" ? "CPU" : "GPU";
    const summary = "重建完成 · " + device + " SIFT · " + job.registered + " / " + job.photos + " 张照片已注册";
    displayReconstructionStatus(job.gs_error || summary + (job.gs_url ? " · GS 已生成" : ""), Boolean(job.gs_error));
    if (latestResult?.id !== job.id) {
      latestResult = job;
      get("download-reconstruction").href = job.cloud_url;
      const downloadGs = get("download-gs");
      downloadGs.hidden = !job.gs_url;
      if (job.gs_url) downloadGs.href = job.gs_url;
      reconstructionResult.hidden = false;
      loadScene(job);
    }
  } else if (job.state === "failed") {
    displayReconstructionStatus(job.error, true);
  } else if (job.state !== "idle" && !submitting) {
    const device = job.stage !== "gaussians" && job.sift_backend
      ? (job.sift_backend === "cpu" ? " · CPU SIFT" : " · GPU SIFT") : "";
    displayReconstructionStatus(stages[job.stage || job.state] + device + "…");
  } else if (job.state === "idle" && prior && prior.state !== "idle") {
    displayReconstructionStatus("");
  }
  updateReconstructionControls();
}

async function pollReconstruction() {
  try {
    const job = await (await request("/reconstruction")).json();
    if (!submitting) renderJob(job);
  } catch {
    if (currentJob?.state === "running" || currentJob?.state === "uploading") {
      displayReconstructionStatus("暂时无法读取进度，正在重试。", true);
    }
  } finally {
    setTimeout(pollReconstruction, 900);
  }
}

chooseFolder.addEventListener("click", () => folderInput.click());
folderInput.addEventListener("change", () => {
  photos = Array.from(folderInput.files).filter(file => /\.(jpe?g|png)$/i.test(file.name));
  folderName = photos[0]?.webkitRelativePath.split("/")[0] || "本地照片";
  const bytes = photos.reduce((total, file) => total + file.size, 0);
  const selection = get("photo-selection");
  const inappropriate = photos.some(file => file.size > 20 * 1024 * 1024);
  if (photos.length < 2 || photos.length > 40 || bytes > 160 * 1024 * 1024 || inappropriate) {
    selection.textContent = "请选择 2–40 张 JPG 或 PNG。每张最多 20 MB，合计最多 160 MB。";
    photos = [];
  } else {
    selection.textContent = folderName + " · " + photos.length + " 张照片";
  }
  updateReconstructionControls();
});

reconstruct.addEventListener("click", async () => {
  const trainGs = gsOption.checked && gsAvailable;
  submitting = true;
  updateReconstructionControls();
  displayReconstructionStatus("正在发送照片…");
  const manifest = new TextEncoder().encode(JSON.stringify({
    name: folderName,
    train_gs: trainGs,
    photos: photos.map(file => ({ name: file.webkitRelativePath || file.name, size: file.size })),
  }));
  try {
    const response = await request("/reconstruction", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-Photo-Manifest-Bytes": String(manifest.byteLength) },
      body: new Blob([manifest, ...photos]),
    }, 120000);
    submitting = false;
    renderJob(await response.json());
  } catch (error) {
    displayReconstructionStatus(error.message, true);
  } finally {
    submitting = false;
    updateReconstructionControls();
  }
});

get("view-reconstruction").addEventListener("click", () => {
  if (latestResult) loadScene(latestResult);
});
get("view-navigation").addEventListener("click", () => loadScene());

loadScene();
pollStatus();
pollReconstruction();
