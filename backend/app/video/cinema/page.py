"""把一张镜头页拼出来：HTML 外壳 + 运行时契约。

为什么不用现成的动效框架：我们要的是**逐帧确定性**。每一帧都由 ``t`` 算出来，
不跑 CSS 动画、不跑 ``requestAnimationFrame`` 驱动的补间——所以同一个 ``t``
永远渲出同一张图，可以单帧重渲、可以倒着放去核对（OpenKatana 那条
``Composition is not deterministic when seeking backwards`` 的教训就来自这里）。

页面与服务端的契约（renderer 只依赖这四个东西）：

    window.__AK        {fps, duration, width, height, palette, fonts}
    window.AKScene     {setup(ctx), render(t)}   ← 镜头自己的 JS 提供
    window.__ready     boolean                   ← setup 跑完置 true
    window.__seek(t)   Promise<boolean>          ← 渲染第 t 秒那一帧

镜头作者只写 ``scene.js``；外壳（字体、画布、离屏缓冲、错误上报）由这里统一生成，
免得每个镜头各写一套还各不相同。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

SHELL_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @font-face {{
    font-family: "AKSC";
    src: url("{font_file}") format("truetype");
    font-weight: 100 900;
    font-display: block;
  }}
  html, body {{
    margin: 0; padding: 0; width: 100%; height: 100%;
    background: #000; overflow: hidden;
  }}
  #stage {{
    position: relative;
    width: {width}px;
    height: {height}px;
    overflow: hidden;
    background: {background};
    font-family: "AKSC", "Noto Sans SC", sans-serif;
    color: {foreground};
    /* 只允许静态样式：一切运动都必须由 render(t) 算出，CSS 动画会毁掉确定性 */
  }}
  #stage * {{ box-sizing: border-box; }}
  canvas {{ display: block; }}
  /* 文字层压在画布上面：中文靠字体渲染比画到贴图上清楚得多 */
  #overlay {{
    position: absolute; inset: 0;
    pointer-events: none;
    overflow: hidden;
  }}
  #overlay .ak-text {{
    position: absolute;
    will-change: opacity, transform;
    white-space: pre-wrap;
    line-height: 1.18;
  }}
</style>
</head>
<body>
<div id="stage"><div id="overlay"></div></div>
<script>{three_inline}</script>
<script>window.__AK = {config};</script>
<script src="scene.js"></script>
<script>
(function () {{
  const stage = document.getElementById("stage");
  const errors = [];
  window.__errors = errors;

  function fail(error) {{
    errors.push(String(error && error.stack || error));
    window.__ready = true;
  }}

  function nextFrame() {{
    return new Promise((resolve) => requestAnimationFrame(() => resolve(true)));
  }}

  window.__seek = async function (t) {{
    if (!window.AKScene || typeof window.AKScene.render !== "function") {{
      throw new Error("scene.js 没有提供 AKScene.render(t)");
    }}
    window.AKScene.render(t);
    // WebGL 的绘制命令是排队的：等两帧再截图，保证画布真的画完了
    await nextFrame();
    await nextFrame();
    return true;
  }};

  (async function boot() {{
    try {{
      if (!window.AKScene || typeof window.AKScene.setup !== "function") {{
        throw new Error("scene.js 没有提供 AKScene.setup(ctx)");
      }}
      await window.AKScene.setup({{ stage: stage, config: window.__AK, three: window.THREE }});
      await nextFrame();
      window.__ready = true;
    }} catch (error) {{
      fail(error);
    }}
  }})();

  window.addEventListener("error", (event) => errors.push(String(event.message)));
}})();
</script>
</body>
</html>
"""


def build_page(
    *,
    title: str,
    width: int,
    height: int,
    fps: int,
    duration: float,
    palette: Mapping[str, str] | None = None,
    fonts: tuple[str, ...] = (),
    font_file: str = "fonts/NotoSansSC-VF.ttf",
    three_inline: str = "",
    foreground: str = "#f2f5f7",
    background: str = "#0a0d16",
    scene_spec: Mapping | None = None,
) -> str:
    """拼出镜头页的 HTML。``three_inline`` 传空就不引入 three.js。

    ``scene_spec`` 是这一镜的"舞台说明书"（物体、文字、摄影机、事件），
    由引擎 ``scene.js`` 解释执行——视觉导演改设计只改这份数据，不碰渲染代码。
    """
    config = {
        "fps": fps,
        "duration": duration,
        "width": width,
        "height": height,
        "palette": dict(palette or {}),
        "fonts": list(fonts),
        "scene": dict(scene_spec or {}),
    }
    return SHELL_TEMPLATE.format(
        title=title,
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        palette=json.dumps(dict(palette or {}), ensure_ascii=False),
        fonts=json.dumps(list(fonts), ensure_ascii=False),
        font_file=font_file,
        three_inline=three_inline,
        foreground=foreground,
        background=background,
        config=json.dumps(config, ensure_ascii=False),
    )


def write_scene_shell(
    scene_dir: Path | str,
    *,
    title: str,
    width: int,
    height: int,
    fps: int,
    duration: float,
    palette: Mapping[str, str] | None = None,
    fonts: tuple[str, ...] = (),
    font_file: str = "fonts/NotoSansSC-VF.ttf",
    three_js: Path | str | None = None,
    foreground: str = "#f2f5f7",
    background: str = "#0a0d16",
    scene_spec: Mapping | None = None,
) -> Path:
    """在镜头目录里写 ``index.html``；``three_js`` 给了就把库内联进去。

    内联而不是 ``<script src>``：首帧截图不能被"库还没加载完"这件事影响，
    而且内联之后整个镜头目录是自包含的，搬到别的机器也能渲。
    """
    target = Path(scene_dir)
    target.mkdir(parents=True, exist_ok=True)
    three_inline = ""
    if three_js:
        three_inline = Path(three_js).read_text(encoding="utf-8", errors="replace")
    html = build_page(
        title=title,
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        palette=palette,
        fonts=fonts,
        font_file=font_file,
        three_inline=three_inline,
        foreground=foreground,
        background=background,
        scene_spec=scene_spec,
    )
    path = target / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


__all__ = ["build_page", "write_scene_shell"]
