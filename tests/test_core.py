import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cloudkit.core import attach, index, project_identity, restore_index, setup


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.runtime = self.base / 'runtime'
        self.state = self.base / 'state'
        self.project = self.base / 'project'
        self.project.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'add', 'origin', 'https://github.com/example/project.git')
        (self.project / 'app.py').write_text('def total(a, b):\n    return a + b\n')
        (self.project / 'AGENTS.md').write_text('Existing project instructions.\n')
        self.git('add', '.')
        self.git('commit', '-m', 'Fixture')

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.project), *args], stderr=subprocess.DEVNULL, text=True)

    def cli(self, *arguments):
        return subprocess.run([sys.executable, '-m', 'cloudkit', *map(str, arguments)], text=True, capture_output=True)

    def test_setup_attach_repeat_preserves_project_and_custom_skill(self):
        first = setup(self.runtime)
        self.assertEqual(first['written_files'], 5)
        self.assertEqual(setup(self.runtime)['written_files'], 0)
        custom = self.project / '.agents/skills/cloudkit-memory-context'
        custom.mkdir(parents=True)
        (custom / 'SKILL.md').write_text('Project-specific implementation\n')
        before = self.git('status', '--porcelain')
        result = attach(self.project, self.runtime, self.state)
        self.assertIn('cloudkit-memory-context', result['project_overrides_preserved'])
        self.assertEqual(len(result['linked']), 4)
        again = attach(self.project, self.runtime, self.state)
        self.assertEqual(again['linked'], [])
        self.assertEqual(len(again['already_linked']), 4)
        self.assertEqual((self.project / 'AGENTS.md').read_text(), 'Existing project instructions.\n')
        self.assertEqual(self.git('status', '--porcelain'), before)

    def test_fresh_attachment_keeps_generated_links_out_of_git(self):
        setup(self.runtime)
        attach(self.project, self.runtime, self.state)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_customized_generated_rules_survive_without_partial_mutation(self):
        setup(self.runtime)
        (self.project / 'AGENTS.cloudkit.md').write_text('User content\n')
        with self.assertRaisesRegex(ValueError, 'preserved'):
            attach(self.project, self.runtime, self.state)
        self.assertFalse((self.project / '.agents').exists())
        self.assertEqual((self.project / 'AGENTS.cloudkit.md').read_text(), 'User content\n')

    def test_runtime_user_modification_is_preserved(self):
        setup(self.runtime)
        target = self.runtime / '.agents/skills/cloudkit-memory-context/SKILL.md'
        target.write_text('Custom runtime skill\n')
        with self.assertRaisesRegex(ValueError, 'preserved'):
            setup(self.runtime)
        self.assertEqual(target.read_text(), 'Custom runtime skill\n')

    def test_symlink_project_parent_and_generated_file_are_not_followed(self):
        setup(self.runtime)
        outside = self.base / 'outside'
        outside.mkdir()
        (self.project / '.agents').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            attach(self.project, self.runtime, self.state)
        self.assertEqual(list(outside.iterdir()), [])
        (self.project / '.agents').unlink()
        (self.project / 'AGENTS.cloudkit.md').symlink_to(outside / 'rules')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            attach(self.project, self.runtime, self.state)
        self.assertFalse((outside / 'rules').exists())

    def test_index_change_rename_delete_and_pipeline_fingerprint(self):
        setup(self.runtime)
        attach(self.project, self.runtime, self.state)
        first = index(self.project, self.state)
        self.assertEqual(first['files'], 2)
        self.assertEqual(first['work_items'], 2)
        repeated = index(self.project, self.state)
        self.assertEqual(repeated['work_items'], 0)
        self.assertEqual(first['generation'], repeated['generation'])
        self.assertEqual(repeated['unchanged_content_reused'], 2)
        (self.project / 'app.py').rename(self.project / 'calc.py')
        renamed = index(self.project, self.state)
        self.assertEqual(renamed['renamed'], [{'from': 'app.py', 'to': 'calc.py'}])
        self.assertEqual(renamed['work_items'], 0)
        (self.project / 'calc.py').write_text('def total(a, b):\n    return a + b + 1\n')
        changed = index(self.project, self.state)
        self.assertEqual(changed['modified'], ['calc.py'])
        (self.project / 'calc.py').unlink()
        self.assertEqual(index(self.project, self.state)['deleted'], ['calc.py'])
        migration = index(self.project, self.state, fingerprint='content-inventory-v2')
        self.assertTrue(migration['fingerprint_changed'])
        self.assertEqual(migration['work_items'], 1)

    def test_restore_new_directory_and_partition_isolation(self):
        snapshot = self.base / 'inventory.json'
        original = index(self.project, self.state, export=snapshot)
        recovered = restore_index(self.project, self.base / 'fresh-state', snapshot, original['export_sha256'])
        self.assertEqual(recovered['generation'], original['generation'])
        self.assertEqual(index(self.project, self.base / 'fresh-state')['work_items'], 0)
        self.git('switch', '-c', 'other')
        with self.assertRaisesRegex(ValueError, 'another project or branch'):
            restore_index(self.project, self.base / 'fresh-state', snapshot, original['export_sha256'])
        snapshot.write_text(snapshot.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            restore_index(self.project, self.base / 'fresh-state', snapshot, original['export_sha256'])

    def test_state_cannot_be_placed_in_public_project(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            index(self.project, self.project / 'data')

    def test_cli_memory_delegation_contract(self):
        protocol = self.base / 'protocol.py'
        protocol.write_text('import json,sys\nprint(json.dumps({"argv":sys.argv[1:]}))\n')
        run = self.cli('memory', '--project-id', 'example/project', '--protocol', protocol,
                       '--state-dir', self.state, 'status')
        self.assertEqual(run.returncode, 0, run.stderr)
        args = json.loads(run.stdout)['argv']
        self.assertEqual(args[:4], ['--project', 'example/project', '--branch', 'main'])
        self.assertEqual(args[-1], 'status')
        self.assertFalse(self.state.exists())

    def test_cli_run_preserves_backend_exit_status(self):
        supervisor = self.base / 'supervisor.py'
        supervisor.write_text('import sys\nassert sys.argv[1:]==["inspect", "--fixture"]\nsys.exit(17)\n')
        run = self.cli('run', '--supervisor', supervisor, '--', 'inspect', '--fixture')
        self.assertEqual(run.returncode, 17, run.stderr)

    def test_credentials_in_origin_are_not_stored(self):
        self.git('remote', 'set-url', 'origin', 'https://username:example-value@github.com/example/project.git')
        identity = project_identity(self.project)
        self.assertEqual(identity['origin'], 'github.com/example/project')
        self.assertNotIn('example-value', json.dumps(identity))


if __name__ == '__main__':
    unittest.main()
