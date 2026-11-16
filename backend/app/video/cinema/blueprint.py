"""视觉制作蓝图（Visual Production Blueprint）：内容、视觉、引擎之间的唯一接口。

为什么要有它：这次整改的核心教训是——**研究报告不能直接变成画面**。中间必须有
一个"视觉导演"把每个知识点翻译成可执行的镜头设计（构图、摄影机、事件、素材、声音、
证据）。如果这份设计只写成 Markdown 或一句 prompt，渲染程序读不懂，最后还是会退回到
"把文字做成卡片"。

所以蓝图是这个仓库里唯一被允许跨层传递的格式：

    内容导演（叙事/脚本/证据）        ← 写 blueprint 的顶层与本层字段
         ↓
    视觉导演（构图/摄影机/事件/素材）  ← 写 blueprint 的 scenes[*]
         ↓
    制作引擎（cinema-html / ...）     ← 只认 blueprint，不认别的东西

三条硬规矩：

1. **未知字段一律拒绝**。写成 ``dataclasses`` + 逐字段校验，而不是随手塞 dict——
   拼错字段名会让"设计"悄悄失效，而画面照旧渲出来，最难查。
2. **每个镜头都必须挂证据**。没有 ``evidence_refs`` 的镜头会直接被拒：这是"不许
   凭空造画面"在数据结构上的落点。
3. **资产必须写清许可**。``AssetRef.license`` 为空即拒绝，和 ``assets_manifest.csv``
   的口径一致（可商用 + 有许可记录）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ENGINES = ("cinema-html",)
ASSET_TYPES = ("3d", "2d", "image", "font", "audio", "video")
EVENT_KINDS = (
    "reveal",        # 元素出现
    "transform",     # 变形/重组
    "flow",          # 能量、资金、数据沿路径流动
    "highlight",     # 强调某个对象
    "camera",        # 摄影机自身的运动事件（推、摇、跟）
    "cut",           # 画面内硬切（不是镜头间转场）
    "count",         # 数值滚动
    "wipe",          # 遮挡/擦除
)
SCENE_STATUS = ("planned", "rendered", "reviewed", "rejected")
REQUIRED_QUALITY = (
    "camera_motion_has_purpose",
    "knowledge_visualized",
    "text_is_not_the_main_visual",
)


class BlueprintError(ValueError):
    """蓝图不合法。``reason`` 是稳定原因码，供测试与上游判断。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message}


def _text(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise BlueprintError("bad_field", f"{field_name} 必须是字符串，收到 {type(value).__name__}")
    text = value.strip()
    if not text and not allow_empty:
        raise BlueprintError("bad_field", f"{field_name} 不能为空")
    return text


def _number(value: Any, field_name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlueprintError("bad_field", f"{field_name} 必须是数字，收到 {type(value).__name__}")
    number = float(value)
    if minimum is not None and number < minimum:
        raise BlueprintError("bad_field", f"{field_name} 不能小于 {minimum}，收到 {number}")
    return number


def _triple(value: Any, field_name: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise BlueprintError("bad_field", f"{field_name} 必须是三个数字组成的数组")
    return tuple(_number(item, f"{field_name}[{index}]") for index, item in enumerate(value))  # type: ignore[return-value]


def _strict(payload: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    unknown = set(payload) - set(allowed)
    if unknown:
        raise BlueprintError("unknown_field", f"{where} 有未知字段：{'、'.join(sorted(unknown))}")


# --------------------------------------------------------------------------- 相机


@dataclass(frozen=True)
class CameraKey:
    """一个摄影机关键帧：什么时候、在哪、看向哪、多大视角。"""

    t: float
    pos: tuple[float, float, float]
    look: tuple[float, float, float] = (0.0, 0.0, 0.0)
    fov: float = 38.0
    roll: float = 0.0

    @staticmethod
    def from_dict(payload: Mapping[str, Any], where: str) -> "CameraKey":
        _strict(payload, ("t", "pos", "look", "fov", "roll"), where)
        return CameraKey(
            t=_number(payload.get("t"), f"{where}.t", minimum=0.0),
            pos=_triple(payload.get("pos"), f"{where}.pos"),
            look=_triple(payload.get("look", (0.0, 0.0, 0.0)), f"{where}.look"),
            fov=_number(payload.get("fov", 38.0), f"{where}.fov"),
            roll=_number(payload.get("roll", 0.0), f"{where}.roll"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "t": self.t,
            "pos": list(self.pos),
            "look": list(self.look),
            "fov": self.fov,
            "roll": self.roll,
        }


# --------------------------------------------------------------------------- 事件


@dataclass(frozen=True)
class VisualEvent:
    """画面里发生的一件事：什么时刻、哪种动作、动的是谁、为什么动。"""

    t: float
    kind: str
    target: str
    note: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(payload: Mapping[str, Any], where: str) -> "VisualEvent":
        _strict(payload, ("t", "kind", "target", "note", "params", "_params"), where)
        kind = _text(payload.get("kind"), f"{where}.kind")
        if kind not in EVENT_KINDS:
            raise BlueprintError("bad_kind", f"{where}.kind 必须是 {'/'.join(EVENT_KINDS)} 之一，收到 {kind}")
        params = payload.get("params") or payload.get("_params") or {}
        if not isinstance(params, Mapping):
            raise BlueprintError("bad_field", f"{where}.params 必须是对象")
        return VisualEvent(
            t=_number(payload.get("t"), f"{where}.t", minimum=0.0),
            kind=kind,
            target=_text(payload.get("target"), f"{where}.target"),
            note=_text(payload.get("note", ""), f"{where}.note", allow_empty=True),
            params=dict(params),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"t": self.t, "kind": self.kind, "target": self.target, "note": self.note, "params": dict(self.params)}


# --------------------------------------------------------------------------- 资产


@dataclass(frozen=True)
class AssetRef:
    id: str
    type: str
    source: str
    license: str
    path: str = ""
    note: str = ""

    @staticmethod
    def from_dict(payload: Mapping[str, Any], where: str) -> "AssetRef":
        _strict(payload, ("id", "type", "source", "license", "path", "note"), where)
        kind = _text(payload.get("type"), f"{where}.type")
        if kind not in ASSET_TYPES:
            raise BlueprintError("bad_kind", f"{where}.type 必须是 {'/'.join(ASSET_TYPES)} 之一，收到 {kind}")
        return AssetRef(
            id=_text(payload.get("id"), f"{where}.id"),
            type=kind,
            source=_text(payload.get("source"), f"{where}.source"),
            license=_text(payload.get("license"), f"{where}.license"),
            path=_text(payload.get("path", ""), f"{where}.path", allow_empty=True),
            note=_text(payload.get("note", ""), f"{where}.note", allow_empty=True),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "license": self.license,
            "path": self.path,
            "note": self.note,
        }


# --------------------------------------------------------------------------- 声音


@dataclass(frozen=True)
class AudioCue:
    """一声：什么时候、哪一类、用哪条素材、多大声。"""

    t: float
    kind: str
    id: str
    gain_db: float = -12.0

    @staticmethod
    def from_dict(payload: Mapping[str, Any], where: str) -> "AudioCue":
        _strict(payload, ("t", "kind", "id", "gain_db"), where)
        return AudioCue(
            t=_number(payload.get("t"), f"{where}.t", minimum=0.0),
            kind=_text(payload.get("kind"), f"{where}.kind"),
            id=_text(payload.get("id"), f"{where}.id"),
            gain_db=_number(payload.get("gain_db", -12.0), f"{where}.gain_db"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"t": self.t, "kind": self.kind, "id": self.id, "gain_db": self.gain_db}


# --------------------------------------------------------------------------- 证据


@dataclass(frozen=True)
class EvidenceRef:
    """这一镜说的话，出自哪一条已核查结论。"""

    source_id: str
    claim: str
    check: str = "confirmed"

    @staticmethod
    def from_dict(payload: Mapping[str, Any], where: str) -> "EvidenceRef":
        _strict(payload, ("source_id", "claim", "check", "finding_index"), where)
        return EvidenceRef(
            source_id=_text(payload.get("source_id"), f"{where}.source_id"),
            claim=_text(payload.get("claim"), f"{where}.claim"),
            check=_text(payload.get("check", "confirmed"), f"{where}.check"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "claim": self.claim, "check": self.check}


# --------------------------------------------------------------------------- 字幕


@dataclass(frozen=True)
class Subtitle:
    text: str
    in_s: float
    out_s: float
    style: str = "lower"

    @staticmethod
    def from_dict(payload: Mapping[str, Any], where: str) -> "Subtitle":
        _strict(payload, ("text", "in", "out", "style"), where)
        in_s = _number(payload.get("in"), f"{where}.in", minimum=0.0)
        out_s = _number(payload.get("out"), f"{where}.out", minimum=0.0)
        if out_s <= in_s:
            raise BlueprintError("bad_field", f"{where}.out 必须大于 in")
        return Subtitle(
            text=_text(payload.get("text"), f"{where}.text"),
            in_s=in_s,
            out_s=out_s,
            style=_text(payload.get("style", "lower"), f"{where}.style"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "in": self.in_s, "out": self.out_s, "style": self.style}


# --------------------------------------------------------------------------- 镜头


@dataclass(frozen=True)
class Scene:
    scene_id: str
    duration: float
    engine: str
    entry: str
    narrative_purpose: str
    knowledge_objective: str
    visual_concept: str
    composition: Mapping[str, str]
    camera: tuple[CameraKey, ...]
    visual_events: tuple[VisualEvent, ...]
    motion_design: Mapping[str, Any]
    assets: tuple[AssetRef, ...]
    audio_cues: tuple[AudioCue, ...]
    subtitle: Subtitle | None
    evidence_refs: tuple[EvidenceRef, ...]
    quality_requirements: tuple[str, ...]
    narration: str = ""
    status: str = "planned"
    qc: Mapping[str, Any] = field(default_factory=dict)

    # ---- 解析 -----------------------------------------------------------------

    @staticmethod
    def from_dict(payload: Mapping[str, Any], where: str) -> "Scene":
        _strict(
            payload,
            (
                "scene_id",
                "duration",
                "engine",
                "entry",
                "narrative_purpose",
                "knowledge_objective",
                "visual_concept",
                "composition",
                "camera",
                "visual_events",
                "motion_design",
                "assets",
                "audio_cues",
                "subtitle",
                "evidence_refs",
                "quality_requirements",
                "narration",
                "status",
                "qc",
            ),
            where,
        )
        engine = _text(payload.get("engine"), f"{where}.engine")
        if engine not in ENGINES:
            raise BlueprintError("bad_engine", f"{where}.engine 必须是 {'/'.join(ENGINES)} 之一，收到 {engine}")
        duration = _number(payload.get("duration"), f"{where}.duration", minimum=0.2)

        composition = payload.get("composition") or {}
        if not isinstance(composition, Mapping):
            raise BlueprintError("bad_field", f"{where}.composition 必须是对象")
        composition_out = {}
        for key, value in composition.items():
            if not isinstance(value, str):
                raise BlueprintError("bad_field", f"{where}.composition.{key} 必须是字符串")
            composition_out[str(key)] = value

        camera = tuple(
            CameraKey.from_dict(item, f"{where}.camera[{index}]")
            for index, item in enumerate(payload.get("camera") or [])
        )
        if not camera:
            raise BlueprintError("no_camera", f"{where} 至少需要一个摄影机关键帧——没有摄影机就没有镜头语言")
        for key in camera:
            if key.t > duration + 1e-6:
                raise BlueprintError(
                    "camera_out_of_range",
                    f"{where}.camera 的关键帧 t={key.t} 超出镜头时长 {duration}",
                )

        events = tuple(
            VisualEvent.from_dict(item, f"{where}.visual_events[{index}]")
            for index, item in enumerate(payload.get("visual_events") or [])
        )
        for event in events:
            if event.t > duration + 1e-6:
                raise BlueprintError(
                    "event_out_of_range", f"{where}.visual_events 里 t={event.t} 的事件超出镜头时长 {duration}"
                )

        assets = tuple(
            AssetRef.from_dict(item, f"{where}.assets[{index}]")
            for index, item in enumerate(payload.get("assets") or [])
        )
        for asset in assets:
            if not asset.license.strip():
                raise BlueprintError("no_license", f"{where}.assets[{asset.id}] 没有写许可")

        cues = tuple(
            AudioCue.from_dict(item, f"{where}.audio_cues[{index}]")
            for index, item in enumerate(payload.get("audio_cues") or [])
        )
        evidence = tuple(
            EvidenceRef.from_dict(item, f"{where}.evidence_refs[{index}]")
            for index, item in enumerate(payload.get("evidence_refs") or [])
        )
        if not evidence:
            raise BlueprintError(
                "no_evidence",
                f"{where} 没有挂证据。任何镜头都必须能说出「这句话出自哪条已核查的结论」，"
                "没有证据的镜头就是在编内容。",
            )

        subtitle_payload = payload.get("subtitle")
        subtitle = None if subtitle_payload in (None, {}) else Subtitle.from_dict(subtitle_payload, f"{where}.subtitle")
        if subtitle is not None and subtitle.out_s > duration + 1e-6:
            raise BlueprintError(
                "subtitle_out_of_range", f"{where}.subtitle.out={subtitle.out_s} 超出镜头时长 {duration}"
            )

        quality = tuple(
            _text(item, f"{where}.quality_requirements") for item in (payload.get("quality_requirements") or [])
        )
        missing = [name for name in REQUIRED_QUALITY if name not in quality]
        if missing:
            raise BlueprintError(
                "missing_quality",
                f"{where}.quality_requirements 缺少必填项：{'、'.join(missing)}",
            )

        status = _text(payload.get("status", "planned"), f"{where}.status")
        if status not in SCENE_STATUS:
            raise BlueprintError("bad_status", f"{where}.status 必须是 {'/'.join(SCENE_STATUS)} 之一，收到 {status}")

        qc = payload.get("qc") or {}
        if not isinstance(qc, Mapping):
            raise BlueprintError("bad_field", f"{where}.qc 必须是对象")

        return Scene(
            scene_id=_text(payload.get("scene_id"), f"{where}.scene_id"),
            duration=duration,
            engine=engine,
            entry=_text(payload.get("entry"), f"{where}.entry"),
            narrative_purpose=_text(payload.get("narrative_purpose"), f"{where}.narrative_purpose"),
            knowledge_objective=_text(payload.get("knowledge_objective"), f"{where}.knowledge_objective"),
            visual_concept=_text(payload.get("visual_concept"), f"{where}.visual_concept"),
            composition=composition_out,
            camera=camera,
            visual_events=events,
            motion_design=dict(payload.get("motion_design") or {}),
            assets=assets,
            audio_cues=cues,
            subtitle=subtitle,
            evidence_refs=evidence,
            quality_requirements=quality,
            narration=_text(payload.get("narration", ""), f"{where}.narration", allow_empty=True),
            status=status,
            qc=dict(qc),
        )

    # ---- 输出 -----------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "duration": self.duration,
            "engine": self.engine,
            "entry": self.entry,
            "narrative_purpose": self.narrative_purpose,
            "knowledge_objective": self.knowledge_objective,
            "visual_concept": self.visual_concept,
            "composition": dict(self.composition),
            "camera": [key.as_dict() for key in self.camera],
            "visual_events": [event.as_dict() for event in self.visual_events],
            "motion_design": dict(self.motion_design),
            "assets": [asset.as_dict() for asset in self.assets],
            "audio_cues": [cue.as_dict() for cue in self.audio_cues],
            "subtitle": self.subtitle.as_dict() if self.subtitle else None,
            "evidence_refs": [ref.as_dict() for ref in self.evidence_refs],
            "quality_requirements": list(self.quality_requirements),
            "narration": self.narration,
            "status": self.status,
            "qc": dict(self.qc),
        }


# --------------------------------------------------------------------------- 影片


@dataclass(frozen=True)
class Film:
    title: str
    fps: int
    canvas: tuple[int, int]
    brand: str
    palette: Mapping[str, str]
    fonts: tuple[str, ...]
    scenes: tuple[Scene, ...]
    logline: str = ""

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "Film":
        _strict(
            payload,
            ("version", "title", "fps", "canvas", "brand", "palette", "fonts", "scenes", "logline"),
            "blueprint",
        )
        version = payload.get("version", 1)
        if version != 1:
            raise BlueprintError("bad_version", f"blueprint.version 只支持 1，收到 {version!r}")

        fps = payload.get("fps", 30)
        if not isinstance(fps, int) or fps < 1 or fps > 120:
            raise BlueprintError("bad_field", f"fps 必须是 1..120 的整数，收到 {fps!r}")

        canvas = payload.get("canvas") or [1080, 1920]
        if not isinstance(canvas, (list, tuple)) or len(canvas) != 2:
            raise BlueprintError("bad_field", "canvas 必须是 [宽, 高]")
        width, height = (_number(canvas[0], "canvas[0]"), _number(canvas[1], "canvas[1]"))
        if int(width) <= 0 or int(height) <= 0:
            raise BlueprintError("bad_field", "canvas 尺寸必须为正")

        palette = payload.get("palette") or {}
        if not isinstance(palette, Mapping) or not all(isinstance(v, str) for v in palette.values()):
            raise BlueprintError("bad_field", "palette 必须是 名字→颜色字符串 的对象")

        scenes_payload = payload.get("scenes") or []
        if not isinstance(scenes_payload, Sequence) or not scenes_payload:
            raise BlueprintError("no_scenes", "blueprint 里没有任何镜头")
        scenes = tuple(
            Scene.from_dict(item, f"scenes[{index}]") for index, item in enumerate(scenes_payload)
        )
        seen: set[str] = set()
        for scene in scenes:
            if scene.scene_id in seen:
                raise BlueprintError("duplicate_scene", f"scene_id 重复：{scene.scene_id}")
            seen.add(scene.scene_id)

        return Film(
            title=_text(payload.get("title"), "title"),
            fps=int(fps),
            canvas=(int(width), int(height)),
            brand=_text(payload.get("brand", "AK"), "brand"),
            palette=dict(palette),
            fonts=tuple(_text(item, "fonts") for item in (payload.get("fonts") or [])),
            scenes=scenes,
            logline=_text(payload.get("logline", ""), "logline", allow_empty=True),
        )

    # ---- 计算 -----------------------------------------------------------------

    def scene_frames(self, scene: Scene) -> int:
        """这个镜头占多少帧（四舍五入到整帧，且至少 2 帧）。"""
        return max(2, int(round(scene.duration * self.fps)))

    def total_frames(self) -> int:
        return sum(self.scene_frames(scene) for scene in self.scenes)

    def total_seconds(self) -> float:
        return self.total_frames() / self.fps

    def offsets(self) -> dict[str, float]:
        """每个镜头在整片里的起始秒数。"""
        result: dict[str, float] = {}
        cursor = 0
        for scene in self.scenes:
            result[scene.scene_id] = cursor / self.fps
            cursor += self.scene_frames(scene)
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "title": self.title,
            "logline": self.logline,
            "fps": self.fps,
            "canvas": list(self.canvas),
            "brand": self.brand,
            "palette": dict(self.palette),
            "fonts": list(self.fonts),
            "scenes": [scene.as_dict() for scene in self.scenes],
        }

    # ---- 渲染前检查 -----------------------------------------------------------

    def assert_renderable(self, root: Path | str) -> None:
        """镜头的入口文件与素材必须真的在磁盘上，否则不要开始渲染。"""
        base = Path(root)
        for scene in self.scenes:
            entry = base / scene.entry
            if not entry.is_file():
                raise BlueprintError("missing_entry", f"{scene.scene_id} 的入口文件不存在：{entry}")
            for asset in scene.assets:
                if not asset.path:
                    continue
                target = base / asset.path
                if not target.exists():
                    raise BlueprintError(
                        "missing_asset", f"{scene.scene_id} 引用的素材不存在：{asset.path}"
                    )


# --------------------------------------------------------------------------- 读写


def load_blueprint(path: Path | str) -> Film:
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BlueprintError("not_found", f"蓝图文件不存在：{target}") from error
    except json.JSONDecodeError as error:
        raise BlueprintError("bad_json", f"蓝图不是合法 JSON（{target}）：{error}") from error
    if not isinstance(payload, Mapping):
        raise BlueprintError("bad_json", "蓝图的顶层必须是对象")
    return Film.from_dict(payload)


def dump_blueprint(film: Film, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(film.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


__all__ = [
    "ASSET_TYPES",
    "AssetRef",
    "AudioCue",
    "BlueprintError",
    "CameraKey",
    "ENGINES",
    "EVENT_KINDS",
    "EvidenceRef",
    "Film",
    "REQUIRED_QUALITY",
    "SCENE_STATUS",
    "Scene",
    "Subtitle",
    "VisualEvent",
    "dump_blueprint",
    "load_blueprint",
]
