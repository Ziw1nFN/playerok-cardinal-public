from __future__ import annotations

import logging
import threading
import time
import base64
import hashlib
import os
import sys
from pathlib import Path
from html import escape

import requests

from Utils import tools
from Utils.envfile import read_env, set_env

logger = logging.getLogger('cardinal.control')
INTERVAL = 300


class ControlClient:
    def __init__(self, tg, url: str, env_path: str = '.env'):
        self.tg = tg
        if not url.startswith('https://'):
            raise ValueError('Control service requires HTTPS')
        self.url = url.rstrip('/')
        self.env_path = env_path
        self.state_file = 'control_client.json'
        self.install_lock = threading.Lock()

    def call(self, path: str, payload: dict) -> dict:
        response = requests.post(self.url + path, json=payload, timeout=(5, 20))
        response.raise_for_status()
        return response.json()

    def tick(self):
        if not self.tg.users:
            return
        state = tools.load_cache(self.state_file, {})
        if not state.get('notice_sent'):
            notice = ('ℹ️ Для обновлений и поддержки бот передаёт разработчику ID установки, '
                      'Telegram ID владельцев панели, время активности и названия установленных плагинов.')
            for user_id in self.tg.users:
                self.tg.bot.send_message(user_id, notice)
            state['notice_sent'] = True
            tools.save_cache(self.state_file, state)
        env = read_env(self.env_path)
        ident, secret = env.get('CONTROL_INSTALLATION_ID'), env.get('CONTROL_INSTALLATION_SECRET')
        if not ident or not secret:
            registered = self.call('/v1/register', {})
            ident, secret = registered['id'], registered['secret']
            set_env({'CONTROL_INSTALLATION_ID': ident, 'CONTROL_INSTALLATION_SECRET': secret}, self.env_path)
            state['after'] = int(registered.get('after', 0))
            tools.save_cache(self.state_file, state)
        plugins = [p.name for p in self.tg.hub.plugins.plugins.values()]
        result = self.call('/v1/heartbeat', {'id': ident, 'secret': secret, 'plugins': plugins,
                                             'after': int(state.get('after', 0)), 'owners': list(self.tg.users)})
        for message in result.get('messages', []):
            for user_id in self.tg.users:
                self.tg.bot.send_message(user_id, f"📢 <b>Сообщение разработчика</b>\n\n{escape(message['text'])}")
            state['after'] = message['id']
            tools.save_cache(self.state_file, state)
        release = result.get('release')
        if release and release['id'] > int(state.get('notified_release', 0)):
            from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup().row(InlineKeyboardButton('📦 Установить обновление', callback_data='upd'))
            for user_id in self.tg.users:
                self.tg.bot.send_message(user_id, '📦 Доступно обновление Playerok Cardinal.', reply_markup=kb)
            state['notified_release'] = release['id']
            state['release'] = release
            tools.save_cache(self.state_file, state)

    def install(self):
        if not self.install_lock.acquire(blocking=False):
            raise ValueError('Установка уже выполняется')
        try:
            return self._install()
        finally:
            self.install_lock.release()

    def _install(self):
        from Utils.release_archive import install_archive
        state = tools.load_cache(self.state_file, {})
        release = state.get('release')
        if not release:
            raise ValueError('Нет доступного обновления')
        if state.get('installed_release') == release['id']:
            raise ValueError('Обновление уже установлено')
        env = read_env(self.env_path)
        result = self.call('/v1/release', {'id': env['CONTROL_INSTALLATION_ID'],
                                          'secret': env['CONTROL_INSTALLATION_SECRET'],
                                          'release_id': release['id']})
        encoded = result['content']
        if not isinstance(encoded, str) or len(encoded) > 21 * 1024 * 1024:
            raise ValueError('Обновление слишком большое')
        content = base64.b64decode(encoded, validate=True)
        if hashlib.sha256(content).hexdigest() != release['digest'] or result['digest'] != release['digest']:
            raise ValueError('Контрольная сумма обновления не совпала')
        folder = Path('backup') / 'updates'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"release-{release['id']}.zip"
        path.write_bytes(content)
        backup = install_archive(str(path), '.', str(folder))
        state['installed_release'] = release['id']
        tools.save_cache(self.state_file, state)
        return backup

    def run(self):
        while True:
            try:
                self.tick()
            except Exception as exc:
                logger.warning('Control service unavailable: %s', type(exc).__name__)
            time.sleep(INTERVAL)

    def start(self):
        threading.Thread(target=self.run, daemon=True, name='control-client').start()
