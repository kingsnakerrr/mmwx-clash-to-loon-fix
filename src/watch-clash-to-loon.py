#!/usr/bin/env python3
import argparse
import os
import shutil
import sqlite3
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml


FIX_DIR = os.environ.get('MMWX_FIX_DIR', '/opt/mmwx-subinfo-proxy')
DB = os.environ.get('MMWX_DB', '/opt/miaomiaowux/data/mmwx.db')
BACKEND = os.environ.get('MMWX_BACKEND', 'http://127.0.0.1:12889')
DOMAIN = os.environ.get('MMWX_DOMAIN', 'localhost')
CONTAINER = os.environ.get('MMWX_CONTAINER', 'miaomiaowux')
LIVE_PROXY = os.path.join(FIX_DIR, 'proxy.py')
HEADER_ONLY_PROXY = os.path.join(FIX_DIR, 'proxy.py.header-only')
STATE = os.path.join(FIX_DIR, 'clash-to-loon-image.state')
RETIRED = os.path.join(FIX_DIR, 'clash-to-loon-fallback.retired')
PREVIOUS_NOTIFY = os.path.join(FIX_DIR, 'notify-subscribe-fetch.previous')


def container_image():
    result = subprocess.run(
        ['docker', 'inspect', CONTAINER, '--format', '{{.Image}}'],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def package_code():
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=2)
    try:
        row = con.execute(
            "select short_code from user_package_assignments "
            "where status='active' and short_code<>'' order by is_primary desc, id limit 1"
        ).fetchone()
        return row[0] if row else ''
    finally:
        con.close()


def fetch(path):
    request = Request(BACKEND + path, headers={
        'Host': DOMAIN,
        'User-Agent': 'mmwx-clash-to-loon-fix-monitor/1.1',
        'Accept-Encoding': 'identity',
    })
    try:
        return urlopen(request, timeout=60)
    except HTTPError as error:
        return error


def official_supports_conversion(code):
    source = fetch(f'/x/{code}?t=clash')
    converted = fetch(f'/x/{code}?t=clash-to-loon')
    try:
        source_body = source.read()
        converted_body = converted.read()
        if source.status != 200 or converted.status != 200:
            return False
        required = (b'[General]', b'\n[Proxy]\n', b'\n[Proxy Group]\n', b'\n[Rule]\n')
        if not all(marker in converted_body for marker in required):
            return False
        config = yaml.safe_load(source_body.decode('utf-8')) or {}
        has_chain = any(
            isinstance(proxy, dict) and str(proxy.get('dialer-proxy', '')).strip()
            for proxy in config.get('proxies') or []
        )
        return not has_chain or b'\n[Proxy Chain]\n' in converted_body
    finally:
        source.close()
        converted.close()


def write_state(image):
    tmp = STATE + '.new'
    with open(tmp, 'w', encoding='ascii') as handle:
        handle.write(image + '\n')
    os.replace(tmp, STATE)


def previous_notify_setting():
    try:
        with open(PREVIOUS_NOTIFY, encoding='ascii') as handle:
            return 1 if handle.read().strip() == '1' else 0
    except FileNotFoundError:
        return 1


def retire_fallback(image):
    if not os.path.isfile(HEADER_ONLY_PROXY):
        raise RuntimeError(f'missing header-only proxy: {HEADER_ONLY_PROXY}')
    replacement = LIVE_PROXY + '.new'
    shutil.copy2(HEADER_ONLY_PROXY, replacement)
    os.replace(replacement, LIVE_PROXY)
    con = sqlite3.connect(DB, timeout=10)
    try:
        con.execute(
            "update system_config set notify_subscribe_fetch=?, "
            "updated_at=datetime('now') where id=1",
            (previous_notify_setting(),),
        )
        con.commit()
    finally:
        con.close()
    with open(RETIRED, 'w', encoding='utf-8') as handle:
        handle.write(f'Official clash-to-loon support verified on image {image}\n')
    subprocess.run(['systemctl', 'restart', 'mmwx-subinfo-proxy.service'], check=True)
    subprocess.run(
        ['systemctl', 'disable', '--now', 'mmwx-clash-to-loon-watch.timer'],
        check=False,
    )
    print('Official support verified; conversion fallback retired.', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force-check', action='store_true')
    args = parser.parse_args()
    if os.path.exists(RETIRED):
        return

    image = container_image()
    try:
        with open(STATE, encoding='ascii') as handle:
            previous = handle.read().strip()
    except FileNotFoundError:
        previous = ''

    if not args.force_check and previous == image:
        return
    if not previous and not args.force_check:
        write_state(image)
        print(f'Initialized image state: {image}', flush=True)
        return

    code = package_code()
    if not code:
        write_state(image)
        print('No active package link available for verification.', flush=True)
        return
    if official_supports_conversion(code):
        retire_fallback(image)
        return

    write_state(image)
    print(f'Official conversion is incomplete on image {image}; fallback retained.', flush=True)


if __name__ == '__main__':
    main()
