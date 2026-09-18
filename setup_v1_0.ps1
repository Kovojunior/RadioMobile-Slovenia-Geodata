#requires -Version 5.1
<#
.SYNOPSIS
  Priprava okolja in vhodnih podatkov za Radio Mobile Slovenija V1.0 (LCV + DMR1/BIL).

.DESCRIPTION
  Skripta je namenjena novemu Windows 10/11 računalniku. Privzeto vse
  programsko okolje, prenose in geografske podatke shrani v podmapo
  ``workspace`` ob tej skripti. Ne zahteva C:\Radio_Mobile in ne zahteva
  skrbniških pravic. Z možnostjo -Root je mogoče delovno mapo preusmeriti
  drugam. Namesti lokalni Miniforge, izdela izolirano okolje Python +
  GDAL + NumPy, pripravi imenike in prenese oziroma pripravi vhodne podatke
  za klasifikacijo LCV. Namesti tudi skripto za izdelavo višinskega modela BIL
  iz javne storitve GURS DMR1; DMR1 se zaradi velike količine podatkov pridobi
  šele ob zagonu namenskega zaganjalnika.

  Samodejni uradni prenosi:
    - MKGP RABA 2026 (arhiv RKG)
    - DRSV Hidrografija - ploskovne površinske vode
    - ZGS sestoji prek uradnega WFS (GGO 01..14)
    - meja Slovenije prek javnega GURS RPE WFS; iz nje izdela majhno masko
      z NoData zunaj Slovenije, ki jo V1.0 uporabi namesto velikega DMV samo
      za določitev slovenskega območja.

  GURS DTM Pokritost tal in GURS DTM Stavbe sta v JGP ponujena prek dinamičnega
  prenosa. Če ne podate neposrednega URL-ja, skripta odpre uradni JGP in počaka,
  da preneseni ZIP datoteki odložite v pripravljena imenika. Nato ju sama
  razširi, najde pravilen SHP in ga postavi na poti, ki jih pričakuje V1.0.

  Originalni Radio Mobile LCV se obravnava podobno: če ni podan URL do arhiva,
  se odpre uradna stran VE2DBE. Prenesite Landcover za 13..17 E / 45..47 N in
  ZIP(e) odložite v pripravljeni imenik; skripta nato preveri vseh 8 tile-ov.

  Skripta je ponovljiva: že veljavne datoteke preskoči. -Force prisili
  ponovno pridobitev/pripravo, kjer je to smiselno.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup_v1_0.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup_v1_0.ps1 -Force

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup_v1_0.ps1 `
    -GursPokritostUrl "https://...zip" `
    -GursBuildingsUrl "https://...zip" `
    -OriginalLcvUrl "https://...zip"
#>

[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$GursPokritostUrl = "",
    [string]$GursBuildingsUrl = "",
    [string]$OriginalLcvUrl = "",
    [switch]$Force,
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Version = "1.0"
$PythonVersion = "3.12"
$GdalVersion = "3.13.2"
$NumpyVersion = "2.4.6"
$RepoDir = $PSScriptRoot

# Privzeto je celoten delovni prostor v eni podmapi GitHub repozitorija.
# -Root ostaja na voljo za uporabnike, ki želijo podatke na drugem disku.
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $RepoDir "workspace"
} else {
    $Root = [IO.Path]::GetFullPath($Root)
}

$GeoRoot = Join-Path $Root "Geodata"
$LandcoverRoot = Join-Path $GeoRoot "Landcover"
$DownloadRoot = Join-Path $Root "_PRENOS_V1_0"
$ToolsRoot = Join-Path $Root "Tools"
$MiniforgeDir = Join-Path $ToolsRoot "Miniforge3"
$CondaExe = Join-Path $MiniforgeDir "Scripts\conda.exe"
$EnvDir = Join-Path $ToolsRoot "lcv-v1.0-env"
$EnvPython = Join-Path $EnvDir "python.exe"
$LogFile = Join-Path $Root "setup_v1_0.log"

$RabaDir = Join-Path $GeoRoot "RABA"
$ZgsDir = Join-Path $GeoRoot "ZGS\SESTOJI_WFS"
$ZgsFinal = Join-Path $ZgsDir "sestoji_slovenija.gpkg"
$HydroDir = Join-Path $GeoRoot "Hidrografija"
$HydroShp = Join-Path $HydroDir "HIDRO5_OBM_PV.shp"
$OriginalDir = Join-Path $LandcoverRoot "Original"

# Višinski model DMR1/BIL. Podatki se ne prenašajo med setupom; glavna DMR1
# skripta jih ob svojem zagonu bere neposredno iz javnega GURS ImageServerja.
$Dmr1Root = Join-Path $GeoRoot "DMR1"
$Dmr1Output = Join-Path $Dmr1Root "OUTPUT"
$LcvOutput = Join-Path $LandcoverRoot "GURS\V1_0"

# Poti so namenoma skladne z izdajo V1.0.
$GursPokritostShp = Join-Path $LandcoverRoot "DTM\DTM_SLO_POKRITOST_TAL_LC_POKRITOSTTAL_P_poligon.shp"
$GursBuildingsDir = Join-Path $LandcoverRoot "Buildings\STAVBE\DTM_SLO_ZGRADBE_BU_STAVBE_P_20260822"
$GursBuildingsShp = Join-Path $GursBuildingsDir "DTM_SLO_ZGRADBE_BU_STAVBE_P_poligon.shp"

# V1.0 potrebuje samo binarno masko slovenskega območja z NoData zunaj države.
# Zaradi tega na novem računalniku ni treba prenesti ~16 GB DMV5 zgolj za masko.
# Maska se izdela neposredno na 1" mreži, poravnani z izhodnimi LCV vozlišči.
$MaskDir = Join-Path $GeoRoot "DMV5\OUTPUT\RADIO_MOBILE\0p25"
$MaskTif = Join-Path $MaskDir "Slovenija_maska_1arcsec.tif"
$MaskVrt = Join-Path $MaskDir "Slovenija_DMV_0p25_TILED.vrt"
$BoundaryGpkg = Join-Path $MaskDir "slovenija_meja.gpkg"

$MainScriptRepo = Join-Path $RepoDir "build_lcv_slovenia_v1_0.py"
$Launcher = Join-Path $RepoDir "run_lcv_v1_0.cmd"

$DmrScriptRepo = Join-Path $RepoDir "dmr1_to_radiomobile_v1_0.py"
$DmrLauncher = Join-Path $RepoDir "run_dmr1_v1_0.cmd"
$DmrService = "https://geohub.gov.si/image/rest/services/TEMELJNI_RASTRI/DMR1/ImageServer/exportImage"

$RabaUrl = "https://rkg.gov.si/arhiv/RABA_Verzije/Raba_2026.zip"
$HydroUrl = "http://www.statika.evode.gov.si/fileadmin/vodkat/DRSV_HIDRO5_OBM_PV.zip"
$ZgsWfs = "https://prostor.zgs.gov.si/geoserver/wfs"
$GursJgp = "https://ipi.eprostor.gov.si/jgp/data"
$GursRpeWfs = "https://ipi.eprostor.gov.si/wfs-si-gurs-rpe/wfs?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"
$RmGeodata = "https://www.ve2dbe.com/geodata/"
$MiniforgeUrl = "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe"

$ExpectedTiles = @(
    "N45E013.lcv", "N45E014.lcv", "N45E015.lcv", "N45E016.lcv",
    "N46E013.lcv", "N46E014.lcv", "N46E015.lcv", "N46E016.lcv"
)

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host ("=" * 78) -ForegroundColor DarkCyan
    Write-Host $Text -ForegroundColor Cyan
    Write-Host ("=" * 78) -ForegroundColor DarkCyan
    Add-Content -LiteralPath $LogFile -Value ("`r`n=== " + $Text + " ===") -Encoding UTF8
}

function Write-Info([string]$Text) {
    Write-Host $Text
    Add-Content -LiteralPath $LogFile -Value $Text -Encoding UTF8
}

function Ensure-Dir([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Download-File([string]$Url, [string]$Destination) {
    Ensure-Dir (Split-Path -Parent $Destination)
    if ((Test-Path -LiteralPath $Destination) -and -not $Force) {
        $len = (Get-Item -LiteralPath $Destination).Length
        if ($len -gt 0) {
            Write-Info "SKIP prenos: $Destination ($len B)"
            return
        }
    }
    if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force }
    Write-Info "Prenos: $Url"
    Write-Info "    -> $Destination"

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($null -ne $curl) {
        & curl.exe -L --fail --retry 5 --retry-delay 3 --connect-timeout 30 -o $Destination $Url
        if ($LASTEXITCODE -ne 0) { throw "curl prenos ni uspel: $Url" }
    } else {
        Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
    }

    if (-not (Test-Path -LiteralPath $Destination)) { throw "Prenos ni ustvaril datoteke: $Destination" }
    if ((Get-Item -LiteralPath $Destination).Length -le 0) { throw "Prenesena datoteka je prazna: $Destination" }
}

function Expand-Zip([string]$Zip, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Zip)) { throw "ZIP ne obstaja: $Zip" }
    Ensure-Dir $Destination
    Write-Info "Razširjam: $Zip"
    Expand-Archive -LiteralPath $Zip -DestinationPath $Destination -Force
}

function Copy-ShapefileFamily([string]$SourceShp, [string]$DestinationShp) {
    Ensure-Dir (Split-Path -Parent $DestinationShp)
    $srcDir = Split-Path -Parent $SourceShp
    $srcStem = [IO.Path]::GetFileNameWithoutExtension($SourceShp)
    $dstDir = Split-Path -Parent $DestinationShp
    $dstStem = [IO.Path]::GetFileNameWithoutExtension($DestinationShp)
    $members = @(Get-ChildItem -LiteralPath $srcDir -File | Where-Object { $_.BaseName -eq $srcStem })
    if ($members.Count -eq 0) { throw "Ni SHP družine za: $SourceShp" }
    foreach ($f in $members) {
        $dest = Join-Path $dstDir ($dstStem + $f.Extension)
        Copy-Item -LiteralPath $f.FullName -Destination $dest -Force
    }
}

function Find-Shp([string]$RootDir, [string]$FileName) {
    $x = Get-ChildItem -LiteralPath $RootDir -Recurse -File -Filter $FileName -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $x) { return $null }
    return $x.FullName
}

function Get-Archives([string]$Dir) {
    if (-not (Test-Path -LiteralPath $Dir)) { return @() }
    return @(Get-ChildItem -LiteralPath $Dir -File -Filter "*.zip" -ErrorAction SilentlyContinue)
}

function Wait-ManualDownload([string]$StageDir, [string]$Title, [string]$Url, [string[]]$Instructions) {
    Ensure-Dir $StageDir
    if ((Get-Archives $StageDir).Count -gt 0) { return }
    if ($NonInteractive) {
        throw "$Title manjka. Prenos je potreben v $StageDir."
    }
    Write-Host ""
    Write-Host $Title -ForegroundColor Yellow
    foreach ($line in $Instructions) { Write-Host "  $line" -ForegroundColor Yellow }
    Write-Host "  Ciljni imenik: $StageDir" -ForegroundColor Yellow
    Start-Process $Url | Out-Null
    [void](Read-Host "Ko je ZIP v ciljnem imeniku, pritisni ENTER")
    if ((Get-Archives $StageDir).Count -eq 0) {
        throw "V $StageDir ni ZIP datoteke."
    }
}

function Run-Python([string]$ScriptPath, [string[]]$Arguments = @()) {
    if (-not (Test-Path -LiteralPath $EnvPython)) { throw "Python okolje ne obstaja: $EnvPython" }
    & $EnvPython $ScriptPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python helper ni uspel: $ScriptPath" }
}

function Find-RadioMobile {
    # Radio Mobile ni pogoj za izdelavo podatkov. Preverjanje je samo informativno.
    $cmdNames = @("rmw.exe", "RadioMobile.exe", "Radio Mobile Deluxe.exe")
    foreach ($name in $cmdNames) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $cmd) { return $cmd.Source }
    }

    if (-not [string]::IsNullOrWhiteSpace($env:RADIO_MOBILE_HOME)) {
        if (Test-Path -LiteralPath $env:RADIO_MOBILE_HOME) { return $env:RADIO_MOBILE_HOME }
    }

    $candidateDirs = @()
    if ($env:ProgramFiles) {
        $candidateDirs += (Join-Path $env:ProgramFiles "Radio Mobile")
        $candidateDirs += (Join-Path $env:ProgramFiles "Radio Mobile Deluxe")
    }
    if (${env:ProgramFiles(x86)}) {
        $candidateDirs += (Join-Path ${env:ProgramFiles(x86)} "Radio Mobile")
        $candidateDirs += (Join-Path ${env:ProgramFiles(x86)} "Radio Mobile Deluxe")
    }
    $candidateDirs += "C:\Radio_Mobile"
    foreach ($d in $candidateDirs) {
        if (-not $d -or -not (Test-Path -LiteralPath $d)) { continue }
        foreach ($name in $cmdNames) {
            $candidateExe = Join-Path $d $name
            if (Test-Path -LiteralPath $candidateExe) { return $candidateExe }
        }
    }

    $uninstallKeys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($key in $uninstallKeys) {
        try {
            $hit = Get-ItemProperty $key -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -like "*Radio Mobile*" } |
                Select-Object -First 1
            if ($null -ne $hit) {
                if ($hit.InstallLocation -and (Test-Path -LiteralPath $hit.InstallLocation)) {
                    return $hit.InstallLocation
                }
                return $hit.DisplayName
            }
        } catch { }
    }
    return $null
}

Ensure-Dir $Root
Set-Content -LiteralPath $LogFile -Value ("Radio Mobile Slovenija V1.0 setup (LCV + DMR1/BIL) - " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -Encoding UTF8

Write-Step "1/8 - Imeniki"
@(
    $GeoRoot, $LandcoverRoot, $DownloadRoot, $ToolsRoot, $RabaDir, $ZgsDir,
    $HydroDir, $OriginalDir, (Split-Path -Parent $GursPokritostShp),
    $GursBuildingsDir, $MaskDir, $LcvOutput, $Dmr1Root, $Dmr1Output
) | ForEach-Object { Ensure-Dir $_ }
Write-Info "Delovni prostor: $Root"
Write-Info "Repozitorij: $RepoDir"

Write-Step "2/8 - Miniforge + izolirano okolje Python/GDAL"
if (-not (Test-Path -LiteralPath $CondaExe)) {
    $installer = Join-Path $DownloadRoot "Miniforge3-Windows-x86_64.exe"
    Download-File $MiniforgeUrl $installer
    Write-Info "Nameščam Miniforge: $MiniforgeDir"
    & $installer /InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=$MiniforgeDir
    if ($LASTEXITCODE -ne 0) { throw "Miniforge namestitev ni uspela." }
}
if (-not (Test-Path -LiteralPath $CondaExe)) { throw "conda.exe ni najden: $CondaExe" }

if ((-not (Test-Path -LiteralPath $EnvPython)) -or $Force) {
    if ($Force -and (Test-Path -LiteralPath $EnvDir)) {
        Write-Info "Odstranjujem obstoječe okolje zaradi -Force ..."
        & $CondaExe env remove -p $EnvDir -y
    }
    Write-Info "Ustvarjam okolje $EnvDir"
    & $CondaExe create -p $EnvDir -y -c conda-forge "python=$PythonVersion" "gdal=$GdalVersion" "numpy=$NumpyVersion"
    if ($LASTEXITCODE -ne 0) { throw "Conda okolje ni bilo ustvarjeno." }
}
& $EnvPython -c "from osgeo import gdal, ogr; import numpy as np; print('GDAL', gdal.VersionInfo('--version')); print('NumPy', np.__version__)"
if ($LASTEXITCODE -ne 0) { throw "Preverjanje GDAL/NumPy ni uspelo." }

Write-Step "3/8 - MKGP RABA"
$rabaStage = Join-Path $DownloadRoot "MKGP_RABA"
Ensure-Dir $rabaStage
$rabaZip = Join-Path $rabaStage "Raba_2026.zip"
$rabaExisting = @(Get-ChildItem -LiteralPath $RabaDir -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension.ToLowerInvariant() -in @(".shp", ".gpkg") } | Select-Object -First 1)
if ($Force -or $rabaExisting.Count -eq 0) {
    try {
        Download-File $RabaUrl $rabaZip
    } catch {
        if ($NonInteractive) { throw }
        Write-Host "Samodejni prenos RABA ni uspel: $($_.Exception.Message)" -ForegroundColor Yellow
        Wait-ManualDownload $rabaStage "MKGP RABA 2026" "https://rkg.gov.si/arhiv/RABA_Verzije/index.html" @(
            "Prenesi Raba_2026.zip z uradnega arhiva RKG.",
            "ZIP shrani neposredno v spodnji ciljni imenik."
        )
        $manual = Get-ChildItem -LiteralPath $rabaStage -File -Filter "*Raba*2026*.zip" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $manual) { throw "Ne najdem ročno prenesenega RABA ZIP-a." }
        $rabaZip = $manual.FullName
    }
    Expand-Zip $rabaZip $RabaDir
} else {
    Write-Info "RABA že obstaja -> SKIP"
}

Write-Step "4/8 - DRSV Hidrografija"
$hydroStage = Join-Path $DownloadRoot "DRSV_HIDROGRAFIJA"
Ensure-Dir $hydroStage
$hydroZip = Join-Path $hydroStage "DRSV_HIDRO5_OBM_PV.zip"
if ($Force -or -not (Test-Path -LiteralPath $HydroShp)) {
    try {
        Download-File $HydroUrl $hydroZip
    } catch {
        if ($NonInteractive) { throw }
        Write-Host "Samodejni prenos DRSV ni uspel: $($_.Exception.Message)" -ForegroundColor Yellow
        Wait-ManualDownload $hydroStage "DRSV Hidrografija - ploskovne površinske vode" "https://podatki.gov.si/" @(
            "Prenesi uradni paket DRSV_HIDRO5_OBM_PV.zip.",
            "ZIP shrani neposredno v spodnji ciljni imenik."
        )
        $manual = Get-ChildItem -LiteralPath $hydroStage -File -Filter "*.zip" | Select-Object -First 1
        if ($null -eq $manual) { throw "Ne najdem ročno prenesenega DRSV ZIP-a." }
        $hydroZip = $manual.FullName
    }
    Expand-Zip $hydroZip $HydroDir
    if (-not (Test-Path -LiteralPath $HydroShp)) {
        $foundHydro = Find-Shp $HydroDir "HIDRO5_OBM_PV.shp"
        if ($null -eq $foundHydro) { throw "Po prenosu ne najdem HIDRO5_OBM_PV.shp" }
        Copy-ShapefileFamily $foundHydro $HydroShp
    }
} else {
    Write-Info "Hidrografija že obstaja -> SKIP"
}

Write-Step "5/8 - ZGS sestoji"
$zgsHelper = Join-Path $DownloadRoot "_download_zgs_v1_0.py"
$zgsCode = @'
from __future__ import annotations
import sys, time
from pathlib import Path
from osgeo import gdal, ogr

gdal.UseExceptions(); ogr.UseExceptions()
WFS = "https://prostor.zgs.gov.si/geoserver/wfs"
out = Path(sys.argv[1])
force = (len(sys.argv) > 2 and sys.argv[2] == "--force")
out.mkdir(parents=True, exist_ok=True)

gdal.SetConfigOption("OGR_WFS_PAGING_ALLOWED", "YES")
gdal.SetConfigOption("OGR_WFS_PAGE_SIZE", "5000")
gdal.SetConfigOption("OGR_WFS_LOAD_MULTIPLE_LAYER_DEFN", "NO")
gdal.SetConfigOption("CPL_HTTP_CONNECTTIMEOUT", "30")
gdal.SetConfigOption("CPL_HTTP_TIMEOUT", "300")
gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "5")
gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "3")

def count(path):
    ds = ogr.Open(str(path), 0)
    if ds is None: return -1
    lyr = ds.GetLayerByName("sestoji")
    if lyr is None:
        lyr = ds.GetLayer(0)
    n = int(lyr.GetFeatureCount()) if lyr is not None else -1
    ds = None
    return n

def discover():
    ds = gdal.OpenEx("WFS:" + WFS, gdal.OF_VECTOR | gdal.OF_READONLY)
    if ds is None: raise RuntimeError("ZGS WFS ni dosegljiv")
    matches=[]
    for i in range(ds.GetLayerCount()):
        lyr=ds.GetLayer(i)
        if lyr is not None and lyr.GetName().split(":")[-1].lower()=="sestoji":
            matches.append(lyr.GetName())
    ds=None
    if not matches: raise RuntimeError("Na ZGS WFS ni sloja sestoji")
    return matches[0]

layer = discover()
print("ZGS sloj:", layer, flush=True)
parts=[]
for i in range(1,15):
    ggo=f"{i:02d}"; dst=out/f"ggo_{ggo}.gpkg"
    if dst.exists() and not force and count(dst)>0:
        print(f"GGO {ggo}: SKIP ({count(dst):,})", flush=True); parts.append(dst); continue
    if dst.exists(): dst.unlink()
    print(f"GGO {ggo}: prenos ...", flush=True)
    src=gdal.OpenEx("WFS:"+WFS, gdal.OF_VECTOR|gdal.OF_READONLY)
    opts=gdal.VectorTranslateOptions(format="GPKG", layers=[layer], where=f"ggo = '{ggo}'", layerName="sestoji", accessMode="overwrite", layerCreationOptions=["SPATIAL_INDEX=YES"])
    r=gdal.VectorTranslate(str(dst), src, options=opts); src=None; r=None
    n=count(dst)
    if n<=0: raise RuntimeError(f"GGO {ggo}: prenos je prazen")
    print(f"GGO {ggo}: OK ({n:,})", flush=True); parts.append(dst)

final=out/"sestoji_slovenija.gpkg"
if final.exists(): final.unlink()
expected=0
for idx,p in enumerate(parts):
    n=count(p); expected += n
    src=gdal.OpenEx(str(p), gdal.OF_VECTOR|gdal.OF_READONLY)
    if idx == 0:
        opts=gdal.VectorTranslateOptions(
            format="GPKG", layers=["sestoji"], layerName="sestoji",
            accessMode="overwrite", layerCreationOptions=["SPATIAL_INDEX=YES"]
        )
    else:
        opts=gdal.VectorTranslateOptions(
            format="GPKG", layers=["sestoji"], layerName="sestoji",
            accessMode="append"
        )
    r=gdal.VectorTranslate(str(final), src, options=opts); src=None; r=None
actual=count(final)
if actual != expected: raise RuntimeError(f"ZGS merge QA FAIL: {actual}/{expected}")
print(f"ZGS merge QA PASS: {actual:,} sestojev -> {final}", flush=True)
'@
Set-Content -LiteralPath $zgsHelper -Value $zgsCode -Encoding UTF8
if ($Force -or -not (Test-Path -LiteralPath $ZgsFinal)) {
    $zgsArgs = @($ZgsDir)
    if ($Force) { $zgsArgs += "--force" }
    Run-Python $zgsHelper $zgsArgs
} else {
    Write-Info "ZGS nacionalni GPKG že obstaja -> SKIP"
}

Write-Step "6/8 - GURS Pokritost tal in Stavbe"
$gursPokStage = Join-Path $DownloadRoot "GURS_POKRITOST_TAL"
$gursBldStage = Join-Path $DownloadRoot "GURS_STAVBE"
Ensure-Dir $gursPokStage; Ensure-Dir $gursBldStage

if ($Force -or -not (Test-Path -LiteralPath $GursPokritostShp)) {
    if ($GursPokritostUrl.Trim()) {
        $zip = Join-Path $gursPokStage "gurs_pokritost_tal.zip"
        Download-File $GursPokritostUrl $zip
    }
    Wait-ManualDownload $gursPokStage "GURS DTM - Pokritost tal" $GursJgp @(
        "V JGP izberi Državni topografski sistem / Zbirka topografskih podatkov (DTM).",
        "Prenesi paket, ki vsebuje ploskovni sloj POKRITOST TAL.",
        "ZIP shrani neposredno v spodnji ciljni imenik."
    )
    $extract = Join-Path $gursPokStage "_EXTRACTED"
    Ensure-Dir $extract
    foreach ($z in (Get-Archives $gursPokStage)) { Expand-Zip $z.FullName $extract }
    $shp = Find-Shp $extract "DTM_SLO_POKRITOST_TAL_LC_POKRITOSTTAL_P_poligon.shp"
    if ($null -eq $shp) { throw "V GURS paketu ne najdem DTM_SLO_POKRITOST_TAL_LC_POKRITOSTTAL_P_poligon.shp" }
    Copy-ShapefileFamily $shp $GursPokritostShp
} else { Write-Info "GURS Pokritost tal že obstaja -> SKIP" }

if ($Force -or -not (Test-Path -LiteralPath $GursBuildingsShp)) {
    if ($GursBuildingsUrl.Trim()) {
        $zip = Join-Path $gursBldStage "gurs_stavbe.zip"
        Download-File $GursBuildingsUrl $zip
    }
    Wait-ManualDownload $gursBldStage "GURS DTM - Stavbe" $GursJgp @(
        "V JGP izberi Državni topografski sistem / Zbirka topografskih podatkov (DTM).",
        "Prenesi paket ZGRADBE, ki vsebuje ploskovni sloj STAVBA.",
        "ZIP shrani neposredno v spodnji ciljni imenik."
    )
    $extract = Join-Path $gursBldStage "_EXTRACTED"
    Ensure-Dir $extract
    foreach ($z in (Get-Archives $gursBldStage)) { Expand-Zip $z.FullName $extract }
    $shp = Find-Shp $extract "DTM_SLO_ZGRADBE_BU_STAVBE_P_poligon.shp"
    if ($null -eq $shp) { throw "V GURS paketu ne najdem DTM_SLO_ZGRADBE_BU_STAVBE_P_poligon.shp" }
    Copy-ShapefileFamily $shp $GursBuildingsShp
} else { Write-Info "GURS Stavbe že obstajajo -> SKIP" }

Write-Step "7/8 - Maska Slovenije in originalni Radio Mobile LCV"
$maskHelper = Join-Path $DownloadRoot "_build_slovenia_mask_v1_0.py"
$maskCode = @'
from __future__ import annotations
import sys, math
from pathlib import Path
from osgeo import gdal, ogr, osr

gdal.UseExceptions(); ogr.UseExceptions()
out_gpkg=Path(sys.argv[1]); out_tif=Path(sys.argv[2]); out_vrt=Path(sys.argv[3]); raba=Path(sys.argv[4])
wfs="https://ipi.eprostor.gov.si/wfs-si-gurs-rpe/wfs?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"
out_gpkg.parent.mkdir(parents=True, exist_ok=True)

def polygon_candidate(ds):
    cand=[]
    for i in range(ds.GetLayerCount()):
        lyr=ds.GetLayer(i)
        if lyr is None: continue
        name=lyr.GetName(); up=name.upper()
        gt=ogr.wkbFlatten(lyr.GetLayerDefn().GetGeomType())
        if gt not in (ogr.wkbPolygon, ogr.wkbMultiPolygon): continue
        if "DRZAV" not in up: continue
        try: n=int(lyr.GetFeatureCount())
        except: n=999999
        score=(0 if up.endswith("DRZAVE") or up.endswith("DRZAVA") else 1, n)
        cand.append((score,name))
    return sorted(cand)[0][1] if cand else None

layer_name=None
try:
    ds=gdal.OpenEx("WFS:"+wfs, gdal.OF_VECTOR|gdal.OF_READONLY)
    if ds is not None: layer_name=polygon_candidate(ds)
    if ds is not None and layer_name:
        if out_gpkg.exists(): out_gpkg.unlink()
        # Celotni sloj države; v RPE je to nacionalni poligon Slovenije.
        opts=gdal.VectorTranslateOptions(format="GPKG", layers=[layer_name], layerName="slovenia", dstSRS="EPSG:4326", geometryType="CONVERT_TO_LINEAR", layerCreationOptions=["SPATIAL_INDEX=YES"])
        r=gdal.VectorTranslate(str(out_gpkg), ds, options=opts); r=None
    ds=None
    # Državni sloj mora predstavljati eno nacionalno geometrijo. Če je rezultat
    # drugačen, je varneje uporabiti RABA kot rezervni vir za masko.
    if out_gpkg.exists():
        check=ogr.Open(str(out_gpkg), 0)
        lyr=check.GetLayerByName("slovenia") if check is not None else None
        n=int(lyr.GetFeatureCount()) if lyr is not None else 0
        check=None
        if n != 1:
            print(f"RPE kandidat ima {n} objektov; fallback na RABA masko.", flush=True)
            out_gpkg.unlink(missing_ok=True)
except Exception as e:
    print("GURS RPE WFS ni uspel, fallback na RABA masko:", e, flush=True)
    layer_name=None

if not out_gpkg.exists():
    # Rezervna pot: RABA pokriva slovensko ozemlje. Poiščemo sloj z RABA_ID in
    # ga prepišemo v enoten WGS84 vir za rasterizacijo maske.
    candidates=[]
    for p in raba.rglob("*"):
        if p.suffix.lower() not in {".shp",".gpkg",".sqlite",".geojson"}: continue
        try:
            ds=gdal.OpenEx(str(p), gdal.OF_VECTOR|gdal.OF_READONLY)
            if ds is None: continue
            for i in range(ds.GetLayerCount()):
                lyr=ds.GetLayer(i); fields=[lyr.GetLayerDefn().GetFieldDefn(j).GetName().lower() for j in range(lyr.GetLayerDefn().GetFieldCount())]
                if "raba_id" in fields: candidates.append((int(lyr.GetFeatureCount()),p,lyr.GetName()))
            ds=None
        except: pass
    if not candidates: raise RuntimeError("Ne najdem GURS RPE meje niti RABA vira za masko")
    _,p,ln=max(candidates,key=lambda x:x[0])
    if out_gpkg.exists(): out_gpkg.unlink()
    opts=gdal.VectorTranslateOptions(format="GPKG", layers=[ln], layerName="slovenia", dstSRS="EPSG:4326", geometryType="CONVERT_TO_LINEAR", layerCreationOptions=["SPATIAL_INDEX=YES"])
    r=gdal.VectorTranslate(str(out_gpkg), str(p), options=opts); r=None

# Maska je izdelana neposredno na isti 1" vozliščni mreži kot končni LCV.
# Središča pikslov so točno na celih ločnih sekundah; robovi so +/- 0.5".
west,south,east,north=13.0,45.0,17.0,47.0
step=1.0/3600.0
width=round((east-west)/step)+1; height=round((north-south)/step)+1
drv=gdal.GetDriverByName("GTiff")
ds=drv.Create(str(out_tif), width, height, 1, gdal.GDT_Byte, options=["TILED=YES","COMPRESS=DEFLATE","PREDICTOR=2"])
srs=osr.SpatialReference(); srs.ImportFromEPSG(4326)
ds.SetProjection(srs.ExportToWkt()); ds.SetGeoTransform((west-step/2,step,0,north+step/2,0,-step))
b=ds.GetRasterBand(1); b.Fill(0); b.SetNoDataValue(0)
r=gdal.Rasterize(ds, str(out_gpkg), options=gdal.RasterizeOptions(layers=["slovenia"], burnValues=[1], allTouched=False))
if r is None: raise RuntimeError("Rasterizacija maske ni uspela")
b.FlushCache(); ds.FlushCache(); ds=None
vrt=gdal.BuildVRT(str(out_vrt), [str(out_tif)])
if vrt is None: raise RuntimeError("Izdelava VRT maske ni uspela")
vrt=None
print(f"Maska: {width}x{height} @ 1\" -> {out_vrt}", flush=True)
'@
Set-Content -LiteralPath $maskHelper -Value $maskCode -Encoding UTF8
if ($Force -or -not (Test-Path -LiteralPath $MaskVrt)) {
    Run-Python $maskHelper @($BoundaryGpkg, $MaskTif, $MaskVrt, $RabaDir)
} else { Write-Info "Maska Slovenije že obstaja -> SKIP" }

$rmStage = Join-Path $DownloadRoot "RM_ORIGINAL_LCV"
Ensure-Dir $rmStage
$missing = @($ExpectedTiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $OriginalDir $_)) })
if ($Force -or $missing.Count -gt 0) {
    if ($OriginalLcvUrl.Trim()) {
        $zip = Join-Path $rmStage "rm_landcover_slovenia.zip"
        Download-File $OriginalLcvUrl $zip
    }
    if ((Get-Archives $rmStage).Count -eq 0) {
        Wait-ManualDownload $rmStage "Originalni Radio Mobile Landcover" $RmGeodata @(
            "Na VE2DBE izberi Landcover za območje Slovenije in neposredno okolico.",
            "Uporabi okvir: min lon 13, max lon 17, min lat 45, max lat 47.",
            "Za izvorni nadomestni vir zadostuje legacy 3\" LCV; V1.0 ga sama poravna na 1\".",
            "Vse prenesene ZIP datoteke shrani v spodnji ciljni imenik."
        )
    }
    $rmExtract = Join-Path $rmStage "_EXTRACTED"
    Ensure-Dir $rmExtract
    foreach ($z in (Get-Archives $rmStage)) { Expand-Zip $z.FullName $rmExtract }
    foreach ($tile in $ExpectedTiles) {
        $src = Get-ChildItem -LiteralPath $rmExtract -Recurse -File -Filter $tile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $src) { Copy-Item -LiteralPath $src.FullName -Destination (Join-Path $OriginalDir $tile) -Force }
    }
}
$missing = @($ExpectedTiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $OriginalDir $_)) })
if ($missing.Count -gt 0) { throw "Manjkajo originalni RM LCV tile-i: $($missing -join ', ')" }
Write-Info "Originalni RM LCV: vseh 8 tile-ov prisotnih."

Write-Step "8/8 - Namestitev V1.0 skript, zaganjalnikov in končna kontrola"
if (-not (Test-Path -LiteralPath $MainScriptRepo)) {
    throw "V istem GitHub imeniku manjka build_lcv_slovenia_v1_0.py"
}
if (-not (Test-Path -LiteralPath $DmrScriptRepo)) {
    throw "V istem GitHub imeniku manjka dmr1_to_radiomobile_v1_0.py"
}

# Skript ne podvajamo v podatkovnih mapah. Zaganjalnika v korenu repozitorija
# nastavita delovni prostor, zato je ena kopija kode vedno edina veljavna kopija.
$launcherText = @"
@echo off
setlocal
set "RMSLO_WORKSPACE=$Root"
cd /d "$RepoDir"
"$EnvPython" "$MainScriptRepo" %*
endlocal
"@
Set-Content -LiteralPath $Launcher -Value $launcherText -Encoding ASCII

$dmrLauncherText = @"
@echo off
setlocal
set "RMSLO_WORKSPACE=$Root"
cd /d "$RepoDir"
"$EnvPython" "$DmrScriptRepo" %*
endlocal
"@
Set-Content -LiteralPath $DmrLauncher -Value $dmrLauncherText -Encoding ASCII

$requiredDirs = @(
    $Root, $ToolsRoot, $GeoRoot, $LandcoverRoot, $LcvOutput, $RabaDir, $ZgsDir,
    $HydroDir, $OriginalDir, (Split-Path -Parent $GursPokritostShp),
    $GursBuildingsDir, $MaskDir, $Dmr1Root, $Dmr1Output
)
foreach ($d in $requiredDirs) {
    if (-not (Test-Path -LiteralPath $d -PathType Container)) {
        throw "Končna kontrola imenikov: manjka $d"
    }
}

$required = @(
    $MainScriptRepo, $DmrScriptRepo, $GursPokritostShp, $GursBuildingsShp, $ZgsFinal,
    $HydroShp, $MaskVrt
)
foreach ($p in $required) {
    if (-not (Test-Path -LiteralPath $p)) { throw "Končna kontrola: manjka $p" }
}
Write-Info "Struktura imenikov: QA PASS ($($requiredDirs.Count) zahtevanih imenikov)."

# Preveri sheme vhodnih podatkov, LCV tile-e in masko. Tako se napaka odkrije
# že med pripravo novega računalnika, ne šele po večurnem produkcijskem izračunu.
$validateHelper = Join-Path $DownloadRoot "_validate_inputs_v1_0.py"
$validateCode = @'
from pathlib import Path
import math, sys
from osgeo import gdal, ogr

gdal.UseExceptions(); ogr.UseExceptions()
raba, gurs, bld, zgs, hydro, mask, original = map(Path, sys.argv[1:8])

def find_layer_with_fields(path, required):
    ds=gdal.OpenEx(str(path), gdal.OF_VECTOR|gdal.OF_READONLY)
    if ds is None: raise RuntimeError(f"Ne morem odpreti vektorskega vira: {path}")
    req={x.lower() for x in required}
    for i in range(ds.GetLayerCount()):
        lyr=ds.GetLayer(i)
        if lyr is None: continue
        d=lyr.GetLayerDefn()
        fields={d.GetFieldDefn(j).GetName().lower() for j in range(d.GetFieldCount())}
        if req.issubset(fields):
            name=lyr.GetName(); n=int(lyr.GetFeatureCount()); ds=None
            if n <= 0: raise RuntimeError(f"Prazen sloj {name}: {path}")
            print(f"OK {path.name}: {name}, {n:,} objektov, polja={','.join(sorted(req))}")
            return
    ds=None
    raise RuntimeError(f"V {path} ni sloja z obveznimi polji: {sorted(req)}")

def discover_raba(root):
    for p in root.rglob("*"):
        if p.suffix.lower() not in {".shp",".gpkg",".sqlite",".geojson"}: continue
        try:
            ds=gdal.OpenEx(str(p), gdal.OF_VECTOR|gdal.OF_READONLY)
            if ds is None: continue
            for i in range(ds.GetLayerCount()):
                lyr=ds.GetLayer(i)
                if lyr is None: continue
                d=lyr.GetLayerDefn()
                fields={d.GetFieldDefn(j).GetName().lower() for j in range(d.GetFieldCount())}
                if "raba_id" in fields and int(lyr.GetFeatureCount()) > 0:
                    print(f"OK RABA: {p} / {lyr.GetName()} / {int(lyr.GetFeatureCount()):,} objektov")
                    ds=None; return
            ds=None
        except Exception:
            pass
    raise RuntimeError(f"V {root} ne najdem uporabnega RABA sloja s poljem RABA_ID")

discover_raba(raba)
find_layer_with_fields(gurs, ["vrsta_pok"])
find_layer_with_fields(bld, ["bui_dtm_id","visina","vis_status","stan_konst","hz_ref_geo","ref_geom"])
find_layer_with_fields(zgs, ["rfaza","sklep","povrsina","lzsku"])
find_layer_with_fields(hydro, ["simbol"])

mds=gdal.Open(str(mask))
if mds is None: raise RuntimeError(f"Ne morem odpreti maske: {mask}")
b=mds.GetRasterBand(1); nd=b.GetNoDataValue()
if nd is None: raise RuntimeError("Maska nima NoData vrednosti")
print(f"OK maska: {mds.RasterXSize}x{mds.RasterYSize}, NoData={nd}")
mds=None

expected=[
    "N45E013.lcv","N45E014.lcv","N45E015.lcv","N45E016.lcv",
    "N46E013.lcv","N46E014.lcv","N46E015.lcv","N46E016.lcv",
]
for name in expected:
    p=original/name
    size=p.stat().st_size
    n=math.isqrt(size)
    if n*n != size or n < 2:
        raise RuntimeError(f"{name}: neveljavna kvadratna Byte LCV mreža ({size} B)")
    arc=3600.0/(n-1)
    print(f"OK {name}: {n}x{n} (~{arc:g}\")")
print("VSI VHODNI PODATKI: QA PASS")
'@
Set-Content -LiteralPath $validateHelper -Value $validateCode -Encoding UTF8
Run-Python $validateHelper @($RabaDir, $GursPokritostShp, $GursBuildingsShp, $ZgsFinal, $HydroShp, $MaskVrt, $OriginalDir)

# Preveri, da se glavna skripta vsaj naloži in da CLI obstaja.
& $EnvPython $MainScriptRepo --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "V1.0 LCV skripta se ne naloži pravilno." }
& $EnvPython $DmrScriptRepo --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "V1.0 DMR1/BIL skripta se ne naloži pravilno." }

$RadioMobileLocation = Find-RadioMobile
if ($null -ne $RadioMobileLocation) {
    Write-Host "Radio Mobile Deluxe: ZAZNAN" -ForegroundColor Green
    Write-Host "  $RadioMobileLocation" -ForegroundColor DarkGray
} else {
    Write-Host "Radio Mobile Deluxe: NI ZAZNAN" -ForegroundColor Yellow
    Write-Host "  To ni napaka. LCV in DMR1/BIL se izdelata brez nameščenega Radio Mobile." -ForegroundColor DarkGray
    Write-Host "  Radio Mobile je potreben šele za neposredno uporabo izdelkov pri propagacijskih izračunih." -ForegroundColor DarkGray
}

$state = [ordered]@{
    version = $Version
    created = (Get-Date).ToString("o")
    environment = [ordered]@{
        python = $PythonVersion
        gdal = $GdalVersion
        numpy = $NumpyVersion
    }
    repository = $RepoDir
    workspace = $Root
    python = $EnvPython
    main_script = $MainScriptRepo
    launcher = $Launcher
    dmr1_script = $DmrScriptRepo
    dmr1_launcher = $DmrLauncher
    dmr1_output = $Dmr1Output
    radio_mobile_detected = ($null -ne $RadioMobileLocation)
    radio_mobile_location = $RadioMobileLocation
    sources = [ordered]@{
        raba = $RabaUrl
        zgs_wfs = $ZgsWfs
        drsv_hydro = $HydroUrl
        gurs_jgp = $GursJgp
        gurs_pokritost_direct_url = $GursPokritostUrl
        gurs_buildings_direct_url = $GursBuildingsUrl
        gurs_rpe_wfs = $GursRpeWfs
        rm_geodata = $RmGeodata
        rm_direct_url = $OriginalLcvUrl
        gurs_dmr1_image_service = $DmrService
    }
}
$state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Root "SETUP_STATE_V1_0.json") -Encoding UTF8

Write-Host ""
Write-Host "PRIPRAVA V1.0 JE KONČANA." -ForegroundColor Green
Write-Host "LCV zaganjalnik:" -ForegroundColor Green
Write-Host "  $Launcher --workers 8 --kernel-workers 8 --preview --qa" -ForegroundColor White
Write-Host ""
Write-Host 'DMR1/BIL zaganjalnik (celotna Slovenija, privzeto 1/9", razrez 4x4):' -ForegroundColor Green
Write-Host "  $DmrLauncher --all-tiles --stage all" -ForegroundColor White
Write-Host ""
Write-Host "DMR1 se ne prenaša med setupom; prenese/prevzorči se iz GURS šele ob zgornjem zagonu." -ForegroundColor DarkGray
Write-Host ""
Write-Host "Neposredno s Python okoljem - LCV:" -ForegroundColor Green
Write-Host "  set RMSLO_WORKSPACE=$Root && cd /d $RepoDir && `"$EnvPython`" build_lcv_slovenia_v1_0.py --workers 8 --kernel-workers 8 --preview --qa" -ForegroundColor White
Write-Host "Neposredno s Python okoljem - DMR1/BIL:" -ForegroundColor Green
Write-Host "  set RMSLO_WORKSPACE=$Root && cd /d $RepoDir && `"$EnvPython`" dmr1_to_radiomobile_v1_0.py --all-tiles --stage all" -ForegroundColor White
Write-Host ""
Write-Host "Delovni prostor: $Root"
Write-Host "Dnevnik: $LogFile"
