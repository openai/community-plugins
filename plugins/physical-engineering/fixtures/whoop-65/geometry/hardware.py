"""Air65-class purchased-equipment reference solids and a custom camera canopy.

Millimetres, +Y forward, +X pilot's right, +Z up.  Motor seat top is Z=0.
The canopy is custom geometry. All purchased parts are interface models.
"""

from math import cos, radians, sin, sqrt
import build123d as bd

ARM = 65 / (2 * sqrt(2))
POST = 26 / sqrt(2)
POSTS = ((-POST, 0), (POST, 0), (0, -POST), (0, POST))
CORNERS = (("FL", -1, 1, "CCW"), ("FR", 1, 1, "CW"), ("RL", -1, -1, "CW"), ("RR", 1, -1, "CCW"))
MOTOR_PATTERN = tuple((3.3 * cos(radians(a)), 3.3 * sin(radians(a))) for a in (90, 210, 330))
CAM_TILT = 25
CAM_ORIGIN = (0, 1.55, 15.0)

ORANGE = "#FF6327"
AQUA = "#72CEC6"
GUN = "#35404A"
STEEL = "#A5B1BE"
COPPER = "#C86932"
BLACK = "#262B32"
GREEN = "#246250"
GOLD = "#DBAA4B"
CREAM = "#DFE7E5"


def part(shape, name, color):
    shape.label = name
    shape.color = color
    return shape


def group(name, children):
    return bd.Compound(children=children, label=name)


def cylinder(r, z0, z1, x=0, y=0):
    return bd.Solid.make_cylinder(r, z1 - z0, bd.Plane(origin=(x, y, z0)))


def box(x, y, z, dx, dy, dz):
    return bd.Pos(x, y, z) * bd.Box(dx, dy, dz)


def rounded_box(x, y, z, dx, dy, dz, r):
    raw = bd.Box(dx, dy, dz)
    return bd.Pos(x, y, z) * bd.fillet(raw.edges(), r)


def capsule(a, b, radius):
    vector = bd.Vector(b) - bd.Vector(a)
    return bd.Solid.make_cylinder(radius, vector.length, bd.Plane(origin=a, z_dir=vector))


def wire(points, radius):
    """A smooth round insulated lead through interpolation points."""
    path = bd.Edge.make_spline(points)
    section = bd.Wire.make_circle(radius, bd.Plane(origin=points[0], z_dir=path.tangent_at(0)))
    return bd.Solid.sweep(section, path)


def trim_hex(x, y, z, direction, radius, length):
    sketch = bd.RegularPolygon(radius, 6)
    return bd.Pos(x, y, z) * bd.extrude(sketch, amount=direction * length)


def screw(x, y, seat, up, length):
    """Thread-free envelope. Seat is the underside of the head."""
    neck = capsule((x, y, seat), (x, y, seat + up * length), 0.67)
    head = capsule((x, y, seat - up * 0.65), (x, y, seat), 1.22)
    socket = trim_hex(x, y, seat - up * 0.66, up, 0.57, 0.34)
    return head.fuse(neck).cut(socket)


def motor():
    # Visible vented outrunner bell; supplier body and shaft envelopes.
    foot = cylinder(4.15, 0, 1.85)
    for x, y in MOTOR_PATTERN:
        foot = foot.cut(cylinder(0.72, -0.1, 1.90, x, y))
    foot = part(foot, "mounting_base", GUN)
    core = part(cylinder(2.3, 1.85, 6.8), "stator_core", "#53616A")
    coils = []
    for i in range(9):
        angle = i * 40
        coil = bd.Rot(Z=angle) * rounded_box(3.03, 0, 4.17, 1.75, 1.65, 4.20, 0.47)
        coils.append(coil)
    windings = part(bd.Compound(coils), "exposed_windings", COPPER)
    wall = cylinder(4.5, 4.95, 7.35).cut(cylinder(4.12, 4.9, 7.45))
    cap = bd.Solid.make_cone(4.50, 3.18, 1.2, bd.Plane(origin=(0, 0, 7.35)))
    bell = wall.fuse(cap)
    for i in range(6):
        vent = bd.Rot(Z=i * 60) * cylinder(0.74, 7.23, 9, 2.53)
        bell = bell.cut(vent)
    shaft = cylinder(0.5, 8.55, 12.75)
    return group(
        "motor",
        [foot, core, windings, part(bell, "bell", "#536977"), part(shaft, "shaft_1mm", STEEL)],
    )


def aero_section(radius, chord, theta, twist, thickness, z=10.47):
    """Closed, cambered section swept backward from the direction of travel (+Y)."""
    t = radians(theta)
    ca, sa = cos(t), sin(t)
    slope = radians(twist)

    def pt(frac, thick):
        dy = frac * chord * cos(slope)
        return (
            radius * ca - dy * sa,
            radius * sa + dy * ca,
            z + frac * chord * sin(slope) + thick * thickness,
        )

    # Leading edge (positive chord coordinate) is higher for CCW rotation.
    upper = [
        pt(-0.5, 0),
        pt(-0.27, 0.42),
        pt(0.03, 0.75),
        pt(0.30, 0.66),
        pt(0.47, 0.40),
        pt(0.5, 0),
    ]
    lower = [
        pt(0.5, 0),
        pt(0.46, -0.32),
        pt(0.24, -0.35),
        pt(-0.05, -0.22),
        pt(-0.32, -0.12),
        pt(-0.5, 0),
    ]
    return bd.Wire([bd.Edge.make_spline(upper), bd.Edge.make_spline(lower)])


def propeller(hand):
    stations = [
        (1.12, 1.5, 8, 34, 0.65),
        (2.4, 2.35, 8, 30, 0.85),
        (5.0, 3.35, 2, 24, 0.68),
        (9.1, 4.7, -5, 20, 0.56),
        (12.6, 4.6, -12, 15, 0.43),
        (14.8, 2.8, -18, 12, 0.30),
        (15.42, 0.90, -20, 10, 0.22),
        (15.499, 0.075, -20.3, 10, 0.12),
    ]
    blade = bd.Solid.make_loft([aero_section(*s) for s in stations])
    hub = cylinder(1.56, 8.64, 12.45)
    shape = hub.fuse(*[bd.Rot(Z=a) * blade for a in (0, 120, 240)]).cut(cylinder(0.5, 8.3, 12.8))
    if hand == "CW":
        shape = shape.mirror(bd.Plane.XZ)
    return shape


def propulsion():
    results = []
    for i, (name, sx, sy, hand) in enumerate(CORNERS):
        # Locations baked only at top grouping level, preserving component labels.
        m = motor()
        m.label = "motor_" + name
        p = bd.Rot(Z=(12, 48, -17, 25)[i]) * propeller(hand)
        part(p, "prop_" + name + "_" + hand, ORANGE if sy > 0 else AQUA)
        fast = [
            part(screw(x, y, -1.25, 1, 3), "M1_4x3_" + str(j + 1), STEEL)
            for j, (x, y) in enumerate(MOTOR_PATTERN)
        ]
        g = group("propulsion_" + name, [m, p, group("motor_screws_" + name, fast)])
        g.position = (sx * ARM, sy * ARM, 0)
        results.append(g)
    return results


def phase_pad(sx, sy, j):
    return sx * (11.25 + j * 1.49), sy * (5.6 - j * 0.86)


def flight_controller():
    # Simplified symmetric perimeter traced against the vendor's top view.
    # Four lobes terminate at the 26 mm square mounting pattern.
    quarter = [
        (20.65, 0),
        (20.37, 1.12),
        (19.5, 1.95),
        (15.05, 4.60),
        (11.1, 7.2),
        (8.42, 9.5),
        (5.72, 13.0),
        (2.0, 19.45),
        (1.12, 20.37),
    ]
    edge = []
    for k in range(4):
        ca, sa = cos(k * radians(90)), sin(k * radians(90))
        edge.extend([(x * ca - y * sa, x * sa + y * ca, 3.5) for x, y in quarter])
    outline = bd.Wire(bd.Edge.make_spline(edge, periodic=True))
    board = bd.extrude(bd.Face(outline), amount=0.95, dir=(0, 0, 1))
    for x, y in POSTS:
        board = board.cut(cylinder(1.5, 3.4, 4.55, x, y))
    parts = [part(board, "pcb", GREEN)]
    packages = [
        (-3, 3.0, 5.5, 5.5, 0.85),
        (4.8, 2.6, 4.0, 4.0, 0.75),
        (-2.3, -4.1, 4.0, 3.9, 0.8),
        (5.3, -4.5, 3.1, 3.1, 0.8),
    ]
    for i, (x, y, dx, dy, dz) in enumerate(packages):
        parts.append(
            part(rounded_box(x, y, 4.45 + dz / 2, dx, dy, dz, 0.12), "IC_" + str(i + 1), BLACK)
        )
    # Illustrative passives and power stages; no electronic routing is represented.
    passives = []
    for sign in (-1, 1):
        for k in range(4):
            passives.append(box(sign * (3.8 + k * 1.55), 8 * sign, 4.68, 1.0, 1.4, 0.46))
    parts.append(part(bd.Compound(passives), "surface_mount_passives", "#9E9786"))
    pads = []
    for sx, sy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
        for j in range(3):
            x, y = phase_pad(sx, sy, j)
            pads.append(bd.Pos(x, y, 3.455) * bd.Rot(Z=-sx * sy * 29) * bd.Box(1.15, 1.55, 0.09))
    parts.append(part(bd.Compound(pads), "underside_motor_phase_pads", GOLD))
    # Underside SH-style programming socket receives an external USB adapter.
    jack = box(0, -13.15, 2.65, 5.4, 2.8, 1.7)
    jack = jack.cut(box(0, -14.51, 2.66, 4.1, 2.0, 0.92))
    parts.append(part(jack, "underside_programming_socket", CREAM))
    cam_socket = bd.Pos(4.8, 6.95, 5.20) * bd.Rot(Z=-47) * bd.Box(4.3, 2.3, 1.5)
    parts.append(part(cam_socket, "camera_input_socket", CREAM))
    vtx = cylinder(0.9, 4.45, 5.17, 13.15, 0).cut(cylinder(0.47, 4.85, 5.3, 13.15, 0))
    parts.append(part(vtx, "VTX_antenna_socket", GOLD))
    for x, y, name in (
        (-6.5, 9.25, "RX_antenna"),
        (-6.6, -8.9, "BATT_plus"),
        (-5.6, -10.3, "BATT_minus"),
    ):
        parts.append(part(cylinder(0.58, 4.45, 4.53, x, y), name + "_pad", GOLD))
    return group("flight_controller", parts)


def camera_pose(shape):
    return bd.Pos(*CAM_ORIGIN) * bd.Rot(X=CAM_TILT) * shape


def y_tube(r, y0, y1):
    return capsule((0, y0, 0), (0, y1, 0), r)


def fpv_camera():
    # Local PCB rear face at y=0; lens rim ends at y=15 mm.
    pcb = box(0, 0.42, 0, 11, 0.84, 14)
    mount = rounded_box(0, 2.65, 0, 8.6, 3.62, 8.6, 0.5)
    tube = y_tube(3.48, 4.46, 9.40)
    collar = y_tube(3.82, 9.40, 11.2)
    rim = y_tube(5, 11.2, 15).cut(y_tube(3.76, 14.36, 15.1))
    glass = y_tube(3.72, 14.36, 14.91)
    optical = y_tube(2.61, 14.91, 15.0)
    items = [
        (pcb, "imager_board", "#313E42"),
        (mount, "sensor_block", BLACK),
        (tube, "M7_lens_barrel", "#383E45"),
        (collar, "focus_collar", GUN),
        (rim, "front_ring", BLACK),
        (glass, "lens_glass", "#2F7788"),
        (optical, "front_element", "#203B52"),
    ]
    return group("fpv_camera", [part(camera_pose(obj), name, c) for obj, name, c in items])


def ellipse_level(z, rx, ry, cy):
    return bd.Wire.make_ellipse(rx, ry, bd.Plane(origin=(0, cy, z)))


def canopy():
    # Continuous 0.85 mm nominal shell skin. Smooth outer surface
    # and side vent windows leave a printable shell and integral mounting tabs.
    levels = [
        (5.30, 12.45, 14.80, 0.4),
        (7.9, 12.7, 14.90, 0.6),
        (12.0, 11.9, 14.0, 1.0),
        (17.1, 9.35, 11.3, 1.75),
        (21.5, 6.6, 8.0, 2.35),
        (23.5, 3.5, 4.5, 2.65),
        (23.86, 0.7, 1.0, 2.7),
    ]
    shell = bd.Solid.make_loft([ellipse_level(*lev) for lev in levels])
    inner = [
        (4.9, 11.55, 13.86, 0.4),
        (7.9, 11.8, 14.0, 0.6),
        (12, 10.95, 13.0, 1.0),
        (17.1, 8.27, 10.2, 1.75),
        (20.6, 6.15, 7.7, 2.3),
        (22.5, 3.31, 4.30, 2.65),
        (22.93, 0.6, 0.8, 2.7),
    ]
    shell = shell.cut(bd.Solid.make_loft([ellipse_level(*lev) for lev in inner]))
    # Two sculpted vents pass across both cheeks. Their ends stay clear of tabs.
    for y, z, ry, rz, angle in ((-4.5, 12.5, 5.2, 2.2, -18), (4.5, 9.5, 3.25, 1.38, 10)):
        pl = bd.Plane(
            origin=(-16, y, z), x_dir=(0, cos(radians(angle)), sin(radians(angle))), z_dir=(1, 0, 0)
        )
        section = bd.Face(bd.Wire.make_ellipse(ry, rz, pl))
        shell = shell.cut(bd.extrude(section, amount=32))
    # Separate rear portal for the low-mass dipoles, opening at the lower edge.
    shell = shell.cut(rounded_box(0, -13.9, 7.1, 9, 8, 7.2, 1))
    # Nose opening accommodates the 10 mm barrel and diverging optical path.
    shell = shell.cut(camera_pose(y_tube(5.42, 4.0, 30)))
    # Integral mounting ears + radial webs, each pilot clearance 1.65 mm.
    for x, y in POSTS:
        angle = 0 if x else 90
        span = 7.8 if x else 5.6
        radial = POST - span / 2
        loc = (
            radial if x > 0 else -radial if x < 0 else 0,
            radial if y > 0 else -radial if y < 0 else 0,
        )
        web = bd.Pos(*loc, 5.80) * bd.Rot(Z=angle) * bd.Box(span, 3.4, 1)
        ear = cylinder(1.95, 5.30, 6.30, x, y).fuse(web)
        if x == 0 and y < 0:
            # Bridge the rear opening on its sides rather than capping it.
            for sign in (-1, 1):
                start = (0, y + 1.0, 5.81)
                end = (sign * 5.05, -12.65, 5.81)
                t = capsule(start, end, 0.72).intersect(box(0, -15, 5.8, 16, 14, 0.98))
                ear = ear.fuse(t[0])
        shell = shell.fuse(ear).cut(cylinder(0.825, 5.1, 6.5, x, y))
    # PCB side channels, open at the bottom. Width/depth are provisional
    # for the 11 mm C03 board. Secure a test-fitted PCB with removable tape.
    for sign in (-1, 1):
        guide = box(sign * 5.78, 0.42, 0.4, 1.42, 2.26, 11.9)
        slot = box(sign * 5.40, 0.42, -0.1, 0.66, 1.16, 12.8)
        guide = camera_pose(guide.cut(slot))
        rib = capsule((sign * 6.0, 1.75, 14.6), (sign * 10.8, 1.75, 14.6), 0.85)
        shell = shell.fuse(guide, rib)
    # Provide clearance at both upper corners of the camera board.
    shell = shell.cut(camera_pose(box(0, 0.42, 0, 11.32, 1.16, 14.32)))
    # Access reliefs allow a plugged-in VTX coax and both battery leads
    # to enter below the skirt without bending against an edge.
    shell = shell.cut(
        rounded_box(12.7, -1.45, 5.33, 5.6, 4.85, 1.38, 0.28),
        rounded_box(-6.95, -11.9, 5.48, 4.0, 5.0, 2.05, 0.45),
    )
    # Preserve a continuous insertion path. With the canopy off the frame,
    # park the complete camera 7 mm aft along its own optical axis, slide up
    # from below parallel to the PCB, then feed it forward into its seat.
    relief = []
    back = 7.0
    lift = 33.0
    for w, lo, hi, h in [(11.30, -0.15, 0.99, 14.30), (8.90, 0.70, 4.61, 8.90)]:
        relief.extend(
            [
                box(0, (lo + hi - back) / 2, 0, w, hi - lo + back, h),
                box(0, (lo + hi) / 2 - back, -lift / 2, w, hi - lo, h + lift),
            ]
        )
    for rad, lo, hi in [(3.68, 4.31, 9.55), (4.02, 9.25, 11.35), (5.2, 11.05, 15.15)]:
        relief.extend(
            [
                y_tube(rad, lo - back, hi),
                box(0, (lo + hi) / 2 - back, -lift / 2, 2 * rad, hi - lo, lift),
            ]
        )
    shell = shell.cut(*[camera_pose(a) for a in relief])
    if len(shell.solids()) != 1:
        raise ValueError(f"Canopy has {len(shell.solids())} disconnected solids")
    return part(shell, "canopy", ORANGE)


def stack_hardware():
    parts = []
    for j, (x, y) in enumerate(POSTS):
        grom = cylinder(2.05, 2.5, 3.5, x, y).fuse(
            cylinder(1.46, 3.5, 4.45, x, y), cylinder(2.05, 4.45, 5.3, x, y)
        )
        grom = grom.cut(cylinder(0.78, 2.45, 5.35, x, y))
        parts.append(part(grom, "FC_grommet_" + str(j + 1), BLACK))
        parts.append(part(screw(x, y, 6.30, -1, 6), "ST1_4x6_" + str(j + 1), STEEL))
    return group("stack_hardware", parts)


def battery_pack():
    # Total 54 x 11 x 6.5 mm includes the terminal-end approximation.
    # The mating harness plug is placed separately aft of y=-27.
    pouch = rounded_box(0, 3.0, -5.0, 11, 48, 6.5, 0.62)
    cap = rounded_box(0, -23.1, -5.0, 11, 4.2, 6.5, 0.46)
    end = rounded_box(0, -26.1, -5, 5.25, 1.8, 4.3, 0.37)
    return group(
        "battery_pack",
        [
            part(pouch, "pouch", "#474F5C"),
            part(cap, "end_cap", "#D77B39"),
            part(end, "BT2_terminal", "#6CBAE7"),
        ],
    )


def strap(y):
    # Rectangular loop through the paired frame slots; cut a radiused
    # channel around the cell. Bend radii and tension are reference values.
    def loop_outline(points):
        p = [(x, y - 1.50, z) for x, z in points]
        return bd.Face(bd.Wire.make_polygon(p, close=True))

    outer = [
        (-7.90, 0.56),
        (7.90, 0.56),
        (7.90, -1.27),
        (6.03, -8.28),
        (5.6, -8.79),
        (-5.6, -8.79),
        (-6.03, -8.28),
        (-7.90, -1.27),
    ]
    inner = [
        (-7.37, 0),
        (7.37, 0),
        (7.37, -1.22),
        (5.51, -8.17),
        (5.40, -8.25),
        (-5.40, -8.25),
        (-5.51, -8.17),
        (-7.37, -1.22),
    ]
    return bd.extrude(loop_outline(outer), amount=3, dir=(0, 1, 0)).cut(
        bd.extrude(loop_outline(inner), amount=3, dir=(0, 1, 0))
    )


def battery_retention():
    parts = []
    for j, y in enumerate((-8.5, 8.5), 1):
        parts.append(part(strap(y), "silicone_loop_" + str(j), BLACK))
        parts.append(part(box(0, y, -1.5, 9.8, 2.7, 0.5), "foam_pad_" + str(j), "#7B8285"))
    return group("battery_retention", parts)


def wiring():
    parts = []
    for name, sx, sy, _ in CORNERS:
        for j in range(3):
            off = (j - 1) * 0.61
            # Lead exits sit above the 1.75 mm screw tips in the motor feet.
            pts = [
                (sx * (ARM - 2.9) + off, sy * (ARM - 2.4) - off, 2.40),
                (sx * 16.7 + off, sy * 16.5 - off, 2.25),
                (sx * (11.6 + j * 1.65), sy * 10.5, 2.62),
                (*phase_pad(sx, sy, j), 3.22),
            ]
            parts.append(
                part(wire(pts, 0.22), f"{name}_phase_{j + 1}", ("#777879", "#8E705B", "#5E7078")[j])
            )
    plug = rounded_box(0, -29.1, -5, 5.6, 4.2, 4.95, 0.48)
    parts.append(part(plug, "BT2_mating_plug", "#8FCAE8"))
    for sign, color in ((-1, BLACK), (1, "#BC4038")):
        pts = [
            (sign * 1.1, -29.3, -2.53),
            (sign * 1.2, -30.3, -0.9),
            (-3.6 + sign * 0.75, -24, 1.85),
            (-7.65 + sign * 0.7, -15.15, 4.90),
            ((-6.6 if sign > 0 else -5.6), (-8.9 if sign > 0 else -10.3), 4.98),
        ]
        parts.append(
            part(wire(pts, 0.53), "battery_lead_" + ("plus" if sign > 0 else "minus"), color)
        )
    for i, color in enumerate((BLACK, "#D75646", "#D5A938")):
        off = (i - 1) * 0.48
        pts = [
            (4.8 + off, 6.70, 5.3),
            (4.2 + off, 5.7, 6.1),
            (3.8 + off, 2.4, 7.7),
            (3.7 + off, 3.95, 9.25),
        ]
        parts.append(part(wire(pts, 0.18), "camera_harness_" + str(i + 1), color))
    # Wire paths are illustrative while the radiators remain purchased items.
    rx = [
        (-6.5, 9.25, 4.79),
        (-8.3, 4.3, 5.23),
        (-8, -4.5, 5.38),
        (-3.5, -10.9, 6.9),
        (-3.1, -16.8, 7.2),
        (-3.5, -22.1, 8.6),
    ]
    vtx = [
        (13.15, 0, 5.19),
        (11.4, -2.5, 5.04),
        (8.2, -6.3, 5.04),
        (2.1, -10.4, 6.85),
        (2.7, -17.4, 9.25),
        (3.2, -23.3, 14.5),
    ]
    parts.append(part(wire(rx, 0.32), "ELRS_receiver_antenna", BLACK))
    parts.append(part(wire(vtx, 0.32), "VTX_coax", GUN))
    parts.append(part(capsule(vtx[-1], (4.2, -30.9, 20.9), 0.72), "VTX_whip", BLACK))
    return group("wiring", parts)
