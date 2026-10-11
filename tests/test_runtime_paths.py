"""Portable persistence, taskbar relaunch and external-process isolation."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import runtime_paths as paths


class RuntimePathsTests(unittest.TestCase):
    def test_source_keeps_data_next_to_project(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'project' / 'runtime_paths.py'
            root, resources = paths.locations(source, Path(folder) / 'python.exe', False)
            self.assertEqual(root, source.parent)
            self.assertEqual(resources, source.parent)

    def test_frozen_data_stays_next_to_exe_instead_of_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            resource = base / '_MEI123' / 'runtime_paths.py'
            exe = base / '中文目录 空格' / '用量挂件.exe'
            root, resources = paths.locations(resource, exe, True)
            self.assertEqual(root, exe.parent)
            self.assertEqual(resources, resource.parent)
            self.assertEqual(paths.locations(base / '_MEI456' / 'runtime_paths.py', exe, True)[0], root)

    def test_frozen_relaunch_and_icon_use_the_actual_exe(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / '中文 挂件.exe'
            exe.touch()
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(exe)):
                self.assertEqual(paths.launch_command(), subprocess.list2cmdline([str(exe)]))
                self.assertEqual(paths.taskbar_icon(), str(exe) + ',0')
                self.assertNotIn('_MEI', paths.taskbar_icon())

    def test_external_process_temporarily_restores_system_search_path(self):
        import ctypes
        setter = Mock(return_value=True)
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(ctypes, 'WinDLL', return_value=Mock(SetDllDirectoryW=setter)), \
                patch.object(paths.subprocess, 'Popen') as popen:
            env = {'PATH': str(paths.RESOURCES) + ';C:\\Windows\\System32'}
            paths.external_process(['codex.exe', 'app-server'], env=env)
            self.assertEqual(popen.call_args.kwargs['env']['PATH'], 'C:\\Windows\\System32')
            self.assertEqual([call.args for call in setter.call_args_list], [(None,), (str(paths.RESOURCES),)])

    def test_external_launch_failure_still_restores_bundle_search_path(self):
        import ctypes
        setter = Mock(return_value=True)
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(ctypes, 'WinDLL', return_value=Mock(SetDllDirectoryW=setter)), \
                patch.object(paths.subprocess, 'Popen', side_effect=OSError('Launch failed')):
            with self.assertRaises(OSError):
                paths.external_process(['missing.exe'])
            self.assertEqual(setter.call_args.args, (str(paths.RESOURCES),))


if __name__ == '__main__':
    unittest.main()
