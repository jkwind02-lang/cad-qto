param(
    [Parameter(Mandatory = $true)][string]$SourceDwg,
    [Parameter(Mandatory = $true)][string]$OutputDxf,
    [ValidateSet('auto', 'coreconsole')][string]$ExportBackend = 'auto',
    [ValidateRange(1, 3600)][int]$TimeoutSeconds = 600
)

$ErrorActionPreference = 'Stop'

function Get-SourceSnapshot([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    return [pscustomobject]@{
        Sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        Length = $item.Length
        LastWriteTimeUtc = $item.LastWriteTimeUtc.Ticks
    }
}

function Close-AutoCad([object]$Document, [object]$Application) {
    if ($Document) { try { $Document.Close($false) } catch { } }
    if ($Application) { try { $Application.Quit() } catch { } }
    if ($Document) { try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Document) } catch { } }
    if ($Application) { try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Application) } catch { } }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

function Invoke-ComExport([string]$WorkingDwg, [string]$TargetDxf) {
    $lastError = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $acad = $null
        $doc = $null
        try {
            $acad = New-Object -ComObject 'AutoCAD.Application'
            $acad.Visible = $false
            $doc = $acad.Documents.Open($WorkingDwg, $true)
            $doc.SaveAs($TargetDxf, 65) # ac2018_dxf
            if (-not (Test-Path -LiteralPath $TargetDxf)) { throw 'AutoCAD COM 未生成交换 DXF。' }
            return
        } catch {
            $lastError = $_
        } finally {
            Close-AutoCad $doc $acad
        }
        if ($attempt -lt 3) { Start-Sleep -Milliseconds 1500 }
    }
    throw $lastError
}

function Find-AccoreConsole {
    $command = Get-Command 'accoreconsole.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    $candidates = Get-ChildItem -Path "$env:ProgramFiles\Autodesk\AutoCAD *\accoreconsole.exe" -File -ErrorAction SilentlyContinue |
        Sort-Object -Property FullName -Descending
    if ($candidates) { return $candidates[0].FullName }
    throw 'AutoCAD COM 导出失败，且未找到 accoreconsole.exe 后备程序。'
}

function Invoke-CoreConsoleExport([string]$WorkingDwg, [string]$TargetDxf, [string]$StageDirectory, [int]$MaximumSeconds) {
    $console = Find-AccoreConsole
    $scriptPath = Join-Path $StageDirectory 'export-dxf.scr'
    $scriptLines = @(
        '_.FILEDIA', '0',
        '_.CMDECHO', '0',
        '_.DXFOUT', ('"{0}"' -f $TargetDxf), '2018',
        '_.QUIT', '_N'
    )
    [System.IO.File]::WriteAllLines($scriptPath, $scriptLines, [System.Text.Encoding]::ASCII)

    $stdoutPath = Join-Path $StageDirectory 'accoreconsole.stdout.log'
    $stderrPath = Join-Path $StageDirectory 'accoreconsole.stderr.log'
    $arguments = @('/i', ('"{0}"' -f $WorkingDwg), '/s', ('"{0}"' -f $scriptPath))
    $process = Start-Process -FilePath $console -ArgumentList $arguments -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit($MaximumSeconds * 1000)) {
        try { $process.Kill() } catch { }
        $process.WaitForExit()
        throw "accoreconsole 超过 $MaximumSeconds 秒仍未完成，已终止工作副本转换；权威源图未被打开。"
    }
    $process.WaitForExit()
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $TargetDxf)) {
        $stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -Raw -LiteralPath $stdoutPath } else { '' }
        $stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -Raw -LiteralPath $stderrPath } else { '' }
        throw "accoreconsole 导出失败（exit=$($process.ExitCode)）：$stderr $stdout"
    }
}

$sourcePath = (Resolve-Path -LiteralPath $SourceDwg).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputDxf)
if ([System.IO.Path]::GetExtension($sourcePath) -ine '.dwg') { throw 'SourceDwg must use the .dwg extension.' }
if ([System.IO.Path]::GetExtension($outputPath) -ine '.dxf') { throw 'OutputDxf must use the .dxf extension.' }
if ($sourcePath.Equals($outputPath, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'The exchange DXF must not overwrite the source DWG.' }

$outputParent = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Force -Path $outputParent | Out-Null
$sourceBefore = Get-SourceSnapshot $sourcePath
$stageDirectory = Join-Path $outputParent ('.qto-dwg-export-' + [Guid]::NewGuid().ToString('N'))
$stageDirectory = [System.IO.Path]::GetFullPath($stageDirectory)
$expectedPrefix = [System.IO.Path]::GetFullPath($outputParent).TrimEnd('\') + '\'
if (-not $stageDirectory.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '临时导出目录不在输出目录内，已拒绝执行。'
}
New-Item -ItemType Directory -Path $stageDirectory | Out-Null
$workingDwg = Join-Path $stageDirectory ([System.IO.Path]::GetFileName($sourcePath))
$backendUsed = $null
$comError = $null

try {
    Copy-Item -LiteralPath $sourcePath -Destination $workingDwg
    if (Test-Path -LiteralPath $outputPath) { Remove-Item -LiteralPath $outputPath -Force }
    if ($ExportBackend -eq 'coreconsole') {
        Invoke-CoreConsoleExport $workingDwg $outputPath $stageDirectory $TimeoutSeconds
        $backendUsed = 'accoreconsole_staged_copy'
    } else {
        try {
            Invoke-ComExport $workingDwg $outputPath
            $backendUsed = 'autocad_com_staged_copy'
        } catch {
            $comError = $_.Exception.Message
            if (Test-Path -LiteralPath $outputPath) { Remove-Item -LiteralPath $outputPath -Force }
            Invoke-CoreConsoleExport $workingDwg $outputPath $stageDirectory $TimeoutSeconds
            $backendUsed = 'accoreconsole_staged_copy'
        }
    }
} finally {
    if (Test-Path -LiteralPath $stageDirectory) {
        $resolvedStage = (Resolve-Path -LiteralPath $stageDirectory).Path
        if (-not $resolvedStage.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw '临时目录校验失败，拒绝清理。'
        }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}

$sourceAfter = Get-SourceSnapshot $sourcePath
if ($sourceBefore.Sha256 -ne $sourceAfter.Sha256 -or $sourceBefore.Length -ne $sourceAfter.Length -or $sourceBefore.LastWriteTimeUtc -ne $sourceAfter.LastWriteTimeUtc) {
    throw '权威源 DWG 在导出期间发生变化；输出已判无效，请先核查源文件。'
}
if (-not (Test-Path -LiteralPath $outputPath)) { throw '未生成交换 DXF。' }

[pscustomobject]@{
    schema_version = 'qto-dwg-exchange-export/1.1'
    status = 'PASS'
    backend = $backendUsed
    source = $sourcePath
    source_sha256 = $sourceAfter.Sha256
    source_immutable_verified = $true
    output = $outputPath
    output_bytes = (Get-Item -LiteralPath $outputPath).Length
    output_sha256 = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    com_fallback_reason = $comError
} | ConvertTo-Json -Compress
