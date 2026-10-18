$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

# Use the artwork uploaded by the user (base64 keeps the PNG source in Git).
$encoded = (Get-Content "assets/icon.png.b64" -Raw).Trim()
$png = [Convert]::FromBase64String($encoded)
[IO.File]::WriteAllBytes((Join-Path $PWD "icon.png"), $png)

# Validate the payload before embedding it in a Windows ICO container.
$stream = New-Object System.IO.MemoryStream(,$png)
try {
    $image = [System.Drawing.Image]::FromStream($stream)
    $width = [int]$image.Width
    $height = [int]$image.Height
    if ($width -ne $height -or $width -lt 64 -or $width -gt 256) {
        throw "Icon artwork must be a square PNG between 64 and 256 pixels."
    }
    $image.Dispose()
}
finally {
    $stream.Dispose()
}

# ICO entry with a PNG payload, supported by modern Windows.
$fs = [IO.File]::Open("icon.ico", [IO.FileMode]::Create)
$bw = New-Object IO.BinaryWriter($fs)
try {
    $bw.Write([UInt16]0)
    $bw.Write([UInt16]1)
    $bw.Write([UInt16]1)
    $bw.Write([Byte]$width)
    $bw.Write([Byte]$height)
    $bw.Write([Byte]0)
    $bw.Write([Byte]0)
    $bw.Write([UInt16]1)
    $bw.Write([UInt16]32)
    $bw.Write([UInt32]$png.Length)
    $bw.Write([UInt32]22)
    $bw.Write($png)
}
finally {
    $bw.Dispose()
    $fs.Dispose()
}
