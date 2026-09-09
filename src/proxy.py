#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import os
import re
import sqlite3
import threading
import time

import yaml


BACKEND = os.environ.get('MMWX_BACKEND', 'http://127.0.0.1:12889')
DB = os.environ.get('MMWX_DB', '/opt/miaomiaowux/data/mmwx.db')
LISTEN = os.environ.get('MMWX_SUBINFO_LISTEN', '127.0.0.1')
PORT = int(os.environ.get('MMWX_SUBINFO_PORT', '12890'))
NOTIFY_DEDUPE_SECONDS = int(os.environ.get('MMWX_NOTIFY_DEDUPE_SECONDS', '60'))
PROXY_NOTIFY_ENABLED = os.environ.get('MMWX_PROXY_NOTIFY', '1') == '1'
TOKEN_RE = re.compile(r'^/x/([^/?#]+)')
NOTIFY_LOCK = threading.Lock()
NOTIFY_RECENT = {}
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


def query_type(path):
    return dict(parse_qsl(urlsplit(path).query, keep_blank_values=True)).get('t')


def with_query_type(path, client_type):
    parts = urlsplit(path)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query['t'] = client_type
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def fetch_backend(path, headers):
    request = Request(BACKEND + path, headers=headers, method='GET')
    try:
        return urlopen(request, timeout=30)
    except HTTPError as error:
        return error


LOON_GENERAL = '''[General]
ip-mode = dual
dns-server = system, 119.29.29.29, 223.5.5.5
sni-sniffing = true
disable-stun = false
dns-reject-mode = LoopbackIP
domain-reject-mode = DNS
udp-fallback-mode = REJECT
wifi-access-http-port = 7222
wifi-access-socks5-port = 7221
allow-wifi-access = false
interface-mode = auto
test-timeout = 5
disconnect-on-policy-change = true
switch-node-after-failure-times = 3
internet-test-url = http://connectivitycheck.platform.hicloud.com/generate_204
proxy-test-url = http://www.gstatic.com/generate_204
resource-parser = https://gitlab.com/sub-store/Sub-Store/-/releases/permalink/latest/downloads/sub-store-parser.loon.min.js
skip-proxy = 192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12, localhost, *.local, e.crashlynatics.com
bypass-tun = 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.88.99.0/24, 192.168.0.0/16, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 255.255.255.255/32'''


def build_loon_proxies_and_chains(config, proxy_lines):
    chain_specs = {}
    for proxy in config.get('proxies') or []:
        name = str(proxy.get('name', '')).strip()
        dialer = str(proxy.get('dialer-proxy', '')).strip()
        if name and dialer:
            chain_specs[name] = dialer

    rendered_proxies = []
    chain_targets = {}
    for line in proxy_lines.splitlines():
        left, separator, right = line.partition('=')
        name = left.strip()
        if separator and name in chain_specs:
            target_name = f'{name} [落地]'
            rendered_proxies.append(f'{target_name} = {right.strip()}')
            chain_targets[name] = target_name
        else:
            rendered_proxies.append(line)

    sections = ['[Proxy]\n' + '\n'.join(rendered_proxies)]
    if chain_targets:
        chains = ['[Proxy Chain]']
        for name, target_name in chain_targets.items():
            chains.append(f'{name} = {chain_specs[name]}, {target_name}, udp=true')
        sections.append('\n'.join(chains))
    return sections


def build_loon_groups(groups):
    lines = ['[Proxy Group]']
    for group in groups or []:
        name = str(group.get('name', '')).strip()
        group_type = str(group.get('type', 'select')).lower()
        proxies = [str(item) for item in group.get('proxies', [])]
        if not proxies:
            proxies = ['DIRECT']
        url = group.get('url') or 'http://www.gstatic.com/generate_204'
        interval = int(group.get('interval') or 300)
        if group_type in ('url-test', 'fallback'):
            line = (
                f"{name} = {group_type}, {', '.join(proxies)}, url = {url}, "
                f"interval = {interval}, img-url = "
                'https://raw.githubusercontent.com/Koolson/Qure/master/IconSet/Color/Auto.png'
            )
            if group_type == 'url-test' and int(group.get('tolerance') or 0) > 0:
                line += f", tolerance = {int(group['tolerance'])}"
        elif group_type == 'load-balance':
            algorithm = 'round-robin' if group.get('strategy') == 'round-robin' else 'pcc'
            line = (
                f"{name} = load-balance, {', '.join(proxies)}, url = {url}, "
                f"interval = {interval}, algorithm = {algorithm}, img-url = "
                'https://raw.githubusercontent.com/Koolson/Qure/master/IconSet/Color/Available.png'
            )
        else:
            line = (
                f"{name} = select, {', '.join(proxies)}, img-url = "
                'https://raw.githubusercontent.com/Koolson/Qure/master/IconSet/Color/Proxy.png'
            )
        lines.append(line)
    return '\n'.join(lines)


def inline_lanlan_provider(provider, policy):
    url = str(provider.get('url', ''))
    parts = urlsplit(url)
    if (
        parts.hostname != 'raw.githubusercontent.com'
        or '/Lanlan13-14/Rules/' not in parts.path
        or not url.endswith('.mrs')
    ):
        return None

    yaml_url = url[:-4] + '.yaml'
    try:
        request = Request(yaml_url, headers={'User-Agent': 'MMWX-Loon-Converter/1.1'})
        with urlopen(request, timeout=15) as response:
            payload = (yaml.safe_load(response.read().decode('utf-8')) or {}).get('payload', [])
    except Exception as exc:
        print(f'failed to inline Loon rule provider {yaml_url}: {exc}', flush=True)
        return None

    behavior = str(provider.get('behavior', '')).lower()
    converted = []
    for value in payload:
        value = str(value).strip()
        if not value:
            continue
        if behavior == 'domain':
            if value.startswith('+.'):
                converted.append(f'DOMAIN-SUFFIX,{value[2:]},{policy}')
            elif value.startswith('.'):
                converted.append(f'DOMAIN-SUFFIX,{value[1:]},{policy}')
            else:
                converted.append(f'DOMAIN,{value},{policy}')
        elif behavior == 'ipcidr':
            rule_type = 'IP-CIDR6' if ':' in value else 'IP-CIDR'
            converted.append(f'{rule_type},{value},{policy},no-resolve')
        elif behavior == 'classical':
            converted.append(f'{value},{policy}')
    return converted


def build_loon_rules(rules, providers):
    lines = ['[Rule]']
    remote = []
    passthrough = {
        'DOMAIN', 'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'IP-CIDR', 'IP-CIDR6',
        'GEOIP', 'SRC-IP-CIDR', 'SRC-PORT', 'DST-PORT', 'PROCESS-NAME', 'IP-ASN',
    }
    for rule in rules or []:
        rule = str(rule)
        parts = [part.strip() for part in rule.split(',')]
        if len(parts) < 2:
            continue
        if parts[0] in passthrough:
            lines.append(rule)
        elif parts[0] == 'MATCH':
            lines.append(f'FINAL,{parts[1]}')
        elif parts[0] == 'RULE-SET' and len(parts) >= 3:
            provider = (providers or {}).get(parts[1], {})
            url = provider.get('url', '')
            if url:
                inlined = inline_lanlan_provider(provider, parts[2])
                if inlined is not None:
                    lines.extend(inlined)
                    continue
                if url.endswith('.yaml') or url.endswith('.mrs'):
                    url = url.rsplit('.', 1)[0] + '.list'
                remote.append(f'{url}, policy={parts[2]}, tag={parts[1]}, enabled=true')
        else:
            lines.append(rule)
    result = '\n'.join(lines)
    if remote:
        result += '\n\n[Remote Rule]\n' + '\n'.join(remote)
    return result


def clash_to_loon(clash_data, loon_proxy_data):
    config = yaml.safe_load(clash_data.decode('utf-8')) or {}
    proxy_lines = loon_proxy_data.decode('utf-8').strip()
    sections = [LOON_GENERAL]
    sections.extend(build_loon_proxies_and_chains(config, proxy_lines))
    sections.extend([
        build_loon_groups(config.get('proxy-groups')),
        build_loon_rules(config.get('rules'), config.get('rule-providers')),
    ])
    return ('\n\n'.join(sections)).encode('utf-8')


def official_conversion_complete(clash_data, loon_data):
    try:
        config = yaml.safe_load(clash_data.decode('utf-8')) or {}
        output = loon_data.decode('utf-8', errors='replace')
    except Exception:
        return False

    required = ('[General]', '[Proxy]', '[Proxy Group]', '[Rule]')
    if not all(section in output for section in required):
        return False

    groups = [
        str(group.get('name', '')).strip()
        for group in config.get('proxy-groups') or []
        if isinstance(group, dict) and str(group.get('name', '')).strip()
    ]
    if any(f'{name} =' not in output for name in groups):
        return False

    chains = [
        str(proxy.get('name', '')).strip()
        for proxy in config.get('proxies') or []
        if isinstance(proxy, dict) and str(proxy.get('dialer-proxy', '')).strip()
    ]
    if chains and '[Proxy Chain]' not in output:
        return False
    if any(f'{name} =' not in output for name in chains):
        return False

    for rule in config.get('rules') or []:
        parts = [part.strip() for part in str(rule).split(',')]
        if len(parts) >= 2 and parts[0] == 'MATCH' and f'FINAL,{parts[1]}' not in output:
            return False
    return True


def maybe_clash_to_loon_fallback(path, headers, response, body):
    client_type = query_type(path)
    supported = {'clash-to-loon', 'clash-to-loon-kelee'}
    if client_type not in supported or not package_assignment(path):
        return None
    marker = f"producer type '{client_type}' not found".encode('utf-8')
    missing_producer = response.status == 500 and marker in body
    if response.status != 200 and not missing_producer:
        return None

    clash_response = fetch_backend(with_query_type(path, 'clash'), headers)
    try:
        if clash_response.status != 200:
            return None
        clash_data = clash_response.read()
        if response.status == 200 and official_conversion_complete(clash_data, body):
            return None
        print('official clash-to-loon output incomplete; applying fallback', flush=True)
        loon_response = fetch_backend(with_query_type(path, 'loon'), headers)
        try:
            if loon_response.status != 200:
                return None
            converted = clash_to_loon(clash_data, loon_response.read())
            return converted, clash_response.headers
        finally:
            loon_response.close()
    finally:
        clash_response.close()


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


def subscription_notification_context(username, path):
    match = TOKEN_RE.match(path)
    code = match.group(1) if match else ''
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=2)
    try:
        config = con.execute(
            'select notify_enabled, telegram_bot_token, telegram_chat_id '
            'from system_config where id=1'
        ).fetchone()
        package = con.execute(
            '''
            select p.name
            from user_package_assignments upa
            join packages p on p.id=upa.package_id
            where upa.short_code=? and upa.status='active'
            limit 1
            ''',
            (code,),
        ).fetchone()
        if not package:
            package = con.execute(
                '''
                select p.name
                from user_package_assignments upa
                join packages p on p.id=upa.package_id
                where upa.username=? and upa.status='active'
                order by upa.is_primary desc, upa.id
                limit 1
                ''',
                (username,),
            ).fetchone()
    finally:
        con.close()
    if not config or not config[0] or not config[1] or not config[2]:
        return None
    return config[1], config[2], package[0] if package else '-'


def send_subscription_notification(username, path, user_agent, client_ip):
    try:
        context = subscription_notification_context(username, path)
        if not context:
            return
        token, chat_id, package_name = context
        upload, download, total = user_info(username)
        used = upload + download
        gib = 1024 ** 3
        remaining = max(total - used, 0)
        message = '\n'.join([
            '订阅获取',
            f'用户:{username}',
            f'客户端:{user_agent or "-"}',
            f'IP:{client_ip or "-"}',
            f'套餐:{package_name}',
            f'流量:{used / gib:.2f} GB / {total / gib:.2f} GB',
            f'剩余:{remaining / gib:.2f} GB',
        ])
        payload = urlencode({'chat_id': chat_id, 'text': message}).encode('utf-8')
        request = Request(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )
        with urlopen(request, timeout=15) as response:
            response.read()
        print(f'subscription notification sent: user={username}', flush=True)
    except Exception as exc:
        print(f'subscription notification failed: {exc}', flush=True)


def schedule_subscription_notification(username, path, user_agent, client_ip):
    if not PROXY_NOTIFY_ENABLED or user_agent.startswith('mmwx-fix-diagnostic/'):
        return
    match = TOKEN_RE.match(path)
    code = match.group(1) if match else path
    key = (username, code, client_ip, user_agent)
    now = time.monotonic()
    with NOTIFY_LOCK:
        expired = [
            item for item, timestamp in NOTIFY_RECENT.items()
            if now - timestamp >= NOTIFY_DEDUPE_SECONDS
        ]
        for item in expired:
            NOTIFY_RECENT.pop(item, None)
        previous = NOTIFY_RECENT.get(key)
        if previous is not None and now - previous < NOTIFY_DEDUPE_SECONDS:
            print(f'subscription notification deduplicated: user={username}', flush=True)
            return
        NOTIFY_RECENT[key] = now
    threading.Thread(
        target=send_subscription_notification,
        args=(username, path, user_agent, client_ip),
        daemon=True,
    ).start()


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
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() != 'host'
        }
        headers['Host'] = '127.0.0.1:12889'
        headers['Accept-Encoding'] = 'identity'
        response = fetch_backend(self.path, headers)
        body = response.read()
        status = response.status
        reason = response.reason
        response_headers = response.headers

        try:
            fallback = maybe_clash_to_loon_fallback(self.path, headers, response, body)
        except Exception as exc:
            fallback = None
            print('clash-to-loon fallback failed:', exc, flush=True)
        fallback_applied = bool(fallback)
        if fallback_applied:
            body, response_headers = fallback
            status = 200
            reason = 'OK'
            print('clash-to-loon fallback applied:', self.path, flush=True)

        username = find_user(self.path) or package_assignment(self.path)
        self.send_response(status, reason)
        for key, value in response_headers.items():
            lower_key = key.lower()
            if lower_key in HOP_BY_HOP or lower_key in {
                'content-length',
                'subscription-userinfo',
            }:
                continue
            if fallback_applied and lower_key in {
                'content-type',
                'content-disposition',
                'profile-update-interval',
            }:
                continue
            self.send_header(key, value)
        if fallback_applied:
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="package-loon.conf"')
            self.send_header('Profile-Update-Interval', '24')
        subinfo = response_headers.get('Subscription-Userinfo')
        if subinfo:
            try:
                subinfo = rewrite_subinfo(subinfo, username)
            except Exception as exc:
                print('rewrite failed:', exc, flush=True)
            self.send_header('Subscription-Userinfo', subinfo)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if not head_only and status == 200 and username:
            client_ip = (
                self.headers.get('X-Real-IP')
                or self.headers.get('X-Forwarded-For', '').split(',', 1)[0].strip()
                or self.client_address[0]
            )
            schedule_subscription_notification(
                username,
                self.path,
                self.headers.get('User-Agent', ''),
                client_ip,
            )
        if not head_only:
            self.wfile.write(body)
        response.close()


if __name__ == '__main__':
    ThreadingHTTPServer((LISTEN, PORT), Handler).serve_forever()
