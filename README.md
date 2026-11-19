# 本地语音转文字（Local Speech to Text）

基于 **SenseVoiceSmall + FunASR** 的本地离线语音识别服务：在浏览器里直接录音或上传音频，本机 CPU 完成推理，音频不出本机。

- 支持中文、英文，兼容中英混说；自动语种识别
- 支持网页实时录音（单次最长 60 秒）与文件上传两种输入
- 录音过程中**边说边出字**：WebSocket 分片传输，停止前即可看到不断更新的识别文本
- 上传的文件和录音都可在页面内回放，确认音频内容后再识别
- 全离线运行，首次下载模型后可断网使用
- 纯 CPU 可用，有 GPU 时切换一个环境变量即可加速
- 无前端构建步骤，原生 HTML / CSS / JavaScript
- 三个 HTTP 接口 + 一个 WebSocket 接口，适合二次集成

## 快速开始

Windows 11（Python 3.12）在项目根目录执行：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

然后打开 <http://127.0.0.1:8000>。首次启动会自动下载模型（约 940MB），下载完成后可离线使用。

## 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 11 x64（其他平台亦可，需自行准备 ffmpeg） |
| Python | 3.12 x64，且 `python --version` 可直接调用 |
| 内存 | 建议 4GB 以上 |
| 磁盘 | 约 2.5GB（依赖 + 模型） |
| 浏览器 | Chrome / Edge 最新稳定版 |
| 网络 | 首次安装依赖与下载模型需要网络，之后可离线 |

## 安装

在项目根目录执行一条命令即可安装全部依赖（含 CPU 版 PyTorch）：

```powershell
cd local-speech-to-text
python -m pip install -r requirements.txt
```

`requirements.txt` 顶部已经声明 CPU wheel 索引，因此 Windows 11 下会直接安装 CPU 版 `torch` / `torchaudio`，不需要再单独执行 PyTorch 安装命令。

> ffmpeg 通过 `imageio-ffmpeg` 随依赖一起安装，无需手动配置 PATH。

如需完全复现测试时的精确版本：

```powershell
python -m pip install -r requirements.lock.txt
```

> 建议使用虚拟环境隔离依赖（可选）：
> ```powershell
> py -3.12 -m venv .venv
> .\.venv\Scripts\Activate.ps1
> python -m pip install -r requirements.txt
> ```
> 激活后后续命令中的 `python` 即指向该虚拟环境。

GPU（可选）：默认安装 CPU 版。如需 CUDA，请先按 <https://pytorch.org> 的说明安装对应 CUDA 版 `torch` / `torchaudio`，再执行 `python -m pip install -r requirements.txt`（已满足的版本不会被覆盖），最后设置 `$env:A2T_DEVICE="cuda"` 后启动。

## 模型初始化

首次运行会自动从 ModelScope 下载模型到 `models/`（SenseVoiceSmall 约 940MB，VAD 约 2MB）。也可以提前下载：

```powershell
python scripts\download_models.py
```

下载完成后如需完全离线运行：

```powershell
$env:A2T_OFFLINE = "1"
```

## 启动

在项目根目录执行：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开浏览器访问 <http://127.0.0.1:8000>。

启动后模型在后台加载，页面右上角显示「模型加载中…」；变为「模型已就绪」即可使用（通常 10–20 秒）。

### 网页录音

页面上方的「实时录音」区域可以直接用麦克风说话：

1. 点击 **开始录音 Start Recording**，浏览器会弹出麦克风授权请求，选择「允许」；
2. 录音中会显示红点和「录音中，请说话…」，右侧实时显示 `已录时长 / 01:00`；
3. 说话过程中「实时识别」区域会持续出现文字：已确定的句子正常显示，正在说的尾部用浅灰色；底部「识别结果」框此时保持为空；
4. 点击 **停止录音 Stop Recording**，服务端补上最后一句并返回最终文本，状态变为「识别完成，可以再次录音」；
5. 录音同时生成可回放的 WebM，停止后可在文件信息区播放。

约束与说明：

- 单次录音最长 **60 秒**，到时自动停止并返回最终结果；
- 实时识别走 WebSocket，音频以 16kHz/16bit/单声道 PCM 分片发送，不经过容器封装；
- 如果浏览器不支持 AudioWorklet 或 WebSocket 连接失败，录音仍会继续，停止后自动改用 `/api/transcribe` 上传识别；
- 麦克风被拒绝、没有设备、被其他程序占用时会显示具体原因；
- 麦克风权限要求页面处于安全上下文：`http://127.0.0.1` / `http://localhost` 或 HTTPS。

### 实时识别（流式）实现

SenseVoiceSmall 不是流式模型，这里用「滚动重解码」实现近实时，不要求模型改造：

1. 前端用 AudioWorklet 采集麦克风，重采样为 16kHz、16bit、单声道 PCM，每约 128ms 发一帧（约 4KB）；
2. 服务端按会话累积「尚未提交」的音频，每新增约 1 秒音频重新解码一次；
3. 解码复用带 fsmn-vad 的识别流程：若缓冲区尾部已有 ≥0.45 秒静音，判定这句话说完，把结果写入 `committed` 并丢弃对应音频；否则整段结果作为 `partial` 返回；
4. 未提交缓冲超过 6 秒时强制提交，保证单次解码时长与内存有上限，长时间连续讲话也不会越来越慢；
5. 停止时对整段录音重新解码一次（最多 60 秒，约 2MB），修正增量提交可能切开的句子，最终文本与上传识别一致。

因此延迟通常在 **1–2 秒**量级（与机器性能、句间停顿有关），而不是等整段录音结束。

消息协议（`/ws/stream`）：

| 方向 | 内容 | 说明 |
|---|---|---|
| 客户端 → 服务端 | `{"type":"start","language":"auto"}` | 可选，指定语种；默认 `auto` |
| 客户端 → 服务端 | 二进制帧 | 16kHz/16bit/单声道 PCM，字节序小端 |
| 客户端 → 服务端 | `{"type":"stop"}` | 结束并请求最终结果 |
| 服务端 → 客户端 | `{"type":"ready",...}` | 连接就绪，返回 `sample_rate`、`max_seconds` |
| 服务端 → 客户端 | `{"type":"partial","text","committed","partial",...}` | 增量结果 |
| 服务端 → 客户端 | `{"type":"final","text","duration_s","elapsed_s","segments"}` | 最终结果 |
| 服务端 → 客户端 | `{"type":"error","detail"}` | 出错原因 |

命令行验证真实模型的流式效果（需先启动服务）：

```powershell
python scripts\check_stream.py                      # 中文样例，按 1 倍速发送
python scripts\check_stream.py samples\english.wav  # 英文样例
```

脚本会逐条打印中间结果及其出现时间，最后打印最终文本，便于观察端到端延迟。

### 音频回放

选择或录制音频后，文件信息下方会出现播放控件：

- 点击 **▶ 播放 / ⏸ 暂停** 回放当前音频，拖动进度条可跳转；
- 右侧显示 `当前时间 / 总时长`；
- 上传文件的总时长来自音频元数据，录音的总时长来自录音计时（MediaRecorder 的 WebM 没有时长元数据）；
- 浏览器无法解码的格式（如部分 WMA/AMR）会提示「浏览器无法预览该音频格式」，但**不影响识别**，服务端仍会用 ffmpeg 正常转码。

### 上传限制

单个文件最大 **10MB**（可用 `A2T_MAX_UPLOAD_MB` 调整）。后端先按 `Content-Length` 预判，明显超限的请求在解析前就返回 `413`；进入接口后还会按 1MB 分块累计兜底，超过上限立即中断。前端从 `/health` 读取同一上限，在选择文件时提前拦截。

### 命令行调用

```powershell
# JSON 结果
curl.exe -F "file=@samples/chinese.wav" "http://127.0.0.1:8000/api/transcribe"

# 纯文本
curl.exe -F "file=@samples/english.wav" "http://127.0.0.1:8000/api/transcribe?response_format=txt"

# SRT 字幕
curl.exe -F "file=@samples/chinese.wav" "http://127.0.0.1:8000/api/transcribe?response_format=srt" -o out.srt

# 健康检查
curl.exe "http://127.0.0.1:8000/health"
```

## 接口

| Method | Endpoint | 说明 |
|---|---|---|
| `GET` | `/health` | 服务健康检查，模型加载期间返回 `warming` |
| `POST` | `/api/transcribe` | 上传音频并识别（`file`、`language`、`response_format`） |
| `WS` | `/ws/stream` | 实时识别：发送 PCM 分片，持续返回增量文本 |
| `GET` | `/` | Web 页面 |

`POST /api/transcribe` 的 `response_format` 支持 `json`（默认）、`txt`、`srt`。上传的文件走该接口；网页录音优先走 `/ws/stream`，实时通道不可用时自动回退到该接口。

## 测试

项目区分「Mock 常规测试」和「真实模型测试」两类：

| 类型 | 命令 | 说明 |
|---|---|---|
| 常规自动化 | `python -m pytest -q` | 使用 `FakeASREngine` / 脚本化引擎模拟模型输出，不加载真实模型、不下载模型、不需要麦克风 |
| 真实模型 | `python -m pytest -m slow -v -s` | 加载本地 SenseVoiceSmall + fsmn-vad，验证中英文样例的真实识别效果与实时分片链路 |

`pytest.ini` 已配置 `addopts = -m "not slow"`，因此 `python -m pytest -q` 会自动跳过真实模型用例；只有显式加 `-m slow` 才会加载模型。

默认套件共 **82 项**，覆盖接口契约、错误码、文本后处理、音频转码、VAD 分段、模型路径解析、WebSocket 流式会话（句尾提交、强制提交、时长截断、停止整段重解码）、前端录音/回放/实时识别静态契约等，几秒内完成。前端录音用预生成的 WebM 音频夹具验证上传链路，**不依赖实际麦克风硬件**。

真实模型测试（3 项）：

- `tests/test_model_slow.py`：读取 `samples/chinese.wav`、`samples/english.wav` 和 `samples/reference.txt`，验证中文、英文的语种判断与字符相似度（阈值 0.6）；
- `tests/test_model_stream_slow.py`：用真实的「分片喂入 + 滚动重解码」链路跑中英文样例，断言停止前已出现部分文本、停止后得到最终文本；
- 三个用例通过会话级 fixture 共享一次模型加载；模型未缓存时首次运行会自动下载约 900MB，因此单独执行、不放入默认套件。

需要覆盖率时：

```powershell
python -m pytest --cov=app
```

详细用例与实际结果见 [docs/test-report.md](docs/test-report.md)。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `A2T_DEVICE` | `cpu` | 设为 `cuda` 使用 GPU |
| `A2T_THREADS` | `min(8, CPU 核数)` | 推理线程数 |
| `A2T_MAX_UPLOAD_MB` | `10` | 上传大小上限 |
| `A2T_STREAM_MAX_SECONDS` | `60` | 实时识别单次最长秒数；设为 `0` 关闭实时识别 |
| `A2T_ASR_MODEL` | `iic/SenseVoiceSmall` | ASR 模型 id |
| `A2T_VAD_MODEL` | `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` | VAD 模型 id（`fsmn-vad` 别名同样可用） |
| `A2T_PUNC_MODEL` | 空 | 设为 `ct-punc` 启用独立标点模型 |
| `A2T_MODELS_DIR` | `./models` | 模型缓存目录 |
| `A2T_OFFLINE` | `0` | 设为 `1` 强制离线 |

## 目录结构

```
local-speech-to-text/
├── app/            后端与模型集成（api.py / streaming.py / asr/）
├── static/         Web 前端（原生 HTML/CSS/JS，含 pcm-worklet.js）
├── tests/          pytest 测试
├── samples/        样例音频与真值
├── docs/           需求、设计、测试报告、开发记录
├── scripts/        模型下载、样例生成、打包、流式检查
├── requirements.txt
└── README.md
```

## 常见问题

**运行 `python -m uvicorn ...` 提示 `No module named uvicorn`？**
说明当前 `python` 与安装依赖时用的解释器不是同一个（例如系统里有 Anaconda）。用 `where python` 和 `python -m pip -V` 确认路径。最稳妥的是直接用项目虚拟环境启动：

```cmd
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

或在 cmd 中先 `.venv\Scripts\activate.bat`，再执行 `python -m uvicorn ...`。如果不使用虚拟环境，则必须用同一个解释器先执行 `python -m pip install -r requirements.txt`。

**首次请求很慢？**
模型加载需要 10–20 秒，启动时已后台预热；页面显示「模型已就绪」后再上传。

**识别多久？**
4 核 CPU 上约 5–15 倍实时：1 小时音频需要 4–12 分钟。GPU 会快一个数量级。

**能离线吗？**
模型下载完成后设置 `A2T_OFFLINE=1` 即可，运行期间不会访问网络。

**麦克风权限被拒绝怎么办？**
点击浏览器地址栏左侧的权限图标，把麦克风改为「允许」后刷新页面；也可以在同一位置重置权限，再点「开始录音」重新申请。录音功能需要 `localhost` / `127.0.0.1` 或 HTTPS 页面。

**文件能多大？**
默认单文件 10MB，可用 `A2T_MAX_UPLOAD_MB` 调整。超过上限时服务端返回 `413`，页面在选择文件时也会提前提示。

**录音能说多久？**
单次最长 60 秒，到时自动停止并识别；识别完成后可以立即开始下一段。

**录音能回放吗？**
可以。停止录音后文件信息区会出现播放按钮和进度条，可直接回放刚才的录音；上传的文件同样可以回放。回放只发生在浏览器本地，不会重复上传。

**播放按钮是灰色的 / 提示无法预览？**
说明当前浏览器不支持该音频编码（常见于 WMA、AMR 等）。识别由服务端 ffmpeg 完成，不受影响，可以直接点「开始识别」。

**支持实时麦克风吗？**
支持「先录音、停止后识别」的录音模式，暂不支持边说边出字的流式识别。实时流式需要额外的 WebSocket 接口和流式模型，属于后续规划。

**商用许可？**
SenseVoiceSmall 权重使用 [FunASR Model License v1.1](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)，允许商用，但需保留模型名称与来源署名。`fsmn-vad` 为 Apache 2.0。

## 分发给其他人

**不要打包 `.venv/`**：它约 1.4GB，且虚拟环境路径与机器绑定，复制到别的电脑不能直接使用。只打包源码即可（约 0.5MB）；`models/` 可选。

### 打包

可以直接复制文件夹后手动删除 `.venv/`、`models/`（按需）、`__pycache__/`、`.pytest_cache/` 和日志文件；也可以使用项目自带的打包脚本：

```powershell
# 仅源码（默认不包含 .venv / models）
powershell -ExecutionPolicy Bypass -File scripts\package.ps1

# 源码 + 模型（约 900MB，对方安装依赖后可离线启动）
powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -IncludeModels
```

输出位于 `dist\local-speech-to-text-<日期>-src.zip` 或 `dist\local-speech-to-text-<日期>-with-models.zip`。脚本会自动排除 `.venv`、`__pycache__`、`.pytest_cache`、`.idea`、`.vscode`、日志和临时文件。

对方电脑需要满足：

1. Windows 11 x64，安装 **Python 3.12 64 位**，且 `python --version`、`python -m pip -V` 都能正常执行并指向同一解释器；
2. 能访问 PyPI 与 `https://download.pytorch.org/whl/cpu`（安装依赖）；
3. 首次启动能访问 ModelScope（自动下载约 900MB 模型）；
4. 项目目录可写（模型缓存在 `models/` 下）；
5. 8000 端口未被占用。

然后在项目根目录执行：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

常见失败原因：

| 现象 | 原因 | 处理 |
|---|---|---|
| `No module named uvicorn` | `python` 与安装依赖的解释器不是同一个（如系统里有 Anaconda） | 用同一个 Python 安装并启动，或使用项目 `.venv` 的解释器 |
| `No module named torch` / 版本不兼容 | Python 不是 3.12 x64，或依赖未装完 | 确认 64 位 Python 3.12，重新执行安装命令 |
| 首次启动卡住或模型加载失败 | 无法访问 ModelScope | 检查网络；或在联网机器上执行 `python scripts\download_models.py` 后把 `models/` 一起打包 |
| 完全离线无法安装依赖 | pip 安装本身也需要网络 | 在有网机器上用 `pip download -r requirements.txt -d wheels` 准备离线包，再用 `pip install --no-index --find-links wheels -r requirements.txt` |
| `Address already in use` | 8000 端口已被占用 | 换端口 `--port 8001`，或停止占用进程 |

> 如果希望对方**完全离线**运行：把本机的 `models/`（约 900MB）一起打包，并保持目录结构为 `local-speech-to-text/models/...`；启动时默认会读取项目内的 `models/`，无需设置环境变量。

## 致谢

- [FunASR](https://github.com/modelscope/FunASR) / [SenseVoice](https://github.com/FunAudioLLM/SenseVoice)
- [FastAPI](https://fastapi.tiangolo.com/)
- [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)
