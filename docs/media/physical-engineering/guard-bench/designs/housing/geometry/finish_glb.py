"""Apply the recipe's declared display colors to the named glTF mesh batches.

CAD transfer can drop a STEP face's display style on repeated runs. The glTF
binary chunk contains positions, normals and indices and is preserved unchanged.
"""
import json
from pathlib import Path
import struct


def apply_materials(path):
    raw = path.read_bytes()
    json_length, chunk = struct.unpack_from("<II", raw, 12)
    if raw[:4] != b"glTF" or chunk != 0x4E4F534A:
        raise ValueError("Expected glTF 2 with a JSON chunk")
    doc = json.loads(raw[20:20 + json_length])
    geometry_chunk = raw[20 + json_length:]
    palette = json.loads(Path(__file__).with_name("display-materials.json").read_text())
    by_name = palette["meshes"]
    if set(by_name) != {mesh["name"] for mesh in doc["meshes"]}:
        raise ValueError("Every exported mesh must have a declared display finish")
    names = sorted(palette["materials"])
    doc["materials"] = [
        {"name":name,"doubleSided":True,"pbrMetallicRoughness":{
            "baseColorFactor":[*palette["materials"][name],1.0]}}
        for name in names
    ]
    for mesh in doc["meshes"]:
        material_id = names.index(by_name[mesh["name"]])
        for batch in mesh["primitives"]:
            batch["material"] = material_id
    text = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    header = struct.pack("<4sII", b"glTF", 2, 20 + len(text) + len(geometry_chunk))
    path.write_bytes(header + struct.pack("<II",len(text),0x4E4F534A) + text + geometry_chunk)
