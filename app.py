"""AI 用量桌面小组件 — Python standard library only."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import csv
import json
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk, messagebox, filedialog

import core
from soft_controls import RoundedButton, RoundedSelect
from ui_helpers import (BG, CARD, PANEL, TEXT, MUTED, GREEN, BLUE, AMBER, RED,
                        SoftCard, fitted_label, center_window, rounded)

FONT = 'Microsoft YaHei'
COLORS = {'codex': GREEN, 'openai': BLUE, 'anthropic': '#9a745d', 'deepseek': '#81799b'}


def label(parent, text, size=10, color=TEXT, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color,
                    font=(FONT, max(11, size), 'bold' if bold else 'normal'), **kwargs)


def button(parent, text, command, accent=False):
    return RoundedButton(parent, text, command, accent)


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.config = deepcopy(app.config)
        app.settings = self
        self.title('平台与提醒设置')
        self.configure(bg=BG)
        if app.details and app.details.winfo_exists():
            self.transient(app.details)
        self.attributes('-topmost', app.config['topmost'])
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.bind('<Escape>', lambda _: self.destroy())
        heading = tk.Frame(self, bg=BG, padx=22, pady=15)
        heading.pack(fill='x')
        label(heading, '连接你的 AI 平台', 20, bold=True).pack(anchor='w')
        label(heading, '平台连接、有效期与用量提醒，集中管理。', 11, MUTED).pack(anchor='w', pady=(6, 0))
        tabs = ttk.Notebook(self)
        tabs.pack(fill='both', expand=True, padx=18)
        general = tk.Frame(tabs, bg=BG, padx=18, pady=15)
        connection_shell = tk.Frame(tabs, bg=BG)
        connection_canvas = tk.Canvas(connection_shell, bg=BG, highlightthickness=0)
        connection_scrollbar = ttk.Scrollbar(connection_shell, orient='vertical', command=connection_canvas.yview)
        connection_canvas.configure(yscrollcommand=connection_scrollbar.set)
        connection_scrollbar.pack(side='right', fill='y')
        connection_canvas.pack(side='left', fill='both', expand=True)
        connections = tk.Frame(connection_canvas, bg=BG, padx=18, pady=12)
        connection_window = connection_canvas.create_window(0, 0, window=connections, anchor='nw')
        connections.bind('<Configure>', lambda _: connection_canvas.configure(scrollregion=connection_canvas.bbox('all')))
        connection_canvas.bind('<Configure>', lambda e: connection_canvas.itemconfigure(connection_window, width=e.width))
        self.bind('<MouseWheel>', lambda e: connection_canvas.yview_scroll(int(-e.delta / 120), 'units') if tabs.select() == str(connection_shell) and e.widget.winfo_toplevel() == self else None)
        manual = tk.Frame(tabs, bg=BG, padx=18, pady=15)
        tabs.add(connection_shell, text='自动连接')
        tabs.add(manual, text='手动平台')
        tabs.add(general, text='刷新与提醒')
        self.fields = {}
        for pid, (name, env) in core.PROVIDERS.items():
            shell = SoftCard(connections, padx=18, pady=16)
            shell.pack(fill='x', pady=(0, 12))
            frame = shell.body
            enabled = tk.BooleanVar(value=self.config['providers'][pid].get('enabled', True))
            tk.Checkbutton(frame, text=name, variable=enabled, bg=CARD, fg=TEXT,
                           selectcolor=PANEL, activebackground=CARD, activeforeground=TEXT,
                           font=(FONT, 12, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w')
            expiry = tk.StringVar(value=self.config['providers'][pid].get('expiry', ''))
            key = tk.StringVar()
            clear = tk.BooleanVar()
            if env:
                label(frame, '组织管理密钥' if pid != 'deepseek' else 'API 密钥', 11, MUTED).grid(row=1, column=0, sticky='w', pady=7)
                ttk.Entry(frame, textvariable=key, show='•', width=40).grid(row=1, column=1, sticky='ew', padx=(12, 0))
                state = '已保存密钥；留空保留' if self.config['providers'][pid].get('key_encrypted') else '填写密钥，或沿用环境中已有配置'
                label(frame, state, 8, MUTED).grid(row=2, column=0, columnspan=2, sticky='w')
                tk.Checkbutton(frame, text='清除已保存密钥', variable=clear, bg=CARD, fg=MUTED,
                           selectcolor=PANEL, activebackground=CARD, font=(FONT, 11)).grid(row=3, column=1, sticky='e', pady=4)
            else:
                label(frame, '沿用 Codex 已有登录；无需复制登录令牌。', 9, MUTED).grid(row=1, column=0, columnspan=2, sticky='w', pady=4)
            label(frame, '到期日（手动）', 11, MUTED).grid(row=4, column=0, sticky='w', pady=(5, 0))
            ttk.Entry(frame, textvariable=expiry, width=24).grid(row=4, column=1, sticky='ew', padx=(12, 0), pady=(5, 0))
            frame.columnconfigure(1, weight=1)
            self.fields[pid] = {'enabled': enabled, 'key': key, 'clear': clear, 'expiry': expiry}
        label(connections, '到期日格式：2026-11-08。留空表示未知；密钥环境变量优先。', 8, MUTED).pack(anchor='w')
        self.interval = tk.StringVar(value=str(self.config['interval']))
        self.threshold = tk.StringVar(value=str(self.config['alert_percent']))
        self.topmost = tk.BooleanVar(value=self.config['topmost'])
        self.binary = tk.StringVar(value=self.config.get('codex_executable', ''))
        for text, variable in [('刷新间隔（秒，至少 60）', self.interval),
                               ('额度已用提醒阈值（%）', self.threshold),
                               ('Codex 程序路径（留空自动寻找）', self.binary)]:
            label(general, text, 10, MUTED).pack(anchor='w', pady=(10, 5))
            ttk.Entry(general, textvariable=variable).pack(fill='x')
        tk.Checkbutton(general, text='窗口始终置顶', variable=self.topmost, bg=BG, fg=TEXT,
                       selectcolor=PANEL, activebackground=BG).pack(anchor='w', pady=18)
        label(general, '到期前 3 天提醒；额度到期倒计时使用北京时间。\n提醒显示在窗口内，最小化时标题会显示“提醒”。', 10, MUTED, justify='left').pack(anchor='w')
        label(general, '数据范围\nCodex 账户数据和本机日志分别展示。\nOpenAI / Claude API 默认统计整个组织，按 UTC 日/月。\n不同平台的 token 不合并，以免重复统计。', 10, MUTED, justify='left').pack(anchor='w', pady=25)
        label(manual, '适用于个人订阅、Gemini、Kimi、通义等其他平台。', 10, MUTED).pack(anchor='w')
        self.listbox = tk.Listbox(manual, bg=CARD, fg=TEXT, selectbackground=PANEL,
                                  relief='flat', font=(FONT, 11), height=11)
        self.listbox.pack(fill='both', expand=True, pady=15)
        row = tk.Frame(manual, bg=BG)
        row.pack(fill='x')
        button(row, '添加平台', lambda: self.manual_editor()).pack(side='left')
        button(row, '编辑', self.edit_selected).pack(side='left', padx=8)
        button(row, '删除', self.delete_selected).pack(side='left')
        self.update_list()
        footer = tk.Frame(self, bg=BG, padx=20, pady=18)
        footer.pack(side='bottom', fill='x', before=tabs)
        button(footer, '保存并刷新', self.save, True).pack(side='right')
        button(footer, '取消', self.destroy).pack(side='right', padx=10)
        center_window(self, app.details or app.root, 780, 840)
        style_window(self)
        app.panel_opened(self)
        self.grab_set()
        self.focus_set()

    def destroy(self):
        app = self.app
        if app.settings is self:
            app.settings = None
        super().destroy()
        app.panel_closed(self)

    def update_list(self):
        self.listbox.delete(0, 'end')
        for item in self.config['manual']:
            self.listbox.insert('end', item['name'] + '  ·  手动记录')

    def edit_selected(self):
        if self.listbox.curselection():
            self.manual_editor(self.listbox.curselection()[0])

    def delete_selected(self):
        if self.listbox.curselection():
            del self.config['manual'][self.listbox.curselection()[0]]
            self.update_list()

    def manual_editor(self, index=None):
        item = self.config['manual'][index] if index is not None else {}
        dialog = tk.Toplevel(self)
        dialog.title('手动平台数据')
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.grab_set()
        def close_editor():
            dialog.destroy()
            self.app.panel_closed(dialog)
            if self.winfo_exists():
                self.grab_set()
        dialog.protocol('WM_DELETE_WINDOW', close_editor)
        dialog.bind('<Escape>', lambda _: close_editor())
        content = tk.Frame(dialog, bg=BG, padx=22, pady=16)
        content.pack(fill='both', expand=True)
        variables = {}
        for key, title in [('name', '平台名称'), ('tokens_today', '今日 token'), ('tokens_month', '本月 token'),
                           ('balance', '余额（数字；币种写在备注）'), ('used_percent', '额度已用（0–100）'),
                           ('reset_date', '额度重置日期 / 时间'), ('expiry', '订阅 / 余额到期日'), ('note', '备注')]:
            label(content, title, 9, MUTED).pack(anchor='w', pady=(5, 3))
            variable = tk.StringVar(value=str(item.get(key, '')))
            variables[key] = variable
            ttk.Entry(content, textvariable=variable).pack(fill='x')
        label(content, '日期：2026-11-08；时间：2026-11-08T18:30', 8, MUTED).pack(anchor='w', pady=10)

        def save_item():
            updated = {k: v.get().strip() for k, v in variables.items()}
            try:
                core.validate_manual(updated)
            except (ValueError, TypeError) as exc:
                messagebox.showerror('请检查填写内容', str(exc), parent=dialog)
                return
            updated.update(id=item.get('id', 'manual-' + uuid.uuid4().hex[:10]), updated_at=core.now().isoformat())
            if index is None:
                self.config['manual'].append(updated)
            else:
                self.config['manual'][index] = updated
            self.update_list()
            close_editor()

        button(content, '保存平台', save_item, True).pack(anchor='e', pady=8)
        center_window(dialog, self, 600, 780)
        style_window(dialog)
        self.app.panel_opened(dialog)
        return dialog

    def save(self):
        try:
            interval = int(self.interval.get())
            threshold = int(self.threshold.get())
            if interval < 60 or interval > 86400 or not 1 <= threshold <= 100:
                raise ValueError('刷新间隔须在 60–86400 秒之间；提醒阈值须在 1–100 之间。')
            for pid, fields in self.fields.items():
                expiry = fields['expiry'].get().strip()
                core.parse_expiry(expiry)
                conf = self.config['providers'][pid]
                conf.update(enabled=fields['enabled'].get(), expiry=expiry)
                if fields['clear'].get():
                    conf.pop('key_encrypted', None)
                value = fields['key'].get().strip()
                if value:
                    conf['key_encrypted'] = core.protect(value)
            binary = self.binary.get().strip().strip('"')
            if binary and (not os.path.isfile(binary) or not binary.lower().endswith('.exe')):
                raise ValueError('请选择存在的原生 codex.exe 文件。')
            self.config.update(interval=interval, alert_percent=threshold,
                               topmost=self.topmost.get(), codex_executable=binary)
            core.atomic_json(core.SETTINGS, self.config)
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('设置未保存', str(exc), parent=self)
            return
        self.app.config = self.config
        self.app.results = {k: v for k, v in self.app.results.items() if k in self.app.enabled_ids()}
        self.app.root.attributes('-topmost', self.config['topmost'])
        self.destroy()
        self.app.refresh(force=True)


def style_window(window):
    """Use a light native caption, the companion icon and rounded corners."""
    if os.name != 'nt':
        return
    try:
        ctypes = core.ctypes
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user.GetAncestor.restype = ctypes.c_void_p
        hwnd = user.GetAncestor(window.winfo_id(), 2)
        dwm = ctypes.WinDLL('dwmapi')
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        if (core.ROOT/'app.ico').exists():
            window.iconbitmap(str(core.ROOT/'app.ico'))
        from windows_integration import set_window_properties, APP_ID
        set_window_properties(hwnd, {2: '"'+str(core.ROOT/'AIUsage.exe')+'"',
                                     3: str(core.ROOT/'app.ico')+',0', 4: 'AI 用量挂件', 5: APP_ID})
        for attribute, value in ((20, 0), (33, 2)):
            flag = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(flag), ctypes.sizeof(flag))
    except (OSError, AttributeError, tk.TclError):
        pass


class Dashboard:
    def __init__(self, root, offline=False):
        self.root = root
        # Pixel-based window dimensions remain compact on high-DPI desktops.
        root.tk.call('tk', 'scaling', 4 / 3)
        for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont'):
            tkfont.nametofont(name).configure(family=FONT, size=12)
        self.offline = offline
        self.config = core.load_settings()
        self.results = {}
        self.scanner = core.LocalCodexTokens()
        self.mailbox = queue.Queue()
        self.busy = False
        self.generation = 0
        self.details = None
        self.settings = None
        self.panels = []
        self.next_refresh = time.monotonic()
        root.title('AI 用量桌面')
        root.geometry('424x262')
        root.minsize(1, 1)
        root.resizable(False, False)
        root.configure(bg=BG)
        root.attributes('-topmost', self.config['topmost'])
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', background=PANEL, foreground=TEXT, padding=(12, 8), font=(FONT, 11))
        style.map('TNotebook.Tab', background=[('selected', CARD)])
        style.configure('TEntry', fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT, padding=7, font=(FONT, 12))
        style.configure('TCombobox', fieldbackground=CARD, background=PANEL, foreground=TEXT, padding=7, arrowsize=16)
        style.map('TCombobox', fieldbackground=[('readonly', CARD)], foreground=[('readonly', TEXT)])
        style.configure('Vertical.TScrollbar', background=PANEL, troughcolor=BG, borderwidth=0, arrowsize=13)
        self.make_ui()
        if core.SNAPSHOT.exists():
            try:
                cached = json.loads(core.SNAPSHOT.read_text(encoding='utf-8'))
                self.results = {r['id']: {**r, 'stale': True} for r in cached if r['id'] in self.enabled_ids()}
            except (ValueError, TypeError, KeyError):
                pass
        self.render()
        root.after(100, self.refresh)
        root.after(200, self.tick)

    def enabled_ids(self):
        return [pid for pid, conf in self.config['providers'].items() if conf.get('enabled')] + [item['id'] for item in self.config['manual']]

    def make_ui(self):
        from pet_ui import PetSurface
        self.pin_var = tk.BooleanVar(value=self.config['topmost'])
        # These unmapped controls retain the existing refresh/configuration interface.
        self.pin_button = tk.Button(self.root)
        self.refresh_button = tk.Button(self.root)
        self.footer = tk.Label(self.root)
        self.surface = PetSurface(self, lambda _: self.open_settings())
        icon = core.ROOT / 'app.ico'
        if icon.exists() and os.name == 'nt':
            self.root.iconbitmap(str(icon))

    def switch_pin(self):
        self.pin_var.set(not self.pin_var.get())
        self.toggle_pin()

    def toggle_pin(self):
        self.config['topmost'] = self.pin_var.get()
        self.root.attributes('-topmost', self.pin_var.get())
        self.pin_button.configure(text='已置顶' if self.pin_var.get() else '置顶')
        if self.details and self.details.winfo_exists():
            self.details.attributes('-topmost', self.pin_var.get())
        try:
            core.atomic_json(core.SETTINGS, self.config)
        except OSError:
            messagebox.showerror('保存失败', '窗口设置未能保存。', parent=self.root)

    def open_details(self, pid=None):
        if self.settings and self.settings.winfo_exists():
            self.settings.lift()
            self.settings.focus_set()
            return
        if not self.details or not self.details.winfo_exists():
            self.details = DetailWindow(self, pid)
        else:
            self.details.selected = pid
            self.details.render()
            self.details.deiconify()
        self.root.withdraw()
        self.details.lift()
        self.details.focus_set()

    def open_settings(self):
        if self.settings and self.settings.winfo_exists():
            self.settings.deiconify()
            self.settings.lift()
            self.settings.focus_set()
        else:
            SettingsDialog(self)

    def active_panel(self):
        return next((w for w in reversed(self.panels) if w.winfo_exists()), None)

    def panel_opened(self, window):
        self.surface.close_popup()
        for panel in self.panels:
            selector = getattr(panel, 'selector', None)
            if selector:
                selector.close_menu()
        self.panels.append(window)
        self.root.withdraw()
        window.deiconify()
        window.lift()

    def panel_closed(self, window):
        self.panels = [w for w in self.panels if w is not window and w.winfo_exists()]
        active = self.active_panel()
        if active:
            active.deiconify()
            active.lift()
            active.focus_set()
        elif self.root.winfo_exists():
            self.surface.render()
            bridge = getattr(self, 'bridge', None)
            if bridge:
                bridge.restore()
            else:
                self.root.deiconify()
                self.root.lift()

    def refresh(self, force=False):
        if self.busy:
            if force:
                self.refresh_pending = True
            return
        self.busy = True
        self.refresh_pending = False
        self.generation += 1
        generation = self.generation
        conf = deepcopy(self.config)
        ids = self.enabled_ids()
        self.refresh_button.configure(text='刷新中…', state='disabled')

        def worker():
            def fetch(pid):
                if self.offline:
                    return self.results.get(pid, core.card(pid, '离线检查', status='unconfigured'))
                return core.fetch_provider(pid, conf, self.scanner)
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(fetch, pid): pid for pid in ids}
                from concurrent.futures import as_completed
                for future in as_completed(futures):
                    try:
                        result = future.result()
                    except Exception:
                        result = core.card(futures[future], '读取失败', status='error', note='读取失败，下次重试。')
                    self.mailbox.put(('result', generation, result))
            self.mailbox.put(('done', generation, None))
        threading.Thread(target=worker, daemon=True).start()

    def tick(self):
        changed = False
        rerun = False
        while True:
            try:
                kind, generation, result = self.mailbox.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation:
                continue
            if kind == 'result':
                pid = result['id']
                if pid in self.enabled_ids():
                    old = self.results.get(pid)
                    if result['status'] == 'error' and old and old.get('metrics'):
                        old.update(stale=True, status='error', note=result['note'] + '；以下为上次成功读取的数据。')
                    else:
                        self.results[pid] = result
                    changed = True
            else:
                self.busy = False
                self.next_refresh = time.monotonic() + self.config['interval']
                self.refresh_button.configure(text='刷新', state='normal')
                self.pin_var.set(self.config['topmost'])
                try:
                    core.atomic_json(core.SNAPSHOT, list(self.results.values()))
                except OSError:
                    self.footer.configure(text='快照未能保存；仍可查看当前数据。')
                rerun = self.refresh_pending
                changed = True
        if changed:
            self.render()
        if rerun or (not self.busy and time.monotonic() >= self.next_refresh):
            self.refresh()
        left = max(0, int(self.next_refresh - time.monotonic()))
        self.footer.configure(text='正在更新用量…' if self.busy else f'北京时间 {core.now():%H:%M}  ·  {left} 秒后刷新')
        self.surface.update_status(left)
        if getattr(self, 'bridge', None):
            self.bridge.poll()
        if not self.busy and int(time.time()) % 30 == 0 and getattr(self, 'last_redraw', 0) != int(time.time()):
            self.last_redraw = int(time.time())
            self.render()
        self.root.after(250, self.tick)

    def render(self):
        self.surface.render()
        notifications = core.alerts(list(self.results.values()), self.config['alert_percent'])
        self.root.title('提醒 · AI 用量挂件' if notifications else 'AI 用量挂件')
        if self.details and self.details.winfo_exists():
            self.details.attributes('-topmost', self.config['topmost'])
            self.details.render()

    def draw_card(self, result, parent):
        pid = result['id']
        shell = SoftCard(parent)
        shell.pack(fill='x', padx=5, pady=(4, 13))
        frame = shell.body
        top = tk.Frame(frame, bg=CARD)
        top.pack(fill='x')
        label(top, result['name'], 18, COLORS.get(pid, BLUE), True).pack(side='left')
        status = {'ok': '已连接', 'partial': '部分可用', 'unconfigured': '未配置', 'error': '读取失败', 'manual': '手动', 'loading': '连接中'}
        state = '旧数据' if result.get('stale') else status.get(result['status'], '')
        label(top, state, 8, GREEN if state == '已连接' else MUTED).pack(side='right')
        if result.get('plan'):
            label(top, result['plan'].upper(), 8, MUTED).pack(side='right', padx=8)
        label(frame, result['source'] + '  ·  ' + self.format_time(result.get('updated_at')), 8, MUTED).pack(anchor='w', pady=(5, 9))
        hero = [m for m in result['metrics'] if '今日 token' in m['label'] or '本月 token' in m['label']]
        if hero:
            grid = tk.Frame(frame, bg=CARD)
            grid.pack(fill='x', pady=(0, 8))
            for i, m in enumerate(hero):
                box = tk.Frame(grid, bg=CARD)
                box.grid(row=i // 2, column=i % 2, sticky='nsew', pady=7,
                         padx=(0, 12) if i % 2 == 0 else (12, 0))
                label(box, m['label'], 8, MUTED).pack(anchor='w')
                fitted_label(box, m['value'], 30).pack(fill='x', pady=(4, 0))
                grid.columnconfigure(i % 2, weight=1, uniform='hero')
        for m in result['metrics']:
            if m in hero:
                continue
            row = tk.Frame(frame, bg=CARD)
            row.pack(fill='x', pady=(12, 0))
            title = label(row, m['label'], 12, MUTED, justify='left', anchor='w')
            title.grid(row=0, column=0, sticky='ew', padx=(0, 12))
            fitted_label(row, m['value'], 14,
                         AMBER if m.get('used', 0) >= self.config['alert_percent'] else TEXT).grid(
                             row=0, column=1, sticky='ew')
            row.columnconfigure(0, weight=1, uniform='metric')
            row.columnconfigure(1, weight=1, uniform='metric')
            row.bind('<Configure>', lambda e, w=title: w.configure(wraplength=max(100, e.width//2-16)))
            if 'used' in m:
                bar = tk.Canvas(frame, bg=CARD, height=7, highlightthickness=0)
                bar.pack(fill='x', pady=(5, 0))
                used = m['used']
                shown_percent = 100-used if '剩余' in m['value'] else used
                color = RED if used >= 95 else AMBER if used >= self.config['alert_percent'] else COLORS.get(pid, BLUE)

                def draw(event, value=shown_percent, canvas=bar, fill=color):
                    canvas.delete('all')
                    rounded(canvas, 0, 0, event.width, 7, 3, PANEL, 'bar')
                    if value > 0:
                        rounded(canvas, 0, 0, event.width * min(100, value) / 100, 7, 3, fill, 'bar')
                bar.bind('<Configure>', draw)
                stamp = m.get('reset_at')
                text = '重置：' + core.remaining(stamp)
                if stamp:
                    text += '  ·  ' + datetime.fromtimestamp(stamp, core.TZ).strftime('%m-%d %H:%M')
                if m.get('stale'):
                    text += '  ·  旧快照'
                label(frame, text, 8, MUTED).pack(anchor='w', pady=(4, 0))
            if m.get('expiry_at'):
                label(frame, core.remaining(m['expiry_at']), 8, AMBER).pack(anchor='w', pady=(3, 0))
        if result.get('daily'):
            self.chart(frame, result['daily'], COLORS.get(pid, BLUE), '近 7 天 token · 本机' if pid == 'codex' else '近 7 天 token · UTC')
        if result.get('note'):
            note = label(frame, result['note'], 11, MUTED, wraplength=600, justify='left')
            note.pack(anchor='w', pady=(15, 0))
            frame.bind('<Configure>', lambda e: note.configure(wraplength=max(200, e.width)), add='+')

    @staticmethod
    def format_time(value):
        try:
            return datetime.fromisoformat(value).astimezone(core.TZ).strftime('%m-%d %H:%M')
        except (ValueError, TypeError):
            return '尚未更新'

    def chart(self, frame, daily, color, title):
        label(frame, title, 8, MUTED).pack(anchor='w', pady=(12, 0))
        chart = tk.Canvas(frame, bg=CARD, height=108, highlightthickness=0)
        chart.pack(fill='x')
        end = datetime.now(core.TZ if '本机' in title else core.timezone.utc).date()
        dates = [end - timedelta(days=i) for i in range(6, -1, -1)]
        values = [daily.get(d.isoformat(), 0) for d in dates]
        peak = max(values) or 1

        def draw(event):
            chart.delete('all')
            step = event.width / 7
            for i, (day, value) in enumerate(zip(dates, values)):
                x = i * step + step * .2
                height = max(2, value / peak * 65)
                rounded(chart, x, 80-height, step*.6, height, 4, color if value else PANEL, 'bar')
                chart.create_text(x + step*.3, 97, text=day.strftime('%m/%d'), fill=MUTED, font=(FONT, 11))
                if value == peak and value:
                    chart.create_text(x + step*.3, 9, text=core.compact(value), fill=TEXT, font=(FONT, 11))
        chart.bind('<Configure>', draw)

    def export(self):
        parent = self.active_panel() or self.root
        filename = filedialog.asksaveasfilename(parent=parent, title='导出当前指标',
                                               defaultextension='.csv', initialfile='AI用量-' + core.now().strftime('%Y%m%d') + '.csv',
                                               filetypes=[('CSV 表格', '*.csv')])
        if not filename:
            return
        try:
            with open(filename, 'w', newline='', encoding='utf-8-sig') as handle:
                writer = csv.writer(handle)
                writer.writerow(['平台', '数据来源', '指标', '值', '更新时间', '是否旧数据'])
                for result in self.results.values():
                    for item in result['metrics']:
                        values = [result['name'], result['source'], item['label'], item['value'], result['updated_at'], str(bool(result.get('stale') or item.get('stale')))]
                        # Protect CSV consumers from spreadsheet formula injection in manual names.
                        writer.writerow(["'" + v if v.startswith(('=', '+', '-', '@')) else v for v in values])
            messagebox.showinfo('已导出', '当前指标已保存。', parent=parent)
        except OSError:
            messagebox.showerror('导出失败', '无法写入该文件，请选择其他位置。', parent=parent)


class DetailWindow(tk.Toplevel):
    def __init__(self, app, pid=None):
        super().__init__(app.root)
        self.app = app
        self.selected = pid
        self.title('AI 用量 · 详细信息')
        self.configure(bg=BG)
        self.attributes('-topmost', app.config['topmost'])
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _: self.close())
        heading = tk.Frame(self, bg=BG, padx=24, pady=18)
        heading.pack(fill='x')
        label(heading, '用量总览', 22, bold=True).pack(side='left')
        button(heading, '连接设置', app.open_settings).pack(side='right')
        self.tabs = tk.Frame(self, bg=BG, padx=20)
        self.tabs.pack(fill='x', pady=(0, 12))
        label(self.tabs, '查看平台', 11, MUTED).pack(side='left', padx=(4, 14))
        self.platform_var = tk.StringVar()
        self.selector = RoundedSelect(self.tabs, textvariable=self.platform_var)
        self.selector.pack(side='left', fill='x', expand=True, padx=(0, 5))
        self.selector.bind('<<ComboboxSelected>>', lambda _: self.choose(self.platform_ids[self.selector.current()]))
        footer = tk.Frame(self, bg=BG, padx=24, pady=14)
        footer.pack(side='bottom', fill='x')
        button(footer, '导出指标', app.export).pack(side='left')
        button(footer, '返回挂件', self.close, True).pack(side='right')
        self.area = tk.Frame(self, bg=BG)
        self.area.pack(fill='both', expand=True, padx=(16, 10))
        self.canvas = tk.Canvas(self.area, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.area, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.content.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.bind('<MouseWheel>', self.scroll)
        self.render()
        center_window(self, app.root, 820, 900)
        style_window(self)
        app.panel_opened(self)

    def scroll(self, event):
        if event.widget.winfo_toplevel() == self:
            self.canvas.yview_scroll(int(-event.delta / 120), 'units')

    def choose(self, pid):
        self.selected = pid
        self.render()
        self.canvas.yview_moveto(0)

    def render(self):
        ids = self.app.enabled_ids()
        if self.selected not in ids:
            self.selected = None
        position = self.canvas.yview()[0]
        for child in self.content.winfo_children():
            child.destroy()
        self.platform_ids = [None]+ids
        names = ['全部平台'] + [self.app.surface.provider_name(pid) for pid in ids]
        self.selector.configure(values=names, option_ids=self.platform_ids)
        self.selector.current(self.platform_ids.index(self.selected))
        notifications = core.alerts(list(self.app.results.values()), self.app.config['alert_percent'])
        if notifications:
            label(self.content, '\n'.join(notifications), 12, AMBER, justify='left', wraplength=620).pack(fill='x', padx=10, pady=8)
        for pid in ids:
            if self.selected is not None and pid != self.selected:
                continue
            result = self.app.results.get(pid) or core.card(pid, '等待首次读取', status='loading')
            self.app.draw_card(result, self.content)
        if not ids:
            label(self.content, '请先连接或添加平台。', 14, MUTED).pack(pady=30)
        self.update_idletasks()
        self.canvas.yview_moveto(position)

    def close(self):
        self.selector.close_menu()
        if self.app.settings and self.app.settings.winfo_exists():
            self.app.settings.destroy()
        self.app.details = None
        self.destroy()
        self.app.panel_closed(self)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--details', action='store_true', help='Open the detailed panel at launch')
    parser.add_argument('--check', action='store_true', help='Read-only connection check without GUI')
    parser.add_argument('--offline', action='store_true', help='Open saved data without network requests')
    parser.add_argument('--smoke-test', action='store_true', help='Build GUI, exercise both layouts, then exit')
    args = parser.parse_args()
    if args.check:
        settings = core.load_settings()
        scanner = core.LocalCodexTokens()
        output = [core.fetch_provider(pid, settings, scanner) for pid, conf in settings['providers'].items() if conf.get('enabled')]
        core.atomic_json(core.SNAPSHOT, output)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    if os.name == 'nt':
        try:
            core.ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    from windows_integration import WindowsBridge
    bridge = None if args.smoke_test else WindowsBridge(core.ROOT)
    if bridge and bridge.activate_existing():
        return
    root = tk.Tk()
    app = Dashboard(root, offline=args.offline or args.smoke_test)
    app.bridge = bridge
    if bridge:
        bridge.attach(root)
    if args.details:
        root.after(200, app.open_details)
    if args.smoke_test:
        def smoke():
            app.render()
            assert root.overrideredirect()
            assert str(root.attributes('-transparentcolor')) == '#ff00ff'
            mini_size = (root.winfo_width(), root.winfo_height())
            app.open_details('codex')
            app.details.update_idletasks()
            assert root.winfo_width() == mini_size[0] and root.winfo_height() == mini_size[1]
            app.details.choose('deepseek')
            app.details.close()
            assert app.details is None
            dialog = SettingsDialog(app)
            dialog.update_idletasks()
            dialog.destroy()
            app.surface.toggle_bubble()
            assert root.winfo_width() == 144
            app.surface.toggle_bubble()
            assert root.winfo_width() == 424
            print('Borderless transparent pet, bubble toggle, details and settings: OK')
            root.destroy()
        root.after(800, smoke)
    try:
        root.mainloop()
    finally:
        if bridge:
            bridge.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (core.ROOT / 'startup-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        if sys.stdout:
            traceback.print_exc()
        try:
            messagebox.showerror('启动失败', '请查看工具目录中的 startup-error.log。')
        except Exception:
            pass
        sys.exit(1)
