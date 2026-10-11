"""Release validation, download integrity, cancellation and atomic replacement."""
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

import updates


def executable(marker=b'new'):
    value = bytearray(512)
    value[:2] = b'MZ'
    struct.pack_into('<I', value, 60, 128)
    value[128:132] = b'PE\0\0'
    value[132:134] = b'\x64\x86'
    value[152:154] = b'\x0b\x02'
    return bytes(value) + marker


def release_data(version='0.6.0', content=None):
    content = content or executable()
    tag = 'v' + version
    return {'tag_name': tag, 'name': '新版本', 'body': '更新说明',
            'draft': False, 'prerelease': False, 'published_at': '2026-10-11T00:00:00Z',
            'assets': [{'name': 'AIUsage.exe', 'state': 'uploaded', 'size': len(content),
                        'browser_download_url': updates.asset_url(tag, 'AIUsage.exe'),
                        'digest': 'sha256:' + hashlib.sha256(content).hexdigest()},
                       {'name': 'SHA256SUMS.txt', 'state': 'uploaded',
                        'browser_download_url': updates.asset_url(tag, 'SHA256SUMS.txt')}]}


class UpdateTests(unittest.TestCase):
    def test_versions_compare_numerically_and_reject_prereleases(self):
        self.assertTrue(updates.newer('v0.10.0', '0.9.9'))
        self.assertFalse(updates.newer('v0.5.0', '0.5.0'))
        self.assertFalse(updates.newer('v0.4.1', '0.5.0'))
        for value in ('1.2', 'v1.2.3-rc1', '01.2.3', '1.2.3.4'):
            with self.assertRaises(updates.UpdateError):
                updates.version_tuple(value)

    def test_metadata_rejects_foreign_urls_and_incomplete_assets(self):
        data = release_data()
        for foreign in ('https://example.com/AIUsage.exe',
                        'http://github.com/baiqingyuan/AI-usage-desktop-pet/releases/download/v0.6.0/AIUsage.exe'):
            data['assets'][0]['browser_download_url'] = foreign
            with self.assertRaises(updates.UpdateError):
                updates.parse_release(data)
        for key in ('draft', 'prerelease'):
            data = release_data()
            data[key] = True
            with self.assertRaises(updates.UpdateError):
                updates.parse_release(data)
        data = release_data()
        data['assets'].pop()
        with self.assertRaises(updates.UpdateError):
            updates.parse_release(data)

    def test_redirects_reject_foreign_hosts_and_http(self):
        handler = updates.TrustedRedirect()
        request = updates.request.Request(updates.asset_url('v0.6.0', 'AIUsage.exe'))
        for url in ('http://release-assets.githubusercontent.com/file',
                    'https://githubusercontent.com.evil.example/file',
                    'https://evil.example/file', 'https://github.com:444/file'):
            with self.assertRaises(updates.UpdateError):
                handler.redirect_request(request, None, 302, '', {}, url)
        self.assertIsNotNone(handler.redirect_request(
            request, None, 302, '', {}, 'https://release-assets.githubusercontent.com/file'))

    def test_checksums_reject_missing_duplicate_and_ambiguous_entries(self):
        digest = 'a' * 64
        self.assertEqual(updates.checksum_text(digest + '  AIUsage.exe\n'), digest)
        for text in ('invalid', digest + ' other.exe', (digest + ' AIUsage.exe\n') * 2):
            with self.assertRaises(updates.UpdateError):
                updates.checksum_text(text)

    def download(self, directory, content, served=None, cancel=None):
        release = updates.parse_release(release_data(content=content))
        digest = hashlib.sha256(content).hexdigest()
        def opened(url):
            return io.BytesIO((digest + '  AIUsage.exe\n').encode() if url.endswith('.txt') else (served if served is not None else content))
        with patch.object(updates, 'open_url', side_effect=opened):
            return updates.download_release(release, directory, cancel=cancel)

    def test_download_verifies_checksum_and_pe_before_returning_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate, digest = self.download(Path(temporary), executable())
            self.assertEqual(candidate.read_bytes(), executable())
            self.assertEqual(updates.file_digest(candidate), digest)
            self.assertFalse(list(Path(temporary).glob('*.part')))

    def test_truncated_tampered_and_non_executable_downloads_do_not_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for expected, served in ((executable(), executable()[:-1]),
                                     (executable(), executable(b'bad')),
                                     (b'not-an-executable', b'not-an-executable')):
                with self.assertRaises(updates.UpdateError):
                    self.download(folder, expected, served)
                self.assertFalse(list(folder.iterdir()))

    def test_cancel_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(updates.Cancelled):
                self.download(Path(temporary), executable(), cancel=cancel)
            self.assertFalse(list(Path(temporary).iterdir()))

    def setup_plan(self, folder):
        root = Path(folder).resolve()
        target = root / '中文 挂件.exe'
        target.write_bytes(executable(b'old'))
        stage = root / '.updates'
        stage.mkdir()
        candidate = stage / 'candidate.exe'
        candidate.write_bytes(executable(b'new'))
        (root / 'settings.json').write_text('{"secret":"UNCHANGED"}', encoding='utf-8')
        (root / 'snapshot.json').write_text('{"usage":"UNCHANGED"}', encoding='utf-8')
        plan = updates.create_plan(candidate, updates.file_digest(candidate), target, '0.6.0', parent_pid=0)
        return target, stage, plan

    def test_apply_preserves_configuration_and_keeps_previous_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, stage, plan = self.setup_plan(temporary)
            with patch.object(updates, 'restart') as restart:
                self.assertTrue(updates.apply_plan(plan))
                restart.assert_called_once_with(target, [])
            self.assertEqual(target.read_bytes(), executable(b'new'))
            self.assertEqual((stage/'previous.exe').read_bytes(), executable(b'old'))
            self.assertEqual((target.parent/'settings.json').read_text(), '{"secret":"UNCHANGED"}')
            self.assertEqual((target.parent/'snapshot.json').read_text(), '{"usage":"UNCHANGED"}')

    def test_locked_target_retries_instead_of_overwriting_or_deleting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, stage, plan = self.setup_plan(temporary)
            original_replace = updates.os.replace
            count = 0
            def replace(source, destination):
                nonlocal count
                if Path(destination) == target and count == 0:
                    count += 1
                    raise PermissionError('busy')
                return original_replace(source, destination)
            with patch.object(updates.os, 'replace', side_effect=replace), patch.object(updates.time, 'sleep'):
                self.assertTrue(updates.apply_plan(plan, relaunch=False))
            self.assertEqual(count, 1)
            self.assertEqual(target.read_bytes(), executable(b'new'))

    def test_failed_launch_restores_original_and_restarts_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, stage, plan = self.setup_plan(temporary)
            with patch.object(updates, 'restart', side_effect=[OSError('failed'), Mock()]) as restart:
                self.assertFalse(updates.apply_plan(plan))
            self.assertEqual(restart.call_count, 2)
            self.assertEqual(target.read_bytes(), executable(b'old'))
            self.assertEqual(json.loads((stage/'result.json').read_text())['status'], 'failed')

    def test_invalid_hash_or_outside_candidate_never_touches_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, stage, plan = self.setup_plan(temporary)
            value = json.loads(plan.read_text())
            value['sha256'] = '0' * 64
            updates.write_json(plan, value)
            with self.assertRaises(updates.UpdateError):
                updates.apply_plan(plan, relaunch=False)
            self.assertEqual(target.read_bytes(), executable(b'old'))
            outside = target.parent/'outside.exe'
            outside.write_bytes(executable())
            with self.assertRaises(updates.UpdateError):
                updates.create_plan(outside, updates.file_digest(outside), target, '0.6.0')

    def test_ready_acknowledgement_requires_matching_nonce_and_live_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, stage, plan = self.setup_plan(temporary)
            nonce = json.loads(plan.read_text())['nonce']
            ready = stage / ('ready-' + nonce + '.json')
            self.assertFalse(updates.helper_ready(plan, ready))
            with patch.object(updates, 'parent_alive', return_value=True):
                updates.write_json(ready, {'nonce': 'bad', 'pid': 123})
                self.assertFalse(updates.helper_ready(plan, ready))
                updates.write_json(ready, {'nonce': nonce, 'pid': 0})
                self.assertFalse(updates.helper_ready(plan, ready))
                ready.write_text('{incomplete', encoding='utf-8')
                self.assertFalse(updates.helper_ready(plan, ready))
                updates.write_json(ready, {'nonce': nonce, 'pid': 123})
                self.assertTrue(updates.helper_ready(plan, ready))
            with patch.object(updates, 'parent_alive', return_value=False):
                self.assertFalse(updates.helper_ready(plan, ready))

    def test_helper_waits_for_old_process_to_exit_before_replacing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, stage, plan = self.setup_plan(temporary)
            nonce = json.loads(plan.read_text())['nonce']
            def waiting(_):
                self.assertTrue((stage / ('ready-' + nonce + '.json')).exists())
                self.assertEqual(target.read_bytes(), executable(b'old'))
                self.assertFalse((stage / 'previous.exe').exists())
            with patch.object(updates, 'parent_alive', side_effect=[True, False]), \
                    patch.object(updates.time, 'sleep', side_effect=waiting):
                self.assertTrue(updates.apply_plan(plan, relaunch=False))
            self.assertEqual(target.read_bytes(), executable(b'new'))

    def test_cancelled_handoff_keeps_old_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, stage, plan = self.setup_plan(temporary)
            value = json.loads(plan.read_text())
            (stage/('cancel-' + value['nonce'] + '.json')).write_text('{}')
            self.assertFalse(updates.apply_plan(plan, relaunch=False))
            self.assertEqual(target.read_bytes(), executable(b'old'))

    def test_cleanup_retains_backup_and_only_removes_own_known_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)/'.updates'
            stage.mkdir()
            for name in ('previous.exe', 'settings.json', 'unrelated.txt', 'helper-' + 'a'*32 + '.exe'):
                (stage/name).write_text('keep')
            updates.cleanup_stage(temporary)
            self.assertTrue((stage/'previous.exe').exists())
            self.assertTrue((stage/'settings.json').exists())
            self.assertTrue((stage/'unrelated.txt').exists())
            self.assertFalse((stage/('helper-' + 'a'*32 + '.exe')).exists())


if __name__ == '__main__':
    unittest.main()
