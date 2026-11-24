$ErrorActionPreference = 'Stop'
$target = Join-Path $HOME 'PlayerokCardinal'
if (Test-Path -LiteralPath $target) {
    throw "Папка $target уже существует. Установка остановлена."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Установите Git и повторите установку.' }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Установите Python 3.11 или новее и повторите установку.' }
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) { throw 'Нужен Python 3.11 или новее.' }
git clone -- 'https://github.com/Ziw1nFN/playerok-cardinal-public.git' $target
if ($LASTEXITCODE -ne 0) { throw 'Не удалось скачать бота.' }
Set-Location -LiteralPath $target
py -3 -m venv venv
if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать окружение Python.' }
& '.\venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости.' }
& '.\venv\Scripts\python.exe' first_setup.py
if ($LASTEXITCODE -ne 0) { throw 'Настройка не завершена.' }
Write-Host 'Запускаю бота. Для остановки нажмите Ctrl+C.'
& '.\venv\Scripts\python.exe' main.py
