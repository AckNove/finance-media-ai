/* AK cinema-html 引擎运行时。
 *
 * 一张镜头 = 一份"舞台说明书"（window.__AK.scene）+ 这个解释器。
 * 说明书里只有数据：物体、文字、摄影机关键帧、事件时刻；解释器负责把它们变成画面。
 *
 * 为什么这么设计：视觉导演改画面时只改 JSON，不碰渲染代码；换主题（电力/储能/光伏）
 * 换的是同一套原语的不同摆法，不需要为每条片子再写一遍 three.js。这正是"视觉制作
 * 蓝图能驱动渲染"的证据——蓝图里的 camera / visual_events / assets 都会落到这里。
 *
 * 三条硬约束：
 *   1. 一切运动都由 render(t) 算出来，不用 CSS 动画、不用 requestAnimationFrame 补间；
 *      同一个 t 必须渲出同一张图（否则逐帧渲染和倒放核对都会崩）。
 *   2. 未知物体类型直接抛错，不静默降级——悄悄少画一个物体比报错更难查。
 *   3. 文字走 DOM 层，不烤进贴图：中文在小字号下烤图会糊。
 */
(function () {
  "use strict";

  const THREE = window.THREE;
  const scene = { objects: new Map(), texts: new Map(), panels: new Map(), camera: [], spec: null, ctx: null };

  // ------------------------------------------------------------------ 工具

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  /** 0→1 的平滑过渡；t 不在区间内时取端点值。 */
  function ramp(t, start, end) {
    if (end <= start) return t >= end ? 1 : 0;
    const k = clamp((t - start) / (end - start), 0, 1);
    return k * k * (3 - 2 * k);
  }

  /** 带一点回弹的出现曲线，避免"硬弹出"的廉价感。 */
  function easeOutBack(k) {
    const c1 = 1.70158;
    const c3 = c1 + 1;
    const x = clamp(k, 0, 1);
    return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
  }

  function hexToColor(hex, fallback) {
    try {
      return new THREE.Color(hex || fallback);
    } catch (error) {
      return new THREE.Color(fallback);
    }
  }

  function material(color, opts) {
    const options = opts || {};
    return new THREE.MeshStandardMaterial({
      color: hexToColor(color, "#ffffff"),
      emissive: hexToColor(options.emissive || color, "#000000"),
      emissiveIntensity: options.emissiveIntensity === undefined ? 0.15 : options.emissiveIntensity,
      metalness: options.metalness === undefined ? 0.35 : options.metalness,
      roughness: options.roughness === undefined ? 0.55 : options.roughness,
      transparent: true,
      opacity: 1,
    });
  }

  function box(w, h, d, color, opts) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material(color, opts));
    mesh.castShadow = false;
    return mesh;
  }

  const _up = new THREE.Vector3(0, 1, 0);

  /** 两点之间的一根杆：输电塔的腿、横担、斜撑都用它，比摆盒子像。 */
  function strut(from, to, radius, color, opts) {
    const start = new THREE.Vector3(from[0], from[1], from[2]);
    const end = new THREE.Vector3(to[0], to[1], to[2]);
    const direction = new THREE.Vector3().subVectors(end, start);
    const length = direction.length();
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, 8), material(color, opts));
    mesh.position.copy(start).addScaledVector(direction, 0.5);
    mesh.quaternion.setFromUnitVectors(_up, direction.normalize());
    return mesh;
  }

  // ------------------------------------------------------------------ 原语

  const builders = {
    grid(spec) {
      const group = new THREE.Group();
      const size = spec.size || 90;
      const div = spec.div || 90;
      const grid = new THREE.GridHelper(size, div, hexToColor(spec.color, "#1b3557"), hexToColor(spec.color2 || spec.color, "#0f1f33"));
      grid.material.transparent = true;
      grid.material.opacity = spec.opacity === undefined ? 0.9 : spec.opacity;
      group.add(grid);
      return group;
    },

    /** 输电塔：四条向上收拢的腿 + 三层横担 + 斜撑。比一排盒子像塔，也比建模省事。 */
    pylon(spec) {
      const group = new THREE.Group();
      const h = spec.height || 6.0;
      const spread = spec.base || 0.9;
      const color = spec.color || "#243447";
      const radius = (spec.thickness || 1) * 0.038;
      const opts = {
        emissive: spec.glow || "#1d3f57",
        emissiveIntensity: spec.glowStrength === undefined ? 0.22 : spec.glowStrength,
        metalness: 0.0,
        roughness: 0.85,
      };
      const corners = [
        [1, 1],
        [1, -1],
        [-1, 1],
        [-1, -1],
      ];
      corners.forEach(([sx, sz]) => {
        group.add(strut([sx * spread, 0, sz * spread], [sx * spread * 0.18, h, sz * spread * 0.18], radius, color, opts));
      });

      const arms = spec.arms === undefined ? 3 : spec.arms;
      for (let i = 0; i < arms; i += 1) {
        const ratio = 0.58 + i * 0.15;
        const y = h * ratio;
        const halfWidth = spread * (1.35 - i * 0.26);
        const legX = spread * (0.68 - i * 0.13);
        group.add(strut([-halfWidth, y, legX * 0.18], [halfWidth, y, legX * 0.18], radius * 0.85, color, opts));
        // 横担两端的绝缘子：小竖杆，让横担不像一根悬空的横线
        [-1, 1].forEach((side) => {
          group.add(strut([side * halfWidth, y, legX * 0.18], [side * halfWidth, y - h * 0.06, legX * 0.18], radius * 1.1, color, opts));
        });
      }

      // 腰部斜撑，避免腿之间空得像梯子
      for (let i = 0; i < 3; i += 1) {
        const y0 = (h * i) / 3;
        const y1 = (h * (i + 1)) / 3;
        const s0 = spread * (1 - (i / 3) * 0.8);
        const s1 = spread * (1 - ((i + 1) / 3) * 0.8);
        group.add(strut([s0, y0, s0], [-s1, y1, -s1], radius * 0.5, color, opts));
        group.add(strut([-s0, y0, s0], [s1, y1, -s1], radius * 0.5, color, opts));
      }
      return group;
    },

    cylinder(spec) {
      const group = new THREE.Group();
      const radius = spec.radius || 0.35;
      const height = spec.height || 2.2;
      const opts = { emissive: spec.glow || spec.color, emissiveIntensity: spec.glowStrength || 0.2, metalness: 0.6, roughness: 0.35 };
      const body = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius * 1.08, height, 24, 1, false), material(spec.color || "#c9d6e6", opts));
      body.position.y = height / 2;
      group.add(body);
      if (spec.cap !== false) {
        const cap = new THREE.Mesh(new THREE.CylinderGeometry(radius * 0.72, radius * 0.9, height * 0.07, 20), material(spec.capColor || "#f2f5f7", opts));
        cap.position.y = height + height * 0.035;
        group.add(cap);
      }
      if (spec.blades) {
        const blades = new THREE.Group();
        for (let i = 0; i < 3; i += 1) {
          const blade = box(radius * 0.18, radius * 3.4, radius * 0.08, spec.bladeColor || "#dbe6f2", { emissive: "#203040" });
          blade.position.y = radius * 1.7;
          const pivot = new THREE.Group();
          pivot.add(blade);
          pivot.rotation.z = (Math.PI * 2 * i) / 3;
          blades.add(pivot);
        }
        blades.position.y = height * 1.02;
        group.add(blades);
        group.userData.blades = blades;
      }
      return group;
    },

    box(spec) {
      // 一块板/一方块：基座、隔板、账本、算力模块都用它
      const size = spec.size || [1, 1, 1];
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(size[0] || 1, size[1] || 1, size[2] || 1),
        material(spec.color || "#2b4a6b", {
          emissive: spec.glow || spec.color || "#2b4a6b",
          emissiveIntensity: spec.glowStrength === undefined ? 0.2 : spec.glowStrength,
          metalness: 0.3,
          roughness: 0.55,
        })
      );
      return mesh;
    },

    bar(spec) {
      const mesh = box(spec.width || 0.55, 1, spec.depth || 0.55, spec.color || "#35e0ff", {
        emissive: spec.glow || spec.color || "#35e0ff",
        emissiveIntensity: spec.glowStrength || 0.35,
        metalness: 0.25,
        roughness: 0.4,
      });
      mesh.geometry.translate(0, 0.5, 0); // 让缩放以底边为基准，"长高"才像柱子
      return mesh;
    },

    ring(spec) {
      const geometry = new THREE.TorusGeometry(spec.radius || 1.6, spec.thickness || 0.03, 12, 96);
      const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
        color: hexToColor(spec.color || "#35e0ff", "#35e0ff"),
        transparent: true,
        opacity: spec.opacity === undefined ? 0.75 : spec.opacity,
      }));
      mesh.rotation.x = Math.PI / 2;
      return mesh;
    },

    sphere(spec) {
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(spec.radius || 0.4, 32, 24),
        material(spec.color || "#b6ff1f", { emissive: spec.glow || spec.color || "#b6ff1f", emissiveIntensity: spec.glowStrength || 1.4, metalness: 0.1, roughness: 0.4 })
      );
      return mesh;
    },

    /** 沿曲线的能量流：一条管 + 一串沿管运动的亮点。 */
    flow(spec) {
      const group = new THREE.Group();
      const points = (spec.points || []).map((p) => new THREE.Vector3(p[0], p[1], p[2]));
      if (points.length < 2) throw new Error("flow 至少需要两个点");
      const curve = new THREE.CatmullRomCurve3(points, false, "catmullrom", 0.2);
      const tube = new THREE.Mesh(
        new THREE.TubeGeometry(curve, 120, spec.radius || 0.05, 8, false),
        new THREE.MeshBasicMaterial({ color: hexToColor(spec.color || "#b6ff1f", "#b6ff1f"), transparent: true, opacity: spec.tubeOpacity === undefined ? 0.28 : spec.tubeOpacity })
      );
      group.add(tube);

      const count = spec.particles || 26;
      const geometry = new THREE.SphereGeometry(spec.particleRadius || 0.07, 10, 8);
      const dotMaterial = new THREE.MeshBasicMaterial({ color: hexToColor(spec.particleColor || spec.color || "#b6ff1f", "#b6ff1f"), transparent: true });
      const dots = [];
      for (let i = 0; i < count; i += 1) {
        const dot = new THREE.Mesh(geometry, dotMaterial.clone());
        dots.push(dot);
        group.add(dot);
      }
      group.userData.flow = { curve, dots, speed: spec.speed === undefined ? 0.22 : spec.speed, spread: spec.spread === undefined ? 1 : spec.spread };
      return group;
    },
  };

  // ------------------------------------------------------------------ 组装

  function place(object, spec) {
    const at = spec.at || [0, 0, 0];
    object.position.set(at[0], at[1], at[2]);
    if (spec.rotation) object.rotation.set(spec.rotation[0], spec.rotation[1], spec.rotation[2]);
    const scale = spec.scale === undefined ? 1 : spec.scale;
    object.scale.setScalar(scale);
  }

  function buildObjects(spec) {
    const root = new THREE.Group();
    (spec.objects || []).forEach((item) => {
      const builder = builders[item.type];
      if (!builder) throw new Error("未知物体类型：" + item.type);
      const object = builder(item);
      place(object, item);
      const record = {
        id: item.id || item.type,
        object,
        spec: item,
        baseScale: item.scale === undefined ? 1 : item.scale,
        baseOpacity: 1,
        mats: [],
        baseHeight: item.height,
      };
      object.traverse((child) => {
        if (child.material && !Array.isArray(child.material)) {
          child.material.transparent = true;
          record.mats.push(child.material);
        }
      });
      root.add(object);
      scene.objects.set(record.id, record);
    });
    return root;
  }

  /** 二维信息层：柱、刻度线、色块标签。
   *
   * 为什么要有一层二维：三维里打光的柱子颜色不可控（同一根柱子在不同角度亮暗不一），
   * 而"这条柱子代表容量收入、涨了多少"必须一眼看清。所以数据图表走 DOM，三维只做场景与气氛，
   * 两层叠在一起就是二维/三维融合，而不是让三维硬扛图表。
   */
  function buildPanels(spec) {
    const overlay = document.getElementById("overlay");
    (spec.panels || []).forEach((item) => {
      const node = document.createElement("div");
      node.className = "ak-panel";
      const width = scene.ctx.width;
      const height = scene.ctx.height;
      const w = (item.w === undefined ? 0.08 : item.w) * width;
      const h = (item.h === undefined ? 0.3 : item.h) * height;
      const x = (item.x === undefined ? 0.1 : item.x) * width;
      const yTop = (item.y === undefined ? 0.4 : item.y) * height;
      node.style.position = "absolute";
      node.style.left = x + "px";
      node.style.width = w + "px";
      node.style.height = h + "px";
      // y 是"柱底所在的高度比例"：底对齐时靠 translateY(-100%) 把柱子往上长
      node.style.top = yTop + "px";
      if (item.anchor !== "top") node.style.transform = "translateY(-100%)";
      node.style.background = item.color || "#2f5d78";
      node.style.borderRadius = (item.radius === undefined ? 6 : item.radius) + "px";
      node.style.opacity = "0";
      if (item.shadow !== false) {
        node.style.boxShadow = "0 0 26px " + (item.glow || item.color || "#35e0ff") + "55";
      }
      if (item.border) node.style.border = "2px solid " + item.border;
      if (item.label) {
        const label = document.createElement("div");
        label.textContent = item.label;
        label.style.cssText =
          "position:absolute;left:50%;transform:translateX(-50%);white-space:nowrap;" +
          "font-size:" + (item.labelSize || 28) + "px;color:" + (item.labelColor || "#c3ccdb") + ";" +
          "font-weight:500;letter-spacing:0.06em;" +
          (item.anchor === "top"
            ? "bottom:calc(100% + 10px);"
            : "top:calc(100% + 10px);");
        node.appendChild(label);
      }
      overlay.appendChild(node);
      scene.panels.set(item.id, {
        node,
        spec: item,
        baseHeight: h,
        top: yTop,
        anchor: item.anchor || "bottom",
        baseX: x,
        moves: (item.moves || []).map((row) => [Number(row[0]), Number(row[1]), Number(row[2])]),
        frameWidth: width,
        frameHeight: height,
      });
    });
  }

  function updatePanels(t) {
    scene.panels.forEach((record) => {
      const spec = record.spec;
      const enter = spec.enter || [0, 0.6];
      const exit = spec.exit || null;
      const appear = clamp((t - enter[0]) / Math.max(1e-6, enter[1] - enter[0]), 0, 1);
      let opacity = easeOutBackK(appear);
      if (exit) {
        const fade = clamp((t - exit[0]) / Math.max(1e-6, exit[1] - exit[0]), 0, 1);
        opacity *= 1 - easeOutBackK(fade);
      }
      record.node.style.opacity = String(Math.max(0, Math.min(1, opacity)));

      // 高度：keyframes 里的比例 × 基准高度；二维柱子的"长高"最直观
      let ratio = 1;
      if (spec.keyframes) {
        const keys = spec.keyframes;
        let index = 0;
        while (index < keys.length - 1 && keys[index + 1][0] <= t) index += 1;
        const current = keys[index];
        const next = keys[Math.min(index + 1, keys.length - 1)];
        const span = Math.max(1e-6, next[0] - current[0]);
        const k = easeOutBackK(clamp((t - current[0]) / span, 0, 1));
        ratio = current[1] + (next[1] - current[1]) * k;
      } else if (spec.grow !== false) {
        ratio = appear;
      }
      const height = Math.max(1, record.baseHeight * ratio);
      record.node.style.height = height + "px";

      // moves：让整块元素（锚芯片、游标）在画面里移动，x/y 都是画面比例
      if (record.moves.length) {
        let previous = record.moves[0];
        let next = record.moves[record.moves.length - 1];
        for (let i = 0; i < record.moves.length; i += 1) {
          if (record.moves[i][0] <= t) {
            previous = record.moves[i];
            next = record.moves[i + 1] || record.moves[i];
          }
        }
        const span = Math.max(1e-6, next[0] - previous[0]);
        const raw = Math.max(0, Math.min(1, (t - previous[0]) / span));
        const eased = raw * raw * (3 - 2 * raw);
        record.node.style.left =
          (previous[1] + (next[1] - previous[1]) * eased) * record.frameWidth + "px";
        record.node.style.top =
          (previous[2] + (next[2] - previous[2]) * eased) * record.frameHeight + "px";
      }
    });
  }

  function buildTexts(spec) {
    const overlay = document.getElementById("overlay");
    const roleStyles = {
      kicker: { size: 34, color: "#35e0ff", weight: 500, tracking: "0.16em" },
      title: { size: 92, color: "#f2f5f7", weight: 700, tracking: "0.01em" },
      subtitle: { size: 52, color: "#f2f5f7", weight: 600, tracking: "0.01em" },
      caption: { size: 40, color: "#c3ccdb", weight: 400, tracking: "0.02em" },
      number: { size: 168, color: "#b6ff1f", weight: 700, tracking: "-0.01em" },
      numberSmall: { size: 96, color: "#b6ff1f", weight: 700, tracking: "0" },
      source: { size: 26, color: "#7f8da3", weight: 400, tracking: "0.04em" },
      label: { size: 34, color: "#8ea3bd", weight: 500, tracking: "0.08em" },
    };

    (spec.texts || []).forEach((item) => {
      const role = roleStyles[item.role] || roleStyles.caption;
      const node = document.createElement("div");
      node.className = "ak-text";
      node.dataset.role = item.role || "caption";
      const width = item.w === undefined ? 0.84 : item.w;
      node.style.width = width * (scene.ctx.width) + "px";
      node.style.left = (item.x === undefined ? 0.08 : item.x) * scene.ctx.width + "px";
      node.style.top = (item.y === undefined ? 0.5 : item.y) * scene.ctx.height + "px";
      node.style.fontSize = (item.size || role.size) + "px";
      node.style.fontWeight = String(item.weight || role.weight);
      node.style.color = item.color || role.color;
      node.style.letterSpacing = item.tracking || role.tracking;
      node.style.textAlign = item.align || "left";
      if (item.align === "center") {
        node.style.left = "50%";
        node.style.transform = "translateX(-50%)";
        node.style.marginLeft = "0px";
      }
      if (item.lineHeight) node.style.lineHeight = String(item.lineHeight);
      if (item.maxLines) {
        node.style.display = "-webkit-box";
        node.style.webkitLineClamp = String(item.maxLines);
        node.style.webkitBoxOrient = "vertical";
        node.style.overflow = "hidden";
      }
      node.textContent = item.text || "";
      overlay.appendChild(node);
      scene.texts.set(item.id, {
        node,
        spec: item,
        baseText: item.text || "",
        centered: item.align === "center",
      });
    });
  }

  // ------------------------------------------------------------------ 更新

  function applyCamera(t) {
    const keys = scene.camera;
    if (!keys.length) return;
    let index = 0;
    while (index < keys.length - 1 && keys[index + 1].t <= t) index += 1;
    const current = keys[index];
    const next = keys[Math.min(index + 1, keys.length - 1)];
    const span = Math.max(1e-6, next.t - current.t);
    const k = easeOutBackK(clamp((t - current.t) / span, 0, 1));
    const lerp = (a, b) => a + (b - a) * k;
    const camera = scene.cameraObject;
    camera.position.set(
      lerp(current.pos[0], next.pos[0]),
      lerp(current.pos[1], next.pos[1]),
      lerp(current.pos[2], next.pos[2])
    );
    const target = new THREE.Vector3(
      lerp(current.look[0], next.look[0]),
      lerp(current.look[1], next.look[1]),
      lerp(current.look[2], next.look[2])
    );
    camera.fov = lerp(current.fov === undefined ? 38 : current.fov, next.fov === undefined ? 38 : next.fov);
    camera.updateProjectionMatrix();
    camera.up.set(0, 1, 0);
    camera.lookAt(target);
    const roll = lerp(current.roll || 0, next.roll || 0);
    camera.rotateZ(roll);
  }

  /** 摄影机用平滑（不是回弹），回弹只给物体出现用。 */
  function easeOutBackK(k) {
    return k * k * (3 - 2 * k);
  }

  function updateObjects(t) {
    scene.objects.forEach((record) => {
      const spec = record.spec;
      const enter = spec.enter || [0, 0.6];
      const k = easeOutBack(clamp((t - enter[0]) / Math.max(1e-6, enter[1] - enter[0]), 0, 1));
      const visible = t >= enter[0];
      const exit = spec.exit;
      const exitK = exit ? ramp(t, exit[0], exit[1]) : 0;
      const opacity = visible ? clamp(k, 0, 1) * (1 - exitK) : 0;

      record.mats.forEach((mat) => {
        mat.opacity = opacity;
      });

      // 生长（柱子）：从 0 长到目标高度
      if (spec.keyframes) {
        // 关键帧控制高度比例：用来演"此消彼长""总量不变但分法变了"
        const keys = spec.keyframes;
        let index = 0;
        while (index < keys.length - 1 && keys[index + 1][0] <= t) index += 1;
        const current = keys[index];
        const next = keys[Math.min(index + 1, keys.length - 1)];
        const span = Math.max(1e-6, next[0] - current[0]);
        const k2 = clamp((t - current[0]) / span, 0, 1);
        const smooth = k2 * k2 * (3 - 2 * k2);
        const ratio = current[1] + (next[1] - current[1]) * smooth;
        const target = spec.height || 1;
        record.object.scale.y = Math.max(0.0001, target * ratio * k);
        record.object.scale.x = record.baseScale;
        record.object.scale.z = record.baseScale;
      } else if (spec.grow) {
        const grow = easeOutBack(clamp((t - spec.grow[0]) / Math.max(1e-6, spec.grow[1] - spec.grow[0]), 0, 1));
        const target = spec.height || spec.to || 1;
        record.object.scale.y = Math.max(0.0001, target * grow);
        record.object.scale.x = record.baseScale;
        record.object.scale.z = record.baseScale;
      } else {
        record.object.scale.setScalar(record.baseScale * (0.86 + 0.14 * k));
      }

      // 位置关键帧：用来演"锚从一个对象移到另一个对象"
      if (spec.move) {
        const keys = spec.move;
        let index = 0;
        while (index < keys.length - 1 && keys[index + 1][0] <= t) index += 1;
        const current = keys[index];
        const next = keys[Math.min(index + 1, keys.length - 1)];
        const span = Math.max(1e-6, next[0] - current[0]);
        const k2 = clamp((t - current[0]) / span, 0, 1);
        const smooth = k2 * k2 * (3 - 2 * k2);
        const from = current[1];
        const to = next[1];
        record.object.position.set(
          from[0] + (to[0] - from[0]) * smooth,
          from[1] + (to[1] - from[1]) * smooth,
          from[2] + (to[2] - from[2]) * smooth
        );
      }

      if (spec.spin) {
        record.object.rotation.y = (spec.spinAngle || spec.rotation ? (spec.rotation ? spec.rotation[1] : 0) : 0) + t * spec.spin;
      }
      if (spec.bob) {
        record.object.position.y += Math.sin(t * (spec.bobSpeed || 1.2)) * spec.bob;
      }
      const blades = record.object.userData.blades;
      if (blades) blades.rotation.z = t * (spec.bladeSpeed || 2.4);

      const flow = record.object.userData.flow;
      if (flow) {
        const n = flow.dots.length;
        flow.dots.forEach((dot, index) => {
          // 负速度（反向流动）会让取模变成负数，先加 1 再取模，别让曲线取到画外
          const raw = t * flow.speed * flow.spread + index / n;
          const phase = ((raw % 1) + 1) % 1;
          const point = flow.curve.getPointAt(phase);
          dot.position.copy(point);
          dot.scale.setScalar(0.55 + 0.45 * Math.sin(Math.PI * phase));
          dot.material.opacity = opacity;
        });
      }

      if (spec.pulse) {
        const strength = 0.5 + 0.5 * Math.sin(t * (spec.pulseSpeed || 3.0) * Math.PI);
        record.mats.forEach((mat) => {
          if (mat.emissiveIntensity !== undefined) {
            mat.emissiveIntensity = (spec.emissiveBase === undefined ? 0.2 : spec.emissiveBase) + strength * (spec.pulse);
          }
        });
      }
    });
  }

  function updateTexts(t) {
    scene.texts.forEach((record) => {
      const spec = record.spec;
      const enter = spec.enter || [0, 0.5];
      const k = ramp(t, enter[0], enter[1]);
      const exitK = spec.exit ? ramp(t, spec.exit[0], spec.exit[1]) : 0;
      const opacity = k * (1 - exitK);
      record.node.style.opacity = String(clamp(opacity, 0, 1));
      const lift = (1 - k) * (spec.rise === undefined ? 26 : spec.rise);
      const scale = 0.985 + 0.015 * k;
      if (record.centered) {
        record.node.style.transform = `translateX(-50%) translateY(${lift}px) scale(${scale})`;
      } else {
        record.node.style.transform = `translateY(${lift}px) scale(${scale})`;
      }
      record.node.style.transformOrigin = record.centered ? "center top" : "left top";

      if (spec.count) {
        const from = spec.count.from === undefined ? 0 : spec.count.from;
        const to = spec.count.to === undefined ? 1 : spec.count.to;
        const decimals = spec.count.decimals === undefined ? 0 : spec.count.decimals;
        const start = spec.count.start === undefined ? enter[0] : spec.count.start;
        const end = spec.count.end === undefined ? enter[1] + 0.6 : spec.count.end;
        const progress = easeOutBackK(clamp((t - start) / Math.max(1e-6, end - start), 0, 1));
        const value = from + (to - from) * progress;
        record.node.textContent = (spec.count.prefix || "") + value.toFixed(decimals) + (spec.count.suffix || "");
      }
    });
  }

  // ------------------------------------------------------------------ 对外

  async function setup(ctx) {
    const config = ctx.config || {};
    const spec = config.scene || {};
    scene.ctx = { width: config.width || 1080, height: config.height || 1920, fps: config.fps || 30, duration: config.duration || 10 };
    scene.spec = spec;
    scene.camera = spec.camera || [];

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(1);
    renderer.setSize(scene.ctx.width, scene.ctx.height);
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = spec.exposure === undefined ? 1.08 : spec.exposure;
    // 画布透明，天空交给 CSS 渐变：比在 WebGL 里贴一张天空图省，而且不受雾影响
    renderer.setClearColor(0x000000, 0);
    const overlay = document.getElementById("overlay");
    ctx.stage.insertBefore(renderer.domElement, overlay);

    const topColor = (spec.sky && spec.sky.top) || "#16294a";
    const bottomColor = (spec.sky && spec.sky.bottom) || "#05070d";
    ctx.stage.style.background =
      (spec.sky && spec.sky.css) ||
      "radial-gradient(ellipse 140% 78% at 50% 4%, " + topColor + " 0%, " + bottomColor + " 58%)";

    const world = new THREE.Scene();
    const skyColor = hexToColor(bottomColor);
    world.fog = new THREE.Fog(skyColor, (spec.sky && spec.sky.fog && spec.sky.fog[0]) || 9, (spec.sky && spec.sky.fog && spec.sky.fog[1]) || 46);

    world.add(buildObjects(spec));

    const camera = new THREE.PerspectiveCamera(38, scene.ctx.width / scene.ctx.height, 0.1, 400);
    scene.cameraObject = camera;

    world.add(new THREE.HemisphereLight(hexToColor((spec.sky && spec.sky.top) || "#6f8fbf"), skyColor, spec.ambient === undefined ? 0.3 : spec.ambient));
    const key = new THREE.DirectionalLight(0xffffff, spec.keyLight === undefined ? 1.0 : spec.keyLight);
    key.position.set(6, 12, 8);
    world.add(key);
    // 轮廓光：从背侧斜上方打一层冷光，让塔的杆件边缘亮起来，别通体一个调子
    const rim = new THREE.DirectionalLight(
      hexToColor(spec.rimColor || "#4f9fd0", "#4f9fd0"),
      spec.rimLight === undefined ? 0.9 : spec.rimLight
    );
    rim.position.set(-7, 5, -20);
    world.add(rim);

    (spec.lights || []).forEach((light) => {
      const point = new THREE.PointLight(hexToColor(light.color || "#35e0ff"), light.intensity === undefined ? 8 : light.intensity, light.distance || 40);
      point.position.set(light.at[0], light.at[1], light.at[2]);
      world.add(point);
    });

    buildPanels(spec);
    buildTexts(spec);

    // 文字压暗层：竖屏片子里文字压在画面上，没有这层就会被亮处吃掉（不是滤镜，是排版需要）
    if (spec.scrim !== false) {
      const scrim = document.createElement("div");
      const strength = spec.scrimStrength === undefined ? 0.72 : spec.scrimStrength;
      scrim.style.cssText =
        "position:absolute;inset:0;pointer-events:none;background:linear-gradient(to top," +
        " rgba(3,5,10," + strength + ") 0%, rgba(3,5,10," + strength * 0.72 + ") 26%," +
        " rgba(3,5,10," + strength * 0.18 + ") 52%, rgba(3,5,10,0) 72%);";
      ctx.stage.insertBefore(scrim, overlay);
    }

    // 暗角：固定不变的一层，给画面加"摄影机拍到"的观感
    const vignette = document.createElement("div");
    vignette.style.cssText =
      "position:absolute;inset:0;pointer-events:none;" +
      "background:radial-gradient(ellipse 78% 62% at 50% 46%, rgba(0,0,0,0) 42%, rgba(0,0,0,0.55) 100%);";
    ctx.stage.insertBefore(vignette, overlay);

    scene.renderer = renderer;
    scene.world = world;
    renderer.render(world, camera);
  }

  function render(t) {
    applyCamera(t);
    updateObjects(t);
    updateTexts(t);
    updatePanels(t);
    scene.renderer.render(scene.world, scene.cameraObject);
  }

  window.AKScene = { setup, render, _scene: scene, _builders: builders };
})();
