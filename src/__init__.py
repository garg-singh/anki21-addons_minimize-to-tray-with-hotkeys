import sys
from types import MethodType

from aqt.qt import (
    sip, Qt, QIcon, QPixmap, QApplication, QMenu, QSystemTrayIcon,
    QAbstractNativeEventFilter, QEvent, QObject, QTimer
)

from aqt import gui_hooks, mw  # mw is the INSTANCE of the main window
from aqt.main import AnkiQt


# Native event filter for handling global hotkeys
class HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, system_tray, hotkey_id):
        super().__init__()
        self.system_tray = system_tray
        self.hotkey_id = hotkey_id
    
    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG" or eventType == "windows_generic_MSG":
            try:
                import ctypes
                msg = ctypes.wintypes.MSG.from_address(int(message))
                
                if msg.message == 0x0312:  # WM_HOTKEY
                    if msg.wParam == self.hotkey_id:
                        self.system_tray._debug_print(f"Hotkey pressed!")
                        if self.system_tray.isMinimizedToTray:
                            self.system_tray._debug_print("Showing all windows via hotkey")
                            self.system_tray.showAll()
                        else:
                            self.system_tray._debug_print("Hiding all windows via hotkey")
                            self.system_tray.hideAll()
                        return True, 0
            except Exception as e:
                self.system_tray._debug_print(f"Error in nativeEventFilter: {e}")
        
        return False, 0


# Event filter for minimize-to-tray functionality
class MinimizeToTrayFilter(QObject):
    def __init__(self, system_tray, parent=None):
        super().__init__(parent)
        self.system_tray = system_tray

    def eventFilter(self, obj, event):
        # Detect when the main window becomes minimized
        if obj is self.system_tray.mw and event.type() == QEvent.Type.WindowStateChange:
            if obj.isMinimized():
                self.system_tray._debug_print("Minimize button pressed, scheduling hideAll")
                # Delay hide to next Qt tick to avoid minimize glitches
                QTimer.singleShot(0, self.system_tray.hideAll)
        return False


class AnkiSystemTray:
    # Windows modifier constants
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008
    
    # Virtual key code mapping for common keys
    VK_CODES = {
        'A': 0x41, 'B': 0x42, 'C': 0x43, 'D': 0x44, 'E': 0x45, 'F': 0x46,
        'G': 0x47, 'H': 0x48, 'I': 0x49, 'J': 0x4A, 'K': 0x4B, 'L': 0x4C,
        'M': 0x4D, 'N': 0x4E, 'O': 0x4F, 'P': 0x50, 'Q': 0x51, 'R': 0x52,
        'S': 0x53, 'T': 0x54, 'U': 0x55, 'V': 0x56, 'W': 0x57, 'X': 0x58,
        'Y': 0x59, 'Z': 0x5A,
        '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
        '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
        'F1': 0x70, 'F2': 0x71, 'F3': 0x72, 'F4': 0x73, 'F5': 0x74,
        'F6': 0x75, 'F7': 0x76, 'F8': 0x77, 'F9': 0x78, 'F10': 0x79,
        'F11': 0x7A, 'F12': 0x7B,
        'SPACE': 0x20, 'ENTER': 0x0D, 'ESC': 0x1B, 'TAB': 0x09,
        'BACKSPACE': 0x08, 'DELETE': 0x2E, 'INSERT': 0x2D,
        'HOME': 0x24, 'END': 0x23, 'PAGEUP': 0x21, 'PAGEDOWN': 0x22,
        'LEFT': 0x25, 'UP': 0x26, 'RIGHT': 0x27, 'DOWN': 0x28,
    }

    def _debug_print(self, message):
        """Helper function to print debug messages when debug mode is enabled."""
        if self.debug:
            print(f"[MinimizeToTray] DEBUG: {message}")

    def _parse_hotkey(self, hotkey_string):
        """Parse hotkey string like 'Alt+N' or 'Ctrl+Shift+F1' into modifier and vk code.
        
        Returns: (modifiers, vk_code) tuple or (None, None) if invalid
        """
        if not hotkey_string:
            return None, None
        
        parts = [p.strip().upper() for p in hotkey_string.split('+')]
        if not parts:
            return None, None
        
        modifiers = 0
        key = parts[-1]  # Last part is the key
        
        # Parse modifiers
        for part in parts[:-1]:
            if part in ('ALT', 'OPTION'):
                modifiers |= self.MOD_ALT
            elif part in ('CTRL', 'CONTROL'):
                modifiers |= self.MOD_CONTROL
            elif part == 'SHIFT':
                modifiers |= self.MOD_SHIFT
            elif part in ('WIN', 'SUPER', 'CMD', 'COMMAND'):
                modifiers |= self.MOD_WIN
            else:
                self._debug_print(f"Unknown modifier: {part}")
                return None, None
        
        # Get virtual key code
        vk_code = self.VK_CODES.get(key)
        if vk_code is None:
            self._debug_print(f"Unknown key: {key}")
            return None, None
        
        return modifiers, vk_code

    def __init__(self, mw):
        """Create a system tray with the Anki icon."""
        self.mw = mw
        config = self.mw.addonManager.getConfig(__name__)
        self.debug = config.get("debug", False)

        self._debug_print("Initializing AnkiSystemTray")

        self.isAnkiFocused = True
        self.isMinimizedToTray = False
        self.lastFocusedWidget = mw
        self.explicitlyHiddenWindows = []
        self.windowVisibilitySnapshot = {}
        self.event_filter = None  # Store reference to prevent garbage collection
        self.minimize_filter = None  # Store reference for minimize event filter

        self._debug_print("Creating tray icon")
        self.trayIcon = self._createTrayIcon()

        QApplication.setQuitOnLastWindowClosed(False)
        self._configureMw()
        self.trayIcon.show()

        self._debug_print(
            f"Configuration: hide_on_startup={config.get('hide_on_startup', False)}, debug={self.debug}"
        )

        if config.get("hide_on_startup", False):
            self._debug_print("Hiding all windows on startup")
            self.hideAll()

    def onActivated(self, reason):
        """Show/hide all Anki windows when the tray icon is clicked.

        The windows are shown if:
        - anki window is not in focus
        - any window is minimized
        - anki is minimize to tray
        The windows are hidden otherwise.

        The focus cannot be detected given that the main window focus is lost before this
        slot is activated. For this reason and to prevent that anki is minimized when not
        focused, on Windows are the windows are never hidden.
        """
        self._debug_print(f"Tray icon activated with reason: {reason}")

        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            should_show = (
                not self.isAnkiFocused
                or self._anyWindowMinimized()
                or self.isMinimizedToTray
            )

            self._debug_print(
                f"Decision factors: isAnkiFocused={self.isAnkiFocused}, anyWindowMinimized={self._anyWindowMinimized()}, isMinimizedToTray={self.isMinimizedToTray}"
            )
            self._debug_print(f"Should show: {should_show}")

            if should_show:
                self.showAll()
            elif not sys.platform.startswith("win32"):
                self._debug_print("Platform is not Windows, hiding all windows")
                self.hideAll()

    def onFocusChanged(self, old, now):
        """Keep track of the focused window in order to refocus it on showAll."""
        self.isAnkiFocused = now is not None
        self._debug_print(
            f"Focus changed: old={old}, now={now}, isAnkiFocused={self.isAnkiFocused}"
        )
        if self.isAnkiFocused:
            self.lastFocusedWidget = now
            self._debug_print(f"Updated lastFocusedWidget to: {now}")

    def onExit(self):
        self._debug_print("Exit action triggered")
        self.mw.close()

    def showAll(self):
        """Show all windows."""
        self._debug_print(
            f"Showing all windows. isMinimizedToTray={self.isMinimizedToTray}"
        )

        if self.isMinimizedToTray:
            windows_to_show = [
                w for w in self.explicitlyHiddenWindows if not sip.isdeleted(w)
            ]
            self._debug_print(
                f"Showing explicitly hidden windows: {len(windows_to_show)} windows"
            )
            self._showWindows(windows_to_show)
            self._restoreWindowStates()
        else:
            visible_windows = self._visibleWindows()
            self._debug_print(
                f"Showing visible windows: {len(visible_windows)} windows"
            )
            self._showWindows(visible_windows)

        if not sip.isdeleted(self.lastFocusedWidget):
            self._debug_print(
                f"Raising and activating last focused widget: {self.lastFocusedWidget}"
            )
            self.lastFocusedWidget.raise_()
            self.lastFocusedWidget.activateWindow()
        else:
            self._debug_print("Last focused widget has been deleted")

        self.isMinimizedToTray = False

    def hideAll(self):
        """Hide all windows."""
        # Prevent re-snapshotting if already hidden
        if self.isMinimizedToTray:
            self._debug_print("Already minimized to tray, skipping hideAll")
            return

        self._snapshotWindowStates()
        self.explicitlyHiddenWindows = self._visibleWindows()
        self._debug_print(
            f"Hiding all windows: {len(self.explicitlyHiddenWindows)} windows to hide"
        )

        for w in self.explicitlyHiddenWindows:
            self._debug_print(f"Hiding window: {w}")
            w.hide()

        self.isMinimizedToTray = True
        self._debug_print("All windows hidden, set isMinimizedToTray=True")

    def _showWindows(self, windows):
        self._debug_print(f"_showWindows called with {len(windows)} windows")

        for w in windows:
            if sip.isdeleted(w):
                self._debug_print(f"Skipping deleted window: {w}")
                continue

            if w.windowState() == Qt.WindowState.WindowMinimized:
                self._debug_print(f"Restoring minimized window: {w}")
                w.showNormal()
            else:
                self._debug_print(f"Showing window with hide/show hack: {w}")
                w.hide()
                w.show()
            w.raise_()

    def _visibleWindows(self):
        """Return the windows actually visible Anki windows.

        Anki has some hidden windows and menus that we should ignore.
        """
        windows = []
        all_widgets = QApplication.topLevelWidgets()

        self._debug_print(
            f"Checking {len(all_widgets)} top level widgets for visible windows"
        )

        for w in all_widgets:
            if w.isWindow() and not w.isHidden():
                if not w.children():
                    self._debug_print(f"Skipping window with no children: {w}")
                    continue
                windows.append(w)
                self._debug_print(f"Found visible window: {w}")
            else:
                self._debug_print(
                    f"Skipping non-window or hidden widget: {w} (isWindow={w.isWindow()}, isHidden={w.isHidden()})"
                )

        self._debug_print(f"Found {len(windows)} visible windows")
        return windows

    def _anyWindowMinimized(self):
        visible_windows = self._visibleWindows()
        minimized_windows = [
            w
            for w in visible_windows
            if w.windowState() == Qt.WindowState.WindowMinimized
        ]

        self._debug_print(
            f"Checking for minimized windows: {len(minimized_windows)} out of {len(visible_windows)} are minimized"
        )

        return len(minimized_windows) > 0

    def _snapshotWindowStates(self):
        """Capture the visibility state of all top-level windows."""
        self.windowVisibilitySnapshot = {}
        all_widgets = QApplication.topLevelWidgets()

        for w in all_widgets:
            if w.isWindow() and w.children():
                self.windowVisibilitySnapshot[w] = not w.isHidden()
                self._debug_print(
                    f"Snapshotting window {w}: visible={self.windowVisibilitySnapshot[w]}"
                )

    def _restoreWindowStates(self):
        """Restore window visibility to match the pre-minimize snapshot."""
        self._debug_print("Restoring window visibility from snapshot")

        for w, was_visible in self.windowVisibilitySnapshot.items():
            if sip.isdeleted(w):
                continue

            is_currently_visible = not w.isHidden()
            if is_currently_visible and not was_visible:
                self._debug_print(
                    f"Hiding window that should not be visible: {w} (was {was_visible}, now {is_currently_visible})"
                )
                w.hide()
            elif not is_currently_visible and was_visible:
                self._debug_print(
                    f"Showing window that should be visible: {w} (was {was_visible}, now {is_currently_visible})"
                )
                w.show()

    def _createTrayIcon(self):
        self._debug_print("Creating system tray icon")

        trayIcon = QSystemTrayIcon(self.mw)
        ankiLogo = QIcon()
        ankiLogo.addPixmap(
            QPixmap("icons:anki.png"), QIcon.Mode.Normal, QIcon.State.Off
        )
        trayIcon.setIcon(QIcon.fromTheme("anki", ankiLogo))

        self._debug_print("Setting up tray menu")
        trayMenu = QMenu(self.mw)
        trayIcon.setContextMenu(trayMenu)
        showAction = trayMenu.addAction("Show all windows")
        showAction.triggered.connect(self.showAll)
        trayMenu.addAction(self.mw.form.actionExit)
        trayIcon.activated.connect(self.onActivated)

        self._debug_print("Tray icon created successfully")
        return trayIcon

    def _configureMw(self):
        self._debug_print("Configuring main window")

        self.mw.app.focusChanged.connect(self.onFocusChanged)

        self._debug_print("Disconnecting and reconnecting exit action")
        self.mw.form.actionExit.triggered.disconnect(self.mw.close)
        self.mw.form.actionExit.triggered.connect(self.onExit)

        # Install event filter for minimize-to-tray (not close event)
        self._debug_print("Installing minimize-to-tray event filter")
        self.minimize_filter = MinimizeToTrayFilter(self, self.mw)
        self.mw.installEventFilter(self.minimize_filter)
        
        # Initialize hotkeys on Windows
        self._debug_print("Initializing hotkeys")
        self._initHotkeys()
        
        # Register cleanup on add-on unload
        gui_hooks.profile_will_close.append(self._cleanup_hotkeys)

    def _cleanup_hotkeys(self):
        """Cleanup hotkeys when profile closes or add-on unloads."""
        if sys.platform == "win32" and hasattr(self.mw, 'hotkey_id'):
            try:
                import ctypes
                ctypes.windll.user32.UnregisterHotKey(int(self.mw.winId()), self.mw.hotkey_id)
                self._debug_print("Cleaned up hotkey on profile close")
            except Exception as e:
                self._debug_print(f"Error cleaning up hotkey: {e}")

    def _initHotkeys(self):
        """Initialize global hotkeys for Windows."""
        if sys.platform == "win32":
            try:
                import ctypes
                
                # Get hotkey from config
                config = self.mw.addonManager.getConfig(__name__)
                hotkey_string = config.get("global_hotkey", "Alt+N")
                
                self._debug_print(f"Parsing hotkey: {hotkey_string}")
                modifiers, vk_code = self._parse_hotkey(hotkey_string)
                
                if modifiers is None or vk_code is None:
                    self._debug_print(f"Invalid hotkey configuration: {hotkey_string}")
                    self._debug_print("Hotkey format examples: 'Alt+N', 'Ctrl+Shift+F1', 'Win+Space'")
                    return
                
                self.mw.hotkey_id = 1
                success = ctypes.windll.user32.RegisterHotKey(
                    int(self.mw.winId()), 
                    self.mw.hotkey_id, 
                    modifiers,
                    vk_code
                )
                
                if success:
                    self._debug_print(f"Native hotkey '{hotkey_string}' registered successfully (Windows Native)")
                    
                    # Install event filter to handle hotkey messages
                    self.event_filter = HotkeyEventFilter(self, self.mw.hotkey_id)
                    QApplication.instance().installNativeEventFilter(self.event_filter)
                    self._debug_print("Event filter installed")
                else:
                    error_code = ctypes.windll.kernel32.GetLastError()
                    self._debug_print(f"Failed to register native hotkey '{hotkey_string}' (Error code: {error_code})")
                    self._debug_print("The hotkey might already be in use by another application")
            except Exception as e:
                self._debug_print(f"Warning: Native hotkey error: {e}")
        else:
            self._debug_print("Not Windows, skipping native hotkey registration")


def minimizeToTrayInit():
    if hasattr(mw, "systemTray"):
        return

    config = mw.addonManager.getConfig(__name__)
    debug = config.get("debug", False)

    if debug:
        print("[MinimizeToTray] DEBUG: Initializing Minimize to Tray addon")

    mw.systemTray = AnkiSystemTray(mw)


gui_hooks.main_window_did_init.append(minimizeToTrayInit)
