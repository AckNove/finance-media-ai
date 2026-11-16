# V2 本轮交付报告（真实场景与电影级视觉整改）

日期：2026-11-16　分支：`main`

## 一、这一轮交付了什么

1. **15–20 秒验证片段**：`remaster_v2/output/prototype_15_20s.mp4`
   —— 18.000 秒 / 1080×1920 / 30fps / H.264 + AAC / 8,030,953 B / 技术 QC 8 项全过。
2. **真实素材体系**：12 项素材全部登记（11 字段），授权不明一律拒绝渲染。
3. **写实三维场景**：程序化开关场（非实拍、非 AI 生成影像，已标注）。
4. **评审与数据**：`qc/prototype_review.md`（六维评分 78.2）、`qc/prototype_qc.json`（技术判据）。

## 二、真实素材登记（节选）

完整表：`remaster_v2/assets/real/source_register.csv`

| 素材 | 内容 | 授权 | 商用 | 署名 | 用在 |
| --- | --- | --- | --- | --- | --- |
| R09 | 西安灞桥电厂（冷却塔 + 输电线 + 城市天际线） | CC0 | 允许 | 不需要 | A1 |
| R02 | 牵引变电站（变压器/构架/母线） | CC BY 4.0 | 允许 | **需要**（Saiphani02） | B1 |
| R11 | 鞍山输电铁塔落日 | CC BY 2.0 | 允许 | **需要**（Rincewind42） | E1（整片用） |
| R12 | 北京夜间工业 | CC BY 2.0 | 允许 | **需要**（Peter Morgan） | D1（整片用） |
| R01/R07/R10 等 | 保加利亚电厂 / 落日线路 / 朔州电厂 | CC0 / CC BY | 允许 | 视具体条目 | 备选 |

规则：只走维基共享资源；许可证白名单（CC0 / Public domain / CC BY 系列）；带 SA / NC / ND 的一律拒；
每份素材记录来源页、发布主体、协议、可否商用、可否改编、是否需署名、下载时间、SHA-256；
渲染器在生成镜头页之前会读登记表，**未登记或 `commercial_use != allowed` 直接抛错，渲不出来**。

## 三、样片的三个镜头

| 时间 | 镜头 | 层级 | 说明 |
| --- | --- | --- | --- |
| 0–5.4s | A1 真实电厂 | 实拍 | 慢推近，字幕「发电量在掉，电费收入却还在涨」 |
| 5.4–11.6s | B1 真实变电站 + 数据层 | 实拍融合 | 两条收入柱长在真实设备画面上：电量收入↓ / 容量收入↑ |
| 11.6–18.0s | C1 写实三维开关场 | 原创三维 | 变压器、套管、构架、母线、避雷器、投光灯，黄昏 + 雾 |

## 四、复现方式

```powershell
cd D:\finance-media-ai

# 1) 生成五个镜头页（实拍 4 个 + 三维 1 个）
.venv\Scripts\python.exe -X utf8 -m backend.scripts.build_v2_scenes
.venv\Scripts\python.exe -X utf8 -c "from backend.app.video.cinema.v2_three_d import build_substation_scene; build_substation_scene()"

# 2) 渲染 + 音频 + 拼片 + 质检（一步到底）
.venv\Scripts\python.exe -X utf8 -m backend.scripts.build_v2_prototype --stage all

# 3) 只重渲指定镜头
.venv\Scripts\python.exe -X utf8 -m backend.scripts.build_v2_prototype --stage render --only B1_substation --force
```

渲染器是 `backend/scripts/cinema_render.py`（Playwright + Chromium 逐帧截图 → ffmpeg 编码），
必须用 `tools\openkatana\.venv\Scripts\python.exe` 跑（仓库主 venv 里没有 playwright）。

## 五、没做的与阻塞

- **没有做整片**：样片评分 78.2 未过 85 关口，按任务书要求先整改。
- **三维质感未达标**：见 `qc/prototype_review.md` 第 3、5 节，三条整改项已列。
- **没有 AI 生成影像**：本轮用的全是真实拍摄 + 程序化三维，AI 生成这条路径没用上（也不需要）。
- **没有真实设备动作镜头**：现有素材是静照，运动靠摄影机与数据层，这是本轮"真实感"的上限所在。
  若要更强的真实感，需要真实视频素材（用户提供或授权来源），本机无视频类可商用素材库。

## 六、影响到谁

- 视频生产链新增两个脚本与两个模块：`build_v2_scenes.py`、`build_v2_prototype.py`、
  `backend/app/video/cinema/photo.py`（新增数据叠加层）、`backend/app/video/cinema/v2_three_d.py`。
- 既有 V1 交付（`remaster_v1/`、`output/final.mp4`）**一字未改**。
