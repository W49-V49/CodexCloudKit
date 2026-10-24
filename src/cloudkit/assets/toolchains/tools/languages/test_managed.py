"""Real filesystem regression checks for ownership-preserving tool upgrades."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from managed import Managed


class ManagedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='language-managed-')
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'runtime';self.source=self.base/'source'
        self.source.write_text('managed version one\n');self.managed=Managed(self.root)

    def test_custom_helper_and_editor_bytes_are_preserved(self):
        for name in ('language-tools','nvim-init.lua'):
            path=self.root/name;path.write_text('user custom '+name)
            with self.assertRaisesRegex(ValueError,'Preserve customized file'):self.managed.file(self.source,path)
            self.assertEqual(path.read_text(),'user custom '+name)
        self.assertFalse(self.managed.receipt.exists())

    def test_tracked_update_is_idempotent_and_rejects_later_user_edit(self):
        path=self.root/'language-tools';self.managed.file(self.source,path,True)
        before=self.managed.receipt.read_bytes();inode=path.stat().st_ino
        self.managed.file(self.source,path,True)
        self.assertEqual(path.stat().st_ino,inode);self.assertEqual(self.managed.receipt.read_bytes(),before)
        self.source.write_text('managed version two\n');Managed(self.root).file(self.source,path,True)
        self.assertEqual(path.read_text(),'managed version two\n')
        path.write_text('user custom after upgrade\n')
        self.source.write_text('managed version three\n')
        with self.assertRaisesRegex(ValueError,'Preserve customized file'):Managed(self.root).file(self.source,path)
        self.assertEqual(path.read_text(),'user custom after upgrade\n')

    def test_custom_internal_symlink_and_regular_launcher_are_preserved(self):
        (self.root/'bin').mkdir();custom=self.root/'custom-go';custom.write_text('custom')
        link=self.root/'bin/go';link.symlink_to(custom)
        with self.assertRaisesRegex(ValueError,'Preserve customized launcher'):self.managed.link(link,self.root/'versions/official/go')
        self.assertEqual(os.readlink(link),str(custom));self.assertEqual(custom.read_text(),'custom')
        regular=self.root/'bin/rustc';regular.write_text('user command')
        with self.assertRaisesRegex(ValueError,'Preserve user-owned non-link'):self.managed.link(regular,self.root/'versions/official/rustc')
        self.assertEqual(regular.read_text(),'user command')

    def test_exact_wanted_link_adoption_and_tracked_upgrade(self):
        path=self.root/'bin/go';path.parent.mkdir();old=self.root/'versions/old/go';path.symlink_to(old)
        self.managed.link(path,old)
        before=self.managed.receipt.read_bytes();inode=path.lstat().st_ino
        self.managed.link(path,old)
        self.assertEqual(path.lstat().st_ino,inode);self.assertEqual(self.managed.receipt.read_bytes(),before)
        new=self.root/'versions/new/go';Managed(self.root).link(path,new)
        self.assertEqual(os.readlink(path),str(new))

    def test_customized_previously_managed_link_is_preserved(self):
        link=self.root/'bin/java';self.managed.link(link,self.root/'jdk/bin/java')
        link.unlink();custom=self.root/'another-user-java';link.symlink_to(custom)
        with self.assertRaisesRegex(ValueError,'Preserve customized launcher'):Managed(self.root).link(link,self.root/'new-jdk/bin/java')
        self.assertEqual(os.readlink(link),str(custom))

    def test_parent_symlink_and_path_escape_are_rejected(self):
        outside=self.base/'outside';outside.mkdir();(self.root/'bin').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'Preserve custom symlink'):self.managed.file(self.source,self.root/'bin/command')
        with self.assertRaises(ValueError):self.managed.file(self.source,self.root/'../escape')
        with self.assertRaises(ValueError):self.managed.file(self.source,self.root/'bin/../escape')
        self.assertEqual(list(outside.iterdir()),[]);self.assertFalse((self.base/'escape').exists())


if __name__=='__main__':unittest.main(verbosity=2)
