import importlib.util
import json
import pathlib
import socket
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('link_checker', ROOT / 'check.py')
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class LinkCheckerTests(unittest.TestCase):
    def test_build_output_preserves_extended_fields_and_groups(self):
        groups = [{
            'class_name': 'Friends',
            'class_desc': 'Original group',
            'custom_group_field': {'keep': True},
            'link_list': [{
                'name': 'Example',
                'link': 'https://example.com',
                'avatar': 'https://example.com/avatar.png',
                'descr': 'Example link',
                'friendslink': 'https://example.com/links',
                'feeds': 'https://example.com/feed.xml',
                'topimg': 'https://example.com/shot.png',
                'tags': ['tech'],
                'custom_link_field': 'keep',
            }],
        }]
        checked = '2026-08-16T00:00:00Z'
        output = CHECKER.build_output(groups, {
            (0, 0): CHECKER.result('正常', 'manual', checked, manual_override=True),
        })

        self.assertEqual(output[0]['class_name'], 'Friends')
        self.assertEqual(output[0]['custom_group_field'], {'keep': True})
        entry = output[0]['link_list'][0]
        self.assertEqual(entry['friendslink'], 'https://example.com/links')
        self.assertEqual(entry['feeds'], 'https://example.com/feed.xml')
        self.assertEqual(entry['topimg'], 'https://example.com/shot.png')
        self.assertEqual(entry['tags'], ['tech'])
        self.assertEqual(entry['custom_link_field'], 'keep')
        self.assertEqual(entry['status'], '正常')
        self.assertTrue(entry['manualOverride'])

    def test_status_ledger_does_not_skip_network_check_and_resets_failure_count(self):
        groups = [{'class_name': 'Friends', 'link_list': [{
            'name': 'Example', 'link': 'https://example.com', 'avatar': '', 'descr': '',
        }]}]
        ledger = {'https://example.com/': {
            'status': '不可访问',
            'consecutiveSuccesses': 0,
            'consecutiveFailures': 2,
        }}

        results = CHECKER.check_entries(
            groups,
            requester=Requester(204),
            resolver=public_resolver,
            checked_at='2026-08-16T00:00:00Z',
        )
        updated = CHECKER.update_status_ledger(ledger, results, groups)

        self.assertEqual(results[(0, 0)]['status'], '正常')
        self.assertEqual(results[(0, 0)]['method'], 'direct-head')
        self.assertFalse(results[(0, 0)]['manualOverride'])
        self.assertEqual(updated['https://example.com/']['consecutiveSuccesses'], 1)
        self.assertEqual(updated['https://example.com/']['consecutiveFailures'], 0)

    def test_three_active_failures_move_full_entry_to_quarantine(self):
        entry = {'name': 'Down', 'link': 'https://down.example', 'custom': {'keep': True}}
        groups = [{'class_name': 'Friends', 'link_list': [entry]}]
        ledger = {'https://down.example/': {
            'status': '不可访问', 'consecutiveSuccesses': 0, 'consecutiveFailures': 2,
        }}
        results = {(0, 0): CHECKER.result('不可访问', 'direct', '2026-08-16T00:00:00Z')}

        updated = CHECKER.update_status_ledger(ledger, results, groups)
        active, quarantined = CHECKER.apply_quarantine(groups, [], updated)

        self.assertEqual(active[0]['link_list'], [])
        self.assertEqual(quarantined, [{
            'entry': entry,
            'originalGroup': 'Friends',
            'originalIndex': 0,
        }])
        self.assertEqual(updated['https://down.example/']['consecutiveFailures'], 3)

    def test_third_active_failure_stays_in_current_check_links_output_after_migration(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = pathlib.Path(temporary_directory)
            yaml_path = directory / 'link.yml'
            quarantine_path = directory / 'link-false.yml'
            ledger_path = directory / 'manual_check.json'
            output_path = directory / 'check_links.json'
            fcircle_output_path = directory / 'friend.json'
            yaml_path.write_text(yaml.safe_dump([{
                'class_name': 'Friends',
                'link_list': [{'name': 'Down', 'link': 'https://down.example'}],
            }], allow_unicode=True, sort_keys=False), encoding='utf-8')
            quarantine_path.write_text('[]\n', encoding='utf-8')
            ledger_path.write_text(json.dumps({'https://down.example/': {
                'status': '不可访问', 'consecutiveSuccesses': 0, 'consecutiveFailures': 2,
            }}), encoding='utf-8')

            with patch.dict('os.environ', {'FCIRCLE_OUTPUT_PATH': str(fcircle_output_path)}), patch.multiple(
                CHECKER,
                YAML_FILE=str(yaml_path),
                QUARANTINE_FILE=str(quarantine_path),
                STATUS_LEDGER_FILE=str(ledger_path),
                OUTPUT_PATH=str(output_path),
            ), patch.object(CHECKER, 'check_entries', side_effect=[{
                (0, 0): CHECKER.result('不可访问', 'direct', '2026-08-16T00:00:00Z'),
            }, {}]):
                CHECKER.main()

            output = json.loads(output_path.read_text(encoding='utf-8'))

        self.assertEqual(output[0]['link_list'][0]['status'], '不可访问')

    def test_first_run_migrates_legacy_ledger_string_and_cleans_stale_records(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = pathlib.Path(temporary_directory)
            yaml_path = directory / 'link.yml'
            quarantine_path = directory / 'link-false.yml'
            ledger_path = directory / 'manual_check.json'
            output_path = directory / 'check_links.json'
            fcircle_output_path = directory / 'friend.json'
            yaml_path.write_text(yaml.safe_dump([{
                'class_name': 'Friends',
                'link_list': [{'name': 'Example', 'link': 'https://example.com'}],
            }], allow_unicode=True, sort_keys=False), encoding='utf-8')
            quarantine_path.write_text('[]\n', encoding='utf-8')
            ledger_path.write_text(json.dumps({
                'https://example.com': '不可访问',
                'https://stale.example': {'status': '正常', 'consecutiveSuccesses': 2},
            }), encoding='utf-8')

            with patch.dict('os.environ', {'FCIRCLE_OUTPUT_PATH': str(fcircle_output_path)}), patch.multiple(
                CHECKER,
                YAML_FILE=str(yaml_path),
                QUARANTINE_FILE=str(quarantine_path),
                STATUS_LEDGER_FILE=str(ledger_path),
                OUTPUT_PATH=str(output_path),
            ), patch.object(CHECKER, 'check_entries', side_effect=[{
                (0, 0): CHECKER.result('正常', 'direct-head', '2026-08-16T00:00:00Z', http_status=204),
            }, {}]):
                CHECKER.main()

            ledger = json.loads(ledger_path.read_text(encoding='utf-8'))

        self.assertEqual(set(ledger), {'https://example.com/'})
        self.assertEqual(ledger['https://example.com/'], {
            'status': '正常',
            'consecutiveSuccesses': 1,
            'consecutiveFailures': 0,
            'checkedAt': '2026-08-16T00:00:00Z',
            'method': 'direct-head',
            'httpStatus': 204,
            'failureReason': None,
        })

    def test_three_quarantined_successes_restore_at_original_index(self):
        first = {'name': 'First', 'link': 'https://first.example'}
        restored = {'name': 'Recovered', 'link': 'https://recovered.example'}
        last = {'name': 'Last', 'link': 'https://last.example'}
        groups = [{'class_name': 'Friends', 'link_list': [first, last]}]
        quarantined = [{'entry': restored, 'originalGroup': 'Friends', 'originalIndex': 1}]
        ledger = {'https://recovered.example/': {
            'status': '正常', 'consecutiveSuccesses': 2, 'consecutiveFailures': 0,
        }}
        results = {(0, 0): CHECKER.result('正常', 'direct', '2026-08-16T00:00:00Z')}

        updated = CHECKER.update_status_ledger(ledger, results, CHECKER.quarantine_groups(quarantined))
        active, remaining = CHECKER.apply_restorations(groups, quarantined, updated)

        self.assertEqual(active[0]['link_list'], [first, restored, last])
        self.assertEqual(remaining, [])
        self.assertEqual(updated['https://recovered.example/']['consecutiveSuccesses'], 3)

    def test_recovered_entry_uses_quarantine_result_in_current_outputs(self):
        restored = {
            'name': 'Recovered',
            'link': 'https://recovered.example',
            'friendslink': 'https://recovered.example/friends',
            'avatar': 'https://recovered.example/avatar.png',
        }
        groups = [{'class_name': 'Friends', 'link_list': []}]
        quarantined = [{'entry': restored, 'originalGroup': 'Friends', 'originalIndex': 0}]
        checked = '2026-08-16T00:00:00Z'
        results = {(0, 0): CHECKER.result('正常', 'direct-head', checked, http_status=204)}
        ledger = {'https://recovered.example/': {
            'status': '正常', 'consecutiveSuccesses': 2, 'consecutiveFailures': 0,
        }}
        updated = CHECKER.update_status_ledger(ledger, results, CHECKER.quarantine_groups(quarantined))
        active, remaining = CHECKER.apply_restorations(groups, quarantined, updated)
        output_results = CHECKER.merge_results_for_output(active, groups, {}, quarantined, results)

        self.assertEqual(remaining, [])
        self.assertEqual(CHECKER.build_output(active, output_results)[0]['link_list'][0]['status'], '正常')
        self.assertEqual(CHECKER.build_fcircle_output(active, output_results)[0], {'friends': [[
            'Recovered',
            'https://recovered.example',
            'https://recovered.example/friends',
            'https://recovered.example/avatar.png',
        ]]})

    def test_out_of_range_restore_index_appends_to_group(self):
        first = {'name': 'First', 'link': 'https://first.example'}
        restored = {'name': 'Recovered', 'link': 'https://recovered.example'}
        groups = [{'class_name': 'Friends', 'link_list': [first]}]
        ledger = {'https://recovered.example/': {
            'status': '正常', 'consecutiveSuccesses': 3, 'consecutiveFailures': 0,
        }}

        active, remaining = CHECKER.apply_restorations(groups, [{
            'entry': restored, 'originalGroup': 'Friends', 'originalIndex': 9,
        }], ledger)

        self.assertEqual(active[0]['link_list'], [first, restored])
        self.assertEqual(remaining, [])

    def test_missing_quarantine_file_loads_as_empty_and_legacy_ledger_is_migrated(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / 'missing.yml'
            self.assertEqual(CHECKER.load_quarantine_entries(path), [])

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / 'ledger.json'
            path.write_text(json.dumps({'https://example.com': '不可访问'}), encoding='utf-8')
            ledger = CHECKER.load_status_ledger(path)

        self.assertEqual(ledger, {})

    def test_ledger_uses_the_matching_url_across_multiple_groups(self):
        groups = [
            {'class_name': 'First', 'link_list': [{'link': 'https://first.example'}]},
            {'class_name': 'Second', 'link_list': [{'link': 'https://second.example'}]},
        ]
        results = {
            (0, 0): CHECKER.result('正常', 'direct', '2026-08-16T00:00:00Z'),
            (1, 0): CHECKER.result('不可访问', 'direct', '2026-08-16T00:00:00Z'),
        }

        ledger = CHECKER.update_status_ledger({}, results, groups)

        self.assertEqual(ledger['https://first.example/']['status'], '正常')
        self.assertEqual(ledger['https://second.example/']['status'], '不可访问')

    def test_duplicate_urls_count_once_per_run_and_only_first_is_quarantined(self):
        first = {'name': 'First', 'link': 'https://duplicate.example'}
        second = {'name': 'Second', 'link': 'https://duplicate.example/'}
        groups = [{'class_name': 'Friends', 'link_list': [first, second]}]
        ledger = {'https://duplicate.example/': {
            'status': '不可访问', 'consecutiveSuccesses': 0, 'consecutiveFailures': 2,
        }}
        results = {
            (0, 0): CHECKER.result('不可访问', 'direct', '2026-08-16T00:00:00Z'),
            (0, 1): CHECKER.result('不可访问', 'direct', '2026-08-16T00:00:00Z'),
        }

        updated = CHECKER.update_status_ledger(ledger, results, groups)
        active, quarantined = CHECKER.apply_quarantine(groups, [], updated)

        self.assertEqual(updated['https://duplicate.example/']['consecutiveFailures'], 3)
        self.assertEqual(active[0]['link_list'], [second])
        self.assertEqual([item['entry'] for item in quarantined], [first])

    def test_missing_original_group_keeps_recovered_link_quarantined(self):
        entry = {'name': 'Recovered', 'link': 'https://recovered.example'}
        ledger = {'https://recovered.example/': {
            'status': '正常', 'consecutiveSuccesses': 3, 'consecutiveFailures': 0,
        }}

        active, remaining = CHECKER.apply_restorations([], [{
            'entry': entry, 'originalGroup': 'Removed group', 'originalIndex': 0,
        }], ledger)

        self.assertEqual(active, [])
        self.assertEqual(remaining, [{
            'entry': entry, 'originalGroup': 'Removed group', 'originalIndex': 0,
        }])

    def test_safe_public_url_rejects_local_and_private_targets(self):
        for value in ['http://localhost/', 'http://127.0.0.1/', 'http://10.0.0.1/', 'ftp://example.com/']:
            safe, _, _ = CHECKER.is_safe_public_url(value, public_resolver)
            self.assertFalse(safe, value)

        safe, normalized, reason = CHECKER.is_safe_public_url('https://example.com', public_resolver)
        self.assertTrue(safe, reason)
        self.assertEqual(normalized, 'https://example.com/')

    def test_private_dns_resolution_is_rejected_before_request(self):
        safe, _, reason = CHECKER.is_safe_public_url('https://example.test', private_resolver)
        self.assertFalse(safe)
        self.assertIn('非公开', reason)

    def test_direct_check_accepts_successful_non_200_response(self):
        checked = '2026-08-16T00:00:00Z'
        response = CHECKER.direct_check('https://example.com/', checked, Requester(204))
        self.assertEqual(response['status'], '正常')
        self.assertEqual(response['httpStatus'], 204)
        self.assertEqual(response['method'], 'direct-head')

    def test_build_fcircle_output_keeps_only_healthy_complete_links_in_source_order(self):
        groups = [
            {'class_name': 'First', 'link_list': [
                {'name': 'Healthy first', 'link': 'https://first.example', 'friendslink': 'https://first.example/friends', 'avatar': 'https://first.example/avatar.png'},
                {'name': 'Unhealthy', 'link': 'https://down.example', 'friendslink': 'https://down.example/friends', 'avatar': 'https://down.example/avatar.png'},
            ]},
            {'class_name': 'Second', 'link_list': [
                {'name': 'Missing friend page', 'link': 'https://missing.example', 'avatar': 'https://missing.example/avatar.png'},
                {'name': 'Healthy second', 'link': 'https://second.example', 'friendslink': 'https://second.example/friends', 'avatar': 'https://second.example/avatar.png'},
            ]},
        ]
        results = {
            (0, 0): CHECKER.result('正常', 'manual', '2026-08-16T00:00:00Z'),
            (0, 1): CHECKER.result('不可访问', 'direct', '2026-08-16T00:00:00Z'),
            (1, 0): CHECKER.result('正常', 'manual', '2026-08-16T00:00:00Z'),
            (1, 1): CHECKER.result('正常', 'manual', '2026-08-16T00:00:00Z'),
        }

        output, skipped = CHECKER.build_fcircle_output(groups, results)

        self.assertEqual(output, {'friends': [
            ['Healthy first', 'https://first.example', 'https://first.example/friends', 'https://first.example/avatar.png'],
            ['Healthy second', 'https://second.example', 'https://second.example/friends', 'https://second.example/avatar.png'],
        ]})
        self.assertEqual(skipped, 1)


def public_resolver(hostname, port, type):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))]


def private_resolver(hostname, port, type):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 0))]


class Response:
    def __init__(self, status_code):
        self.status_code = status_code

    def close(self):
        pass


class Requester:
    def __init__(self, head_status):
        self.head_status = head_status

    def head(self, *args, **kwargs):
        return Response(self.head_status)

    def get(self, *args, **kwargs):
        raise AssertionError('GET should not run when HEAD is successful')


if __name__ == '__main__':
    unittest.main()
