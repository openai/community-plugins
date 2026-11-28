"""Replace the Desktop fixture's two envelope references with the measured accelerometer housing."""

import argparse, json, sys, struct, tempfile
from pathlib import Path
import build123d as bd
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("fixture", type=Path, help="Verified Desktop guard fixture handoff")
parser.add_argument("recorder", type=Path, help="Verified routed accelerometer handoff")
parser.add_argument("destination", type=Path, help="New station output directory")
args = parser.parse_args()
bench = args.fixture.resolve()
case = args.recorder.resolve()
destination = args.destination.resolve()
if destination.exists():
    parser.error("Choose a fresh station output path")
destination.parent.mkdir(parents=True, exist_ok=True)
staging = tempfile.TemporaryDirectory(prefix=".station-", dir=destination.parent)
out = Path(staging.name) / "output"
out.mkdir()
sys.path.insert(0, str(case / "source/design/geometry/validation"))
from inspect_integration import vertical_holes, bbox, file_hash, overlap_volume


def members(path):
    return {x.label: x for x in bd.import_step(path).children}


def get_cylinders(shape):
    for face in shape.faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            cyl = surface.Cylinder()
            if abs(cyl.Axis().Direction().Z()) > 0.99999:
                yield cyl.Radius(), bbox(face)


def seat(shape):
    return min(
        b[0][2] for radius, b in get_cylinders(shape) if abs(radius - 2.75) < 0.001
    )


fixture = members(bench / "assembly.step")
housing = members(case / "assembly.step")
proxies = {k: fixture.pop(k) for k in ("sensor_base_proxy", "sensor_lid_proxy")}
axes = [[-2, 0], [62, 0]]
mounts = vertical_holes(fixture["plate"], 3.4, axes, 0.025)
local = vertical_holes(housing["base"], 3.4, [[-32, 0], [32, 0]], 0.025)
assert len(mounts) == len(local) == 2
translation = [
    mounts[0]["xy"][0] - local[0]["xy"][0],
    mounts[0]["xy"][1] - local[0]["xy"][1],
    mounts[0]["z"][1] - local[0]["z"][0],
]
tr = bd.Pos(*translation)
case_installed = {}
for name, part in housing.items():
    p = tr * part
    p.label = name
    case_installed[name] = p
located_holes = vertical_holes(case_installed["base"], 3.4, axes, 0.025)
assert [h["xy"] for h in located_holes] == [h["xy"] for h in mounts]
motion = {}
for slot in ["left", "right"]:
    k = "sensor_screw_" + slot
    screw = fixture[k]
    old_seat = seat(screw)
    correct = located_holes[0]["z"][1]
    delta = correct - old_seat
    p = bd.Pos(0, 0, delta) * screw
    p.label = k
    fixture[k] = p
    nut = fixture["sensor_nut_" + slot]
    nb = bbox(nut)
    sb = bbox(p)
    motion[k] = {
        "raise_mm": round(delta, 5),
        "head_underside_z_mm": round(seat(p), 5),
        "ear_top_z_mm": correct,
        "shank_bottom_z_mm": sb[0][2],
        "nut_vertical_span_mm": [nb[0][2], nb[1][2]],
        "nominal_shank_engagement_in_nut_mm": round(
            min(seat(p), nb[1][2]) - max(sb[0][2], nb[0][2]), 5
        ),
        "screw_ear_overlap_mm3": overlap_volume(p, case_installed["base"]),
    }
    assert abs(seat(p) - correct) < 0.001 and motion[k]["screw_ear_overlap_mm3"] < 0.001
all_parts = [*fixture.values(), *case_installed.values()]
closed = bd.Compound(children=all_parts, label="Guard_bench_with_accelerometer")
assert bd.export_step(closed, out / "assembly.step")
assert bd.export_gltf(
    closed,
    out / "assembly.glb",
    binary=True,
    linear_deflection=0.035,
    angular_deflection=0.15,
)


# Match each installed mesh to the actual source display finish. The STEP transfer
# can otherwise omit an inherited face style in assemblies of reused components.
def read_glb(path):
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20 : 20 + length]), data[20 + length :]


styles = {}
for original in [bench / "assembly.glb", case / "assembly.glb"]:
    doc, _ = read_glb(original)
    for mesh in doc["meshes"]:
        ids = {pr["material"] for pr in mesh["primitives"]}
        if len(ids) != 1:
            raise ValueError(
                "Expected a single authored display finish on " + mesh["name"]
            )
        finish = dict(doc["materials"][ids.pop()])
        finish.pop("name", None)
        styles[mesh["name"]] = json.dumps(finish, sort_keys=True)
path = out / "assembly.glb"
doc, bin_chunk = read_glb(path)
names = sorted({styles[m["name"]] for m in doc["meshes"]})
doc["materials"] = [
    dict(name=f"finish_{i}", **json.loads(value)) for i, value in enumerate(names)
]
for mesh in doc["meshes"]:
    for pr in mesh["primitives"]:
        pr["material"] = names.index(styles[mesh["name"]])
header = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
header += b" " * (-len(header) % 4)
path.write_bytes(
    struct.pack("<4sII", b"glTF", 2, 20 + len(header) + len(bin_chunk))
    + struct.pack("<II", len(header), 0x4E4F534A)
    + header
    + bin_chunk
)
actual = members(out / "assembly.step")
rest = vertical_holes(actual["base"], 3.4, axes, 0.025)
plateholes = vertical_holes(actual["plate"], 3.4, axes, 0.025)
z0 = case_installed["base"].bounding_box().min.Z
report = {
    "input": {
        "fixture_assembly_sha256": file_hash(bench / "assembly.step"),
        "recorder_assembly_sha256": file_hash(case / "assembly.step"),
    },
    "output": {
        "station_step_sha256": file_hash(out / "assembly.step"),
        "station_glb_sha256": file_hash(out / "assembly.glb"),
    },
    "translation_mm": translation,
    "housing_rotation_degrees": 0,
    "reference_envelopes_replaced": list(proxies),
    "retained_fixture_nodes": list(fixture),
    "mount_holes_both_components": {"case": rest, "plate": plateholes},
    "plate_housing_solid_overlap_mm3": overlap_volume(actual["plate"], actual["base"]),
    "coupon_to_final_enclosure_gap_mm": round(
        actual["coupon_100"].distance_to(actual["base"]), 5
    ),
    "coupon_tip_to_stop_gap_mm": round(
        actual["coupon_100"].distance_to(actual["travel_stop"]), 5
    ),
    "installation_hardware": motion,
    "lid_opening": "The open illustration removes only the lid and its four lid screws; all board and fixture parts remain in their installed locations.",
}
assert (
    report["plate_housing_solid_overlap_mm3"] == 0
    and abs(report["coupon_tip_to_stop_gap_mm"] - 2.0) < 0.02
)
(out / "registration.json").write_text(json.dumps(report, indent=2) + "\n")
out.rename(destination)
staging.cleanup()
print(json.dumps(report, indent=2))
