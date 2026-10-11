"""Soft update details, optional download and background release checks."""
from datetime import datetime
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import core
import runtime_paths
import updates
from soft_controls import RoundedButton
from ui_helpers import BG, CARD, GREEN, TEXT, MUTED, RED, FONT, SoftCard, center_window


def text(parent, value='', size=11, color=TEXT, bold=False, **kwargs):
    return tk.Label(parent, text=value, bg=parent.cget('bg'), fg=color,
                    font=(FONT, size, 'bold' if bold else 'normal'), **kwargs)


def set_button(button, value, enabled=True):
    button.text = value
    button.configure(state='normal' if enabled else 'disabled', cursor='hand2' if enabled else 'arrow')
    button.draw()


class UpdateManager:
    def __init__(self, app):
        self.app = app
        self.latest = None
        self.state = 'idle'
        self.error = ''
        self.progress = 0
        self.window = None
        self.mailbox = queue.Queue()
        self.next_check = time.monotonic() + 5
        self.next_cleanup = time.monotonic() + 30
        self.cancel = threading.Event()
        self.worker = None
        self.helper = None
        self.ready_file = None
        self.plan = None
        self.deadline = 0
        result = runtime_paths.ROOT / '.updates' / 'result.json'
        if not app.offline and result.exists():
            try:
                value = json.loads(result.read_text(encoding='utf-8'))
                if value.get('status') == 'failed':
                    self.error = value.get('message', '上次更新未能完成，可重新检查。')
                    self.state = 'error'
                result.unlink()
            except (OSError, ValueError):
                pass
        if not app.offline:
            updates.cleanup_stage(runtime_paths.ROOT)

    @property
    def available(self):
        return self.latest is not None and updates.newer(self.latest.version)

    @property
    def busy(self):
        return self.state in ('checking', 'downloading', 'applying')

    def show(self, refresh=False):
        if self.window and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_set()
            if refresh and not self.busy:
                self.check()
            return self.window
        self.window = UpdateWindow(self.app, self)
        if (refresh or not self.latest) and not self.busy:
            self.check()
        return self.window

    def redraw(self):
        self.app.surface.render()
        if self.app.details and self.app.details.winfo_exists():
            self.app.details.update_update_button()
        if self.window and self.window.winfo_exists():
            self.window.render()

    def check(self):
        if self.busy:
            return
        self.next_check = time.monotonic() + 6 * 3600
        if self.app.offline:
            self.error = '当前处于离线模式，无法检查更新。'
            self.state = 'error'
            self.redraw()
            return
        self.state, self.error = 'checking', ''
        self.redraw()

        def worker():
            try:
                self.mailbox.put(('release', updates.fetch_latest()))
            except Exception as exc:
                self.mailbox.put(('error', updates.safe_error(exc)))
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def confirm(self):
        if self.busy or not self.available:
            return
        self.state, self.error, self.progress = 'downloading', '', 0
        self.cancel = threading.Event()
        release, cancel = self.latest, self.cancel
        self.redraw()

        def worker():
            try:
                candidate, digest = updates.download_release(
                    release, runtime_paths.ROOT / '.updates',
                    lambda value: self.mailbox.put(('progress', value)), cancel)
                self.mailbox.put(('downloaded', (candidate, digest, release.version)))
            except updates.Cancelled:
                self.mailbox.put(('cancelled', None))
            except Exception as exc:
                self.mailbox.put(('error', updates.safe_error(exc)))
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def start_helper(self, candidate, digest, version, relaunch=True):
        if self.cancel.is_set():
            candidate.unlink(missing_ok=True)
            self.state = 'idle'
            return
        try:
            target = runtime_paths.launcher()
            if not target.is_file():
                raise updates.UpdateError('请使用免安装 EXE 版本进行内置更新。')
            args = ['--details'] if self.app.details and self.app.details.winfo_exists() else []
            self.plan = updates.create_plan(candidate, digest, target, version, relaunch_args=args)
            self.helper, self.ready_file = updates.launch_helper(self.plan, relaunch=relaunch)
            self.deadline = time.monotonic() + 20
            self.state = 'applying'
        except Exception as exc:
            self.state, self.error = 'error', updates.safe_error(exc)

    def poll(self):
        changed = False
        while True:
            try:
                kind, value = self.mailbox.get_nowait()
            except queue.Empty:
                break
            if kind == 'release':
                self.latest, self.state, self.error = value, 'idle', ''
            elif kind == 'progress':
                self.progress = value
            elif kind == 'downloaded':
                self.start_helper(*value)
            elif kind == 'cancelled':
                self.state = 'idle'
            else:
                self.state, self.error = 'error', value
            changed = True
        if self.state == 'applying':
            if updates.helper_ready(self.plan, self.ready_file) and self.helper.poll() is None:
                # The helper has validated its plan and is waiting for this
                # instance to exit; the bootloader's file lock is also retried.
                self.app.surface.quit()
                return False
            if self.helper.poll() is not None or time.monotonic() > self.deadline:
                marker = self.plan.with_name(self.plan.name.replace('plan-', 'cancel-'))
                updates.write_json(marker, {'cancelled': True})
                self.state, self.error = 'error', '更新准备未能完成，当前版本仍可使用。'
                changed = True
        if changed:
            self.redraw()
        if not self.app.offline and not self.busy and time.monotonic() >= self.next_check:
            self.check()
        if not self.app.offline and not self.busy and (not self.helper or self.helper.poll() is not None) and time.monotonic() >= self.next_cleanup:
            updates.cleanup_stage(runtime_paths.ROOT)
            self.next_cleanup = time.monotonic() + 6 * 3600
        return True


class UpdateWindow(tk.Toplevel):
    def __init__(self, app, manager):
        super().__init__(app.root)
        self.app, self.manager = app, manager
        self.notes_value = None
        self.title('AI 用量 · 软件更新')
        self.configure(bg=BG)
        self.attributes('-topmost', app.config['topmost'])
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _: self.close())
        anchor = app.active_panel() or app.root
        if app.details and app.details.winfo_exists():
            self.transient(app.details)
        heading = tk.Frame(self, bg=BG, padx=26, pady=20)
        heading.pack(fill='x')
        text(heading, '软件更新', 22, bold=True).pack(anchor='w')
        text(heading, '按你的选择更新，账号设置与用量记录继续保留。', 11, MUTED).pack(anchor='w', pady=(6, 0))
        card = SoftCard(self, padx=22, pady=18)
        card.pack(fill='both', expand=True, padx=20)
        self.headline = text(card.body, '', 18, GREEN, True)
        self.headline.pack(anchor='w')
        self.versions = text(card.body, '', 11, MUTED, wraplength=560, justify='left')
        self.versions.pack(anchor='w', pady=(8, 14))
        area = tk.Frame(card.body, bg=CARD)
        area.pack(fill='both', expand=True)
        self.notes = tk.Text(area, bg=CARD, fg=TEXT, font=(FONT, 11),
                             wrap='word', relief='flat', bd=0, highlightthickness=0,
                             padx=0, pady=5, spacing1=4, spacing3=5, cursor='arrow',
                             height=9, width=1, state='disabled')
        scroll = ttk.Scrollbar(area, orient='vertical', command=self.notes.yview)
        self.notes.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.notes.pack(fill='both', expand=True)
        self.status_label = text(self, '', 11, MUTED, wraplength=600, justify='left', anchor='w')
        self.status_label.pack(fill='x', padx=26, pady=(12, 4))
        self.progress_bar = ttk.Progressbar(self, mode='determinate', maximum=100)
        self.progress_bar.pack(fill='x', padx=26, pady=(0, 6))
        footer = tk.Frame(self, bg=BG, padx=24, pady=14)
        footer.pack(fill='x')
        self.check_button = RoundedButton(footer, '检查更新', manager.check)
        self.check_button.pack(side='left')
        self.confirm_button = RoundedButton(footer, '确认更新', manager.confirm, True)
        self.confirm_button.pack(side='right')
        self.close_button = RoundedButton(footer, '稍后再说', self.close)
        self.close_button.pack(side='right', padx=8)
        center_window(self, anchor, 680, 640)
        app.style_window(self)
        app.panel_opened(self)
        self.grab_set()
        self.focus_set()
        self.render()

    def render(self):
        manager = self.manager
        release = manager.latest
        if release and manager.available:
            title = '发现新版本 v' + release.version
            versions = f'当前 v{runtime_paths.VERSION}  →  新版 v{release.version}  ·  {release.size / 1024 / 1024:.1f} MB'
            if release.published:
                try:
                    date = datetime.fromisoformat(release.published.replace('Z', '+00:00')).astimezone(core.TZ)
                    versions += f'  ·  {date:%Y-%m-%d}'
                except ValueError:
                    pass
            body = release.notes.replace('**', '').replace(chr(96), '')
        else:
            title = '正在检查新版本…' if manager.state == 'checking' else '暂无可用更新'
            versions = f'当前版本 v{runtime_paths.VERSION}'
            body = '有新版本时，机器人脚下会出现红色 New 按钮。\n\n点击后先查看更新说明，确认后才会下载安装。'
        self.headline.configure(text=title)
        self.versions.configure(text=versions)
        if body != self.notes_value:
            self.notes.configure(state='normal')
            self.notes.delete('1.0', 'end')
            self.notes.insert('1.0', body)
            self.notes.configure(state='disabled')
            self.notes_value = body
        status = manager.error or {
            'checking': '正在检查版本，请稍候…',
            'downloading': f'正在下载 {manager.progress}% · 可取消',
            'applying': '正在准备替换程序，完成后会自动重新打开。',
        }.get(manager.state, '确认后将下载安装、校验并重新打开程序。' if manager.available else '检查完成，当前版本可继续使用。')
        self.status_label.configure(text=status, fg=RED if manager.error else MUTED)
        self.progress_bar.configure(value=manager.progress if manager.state in ('downloading', 'applying') else 0)
        set_button(self.confirm_button, '更新中…' if manager.state in ('downloading', 'applying') else '确认更新',
                   manager.available and not manager.busy)
        set_button(self.check_button, '检查更新', not manager.busy)
        set_button(self.close_button, '取消下载' if manager.state == 'downloading' else '稍后再说',
                   manager.state != 'applying')

    def close(self):
        if self.manager.state == 'applying':
            return
        if self.manager.state == 'downloading':
            self.manager.cancel.set()
        self.grab_release()
        self.manager.window = None
        super().destroy()
        self.app.panel_closed(self)

    def destroy(self):
        self.close()
