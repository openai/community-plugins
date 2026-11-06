import * as THREE from "three";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { toCreasedNormals } from "three/addons/utils/BufferGeometryUtils.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
const W = 1920,
  H = 1080,
  DURATION = 120;
const stage = document.querySelector("#stage"),
  copy = document.querySelector("#copy"),
  labels = document.querySelector("#labels"),
  note = document.querySelector("#note");
const renderer = new THREE.WebGLRenderer({
  antialias: true,
  alpha: true,
  preserveDrawingBuffer: true,
  powerPreference: "high-performance",
});
renderer.setSize(W, H);
renderer.setPixelRatio(1);
renderer.setClearColor(0x07111e, 0);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.95;
renderer.outputColorSpace = THREE.SRGBColorSpace;
stage.prepend(renderer.domElement);
const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
const camera = new THREE.OrthographicCamera(-100, 100, 100, -100, 1, 1400);
camera.up.set(0, 0, 1);
scene.add(new THREE.HemisphereLight(0xe0efff, 0x17283f, 0.65));
function light(color, intensity, pos) {
  let l = new THREE.DirectionalLight(color, intensity);
  l.position.set(...pos);
  scene.add(l);
  return l;
}
const key = light(0xe5efff, 2.2, [-90, -85, 180]);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, {
  left: -170,
  right: 170,
  top: 170,
  bottom: -170,
  near: 1,
  far: 500,
});
key.shadow.normalBias = 0.2;
key.shadow.bias = -0.0001;
key.shadow.radius = 4;
light(0x67aaff, 1.1, [120, 70, 130]);
light(0xffffff, 0.6, [0, 130, 70]);
const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(1600, 1600),
  new THREE.ShadowMaterial({ color: 0x020611, opacity: 0.5 }),
);
floor.position.z = -0.45;
floor.receiveShadow = true;
scene.add(floor);
const rings = new THREE.Group();
for (let r of [80, 105, 135]) {
  const pts = [];
  for (let i = 0; i <= 200; i++) {
    let a = (i / 200) * Math.PI * 2;
    pts.push(new THREE.Vector3(r * Math.cos(a), r * Math.sin(a), -0.3));
  }
  rings.add(
    new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({
        color: 0x749dcb,
        transparent: true,
        opacity: 0.09,
      }),
    ),
  );
}
scene.add(rings);
const mats = {
  tray: new THREE.MeshPhysicalMaterial({
    color: 0x246acb,
    metalness: 0.08,
    roughness: 0.43,
    clearcoat: 0.3,
    clearcoatRoughness: 0.35,
  }),
  bridge: new THREE.MeshPhysicalMaterial({
    color: 0x263a54,
    metalness: 0.2,
    roughness: 0.44,
    clearcoat: 0.2,
  }),
  battery: new THREE.MeshPhysicalMaterial({
    color: 0xffc267,
    metalness: 0.12,
    roughness: 0.35,
    transparent: true,
    opacity: 0.62,
    depthWrite: false,
  }),
  board: new THREE.MeshPhysicalMaterial({
    color: 0x14916e,
    metalness: 0.05,
    roughness: 0.32,
  }),
  clash: new THREE.MeshStandardMaterial({
    color: 0xff356c,
    emissive: 0xb00036,
    emissiveIntensity: 1.4,
    roughness: 0.3,
    transparent: true,
    opacity: 0.92,
  }),
};
const objects = {},
  loader = new STLLoader();
const manifest =
  window.__SHOWCASE__?.manifest ??
  (await fetch("/assets/manifest.json").then((r) => r.json()));
const files = {
  clashSection: "failed-b-intersection-section.stl",
  reproducedTray: "reproduced-tray.stl",
  reproducedBridge: "reproduced-bridge.stl",
  aTraySection: "a-tray-section.stl",
  bTraySection: "b-tray-section.stl",
  aBatterySection: "battery-a-section.stl",
  bBatterySection: "battery-b-section.stl",
  aTray: "a-tray.stl",
  aBridge: "a-bridge.stl",
  bTray: "b-tray.stl",
  bBridge: "b-bridge.stl",
  aBattery: "battery-a.stl",
  bBattery: "battery-b.stl",
  board: "board.stl",
  clash: "failed-b-intersection.stl",
};
await Promise.all(
  Object.entries(files).map(async ([name, path]) => {
    const encoded = window.__SHOWCASE__?.meshes?.[path];
    const raw = encoded
      ? loader.parse(
          Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0)).buffer,
        )
      : await loader.loadAsync("/assets/" + path);
    const g = toCreasedNormals(raw, Math.PI / 6);
    const mat = (
      /Tray/.test(name)
        ? mats.tray
        : /Bridge/.test(name)
          ? mats.bridge
          : /Battery/.test(name)
            ? mats.battery
            : name === "board"
              ? mats.board
              : mats.clash
    ).clone();
    const mesh = new THREE.Mesh(g, mat);
    mesh.castShadow = !/Battery|clash/.test(name);
    mesh.receiveShadow = true;
    mesh.name = name;
    // Section fills share a cut plane with the tray. Layer the transparent
    // battery fill explicitly, preserving every original mesh coordinate.
    if (name.endsWith("BatterySection")) {
      mesh.material.depthTest = false;
      mesh.renderOrder = 4;
    }
    objects[name] = mesh;
    scene.add(mesh);
  }),
);
const secondTray = new THREE.Mesh(
    objects.reproducedTray.geometry,
    mats.tray.clone(),
  ),
  secondBridge = new THREE.Mesh(
    objects.reproducedBridge.geometry,
    mats.bridge.clone(),
  );
secondTray.castShadow = secondBridge.castShadow = true;
scene.add(secondTray, secondBridge);
objects.secondTray = secondTray;
objects.secondBridge = secondBridge;
const dots = new THREE.Group();
scene.add(dots);
function marker(x, y, z, c) {
  const g = new THREE.Group();
  const m = new THREE.Mesh(
    new THREE.TorusGeometry(3, 0.27, 8, 40),
    new THREE.MeshBasicMaterial({ color: c, transparent: true, opacity: 0.95 }),
  );
  g.add(m);
  g.position.set(x, y, z);
  dots.add(g);
  return g;
}
const frameDots = [
  [-50, -30],
  [-50, 30],
  [50, -30],
  [50, 30],
].map(([x, y]) => marker(x, y, 3.4, 0x94d8ff));
const boardDots = [
  [-15, -15],
  [-15, 15],
  [15, -15],
  [15, 15],
].map(([x, y]) => marker(x, y, 33.4, 0x8ff4d7));
const clamp = (x) => Math.min(1, Math.max(0, x));
const ease = (x) => {
  x = clamp(x);
  return x * x * (3 - 2 * x);
};
const mix = (a, b, t) => a + (b - a) * t;
const scenes = [
  {
    start: 0,
    end: 9,
    chapter: 0,
    eyebrow: "A controlled engineering change",
    title: "A bigger battery.<br><em>The same interfaces.</em>",
    sub: "Change one requirement. Keep the engineering intent.",
    note: "Two printable parts. One traceable change.",
  },
  {
    start: 9,
    end: 23,
    chapter: 0,
    eyebrow: "01 / Understand the assembly",
    title: "Know what<br><em>can change.</em>",
    sub: "A battery tray supports the pack. A removable bridge carries the electronics.",
    pills: ["Battery + electronics", "2 printable parts"],
    note: "Battery tray. Electronics bridge. Clear, fixed interfaces.",
  },
  {
    start: 23,
    end: 36,
    chapter: 0,
    eyebrow: "The design contract",
    title: "Lock the<br><em>interfaces.</em>",
    sub: "The frame and board mounting patterns stay fixed through the revision.",
    stats: [
      ["Frame pitch", "100 × 60", "mm"],
      ["Board pitch", "30 × 30", "mm"],
    ],
    note: "Required battery clearance: 0.5–1.0 mm per side.",
  },
  {
    start: 36,
    end: 49,
    chapter: 1,
    eyebrow: "02 / Change one requirement",
    title: "Just <em>5 mm</em><br>wider.",
    sub: "The battery requirement changes from A: 35 mm to B: 40 mm. The existing tray stays fixed.",
    stats: [
      ["Battery A", "35", "mm"],
      ["Battery B", "40", "mm"],
    ],
    note: "Same 100 mm length and 25 mm height.",
  },
  {
    start: 49,
    end: 64,
    chapter: 2,
    eyebrow: "03 / Detect the real failure",
    title: 'The old tray<br><em style="color:#ff90ac">fails the fit.</em>',
    sub: "A 40 mm battery cannot fit inside a 36.5 mm cavity.",
    stats: [["Clearance / side", "−1.75", "mm", "bad"]],
    pills: ["Interference detected"],
    note: "The collision is detected before the design reaches handoff.",
  },
  {
    start: 64,
    end: 80,
    chapter: 3,
    eyebrow: "04 / Revise and measure",
    title: "Widen the tray.<br><em>Keep the mounts.</em>",
    sub: "Rebuild the cavity to 41.5 mm. The frame and board interfaces remain fixed.",
    stats: [["Clearance / side", "+0.75", "mm", "ok"]],
    pills: ["100 × 60 mm frame", "30 × 30 mm board"],
    note: "Mount positions and acceptance limits stay fixed.",
  },
  {
    start: 80,
    end: 93,
    chapter: 3,
    eyebrow: "Verify the delivered geometry",
    title: "Check the part.<br><em>Then the handoff.</em>",
    sub: "Fit, mounting bores, insertion path and export consistency all pass.",
    pills: ["STEP geometry", "STL consistency", "PDF callouts"],
    stats: [["Modeled polymer", "43.97", "g"]],
    note: "Fit and service path verified from the exported geometry.",
  },
  {
    start: 93,
    end: 109,
    chapter: 4,
    eyebrow: "05 / Deliver a verified handoff",
    title: "Ready to share.<br><em>Ready to build.</em>",
    sub: "Editable source, CAD, drawings and a bill of materials—checked together.",
    stats: [["Handoff verification", "PASS", "", "ok"]],
    pills: ["Source", "STEP / STL", "Drawing / BOM"],
    note: "The delivered source rebuilds to the same measured geometry.",
  },
  {
    start: 109,
    end: 120,
    chapter: 4,
    eyebrow: "Physical Engineering for Codex",
    title: "Let’s run it.<br><em>In Codex.</em>",
    sub: "Change the requirement. Verify the geometry. Deliver the handoff.",
    pills: ["Build", "Verify", "Hand off"],
    note: "Physical Engineering · Build → Verify → Hand off.",
  },
];
function html(s) {
  return `<div class="eyebrow">${s.eyebrow}</div><h1>${s.title}</h1><p class="sub">${s.sub}</p>${s.stats ? '<div class="stats">' + s.stats.map(([label, value, unit, c]) => `<div class="stat ${c || ""}"><label>${label}</label><strong>${value}</strong><small>${unit}</small></div>`).join("") + "</div>" : ""}${s.pills ? '<div class="pills">' + s.pills.map((p) => `<span class="pill ${p === "Interference detected" ? "bad" : ""}">${p}</span>`).join("") + "</div>" : ""}`;
}
let lines = [];
const project = (p) => {
  const v = new THREE.Vector3(...p).project(camera);
  return [((v.x + 1) * W) / 2, ((1 - v.y) * H) / 2];
};
function callout(p, text, dx, dy, color = "#bad4f1", size = 23) {
  const [x, y] = project(p),
    ex = x + dx,
    ey = y + dy;
  lines.push(
    `<path d="M${x},${y} L${ex},${ey} h${dx > 0 ? 18 : -18}" fill="none" stroke="${color}" stroke-width="1.5" opacity=".75"/><circle cx="${x}" cy="${y}" r="3.5" fill="${color}"/><text x="${ex + (dx > 0 ? 27 : -27)}" y="${ey + 7}" fill="${color}" font-size="${size}" text-anchor="${dx > 0 ? "start" : "end"}" font-weight="500">${text}</text>`,
  );
}
function dimension(a, b, text, offset = 35, color = "#a7cff5") {
  let [x1, y1] = project(a),
    [x2, y2] = project(b);
  let dx = x2 - x1,
    dy = y2 - y1,
    d = Math.hypot(dx, dy),
    nx = -dy / d,
    ny = dx / d;
  let ax = x1 + nx * offset,
    ay = y1 + ny * offset,
    bx = x2 + nx * offset,
    by = y2 + ny * offset;
  lines.push(
    `<g stroke="${color}" fill="none" stroke-width="1.5"><path d="M${x1},${y1} L${ax + nx * 7},${ay + ny * 7} M${x2},${y2} L${bx + nx * 7},${by + ny * 7} M${ax},${ay} L${bx},${by}"/><path d="M${ax + nx * 5 - (dx / d) * 5},${ay + ny * 5 - (dy / d) * 5} L${ax - nx * 5 + (dx / d) * 5},${ay - ny * 5 + (dy / d) * 5} M${bx + nx * 5 - (dx / d) * 5},${by + ny * 5 - (dy / d) * 5} L${bx - nx * 5 + (dx / d) * 5},${by - ny * 5 + (dy / d) * 5}"/></g><text x="${(ax + bx) / 2 + nx * 20}" y="${(ay + by) / 2 + ny * 20}" text-anchor="middle" font-size="24" font-weight="550" fill="${color}" paint-order="stroke" stroke="#101e32" stroke-width="8" stroke-linejoin="round">${text}</text>`,
  );
}
function opacity(m, o) {
  m.visible = o > 0.001;
  if (m.material.transparent !== o < 1) {
    m.material.transparent = o < 1;
    m.material.needsUpdate = true;
  }
  m.material.opacity = o;
  m.material.depthWrite = o > 0.9;
}
let current = -1;
function update(t) {
  t = Math.max(0, Math.min(t, DURATION - 0.001));
  const index = scenes.findIndex((s) => t < s.end),
    s = scenes[index],
    u = (t - s.start) / (s.end - s.start);
  if (current !== index) {
    copy.innerHTML = html(s);
    note.textContent = s.note;
    document
      .querySelectorAll(".chapter")
      .forEach(
        (e, i) =>
          (e.className =
            "chapter " +
            (i === s.chapter ? "active" : i < s.chapter ? "done" : "")),
      );
    current = index;
  }
  const fade = Math.min(
    ease((t - s.start) / 0.7),
    index === scenes.length - 1 ? 1 : ease((s.end - t) / 0.45),
  );
  copy.style.opacity = fade;
  note.style.opacity = fade;
  document.querySelector("#progress").style.width = (t / DURATION) * W + "px";
  lines = [];
  for (const m of Object.values(objects)) {
    m.visible = false;
    m.position.set(0, 0, 0);
    m.material.color.copy(
      (/Tray/.test(m.name)
        ? mats.tray
        : /Bridge/.test(m.name)
          ? mats.bridge
          : /Battery/.test(m.name)
            ? mats.battery
            : m.name === "board"
              ? mats.board
              : mats.clash
      ).color,
    );
  }
  dots.visible = false;
  frameDots.forEach((m) => (m.visible = true));
  boardDots.forEach((m) => (m.visible = true));
  secondTray.material.color.copy(mats.tray.color);
  secondBridge.material.color.copy(mats.bridge.color);
  let az = -0.87,
    el = 0.65,
    scale = 76,
    targetZ = 19,
    pan = 54;
  let a = false,
    b = false,
    batA = 0,
    batB = 0,
    bridgeOpacity = 1,
    boardOpacity = 0,
    explode = 0;
  if (index === 0) {
    b = true;
    batB = 0;
    boardOpacity = 0;
    az = -1.04 + u * 0.32;
    el = 0.62;
    scale = 74;
  }
  if (index === 1) {
    a = true;
    batA = 0.48;
    boardOpacity = 1;
    explode = ease(Math.min(u * 2, 1)) * 33;
    az = -0.91 + u * 0.24;
    el = 0.54;
    scale = 90;
    targetZ = 41;
  }
  if (index === 2) {
    a = true;
    boardOpacity = 0.45;
    az = -Math.PI / 2;
    el = mix(0.7, 1.5, ease(u * 2));
    scale = 77;
    targetZ = 13;
    dots.visible = true;
  }
  if (index === 3) {
    az = 0;
    el = 0.03;
    scale = 40;
    targetZ = 15;
    pan = 29;
    opacity(objects.aTraySection, 1);
    opacity(objects.aBatterySection, (1 - ease((u - 0.15) / 0.3)) * 0.8);
    opacity(objects.bBatterySection, ease((u - 0.25) / 0.3) * 0.76);
  }
  if (index === 4) {
    az = 0;
    el = 0.03;
    scale = 40;
    targetZ = 15;
    pan = 29;
    opacity(objects.aTraySection, 1);
    opacity(objects.bBatterySection, 0.68);
    opacity(objects.clashSection, 0.85);
    objects.clashSection.renderOrder = 8;
    objects.clashSection.material.depthTest = false;
  }
  if (index === 5) {
    if (u < 0.5) {
      az = 0;
      el = 0.03;
      scale = 40;
      targetZ = 15;
      pan = 29;
      opacity(objects.bTraySection, 1);
      opacity(objects.bBatterySection, 0.68);
    } else {
      b = true;
      batB = 0.36;
      boardOpacity = 0.3;
      az = -0.4 - (u - 0.5) * 0.4;
      el = 0.75;
      scale = 76;
      targetZ = 14;
      dots.visible = true;
      boardDots.forEach((m) => (m.visible = false));
    }
  }
  if (index === 6) {
    b = true;
    batB = 0.45;
    boardOpacity = 1;
    az = -0.85;
    el = 0.58;
    scale = 85;
    targetZ = 18;
    objects.bBattery.position.x = -125 * (1 - ease(u * 1.8));
  }
  if (index === 7) {
    b = true;
    scale = 145;
    targetZ = 17;
    pan = 98;
    az = -Math.PI / 2;
    el = 0.77;
    objects.bTray.position.x = -69;
    objects.bBridge.position.x = -69;
    secondTray.position.x = 69;
    secondBridge.position.x = 69;
    opacity(secondTray, ease(u * 3));
    opacity(secondBridge, ease(u * 3));
  }
  if (index === 8) {
    b = true;
    batB = 0;
    boardOpacity = 0;
    az = -0.87 + u * 0.36;
    scale = 74;
    targetZ = 19;
  }
  if (a) {
    opacity(objects.aTray, 1);
    opacity(objects.aBridge, bridgeOpacity);
  }
  if (b) {
    opacity(objects.bTray, 1);
    opacity(objects.bBridge, bridgeOpacity);
  }
  opacity(objects.aBattery, batA);
  opacity(objects.bBattery, batB);
  opacity(objects.board, boardOpacity);
  if (index === 1) {
    objects.aBridge.position.z = explode;
    objects.board.position.z = explode * 1.95;
    objects.aBattery.position.z = explode * 0.38;
  }
  camera.left = (-W / H) * scale - pan;
  camera.right = (W / H) * scale - pan;
  camera.top = scale;
  camera.bottom = -scale;
  camera.position.set(
    Math.cos(az) * 250 * Math.cos(el),
    Math.sin(az) * 250 * Math.cos(el),
    targetZ + 250 * Math.sin(el),
  );
  camera.lookAt(0, 0, targetZ);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  if (index === 1 && u > 0.15) {
    callout([-45, -20, 3], "BATTERY TRAY", -30, 65, "#8fc5ff");
    callout([20, 22, 31 + explode], "ELECTRONICS BRIDGE", 90, 5, "#c2d2e6");
    callout([0, -17.5, 15.5 + explode * 0.38], "BATTERY", -90, -24, "#ffd398");
    callout([12, 18, 34 + explode * 1.95], "BOARD", 75, -36, "#93edcf");
  }
  if (index === 2) {
    dimension([-50, -30, 3.5], [50, -30, 3.5], "100 mm", 42);
    dimension([50, -30, 3.5], [50, 30, 3.5], "60 mm", 48);
    if (u > 0.5)
      dimension([-15, 15, 33.5], [15, 15, 33.5], "30 mm", -35, "#8be8cc");
  }
  if (index === 3) {
    dimension(
      [1, -20, 28],
      [1, 20, 28],
      u < 0.4 ? "35 → 40 mm battery" : "40 mm battery",
      -55,
      "#ffd398",
    );
    dimension([1, -18.25, 3], [1, 18.25, 3], "36.5 mm cavity", 65);
    callout([1, 0, 16], "BATTERY", 0, 0, "#ffd398", 22);
  }
  if (index === 4) {
    dimension([1, -20, 28], [1, 20, 28], "40 mm battery", -55, "#ffd398");
    dimension([1, -18.25, 3], [1, 18.25, 3], "36.5 mm cavity", 65);
    callout([1, -19, 7], "INTERFERENCE", 100, -45, "#ff91af", 22);
  }
  if (index === 5) {
    if (u < 0.5) {
      dimension([1, -20, 28], [1, 20, 28], "40 mm battery", -55, "#ffd398");
      dimension([1, -20.75, 3], [1, 20.75, 3], "41.5 mm cavity", 65, "#8febc7");
      callout([1, -20.375, 7], "0.75 mm CLEARANCE", 90, -45, "#8febc7", 22);
    } else {
      dimension(
        [-49, -20.75, 11],
        [-49, 20.75, 11],
        "41.5 mm cavity",
        40,
        "#8febc7",
      );
      callout([50, -30, 3], "MOUNT CENTERS UNCHANGED", 35, 75, "#a6d7ff", 20);
    }
  }
  if (index === 6 && u > 0.6) {
    callout([40, -20.3, 11], "0.75 mm / SIDE", 60, 60, "#8fe8c7");
  }
  if (index === 7) {
    const p = project([-69, 0, 0]),
      q = project([69, 0, 0]);
    lines.push(
      `<text x="${p[0]}" y="${p[1] + 178}" text-anchor="middle" fill="#a5c3e4" font-size="24">DESIGN</text><text x="${q[0]}" y="${q[1] + 178}" text-anchor="middle" fill="#87e9c1" font-size="24">VERIFIED BUILD</text>`,
    );
  }
  if ([3, 4].includes(index) || (index === 5 && u < 0.5)) {
    lines.push(
      '<text x="1350" y="855" text-anchor="middle" fill="#8ea7c3" font-size="19" letter-spacing="1.5">CENTRAL CAD SECTION · X = 0</text>',
    );
  }
  labels.innerHTML = lines.join("");
  labels.style.opacity = fade;
  renderer.render(scene, camera);
  window.currentTime = t;
  document.querySelector("#seek").value = t;
  document.querySelector("#time").textContent =
    `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")} / 2:00`;
  if (window.captions) {
    const cue = window.captions.find((c) => c.start <= t && t < c.end);
    document.querySelector("#caption").innerHTML = cue
      ? `<span>${cue.text}</span>`
      : "";
  }
  return {
    time: t,
    scene: index,
    visible: Object.values(objects)
      .filter((m) => m.visible)
      .map((m) => m.name),
    camera: { az, el, scale },
  };
}
window.seekShowcase = update;
window.showcaseDuration = DURATION;
window.showcaseManifest = manifest;
window.showcaseScenes = scenes;
window.ready = true;
update(0.7);
let running = false,
  base = 0,
  started = 0;
const audio = document.querySelector("#audio");
if (window.__SHOWCASE__?.audio)
  audio.src = "data:audio/wav;base64," + window.__SHOWCASE__.audio;
function pause() {
  if (running) {
    base = Math.min(DURATION, base + (performance.now() - started) / 1000);
    running = false;
  }
  audio.pause();
  document.querySelector("#play").textContent = "Play";
}
async function play() {
  if (base >= DURATION - 0.01) base = 0;
  running = true;
  started = performance.now();
  audio.currentTime = base;
  audio.play().catch(() => {});
  document.querySelector("#play").textContent = "Pause";
  requestAnimationFrame(tick);
}
function tick() {
  if (!running) return;
  const t = base + (performance.now() - started) / 1000;
  update(t);
  if (t >= DURATION) {
    pause();
    base = DURATION;
    return;
  }
  requestAnimationFrame(tick);
}
document.querySelector("#play").onclick = () => (running ? pause() : play());
document.querySelector("#seek").oninput = (e) => {
  pause();
  base = Number(e.target.value);
  update(base);
  audio.currentTime = base;
};
document.querySelector("#restart").onclick = () => {
  pause();
  base = 0;
  play();
};
document.querySelector("#cc").onchange = (e) => {
  document.querySelector("#caption").style.display = e.target.checked
    ? "block"
    : "none";
  note.style.visibility = e.target.checked ? "hidden" : "visible";
};
window.addEventListener("keydown", (e) => {
  if (e.code === "Space" && e.target.tagName !== "INPUT") {
    e.preventDefault();
    running ? pause() : play();
  }
});
function resize() {
  stage.style.transform = `scale(${Math.min(innerWidth / W, innerHeight / H)})`;
}
window.addEventListener("resize", resize);
resize();
if (window.__SHOWCASE__?.captions)
  window.captions = window.__SHOWCASE__.captions;
else
  fetch("/captions.json")
    .then((r) => (r.ok ? r.json() : null))
    .then((c) => (window.captions = c))
    .catch(() => {});
