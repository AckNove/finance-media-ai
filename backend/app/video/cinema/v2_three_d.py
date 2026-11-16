"""写实三维场景：黄昏的变电站/开关场（原创，非实拍、非 AI 生成影像）。

为什么不用上一版的"发光线条塔"：那种东西只能表达"这里有输电"，表达不了
"设备之间怎么连、电从哪进从哪出"。这一版按真实开关场的构成来搭：

    地面（碎石混凝土） → 设备基础（混凝土墩） → 变压器本体 + 油枕 + 绝缘套管
    → 母线（管型） → 门型构架 → 避雷器/隔离开关 → 照明灯杆 → 远景输电铁塔群

材质与光照都按"看得见的东西"来配：金属 0.35 度金属度 + 0.55 粗糙度，混凝土近全漫反射，
黄昏低角度主光 + 半球环境光 + 一点冷色补光；加指数雾把远景压进空气里。

所有运动都是 t 的纯函数（摄影机沿轨道推进 + 轻微上摇），逐帧渲染结果可复现。

用法：
    python -m backend.scripts.build_v2_three_d
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 本文件在 backend/app/video/cinema/ 下：parents[0]=cinema [1]=video [2]=app [3]=backend [4]=仓库根
PROJECT_ROOT = Path(__file__).resolve().parents[4]
JOB = PROJECT_ROOT / "content" / "jobs" / "v6_power_first_film"
REM = JOB / "remaster_v2"
SCENES = REM / "production" / "scenes"
THREE_JS = PROJECT_ROOT / "tools" / "vendor" / "three.min.js"
FONT_SRC = "fonts/NotoSansSC-VF.ttf"
FPS = 30


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @font-face {{ font-family: "AKSC"; src: url("{font}") format("truetype"); font-display: block; }}
  html, body {{ margin: 0; padding: 0; background: #070b12; overflow: hidden; }}
  #stage {{ position: relative; width: {width}px; height: {height}px; overflow: hidden;
            font-family: "AKSC", "Microsoft YaHei", sans-serif; }}
  canvas {{ position: absolute; inset: 0; }}
  #overlay {{ position: absolute; inset: 0; pointer-events: none;
              background: linear-gradient(0deg, rgba(4,8,14,0.92) 0%, rgba(4,8,14,0.35) 26%, rgba(4,8,14,0) 46%); }}
  .ak-text {{ position: absolute; left: 72px; right: 72px; color: #f2f5f7; line-height: 1.22;
              white-space: pre-wrap; text-shadow: 0 2px 18px rgba(0,0,0,0.9); will-change: opacity; }}
  #badge {{ position: absolute; left: 40px; top: 40px; padding: 8px 16px; font-size: 24px;
            letter-spacing: 2px; color: rgba(242,245,247,0.85); border: 1px solid rgba(242,245,247,0.3);
            border-radius: 4px; background: rgba(4,8,14,0.45); }}
  #credit {{ position: absolute; left: 72px; bottom: 26px; font-size: 20px;
             color: rgba(242,245,247,0.45); letter-spacing: 0.4px; }}
</style>
</head>
<body>
<div id="stage">
  <div id="overlay"></div>
  {text_nodes}
  <div id="badge">原创三维 · 非实拍</div>
  <div id="credit">AK 视频生产系统 · 程序化建模</div>
</div>
<script>{three}</script>
<script>
const SCENE = {scene_json};

/* ---------- 程序化贴图：碎石地面 / 混凝土 / 金属漆面 ---------- */
function noiseTexture(size, base, spread, grain) {{
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, size, size);
  const image = ctx.getImageData(0, 0, size, size);
  for (let i = 0; i < image.data.length; i += 4) {{
    const n = (Math.random() - 0.5) * spread;
    image.data[i] = Math.max(0, Math.min(255, image.data[i] + n));
    image.data[i + 1] = Math.max(0, Math.min(255, image.data[i + 1] + n));
    image.data[i + 2] = Math.max(0, Math.min(255, image.data[i + 2] + n));
  }}
  ctx.putImageData(image, 0, 0);
  for (let i = 0; i < grain; i += 1) {{
    const x = Math.random() * size;
    const y = Math.random() * size;
    const r = Math.random() * 2.2 + 0.4;
    ctx.fillStyle = `rgba(255,255,255,${{0.02 + Math.random() * 0.05}})`;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }}
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  return texture;
}}

const renderer = new THREE.WebGLRenderer({{ antialias: true, alpha: false }});
renderer.setSize(SCENE.width, SCENE.height, false);
renderer.setPixelRatio(1);
renderer.outputEncoding = THREE.sRGBEncoding;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.02;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.setClearColor(0x0a1119, 1);
document.getElementById("stage").insertBefore(renderer.domElement, document.getElementById("overlay"));

const scene3 = new THREE.Scene();
scene3.fog = new THREE.FogExp2(0x263444, 0.028);

/* ---------- 黄昏天空：竖向渐变贴图当背景（比纯黑底强得多） ---------- */
function skyTexture() {{
  const canvas = document.createElement("canvas");
  canvas.width = 8;
  canvas.height = 512;
  const ctx = canvas.getContext("2d");
  const grad = ctx.createLinearGradient(0, 0, 0, 512);
  grad.addColorStop(0.00, "#0b1521");
  grad.addColorStop(0.42, "#1b3247");
  grad.addColorStop(0.68, "#3d5568");
  grad.addColorStop(0.86, "#7a6a53");
  grad.addColorStop(1.00, "#3a3128");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 8, 512);
  const texture = new THREE.CanvasTexture(canvas);
  texture.encoding = THREE.sRGBEncoding;
  return texture;
}}
scene3.background = skyTexture();


const camera = new THREE.PerspectiveCamera(44, SCENE.width / SCENE.height, 0.1, 400);

/* ---------- 光照：黄昏低角度主光 + 半球环境 + 冷色补光 ---------- */
const sun = new THREE.DirectionalLight(0xffc98a, 1.2);
sun.position.set(-22, 9, -16);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.near = 1;
sun.shadow.camera.far = 120;
sun.shadow.camera.left = -40;
sun.shadow.camera.right = 40;
sun.shadow.camera.top = 40;
sun.shadow.camera.bottom = -40;
scene3.add(sun);

const hemi = new THREE.HemisphereLight(0x8fb6d8, 0x1a232e, 0.5);
scene3.add(hemi);

const fill = new THREE.DirectionalLight(0x5f8fb8, 0.32);
fill.position.set(18, 6, 20);
scene3.add(fill);

/* ---------- 地面 ---------- */
const groundTex = noiseTexture(512, "#1d2229", 20, 2600);
groundTex.repeat.set(26, 26);
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(220, 220),
  new THREE.MeshStandardMaterial({{ map: groundTex, roughness: 0.93, metalness: 0.04, color: 0x7b838b }})
);
ground.rotation.x = -Math.PI / 2;
ground.receiveShadow = true;
scene3.add(ground);

/* ---------- 材质 ---------- */
const concreteTex = noiseTexture(256, "#585f66", 34, 900);
concreteTex.repeat.set(3, 3);
const MAT_CONCRETE = new THREE.MeshStandardMaterial({{ map: concreteTex, roughness: 0.95, metalness: 0.02, color: 0x8b9299 }});
const MAT_STEEL = new THREE.MeshStandardMaterial({{ color: 0x5a656f, roughness: 0.6, metalness: 0.4 }});
const MAT_TANK = new THREE.MeshStandardMaterial({{ map: concreteTex, color: 0x333d47, roughness: 0.72, metalness: 0.22 }});
const MAT_CERAMIC = new THREE.MeshStandardMaterial({{ color: 0x9ba6ad, roughness: 0.42, metalness: 0.04 }});
const MAT_BUS = new THREE.MeshStandardMaterial({{ color: 0x8f9aa3, roughness: 0.42, metalness: 0.6 }});

function add(mesh, {{ cast = true, receive = true }} = {{}}) {{
  mesh.castShadow = cast;
  mesh.receiveShadow = receive;
  scene3.add(mesh);
  return mesh;
}}

function plinth(x, z, w = 3.2, d = 2.6, h = 0.5) {{
  const slab = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), MAT_CONCRETE);
  slab.position.set(x, h / 2, z);
  return add(slab);
}}

/* 变压器：本体 + 油枕 + 散热片 + 套管 */
function transformer(x, z, scale = 1) {{
  plinth(x, z, 4.6 * scale, 3.6 * scale, 0.45);
  const body = new THREE.Mesh(new THREE.BoxGeometry(3.4 * scale, 2.6 * scale, 2.4 * scale), MAT_TANK);
  body.position.set(x, 0.45 + 1.3 * scale, z);
  add(body);

  const conservator = new THREE.Mesh(new THREE.CylinderGeometry(0.42 * scale, 0.42 * scale, 3.0 * scale, 20), MAT_TANK);
  conservator.rotation.z = Math.PI / 2;
  conservator.position.set(x, 0.45 + 2.75 * scale, z - 0.9 * scale);
  add(conservator);

  for (let i = 0; i < 9; i += 1) {{
    const fin = new THREE.Mesh(new THREE.BoxGeometry(0.06 * scale, 1.7 * scale, 1.1 * scale), MAT_STEEL);
    fin.position.set(x - 1.55 * scale, 0.45 + 1.35 * scale, z - 0.9 * scale + i * 0.22 * scale);
    add(fin);
  }}

  for (let i = 0; i < 3; i += 1) {{
    const bushing = new THREE.Group();
    for (let s = 0; s < 7; s += 1) {{
      const r = (0.26 - s * 0.022) * scale;
      const shed = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 1.12, 0.16 * scale, 14), MAT_CERAMIC);
      shed.position.y = 0.45 + 2.6 * scale + s * 0.17 * scale;
      shed.castShadow = true;
      bushing.add(shed);
    }}
    bushing.position.set(x - 0.9 * scale + i * 0.9 * scale, 0, z + 0.6 * scale);
    scene3.add(bushing);
  }}
  return {{ x, z, scale }};
}}

/* 门型构架：两根立柱 + 横梁 + 悬垂绝缘子串 */
function gantry(x, z, width = 9, height = 8) {{
  for (const side of [-1, 1]) {{
    const leg = new THREE.Mesh(new THREE.BoxGeometry(0.36, height, 0.36), MAT_STEEL);
    leg.position.set(x + side * width / 2, height / 2, z);
    add(leg);
  }}
  const beam = new THREE.Mesh(new THREE.BoxGeometry(width + 0.6, 0.42, 0.42), MAT_STEEL);
  beam.position.set(x, height, z);
  add(beam);
  for (const offset of [-width / 3, 0, width / 3]) {{
    for (let s = 0; s < 6; s += 1) {{
      const disc = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.18, 0.12, 12), MAT_CERAMIC);
      disc.position.set(x + offset, height - 0.45 - s * 0.16, z);
      add(disc);
    }}
  }}
}}

/* 管型母线：沿 z 方向拉过去，架在支柱上 */
function busRun(x, z0, z1, y = 5.2) {{
  const length = Math.abs(z1 - z0);
  const tube = new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.13, length, 14), MAT_BUS);
  tube.rotation.x = Math.PI / 2;
  tube.position.set(x, y, (z0 + z1) / 2);
  add(tube);
  for (let z = Math.min(z0, z1) + 3; z < Math.max(z0, z1); z += 6) {{
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.26, y, 12), MAT_CERAMIC);
    post.position.set(x, y / 2, z);
    add(post);
  }}
}}

/* 避雷器：细长柱 + 伞裙 */
function arrester(x, z) {{
  plinth(x, z, 1.4, 1.4, 0.35);
  for (let s = 0; s < 12; s += 1) {{
    const disc = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.26, 0.14, 12), MAT_CERAMIC);
    disc.position.set(x, 0.35 + 0.2 + s * 0.19, z);
    add(disc);
  }}
}}

/* 灯杆 + 投光灯（暖光，给画面加一点人气） */
function floodlight(x, z, height = 12) {{
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.22, height, 12), MAT_STEEL);
  pole.position.set(x, height / 2, z);
  add(pole);
  const head = new THREE.Mesh(
    new THREE.BoxGeometry(1.5, 0.3, 0.7),
    new THREE.MeshStandardMaterial({{ color: 0x2a3138, roughness: 0.6, metalness: 0.4, emissive: 0xffe3b0, emissiveIntensity: 0.6 }})
  );
  head.position.set(x, height, z);
  add(head);
  const lamp = new THREE.PointLight(0xffd8a0, 9, 34, 2);
  lamp.position.set(x, height - 0.4, z);
  scene3.add(lamp);
}}

/* 远景铁塔群（几何骨架，靠雾压远，不是主角） */
function towerFar(x, z, height = 11) {{
  const group = new THREE.Group();
  for (const side of [-1, 1]) {{
    for (const tilt of [-0.55, 0.55]) {{
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.2, height, 8), MAT_STEEL);
      leg.position.set(side * 1.1 + tilt * 0.5, height / 2, tilt * 0.6);
      leg.rotation.z = side * 0.055;
      leg.castShadow = true;
      group.add(leg);
    }}
  }}
  for (const y of [height * 0.45, height * 0.66, height * 0.86]) {{
    const arm = new THREE.Mesh(new THREE.BoxGeometry(5.4, 0.16, 0.16), MAT_STEEL);
    arm.position.y = y;
    arm.castShadow = true;
    group.add(arm);
  }}
  group.position.set(x, 0, z);
  scene3.add(group);
}}

/* ---------- 场景布置 ---------- */
transformer(-7, -2, 1.0);
transformer(1.5, -6, 0.92);
transformer(11, -3, 1.05);
gantry(-1, -14, 16, 9);
gantry(12, -18, 12, 8);
busRun(-7, -6, -18, 6.4);
busRun(11, -7, -22, 6.0);
arrester(-12, -4);
arrester(-3.5, -9);
arrester(7, -9);
floodlight(-15, -12, 13);
floodlight(-21, 15, 11);
floodlight(23, 15, 11);
floodlight(17, -10, 12);
for (let i = 0; i < 6; i += 1) {{
  towerFar(-42 + i * 17, -46 - (i % 2) * 7, 12 + (i % 3));
}}

/* ---------- 摄影机轨迹（纯 t 的函数） ---------- */
const path = SCENE.camera;
function cameraAt(t) {{
  const k = SCENE.duration > 0 ? Math.min(1, Math.max(0, t / SCENE.duration)) : 0;
  const smooth = k * k * (3 - 2 * k);
  const p0 = path.from;
  const p1 = path.to;
  camera.position.set(
    p0[0] + (p1[0] - p0[0]) * smooth,
    p0[1] + (p1[1] - p0[1]) * smooth,
    p0[2] + (p1[2] - p0[2]) * smooth
  );
  const l0 = path.lookFrom;
  const l1 = path.lookTo;
  camera.lookAt(
    l0[0] + (l1[0] - l0[0]) * smooth,
    l0[1] + (l1[1] - l0[1]) * smooth,
    l0[2] + (l1[2] - l0[2]) * smooth
  );
}}

const textNodes = Array.from(document.querySelectorAll(".ak-text"));
function seek(t) {{
  cameraAt(t);
  renderer.render(scene3, camera);
  for (const node of textNodes) {{
    const at = Number(node.dataset.at || 0);
    const p = Math.min(1, Math.max(0, (t - at) / 0.5));
    node.style.opacity = String(p);
  }}
}}

window.__AK = {{ fps: SCENE.fps, duration: SCENE.duration, width: SCENE.width, height: SCENE.height }};
window.__seek = seek;
seek(0);
window.__ready = true;
</script>
</body>
</html>
"""


def _texts(items: tuple[tuple[str, str, float, int], ...], height: int) -> str:
    nodes = []
    for role, text, y, size in items:
        nodes.append(
            f'<div class="ak-text" data-at="0.35" style="top:{y * height:.0f}px;'
            f'font-size:{size}px;opacity:0">{text}</div>'
        )
    return "\n  ".join(nodes)


def build_substation_scene(
    *,
    scene_id: str = "C1_substation",
    duration: float = 6.4,
    width: int = 1080,
    height: int = 1920,
    texts: tuple[tuple[str, str, float, int], ...] = (
        ("kicker", "原创三维 · 变电站空间关系", 0.60, 30),
        ("title", "利润在降，\n说明这是存量再分配", 0.655, 60),
    ),
) -> dict:
    scene_dir = SCENES / scene_id
    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / "fonts").mkdir(parents=True, exist_ok=True)
    font_src = JOB / "fonts" / "NotoSansSC-VF.ttf"
    target_font = scene_dir / FONT_SRC
    if font_src.is_file() and not target_font.exists():
        target_font.write_bytes(font_src.read_bytes())

    three_code = THREE_JS.read_text(encoding="utf-8", errors="ignore")
    html = PAGE.format(
        title=scene_id,
        font=FONT_SRC,
        width=width,
        height=height,
        three=three_code,
        text_nodes=_texts(texts, height),
        scene_json=json.dumps(
            {
                "fps": FPS,
                "duration": duration,
                "width": width,
                "height": height,
                "camera": {
                    "from": [20.0, 3.8, 19.0],
                    "to": [12.5, 4.6, 8.5],
                    "lookFrom": [-4.0, 3.2, -16.0],
                    "lookTo": [-9.0, 3.8, -24.0],
                },
            },
            ensure_ascii=False,
        ),
    )
    (scene_dir / "index.html").write_text(html, encoding="utf-8", newline="\n")
    return {
        "scene": scene_id,
        "dir": str(scene_dir),
        "duration": duration,
        "frames": int(round(duration * FPS)),
        "engine": "cinema-html-procedural-3d",
        "asset_note": "原创程序化建模，非实拍、非 AI 生成影像",
    }


def main() -> int:
    receipt = build_substation_scene()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
