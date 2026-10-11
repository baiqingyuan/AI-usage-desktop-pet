"""Read-only release checks and a confirmed, atomic portable EXE update."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
from urllib import request, error, parse

import runtime_paths

REPOSITORY = 'baiqingyuan/AI-usage-desktop-pet'
API_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases/latest'
MAX_PACKAGE = 128 * 1024 * 1024


class UpdateError(Exception):
    pass


class Cancelled(UpdateError):
    pass


def version_tuple(value):
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', str(value))
    if not match:
        raise UpdateError('版本信息不完整，请稍后重试。')
    return tuple(map(int, match.groups()))


def newer(latest, current=None):
    return version_tuple(latest) > version_tuple(current or runtime_paths.VERSION)


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    title: str
    notes: str
    published: str
    size: int
    package_url: str
    checksum_url: str
    digest: str = ''


def asset_url(tag, name):
    version_tuple(tag)
    return f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'


def parse_release(data):
    if not isinstance(data, dict) or data.get('draft') or data.get('prerelease'):
        raise UpdateError('暂未找到正式发布的版本。')
    tag = data.get('tag_name', '')
    version_tuple(tag)
    assets = {item.get('name'): item for item in data.get('assets', [])
              if isinstance(item, dict) and item.get('state', 'uploaded') == 'uploaded'}
    package, sums = assets.get('AIUsage.exe'), assets.get('SHA256SUMS.txt')
    if not package or not sums:
        raise UpdateError('新版本的安装包尚未就绪，请稍后重试。')
    for name, item in (('AIUsage.exe', package), ('SHA256SUMS.txt', sums)):
        if item.get('browser_download_url') != asset_url(tag, name):
            raise UpdateError('安装包来源不匹配，已停止更新。')
    size = package.get('size')
    if not isinstance(size, int) or not 1 <= size <= MAX_PACKAGE:
        raise UpdateError('安装包大小异常，已停止更新。')
    digest = package.get('digest') or ''
    if digest and not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise UpdateError('安装包校验信息不完整。')
    return Release(tag.removeprefix('v'), tag, str(data.get('name') or tag)[:160],
                   str(data.get('body') or '此版本未提供更新说明。')[:30000],
                   str(data.get('published_at') or ''), size,
                   package['browser_download_url'], sums['browser_download_url'],
                   digest.removeprefix('sha256:').lower())


class TrustedRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = parse.urlsplit(newurl)
        host = (target.hostname or '').lower()
        if (target.scheme != 'https' or target.username or target.password
                or target.port not in (None, 443)
                or not (host in ('github.com', 'api.github.com')
                        or host.endswith('.githubusercontent.com'))):
            raise UpdateError('下载跳转来源不匹配，已停止更新。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    req = request.Request(url, headers={
        'User-Agent': 'AIUsageDesktop/' + runtime_paths.VERSION,
        'Accept': 'application/vnd.github+json' if url == API_URL else 'application/octet-stream',
    })
    # Update requests never carry provider keys, Codex credentials or GitHub tokens.
    return request.build_opener(TrustedRedirect()).open(req, timeout=18)


def fetch_latest():
    with open_url(API_URL) as response:
        raw = response.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise UpdateError('版本信息过大，请稍后重试。')
    try:
        return parse_release(json.loads(raw))
    except (ValueError, TypeError, KeyError) as exc:
        raise UpdateError('版本信息暂时不可用，请稍后重试。') from exc


def checksum_text(text):
    matches = [match.group(1).lower() for line in text.splitlines()
               if (match := re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?AIUsage\.exe', line.strip()))]
    if len(matches) != 1:
        raise UpdateError('校验文件无效，已停止更新。')
    return matches[0]


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(256 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_executable(path, expected):
    if not re.fullmatch(r'[0-9a-f]{64}', expected) or file_digest(path) != expected:
        raise UpdateError('安装包校验失败，当前版本未被替换。')
    with open(path, 'rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise UpdateError('安装包格式不正确，当前版本未被替换。')
        offset = struct.unpack_from('<I', header, 60)[0]
        if not 64 <= offset <= min(path.stat().st_size - 26, 1024 * 1024):
            raise UpdateError('安装包格式不正确，当前版本未被替换。')
        stream.seek(offset)
        pe = stream.read(26)
        if pe[:4] != b'PE\0\0' or pe[4:6] != b'\x64\x86' or pe[24:26] != b'\x0b\x02':
            raise UpdateError('安装包不是支持的 Windows 64 位版本。')


def download_release(release, folder, progress=lambda _: None, cancel=None):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / ('candidate-' + uuid.uuid4().hex + '.part')
    candidate = partial.with_suffix('.exe')
    try:
        for name, url in (('AIUsage.exe', release.package_url), ('SHA256SUMS.txt', release.checksum_url)):
            if url != asset_url(release.tag, name):
                raise UpdateError('安装包来源不匹配，已停止更新。')
        with open_url(release.checksum_url) as response:
            raw = response.read(4097)
        if len(raw) > 4096:
            raise UpdateError('校验文件无效，已停止更新。')
        expected = checksum_text(raw.decode('ascii'))
        if release.digest and release.digest != expected:
            raise UpdateError('安装包校验信息不一致，已停止更新。')
        total = 0
        deadline = time.monotonic() + 180
        with open_url(release.package_url) as response, partial.open('wb') as stream:
            while True:
                if cancel and cancel.is_set():
                    raise Cancelled('已取消更新。')
                if time.monotonic() > deadline:
                    raise UpdateError('下载超时，请稍后重试。')
                block = response.read(128 * 1024)
                if not block:
                    break
                total += len(block)
                if total > release.size or total > MAX_PACKAGE:
                    raise UpdateError('安装包大小不匹配，已停止更新。')
                stream.write(block)
                progress(min(99, int(total * 100 / release.size)))
        if total != release.size:
            raise UpdateError('安装包下载不完整，请重试。')
        if cancel and cancel.is_set():
            raise Cancelled('已取消更新。')
        verify_executable(partial, expected)
        os.replace(partial, candidate)
        progress(100)
        return candidate, expected
    finally:
        partial.unlink(missing_ok=True)


def safe_error(exc):
    if isinstance(exc, UpdateError):
        return str(exc)
    if isinstance(exc, error.HTTPError):
        return '检查暂时受限，请稍后重试。' if exc.code in (403, 429) else '更新服务器暂时不可用，请重试。'
    if isinstance(exc, (error.URLError, TimeoutError)):
        return '无法连接更新服务器，请检查网络后重试。'
    if isinstance(exc, OSError):
        return '无法保存更新文件，请确认程序文件夹可写。'
    return '更新未能完成，当前版本仍可使用。'


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def create_plan(candidate, expected, target, version, parent_pid=None, relaunch_args=None):
    target, candidate = Path(target).resolve(), Path(candidate).resolve()
    stage = target.parent / '.updates'
    if candidate.parent != stage.resolve() or target.suffix.lower() != '.exe':
        raise UpdateError('更新路径无效，已停止更新。')
    verify_executable(candidate, expected)
    version_tuple(version)
    nonce = uuid.uuid4().hex
    path = stage / ('plan-' + nonce + '.json')
    write_json(path, {'target': str(target), 'candidate': str(candidate),
                      'sha256': expected, 'version': version, 'nonce': nonce,
                      'parent_pid': os.getpid() if parent_pid is None else parent_pid,
                      'relaunch_args': relaunch_args or []})
    return path


def load_plan(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    nonce = data.get('nonce', '')
    stage = path.parent
    if stage.name != '.updates' or not re.fullmatch(r'[0-9a-f]{32}', nonce) or path.name != 'plan-' + nonce + '.json':
        raise UpdateError('更新计划无效。')
    target, candidate = Path(data['target']).resolve(), Path(data['candidate']).resolve()
    if target.parent != stage.parent or candidate.parent != stage or target.suffix.lower() != '.exe' or not target.is_file():
        raise UpdateError('更新路径无效。')
    if data.get('relaunch_args') not in ([], ['--details'], ['--offline']):
        raise UpdateError('更新启动参数无效。')
    if not isinstance(data.get('parent_pid'), int) or not 0 <= data['parent_pid'] < 2**32:
        raise UpdateError('更新计划无效。')
    version_tuple(data['version'])
    verify_executable(candidate, data['sha256'])
    return data, target, candidate, stage


def launch_helper(plan, relaunch=True):
    data, target, candidate, stage = load_plan(plan)
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
    if getattr(sys, 'frozen', False):
        helper = stage / ('helper-' + data['nonce'] + '.exe')
        shutil.copy2(sys.executable, helper)
        command = [str(helper), '--apply-update', str(plan)]
    else:
        command = [sys.executable, str(runtime_paths.ROOT / 'app.py'), '--apply-update', str(plan)]
    if not relaunch:
        command.append('--update-no-relaunch')
    process = subprocess.Popen(command, cwd=target.parent, env=env,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    return process, stage / ('ready-' + data['nonce'] + '.json')


def helper_ready(plan, ready):
    try:
        if ready.stat().st_size > 4096:
            return False
        value = json.loads(ready.read_text(encoding='utf-8'))
        nonce = Path(plan).stem.removeprefix('plan-')
        pid = value.get('pid')
        return (value.get('nonce') == nonce and type(pid) is int
                and 0 < pid < 2**32 and parent_alive(pid))
    except (OSError, ValueError, AttributeError, UpdateError):
        return False


def parent_alive(pid):
    if not pid:
        return False
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 5:
            raise UpdateError('无法确认旧程序已退出，已停止更新。')
        return False
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        result = kernel.WaitForSingleObject(handle, 0)
        if result not in (0, 258):
            raise UpdateError('无法确认旧程序已退出，已停止更新。')
        return result == 258
    finally:
        kernel.CloseHandle(handle)


def restart(target, args):
    return subprocess.Popen([str(target), *args], cwd=target.parent,
                            env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'),
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def apply_plan(path, relaunch=True, timeout=75):
    data, target, candidate, stage = load_plan(path)
    cancel = stage / ('cancel-' + data['nonce'] + '.json')
    ready = stage / ('ready-' + data['nonce'] + '.json')
    backup = stage / 'previous.exe'
    result = stage / 'result.json'
    write_json(ready, {'nonce': data['nonce'], 'pid': os.getpid()})
    changed = False
    deadline = time.monotonic() + timeout
    try:
        while parent_alive(data['parent_pid']):
            if cancel.exists():
                return False
            if time.monotonic() >= deadline:
                raise UpdateError('旧程序仍在运行，未能替换；请退出后重试。')
            time.sleep(.2)
        if cancel.exists():
            return False
        temporary_backup = stage / 'previous.tmp'
        shutil.copy2(target, temporary_backup)
        os.replace(temporary_backup, backup)
        verify_executable(candidate, data['sha256'])
        while True:
            if cancel.exists():
                return False
            try:
                os.replace(candidate, target)
                changed = True
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise UpdateError('程序文件仍被占用，未能替换；请稍后重试。')
                time.sleep(.2)
        write_json(result, {'status': 'success', 'version': data['version']})
        if relaunch:
            restart(target, data['relaunch_args'])
        return True
    except Exception as exc:
        if changed:
            restore = stage / 'restore.tmp'
            shutil.copy2(backup, restore)
            os.replace(restore, target)
        write_json(result, {'status': 'failed', 'message': safe_error(exc)})
        if relaunch and not parent_alive(data['parent_pid']):
            restart(target, [])
        return False


def cleanup_stage(folder):
    stage = Path(folder) / '.updates'
    if not stage.is_dir() or stage.resolve().parent != Path(folder).resolve():
        return
    for item in stage.iterdir():
        if re.fullmatch(r'(?:helper-[0-9a-f]{32}\.exe|candidate-[0-9a-f]{32}\.(?:exe|part)|(?:plan|ready|cancel)-[0-9a-f]{32}\.json)', item.name):
            try:
                item.unlink()
            except OSError:
                pass
