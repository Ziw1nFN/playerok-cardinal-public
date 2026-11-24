#!/usr/bin/env bash
set -euo pipefail

repo='https://github.com/Ziw1nFN/playerok-cardinal-public.git'
target="${HOME}/PlayerokCardinal"

if [[ ! -t 0 ]]; then
    if [[ ! -r /dev/tty ]]; then
        printf '%s\n' 'Нужен интерактивный терминал для настройки.' >&2
        exit 1
    fi
    exec </dev/tty
fi
for cmd in git python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        printf 'Не найдена команда %s. Установите её и повторите установку.\n' "$cmd" >&2
        exit 1
    fi
done
if [[ -e "$target" ]]; then
    printf 'Папка %s уже существует. Выберите другое расположение или сохраните её перед установкой.\n' "$target" >&2
    exit 1
fi
git clone -- "$repo" "$target"
cd "$target"
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python first_setup.py
printf '%s\n' 'Запускаю бота. Для остановки нажмите Ctrl+C.'
exec venv/bin/python main.py
