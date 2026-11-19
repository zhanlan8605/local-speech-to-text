"""前端录音功能的静态检查。

浏览器行为无法在 pytest 中完整复现，这里锁定关键契约：
录音控件、麦克风权限请求、60 秒上限、停止后才提交识别、失败提示。
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")


def test_index_exposes_recording_controls():
    assert 'id="record-start"' in INDEX
    assert 'id="record-stop"' in INDEX
    assert 'id="record-state"' in INDEX
    assert 'id="record-timer"' in INDEX
    assert "开始录音 Start Recording" in INDEX
    assert "停止录音 Stop Recording" in INDEX
    assert "单次最长 60 秒" in INDEX


def test_js_requests_microphone_and_enforces_60_seconds():
    assert "navigator.mediaDevices.getUserMedia" in APP_JS
    assert "new MediaRecorder" in APP_JS
    assert "MAX_RECORD_MS = 60 * 1000" in APP_JS
    assert "recordLimitId = setTimeout" in APP_JS
    assert "已达到 60 秒上限" in APP_JS


def test_js_only_submits_after_recorder_stops():
    stop_listener = APP_JS.index('recorder.addEventListener("stop"')
    handle_def = APP_JS.index("async function handleRecorderStop()")
    transcribe_call = APP_JS.index("transcribeFile(file, `recording-${stamp}`)")
    assert stop_listener < handle_def < transcribe_call


def test_js_reports_permission_and_device_errors():
    assert "NotAllowedError" in APP_JS
    assert "麦克风权限被拒绝" in APP_JS
    assert "没有找到可用的麦克风设备" in APP_JS
    assert "被其他程序占用" in APP_JS
    assert "录音时间太短" in APP_JS


def test_js_allows_recording_again_after_result():
    assert "识别完成，可以再次录音" in APP_JS
    assert "setRecordState(\"done\"" in APP_JS


def test_js_recording_file_is_accepted_while_stopping():
    # 录音收尾时 recordPhase 已切到 stopping，不能因此把自己的录音判为「不可用」
    assert "!options.fromRecording && isLocked()" in APP_JS
    assert "{ fromRecording: true }" in APP_JS


def test_index_exposes_player_controls():
    assert 'id="play-toggle"' in INDEX
    assert 'id="play-seek"' in INDEX
    assert 'id="play-time"' in INDEX
    assert 'id="audio-preview"' in INDEX
    assert "播放" in INDEX


def test_js_plays_uploaded_and_recorded_audio():
    # 播放按钮复用同一个 <audio>，上传与录音都必须挂上可播放的 object URL
    assert "async function togglePlayback()" in APP_JS
    assert "await els.audio.play()" in APP_JS
    assert 'els.audio.addEventListener("ended"' in APP_JS
    assert "URL.createObjectURL(file)" in APP_JS
    # 录音在 acceptFile 中传入计时得到的时长，避免 WebM 元数据缺失
    assert "acceptFile(file, `录音" in APP_JS
    assert "applyPlayerDuration(knownDurationMs / 1000)" in APP_JS


def test_js_fixes_missing_webm_duration():
    # MediaRecorder 产出的 WebM duration 为 Infinity，需要探测出真实时长
    assert "audio.currentTime = 1e101" in APP_JS
    assert "probePlayerDuration" in APP_JS
    assert "浏览器无法预览该音频格式" in APP_JS


def test_upload_limit_is_10mb_and_synced_from_health():
    assert "DEFAULT_MAX_UPLOAD_MB = 10" in APP_JS
    assert "data.max_upload_mb" in APP_JS
    assert "上限 ${maxUploadMb}MB" in APP_JS
    assert "单个文件最大 10MB" in INDEX


# ---------------------------------------------------------------- 实时识别
WORKLET = STATIC / "pcm-worklet.js"
WORKLET_JS = WORKLET.read_text(encoding="utf-8")


def test_index_exposes_live_transcript_area():
    assert 'id="live"' in INDEX
    assert 'id="live-text"' in INDEX
    assert 'id="live-committed"' in INDEX
    assert 'id="live-partial"' in INDEX
    assert "实时识别" in INDEX
    assert "边说边出字" in INDEX


def test_js_connects_websocket_and_streams_pcm():
    assert 'const STREAM_PATH = "/ws/stream"' in APP_JS
    assert "new WebSocket(streamUrl())" in APP_JS
    assert "ws.binaryType = \"arraybuffer\"" in APP_JS
    assert 'JSON.stringify({ type: "start", language: els.language.value })' in APP_JS
    assert 'JSON.stringify({ type: "stop" })' in APP_JS


def test_js_renders_partial_results_from_websocket():
    assert 'data.type === "partial"' in APP_JS
    assert 'data.type === "final"' in APP_JS
    assert "els.liveCommitted.textContent" in APP_JS
    assert "els.livePartial.textContent" in APP_JS
    assert "showLiveText(" in APP_JS


def test_live_partial_does_not_overwrite_result_box():
    # 实时文本只出现在实时区域，底部结果框留给停止后的最终文本
    start = APP_JS.index("function showLiveText")
    end = APP_JS.index("function closeStreamSocket")
    body = APP_JS[start:end]
    assert "els.result.value" not in body
    assert "setResultActions" not in body


def test_js_captures_microphone_with_audio_worklet():
    assert "ctx.audioWorklet.addModule(\"/static/pcm-worklet.js\")" in APP_JS
    assert 'new window.AudioWorkletNode(ctx, "pcm-capture"' in APP_JS
    assert "STREAM_SAMPLE_RATE = 16000" in APP_JS
    assert "createScriptProcessorCapture" in APP_JS  # 无 AudioWorklet 时的降级路径


def test_worklet_emits_16k_int16_frames():
    assert 'registerProcessor("pcm-capture"' in WORKLET_JS
    assert "Int16Array" in WORKLET_JS
    assert "targetRate" in WORKLET_JS
    assert "port.postMessage" in WORKLET_JS


def test_streaming_falls_back_to_upload_when_unavailable():
    # 实时通道失败时仍能录音并上传识别，不能把录音功能一起弄坏
    assert "startStreamCapture()" in APP_JS
    assert "showStreamUnavailable(err)" in APP_JS
    assert "pendingFinal" in APP_JS
    assert "transcribeFile(file, `recording-${stamp}`)" in APP_JS
    assert "实时识别不可用，停止后会自动识别" in APP_JS
    assert "实时识别不可用，已改用上传识别" in APP_JS


def test_js_reads_streaming_flags_from_health():
    assert "streamingEnabled = data.streaming_enabled !== false" in APP_JS
    assert "服务端已关闭实时识别" in APP_JS
