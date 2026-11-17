/* 本地语音转文字 —— 前端逻辑（无构建步骤，原生 JS） */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("status"),
    statusText: $("status-text"),
    recorder: $("recorder"),
    recordState: $("record-state"),
    recordTimer: $("record-timer"),
    recordStart: $("record-start"),
    recordStop: $("record-stop"),
    dropzone: $("dropzone"),
    uploadLimit: $("upload-limit"),
    fileInput: $("file-input"),
    fileInfo: $("file-info"),
    fileName: $("file-name"),
    fileSize: $("file-size"),
    playToggle: $("play-toggle"),
    playIcon: $("play-icon"),
    playLabel: $("play-label"),
    playSeek: $("play-seek"),
    playTime: $("play-time"),
    playNote: $("play-note"),
    audio: $("audio-preview"),
    language: $("language"),
    format: $("format"),
    submit: $("submit"),
    progress: $("progress"),
    progressText: $("progress-text"),
    error: $("error"),
    result: $("result"),
    meta: $("meta"),
    copy: $("copy"),
    download: $("download"),
  };

  const EXTENSIONS = [
    ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma",
    ".amr", ".mp4", ".mkv", ".mov", ".webm", ".avi",
  ];
  // 后端在 /health 中返回真实上限，未取到前先按默认值 10MB 拦截
  const DEFAULT_MAX_UPLOAD_MB = 10;
  let maxUploadMb = DEFAULT_MAX_UPLOAD_MB;

  function maxUploadBytes() {
    return maxUploadMb * 1024 * 1024;
  }

  // 单次录音上限与可接受的最短录音
  const MAX_RECORD_MS = 60 * 1000;
  const MIN_RECORD_MS = 300;
  // Chrome/Edge 支持 webm/opus；其余候选用于其他浏览器
  const RECORD_MIME_TYPES = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];

  let selectedFile = null;
  let busy = false;
  let timerId = null;
  let objectUrl = null;
  let lastResult = { text: "", format: "json" };
  let lastSourceName = "transcript";

  // 播放器状态
  let playerDuration = 0;       // 秒；0 表示时长未知
  let durationLocked = false;   // 录音场景在创建时已知时长，不再依赖元数据
  let probingDuration = false;
  let playerSeeking = false;

  // 录音状态机：idle -> requesting -> recording -> stopping -> idle
  let recordPhase = "idle";
  let mediaStream = null;
  let recorder = null;
  let chunks = [];
  let recordStartedAt = 0;
  let recordElapsedMs = 0;
  let recordTimerId = null;
  let recordLimitId = null;
  let recordFailed = false;
  let autoStopped = false;

  // ---------------------------------------------------------------- 工具
  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
  }

  function formatDuration(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    const mm = String(Math.floor(total / 60)).padStart(2, "0");
    const ss = String(total % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  }

  function timestamp() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return (
      `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-` +
      `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
    );
  }

  function extensionForMime(type) {
    const value = (type || "").toLowerCase();
    if (value.includes("ogg")) return "ogg";
    if (value.includes("mp4") || value.includes("m4a")) return "m4a";
    return "webm";
  }

  function isLocked() {
    return busy || recordPhase !== "idle";
  }

  function setStatus(kind, text) {
    els.status.className = `status status--${kind}`;
    els.statusText.textContent = text;
  }

  function showError(message) {
    els.error.textContent = message;
    els.error.classList.remove("hidden");
  }

  function clearError() {
    els.error.textContent = "";
    els.error.classList.add("hidden");
  }

  // ---------------------------------------------------------------- 控件状态
  function setRecordState(state, text) {
    els.recorder.dataset.state = state;
    if (text != null) els.recordState.textContent = text;
  }

  function renderRecordTimer() {
    const elapsed = recordPhase === "recording"
      ? performance.now() - recordStartedAt
      : recordElapsedMs;
    els.recordTimer.textContent = `${formatDuration(elapsed)} / 01:00`;
  }

  function syncControls() {
    const locked = isLocked();
    els.submit.disabled = busy || recordPhase !== "idle" || !selectedFile;
    els.submit.textContent = busy ? "识别中…" : "开始识别";
    els.fileInput.disabled = locked;
    els.language.disabled = busy || recordPhase === "recording" || recordPhase === "stopping";
    els.format.disabled = busy || recordPhase === "recording" || recordPhase === "stopping";
    els.recordStart.disabled = locked;
    els.recordStop.disabled = recordPhase !== "recording";
    els.dropzone.setAttribute("aria-disabled", locked ? "true" : "false");
  }

  function setBusy(value) {
    busy = value;
    syncControls();
  }

  function setResultActions(enabled) {
    els.copy.disabled = !enabled;
    els.download.disabled = !enabled;
  }

  // ---------------------------------------------------------------- 音频播放
  function setPlayerPlaying(playing) {
    els.playIcon.textContent = playing ? "⏸" : "▶";
    els.playLabel.textContent = playing ? "暂停" : "播放";
    els.playToggle.setAttribute("aria-label", playing ? "暂停播放" : "播放音频");
    els.playToggle.classList.toggle("player__toggle--playing", playing);
  }

  function playerTotalSeconds() {
    if (playerDuration > 0) return playerDuration;
    const value = els.audio.duration;
    return Number.isFinite(value) && value > 0 ? value : 0;
  }

  function renderPlayerTime() {
    const current = Number.isFinite(els.audio.currentTime) ? els.audio.currentTime : 0;
    const total = playerTotalSeconds();
    els.playTime.textContent =
      `${formatDuration(current * 1000)} / ${total > 0 ? formatDuration(total * 1000) : "--:--"}`;
  }

  function renderPlayerProgress() {
    if (playerSeeking) return;
    const current = Number.isFinite(els.audio.currentTime) ? els.audio.currentTime : 0;
    const total = playerTotalSeconds();
    els.playSeek.value = total > 0
      ? String(Math.round(Math.min(1, current / total) * 1000))
      : "0";
    renderPlayerTime();
  }

  function applyPlayerDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return false;
    playerDuration = seconds;
    els.playSeek.disabled = false;
    renderPlayerTime();
    return true;
  }

  function probePlayerDuration() {
    if (durationLocked || playerDuration > 0 || probingDuration) return;

    const audio = els.audio;
    const duration = audio.duration;
    if (Number.isFinite(duration) && duration > 0) {
      applyPlayerDuration(duration);
      return;
    }
    if (duration !== Infinity) return;

    // MediaRecorder 生成的 WebM 没有时长元数据，duration 为 Infinity。
    // 把 currentTime 跳到极大值可让浏览器扫描整个 Blob 并算出真实时长。
    probingDuration = true;
    const finish = (seconds) => {
      probingDuration = false;
      audio.removeEventListener("timeupdate", onTimeUpdate);
      try {
        audio.currentTime = 0;
      } catch {
        /* 部分浏览器拒绝在没有源时设置进度 */
      }
      applyPlayerDuration(seconds);
      renderPlayerProgress();
    };
    const onTimeUpdate = () => {
      const real = Number.isFinite(audio.duration) && audio.duration > 0
        ? audio.duration
        : (audio.seekable.length ? audio.seekable.end(0) : 0);
      if (real > 0) finish(real);
    };

    audio.addEventListener("timeupdate", onTimeUpdate);
    try {
      audio.currentTime = 1e101;
    } catch {
      probingDuration = false;
      audio.removeEventListener("timeupdate", onTimeUpdate);
    }
  }

  function resetPlayer() {
    els.audio.pause();
    try {
      els.audio.currentTime = 0;
    } catch {
      /* 还没有音频源时忽略 */
    }
    playerDuration = 0;
    durationLocked = false;
    probingDuration = false;
    playerSeeking = false;
    els.playSeek.value = "0";
    els.playSeek.disabled = true;
    els.playToggle.disabled = true;
    els.playNote.textContent = "";
    els.playNote.classList.add("hidden");
    setPlayerPlaying(false);
    renderPlayerTime();
  }

  async function togglePlayback() {
    if (!selectedFile) return;
    if (!els.audio.paused) {
      els.audio.pause();
      return;
    }
    try {
      await els.audio.play();
    } catch (err) {
      setPlayerPlaying(false);
      els.playNote.textContent =
        `浏览器无法播放该音频：${(err && err.message) || "不支持的格式"}；仍可正常识别。`;
      els.playNote.classList.remove("hidden");
    }
  }

  els.playToggle.addEventListener("click", () => { void togglePlayback(); });

  els.playSeek.addEventListener("input", () => {
    playerSeeking = true;
    const total = playerTotalSeconds();
    if (total > 0) {
      els.audio.currentTime = (Number(els.playSeek.value) / 1000) * total;
    }
    renderPlayerTime();
  });
  els.playSeek.addEventListener("change", () => {
    playerSeeking = false;
    renderPlayerProgress();
  });

  els.audio.addEventListener("play", () => setPlayerPlaying(true));
  els.audio.addEventListener("pause", () => setPlayerPlaying(false));
  els.audio.addEventListener("timeupdate", renderPlayerProgress);
  els.audio.addEventListener("seeked", renderPlayerProgress);
  els.audio.addEventListener("loadedmetadata", probePlayerDuration);
  els.audio.addEventListener("durationchange", probePlayerDuration);
  els.audio.addEventListener("ended", () => {
    setPlayerPlaying(false);
    try {
      els.audio.currentTime = 0;
    } catch {
      /* 忽略 */
    }
    els.playSeek.value = "0";
    renderPlayerTime();
  });
  els.audio.addEventListener("error", () => {
    // 识别由服务端 ffmpeg 完成，浏览器不支持该编码不影响转写。
    if (!selectedFile) return;
    els.playToggle.disabled = true;
    els.playSeek.disabled = true;
    els.playTime.textContent = "--:-- / --:--";
    els.playNote.textContent = "浏览器无法预览该音频格式，但仍可正常识别。";
    els.playNote.classList.remove("hidden");
  });

  // ---------------------------------------------------------------- 健康检查
  function applyUploadLimit(mb) {
    if (!Number.isFinite(mb) || mb <= 0) return;
    maxUploadMb = mb;
    els.uploadLimit.textContent =
      `wav / mp3 / m4a / flac / mp4 等，单个文件最大 ${mb}MB`;
  }

  async function pollHealth() {
    try {
      const res = await fetch("/health", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      applyUploadLimit(data.max_upload_mb);
      if (data.model_loaded) {
        setStatus("ready", "模型已就绪");
        return true;
      }
      setStatus("warming", "模型加载中…");
      return false;
    } catch {
      setStatus("error", "服务未就绪");
      return false;
    }
  }

  async function watchHealth() {
    const ready = await pollHealth();
    if (!ready) setTimeout(watchHealth, 3000);
  }

  function watchMicrophonePermission() {
    if (!navigator.permissions || !navigator.permissions.query) return;
    navigator.permissions.query({ name: "microphone" }).then((status) => {
      const apply = () => {
        if (isLocked()) return;
        if (status.state === "granted") {
          setRecordState("idle", "麦克风已就绪，可以开始录音");
        } else if (status.state === "denied") {
          setRecordState("error", "麦克风权限已被拒绝");
        } else {
          setRecordState("idle", "麦克风未启用");
        }
      };
      apply();
      status.addEventListener("change", apply);
    }).catch(() => {
      /* 部分浏览器不支持查询麦克风权限，忽略即可 */
    });
  }

  // ---------------------------------------------------------------- 文件选择
  function acceptFile(file, displayName, knownDurationMs = 0) {
    if (!file) return false;
    if (isLocked()) {
      showError("请等待当前任务结束后再选择文件。");
      return false;
    }

    const lower = file.name.toLowerCase();
    if (!EXTENSIONS.some((ext) => lower.endsWith(ext))) {
      showError(`不支持的文件类型：${file.name}`);
      return false;
    }
    if (file.size > maxUploadBytes()) {
      showError(`文件过大：${formatBytes(file.size)}，上限 ${maxUploadMb}MB`);
      return false;
    }

    clearError();
    resetPlayer();
    selectedFile = file;
    els.fileName.textContent = displayName || file.name;
    els.fileSize.textContent = formatBytes(file.size);
    els.fileInfo.classList.remove("hidden");

    // 先挂上新地址再释放旧地址，避免播放器读到已失效的 URL
    const previousUrl = objectUrl;
    objectUrl = URL.createObjectURL(file);
    els.audio.src = objectUrl;
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    els.playToggle.disabled = false;

    // 录音时长来自计时器，比 WebM 元数据更可靠；上传文件则等 loadedmetadata。
    durationLocked = Boolean(knownDurationMs && knownDurationMs > 0);
    if (durationLocked) {
      applyPlayerDuration(knownDurationMs / 1000);
    }
    renderPlayerTime();

    syncControls();
    return true;
  }

  els.dropzone.addEventListener("click", () => {
    if (!isLocked()) els.fileInput.click();
  });
  els.dropzone.addEventListener("keydown", (event) => {
    if (isLocked()) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      els.fileInput.click();
    }
  });
  els.fileInput.addEventListener("change", () => {
    acceptFile(els.fileInput.files && els.fileInput.files[0]);
  });

  ["dragenter", "dragover"].forEach((name) => {
    els.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      if (isLocked()) return;
      els.dropzone.classList.add("dropzone--over");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    els.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      els.dropzone.classList.remove("dropzone--over");
    });
  });
  els.dropzone.addEventListener("drop", (event) => {
    if (isLocked()) {
      showError("请等待当前任务结束后再上传文件。");
      return;
    }
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    acceptFile(file);
  });

  // ---------------------------------------------------------------- 录音
  function describeMediaError(err) {
    const name = (err && err.name) || "";
    switch (name) {
      case "NotAllowedError":
      case "SecurityError":
        return "麦克风权限被拒绝。请点击地址栏的麦克风图标，把本页面设为允许后重试。";
      case "NotFoundError":
      case "DevicesNotFoundError":
        return "没有找到可用的麦克风设备，请检查麦克风是否已连接。";
      case "NotReadableError":
      case "TrackStartError":
        return "麦克风无法访问，可能被其他程序占用。请关闭占用麦克风的程序后重试。";
      case "OverconstrainedError":
      case "ConstraintNotSatisfiedError":
        return "当前麦克风不支持所需的录音参数，请更换设备后重试。";
      case "AbortError":
        return "麦克风启动被中断，请重试。";
      default:
        return `无法访问麦克风：${(err && err.message) || name || "未知错误"}`;
    }
  }

  function clearRecorderTimers() {
    if (recordTimerId) {
      clearInterval(recordTimerId);
      recordTimerId = null;
    }
    if (recordLimitId) {
      clearTimeout(recordLimitId);
      recordLimitId = null;
    }
  }

  function stopStream() {
    if (mediaStream) {
      mediaStream.getTracks().forEach((track) => track.stop());
      mediaStream = null;
    }
  }

  function pickRecorderMime() {
    if (typeof window.MediaRecorder.isTypeSupported !== "function") return "";
    return RECORD_MIME_TYPES.find((type) => window.MediaRecorder.isTypeSupported(type)) || "";
  }

  async function startRecording() {
    if (isLocked()) return;

    // 避免扬声器里的回放被麦克风再次录入
    els.audio.pause();

    clearError();
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      setRecordState("error", "浏览器不支持录音");
      showError("当前浏览器不支持录音功能，请使用最新版 Chrome 或 Edge。");
      return;
    }
    if (typeof window.MediaRecorder === "undefined") {
      setRecordState("error", "浏览器不支持录音");
      showError("当前浏览器不支持 MediaRecorder，请使用最新版 Chrome 或 Edge。");
      return;
    }

    recordPhase = "requesting";
    recordElapsedMs = 0;
    autoStopped = false;
    renderRecordTimer();
    setRecordState("requesting", "正在请求麦克风权限…");
    syncControls();

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      recordPhase = "idle";
      setRecordState("error", "麦克风不可用");
      showError(describeMediaError(err));
      syncControls();
      return;
    }

    const mimeType = pickRecorderMime();
    try {
      recorder = mimeType
        ? new MediaRecorder(mediaStream, { mimeType })
        : new MediaRecorder(mediaStream);
    } catch (err) {
      stopStream();
      recordPhase = "idle";
      setRecordState("error", "录音初始化失败");
      showError(`无法启动录音：${(err && err.message) || err}`);
      syncControls();
      return;
    }

    chunks = [];
    recordFailed = false;
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size > 0) chunks.push(event.data);
      // 后台标签页的定时器可能被节流，这里再用数据回调兜底一次
      if (recordPhase === "recording" && performance.now() - recordStartedAt >= MAX_RECORD_MS) {
        autoStopped = true;
        stopRecording();
      }
    });
    recorder.addEventListener("error", (event) => {
      recordFailed = true;
      const reason = (event && event.error && event.error.message) || "录音过程中出错";
      showError(`录音失败：${reason}`);
      setRecordState("error", "录音失败");
    });
    recorder.addEventListener("stop", () => {
      handleRecorderStop().catch((err) => {
        recordFailed = false;
        clearRecorderTimers();
        stopStream();
        recorder = null;
        recordPhase = "idle";
        recordElapsedMs = 0;
        renderRecordTimer();
        setRecordState("error", "录音处理失败");
        showError(`录音处理失败：${(err && err.message) || err}`);
        syncControls();
      });
    });

    try {
      recorder.start(250);
    } catch (err) {
      stopStream();
      recorder = null;
      recordPhase = "idle";
      setRecordState("error", "录音启动失败");
      showError(`无法开始录音：${(err && err.message) || err}`);
      syncControls();
      return;
    }

    recordStartedAt = performance.now();
    recordPhase = "recording";
    setRecordState("recording", "录音中，请说话…");
    renderRecordTimer();
    recordTimerId = setInterval(renderRecordTimer, 100);
    recordLimitId = setTimeout(() => {
      if (recordPhase === "recording") {
        autoStopped = true;
        stopRecording();
      }
    }, MAX_RECORD_MS);
    syncControls();
  }

  function stopRecording() {
    if (recordPhase !== "recording" || !recorder) return;

    recordElapsedMs = Math.min(MAX_RECORD_MS, performance.now() - recordStartedAt);
    recordPhase = "stopping";
    clearRecorderTimers();
    renderRecordTimer();
    setRecordState(
      "stopping",
      autoStopped ? "已达到 60 秒上限，正在结束录音…" : "正在结束录音…"
    );
    syncControls();

    try {
      recorder.stop();
    } catch (err) {
      recordFailed = true;
      showError(`停止录音失败：${(err && err.message) || err}`);
      setRecordState("error", "录音停止失败");
      // stop 事件未必会触发，手动收尾
      void handleRecorderStop();
    }
  }

  async function handleRecorderStop() {
    clearRecorderTimers();
    stopStream();

    const wasAutoStopped = autoStopped;
    autoStopped = false;
    const elapsed = recordElapsedMs ||
      Math.max(0, performance.now() - recordStartedAt);
    recordElapsedMs = elapsed;
    recorder = null;
    recordPhase = "idle";
    els.recorder.classList.remove("recorder--active");
    renderRecordTimer();
    syncControls();

    if (recordFailed) {
      recordFailed = false;
      chunks = [];
      recordElapsedMs = 0;
      renderRecordTimer();
      setRecordState("error", "录音失败，请重试");
      return;
    }

    const blobType = (chunks[0] && chunks[0].type) || "audio/webm";
    const blob = new Blob(chunks, { type: blobType });
    chunks = [];

    if (elapsed < MIN_RECORD_MS || blob.size < 512) {
      recordElapsedMs = 0;
      renderRecordTimer();
      setRecordState("error", "录音过短");
      showError("录音时间太短或没有录到声音，请至少说一句话后再停止。");
      return;
    }

    const stamp = timestamp();
    const ext = extensionForMime(blobType);
    const file = new File([blob], `recording-${stamp}.${ext}`, {
      type: blobType || "audio/webm",
    });

    if (!acceptFile(file, `录音 ${formatDuration(elapsed)}（${formatBytes(blob.size)}）`, elapsed)) {
      setRecordState("error", "录音数据不可用");
      return;
    }

    setRecordState(
      "recognizing",
      wasAutoStopped ? "已达到 60 秒上限，正在识别…" : "录音已停止，正在识别…"
    );
    const result = await transcribeFile(file, `recording-${stamp}`);
    if (result === "ok") {
      setRecordState("done", "识别完成，可以再次录音");
    } else if (result === "empty") {
      setRecordState("error", "未识别到语音内容，可以重试");
    } else {
      setRecordState("error", "识别失败，请重试");
    }
  }

  els.recordStart.addEventListener("click", () => { void startRecording(); });
  els.recordStop.addEventListener("click", () => stopRecording());

  // ---------------------------------------------------------------- 提交识别
  async function transcribeFile(file, downloadBase) {
    if (!file || busy) return "skipped";

    clearError();
    setResultActions(false);
    els.result.value = "";
    els.meta.classList.add("hidden");
    lastSourceName = downloadBase || file.name.replace(/\.[^.]+$/, "");
    setBusy(true);
    els.progress.classList.remove("hidden");

    const started = performance.now();
    const renderTimer = () => {
      els.progressText.textContent =
        `识别中… ${((performance.now() - started) / 1000).toFixed(1)}s`;
    };
    renderTimer();
    timerId = setInterval(renderTimer, 100);

    let status = "error";
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("language", els.language.value);

      const format = els.format.value;
      const res = await fetch(`/api/transcribe?response_format=${encodeURIComponent(format)}`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        let detail = `请求失败（HTTP ${res.status}）`;
        try {
          const payload = await res.json();
          if (payload && payload.detail) detail = payload.detail;
        } catch {
          /* 非 JSON 错误体，保留默认文案 */
        }
        throw new Error(detail);
      }

      if (format === "json") {
        const data = await res.json();
        const text = data.text || "";
        els.result.value = text;
        els.meta.textContent =
          `语言：${data.language} · 音频时长：${data.duration_s.toFixed(1)}s · ` +
          `推理耗时：${data.elapsed_s.toFixed(1)}s · 模型：${data.model}`;
        els.meta.classList.remove("hidden");
        lastResult = { text, format: "json" };
        status = text.trim().length > 0 ? "ok" : "empty";
        if (status === "empty") showError("没有识别到语音内容，请检查录音或音频后重试。");
      } else {
        const text = await res.text();
        els.result.value = text;
        lastResult = { text, format };
        status = text.trim().length > 0 ? "ok" : "empty";
        if (status === "empty") showError("没有识别到语音内容，请检查录音或音频后重试。");
      }

      if (status === "ok") setResultActions(true);
    } catch (err) {
      showError(err && err.message ? err.message : String(err));
      els.result.value = "";
    } finally {
      clearInterval(timerId);
      timerId = null;
      els.progress.classList.add("hidden");
      setBusy(false);
    }

    return status;
  }

  els.submit.addEventListener("click", () => {
    if (!selectedFile || busy) return;
    void transcribeFile(selectedFile);
  });

  // ---------------------------------------------------------------- 结果操作
  els.copy.addEventListener("click", async () => {
    if (!els.result.value) return;
    try {
      await navigator.clipboard.writeText(els.result.value);
      const original = els.copy.textContent;
      els.copy.textContent = "已复制";
      setTimeout(() => { els.copy.textContent = original; }, 1200);
    } catch {
      els.result.select();
      document.execCommand("copy");
    }
  });

  els.download.addEventListener("click", () => {
    if (!lastResult.text) return;
    const ext = lastResult.format === "json" ? "txt" : lastResult.format;
    const base = lastSourceName || "transcript";
    const blob = new Blob([lastResult.text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${base}.${ext}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  window.addEventListener("beforeunload", (event) => {
    if (busy || recordPhase !== "idle") {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  window.addEventListener("pagehide", () => {
    clearRecorderTimers();
    stopStream();
  });

  watchHealth();
  watchMicrophonePermission();
})();
