#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import os
import re
import shutil
import sqlite3
import time


BACKEND = os.environ.get('MMWX_BACKEND', 'http://127.0.0.1:12889')
DB = os.environ.get('MMWX_DB', '/opt/miaomiaowux/data/mmwx.db')
LISTEN = os.environ.get('MMWX_SUBINFO_LISTEN', '127.0.0.1')
PORT = int(os.environ.get('MMWX_SUBINFO_PORT', '12890'))
TOKEN_RE = re.compile(r'^/x/([^/?#]+)')
HOP_BY_HOP = {
    'connection',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailer',
    'transfer-encoding',
    'upgrade',
}


def find_user(path):
    match = TOKEN_RE.match(path)
    if not match:
        return None
    code = match.group(1)
    try:
        con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=2)
        rows = con.execute(
            'select username, user_short_code, custom_user_short_code '
            'from user_tokens'
        ).fetchall()
        con.close()
    except Exception:
        return None
    matches = []
    for username, short_code, custom_code in rows:
        for candidate in (custom_code or '', short_code or ''):
            if candidate and code.endswith(candidate):
                matches.append((len(candidate), username))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def package_assignment(path):
    match = TOKEN_RE.match(path)
    if not match:
        return None
    code = match.group(1)
    try:
        con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=2)
        row = con.execute(
            "select username from user_package_assignments "
            "where short_code=? and status='active' limit 1",
            (code,),
        ).fetchone()
        con.close()
    except Exception:
        return None
    return row[0] if row else None


def user_info(username):
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=2)
    try:
        traffic = con.execute(
            'select coalesce(sum(uplink),0), coalesce(sum(downlink),0) '
            'from user_traffic where username=?',
            (username,),
        ).fetchone()
        quota = con.execute(
            '''
            select coalesce(
                       upa.traffic_limit_override,
                       u.traffic_limit_override,
                       p.traffic_limit_bytes,
                       0
                   ),
                   coalesce(upa.package_end_date, u.package_end_date, '')
            from users u
            left join user_package_assignments upa
              on upa.username=u.username
             and upa.status='active'
             and upa.is_primary=1
            left join packages p
              on p.id=coalesce(upa.package_id, u.package_id)
            where u.username=?
            ''',
            (username,),
        ).fetchone()
        upload = int(traffic[0] or 0)
        download = int(traffic[1] or 0)
        total = int(quota[0] or 0) if quota else 0
        return upload, download, total
    finally:
        con.close()


def rewrite_subinfo(original, username):
    if not original or not username:
        return original
    upload, download, total = user_info(username)
    fields = {}
    for part in original.split(';'):
        if '=' in part:
            key, value = part.strip().split('=', 1)
            fields[key.strip().lower()] = value.strip()
    fields['upload'] = str(upload)
    fields['download'] = str(download)
    if total > 0:
        fields['total'] = str(total)
    order = ['upload', 'download', 'total', 'expire']
    output = [f'{key}={fields[key]}' for key in order if key in fields]
    output.extend(
        f'{key}={value}' for key, value in fields.items() if key not in order
    )
    return '; '.join(output)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_HEAD(self):
        self.forward(head_only=True)

    def do_GET(self):
        self.forward(head_only=False)

    def log_message(self, fmt, *args):
        print(
            '%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), fmt % args),
            flush=True,
        )

    def forward(self, head_only=False):
        target = BACKEND + self.path
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() != 'host'
        }
        headers['Host'] = '127.0.0.1:12889'
        request = Request(target, headers=headers, method='GET')
        try:
            response = urlopen(request, timeout=30)
            status = response.status
            reason = response.reason
            response_headers = response.headers
        except HTTPError as error:
            response = error
            status = error.code
            reason = error.reason
            response_headers = error.headers

        username = find_user(self.path) or package_assignment(self.path)
        self.send_response(status, reason)
        for key, value in response_headers.items():
            lower_key = key.lower()
            if lower_key in HOP_BY_HOP or lower_key in {
                'content-length',
                'subscription-userinfo',
            }:
                continue
            self.send_header(key, value)
        subinfo = response_headers.get('Subscription-Userinfo')
        if subinfo:
            try:
                subinfo = rewrite_subinfo(subinfo, username)
            except Exception as exc:
                print('rewrite failed:', exc, flush=True)
            self.send_header('Subscription-Userinfo', subinfo)
        content_length = response_headers.get('Content-Length')
        if content_length:
            self.send_header('Content-Length', content_length)
        else:
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()
        if not head_only:
            shutil.copyfileobj(response, self.wfile)
        response.close()


if __name__ == '__main__':
    ThreadingHTTPServer((LISTEN, PORT), Handler).serve_forever()
