"""Build and verify a single-file Windows release without personal data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify_bundle(executable):
    import updates
    import runtime_paths
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
        # Exercise the actual compiled update helper in the same portable folder.
        # Old/new bytes differ; configuration must survive exactly, including
        # encrypted provider settings and the saved usage snapshot.
        settings_before = settings.read_bytes()
        snapshot = home / 'snapshot.json'
        snapshot.write_text('[{"id":"test","usage":"UNCHANGED"}]', encoding='utf-8')
        snapshot_before = snapshot.read_bytes()
        stage = home / '.updates'
        stage.mkdir()
        candidate = stage / 'candidate.exe'
        shutil.copy2(executable, candidate)
        with app.open('ab') as stream:
            stream.write(b'previous-version-marker')
        old_digest = updates.file_digest(app)
        digest = updates.file_digest(candidate)
        plan = updates.create_plan(candidate, digest, app, runtime_paths.VERSION, parent_pid=0)
        nonce = json.loads(plan.read_text(encoding='utf-8'))['nonce']
        helper = stage / ('helper-' + nonce + '.exe')
        shutil.copy2(executable, helper)
        update_env = dict(env, PYINSTALLER_RESET_ENVIRONMENT='1')
        subprocess.run([str(helper), '--apply-update', str(plan), '--update-no-relaunch'],
                       cwd=temporary, env=update_env, check=True, timeout=90)
        assert updates.file_digest(app) == digest, 'Compiled helper did not replace the EXE'
        assert updates.file_digest(stage / 'previous.exe') == old_digest, 'Previous EXE was not preserved'
        assert settings.read_bytes() == settings_before, 'Update modified configuration'
        assert snapshot.read_bytes() == snapshot_before, 'Update modified the saved usage'
        assert json.loads((stage / 'result.json').read_text(encoding='utf-8'))['status'] == 'success'
        subprocess.run([str(app), '--smoke-test', '--smoke-report', str(report)],
                       cwd=temporary, env=update_env, check=True, timeout=60)
        assert json.loads(report.read_text(encoding='utf-8'))['interval'] == 237
        (stage / 'result.json').unlink()
        subprocess.run([str(app), '--smoke-test', '--smoke-update', '--smoke-report', str(report)],
                       cwd=temporary, env=update_env, check=True, timeout=60)
        deadline = time.monotonic() + 40
        while not (stage / 'result.json').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('Compiled GUI-to-helper update handoff failed')
            time.sleep(.2)
        # Wait for a helper to exit after its result; Windows keeps a running
        # helper EXE locked. Never terminate unrelated desktop processes.
        helpers = list(stage.glob('helper-*.exe'))
        while helpers:
            remaining = []
            for helper in helpers:
                try:
                    helper.unlink()
                except PermissionError:
                    remaining.append(helper)
            if remaining and time.monotonic() >= deadline:
                raise RuntimeError('Compiled update helper did not exit')
            helpers = remaining
            if helpers:
                time.sleep(.2)
        assert json.loads((stage / 'result.json').read_text(encoding='utf-8'))['status'] == 'success'
        assert updates.file_digest(app) == digest
        assert updates.file_digest(stage / 'previous.exe') == digest
        assert json.loads(settings.read_text(encoding='utf-8'))['interval'] == 237
        # The GUI's normal offline refresh may rewrite the saved snapshot.
        # Exact preservation by the replacement itself was checked above.
        assert snapshot.is_file() and isinstance(json.loads(snapshot.read_text(encoding='utf-8')), list)

    print('Portable EXE verified: bundled GUI, update dialog/helper, atomic replacement, backup, settings, Chinese path, no Python on PATH.')


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build Windows executables on Windows.')
    import tkinter
    import runtime_paths
    tag = os.environ.get('GITHUB_REF', '')
    if tag.startswith('refs/tags/') and tag != 'refs/tags/v' + runtime_paths.VERSION:
        raise SystemExit('Release tag must match the application version.')
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
