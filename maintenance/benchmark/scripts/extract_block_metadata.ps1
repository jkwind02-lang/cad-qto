param(
    [Parameter(Mandatory = $true)][string]$SourceDwg,
    [Parameter(Mandatory = $true)][string]$OutputJson
)

$ErrorActionPreference = 'Stop'
$sourcePath = (Resolve-Path -LiteralPath $SourceDwg).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputJson)
$outputDirectory = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

function Get-ComValue {
    param([object]$Object, [string]$Name)
    try { return $Object.$Name } catch { return $null }
}

function Convert-Point {
    param([object]$Value)
    if ($null -eq $Value) { return $null }
    try {
        $values = @($Value)
        if ($values.Count -lt 2) { return $null }
        return ,@([double]$values[0], [double]$values[1], $(if ($values.Count -ge 3) { [double]$values[2] } else { 0.0 }))
    } catch { return $null }
}

$acad = $null
$doc = $null
try {
    $acad = New-Object -ComObject 'AutoCAD.Application.26'
    $acad.Visible = $false
    Start-Sleep -Seconds 2
    $doc = $acad.Documents.Open($sourcePath, $true)
    Start-Sleep -Seconds 2
    $records = [System.Collections.Generic.List[object]]::new()
    $failures = [System.Collections.Generic.List[object]]::new()

    for ($index = 0; $index -lt $doc.Blocks.Count; $index++) {
        $block = $null
        try {
            $block = $doc.Blocks.Item($index)
            $name = [string](Get-ComValue $block 'Name')
            $origin = Convert-Point (Get-ComValue $block 'Origin')
            $records.Add([ordered]@{
                block_name = $name
                handle = [string](Get-ComValue $block 'Handle')
                origin = $origin
                entity_count = [int]$block.Count
                is_layout = [bool](Get-ComValue $block 'IsLayout')
                is_xref = [bool](Get-ComValue $block 'IsXRef')
                local_bbox = $null
                extent_status = 'NOT_EXTRACTED_OR_REQUIRED_FOR_BASEPOINT_PASS'
            })
        } catch {
            $failures.Add([ordered]@{ block_index = $index; error = $_.Exception.Message })
        }
    }

    $payload = [ordered]@{
        schema_version = 'qto-cad-block-metadata/1.0'
        source_path = $sourcePath
        source_sha256 = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
        source_bytes = (Get-Item -LiteralPath $sourcePath).Length
        extractor = 'AutoCAD 2027 ActiveX read-only block metadata'
        acad_version = [string](Get-ComValue $acad 'Version')
        document_name = [string](Get-ComValue $doc 'Name')
        model_entity_count = [int]$doc.ModelSpace.Count
        document_block_count = [int]$doc.Blocks.Count
        block_count = $records.Count
        failure_count = $failures.Count
        blocks = $records
        failures = $failures
    }
    $payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $outputPath -Encoding utf8NoBOM
} finally {
    if ($doc) {
        try { $doc.Close($false) } catch { }
    }
    if ($acad) {
        try { $acad.Quit() } catch { }
    }
    if ($doc) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc) }
    if ($acad) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($acad) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
