import concurrent.futures
import copy
import ipaddress
import json
import os
import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import requests
import yaml

YAML_FILE = os.getenv('YAML_FILE', 'link.yml')
OUTPUT_PATH = os.getenv('OUTPUT_PATH', os.path.join('public', 'check_links.json'))
MANUAL_CHECK_FILE = os.getenv('MANUAL_CHECK_FILE', 'manual_check.json')
CF_WORKER_URL = os.getenv('CF_WORKER_URL', '')
CF_WORKER_TOKEN = os.getenv('CF_WORKER_TOKEN', '')
MAX_WORKERS = int(os.getenv('CHECK_WORKERS', '10'))
DIRECT_TIMEOUT_SECONDS = 10
FALLBACK_TIMEOUT_SECONDS = 30

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)


def checked_at_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def normalize_url(value):
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    try:
        parts = urlsplit(raw)
    except ValueError:
        return None

    if parts.scheme.lower() not in {'http', 'https'} or not parts.hostname:
        return None

    hostname = parts.hostname.lower().rstrip('.')
    try:
        port = parts.port
    except ValueError:
        return None

    netloc = hostname
    if ':' in hostname and not hostname.startswith('['):
        netloc = f'[{hostname}]'
    if port is not None:
        netloc = f'{netloc}:{port}'

    path = parts.path or '/'
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ''))


def is_public_ip(address):
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def is_safe_public_url(value, resolver=socket.getaddrinfo):
    normalized = normalize_url(value)
    if not normalized:
        return False, None, 'URL 必须为包含主机名的 http 或 https 地址'

    hostname = urlsplit(normalized).hostname
    if hostname in {'localhost', 'localhost.localdomain'} or hostname.endswith('.localhost'):
        return False, normalized, '不允许检测本地地址'

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if literal_ip.is_global:
            return True, normalized, None
        return False, normalized, '不允许检测非公开 IP 地址'

    try:
        resolved = resolver(hostname, None, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror):
        return False, normalized, '域名无法解析为公开 IP 地址'

    addresses = {item[4][0] for item in resolved if item and item[4]}
    if not addresses:
        return False, normalized, '域名无法解析为公开 IP 地址'
    if not all(is_public_ip(address) for address in addresses):
        return False, normalized, '域名解析到了非公开 IP 地址'

    return True, normalized, None


def successful_http_status(status_code):
    return 200 <= status_code < 400


def result(status, method, checked_at, http_status=None, failure_reason=None, manual_override=False):
    return {
        'status': status,
        'checkedAt': checked_at,
        'method': method,
        'httpStatus': http_status,
        'failureReason': failure_reason,
        'manualOverride': manual_override,
    }


def direct_check(url, checked_at, requester=requests):
    headers = {'User-Agent': USER_AGENT}
    try:
        response = requester.head(url, headers=headers, timeout=DIRECT_TIMEOUT_SECONDS, allow_redirects=False)
        if successful_http_status(response.status_code):
            return result('正常', 'direct-head', checked_at, http_status=response.status_code)

        response = requester.get(url, headers=headers, timeout=DIRECT_TIMEOUT_SECONDS, allow_redirects=False, stream=True)
        try:
            if successful_http_status(response.status_code):
                return result('正常', 'direct-get', checked_at, http_status=response.status_code)
            return result('不可访问', 'direct-get', checked_at, http_status=response.status_code, failure_reason=f'HTTP {response.status_code}')
        finally:
            response.close()
    except requests.RequestException as error:
        return result('不可访问', 'direct', checked_at, failure_reason=str(error))


def api_check(url, checked_at, requester=requests):
    headers = {'User-Agent': USER_AGENT}
    try:
        response = requester.get(
            'https://v2.xxapi.cn/api/status',
            params={'url': url},
            headers=headers,
            timeout=FALLBACK_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get('code', 0)) == 200 and int(payload.get('data', 0)) == 200:
            return result('正常', 'third-party-api', checked_at, http_status=200)
        return result('不可访问', 'third-party-api', checked_at, failure_reason='第三方 API 未确认可访问')
    except (requests.RequestException, TypeError, ValueError) as error:
        return result('不可访问', 'third-party-api', checked_at, failure_reason=str(error))


def worker_check(url, checked_at, worker_url=CF_WORKER_URL, worker_token=CF_WORKER_TOKEN, requester=requests):
    if not worker_url or not worker_token:
        return result('不可访问', 'cloudflare-worker', checked_at, failure_reason='Cloudflare Worker 未配置')

    try:
        response = requester.get(
            worker_url,
            params={'url': url},
            headers={'Authorization': f'Bearer {worker_token}'},
            timeout=DIRECT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get('status') == 'up':
            return result('正常', 'cloudflare-worker', checked_at, http_status=200)
        return result('不可访问', 'cloudflare-worker', checked_at, failure_reason=payload.get('error') or 'Worker 未确认可访问')
    except (requests.RequestException, ValueError) as error:
        return result('不可访问', 'cloudflare-worker', checked_at, failure_reason=str(error))


def check_link(url, checked_at, requester=requests, resolver=socket.getaddrinfo):
    safe, normalized, reason = is_safe_public_url(url, resolver)
    if not safe:
        return normalized, result('不可访问', 'validation', checked_at, failure_reason=reason)

    direct = direct_check(normalized, checked_at, requester)
    if direct['status'] == '正常':
        return normalized, direct

    fallback = api_check(normalized, checked_at, requester)
    if fallback['status'] == '正常':
        return normalized, fallback

    worker = worker_check(normalized, checked_at, requester=requester)
    if worker['status'] == '正常':
        return normalized, worker

    worker['failureReason'] = worker['failureReason'] or fallback['failureReason'] or direct['failureReason']
    return normalized, worker


def load_manual_checks(path):
    if not os.path.exists(path):
        return {}

    with open(path, 'r', encoding='utf-8') as file:
        raw = json.load(file)

    if not isinstance(raw, dict):
        raise ValueError('manual_check.json 顶层必须是 URL 到状态的对象')

    checks = {}
    for url, status in raw.items():
        normalized = normalize_url(url)
        if normalized and isinstance(status, str) and status.strip():
            checks[normalized] = status.strip()
    return checks


def collect_entries(groups):
    entries = []
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        link_list = group.get('link_list')
        if not isinstance(link_list, list):
            continue
        for link_index, entry in enumerate(link_list):
            if isinstance(entry, dict):
                entries.append((group_index, link_index, entry))
    return entries


def check_entries(groups, manual_checks, requester=requests, resolver=socket.getaddrinfo, checked_at=None):
    checked_at = checked_at or checked_at_now()
    results = {}
    pending = []

    for group_index, link_index, entry in collect_entries(groups):
        normalized = normalize_url(entry.get('link'))
        if normalized and normalized in manual_checks:
            results[(group_index, link_index)] = result(
                manual_checks[normalized],
                'manual',
                checked_at,
                manual_override=True,
            )
        else:
            pending.append((group_index, link_index, entry.get('link')))

    def run(item):
        group_index, link_index, url = item
        _, check_result = check_link(url, checked_at, requester, resolver)
        return (group_index, link_index), check_result

    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for key, check_result in executor.map(run, pending):
                results[key] = check_result

    return results


def build_output(groups, results):
    output = copy.deepcopy(groups)
    for group_index, group in enumerate(output):
        if not isinstance(group, dict) or not isinstance(group.get('link_list'), list):
            continue
        for link_index, entry in enumerate(group['link_list']):
            if not isinstance(entry, dict):
                continue
            entry.update(results.get(
                (group_index, link_index),
                result('不可访问', 'validation', checked_at_now(), failure_reason='友链记录格式无效'),
            ))
    return output


def main():
    print(f'读取配置文件: {YAML_FILE}')
    with open(YAML_FILE, 'r', encoding='utf-8') as file:
        groups = yaml.safe_load(file) or []

    if not isinstance(groups, list):
        raise ValueError('link.yml 顶层必须是分组数组')

    manual_checks = load_manual_checks(MANUAL_CHECK_FILE)
    print(f'共发现 {len(collect_entries(groups))} 个链接，手动覆盖 {len(manual_checks)} 条')
    results = check_entries(groups, manual_checks)
    output = build_output(groups, results)

    os.makedirs(os.path.dirname(OUTPUT_PATH) or '.', exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as file:
        json.dump(output, file, ensure_ascii=False, indent=2)
        file.write('\n')

    all_results = list(results.values())
    normal_count = sum(item['status'] == '正常' for item in all_results)
    print(f'结果已写入: {OUTPUT_PATH}')
    print(f'总计: {len(all_results)}')
    print(f'正常: {normal_count}')
    print(f'异常: {len(all_results) - normal_count}')


if __name__ == '__main__':
    main()
