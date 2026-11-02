# Decides the channel order of to_numpy_ref() on an RGB888 image.
#
# Frame layout (produced by tools/_prep_plate_frame.py):
#   background (32, 38, 48) everywhere outside the plate
#   blue plate body (20, 60, 180) filling rect (20, 95) - (780, 385)
#   white border and glyphs (245, 245, 245)
#
# A blue-dominant plate pixel tells the order directly: if the third component is
# the large one the buffer is RGB, if the first is large it is BGR.

import image

img = image.Image("/data/plate/real.jpg").to_rgb888()
ref = img.to_numpy_ref()
print("dims {}x{}x{}".format(len(ref), len(ref[0]), len(ref[0][0])))

# Blue plate interior, away from glyph strokes and the white border.
POINTS = [
    (40, 130), (40, 240), (40, 350),
    (760, 130), (760, 240), (760, 350),
    (400, 110), (400, 370),
    (400, 20), (60, 20),
]
for x, y in POINTS:
    px = ref[y][x]
    print("({:3d},{:3d}) -> c0={:3d} c1={:3d} c2={:3d}".format(
        x, y, int(px[0]), int(px[1]), int(px[2])))

print("")
print("blue plate source = (20, 60, 180); background source = (32, 38, 48)")
print("RESULT: DONE")
