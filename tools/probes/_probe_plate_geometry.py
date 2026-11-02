# Calibrates plate geometry with self-verifying measurements.
#
# Two things went wrong on the real photo and neither can be settled by reading
# the reference code, because the reference never prints coordinates:
#   1) the detected quad's y sits about 160 too low, which matches the letterbox
#      y_off already documented for the COCO detection family
#   2) the recognised text is wrong (a single unrelated character)
#
# A third-party ground truth is needed, so this probe finds the plate by colour
# first: the body is a saturated blue, so scanning for it gives the true rectangle
# in frame coordinates. The quad is then compared against that rectangle, and the
# crop is exported so the exact pixels handed to the recogniser can be inspected
# on the PC.
#
# No Sensor and no kpu: pure image work plus ocr_rec_preprocess, so the run cannot
# wedge the board.

import image
import ulab.numpy as np
import aidemo

PREVIEW_W = 800
PREVIEW_H = 480
CASES = [("real", "/data/plate/real.jpg"), ("small", "/data/plate/small.jpg")]


def load_rgb(path):
    return image.Image(path).to_rgb888()


def blue_bbox(ref, w, h):
    """Bounding box of saturated blue pixels: (x0, y0, x1, y1) or None.

    The plate body is near (20, 60, 180) with the blue channel far above the red
    one, so the test is blue > 110 and red < 90. Background and the white glyphs
    both fail it, which is what makes this a usable ground truth.
    """
    x0, y0, x1, y1 = w, h, -1, -1
    step = 2
    for y in range(0, h, step):
        row = ref[y]
        for x in range(0, w, step):
            px = row[x]
            r = int(px[0])
            g = int(px[1])
            b = int(px[2])
            if b > 110 and r < 90 and b - r > 60:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                if y > y1:
                    y1 = y
    if x1 < 0:
        return None
    return (x0, y0, x1, y1)


def save_jpeg(img, path):
    """Export through the instance encoder: save() rejects the RGB888 format."""
    data = img.to_jpeg()
    f = open(path, "wb")
    f.write(data)
    f.close()
    print("      wrote {} ({} bytes)".format(path, len(data)))


for tag, path in CASES:
    print("")
    print("===== {}".format(tag))
    img = load_rgb(path)
    w = img.width()
    h = img.height()
    ref = img.to_numpy_ref()

    box = blue_bbox(ref, w, h)
    print("  blue bbox = {}".format(box))

    # CHW, matching what ocr_rec_preprocess requires (measured).
    chw = np.zeros((3, h, w), dtype=np.uint8)
    chw[0] = ref[:, :, 0]
    chw[1] = ref[:, :, 1]
    chw[2] = ref[:, :, 2]

    # Feed the *known* rectangle so the crop content is not contaminated by a
    # detection error. Order: four corners as (x, y) pairs.
    if box is None:
        print("  no blue region, skipped")
        continue
    x0, y0, x1, y1 = box
    quad = np.array([float(x0), float(y0), float(x1), float(y0),
                     float(x1), float(y1), float(x0), float(y1)])
    try:
        res = aidemo.ocr_rec_preprocess(chw, [PREVIEW_H, PREVIEW_W], [quad])
        imgs = res[0]
        print("  crop count = {}".format(len(imgs)))
        if len(imgs) > 0:
            crop = imgs[0]
            print("  crop shape = {}x{}x{}x{}".format(
                len(crop), len(crop[0]), len(crop[0][0]), len(crop[0][0][0])))

            # Rebuild the crop as a GRAYSCALE / RGB image so the exact pixels can
            # be looked at on the PC. Two layouts are tried because the crop
            # reports a single channel.
            ch = len(crop[0])
            ch_h = len(crop[0][0])
            ch_w = len(crop[0][0][0])
            print("  crop channels={} h={} w={}".format(ch, ch_h, ch_w))
            try:
                plane = crop[0][0]
                out1 = image.Image(ch_w, ch_h, image.GRAYSCALE)
                buf = out1.bytearray()
                for yy in range(ch_h):
                    base = yy * ch_w
                    row = plane[yy]
                    for xx in range(ch_w):
                        buf[base + xx] = int(row[xx])
                save_jpeg(out1, "/data/plate/crop_" + tag + ".jpg")
                del out1, buf
            except BaseException as error:
                print("      crop export FAILED:", error)

            # Averages per channel, so a wrong plane count shows up numerically.
            try:
                for c in range(ch):
                    plane = crop[0][c]
                    total = 0
                    count = 0
                    for yy in range(0, ch_h, 8):
                        row = plane[yy]
                        for xx in range(0, ch_w, 8):
                            total += int(row[xx])
                            count += 1
                    print("      plane[{}] mean = {:.1f}".format(
                        c, float(total) / count if count else 0.0))
            except BaseException as error:
                print("      plane mean FAILED:", error)
    except BaseException as error:
        print("  crop FAILED:", error)

    # Draw the ground-truth rectangle so the frame can be compared visually.
    try:
        draw = load_rgb(path)
        draw.draw_line(x0, y0, x1, y0, color=(255, 0, 0), thickness=3)
        draw.draw_line(x1, y0, x1, y1, color=(255, 0, 0), thickness=3)
        draw.draw_line(x1, y1, x0, y1, color=(255, 0, 0), thickness=3)
        draw.draw_line(x0, y1, x0, y0, color=(255, 0, 0), thickness=3)
        save_jpeg(draw, "/data/plate/truth_" + tag + ".jpg")
        del draw
    except BaseException as error:
        print("  truth draw FAILED:", error)

    del chw, ref, img

print("")
print("RESULT: DONE")
