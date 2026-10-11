"""Build and verify a single-file Windows release without personal data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def verify_bundle(executable):
    # Renaming/moving the app, Chinese paths, a foreign working directory and no
    # Python on PATH reproduce the ordinary download-and-double-click setup.
    workspace = ROOT / 'build'
    workspace.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='portable-check-', dir=workspace) as temporary:
        home = Path(temporary) / '中文目录 空格'
        home.mkdir()
        app = home / '用量挂件.exe'
        shutil.copy2(executable, app)
        report = home / 'smoke-report.json'
        env = dict(os.environ)
        for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'TCL_LIBRARY', 'TK_LIBRARY'):
            env.pop(name, None)
        system = Path(env.get('SystemRoot', r'C:\Windows'))
        env['PATH'] = os.pathsep.join([str(system / 'System32'), str(system)])
        subprocess.run([str(app), '--smoke-test', '--smoke-report', str(report)],
                       cwd=temporary, env=env, check=True, timeout=60)
        result = json.loads(report.read_text(encoding='utf-8'))
        assert result['ok'] and result['frozen'], result
        assert Path(result['data_root']).samefile(home), result
        assert result['icon_available'], result
        assert result['taskbar_icon'] == str(app.resolve()) + ',0', result
        settings = home / 'settings.json'
        assert settings.is_file(), 'Configuration was not saved next to the executable'
        assert not (home / 'startup-error.log').exists()
        saved = json.loads(settings.read_text(encoding='utf-8'))
        saved['interval'] = 237
        settings.write_text(json.dumps(saved), encoding='utf-8')
        subprocess.run([str(app), '--smoke-test', '--smoke-report', str(report)],
                       cwd=temporary, env=env, check=True, timeout=60)
        result = json.loads(report.read_text(encoding='utf-8'))
        assert result['interval'] == 237, 'Preferences were lost across launches'
    print('Portable EXE verified: bundled GUI, saved settings, Chinese path, no Python on PATH.')


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build Windows executables on Windows.')
    import tkinter
    notices = []
    for license_path, destination in (
        (Path(sys.base_prefix) / 'LICENSE.txt', 'licenses/Python'),
        (Path(tkinter.Tcl().eval('info library')) / 'license.terms', 'licenses/Tcl'),
    ):
        if license_path.is_file():
            notices.extend(['--add-data', str(license_path) + ':' + destination])
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--onefile', '--windowed', '--noupx', '--name', 'AIUsage',
        '--icon', str(ROOT / 'app.ico'),
        '--add-data', str(ROOT / 'app.ico') + ':.',
        '--version-file', str(ROOT / 'packaging' / 'version_info.txt'),
        '--specpath', str(ROOT / 'build'),
        '--workpath', str(ROOT / 'build' / 'pyinstaller'),
        '--distpath', str(ROOT / 'dist'), *notices, str(ROOT / 'app.py')
    ], cwd=ROOT, check=True)
    executable = ROOT / 'dist' / 'AIUsage.exe'
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    assert not ({'settings.json', 'snapshot.json', 'startup-error.log'} & set(archive.toc)), 'Private data was bundled'
    verify_bundle(executable)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (ROOT / 'dist' / 'SHA256SUMS.txt').write_text(
        digest + '  AIUsage.exe\n', encoding='utf-8')
    print(f'Release ready: {executable} ({executable.stat().st_size / 1024 / 1024:.1f} MB)')


if __name__ == '__main__':
    main()
