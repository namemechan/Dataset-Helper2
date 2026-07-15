"""
image/file_utils.py

이미지 변환기에서 사용하는 파일 I/O 유틸리티.

담당:
  - 디렉터리 스캔 (특정 확장자 필터)
  - 파일 접근 권한 확인
  - 출력 파일 경로 생성 (일반 모드 / 입력폴더 출력 모드)
  - 파일 충돌 처리 (skip / overwrite / rename)
  - 파일 상세 정보 조회
  - 파일 백업 생성
"""

from __future__ import annotations

import os
import pathlib
import shutil
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 디렉터리 스캔
# ---------------------------------------------------------------------------

def scan_directory(
    directory_path: str,
    extensions: List[str],
    include_subdirs: bool = True,
) -> List[str]:
    """
    지정 디렉터리에서 특정 확장자 파일 목록을 반환한다.

    Args:
        directory_path: 검색할 루트 디렉터리
        extensions:     허용 확장자 목록 (예: ['.jpg', '.png'])
        include_subdirs: True 이면 하위 폴더도 재귀 탐색

    Returns:
        조건에 맞는 파일 경로 목록 (절대경로)
    """
    # 호출부/구버전 설정에 따라 '.png' 또는 'png'가 모두 들어올 수 있다.
    # pathlib.Path.suffix()는 항상 점을 포함하므로 내부 비교 형식을 통일한다.
    allowed = {
        f'.{str(ext).lower().lstrip(".")}'
        for ext in extensions
        if str(ext).strip()
    }
    found: List[str] = []

    try:
        if include_subdirs:
            for root, _, files in os.walk(directory_path):
                for name in files:
                    if pathlib.Path(name).suffix.lower() in allowed:
                        found.append(os.path.join(root, name))
        else:
            for name in os.listdir(directory_path):
                full = os.path.join(directory_path, name)
                if os.path.isfile(full) and pathlib.Path(name).suffix.lower() in allowed:
                    found.append(full)
    except FileNotFoundError:
        pass
    except PermissionError:
        pass

    return found


# ---------------------------------------------------------------------------
# 파일 접근 권한
# ---------------------------------------------------------------------------

def validate_file_access(file_path: str, mode: str = 'r') -> bool:
    """
    파일(또는 디렉터리)에 대한 읽기/쓰기 권한을 확인한다.

    Args:
        file_path: 확인할 파일 경로
        mode:      'r' (읽기) | 'w' (쓰기)

    Returns:
        권한이 있으면 True, 없거나 오류 시 False
    """
    try:
        if mode == 'r':
            return os.access(file_path, os.R_OK)
        if mode == 'w':
            parent = os.path.dirname(file_path) or '.'
            return os.access(parent, os.W_OK)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# 출력 경로 생성
# ---------------------------------------------------------------------------

def generate_output_filename(
    input_path: str,
    output_dir: str,
    target_format: str,
    naming_pattern: str = '{original_name}_converted',
) -> str:
    """
    출력 폴더가 입력 폴더와 다른 일반 모드에서 출력 파일 경로를 생성한다.

    Args:
        input_path:     원본 파일 경로
        output_dir:     출력 디렉터리
        target_format:  저장 포맷 (예: 'png', 'webp')
        naming_pattern: 파일명 패턴. {original_name} / {ext} 치환 지원

    Returns:
        최종 출력 파일 경로 문자열
    """
    original_name = pathlib.Path(input_path).stem
    ext = target_format.lower().lstrip('.')
    try:
        new_name = naming_pattern.format(original_name=original_name, ext=ext)
    except (KeyError, ValueError):
        new_name = f"{original_name}_converted"

    # Path Traversal 방지
    safe_name = os.path.basename(new_name)
    return os.path.join(output_dir, f"{safe_name}.{ext}")


def generate_output_filename_to_input(
    input_path: str,
    target_format: str,
    naming_pattern: str = '{original_name}',
) -> str:
    """
    출력 폴더 = 입력 파일 폴더 모드에서 출력 파일 경로를 생성한다.

    Args:
        input_path:     원본 파일 경로
        target_format:  저장 포맷
        naming_pattern: 파일명 패턴

    Returns:
        최종 출력 파일 경로 문자열
    """
    p = pathlib.Path(input_path)
    original_name = p.stem
    ext = target_format.lower().lstrip('.')
    try:
        new_name = naming_pattern.format(original_name=original_name, ext=ext)
    except (KeyError, ValueError):
        new_name = original_name

    safe_name = os.path.basename(new_name)
    return str(p.parent / f"{safe_name}.{ext}")


# ---------------------------------------------------------------------------
# 파일 충돌 처리
# ---------------------------------------------------------------------------

def handle_file_conflicts(
    output_path: str,
    policy: str = 'rename',
) -> Optional[str]:
    """
    출력 폴더가 다른 일반 모드의 파일 충돌을 처리한다.

    Args:
        output_path: 원하는 출력 파일 경로
        policy:      'skip' | 'overwrite' | 'rename'

    Returns:
        사용할 최종 경로. policy='skip' 이고 파일이 이미 존재하면 None.
    """
    if not os.path.exists(output_path) or policy == 'overwrite':
        return output_path
    if policy == 'skip':
        return None

    # rename: _1, _2, … 접미사
    base, ext = os.path.splitext(output_path)
    counter = 1
    while True:
        candidate = f"{base}_{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def handle_file_conflicts_for_input(
    output_path: str,
    policy: str = 'rename',
) -> Optional[str]:
    """
    출력 폴더 = 입력 폴더 모드의 파일 충돌을 처리한다.
    handle_file_conflicts 와 동일한 로직이나 별도 함수로 유지해
    호출부에서 맥락을 명확히 구분한다.

    Args:
        output_path: 원하는 출력 파일 경로
        policy:      'skip' | 'overwrite' | 'rename'

    Returns:
        사용할 최종 경로. policy='skip' 이고 파일이 이미 존재하면 None.
    """
    if not os.path.exists(output_path) or policy == 'overwrite':
        return output_path
    if policy == 'skip':
        return None

    base, ext = os.path.splitext(output_path)
    counter = 1
    while True:
        candidate = f"{base}_{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# 파일 정보 / 백업
# ---------------------------------------------------------------------------

def get_file_info(file_path: str) -> Optional[Dict[str, Any]]:
    """
    파일의 상세 정보를 딕셔너리로 반환한다.

    Returns:
        파일 정보 딕셔너리. 파일이 없으면 None.
    """
    try:
        stat = os.stat(file_path)
        return {
            'path':          file_path,
            'size':          stat.st_size,
            'created_time':  stat.st_ctime,
            'modified_time': stat.st_mtime,
            'is_readable':   os.access(file_path, os.R_OK),
            'is_writable':   os.access(file_path, os.W_OK),
        }
    except FileNotFoundError:
        return None


def create_backup(
    file_path: str,
    backup_dir: Optional[str] = None,
) -> Optional[str]:
    """
    파일 백업을 생성한다.

    Args:
        file_path:  백업할 원본 파일
        backup_dir: 백업 저장 디렉터리. None 이면 원본 옆에 .bak 확장자로 저장.

    Returns:
        백업 파일 경로. 실패 시 None.
    """
    if not os.path.exists(file_path):
        return None

    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, os.path.basename(file_path))
    else:
        backup_path = file_path + '.bak'

    try:
        shutil.copy2(file_path, backup_path)
        return backup_path
    except Exception:
        return None
