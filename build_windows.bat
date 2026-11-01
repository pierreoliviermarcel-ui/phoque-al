@echo off
rem Builds dist\PhoqueAl (PyInstaller) and dist\PhoqueAl_Setup.exe (Inno Setup).
rem Needs: Python 3.11+, VLC (64-bit) and FFmpeg installed on the build PC.
setlocal
cd /d "%~dp0"
set APP=dist\PhoqueAl
if not exist ".venv\Scripts\python.exe" python -m venv .venv || goto fail
set PY=.venv\Scripts\python.exe
"%PY%" -m pip install -q --disable-pip-version-check -r requirements.txt pyinstaller reportlab || goto fail

echo [1/4] User guide (PDF)
pushd docs
"..\%PY%" notice_source.py
popd

echo [2/4] PyInstaller
if exist dist rmdir /s /q dist
"%PY%" -m PyInstaller --noconfirm --clean --windowed --name PhoqueAl --distpath dist --workpath build --specpath build --collect-submodules phoqueal --collect-all onnxruntime run_phoqueal.py || goto fail

echo [3/4] Bundled tools and their licenses
mkdir "%APP%\tools" "%APP%\licenses" "%APP%\data" 2>nul
copy /y LICENSE "%APP%\LICENSE.txt" >nul
copy /y THIRD_PARTY_NOTICES.md "%APP%\licenses\" >nul
if exist NOTICE_Phoque_Al.pdf copy /y NOTICE_Phoque_Al.pdf "%APP%\" >nul
for /f "delims=" %%F in ('where ffmpeg 2^>nul') do if not exist "%APP%\tools\ffmpeg.exe" (
  copy /y "%%F" "%APP%\tools\ffmpeg.exe" >nul
  if exist "%%~dpF..\LICENSE" copy /y "%%~dpF..\LICENSE" "%APP%\licenses\FFmpeg_LICENSE.txt" >nul
  if exist "%%~dpF..\README.txt" copy /y "%%~dpF..\README.txt" "%APP%\licenses\FFmpeg_README.txt" >nul
)
for /f "delims=" %%F in ('where ffprobe 2^>nul') do if not exist "%APP%\tools\ffprobe.exe" copy /y "%%F" "%APP%\tools\ffprobe.exe" >nul
set VLC=%ProgramFiles%\VideoLAN\VLC
if exist "%VLC%\libvlc.dll" (
  mkdir "%APP%\tools\vlc" 2>nul
  copy /y "%VLC%\libvlc.dll" "%APP%\tools\vlc\" >nul
  copy /y "%VLC%\libvlccore.dll" "%APP%\tools\vlc\" >nul
  robocopy "%VLC%\plugins" "%APP%\tools\vlc\plugins" /E /NFL /NDL /NJH /NJS /NP >nul
  copy /y "%VLC%\COPYING.txt" "%APP%\licenses\VLC_COPYING.txt" >nul
) else echo WARNING: VLC not found, the build will need VLC installed on the target PC.
rem UnRAR / 7-Zip are NOT bundled (license); the app uses them if the user has them installed.

echo [4/4] Inno Setup
set ISCC=
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do if exist %%P set ISCC=%%~P
if not defined ISCC winget install -e --id JRSoftware.InnoSetup --scope user --silent --accept-package-agreements --accept-source-agreements
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do if exist %%P set ISCC=%%~P
if not defined ISCC goto fail
"%ISCC%" /Q installer\phoque_al.iss || goto fail
echo Done: dist\PhoqueAl_Setup.exe
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
