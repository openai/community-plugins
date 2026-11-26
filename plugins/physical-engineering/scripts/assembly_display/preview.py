"""Draw an orthographic studio view of the exported GLB on a headless CPU."""

import numpy as np
from PIL import Image, ImageFilter
from display_mesh import primitive_groups


def unit(vector):
    return vector / np.maximum(np.linalg.norm(vector, axis=-1, keepdims=True), 1e-12)


def triangles(path):
    """Expand display primitives to per-triangle geometry and materials."""
    vertices, normals, colors = [], [], []
    for _, primitives in primitive_groups(path):
        for xyz, normal, rgb, indices in primitives:
            vertices.append(xyz[indices])
            normals.append(normal[indices])
            colors.append(np.broadcast_to(rgb, (len(indices), 3)))
    return tuple(np.concatenate(items).astype(np.float32) for items in (vertices, normals, colors))


class View:
    """Projected coordinates and the foremost triangle at each pixel center."""

    def __init__(self, vertices, eye, width, height):
        self.eye = unit(np.asarray(eye))
        right = unit(np.cross([0, 1, 0], self.eye))
        self.basis = np.stack([right, -np.cross(self.eye, right), self.eye])
        projected = vertices @ self.basis.T
        lo = projected[:, :, :2].min(axis=(0, 1))
        hi = projected[:, :, :2].max(axis=(0, 1))
        self.scale = 0.89 * min(np.array([width, height]) / (hi - lo))
        self.offset = np.array([width, height]) / 2 - self.scale * (lo + hi) / 2
        projected[:, :, :2] = self.scale * projected[:, :, :2] + self.offset

        # Enumerate triangle bounding boxes in batches, then apply the depth test.
        start = np.maximum(np.ceil(projected[:, :, :2].min(axis=1)).astype(int), 0)
        end = np.minimum(
            np.floor(projected[:, :, :2].max(axis=1)).astype(int), [width - 1, height - 1]
        )
        spans = np.maximum(end - start + 1, 0)
        counts = np.prod(spans, axis=1)
        ends = np.cumsum(counts)
        depth = np.full(width * height, -np.inf)
        faces = np.full(width * height, -1, dtype=np.int32)
        weights = np.zeros((width * height, 3), dtype=np.float32)
        for begin in range(0, int(ends[-1]), 180_000):
            sample = np.arange(begin, min(ends[-1], begin + 180_000))
            face = np.searchsorted(ends, sample, side="right")
            pos = sample - ends[face] + counts[face]
            x = start[face, 0] + pos % spans[face, 0]
            y = start[face, 1] + pos // spans[face, 0]
            a, b, c = projected[face].transpose(1, 0, 2)
            ab, ac = b - a, c - a
            dx, dy = x - a[:, 0], y - a[:, 1]
            determinant = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
            valid = abs(determinant) > 1e-8
            det = np.where(valid, determinant, 1)
            u = (dx * ac[:, 1] - dy * ac[:, 0]) / det
            v = (ab[:, 0] * dy - ab[:, 1] * dx) / det
            valid &= (u >= 0) & (v >= 0) & (u + v <= 1)
            z = (a[:, 2] + ab[:, 2] * u + ac[:, 2] * v)[valid]
            pixel = (y * width + x)[valid]
            np.maximum.at(depth, pixel, z)
            front = z == depth[pixel]
            ids = pixel[front]
            faces[ids] = face[valid][front]
            u, v = u[valid][front], v[valid][front]
            weights[ids] = np.stack([1 - u - v, u, v], axis=-1)
        self.depth = depth.reshape(height, width)
        self.faces = faces.reshape(height, width)
        self.weights = weights.reshape(height, width, 3)


def render(glb, destination):
    vertices, normals, colors = triangles(glb)
    width, height = 1440, 920
    camera = View(vertices, [0.44, 0.76, -1], width, height)
    light = unit(np.array([0.50, 1.3, -0.72]))
    shadow = View(vertices, light, 720, 720)
    fill = unit(np.array([-0.80, 0.85, 0.44]))

    # Reconstruct visible surface positions; missing pixels intersect a matte floor.
    y, x = np.indices((height, width))
    screen = (np.stack([x, y], axis=-1) - camera.offset) / camera.scale
    plane = screen @ camera.basis[:2]
    hit = camera.faces >= 0
    span = np.ptp(vertices.reshape(-1, 3), axis=0).max()
    floor = vertices[:, :, 1].min() - 0.0012 * span
    distance = np.where(hit, camera.depth, (floor - plane[:, :, 1]) / camera.eye[1])
    positions = plane + distance[:, :, None] * camera.eye
    light_space = positions @ shadow.basis.T
    projected = np.rint(light_space[:, :, :2] * shadow.scale + shadow.offset).astype(int)
    sx, sy = projected.transpose(2, 0, 1)
    inside = (sx >= 0) & (sx < 720) & (sy >= 0) & (sy < 720)
    occluded = np.zeros((height, width), dtype=bool)
    occluded[inside] = (
        light_space[:, :, 2][inside] + 0.0024 * span < shadow.depth[sy[inside], sx[inside]]
    )
    soft = Image.fromarray(occluded.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(8))
    shadow_strength = np.asarray(soft, dtype=float) / 255
    ground = (
        1 - 0.18 * shadow_strength - 0.025 * ((x / width - 0.5) ** 2 + (y / height - 0.55) ** 2)
    )
    linear = ground[:, :, None] * np.array([0.895, 0.915, 0.915])

    face = camera.faces[hit]
    normal = unit((normals[face] * camera.weights[hit][:, :, None]).sum(axis=1))
    normal[(normal @ camera.eye) < 0] *= -1
    key = np.maximum(0, normal @ light)
    visibility = np.where(occluded[hit], 0.30, 1)
    ambient = 0.27 + 0.15 * np.maximum(0, normal[:, 1])
    intensity = ambient + 0.93 * key * visibility + 0.29 * np.maximum(0, normal @ fill)
    highlight = 0.17 * np.maximum(0, normal @ unit(light + camera.eye)) ** 42 * visibility
    linear[hit] = colors[face] * intensity[:, None] + highlight[:, None]
    srgb = np.where(linear < 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)
    image = Image.fromarray(np.uint8(np.clip(srgb, 0, 1) * 255))
    image.resize((1152, 736), Image.Resampling.LANCZOS).save(destination)
