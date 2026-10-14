param(
    [Parameter(Mandatory = $true)][string]$SourceDwg,
    [Parameter(Mandatory = $true)][string]$OutputDxf
)

$ErrorActionPreference = 'Stop'
$sourcePath = (Resolve-Path -LiteralPath $SourceDwg).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputDxf)
if ([System.IO.Path]::GetExtension($outputPath) -ne '.dxf') {
    throw 'OutputDxf must use the .dxf extension.'
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputPath) | Out-Null

$acad = $null
$doc = $null
try {
    $acad = New-Object -ComObject 'AutoCAD.Application.26'
    $acad.Visible = $false
    Start-Sleep -Seconds 2
    $doc = $acad.Documents.Open($sourcePath, $true)
    Start-Sleep -Seconds 2
    # 65 = ac2018_dxf. This is one database export call, not per-entity COM.
    $doc.SaveAs($outputPath, 65)
    if (-not (Test-Path -LiteralPath $outputPath)) {
        throw 'AutoCAD SaveAs returned without creating the DXF.'
    }
    [pscustomobject]@{
        status = 'PASS'
        source = $sourcePath
        output = $outputPath
        output_bytes = (Get-Item -LiteralPath $outputPath).Length
        output_sha256 = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    } | ConvertTo-Json -Compress
} finally {
    if ($doc) { try { $doc.Close($false) } catch { } }
    if ($acad) { try { $acad.Quit() } catch { } }
    if ($doc) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc) }
    if ($acad) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($acad) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
