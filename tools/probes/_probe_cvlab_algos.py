# CV Lab algorithm facts: which classic detectors exist in this image binding, what
# their parameters actually do at 400x240, and how they behave on live camera frames.
# No LVGL objects are created, so no --lvgl is needed.

import gc
import time

import image
from media.sensor import Sensor, CAM_CHN_ID_1

W = 400
H = 240
WHITE = 0xFFFFFF
BLACK = 0

sensor = None


def gray_frame(bg=WHITE):
    img = image.Image(W, H, image.GRAYSCALE)
    img.draw_rectangle(0, 0, W, H, color=bg, fill=True)
    return img


def caps(img):
    names = ("find_line_segments", "find_lines", "find_rects", "find_circles",
             "find_corners", "find_edges", "find_blobs", "mean_pool", "binary")
    return " ".join("{}={}".format(n, hasattr(img, n)) for n in names)


def timed(name, fn):
    t0 = time.ticks_us()
    try:
        value = fn()
        cost = time.ticks_diff(time.ticks_us(), t0)
        print("ALG {} {} us -> {}".format(name, cost, value))
        return value
    except BaseException as error:
        cost = time.ticks_diff(time.ticks_us(), t0)
        print("ALG {} {} us ERROR {}".format(name, cost, error))
        return None


def synthetic():
    img = gray_frame()
    print("CAP", caps(img))

    blobs = img.copy()
    blobs.draw_rectangle(30, 30, 120, 80, color=BLACK, fill=True)
    timed("blobs-dark", lambda: [list(b.rect()) for b in blobs.find_blobs(
        [(0, 80)], pixels_threshold=150, area_threshold=150, merge=True,
        margin=10)])

    lines = gray_frame()
    lines.draw_line(20, 120, 380, 120, color=BLACK, thickness=4)
    timed("lines-1000", lambda: [list(l.line()) for l in lines.find_lines(
        threshold=1000, theta_margin=25, rho_margin=25)])
    timed("lines-300", lambda: [list(l.line()) for l in lines.find_lines(
        threshold=300, theta_margin=25, rho_margin=25)])
    if hasattr(lines, "find_line_segments"):
        timed("segments", lambda: [
            (s.x1(), s.y1(), s.x2(), s.y2(), s.theta(), s.magnitude())
            for s in lines.find_line_segments(merge_distance=10,
                                              max_theta_diff=15)][:6])

    rects = gray_frame()
    rects.draw_rectangle(80, 40, 240, 160, color=BLACK, fill=True)
    timed("rects", lambda: [(list(r.rect()), [list(c) for c in r.corners()])
                            for r in rects.find_rects(threshold=10000)])

    corners = gray_frame()
    corners.draw_rectangle(80, 40, 240, 160, color=BLACK, fill=True)
    if hasattr(corners, "find_corners"):
        timed("corners", lambda: [list(c) for c in corners.find_corners(
            threshold=90)][:8])

    circ = gray_frame()
    circ.draw_circle(200, 120, 80, color=BLACK, thickness=4)
    for threshold in (2000, 1000, 700):
        timed("circles-{}".format(threshold), lambda t=threshold: [
            (c.x(), c.y(), c.r()) for c in circ.find_circles(
                threshold=t, x_margin=10, y_margin=10, r_margin=10,
                r_min=8, r_max=120, r_step=2)][:4])

    edges = gray_frame()
    edges.draw_rectangle(80, 40, 240, 160, color=BLACK, fill=True)
    try:
        edges.find_edges(image.EDGE_CANNY, threshold=(50, 80))
        print("ALG canny pixel(80,40)={} pixel(10,10)={}".format(
            edges.get_pixel(80, 40), edges.get_pixel(10, 10)))
    except BaseException as error:
        print("ALG canny ERROR", error)


def live():
    global sensor
    sensor = Sensor(fps=30)
    sensor.reset()
    sensor.set_framesize(w=W, h=H, chn=CAM_CHN_ID_1)
    sensor.set_pixformat(Sensor.GRAYSCALE, chn=CAM_CHN_ID_1)
    sensor.run()
    print("ALG sensor ready")

    for _ in range(3):
        frame = sensor.snapshot(chn=CAM_CHN_ID_1)
    print("ALG live frame {}x{}".format(frame.width(), frame.height()))

    timed("live-blobs", lambda: len(frame.find_blobs(
        [(0, 80)], pixels_threshold=150, area_threshold=150, merge=True,
        margin=10)))
    timed("live-blobs-bright", lambda: len(frame.find_blobs(
        [(175, 255)], pixels_threshold=150, area_threshold=150, merge=True,
        margin=10)))
    timed("live-lines-300", lambda: len(frame.find_lines(
        threshold=300, theta_margin=25, rho_margin=25)))
    timed("live-lines-100", lambda: len(frame.find_lines(
        threshold=100, theta_margin=25, rho_margin=25)))
    if hasattr(frame, "find_line_segments"):
        timed("live-segments", lambda: len(frame.find_line_segments(
            merge_distance=10, max_theta_diff=15)))
    timed("live-rects", lambda: len(frame.find_rects(threshold=10000)))
    timed("live-circles-2000", lambda: len(frame.find_circles(
        threshold=2000, x_margin=10, y_margin=10, r_margin=10, r_min=8,
        r_max=120, r_step=2)))
    timed("live-circles-1000", lambda: len(frame.find_circles(
        threshold=1000, x_margin=10, y_margin=10, r_margin=10, r_min=8,
        r_max=120, r_step=2)))
    print("ALG mem_free={}".format(gc.mem_free()))


def main():
    synthetic()
    try:
        live()
    finally:
        if sensor is not None:
            try:
                sensor.stop()
            except BaseException as error:
                print("ALG sensor.stop error:", error)
            try:
                sensor.deinit()
            except BaseException as error:
                print("ALG sensor.deinit error:", error)
    print("PROBE done")


main()
