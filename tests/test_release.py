"""Standard-library packaging tests using tiny fixtures, never production models."""
import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from scripts import build_release as release

class ReleaseTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='content-engine-release-test-')
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'source'
        self.root.mkdir()
        for relative in release.REQUIRED_FILES:
            self.write(relative, ('fixture-only: ' + relative + '\n').encode('utf-8'))
        self.output = self.base / 'delivery.zip'

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, content=b'fixture\n'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def build(self, output=None):
        return release.build_release(self.root, output or self.output, source_date_epoch=1788134400)

    def test_manifest_matches_every_member_and_has_no_absolute_source_path(self):
        self.write('scripts/helper.py', b"print('fixture')\n")
        result = self.build()
        with zipfile.ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            manifest_bytes = archive.read('MANIFEST.json')
            manifest = json.loads(manifest_bytes)
            expected = {item['path']: item for item in manifest['files']}
            self.assertEqual(set(expected), set(archive.namelist()) - {'MANIFEST.json'})
            self.assertEqual(len(expected), result['payload_files'])
            self.assertEqual(manifest['release']['payload_file_count'], len(expected))
            total_size = 0
            for (name, item) in expected.items():
                data = archive.read(name)
                total_size += len(data)
                self.assertEqual(item['size_bytes'], len(data), name)
                self.assertEqual(item['sha256'], hashlib.sha256(data).hexdigest(), name)
                self.assertFalse(Path(name).is_absolute())
                self.assertNotIn('..', name.split('/'))
            self.assertEqual(manifest['release']['payload_bytes'], total_size)
            self.assertNotIn(str(self.root).encode('utf-8'), manifest_bytes)
        self.assertEqual(result['sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_member_sorting_and_fixed_epoch_bytes_are_deterministic(self):
        self.write('douyin_engine/web/z.js', b'z\n')
        self.write('douyin_engine/web/a.js', b'a\n')
        self.build()
        second = self.base / 'second.zip'
        self.build(second)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.namelist(), sorted(archive.namelist()))
        self.assertEqual(self.output.read_bytes(), second.read_bytes())

    def test_existing_output_is_not_overwritten(self):
        original = b'user-owned existing archive'
        self.output.write_bytes(original)
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(self.output.read_bytes(), original)

    def test_existing_directory_output_is_not_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertTrue(self.output.is_dir())

    def test_output_inside_included_tree_is_refused(self):
        for relative in ('douyin_engine/web/release.zip', 'scripts/release.zip', 'docs/release.zip', 'models/sarcasm_m4/release.zip', 'artifacts/release.zip', 'data/release.zip'):
            with self.subTest(relative=relative):
                target = self.root / relative
                with self.assertRaises(release.ReleaseError):
                    self.build(target)
                self.assertFalse(target.exists())

    def test_output_in_excluded_dist_never_includes_itself_or_old_archives(self):
        self.write('dist/previous.zip', b'old archive')
        target = self.root / 'dist/new.zip'
        self.build(target)
        with zipfile.ZipFile(target) as archive:
            self.assertFalse(any((name.startswith('dist/') for name in archive.namelist())))

    def test_path_traversal_absolute_windows_and_ambiguous_members_are_rejected(self):
        names = ('../secret', '/absolute', 'douyin_engine/web/../../secret', 'douyin_engine/web/./app.js', 'douyin_engine/web//app.js', 'C:/secret', 'web\\..\\secret', 'douyin_engine/web/evil. ', 'douyin_engine/web/line\nbreak.js', 'douyin_engine/web/CON.txt', 'douyin_engine/web/NUL', 'douyin_engine/web/LPT1.js')
        for name in names:
            with self.subTest(name=name), self.assertRaises(release.ReleaseError):
                release.safe_archive_name(name)
        self.assertEqual(release.safe_archive_name('docs/使用说明.md'), 'docs/使用说明.md')

    @unittest.skipUnless(hasattr(os, 'symlink') and os.name != 'nt', 'symlink permissions not assumed on Windows')
    def test_source_file_symlink_is_rejected(self):
        outside = self.base / 'external-config.py'
        outside.write_bytes(b'private configuration')
        (self.root / 'douyin_engine/engine/linked.py').symlink_to(outside)
        with self.assertRaises(release.ReleaseError):
            self.build()
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(hasattr(os, 'symlink') and os.name != 'nt', 'symlink permissions not assumed on Windows')
    def test_source_directory_symlink_is_rejected(self):
        outside = self.base / 'external'
        outside.mkdir()
        (outside / 'secret.py').write_bytes(b'private')
        (self.root / 'douyin_engine/engine/external').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(release.ReleaseError):
            self.build()

    @unittest.skipUnless(hasattr(os, 'symlink') and os.name != 'nt', 'symlink permissions not assumed on Windows')
    def test_source_root_symlink_is_rejected(self):
        linked = self.base / 'linked-root'
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(release.ReleaseError):
            release.build_release(linked, self.output)

    @unittest.skipUnless(hasattr(os, 'symlink') and os.name != 'nt', 'symlink permissions not assumed on Windows')
    def test_output_symlink_and_symlink_parent_are_rejected(self):
        outside = self.base / 'existing.zip'
        outside.write_bytes(b'preserve')
        self.output.symlink_to(outside)
        with self.assertRaises(release.ReleaseError):
            self.build()
        self.assertEqual(outside.read_bytes(), b'preserve')
        linked_dir = self.base / 'linked-output'
        linked_dir.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(release.ReleaseError):
            self.build(linked_dir / 'other.zip')

    def test_missing_or_empty_required_file_refuses_incomplete_release(self):
        required = self.root / 'README.md'
        required.unlink()
        with self.assertRaisesRegex(release.ReleaseError, 'README.md'):
            self.build()
        required.write_bytes(b'')
        with self.assertRaisesRegex(release.ReleaseError, 'empty'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_launch_scripts_have_executable_zip_metadata(self):
        self.build()
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.getinfo('start.command').external_attr >> 16 & 511, 493)
            self.assertEqual(archive.getinfo('setup.sh').external_attr >> 16 & 511, 493)
            self.assertEqual(archive.getinfo('README.md').external_attr >> 16 & 511, 420)

    def test_source_mutation_between_hash_and_copy_rejects_and_removes_partial_zip(self):
        original = release._read_chunks
        seen = set()

        def mutate_on_copy(path, consume):
            if consume is not None and path.name == 'README.md' and (path not in seen):
                seen.add(path)
                path.write_bytes(b'changed after manifest snapshot\n')
            return original(path, consume)
        with patch.object(release, '_read_chunks', side_effect=mutate_on_copy):
            with self.assertRaisesRegex(release.ReleaseError, 'changed'):
                self.build()
        self.assertFalse(self.output.exists())

    def test_non_zip_output_and_invalid_epoch_are_rejected(self):
        with self.assertRaises(release.ReleaseError):
            self.build(self.base / 'release.txt')
        with self.assertRaises(release.ReleaseError):
            release.build_release(self.root, self.output, source_date_epoch=-1)
        with self.assertRaises(release.ReleaseError):
            release.build_release(self.root, self.output, version='../../escape')
        self.assertFalse(self.output.exists())

    def test_allowlist_excludes_models_private_data_reports_and_artifacts(self):
        excluded = ('.env', 'private/input.json', 'models/sarcasm_m4/model.safetensors', 'models/sarcasm_m4/config.json', 'artifacts/benchmark.json', 'data/raw/train.json', '.venv/private.py', 'douyin_engine/data/private.json', 'douyin_engine/engine/__pycache__/cache.pyc', 'docs/old-report.docx', 'docs/old-report.pdf', 'docs/screenshot.png')
        for name in excluded:
            self.write(name, b'must not be packaged')
        included = ('.gitignore', 'docs/API.md', 'scripts/test_frontend.cjs', 'douyin_engine/engine/helper.py')
        for name in included:
            self.write(name)
        self.build()
        with zipfile.ZipFile(self.output) as archive:
            names = set(archive.namelist())
        self.assertTrue(set(included).issubset(names))
        self.assertFalse(set(excluded) & names)

    def test_required_demo_and_package_resources_are_enforced(self):
        (self.root / 'douyin_engine/data/demo.json').unlink()
        with self.assertRaisesRegex(release.ReleaseError, 'demo.json'):
            self.build()
        self.assertFalse(self.output.exists())
if __name__ == '__main__':
    unittest.main()
