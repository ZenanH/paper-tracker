import copy
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import gist_sync
import journal_config
import journal_manager
import user_backup
import validate_data
from common import now_beijing, read_json, write_json

spec = importlib.util.spec_from_file_location('backup_archive_api', Path(__file__).resolve().parents[1] / 'server/archive-api.py')
archive_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_api)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.archive = self.root / 'archive/archive.json'
        self.today = now_beijing().date().isoformat()
        self.patches = [patch.object(user_backup, 'DATA_DIR', self.data), patch.object(user_backup, 'ARCHIVE_FILE', self.archive),
                        patch.object(validate_data, 'DATA_DIR', self.data), patch.object(validate_data, 'iter_day_files', lambda: sorted((self.data / 'days').glob('*.json'))),
                        patch.object(archive_api, 'ARCHIVE_FILE', self.archive), patch.object(archive_api, 'MANIFEST_FILE', self.data / 'manifest.json')]
        for item in self.patches:
            item.start()
        self.journal = {'id': 'nature', 'name': 'Nature', 'group': '顶刊', 'url': 'https://www.nature.com/nature/',
                        'cas': {'zone': 1}, 'impact_factor': {'value': 50, 'year': 2025, 'status': 'verified'}}
        self.paper = {'id': 'doi:10.1000/example', 'doi': '10.1000/example', 'journal_id': 'nature', 'published_date': self.today,
                      'title_en': 'Mechanics of materials', 'title_zh': '材料力学', 'url': 'https://doi.org/10.1000/example',
                      'translation_status': 'translated', 'first_discovered_at': '2026-10-21T01:00:00+08:00'}
        write_json(self.data / 'journals.json', {'journals': [self.journal]})
        write_json(self.data / 'journal-config.json', {'categories': {'顶刊': [{'name': 'Nature', 'url': self.journal['url']}]}})
        write_json(self.data / 'filter-config.json', {'journal_ids': ['nature'], 'prompt': '只保留力学'})
        write_json(self.data / 'days' / (self.today + '.json'), {'articles': [self.paper]})
        old = {**self.paper, 'id': 'old-favorite', 'date': '2020-01-01'}
        old.pop('published_date')
        write_json(self.archive, {'items': {self.paper['id']: {**self.paper, 'date': self.today}}, 'cleared_items': {},
                                  'favorites': {self.paper['id']: {**self.paper, 'date': self.today}, 'old-favorite': old}})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def files(self):
        with user_backup.archive_lock():
            return {name: value['content'] for name, value in user_backup.snapshot_files().items()}

    def alter(self, files, callback):
        files = copy.deepcopy(files)
        manifest = json.loads(files['paper-tracker.json'])
        name = manifest['files'][0]['name']
        part = json.loads(files[name]); callback(part)
        files[name] = user_backup.compact(part)
        manifest['files'][0]['sha256'] = user_backup.sha(files[name])
        files['paper-tracker.json'] = user_backup.compact(manifest)
        return files

    def test_roundtrip_preserves_translations_independent_states_and_old_favorites(self):
        files = self.files()
        self.assertNotIn('api_key', ''.join(files.values()))
        preview = user_backup.stage_import(files)
        self.assertEqual(preview['favorites'], 2)
        result = user_backup.restore_preview(preview['preview_id'], validate_data.main)
        self.assertEqual(result['papers'], 1)
        state = read_json(self.archive)
        self.assertEqual(state['favorites']['old-favorite']['date'], '2020-01-01')
        self.assertEqual(state['favorites'][self.paper['id']]['title_zh'], '材料力学')
        self.assertIn(self.paper['id'], state['items'])
        self.assertNotIn('old-favorite', state['items'])
        self.assertNotEqual(state['dataset_id'], 'initial')
        self.assertTrue(list((self.archive.parent / 'backups').glob('backup-*/index.json')))
        self.assertIsNone(read_json(self.data / 'manifest.json')['collection_checked_at'])

    def test_corrupt_or_extra_fields_or_traversal_are_rejected(self):
        files = self.files()
        broken = dict(files); broken[next(name for name in files if name != 'paper-tracker.json')] += ' '
        with self.assertRaisesRegex(ValueError, 'checksum'):
            user_backup.stage_import(broken)
        with self.assertRaises(ValueError):
            user_backup.stage_import({**files, '../../secrets.json': '{}'})
        for edit in [lambda p: p['papers'][0].update(api_key='should-not-import'),
                     lambda p: p['papers'][0].update(url='javascript:alert(1)'),
                     lambda p: p['journal'].update(url='https://token:secret@example.org'),
                     lambda p: p['journal'].update(id='../escape'),
                     lambda p: p['journal']['cas'].update(zone=4),
                     lambda p: p['journal'].update(config_entry={'api_key': 'secret'})]:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                user_backup.stage_import(self.alter(files, edit))
        self.assertEqual(read_json(self.archive)['favorites'][self.paper['id']]['title_zh'], '材料力学')

    def test_failed_validation_rolls_back_every_managed_file(self):
        before = {path: path.read_bytes() for path in user_backup.managed_paths() if path.exists()}
        preview = user_backup.stage_import(self.files())
        def fail():
            raise ValueError('Simulated validation failure')
        with self.assertRaisesRegex(ValueError, 'Simulated'):
            user_backup.restore_preview(preview['preview_id'], fail)
        self.assertEqual(before, {path: path.read_bytes() for path in user_backup.managed_paths() if path.exists()})
        self.assertFalse((self.archive.parent / '.restore-pending.json').exists())

    def test_crash_marker_blocks_archive_then_startup_recovers(self):
        preview = user_backup.stage_import(self.files())
        user_backup.restore_preview(preview['preview_id'])
        backup = next((self.archive.parent / 'backups').glob('backup-*'))
        write_json(self.archive.parent / '.restore-pending.json', {'backup': backup.name})
        write_json(self.archive, {'items': {}, 'favorites': {}, 'cleared_items': {}})
        with self.assertRaises(archive_api.ArchiveStateError):
            archive_api.read_pruned_state()
        user_backup.recover_restore()
        self.assertIn(self.paper['id'], archive_api.read_pruned_state()['favorites'])

    def test_old_browser_cannot_write_after_import(self):
        preview = user_backup.stage_import(self.files())
        user_backup.restore_preview(preview['preview_id'])
        for payload in ({'action': 'unfavorite', 'ids': [self.paper['id']]},
                        {'action': 'unfavorite', 'ids': [self.paper['id']], 'dataset_id': 'initial'}):
            with self.assertRaisesRegex(ValueError, 'dataset changed'):
                archive_api.apply_archive(payload)
        current = read_json(self.archive)['dataset_id']
        archive_api.apply_archive({'action': 'unfavorite', 'ids': [self.paper['id']], 'dataset_id': current})
        self.assertIn(self.paper['id'], read_json(self.archive)['items'])

    def test_stale_preview_and_corrupt_archive_do_not_overwrite(self):
        with self.assertRaises(ValueError):
            user_backup.restore_preview('../bad')
        preview = user_backup.stage_import(self.files())
        self.archive.write_text('{broken')
        with self.assertRaises(ValueError):
            user_backup.restore_preview(preview['preview_id'])
        self.assertEqual(self.archive.read_text(), '{broken')

    def test_unfollow_preserves_favorites_and_refollow_stable_id(self):
        with patch.object(journal_config, 'CONFIG_PATH', self.data / 'journal-config.json'), patch.object(journal_config, 'DATA_DIR', self.data):
            journal_config.remove_journal('nature')
            cfg = read_json(self.data / 'journal-config.json')
            self.assertFalse(cfg['categories']['顶刊'])
            self.assertEqual(cfg['unfollowed_journals'][0]['impact_factor']['value'], 50)
            # Unfollow cleanup cannot delete favorites.
            class Response:
                def __enter__(inner): return inner
                def __exit__(inner, *args): return False
                def read(inner, *args): return self.archive.read_bytes()
            calls = []
            def api(request, **kwargs):
                if not isinstance(request, str):
                    payload = json.loads(request.data); calls.append(payload)
                    archive_api.apply_archive(payload)
                return Response()
            with patch.object(journal_manager.urllib.request, 'urlopen', side_effect=api):
                journal_manager.remove_from_archive('nature', [])
            self.assertEqual(calls[0]['action'], 'remove')
            self.assertFalse(read_json(self.archive)['items'])
            self.assertEqual(len(read_json(self.archive)['favorites']), 2)
            restored = journal_config.add_journal('Nature')
            self.assertEqual(restored['id'], 'nature')
            self.assertFalse(read_json(self.data / 'journal-config.json')['unfollowed_journals'])

    def test_unicode_chunks_are_bounded_and_validate_without_duplication(self):
        papers = [{**self.paper, 'id': f'paper-{i}', 'title_zh': '力学' * 4500} for i in range(52)]
        write_json(self.data / 'days' / (self.today + '.json'), {'articles': papers})
        files = self.files()
        self.assertGreater(len(files), 2)
        self.assertTrue(all(len(value.encode('utf-8')) < 900000 for value in files.values()))
        self.assertEqual(len(user_backup.validate_files(files)['papers']), 54)


class GitHubAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / 'protected/github-gist.json'
        self.patcher = patch.object(gist_sync, 'CONFIG_PATH', self.config); self.patcher.start()
        gist_sync.FLOWS.clear()

    def tearDown(self):
        self.patcher.stop(); self.temp.cleanup(); gist_sync.FLOWS.clear()

    def connect(self):
        gist_sync.protected_write(self.config, {'client_id': 'Ov23public123', 'access_token': 'test-token-never-export', 'user_id': 42, 'login': 'example'})

    def test_device_pending_slowdown_success_never_returns_token(self):
        replies = [dict(device_code='device-private', user_code='ABCD-1234', expires_in=900, interval=5),
                   dict(error='authorization_pending'), dict(error='slow_down'),
                   dict(access_token='private-access-token', scope='gist', expires_in=28800), dict(login='example', id=42)]
        with patch.object(gist_sync, 'request', side_effect=replies) as network:
            flow = gist_sync.begin_authorization('Ov23public123')
            self.assertNotIn('device_code', flow)
            self.assertEqual(network.call_args.args[1]['scope'], 'gist')
            for expected in (5, 10):
                gist_sync.FLOWS[flow['flow_id']]['next_poll'] = 0
                self.assertEqual(gist_sync.poll_authorization(flow['flow_id'])['interval'], expected)
            gist_sync.FLOWS[flow['flow_id']]['next_poll'] = 0
            result = gist_sync.poll_authorization(flow['flow_id'])
        self.assertTrue(result['connected'])
        self.assertNotIn('token', json.dumps(result))
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.config.parent.stat().st_mode & 0o777, 0o700)
        gist_sync.disconnect()
        self.assertNotIn('access_token', gist_sync.settings())

    def test_expired_and_incomplete_permissions_do_not_connect(self):
        self.connect(); value = gist_sync.settings(); value['expires_at'] = time.time() - 1
        gist_sync.protected_write(self.config, value)
        with self.assertRaisesRegex(ValueError, 'Connect GitHub'):
            gist_sync.credentials()
        with patch.object(gist_sync, 'request', return_value=dict(device_code='private', user_code='ABCD', expires_in=1, interval=5)):
            flow = gist_sync.begin_authorization('Ov23public123')
        gist_sync.FLOWS[flow['flow_id']]['next_poll'] = 0
        with patch.object(gist_sync, 'request', return_value={'access_token': 'private', 'scope': 'read:user'}), self.assertRaisesRegex(ValueError, 'Gist access'):
            gist_sync.poll_authorization(flow['flow_id'])

    def test_export_is_secret_and_import_requires_owner_complete_content(self):
        self.connect()
        identifier = 'a' * 32
        with patch.object(gist_sync, 'request', return_value={'id': identifier}) as network:
            result = gist_sync.export_files({'paper-tracker.json': {'content': '{}'}})
            self.assertFalse(network.call_args.args[1]['public'])
            self.assertEqual(result['gist_url'], f'https://gist.github.com/example/{identifier}')
        for response in ({'owner': {'id': 999}, 'files': {}},
                         {'owner': {'id': 42}, 'truncated': True, 'files': {}},
                         {'owner': {'id': 42}, 'files': {'file.json': {'content': '{}', 'truncated': True}}}):
            with patch.object(gist_sync, 'request', return_value=response), self.assertRaises(ValueError):
                gist_sync.import_files(identifier)
        with patch.object(gist_sync, 'request', return_value={'owner': {'id': 42}, 'files': {'x.json': {'content': '{}'}}}):
            self.assertEqual(gist_sync.import_files(identifier), {'x.json': '{}'})
        for url in ('https://evil.example/' + identifier, 'http://gist.github.com/u/' + identifier, '../secret'):
            with self.assertRaises(ValueError): gist_sync.gist_id(url)

    def test_jobs_and_confirmation_guard_import(self):
        from test_admin_routes import AdminRoutesTests
        tester = AdminRoutesTests()
        with patch.dict(journal_manager.JOB, {'status': 'running'}):
            self.assertEqual(tester.post('/api/admin/gist/export', {})[0], 400)
        with patch.dict(journal_manager.JOB, {'status': 'idle'}), patch.object(journal_manager, 'start_job') as start:
            self.assertEqual(tester.post('/api/admin/gist/import', {'preview_id': 'x'})[0], 400)
            start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
