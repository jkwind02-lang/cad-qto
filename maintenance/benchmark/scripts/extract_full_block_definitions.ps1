param(
    [Parameter(Mandatory = $true)][string]$SourceDwg,
    [Parameter(Mandatory = $true)][string]$OutputNdjson,
    [Parameter(Mandatory = $true)][string]$MetadataJson
)

$ErrorActionPreference = 'Stop'
$sourcePath = (Resolve-Path -LiteralPath $SourceDwg).Path
$streamPath = [System.IO.Path]::GetFullPath($OutputNdjson)
$metadataPath = [System.IO.Path]::GetFullPath($MetadataJson)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $streamPath),(Split-Path -Parent $metadataPath) | Out-Null
$sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()

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

function Get-EntityType {
    param([string]$ObjectName)
    switch ($ObjectName) {
        'AcDbLine' { 'LINE' }
        'AcDbPolyline' { 'LWPOLYLINE' }
        'AcDb2dPolyline' { 'POLYLINE' }
        'AcDb3dPolyline' { '3DPOLYLINE' }
        'AcDbArc' { 'ARC' }
        'AcDbCircle' { 'CIRCLE' }
        'AcDbSpline' { 'SPLINE' }
        'AcDbEllipse' { 'ELLIPSE' }
        'AcDbBlockReference' { 'INSERT' }
        'AcDbMInsertBlock' { 'MINSERT' }
        'AcDbText' { 'TEXT' }
        'AcDbMText' { 'MTEXT' }
        'AcDbAttribute' { 'ATTRIB' }
        'AcDbAttributeDefinition' { 'ATTDEF' }
        'AcDbPoint' { 'POINT' }
        'AcDbHatch' { 'HATCH' }
        'AcDbSolid' { 'SOLID' }
        default { $ObjectName }
    }
}

function Get-CurveEndpoints {
    param([object]$Entity, [string]$EntityType)
    $start = $null
    $end = $null
    if ($EntityType -in @('LINE', 'ARC')) {
        $start = Convert-Point (Get-ComValue $Entity 'StartPoint')
        $end = Convert-Point (Get-ComValue $Entity 'EndPoint')
    } elseif ($EntityType -in @('LWPOLYLINE', 'POLYLINE', '3DPOLYLINE')) {
        try {
            $coordinates = @($Entity.Coordinates)
            $step = if ($EntityType -eq 'LWPOLYLINE') { 2 } else { 3 }
            if ($coordinates.Count -ge ($step * 2)) {
                $start = @([double]$coordinates[0], [double]$coordinates[1], $(if ($step -eq 3) { [double]$coordinates[2] } else { 0.0 }))
                $last = $coordinates.Count - $step
                $end = @([double]$coordinates[$last], [double]$coordinates[$last + 1], $(if ($step -eq 3) { [double]$coordinates[$last + 2] } else { 0.0 }))
            }
        } catch { }
    }
    return @($start, $end)
}

function Get-NativeLength {
    param([object]$Entity, [string]$EntityType)
    try {
        switch ($EntityType) {
            'ARC' { return [double]$Entity.ArcLength }
            'CIRCLE' { return [double]$Entity.Circumference }
            { $_ -in @('LINE', 'LWPOLYLINE', 'POLYLINE', '3DPOLYLINE', 'SPLINE') } { return [double]$Entity.Length }
        }
    } catch { }
    return $null
}

function New-BlockEntityRecord {
    param([object]$Entity, [string]$ParentBlock, [int]$EntityIndex)
    $objectName = [string](Get-ComValue $Entity 'ObjectName')
    $entityType = Get-EntityType $objectName
    $handle = [string](Get-ComValue $Entity 'Handle')
    if ([string]::IsNullOrWhiteSpace($handle)) { $handle = "BLOCK-$ParentBlock-INDEX-$EntityIndex" }
    $endpoints = Get-CurveEndpoints $Entity $entityType
    $start = $endpoints[0]
    $end = $endpoints[1]
    $insertion = Convert-Point (Get-ComValue $Entity 'InsertionPoint')
    $center = Convert-Point (Get-ComValue $Entity 'Center')
    $anchor = if ($start) { $start } elseif ($insertion) { $insertion } elseif ($center) { $center } else { $null }
    $bbox = $null
    if ($start -and $end) {
        $bbox = @(
            [Math]::Min([double]$start[0], [double]$end[0]),
            [Math]::Min([double]$start[1], [double]$end[1]),
            [Math]::Max([double]$start[0], [double]$end[0]),
            [Math]::Max([double]$start[1], [double]$end[1])
        )
    } elseif ($anchor) {
        $bbox = @([double]$anchor[0], [double]$anchor[1], [double]$anchor[0], [double]$anchor[1])
    }
    $record = [ordered]@{
        row_type = 'entity'
        layout = 'BlockDefinition'
        parent_block = $ParentBlock
        block_entity_index = $EntityIndex
        record_id = 'DWG-' + $handle
        entity_handle = $handle
        candidate_physical_id = 'DWG-' + $handle
        entity_type = $entityType
        object_name = $objectName
        layer = [string](Get-ComValue $Entity 'Layer')
        outcome = 'extracted'
        measurement_shape = if ($entityType -in @('LINE','LWPOLYLINE','POLYLINE','3DPOLYLINE','ARC','SPLINE')) { 'centerline' } elseif ($entityType -in @('CIRCLE','ELLIPSE')) { 'symbol_candidate' } else { 'not_applicable' }
        geometry_method = 'autocad_activex_block_definition'
        confidence = 'A'
        raw_unit = 'Millimeters'
        source_sha256 = $sourceHash
        x = if ($anchor) { [double]$anchor[0] } else { $null }
        y = if ($anchor) { [double]$anchor[1] } else { $null }
        z = if ($anchor) { [double]$anchor[2] } else { $null }
        bbox = $bbox
        start = $start
        end = $end
        raw_length = Get-NativeLength $Entity $entityType
        closed = Get-ComValue $Entity 'Closed'
        text = $null
        block_name = $null
        effective_block_name = $null
        rotation = Get-ComValue $Entity 'Rotation'
        scale_factors = $null
        attributes = $null
        geometry_signature = $null
    }
    if ($entityType -in @('TEXT','MTEXT','ATTRIB','ATTDEF')) {
        $record.text = [string](Get-ComValue $Entity 'TextString')
    }
    if ($entityType -in @('INSERT','MINSERT')) {
        $record.block_name = [string](Get-ComValue $Entity 'Name')
        $record.effective_block_name = [string](Get-ComValue $Entity 'EffectiveName')
        $record.scale_factors = @(
            (Get-ComValue $Entity 'XScaleFactor'),
            (Get-ComValue $Entity 'YScaleFactor'),
            (Get-ComValue $Entity 'ZScaleFactor')
        )
    }
    return $record
}

$acad = $null
$doc = $null
$writer = $null
try {
    $acad = New-Object -ComObject 'AutoCAD.Application.26'
    $acad.Visible = $false
    Start-Sleep -Seconds 2
    $doc = $acad.Documents.Open($sourcePath, $true)
    Start-Sleep -Seconds 2
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $writer = [System.IO.StreamWriter]::new($streamPath, $false, $utf8)
    $summaries = [System.Collections.Generic.List[object]]::new()
    $failures = [System.Collections.Generic.List[object]]::new()
    $written = 0
    $expected = 0

    for ($blockIndex = 0; $blockIndex -lt $doc.Blocks.Count; $blockIndex++) {
        $block = $doc.Blocks.Item($blockIndex)
        $blockName = [string](Get-ComValue $block 'Name')
        $isLayout = [bool](Get-ComValue $block 'IsLayout')
        $origin = Convert-Point (Get-ComValue $block 'Origin')
        $blockWritten = 0
        $blockFailures = 0
        if (-not $isLayout) {
            $expected += $block.Count
            for ($entityIndex = 0; $entityIndex -lt $block.Count; $entityIndex++) {
                try {
                    $entity = $block.Item($entityIndex)
                    $record = New-BlockEntityRecord $entity $blockName $entityIndex
                    $writer.WriteLine(($record | ConvertTo-Json -Compress -Depth 10))
                    $written++
                    $blockWritten++
                } catch {
                    $failure = [ordered]@{
                        row_type = 'extraction_failure'
                        layout = 'BlockDefinition'
                        parent_block = $blockName
                        block_entity_index = $entityIndex
                        record_id = "BLOCK-$blockName-INDEX-$entityIndex"
                        entity_type = $null
                        outcome = 'extraction_failed'
                        error = $_.Exception.Message
                        source_sha256 = $sourceHash
                    }
                    $writer.WriteLine(($failure | ConvertTo-Json -Compress -Depth 5))
                    $written++
                    $blockWritten++
                    $blockFailures++
                    $failures.Add($failure)
                }
                if ($written -gt 0 -and $written % 20000 -eq 0) {
                    Write-Output ("已提取块定义对象 {0}" -f $written)
                    $writer.Flush()
                }
            }
        }
        $summaries.Add([ordered]@{
            block_name = $blockName
            origin = $origin
            expected_entity_count = if ($isLayout) { 0 } else { [int]$block.Count }
            written_record_count = $blockWritten
            failure_count = $blockFailures
            is_layout = $isLayout
            is_xref = [bool](Get-ComValue $block 'IsXRef')
        })
        if (($blockIndex + 1) % 25 -eq 0) {
            Write-Output ("已检查块定义 {0}/{1}" -f ($blockIndex + 1), $doc.Blocks.Count)
        }
    }
    $writer.Flush()
    $writer.Close()
    $writer = $null
    $streamHash = (Get-FileHash -LiteralPath $streamPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $payload = [ordered]@{
        schema_version = 'qto-cad-full-block-closure/1.0'
        extractor_version = 'autocad-activex-full-block-definitions/1.0.0'
        source_path = $sourcePath
        source_sha256 = $sourceHash
        source_bytes = (Get-Item -LiteralPath $sourcePath).Length
        acad_version = [string](Get-ComValue $acad 'Version')
        block_definition_count = $doc.Blocks.Count
        expected_non_layout_entity_count = $expected
        written_record_count = $written
        extraction_failure_count = $failures.Count
        completion_rate = if ($expected -gt 0) { ($written - $failures.Count) / $expected } else { 1.0 }
        block_stream_path = $streamPath
        block_stream_sha256 = $streamHash
        blocks = $summaries
        failures = $failures
    }
    $payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $metadataPath -Encoding utf8NoBOM
    Write-Output ("块定义闭包提取完成：期望 {0}，写入 {1}，失败 {2}" -f $expected, $written, $failures.Count)
} finally {
    if ($writer) { $writer.Dispose() }
    if ($doc) { try { $doc.Close($false) } catch { } }
    if ($acad) { try { $acad.Quit() } catch { } }
    if ($doc) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc) }
    if ($acad) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($acad) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
