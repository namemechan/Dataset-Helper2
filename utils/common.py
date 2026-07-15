"""
utils/common.py

UI에 의존하지 않는 순수 공통 유틸리티.
상수, 파일 판별, 파일 쌍 탐색, 멀티코어 처리, 숫자 포맷 등을 담당한다.
"""

from __future__ import annotations

import math
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Callable, List, Tuple

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# 지원하는 이미지 확장자
IMAGE_EXTENSIONS: frozenset[str] = frozenset({'.jpg', '.jpeg', '.png', '.gif', '.webp'})

# 텍스트(태그) 파일 확장자
TEXT_EXTENSION: str = '.txt'

# 인원수 태그 집합
PERSON_COUNT_TAGS: frozenset[str] = frozenset({
    '1other',
    '1girl',
    '2girls',
    '3girls',
    '4girls',
    '5girls',
    '6+girls',
    'multiple girls',
    '1boy',
    '2boys',
    '3boys',
    '4boys',
    '5boys',
    '6+boys',
})


# ---------------------------------------------------------------------------
# 파일 판별
# ---------------------------------------------------------------------------

def is_image_file(file_path: Path) -> bool:
    """파일이 지원하는 이미지 파일인지 확인"""
    return file_path.suffix.lower() in IMAGE_EXTENSIONS


def is_text_file(file_path: Path) -> bool:
    """파일이 txt 파일인지 확인"""
    return file_path.suffix.lower() == TEXT_EXTENSION


# ---------------------------------------------------------------------------
# 파일 쌍 탐색
# ---------------------------------------------------------------------------

def get_paired_files(
    folder_path: Path | str,
    recursive: bool = False,
) -> List[Tuple[Path, Path]]:
    """
    폴더 내 이미지-텍스트 파일 쌍을 반환한다.

    Args:
        folder_path: 검색할 폴더 경로
        recursive:   하위 폴더 포함 여부

    Returns:
        [(image_path, text_path), ...] — 전체 경로 기준 오름차순 정렬
    """
    folder = Path(folder_path)
    if not folder.exists():
        return []

    files = folder.rglob("*") if recursive else folder.iterdir()
    image_files = [f for f in files if f.is_file() and is_image_file(f)]

    paired: List[Tuple[Path, Path]] = []
    for img in image_files:
        txt = img.with_suffix(TEXT_EXTENSION)
        if txt.exists():
            paired.append((img, txt))

    return sorted(paired, key=lambda x: str(x[0]))


# ---------------------------------------------------------------------------
# 멀티코어 처리
# ---------------------------------------------------------------------------

def process_with_multicore(
    func: Callable[[Any], Any],
    items: List[Any],
    num_cores: int,
) -> List[Any]:
    """
    멀티코어로 작업을 처리한다.

    Args:
        func:      처리할 함수
        items:     처리할 아이템 리스트
        num_cores: 사용할 코어 수 (1이면 단일 스레드)

    Returns:
        처리 결과 리스트
    """
    if num_cores <= 1 or len(items) == 0:
        return [func(item) for item in items]

    with Pool(processes=num_cores) as pool:
        return pool.map(func, items)


# ---------------------------------------------------------------------------
# 숫자 / 크기 포맷
# ---------------------------------------------------------------------------

def format_number(num: int, digits: int) -> str:
    """숫자를 지정된 자릿수로 제로패딩 포맷팅"""
    return str(num).zfill(digits)


def format_file_size(size_bytes: int) -> str:
    """바이트 수를 사람이 읽기 쉬운 단위(B/KB/MB/GB …)로 변환"""
    if size_bytes == 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    return f"{round(size_bytes / p, 2)} {units[i]}"


# ---------------------------------------------------------------------------
# 진행률 / 남은 시간
# ---------------------------------------------------------------------------

def calculate_progress(current: int, total: int) -> float:
    """진행률을 0.0~100.0 사이의 백분율로 반환"""
    if total == 0:
        return 0.0
    return (current / total) * 100.0


def estimate_remaining_time(
    start_time: float,
    current_progress: float,
    total_progress: float = 100.0,
) -> str:
    """
    남은 예상 시간을 계산해 문자열로 반환.

    Args:
        start_time:       작업 시작 시각 (time.time() 값)
        current_progress: 현재까지 진행된 양
        total_progress:   전체 작업량 (기본 100)
    """
    if current_progress <= 0:
        return "계산 중..."

    elapsed = time.time() - start_time
    total_estimated = elapsed / (current_progress / total_progress)
    remaining = total_estimated - elapsed

    if remaining < 0:
        return "완료 직전..."

    hours, rem = divmod(remaining, 3600)
    minutes, seconds = divmod(rem, 60)

    if hours >= 1:
        return f"{int(hours):02d}시간 {int(minutes):02d}분 {int(seconds):02d}초"
    if minutes >= 1:
        return f"{int(minutes):02d}분 {int(seconds):02d}초"
    return f"{int(seconds):02d}초"


# ---------------------------------------------------------------------------
# 속도 제한기 (GUI 업데이트 과다 방지)
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    지정한 초당 최대 호출 횟수를 넘지 않도록 제한하는 간단한 속도 제한기.
    주로 진행률 콜백 등 GUI 업데이트가 너무 자주 발생하는 것을 막을 때 사용한다.
    """

    def __init__(self, max_calls_per_second: float) -> None:
        self._min_interval = 1.0 / max_calls_per_second
        self._last_call: float = 0.0

    def is_allowed(self) -> bool:
        """지금 호출해도 되는지 여부를 반환하고, 허용 시 타임스탬프를 갱신한다."""
        now = time.time()
        if now - self._last_call >= self._min_interval:
            self._last_call = now
            return True
        return False
