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
QUARANTINE_FILE = os.getenv('QUARANTINE_FILE', 'link-false.yml')
OUTPUT_PATH = os.getenv('OUTPUT_PATH', os.path.join('public', 'check_links.json'))
STATUS_LEDGER_FILE = os.getenv('MANUAL_CHECK_FILE', 'manual_check.json')
CF_WORKER_URL = os.getenv('CF_WORKER_URL', '')
CF_WORKER_TOKEN = os.getenv('CF_WORKER_TOKEN', '')
MAX_WORKERS = int(os.getenv('CHECK_WORKERS', '10'))
DIRECT_TIMEOUT_SECONDS = 10
FALLBACK_TIMEOUT_SECONDS = 30
FAILURE_THRESHOLD = 3
SUCCESS_THRESHOLD = 3

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


def load_status_ledger(path):
    if not os.path.exists(path):
        return {}

    with open(path, 'r', encoding='utf-8') as file:
        raw = json.load(file)
    if not isinstance(raw, dict):
        raise ValueError('manual_check.json 顶层必须是 URL 到状态记录的对象')

    ledger = {}
    for url, record in raw.items():
        normalized = normalize_url(url)
        if not normalized or not isinstance(record, dict):
            continue
        status = record.get('status')
        if status not in {'正常', '不可访问'}:
            continue
        ledger[normalized] = {
            'status': status,
            'consecutiveSuccesses': max(0, int(record.get('consecutiveSuccesses', 0))),
            'consecutiveFailures': max(0, int(record.get('consecutiveFailures', 0))),
            'checkedAt': record.get('checkedAt'),
            'method': record.get('method'),
            'httpStatus': record.get('httpStatus'),
            'failureReason': record.get('failureReason'),
        }
    return ledger


def write_status_ledger(path, ledger):
    write_json(path, ledger)


def load_quarantine_entries(path):
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as file:
        entries = yaml.safe_load(file) or []
    if not isinstance(entries, list):
        raise ValueError('link-false.yml 顶层必须是隔离记录数组')
    return [entry for entry in entries if isinstance(entry, dict)]


def write_quarantine_entries(path, entries):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as file:
        yaml.safe_dump(entries, file, allow_unicode=True, sort_keys=False)


def write_groups(path, groups):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as file:
        yaml.safe_dump(groups, file, allow_unicode=True, sort_keys=False)


def write_json(path, payload):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write('\n')


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


def check_entries(groups, requester=requests, resolver=socket.getaddrinfo, checked_at=None):
    checked_at = checked_at or checked_at_now()
    pending = [(group_index, link_index, entry.get('link')) for group_index, link_index, entry in collect_entries(groups)]

    def run(item):
        group_index, link_index, url = item
        _, check_result = check_link(url, checked_at, requester, resolver)
        return (group_index, link_index), check_result

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        return dict(executor.map(run, pending)) if pending else {}


def quarantine_groups(entries):
    return [{'link_list': [item.get('entry') for item in entries]}]


def check_quarantine_entries(entries, requester=requests, resolver=socket.getaddrinfo, checked_at=None):
    return check_entries(quarantine_groups(entries), requester, resolver, checked_at)


def result_urls(groups, results):
    entries = {(group_index, link_index): entry for group_index, link_index, entry in collect_entries(groups)}
    return {
        key: normalize_url(entries.get(key, {}).get('link'))
        for key in results
    }


def update_status_ledger(ledger, results, source):
    updated = copy.deepcopy(ledger)
    urls = result_urls(source, results)
    updated_urls = set()
    for index, check_result in results.items():
        url = urls.get(index)
        if not url or url in updated_urls:
            continue
        updated_urls.add(url)
        previous = updated.get(url, {})
        status = check_result['status']
        updated[url] = {
            'status': status,
            'consecutiveSuccesses': previous.get('consecutiveSuccesses', 0) + 1 if status == '正常' else 0,
            'consecutiveFailures': previous.get('consecutiveFailures', 0) + 1 if status == '不可访问' else 0,
            'checkedAt': check_result['checkedAt'],
            'method': check_result['method'],
            'httpStatus': check_result['httpStatus'],
            'failureReason': check_result['failureReason'],
        }
    return updated


def apply_quarantine(groups, quarantined, ledger):
    active = copy.deepcopy(groups)
    quarantine = copy.deepcopy(quarantined)
    quarantined_urls = {normalize_url(item.get('entry', {}).get('link')) for item in quarantine}
    for group in active:
        if not isinstance(group, dict) or not isinstance(group.get('link_list'), list):
            continue
        kept = []
        for index, entry in enumerate(group['link_list']):
            url = normalize_url(entry.get('link')) if isinstance(entry, dict) else None
            if url and ledger.get(url, {}).get('consecutiveFailures', 0) >= FAILURE_THRESHOLD and url not in quarantined_urls:
                quarantine.append({'entry': entry, 'originalGroup': group.get('class_name'), 'originalIndex': index})
                quarantined_urls.add(url)
            else:
                kept.append(entry)
        group['link_list'] = kept
    return active, quarantine


def apply_restorations(groups, quarantined, ledger):
    active = copy.deepcopy(groups)
    remaining = []
    groups_by_name = {group.get('class_name'): group for group in active if isinstance(group, dict) and isinstance(group.get('link_list'), list)}
    active_urls = {normalize_url(entry.get('link')) for _, _, entry in collect_entries(active)}
    for item in quarantined:
        entry = item.get('entry') if isinstance(item, dict) else None
        url = normalize_url(entry.get('link')) if isinstance(entry, dict) else None
        group = groups_by_name.get(item.get('originalGroup')) if isinstance(item, dict) else None
        if url and ledger.get(url, {}).get('consecutiveSuccesses', 0) >= SUCCESS_THRESHOLD and group and url not in active_urls:
            index = item.get('originalIndex')
            if not isinstance(index, int) or index < 0 or index >= len(group['link_list']):
                group['link_list'].append(entry)
            else:
                group['link_list'].insert(index, entry)
            active_urls.add(url)
        else:
            remaining.append(item)
    return active, remaining


def active_ledger_urls(groups, quarantined):
    urls = {normalize_url(entry.get('link')) for _, _, entry in collect_entries(groups)}
    urls.update(normalize_url(item.get('entry', {}).get('link')) for item in quarantined if isinstance(item, dict))
    return {url for url in urls if url}


def merge_results_for_output(groups, active_groups, active_results, quarantined, quarantine_results):
    results_by_url = {}
    for source, results in ((active_groups, active_results), (quarantine_groups(quarantined), quarantine_results)):
        for index, url in result_urls(source, results).items():
            if url and url not in results_by_url:
                results_by_url[url] = results[index]
    return {
        (group_index, link_index): results_by_url[url]
        for group_index, link_index, entry in collect_entries(groups)
        for url in [normalize_url(entry.get('link'))]
        if url in results_by_url
    }


def build_output(groups, results):
    output = copy.deepcopy(groups)
    for group_index, group in enumerate(output):
        if not isinstance(group, dict) or not isinstance(group.get('link_list'), list):
            continue
        for link_index, entry in enumerate(group['link_list']):
            if not isinstance(entry, dict):
                continue
            entry.update(results.get((group_index, link_index), result('不可访问', 'validation', checked_at_now(), failure_reason='友链记录格式无效')))
    return output


def build_fcircle_output(groups, results):
    friends = []
    skipped_missing_fields = 0
    for group_index, link_index, entry in collect_entries(groups):
        check_result = results.get((group_index, link_index))
        if not check_result or check_result.get('status') != '正常':
            continue
        values = [entry.get('name'), entry.get('link'), entry.get('friendslink'), entry.get('avatar')]
        if not all(isinstance(value, str) and value.strip() for value in values):
            skipped_missing_fields += 1
            continue
        friends.append([value.strip() for value in values])
    return {'friends': friends}, skipped_missing_fields


def main():
    with open(YAML_FILE, 'r', encoding='utf-8') as file:
        groups = yaml.safe_load(file) or []
    if not isinstance(groups, list):
        raise ValueError('link.yml 顶层必须是分组数组')

    quarantined = load_quarantine_entries(QUARANTINE_FILE)
    ledger = load_status_ledger(STATUS_LEDGER_FILE)
    checked_groups = copy.deepcopy(groups)
    checked_quarantined = copy.deepcopy(quarantined)
    checked_at = checked_at_now()
    active_results = check_entries(groups, checked_at=checked_at)
    quarantine_results = check_quarantine_entries(quarantined, checked_at=checked_at)
    ledger = update_status_ledger(ledger, active_results, groups)
    ledger = update_status_ledger(ledger, quarantine_results, quarantine_groups(quarantined))
    groups, quarantined = apply_quarantine(groups, quarantined, ledger)
    groups, quarantined = apply_restorations(groups, quarantined, ledger)
    ledger = {url: record for url, record in ledger.items() if url in active_ledger_urls(groups, quarantined)}

    output_results = merge_results_for_output(checked_groups, checked_groups, active_results, checked_quarantined, quarantine_results)
    output = build_output(checked_groups, output_results)
    fcircle_output, skipped_missing_fields = build_fcircle_output(groups, output_results)
    fcircle_output_path = os.getenv('FCIRCLE_OUTPUT_PATH', os.path.join('public', 'friend.json'))
    write_groups(YAML_FILE, groups)
    write_quarantine_entries(QUARANTINE_FILE, quarantined)
    write_status_ledger(STATUS_LEDGER_FILE, ledger)
    write_json(OUTPUT_PATH, output)
    write_json(fcircle_output_path, fcircle_output)

    all_results = list(active_results.values()) + list(quarantine_results.values())
    normal_count = sum(item['status'] == '正常' for item in all_results)
    print(f'检测结果已写入: {OUTPUT_PATH}')
    print(f'总计: {len(all_results)}，正常: {normal_count}，异常: {len(all_results) - normal_count}')
    print(f'fcircle 导出: {len(fcircle_output["friends"])}，缺少必填字段跳过: {skipped_missing_fields}')


if __name__ == '__main__':
    main()
