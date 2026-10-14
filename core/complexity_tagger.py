"""Image attribute analysis, calibration, and caption tagging.

The cache and calibration files live under the application's workspace; dataset
images are opened read-only and caption updates use atomic replacement.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import multiprocessing
import os
import random
import shutil
import tempfile
import unicodedata
from bisect import bisect_left, bisect_right
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageStat, __version__ as PILLOW_VERSION
cv2.setNumThreads(1)

ATTRS = ('brightness', 'saturation', 'contrast', 'color_temperature', 'complexity')
LABELS = {a: ('very_low', 'low', 'medium', 'high', 'very_high') for a in ATTRS}
LABELS['color_temperature'] = ('very_cool', 'cool', 'neutral', 'warm', 'very_warm')
EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
FIELDS = ('path', 'size', 'mtime_ns', 'width', 'height', *ATTRS,
          'in_calib', 'status', 'error')
METRIC_VERSION = 'v9'
_CX_SCALE_WEIGHTS = (0.5, 0.3, 0.2)
_CX_NOISE_FLOOR = 3.0
_CX_SIGMA_MIN = 1.0
_CX_CHROMA_WEIGHT = 0.35
_CX_PNORM = 1.5
_CX_ACTIVE_THRESHOLD = 0.25
_CX_ORIENTATION_BINS = 8
_CX_DISPERSION_REFERENCE = 0.30
_CX_DIVERSITY_PRIOR_N = 6.0
_CX_DIVERSITY_PRIOR = 0.5
_CX_PATCHES_PER_LONG_SIDE = 32
_CX_NOISE_WINDOW_DIVISOR = 48
_CX_REFERENCE_LONG_SIDE = 768.0
_CX_KERNEL = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
_CX_NOISE_CONSTANT = math.sqrt(math.pi / 2.0) / 6.0


def _normalize_resolution(settings: dict | None = None) -> dict:
    settings = settings or {}
    mode = settings.get('mode', 'original')
    if mode not in ('original', 'percent', 'cutoff'):
        raise ValueError('계산 해상도 설정을 확인하세요.')
    return {'mode': mode,
            'percent': max(10, min(100, int(settings.get('percent', 100)))),
            'width': max(16, int(settings.get('width', 2048))),
            'height': max(16, int(settings.get('height', 2048)))}


def _metric_metadata(resolution: dict | None = None) -> dict:
    return {'version': METRIC_VERSION, 'resolution': _normalize_resolution(resolution),
            'pillow_version': PILLOW_VERSION, 'numpy_version': np.__version__,
            'opencv_version': cv2.__version__,
            'patches_per_long_side': _CX_PATCHES_PER_LONG_SIDE,
            'noise_window_divisor': _CX_NOISE_WINDOW_DIVISOR,
            'relative_unit_reference': _CX_REFERENCE_LONG_SIDE}


def _metric_matches(metric: dict, resolution: dict | None = None) -> bool:
    expected = _metric_metadata(resolution)
    return all(metric.get(key) == value for key, value in expected.items())


def _noise_sigma(channel: np.ndarray, patch: int, percentile: float = 10.0) -> float:
    """Estimate the noise floor from the lower tail of patch residuals."""
    residual = np.abs(cv2.filter2D(
        channel, cv2.CV_32F, _CX_KERNEL, borderType=cv2.BORDER_REFLECT
    ))[1:-1, 1:-1]
    height, width = residual.shape
    ny, nx = height // patch, width // patch
    if ny < 1 or nx < 1:
        return _CX_NOISE_CONSTANT * float(residual.mean())
    cropped = residual[:ny * patch, :nx * patch]
    patch_means = cropped.reshape(ny, patch, nx, patch).mean(axis=(1, 3))
    return _CX_NOISE_CONSTANT * float(np.percentile(patch_means, percentile))


def _complexity_detail(lab: np.ndarray, want_orientation: bool,
                       pixel_unit: float, noise_patch: int):
    """Return noise-suppressed luminance/chroma detail and optional directions."""
    luminance_detail = None
    chroma_detail = None
    jxx = jyy = jxy = None
    channel_weights = (1.0, _CX_CHROMA_WEIGHT, _CX_CHROMA_WEIGHT)
    for channel_idx in range(3):
        channel = np.ascontiguousarray(lab[..., channel_idx])
        # Express both gradients and the estimated noise in image-relative units.
        sigma = max(_noise_sigma(channel, patch=noise_patch) * pixel_unit,
                    _CX_SIGMA_MIN) * math.sqrt(12.0)
        gx = cv2.Sobel(channel, cv2.CV_32F, 1, 0, ksize=3, borderType=cv2.BORDER_REFLECT)
        gy = cv2.Sobel(channel, cv2.CV_32F, 0, 1, ksize=3, borderType=cv2.BORDER_REFLECT)
        gx *= pixel_unit
        gy *= pixel_unit
        magnitude = np.hypot(gx, gy)
        response = np.log1p(np.maximum(magnitude - _CX_NOISE_FLOOR * sigma, 0.0) / sigma)
        if channel_idx == 0:
            luminance_detail = response
        elif chroma_detail is None:
            chroma_detail = response
        else:
            chroma_detail += response
        if want_orientation:
            weight = channel_weights[channel_idx]
            xx, yy, xy = weight * gx * gx, weight * gy * gy, weight * gx * gy
            if jxx is None:
                jxx, jyy, jxy = xx, yy, xy
            else:
                jxx += xx
                jyy += yy
                jxy += xy
        del channel, gx, gy, magnitude, response

    if chroma_detail is None:
        chroma_detail = np.zeros_like(luminance_detail)
    detail = luminance_detail + _CX_CHROMA_WEIGHT * chroma_detail
    orientation = None
    if want_orientation:
        theta = 0.5 * np.arctan2(2.0 * jxy, jxx - jyy)
        orientation = np.clip(
            ((theta + np.pi / 2.0) / np.pi * _CX_ORIENTATION_BINS).astype(np.int32),
            0, _CX_ORIENTATION_BINS - 1,
        )
    return detail, luminance_detail, chroma_detail, orientation


def _hellinger_distance(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.sqrt(0.5 * np.sum((np.sqrt(left) - np.sqrt(right)) ** 2, axis=-1))


def complexity(rgb):
    """Native-resolution, noise-suppressed local detail score in [0, 1)."""
    source = rgb if rgb.mode == 'RGB' else rgb.convert('RGB')
    rgb_array = np.ascontiguousarray(np.asarray(source, dtype=np.uint8))
    height, width = rgb_array.shape[:2]
    if min(height, width) < 16:
        raise ValueError('too_small')

    lab_u8 = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2LAB)
    lab = lab_u8.astype(np.float32)
    long_side = max(height, width)
    # Keep a stable number of patches per relative image extent rather than
    # using a fixed patch width in source pixels.
    ny = max(1, round(height / long_side * _CX_PATCHES_PER_LONG_SIDE))
    nx = max(1, round(width / long_side * _CX_PATCHES_PER_LONG_SIDE))
    patch_count = ny * nx
    bin_count = _CX_ORIENTATION_BINS

    scale_cells, scale_luminance, scale_chroma = [], [], []
    orientation_histogram = None
    current = lab
    for scale_idx in range(3):
        if scale_idx:
            current = cv2.resize(
                current,
                (max(1, current.shape[1] // 2), max(1, current.shape[0] // 2)),
                interpolation=cv2.INTER_AREA,
            )
        scale_unit = long_side / (_CX_REFERENCE_LONG_SIDE * (2 ** scale_idx))
        noise_patch = max(
            1, round(max(current.shape[:2]) / _CX_NOISE_WINDOW_DIVISOR)
        )
        detail, luminance_detail, chroma_detail, orientation = _complexity_detail(
            current, want_orientation=(scale_idx == 0),
            pixel_unit=scale_unit, noise_patch=noise_patch,
        )
        size = (nx, ny)
        scale_cells.append(cv2.resize(detail, size, interpolation=cv2.INTER_AREA).ravel())
        scale_luminance.append(cv2.resize(luminance_detail, size, interpolation=cv2.INTER_AREA).ravel())
        scale_chroma.append(cv2.resize(chroma_detail, size, interpolation=cv2.INTER_AREA).ravel())

        if scale_idx == 0:
            orientation_histogram = np.zeros((patch_count, bin_count), dtype=np.float64)
            for row in range(ny):
                y0, y1 = row * height // ny, (row + 1) * height // ny
                for col in range(nx):
                    x0, x1 = col * width // nx, (col + 1) * width // nx
                    patch_orientation = orientation[y0:y1, x0:x1].ravel()
                    patch_detail = detail[y0:y1, x0:x1].ravel()
                    orientation_histogram[row * nx + col] = np.bincount(
                        patch_orientation, weights=patch_detail, minlength=bin_count
                    )

    patch_detail = sum(weight * values for weight, values in zip(_CX_SCALE_WEIGHTS, scale_cells))
    detail_score = float(np.mean(patch_detail ** _CX_PNORM) ** (1.0 / _CX_PNORM))
    active = patch_detail > _CX_ACTIVE_THRESHOLD
    active_count = int(active.sum())
    active_fraction = float(active.mean())

    if active_count >= 2:
        active_orientation = orientation_histogram[active]
        active_orientation /= np.maximum(active_orientation.sum(axis=1, keepdims=True), 1e-9)
        scale_signature = np.stack([values[active] for values in scale_cells], axis=1)
        scale_signature /= np.maximum(scale_signature.sum(axis=1, keepdims=True), 1e-9)
        chroma_ratio = (
            _CX_CHROMA_WEIGHT * scale_chroma[0][active]
            / np.maximum(scale_luminance[0][active] + _CX_CHROMA_WEIGHT * scale_chroma[0][active], 1e-9)
        )
        chroma_signature = np.stack([chroma_ratio, 1.0 - chroma_ratio], axis=1)
        descriptors = np.concatenate(
            [active_orientation, scale_signature, chroma_signature], axis=1
        ) / 3.0
        mean_descriptor = descriptors.mean(axis=0, keepdims=True)
        dispersion = float(_hellinger_distance(descriptors, mean_descriptor).mean())

        combined_orientation = active_orientation.sum(axis=0)
        combined_orientation /= max(float(combined_orientation.sum()), 1e-9)
        nonzero = combined_orientation[combined_orientation > 0]
        orientation_entropy = float(
            -(nonzero * np.log(nonzero)).sum() / math.log(bin_count)
        )
        raw_diversity = 0.5 * orientation_entropy + 0.5 * min(
            1.0, dispersion / _CX_DISPERSION_REFERENCE
        )
    else:
        raw_diversity = _CX_DIVERSITY_PRIOR
    diversity = (
        active_count * raw_diversity + _CX_DIVERSITY_PRIOR_N * _CX_DIVERSITY_PRIOR
    ) / (active_count + _CX_DIVERSITY_PRIOR_N)

    if active_count:
        mean_fine, mean_half, mean_quarter = (
            float(values[active].mean()) for values in scale_cells
        )
        persistence = float(np.clip(
            (0.6 * mean_half + 0.4 * mean_quarter) / (mean_fine + 1e-6), 0.0, 1.0
        ))
    else:
        persistence = 1.0

    # Mean color and edge density are intentionally excluded: both duplicate
    # information already represented by the calibrated attributes/detail core.
    core = (
        detail_score
        * (0.75 + 0.25 * diversity)
        * (0.6 + 0.4 * persistence)
        * (0.8 + 0.2 * active_fraction)
    )
    return core / (1.0 + core)


def _norm(rel: str) -> str:
    return unicodedata.normalize('NFC', rel.replace('\\', '/'))


def scan_images(root: str, exts=None, recursive: bool = True) -> list[str]:
    """Return supported image paths relative to root in stable order."""
    root_path = Path(root)
    allowed = {e.lower() if e.startswith('.') else f'.{e.lower()}' for e in (exts or EXTS)}
    result = []
    if recursive:
        for base, dirs, files in os.walk(root_path, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
            for filename in files:
                if Path(filename).suffix.lower() in allowed:
                    result.append(_norm(os.path.relpath(os.path.join(base, filename), root)))
    else:
        for filename in os.listdir(root_path):
            path = root_path / filename
            if path.is_file() and path.suffix.lower() in allowed:
                result.append(_norm(os.path.relpath(path, root)))
    return sorted(result)


def analyze_image(path: str, resolution: dict | None = None):
    with Image.open(path) as im:
        w0, h0 = im.size
        if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
            rgba = im.convert('RGBA')
            bg = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
            rgb = Image.alpha_composite(bg, rgba).convert('RGB')
        else:
            rgb = im.convert('RGB')
    resolution = _normalize_resolution(resolution)
    if resolution['mode'] == 'percent':
        scale = resolution['percent'] / 100.0
    elif resolution['mode'] == 'cutoff':
        scale = min(1.0, resolution['width'] / rgb.width,
                    resolution['height'] / rgb.height)
    else:
        scale = 1.0
    if scale < 1.0:
        size = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
        rgb = rgb.resize(size, Image.Resampling.BOX)
    if min(rgb.size) < 16:
        raise ValueError('too_small')
    complexity_value = complexity(rgb)
    lum = rgb.convert('L')
    r, g, b = rgb.split()
    mx = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mn = ImageChops.darker(ImageChops.darker(r, g), b)
    mr, _, mb = ImageStat.Stat(rgb).mean
    values = {
        'brightness': ImageStat.Stat(lum).mean[0] / 255,
        'saturation': ImageStat.Stat(ImageChops.difference(mx, mn)).mean[0] / 255,
        'contrast': ImageStat.Stat(lum).stddev[0] / 255,
        'color_temperature': (mr - mb) / (mr + mb) if mr + mb else 0.0,
        'complexity': complexity_value,
    }
    return w0, h0, {key: round(value, 6) for key, value in values.items()}


def _stat_row(root_rel):
    if len(root_rel) == 2:
        root, rel = root_rel
        resolution = None
    else:
        root, rel, resolution = root_rel
    path = os.path.join(root, *rel.split('/'))
    try:
        st = os.stat(path)
        w, h, raw = analyze_image(path, resolution)
        return {'path': rel, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns,
                'width': w, 'height': h, **raw, 'in_calib': 0,
                'status': 'ok', 'error': ''}
    except Exception as exc:
        try:
            st = os.stat(path)
            size, mtime = st.st_size, st.st_mtime_ns
        except OSError:
            size, mtime = 0, 0
        return {'path': rel, 'size': size, 'mtime_ns': mtime,
                'width': 0, 'height': 0, **{a: '' for a in ATTRS},
                'in_calib': 0, 'status': 'error',
                'error': type(exc).__name__ if str(exc) != 'too_small' else 'too_small'}


def load_cache(csv_path: str) -> dict[str, dict]:
    rows = {}
    if not os.path.isfile(csv_path):
        return rows
    try:
        with open(csv_path, encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            if tuple(reader.fieldnames or ()) != FIELDS:
                return rows
            for row in reader:
                if len(row) == len(FIELDS) and row.get('path'):
                    rows[_norm(row['path'])] = row
    except (OSError, csv.Error, UnicodeError):
        pass
    return rows


def _append_cache(csv_path: str, rows: list[dict]) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    if os.path.exists(csv_path) and os.path.getsize(csv_path):
        with open(csv_path, 'rb+') as existing:
            existing.seek(-1, os.SEEK_END)
            if existing.read(1) not in (b'\n', b'\r'):
                existing.seek(0, os.SEEK_END)
                existing.write(b'\n')
    with open(csv_path, 'a', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator='\n')
        if f.tell() == 0:
            writer.writeheader()
        writer.writerows(rows)


def compact_cache(csv_path: str) -> None:
    rows = load_cache(csv_path)
    directory = os.path.dirname(csv_path)
    fd, tmp = tempfile.mkstemp(prefix='.image_data_', suffix='.csv', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator='\n')
            writer.writeheader()
            for key in sorted(rows):
                writer.writerow(rows[key])
        os.replace(tmp, csv_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def percentile(sorted_values: list[float], p: float) -> float:
    k = (len(sorted_values) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def grade_label(attr: str, value: float, boundaries: list[float]) -> str:
    left = bisect_left(boundaries, value)
    right = bisect_right(boundaries, value)
    if right - left > 1:
        # Collapsed automatic percentile cuts: assign an equal-valued score
        # to the middle class represented by the tied boundaries.
        index = (left + right) // 2
    else:
        index = right
    return LABELS[attr][index]


def format_tag(raw: dict, thresholds: dict) -> str:
    return ', '.join(f'{a}={grade_label(a, float(raw[a]), thresholds[a])}' for a in ATTRS)


def _parse_item(value: str):
    key, sep, label = value.partition('=')
    key, label = key.strip().lower(), label.strip().lower()
    return (key, label) if sep and key in LABELS and label in LABELS[key] else None


def split_tag(text: str):
    body = text.rstrip().rstrip(',').rstrip()
    found = {}
    while body:
        i = body.rfind(',')
        parsed = _parse_item(body[i + 1:])
        if parsed is None:
            break
        found.setdefault(*parsed)
        body = body[:i].rstrip() if i >= 0 else ''
    return body, found


def tag_status(text: str) -> str:
    found = split_tag(text)[1]
    return 'complete' if len(found) == len(ATTRS) else ('partial' if found else 'none')


def strip_tag(text: str) -> str:
    return split_tag(text)[0]


def build_caption(old: str, tag: str) -> str:
    base = strip_tag(old)
    ending = '\r\n' if old.endswith('\r\n') else ('\n' if old.endswith('\n') else ('\r' if old.endswith('\r') else ''))
    if ending:
        return (f'{base}, {tag}' if base else tag) + ending
    return f'{base}, {tag}' if base else tag


def read_caption(img_path: str) -> str:
    path = os.path.splitext(img_path)[0] + '.txt'
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()


def write_caption(img_path: str, text: str, backup_dir: str | None = None) -> None:
    path = os.path.splitext(img_path)[0] + '.txt'
    directory = os.path.dirname(path)
    if backup_dir and os.path.exists(path):
        rel = hashlib.sha1(os.path.abspath(path).encode('utf-8')).hexdigest()[:12]
        backup = os.path.join(backup_dir, rel, os.path.basename(path))
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(prefix='.caption_', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def list_captions(root: str) -> list[str]:
    result = []
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        result.extend(os.path.join(base, f) for f in files if f.lower().endswith('.txt'))
    return sorted(result, key=lambda p: _norm(os.path.relpath(p, root)))


class ComplexityTagger:
    """Persistent per-calibration workspace and operations used by the tab."""

    def __init__(self, name: str | None = None, workspace_dir: str | None = None):
        self.name = name or datetime.now().strftime('%Y-%m-%d')
        if (self.name in ('.', '..') or os.path.basename(self.name) != self.name
                or any(c in self.name for c in '<>:"|?*')):
            raise ValueError('기준선 이름은 폴더 이름으로 사용할 수 있는 값이어야 합니다.')
        self.workspace = os.path.abspath(workspace_dir) if workspace_dir else os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'workspace', self.name)
        self.csv_path = os.path.join(self.workspace, 'image_data.csv')
        self.json_path = os.path.join(self.workspace, 'calibration.json')

    def import_existing(self, selected_file: str) -> dict:
        """Seed this session from a prior calibration JSON or raw-value CSV."""
        selected_file = os.path.abspath(selected_file)
        if not os.path.isfile(selected_file):
            raise FileNotFoundError(selected_file)
        if selected_file.lower().endswith('.json'):
            source_json = selected_file
            source_csv = os.path.join(os.path.dirname(selected_file), 'image_data.csv')
        elif selected_file.lower().endswith('.csv'):
            source_csv = selected_file
            source_json = os.path.join(os.path.dirname(selected_file), 'calibration.json')
        else:
            raise ValueError('calibration.json 또는 image_data.csv 파일을 선택하세요.')
        imported_config = {}
        if os.path.isfile(source_json):
            with open(source_json, encoding='utf-8') as f:
                imported_config = json.load(f)
            # Imported grade boundaries become fixed for this new session.
            for attr in ATTRS:
                threshold = imported_config.get('thresholds', {}).get(attr)
                if threshold and len(threshold.get('boundaries', [])) == 4:
                    threshold['method'] = 'manual'
        if os.path.isfile(source_csv) and os.path.abspath(source_csv) != os.path.abspath(self.csv_path):
            os.makedirs(self.workspace, exist_ok=True)
            imported_rows = load_cache(source_csv)
            if imported_rows:
                _append_cache(self.csv_path, list(imported_rows.values()))
                compact_cache(self.csv_path)
        if imported_config:
            imported_config['name'] = self.name
            self._save_config(imported_config)
        return {'cache_rows': len(load_cache(self.csv_path)),
                'thresholds_loaded': all(a in imported_config.get('thresholds', {}) for a in ATTRS)}

    def _load_config(self) -> dict:
        try:
            with open(self.json_path, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {'schema_version': 1, 'name': self.name,
                    # An absent config cannot establish which algorithm produced
                    # a possibly existing CSV cache, so force its recalculation.
                    'metric': {},
                    'percentiles': [5, 25, 75, 95], 'thresholds': {}, 'stats': {}}

    def _save_config(self, config: dict) -> None:
        os.makedirs(self.workspace, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.calibration_', dir=self.workspace)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.json_path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def _lock(self):
        os.makedirs(self.workspace, exist_ok=True)
        path = os.path.join(self.workspace, '.lock')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return path

    def calibrate(self, root: str, workers: int = 1, recursive: bool = True,
                  progress_cb=None,
                  resolution: dict | None = None) -> dict:
        resolution = _normalize_resolution(resolution)
        lock = self._lock()
        try:
            paths = scan_images(root, recursive=recursive)
            selected = set(paths)
            cache = load_cache(self.csv_path)
            config_before = self._load_config()
            old_metric = config_before.get('metric', {})
            if not _metric_matches(old_metric, resolution):
                cache = {}
                os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
                with open(self.csv_path, 'w', encoding='utf-8', newline='') as f:
                    csv.writer(f, lineterminator='\n').writerow(FIELDS)
            todo = []
            cached = []
            processed = 0
            for rel in paths:
                path = os.path.join(root, *rel.split('/'))
                st = os.stat(path)
                row = cache.get(rel)
                try:
                    valid = (row and int(row['size']) == st.st_size
                             and int(row['mtime_ns']) == st.st_mtime_ns)
                except (KeyError, TypeError, ValueError):
                    valid = False
                if valid:
                    row['in_calib'] = '1' if rel in selected else '0'
                    cached.append(row)
                    processed += 1
                    if progress_cb: progress_cb(processed, max(1, len(paths)), rel)
                else:
                    todo.append(rel)
            results = []
            if todo:
                with multiprocessing.Pool(max(1, workers)) as pool:
                    for row in pool.imap(_stat_row, [(root, rel, resolution) for rel in todo], chunksize=16):
                        row['in_calib'] = 1 if row['path'] in selected else 0
                        results.append(row)
                        processed += 1
                        if progress_cb: progress_cb(processed, max(1, len(paths)), row['path'])
                _append_cache(self.csv_path, results)
            _append_cache(self.csv_path, cached)
            compact_cache(self.csv_path)
            cache.update({r['path']: r for r in results + cached})
            current_rows = {}
            for rel in paths:
                row = cache.get(rel)
                if not row:
                    continue
                try:
                    st = os.stat(os.path.join(root, *rel.split('/')))
                    if int(row['size']) == st.st_size and int(row['mtime_ns']) == st.st_mtime_ns:
                        current_rows[rel] = row
                except (OSError, KeyError, ValueError):
                    continue
            values = {a: [] for a in ATTRS}
            for rel, row in current_rows.items():
                if str(row.get('in_calib')) == '1' and row.get('status') == 'ok':
                    for a in ATTRS: values[a].append(float(row[a]))
            config = self._load_config()
            stats, thresholds, warnings = {}, config.get('thresholds', {}), []
            ps = config.get('percentiles', [5, 25, 75, 95])
            for attr, vals in values.items():
                vals.sort()
                if not vals: continue
                ps_values = [percentile(vals, p) for p in (5, 25, 50, 75, 95)]
                stats[attr] = {'count': len(vals), 'min': vals[0], 'max': vals[-1],
                               'mean': sum(vals) / len(vals),
                               'std': (sum((v - sum(vals)/len(vals))**2 for v in vals) / len(vals))**0.5,
                               **{f'p{p:02}': value for p, value in zip((5, 25, 50, 75, 95), ps_values)}}
                bounds = [percentile(vals, p) for p in ps]
                if thresholds.get(attr, {}).get('method') != 'manual':
                    thresholds[attr] = {'method': 'auto', 'boundaries': bounds}
                if len(set(bounds)) < 4: warnings.append(f'{attr}: thresholds overlap')
            previous_seed = config.get('sampling', {}).get('seed', 381742)
            config.update(name=self.name, dataset_root=os.path.abspath(root), sampling={'percent': 100,
                'seed': previous_seed, 'calib_count': len(selected), 'dataset_count': len(paths)},
                thresholds=thresholds, stats=stats, metric=_metric_metadata(resolution))
            config['thresholds_hash'] = hashlib.sha1(json.dumps(
                {a: thresholds.get(a) for a in ATTRS}, sort_keys=True).encode()).hexdigest()
            self._save_config(config)
            display_rows = []
            for rel in paths:
                row = current_rows.get(rel)
                if row is None:
                    row = {'path': rel, 'status': 'not_analyzed', 'error': '',
                           'width': 0, 'height': 0, **{a: '' for a in ATTRS}}
                display_rows.append(row)
            analyzed_rows = results + cached
            return {'images': len(paths), 'baseline_count': len(selected),
                    'ok': sum(1 for r in analyzed_rows if r.get('status') == 'ok'),
                    'errors': sum(1 for r in analyzed_rows if r.get('status') == 'error'),
                    'thresholds': thresholds, 'stats': stats, 'warnings': warnings,
                    'rows': display_rows}
        finally:
            try: os.unlink(lock)
            except OSError: pass

    def run_tagging(self, root: str, workers: int = 1, recursive: bool = True,
                    create_missing: bool = False, on_existing: str = 'replace',
                    dry_run: bool = False, backup_dir: str | None = None,
                    progress_cb=None, resolution: dict | None = None) -> dict:
        resolution = _normalize_resolution(resolution)
        config = self._load_config()
        if not all(a in config.get('thresholds', {}) for a in ATTRS):
            raise ValueError('먼저 기준선 분석을 실행하세요.')
        metric = config.get('metric', {})
        if not _metric_matches(metric, resolution):
            raise ValueError('기준선과 현재 계산 해상도 설정이 다릅니다. 같은 설정으로 기준선을 다시 계산하세요.')
        lock = self._lock()
        try:
            paths = scan_images(root, recursive=recursive)
            cache = load_cache(self.csv_path)
            counters = {'images': len(paths), 'cache_hit': 0, 'analyzed_now': 0,
                        'updated': 0, 'unchanged': 0, 'skipped_tagged': 0,
                        'skipped_no_caption': 0, 'analysis_error': 0, 'write_error': 0}
            candidates, missing = [], []
            for idx, rel in enumerate(paths):
                image = os.path.join(root, *rel.split('/'))
                try:
                    old = read_caption(image)
                except FileNotFoundError:
                    if not create_missing:
                        counters['skipped_no_caption'] += 1
                        continue
                    old = ''
                except (OSError, UnicodeError):
                    counters['write_error'] += 1
                    continue
                if on_existing == 'skip' and tag_status(old) == 'complete':
                    counters['skipped_tagged'] += 1
                    continue
                try:
                    st = os.stat(image)
                except OSError:
                    counters['analysis_error'] += 1
                    continue
                row = cache.get(rel)
                if row and int(row['size']) == st.st_size and int(row['mtime_ns']) == st.st_mtime_ns:
                    if row.get('status') == 'ok':
                        counters['cache_hit'] += 1
                else:
                    missing.append(rel)
                    row = None
                candidates.append((idx, rel, image, old, row))
                if progress_cb: progress_cb(idx + 1, len(paths), rel)

            analyzed = []
            if missing:
                with multiprocessing.Pool(max(1, workers)) as pool:
                    analyzed = pool.map(_stat_row, [(root, rel, resolution) for rel in missing], chunksize=16)
                for row in analyzed:
                    row['in_calib'] = 0
                    cache[row['path']] = row
                counters['analyzed_now'] = len(analyzed)
            rows_by_path = {r['path']: r for r in analyzed}
            for idx, rel, image, old, row in candidates:
                if row is None:
                    row = rows_by_path.get(rel)
                if row is None:
                    counters['analysis_error'] += 1
                    continue
                if row['status'] != 'ok':
                    counters['analysis_error'] += 1
                    continue
                tag = format_tag(row, {a: config['thresholds'][a]['boundaries'] for a in ATTRS})
                new = build_caption(old, tag)
                if new == old:
                    counters['unchanged'] += 1
                elif not dry_run:
                    try:
                        write_caption(image, new, backup_dir)
                        counters['updated'] += 1
                    except (OSError, UnicodeError): counters['write_error'] += 1
            _append_cache(self.csv_path, analyzed)
            if analyzed: compact_cache(self.csv_path)
            config['last_tagging'] = {'at': datetime.now().isoformat(timespec='seconds'),
                                      'thresholds_hash': config.get('thresholds_hash')}
            self._save_config(config)
            counters['rows'] = []
            for rel in paths:
                row = cache.get(rel)
                try:
                    st = os.stat(os.path.join(root, *rel.split('/')))
                    if row and int(row['size']) == st.st_size and int(row['mtime_ns']) == st.st_mtime_ns:
                        counters['rows'].append(row)
                        continue
                except (OSError, KeyError, ValueError):
                    pass
                counters['rows'].append(
                    {'path': rel, 'status': 'not_analyzed', 'error': '', 'width': 0, 'height': 0,
                     **{a: '' for a in ATTRS}}
                )
            counters['thresholds'] = config['thresholds']
            return counters
        finally:
            try: os.unlink(lock)
            except OSError: pass

    def remove_tags(self, root: str, recursive: bool = True, dry_run: bool = False,
                    backup_dir: str | None = None) -> dict:
        # Deliberately does not access calibration or cache files.
        files = list_captions(root)
        if not recursive:
            files = [p for p in files if os.path.dirname(p) == os.path.abspath(root)]
        report = {'files': len(files), 'removed': 0, 'partial_removed': 0,
                  'no_tag': 0, 'write_error': 0}
        for path in files:
            try:
                with open(path, 'r', encoding='utf-8', newline='') as f: old = f.read()
                body, found = split_tag(old)
                if not found:
                    report['no_tag'] += 1
                elif not dry_run:
                    partial = len(found) < len(ATTRS)
                    ending = '\r\n' if old.endswith('\r\n') else ('\n' if old.endswith('\n') else ('\r' if old.endswith('\r') else ''))
                    body = body.rstrip() + ending
                    # atomic text write for caption paths that do not necessarily have image companions
                    fd, tmp = tempfile.mkstemp(prefix='.caption_', dir=os.path.dirname(path))
                    try:
                        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f: f.write(body)
                        if backup_dir:
                            rel = os.path.relpath(path, root); backup = os.path.join(backup_dir, rel)
                            os.makedirs(os.path.dirname(backup), exist_ok=True)
                            if not os.path.exists(backup): shutil.copy2(path, backup)
                        os.replace(tmp, path)
                    finally:
                        if os.path.exists(tmp): os.unlink(tmp)
                    report['removed'] += 1
                    if partial: report['partial_removed'] += 1
            except (OSError, UnicodeError): report['write_error'] += 1
        return report

    def set_manual_thresholds(self, attr: str, boundaries: list[float]) -> None:
        if attr not in ATTRS or len(boundaries) != 4:
            raise ValueError('속성과 경계값 4개를 확인하세요.')
        values = [float(v) for v in boundaries]
        limits = {'brightness': (0, 1), 'saturation': (0, 1), 'contrast': (0, .5),
                  'color_temperature': (-1, 1), 'complexity': (0, 1)}[attr]
        if not all(limits[0] <= v <= limits[1] for v in values) or any(a >= b for a, b in zip(values, values[1:])):
            raise ValueError('경계값은 속성 범위 내에서 엄격히 오름차순이어야 합니다.')
        config = self._load_config()
        config.setdefault('thresholds', {})[attr] = {'method': 'manual', 'boundaries': values}
        config['thresholds_hash'] = hashlib.sha1(json.dumps(config['thresholds'], sort_keys=True).encode()).hexdigest()
        self._save_config(config)

    def recalibrate(self, percentiles: list[float] | None = None) -> dict:
        """Recompute automatic boundaries and statistics from the existing cache."""
        config = self._load_config()
        cache = load_cache(self.csv_path)
        percentiles = percentiles or [5, 25, 75, 95]
        if len(percentiles) != 4 or any(not 0 <= float(p) <= 100 for p in percentiles) or any(float(a) >= float(b) for a, b in zip(percentiles, percentiles[1:])):
            raise ValueError('백분위는 0~100 범위에서 오름차순 4개여야 합니다.')
        values = {a: [] for a in ATTRS}
        for row in cache.values():
            if row.get('status') == 'ok' and str(row.get('in_calib')) == '1':
                for attr in ATTRS: values[attr].append(float(row[attr]))
        stats = config.get('stats', {})
        thresholds = config.get('thresholds', {})
        for attr, vals in values.items():
            vals.sort()
            if not vals: continue
            mean = sum(vals) / len(vals)
            ps = {p: percentile(vals, p) for p in (5, 25, 50, 75, 95)}
            stats[attr] = {'count': len(vals), 'min': vals[0], 'max': vals[-1],
                           'mean': mean,
                           'std': (sum((v - mean) ** 2 for v in vals) / len(vals)) ** .5,
                           **{f'p{p:02}': v for p, v in ps.items()}}
            if thresholds.get(attr, {}).get('method') != 'manual':
                thresholds[attr] = {'method': 'auto',
                                    'boundaries': [percentile(vals, float(p)) for p in percentiles]}
        config.update(percentiles=[float(p) for p in percentiles], stats=stats, thresholds=thresholds)
        config['thresholds_hash'] = hashlib.sha1(json.dumps(
            {a: thresholds.get(a) for a in ATTRS}, sort_keys=True).encode()).hexdigest()
        self._save_config(config)
        return {'thresholds': thresholds, 'stats': stats}

    def extend_calibration(self, root: str, new_percent: int, workers: int = 1,
                           recursive: bool = True, progress_cb=None) -> dict:
        """Add a deterministic sample from uncalibrated images to the calibration set."""
        config = self._load_config()
        resolution = config.get('metric', {}).get('resolution', {})
        cache = load_cache(self.csv_path)
        paths = scan_images(root, recursive=recursive)
        target = max(1, round(len(paths) * new_percent / 100)) if paths else 0
        current = {p for p in paths if str(cache.get(p, {}).get('in_calib', '0')) == '1'}
        remaining = [p for p in paths if p not in current]
        additional = max(0, min(len(remaining), target - len(current)))
        seed = f"{config.get('sampling', {}).get('seed', 381742)}:{new_percent}"
        chosen = set(random.Random(seed).sample(remaining, additional)) if additional else set()
        # Preserve current calibration flags while reusing the standard safe calibration pipeline.
        for rel in chosen:
            row = cache.get(rel)
            path = os.path.join(root, *rel.split('/'))
            st = os.stat(path)
            if row and int(row['size']) == st.st_size and int(row['mtime_ns']) == st.st_mtime_ns:
                row['in_calib'] = '1'
                _append_cache(self.csv_path, [row])
            else:
                result = _stat_row((root, rel, resolution)); result['in_calib'] = 1
                _append_cache(self.csv_path, [result])
        compact_cache(self.csv_path)
        return self.recalibrate(config.get('percentiles', [5, 25, 75, 95]))

    def check_dataset(self, root: str, recursive: bool = True) -> dict:
        """Report uncached current images and cached rows no longer in the dataset."""
        paths = set(scan_images(root, recursive=recursive))
        cache = load_cache(self.csv_path)
        return {'uncached': sorted(paths - set(cache)),
                'missing': sorted(set(cache) - paths)}

