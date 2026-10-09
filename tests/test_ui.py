"""Regression checks for complete numbers, window focus and centered panels."""
import ctypes as ct
from pathlib import Path
import tempfile
import subprocess
import sys
import tkinter as tk
from tkinter import font as tkfont
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app
import core
from ui_helpers import RECT, native_window, work_area, CARD
from windows_integration import WindowsBridge


class DesktopUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            ct.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ui-check-', dir=Path(__file__).parent)
        self.original_paths = core.SETTINGS, core.SNAPSHOT
        core.SETTINGS = Path(self.temp.name)/'settings.json'
        core.SNAPSHOT = Path(self.temp.name)/'snapshot.json'
        self.root = tk.Tk()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.dashboard = app.Dashboard(self.root, offline=True)
        for timer in self.root.tk.call('after', 'info'):
            callback = str(self.root.tk.call('after', 'info', timer))
            if 'refresh' in callback or 'tick' in callback:
                self.root.after_cancel(timer)
        self.value = '3053.63123456789 万'
        self.dashboard.results = {'codex': core.card('codex', '本机日志', [
            core.metric('今日 token · 本机', self.value),
            core.metric('本月 token · 本机', '1234567890.123456789 亿'),
            core.metric('5 小时额度', '41%', used=41),
            core.metric('周额度', '9%', used=9)])}
        self.dashboard.surface.render()
        self.bridge = WindowsBridge(self.temp.name)
        self.bridge.folder = core.ROOT
        self.dashboard.bridge = self.bridge
        self.bridge.attach(self.root)
        self.root.update()

    def tearDown(self):
        self.bridge.close()
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        self.root.destroy()
        core.SETTINGS, core.SNAPSHOT = self.original_paths
        self.temp.cleanup()
        self.assertEqual(self.callback_errors, [], 'No errors in scheduled GUI callbacks')

    def assert_centered(self, window, anchor):
        window.update_idletasks()
        left, top, right, bottom = work_area(anchor)
        user, hwnd = native_window(window)
        user.GetWindowRect.argtypes = [ct.c_void_p, ct.POINTER(RECT)]
        rect = RECT()
        self.assertTrue(user.GetWindowRect(hwnd, ct.byref(rect)))
        self.assertLessEqual(abs((rect.left+rect.right)-(left+right)), 2)
        self.assertLessEqual(abs((rect.top+rect.bottom)-(top+bottom)), 2)
        self.assertGreaterEqual(rect.top, top)
        self.assertLessEqual(rect.bottom, bottom)

    def test_collapsed_and_expanded_amounts_remain_complete(self):
        surface = self.dashboard.surface
        for expanded in (False, True):
            surface.visible_bubble = expanded
            surface.render()
            matches = [i for i in surface.canvas.find_withtag('amount')
                       if surface.canvas.itemcget(i, 'text') == self.value]
            self.assertEqual(len(matches), 1)
            item = matches[0]
            bounds = surface.canvas.bbox(item)
            self.assertGreaterEqual(bounds[0], 0)
            self.assertLessEqual(bounds[2], 412 if expanded else 137)
            self.assertNotIn('…', surface.canvas.itemcget(item, 'text'))
            if not expanded:
                font = tkfont.Font(root=self.root, font=surface.canvas.itemcget(item, 'font'))
                self.assertLess(font.actual('size'), 16)

    def test_bubble_click_hides_companion_and_closing_restores_it(self):
        self.dashboard.surface.visible_bubble = True
        self.dashboard.surface.render()
        self.dashboard.surface.release(SimpleNamespace(x=180, y=234))
        details = self.dashboard.details
        self.root.update()
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertTrue(details.winfo_ismapped())
        self.assert_centered(details, self.root)
        self.bridge.restore()
        self.root.update()
        self.assertEqual(self.root.state(), 'withdrawn')
        duplicate = WindowsBridge(self.temp.name)
        self.assertTrue(duplicate.activate_existing())
        self.bridge.poll()
        self.assertEqual(self.root.state(), 'withdrawn')
        self.dashboard.render()
        self.assertEqual(self.root.state(), 'withdrawn')
        details.close()
        self.root.update()
        self.assertEqual(self.root.state(), 'normal')
        self.assertTrue(self.root.winfo_ismapped())
        self.assertTrue(self.bridge.user.GetWindowLongPtrW(self.bridge.hwnd, -20) & 0x8)

    def test_settings_from_details_keep_companion_hidden_until_both_close(self):
        self.dashboard.open_details('codex')
        details = self.dashboard.details
        self.dashboard.open_settings()
        settings = self.dashboard.settings
        self.root.update()
        self.assert_centered(settings, details)
        self.assertEqual(self.root.state(), 'withdrawn')
        self.bridge.restore()
        self.assertEqual(self.root.state(), 'withdrawn')
        settings.destroy()
        self.root.update()
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertIs(self.dashboard.active_panel(), details)
        details.close()
        self.root.update()
        self.assertEqual(self.root.state(), 'normal')
        self.dashboard.open_settings()
        settings = self.dashboard.settings
        self.assert_centered(settings, self.root)
        self.assertEqual(self.root.state(), 'withdrawn')
        settings.destroy()
        self.root.update()
        self.assertEqual(self.root.state(), 'normal')

    def test_main_numeric_labels_resize_without_omitting_digits(self):
        self.dashboard.open_details('codex')
        details = self.dashboard.details
        details.geometry('600x600')
        self.root.update()
        def descend(widget):
            for child in widget.winfo_children():
                yield child
                yield from descend(child)
        amounts = [w for w in descend(details) if isinstance(w, tk.Label)
                   and w.cget('text') in (self.value, '1234567890.123456789 亿')]
        self.assertEqual(len(amounts), 2)
        for widget in amounts:
            self.assertNotIn('…', widget.cget('text'))
            font = tkfont.Font(root=self.root, font=widget.cget('font'))
            self.assertLessEqual(font.measure(widget.cget('text')), widget.winfo_width())
            self.assertEqual(widget.cget('bg'), CARD)
        details.close()

    def test_manual_configuration_is_centered_and_returns_to_settings(self):
        self.dashboard.open_settings()
        settings = self.dashboard.settings
        editor = settings.manual_editor()
        self.root.update()
        self.assert_centered(editor, settings)
        self.assertIs(self.dashboard.active_panel(), editor)
        editor.tk.call(editor.protocol('WM_DELETE_WINDOW'))
        self.root.update()
        self.assertIs(self.dashboard.active_panel(), settings)
        self.assertEqual(self.root.state(), 'withdrawn')
        settings.destroy()
        self.root.update()
        self.assertEqual(self.root.state(), 'normal')


    def wait_for_events(self, milliseconds=300):
        done = tk.BooleanVar(master=self.root, value=False)
        self.root.after(milliseconds, lambda: done.set(True))
        self.root.wait_variable(done)

    def click_popup_row(self, popup, index):
        start, end = popup.rows[index]
        y = int((start+end)/2-popup.body.canvasy(0))
        for event in ('<ButtonPress-1>', '<ButtonRelease-1>'):
            popup.body.event_generate(event, x=22, y=y,
                                      rootx=popup.body.winfo_rootx()+22,
                                      rooty=popup.body.winfo_rooty()+y)
        self.root.update()

    def test_provider_menu_survives_opening_release_refresh_and_taskbar_activation(self):
        surface = self.dashboard.surface
        surface.visible_bubble = True
        surface.render()
        popup = surface.provider_menu()
        self.root.update()
        popup.body.event_generate('<ButtonRelease-1>', x=22, y=23,
                                  rootx=popup.body.winfo_rootx()+22,
                                  rooty=popup.body.winfo_rooty()+23)
        self.wait_for_events()
        self.assertFalse(popup.closed)
        self.assertTrue(popup.winfo_ismapped())
        self.assertIs(popup.grab_current(), popup)
        self.dashboard.render()
        self.bridge.poll()
        self.bridge.restore()
        self.wait_for_events()
        self.assertFalse(popup.closed)
        user, hwnd = native_window(popup)
        user.GetWindowLongPtrW.argtypes = [ct.c_void_p, ct.c_int]
        user.GetWindowLongPtrW.restype = ct.c_ssize_t
        style = user.GetWindowLongPtrW(hwnd, -20)
        self.assertTrue(style & 0x8)  # WS_EX_TOPMOST
        self.assertTrue(style & 0x80)  # WS_EX_TOOLWINDOW: no extra taskbar button
        self.assertFalse(style & 0x40000)
        user.GetWindow.argtypes = [ct.c_void_p, ct.c_uint]
        user.GetWindow.restype = ct.c_void_p
        below = user.GetWindow(hwnd, 2)
        visited = set()
        while below and below != self.bridge.hwnd and below not in visited:
            visited.add(below)
            below = user.GetWindow(below, 2)
        self.assertEqual(below, self.bridge.hwnd, 'Popup must be above the companion')
        pid = next(pid for pid in self.dashboard.enabled_ids() if pid != surface.main_id)
        self.click_popup_row(popup, self.dashboard.enabled_ids().index(pid))
        self.assertTrue(popup.closed)
        self.assertIsNone(surface.popup_window)
        self.assertEqual(surface.main_id, pid)
        self.assertEqual(core.load_settings()['display_provider'], pid)
        self.dashboard.render()
        self.assertEqual(surface.main_id, pid)
        self.assertIsNone(self.root.grab_current())

    def test_context_menu_hides_bubble_restores_preference_and_opens_only_details(self):
        surface = self.dashboard.surface
        surface.visible_bubble = True
        self.dashboard.config['bubble_visible'] = True
        surface.render()
        popup = surface.menu(SimpleNamespace(x_root=self.root.winfo_rootx()+80,
                                              y_root=self.root.winfo_rooty()+170))
        self.wait_for_events()
        self.assertFalse(popup.closed)
        self.assertTrue(surface.context_menu_open)
        self.assertFalse(surface.visible_bubble)
        self.assertTrue(self.dashboard.config['bubble_visible'])
        self.assertEqual(surface.canvas.find_withtag('data'), ())
        self.assertEqual(self.root.winfo_width(), 144)
        self.bridge.restore()
        self.wait_for_events()
        self.assertFalse(popup.closed)
        popup.escape()
        self.root.update()
        self.assertTrue(surface.visible_bubble)
        self.assertFalse(surface.context_menu_open)
        self.assertEqual(self.root.winfo_width(), surface.WIDTH)
        popup = surface.menu()
        self.root.update()
        self.click_popup_row(popup, 0)
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertIsNone(surface.popup_window)
        self.assertTrue(self.dashboard.details.winfo_ismapped())
        self.dashboard.details.close()
        self.root.update()
        self.assertTrue(surface.visible_bubble)
        self.assertEqual(self.root.winfo_width(), surface.WIDTH)

    def test_context_toggle_changes_preference_only_when_selected(self):
        surface = self.dashboard.surface
        surface.visible_bubble = True
        self.dashboard.config['bubble_visible'] = True
        popup = surface.menu()
        self.root.update()
        self.click_popup_row(popup, 3)
        self.assertFalse(surface.visible_bubble)
        self.assertFalse(self.dashboard.config['bubble_visible'])
        popup = surface.menu()
        self.root.update()
        self.assertEqual(popup.items[3].label, '展开气泡')
        popup.escape()
        self.root.update()
        self.assertFalse(surface.visible_bubble)
        popup = surface.menu()
        self.root.update()
        self.click_popup_row(popup, 3)
        self.assertTrue(surface.visible_bubble)
        self.assertTrue(self.dashboard.config['bubble_visible'])

    def test_main_selector_keeps_platform_identity_across_refresh_and_duplicate_names(self):
        self.dashboard.open_details('codex')
        details = self.dashboard.details
        self.root.update()
        selector = details.selector
        selector.open_menu()
        self.wait_for_events()
        popup = selector.popup_window
        self.assertFalse(popup.closed)
        ids = list(details.platform_ids)
        pid = next(pid for pid in ids if pid and pid != 'codex')
        chosen_row = ids.index(pid)
        # An async refresh can reorder options or have two identical display labels.
        new_ids = list(reversed(ids))
        selector.configure(values=['同名平台']*len(new_ids), option_ids=new_ids)
        self.click_popup_row(popup, chosen_row)
        self.assertEqual(details.selected, pid)
        self.assertEqual(details.platform_ids[selector.current()], pid)
        self.assertIsNone(selector.popup_window)
        self.assertEqual(self.root.state(), 'withdrawn')
        selector.open_menu()
        self.root.update()
        popup = selector.popup_window
        self.assertFalse(popup.closed)
        popup.escape()
        self.root.update()
        self.assertIsNone(selector.popup_window)
        self.assertTrue(details.winfo_ismapped())
        details.close()

    def test_popup_outside_click_dismisses_without_changing_provider(self):
        surface = self.dashboard.surface
        original = surface.main_id
        popup = surface.provider_menu()
        self.wait_for_events()
        popup.shell.event_generate('<ButtonPress-1>', x=-10, y=-10,
                                   rootx=popup.winfo_rootx()-10,
                                   rooty=popup.winfo_rooty()-10)
        self.root.update()
        self.assertTrue(popup.closed)
        self.assertEqual(surface.main_id, original)
        self.assertIsNone(self.root.grab_current())


    def test_popup_focus_loss_and_keyboard_escape_restore_companion(self):
        surface = self.dashboard.surface
        surface.visible_bubble = True
        popup = surface.menu()
        self.wait_for_events()
        self.root.focus_force()
        self.wait_for_events()
        self.assertTrue(popup.closed)
        self.assertTrue(surface.visible_bubble)
        popup = surface.provider_menu()
        self.wait_for_events()
        popup.event_generate('<Down>')
        popup.event_generate('<Escape>')
        self.root.update()
        self.assertTrue(popup.closed)
        self.assertEqual(self.root.state(), 'normal')
        self.assertIsNone(self.root.grab_current())

    def test_exit_from_popup_destroys_application_without_callback_error(self):
        source = '''
from pathlib import Path
import tempfile
import tkinter as tk
import app, core
with tempfile.TemporaryDirectory(dir=Path.cwd()/'tests') as folder:
    core.SETTINGS = Path(folder)/'settings.json'
    core.SNAPSHOT = Path(folder)/'snapshot.json'
    root = tk.Tk()
    errors = []
    root.report_callback_exception = lambda *error: errors.append(error)
    dashboard = app.Dashboard(root, offline=True)
    popup = dashboard.surface.menu()
    root.update()
    popup.select(len(popup.items)-1)
    assert not errors, errors
    assert popup.closed
print('Exit menu closed cleanly')
'''
        process = subprocess.run([sys.executable, '-c', source], cwd=core.ROOT,
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, '')
        self.assertIn('Exit menu closed cleanly', process.stdout)

    def test_context_menu_never_overlaps_companion_at_screen_edges(self):
        surface = self.dashboard.surface
        surface.visible_bubble = False
        surface.render()
        left, top, right, bottom = work_area(self.root)
        positions = [(left+8, top+8), (right-152, top+8),
                     (left+8, bottom-surface.HEIGHT-8),
                     (right-152, bottom-surface.HEIGHT-8),
                     (left+(right-left)//2, top+(bottom-top-surface.HEIGHT)//2)]
        for x, y in positions:
            with self.subTest(position=(x, y)):
                self.root.geometry(f'144x{surface.HEIGHT}+{x}+{y}')
                self.root.update()
                popup = surface.menu(SimpleNamespace(
                    x_root=self.root.winfo_rootx()+72,
                    y_root=self.root.winfo_rooty()+160))
                self.wait_for_events(160)
                px, py = popup.winfo_rootx(), popup.winfo_rooty()
                rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
                pw, ph = popup.winfo_width(), popup.winfo_height()
                rw, rh = self.root.winfo_width(), self.root.winfo_height()
                self.assertTrue(popup.avoids_owner)
                self.assertFalse(surface.hide_pet_for_menu)
                self.assertTrue(px+pw+12 <= rx or px >= rx+rw+12 or
                                py+ph+12 <= ry or py >= ry+rh+12)
                self.assertGreaterEqual(px, left)
                self.assertGreaterEqual(py, top)
                self.assertLessEqual(px+pw, right)
                self.assertLessEqual(py+ph, bottom)
                self.assertFalse(popup.closed)
                self.dashboard.render()
                self.bridge.restore()
                self.assertTrue(popup.winfo_ismapped())
                popup.escape()
                self.root.update()

    def test_tiny_work_area_temporarily_hides_pet_instead_of_covering_menu(self):
        surface = self.dashboard.surface
        surface.visible_bubble = False
        surface.render()
        surface.animate()
        self.root.update()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        tiny_area = (rx-8, ry-8, rx+152, ry+surface.HEIGHT+8)
        with patch('soft_controls.work_area', return_value=tiny_area):
            popup = surface.menu(SimpleNamespace(x_root=rx+72, y_root=ry+160))
        self.root.update()
        self.assertFalse(popup.avoids_owner)
        self.assertTrue(surface.hide_pet_for_menu)
        self.assertEqual(surface.canvas.find_withtag('pet'), ())
        surface.animate()
        self.assertEqual(surface.canvas.find_withtag('pet'), ())
        self.assertTrue(popup.winfo_ismapped())
        popup.escape()
        surface.animate()
        self.root.update()
        self.assertFalse(surface.hide_pet_for_menu)
        self.assertTrue(surface.canvas.find_withtag('pet'))
        self.assertFalse(surface.visible_bubble)

    def test_robot_click_toggles_bubble_before_details_and_keeps_it_onscreen(self):
        surface = self.dashboard.surface
        surface.visible_bubble = False
        surface.render()
        left, top, right, bottom = work_area(self.root)
        self.root.geometry(f'144x{surface.HEIGHT}+{right-144}+{top+20}')
        self.root.update()
        surface.release(SimpleNamespace(x=70, y=160))
        self.root.update()
        self.assertTrue(surface.visible_bubble)
        self.assertTrue(self.dashboard.config['bubble_visible'])
        self.assertIsNone(self.dashboard.details)
        self.assertEqual(self.root.state(), 'normal')
        self.assertLessEqual(self.root.winfo_rootx()+self.root.winfo_width(), right)
        surface.release(SimpleNamespace(x=70, y=160))
        self.root.update()
        self.assertFalse(surface.visible_bubble)
        self.assertFalse(self.dashboard.config['bubble_visible'])
        self.assertFalse(core.load_settings()['bubble_visible'])
        self.assertEqual(self.root.winfo_width(), 144)
        self.assertIsNone(self.dashboard.details)
        surface.release(SimpleNamespace(x=70, y=160))
        self.root.update()
        self.assertTrue(surface.visible_bubble)
        self.assertTrue(core.load_settings()['bubble_visible'])
        self.assertIsNone(self.dashboard.details)
        surface.release(SimpleNamespace(x=180, y=234))
        self.root.update()
        self.assertTrue(self.dashboard.details.winfo_ismapped())
        self.assertEqual(self.root.state(), 'withdrawn')
        self.dashboard.details.close()

    def test_bubble_provider_menu_never_overlaps_bubble_or_pet(self):
        surface = self.dashboard.surface
        surface.visible_bubble = True
        surface.render()
        left, top, right, bottom = work_area(self.root)
        positions = [(left+8, top+8), (right-surface.WIDTH-8, top+8),
                     (left+8, bottom-surface.HEIGHT-8),
                     (right-surface.WIDTH-8, bottom-surface.HEIGHT-8)]
        for x, y in positions:
            with self.subTest(position=(x, y)):
                self.root.geometry(f'{surface.WIDTH}x{surface.HEIGHT}+{x}+{y}')
                self.root.update()
                popup = surface.provider_menu()
                self.wait_for_events(160)
                px, py = popup.winfo_rootx(), popup.winfo_rooty()
                rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
                self.assertTrue(popup.avoids_owner)
                self.assertTrue(surface.visible_bubble)
                self.assertTrue(px+popup.winfo_width()+12 <= rx or
                                px >= rx+self.root.winfo_width()+12 or
                                py+popup.winfo_height()+12 <= ry or
                                py >= ry+self.root.winfo_height()+12)
                self.assertFalse(popup.closed)
                popup.escape()
                self.root.update()

    def test_panel_selector_moves_with_panel_and_resize_keeps_it_attached(self):
        self.dashboard.open_details('codex')
        details = self.dashboard.details
        selector = details.selector
        selector.open_menu()
        self.wait_for_events(160)
        popup = selector.popup_window
        self.assertIsInstance(popup, tk.Frame)
        self.assertIs(popup.winfo_toplevel(), details)
        self.assertIsNone(self.root.grab_current())
        self.assertEqual(popup.winfo_rootx(), selector.winfo_rootx())
        self.assertEqual(popup.winfo_rooty(), selector.winfo_rooty()+selector.winfo_height()+5)
        offset = (popup.winfo_rootx()-details.winfo_rootx(),
                  popup.winfo_rooty()-details.winfo_rooty())
        before = popup.winfo_rootx(), popup.winfo_rooty()
        user, hwnd = native_window(details)
        user.GetWindowRect.argtypes = [ct.c_void_p, ct.POINTER(RECT)]
        outer = RECT()
        self.assertTrue(user.GetWindowRect(hwnd, ct.byref(outer)))
        user.SetWindowPos.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_int, ct.c_int,
                                     ct.c_int, ct.c_int, ct.c_uint]
        user.SetWindowPos(hwnd, None, outer.left+42, outer.top+28, 0, 0, 0x15)
        self.root.update()
        self.assertFalse(popup.closed)
        self.assertEqual((popup.winfo_rootx()-details.winfo_rootx(),
                          popup.winfo_rooty()-details.winfo_rooty()), offset)
        self.assertEqual((popup.winfo_rootx()-before[0], popup.winfo_rooty()-before[1]), (42, 28))
        details.geometry('600x600')
        self.root.update()
        self.assertFalse(popup.closed)
        self.assertGreaterEqual(popup.winfo_x(), 0)
        self.assertLessEqual(popup.winfo_x()+popup.winfo_width(), details.winfo_width())
        self.assertGreaterEqual(popup.winfo_y(), 0)
        self.assertLessEqual(popup.winfo_y()+popup.winfo_height(), details.winfo_height())
        details.close()

    def test_native_panel_close_works_while_selector_menu_is_open(self):
        self.dashboard.open_details('codex')
        details = self.dashboard.details
        details.selector.open_menu()
        self.wait_for_events(160)
        popup = details.selector.popup_window
        self.assertIsNone(self.root.grab_current())
        user, hwnd = native_window(details)
        user.PostMessageW.argtypes = [ct.c_void_p, ct.c_uint, ct.c_size_t, ct.c_ssize_t]
        self.assertTrue(user.PostMessageW(hwnd, 0x10, 0, 0))  # Native title-bar close.
        self.root.update()
        self.assertFalse(details.winfo_exists())
        self.assertTrue(popup.closed)
        self.assertIsNone(self.dashboard.details)
        self.assertEqual(self.root.state(), 'normal')

    def test_panel_return_button_works_in_one_click_with_selector_open(self):
        self.dashboard.open_details('codex')
        details = self.dashboard.details
        details.selector.open_menu()
        self.wait_for_events(160)
        popup = details.selector.popup_window
        def descend(widget):
            for child in widget.winfo_children():
                yield child
                yield from descend(child)
        from soft_controls import RoundedButton
        target = next(w for w in descend(details) if isinstance(w, RoundedButton)
                      and w.text == '返回挂件')
        for event in ('<ButtonPress-1>', '<ButtonRelease-1>'):
            target.event_generate(event, x=20, y=20,
                                  rootx=target.winfo_rootx()+20, rooty=target.winfo_rooty()+20)
        self.root.update()
        self.assertFalse(details.winfo_exists())
        self.assertTrue(popup.closed)
        self.assertEqual(self.root.state(), 'normal')

    def test_panel_selector_closes_on_outside_click_and_repeated_arrow_click(self):
        self.dashboard.open_details('codex')
        details = self.dashboard.details
        selector = details.selector
        selector.open_menu()
        self.wait_for_events(160)
        popup = selector.popup_window
        details.area.event_generate('<ButtonPress-1>', x=2, y=2,
                                    rootx=details.area.winfo_rootx()+2,
                                    rooty=details.area.winfo_rooty()+2)
        self.root.update()
        self.assertTrue(popup.closed)
        self.assertTrue(details.winfo_exists())
        selector.open_menu()
        self.wait_for_events(160)
        popup = selector.popup_window
        for event in ('<ButtonPress-1>', '<ButtonRelease-1>'):
            selector.event_generate(event, x=20, y=20,
                                    rootx=selector.winfo_rootx()+20, rooty=selector.winfo_rooty()+20)
        self.root.update()
        self.assertTrue(popup.closed)
        self.assertIsNone(selector.popup_window)
        details.close()

if __name__ == '__main__':
    unittest.main()
