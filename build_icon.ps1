Add-Type -AssemblyName System.Drawing
function Add-RoundedRect($graphics,$brush,$x,$y,$w,$h,$r) {
  $p=New-Object System.Drawing.Drawing2D.GraphicsPath
  $d=$r*2
  $p.AddArc($x,$y,$d,$d,180,90); $p.AddArc($x+$w-$d,$y,$d,$d,270,90)
  $p.AddArc($x+$w-$d,$y+$h-$d,$d,$d,0,90); $p.AddArc($x,$y+$h-$d,$d,$d,90,90); $p.CloseFigure()
  $graphics.FillPath($brush,$p); $p.Dispose()
}
function Draw-RoundedRect($graphics,$pen,$x,$y,$w,$h,$r) {
  $p=New-Object System.Drawing.Drawing2D.GraphicsPath
  $d=$r*2
  $p.AddArc($x,$y,$d,$d,180,90); $p.AddArc($x+$w-$d,$y,$d,$d,270,90)
  $p.AddArc($x+$w-$d,$y+$h-$d,$d,$d,0,90); $p.AddArc($x,$y+$h-$d,$d,$d,90,90); $p.CloseFigure()
  $graphics.DrawPath($pen,$p); $p.Dispose()
}
$bmp=New-Object System.Drawing.Bitmap 256,256
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode=[System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::Transparent)
$bg=New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255,13,15,22))
$accent=New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255,177,153,255))
$pen=New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255,118,92,255),8)
Add-RoundedRect $g $bg 18 18 220 220 52
Draw-RoundedRect $g $pen 18 18 220 220 52
$points=[System.Drawing.Point[]]@(
  (New-Object System.Drawing.Point(72,170)),(New-Object System.Drawing.Point(111,75)),(New-Object System.Drawing.Point(139,75)),
  (New-Object System.Drawing.Point(184,170)),(New-Object System.Drawing.Point(153,170)),(New-Object System.Drawing.Point(145,147)),
  (New-Object System.Drawing.Point(108,147)),(New-Object System.Drawing.Point(100,170)))
$p=New-Object System.Drawing.Drawing2D.GraphicsPath; $p.AddPolygon($points); $g.FillPath($accent,$p); $p.Dispose()
$g.Dispose(); $bmp.Save("icon.png",[System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
$png=[IO.File]::ReadAllBytes("icon.png")
$fs=[IO.File]::Open("icon.ico",[IO.FileMode]::Create); $bw=New-Object IO.BinaryWriter($fs)
$bw.Write([UInt16]0);$bw.Write([UInt16]1);$bw.Write([UInt16]1)
$bw.Write([Byte]0);$bw.Write([Byte]0);$bw.Write([Byte]0);$bw.Write([Byte]0);$bw.Write([UInt16]1);$bw.Write([UInt16]32);$bw.Write([UInt32]$png.Length);$bw.Write([UInt32]22);$bw.Write($png)
$bw.Close();$fs.Close()
