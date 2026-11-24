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
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    printf '%s\n' 'Нужен Python 3.11 или новее.' >&2
    exit 1
fi
if [[ -e "$target" ]]; then
    if [[ ! -d "$target/.git" || ! -f "$target/main.py" ]]; then
        printf 'Папка %s существует, но не похожа на установку Playerok Cardinal. Файлы не изменены.\n' "$target" >&2
        exit 1
    fi
    printf 'Найдена существующая установка. Обновляю код, сохраняя .env и пользовательские данные.\n'
    git -C "$target" pull --ff-only
else
    git clone -- "$repo" "$target"
fi
cd "$target"
[[ -x venv/bin/python ]] || python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python first_setup.py
chmod 600 .env
service_user="$(id -un)"
service_group="$(id -gn)"
service_file="$(mktemp)"
trap 'rm -f "$service_file"' EXIT
sed -e "s|@APP_DIR@|$target|g" -e "s|@SERVICE_USER@|$service_user|g" -e "s|@SERVICE_GROUP@|$service_group|g" \
    scripts/playerok-cardinal.service.in > "$service_file"
sudo install -m 644 "$service_file" /etc/systemd/system/playerok-cardinal.service
sudo systemctl daemon-reload
sudo systemctl enable playerok-cardinal.service
sudo systemctl restart playerok-cardinal.service
sudo systemctl --no-pager --full status playerok-cardinal.service
printf '\nБот работает как служба и продолжит работу после закрытия Termius.\n'
printf 'Логи: sudo journalctl -u playerok-cardinal -f\n'
