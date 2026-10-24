"""Per-project cloud profile isolation; no HOME writes or upstream mutation."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from project_profile import profile

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='design-profile-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=self.root/'project';self.store=self.root/'profiles'
        subprocess.run(['git','init','--quiet','--initial-branch=main',str(self.project)],check=True)
        subprocess.run(['git','-C',str(self.project),'remote','add','origin','https://github.com/test/isolated-profile.git'],check=True)
    def test_project_branch_profile_is_isolated_and_unchanged_source_reused(self):
        first=profile(self.project,self.store);path=Path(first['profile']);original=path.read_bytes()
        second=profile(self.project,self.store);self.assertFalse(second['created']);self.assertEqual(original,path.read_bytes())
        self.assertTrue(second['upstream_source_unchanged']);self.assertIn('Font source: `system`',path.read_text())
        subprocess.run(['git','-C',str(self.project),'symbolic-ref','HEAD','refs/heads/feature'],check=True)
        third=profile(self.project,self.store);self.assertNotEqual(first['partition_key'],third['partition_key'])
        self.assertEqual(original,path.read_bytes())
    def test_customized_profile_is_preserved(self):
        first=profile(self.project,self.store);path=Path(first['profile']);custom=path.read_text()+'\nProject-specific branding note.\n';path.write_text(custom)
        second=profile(self.project,self.store);self.assertTrue(second['profile_existing_preserved']);self.assertEqual(custom,path.read_text())
    def test_unsafe_slug_and_symlink_root_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'slug'):profile(self.project,self.store,name='../escape')
        self.store.symlink_to(self.root,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlinks'):profile(self.project,self.store)

if __name__=='__main__':unittest.main(verbosity=2)
