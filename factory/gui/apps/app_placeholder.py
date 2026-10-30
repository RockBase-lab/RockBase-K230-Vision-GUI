# Placeholder app: lights up the paging indicator during bring-up. The second
# page only appears once the icon grid exceeds eight entries.
# Once a real app is ready, drop its entry from the APPS table in gui.py.

import lvgl as lv


class PlaceholderApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        label = lv.label(content)
        label.set_text("{}\n\n(placeholder app)\nexit: Home in status bar, or CANCEL key".format(
            getattr(ctx, "app_name", "Placeholder")))
        label.set_style_text_color(lv.color_hex(0x9CA3AF), 0)
        label.align(lv.ALIGN.CENTER, 0, 0)

    def step(self):
        pass

    def destroy(self):
        pass


def create_app(content, ctx):
    return PlaceholderApp(content, ctx)
