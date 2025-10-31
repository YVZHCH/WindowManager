
# 依赖: pywin32, keyboard, PyQt5
# pip install pywin32 keyboard PyQt5
# pyinstaller --onefile --windowed --icon=icon.ico --add-data "icon.ico;." main.py



import sys
import threading
import time
import json
from functools import partial

import keyboard  # global hotkeys
from PyQt5 import QtWidgets, QtGui, QtCore
import win32gui
import win32con
import win32api
import win32process

import os, sys

def resource_path(relative_path):
    """获取打包后资源的正确路径"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# ---------------------------
# Utility: Win32 helpers
# ---------------------------

def enum_windows():
    """Return list of (hwnd, title) for visible top-level windows with non-empty titles, excluding tray, tool windows and own process windows."""
    import win32process  # 需确保导入该模块
    windows = []
    # 获取当前进程ID
    current_pid = win32process.GetCurrentProcessId()

    def callback(hwnd, extra):
        # 检查窗口是否可见
        if not win32gui.IsWindowVisible(hwnd):
            return
        # 获取窗口所属进程ID
        _, process_id = win32process.GetWindowThreadProcessId(hwnd)
        if process_id == current_pid:
            return  # 排除自身进程窗口
        # 获取窗口标题
        title = win32gui.GetWindowText(hwnd)
        if not title or not title.strip():
            return
        # 检查窗口样式，排除工具窗口和托盘相关窗口
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)

        # 排除工具窗口 (WS_EX_TOOLWINDOW) 和不显示在任务栏的窗口 (WS_EX_APPWINDOW 取反)
        if (ex_style & win32con.WS_EX_TOOLWINDOW) and not (ex_style & win32con.WS_EX_APPWINDOW):
            return

        windows.append((hwnd, title))

    win32gui.EnumWindows(callback, None)
    return windows

def is_window(hwnd):
    try:
        return win32gui.IsWindow(hwnd)
    except Exception:
        return False


def set_topmost(hwnd, on=True):
    if not is_window(hwnd): return False
    try:
        if on:
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        else:
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        return True
    except Exception as e:
        print("set_topmost error:", e)
        return False


def minimize_window(hwnd):
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    except Exception as e:
        print("minimize error", e)


def restore_window(hwnd):
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception as e:
        print("restore error", e)


def focus_window(hwnd):
    try:
        if not is_window(hwnd): return False
        # try to bring to foreground
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            # fallback: attach thread input
            fg = win32gui.GetForegroundWindow()
            if fg:
                tid_fore = win32process.GetWindowThreadProcessId(fg)[0]
                tid_target = win32process.GetWindowThreadProcessId(hwnd)[0]
                try:
                    win32api.AttachThreadInput(tid_fore, tid_target, True)
                    win32gui.SetForegroundWindow(hwnd)
                    win32api.AttachThreadInput(tid_fore, tid_target, False)
                except Exception:
                    pass
        return True
    except Exception as e:
        print("focus_window error", e)
        return False


def get_window_rect(hwnd):
    try:
        return win32gui.GetWindowRect(hwnd)
    except Exception as e:
        return None


def set_window_opacity(hwnd, alpha):
    """
    alpha: 0-255
    """
    try:
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex | win32con.WS_EX_LAYERED)
        # Use SetLayeredWindowAttributes
        win32gui.SetLayeredWindowAttributes(hwnd, 0, int(alpha), win32con.LWA_ALPHA)
        return True
    except Exception as e:
        print("set_window_opacity error", e)
        return False


def set_window_clickthrough(hwnd, on=True):
    """
    Make window click-through by setting WS_EX_TRANSPARENT. Note this affects input to the window.
    """
    try:
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if on:
            new = ex | win32con.WS_EX_TRANSPARENT
        else:
            new = ex & (~win32con.WS_EX_TRANSPARENT)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, new)
        # refresh
        win32gui.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_FRAMECHANGED)
        return True
    except Exception as e:
        print("set_window_clickthrough error", e)
        return False


def get_foreground_hwnd():
    try:
        return win32gui.GetForegroundWindow()
    except Exception:
        return None


def hwnd_to_title(hwnd):
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


# ---------------------------
# Data model: groups, hotkeys
# ---------------------------
DEFAULT_HOTKEYS = {
    'topmost': 't',
    'show_only': 'm',
    'transparent': 'p',
    'open_group_manager': 'g',
}

PERSIST_FILE = 'wm_config.json'


class Model:
    def __init__(self):
        # groups: map int->list of hwnds
        self.groups = {}  # example: {1: [hwnd, ...], 2: [...]}
        self.hotkeys = DEFAULT_HOTKEYS.copy()
        # group names support
        self.group_names = {i: f"组 {i}" for i in range(10)}
        self.load()

    def load(self):
        try:
            with open(PERSIST_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.groups = {int(k): v for k, v in data.get('groups', {}).items()}
                self.hotkeys = data.get('hotkeys', DEFAULT_HOTKEYS.copy())
                names = data.get('group_names', {})
                for i, n in names.items():
                    try:
                        self.group_names[int(i)] = n
                    except:
                        pass
        except FileNotFoundError:
            pass
        except Exception as e:
            print("load config error:", e)

    def save(self):
        try:
            data = {'groups': {str(k): v for k, v in self.groups.items()},
                    'hotkeys': self.hotkeys,
                    'group_names': {str(k): v for k, v in self.group_names.items()}}
            with open(PERSIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("save config error:", e)

    def add_to_group(self, group_id, hwnd):
        if not is_window(hwnd):
            return
        self.groups.setdefault(group_id, [])
        if hwnd not in self.groups[group_id]:
            self.groups[group_id].append(hwnd)
            self.save()

    def remove_from_group(self, group_id, hwnd):
        if group_id in self.groups and hwnd in self.groups[group_id]:
            self.groups[group_id].remove(hwnd)
            self.save()

    def set_group(self, group_id, hwnd_list):
        self.groups[group_id] = [h for h in hwnd_list if is_window(h)]
        self.save()

    def set_group_name(self, group_id, name):
        self.group_names[group_id] = name
        self.save()


# ---------------------------
# Overlay: a small always-on-top PyQt window placed over target window
# ---------------------------

class OverlayWindow(QtWidgets.QWidget):
    """
    Small overlay window that sits on top of target hwnd.
    Contains a small button; clicking expands a toolbar with slider and checkbox.
    """

    def __init__(self, target_hwnd, controller):
        super().__init__(flags=QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        self.target_hwnd = target_hwnd
        self.controller = controller  # reference to main controller to change opacity etc
        self.init_ui()
        self.update_position()
        # track target moving - poll
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self.update_position)
        self.poll_timer.start(400)

    def init_ui(self):
        self.button = QtWidgets.QPushButton("☰", self)
        self.button.setFixedSize(34, 24)
        self.button.clicked.connect(self.toggle_toolbar)
        self.toolbar = QtWidgets.QWidget(self)
        h = QtWidgets.QHBoxLayout(self.toolbar)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(30, 255)
        self.slider.setValue(self.controller.current_alpha)
        self.slider.valueChanged.connect(self.on_slider)
        self.checkbox = QtWidgets.QCheckBox("可交互")
        self.checkbox.setChecked(not self.controller.current_clickthrough)
        self.checkbox.stateChanged.connect(self.on_checkbox)
        h.addWidget(QtWidgets.QLabel("透明"))
        h.addWidget(self.slider)
        h.addWidget(self.checkbox)
        self.toolbar.hide()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.button)
        layout.addWidget(self.toolbar)
        self.setLayout(layout)
        self.adjustSize()
        self.setWindowOpacity(0.95)

    def toggle_toolbar(self):
        if self.toolbar.isVisible():
            self.toolbar.hide()
            self.adjustSize()
        else:
            self.toolbar.show()
            self.adjustSize()

    def on_slider(self, val):
        self.controller.set_transparent_alpha(val)

    def on_checkbox(self, state):
        # state: 2 checked => interactive (not clickthrough)
        interactive = (state == QtCore.Qt.Checked)
        self.controller.set_clickthrough(not interactive)

    def update_position(self):
        if not is_window(self.target_hwnd):
            self.close()
            return
        rect = get_window_rect(self.target_hwnd)
        if not rect:
            return
        left, top, right, bottom = rect
        w = right - left
        # place overlay top-center of target window
        self.move(left + max(0, w // 2 - self.width() // 2), top + 6)
        # ensure always-on-top
        self.raise_()


# ---------------------------
# Main Controller: handles operations and hotkeys
# ---------------------------

class Controller(QtCore.QObject):
    # signals for UI updates
    # now emit the foreground hwnd (int) when requesting group manager
    group_manager_requested = QtCore.pyqtSignal(int)
    hotkey_config_requested = QtCore.pyqtSignal()

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.pending_group = None  # when user pressed group-digit, waiting for letter
        self.pending_timer = None
        # hwnd -> OverlayWindow
        self.overlay_windows = {}
        # mapping overlay window handle (winId) -> target hwnd, to detect focus on overlay
        self.overlay_winid_map = {}
        self.topmost_state = {}  # hwnd->bool (tracks manual toggles)
        # transparent_state: hwnd -> dict(alpha, clickthrough, was_topmost)
        self.transparent_state = {}
        self.current_alpha = 200
        self.current_clickthrough = False

        # Start keyboard hooks in separate thread
        self.register_hotkeys()

    # -----------------------
    # Hotkey handling
    # -----------------------
    def register_hotkeys(self):
        # unregister existing and start a new keyboard thread
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        threading.Thread(target=self._keyboard_thread, daemon=True).start()

    def _keyboard_thread(self):
        # Register digits 0-9 for group select
        for d in '0123456789':
            try:
                keyboard.add_hotkey(f'ctrl+alt+{d}', lambda dd=d: self.on_group_digit(dd))
            except Exception as e:
                print("hotkey reg digit error", e)
        # Register action keys
        try:
            keyboard.add_hotkey(f'ctrl+alt+{self.model.hotkeys.get("topmost", "t")}',
                                lambda: self.on_action_trigger('topmost'))
            keyboard.add_hotkey(f'ctrl+alt+{self.model.hotkeys.get("show_only", "m")}',
                                lambda: self.on_action_trigger('show_only'))
            keyboard.add_hotkey(f'ctrl+alt+{self.model.hotkeys.get("transparent", "p")}',
                                lambda: self.on_action_trigger('transparent'))
            keyboard.add_hotkey(f'ctrl+alt+{self.model.hotkeys.get("open_group_manager", "g")}',
                                lambda: self.emit_group_manager())
        except Exception as e:
            print("hotkey reg error", e)
        # block the thread
        keyboard.wait()

    def emit_group_manager(self):
        hwnd = get_foreground_hwnd()
        if hwnd is None: hwnd = 0
        self.group_manager_requested.emit(int(hwnd))

    def on_group_digit(self, digit):
        try:
            gid = int(digit)
        except:
            return
        self.pending_group = gid
        if self.pending_timer and self.pending_timer.is_alive():
            self.pending_timer.cancel()
        self.pending_timer = threading.Timer(4.0, self.clear_pending_group)
        self.pending_timer.daemon = True
        self.pending_timer.start()
        QtCore.QMetaObject.invokeMethod(app_window, "show_group_prompt", QtCore.Qt.QueuedConnection,
                                        QtCore.Q_ARG(int, gid))

    def clear_pending_group(self):
        self.pending_group = None

    def on_action_trigger(self, action):
        """
        If pending_group is set, then perform action on that group.
        Otherwise operate on current foreground window.
        Special: if foreground hwnd corresponds to an overlay window, map to its target hwnd
        """
        target_hwnds = []
        if self.pending_group is not None:
            gid = self.pending_group
            self.pending_group = None
            if gid in self.model.groups:
                target_hwnds = [h for h in self.model.groups.get(gid, []) if is_window(h)]
            else:
                QtCore.QMetaObject.invokeMethod(app_window, "show_message", QtCore.Qt.QueuedConnection,
                                                QtCore.Q_ARG(str, f"分组 {gid} 为空"))
                return
        else:
            hwnd = get_foreground_hwnd()
            if not hwnd:
                return
            # if the foreground hwnd is actually one of our overlays, map it to the target window
            mapped = self.overlay_winid_map.get(int(hwnd))
            if mapped and is_window(mapped):
                hwnd = mapped
            target_hwnds = [hwnd]

        if action == 'topmost':
            for h in target_hwnds:
                self.toggle_topmost(h)
        elif action == 'show_only':
            for h in target_hwnds:
                self.toggle_show_only(h)
        elif action == 'transparent':
            for h in target_hwnds:
                self.toggle_transparent(h)

    # -----------------------
    # Action implementations
    # -----------------------
    def toggle_topmost(self, hwnd):
        prev = self.topmost_state.get(hwnd, False)
        new = not prev
        if not is_window(hwnd):
            return
        set_topmost(hwnd, new)
        self.topmost_state[hwnd] = new
        if new:
            QtCore.QMetaObject.invokeMethod(app_window, "show_message", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, f"{hwnd_to_title(hwnd)} 设置置顶"))
        else:
            QtCore.QMetaObject.invokeMethod(app_window, "show_message", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, f"{hwnd_to_title(hwnd)} 取消置顶"))

    def toggle_show_only(self, hwnd):
        already_only = getattr(self, 'only_shown_hwnd', None) == hwnd
        if already_only:
            to_restore = getattr(self, 'minimized_by_only', [])
            for h in to_restore:
                if is_window(h):
                    restore_window(h)
            self.only_shown_hwnd = None
            self.minimized_by_only = []
            QtCore.QMetaObject.invokeMethod(app_window, "show_message", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, "恢复所有窗口"))
            return

        # 判断是否是分组操作
        group_hwnds = []
        for gid, hwnds in self.model.groups.items():
            if hwnd in hwnds:
                group_hwnds = [h for h in hwnds if is_window(h)]
                break

        if group_hwnds:
            target_hwnds = group_hwnds
        else:
            target_hwnds = [hwnd]

        all_windows = enum_windows()
        minimized = []
        for h, title in all_windows:
            if h in target_hwnds:
                continue
            if self.topmost_state.get(h, False):
                continue
            try:
                win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
                minimized.append(h)
            except Exception:
                pass

        # 恢复目标分组窗口
        for h in target_hwnds:
            try:
                restore_window(h)
            except Exception:
                pass

        self.only_shown_hwnd = hwnd
        self.minimized_by_only = minimized
        QtCore.QMetaObject.invokeMethod(app_window, "show_message", QtCore.Qt.QueuedConnection,
                                        QtCore.Q_ARG(str,
                                                     f"仅显示: {', '.join(hwnd_to_title(h) for h in target_hwnds)}"))

    def toggle_transparent(self, hwnd):
        # if already transparent -> cancel (restore)
        state = self.transparent_state.get(hwnd, None)
        if state is not None:
            try:
                set_window_opacity(hwnd, 255)
                set_window_clickthrough(hwnd, False)
                was_top = state.get('was_topmost', False)
                set_topmost(hwnd, was_top)
            except Exception:
                pass

            self.transparent_state.pop(hwnd, None)

            # --- 修复闪退关键 ---
            if hwnd in self.overlay_windows:
                try:
                    ov = self.overlay_windows.pop(hwnd)
                    wid = int(ov.winId())
                    if wid in self.overlay_winid_map:
                        self.overlay_winid_map.pop(wid, None)
                    # 先停止计时器再安全关闭
                    if hasattr(ov, "poll_timer"):
                        ov.poll_timer.stop()
                        ov.poll_timer.deleteLater()
                    ov.deleteLater()  # 使用 Qt 安全删除
                except Exception as e:
                    print("overlay close error:", e)

            QtCore.QMetaObject.invokeMethod(
                app_window, "show_message", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, f"{hwnd_to_title(hwnd)} 取消半透明")
            )
            return

        # Apply semi-transparent + topmost + overlay
        was_topmost = bool(self.topmost_state.get(hwnd, False))
        set_topmost(hwnd, True)
        alpha = self.current_alpha
        set_window_opacity(hwnd, alpha)
        set_window_clickthrough(hwnd, self.current_clickthrough)
        self.transparent_state[hwnd] = {
            'alpha': alpha, 'clickthrough': self.current_clickthrough, 'was_topmost': was_topmost
        }

        QtCore.QMetaObject.invokeMethod(
            app_window, "_create_overlay_for_hwnd", QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(int, hwnd)
        )
        QtCore.QMetaObject.invokeMethod(
            app_window, "show_message", QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, f"{hwnd_to_title(hwnd)} 设置半透明")
        )

    def set_transparent_alpha(self, alpha):
        self.current_alpha = alpha
        for hwnd in list(self.transparent_state.keys()):
            if is_window(hwnd):
                set_window_opacity(hwnd, alpha)
                self.transparent_state[hwnd]['alpha'] = alpha

    def set_clickthrough(self, on):
        self.current_clickthrough = on
        for hwnd in list(self.transparent_state.keys()):
            if is_window(hwnd):
                set_window_clickthrough(hwnd, on)
                self.transparent_state[hwnd]['clickthrough'] = on


# ---------------------------
# PyQt UI: main app window, tray, group manager, hotkey config
# ---------------------------

class AppWindow(QtWidgets.QMainWindow):
    def __init__(self, model, controller):
        super().__init__()
        self.model = model
        self.controller = controller
        # connect group_manager_requested signal with hwnd arg
        self.controller.group_manager_requested.connect(self.open_group_manager_by_hwnd)
        self.controller.hotkey_config_requested.connect(self.open_hotkey_config)
        self.setWindowTitle("Window Manager")
        self.setGeometry(300, 300, 500, 400)
        icon_path = resource_path("icon.ico")
        self.tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(icon_path), self)
        self.tray.setToolTip("窗口管理器 - 运行中")
        self.tray.setVisible(True)
        # self.tray.activated.connect(self.on_tray_activated)
        self.create_tray_menu()
        self.status_label = QtWidgets.QLabel("运行中，托盘可用。Ctrl+Alt+T/M/P/G 等", self)
        self.setCentralWidget(self.status_label)
        self.prompt = None

    def create_tray_menu(self):
        menu = QtWidgets.QMenu()

        # === 动态比例计算 ===
        screen = QtWidgets.QApplication.primaryScreen()
        rect = screen.availableGeometry()
        w, h = rect.width(), rect.height()

        font_ratio = max(0.9, min(1.5, w / 1920))  # 基于 1080p 比例
        padding_v = int(6 * font_ratio)
        padding_h = int(20 * font_ratio)
        border_radius = int(6 * font_ratio)
        font_size = int(14 * font_ratio)

        # === 深色样式（自适应比例） ===
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: #2D2D30;
                color: white;
                border: 1px solid #5A5A5A;
                border-radius: {border_radius}px;
                padding: {padding_v}px;
                font-size: {font_size}px;
            }}
            QMenu::item {{
                padding: {padding_v}px {padding_h}px;
                background-color: transparent;
            }}
            QMenu::item:selected {{
                background-color: #505050;
                border-radius: {border_radius}px;
            }}
            QMenu::separator {{
                height: 1px;
                background: #5A5A5A;
                margin: {int(padding_v / 2)}px {int(padding_h / 2)}px;
            }}
        """)

        # === 菜单项 ===

        open_groups_action = menu.addAction("打开分组管理")
        open_groups_action.triggered.connect(self.open_group_manager)
        hotkey_action = menu.addAction("修改快捷键")
        hotkey_action.triggered.connect(self.open_hotkey_config)
        about_action = menu.addAction("关于")
        about_action.triggered.connect(self.show_about)
        exit_action = menu.addAction("退出")
        exit_action.triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)

    @QtCore.pyqtSlot()
    def open_group_manager(self):
        # fallback when invoked from menu: no selected hwnd
        gm = GroupManager(self.model, self.controller, select_hwnd=0)
        gm.exec_()

    @QtCore.pyqtSlot(int)
    def open_group_manager_by_hwnd(self, hwnd):
        gm = GroupManager(self.model, self.controller, select_hwnd=hwnd)
        gm.exec_()

    @QtCore.pyqtSlot()
    def open_hotkey_config(self):
        dlg = HotkeyConfigDialog(self.model)
        if dlg.exec_():
            # saved: re-register hotkeys
            self.model.save()
            self.controller.register_hotkeys()
            self.show_message("已保存快捷键并重新注册")

    # @QtCore.pyqtSlot()
    # def show_about(self):
    #     QtWidgets.QMessageBox.information(self, "关于", "窗口管理器\n作者: 羽中\n说明: 通过快捷键对窗口进行便捷管理")
    @QtCore.pyqtSlot()
    def show_about(self):
        """显示关于对话框（自适应比例 + 可点击关闭）"""
        # 获取屏幕尺寸
        screen = QtWidgets.QApplication.primaryScreen()
        rect = screen.availableGeometry()
        w, h = rect.width(), rect.height()

        # 根据分辨率动态计算比例
        font_ratio = max(0.9, min(1.5, w / 1920))  # 1080p 为基准
        radius = int(10 * font_ratio)
        padding_v = int(14 * font_ratio)
        padding_h = int(45 * font_ratio)
        font_size = int(18 * font_ratio)
        button_size = int(16 * font_ratio)

        # 创建无边框淡色面板
        about_widget = QtWidgets.QWidget(
            flags=QtCore.Qt.Tool |
                  QtCore.Qt.FramelessWindowHint |
                  QtCore.Qt.WindowStaysOnTopHint
        )
        about_widget.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        about_widget.setWindowTitle("关于窗口管理器")

        about_widget.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(40, 40, 40, 230);
                color: white;
                border-radius: {radius}px;
                padding: {padding_v}px {padding_h}px;
                font-size: {font_size}px;
            }}
            QPushButton {{
                background-color: rgba(40, 40, 40, 230);
                color: white;
                border: none;
                padding: {button_size}px {button_size * 2}px;
                border-radius: {int(radius * 0.6)}px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 60);
            }}
            a {{
                color: #7cd3ff;
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
        """)

        # 文本内容
        text_label = QtWidgets.QLabel("""
            <html>
            <body>
            <p><b>窗口管理器 v1.0</b></p>
            <p>通过快捷键对窗口进行分组、置顶、半透明、仅显示等操作</p>
            <p>
                Powered by 羽中-Bilibili
                <a href='https://space.bilibili.com/353357823'>↗</a>
                -Github
                <a href='https://github.com/YVZHCH'>↗</a> 
            </p>
            <p>© 2025 窗口管理器</p>
            </body>
            </html>
        """)
        text_label.setTextFormat(QtCore.Qt.RichText)
        text_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.LinksAccessibleByMouse)
        text_label.setOpenExternalLinks(True)
        text_label.setAlignment(QtCore.Qt.AlignCenter)

        # 关闭按钮（位于窗口内、底部居中）
        close_btn = QtWidgets.QPushButton("关闭")
        close_btn.clicked.connect(about_widget.close)

        # 布局：文本在上，关闭按钮在下方居中
        layout = QtWidgets.QVBoxLayout(about_widget)
        layout.setSpacing(int(10 * font_ratio))
        layout.addWidget(text_label)
        layout.addWidget(close_btn, alignment=QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom)

        about_widget.adjustSize()
        about_widget.move(rect.center().x() - about_widget.width() // 2,
                          rect.center().y() - about_widget.height() // 2)
        about_widget.show()

        # 支持鼠标拖动移动
        def mousePressEvent(event):
            about_widget._drag_pos = event.globalPos() - about_widget.frameGeometry().topLeft()

        def mouseMoveEvent(event):
            if hasattr(about_widget, "_drag_pos"):
                about_widget.move(event.globalPos() - about_widget._drag_pos)

        about_widget.mousePressEvent = mousePressEvent
        about_widget.mouseMoveEvent = mouseMoveEvent

    @QtCore.pyqtSlot()
    def quit_app(self):
        QtWidgets.QApplication.quit()

    # @QtCore.pyqtSlot(int)
    # def show_group_prompt(self, gid):
    #     # show a small non-modal prompt telling user to press a letter for action
    #     if self.prompt:
    #         try:
    #             self.prompt.close()
    #         except:
    #             pass
    #     self.prompt = QtWidgets.QWidget(
    #         flags=QtCore.Qt.Tool | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.FramelessWindowHint)
    #     self.prompt.setLayout(QtWidgets.QVBoxLayout())
    #     self.prompt.layout().addWidget(
    #         QtWidgets.QLabel(f"分组 {gid} - 请输入一个字母执行操作（T:置顶, M:仅显示, P:半透明）"))
    #     self.prompt.adjustSize()
    #     cursor_pos = QtGui.QCursor.pos()
    #     self.prompt.move(cursor_pos.x(), cursor_pos.y())
    #     self.prompt.show()
    #     self._group_prompt_gid = gid
    #     self.prompt.keyPressEvent = lambda ev: self._on_prompt_key(ev)
    #     QtCore.QTimer.singleShot(4000, lambda: (self.prompt.close() if self.prompt else None))
    @QtCore.pyqtSlot(int)
    def show_group_prompt(self, gid):
        # show a small non-modal prompt telling user to press a letter for action
        if self.prompt:
            try:
                self.prompt.close()
            except:
                pass
        self.prompt = QtWidgets.QWidget(
            flags=QtCore.Qt.Tool | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.FramelessWindowHint)
        self.prompt.setLayout(QtWidgets.QVBoxLayout())
        label = QtWidgets.QLabel(f"分组 {gid} - 请输入一个字母执行操作（T:置顶, M:仅显示, P:半透明）")
        label.setAlignment(QtCore.Qt.AlignCenter)
        self.prompt.layout().addWidget(label)
        self.prompt.adjustSize()

        # === 深色样式 ===
        self.prompt.setStyleSheet("""
            QWidget {
                background-color: rgba(45, 45, 48, 230);
                color: white;
                border-radius: 8px;
                padding: 10px 20px;
                font-size: 16px;
            }
            QLabel {
                color: white;
            }
        """)

        cursor_pos = QtGui.QCursor.pos()
        self.prompt.move(cursor_pos.x(), cursor_pos.y())
        self.prompt.show()
        self._group_prompt_gid = gid
        self.prompt.keyPressEvent = lambda ev: self._on_prompt_key(ev)
        QtCore.QTimer.singleShot(4000, lambda: (self.prompt.close() if self.prompt else None))




    def _on_prompt_key(self, ev):
        ch = ev.text().lower()
        gid = getattr(self, '_group_prompt_gid', None)
        if not gid:
            return
        action_map = {'t': 'topmost', 'm': 'show_only', 'p': 'transparent'}
        if ch in action_map:
            self.prompt.close()
            self.controller.pending_group = gid
            self.controller.on_action_trigger(action_map[ch])

    # @QtCore.pyqtSlot(str)
    # def show_message(self, text):
    #     try:
    #         self.tray.showMessage("Window Manager", text, QtGui.QIcon(), 100)
    #     except Exception:
    #         pass
    #     self.status_label.setText(text)
    @QtCore.pyqtSlot(str)
    def show_message(self, text):
        # 更新状态栏文字
        self.status_label.setText(text)

        # 若已有旧提示，先关闭
        if hasattr(self, "_msg_popup") and self._msg_popup is not None:
            try:
                self._msg_popup.close()
            except Exception:
                pass

        # 获取屏幕尺寸信息
        screen = QtWidgets.QApplication.primaryScreen()
        rect = screen.availableGeometry()
        w, h = rect.width(), rect.height()

        # 根据屏幕分辨率动态计算尺寸比例
        font_ratio = max(0.9, min(1.5, w / 1920))  # 在 1080p 基准下缩放字体
        margin_x = int(w * 0.015)  # 右边距约 1.5%
        margin_y = int(h * 0.025)  # 底边距约 2.5%
        radius = int(6 * font_ratio)  # 圆角按比例
        padding_v = int(8 * font_ratio)  # 垂直内边距
        padding_h = int(35 * font_ratio)  # 水平内边距
        font_size = int(20 * font_ratio)  # 字体大小按比例

        # 创建提示窗
        popup = QtWidgets.QWidget(
            flags=QtCore.Qt.Tool |
                  QtCore.Qt.FramelessWindowHint |
                  QtCore.Qt.WindowStaysOnTopHint
        )
        popup.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        popup.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(50, 50, 50, 200);
                color: white;
                border-radius: {radius}px;
                padding: {padding_v}px {padding_h}px;
                font-size: {font_size}px;
            }}
        """)

        label = QtWidgets.QLabel(text, popup)
        label.setWordWrap(True)
        layout = QtWidgets.QVBoxLayout(popup)
        layout.addWidget(label)
        popup.adjustSize()

        # 定位到屏幕右下角
        popup.move(rect.right() - popup.width() - margin_x,
                   rect.bottom() - popup.height() - margin_y)
        popup.show()

        # 保存引用以防重复创建
        self._msg_popup = popup

        # 动画控制：0.5秒后开始淡出，持续1秒
        fade_anim = QtCore.QPropertyAnimation(popup, b"windowOpacity")
        fade_anim.setDuration(1000)
        fade_anim.setStartValue(1.0)
        fade_anim.setEndValue(0.0)

        def start_fade():
            fade_anim.start()

        def close_popup():
            popup.close()
            self._msg_popup = None

        QtCore.QTimer.singleShot(500, start_fade)  # 0.5 秒后开始淡出
        QtCore.QTimer.singleShot(1500, close_popup)  # 1.5 秒后关闭

    @QtCore.pyqtSlot(int)
    def _create_overlay_for_hwnd(self, hwnd):
        if not is_window(hwnd):
            return
        # recreate if exists
        if hwnd in self.controller.overlay_windows:
            try:
                self.controller.overlay_windows[hwnd].close()
            except:
                pass
        ov = OverlayWindow(hwnd, self.controller)
        ov.show()
        self.controller.overlay_windows[hwnd] = ov
        # map overlay window id -> target hwnd (so we can detect focus being on overlay)
        try:
            wid = int(ov.winId())
            self.controller.overlay_winid_map[wid] = hwnd
        except Exception:
            pass

    def on_tray_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()


# ---------------------------
# Group Manager Dialog (drag-drop)
# ---------------------------

class DragList(QtWidgets.QListWidget):
    """A list that can drag items (including encoding hwnd in mime)"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setDragEnabled(True)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)

    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items:
            return
        mime = QtCore.QMimeData()
        # collect lines with "title (hwnd)" and embed hwnds in custom format
        text_lines = []
        data_lines = []
        for it in items:
            hwnd = it.data(QtCore.Qt.UserRole)
            text_lines.append(it.text())
            data_lines.append(str(hwnd))
        mime.setText('\n'.join(text_lines))
        mime.setData('application/x-wm-hwnds', '\n'.join(data_lines).encode('utf-8'))
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.exec_(QtCore.Qt.MoveAction)


class DropList(DragList):
    """Accept drops from DragList and internal moves"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        # allow internal moves too
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDrop)

    def dragEnterEvent(self, e):
        e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        # try to read our custom data
        md = e.mimeData()
        added = []
        if md.hasFormat('application/x-wm-hwnds'):
            b = md.data('application/x-wm-hwnds')
            try:
                s = bytes(b).decode('utf-8')
            except:
                s = str(b)
            for line in s.splitlines():
                try:
                    h = int(line.strip())
                except:
                    continue
                if is_window(h):
                    title = hwnd_to_title(h)
                    it = QtWidgets.QListWidgetItem(f"{title} ({h})")
                    it.setData(QtCore.Qt.UserRole, h)
                    self.addItem(it)
                    added.append(h)
        else:
            # fallback to text parse
            txt = md.text()
            for line in txt.splitlines():
                if '(' in line and line.strip().endswith(')'):
                    try:
                        h = int(line.split('(')[-1][:-1])
                        if is_window(h):
                            title = hwnd_to_title(h)
                            it = QtWidgets.QListWidgetItem(f"{title} ({h})")
                            it.setData(QtCore.Qt.UserRole, h)
                            self.addItem(it)
                            added.append(h)
                    except:
                        pass
        e.acceptProposedAction()


class DeleteZone(DropList):
    """Special drop zone for removing items from groups"""

    def __init__(self, parent_group_manager, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent_group_manager = parent_group_manager
        self.setStyleSheet("background-color: #661111; border: 2px dashed #ff0000;")
        self.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
        self.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.setAlternatingRowColors(False)
        # Add placeholder text
        placeholder = QtWidgets.QListWidgetItem("拖到此处删除分组中的窗口")
        placeholder.setFlags(QtCore.Qt.ItemIsEnabled)  # Make non-selectable
        self.addItem(placeholder)

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasFormat('application/x-wm-hwnds'):
            b = md.data('application/x-wm-hwnds')
            try:
                s = bytes(b).decode('utf-8')
            except:
                s = str(b)

            # Get source group from parent
            source_group = self.parent_group_manager.get_source_group()
            if source_group is None:
                e.ignore()
                return

            # Process each dropped window
            for line in s.splitlines():
                try:
                    hwnd = int(line.strip())
                except:
                    continue

                # Remove from source group
                self.parent_group_manager.model.remove_from_group(source_group, hwnd)

                # Remove from UI
                group_list = self.parent_group_manager.group_lists.get(source_group)
                if group_list:
                    for i in range(group_list.count()):
                        item = group_list.item(i)
                        if item.data(QtCore.Qt.UserRole) == hwnd:
                            group_list.takeItem(i)
                            break

        e.acceptProposedAction()


class GroupManager(QtWidgets.QDialog):
    def __init__(self, model, controller, select_hwnd=0):
        super().__init__()
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.model = model
        self.controller = controller
        self.select_hwnd = select_hwnd
        self.dragging_source_group = None  # Track which group is being dragged from
        self.setWindowTitle("分组管理")
        self.resize(900, 600)
        layout = QtWidgets.QHBoxLayout(self)

        # left: all windows
        left_box = QtWidgets.QVBoxLayout()
        left_box.addWidget(QtWidgets.QLabel("全部窗口（拖动到右侧分组）"))
        self.all_list = DragList()
        self.all_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        left_box.addWidget(self.all_list)
        layout.addLayout(left_box, 1)

        # right: groups area
        right_box = QtWidgets.QVBoxLayout()
        header_layout = QtWidgets.QHBoxLayout()
        header_layout.addWidget(QtWidgets.QLabel("分组"))
        btn_edit_names = QtWidgets.QPushButton("编辑分组名")
        btn_edit_names.clicked.connect(self.edit_group_names)
        header_layout.addWidget(btn_edit_names)
        right_box.addLayout(header_layout)

        self.group_lists = {}
        group_grid = QtWidgets.QGridLayout()
        for i in [1, 2, 3, 4, 5, 6, 7, 8, 9, 0]:
            if i in [1, 2, 3, 4, 5, 6, 7, 8, 9]:
                j = i - 1
            else:
                j = 9
            # label that supports double-click rename
            lbl = QtWidgets.QLabel(self.model.group_names.get(i, f"组 {i}"))
            lbl.setObjectName(f"group_label_{i}")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setFrameStyle(QtWidgets.QFrame.Panel | QtWidgets.QFrame.Raised)
            lbl.mouseDoubleClickEvent = partial(self.rename_group_label, i, lbl)
            group_grid.addWidget(lbl, j // 5 * 2, j % 5)

            w = DropList()
            w.setObjectName(f"group_{i}")
            # Track drag start to identify source group
            w.startDrag = partial(self.on_group_drag_start, i, w.startDrag)

            group_grid.addWidget(w, j // 5 * 2 + 1, j % 5)
            self.group_lists[i] = w
        right_box.addLayout(group_grid)

        # Add delete zone
        delete_label = QtWidgets.QLabel("删除区")
        delete_label.setAlignment(QtCore.Qt.AlignCenter)
        self.delete_zone = DeleteZone(self)
        right_box.addWidget(delete_label)
        right_box.addWidget(self.delete_zone)

        btn_refresh = QtWidgets.QPushButton("刷新窗口列表")
        btn_refresh.clicked.connect(self.refresh_all_windows)
        btn_save = QtWidgets.QPushButton("保存分组")
        btn_save.clicked.connect(self.save_groups)
        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        right_box.addWidget(btn_refresh)
        right_box.addWidget(btn_save)
        right_box.addWidget(btn_close)
        layout.addLayout(right_box, 2)

        self.refresh_all_windows()
        self.load_groups()
        # if select_hwnd provided, select it
        if select_hwnd:
            self.select_left_hwnd(select_hwnd)


        # === 深色样式 ===
        self.apply_dark_theme()

    def apply_dark_theme(self):
        """应用深色主题样式"""
        dark_palette = QtGui.QPalette()

        # 主背景色与文字色
        dark_palette.setColor(QtGui.QPalette.Window, QtGui.QColor(45, 45, 48))
        dark_palette.setColor(QtGui.QPalette.WindowText, QtCore.Qt.white)
        dark_palette.setColor(QtGui.QPalette.Base, QtGui.QColor(30, 30, 30))
        dark_palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(45, 45, 48))
        dark_palette.setColor(QtGui.QPalette.ToolTipBase, QtCore.Qt.white)
        dark_palette.setColor(QtGui.QPalette.ToolTipText, QtCore.Qt.white)
        dark_palette.setColor(QtGui.QPalette.Text, QtCore.Qt.white)
        dark_palette.setColor(QtGui.QPalette.Button, QtGui.QColor(60, 60, 60))
        dark_palette.setColor(QtGui.QPalette.ButtonText, QtCore.Qt.white)
        dark_palette.setColor(QtGui.QPalette.BrightText, QtCore.Qt.red)
        dark_palette.setColor(QtGui.QPalette.Link, QtGui.QColor(42, 130, 218))

        # 禁用状态
        dark_palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, QtGui.QColor(128, 128, 128))
        dark_palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor(128, 128, 128))

        # 高亮选中
        dark_palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(100, 100, 150))
        dark_palette.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.white)

        self.setPalette(dark_palette)

        # 细化控件样式表
        self.setStyleSheet("""
            QDialog {
                background-color: #2D2D30;
                color: #FFFFFF;
                border-radius: 8px;
            }
            QLabel {
                color: #FFFFFF;
            }
            QPushButton {
                background-color: #3C3C3C;
                color: #FFFFFF;
                border: 1px solid #5A5A5A;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            QPushButton:pressed {
                background-color: #2A2A2A;
            }
            QLineEdit, QListWidget, QTextEdit {
                background-color: #1E1E1E;
                color: #FFFFFF;
                border: 1px solid #5A5A5A;
                border-radius: 4px;
            }
            QScrollBar:vertical, QScrollBar:horizontal {
                background: #2D2D30;
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle {
                background: #5A5A5A;
                border-radius: 4px;
            }
            QScrollBar::handle:hover {
                background: #707070;
            }
        """)


    def on_group_drag_start(self, group_id, original_start_drag, supported_actions):
        """Track which group is the source of the drag"""
        self.dragging_source_group = group_id
        original_start_drag(supported_actions)

    def get_source_group(self):
        """Get the group that is the source of the current drag operation"""
        return self.dragging_source_group

    def refresh_all_windows(self):
        self.all_list.clear()
        for hwnd, title in enum_windows():
            item = QtWidgets.QListWidgetItem(f"{title} ({hwnd})")
            item.setData(QtCore.Qt.UserRole, hwnd)
            self.all_list.addItem(item)

    def load_groups(self):
        for i, w in self.group_lists.items():
            w.clear()
            for hwnd in self.model.groups.get(i, []):
                if is_window(hwnd):
                    title = hwnd_to_title(hwnd)
                    it = QtWidgets.QListWidgetItem(f"{title} ({hwnd})")
                    it.setData(QtCore.Qt.UserRole, hwnd)
                    w.addItem(it)
            # update label text in case name changed
            lbl = self.findChild(QtWidgets.QLabel, f"group_label_{i}")
            if lbl:
                lbl.setText(self.model.group_names.get(i, f"组 {i}"))

    def save_groups(self):
        for i, w in self.group_lists.items():
            hwnds = []
            for idx in range(w.count()):
                item = w.item(idx)
                hwnd = item.data(QtCore.Qt.UserRole)
                if is_window(hwnd):
                    hwnds.append(hwnd)
            self.model.set_group(i, hwnds)
        QtWidgets.QMessageBox.information(self, "保存", "已保存分组到配置文件")
        self.accept()

    def select_left_hwnd(self, hwnd):
        for i in range(self.all_list.count()):
            it = self.all_list.item(i)
            if it.data(QtCore.Qt.UserRole) == hwnd:
                self.all_list.setCurrentRow(i)
                self.all_list.scrollToItem(it)
                return

    def rename_group_label(self, group_id, label_widget, ev):
        # show rename dialog
        text, ok = QtWidgets.QInputDialog.getText(self, "重命名分组", "请输入分组名称：",
                                                  text=self.model.group_names.get(group_id, f"组 {group_id}"))
        if ok and text.strip():
            self.model.set_group_name(group_id, text.strip())
            label_widget.setText(text.strip())

    def edit_group_names(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("编辑分组名")
        layout = QtWidgets.QFormLayout(dlg)
        edits = {}
        for i in range(10):
            e = QtWidgets.QLineEdit(self.model.group_names.get(i, f"组 {i}"))
            edits[i] = e
            layout.addRow(f"组 {i}:", e)
        btn = QtWidgets.QPushButton("保存")
        btn.clicked.connect(lambda: (self._save_group_names(edits), dlg.accept()))
        layout.addRow(btn)
        dlg.exec_()
        self.load_groups()

    def _save_group_names(self, edits):
        for i, e in edits.items():
            val = e.text().strip()
            if val:
                self.model.set_group_name(i, val)




# ---------------------------
# Hotkey Config Dialog
# ---------------------------

class HotkeyConfigDialog(QtWidgets.QDialog):
    def __init__(self, model):
        super().__init__()
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.model = model
        self.setWindowTitle("快捷键设置")
        self.resize(400, 200)
        layout = QtWidgets.QFormLayout(self)
        self.inputs = {}
        # === 动作名称映射（显示为中文） ===
        action_labels = {
            'topmost': '置顶窗口',
            'show_only': '仅显示',
            'transparent': '半透明',
            'open_group_manager': '打开分组管理',
        }

        for action in ['topmost', 'show_only', 'transparent', 'open_group_manager']:
            label_text = action_labels.get(action, action)
            inp = QtWidgets.QLineEdit(self.model.hotkeys.get(action, ''))
            layout.addRow(label_text + "：", inp)
            self.inputs[action] = inp
        btn = QtWidgets.QPushButton("保存")
        btn.clicked.connect(self.save_and_close)
        layout.addRow(btn)

        # === 深色样式 ===（模仿 GroupManager）
        dark_palette = QtGui.QPalette()
        dark_palette.setColor(QtGui.QPalette.Window, QtGui.QColor(45, 45, 48))
        dark_palette.setColor(QtGui.QPalette.WindowText, QtCore.Qt.white)
        dark_palette.setColor(QtGui.QPalette.Base, QtGui.QColor(30, 30, 30))
        dark_palette.setColor(QtGui.QPalette.Text, QtCore.Qt.white)
        dark_palette.setColor(QtGui.QPalette.Button, QtGui.QColor(60, 60, 60))
        dark_palette.setColor(QtGui.QPalette.ButtonText, QtCore.Qt.white)
        dark_palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(100, 100, 150))
        dark_palette.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.white)
        self.setPalette(dark_palette)

        self.setStyleSheet("""
            QDialog {
                background-color: #2D2D30;
                color: #FFFFFF;
                border-radius: 8px;
            }
            QLabel {
                color: #FFFFFF;
            }
            QLineEdit {
                background-color: #1E1E1E;
                color: #FFFFFF;
                border: 1px solid #5A5A5A;
                border-radius: 4px;
                padding: 4px;
            }
            QPushButton {
                background-color: #3C3C3C;
                color: #FFFFFF;
                border: 1px solid #5A5A5A;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            QPushButton:pressed {
                background-color: #2A2A2A;
            }
        """)




    def save_and_close(self):
        for action, inp in self.inputs.items():
            val = inp.text().strip().lower()
            if not val:
                QtWidgets.QMessageBox.warning(self, "错误", f"{action} 不能为空")
                return
            self.model.hotkeys[action] = val
        self.model.save()
        self.accept()


# ---------------------------
# Main entry
# ---------------------------

def main():
    global app, app_window
    model = Model()
    controller = Controller(model)
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app_window = AppWindow(model, controller)
    # app_window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()