"""Read-only usage adapters. No model requests, credential scraping, or billing changes."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib import request, parse, error

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / 'settings.json'
SNAPSHOT = ROOT / 'snapshot.json'
TZ = timezone(timedelta(hours=8))
PROVIDERS = {
    'codex': ('Codex', None),
    'openai': ('OpenAI API', 'OPENAI_ADMIN_KEY'),
    'anthropic': ('Claude API', 'ANTHROPIC_ADMIN_KEY'),
    'deepseek': ('DeepSeek API', 'DEEPSEEK_API_KEY'),
}


def now():
    return datetime.now(TZ)


def atomic_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def load_settings():
    default = {'interval': 120, 'topmost': True, 'alert_percent': 80,
               'providers': {k: {'enabled': True, 'expiry': ''} for k in PROVIDERS},
               'manual': [], 'codex_executable': ''}
    if SETTINGS.exists():
        saved = json.loads(SETTINGS.read_text(encoding='utf-8-sig'))
        default.update({k: v for k, v in saved.items() if k != 'providers'})
        for k, v in saved.get('providers', {}).items():
            if k in default['providers']:
                default['providers'][k].update(v)
    default['interval'] = max(60, int(default['interval']))
    return default


class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(text, decrypt=False):
    """Windows DPAPI, current user; keys never saved as plaintext."""
    if os.name != 'nt':
        raise RuntimeError('保存密钥需要 Windows；其他系统请使用环境变量。')
    raw = base64.b64decode(text, validate=True) if decrypt else text.encode('utf-8')
    buf = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    fn.restype = ctypes.c_int
    if not fn(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise RuntimeError('Windows 密钥加解密失败。')
    try:
        result = ctypes.string_at(outgoing.data, outgoing.size)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        kernel.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))


def get_key(pid, settings):
    env = PROVIDERS[pid][1]
    if env and os.environ.get(env):
        return os.environ[env].strip()
    encrypted = settings['providers'][pid].get('key_encrypted')
    return protect(encrypted, decrypt=True) if encrypted else None


def compact(value):
    if value is None:
        return '—'
    v = float(value)
    if v >= 1e8:
        return f'{v / 1e8:.2f} 亿'
    if v >= 1e4:
        return f'{v / 1e4:.2f} 万'
    return f'{v:,.0f}'


def parse_expiry(value):
    if not value:
        return None
    value = value.strip()
    result = datetime.fromisoformat(value)
    if len(value) == 10:
        result = result.replace(hour=23, minute=59, second=59)
    if result.tzinfo is None:
        result = result.replace(tzinfo=TZ)
    return result.timestamp()


def remaining(stamp):
    if stamp is None:
        return '时间未提供'
    seconds = int(stamp - time.time())
    if seconds <= 0:
        return '已到期 / 等待刷新'
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    mins = seconds // 60
    return f'{days}天 {hours}小时 {mins}分' if days else f'{hours}小时 {mins}分'


def metric(label, value, **extra):
    return {'label': label, 'value': str(value), **extra}


def card(pid, source, metrics=None, status='ok', note=''):
    return {'id': pid, 'name': PROVIDERS.get(pid, (pid,))[0], 'source': source,
            'metrics': metrics or [], 'status': status, 'note': note,
            'updated_at': now().isoformat()}


def safe_error(exc):
    # Do not expose provider response bodies, request headers, keys or account IDs.
    if isinstance(exc, error.HTTPError):
        return {401: '密钥无效或已过期', 403: '权限不足，请检查 Admin Key 和组织权限',
                429: '请求过于频繁，下次自动重试'}.get(exc.code, f'服务返回 HTTP {exc.code}')
    if isinstance(exc, (TimeoutError, queue.Empty, subprocess.TimeoutExpired)):
        return '连接超时，下次自动重试'
    if isinstance(exc, error.URLError):
        return '网络连接失败，请检查网络或代理'
    return '读取失败，请检查配置；可以再次刷新'


def http_json(url, headers):
    req = request.Request(url, headers={'User-Agent': 'AIUsageDesktop/0.1.0', **headers})
    with request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def paged(url, params, headers):
    output = []
    visited = set()
    deadline = time.monotonic() + 50
    for _ in range(100):
        if time.monotonic() > deadline:
            raise TimeoutError()
        data = http_json(url + '?' + parse.urlencode(params, doseq=True), headers)
        output.extend(data.get('data', []))
        if not data.get('has_more'):
            return output
        cursor = data.get('next_page')
        if not cursor or cursor in visited:
            raise RuntimeError('invalid pagination')
        visited.add(cursor)
        params = {**params, 'page': cursor}
    raise RuntimeError('pagination limit')


def codex_binary(settings):
    configured = settings.get('codex_executable')
    if configured:
        return configured
    found = shutil.which('codex')
    if found:
        return found
    folder = Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI' / 'Codex' / 'bin'
    candidates = list(folder.glob('*/codex.exe'))
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    raise FileNotFoundError('Codex CLI missing')


def codex_rpc(settings):
    """Only initialize and two account reads; no threads/turns created."""
    binary = codex_binary(settings)
    if Path(binary).suffix.lower() in ('.cmd', '.bat'):
        raise RuntimeError('设置中请选择原生 codex.exe')
    p = subprocess.Popen([binary, 'app-server', '--stdio'], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, encoding='utf-8',
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    messages = queue.Queue()

    def reader():
        try:
            for line in p.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    pass
        finally:
            messages.put({'_closed': True})

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + 25

    def send(data):
        p.stdin.write(json.dumps(data) + '\n')
        p.stdin.flush()

    def receive(ids):
        results = {}
        while ids:
            wait = deadline - time.monotonic()
            if wait <= 0:
                raise TimeoutError()
            data = messages.get(timeout=wait)
            if data.get('_closed'):
                raise RuntimeError('app-server closed')
            if data.get('id') in ids:
                ident = data['id']
                results[ident] = data
                ids.remove(ident)
            elif 'id' in data and 'method' in data:
                # Externally managed authentication cannot be refreshed by this monitor.
                send({'id': data['id'], 'error': {'code': -32601, 'message': 'Unsupported by usage monitor'}})
        return results

    try:
        send({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'ai_usage_desktop', 'title': 'AI Usage Desktop', 'version': '0.1.0'},
            'capabilities': {'experimentalApi': True}}})
        init = receive({1})[1]
        if init.get('error'):
            raise RuntimeError('initialize failed')
        send({'method': 'initialized'})
        send({'id': 2, 'method': 'account/rateLimits/read', 'params': {
            'excludeResetCreditDetails': False, 'supportsLunaReserve': False}})
        send({'id': 3, 'method': 'account/usage/read'})
        return receive({2, 3})
    finally:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=3)
        thread.join(timeout=1)
        for pipe in (p.stdin, p.stdout):
            pipe.close()


class LocalCodexTokens:
    """Incremental, in-memory token-only scan; conversation text is never persisted."""
    def __init__(self, home=None):
        self.home = Path(home or os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        self.files = {}
        self.month = None
        self.latest_limit = None
        self.skipped = 0

    def scan(self):
        start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month = start.strftime('%Y-%m')
        if month != self.month:
            self.files.clear()
            self.month = month
        active = set()
        for folder in ('sessions', 'archived_sessions'):
            for file in (self.home / folder).rglob('*.jsonl'):
                try:
                    stat = file.stat()
                    if stat.st_mtime < start.timestamp():
                        continue
                    key = str(file)
                    active.add(key)
                    state = self.files.get(key)
                    if not state or stat.st_size < state['offset'] or (stat.st_size == state['offset'] and stat.st_mtime_ns != state['mtime']):
                        state = {'offset': 0, 'mtime': 0, 'previous': {}, 'events': {}}
                        self.files[key] = state
                    with file.open('rb') as handle:
                        handle.seek(state['offset'])
                        while True:
                            before = handle.tell()
                            line = handle.readline()
                            if not line:
                                break
                            if not line.endswith(b'\n'):
                                handle.seek(before)
                                break  # Retry partial writes at the next refresh.
                            if b'"token_count"' not in line:
                                continue
                            try:
                                event = json.loads(line)
                                payload = event.get('payload') or {}
                                if event.get('type') != 'event_msg' or payload.get('type') != 'token_count':
                                    continue
                                stamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
                                if payload.get('rate_limits'):
                                    if not self.latest_limit or stamp.timestamp() > self.latest_limit[0]:
                                        self.latest_limit = (stamp.timestamp(), payload['rate_limits'])
                                usage = (payload.get('info') or {}).get('total_token_usage')
                                if not usage:
                                    continue
                                counters = {k: int(usage.get(k) or 0) for k in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')}
                                previous = state['previous']
                                changed = any(counters[k] != previous.get(k, 0) for k in counters)
                                reset = counters['input_tokens'] + counters['output_tokens'] < previous.get('input_tokens', 0) + previous.get('output_tokens', 0)
                                delta = {k: max(0, v - (0 if reset else previous.get(k, 0))) for k, v in counters.items()}
                                state['previous'] = counters
                                if changed and stamp >= start:
                                    signature = hashlib.sha256((event['timestamp'] + json.dumps(counters, sort_keys=True)).encode()).hexdigest()
                                    state['events'][signature] = (stamp.astimezone(TZ).date().isoformat(), delta)
                            except (ValueError, TypeError, KeyError):
                                self.skipped += 1
                        state['offset'] = handle.tell()
                        state['mtime'] = stat.st_mtime_ns
                except OSError:
                    self.skipped += 1
        for key in set(self.files) - active:
            del self.files[key]
        events = {}
        for state in self.files.values():
            events.update(state['events'])  # Deduplicate inherited events in forked/archived logs.
        daily = {}
        for day, delta in events.values():
            row = daily.setdefault(day, {k: 0 for k in delta})
            for k, v in delta.items():
                row[k] += v
        total = sum(row['input_tokens'] + row['output_tokens'] for row in daily.values())
        today = daily.get(now().date().isoformat(), {})
        return {'today': today.get('input_tokens', 0) + today.get('output_tokens', 0),
                'month': total, 'daily': {d: r['input_tokens'] + r['output_tokens'] for d, r in daily.items()},
                'input': sum(r['input_tokens'] for r in daily.values()),
                'output': sum(r['output_tokens'] for r in daily.values()),
                'cached': sum(r['cached_input_tokens'] for r in daily.values()),
                'files': len(self.files), 'skipped': self.skipped}


def quota_metrics(snapshot, suffix=''):
    metrics = []
    for key in ('primary', 'secondary'):
        w = snapshot.get(key)
        if not w:
            continue
        minutes = w.get('windowDurationMins', w.get('window_minutes'))
        label = {300: '5 小时额度', 10080: '周额度'}.get(minutes, f'{minutes} 分钟额度' if minutes else '额度')
        used = w.get('usedPercent', w.get('used_percent'))
        if used is None:
            continue
        used = max(0, min(100, float(used)))
        metrics.append(metric(label + suffix, f'剩余 {100-used:g}%', used=used,
                              reset_at=w.get('resetsAt', w.get('resets_at'))))
    return metrics


def fetch_codex(settings, scanner):
    local = scanner.scan()
    metrics = [metric('今日 token · 本机', compact(local['today']), raw=local['today']),
               metric('本月 token · 本机', compact(local['month']), raw=local['month'])]
    note = '本机统计仅含此电脑的 Codex 日志，按北京时间；缓存输入和推理输出已包含在总数中。'
    result = card('codex', '本机日志', metrics, note=note)
    result['daily'] = local['daily']
    result['local_breakdown'] = local
    try:
        rpc = codex_rpc(settings)
        limits = rpc[2].get('result')
        activity = rpc[3].get('result')
        if limits:
            snapshots = limits.get('rateLimitsByLimitId') or {'codex': limits.get('rateLimits') or {}}
            for ident, snapshot in snapshots.items():
                if snapshot:
                    suffix = '' if ident == 'codex' else f' · {snapshot.get("limitName") or ident}'
                    result['metrics'].extend(quota_metrics(snapshot, suffix))
            main = limits.get('rateLimits') or {}
            result['plan'] = main.get('planType')
            if limits.get('ordinaryUsageAllowed') is not None:
                result['metrics'].append(metric('普通额度状态', '可用' if limits['ordinaryUsageAllowed'] else '受限'))
            individual = main.get('individualLimit')
            if individual:
                result['metrics'].append(metric('个人支出额度', f'{individual.get("used", "—")} / {individual.get("limit", "—")}',
                    used=100-float(individual['remainingPercent']), reset_at=individual.get('resetsAt')))
            if main.get('spendControlReached'):
                result['note'] += ' 已达到账户支出控制上限。'
            credit = main.get('credits') or {}
            if credit.get('unlimited'):
                result['metrics'].append(metric('积分', '不限额'))
            elif credit.get('balance') is not None:
                result['metrics'].append(metric('积分余额', credit['balance']))
            resets = limits.get('rateLimitResetCredits') or {}
            if resets.get('availableCount') is not None:
                result['metrics'].append(metric('可用重置券', resets['availableCount']))
            expiries = [c['expiresAt'] for c in resets.get('credits') or [] if c.get('expiresAt') and c.get('status') == 'available']
            if expiries:
                result['metrics'].append(metric('最早重置券到期', datetime.fromtimestamp(min(expiries), TZ).strftime('%m-%d %H:%M'), expiry_at=min(expiries)))
            result['source'] = '官方账户接口 + 本机日志'
        else:
            result['status'] = 'partial'
            result['note'] += ' 账户额度无法读取；请在 Codex 登录 ChatGPT。'
            if scanner.latest_limit:
                stamp, snapshot = scanner.latest_limit
                cached = quota_metrics(snapshot, ' · 日志快照')
                for item in cached:
                    item['observed_at'] = stamp
                    item['stale'] = True
                result['metrics'].extend(cached)
        if activity:
            summary = activity.get('summary') or {}
            for key, label in [('lifetimeTokens', '账户累计 token'), ('peakDailyTokens', '历史单日峰值 token'),
                               ('currentStreakDays', '连续活跃天数')]:
                if summary.get(key) is not None:
                    result['metrics'].append(metric(label, compact(summary[key]), raw=summary[key]))
            buckets = activity.get('dailyUsageBuckets')
            if buckets:
                result['account_daily'] = {b['startDate']: b['tokens'] for b in buckets if b.get('tokens') is not None}
                result['note'] += f' 账户日报最新日期：{max(result["account_daily"])}，可能有延迟。'
        else:
            result['note'] += ' 账户 token 汇总暂不可用。'
    except Exception as exc:
        result['status'] = 'partial'
        result['note'] += ' ' + safe_error(exc) + '；本机统计仍可用。'
    if not local['files']:
        result['note'] += ' 未找到本月日志；本机统计可能不完整。'
    return result


def fetch_openai(settings):
    key = get_key('openai', settings)
    if not key:
        return card('openai', '待连接', status='unconfigured', note='在设置中填入 OpenAI Admin Key；普通模型 API Key 无法读取组织用量。')
    utc = datetime.now(timezone.utc)
    start = utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    headers = {'Authorization': 'Bearer ' + key}
    params = {'start_time': int(start.timestamp()), 'end_time': int(utc.timestamp()), 'bucket_width': '1d', 'limit': 31}
    rows = paged('https://api.openai.com/v1/organization/usage/completions', params, headers)
    daily = {}
    cached = 0
    requests = 0
    for bucket in rows:
        day = datetime.fromtimestamp(bucket['start_time'], timezone.utc).date().isoformat()
        daily[day] = daily.get(day, 0) + sum(int(r.get('input_tokens') or 0) + int(r.get('output_tokens') or 0) for r in bucket.get('results', []))
        for r in bucket.get('results', []):
            cached += int(r.get('input_cached_tokens') or 0)
            requests += int(r.get('num_model_requests') or 0)
    result = card('openai', '官方组织接口 · UTC', [
        metric('今日 token · UTC', compact(daily.get(utc.date().isoformat(), 0))),
        metric('本月 token · UTC', compact(sum(daily.values()))),
        metric('本月缓存输入 token', compact(cached)), metric('本月请求数', compact(requests))],
        note='组织范围的 completions 统计；不覆盖独立的音频、图像、嵌入等用量。报表可能有延迟。')
    result['daily'] = daily
    try:
        costs = paged('https://api.openai.com/v1/organization/costs', params, headers)
        amount = sum((Decimal(str(r.get('amount', {}).get('value', 0))) for b in costs for r in b.get('results', []) if r.get('amount', {}).get('currency', 'usd').lower() == 'usd'), Decimal(0))
        result['metrics'].append(metric('本月费用 · USD', f'${amount:.2f}'))
    except Exception as exc:
        result['status'] = 'partial'
        result['note'] += ' 费用：' + safe_error(exc)
    return result


def fetch_anthropic(settings):
    key = get_key('anthropic', settings)
    if not key:
        return card('anthropic', '待连接', status='unconfigured', note='需要 Claude 组织 Admin Key；个人订阅可在设置中手动记录额度和到期日。')
    utc = datetime.now(timezone.utc)
    params = {'starting_at': utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ'),
              'ending_at': utc.strftime('%Y-%m-%dT%H:%M:%SZ'), 'bucket_width': '1d', 'limit': 31}
    rows = paged('https://api.anthropic.com/v1/organizations/usage_report/messages', params,
                 {'x-api-key': key, 'anthropic-version': '2023-06-01'})
    daily = {}
    cached = 0
    for b in rows:
        day = b['starting_at'][:10]
        count = 0
        for r in b.get('results', []):
            cache = r.get('cache_creation') or {}
            cache_write = int(cache.get('ephemeral_1h_input_tokens') or 0) + int(cache.get('ephemeral_5m_input_tokens') or 0)
            cache_read = int(r.get('cache_read_input_tokens') or 0)
            count += int(r.get('uncached_input_tokens') or 0) + int(r.get('output_tokens') or 0) + cache_write + cache_read
            cached += cache_read
        daily[day] = daily.get(day, 0) + count
    result = card('anthropic', '官方组织接口 · UTC', [
        metric('今日 token · UTC', compact(daily.get(utc.date().isoformat(), 0))),
        metric('本月 token · UTC', compact(sum(daily.values()))),
        metric('本月缓存读取 token', compact(cached))], note='Claude API 组织用量，包含缓存创建及读取。个人 Claude / Claude Code 订阅额度需手动记录。')
    result['daily'] = daily
    return result


def fetch_deepseek(settings):
    key = get_key('deepseek', settings)
    if not key:
        return card('deepseek', '待连接', status='unconfigured', note='在设置中填入 DeepSeek API Key，即可自动查询余额。')
    data = http_json('https://api.deepseek.com/user/balance', {'Authorization': 'Bearer ' + key})
    metrics = []
    for balance in data.get('balance_infos', []):
        currency = balance.get('currency', '')
        metrics.extend([metric('可用余额 · ' + currency, balance.get('total_balance', '—')),
                        metric('赠金余额 · ' + currency, balance.get('granted_balance', '—')),
                        metric('充值余额 · ' + currency, balance.get('topped_up_balance', '—'))])
    return card('deepseek', '官方余额接口', metrics,
                note='此接口不返回账户历史 token 或赠金到期日；可在设置里补充手动记录。' + (' 当前余额不可调用。' if data.get('is_available') is False else ''))


def manual_card(item):
    ident = item.get('id', 'manual')
    metrics = []
    for key, label in [('tokens_today', '今日 token · 手动'), ('tokens_month', '本月 token · 手动'), ('balance', '余额 · 手动')]:
        if item.get(key) not in (None, ''):
            metrics.append(metric(label, compact(item[key]) if key.startswith('tokens') else item[key]))
    if item.get('used_percent') not in (None, ''):
        used = float(item['used_percent'])
        metrics.append(metric('额度 · 手动', f'剩余 {100-used:g}%', used=used, reset_at=parse_expiry(item.get('reset_date'))))
    result = card(ident, '手动记录', metrics, status='manual', note=item.get('note') or '手动数据不会自动更新；通过设置维护。')
    result['name'] = item['name']
    result['updated_at'] = item.get('updated_at', now().isoformat())
    return result


def expiry_metric(value):
    stamp = parse_expiry(value)
    return metric('订阅 / 余额到期 · 手动', datetime.fromtimestamp(stamp, TZ).strftime('%Y-%m-%d'), expiry_at=stamp) if stamp else None


ADAPTERS = {'openai': fetch_openai, 'anthropic': fetch_anthropic, 'deepseek': fetch_deepseek}


def fetch_provider(pid, settings, scanner=None):
    conf = settings['providers'].get(pid, {}) if pid in PROVIDERS else next(x for x in settings['manual'] if x['id'] == pid)
    try:
        if pid == 'codex':
            result = fetch_codex(settings, scanner or LocalCodexTokens())
        elif pid in ADAPTERS:
            result = ADAPTERS[pid](settings)
        else:
            result = manual_card(conf)
        expiry = expiry_metric(conf.get('expiry'))
        if expiry:
            result['metrics'].append(expiry)
        return result
    except Exception as exc:
        return card(pid, '读取失败', status='error', note=safe_error(exc))


def validate_manual(item):
    if not item.get('name', '').strip():
        raise ValueError('请填写平台名称。')
    for key in ('tokens_today', 'tokens_month', 'balance', 'used_percent'):
        if item.get(key) not in (None, ''):
            v = float(item[key])
            if not math.isfinite(v) or v < 0:
                raise ValueError('数字必须为非负有限数。')
            if key == 'used_percent' and v > 100:
                raise ValueError('已用百分比必须为 0 到 100。')
    for key in ('expiry', 'reset_date'):
        if item.get(key):
            parse_expiry(item[key])


def alerts(results, threshold=80):
    output = []
    for result in results:
        if result.get('status') == 'error' or result.get('stale'):
            continue
        for m in result.get('metrics', []):
            if m.get('used', 0) >= threshold and not m.get('stale') and (m.get('reset_at') is None or m['reset_at'] > time.time()):
                output.append(f'{result["name"]}：{m["label"]} 已用 {m["used"]:g}%')
            stamp = m.get('expiry_at')
            if stamp and stamp - time.time() <= 3 * 86400:
                output.append(f'{result["name"]}：{m["label"]} {remaining(stamp)}')
    return output
