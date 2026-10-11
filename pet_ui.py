"""A soft, transparent desktop companion with a persistent provider selection."""
import math
import time
import tkinter as tk
from tkinter import font as tkfont

import core
from ui_helpers import fitting_size, keep_window_visible
from soft_controls import SoftPopup, PopupItem

KEY = '#ff00ff'
INK = '#edf7f2'
MINT = '#397e70'
WHITE = '#23473e'
MUTED = '#57746b'
FONT = 'Microsoft YaHei'


class PetSurface:
    WIDTH = 424
    HEIGHT = 262

    def __init__(self, app, settings_dialog):
        self.app, self.root = app, app.root
        self.root.companion_surface = self
        self.settings_dialog = settings_dialog
        self.visible_bubble = app.config.get('bubble_visible', True)
        self.main_id = app.config.get('display_provider')
        self.period = app.config.get('display_period', 'today')
        if self.period not in ('today', 'month'):
            self.period = 'today'
        self.drag = None
        self.popup_window = None
        self.context_menu_open = False
        self.hide_pet_for_menu = False
        self.started = time.monotonic()
        self.root.overrideredirect(True)
        self.root.configure(bg=KEY)
        if self.root.tk.call('tk', 'windowingsystem') == 'win32':
            self.root.attributes('-transparentcolor', KEY)
        self.root.minsize(1, 1)
        self.canvas = tk.Canvas(self.root, bg=KEY, highlightthickness=0, bd=0,
                                width=self.WIDTH, height=self.HEIGHT)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.motion)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<Button-3>', self.menu)
        self.canvas.bind('<Motion>', self.hover)
        self.root.bind('<Control-d>', lambda _: app.open_details())
        self.root.bind('<Escape>', lambda _: self.minimize())
        self.root.protocol('WM_DELETE_WINDOW', self.quit)
        pos = app.config.get('widget_position')
        if not isinstance(pos, list) or len(pos) != 2:
            pos = [self.root.winfo_screenwidth()-self.WIDTH-40,
                   self.root.winfo_screenheight()-self.HEIGHT-100]
        x = max(0, min(int(pos[0]), self.root.winfo_screenwidth()-self.WIDTH))
        y = max(0, min(int(pos[1]), self.root.winfo_screenheight()-self.HEIGHT-60))
        self.root.geometry(f'{self.WIDTH}x{self.HEIGHT}+{x}+{y}')
        self.root.after(40, self.animate)

    def rounded(self, x, y, w, h, r, fill, tag='data', outline=''):
        points = [x+r,y, x+w-r,y, x+w,y, x+w,y+r, x+w,y+h-r,
                  x+w,y+h, x+w-r,y+h, x+r,y+h, x,y+h, x,y+h-r,
                  x,y+r, x,y]
        return self.canvas.create_polygon(points, smooth=True, splinesteps=32,
                                          fill=fill, outline=outline, tags=tag)

    def text(self, x, y, value, size=12, color=WHITE, bold=False, anchor='nw', tag='data', width=None):
        value = str(value)
        font = tkfont.Font(family=FONT, size=size, weight='bold' if bold else 'normal')
        if width:
            while len(value) > 1 and font.measure(value) > width:
                value = value[:-2]+'…'
        return self.canvas.create_text(x, y, text=value, fill=color, anchor=anchor,
                                       font=(FONT, size, 'bold' if bold else 'normal'), tags=tag)

    def provider_name(self, pid):
        result = self.app.results.get(pid)
        if result:
            return result['name']
        if pid in core.PROVIDERS:
            return core.PROVIDERS[pid][0]
        return next((m['name'] for m in self.app.config['manual'] if m['id'] == pid), 'AI 平台')

    def number(self, x, y, value, size=16, color=WHITE, width=118, anchor='nw', bold=True):
        size = fitting_size(self.root, value, size, width, bold)
        # Numeric values are fitted in full; never pass them through text ellipsis.
        return self.text(x, y, str(value), size, color, bold, anchor=anchor, tag=('data', 'amount'))

    def selected_result(self):
        ids = self.app.enabled_ids()
        if self.main_id not in ids:
            self.main_id = next((pid for pid in ids if self.app.results.get(pid, {}).get('metrics')),
                                ids[0] if ids else None)
        result = self.app.results.get(self.main_id)
        if result is None:
            result = core.card(self.main_id or 'AI', '等待连接', status='loading')
            result['name'] = self.provider_name(self.main_id)
        return result

    def primary_metric(self, result):
        token = '今日 token' if self.period == 'today' else '本月 token'
        return next((m for m in result['metrics'] if token in m['label']),
                    next((m for m in result['metrics'] if '余额' in m['label'] and '赠金' not in m['label']), None))

    def render(self):
        c = self.canvas
        c.delete('data')
        result = self.selected_result()
        primary = self.primary_metric(result)
        self.period_controls = any(' token' in m['label'] for m in result['metrics']) or (
            not result['metrics'] and self.main_id != 'deepseek')
        width = self.WIDTH if self.visible_bubble else 144
        if self.root.winfo_width() != width:
            self.root.geometry(f'{width}x{self.HEIGHT}')
        if self.context_menu_open:
            self.root.update_idletasks()
            return
        if self.visible_bubble:
            # Pastel edge layers and a pale highlight give a quiet, soft glow.
            self.rounded(132, 8, 282, 248, 27, '#bedace')
            self.rounded(133, 9, 280, 245, 26, '#d6e9dd')
            self.rounded(134, 10, 278, 242, 25, INK)
            self.rounded(138, 14, 270, 64, 22, '#f5fcf8')
            c.create_polygon(136,148, 122,159, 142,166, fill=INK, outline='', tags='data')
            self.rounded(153, 24, 190, 32, 13, '#e1efe7')
            self.text(165, 39, result['name'], 12, MINT, True, anchor='w', width=145)
            c.create_line(325,37, 329,41, 333,37, fill=MINT, width=1.6, tags='data')
            for x in (382, 388, 394):
                c.create_oval(x-1.5,36, x+1.5,39, fill=MUTED, outline='', tags='data')
            if self.period_controls:
                self.rounded(154, 67, 119, 27, 12, '#deebe3')
                self.rounded(156 if self.period == 'today' else 215, 69, 56, 23, 10, '#fbfefa')
                self.text(184, 80, '今日', 11, MINT if self.period == 'today' else MUTED,
                          self.period == 'today', anchor='center')
                self.text(243, 80, '本月', 11, MINT if self.period == 'month' else MUTED,
                          self.period == 'month', anchor='center')
                title = ('Token · '+primary['label'].split(' · ')[-1]
                         if primary and ' · ' in primary['label'] and 'token' in primary['label']
                         else 'Token' if primary and 'token' in primary['label'] else '未提供')
                self.number(390, 80, title, 10, MUTED, anchor='e', width=107, bold=False)
            else:
                self.text(155, 80, primary['label'] if primary else '连接后查看用量', 11, MUTED,
                          anchor='w', width=235)
            value = primary['value'] if primary else '未连接' if result['status'] == 'unconfigured' else '—'
            self.number(153, 104, value, 27, WHITE, width=235)
            quotas = [m for m in result['metrics'] if 'used' in m][:2]
            if quotas:
                for i, m in enumerate(quotas):
                    x = 155 + i*123
                    caption = m['label'].replace('额度', '').replace(' · 日志快照', '').strip()
                    self.text(x, 159, caption+'剩余', 11, MUTED, width=112)
                    available = max(0, min(100, 100-m['used']))
                    color = '#936737' if m['used'] >= self.app.config['alert_percent'] else MINT
                    self.number(x, 180, f'{available:g}%', 17, color, width=104)
                    self.rounded(x, 214, 104, 4, 2, '#d5e4db')
                    if available:
                        self.rounded(x, 214, max(2, available*1.04), 4, 2, '#87baa3')
            else:
                secondary = next((m for m in result['metrics'] if
                    ('本月 token' if self.period == 'today' else '今日 token') in m['label']), None)
                if secondary:
                    self.text(155, 178, secondary['label'].split(' · ')[0], 11, MUTED)
                    self.number(392, 178, secondary['value'], 14, MINT, anchor='ne', width=110)
                elif primary and '余额' in primary['label']:
                    self.text(155, 183, '余额指标 · Token 统计未提供', 11, MUTED, width=236)
                else:
                    self.text(155, 183, '选择平台 · 点击气泡查看详情', 11, MUTED, width=236)
            self.footer_id = self.text(155, 225, '', 10, MUTED, width=237)
            self.update_status()
        else:
            self.rounded(7, 24, 130, 51, 18, '#e5f3ec')
            self.text(72, 38, result['name'], 10, MUTED, anchor='center', width=118)
            self.number(72, 60, primary['value'] if primary else 'AI 用量', 16, MINT,
                        anchor='center', width=118)
        if self.app.updater.available:
            self.rounded(42, 233, 60, 27, 14, '#f4cccf', ('data', 'update-badge'))
            self.rounded(44, 232, 56, 26, 13, '#ca5260', ('data', 'update-badge'))
            self.text(72, 245, 'New', 10, '#fff9f7', True, anchor='center',
                      tag=('data', 'update-badge'))
        self.root.update_idletasks()

    def update_status(self, left=None):
        if not self.visible_bubble or not hasattr(self, 'footer_id'):
            return
        result = self.app.results.get(self.main_id, {})
        if self.app.busy:
            status = '正在更新…'
        elif result.get('stale'):
            status = '旧数据 · ' + self.app.format_time(result.get('updated_at'))
        elif result.get('status') == 'unconfigured':
            status = '点击连接平台  ›'
        elif result.get('status') == 'error':
            status = '读取失败 · 点击查看详情  ›'
        else:
            alerts = core.alerts([result] if result else [], self.app.config['alert_percent'])
            status = '额度 / 到期提醒 · 点击查看' if alerts else '点击查看详情  ›'
        font = tkfont.Font(family=FONT, size=10)
        while len(status) > 1 and font.measure(status) > 237:
            status = status[:-2]+'…'
        self.canvas.itemconfigure(self.footer_id, text=status)

    def animate(self):
        if not self.root.winfo_exists():
            return
        c = self.canvas
        c.delete('pet')
        if self.hide_pet_for_menu:
            self.root.after(50, self.animate)
            return
        phase = time.monotonic()-self.started
        bob = math.sin(phase*1.6)*2
        tag = 'pet'
        c.create_oval(17,211, 127,224, fill='#cee5d9', outline='', tags=tag)
        for inset, fill in ((0, '#d5e8df'), (6, '#dfeee4'), (12, '#e9f4e9')):
            c.create_oval(4+inset,93+inset+bob, 140-inset,226-inset+bob,
                          fill=fill, outline='', tags=tag)
        c.create_oval(39,101+bob, 58,135+bob, fill='#d1f0de', outline='', tags=tag)
        c.create_oval(84,101+bob, 103,135+bob, fill='#d1f0de', outline='', tags=tag)
        c.create_line(72,109+bob, 72,95+bob, fill='#9bd2b8', width=3, tags=tag)
        c.create_oval(66,85+bob, 78,97+bob, fill='#effde8', outline='', tags=tag)
        c.create_oval(17,165+bob, 37,192+bob, fill='#b1dac6', outline='', tags=tag)
        c.create_oval(107,165+bob, 127,192+bob, fill='#b1dac6', outline='', tags=tag)
        c.create_oval(35,200+bob, 63,219+bob, fill='#aad3bd', outline='', tags=tag)
        c.create_oval(82,200+bob, 110,219+bob, fill='#aad3bd', outline='', tags=tag)
        self.rounded(27,117+bob, 90,93, 30, '#a5d1bd', tag)
        self.rounded(27,116+bob, 90,86, 30, '#d7f0df', tag)
        self.rounded(33,126+bob, 78,58, 24, '#5b9585', tag)
        self.rounded(37,130+bob, 70,50, 20, '#3b7369', tag)
        blink = phase % 6.2 > 6.04
        px = self.root.winfo_pointerx()-self.root.winfo_rootx()
        py = self.root.winfo_pointery()-self.root.winfo_rooty()
        dx = max(-2, min(2, (px-72)/140))
        dy = max(-1.5, min(1.5, (py-160-bob)/160))
        for x in (54,89):
            if blink:
                c.create_line(x-6,154+bob, x+6,154+bob, fill='#e9ffe4', width=3, tags=tag)
            else:
                self.rounded(x-5+dx,144+bob+dy, 10,17, 5, '#efffe9', tag)
        c.create_arc(66,154+bob, 79,167+bob, start=195, extent=145, style='arc',
                     outline='#b7e6c8', width=2, tags=tag)
        c.create_oval(44,167+bob, 54,171+bob, fill='#8ab6a1', outline='', tags=tag)
        c.create_oval(91,167+bob, 101,171+bob, fill='#8ab6a1', outline='', tags=tag)
        c.create_oval(66,189+bob, 79,198+bob, fill='#f6fff0', outline='', tags=tag)
        if c.find_withtag('data'):
            c.tag_lower('pet', 'data')
        self.root.after(50, self.animate)

    def hover(self, event):
        self.canvas.configure(cursor='hand2' if event.y < 95 or event.x > 133 or (
            self.app.updater.available and 42 <= event.x <= 102 and 232 <= event.y <= 260) else 'fleur')

    def press(self, event):
        self.drag = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y(), False)

    def motion(self, event):
        if not self.drag:
            return
        sx, sy, ox, oy, moved = self.drag
        dx, dy = event.x_root-sx, event.y_root-sy
        if abs(dx)+abs(dy)>5 or moved:
            x = max(0, min(ox+dx, self.root.winfo_screenwidth()-self.root.winfo_width()))
            y = max(0, min(oy+dy, self.root.winfo_screenheight()-self.root.winfo_height()-45))
            self.root.geometry(f'+{x}+{y}')
            self.drag = (sx, sy, ox, oy, True)

    def release(self, event):
        moved = self.drag and self.drag[-1]
        self.drag = None
        if moved:
            self.save_position()
        elif self.app.updater.available and 42 <= event.x <= 102 and 232 <= event.y <= 260:
            self.app.open_updates()
        elif not self.visible_bubble or event.x < 144:
            self.toggle_bubble()
        elif self.visible_bubble and 153 <= event.x <= 343 and 24 <= event.y <= 56:
            self.provider_menu()
        elif self.visible_bubble and self.period_controls and 154 <= event.x <= 273 and 67 <= event.y <= 94:
            self.choose_period('today' if event.x < 214 else 'month')
        elif self.visible_bubble and event.x > 360 and event.y < 60:
            self.menu(event)
        else:
            self.app.open_details(self.main_id)

    def persist(self):
        try:
            core.atomic_json(core.SETTINGS, self.app.config)
        except OSError:
            pass

    def save_position(self):
        self.app.config['widget_position'] = [self.root.winfo_x(), self.root.winfo_y()]
        self.persist()

    def choose_provider(self, pid):
        if pid not in self.app.enabled_ids():
            return
        self.main_id = pid
        self.app.config['display_provider'] = pid
        self.persist()
        self.render()

    def choose_period(self, period):
        if period not in ('today', 'month'):
            return
        self.period = period
        self.app.config['display_period'] = period
        self.persist()
        self.render()

    def close_popup(self):
        popup = self.popup_window
        if popup and not popup.closed:
            popup.close('dismissed')

    def popup(self, items, x, y, title='选择平台', context=False, avoid_surface=False):
        self.close_popup()
        previous_bubble = self.visible_bubble
        avoid_bounds = None
        if context:
            self.context_menu_open = True
            self.visible_bubble = False
            self.render()
        if context or avoid_surface:
            self.root.update_idletasks()
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            avoid_bounds = (rx, ry, rx+self.root.winfo_width(), ry+self.root.winfo_height())

        def closed(reason):
            self.popup_window = None
            if context or avoid_surface:
                self.context_menu_open = False
                self.hide_pet_for_menu = False
                self.visible_bubble = previous_bubble
            # A selected command may open a panel; do not flash the bubble first.
            try:
                if reason != 'selected' and self.root.winfo_exists():
                    self.render()
            except tk.TclError:
                pass

        def after_action():
            try:
                if self.root.winfo_exists() and self.root.winfo_ismapped():
                    self.render()
            except tk.TclError:
                pass  # The Exit command has already destroyed the Tk application.

        self.popup_window = SoftPopup(self.root, items, x, y, title=title,
                                      on_close=closed, after_action=after_action,
                                      avoid_bounds=avoid_bounds)
        if (context or avoid_surface) and not self.popup_window.avoids_owner:
            self.context_menu_open = True
            self.visible_bubble = False
            self.hide_pet_for_menu = True
            self.render()
            self.canvas.delete('pet')
        return self.popup_window

    def provider_menu(self):
        items = []
        for pid in self.app.enabled_ids():
            result = self.app.results.get(pid, {})
            detail = '未连接' if result.get('status') == 'unconfigured' else ''
            items.append(PopupItem(self.provider_name(pid),
                                   lambda p=pid: self.choose_provider(p),
                                   selected=pid == self.main_id, detail=detail))
        if not items:
            items.append(PopupItem('连接平台', lambda: self.settings_dialog(self.app)))
        return self.popup(items, self.root.winfo_rootx()+153, self.root.winfo_rooty()+58,
                          avoid_surface=True)

    def toggle_bubble(self):
        self.close_popup()
        self.visible_bubble = not self.visible_bubble
        self.app.config['bubble_visible'] = self.visible_bubble
        self.render()
        if self.visible_bubble:
            keep_window_visible(self.root)
        self.save_position()

    def menu(self, event=None):
        self.close_popup()
        items = [
            PopupItem('全部用量', self.app.open_details),
            PopupItem('连接设置', lambda: self.settings_dialog(self.app)),
            PopupItem('刷新用量', lambda: self.app.refresh(force=True)),
            PopupItem('收起气泡' if self.visible_bubble else '展开气泡', self.toggle_bubble),
            PopupItem(separator=True),
            PopupItem('隐藏挂件', self.minimize),
            PopupItem('退出', self.quit),
        ]
        return self.popup(items, event.x_root if event else self.root.winfo_pointerx(),
                          event.y_root if event else self.root.winfo_pointery(),
                          title='AI 用量挂件', context=True)

    def minimize(self):
        self.close_popup()
        bridge = getattr(self.app, 'bridge', None)
        if bridge:
            bridge.minimize()

    def quit(self):
        self.close_popup()
        self.save_position()
        bridge = getattr(self.app, 'bridge', None)
        if bridge:
            bridge.close()
        self.root.destroy()
