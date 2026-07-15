"""
core/search_filter.py

검색 및 분류 모듈 — 데이터셋 파일을 조건별로 검색하고 처리하는 로직.
"""

from __future__ import annotations

import concurrent.futures
import shutil
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image

from utils.common import IMAGE_EXTENSIONS, TEXT_EXTENSION, is_image_file, is_text_file


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------

class FileEntry:
    """검색 결과의 단일 항목 (이미지 + 텍스트 쌍 또는 한쪽만)."""

    def __init__(
        self,
        image_path: Optional[Path],
        txt_path: Optional[Path],
    ) -> None:
        self.image_path = image_path
        self.txt_path   = txt_path

    @property
    def display_name(self) -> str:
        base = self.image_path or self.txt_path
        return base.name if base else ''

    @property
    def stem(self) -> str:
        base = self.image_path or self.txt_path
        return base.stem if base else ''

    @property
    def folder(self) -> str:
        base = self.image_path or self.txt_path
        return str(base.parent) if base else ''

    @property
    def image_ext(self) -> str:
        return self.image_path.suffix.lower() if self.image_path else ''

    @property
    def file_size_bytes(self) -> int:
        target = self.image_path or self.txt_path
        try:
            return target.stat().st_size if target and target.exists() else 0
        except Exception:
            return 0

    @property
    def file_size_kb(self) -> float:
        return round(self.file_size_bytes / 1024, 1)

    @property
    def resolution(self) -> Optional[Tuple[int, int]]:
        if not self.image_path or not self.image_path.exists():
            return None
        try:
            with Image.open(self.image_path) as img:
                return img.size
        except Exception:
            return None

    @property
    def tag_content(self) -> str:
        if not self.txt_path or not self.txt_path.exists():
            return ''
        try:
            return self.txt_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            return ''

    @property
    def tags(self) -> List[str]:
        content = self.tag_content
        if not content.strip():
            return []
        return [t.strip().lower() for t in content.split(',') if t.strip()]

    def has_image(self) -> bool:
        return self.image_path is not None and self.image_path.exists()

    def has_txt(self) -> bool:
        return self.txt_path is not None and self.txt_path.exists()


# ---------------------------------------------------------------------------
# 검색 조건 매칭 함수
# ---------------------------------------------------------------------------

def _parse_tag_query(query_str: str) -> List[str]:
    return [t.strip().lower() for t in query_str.split('|') if t.strip()]


def _match_filename(entry: FileEntry, pattern: str) -> bool:
    return pattern.lower() in entry.stem.lower()


def _match_size(
    entry: FileEntry,
    min_kb: Optional[float],
    max_kb: Optional[float],
) -> bool:
    size = entry.file_size_kb
    if min_kb is not None and size < min_kb:
        return False
    if max_kb is not None and size > max_kb:
        return False
    return True


def _match_resolution(
    entry: FileEntry,
    min_w: Optional[int], max_w: Optional[int],
    min_h: Optional[int], max_h: Optional[int],
) -> bool:
    res = entry.resolution
    if res is None:
        return not any(x is not None for x in (min_w, max_w, min_h, max_h))
    w, h = res
    if min_w is not None and w < min_w: return False
    if max_w is not None and w > max_w: return False
    if min_h is not None and h < min_h: return False
    if max_h is not None and h > max_h: return False
    return True


def _match_tags(entry: FileEntry, query_tags: List[str]) -> bool:
    """OR 방식 — query_tags 중 하나라도 포함되면 True."""
    if not query_tags:
        return True
    entry_tags = entry.tags
    return any(qt in entry_tags for qt in query_tags)


def _match_tags_all(entry: FileEntry, query_tags: List[str]) -> bool:
    """AND 방식 — query_tags 모두 포함되면 True."""
    if not query_tags:
        return True
    entry_tags = entry.tags
    return all(qt in entry_tags for qt in query_tags)


# ---------------------------------------------------------------------------
# 조건 평가
# ---------------------------------------------------------------------------

def _evaluate_condition(entry: FileEntry, condition: Dict) -> bool:
    """
    단일 조건을 평가한다.

    condition 구조:
      {
        'mode': 'unused' | 'and' | 'or' | 'not',
        'type': 'filename' | 'size' | 'resolution' | 'tag',
        ...  # type별 파라미터
      }
    """
    mode = condition.get('mode', 'unused')
    if mode == 'unused':
        return True

    ctype = condition.get('type')

    if ctype == 'filename':
        pattern = condition.get('pattern', '')
        if not pattern:
            return True
        matched = _match_filename(entry, pattern)

    elif ctype == 'size':
        matched = _match_size(
            entry,
            condition.get('min_kb'),
            condition.get('max_kb'),
        )

    elif ctype == 'resolution':
        matched = _match_resolution(
            entry,
            condition.get('min_w'), condition.get('max_w'),
            condition.get('min_h'), condition.get('max_h'),
        )

    elif ctype == 'tag':
        query_tags = _parse_tag_query(condition.get('query', ''))
        if not query_tags:
            return True
        matched = _match_tags(entry, query_tags) if mode == 'or' else _match_tags_all(entry, query_tags)

    else:
        return True

    return not matched if mode == 'not' else matched


def _all_conditions_unused(conditions: List[Dict]) -> bool:
    """모든 조건이 미사용 상태인지 확인한다. (참고용 헬퍼 — entry_passes_filter는 자체적으로 동일 검사를 인라인 수행한다)"""
    return all(c.get('mode', 'unused') == 'unused' for c in conditions)


def entry_passes_filter(entry: FileEntry, conditions: List[Dict]) -> bool:
    """
    조건 리스트를 평가해 항목이 필터를 통과하는지 반환한다.

    로직:
      - unused 조건은 제외.
      - AND/NOT 조건 중 하나라도 실패 → False.
      - OR 조건이 있으면 AND/NOT 모두 통과 AND OR 중 하나 이상 통과 → True.
      - 활성 조건이 OR만 있으면 OR 중 하나라도 통과 → True.
    """
    active     = [c for c in conditions if c.get('mode', 'unused') != 'unused']
    if not active:
        return True

    and_not = [c for c in active if c.get('mode') in ('and', 'not')]
    or_cond = [c for c in active if c.get('mode') == 'or']

    for cond in and_not:
        if not _evaluate_condition(entry, cond):
            return False

    if or_cond:
        return any(_evaluate_condition(entry, c) for c in or_cond)

    return True


# ---------------------------------------------------------------------------
# 스캔 및 검색
# ---------------------------------------------------------------------------

def _collect_entries(folder: Path, recursive: bool) -> List[FileEntry]:
    """폴더 내 이미지 파일 기준으로 FileEntry 목록을 수집한다."""
    entries: List[FileEntry] = []
    seen_stems: set          = set()

    iter_fn = folder.rglob('*') if recursive else folder.iterdir()

    for f in iter_fn:
        if not f.is_file():
            continue
        if is_image_file(f):
            txt = f.with_suffix(TEXT_EXTENSION)
            entries.append(FileEntry(
                image_path=f,
                txt_path=txt if txt.exists() else None,
            ))
            seen_stems.add((f.parent, f.stem))

    # txt만 있는 orphan 파일도 수집
    iter_fn2 = folder.rglob('*') if recursive else folder.iterdir()
    for f in iter_fn2:
        if not f.is_file():
            continue
        if is_text_file(f) and (f.parent, f.stem) not in seen_stems:
            entries.append(FileEntry(image_path=None, txt_path=f))

    return entries


def search_files(
    folder_path: str,
    recursive: bool,
    conditions: List[Dict],
    num_cores: int = 1,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> List[FileEntry]:
    """
    조건에 맞는 FileEntry 목록을 반환한다.

    Args:
        folder_path:       검색 루트 폴더
        recursive:         하위 폴더 포함 여부
        conditions:        필터 조건 목록
        num_cores:         해상도 조건 처리 시 스레드 수
        progress_callback: (done, total) 콜백
        stop_event:        중단 신호 이벤트

    Returns:
        조건을 통과한 FileEntry 목록
    """
    folder = Path(folder_path)
    if not folder.exists():
        return []

    entries = _collect_entries(folder, recursive)
    total   = len(entries)
    if total == 0:
        return []

    needs_resolution = any(
        c.get('type') == 'resolution' and c.get('mode', 'unused') != 'unused'
        for c in conditions
    )

    if needs_resolution and num_cores > 1:
        def _preload(e: FileEntry) -> FileEntry:
            _ = e.resolution
            return e

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_cores) as ex:
            futures = {ex.submit(_preload, e): i for i, e in enumerate(entries)}
            done    = 0
            for fut in concurrent.futures.as_completed(futures):
                if stop_event and stop_event.is_set():
                    ex.shutdown(wait=False, cancel_futures=True)
                    return []
                done += 1
                if progress_callback:
                    progress_callback(done, total)

    results: List[FileEntry] = []
    for i, entry in enumerate(entries):
        if stop_event and stop_event.is_set():
            break
        if entry_passes_filter(entry, conditions):
            results.append(entry)
        if progress_callback and not needs_resolution:
            progress_callback(i + 1, total)

    return results


# ---------------------------------------------------------------------------
# 파일 처리 (삭제 / 이동 / 복사)
# ---------------------------------------------------------------------------

def _resolve_conflict_path(dest_path: Path) -> Path:
    """대상 경로가 이미 존재하면 _{n} 접미사를 붙여 고유한 경로를 반환한다."""
    if not dest_path.exists():
        return dest_path
    stem, suffix, parent = dest_path.stem, dest_path.suffix, dest_path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _get_target_files(entry: FileEntry, target_type: str) -> List[Path]:
    """target_type 에 따라 처리할 파일 경로 목록을 반환한다."""
    files: List[Path] = []
    if target_type in ('both', 'image') and entry.has_image():
        files.append(entry.image_path)
    if target_type in ('both', 'txt') and entry.has_txt():
        files.append(entry.txt_path)
    return files


def process_entries(
    entries: List[FileEntry],
    action: str,
    target_type: str,
    dest_folder: str = '',
) -> Tuple[int, int, List[str]]:
    """
    선택된 항목들에 지정한 액션을 수행한다.

    Args:
        entries:     처리할 FileEntry 목록
        action:      'delete' | 'move' | 'copy'
        target_type: 'both' | 'image' | 'txt'
        dest_folder: 이동/복사 대상 폴더 (action이 delete이면 불필요)

    Returns:
        (성공 수, 실패 수, 로그 메시지 리스트)
    """
    success = fail = 0
    logs: List[str] = []

    if action in ('move', 'copy') and dest_folder:
        Path(dest_folder).mkdir(parents=True, exist_ok=True)

    for entry in entries:
        files = _get_target_files(entry, target_type)
        if not files:
            logs.append(f"[건너뜀] 처리 대상 없음: {entry.display_name}")
            continue

        for fpath in files:
            try:
                if action == 'delete':
                    fpath.unlink()
                    logs.append(f"[삭제] {fpath.name}")
                    success += 1

                elif action == 'move':
                    dest = _resolve_conflict_path(Path(dest_folder) / fpath.name)
                    shutil.move(str(fpath), str(dest))
                    suffix = f" → {dest.name}" if dest.name != fpath.name else ''
                    logs.append(f"[이동] {fpath.name}{suffix}")
                    success += 1

                elif action == 'copy':
                    dest = _resolve_conflict_path(Path(dest_folder) / fpath.name)
                    shutil.copy2(str(fpath), str(dest))
                    suffix = f" → {dest.name}" if dest.name != fpath.name else ''
                    logs.append(f"[복사] {fpath.name}{suffix}")
                    success += 1

            except Exception as e:
                logs.append(f"[실패] {fpath.name}: {e}")
                fail += 1

    return success, fail, logs


def get_orphan_warning(entries: List[FileEntry], target_type: str) -> List[str]:
    """
    처리 후 짝을 잃게 될 파일 경로 목록을 반환한다 (사용자 경고용).
    target_type == 'both' 이면 항상 빈 리스트.
    """
    if target_type == 'both':
        return []
    orphans: List[str] = []
    for entry in entries:
        if target_type == 'image' and entry.has_image() and entry.has_txt():
            orphans.append(str(entry.txt_path))
        elif target_type == 'txt' and entry.has_txt() and entry.has_image():
            orphans.append(str(entry.image_path))
    return orphans
