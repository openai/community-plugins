"""65 mm wheelbase indoor 1S frame. Millimetres; +Y nose, +Z up.

The Z=0 plane is the seating surface for each motor. A 26 mm square FC
pattern is rotated 45 degrees, putting its mounting bosses between rotors.
"""

import math

import build123d as bd

MOTOR_OFFSET = 65.0 / math.sqrt(8)
FC_OFFSET = 26.0 / math.sqrt(2)
PLATE_BOTTOM = -1.25
RIM_INNER = 16.30
GUARD_WALL = 1.40
RIM_OUTER = RIM_INNER + GUARD_WALL


def cylinder(x, y, radius, z0, z1):
    return bd.Pos(x, y, (z0 + z1) / 2) * bd.Cylinder(radius, z1 - z0)


def link(a, b, width):
    """Flat link whose ends enter the adjacent bosses."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    center = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, PLATE_BOTTOM / 2)
    return (
        bd.Pos(*center)
        * bd.Rot(0, 0, math.degrees(math.atan2(dy, dx)))
        * bd.Box(length, width, -PLATE_BOTTOM)
    )


def duct_strut(cx, cy, heading):
    """A level root keeps the motor free above the seat, then rises to the guard."""
    section = bd.Plane.XZ * bd.Polygon(
        (4.25, -1.25),
        (6.15, -1.25),
        (16.82, 7.04),
        (16.82, 8.20),
        (5.75, 0.0),
        (4.25, 0.0),
        align=None,
    )
    beam = bd.extrude(section, amount=0.80, both=True)
    return bd.Pos(cx, cy, 0) * bd.Rot(0, 0, heading) * beam


def component():
    fc_posts = [(FC_OFFSET, 0), (0, FC_OFFSET), (-FC_OFFSET, 0), (0, -FC_OFFSET)]
    solids, bores = [], []
    for index, post in enumerate(fc_posts):
        solids.append(cylinder(*post, 2.15, PLATE_BOTTOM, 2.5))
        solids.append(link(post, fc_posts[(index + 1) % 4], 1.85))
        bores.append(cylinder(*post, 0.625, -2, 3))

    for sx in (-1, 1):
        for sy in (-1, 1):
            cx, cy = sx * MOTOR_OFFSET, sy * MOTOR_OFFSET
            seat = cylinder(cx, cy, 5.05, PLATE_BOTTOM, 0)
            guard = cylinder(cx, cy, RIM_OUTER, 7, 12.5).cut(cylinder(cx, cy, RIM_INNER, 6.5, 13))
            solids.extend(
                (
                    seat,
                    guard,
                    link((sx * FC_OFFSET, 0), (cx, cy), 2.15),
                    link((0, sy * FC_OFFSET), (cx, cy), 2.15),
                )
            )
            for step in (0, 120, 240):
                solids.append(duct_strut(cx, cy, math.degrees(math.atan2(sy, sx)) + step))
            bores.append(cylinder(cx, cy, 2.00, -2, 0.35))
            for deg in (90, 210, 330):
                phi = math.radians(deg)
                bores.append(
                    cylinder(cx + 3.3 * math.cos(phi), cy + 3.3 * math.sin(phi), 0.775, -2, 0.35)
                )

    for y in (-8.5, 8.5):
        crossbar = bd.Pos(0, y, PLATE_BOTTOM) * bd.extrude(
            bd.RectangleRounded(22.6, 7.4, 1.7), amount=-PLATE_BOTTOM
        )
        solids.append(crossbar)
        for x in (-7.6, 7.6):
            slot = bd.Pos(x, y, -2) * bd.extrude(bd.RectangleRounded(2.0, 4.8, 0.45), amount=3)
            bores.append(slot)

    part = solids[0].fuse(*solids[1:]).cut(*bores).clean()
    if len(part.solids()) != 1 or not part.is_valid:
        raise ValueError("Frame must be one valid solid")
    part = part.solids()[0]
    part.label = "whoop_frame"
    part.color = "#365361"
    return part
