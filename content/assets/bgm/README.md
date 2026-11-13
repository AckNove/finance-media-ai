# 背景音乐曲库

这个目录只放**已经登记过授权**的背景音乐。默认是空的——空曲库走本地合成配乐
（`synthesize_score`，不含任何版权素材），这是正常状态，不是故障。

## 怎么加一首

1. 把音频文件放进本目录（支持 `.mp3 / .wav / .m4a / .aac / .flac / .ogg`）；
2. 在 `library.yaml` 的 `tracks` 里登记一条：

```yaml
tracks:
  - id: calm-piano-01
    title: 舒缓钢琴（示例占位，请填入真实曲名）
    file: calm-piano-01.mp3
    licence: cc-by-4.0          # 必须是可商用的授权，见下面白名单
    attribution: 作者名 / 曲名 / 来源链接
    source_url: https://example.com/track
    duration_seconds: 96
    mood: 舒缓
    notes: 只在结尾卡下做 6 秒淡出
```

## 规矩（代码里是硬的）

- 授权必须在白名单里：`cc0`、`public-domain`、`public_domain`、`cc-by`、`cc-by-4.0`、
  `cc-by-sa`、`royalty-free`、`purchased`、`own-work`。**不在名单里的直接拒绝渲染。**
- 需要署名的授权（`cc-by*`）必须写 `attribution`，否则拒绝。
- 登记的音频文件必须真的存在，格式必须是音频。
- 曲库不联网、不下载、不自动抓歌。

## 自检

```powershell
python -m backend.scripts.bgm_check --list
python -m backend.scripts.bgm_check --check calm-piano-01
```

`--list` 会把每条曲目的授权、是否可商用、是否缺署名、文件在不在都列出来；
`--check` 只验一首，不能用时退出码 1 并说明原因。
