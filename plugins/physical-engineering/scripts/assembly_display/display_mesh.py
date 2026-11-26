"""Read the assembly GLB as named groups of placed triangle primitives."""

import json
import struct

import numpy as np


def placement(node):
    if "matrix" in node:
        return np.array(node["matrix"]).reshape((4, 4), order="F")
    x, y, z, w = node.get("rotation", (0, 0, 0, 1))
    rotation = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )
    matrix = np.eye(4)
    matrix[:3, :3] = rotation @ np.diag(node.get("scale", (1, 1, 1)))
    matrix[:3, 3] = node.get("translation", (0, 0, 0))
    return matrix


def primitive_groups(path):
    """Yield each assembly group name and its (positions, normals, RGB, indices) primitives."""
    raw = path.read_bytes()
    (json_length,) = struct.unpack_from("<I", raw, 12)
    scene = json.loads(raw[20 : 20 + json_length])
    binary = memoryview(raw)[28 + json_length :]

    def accessor(index):
        item = scene["accessors"][index]
        view = scene["bufferViews"][item["bufferView"]]
        types = {5121: "u1", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
        dtype = np.dtype(types[item["componentType"]])
        width = {"SCALAR": 1, "VEC3": 3}[item["type"]]
        return np.ndarray(
            (item["count"], width),
            dtype,
            buffer=binary,
            offset=view.get("byteOffset", 0) + item.get("byteOffset", 0),
            strides=(view.get("byteStride", width * dtype.itemsize), dtype.itemsize),
        )

    def visit(idx, parent):
        node = scene["nodes"][idx]
        transform = parent @ placement(node)
        if "mesh" in node:
            basis = transform[:3, :3]
            normal_transform = np.linalg.inv(basis)
            for primitive in scene["meshes"][node["mesh"]]["primitives"]:
                if primitive.get("mode", 4) != 4:
                    raise ValueError("Display requires triangulated geometry")
                attrs = primitive["attributes"]
                xyz = accessor(attrs["POSITION"]) @ basis.T + transform[:3, 3]
                normals = accessor(attrs["NORMAL"]) @ normal_transform
                normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-10)
                if "material" in primitive:
                    material = scene["materials"][primitive["material"]].get(
                        "pbrMetallicRoughness", {}
                    )
                    rgb = np.array(material.get("baseColorFactor", [1, 1, 1, 1])[:3])
                else:
                    rgb = np.array([0.60, 0.65, 0.68])
                yield xyz, normals, rgb, accessor(primitive["indices"]).reshape(-1, 3)
        for child in node.get("children", []):
            yield from visit(child, transform)

    (root_index,) = scene["scenes"][scene.get("scene", 0)]["nodes"]
    root = scene["nodes"][root_index]
    world = placement(root)
    for child in root["children"]:
        yield scene["nodes"][child]["name"], visit(child, world)
