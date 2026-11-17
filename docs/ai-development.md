# AI 辅助开发记录

本文记录使用 OpenCode 完成本项目的过程：任务如何拆分、AI 产出了什么、哪些地方需要人工判断、以及遇到的问题和处理方式。

## 1. 工作方式

- **工具**：OpenCode（终端 AI 编码代理），模型通过 OpenCode 调用
- **流程**：先以 Plan 模式讨论选型与方案 → 人工确认接口与目录结构 → 切 Build 模式实施 → 每阶段跑测试再进入下一阶段
- **原则**：AI 负责代码与文档草稿，人负责三件事——确认需求边界、审查关键决策、验证真实模型效果

## 2. 任务拆分

| 阶段 | 任务 | 产出 |
|---|---|---|
| P0 | 环境验证、目录骨架、依赖安装 | venv、requirements、可启动的空应用 |
| P1 | 音频转码、SenseVoice 引擎、`/api/transcribe`、Web 页面 | 可用的文件转写 MVP |
| P2 | 标点模型开关、SRT 输出 | 字幕导出 |
| P3 | 测试补齐、文档 | 四份文档 + 测试报告 |
| P4（规划） | WebSocket 实时流式 | 见 design.md「已知限制」 |

拆分依据：**先打通最长的链路**（上传 → 转码 → 推理 → 返回），再补附属能力。这样最早的失败点（模型能否装、能否跑）会在 P0/P1 暴露，而不是等到最后。

## 3. 关键决策

| 决策 | 选择 | 理由 | 被否决的方案 |
|---|---|---|---|
| ASR 模型 | SenseVoiceSmall | CPU 上 20× 实时，中文质量好，自带标点与语种 | Qwen3-ASR-1.7B（CPU 跑不动）、Whisper（中文弱） |
| 分段 | fsmn-vad | 1MB，与 FunASR 原生集成，提供时间戳 | 纯 ffmpeg 静音检测（边界不准） |
| 接口形态 | 同步 `POST /api/transcribe` | 契约简单，前端一次请求拿结果 | 任务队列 + 轮询（多两个接口、多一套状态机） |
| 并发 | 单进程 + 单锁串行 | CPU 上并行只会互相拖慢 | 多 worker（每个都加载一份模型） |
| 前端 | 原生 HTML/CSS/JS | 零构建、零 npm，改起来直观 | React + Vite（引入构建链） |
| 模型缓存 | 项目内 `models/` | 可随项目迁移，便于离线分发 | 用户目录缓存（不利于打包） |

## 4. 遇到的问题与处理

### 4.1 选型阶段的信息过时风险

**问题**：ASR 领域迭代快，凭训练数据里的印象选型容易选到已被超越的模型。
**处理**：开发前用联网检索核对 2026 年的模型横评（Qwen3-ASR、SenseVoice、Whisper、Parakeet、Nemotron），确认「CPU 场景 + 中英文」下 SenseVoice 仍是最优解，并把对比写进 `design.md`，方便日后复核。

### 4.2 torch 与 torchaudio 版本不一致

**现象**：从 CPU 源安装得到 `torch 2.15.0+cpu` 与 `torchaudio 2.11.1+cpu`，两者主版本号不同。
**处理**：先验证 `import torch, torchaudio` 是否成功；本项目不直接调用 torchaudio API（由 funasr 间接使用），因此只要导入正常即可接受。若后续推理报符号错误，降级 torchaudio 到与 torch 匹配的版本。

### 4.3 funasr 依赖链在 Python 3.12 + Windows 上的可行性

**风险**：funasr 会带入 `numba` / `llvmlite` / `umap-learn` 等含本地扩展的包，历史上有 Python 版本与 Windows 轮子缺失的问题。
**结果**：本次安装全部获得预编译轮子，`llvmlite`、`numba`、`umap_learn` 均正常安装，未触发编译。
**兜底方案**：若目标机器安装失败，改用 `sherpa-onnx`（纯 wheel、无 torch），只需新增一个 `ASREngine` 实现。

### 4.4 GitHub 原始文件不可达

**问题**：计划从 whisper.cpp 仓库下载 `jfk.wav` 作为英文样例，`raw.githubusercontent.com` 连接被重置。
**处理**：改用 Windows 内置语音合成（SAPI）生成中英文样例，文本已知，`samples/reference.txt` 的真值因此完全可靠；生成逻辑固化在 `scripts/make_samples.ps1`，可重复执行。

### 4.5 PowerShell 5.1 无法解析含中文的脚本

**现象**：`make_samples.ps1` 保存为 UTF-8 无 BOM，Windows PowerShell 5.1 按 ANSI 解码，中文变成乱码后引号配对错乱，报 `UnexpectedToken`。
**处理**：将脚本重写为 UTF-8 with BOM。这是 Windows 上分发含中文脚本的通用坑，已记录在 README 的脚本说明中。

### 4.6 同步接口的取舍

**问题**：只有三个接口时，长音频转写会长时间占用一个 HTTP 请求，且无法展示服务端进度。
**处理**：接受该限制并在 `docs/requirements.md` 的「边界条件」中写明；前端用本地计时器缓解等待焦虑，并加 `beforeunload` 防止误关闭。真正的进度反馈留给后续的异步任务版本。

### 4.7 FunASR 不返回时间戳，SRT 只能自己算

**现象**：按 FunASR 文档把 `vad_model` 传给 `AutoModel`，期望 `generate()` 返回按 VAD 切分的多条结果。实测 funasr 1.4.26 无论 `merge_vad=True` 还是 `False`，都只返回一条 `{"key": "<文件名>", "text": "..."}`，key 里没有时间区间，也没有 `timestamp` 字段。结果是 JSON 的 `segments` 起止全为 0，SRT 退化成一条字幕。

**排查**：写了两个临时探针（`probe_asr2.py` / `probe_asr3.py`），分别把 `merge_vad` 设为 True/False，并直接调用 `model.vad_model.generate()`。确认：
- 内置管线的输出确实不含时间戳；
- `model.vad_model` 是 `FsmnVADStreaming`，没有可复用的 `.generate()` 结果结构；
- 单独 `AutoModel(model="fsmn-vad")` 调用 `generate(input=..., cache={}, is_final=True)` 会返回 `[{"value": [[start_ms, end_ms], ...]}]`。

**处理**：重构引擎为「独立 VAD 取区间 → 用 soundfile 按区间切 16k 音频 → 批量送入 SenseVoice」。批量上限 60 秒 / 16 段，短于 50ms 的片段丢弃；VAD 失败时退回整段识别。这既拿到可用时间轴，也顺带限制了单次推理的内存占用。

**验证**：`samples/chinese.wav` 返回 `segments:[{start:0.0, end:7.81, ...}]`；把中文与英文样例用 1 秒静音拼接成 16.44 秒音频，返回两段（0–11.62、12.1–16.42），SRT 输出两条字幕。

**教训**：第三方框架的「文档行为」与「实际行为」可能不一致，尤其是会随版本变化的管线封装。遇到这类问题先写最小探针把真实返回结构打印出来，再决定是适配还是绕开，比反复读文档快。

### 4.8 离线模式仍打印「download models from model hub」

**现象**：设置 `A2T_OFFLINE=1` 并已缓存模型后，启动日志仍出现两条 `download models from model hub: ms`，一度让人以为离线没有生效。

**排查**：先看日志里有没有 `Downloading N files from ...`。修复前确实有（SenseVoiceSmall 20 个文件、VAD 8 个），说明 FunASR 拿到了模型 id 字符串就去访问 ModelScope；断网时虽然最终能回退到本地缓存，但要先等网络超时。阅读 `funasr/auto/auto_model.py` 与 `funasr/download/download_model_from_hub.py` 后确认：

- `download models from model hub: ms` 是 `AutoModel.__init__` 在 `model_conf` 缺省时的**无条件日志**；
- 真正决定是否联网的是 `download_from_ms()` 里的 `if not os.path.exists(model_or_path)`；
- 也就是说，只要把模型 id 换成已存在的本地目录，就不会有网络请求，但那条日志仍会打印。

**处理**：新增 `app/config.py:resolve_local_model()`，在传给 `AutoModel` 之前把 id 解析成 ModelScope 缓存里的快照目录（并支持 `fsmn-vad` 别名）。引擎加载改为 `AutoModel(model=resolve_local_model(self.model_id))`。补充 `tests/test_config.py` 覆盖三种缓存布局与回退行为。

**验证**：修复后离线启动日志只剩两条 `download models from model hub: ms`，`Downloading N files` 计数为 0，`Loading pretrained params from <本地路径>` 正常，中文识别耗时 0.77s。再把 `HTTP_PROXY`/`HTTPS_PROXY` 指向无效端口 `127.0.0.1:9` 复测，模型仍能加载并识别，证明没有任何出站请求。慢速测试从 35.08s 降到 24.96s，省掉的正是联网探测的等待。

**教训**：判断「是否联网」不能只看某一行日志，要找能区分真实网络行为的证据（下载进度行、耗时、断网复测）。日志文本有时只是框架的初始化提示，与真实行为无关。

### 4.9 实时录音：不新增接口，复用现有识别链路

**需求变化**：需要在网页里直接录音（Start / Stop、状态与时长、最长 60 秒、停止后识别、权限失败提示、可重复录音）。

**设计取舍**：

- 不新增后端接口。`MediaRecorder` 产出 WebM/Opus，前端把它包成 `File` 后仍调用 `POST /api/transcribe`；服务端已有 `.webm` 白名单与 ffmpeg 转码，链路零改动。
- 不做流式。需求明确「停止录音后才能发起完整识别」，因此采用「先录后传」，避免引入 WebSocket 与流式模型。
- 用状态机管住按钮：`idle → requesting → recording → stopping → idle`。录音中禁用上传与「开始识别」，防止并发。
- 60 秒上限用 `setTimeout`，并在 `dataavailable` 回调里按实际耗时兜底；后台标签页定时器可能被节流，两个机制一起保证不会无限录制。
- 录音格式按能力探测：`audio/webm;codecs=opus` → `audio/webm` → `audio/ogg;codecs=opus` → `audio/mp4`，文件名扩展名随之变化。

**验证难点**：浏览器自动化无法点击系统级麦克风授权。解决办法是只 mock `getUserMedia`，保留真实的 `MediaRecorder` 和后端：用 Web Audio 把 `samples/chinese.wav` 接到 `MediaStreamAudioDestinationNode`，当作麦克风流；权限拒绝路径则让 `getUserMedia` 抛 `NotAllowedError`。这样编码、上传、转码、推理都是真的。

**结果**：9 秒合成语音录音（含 7.8 秒语音）得到正确中文；60 秒时自动停止并提交；纯音录音提示「没有识别到语音内容」；同一页面连续录音 3 次正常；控制台无错误。详细数据见 `docs/test-report.md` 5.7。

**遗留**：物理麦克风与真实授权弹窗仍需人工在真机点一次「允许」，已作为 K12 记录。

### 4.10 音频回放：自定义控件 + 两种时长来源

**需求变化**：上传文件和录音都要能在页面内播放，最好有播放/暂停、进度和时间显示。

**问题**：之前录音场景为了绕开「WebM 无时长元数据，原生 `<audio controls>` 显示 0:00」的问题，直接隐藏了音频预览，导致录音无法回放。

**方案**：

- 统一使用一套自定义播放控件（播放/暂停按钮 + range 进度条 + `当前时间 / 总时长`），隐藏原生 `<audio>`，只把它当播放引擎。
- **录音时长直接复用计时器**：`acceptFile(file, name, elapsed)` 把 `recordElapsedMs` 传进去，不依赖 WebM 元数据。
- **上传时长读元数据**：`loadedmetadata` 时取 `audio.duration`；若仍是 `Infinity`，把 `currentTime` 设成 `1e101` 触发浏览器扫描整个 Blob，`timeupdate` 后取真实值并复位。
- `play` / `pause` / `ended` / `timeupdate` 事件驱动按钮文案、进度条和时间文本；播放结束自动复位到 0。
- 浏览器无法解码的格式（WMA/AMR）会触发 `error`，此时禁用播放并提示「浏览器无法预览该音频格式，但仍可正常识别」，不阻塞识别。
- 点击 Start Recording 前先 `audio.pause()`，避免扬声器回放被麦克风录进去。

**验证**：上传 7.8s 中文时总时长 `00:07`，播放 1.5s 后进度 `00:01 / 00:07`，拖到 50% 时 `currentTime=3.91s`；5 秒录音总时长 `00:05`，播放、拖动、播完复位均正常；无法解码的文件显示提示且按钮禁用；控制台无错误。详细数据见 `docs/test-report.md` 5.7。

### 4.11 上传上限收紧到 10MB，并让前端自动同步

**需求变化**：后端需要限制上传文件大小，建议上限 10MB。

**现状**：后端原本已有 `A2T_MAX_UPLOAD_MB`（默认 500）与分块落盘时的 413 校验；但前端把 500MB 写死在 `MAX_BYTES` 和提示文案里，两边容易不一致。

**改动**：

- 后端默认值改为 `10`（`app/config.py`）；新增 HTTP 中间件按 `Content-Length` 在 multipart 解析前直接拒绝明显超限的请求，`_save_upload()` 保留按 1MB 分块累计的兜底校验。
- `/health` 增加 `max_upload_mb` 字段，作为前后端唯一事实来源。
- 前端默认按 10MB 拦截，并在健康检查时用后端返回值更新 `maxUploadMb` 与拖拽区提示文案；选择文件时提示「上限 10MB」。
- 新增测试：配置 1MB 上限时上传 1MB+1 返回 413 且 `detail` 含「1MB」；伪造超大 `Content-Length` 时中间件直接 413；前端静态检查默认值与 `/health` 同步逻辑。

**验证**：`GET /health` 返回 `"max_upload_mb": 10`；11MB `big.wav` 通过 curl 上传返回 `413 {"detail":"文件超过 10MB 上限"}`；页面拖入 11MB 文件立即提示「文件过大：11.0 MB，上限 10MB」；60 秒录音（约 0.5–1MB）不受影响。详细数据见 `docs/test-report.md` 5.8。

### 4.12 统一启动方式：requirements.txt 自带 PyTorch CPU 版

**需求变化**：Windows 11 下应只需两条命令启动：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**问题**：此前 `requirements.txt` 没有列出 `torch` / `torchaudio`（FunASR 的元数据也没有声明它们），README 要求先手工执行一条带 `--index-url` 的 PyTorch 安装命令，再安装其余依赖；启动命令还依赖激活虚拟环境后 PATH 中的 `uvicorn.exe`。这不符合「统一启动方式」。

**改动**：

- `requirements.txt` 顶部增加 `--extra-index-url https://download.pytorch.org/whl/cpu`，并显式加入 `torch>=2.2,<3`、`torchaudio>=2.2,<3`；Windows 下一条命令即可装好 CPU 版。
- `requirements.lock.txt` 顶部增加同一索引，锁文件中的 `torch==2.15.0+cpu` / `torchaudio==2.11.1+cpu` 可直接解析。
- README 的安装/启动/测试命令统一为 `python -m pip`、`python -m uvicorn`、`python -m pytest`；虚拟环境改为可选建议，不再要求激活后使用裸 `uvicorn`。
- `app/main.py` 的模块文档改为 `python -m uvicorn ...`。

**验证**：在全新临时 venv 中实际执行 `python -m pip install -r requirements.txt`（非 dry-run），`pip exit 0`，安装出 torch `2.15.0+cpu`、torchaudio `2.11.1+cpu` 等依赖；再用该环境执行 `python -m uvicorn app.main:app --port 8020`，`/health` 返回 `model_loaded=true`，首页 200，上传样例识别正常。详细数据见 `docs/test-report.md` 5.9。

## 5. AI 产出的校验方式

| 产出 | 校验手段 |
|---|---|
| 音频转码 | 单测断言输出采样率 16k、单声道；损坏文件必须抛错 |
| 文本后处理 | 用真实的 SenseVoice 标签样例做表驱动测试 |
| 接口行为 | `TestClient` + 假引擎，覆盖 200/400/413/415/500 |
| 上传上限 | 单测设小上限验证 413；浏览器与 curl 分别验证 11MB 文件被拦截 |
| 模型可用性 | `pytest -m slow` 跑真实模型，与人工真值计算相似度 |
| 前端 | 浏览器手动验证上传、进度、复制、下载、错误提示 |
| 录音 | 静态检查控件与状态机；浏览器里 mock 权限并用合成音源跑真实 MediaRecorder |
| 回放 | 静态检查控件与事件绑定；浏览器里验证播放/暂停、拖动进度、结束复位与无法解码提示 |

## 6. 经验总结

1. **先验证最贵的假设**：模型能否在目标环境跑起来，比代码写得多漂亮更重要，所以 P0 先装依赖、先跑一次推理。
2. **把不确定性关进接口**：`ASREngine` 协议让「换模型 / 换框架」变成新增一个文件，而不是重写业务层。
3. **AI 擅长铺量，不擅长边界**：错误码、并发、编码、超时这些边界需要人工逐条过一遍，并写成测试。
4. **文档与代码同步产出**：设计文档在写代码时完成，测试报告在测试跑完后立刻写，避免事后回忆失真。
