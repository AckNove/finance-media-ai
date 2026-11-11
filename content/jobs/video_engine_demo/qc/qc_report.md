# video_engine_demo 质检报告

被测文件：`content/jobs/video_engine_demo/output/preview_20_30s.mp4`
生成方式：`python -m openkatana call start_render`（job `5285cacafd2251478506f3621aea7ed0`）→ 本机 ffmpeg 4.2.2 编码
质检脚本：`content/jobs/video_engine_demo/script/qc_demo.py`（ffprobe + ffmpeg 滤镜，纯技术指标）
机读结果：`content/jobs/video_engine_demo/qc/qc_metrics.json`

## 一、结论

9 项技术指标全部通过（`passed: true`）。**本报告只判断技术指标，不判断"好不好看"**——观众喜好、
镜头语言是否有效、文案是否有说服力，都必须由人工或多模态模型评审，程序无法代判。

## 二、逐项结果

| 检查项 | 判据 | 实测 | 结果 |
|---|---|---|---|
| 分辨率 | 1080×1920（9:16 竖屏） | 1080×1920 | 通过 |
| 帧率 | 30 fps | 30/1 | 通过 |
| 时长 | 20–30 秒 | 26.022 秒 | 通过 |
| 音频流 | 存在且为 AAC | aac / 48000 Hz / 2 ch | 通过 |
| 音量 | 有声音、不削波 | mean −26.7 dB / max −15.5 dB | 通过 |
| 黑帧 | 无超过 0.45 秒的纯黑段 | 1 段，最长 0.23 秒（21.43–21.67 秒） | 通过 |
| 静音 | 无超过 0.8 秒的静音段 | 0 段 | 通过 |
| 画面冻结 | 无超过 0.6 秒的静止段 | 0 段 | 通过 |
| 镜头数 | ≥ 6（以时间轴声明为准） | 时间轴声明 6 个镜头 | 通过 |

## 三、需要人工判断的三件事

1. **音量偏低**：最大 −15.5 dB、平均 −26.7 dB，是本地合成配乐的保守电平；正式作品应做响度归一
   （目标 −14 LUFS 左右），当前脚本**没有**做这一步，是已知缺口。
2. **唯一黑帧是刻意的**：21.43–21.67 秒是进入结尾镜头的 `dip`（淡黑）转场，0.23 秒，属于设计内。
3. **场景自动检测在本片上失效**：深色低对比画面上 `scene` 分数几乎不变，检出 0 处画面突变，
   与"实际有 6 个镜头"不符。因此**镜头数以时间轴的 `shots` 为准**，自动检测只作旁证。

## 四、素材与合规

- 本片不含任何真实拍摄素材、不含新闻画面、不含第三方音乐音效；六块画板与配乐均为程序生成（B 类原创），
  字体 NotoSansSC（OFL-1.1）随项目目录复制。明细见 `content/jobs/video_engine_demo/assets_manifest.csv`。
- 画面中的数字（3.5 / 4 / 5 / 4.5 秒、75 个工具）在本片中作为**引擎能力示意**，不构成任何事实主张；
  片内已标注"示意数据 · 引擎能力演示"。
- 本片**没有旁白音轨**，只有合成配乐；未使用 SAPI 机器音冒充配音。

## 五、复现命令

```powershell
# 重新出片（约 6 分钟：六块画板渲染 + 时间轴渲染）
.\.venv\Scripts\python.exe content\jobs\video_engine_demo\script\build_demo.py --stage render
.\.venv\Scripts\python.exe content\jobs\video_engine_demo\script\build_demo.py --stage timeline

# 质检（需要把渲染结果复制到 output/ 之后）
.\.venv\Scripts\python.exe content\jobs\video_engine_demo\script\qc_demo.py
```
