# 测试报告

## 1. 测试环境

| 项目 | 实测配置 |
|---|---|
| 操作系统 | Windows 10 家庭中文版 22H2（10.0.19045） |
| CPU | Intel Core i5-8400 @ 2.80GHz，6 核 6 线程 |
| 内存 | 23.9 GB |
| Python | 3.12.10（虚拟环境 `.venv`） |
| 推理设备 | CPU（`A2T_DEVICE=cpu`），线程数 6 |
| 浏览器 | Chrome / Edge 最新稳定版（手工验证） |

主要依赖版本：

| 包 | 版本 |
|---|---|
| torch | 2.15.0+cpu |
| torchaudio | 2.11.1+cpu |
| funasr | 1.4.26 |
| modelscope | 1.40.3 |
| transformers | 5.23.0 |
| fastapi | 0.163.1 |
| starlette | 1.10.0 |
| uvicorn | 0.54.0 |
| numpy | 2.5.5 |
| soundfile | 0.14.0 |
| imageio-ffmpeg | 0.8.1 |
| pytest | 9.2.0 |

> **环境偏差说明**：项目目标环境是 Windows 11 x64，本次实测机器为 Windows 10 22H2。项目依赖均为纯 Python 或官方预编译 wheel（未使用任何需要本地编译的包），两者行为一致；但严格来说，「Windows 11 实机复测」尚未执行，列入已知问题。

## 2. 测试策略

| 层次 | 做法 | 目的 |
|---|---|---|
| 单元测试 | 直接调用 `postprocess` / `srt` / `audio` 函数 | 覆盖文本清洗、中英拼接、格式转换、时长读取 |
| 接口测试 | `TestClient` + `FakeASREngine` | 毫秒级验证 HTTP 契约、错误码、参数传递 |
| 真实模型测试 | `python -m pytest -m slow`，加载 SenseVoiceSmall + fsmn-vad | 验证模型可加载、中英文可识别、语种判断正确 |
| 端到端验证 | 启动 uvicorn，用 curl 上传样例 | 验证完整链路、响应格式、SRT 时间轴、真实耗时 |
| 前端验证 | 浏览器手工操作 | 拖拽、上传、进度、复制、下载、错误提示 |
| 录音验证 | 浏览器 + 合成音源 + 权限 mock | 权限拒绝、录音、计时、60 秒自动停止、停止后识别、连续录音 |
| 离线验证 | `A2T_OFFLINE=1` + 无效代理启动 | 确认模型加载与识别不依赖网络 |

默认 `python -m pytest -q` 通过 `pytest.ini` 中的 `-m "not slow"` 排除真实模型用例；接口测试注入 `FakeASREngine`，录音格式用预生成 WebM 夹具验证。因此常规测试**不加载、不下载模型，也不依赖实际麦克风硬件**，几秒内完成。真实模型测试需要显式加 `-m slow` 单独执行。

## 3. 测试用例与结果

### 3.1 默认套件（不加载模型）

执行命令：

```powershell
python -m pytest -q
```

实际结果：**57 passed, 1 deselected in 5.74s**

> 为确认默认套件确实不依赖模型，另做一次隔离验证：把 `A2T_MODELS_DIR` 指向空目录，并设置 `A2T_OFFLINE=1`，再执行 `python -m pytest -q`。结果仍为 **57 passed, 1 deselected**（9.62s），空模型目录内未产生任何文件，说明默认测试没有触发模型下载或加载。

| 文件 | 用例数 | 覆盖内容 | 结果 |
|---|---|---|---|
| `tests/test_audio.py` | 5 | 扩展名白名单、任意音频转 16kHz 单声道、Opus/WebM 录音转码、损坏音频抛错、缺失文件时长为 0 | 全部通过 |
| `tests/test_health.py` | 3 | 模型就绪/加载中两种健康状态、上传上限字段、首页 HTML 可访问 | 全部通过 |
| `tests/test_postprocess.py` | 12 | 标签清除、中英文空格归一、零宽字符、语种提取、多分段拼接规则 | 全部通过 |
| `tests/test_transcribe_api.py` | 14 | JSON/txt/srt 三种输出、WebM 录音上传、语言参数透传、非法参数 400、非法扩展名 415、空文件 400、超限 413、Content-Length 预判 413、推理异常 500 | 全部通过 |
| `tests/test_engine.py` | 6 | VAD 毫秒区间解析、VAD 失败回退、按区间切音频、文本与时间戳对应、过短片段丢弃、推理异常包装 | 全部通过 |
| `tests/test_config.py` | 6 | ModelScope 缓存布局解析、多快照取最新、`fsmn-vad` 别名、手工目录、已有目录直用、找不到时回退模型 id | 全部通过 |
| `tests/test_frontend.py` | 9 | 录音控件、麦克风请求、60 秒上限、停止后才提交、权限/设备错误文案、再次录音、播放控件、WebM 时长探测、10MB 上限同步 | 全部通过 |
| `tests/test_packaging.py` | 2 | `requirements.txt` 自带 CPU 版 torch/torchaudio、锁文件包含 CPU 索引 | 全部通过 |

关键用例明细：

| 用例 | 断言 |
|---|---|
| `test_transcribe_returns_json` | `text/language/duration_s/elapsed_s/model/segments` 字段齐全，`segments` 元素含 `start/end/text` |
| `test_transcribe_srt_format` | 输出含 `-->`，时间轴为 `00:00:00,000 --> 00:00:04,000` |
| `test_transcribe_auto_language_passes_none` | `auto` 时向引擎传 `None`，由模型自行判断 |
| `test_rejects_too_large_file` | 上限设为 0 时返回 413，且不会读完整个文件 |
| `test_rejects_file_over_configured_limit` | 上限 1MB 时上传 1MB+1 返回 413，`detail` 含「1MB」 |
| `test_rejects_oversized_content_length_before_parsing` | `Content-Length` 超限时由中间件直接返回 413，不进入 multipart 解析 |
| `test_engine_failure_returns_500` | 引擎抛异常时返回 500，`detail` 含「识别失败」 |
| `test_join_texts_*` | 中文分段不补空格；英文分段补空格；中英相邻补空格；中文标点后不补 |
| `test_clean_text_removes_spaces_between_chinese_chars` | `"你好 世界 ，今天 天气 不错"` → `"你好世界，今天天气不错"` |
| `test_transcribe_accepts_webm_recording` | 浏览器录音格式 `.webm`（Opus）可直接上传并返回识别文本 |
| `test_convert_opus_webm_to_16k` | WebM/Opus 经 ffmpeg 转为 16kHz 单声道，时长与样例一致 |
| `test_js_only_submits_after_recorder_stops` | 源码顺序必须为「stop 事件 → 构造 Blob → transcribe」 |
| `test_js_requests_microphone_and_enforces_60_seconds` | 存在 `getUserMedia`、`MediaRecorder` 与 60000ms 自动停止逻辑 |
| `test_index_exposes_player_controls` | 页面含播放按钮、进度条、时间文本与隐藏的 `<audio>` |
| `test_js_plays_uploaded_and_recorded_audio` | 播放调用 `audio.play()`，录音也创建 object URL，并传入计时时长 |
| `test_js_fixes_missing_webm_duration` | 存在 WebM 时长探测与「无法预览但仍可识别」提示 |
| `test_upload_limit_is_10mb_and_synced_from_health` | 默认 10MB，前端从 `/health` 读取 `max_upload_mb` 并用于本地拦截 |
| `test_requirements_install_torch_cpu_together` | `requirements.txt` 含 CPU wheel 索引与 `torch` / `torchaudio`，一条命令即可安装 |
| `test_lock_file_is_self_contained` | 锁文件自带 CPU 索引，torch 版本带 `+cpu` 后缀 |

### 3.2 真实模型慢速测试

执行命令：

```powershell
python -m pytest -m slow -v -s
```

实际结果：**1 passed, 57 deselected in 161.89s**（本次模型加载耗时受机器负载影响；此前同样用例约 24–76s。中文 `ratio=0.960, lang=zh`，英文 `ratio=1.000, lang=en`）

首次运行时模型尚未下载，同一用例耗时 **139.84s**，其中约 70 秒用于下载 SenseVoiceSmall（936MB）与 fsmn-vad（2MB）。

## 4. 真实模型识别效果

样例音频由 Windows SAPI 语音合成生成，真值见 `samples/reference.txt`。评测指标为去掉空格与标点后的字符相似度（`difflib.SequenceMatcher`）。

| 样例 | 音频时长 | 期望语种 | 实际语种 | 字符相似度 | 结果 |
|---|---|---|---|---|---|
| `chinese.wav` | 7.83s | zh | zh | **0.960** | 通过 |
| `english.wav` | 7.61s | en | en | **1.000** | 通过 |

识别文本对照：

| 样例 | 真值 | 实际输出 |
|---|---|---|
| 中文 | 今天天气不错，我们下午三点在会议室开会。请大家准时参加。 | 今天天气不错，我们下午3点在会议室开会，请大家准时参加。 |
| 英文 | The quick brown fox jumps over the lazy dog. This audio is used to test a local speech recognition system. | The quick brown fox jumps over the lazy dog. This audio is used to test a local speech recognition system. |

中文的 0.040 差异来自 SenseVoice 的逆文本归一化（ITN）：把「三点」写成「3点」，语义正确，仅数字写法不同。英文完全一致。

## 5. 端到端 HTTP 验证

服务启动方式：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 5.1 健康检查

模型加载期间：

```json
{"status":"warming","model_loaded":false,"device":"cpu","backend":"funasr","threads":6,"version":"0.1.0"}
```

加载完成后：

```json
{"status":"ok","model_loaded":true,"device":"cpu","backend":"funasr","threads":6,"version":"0.1.0"}
```

### 5.2 中文识别（JSON）

```powershell
curl.exe -F "file=@samples/chinese.wav" "http://127.0.0.1:8000/api/transcribe"
```

```json
{
  "text": "今天天气不错，我们下午3点在会议室开会，请大家准时参加。",
  "language": "zh",
  "duration_s": 7.827,
  "elapsed_s": 0.67,
  "model": "SenseVoiceSmall",
  "segments": [
    {"start": 0.0, "end": 7.81, "text": "今天天气不错，我们下午3点在会议室开会，请大家准时参加。"}
  ]
}
```

HTTP 200，端到端耗时 0.80s（含上传、ffmpeg 转码、推理、序列化）；纯推理 0.67s，约 **11.7× 实时**。

### 5.3 英文识别

```powershell
curl.exe -F "file=@samples/english.wav" "http://127.0.0.1:8000/api/transcribe"
```

```json
{
  "text": "The quick brown fox jumps over the lazy dog This audio is used to test a local speech recognition system.",
  "language": "en",
  "duration_s": 7.613,
  "elapsed_s": 0.663,
  "model": "SenseVoiceSmall",
  "segments": [
    {"start": 0.0, "end": 7.59, "text": "The quick brown fox jumps over the lazy dog This audio is used to test a local speech recognition system."}
  ]
}
```

HTTP 200，端到端 0.79s，纯推理 0.66s。`response_format=txt` 时直接返回上述 `text`（`text/plain; charset=utf-8`）。

> 该次输出在「dog」后没有句号；慢速测试直接读取原始 22.05kHz 样例时是有句号的。两种结果都来自同一模型，差异由输入重采样与解码的细微波动造成，不影响可懂度。规范化后相似度仍为 1.000。

### 5.4 SRT 字幕与多分段

把中文样例、1 秒静音、英文样例拼成 16.44 秒音频后请求 `response_format=srt`：

```srt
1
00:00:00,000 --> 00:00:11,620
今天天气不错，我们下午3点在会议室开会，请大家准时参加。 The quick brown fox jumps over the lazy dog。

2
00:00:12,100 --> 00:00:16,420
This audio is used to test a local speech recognition system.
```

JSON 模式下同一文件返回两个 `segments`（0–11.62、12.1–16.42），说明 VAD 时间轴真实可用。端到端 1.55s，纯推理 1.43s，约 **11.5× 实时**。

> 注意：1 秒的静音没有被 VAD 切开，中文与紧随其后的英文落在同一段内，因此第一块字幕横跨 11.62 秒。这是 VAD 静音阈值导致的，不是时间戳错误。

### 5.5 浏览器界面

用 Chrome（OpenCode 内置浏览器，Chromium）打开 `http://127.0.0.1:8000`，控制台无报错，然后：

1. 拖入 `samples/chinese.wav`，文件信息与音频播放控件正常显示；
2. 点击「开始识别」，按钮进入禁用态并显示计时；
3. 识别完成后结果框显示「今天天气不错，我们下午3点在会议室开会，请大家准时参加。」；
4. 元信息显示「语言：zh · 音频时长：7.8s · 推理耗时：0.8s · 模型：SenseVoiceSmall」；
5. 「复制」「下载」按钮由禁用变为可用；点击下载实际生成 `chinese.txt`，内容为 UTF-8 的正确识别文本；
6. 右上角状态为「模型已就绪」。

复制按钮点击后未报错；浏览器自动化环境拒绝了剪贴板读取权限，因此未对剪贴板内容做断言，需人工在真实窗口点一次确认。其余前端功能（拖拽、进度、下载、错误提示）均正常。

### 5.6 离线模式验证

```powershell
$env:A2T_OFFLINE = "1"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --log-level warning
```

在模型已缓存的前提下，服务正常启动并完成中文识别：

```json
{"text":"今天天气不错，我们下午3点在会议室开会，请大家准时参加。","language":"zh","duration_s":7.827,"elapsed_s":0.771,"model":"SenseVoiceSmall","segments":[{"start":0.0,"end":7.81,"text":"今天天气不错，我们下午3点在会议室开会，请大家准时参加。"}]}
```

日志中仍有 2 条 FunASR 的 `download models from model hub: ms`，但**没有任何 `Downloading N files from ...` 行**，说明并未真正联网。该日志是 `funasr/auto/auto_model.py` 中 `if "model_conf" not in kwargs` 分支的无条件打印；随后 `download_from_ms()` 发现传入的是已存在的目录（`os.path.exists(model_or_path)`），直接跳过下载并加载本地 `model.pt`。

为排除「日志没打印但仍有请求」的可能，又做了一次更严格的验证：把 `HTTP_PROXY` / `HTTPS_PROXY` 指向必然失败的 `http://127.0.0.1:9`，同时设置 `A2T_OFFLINE=1`，服务仍正常加载模型并完成中文识别（`elapsed_s=0.786`）。如果加载过程中有任何 HTTP 请求，都会因连接被拒绝而失败，因此可以确认模型加载完全走本地文件。

> 修复前（未做本地路径解析时）同样命令下会出现 `Downloading 20 files from iic/SenseVoiceSmall@master` 与 VAD 的 `Downloading 8 files`，即使断网也能加载，但会先等待网络超时。现在通过 `resolve_local_model()` 提前把 id 解析成本地快照目录，彻底避免这段等待。

### 5.7 实时录音功能验证

验证环境为 OpenCode 内置 Chromium（与 Chrome 同源），页面 `http://127.0.0.1:8000`。

**验证方式**：浏览器自动化无法真实点击系统级「允许麦克风」弹窗，因此用两种方式覆盖：

1. 权限拒绝：把 `navigator.mediaDevices.getUserMedia` 替换为返回 `NotAllowedError`；
2. 正常录音：用 Web Audio 把 `samples/chinese.wav` 接入 `MediaStreamAudioDestinationNode`，作为 `getUserMedia` 返回的麦克风流。这样仍然是**真实的 MediaRecorder 编码 + 真实后端转码与推理**，只是音源来自文件而不是物理麦克风。

| 场景 | 操作 | 实际结果 |
|---|---|---|
| 权限被拒绝 | Mock `NotAllowedError` 后点击 Start Recording | 状态「麦克风不可用」，错误提示「麦克风权限被拒绝。请点击地址栏的麦克风图标…」，Start 仍可重试，Stop 保持禁用 |
| 正常录音 | 录制 9 秒中文语音后点击 Stop Recording | 计时 `00:09 / 01:00`，文件 `录音 00:09（116.4 KB）`，自动提交后返回「今天天气不错，我们下午3点在会议室开会，请大家准时参加。」，元信息「语言：zh · 音频时长：7.8s · 推理耗时：0.7s」，状态「识别完成，可以再次录音」，Start 恢复可用 |
| 60 秒上限 | 持续录音不手动停止 | 计时到 `00:55` 仍为「录音中」；`01:00` 时自动停止并提交，状态回到「识别完成，可以再次录音」，不会无限录制 |
| 无语音音频 | 录制 3 秒纯音后停止 | 提示「没有识别到语音内容，请检查录音或音频后重试。」，状态「未识别到语音内容，可以重试」，Start 恢复可用 |
| 连续录音 | 同一页面完成 3 次录音 | 每次结束后 Start 都恢复，第三次仍能正常提交 |
| 录音回放 | 录制 5 秒后点击播放 | 总时长显示 `00:05`，播放 1.5s 后进度为 `00:01 / 00:05`，按钮变为「暂停」；暂停后回到「播放」 |
| 录音进度拖动 | 录音回放时把进度条拖到 50% | `currentTime` 跳到 2.52s，时间文本变为 `00:02 / 00:05` |
| 播完复位 | 从 4.85s 开始播放到结尾 | 自动暂停并复位到 0，按钮回到「播放」 |
| 上传回放 | 拖入 `chinese.wav` 后播放 | 总时长 `00:07`，播放中进度与按钮状态正确；拖到 50% 时 `currentTime=3.91s` |
| 无法预览 | 拖入浏览器无法解码的音频 | 播放/进度按钮禁用，提示「浏览器无法预览该音频格式，但仍可正常识别。」；识别流程不受影响 |
| 控制台 | 全程观察 | 无 JS 错误 |

> 60 秒用例中，合成音源本身只有 7.8 秒语音，因此后端报告的音频时长仍是 7.8s；该用例验证的是计时与自动停止逻辑，真实麦克风会持续供给 60 秒音频。
>
> MediaRecorder 生成的 WebM 缺少时长元数据，`audio.duration` 为 `NaN`/`Infinity`。现在录音场景直接把计时得到的 `elapsed` 传给播放器作为总时长，进度条与时间文本正常；上传的 WebM 若同样缺少元数据，则通过把 `currentTime` 跳到极大值触发浏览器扫描来补全。
>
> 未覆盖项：物理麦克风、真实授权弹窗、拔插设备。自动化环境无法完成系统级授权，需人工在真实设备上点一次「允许」确认，已记入 K12。

### 5.8 上传大小限制验证

`A2T_MAX_UPLOAD_MB` 默认 **10**，`GET /health` 返回 `"max_upload_mb": 10`；前端读取该值并显示「单个文件最大 10MB」。后端有两层校验：

1. HTTP 中间件在 multipart 解析前检查 `Content-Length`（预留 64KB 边界/字段开销），明显超限直接返回 413；
2. `_save_upload()` 再按 1MB 分块累计字节，覆盖未声明长度或头部不准确的情况，超过上限立即中断。

| 场景 | 操作 | 实际结果 |
|---|---|---|
| 后端超限 | 创建 11MB `big.wav`，`curl -F file=@big.wav /api/transcribe` | HTTP 413，`{"detail":"文件超过 10MB 上限"}` |
| 边界值 | 恰好 10MB 的无效音频 | 通过大小校验（未被 413 拦截），因内容不是音频返回 400，证明上限为「≤ 10MB」 |
| 提前拒绝 | 请求头声明超大 `Content-Length` | 中间件直接 413，不读取完整 body、不进入 multipart 解析 |
| 前端超限 | 在页面构造 11MB `File` 并拖入 | 立即提示「文件过大：11.0 MB，上限 10MB」，不进入识别，文件信息区不显示 |
| 上限同步 | `/health` 增加 `max_upload_mb` | 页面提示文字自动显示后端当前值，避免前后端写死不一致 |
| 录音场景 | 60 秒 WebM/Opus 录音（约 0.5–1MB） | 远低于 10MB，正常提交识别 |

> 上限可通过 `A2T_MAX_UPLOAD_MB` 调整，修改后重启服务并刷新页面即可，前端会自动同步，无需改代码。

### 5.9 统一启动方式验证

Windows 11 上不再需要单独安装 PyTorch，也不依赖虚拟环境激活脚本，两条命令即可：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

验证内容：

| 检查 | 操作 | 实际结果 |
|---|---|---|
| 全新环境安装 | 新建空 venv，执行 `python -m pip install -r requirements.txt` | `pip exit 0`；导入版本：torch `2.15.0+cpu`、torchaudio `2.11.1+cpu`、fastapi `0.163.1`、uvicorn `0.54.0`、funasr `1.4.26`、modelscope `1.40.3` |
| 模块启动 | 用该全新环境执行 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8020` | 正常启动，无需激活脚本或 PATH 中的 `uvicorn.exe` |
| 健康检查 | `GET http://127.0.0.1:8020/health` | `{"status":"ok","model_loaded":true,...,"max_upload_mb":10}` |
| 页面与识别 | 打开首页并上传 `samples/chinese.wav` | 首页 HTTP 200，识别返回「今天天气不错，我们下午3点在会议室开会，请大家准时参加。」，推理 1.05s |

> 该用例完整模拟了「新电脑只装 Python 3.12，然后执行两条命令」的场景，依赖是从索引重新解析安装的，不是复用项目 `.venv`。模型使用项目内已有的 `models/` 缓存；如果交付包不含该目录，首次启动会额外从 ModelScope 下载约 900MB，需要网络。

## 6. 性能数据

| 场景 | 音频时长 | 推理耗时 | 实时倍率 |
|---|---|---|---|
| 中文样例 | 7.83s | 0.67s | 11.7× |
| 英文样例 | 7.61s | 0.66s | 11.5× |
| 中英拼接 | 16.44s | 1.43s | 11.5× |
| 网页录音（9 秒录音，含 7.8s 语音） | 7.83s | 0.70s | 11.2× |
| 模型加载（已缓存，冷启动一次） | — | 约 10s | — |

以上为 6 核 CPU、`A2T_THREADS=6` 下的单请求数据。长音频按 VAD 分段批处理，内存占用受 60 秒/16 段上限约束，不会随音频时长线性增长。

## 7. 已知问题与限制

| 编号 | 问题 | 影响 | 现状 |
|---|---|---|---|
| K1 | 实测环境为 Windows 10 22H2，非目标 Windows 11 | 未在 Win11 实机复测 | 依赖均为预编译 wheel，预期一致；后续在 Win11 机器上补跑一次即可 |
| K2 | FunASR 1.4.26 内置 SenseVoice 管线不返回时间戳 | 若直接使用内置串联，SRT 只有一条字幕 | 已改为独立 fsmn-vad 取区间，绕开该问题（见 `docs/ai-development.md` 4.7） |
| K3 | 时间戳粒度是 VAD 片段，不是字级 | 字幕按句/段切分，无法做卡拉 OK 式逐字高亮 | 设计如此；需要字级时间戳时应换 Paraformer 等支持字级对齐的模型 |
| K4 | ITN 会把中文数字改写成阿拉伯数字 | 「三点」→「3点」 | 语义正确，属于预期行为；如需保留中文数字可关闭 `use_itn` |
| K5 | 中英混说时标点/空格可能不一致 | 英文句末偶尔出现中文句号「。」，多次识别同一音频的空格也可能不同 | SenseVoice 自带标点模型的行为，且随输入重采样有小幅波动；可接 `ct-punc` 独立标点模型改善 |
| K6 | 短静音不会切段 | 两句话间隔 1 秒时仍可能落在同一 VAD 片段 | VAD 静音阈值决定；属于分段粒度取舍 |
| K7 | 同步接口 | 长音频占用一个 HTTP 连接，刷新页面会丢失结果，无服务端进度 | 明确的 v1 取舍，异步任务队列留待后续 |
| K8 | 并发请求串行 | 第二个请求的耗时包含排队时间 | CPU 上并行推理只会互相拖慢，属有意设计 |
| K9 | 扩展名白名单校验，未做内容嗅探 | 把非音频内容改成 `.wav` 才会进入 ffmpeg，最终返回 400 | 由 ffmpeg 兜底，错误信息可读 |
| K10 | 样例覆盖有限 | 仅有合成 TTS 的中英文各一条，未覆盖噪声、口音、方言、长音频 | 慢速测试只做冒烟；真实业务音频需要另行评估 |
| K11 | 首次请求需等模型加载 | 冷启动约 10 秒，期间 `/health` 返回 `warming` | 启动时已后台预热；前端轮询状态提示 |
| K12 | 录音链路用合成音源 + 权限 mock 验证 | 未在物理麦克风和真实授权弹窗上复测 | 编码、上传、识别链路均为真实浏览器行为；需人工在真机点一次「允许」确认 |
| K13 | 录音格式依赖浏览器 | Chrome/Edge 为 WebM/Opus，Firefox/Safari 的 MediaRecorder 未验证 | 前端已按能力探测 mimeType，服务端支持 `.webm/.ogg/.m4a`；目标浏览器为 Chrome/Edge |
| K14 | 录音为「先录后传」 | 无法边说边出字 | 与 FR-12 一致；流式识别在范围之外 |
| K15 | 录音只保存在页面内存 | 刷新或关闭会丢失未提交的录音 | `beforeunload` 已提示；正式使用请先停止并等待识别完成 |

## 8. 复现步骤

```powershell
cd local-speech-to-text
python -m pip install -r requirements.txt

# 默认回归（约 3 秒）
python -m pytest -q

# 真实模型冒烟（首次需下载约 940MB）
python -m pytest -m slow -v -s

# 启动服务后手工验证
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
curl.exe -F "file=@samples/chinese.wav" "http://127.0.0.1:8000/api/transcribe"

# 浏览器打开 http://127.0.0.1:8000
# 1) 拖入 samples/chinese.wav → 点击播放确认音频，再开始识别
# 2) 允许麦克风 → 开始录音 → 说话 → 停止录音 → 回放并查看识别结果
```

## 9. 结论

- 默认测试套件 57 项全部通过，覆盖参数校验、错误码、文本后处理、音频转码、VAD 分段映射、模型路径解析、安装启动契约与录音/回放前端契约。
- 真实模型在中英文样例上分别达到 0.960 / 1.000 的字符相似度，语种判断正确。
- 端到端 HTTP 链路可用，JSON / txt / SRT 三种输出均验证通过，SRT 时间轴来自真实 VAD 区间。
- 浏览器界面的拖拽上传、识别、元信息、下载均验证通过，控制台无报错。
- 实时录音完成权限拒绝、正常录音、60 秒自动停止、无语音提示、连续录音五项验证；录音经真实 MediaRecorder 编码后可直接被后端识别。
- 上传文件与录音都能在页面内播放/暂停、拖动进度、显示时间；无时长元数据的 WebM 录音用计时时长补全，无法解码的格式会提示但不影响识别。
- 后端默认 10MB 上传上限生效：超限请求在 `Content-Length` 预判或分块累计阶段即返回 413；前端从 `/health` 同步上限并在选择文件时提前拦截。
- 离线模式（`A2T_OFFLINE=1` + 无效代理）下模型仍能加载并识别中文，确认加载不依赖网络。
- 统一启动方式验证通过：全新环境中 `python -m pip install -r requirements.txt` 会解析出 CPU 版 PyTorch，`python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` 可直接启动，无需额外安装 torch 或激活虚拟环境。
- 主要遗留项是 Windows 11 实机复测、物理麦克风授权复测、字级时间戳、长音频/真实场景效果评估，均已在第 7 节列明。
