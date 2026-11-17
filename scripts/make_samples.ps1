# 生成测试样例音频（Windows 内置语音合成）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/make_samples.ps1
#
# 说明：仓库里已包含生成结果，本脚本仅用于重新生成。
# 替换文本时请同步修改 samples/reference.txt，否则慢速测试会失败。

param(
    [string]$OutDir = (Join-Path $PSScriptRoot "..\samples")
)

Add-Type -AssemblyName System.Speech

$OutDir = [System.IO.Path]::GetFullPath($OutDir)
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$samples = @(
    @{
        File  = "chinese.wav"
        Voice = "Microsoft Huihui Desktop"
        Text  = "今天天气不错，我们下午三点在会议室开会。请大家准时参加。"
    },
    @{
        File  = "english.wav"
        Voice = "Microsoft Zira Desktop"
        Text  = "The quick brown fox jumps over the lazy dog. This audio is used to test a local speech recognition system."
    }
)

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer

foreach ($sample in $samples) {
    $target = Join-Path $OutDir $sample.File
    $synth.SelectVoice($sample.Voice)
    $synth.SetOutputToWaveFile($target)
    $synth.Speak($sample.Text)
    $synth.SetOutputToNull()
    Write-Host "生成：$target"
}

$synth.Dispose()
Write-Host "完成。请确认 samples/reference.txt 与上述文本一致。"
