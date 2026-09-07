#!/usr/bin/env python3
import argparse
import os
import sqlite3
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml


DB = os.environ.get('MMWX_DB', '/opt/miaomiaowux/data/mmwx.db')
BACKEND = os.environ.get('MMWX_BACKEND', 'http://127.0.0.1:12889')
DOMAIN = os.environ.get('MMWX_DOMAIN', 'localhost')
CONTAINER = os.environ.get('MMWX_CONTAINER', 'miaomiaowux')
PROXY = os.environ.get('MMWX_DIAGNOSTIC_PROXY', 'http://127.0.0.1:12890')
USER_AGENT = 'mmwx-fix-diagnostic/1.0'


class Report:
    def __init__(self, mode):
        self.mode = mode
        self.errors = []
        self.bugs = []

    def error(self, code, message):
        self.errors.append(code)
        print(f'[错误] {code}: {message}')

    def bug(self, code, message):
        if code not in self.bugs:
            self.bugs.append(code)
        print(f'[Bug] {code}: {message}')

    def finish(self):
        print(f'MMWX_CHECK_MODE={self.mode}')
        print(f'MMWX_DETECTED_BUGS={",".join(self.bugs) or "none"}')
        print(f'MMWX_BUG_STATUS={"存在" if self.bugs else "不存在"}')
        print(f'MMWX_CHECK_ERRORS={",".join(self.errors) or "none"}')
        if self.errors:
            print('MMWX_CHECK_RESULT=FAIL')
            if self.mode == 'post':
                print('MMWX_FIX_RESULT=修复无效')
                print(f'MMWX_UNRESOLVED_BUGS={",".join(self.bugs + self.errors)}')
            return 1
        print('MMWX_CHECK_RESULT=PASS')
        if self.mode == 'post':
            if self.bugs:
                print('MMWX_FIX_RESULT=修复无效')
                print(f'MMWX_UNRESOLVED_BUGS={",".join(self.bugs)}')
                return 1
            print('MMWX_FIX_RESULT=修复有效')
            print('MMWX_UNRESOLVED_BUGS=none')
        return 0


def request(base, path):
    req = Request(base + path, headers={
        'Host': DOMAIN,
        'User-Agent': USER_AGENT,
        'Accept-Encoding': 'identity',
    })
    try:
        response = urlopen(req, timeout=60)
    except HTTPError as error:
        response = error
    body = response.read()
    result = response.status, response.headers, body
    response.close()
    return result


def inspect_installation(report):
    if not os.path.isfile(DB):
        report.error('mmwx-database-missing', f'找不到数据库 {DB}')
        return False
    try:
        result = subprocess.run(
            ['docker', 'inspect', CONTAINER, '--format', '{{.State.Running}}'],
            check=True, capture_output=True, text=True,
        )
        if result.stdout.strip().lower() != 'true':
            report.error('mmwx-container-stopped', f'容器 {CONTAINER} 没有运行')
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        report.error('mmwx-container-missing', f'无法检查容器 {CONTAINER}: {exc}')

    required = {
        'system_config', 'users', 'user_tokens', 'user_traffic',
        'packages', 'user_package_assignments',
    }
    try:
        con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=3)
        tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
        con.close()
        missing = sorted(required - tables)
        if missing:
            report.error('mmwx-schema-unsupported', '数据库缺少表: ' + ', '.join(missing))
    except sqlite3.Error as exc:
        report.error('mmwx-database-unreadable', str(exc))
    return not report.errors


def active_code():
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=3)
    try:
        row = con.execute(
            "select short_code from user_package_assignments "
            "where status='active' and short_code<>'' order by is_primary desc, id limit 1"
        ).fetchone()
        if row:
            return row[0]
        row = con.execute(
            "select coalesce(nullif(custom_user_short_code,''), user_short_code) "
            "from user_tokens where user_short_code<>'' limit 1"
        ).fetchone()
        return row[0] if row else ''
    finally:
        con.close()


def expected_userinfo(code):
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=3)
    try:
        row = con.execute(
            "select username from user_package_assignments "
            "where short_code=? and status='active' limit 1", (code,),
        ).fetchone()
        if not row:
            return None
        username = row[0]
        traffic = con.execute(
            'select coalesce(sum(uplink),0), coalesce(sum(downlink),0) '
            'from user_traffic where username=?', (username,),
        ).fetchone()
        quota = con.execute(
            '''
            select coalesce(upa.traffic_limit_override, u.traffic_limit_override,
                            p.traffic_limit_bytes, 0)
            from users u
            left join user_package_assignments upa
              on upa.username=u.username and upa.status='active' and upa.is_primary=1
            left join packages p on p.id=coalesce(upa.package_id, u.package_id)
            where u.username=?
            ''', (username,),
        ).fetchone()
        return int(traffic[0] or 0), int(traffic[1] or 0), int(quota[0] or 0)
    finally:
        con.close()


def parse_userinfo(value):
    fields = {}
    for part in (value or '').split(';'):
        if '=' in part:
            key, item = part.strip().split('=', 1)
            try:
                fields[key.lower()] = int(item)
            except ValueError:
                pass
    return fields


def validate_conversion(report, source_body, status, headers, body, code):
    if status != 200:
        text = body.decode('utf-8', errors='replace')[:300]
        if 'producer type' in text and 'not found' in text:
            report.bug('clash-to-loon-producer-missing', '服务端未注册 clash-to-loon 转换器')
        else:
            report.bug('clash-to-loon-http-error', f'返回 HTTP {status}: {text}')
        return
    required = ('[General]', '[Proxy]', '[Proxy Group]', '[Rule]')
    output = body.decode('utf-8', errors='replace')
    missing = [section for section in required if section not in output]
    if missing:
        report.bug('loon-sections-incomplete', '缺少配置段: ' + ', '.join(missing))
        return

    try:
        config = yaml.safe_load(source_body.decode('utf-8')) or {}
    except Exception as exc:
        report.error('clash-source-invalid', f'Clash YAML 无法解析: {exc}')
        return

    chains = [
        str(item.get('name', '')).strip()
        for item in config.get('proxies') or []
        if isinstance(item, dict) and str(item.get('dialer-proxy', '')).strip()
    ]
    if chains and '[Proxy Chain]' not in output:
        report.bug('proxy-chain-lost', f'{len(chains)} 个 dialer-proxy 节点未生成 [Proxy Chain]')
    for name in chains:
        if f'{name} =' not in output:
            report.bug('proxy-chain-name-lost', f'代理链 {name} 未保留')
            break

    group_names = [
        str(group.get('name', '')).strip()
        for group in config.get('proxy-groups') or [] if isinstance(group, dict)
    ]
    missing_groups = [name for name in group_names if name and f'{name} =' not in output]
    if missing_groups:
        report.bug('proxy-groups-lost', '缺少策略组: ' + ', '.join(missing_groups[:5]))

    match_policies = []
    for rule in config.get('rules') or []:
        parts = [part.strip() for part in str(rule).split(',')]
        if len(parts) >= 2 and parts[0] == 'MATCH':
            match_policies.append(parts[1])
    if any(f'FINAL,{policy}' not in output for policy in match_policies):
        report.bug('final-rule-lost', 'Clash MATCH 没有正确转换为 Loon FINAL')

    expected = expected_userinfo(code)
    if expected:
        actual = parse_userinfo(headers.get('Subscription-Userinfo'))
        keys = ('upload', 'download', 'total')
        if any(actual.get(key) != value for key, value in zip(keys, expected)):
            report.bug('subscription-userinfo-wrong', '流量响应头与数据库套餐/用量不一致')


def check_notification_handoff(report):
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=3)
    try:
        row = con.execute('select notify_subscribe_fetch from system_config where id=1').fetchone()
    finally:
        con.close()
    if not row or int(row[0] or 0) != 0:
        report.bug('duplicate-notification-risk', '内置订阅通知仍开启，兼容转换可能重复通知')


def main():
    parser = argparse.ArgumentParser(description='MMWX compatibility diagnostics')
    parser.add_argument('--mode', choices=('pre', 'post'), required=True)
    args = parser.parse_args()
    report = Report(args.mode)
    if not inspect_installation(report):
        return report.finish()

    try:
        code = active_code()
    except sqlite3.Error as exc:
        report.error('subscription-code-query-failed', str(exc))
        return report.finish()
    if not code:
        report.error('subscription-code-missing', '没有可用于检测的有效套餐或用户订阅短码')
        return report.finish()

    try:
        source_status, _, source_body = request(BACKEND, f'/x/{code}?t=clash')
        if source_status != 200:
            report.error('clash-source-unavailable', f'官方 Clash 订阅返回 HTTP {source_status}')
            return report.finish()
        target = BACKEND if args.mode == 'pre' else PROXY
        status, headers, body = request(target, f'/x/{code}?t=clash-to-loon')
        validate_conversion(report, source_body, status, headers, body, code)
        if args.mode == 'post':
            check_notification_handoff(report)
    except (URLError, TimeoutError, OSError) as exc:
        report.error('subscription-request-failed', str(exc))
    return report.finish()


if __name__ == '__main__':
    sys.exit(main())
