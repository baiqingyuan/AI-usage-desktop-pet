"""Soft canvas controls and stable, topmost popup menus for the companion."""
import ctypes as ct
from dataclasses import dataclass
import os
import tkinter as tk
from tkinter import font as tkfont

from ui_helpers import FONT, BG, CARD, PANEL, TEXT, MUTED, GREEN, rounded, fitting_size, work_area, native_window

KEY = '#ff00ff'
BORDER = '#c4ddcf'
HOVER = '#e4f1e9'


@dataclass
class PopupItem:
    label: str = ''
    command: object = None
    selected: bool = False
    detail: str = ''
    separator: bool = False


def popup_position(area, width, height, x, y, avoid_bounds=None):
    """Keep a popup in the work area and out of the companion's rectangle."""
    left, top, right, bottom = area
    margin, gap = 8, 12
    x = max(left+margin, min(int(x), right-width-margin))
    y = max(top+margin, min(int(y), bottom-height-margin))
    if avoid_bounds is None:
        return x, y, True
    ax, ay, bx, by = avoid_bounds

    def clear(xx, yy):
        return (xx+width <= ax-gap or xx >= bx+gap or
                yy+height <= ay-gap or yy >= by+gap)

    if clear(x, y):
        return x, y, True
    candidates = [(bx+gap, y), (ax-gap-width, y),
                  (x, by+gap), (x, ay-gap-height)]
    available = [(xx, yy) for xx, yy in candidates
                 if xx >= left+margin and yy >= top+margin
                 and xx+width <= right-margin and yy+height <= bottom-margin
                 and clear(xx, yy)]
    if available:
        # Prefer the nearest free side so menu placement follows the right click.
        xx, yy = min(available, key=lambda point: abs(point[0]-x)+abs(point[1]-y))
        return xx, yy, True
    # Tiny work areas can have no free region large enough: the caller hides the pet.
    return x, y, False


class SoftPopup(tk.Toplevel):
    """An owned floating menu; retains its grab until selection or dismissal."""
    def __init__(self, owner, items, x, y, title='选择平台', width=272,
                 on_close=None, after_action=None, avoid_bounds=None):
        super().__init__(owner, bg=KEY)
        self.withdraw()
        self.owner, self.items = owner, list(items)
        self.on_close, self.after_action = on_close, after_action
        self.closed = False
        self.armed = False
        self.pressed = None
        self.hovered = next((i for i, item in enumerate(items) if item.selected), None)
        self.previous_grab = owner.grab_current()
        self.timers = set()
        self.overrideredirect(True)
        if self.tk.call('tk', 'windowingsystem') == 'win32':
            self.attributes('-transparentcolor', KEY)
        self.attributes('-topmost', True)
        if owner.winfo_ismapped():
            self.transient(owner.winfo_toplevel())
        self.title(title)
        self.width = width
        self.rows = []
        row_y = 0
        for item in self.items:
            row_height = 12 if item.separator else 46
            self.rows.append((row_y, row_y+row_height))
            row_y += row_height
        left, top, right, bottom = work_area(owner)
        self.body_height = min(max(46, row_y), max(46, bottom-top-96))
        height = self.body_height+66
        width = min(width, right-left-16)
        self.width = width
        x, y, self.avoids_owner = popup_position(
            (left, top, right, bottom), width, height, x, y, avoid_bounds)
        self.geometry(f'{width}x{height}+{x}+{y}')
        self.shell = tk.Canvas(self, bg=KEY, highlightthickness=0, bd=0)
        self.shell.pack(fill='both', expand=True)
        rounded(self.shell, 0, 2, width, height-2, 20, BORDER, 'shell')
        rounded(self.shell, 1, 1, width-2, height-4, 19, CARD, 'shell')
        self.shell.create_text(20, 25, text=title, anchor='w', fill=MUTED,
                               font=(FONT, 11, 'bold'))
        self.body = tk.Canvas(self, bg=CARD, highlightthickness=0, bd=0,
                              width=width-20, height=self.body_height,
                              scrollregion=(0, 0, width-20, row_y))
        self.body.place(x=10, y=48, width=width-20, height=self.body_height)
        self.draw_rows()
        self.bind('<ButtonPress-1>', self.press)
        self.bind('<ButtonRelease-1>', self.release)
        self.bind('<ButtonPress-3>', lambda _: self.close('dismissed'))
        self.bind('<Motion>', self.motion)
        self.bind('<MouseWheel>', self.wheel)
        self.bind('<Escape>', self.escape)
        self.bind('<Up>', lambda _: self.navigate(-1))
        self.bind('<Down>', lambda _: self.navigate(1))
        self.bind('<Home>', lambda _: self.navigate(-1, edge=True))
        self.bind('<End>', lambda _: self.navigate(1, edge=True))
        self.bind('<Return>', self.keyboard_select)
        self.bind('<space>', self.keyboard_select)
        self.bind('<FocusOut>', self.focus_out)
        self.owner_destroy_binding = owner.bind('<Destroy>', self.owner_destroyed, add='+')
        self.deiconify()
        self.update_idletasks()
        self.lift()
        self.native_topmost()
        self.schedule(0, self.acquire_focus)
        self.schedule(120, self.arm)

    def schedule(self, delay, callback):
        if self.closed:
            return
        token = None
        def run():
            self.timers.discard(token)
            if not self.closed:
                callback()
        token = self.after(delay, run)
        self.timers.add(token)

    def native_topmost(self):
        if os.name != 'nt':
            return
        user, hwnd = native_window(self)
        user.GetWindowLongPtrW.argtypes = [ct.c_void_p, ct.c_int]
        user.GetWindowLongPtrW.restype = ct.c_ssize_t
        user.SetWindowLongPtrW.argtypes = [ct.c_void_p, ct.c_int, ct.c_ssize_t]
        user.SetWindowPos.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_int, ct.c_int,
                                     ct.c_int, ct.c_int, ct.c_uint]
        style = user.GetWindowLongPtrW(hwnd, -20)
        user.SetWindowLongPtrW(hwnd, -20, (style | 0x80) & ~0x40000)
        user.SetWindowPos(hwnd, ct.c_void_p(-1), 0, 0, 0, 0, 0x13)

    def acquire_focus(self):
        self.grab_set()
        self.focus_force()
        # Focus activation can raise the owner; put the menu in front afterwards.
        self.native_topmost()

    def arm(self):
        self.armed = True
        self.check_focus()

    def draw_rows(self):
        self.body.delete('rows')
        for i, (item, (start, end)) in enumerate(zip(self.items, self.rows)):
            if item.separator:
                self.body.create_line(12, start+6, self.width-32, start+6,
                                      fill='#e0ebe3', tags='rows')
                continue
            if i == self.hovered or item.selected:
                rounded(self.body, 0, start+1, self.width-20, end-start-3,
                        13, HOVER if i == self.hovered else '#edf5ef', 'rows')
            label_width = self.width-74-(70 if item.detail else 0)
            size = fitting_size(self, item.label, 12, label_width, True, 9)
            self.body.create_text(14, (start+end)/2, text=item.label, anchor='w',
                                  font=(FONT, size, 'bold'), fill=TEXT, tags='rows')
            if item.detail:
                self.body.create_text(self.width-49, (start+end)/2, text=item.detail,
                                      anchor='e', font=(FONT, 9), fill=MUTED, tags='rows')
            if item.selected:
                xx, yy = self.width-38, (start+end)/2
                self.body.create_line(xx-5, yy, xx-1, yy+4, xx+6, yy-4,
                                      width=2, fill=GREEN, tags='rows')

    def row_at(self, event):
        # Events redirected by the local grab retain their original screen coordinates.
        x, y = event.x_root-self.body.winfo_rootx(), event.y_root-self.body.winfo_rooty()
        if not (0 <= x < self.body.winfo_width() and 0 <= y < self.body.winfo_height()):
            return None
        position = self.body.canvasy(y)
        return next((i for i, (start, end) in enumerate(self.rows)
                     if start <= position < end and not self.items[i].separator), None)

    def motion(self, event):
        hovered = self.row_at(event)
        if hovered != self.hovered:
            self.hovered = hovered
            self.draw_rows()
        self.body.configure(cursor='hand2' if hovered is not None else '')
        return 'break'

    def press(self, event):
        if not (self.winfo_rootx() <= event.x_root < self.winfo_rootx()+self.winfo_width()
                and self.winfo_rooty() <= event.y_root < self.winfo_rooty()+self.winfo_height()):
            self.close('dismissed')
            return 'break'
        self.pressed = self.row_at(event)
        return 'break'

    def release(self, event):
        row = self.row_at(event)
        pressed, self.pressed = self.pressed, None
        # The release that originally opened the menu has no matching menu press.
        if pressed is not None and row == pressed:
            self.select(row)
        return 'break'

    def select(self, index):
        if self.closed or index is None or self.items[index].separator:
            return
        command = self.items[index].command
        self.close('selected', restore_focus=True)
        try:
            if command:
                command()
        finally:
            if self.after_action:
                self.after_action()

    def navigate(self, direction, edge=False):
        options = [i for i, item in enumerate(self.items) if not item.separator]
        if not options:
            return 'break'
        if edge:
            self.hovered = options[0 if direction < 0 else -1]
        elif self.hovered not in options:
            self.hovered = options[0 if direction > 0 else -1]
        else:
            self.hovered = options[(options.index(self.hovered)+direction) % len(options)]
        start, end = self.rows[self.hovered]
        visible = self.body.canvasy(0)
        total = max(self.rows[-1][1], self.body_height)
        if start < visible:
            self.body.yview_moveto(start/total)
        elif end > visible+self.body_height:
            self.body.yview_moveto((end-self.body_height)/total)
        self.draw_rows()
        return 'break'

    def keyboard_select(self, event=None):
        self.select(self.hovered)
        return 'break'

    def escape(self, event=None):
        self.close('dismissed', restore_focus=True)
        return 'break'

    def wheel(self, event):
        self.body.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def focus_out(self, event):
        if self.armed:
            self.schedule(75, self.check_focus)

    def check_focus(self):
        focused = self.focus_get()
        if focused is None or focused.winfo_toplevel() is not self:
            self.close('dismissed')

    def owner_destroyed(self, event):
        if event.widget is self.owner:
            self.close('owner-destroyed')

    def close(self, reason='dismissed', restore_focus=False):
        if self.closed:
            return
        self.closed = True
        for timer in tuple(self.timers):
            self.after_cancel(timer)
        self.timers.clear()
        try:
            if self.grab_current() is self:
                self.grab_release()
            self.owner.unbind('<Destroy>', self.owner_destroy_binding)
        except tk.TclError:
            pass
        super().destroy()
        try:
            if self.previous_grab and self.previous_grab.winfo_exists():
                self.previous_grab.grab_set()
            if restore_focus and self.owner.winfo_exists() and self.owner.winfo_ismapped():
                self.owner.focus_set()
        except tk.TclError:
            pass
        if self.on_close:
            self.on_close(reason)


class InlinePopup(tk.Frame):
    """A non-modal menu inside its panel, so native close and drag remain usable."""
    schedule = SoftPopup.schedule
    draw_rows = SoftPopup.draw_rows
    row_at = SoftPopup.row_at
    motion = SoftPopup.motion
    press = SoftPopup.press
    release = SoftPopup.release
    select = SoftPopup.select
    navigate = SoftPopup.navigate
    keyboard_select = SoftPopup.keyboard_select
    escape = SoftPopup.escape
    wheel = SoftPopup.wheel

    def __init__(self, anchor, items, title='选择平台', width=360, on_close=None):
        owner = anchor.winfo_toplevel()
        super().__init__(owner, bg=owner.cget('bg'), bd=0, takefocus=True)
        self.owner, self.anchor, self.items = owner, anchor, list(items)
        self.on_close, self.after_action = on_close, None
        self.closed, self.armed, self.pressed = False, False, None
        self.timers = set()
        self.hovered = next((i for i, item in enumerate(items) if item.selected), None)
        self.requested_width, self.width = width, width
        self.rows = []
        row_y = 0
        for item in items:
            row_height = 12 if item.separator else 46
            self.rows.append((row_y, row_y+row_height))
            row_y += row_height
        self.total_rows_height = row_y
        self.title_text = title
        self.shell = tk.Canvas(self, bg=self.cget('bg'), highlightthickness=0, bd=0)
        self.shell.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Canvas(self, bg=CARD, highlightthickness=0, bd=0)
        for widget in (self, self.shell, self.body):
            widget.bind('<ButtonPress-1>', self.press)
            widget.bind('<ButtonRelease-1>', self.release)
            widget.bind('<Motion>', self.motion)
            widget.bind('<MouseWheel>', self.wheel)
            widget.bind('<Escape>', self.escape)
            widget.bind('<Up>', lambda _: self.navigate(-1))
            widget.bind('<Down>', lambda _: self.navigate(1))
            widget.bind('<Home>', lambda _: self.navigate(-1, edge=True))
            widget.bind('<End>', lambda _: self.navigate(1, edge=True))
            widget.bind('<Return>', self.keyboard_select)
            widget.bind('<space>', self.keyboard_select)
        self.bind('<FocusOut>', self.focus_out)
        self.owner_bindings = {
            '<Configure>': owner.bind('<Configure>', self.owner_configured, add='+'),
            '<ButtonPress-1>': owner.bind('<ButtonPress-1>', self.outside_pressed, add='+'),
            '<Unmap>': owner.bind('<Unmap>', self.owner_unmapped, add='+'),
        }
        self.owner.update_idletasks()
        self.layout()
        self.lift()
        self.schedule(0, self.focus_set)
        self.schedule(0, self.layout)

    def contains(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, 'master', None)
        return False

    def layout(self):
        if self.closed:
            return
        ow, oh = self.owner.winfo_width(), self.owner.winfo_height()
        ax, ay = 0, 0
        ancestor = self.anchor
        while ancestor is not self.owner:
            ax += ancestor.winfo_x()
            ay += ancestor.winfo_y()
            ancestor = ancestor.master
        self.width = min(self.requested_width, max(120, ow-16))
        x = max(8, min(ax, ow-self.width-8))
        below, above = oh-ay-self.anchor.winfo_height()-13, ay-13
        wanted = self.total_rows_height+66
        height = min(wanted, max(100, below, above))
        y = ay+self.anchor.winfo_height()+5 if below >= height else max(8, ay-height-5)
        self.body_height = max(34, height-66)
        self.place(x=x, y=y, width=self.width, height=height)
        self.shell.delete('shell')
        rounded(self.shell, 0, 2, self.width, height-2, 18, BORDER, 'shell')
        rounded(self.shell, 1, 1, self.width-2, height-4, 17, CARD, 'shell')
        self.shell.create_text(20, 25, text=self.title_text, anchor='w', fill=MUTED,
                               font=(FONT, 11, 'bold'), tags='shell')
        self.body.place(x=10, y=48, width=self.width-20, height=self.body_height)
        self.body.configure(scrollregion=(0, 0, self.width-20, self.total_rows_height))
        self.draw_rows()

    def owner_configured(self, event):
        if event.widget in (self.owner, self.anchor, self.anchor.master):
            self.layout()

    def outside_pressed(self, event):
        if event.widget is self.anchor:
            return  # The selector's own release toggles the open menu.
        if not self.contains(event.widget):
            self.close('dismissed')
        # Do not consume the event: the same click can close or use the panel.

    def owner_unmapped(self, event):
        if event.widget is self.owner:
            self.close('owner-unmapped')

    def owner_destroyed(self, event):
        if event.widget is self.owner:
            self.close('owner-destroyed')

    def focus_out(self, event):
        self.schedule(75, self.check_focus)

    def check_focus(self):
        if not self.contains(self.focus_get()):
            self.close('dismissed')

    def close(self, reason='dismissed', restore_focus=False):
        if self.closed:
            return
        self.closed = True
        for timer in tuple(self.timers):
            self.after_cancel(timer)
        self.timers.clear()
        for sequence, binding in self.owner_bindings.items():
            try:
                self.owner.unbind(sequence, binding)
            except tk.TclError:
                pass
        tk.Frame.destroy(self)
        try:
            if restore_focus and self.anchor.winfo_exists():
                self.anchor.focus_set()
        except tk.TclError:
            pass
        if self.on_close:
            self.on_close(reason)

    def destroy(self):
        self.close('owner-destroyed')


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command, accent=False):
        font = tkfont.Font(root=parent, family=FONT, size=12, weight='bold')
        super().__init__(parent, bg=parent.cget('bg'), width=font.measure(text)+36,
                         height=42, highlightthickness=0, bd=0, takefocus=True,
                         cursor='hand2')
        self.text, self.command, self.accent = text, command, accent
        self.hovered, self.pressed = False, False
        self.bind('<Configure>', self.draw)
        self.bind('<Enter>', self.enter)
        self.bind('<Leave>', self.leave)
        self.bind('<ButtonPress-1>', self.press)
        self.bind('<ButtonRelease-1>', self.release)
        self.bind('<FocusIn>', self.draw)
        self.bind('<FocusOut>', self.draw)
        self.bind('<Return>', self.key)
        self.bind('<space>', self.key)

    def draw(self, event=None):
        self.delete('button')
        w, h = self.winfo_width(), self.winfo_height()
        border = GREEN if self.focus_get() is self else '#cedfd3'
        fill = '#38775f' if self.accent and (self.hovered or self.pressed) else (
            GREEN if self.accent else '#d8eadd' if self.hovered else PANEL)
        rounded(self, 1, 1, w-2, h-2, 15, border, 'button')
        rounded(self, 2, 2, w-4, h-4, 14, fill, 'button')
        self.create_text(w/2, h/2, text=self.text, font=(FONT, 12, 'bold'),
                         fill='#f8fcf8' if self.accent else TEXT, tags='button')

    def enter(self, event):
        self.hovered = True
        self.draw()

    def leave(self, event):
        self.hovered = False
        self.draw()

    def press(self, event):
        self.pressed = True
        self.focus_set()
        self.draw()
        return 'break'

    def release(self, event):
        pressed, self.pressed = self.pressed, False
        self.draw()
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()
        return 'break'

    def invoke(self):
        if self.command:
            return self.command()

    def key(self, event):
        self.invoke()
        return 'break'


class RoundedSelect(tk.Canvas):
    def __init__(self, parent, textvariable):
        super().__init__(parent, bg=parent.cget('bg'), width=300, height=40,
                         highlightthickness=0, bd=0, takefocus=True, cursor='hand2')
        self.variable = textvariable
        self.values, self.option_ids = [], []
        self.index, self.popup_window = -1, None
        self.bind('<Configure>', self.draw)
        self.bind('<ButtonRelease-1>', lambda _: self.open_menu())
        self.bind('<Down>', self.key)
        self.bind('<space>', self.key)
        self.bind('<Return>', self.key)
        self.bind('<FocusIn>', self.draw)
        self.bind('<FocusOut>', self.draw)
        self.bind('<Destroy>', self.on_destroy)

    def configure(self, cnf=None, **kwargs):
        if 'values' in kwargs:
            self.values = list(kwargs.pop('values'))
            self.option_ids = list(kwargs.pop('option_ids', range(len(self.values))))
        if cnf or kwargs:
            return super().configure(cnf, **kwargs)
        self.draw()

    config = configure

    def current(self, index=None):
        if index is None:
            return self.index
        self.index = index
        self.variable.set(self.values[index] if 0 <= index < len(self.values) else '')
        self.draw()

    def get(self):
        return self.variable.get()

    def draw(self, event=None):
        self.delete('selector')
        w, h = self.winfo_width(), self.winfo_height()
        edge = GREEN if self.focus_get() is self else BORDER
        rounded(self, 1, 1, w-2, h-2, 13, edge, 'selector')
        rounded(self, 2, 2, w-4, h-4, 12, CARD, 'selector')
        size = fitting_size(self, self.get(), 12, max(1, w-54), True, 9)
        self.create_text(14, h/2, text=self.get(), anchor='w', fill=TEXT,
                         font=(FONT, size, 'bold'), tags='selector')
        self.create_line(w-25, h/2-2, w-21, h/2+2, w-17, h/2-2,
                         fill=MUTED, width=2, tags='selector')

    def key(self, event):
        self.open_menu()
        return 'break'

    def open_menu(self):
        if self.popup_window and not self.popup_window.closed:
            self.popup_window.escape()
            return
        self.winfo_toplevel().update_idletasks()
        self.focus_set()
        current_id = self.option_ids[self.index] if 0 <= self.index < len(self.option_ids) else object()
        items = [PopupItem(name, lambda key=key: self.pick(key), key == current_id)
                 for key, name in zip(self.option_ids, self.values)]
        self.popup_window = InlinePopup(self, items,
                                        width=max(272, min(360, self.winfo_width())),
                                        on_close=lambda _: self.popup_closed())

    def close_menu(self):
        if self.popup_window and not self.popup_window.closed:
            self.popup_window.close('dismissed')

    def popup_closed(self):
        self.popup_window = None

    def pick(self, key):
        if key not in self.option_ids:
            return
        self.current(self.option_ids.index(key))
        self.event_generate('<<ComboboxSelected>>')

    def on_destroy(self, event):
        if event.widget is self and self.popup_window:
            self.popup_window.close('owner-destroyed')
