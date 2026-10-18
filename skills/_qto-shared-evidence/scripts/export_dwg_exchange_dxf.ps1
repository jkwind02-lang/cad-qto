param(
    [Parameter(Mandatory = $true)][string]$SourceDwg,
    [Parameter(Mandatory = $true)][string]$OutputDxf,
    [ValidateSet('auto', 'coreconsole', 'oda', 'oda-audit')][string]$ExportBackend = 'auto',
    [ValidateRange(1, 3600)][int]$TimeoutSeconds = 600
)

$ErrorActionPreference = 'Stop'
# A Python caller launched from pwsh can leave Windows PowerShell 5.1 unable to
# auto-discover the Get-FileHash function. Load its built-in module explicitly.
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop

function Get-SourceSnapshot([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    return [pscustomobject]@{
        Sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        Length = $item.Length
        LastWriteTimeUtc = $item.LastWriteTimeUtc.Ticks
    }
}

function Test-DxfCompletion([string]$Path) {
    # Export backends produce ASCII DXF. A nonempty file or exit=0 does not
    # prove a complete export: some hosts stop midway through a block record.
    # The scan runs in compiled C# (DxfCompletion.cs next to this script): the
    # former PowerShell per-line loop took 80-230 s on 200 MB exports, longer
    # than the export itself; the compiled scan takes seconds.
    if (-not ('QtoDxfCompletion' -as [type])) {
        Add-Type -Path (Join-Path $PSScriptRoot 'DxfCompletion.cs') -ErrorAction Stop
    }
    $failure = [QtoDxfCompletion]::Check($Path)
    if ($null -ne $failure) { throw $failure }
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
        # ASCII command tokens only; Unicode paths stay in the process API.
        '_.DXFOUT', 'exchange.dxf', '_V', '2018', '16',
        '_.QUIT', '_N'
    )
    [System.IO.File]::WriteAllLines($scriptPath, $scriptLines, [System.Text.Encoding]::ASCII)

    $stdoutPath = Join-Path $StageDirectory 'accoreconsole.stdout.log'
    $stderrPath = Join-Path $StageDirectory 'accoreconsole.stderr.log'
    $arguments = @('/i', ('"{0}"' -f $WorkingDwg), '/s', ('"{0}"' -f $scriptPath))
    $process = Start-Process -FilePath $console -ArgumentList $arguments -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -WindowStyle Hidden -WorkingDirectory $StageDirectory -PassThru
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

function Invoke-OdaExport([string]$WorkingDwg, [string]$TargetDxf, [string]$StageDirectory, [int]$MaximumSeconds, [bool]$AuditCopy = $false) {
    $converter = Get-Command 'ODAFileConverter.exe' -ErrorAction SilentlyContinue
    $executable = if ($converter) { $converter.Source } else { $null }
    if (-not $executable) {
        $odaRoot = Join-Path $env:ProgramFiles 'ODA'
        if (Test-Path -LiteralPath $odaRoot) {
            $candidates = @(Get-ChildItem -LiteralPath $odaRoot -Directory | Sort-Object Name -Descending)
            foreach ($candidate in $candidates) {
                $candidateExe = Join-Path $candidate.FullName 'ODAFileConverter.exe'
                if (Test-Path -LiteralPath $candidateExe) { $executable = $candidateExe; break }
            }
        }
    }
    if (-not $executable) { throw '未找到已安装的ODAFileConverter；未自动安装或修复原图。' }
    $odaInput = Join-Path $StageDirectory 'oda-input'
    $odaOutput = Join-Path $StageDirectory 'oda-output'
    New-Item -ItemType Directory -Path $odaInput, $odaOutput | Out-Null
    Copy-Item -LiteralPath $WorkingDwg -Destination (Join-Path $odaInput 'source.dwg')
    # Restrict to the one staged drawing; no recursive traversal or audit repair.
    $auditFlag = if ($AuditCopy) { '1' } else { '0' }
    $arguments = @(('"{0}"' -f $odaInput), ('"{0}"' -f $odaOutput), 'ACAD2018', 'DXF', '0', $auditFlag, 'source.dwg')
    $process = Start-Process -FilePath $executable -ArgumentList $arguments -WindowStyle Hidden -PassThru -WorkingDirectory $StageDirectory -RedirectStandardOutput (Join-Path $StageDirectory 'oda.stdout.log') -RedirectStandardError (Join-Path $StageDirectory 'oda.stderr.log')
    if (-not $process.WaitForExit($MaximumSeconds * 1000)) {
        try { $process.Kill() } catch { }
        $process.WaitForExit()
        throw 'ODA工作副本转换超时。'
    }
    $process.WaitForExit()
    $converted = Join-Path $odaOutput 'source.dxf'
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $converted)) { throw 'ODA未生成交换DXF。' }
    $errorFile = $converted + '.err'
    if ((Test-Path -LiteralPath $errorFile) -and (Get-Item -LiteralPath $errorFile).Length -gt 0) {
        throw ('ODA导出报告错误：' + (Get-Content -Raw -LiteralPath $errorFile))
    }
    # Completion is verified once by the main flow after the copy; scanning here too doubled the cost.
    Copy-Item -LiteralPath $converted -Destination $TargetDxf -Force
}

function Clear-ExportStage([string]$StageDirectory, [string]$ExpectedPrefix) {
    # Cleanup has its own outcome: it must never replace the export's error or
    # prevent a verified DXF from receiving its success receipt.
    $result = [pscustomobject]@{
        status = 'NOT_NEEDED'
        stage_directory = $StageDirectory
        retained = $false
        attempts = 0
        warning = $null
    }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $canRetry = $false
        try {
            if (-not (Test-Path -LiteralPath $StageDirectory)) { return $result }
            $result.attempts = $attempt
            $resolved = (Resolve-Path -LiteralPath $StageDirectory).Path
            if (-not $resolved.Equals([IO.Path]::GetFullPath($StageDirectory), [StringComparison]::OrdinalIgnoreCase) -or
                -not $resolved.StartsWith($ExpectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw '临时目录校验失败，拒绝清理。'
            }
            # Inspect without following a child junction: recursive deletion is
            # allowed only for the exact tree created by this invocation.
            $pending = New-Object 'System.Collections.Generic.Stack[string]'
            $pending.Push($resolved)
            while ($pending.Count) {
                $directory = $pending.Pop()
                if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    throw '临时目录含链接，拒绝递归清理。'
                }
                foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
                    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                        throw '临时目录含链接，拒绝递归清理。'
                    }
                    if ($item.PSIsContainer) { $pending.Push($item.FullName) }
                }
            }
            $canRetry = $true
            Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction Stop
            $result.status = 'CLEANED'
            $result.retained = $false
            $result.warning = $null
            return $result
        } catch {
            $result.status = if ($canRetry) { 'RETAINED' } else { 'REFUSED' }
            $result.retained = $true
            $result.warning = $_.Exception.Message
            if (-not $canRetry) { return $result }
        }
        if ($attempt -lt 3) { Start-Sleep -Milliseconds 200 }
    }
    return $result
}

$sourcePath = (Resolve-Path -LiteralPath $SourceDwg).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputDxf)
if ([System.IO.Path]::GetExtension($sourcePath) -ine '.dwg') { throw 'SourceDwg must use the .dwg extension.' }
if ([System.IO.Path]::GetExtension($outputPath) -ine '.dxf') { throw 'OutputDxf must use the .dxf extension.' }
if ($sourcePath.Equals($outputPath, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'The exchange DXF must not overwrite the source DWG.' }
if (Test-Path -LiteralPath $outputPath) { throw '目标 DXF 已存在；请使用新的输出文件名，已有成果未修改。' }
$exportReceiptPath = $outputPath + '.export.json'
if (Test-Path -LiteralPath $exportReceiptPath) { throw '目标转换记录已存在；请使用新的输出文件名。' }
$failureReceiptPath = $outputPath + '.导出失败.json'
if (Test-Path -LiteralPath $failureReceiptPath) { throw '目标导出失败记录已存在；请使用新的输出文件名。' }

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
$workingDwg = Join-Path $stageDirectory 'source.dwg'
$stagedDxf = Join-Path $stageDirectory 'exchange.dxf'
$backendUsed = $null
$comError = $null
# The source is hashed once before and once after the export (in the try block, or in
# finally when the export failed earlier); a third pass on multi-GB sources was pure cost.
$sourceAfter = $null
$exportError = $null
$sourceVerificationError = $null
$backendCompleted = $false
$completionVerified = $false
$sourceImmutable = $false
$outputPublished = $false
$diagnostic = $null
$diagnosticWriteError = $null
$cleanup = $null

try {
    Copy-Item -LiteralPath $sourcePath -Destination $workingDwg
    if ($ExportBackend -in @('oda', 'oda-audit')) {
        $backendUsed = if ($ExportBackend -eq 'oda-audit') { 'oda_staged_copy_audit' } else { 'oda_staged_copy_no_audit' }
        Invoke-OdaExport $workingDwg $stagedDxf $stageDirectory $TimeoutSeconds ($ExportBackend -eq 'oda-audit')
    } elseif ($ExportBackend -eq 'coreconsole') {
        $backendUsed = 'accoreconsole_staged_copy'
        Invoke-CoreConsoleExport $workingDwg $stagedDxf $stageDirectory $TimeoutSeconds
    } else {
        try {
            $backendUsed = 'autocad_com_staged_copy'
            Invoke-ComExport $workingDwg $stagedDxf
        } catch {
            $comError = $_.Exception.Message
            if (Test-Path -LiteralPath $stagedDxf) { Remove-Item -LiteralPath $stagedDxf -Force }
            $backendUsed = 'accoreconsole_staged_copy'
            Invoke-CoreConsoleExport $workingDwg $stagedDxf $stageDirectory $TimeoutSeconds
        }
    }
    $backendCompleted = $true
    if (-not (Test-Path -LiteralPath $stagedDxf) -or (Get-Item -LiteralPath $stagedDxf).Length -eq 0) {
        throw '未生成非空交换 DXF；已有成果未修改。'
    }
    Test-DxfCompletion $stagedDxf
    $completionVerified = $true
    $sourceAfter = Get-SourceSnapshot $sourcePath
    if ($sourceBefore.Sha256 -ne $sourceAfter.Sha256 -or $sourceBefore.Length -ne $sourceAfter.Length -or $sourceBefore.LastWriteTimeUtc -ne $sourceAfter.LastWriteTimeUtc) {
        throw '权威源 DWG 在导出期间发生变化；不发布本次输出。'
    }
    Move-Item -LiteralPath $stagedDxf -Destination $outputPath -ErrorAction Stop
    $outputPublished = $true
} catch {
    $exportError = $_
    $diagnostic = $_.Exception.Message
    # Capture backend logs before cleanup can remove them; diagnostic failures
    # remain secondary and cannot replace the original export error.
    foreach ($logName in @('accoreconsole.stdout.log', 'accoreconsole.stderr.log', 'oda.stdout.log', 'oda.stderr.log')) {
        try {
            $logPath = Join-Path $stageDirectory $logName
            if (Test-Path -LiteralPath $logPath) { $diagnostic += "`n" + (Get-Content -Raw -LiteralPath $logPath) }
        } catch {
            $diagnostic += "`n读取转换日志失败：" + $_.Exception.Message
        }
    }
} finally {
    try {
        if ($null -eq $sourceAfter) { $sourceAfter = Get-SourceSnapshot $sourcePath }
        $sourceImmutable = ($sourceBefore.Sha256 -eq $sourceAfter.Sha256 -and $sourceBefore.Length -eq $sourceAfter.Length -and
            $sourceBefore.LastWriteTimeUtc -eq $sourceAfter.LastWriteTimeUtc)
        if (-not $sourceImmutable) { throw '权威源 DWG 在导出期间发生变化；本次导出无效。' }
    } catch {
        $sourceVerificationError = $_.Exception.Message
        if ($null -eq $exportError) { $exportError = $_ }
    }
    $cleanup = Clear-ExportStage $stageDirectory $expectedPrefix
}

if ($cleanup.retained) {
    Write-Warning ("导出暂存资源未清理：{0}；{1}" -f $cleanup.stage_directory, $cleanup.warning) -WarningAction Continue
}
if ($null -ne $exportError) {
    $diagnosticPath = $outputPath + '.导出失败.log'
    if ($null -eq $diagnostic) { $diagnostic = $exportError.Exception.Message }
    if ($sourceVerificationError) { $diagnostic += "`n源文件后验：" + $sourceVerificationError }
    try {
        if (-not (Test-Path -LiteralPath $diagnosticPath)) {
            [IO.File]::WriteAllText($diagnosticPath, $diagnostic, [Text.UTF8Encoding]::new($false))
        }
    } catch { $diagnosticWriteError = $_.Exception.Message }
    # A failure receipt deliberately uses a different name: consumers must not
    # mistake it for the success .export.json bound to a usable exchange DXF.
    $failureReceipt = [pscustomobject]@{
        schema_version = 'qto-dwg-exchange-export/1.4'
        status = 'FAIL'
        export_status = 'FAILED'
        backend = $backendUsed
        backend_completed = $backendCompleted
        source = $sourcePath
        source_sha256_before = $sourceBefore.Sha256
        source_sha256_after = $(if ($sourceAfter) { $sourceAfter.Sha256 } else { $null })
        source_immutable_verified = $sourceImmutable
        source_verification_error = $sourceVerificationError
        dxf_completion_verified = $completionVerified
        output = $outputPath
        output_published = $outputPublished
        export_error = [pscustomobject]@{ message = $exportError.Exception.Message; type = $exportError.Exception.GetType().FullName }
        diagnostic_path = $diagnosticPath
        diagnostic_write_error = $diagnosticWriteError
        cleanup = $cleanup
    }
    try {
        [IO.File]::WriteAllText($failureReceiptPath, ($failureReceipt | ConvertTo-Json -Depth 6 -Compress), [Text.UTF8Encoding]::new($false))
    } catch {
        Write-Warning ('导出失败回执写入失败：' + $_.Exception.Message) -WarningAction Continue
    }
    throw $exportError
}

if (-not (Test-Path -LiteralPath $outputPath)) { throw '未生成交换 DXF。' }

$exportReceipt = [pscustomobject]@{
    schema_version = 'qto-dwg-exchange-export/1.4'
    status = 'PASS'
    export_status = 'SUCCEEDED'
    backend = $backendUsed
    backend_completed = $backendCompleted
    source = $sourcePath
    source_sha256 = $sourceAfter.Sha256
    source_immutable_verified = $true
    dxf_completion_verified = $true
    exchange_geometry_review_required = ($ExportBackend -eq 'oda-audit')
    output = $outputPath
    output_bytes = (Get-Item -LiteralPath $outputPath).Length
    output_sha256 = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    com_fallback_reason = $comError
    cleanup = $cleanup
}
$receiptJson = $exportReceipt | ConvertTo-Json -Depth 6 -Compress
[System.IO.File]::WriteAllText($exportReceiptPath, $receiptJson, [System.Text.UTF8Encoding]::new($false))
$receiptJson
