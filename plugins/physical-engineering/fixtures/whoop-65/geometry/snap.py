"""PETG snap canopy and fixed dock. Geometry and reference STEP coordinates in mm.

Shares the flight-assembly datum (the motor seat upper surface is Z=0).
"""

import math
import build123d as bd

POST = 26 / math.sqrt(2)
BASE = 5.30
SEAT = 8.10
CATCH_TOP = 9.45
SHOULDER = 9.70
BEAM_IN, BEAM_OUT = 14.70, 15.90
CATCH_IN, BARB_OUT = 16.25, 16.98
BAR_OUT = 17.65
ROOT_Y = -11.6
TIP_Y = 0.0
KEY_R, KEY_Y = 15.0, 3.60


def box(x, y, z, dx, dy, dz):
    return bd.Pos(x, y, z) * bd.Box(dx, dy, dz)


def slab(x0, x1, y0, y1, z0, z1):
    return box((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2, x1 - x0, y1 - y0, z1 - z0)


def circle(r, z0, z1, x, y):
    return bd.Solid.make_cylinder(r, z1 - z0, bd.Plane(origin=(x, y, z0)))


def rounded_panel(x, y, w, h, r, z0, z1):
    return bd.Pos(x, y, z0) * bd.extrude(bd.RectangleRounded(w, h, r), amount=z1 - z0)


def solid(shape, label, color):
    shape = shape.clean()
    if len(shape.solids()) != 1 or not shape.is_valid:
        raise RuntimeError(f"{label} must be one valid solid; got {len(shape.solids())}")
    result = bd.Solid(shape.solids()[0].wrapped)
    result.label = label
    result.color = color
    return result


def trace(a, b, width, z0, z1):
    """Round-ended flat strut between a pair of XY points."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    span = math.hypot(dx, dy)
    center = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (z0 + z1) / 2)
    web = (
        bd.Pos(*center) * bd.Rot(Z=math.degrees(math.atan2(dy, dx))) * bd.Box(span, width, z1 - z0)
    )
    return web.fuse(circle(width / 2, z0, z1, *a), circle(width / 2, z0, z1, *b))


def xz_prism(points, y_half):
    boundary = bd.Wire.make_polygon([(r, -y_half, z) for r, z in points], close=True)
    return bd.extrude(bd.Face(boundary), amount=2 * y_half, dir=(0, 1, 0))


def rail_outline():
    # One quarter from side screw to nose screw. Kept outside the flexing arm.
    return [
        (POST, 0),
        (18.40, 6.5),
        (17.9, 8.7),
        (16.55, 10.25),
        (13.4, 11.3),
        (9.3, 13.15),
        (4.9, 16.0),
        (0, POST),
    ]


def spring_half(keyed):
    # This root transition rounds the jaw in the XY plane. The active arm
    # is unrestrained from Y=-10.3 to the load point at Y=0.
    # Large inward deflection is unnecessary: release travel is 1.03 mm.
    arm = rounded_panel(15.30, -5.20, 1.20, 13.40, 0.48, BASE, 6.85)
    root = rounded_panel(15.30, ROOT_Y, 3.30, 2.75, 0.70, BASE, 6.85)
    transition = bd.Pos(0, 0, BASE) * bd.extrude(
        bd.Polygon((13.92, -11.5), (16.68, -11.5), (15.90, -9.80), (14.70, -9.80), align=None),
        amount=1.55,
    )
    stem = slab(BEAM_IN, BEAM_OUT, -1.28, 1.28, 6.25, 11.03)
    # Outward-facing lock. The shallow underside lead leaves a positive,
    # horizontal shoulder. The tall upper slope cams the jaw inward.
    hook = xz_prism(
        [
            (BEAM_OUT - 0.12, 9.36),
            (BEAM_OUT + 0.23, SHOULDER),
            (BARB_OUT, SHOULDER),
            (BARB_OUT, 9.94),
            (BEAM_OUT, 11.11),
            (BEAM_OUT - 0.12, 11.11),
        ],
        1.28,
    )
    button = xz_prism(
        [
            (BEAM_IN, 10.82),
            (BEAM_OUT, 10.82),
            (BEAM_OUT, 11.83),
            (BEAM_OUT - 0.22, 12.05),
            (BEAM_IN + 0.16, 12.05),
            (BEAM_IN, 11.89),
        ],
        1.28,
    )
    sections = [arm, root, transition, stem, hook, button]
    # Stops beneath the front and rear runner, independent of the moving arm.
    # The front ledge also carries the single anti-reversal guide pin.
    sections.append(rounded_panel(16.69, KEY_Y, 4.92, 2.55, 0.48, BASE, SEAT))
    sections.append(rounded_panel(17.77, -KEY_Y, 2.67, 2.55, 0.45, BASE, SEAT))
    if keyed:
        sections.append(rounded_panel(KEY_R, KEY_Y, 1.70, 1.70, 0.30, SEAT, 9.73))
        # Smaller chamfered guide tip enters before the latches engage.
        levels = [
            bd.Pos(KEY_R, KEY_Y, z) * bd.RectangleRounded(a, a, r)
            for z, a, r in [(9.73, 1.70, 0.30), (10.20, 1.10, 0.22)]
        ]
        sections.append(bd.loft(levels))
    return sections


def oriented(shape, sx):
    return shape if sx > 0 else shape.mirror(bd.Plane.YZ)


def clip_dock():
    pieces = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            quarter = rail_outline()
            if sx < 0 and sy < 0:
                # Keep the crossing over the power pair aft of its rise
                # to the PCB. All members remain on the print bed.
                quarter = quarter[:5] + [(10.4, 14.5), (9.3, 18.5), (4.9, 18.65), (0, POST)]
            points = [(sx * x, sy * y) for x, y in quarter]
            for a, b in zip(points, points[1:]):
                pieces.append(trace(a, b, 1.40, BASE, 6.50))
        pieces.extend(oriented(s, sx) for s in spring_half(sx < 0))
        pieces.append(oriented(trace((15.30, ROOT_Y), (13.45, -11.25), 2.10, BASE, 6.85), sx))
    bores = []
    for x, y in ((-POST, 0), (POST, 0), (0, POST), (0, -POST)):
        pieces.append(circle(2.22, BASE, 6.50, x, y))
        bores.extend([circle(0.86, BASE - 0.25, 6.75, x, y), circle(1.44, 6.30, 6.72, x, y)])
    dock = pieces[0].fuse(*pieces[1:]).cut(*bores)
    return solid(dock, "snap_dock", "#396A77")


def shell_catch(keyed):
    # Flat catch opens at its center to accept the dock hook.
    bars = [
        rounded_panel((CATCH_IN + BAR_OUT) / 2, 0, BAR_OUT - CATCH_IN, 9.55, 0.40, SEAT, CATCH_TOP)
    ]
    for y in (-KEY_Y, KEY_Y):
        bars.append(rounded_panel(14.0, y, 7.3, 3.65, 0.52, SEAT, CATCH_TOP))
    catch = bars[0].fuse(*bars[1:])
    if keyed:
        catch = catch.cut(
            rounded_panel(KEY_R, KEY_Y, 2.32, 2.32, 0.48, SEAT - 0.4, CATCH_TOP + 0.4)
        )
    return catch


def clip_canopy(shell):
    # Keep the optics port, camera channels, installation reliefs, vented
    # dome and original installed camera pose; discard the old screw ears.
    shell = shell.intersect(slab(-27, 27, -27, 27, SEAT, 32))[0]
    shell = shell.fuse(oriented(shell_catch(True), -1), shell_catch(False))
    return solid(shell, "snap_canopy", "#FF6327")


def coupons():
    # One exact keyed latch sector, including the XY flexure, interlock,
    # two seats, guide and guide pocket. Handles replace the rest of the dock
    # and shell. Original assembly datums simplify independent measurements.
    jaw = spring_half(True)
    fixed_rail = rounded_panel(18.61, -3.5, 1.56, 21.0, 0.55, BASE, 6.50)
    bottom = rounded_panel(17.25, -12.25, 6.1, 2.7, 0.8, BASE, 6.85)
    grip = rounded_panel(17.25, -17.8, 9.5, 10.75, 1.45, BASE, 6.85)
    # Copy the installed root restraint as well as its spring geometry.
    rail = [(x, -y) for x, y in rail_outline()[1:5]]
    root_rails = [trace(a, b, 1.40, BASE, 6.50) for a, b in zip(rail, rail[1:])]
    root_rails.append(trace((15.30, ROOT_Y), (13.45, -11.25), 2.10, BASE, 6.85))
    spring = solid(fixed_rail.fuse(*jaw, *root_rails, bottom, grip), "coupon_spring", "#396A77")
    catch = shell_catch(True)
    handle = rounded_panel(7.0, 0, 8.8, 9.8, 1.3, SEAT, CATCH_TOP)
    catch = solid(catch.fuse(handle), "coupon_catch", "#FF6327")
    return spring, catch
