"""
core/duplicate_finder.py

이미지 중복 탐지 모듈.

지원 탐지 방식:
  - MD5 해시  : 완전 동일 파일
  - dHash     : 시각적 유사도 (퍼셉추얼 해시)
  - 태그 유사도: Jaccard Similarity 기반 태그 집합 비교
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import os
import threading
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from PIL import Image


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff',
})


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------

class ImageInfo:
    """이미지 파일 메타데이터 컨테이너."""

    __slots__ = ('path', 'size', 'resolution', 'md5_val', 'dhash_val', 'tag_set')

    def __init__(self, path: str) -> None:
        self.path:       str                    = path
        self.size:       int                    = os.path.getsize(path)
        self.resolution: Tuple[int, int]        = (0, 0)
        self.md5_val:    Optional[str]          = None
        self.dhash_val:  Optional[int]          = None
        self.tag_set:    Optional[Set[str]]     = None


class UnionFind:
    """그룹핑을 위한 경로 압축 Union-Find 자료구조."""

    def __init__(self, elements) -> None:
        self.parent = {e: e for e in elements}

    def find(self, k):
        if self.parent[k] != k:
            self.parent[k] = self.find(self.parent[k])
        return self.parent[k]

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


# ---------------------------------------------------------------------------
# 병렬 처리 워커 함수 (모듈 최상위 정의 — pickle 가능)
# ---------------------------------------------------------------------------

def process_image_meta(path: str) -> Tuple[str, Tuple[int, int]]:
    """해상도 정보만 빠르게 읽는 워커."""
    try:
        with Image.open(path) as img:
            return path, img.size
    except Exception:
        return path, (0, 0)


def read_tags_worker(path: str) -> Tuple[str, Set[str]]:
    """이미지 경로에 대응하는 .txt 파일의 태그 집합을 읽는 워커."""
    try:
        txt_path = os.path.splitext(path)[0] + '.txt'
        if os.path.exists(txt_path):
            with open(txt_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            tags = {t.strip().lower() for t in content.split(',') if t.strip()}
            return path, tags
    except Exception:
        pass
    return path, set()


def compute_md5_worker(path: str) -> Tuple[str, str]:
    """MD5 해시를 계산하는 워커."""
    hasher = hashlib.md5()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                hasher.update(chunk)
        return path, hasher.hexdigest()
    except Exception:
        return path, ''


def compute_dhash_worker(path: str, hash_size: int = 8) -> Tuple[str, Optional[int]]:
    """
    dHash(차분 해시)를 정수형으로 계산하는 워커.

    Args:
        path:      이미지 파일 경로
        hash_size: 해시 크기 (hash_size × hash_size 비트)

    Returns:
        (path, hash_int) — 실패 시 hash_int 는 None
    """
    try:
        with Image.open(path) as img:
            img   = img.convert('L')
            img   = img.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
            pixels = list(img.getdata())

        diff      = 0
        width     = hash_size + 1
        bit_index = 0

        for row in range(hash_size):
            for col in range(hash_size):
                if pixels[row * width + col] > pixels[row * width + col + 1]:
                    diff |= (1 << bit_index)
                bit_index += 1

        return path, diff
    except Exception:
        return path, None


# ---------------------------------------------------------------------------
# 메인 클래스
# ---------------------------------------------------------------------------

class DuplicateFinder:
    """이미지 중복/유사 파일 탐색기."""

    def __init__(self) -> None:
        self.stop_event  = threading.Event()
        # I/O 바운드 + PIL/hashlib은 GIL 해제 → 스레드 풀이 효과적
        self.max_workers = min(32, (os.cpu_count() or 1) * 4)

    # ------------------------------------------------------------------
    # 파일 스캔
    # ------------------------------------------------------------------

    def scan_files(self, folder_path: str, recursive: bool = True) -> List[str]:
        """지정 폴더에서 이미지 파일 경로 목록을 수집한다."""
        image_files: List[str] = []

        if recursive:
            for root, _, files in os.walk(folder_path):
                if self.stop_event.is_set():
                    break
                for f in files:
                    if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                        image_files.append(os.path.join(root, f))
        else:
            for f in os.listdir(folder_path):
                if self.stop_event.is_set():
                    break
                full = os.path.join(folder_path, f)
                if os.path.isfile(full) and os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                    image_files.append(full)

        return image_files

    # ------------------------------------------------------------------
    # 중복 탐지
    # ------------------------------------------------------------------

    def find_duplicates(
        self,
        folder_path: str,
        check_md5:               bool                        = False,
        check_dhash:             bool                        = False,
        check_tag:               bool                        = False,
        match_resolution:        bool                        = True,
        similarity_threshold:    int                         = 5,
        tag_similarity_threshold: int                        = 100,
        progress_callback=None,
        max_workers:             Optional[int]               = None,
        range_threshold:         Optional[Tuple[int, int]]   = None,
    ) -> Dict[str, Any]:
        """
        이미지 중복/유사 파일을 탐지한다.

        Args:
            folder_path:               검색 루트 폴더
            check_md5:                 MD5 완전 일치 탐지 여부
            check_dhash:               dHash 시각적 유사도 탐지 여부
            check_tag:                 태그 유사도 탐지 여부
            match_resolution:          True 이면 해상도 비율이 같은 파일끼리만 비교
            similarity_threshold:      dHash 허용 거리 (0=완전일치, 클수록 유사)
            tag_similarity_threshold:  태그 Jaccard 유사도 임계값 (0~100%)
            progress_callback:         (done, total, status_msg) 콜백
            max_workers:               스레드 수 (None 이면 자동)
            range_threshold:           (start, end) 설정 시 범위 검색 모드

        Returns:
            일반 모드: {'group_N': {'type': 'exact'|'similar', 'items': [ImageInfo, ...]}}
            범위 모드: {'mode': 'range', 'md5': {...}, 'dhash': {threshold: {...}}}
        """
        self.stop_event.clear()
        workers = max_workers or self.max_workers

        # ── 1. 파일 스캔 ──────────────────────────────────────
        files = self.scan_files(folder_path, recursive=True)
        if not files:
            return {}

        total = len(files)

        # ── 2. 해상도 병렬 로드 ───────────────────────────────
        if progress_callback:
            progress_callback(0, total, '파일 정보 읽는 중...')

        infos_map: Dict[str, ImageInfo] = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(process_image_meta, f): f for f in files}
            done = 0
            for fut in concurrent.futures.as_completed(futs):
                if self.stop_event.is_set():
                    break
                path, size = fut.result()
                if size != (0, 0):
                    info            = ImageInfo(path)
                    info.resolution = size
                    infos_map[path] = info
                done += 1
                if progress_callback and done % 50 == 0:
                    progress_callback(done, total, '파일 정보 읽는 중...')

        if self.stop_event.is_set():
            return {}

        image_infos = list(infos_map.values())

        # ── 3. 종횡비 기준 1차 그룹화 ────────────────────────
        potential_groups: Dict = defaultdict(list)
        if match_resolution:
            for info in image_infos:
                w, h = info.resolution
                ratio = round(w / h, 2) if h else 0
                potential_groups[ratio].append(info)
        else:
            potential_groups['all'] = image_infos

        # ── 4-1. MD5 계산 ─────────────────────────────────────
        if check_md5:
            targets = [
                info.path
                for group in potential_groups.values()
                if len(group) >= 2
                for info in group
            ]
            if targets:
                if progress_callback:
                    progress_callback(0, len(targets), '완전 중복(MD5) 계산 중...')
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    futs = {ex.submit(compute_md5_worker, p): p for p in targets}
                    done = 0
                    for fut in concurrent.futures.as_completed(futs):
                        if self.stop_event.is_set():
                            break
                        path, md5 = fut.result()
                        if path in infos_map:
                            infos_map[path].md5_val = md5
                        done += 1
                        if progress_callback and done % 50 == 0:
                            progress_callback(done, len(targets), '완전 중복(MD5) 계산 중...')

        # ── 4-2. 태그 읽기 ────────────────────────────────────
        if check_tag and not self.stop_event.is_set():
            targets = [
                info.path
                for group in potential_groups.values()
                if len(group) >= 2
                for info in group
            ]
            if targets:
                if progress_callback:
                    progress_callback(0, len(targets), '태그 정보 읽는 중...')
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    futs = {ex.submit(read_tags_worker, p): p for p in targets}
                    done = 0
                    for fut in concurrent.futures.as_completed(futs):
                        if self.stop_event.is_set():
                            break
                        path, tags = fut.result()
                        if path in infos_map:
                            infos_map[path].tag_set = tags
                        done += 1
                        if progress_callback and done % 50 == 0:
                            progress_callback(done, len(targets), '태그 정보 읽는 중...')

        # ── 4-3. dHash 계산 ───────────────────────────────────
        if check_dhash and not self.stop_event.is_set():
            targets = [
                info.path
                for group in potential_groups.values()
                if len(group) >= 2
                for info in group
            ]
            if targets:
                if progress_callback:
                    progress_callback(0, len(targets), '유사도(dHash) 계산 중...')
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    futs = {ex.submit(compute_dhash_worker, p): p for p in targets}
                    done = 0
                    for fut in concurrent.futures.as_completed(futs):
                        if self.stop_event.is_set():
                            break
                        path, dhash = fut.result()
                        if path in infos_map:
                            infos_map[path].dhash_val = dhash
                        done += 1
                        if progress_callback and done % 50 == 0:
                            progress_callback(done, len(targets), '유사도(dHash) 계산 중...')

        if self.stop_event.is_set():
            return {}

        # ── 5. 간선 수집 ──────────────────────────────────────
        if progress_callback:
            progress_callback(0, 0, '비교 분석 중...')

        md5_edges:   List[Tuple]              = []
        tag_edges:   List[Tuple]              = []
        dhash_edges: List[Tuple[Any, Any, int]] = []  # (u, v, dist)

        for group in potential_groups.values():
            if len(group) < 2:
                continue

            # MD5
            if check_md5:
                md5_map: Dict[str, List] = defaultdict(list)
                for info in group:
                    if info.md5_val:
                        md5_map[info.md5_val].append(info)
                for items in md5_map.values():
                    for i in range(len(items) - 1):
                        md5_edges.append((items[i], items[i + 1]))

            # Tag / dHash (N² 루프)
            if check_tag or check_dhash:
                n = len(group)
                for i in range(n):
                    for j in range(i + 1, n):
                        u, v = group[i], group[j]

                        if check_tag and u.tag_set and v.tag_set:
                            inter = len(u.tag_set & v.tag_set)
                            union = len(u.tag_set | v.tag_set)
                            if union > 0 and (inter / union) * 100 >= tag_similarity_threshold:
                                tag_edges.append((u, v))

                        if check_dhash and u.dhash_val is not None and v.dhash_val is not None:
                            dist  = (u.dhash_val ^ v.dhash_val).bit_count()
                            limit = range_threshold[1] if range_threshold else similarity_threshold
                            if dist <= limit:
                                dhash_edges.append((u, v, dist))

        # ── 6. Union-Find 그룹핑 헬퍼 ────────────────────────
        all_nodes = set(image_infos)

        def build_groups(nodes, edges) -> Dict:
            if not edges:
                return {}
            uf = UnionFind(nodes)
            for u, v in edges:
                uf.union(u, v)
            groups: Dict = defaultdict(list)
            for node in nodes:
                groups[uf.find(node)].append(node)
            result, counter = {}, 0
            for items in groups.values():
                if len(items) > 1:
                    result[f'group_{counter}'] = {'type': 'similar', 'items': items}
                    counter += 1
            return result

        # ── 7. 결과 반환 ──────────────────────────────────────

        # 일반 모드
        if not range_threshold:
            active_edges = (
                md5_edges
                + tag_edges
                + [(u, v) for u, v, _ in dhash_edges]
            )
            final_groups = build_groups(all_nodes, active_edges)
            result_type  = 'exact' if (check_md5 and not check_dhash and not check_tag) else 'similar'
            for val in final_groups.values():
                val['type'] = result_type
            return final_groups

        # 범위 검색 모드
        start_th, end_th = range_threshold
        md5_only = build_groups(all_nodes, md5_edges)
        for v in md5_only.values():
            v['type'] = 'exact'

        range_results: Dict[int, Dict] = {}
        for th in range(start_th, end_th + 1):
            current_dhash = [(u, v) for u, v, d in dhash_edges if d <= th]
            combined      = current_dhash + tag_edges + md5_edges
            if not combined:
                continue
            th_groups = build_groups(all_nodes, combined)
            if th_groups:
                range_results[th] = th_groups

        return {'mode': 'range', 'md5': md5_only, 'dhash': range_results}

    # ------------------------------------------------------------------
    # 중단
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """진행 중인 탐색을 중단한다."""
        self.stop_event.set()
