[CmdletBinding(DefaultParameterSetName = 'Ocr')]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$InputPath,
    [ValidateRange(1, 10485760)][int]$MaxBytes = 10485760,
    [ValidateRange(1, 4096)][int]$MaxDimension = 4096,
    [ValidateRange(1, 4)][int]$UpscaleFactor = 2,
    [Parameter(ParameterSetName = 'Ocr')][ValidateRange(0, 32)][int]$PaddingPixels = 0,
    [ValidatePattern('^[A-Za-z-]+(?:,[A-Za-z-]+)*$')][string]$PreferredLanguages = 'zh-Hant,en',
    [Parameter(ParameterSetName = 'Fingerprint', Mandatory = $true)][switch]$FingerprintRegion,
    [Parameter(ParameterSetName = 'Fingerprint', Mandatory = $true)][ValidateRange(0, 2147483647)][int]$RegionX,
    [Parameter(ParameterSetName = 'Fingerprint', Mandatory = $true)][ValidateRange(0, 2147483647)][int]$RegionY,
    [Parameter(ParameterSetName = 'Fingerprint', Mandatory = $true)][ValidateRange(1, 2147483647)][int]$RegionWidth,
    [Parameter(ParameterSetName = 'Fingerprint', Mandatory = $true)][ValidateRange(1, 2147483647)][int]$RegionHeight,
    [Parameter(ParameterSetName = 'Fingerprint')][switch]$IncludeImage,
    [Parameter(ParameterSetName = 'Fingerprint')][ValidateRange(1, 1048576)][int]$MaxReturnedImageBytes = 1048576
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

function Write-OcrResponse {
    param([Parameter(Mandatory = $true)]$Value, [int]$ExitCode = 0)
    [Console]::Out.WriteLine(($Value | ConvertTo-Json -Depth 8 -Compress))
    exit $ExitCode
}

function Stop-Ocr {
    param(
        [Parameter(Mandatory = $true)][string]$Code,
        [Parameter(Mandatory = $true)][string]$Message,
        [hashtable]$Details = @{}
    )
    $body = [ordered]@{ success = $false; code = $Code; message = $Message }
    foreach ($key in $Details.Keys) { $body[$key] = $Details[$key] }
    Write-OcrResponse $body 20
}

function Test-PngSignature {
    param([Parameter(Mandatory = $true)][string]$Path)
    $expected = [byte[]](0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
        $actual = New-Object byte[] 8
        if ($stream.Read($actual, 0, $actual.Length) -ne $actual.Length) { return $false }
        for ($index = 0; $index -lt $expected.Length; $index += 1) {
            if ($actual[$index] -ne $expected[$index]) { return $false }
        }
        return $true
    } finally {
        $stream.Dispose()
    }
}

function Get-ImageDimensions {
    param([Parameter(Mandatory = $true)][string]$Path)
    Add-Type -AssemblyName System.Drawing
    $image = $null
    try {
        $image = [System.Drawing.Image]::FromFile($Path, $false)
        return [ordered]@{ width = [int]$image.Width; height = [int]$image.Height }
    } finally {
        if ($null -ne $image) { $image.Dispose() }
    }
}

function New-PaddedOcrInput {
    param([string]$Path, [int]$Padding)
    Add-Type -AssemblyName System.Drawing
    $source = $null
    $padded = $null
    $graphics = $null
    $outputPath = Join-Path ([System.IO.Path]::GetDirectoryName($Path)) ("padded-$([guid]::NewGuid().ToString('N')).png")
    try {
        $source = [System.Drawing.Bitmap]::new($Path)
        $padded = [System.Drawing.Bitmap]::new(($source.Width + 2 * $Padding), ($source.Height + 2 * $Padding), [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [System.Drawing.Graphics]::FromImage($padded)
        $graphics.Clear([System.Drawing.Color]::White)
        $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
        $graphics.DrawImageUnscaled($source, $Padding, $Padding)
        $padded.Save($outputPath, [System.Drawing.Imaging.ImageFormat]::Png)
        return $outputPath
    } finally {
        if ($null -ne $graphics) { $graphics.Dispose() }
        if ($null -ne $padded) { $padded.Dispose() }
        if ($null -ne $source) { $source.Dispose() }
    }
}

function Get-RegionFingerprint {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$X,
        [Parameter(Mandatory = $true)][int]$Y,
        [Parameter(Mandatory = $true)][int]$Width,
        [Parameter(Mandatory = $true)][int]$Height,
        [switch]$ReturnImage
    )

    Add-Type -AssemblyName System.Drawing
    $source = $null
    $cropped = $null
    $graphics = $null
    $lockedBitmap = $null
    $hashAlgorithm = $null
    $imageStream = $null
    try {
        $source = [System.Drawing.Bitmap]::new($Path)
        $cropped = [System.Drawing.Bitmap]::new($Width, $Height, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [System.Drawing.Graphics]::FromImage($cropped)
        $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
        $destination = [System.Drawing.Rectangle]::new(0, 0, $Width, $Height)
        $graphics.DrawImage($source, $destination, $X, $Y, $Width, $Height, [System.Drawing.GraphicsUnit]::Pixel)

        $pixelBounds = [System.Drawing.Rectangle]::new(0, 0, $Width, $Height)
        $lockedBitmap = $cropped.LockBits($pixelBounds, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $byteCount = [int64][Math]::Abs([int]$lockedBitmap.Stride) * [int64]$Height
        if ($byteCount -lt 1 -or $byteCount -gt [int]::MaxValue) { throw 'The requested fingerprint crop was too large.' }
        $pixelBytes = New-Object byte[] ([int]$byteCount)
        [System.Runtime.InteropServices.Marshal]::Copy($lockedBitmap.Scan0, $pixelBytes, 0, $pixelBytes.Length)
        $cropped.UnlockBits($lockedBitmap)
        $lockedBitmap = $null

        $hashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
        $digest = $hashAlgorithm.ComputeHash($pixelBytes)
        $sha256 = ([System.BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()

        $imageBytes = $null
        if ($ReturnImage) {
            $imageStream = [System.IO.MemoryStream]::new()
            $cropped.Save($imageStream, [System.Drawing.Imaging.ImageFormat]::Png)
            $imageBytes = $imageStream.ToArray()
        }
        return [pscustomobject]@{ sha256 = $sha256; imageBytes = $imageBytes }
    } finally {
        if ($null -ne $lockedBitmap -and $null -ne $cropped) { try { $cropped.UnlockBits($lockedBitmap) } catch {} }
        if ($null -ne $imageStream) { $imageStream.Dispose() }
        if ($null -ne $hashAlgorithm) { $hashAlgorithm.Dispose() }
        if ($null -ne $graphics) { $graphics.Dispose() }
        if ($null -ne $cropped) { $cropped.Dispose() }
        if ($null -ne $source) { $source.Dispose() }
    }
}

function Get-AsTaskMethod {
    return [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
            $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.IsGenericType -and
            $_.GetParameters()[0].ParameterType.GetGenericTypeDefinition().FullName -eq 'Windows.Foundation.IAsyncOperation`1'
        } |
        Select-Object -First 1
}

function Invoke-WinRtAsync {
    param(
        [Parameter(Mandatory = $true)]$Operation,
        [Parameter(Mandatory = $true)][Type]$ResultType
    )
    $invokeArgs = New-Object object[] 1
    $invokeArgs[0] = $Operation
    $task = $script:AsTaskMethod.MakeGenericMethod($ResultType).Invoke($null, $invokeArgs)
    return $task.GetAwaiter().GetResult()
}

function Get-UnionBounds {
    param([Parameter(Mandatory = $true)][object[]]$Words)
    if ($Words.Count -eq 0) { return [ordered]@{ x = 0; y = 0; width = 0; height = 0 } }
    $left = [double]::PositiveInfinity
    $top = [double]::PositiveInfinity
    $right = [double]::NegativeInfinity
    $bottom = [double]::NegativeInfinity
    foreach ($word in $Words) {
        $left = [Math]::Min($left, [double]$word.x)
        $top = [Math]::Min($top, [double]$word.y)
        $right = [Math]::Max($right, [double]$word.x + [double]$word.width)
        $bottom = [Math]::Max($bottom, [double]$word.y + [double]$word.height)
    }
    return [ordered]@{ x = $left; y = $top; width = $right - $left; height = $bottom - $top }
}

try {
    $fullInputPath = [System.IO.Path]::GetFullPath($InputPath)
    if (-not [System.IO.File]::Exists($fullInputPath)) {
        Stop-Ocr 'LINE_OCR_INVALID_IMAGE' 'The explicit OCR input file was not found.'
    }
    $inputInfo = Get-Item -LiteralPath $fullInputPath -Force
    if ($inputInfo.PSIsContainer -or $inputInfo.Length -gt $MaxBytes) {
        Stop-Ocr 'LINE_OCR_IMAGE_TOO_LARGE' "The explicit OCR input file exceeds the $MaxBytes-byte limit."
    }
    if (-not (Test-PngSignature $fullInputPath)) {
        Stop-Ocr 'LINE_OCR_INVALID_IMAGE' 'The explicit OCR input file is not a PNG.'
    }
    $sourceDimensions = Get-ImageDimensions $fullInputPath
    if ($sourceDimensions.width -lt 1 -or $sourceDimensions.height -lt 1 -or $sourceDimensions.width -gt $MaxDimension -or $sourceDimensions.height -gt $MaxDimension) {
        Stop-Ocr 'LINE_OCR_IMAGE_TOO_LARGE' "The explicit OCR input image exceeds the $MaxDimension-pixel dimension limit."
    }
} catch {
    Stop-Ocr 'LINE_OCR_INVALID_IMAGE' 'The explicit OCR input file could not be decoded as a PNG.'
}

if ($FingerprintRegion) {
    try {
        $right = [int64]$RegionX + [int64]$RegionWidth
        $bottom = [int64]$RegionY + [int64]$RegionHeight
        if ($right -gt [int64]$sourceDimensions.width -or $bottom -gt [int64]$sourceDimensions.height) {
            Stop-Ocr 'LINE_OCR_INVALID_REGION' 'Fingerprint region must be wholly inside the input PNG.'
        }
        $fingerprint = Get-RegionFingerprint -Path $fullInputPath -X $RegionX -Y $RegionY -Width $RegionWidth -Height $RegionHeight -ReturnImage:$IncludeImage
        if ($IncludeImage -and ($null -eq $fingerprint.imageBytes -or $fingerprint.imageBytes.Length -gt $MaxReturnedImageBytes)) {
            Stop-Ocr 'LINE_OCR_IMAGE_TOO_LARGE' "The cropped PNG exceeds the $MaxReturnedImageBytes-byte return limit."
        }
        $response = [ordered]@{
            success = $true
            sha256 = [string]$fingerprint.sha256
            width = $RegionWidth
            height = $RegionHeight
            region = [ordered]@{ x = $RegionX; y = $RegionY; width = $RegionWidth; height = $RegionHeight }
        }
        if ($IncludeImage) { $response.imageData = [Convert]::ToBase64String($fingerprint.imageBytes) }
        Write-OcrResponse $response
    } catch {
        $failureType = $_.Exception.GetType().FullName
        if ($null -ne $_.Exception.InnerException) {
            $failureType = "$($failureType):$($_.Exception.InnerException.GetType().FullName)"
        }
        Stop-Ocr 'LINE_OCR_FAILED' 'The explicit local PNG region could not be fingerprinted.' @{ failureType = $failureType }
    }
}

if ($PSVersionTable.PSEdition -ne 'Desktop' -or $PSVersionTable.PSVersion.Major -ne 5) {
    Stop-Ocr 'LINE_OCR_RUNTIME_UNAVAILABLE' 'Local LINE OCR requires Windows PowerShell 5.1.'
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapTransform, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapInterpolationMode, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.ExifOrientationMode, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.ColorManagementMode, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Storage.Streams.RandomAccessStreamReference, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Storage.Streams.IRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Foundation, ContentType = WindowsRuntime]
    $script:AsTaskMethod = Get-AsTaskMethod
    if ($null -eq $script:AsTaskMethod) { throw 'Windows Runtime AsTask conversion was unavailable.' }
} catch {
    Stop-Ocr 'LINE_OCR_RUNTIME_UNAVAILABLE' 'Windows.Media.Ocr is unavailable in this Windows PowerShell runtime.'
}

$availableLanguages = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages)
$availableTags = @($availableLanguages | ForEach-Object { [string]$_.LanguageTag })
$requestedLanguages = @($PreferredLanguages.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$engine = $null
$selectedLanguage = $null
foreach ($preference in $requestedLanguages) {
    $candidateLanguage = @($availableLanguages | Where-Object { $_.LanguageTag -ieq $preference -or $_.LanguageTag -like "$preference-*" } | Select-Object -First 1)[0]
    if ($null -ne $candidateLanguage) {
        $candidateEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($candidateLanguage)
        if ($null -ne $candidateEngine) {
            $engine = $candidateEngine
            $selectedLanguage = [string]$candidateLanguage.LanguageTag
            break
        }
    }
}
if ($null -eq $engine) {
    Stop-Ocr 'LINE_OCR_LANGUAGE_UNAVAILABLE' 'No locally installed zh-Hant or English OCR language was available.' @{ availableLanguages = $availableTags; requestedLanguages = $requestedLanguages }
}

$stream = $null
$bitmap = $null
$paddedInputPath = $null
try {
    $width = [int]$sourceDimensions.width
    $height = [int]$sourceDimensions.height
    $ocrInputWidth = [int64]$width + 2 * [int64]$PaddingPixels
    $ocrInputHeight = [int64]$height + 2 * [int64]$PaddingPixels
    if ($ocrInputWidth -gt $MaxDimension -or $ocrInputHeight -gt $MaxDimension) {
        Stop-Ocr 'LINE_OCR_IMAGE_TOO_LARGE' "The padded OCR image exceeds the $MaxDimension-pixel dimension limit."
    }
    $ocrInputPath = $fullInputPath
    if ($PaddingPixels -gt 0) {
        $paddedInputPath = New-PaddedOcrInput -Path $fullInputPath -Padding $PaddingPixels
        $ocrInputPath = $paddedInputPath
    }
    $storageFile = Invoke-WinRtAsync ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ocrInputPath)) ([Windows.Storage.StorageFile])
    $reference = [Windows.Storage.Streams.RandomAccessStreamReference]::CreateFromFile($storageFile)
    $stream = Invoke-WinRtAsync ($reference.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    $createDecoderMethod = [Windows.Graphics.Imaging.BitmapDecoder].GetMethods() |
        Where-Object { $_.Name -eq 'CreateAsync' -and $_.GetParameters().Count -eq 1 } |
        Select-Object -First 1
    if ($null -eq $createDecoderMethod) { throw 'Windows.Media.Ocr bitmap decoder creation was unavailable.' }
    $createDecoderArgs = New-Object object[] 1
    $createDecoderArgs[0] = $stream
    $decoder = Invoke-WinRtAsync ($createDecoderMethod.Invoke($null, $createDecoderArgs)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $ocrScaleFactor = 1
    $maxOcrDimension = [int][Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    $scaledWidth = $ocrInputWidth * [int64]$UpscaleFactor
    $scaledHeight = $ocrInputHeight * [int64]$UpscaleFactor
    if ($UpscaleFactor -gt 1 -and $scaledWidth -le $maxOcrDimension -and $scaledHeight -le $maxOcrDimension) {
        $ocrScaleFactor = $UpscaleFactor
    }

    if ($ocrScaleFactor -gt 1) {
        $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
        $transform.ScaledWidth = [uint32]($ocrInputWidth * $ocrScaleFactor)
        $transform.ScaledHeight = [uint32]($ocrInputHeight * $ocrScaleFactor)
        $transform.InterpolationMode = [Windows.Graphics.Imaging.BitmapInterpolationMode]::Fant
        $getScaledBitmapMethod = [Windows.Graphics.Imaging.BitmapDecoder].GetMethods() |
            Where-Object { $_.Name -eq 'GetSoftwareBitmapAsync' -and $_.GetParameters().Count -eq 5 } |
            Select-Object -First 1
        if ($null -eq $getScaledBitmapMethod) { throw 'Windows.Media.Ocr scaled bitmap decoding was unavailable.' }
        $getScaledBitmapArgs = New-Object object[] 5
        $getScaledBitmapArgs[0] = [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8
        $getScaledBitmapArgs[1] = [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
        $getScaledBitmapArgs[2] = $transform.PSObject.BaseObject
        $getScaledBitmapArgs[3] = [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation
        $getScaledBitmapArgs[4] = [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
        $bitmap = Invoke-WinRtAsync ($getScaledBitmapMethod.Invoke($decoder, $getScaledBitmapArgs)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    } else {
        $bitmap = Invoke-WinRtAsync ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    }
    $ocrWidth = [int]$bitmap.PixelWidth
    $ocrHeight = [int]$bitmap.PixelHeight
    if ($ocrWidth -lt 1 -or $ocrHeight -lt 1 -or $ocrWidth -gt $maxOcrDimension -or $ocrHeight -gt $maxOcrDimension) {
        Stop-Ocr 'LINE_OCR_IMAGE_TOO_LARGE' "The decoded OCR image exceeds the $maxOcrDimension-pixel OCR engine dimension limit."
    }
    if ($ocrWidth -ne ($ocrInputWidth * $ocrScaleFactor) -or $ocrHeight -ne ($ocrInputHeight * $ocrScaleFactor)) {
        throw 'Windows.Media.Ocr returned an unexpected scaled bitmap size.'
    }
    $recognition = Invoke-WinRtAsync ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = New-Object System.Collections.Generic.List[object]
    foreach ($ocrLine in @($recognition.Lines)) {
        $words = New-Object System.Collections.Generic.List[object]
        foreach ($ocrWord in @($ocrLine.Words)) {
            $rect = $ocrWord.BoundingRect
            $words.Add([ordered]@{
                text = [string]$ocrWord.Text
                x = [double]$rect.X / $ocrScaleFactor - $PaddingPixels
                y = [double]$rect.Y / $ocrScaleFactor - $PaddingPixels
                width = [double]$rect.Width / $ocrScaleFactor
                height = [double]$rect.Height / $ocrScaleFactor
            })
        }
        if ($PaddingPixels -gt 0 -and $words.Count -eq 0) { throw 'Padded OCR returned a line without word geometry.' }
        $bounds = Get-UnionBounds @($words.ToArray())
        $lines.Add([ordered]@{
            text = [string]$ocrLine.Text
            words = @($words.ToArray())
            x = [double]$bounds.x
            y = [double]$bounds.y
            width = [double]$bounds.width
            height = [double]$bounds.height
        })
    }

    Write-OcrResponse ([ordered]@{
        success = $true
        width = $width
        height = $height
        language = $selectedLanguage
        ocrScaleFactor = $ocrScaleFactor
        lines = @($lines.ToArray())
    })
} catch {
    $failureType = $_.Exception.GetType().FullName
    if ($null -ne $_.Exception.InnerException) {
        $failureType = "$($failureType):$($_.Exception.InnerException.GetType().FullName)"
    }
    Stop-Ocr 'LINE_OCR_FAILED' 'Windows.Media.Ocr could not recognize the explicit local PNG input.' @{ failureType = $failureType }
} finally {
    if ($null -ne $bitmap) { try { $bitmap.Dispose() } catch {} }
    if ($null -ne $stream) { try { $stream.Dispose() } catch {} }
    if ($null -ne $paddedInputPath) { Remove-Item -LiteralPath $paddedInputPath -Force -ErrorAction SilentlyContinue }
}
