Param(
  [string]$Python = ".\\.venv\\Scripts\\python.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Python)) {
  throw "未找到 Python：$Python。请先创建虚拟环境并安装依赖（GDAL wheel / numpy / pyinstaller 等）。"
}

& $Python -m pip install -U pip setuptools wheel

# 安装项目运行/打包依赖（安装到指定的虚拟环境）
$gdalWheel = Get-ChildItem -Path "." -Filter "GDAL-*-win_amd64.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $gdalWheel) {
  Write-Host "Installing GDAL wheel: $($gdalWheel.Name)"
  & $Python -m pip install -U ".\\$($gdalWheel.Name)"
} else {
  Write-Host "未在项目根目录找到 GDAL-*-win_amd64.whl（如已通过其它方式安装 GDAL，可忽略）"
}

& $Python -m pip install -U numpy pillow pyproj
& $Python -m pip install -U pyinstaller

Write-Host "Building GeoMosaic (onedir) ..."
& $Python -m PyInstaller --noconfirm --clean ".\\GeoMosaic.spec"
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller 失败，退出码=$LASTEXITCODE"
}

Write-Host ""
Write-Host "完成：dist\\GeoMosaic\\GeoMosaic.exe"

Write-Host ""
Write-Host "Building GeoTiffTool (onedir) ..."
& $Python -m PyInstaller --noconfirm --clean ".\\GeoTiffTool.spec"
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller 失败，退出码=$LASTEXITCODE"
}

Write-Host ""
Write-Host "完成：dist\\GeoTiffTool\\GeoTiffTool.exe"

