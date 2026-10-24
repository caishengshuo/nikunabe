param()

# Windows OCR (Windows.Media.Ocr) persistent worker.
# Protocol: stdin, one line per request.
#   <absolute png path>          -> OCR that file
#   B64 <base64Length>           -> next line is base64 png (no temp file, no file handle)
#   QUIT                         -> exit
# stdout: one JSON object per line.
# This file must stay pure ASCII (PowerShell 5.1 encoding safety).

$ErrorActionPreference = 'Stop'
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding  = [System.Text.Encoding]::UTF8
} catch {}

function Write-Json($obj) {
    [Console]::Out.WriteLine(($obj | ConvertTo-Json -Compress -Depth 6))
    [Console]::Out.Flush()
}

function Convert-OcrResult($res) {
    $items = @()
    foreach ($ln in $res.Lines) {
        $txt = ''
        $x1 = [double]::MaxValue; $y1 = [double]::MaxValue; $x2 = 0.0; $y2 = 0.0
        foreach ($wd in $ln.Words) {
            $txt += $wd.Text
            $r = $wd.BoundingRect
            if ($r.X -lt $x1) { $x1 = $r.X }
            if ($r.Y -lt $y1) { $y1 = $r.Y }
            if (($r.X + $r.Width) -gt $x2) { $x2 = $r.X + $r.Width }
            if (($r.Y + $r.Height) -gt $y2) { $y2 = $r.Y + $r.Height }
        }
        if ($txt -eq '') { continue }
        $items += [pscustomobject]@{
            text = $txt
            x = [math]::Round([double]$x1, 1)
            y = [math]::Round([double]$y1, 1)
            w = [math]::Round([double]($x2 - $x1), 1)
            h = [math]::Round([double]($y2 - $y1), 1)
        }
    }
    return ,@($items)
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

    function Await($op, $type) {
        $m = $asTaskGeneric.MakeGenericMethod($type)
        $t = $m.Invoke($null, @($op))
        $t.Wait(-1) | Out-Null
        return $t.Result
    }

    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.DataWriter, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
    [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

    $langTag = 'ja'
    if ($args.Count -ge 1 -and $args[0]) { $langTag = [string]$args[0] }

    # 已安装的 OCR 语言包（报错时一起返回，方便用户知道该装什么）
    $available = @()
    try {
        $available = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag })
    } catch {}

    # 候选标签：原样 -> 带区域（ja -> ja-JP）-> 去区域（ja-JP -> ja）
    $cands = @($langTag)
    if ($langTag -notmatch '-') { $cands += ($langTag + '-JP') } else { $cands += ($langTag -split '-')[0] }
    # 已装语言里若只有等价标签（如只装了 ja-JP），直接用它
    foreach ($a in $available) {
        if ($a -eq $langTag) { continue }
        if ($a -like ($langTag + '-*') -or $a -like (($langTag -split '-')[0] + '-*')) { $cands += $a }
    }

    $engine = $null
    $usedTag = ''
    foreach ($tag in ($cands | Select-Object -Unique)) {
        if ($null -ne $engine) { break }
        try {
            $lang = New-Object Windows.Globalization.Language($tag)
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
            if ($null -ne $engine) { $usedTag = $tag }
        } catch {}
    }

    if ($null -eq $engine) {
        # 不再回退到「用户配置语言」：用中文/英文包去认日文只会出一堆乱码，比直接报错更难排查
        Write-Json ([pscustomobject]@{
            ok = $false; error = 'no_lang_pack'; requested = $langTag;
            detail = ('Windows OCR 缺少语言包：' + $langTag); available = $available
        })
        exit 0
    }

    Write-Json ([pscustomobject]@{
        ok = $true; ready = $true; lang = $engine.RecognizerLanguage.LanguageTag;
        requested = $langTag; used = $usedTag; available = $available;
        maxDim = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    })

    while ($true) {
        $line = [Console]::In.ReadLine()
        if ($null -eq $line) { break }
        $line = $line.Trim()
        if ($line -eq '') { continue }
        if ($line -eq 'QUIT') { break }

        # ---- 内存传输：base64 PNG，不落盘 ----
        if ($line.StartsWith('B64 ')) {
            $payload = [Console]::In.ReadLine()
            if ($null -eq $payload) { break }
            $stream = $null
            try {
                $bytes = [Convert]::FromBase64String($payload.Trim())
                $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
                $dw = New-Object Windows.Storage.Streams.DataWriter($stream)
                $dw.WriteBytes($bytes)
                Await ($dw.StoreAsync()) ([uint32]) | Out-Null
                $dw.DetachStream() | Out-Null
                $dw.Dispose()
                $stream.Seek(0)
                $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
                $bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
                $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
                $items = Convert-OcrResult $res
                $bmp.Dispose()
                Write-Json ([pscustomobject]@{ ok = $true; lines = $items })
            } catch {
                $msg = $_.Exception.Message
                if ($null -ne $_.Exception.InnerException) {
                    $msg = $_.Exception.GetBaseException().Message
                    if (-not $msg) { $msg = $_.Exception.Message }
                }
                Write-Json ([pscustomobject]@{ ok = $false; error = 'ocr_failed'; detail = $msg })
            } finally {
                if ($null -ne $stream) { try { $stream.Dispose() } catch {} }
            }
            continue
        }

        # ---- 文件传输（兼容旧调用）----
        $stream = $null
        try {
            $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($line)) ([Windows.Storage.StorageFile])
            $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
            $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
            $items = Convert-OcrResult $res
            $bmp.Dispose()
            Write-Json ([pscustomobject]@{ ok = $true; lines = $items })
        } catch {
            $msg = $_.Exception.Message
            if ($null -ne $_.Exception.InnerException) {
                $msg = $_.Exception.GetBaseException().Message
                if (-not $msg) { $msg = $_.Exception.Message }
            }
            Write-Json ([pscustomobject]@{ ok = $false; error = 'ocr_failed'; detail = $msg })
        } finally {
            # 关键：任何分支都必须放掉文件句柄，否则同名文件再也写不进去
            if ($null -ne $stream) { try { $stream.Dispose() } catch {} }
        }
    }
} catch {
    Write-Json ([pscustomobject]@{ ok = $false; error = 'init_failed'; detail = $_.Exception.Message })
    exit 1
}
