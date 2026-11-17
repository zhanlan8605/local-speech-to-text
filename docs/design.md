# 设计文档

## 1. 总体架构

浏览器 / 服务器（B/S）单体架构：一个 FastAPI 进程同时提供静态页面与 JSON 接口，推理在进程内的后台线程中完成。

```
┌────────────────────────── 浏览器 (Chrome / Edge) ──────────────────────────┐
│  index.html + app.js + style.css                                          │
│    · 拖拽选择文件 / 麦克风录音（MediaRecorder）                            │
│    · 录音状态机、60 秒上限、权限与错误提示                                  │
│    · fetch POST /api/transcribe（FormData）                               │
│    · 轮询 GET /health 直到模型就绪                                         │
└───────────────────────────────────┬───────────────────────────────────────┘
                                    │ HTTP (127.0.0.1:8000)
┌───────────────────────────────────▼───────────────────────────────────────┐
│ FastAPI (uvicorn, 单进程)                                                  │
│                                                                           │
│  GET  /               → static/index.html                                 │
│  GET  /health         → 读取 engine.loaded，立即返回                       │
│  POST /api/transcribe → 校验 → 落盘 → 转码 → 推理 → 组装响应               │
│                                                                           │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────────┐   │
│  │ api.py       │──▶│ audio.py     │──▶│ asr/sensevoice.py            │   │
│  │ 参数与错误码  │   │ ffmpeg 16k   │   │ FunASR AutoModel             │   │
│  └──────────────┘   └──────────────┘   │ + fsmn-vad + 全局锁          │   │
│         │                              └──────────────────────────────┘   │
│         │                                          │                       │
│         ▼                                          ▼                       │
│  ┌──────────────┐                        ┌──────────────────┐             │
│  │ srt.py       │◀───────────────────────│ Transcript       │             │
│  │ txt / json   │                        │ (text, segments) │             │
│  └──────────────┘                        └──────────────────┘             │
└───────────────────────────────────────────────────────────────────────────┘
```

## 2. ASR 模型选型

### 2.1 候选对比（2026-11）

| 模型 | 参数 | 中英效果 | CPU 可行性 | 许可 | 结论 |
|---|---|---|---|---|---|
| **SenseVoiceSmall** | 234M | 中文强，英文可用 | **~20× 实时**（官方 CPU 数据） | FunASR 模型协议（可商用，需署名） | ✅ 选为默认 |
| Qwen3-ASR-1.7B | 1.7B | 开源 SOTA | 1.7B 解码器，CPU 实时性不足 | Apache 2.0 | 有 GPU 时的升级路径 |
| Whisper large-v3-turbo | 809M | 多语言均衡，中文偏弱 | CPU 可用但慢 | MIT | 需要 99 语言时再引入 |
| Paraformer-zh | 220M | 中文强，英文弱 | 很快 | Apache 2.0 | 需要字级时间戳/热词时引入 |

### 2.2 选择 SenseVoiceSmall 的理由

1. **CPU 优先**：非自回归结构，一次前向出完整文本，官方基准在 CPU 上约 20× 实时；1.7B 级 LLM-ASR 在普通笔记本上无法满足交互体验。
2. **中文质量**：同体积下中文 CER 显著优于 Whisper；对中英混说友好，不需要像 Whisper 那样强制指定语言。
3. **一体化输出**：自带语种识别、标点、数字归一化（ITN），减少后处理链路。
4. **生态成熟**：FunASR 同时提供 VAD、标点、说话人模型，后续增强不需要更换框架。

### 2.3 为什么还需要 fsmn-vad

SenseVoice 单次推理只处理约 30 秒音频，长音频必须切分。`fsmn-vad` 只有 0.4M 参数（约 1MB），本项目**独立加载**该模型（而不是交给 `AutoModel(vad_model=...)` 隐式串联），负责：

- 语音端点检测，去掉静音
- 为每个片段提供起止时间（毫秒），供 SRT 使用
- 避免长音频一次性进入模型导致内存暴涨

> 为什么不复用 FunASR 的内置串联：实测 FunASR 1.4.26 的 SenseVoice 管线在 `generate()` 中会把 VAD 切出的片段重新合并成一条结果（`merge_vad=True` 与 `False` 都一样），返回的 key 只有文件名、**没有任何时间戳**，`timestamp` 字段也不存在。为了让 SRT 有真实时间轴，只能单独跑一次 `fsmn-vad` 拿到 `[[start_ms, end_ms], ...]`，再按区间切音频、批量送入 SenseVoice。

### 2.4 升级与替换路径

| 需求变化 | 动作 |
|---|---|
| 有 NVIDIA GPU | `A2T_DEVICE=cuda`，代码不变 |
| 需要更高精度 | 把 `A2T_ASR_MODEL` 换成 Qwen3-ASR（需新增一个引擎实现） |
| 需要字级时间戳/热词 | 新增 `ParaformerEngine`，实现同一 `ASREngine` 协议 |
| FunASR 在目标机器装不上 | 新增 `SherpaOnnxEngine`（纯 wheel、无 torch），接口不变 |

## 3. 目录与模块职责

```
local-speech-to-text/
├── app/
│   ├── main.py          应用工厂、静态托管、生命周期（预热/卸载）
│   ├── config.py        环境变量配置、模型缓存目录、上传限制
│   ├── schemas.py       Pydantic 响应模型
│   ├── api.py           三个接口的实现与错误码
│   ├── audio.py         ffmpeg 转码、时长读取、扩展名校验
│   ├── postprocess.py   SenseVoice 标签清理、语种提取、空格归一
│   ├── srt.py           SRT 时间戳与分段渲染
│   └── asr/
│       ├── base.py      ASREngine 协议、Transcript/Segment 数据类
│       ├── sensevoice.py FunASR 实现（独立 VAD 分段 + 懒加载 + 单锁 + 预热）
│       └── fake.py      测试替身
├── static/              原生 HTML/CSS/JS，无构建步骤（含录音状态机）
├── tests/               pytest：单元 + 接口 + 慢速真模型
├── samples/             样例音频与真值
├── docs/                需求、设计、测试报告、AI 开发记录
└── scripts/             模型下载、样例生成
```

**分层原则**：`api.py` 只做参数校验与响应组装；`audio.py` 只做格式转换；`asr/*` 只做推理；`postprocess.py` / `srt.py` 只做文本处理。任何一层都可以被单独测试或替换。

## 4. 音频处理流程

输入有两条路径，最终汇合到同一条转码与识别链路：

1. **文件上传**：选择或拖入任意音频/视频文件；
2. **网页录音**：`MediaRecorder` 在浏览器内录音，**停止后**把整段音频作为 WebM/Opus 文件上传。

录音不引入新的后端接口，`/api/transcribe` 对两种来源完全一致；服务端只关心最终上传的文件。

```
上传文件
   │  分块写入临时文件（1MB/次，超过 10MB 立即 413）
   ▼
ffmpeg -i input -vn -ac 1 -ar 16000 -c:a pcm_s16le audio.wav
   │  失败 → 400（附 ffmpeg 最后一行错误）
   ▼
FunASR AutoModel（SenseVoiceSmall） + 独立 AutoModel（fsmn-vad）
   │  VAD 取语音区间 → 按区间切 16k 音频 → 分批识别 → 合并
   ▼
rich 标签清理 → 语种提取 → 文本拼接（中英边界补空格）
   │
   ├─ json：text + language + duration + elapsed + segments
   ├─ txt ：纯文本
   └─ srt ：按 VAD 分段生成时间轴（区间由 VAD 直接给出）
```

**分段策略**：单批累计语音时长上限 60 秒、最多 16 段，避免长音频一次性占满内存；短于 50ms 的片段直接丢弃。若 VAD 不可用或失败，退化为整段识别（此时 `segments` 的起止为 0，SRT 会渲染成一条覆盖全时长的字幕）。

**为什么统一转 16kHz 单声道**：ASR 模型内部固定按 16kHz 处理，提前重采样可以避免每次推理重复解码，也让任意输入格式走同一条路径。

**临时文件生命周期**：绑定 `TemporaryDirectory`，请求结束即删除，不保留用户音频。

### 4.1 浏览器录音（前端）

录音完全在前端完成，后端不感知来源：

```
点击 Start Recording
   → navigator.mediaDevices.getUserMedia({audio})
   → 权限拒绝/无设备/被占用 → 显示具体原因，回到 idle
   → new MediaRecorder(stream, WebM/Opus 优先)
   → 状态 recording，启动 100ms 计时器 + 60s 自动停止定时器
点击 Stop Recording（或到达 60s）
   → recorder.stop() → 收集 dataavailable → Blob
   → 停止麦克风轨道
   → new File([blob], "recording-<时间戳>.webm")
   → 复用 /api/transcribe 上传识别
   → 状态 done，Start Recording 重新可用
```

关键设计：

| 点 | 做法 | 原因 |
|---|---|---|
| 状态机 | `idle → requesting → recording → stopping → idle` | 按钮和上传区按状态禁用，避免并发录音/重复提交 |
| 60 秒上限 | `setTimeout` 到点调用 `stopRecording()`，`dataavailable` 回调再做一次兜底 | 后台标签页定时器会被节流，双保险确保不会无限录制 |
| 格式 | 依次探测 `audio/webm;codecs=opus`、`audio/webm`、`audio/ogg;codecs=opus`、`audio/mp4` | Chrome/Edge 原生 WebM，其他浏览器可退化；服务端已白名单 `.webm/.ogg/.m4a` |
| 提交时机 | 只在 `recorder.stop` 事件后构造 Blob 并识别 | 保证进入模型的是完整一段录音，不是半截流 |
| 计时 | 基于 `performance.now()` 的 100ms 更新 | 只影响显示，不受系统时间调整影响 |
| 权限错误 | `NotAllowedError` / `NotFoundError` / `NotReadableError` 等分别映射文案 | 用户能知道是拒绝授权、没设备还是被占用 |
| 麦克风释放 | `getTracks().forEach(track => track.stop())` | 停止后浏览器不再显示录音指示，保护隐私 |
| 再次录音 | 识别结束（成功/失败）后状态回 `idle`，Start 恢复可用 | 满足连续多段录音 |

> 录音本身不经过服务器，停止前音频只存在于浏览器内存；上传后服务端仍走「临时文件 → 转码 → 识别 → 删除」流程。`beforeunload` 会在录音或识别中提示离开页面。

### 4.2 音频回放（前端）

上传文件和录音共用一套自定义播放控件，底层是同一个隐藏的 `<audio>` 元素：

```
选择文件 / 录音结束
   → URL.createObjectURL(file) → <audio src>
   → 上传：loadedmetadata 读取真实时长
   → 录音：直接用计时器的 elapsed 作为时长（WebM 无元数据）
点击 ▶ 播放 / ⏸ 暂停
   → audio.play() / audio.pause()
   → play/pause 事件切换按钮图标与文案
拖动进度条 / timeupdate
   → currentTime = 比例 × 总时长 → 刷新 当前时间 / 总时长
播放到结尾
   → 复位到 0，按钮回到「播放」
```

关键设计：

| 点 | 做法 | 原因 |
|---|---|---|
| 控件 | 自定义按钮 + range + 时间文本，`<audio>` 仅作为播放引擎 | 原生控件在时长未知的 WebM 上显示 `0:00` 且样式不可控 |
| 录音时长 | `acceptFile(file, name, elapsed)` 直接传入录音计时 | MediaRecorder 的 WebM 没有 duration 元数据，`audio.duration` 为 `NaN` 或 `Infinity` |
| 上传时长 | `loadedmetadata` 读取；若仍为 `Infinity` 则把 `currentTime` 跳到极大值触发浏览器扫描 | 少数无索引的 WebM/MKV 也需要这一步 |
| 播放失败 | `error` 事件禁用按钮并提示「浏览器无法预览该音频格式，但仍可正常识别」 | 浏览器解码能力有限（WMA/AMR 等），而识别走服务端 ffmpeg |
| 资源释放 | 新文件先挂新 object URL，再释放旧 URL | 避免播放器短暂读到已失效地址 |
| 录音互斥 | 点击 Start Recording 时先暂停回放 | 防止扬声器声音被麦克风二次录入 |

> 回放完全是浏览器本地行为，音频不会因为播放而再次上传；识别仍然只在点击「开始识别」或录音停止后发生。

## 5. 接口设计

### GET /health

```json
{
  "status": "ok",
  "model_loaded": true,
  "device": "cpu",
  "backend": "funasr",
  "threads": 8,
  "max_upload_mb": 10,
  "version": "0.1.0"
}
```

`status` 为 `warming` 时表示模型仍在加载。该接口不获取推理锁，因此模型加载期间也能立即响应。`max_upload_mb` 为当前生效的上传上限，前端用它同步提示与本地校验，避免前后端上限不一致。

### POST /api/transcribe

请求（`multipart/form-data`）：

| 字段 | 位置 | 必填 | 默认 | 取值 |
|---|---|---|---|---|
| `file` | body | 是 | — | 音频/视频文件 |
| `language` | body | 否 | `auto` | auto / zh / en / ja / ko / yue |
| `response_format` | query | 否 | `json` | json / txt / srt |

响应：

- `json`：`{text, language, duration_s, elapsed_s, model, segments[]}`
- `txt`：`text/plain` 纯文本
- `srt`：`application/x-subrip` 字幕

错误码：

| 码 | 场景 |
|---|---|
| 400 | 空文件、语言/格式非法、音频解码失败 |
| 413 | 超过上传上限 |
| 415 | 扩展名不在白名单 |
| 500 | 模型未就绪或推理异常 |

上传上限采用两层校验：HTTP 中间件在 multipart 解析前检查 `Content-Length`（预留 64KB 边界与字段开销），明显超限直接返回 413；进入接口后再由 `_save_upload()` 按 1MB 分块累计，兜底处理未声明长度或头部不准确的情况。默认上限 10MB，可用 `A2T_MAX_UPLOAD_MB` 调整，并通过 `/health` 的 `max_upload_mb` 同步给前端。

### GET /

返回 `static/index.html`，页面内通过 `/static/*` 加载样式与脚本。

## 6. 并发与资源管理

- **单进程**：不启用多 worker。每个 worker 都会加载一份模型，CPU 上只会互相拖慢。
- **推理串行**：`SenseVoiceEngine` 内部使用一把 `RLock`，把「模型加载 + 推理」串行化。第二个并发请求会等待，而不是抢占 CPU。
- **不阻塞事件循环**：转码与推理通过 `run_in_threadpool` 执行，`/health` 与静态资源在推理期间仍可响应。
- **线程数**：默认 `min(8, os.cpu_count())`，可通过 `A2T_THREADS` 调整；`torch.set_num_threads()` 在加载时设置。
- **预热与卸载**：启动时后台线程加载模型；应用关闭时 `unload()` 释放引用。

## 7. 错误处理原则

1. 面向用户的错误一律转换为 `{"detail": "中文说明"}`，不返回堆栈。
2. 请求侧问题（格式、大小）在进入推理前拦截，避免浪费 CPU。
3. 模型错误与推理错误区分：前者由 `/health` 暴露，后者返回 500 并记录日志。
4. 上传采用流式校验，超限时立即中断，防止磁盘被占满。

## 8. 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `A2T_DEVICE` | `cpu` | `cpu` / `cuda` |
| `A2T_THREADS` | min(8, 核数) | 推理线程数 |
| `A2T_MAX_UPLOAD_MB` | `10` | 上传大小上限 |
| `A2T_ASR_MODEL` | `iic/SenseVoiceSmall` | ASR 模型 id |
| `A2T_VAD_MODEL` | `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` | VAD 模型 id，置空可关闭；`fsmn-vad` 别名也支持 |
| `A2T_PUNC_MODEL` | 空 | 设为 `ct-punc` 启用独立标点模型 |
| `A2T_MODELS_DIR` | `<项目>/models` | 模型缓存目录 |
| `A2T_OFFLINE` | `0` | 设为 `1` 强制离线模式 |

### 8.1 模型路径解析（离线关键）

`resolve_local_model()`（`app/config.py`）会在加载前把模型 id 解析成本地目录，避免 FunASR 在拿到「形似模型 id 的字符串」时仍然访问 ModelScope：

1. 传入的本身就是存在的目录 → 直接使用；
2. ModelScope 缓存布局 `<models>/models/<org>--<name>/snapshots/<version>` → 取最新快照；
3. 手工放置的 `<models>/<org>/<name>` → 直接使用；
4. 都没找到 → 原样返回 id，由 FunASR 触发下载。

同时维护 `fsmn-vad` → `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` 的别名映射，保证旧配置仍可用。

> 注意：即使传入本地目录，FunASR 仍会打印 `download models from model hub: ms`。这是 `AutoModel` 的无条件日志，实际 `download_from_ms()` 发现路径存在后直接跳过下载（源码 `os.path.exists(model_or_path)` 分支）。以是否出现 `Downloading N files from ...` 判断有没有真正联网。

## 9. 已知限制

1. **同步请求**：长音频会占用一个 HTTP 连接直到识别完成，刷新页面会丢失结果。
2. **无任务队列**：并发请求只是排队，没有进度反馈，只能看到前端计时。
3. **时间戳精度**：来自 VAD 分段，不是字级对齐；SRT 适合整句字幕。
4. **标点**：默认使用 SenseVoice 自带标点，中文场景足够；中英混排的英文标点可能不统一，可通过 `A2T_PUNC_MODEL=ct-punc` 改善。
5. **模型许可**：SenseVoiceSmall 权重使用 FunASR 模型开源协议 v1.1，商用需保留模型名称与来源署名。
6. **首次启动**：需要下载约 940MB 模型（SenseVoiceSmall 936MB + VAD 2MB），网络受限时请先运行 `scripts/download_models.py`。
7. **录音不是流式识别**：必须停止录音后才上传，无法边说边出字；真正的实时流式需要 WebSocket 与流式模型。
8. **录音格式依赖浏览器**：Chrome/Edge 产出 WebM/Opus；Safari/Firefox 的 `MediaRecorder` 行为未在本期验证。
9. **录音状态仅存于页面内存**：刷新页面会丢失尚未提交的录音（`beforeunload` 会提示）。
10. **回放依赖浏览器解码**：WMA、AMR 等格式浏览器可能无法播放，此时只影响预览，识别仍由服务端 ffmpeg 完成。
11. **上传上限 10MB**：以 128kbps MP3 估算约 10 分钟；更长的音频需要先压缩/切分，或调大 `A2T_MAX_UPLOAD_MB`。
