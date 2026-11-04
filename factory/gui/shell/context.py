# Context handed to every app: content-area size, exit request, and the hooks an
# app uses to talk back to the launcher without importing it.

from hardware import panel
from shell.theme import STATUS_H


class AppContext:
    """Everything an app may touch in the shell.

    A read-and-clear flag is used rather than a callback, so an app that has not
    implemented the interface cannot crash the launcher.
    """

    def __init__(self):
        self.width = panel.WIDTH
        self.height = panel.HEIGHT - STATUS_H
        self.screen_w = panel.WIDTH
        self.screen_h = panel.HEIGHT
        # Absolute offset of the content area on screen (apps use it to map screen
        # touch coordinates into their local space)
        self.content_x = 0
        self.content_y = STATUS_H
        self.app_name = ""
        self.want_exit = False
        self.touch = None  # TouchScreen instance; apps can call touch.read_raw()
        # Physical OK key (GPIO53) press edge: set by HardwareButtons, consumed by the
        # app via take_ok().
        self.ok_pressed = False
        # Whether the OK key also plays a click sound (read by HardwareButtons).
        # Default True; apps with their own audio (Camera's shutter, for one) set it
        # to False while constructing so the two sounds never overlap.
        self.ok_beep = True
        # CANCEL key interception: lets an app walk back one layer at a time (gallery
        # full screen -> grid -> home screen, for instance).
        # Returning True means the key was consumed (stay in the app); None or False
        # keeps the default behaviour of exiting.
        self.back_handler = None
        # Hardware failing while the app runs (camera unplugged mid-session, say):
        # the app calls report_unavailable(), and the main loop releases the app,
        # hides its UI and shows the same notice panel as a failed start.
        self.unavailable_msg = None

    def request_exit(self):
        self.want_exit = True

    def report_unavailable(self, message):
        """An app reports at runtime that a hardware dependency is unusable (frames
        keep failing, for example).

        The app neither shows a panel nor exits itself: the launcher handles it
        centrally (release handles + notice + Cancel back to the home screen),
        keeping the experience identical to a failed construction. Repeated calls
        keep only the first message.
        """
        if self.unavailable_msg is None:
            self.unavailable_msg = message

    def set_back_handler(self, handler):
        """An app registers its CANCEL handler: returning True consumes the key and
        does not exit the app."""
        self.back_handler = handler

    def take_ok(self):
        """For apps: whether the OK key was pressed in this cycle; one read clears it."""
        if self.ok_pressed:
            self.ok_pressed = False
            return True
        return False
