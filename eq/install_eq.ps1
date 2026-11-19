#Requires -Version 5.1
<#
    FPS 脚步增强 EQ —— Equalizer APO 一键安装 / 卸载
    用法：
      powershell -ExecutionPolicy Bypass -File install_eq.ps1
      powershell -ExecutionPolicy Bypass -File install_eq.ps1 -Uninstall
      powershell -ExecutionPolicy Bypass -File install_eq.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$DryRun,
    [string]$ApoRoot = "",
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"
$ProfileName = "FPS_Footstep_7.1.txt"
$IncludeLine = "Include: $ProfileName"

function Find-ApoRoot {
    $cands = New-Object System.Collections.Generic.List[string]
    $pf   = $env:ProgramFiles
    $pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($pf)   { $cands.Add((Join-Path $pf   "EqualizerAPO")) }
    if ($pf86) { $cands.Add((Join-Path $pf86 "EqualizerAPO")) }
    $cands.Add("C:\Program Files\EqualizerAPO")
    foreach ($c in $cands) {
        if ((Test-Path $c) -and (Test-Path (Join-Path $c "config"))) { return $c }
    }
    return $null
}

$apoRoot = if ($ApoRoot) { $ApoRoot } else { Find-ApoRoot }
if (-not $apoRoot) {
    Write-Host "没找到 Equalizer APO。" -ForegroundColor Yellow
    Write-Host "先装 Equalizer APO（安装时勾选你那根 7.1 虚拟声卡），装完再跑本脚本。" -ForegroundColor Yellow
    Write-Host "下载：https://sourceforge.net/projects/equalizerapo/" -ForegroundColor Gray
    if ($DryRun) { exit 0 }
    exit 1
}

$cfgDir  = Join-Path $apoRoot "config"
$cfgFile = Join-Path $cfgDir "config.txt"
$profile = Join-Path $cfgDir $ProfileName

Write-Host "Equalizer APO: $apoRoot" -ForegroundColor Cyan
Write-Host "config.txt   : $cfgFile"

if (-not $DryRun -and -not $NoElevate) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Host "需要管理员权限，正在提权..." -ForegroundColor Yellow
        $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath)
        if ($Uninstall) { $argv += "-Uninstall" }
        Start-Process powershell -Verb RunAs -ArgumentList $argv | Out-Null
        exit 0
    }
}

if (Test-Path $cfgFile) {
    $backup = "$cfgFile.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    if ($DryRun) { Write-Host "[dry] 备份 -> $backup" }
    else { Copy-Item $cfgFile $backup; Write-Host "已备份 -> $backup" -ForegroundColor DarkGray }
}

$raw = @()
if (Test-Path $cfgFile) { $raw = @(Get-Content -Path $cfgFile -Encoding UTF8) }
# @() 包住：单行文件时 Where-Object 会退化成字符串，+= 就不换行了
$clean = @($raw | Where-Object { $_ -notmatch [regex]::Escape($ProfileName) })

if ($Uninstall) {
    if ($DryRun) {
        Write-Host "[dry] 从 config.txt 移除: $IncludeLine"
        Write-Host "[dry] 删除: $profile"
        exit 0
    }
    [System.IO.File]::WriteAllText($cfgFile, (($clean -join [Environment]::NewLine) + [Environment]::NewLine),
        (New-Object System.Text.UTF8Encoding($false)))
    if (Test-Path $profile) { Remove-Item $profile -Force }
    Write-Host "已卸载。重启音频后生效（Equalizer APO Configurator -> Restart audio service）。" -ForegroundColor Green
    exit 0
}

# Equalizer APO 的解析器对非 ASCII 注释不稳，这里全部用英文
$profileBody = @'
# FPS footstep EQ (Equalizer APO)
# Cut 60-150Hz rumble / vehicle low end, lift 2-4kHz footstep transients.
# IMPORTANT: same curve for ALL channels - per-channel curves break direction.
Preamp: -4 dB

# ---- low end ----
Filter: ON HPQ Fc 25 Hz  Q 0.70
Filter: ON HPQ Fc 90 Hz  Q 0.70
Filter: ON PK  Fc 220 Hz Gain -3 dB Q 1.10

# ---- mid ----
Filter: ON PK  Fc 1200 Hz Gain +1.5 dB Q 1.00
Filter: ON PK  Fc 2800 Hz Gain +3.0 dB Q 1.20
Filter: ON PK  Fc 4500 Hz Gain +1.5 dB Q 1.50

# ---- top ----
Filter: ON HS  Fc 9000 Hz Gain -4 dB Q 0.70
'@

if ($DryRun) {
    Write-Host "[dry] 写入 $profile"
    Write-Host "[dry] config.txt 追加: $IncludeLine"
    exit 0
}

$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($profile, $profileBody, $utf8)
$clean += $IncludeLine
[System.IO.File]::WriteAllText($cfgFile, (($clean -join [Environment]::NewLine) + [Environment]::NewLine), $utf8)

Write-Host "已安装。" -ForegroundColor Green
Write-Host "下一步：" -ForegroundColor Cyan
Write-Host "  1. 打开 Equalizer APO Configurator，确认你的 7.1 虚拟声卡已启用"
Write-Host "  2. 点 Configurator 里的 Restart audio service（或重启游戏）"
Write-Host "  3. 用本工具的电平表确认：同一位置脚步应该比之前更容易越过阈值"
Write-Host "卸载：本脚本加 -Uninstall" -ForegroundColor Gray
