"""Shared soft cards, complete-value typography and window placement."""
import ctypes as ct
import os
import tkinter as tk
from tkinter import font as tkfont

FONT = 'Microsoft YaHei'
BG = '#edf5ef'
CARD = '#f8fcf8'
PANEL = '#e0ece3'
TEXT = '#25483d'
MUTED = '#60796d'
GREEN = '#43836c'
BLUE = '#527d92'
AMBER = '#906330'
RED = '#b46664'


def fitting_size(widget, value, maximum, width, bold=False, minimum=1):
    value = str(value)
    size = maximum
    font = tkfont.Font(root=widget, family=FONT, size=size,
                       weight='bold' if bold else 'normal')
    while size > minimum and font.measure(value) > max(1, width):
        size -= 1
        font.configure(size=size)
    return size


def fitted_label(parent, value, maximum=28, color=TEXT, bold=True, minimum=8):
    # A one-character requested width prevents long numbers from widening their grid column.
    label = tk.Label(parent, text=str(value), bg=parent.cget('bg'), fg=color,
                     font=(FONT, maximum, 'bold' if bold else 'normal'), width=1,
                     anchor='w', justify='left', bd=0, padx=0, pady=0)
    def fit(event):
        width = max(1, event.width-2)
        size = fitting_size(label, value, maximum, width, bold, minimum)
        label.configure(font=(FONT, size, 'bold' if bold else 'normal'), wraplength=width)
    label.bind('<Configure>', fit)
    return label


def rounded(canvas, x, y, width, height, radius, fill, tag):
    r = min(radius, width/2, height/2)
    points = [x+r,y, x+width-r,y, x+width,y, x+width,y+r,
              x+width,y+height-r, x+width,y+height, x+width-r,y+height,
              x+r,y+height, x,y+height, x,y+height-r, x,y+r, x,y]
    return canvas.create_polygon(points, smooth=True, splinesteps=32,
                                 fill=fill, outline='', tags=tag)


class SoftCard(tk.Frame):
    def __init__(self, parent, padx=22, pady=18):
        super().__init__(parent, bg=parent.cget('bg'))
        self.padx, self.pady = padx, pady
        self.canvas = tk.Canvas(self, bg=self.cget('bg'), width=1, height=1,
                                highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=CARD)
        self.body.pack(fill='both', expand=True, padx=padx, pady=pady)
        self.canvas.bind('<Configure>', self.layout)

    def layout(self, event):
        self.canvas.delete('card')
        rounded(self.canvas, 1, 3, event.width-2, event.height-3, 22, '#dce9de', 'card')
        rounded(self.canvas, 0, 0, event.width-2, event.height-3, 22, CARD, 'card')
        self.canvas.tag_lower('card')


class RECT(ct.Structure):
    _fields_ = [('left',ct.c_long), ('top',ct.c_long), ('right',ct.c_long), ('bottom',ct.c_long)]


class MONITORINFO(ct.Structure):
    _fields_ = [('cbSize',ct.c_uint32), ('rcMonitor',RECT), ('rcWork',RECT), ('dwFlags',ct.c_uint32)]


def native_window(widget):
    user = ct.WinDLL('user32')
    user.GetAncestor.argtypes = [ct.c_void_p, ct.c_uint]
    user.GetAncestor.restype = ct.c_void_p
    return user, user.GetAncestor(widget.winfo_id(), 2)


def work_area(anchor):
    if os.name == 'nt':
        try:
            user, hwnd = native_window(anchor)
            user.MonitorFromWindow.argtypes = [ct.c_void_p, ct.c_uint]
            user.MonitorFromWindow.restype = ct.c_void_p
            user.GetMonitorInfoW.argtypes = [ct.c_void_p, ct.POINTER(MONITORINFO)]
            info = MONITORINFO()
            info.cbSize = ct.sizeof(info)
            if user.GetMonitorInfoW(user.MonitorFromWindow(hwnd, 2), ct.byref(info)):
                r = info.rcWork
                return r.left, r.top, r.right, r.bottom
        except (OSError, tk.TclError):
            pass
    return 0, 0, anchor.winfo_screenwidth(), anchor.winfo_screenheight()


def center_window(window, anchor, width, height):
    left, top, right, bottom = work_area(anchor)
    width = max(260, min(width, right-left-64))
    height = max(280, min(height, bottom-top-64))
    window.minsize(min(600,width), min(500,height))
    x, y = left+(right-left-width)//2, top+(bottom-top-height)//2
    window.geometry(f'{width}x{height}+{x}+{y}')
    window.update_idletasks()
    if os.name == 'nt':
        user, hwnd = native_window(window)
        user.GetWindowRect.argtypes = [ct.c_void_p, ct.POINTER(RECT)]
        rect = RECT()
        if user.GetWindowRect(hwnd, ct.byref(rect)):
            x = left+(right-left-(rect.right-rect.left))//2
            y = top+(bottom-top-(rect.bottom-rect.top))//2
            user.SetWindowPos.argtypes = [ct.c_void_p,ct.c_void_p,ct.c_int,ct.c_int,ct.c_int,ct.c_int,ct.c_uint]
            user.SetWindowPos(hwnd, None, x, y, 0, 0, 0x15)


def keep_window_visible(window):
    window.update_idletasks()
    left, top, right, bottom = work_area(window)
    width, height = window.winfo_width(), window.winfo_height()
    x = max(left, min(window.winfo_rootx(), right-width))
    y = max(top, min(window.winfo_rooty(), bottom-height))
    if os.name == 'nt':
        user, hwnd = native_window(window)
        user.SetWindowPos.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_int, ct.c_int,
                                     ct.c_int, ct.c_int, ct.c_uint]
        user.SetWindowPos(hwnd, None, x, y, 0, 0, 0x15)
    else:
        window.geometry(f'{width}x{height}+{x}+{y}')
