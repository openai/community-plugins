#!/usr/bin/env python3
"""Compare delivered and rebuilt STEP solids in world coordinates.

Summary measurements cannot establish shape identity. This comparison performs
both native Boolean cuts for each named solid and retains their residual volumes.
Tolerance is fixed numerical readback allowance, not a manufacturing tolerance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import verify_geometry as geometry

MAX_SYMMETRIC_DIFFERENCE_MM3 = .001
MAX_BOUNDS_DRIFT_MM = .00001


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _finite(value):
    if not math.isfinite(value):
        raise ValueError('Nonfinite geometry measurement')
    return value


def _parts(path, names):
    source = geometry._kernel().import_step(str(Path(path).resolve(strict=True)))
    result = {}
    for part in geometry.world_parts(source):
        if not part.label or part.label in result or part.label not in names:
            raise ValueError('Duplicate, missing or unexpected solid identity')
        if not part.is_valid or len(part.solids()) != 1 or _finite(part.volume) <= 0:
            raise ValueError('Each expected part must be one valid positive-volume solid')
        result[part.label] = part
    if set(result) != set(names):
        raise ValueError('Delivered and rebuilt STEP must contain every expected part')
    return result


def _readback(part):
    box = part.bounding_box()
    values = {'solid_count': len(part.solids()), 'valid': part.is_valid,
              'volume_mm3': part.volume, 'bounds_min_mm': list(box.min), 'bounds_max_mm': list(box.max),
              'world_location': {'position_mm': list(part.location.position),
                                 'orientation_deg': list(part.location.orientation)}}
    geometry._finite(values)
    return values


def _residual_volume(first, second):
    residual = first - second
    if not residual.is_valid:
        raise ValueError('Native Boolean difference produced invalid geometry')
    volume = _finite(residual.volume)
    if volume < -1e-9:
        raise ValueError('Native Boolean difference produced negative volume')
    return max(0., volume)


def compare_steps(delivered, rebuilt, expected_names):
    paths = {'delivered': Path(delivered), 'rebuilt': Path(rebuilt)}
    report = {'schema_version': 1, 'status': 'unknown', 'units': 'mm', 'expected_parts': sorted(expected_names),
              'tolerances': {'symmetric_difference_mm3': MAX_SYMMETRIC_DIFFERENCE_MM3,
                             'bounds_drift_mm': MAX_BOUNDS_DRIFT_MM},
              'inputs': {}, 'parts': {},
              'method': 'Named single solids; ancestor placements accumulated; native A-minus-B and B-minus-A BRep cuts in world coordinates.'}
    try:
        if not expected_names or len(set(expected_names)) != len(expected_names):
            raise ValueError('Expected part identities must be nonempty and unique')
        for role, path in paths.items():
            if path.is_symlink() or not path.is_file():
                raise ValueError('STEP input must be a regular file')
            report['inputs'][role] = {'file': path.name, 'sha256': digest(path)}
        parts = {role: _parts(path, expected_names) for role, path in paths.items()}
        for name in sorted(expected_names):
            a, b = parts['delivered'][name], parts['rebuilt'][name]
            actual_a, actual_b = _readback(a), _readback(b)
            ab, ba = _residual_volume(a, b), _residual_volume(b, a)
            symmetric_difference = ab + ba
            bound_a = actual_a['bounds_min_mm'] + actual_a['bounds_max_mm']
            bound_b = actual_b['bounds_min_mm'] + actual_b['bounds_max_mm']
            drift = max(abs(x-y) for x, y in zip(bound_a, bound_b))
            report['parts'][name] = {'status': 'pass' if symmetric_difference <= MAX_SYMMETRIC_DIFFERENCE_MM3 and drift <= MAX_BOUNDS_DRIFT_MM else 'fail',
                                     'delivered': actual_a, 'rebuilt': actual_b,
                                     'delivered_minus_rebuilt_mm3': ab, 'rebuilt_minus_delivered_mm3': ba,
                                     'symmetric_difference_mm3': symmetric_difference, 'max_bounds_drift_mm': drift}
        for role, path in paths.items():
            if digest(path) != report['inputs'][role]['sha256']:
                raise ValueError('STEP input changed during comparison')
        report['status'] = 'pass' if all(part['status'] == 'pass' for part in report['parts'].values()) else 'fail'
    except Exception as exc:
        report['status'] = 'unknown'
        report['error'] = f'{type(exc).__name__}: {exc}'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('delivered')
    parser.add_argument('rebuilt')
    parser.add_argument('--part', action='append', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    report = compare_steps(args.delivered, args.rebuilt, args.part)
    Path(args.out).write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': report['status'], 'parts': len(report['parts'])}))
    return {'pass': 0, 'fail': 1, 'unknown': 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
