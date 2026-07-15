"""
core/dataset_analyzer.py

데이터셋 분석 및 스냅샷 모듈.

담당:
  DatasetAnalyzer  — 버킷 계산, 폴더 분석, 리핏 추천, 낭비율 계산
  DatasetSnapshot  — 데이터셋 현황 수집·저장·불러오기·비교
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from PIL import Image

from utils.common import is_image_file, TEXT_EXTENSION, process_with_multicore
from utils.settings import DATA_DIR

# 대용량 이미지 처리 시 경고 방지
Image.MAX_IMAGE_PIXELS = None


# ---------------------------------------------------------------------------
# DatasetAnalyzer
# ---------------------------------------------------------------------------

class DatasetAnalyzer:
    """버킷 생성, 폴더 분석, 리핏 추천, 낭비율 계산 담당."""

    # 버킷 설정 기본값 (UI에서 변경 가능)
    DEFAULT_STEPS: int = 64
    DEFAULT_MIN:   int = 256
    DEFAULT_MAX:   int = 2048

    # ------------------------------------------------------------------
    # 버킷 계산
    # ------------------------------------------------------------------

    @staticmethod
    def make_buckets(
        target_res: int,
        min_res:    int,
        max_res:    int,
        steps:      int,
    ) -> List[Tuple[int, int]]:
        """
        kohya-ss (sd-scripts) 스타일 정밀 버킷 목록 생성.

        target_res² 면적을 유지할 수 있는 유효한 해상도 조합만 생성한다.
        """
        target_area = target_res * target_res
        buckets: set = {(target_res, target_res)}

        for w in range(min_res, max_res + 1, steps):
            h = (target_area // w // steps) * steps
            if min_res <= h <= max_res:
                buckets.add((w, h))
                buckets.add((h, w))

        return sorted(buckets, key=lambda x: x[0] / x[1])

    @staticmethod
    def get_bucket_size(
        width:      int,
        height:     int,
        steps:      int = 64,
        min_res:    int = 256,
        max_res:    int = 2048,
        target_res: int = 1024,
    ) -> Tuple[int, int]:
        """이미지 해상도에 가장 가까운 비율의 버킷을 반환한다."""
        buckets   = DatasetAnalyzer.make_buckets(target_res, min_res, max_res, steps)
        orig_ratio = width / height
        best       = min(buckets, key=lambda b: abs(orig_ratio - b[0] / b[1]))
        return best

    @staticmethod
    def rebucketize(
        dims:       List[Tuple[int, int]],
        steps:      int,
        min_res:    int,
        max_res:    int,
        target_res: int = 1024,
    ) -> Dict[str, int]:
        """이미지 해상도 리스트를 새로운 설정으로 버킷 분포를 다시 계산한다."""
        new_buckets: Dict[str, int] = defaultdict(int)
        bucket_list = DatasetAnalyzer.make_buckets(target_res, min_res, max_res, steps)
        bucket_ars  = [bw / bh for bw, bh in bucket_list]

        for w, h in dims:
            orig_ar  = w / h
            diffs    = [abs(orig_ar - b_ar) for b_ar in bucket_ars]
            best_idx = diffs.index(min(diffs))
            bw, bh   = bucket_list[best_idx]
            new_buckets[f"{bw}x{bh}"] += 1

        return dict(new_buckets)

    # ------------------------------------------------------------------
    # 폴더 분석 워커
    # ------------------------------------------------------------------

    @staticmethod
    def analyze_folder_worker(folder_info: Dict) -> Dict:
        """단일 폴더를 분석하는 멀티코어 워커 함수."""
        path             = folder_info['path']
        include_untagged = folder_info['include_untagged']
        steps            = folder_info.get('bucket_steps',  64)
        min_res          = folder_info.get('bucket_min',   256)
        max_res          = folder_info.get('bucket_max',  2048)
        target_res       = folder_info.get('target_res',  1024)

        bucket_list = DatasetAnalyzer.make_buckets(target_res, min_res, max_res, steps)
        bucket_ars  = [bw / bh for bw, bh in bucket_list]

        images_in_folder: List[Path]  = []
        buckets:          Dict[str, int] = defaultdict(int)
        image_dims:       List[Tuple[int, int]] = []
        mismatches:       List[Dict]    = []

        try:
            for entry in os.scandir(path):
                if not entry.is_file():
                    continue
                file_path = Path(entry.path)
                if not is_image_file(file_path):
                    continue
                if not include_untagged and not file_path.with_suffix(TEXT_EXTENSION).exists():
                    continue
                try:
                    with Image.open(file_path) as img:
                        w, h = img.size

                    image_dims.append((w, h))
                    orig_ar  = w / h
                    diffs    = [abs(orig_ar - b_ar) for b_ar in bucket_ars]
                    best_idx = diffs.index(min(diffs))
                    bw, bh   = bucket_list[best_idx]
                    b_ar     = bucket_ars[best_idx]

                    # 종횡비 미스매치 감지 (30% 이상 차이)
                    if abs(orig_ar - b_ar) / b_ar > 0.3:
                        mismatches.append({
                            'file_name':   file_path.name,
                            'resolution':  f"{w}x{h}",
                            'orig_ar':     round(orig_ar, 3),
                            'bucket_ar':   round(b_ar, 3),
                            'bucket_res':  f"{bw}x{bh}",
                            'folder_path': str(path),
                        })

                    buckets[f"{bw}x{bh}"] += 1
                    images_in_folder.append(file_path)
                except Exception:
                    continue
        except Exception as e:
            print(f"폴더 분석 오류 ({path}): {e}")

        return {
            'folder_name': path.name,
            'folder_path': str(path),
            'count':       len(images_in_folder),
            'buckets':     dict(buckets),
            'image_dims':  image_dims,
            'mismatches':  mismatches,
        }

    @staticmethod
    def scan_directories(
        root_path:        str,
        recursive:        bool,
        include_empty:    bool,
        include_untagged: bool,
        num_cores:        int        = 1,
        bucket_settings:  Optional[Dict] = None,
    ) -> List[Dict]:
        """루트 경로 아래의 폴더들을 분석해 결과 목록을 반환한다."""
        root = Path(root_path)
        if not root.exists():
            return []

        def is_leaf_dir(p: Path) -> bool:
            try:
                return not any(e.is_dir() for e in os.scandir(p))
            except Exception:
                return True

        target_folders: List[Path] = []
        if recursive:
            for p in root.rglob('*'):
                if not p.is_dir():
                    continue
                if include_empty:
                    if is_leaf_dir(p):
                        target_folders.append(p)
                else:
                    target_folders.append(p)
        else:
            target_folders.append(root)
            if include_empty:
                for p in root.iterdir():
                    if p.is_dir() and is_leaf_dir(p):
                        target_folders.append(p)

        target_folders = sorted(set(target_folders))

        worker_input = [
            {'path': p, 'include_untagged': include_untagged, **(bucket_settings or {})}
            for p in target_folders
        ]

        results = process_with_multicore(
            DatasetAnalyzer.analyze_folder_worker,
            worker_input,
            num_cores,
        )

        if not include_empty:
            results = [r for r in results if r['count'] > 0]

        return results

    # ------------------------------------------------------------------
    # 리핏 추천 / 낭비율 계산
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_recommend_repeats(
        folders:     List[Dict],
        batch_total: int,
    ) -> List[int]:
        """C+B 혼합 방식: 전체 폴더의 스텝 균형을 잡은 뒤 낭비율을 최소화하는 리핏을 산출한다."""
        if not folders:
            return []
        if batch_total <= 0:
            return [1] * len(folders)

        # 1단계: 리핏 1 기준 각 폴더의 기본 스텝 계산
        base_steps = [
            DatasetAnalyzer.calculate_waste(f['buckets'], 1, batch_total)[2]
            for f in folders
        ]

        # 2단계: 목표 스텝 (유효값의 중앙값)
        valid_steps = [s for s in base_steps if s > 0]
        if not valid_steps:
            return [1] * len(folders)
        target_step = statistics.median(valid_steps)

        results: List[int] = []
        for i, f in enumerate(folders):
            bs = base_steps[i]
            if bs <= 0:
                results.append(1)
                continue

            approx_r    = target_step / bs
            target_r    = max(1, round(approx_r))
            search_range = max(2, int(target_r * 0.25))
            start_r     = max(1, target_r - search_range)
            end_r       = target_r + search_range

            best_r   = target_r
            min_waste = float('inf')

            for r in range(start_r, end_r + 1):
                _, waste_rate, _ = DatasetAnalyzer.calculate_waste(f['buckets'], r, batch_total)
                if waste_rate < min_waste:
                    min_waste = waste_rate
                    best_r    = r
                elif abs(waste_rate - min_waste) < 1e-7:
                    if abs(r - target_r) < abs(best_r - target_r):
                        best_r = r

            results.append(best_r)

        return results

    @staticmethod
    def calculate_waste(
        count_per_bucket: Dict[str, int],
        repeat:           int,
        batch_total:      int,
    ) -> Tuple[int, float, int]:
        """
        낭비 슬롯 수, 낭비율(%), 총 스텝 수를 계산한다.

        Returns:
            (waste_slots, waste_rate, total_steps)
        """
        total_slots = 0
        waste_slots = 0
        total_steps = 0

        for count in count_per_bucket.values():
            bucket_total = count * repeat
            remainder    = bucket_total % batch_total
            steps        = (bucket_total + batch_total - 1) // batch_total
            total_steps += steps
            if remainder > 0:
                waste_slots += batch_total - remainder
            total_slots += steps * batch_total

        waste_rate = (waste_slots / total_slots * 100) if total_slots > 0 else 0.0
        return waste_slots, waste_rate, total_steps

    @staticmethod
    def calculate_theoretical_steps(
        count:       int,
        repeat:      int,
        batch_total: int,
    ) -> float:
        """단순 공식: (데이터 수 × 리핏) / 배치."""
        if batch_total == 0:
            return 0.0
        return (count * repeat) / batch_total


# ---------------------------------------------------------------------------
# DatasetSnapshot
# ---------------------------------------------------------------------------

class DatasetSnapshot:
    """데이터셋 스냅샷 수집·저장·불러오기·비교 기능을 담당하는 정적 유틸리티 클래스."""

    FORMAT_VERSION: str = '1.0'

    # 지원 이미지 확장자
    _IMAGE_EXTS: frozenset[str] = frozenset({
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff',
    })

    # ------------------------------------------------------------------
    # 경로 헬퍼
    # ------------------------------------------------------------------

    @staticmethod
    def get_snapshot_dir() -> Path:
        """
        스냅샷 저장 폴더 경로를 반환한다.
        utils.settings.DATA_DIR 기준으로 통일 (exe 패키징 자동 대응).
        """
        return DATA_DIR / 'snapshots'

    # ------------------------------------------------------------------
    # 포맷 유틸
    # ------------------------------------------------------------------

    @staticmethod
    def format_size(size_bytes: int) -> str:
        """바이트 수를 사람이 읽기 쉬운 단위로 변환한다."""
        if size_bytes == 0:
            return '0 B'
        val = float(size_bytes)
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if val < 1024.0:
                return f"{val:.1f} {unit}"
            val /= 1024.0
        return f"{val:.1f} PB"

    @staticmethod
    def _is_image(path: Path) -> bool:
        return path.suffix.lower() in DatasetSnapshot._IMAGE_EXTS

    @staticmethod
    def _is_leaf(p: Path) -> bool:
        try:
            return not any(x.is_dir() for x in p.iterdir())
        except Exception:
            return True

    # ------------------------------------------------------------------
    # 데이터 수집
    # ------------------------------------------------------------------

    @staticmethod
    def collect(root_path: str) -> Optional[dict]:
        """
        지정된 루트 폴더의 구조와 데이터셋 현황을 수집한다.

        Returns:
            메타데이터 딕셔너리 (save() 에 바로 전달 가능). 경로 오류 시 None.
        """
        root = Path(root_path)
        if not root.exists():
            return None

        leaf_folders: List[dict] = []

        def scan_dir(p: Path) -> None:
            if DatasetSnapshot._is_leaf(p):
                images: List[str] = []
                pairs   = 0
                size    = 0
                try:
                    for f in p.iterdir():
                        if not f.is_file():
                            continue
                        try:
                            size += f.stat().st_size
                        except OSError:
                            pass
                        if DatasetSnapshot._is_image(f):
                            images.append(f.stem)
                    pairs = sum(1 for stem in images if (p / f"{stem}.txt").exists())
                except Exception:
                    pass

                try:
                    rel = str(p.relative_to(root))
                except ValueError:
                    rel = p.name
                display_path = root.name if rel == '.' else rel

                leaf_folders.append({
                    'rel_path':    display_path,
                    'name':        p.name,
                    'image_count': len(images),
                    'pair_count':  pairs,
                    'unpaired':    len(images) - pairs,
                    'size_bytes':  size,
                })
            else:
                try:
                    for child in sorted(p.iterdir()):
                        if child.is_dir():
                            scan_dir(child)
                except Exception:
                    pass

        scan_dir(root)

        def build_tree(p: Path) -> dict:
            node: dict = {'name': p.name, 'children': []}
            try:
                for child in sorted(p.iterdir()):
                    if child.is_dir():
                        node['children'].append(build_tree(child))
            except Exception:
                pass
            return node

        total_images = sum(f['image_count'] for f in leaf_folders)
        total_pairs  = sum(f['pair_count']  for f in leaf_folders)
        total_size   = sum(f['size_bytes']  for f in leaf_folders)

        return {
            'format_version':    DatasetSnapshot.FORMAT_VERSION,
            'name':              '',
            'created_at':        datetime.datetime.now().isoformat(),
            'root_path':         str(root),
            'root_name':         root.name,
            'memo':              '',
            'total_images':      total_images,
            'total_pairs':       total_pairs,
            'total_unpaired':    total_images - total_pairs,
            'total_size_bytes':  total_size,
            'leaf_folder_count': len(leaf_folders),
            'leaf_folders':      leaf_folders,
            'folder_tree':       build_tree(root),
        }

    # ------------------------------------------------------------------
    # 저장 / 불러오기
    # ------------------------------------------------------------------

    @staticmethod
    def save(data: dict, name: str, memo: str = '') -> Path:
        """
        스냅샷 데이터를 JSON 파일로 저장한다.

        Returns:
            저장된 파일의 Path
        """
        snapshot_dir = DatasetSnapshot.get_snapshot_dir()
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        data         = {**data, 'name': name, 'memo': memo}
        safe_name    = ''.join(
            c if (c.isalnum() or c in ('-', '_', ' ')) else '_'
            for c in name
        ).strip().replace(' ', '_') or 'snapshot'

        ts       = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filepath = snapshot_dir / f"{safe_name}_{ts}.json"

        counter = 1
        while filepath.exists():
            filepath = snapshot_dir / f"{safe_name}_{ts}_{counter}.json"
            counter += 1

        with filepath.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return filepath

    @staticmethod
    def load(filepath: str) -> dict:
        """JSON 스냅샷 파일을 딕셔너리로 불러온다."""
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    @staticmethod
    def list_snapshots() -> List[Tuple[str, str]]:
        """
        저장된 스냅샷 목록을 최신순으로 반환한다.

        Returns:
            [(표시 이름, 절대 파일 경로), ...]
        """
        snapshot_dir = DatasetSnapshot.get_snapshot_dir()
        if not snapshot_dir.exists():
            return []

        result: List[Tuple[str, str]] = []
        for f in sorted(snapshot_dir.glob('*.json'), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                with f.open('r', encoding='utf-8') as fp:
                    meta    = json.load(fp)
                dt_str  = meta.get('created_at', '')[:16].replace('T', ' ')
                display = f"[{dt_str}]  {meta.get('name', f.stem)}"
                result.append((display, str(f)))
            except Exception:
                result.append((f.stem, str(f)))

        return result

    # ------------------------------------------------------------------
    # 비교
    # ------------------------------------------------------------------

    @staticmethod
    def compare(base: dict, comp: dict) -> dict:
        """
        두 스냅샷을 비교해 차이점 딕셔너리를 반환한다.

        비교 전략:
          1. rel_path 완전 일치 → 정확 매칭
          2. 폴더 이름 일치 → 퍼지 매칭 (경로 변경 감지)
          3. 나머지 → 추가/삭제 처리

        Returns:
            {'added', 'removed', 'changed', 'fuzzy_matched', 'unchanged_count', 'summary'}
        """
        base_map: Dict[str, dict] = {f['rel_path']: f for f in base.get('leaf_folders', [])}
        comp_map: Dict[str, dict] = {f['rel_path']: f for f in comp.get('leaf_folders', [])}

        base_keys    = set(base_map)
        comp_keys    = set(comp_map)
        exact_common = base_keys & comp_keys
        base_only    = base_keys - comp_keys
        comp_only    = comp_keys - base_keys

        # 퍼지 매칭 (폴더 이름 기준)
        comp_name_map: Dict[str, List[str]] = defaultdict(list)
        for ck in comp_only:
            comp_name_map[comp_map[ck]['name']].append(ck)

        fuzzy_matched: List[dict] = []
        matched_base:  Set[str]   = set()
        matched_comp:  Set[str]   = set()

        for bk in sorted(base_only):
            bname      = base_map[bk]['name']
            candidates = [ck for ck in comp_name_map.get(bname, []) if ck not in matched_comp]
            if candidates:
                ck = candidates[0]
                b, c = base_map[bk], comp_map[ck]
                fuzzy_matched.append({
                    'base_path':    bk,
                    'comp_path':    ck,
                    'base':         b,
                    'comp':         c,
                    'delta_images': c['image_count'] - b['image_count'],
                    'delta_pairs':  c['pair_count']  - b['pair_count'],
                    'delta_size':   c['size_bytes']  - b['size_bytes'],
                })
                matched_base.add(bk)
                matched_comp.add(ck)

        removed = [{'path': k, **base_map[k]} for k in base_only if k not in matched_base]
        added   = [{'path': k, **comp_map[k]} for k in comp_only if k not in matched_comp]

        changed:         List[dict] = []
        unchanged_count: int        = 0
        for k in exact_common:
            b, c = base_map[k], comp_map[k]
            if b['image_count'] != c['image_count'] or b['size_bytes'] != c['size_bytes']:
                changed.append({
                    'path':         k,
                    'base':         b,
                    'comp':         c,
                    'delta_images': c['image_count'] - b['image_count'],
                    'delta_pairs':  c['pair_count']  - b['pair_count'],
                    'delta_size':   c['size_bytes']  - b['size_bytes'],
                })
            else:
                unchanged_count += 1

        b_img  = base.get('total_images',     0)
        c_img  = comp.get('total_images',     0)
        b_pair = base.get('total_pairs',      0)
        c_pair = comp.get('total_pairs',      0)
        b_sz   = base.get('total_size_bytes', 0)
        c_sz   = comp.get('total_size_bytes', 0)

        d_img  = c_img  - b_img
        d_pair = c_pair - b_pair
        d_sz   = c_sz   - b_sz

        return {
            'added':           added,
            'removed':         removed,
            'changed':         changed,
            'fuzzy_matched':   fuzzy_matched,
            'unchanged_count': unchanged_count,
            'summary': {
                'delta_images':    d_img,
                'delta_pairs':     d_pair,
                'delta_size':      d_sz,
                'rate_images':     (d_img / b_img * 100) if b_img > 0 else 0.0,
                'rate_size':       (d_sz  / b_sz  * 100) if b_sz  > 0 else 0.0,
                'added_count':     len(added),
                'removed_count':   len(removed),
                'changed_count':   len(changed),
                'fuzzy_count':     len(fuzzy_matched),
                'unchanged_count': unchanged_count,
            },
        }


# ---------------------------------------------------------------------------
# BatchMover — 데이터셋 폴더 일괄 이동/복사
# ---------------------------------------------------------------------------

def format_size_generic(n: float) -> str:
    """범용 바이트 수 → 사람이 읽기 쉬운 단위 변환. (DatasetSnapshot.format_size와 별개로,
    소수점 표시 자릿수가 다른 호출부(BatchMover)를 위해 별도 함수로 둔다.)"""
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class BatchMover:
    """
    데이터셋 폴더를 다른 위치로 일괄 이동/복사하는 로직.

    UI(ui/tabs/analyzer_tab.py의 _BatchMoveDialog)는 이 클래스의 정적 메서드를
    호출하기만 하고, 실제 파일시스템 조작은 여기서 전담한다.
    """

    @staticmethod
    def folder_size(path: str) -> int:
        """폴더 내 전체 파일 용량(bytes)을 합산한다."""
        total = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
        return total

    @staticmethod
    def count_files(path: str) -> int:
        """폴더 내 전체 파일 수를 센다."""
        total = 0
        for _, _, files in os.walk(path):
            total += len(files)
        return total

    @staticmethod
    def _resolve_file_conflict(src_file: str, dest_file: str, tgt_dir: str, op: str) -> tuple[str, str]:
        """
        이름이 겹치는 파일을 _000001 스타일 번호로 증가시켜 배치한다.
        같은 stem의 .txt 캡션 파일도 함께 처리한다.

        Returns:
            (new_dest_path, new_dest_txt_path)
        """
        stem, ext = os.path.splitext(os.path.basename(src_file))
        # 이미 번호가 붙어있으면 분리 (_123456 형태)
        m = re.match(r'^(.+?)(_\d{6})$', stem)
        base_stem = m.group(1) if m else stem

        # 대상 폴더 내 기존 번호들 수집
        existing: set[int] = set()
        for f in os.listdir(tgt_dir):
            fn, _fe = os.path.splitext(f)
            mm = re.match(r'^(.+?)(_\d{6})$', fn)
            if mm and mm.group(1) == base_stem:
                existing.add(int(mm.group(2)[1:]))  # '_000123' -> 123

        new_num  = (max(existing) + 1) if existing else 1
        new_stem = f"{base_stem}_{new_num:06d}"
        new_dest = os.path.join(tgt_dir, f"{new_stem}{ext}")

        if op == 'copy':
            shutil.copy2(src_file, new_dest)
        else:
            shutil.move(src_file, new_dest)

        # txt 캡션 파일 동반 처리
        src_txt      = os.path.join(os.path.dirname(src_file), f"{stem}.txt")
        new_dest_txt = os.path.join(tgt_dir, f"{new_stem}.txt")
        if os.path.isfile(src_txt):
            if op == 'copy':
                shutil.copy2(src_txt, new_dest_txt)
            else:
                shutil.move(src_txt, new_dest_txt)

        return new_dest, new_dest_txt

    @staticmethod
    def process_one_folder(
        src_path: str,
        dest_path: str,
        dest_name: str,
        dest_root: str,
        op: str,
        dup: str,
    ) -> None:
        """
        폴더 하나에 대해 이동/복사 및 중복 처리(건너뛰기/숫자추가/합치기)를 수행한다.

        Args:
            src_path:  원본 폴더 경로
            dest_path: 목적지 경로 (충돌이 없을 때 그대로 사용)
            dest_name: 목적지 폴더명 (숫자추가 모드에서 베이스 이름으로 사용)
            dest_root: 목적지 루트 폴더 (숫자추가 모드에서 후보 경로 생성에 사용)
            op:        'move' | 'copy'
            dup:       'skip' | 'number' | 'merge'
        """
        if not os.path.exists(dest_path):
            # 중복 없음 — 그냥 이동/복사
            if op == 'copy':
                shutil.copytree(src_path, dest_path)
            else:
                shutil.move(src_path, dest_path)
            return

        # ── 중복 처리 ─────────────────────────────────────────
        if dup == 'skip':
            return  # 건너뛰기

        if dup == 'number':
            # '폴더명|N' 형태로 증가시켜 빈 이름을 찾음
            n = 1
            while True:
                candidate = os.path.join(dest_root, f"{dest_name}|{n}")
                if not os.path.exists(candidate):
                    if op == 'copy':
                        shutil.copytree(src_path, candidate)
                    else:
                        shutil.move(src_path, candidate)
                    return
                n += 1

        if dup == 'merge':
            # 파일 하나씩 병합
            for dirpath, _dirnames, filenames in os.walk(src_path):
                rel     = os.path.relpath(dirpath, src_path)
                tgt_dir = os.path.join(dest_path, rel) if rel != '.' else dest_path
                os.makedirs(tgt_dir, exist_ok=True)

                for fname in filenames:
                    src_file  = os.path.join(dirpath, fname)
                    dest_file = os.path.join(tgt_dir, fname)

                    if not os.path.exists(dest_file):
                        if op == 'copy':
                            shutil.copy2(src_file, dest_file)
                        else:
                            shutil.move(src_file, dest_file)
                    else:
                        # 파일명 충돌 -> 번호 증가 (_000001 스타일, 태그 txt 동반)
                        BatchMover._resolve_file_conflict(src_file, dest_file, tgt_dir, op)

            # 이동 모드에서 원본 빈 폴더 정리
            if op == 'move':
                try:
                    shutil.rmtree(src_path)
                except Exception:
                    pass
