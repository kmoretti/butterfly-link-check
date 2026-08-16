import importlib.util
import pathlib
import socket
import unittest

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

    def test_manual_override_skips_network_check_and_uses_normalized_url(self):
        groups = [{'class_name': 'Friends', 'link_list': [{
            'name': 'Example', 'link': 'https://example.com', 'avatar': '', 'descr': '',
        }]}]
        manual_checks = {'https://example.com/': '正常'}

        results = CHECKER.check_entries(
            groups,
            manual_checks,
            requester=FailingRequester(),
            resolver=public_resolver,
            checked_at='2026-08-16T00:00:00Z',
        )

        self.assertEqual(results[(0, 0)]['status'], '正常')
        self.assertEqual(results[(0, 0)]['method'], 'manual')
        self.assertTrue(results[(0, 0)]['manualOverride'])

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


def public_resolver(hostname, port, type):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))]


def private_resolver(hostname, port, type):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 0))]


class FailingRequester:
    def head(self, *args, **kwargs):
        raise AssertionError('manual override should avoid network requests')

    def get(self, *args, **kwargs):
        raise AssertionError('manual override should avoid network requests')


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
