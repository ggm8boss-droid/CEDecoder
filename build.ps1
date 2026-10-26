# Builds the decompilers cedecode.py uses:
#   tools\unluac.jar  (HansWessels/unluac, needs a JDK)
#   tools\luadec.exe  (viruscamp/luadec for Lua 5.3, needs Visual Studio with C++ tools)
# Sources are cloned into ref\, intermediates go to build\, and TMP/TEMP point into build\tmp
# so nothing is written outside this folder.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1 [-Clean]

param([switch]$Clean)
$ErrorActionPreference = "Stop"

$root  = $PSScriptRoot
$ref   = Join-Path $root "ref"
$build = Join-Path $root "build"
$tools = Join-Path $root "tools"
$tmp   = Join-Path $build "tmp"

if ($Clean) { Remove-Item -Recurse -Force $build, $tools -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force $ref, $build, $tools, $tmp | Out-Null
$env:TMP = $tmp
$env:TEMP = $tmp

function Invoke-Git {
    # git reports progress on stderr; don't let that count as a failure
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & git @args 2>&1 | ForEach-Object { "  $_" }
    $ErrorActionPreference = $old
    if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') failed" }
}

# ------------------------------------------------------------------ sources

$unluacSrc = Join-Path $ref "unluac"
if (-not (Test-Path $unluacSrc)) {
    Write-Host "Cloning unluac..."
    Invoke-Git clone -q --depth 1 https://github.com/HansWessels/unluac $unluacSrc
}

$luadecSrc = Join-Path $ref "luadec"
if (-not (Test-Path $luadecSrc)) {
    Write-Host "Cloning luadec..."
    Invoke-Git clone -q --depth 1 https://github.com/viruscamp/luadec $luadecSrc
}
if (-not (Test-Path (Join-Path $luadecSrc "lua-5.3\src\lua.h"))) {
    Write-Host "Fetching luadec's Lua 5.3 submodule..."
    Invoke-Git -C $luadecSrc submodule update --init --depth 1 lua-5.3
}

# ------------------------------------------------------------------ unluac.jar

$javac = $null
if ($env:JAVA_HOME -and (Test-Path "$env:JAVA_HOME\bin\javac.exe")) { $javac = "$env:JAVA_HOME\bin\javac.exe" }
if (-not $javac) {
    $jdk = Get-ChildItem "$env:ProgramFiles\Java", "$env:ProgramFiles\Microsoft", "$env:ProgramFiles\Eclipse Adoptium" -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path "$($_.FullName)\bin\javac.exe" } | Sort-Object Name -Descending | Select-Object -First 1
    if ($jdk) { $javac = "$($jdk.FullName)\bin\javac.exe" }
}
if (-not $javac) { throw "No JDK found (javac.exe). Install one or set JAVA_HOME." }
$jar = Join-Path (Split-Path $javac) "jar.exe"

Write-Host "Building unluac.jar with $javac"
$classes = Join-Path $build "unluac_classes"
Remove-Item -Recurse -Force $classes -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $classes | Out-Null
& $javac -nowarn -encoding UTF-8 -d $classes -sourcepath "$unluacSrc\src" "$unluacSrc\src\unluac\Main.java"
if ($LASTEXITCODE -ne 0) { throw "javac failed" }
& $jar -cfm (Join-Path $tools "unluac.jar") "$unluacSrc\src\unluac\Manifest.mf" -C $classes .
if ($LASTEXITCODE -ne 0) { throw "jar failed" }

# ------------------------------------------------------------------ luadec.exe

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "Visual Studio not found (vswhere.exe missing)." }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "Visual Studio C++ build tools not installed." }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"

Write-Host "Building luadec.exe with $vs"
$obj = Join-Path $build "luadec_obj"
Remove-Item -Recurse -Force $obj -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $obj | Out-Null

$luaFiles = Get-ChildItem "$luadecSrc\lua-5.3\src\*.c" | Where-Object { $_.Name -notin "lua.c", "luac.c" } |
    ForEach-Object { "`"$($_.FullName)`"" }
$decFiles = "guess luadec decompile disassemble proto StringBuffer structs statement macro-array expression".Split(" ") |
    ForEach-Object { "`"$luadecSrc\luadec\$_.c`"" }

$cmd = Join-Path $build "build_luadec.cmd"
@"
@echo off
set "PATH=${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer;%PATH%"
call "$vcvars" >nul || exit /b 1
cd /d "$obj"
cl /nologo /O2 /W0 /DLUA_COMPAT_5_2 /D_CRT_SECURE_NO_WARNINGS "/DSRCVERSION=\"git\"" /I"$luadecSrc\lua-5.3\src" /Fd"$obj\\" $($luaFiles -join ' ') $($decFiles -join ' ') /Fe:"$tools\luadec.exe" >build.log 2>&1 || (type build.log & exit /b 1)
"@ | Set-Content -Encoding ascii $cmd
cmd /c "`"$cmd`""
if ($LASTEXITCODE -ne 0) { throw "luadec build failed (see $obj\build.log)" }

Write-Host ""
Write-Host "Done:"
Get-Item (Join-Path $tools "unluac.jar"), (Join-Path $tools "luadec.exe") | ForEach-Object { "  {0}  ({1:N0} bytes)" -f $_.FullName, $_.Length }
