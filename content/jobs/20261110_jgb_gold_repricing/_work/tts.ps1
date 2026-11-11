
param([string]$TextFile, [string]$OutFile, [string]$Voice)
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice($Voice)
$synth.Rate = 0
$synth.Volume = 100
$synth.SetOutputToWaveFile($OutFile)
$synth.Speak($text)
$synth.Dispose()
