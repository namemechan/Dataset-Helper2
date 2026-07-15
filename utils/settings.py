"""
utils/settings.py

두 가지 역할을 하나로 통합한다.

1. 경로 관리 (paths)
   - 소스 실행 / exe 패키징 환경 모두에서 동일한 기준점을 제공한다.
   - 런타임에 생성되는 모든 파일(설정, 로그, undo 등)은
     APP_DIR 아래 data/ 하위에 저장된다.
   - 앱 시작 시 ensure_data_dirs() 를 한 번만 호출하면
     필요한 디렉터리가 없을 경우 자동으로 생성된다.

2. 앱 설정 저장 / 로드 (app settings)
   - 메인 윈도우 UI 상태(폴더 경로, 탭별 옵션 등)를 JSON으로 저장한다.
   - 이미지 변환기 전용 설정(converter_config)도 여기서 관리한다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 1. 경로 관리
# ---------------------------------------------------------------------------

def _resolve_app_dir() -> Path:
    """
    실행 환경에 관계없이 '실행파일(또는 main.py)이 있는 폴더'를 반환한다.

    - PyInstaller exe: sys.executable 의 부모 디렉터리
    - 소스 직접 실행:  이 파일(utils/settings.py)의 두 단계 위 = 프로젝트 루트
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


#: 프로젝트 루트 (소스) 또는 exe 가 위치한 디렉터리
APP_DIR: Path = _resolve_app_dir()

#: 런타임 생성 데이터 최상위 폴더
DATA_DIR: Path = APP_DIR / 'data'

#: 앱 전체 설정 파일 (메인 윈도우 UI 상태)
APP_SETTINGS_FILE: Path = DATA_DIR / 'config' / 'app_settings.json'

#: 이미지 변환기 전용 설정 파일
CONVERTER_CONFIG_FILE: Path = DATA_DIR / 'config' / 'converter_config.json'

#: 로그 디렉터리
LOG_DIR: Path = DATA_DIR / 'logs'

#: 실행 취소(undo) 데이터 디렉터리
UNDO_DIR: Path = DATA_DIR / 'undo'


def ensure_data_dirs() -> None:
    """
    런타임에 필요한 디렉터리를 모두 생성한다.
    앱 진입점(main.py)에서 QApplication 생성 직후 한 번만 호출한다.
    이미 존재하면 아무 작업도 하지 않는다.
    """
    for d in (
        DATA_DIR,
        DATA_DIR / 'config',
        LOG_DIR,
        UNDO_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 2-A. 앱 전체 설정 (메인 윈도우 UI 상태)
# ---------------------------------------------------------------------------

def load_app_settings() -> dict[str, Any]:
    """
    저장된 앱 설정을 반환한다.
    파일이 없거나 손상된 경우 빈 딕셔너리를 반환한다.
    """
    if not APP_SETTINGS_FILE.exists():
        return {}
    try:
        with APP_SETTINGS_FILE.open('r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_app_settings(settings: dict[str, Any]) -> bool:
    """
    앱 설정을 JSON 파일로 저장한다.

    Returns:
        저장 성공 여부
    """
    try:
        APP_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with APP_SETTINGS_FILE.open('w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except (OSError, TypeError) as e:
        print(f"앱 설정 저장 실패: {e}")
        return False


# ---------------------------------------------------------------------------
# 2-B. 이미지 변환기 전용 설정
# ---------------------------------------------------------------------------

def _default_converter_settings() -> dict[str, Any]:
    """이미지 변환기 기본 설정값을 반환한다."""
    return {
        'input_settings': {
            'source_folder':      '',
            'supported_formats':  ['jpg', 'jpeg', 'png', 'gif', 'webp'],
            'include_subfolders': True,
        },
        'output_settings': {
            'target_folder':      '',
            'target_format':      'png',
            'naming_pattern':     '{original_name}_converted',
            'use_suffix':         True,
            'suffix_text':        '_converted',
            'overwrite_policy':   'rename',   # 'skip' | 'overwrite' | 'rename'
            'output_to_input':    False,
            'input_conflict_mode': 'rename',
        },
        'delete_settings': {
            'delete_original':    False,
            'delete_confirm_popup': True,
        },
        'conversion_settings': {
            'quality_enabled': True,
            'quality_value':   95,
            'resize_enabled':  False,
            'resize_scale':    1.0,
            'optimize':        True,
        },
        'metadata_settings': {
            'preserve_enabled': True,
            'preservation_methods': {
                'exif':          True,
                'png_text':      True,
                'steganography': True,
                'all_methods':   False,
            },
            'ai_info_priority':         ['stealth_pnginfo', 'png_text', 'exif'],
            'compression_steganography': True,
        },
        'processing_settings': {
            'multiprocessing_enabled': True,
            'max_workers':    os.cpu_count() or 1,
            'chunk_size':     100,
            'memory_limit_mb': 4096,
        },
        'logging_settings': {
            'log_level':      'INFO',
            'save_logs':      True,
            'max_log_size_mb': 10,
        },
    }


def load_converter_settings() -> dict[str, Any]:
    """
    이미지 변환기 설정을 반환한다.
    파일이 없거나 손상된 경우 기본값을 반환한다.
    """
    if not CONVERTER_CONFIG_FILE.exists():
        return _default_converter_settings()
    try:
        with CONVERTER_CONFIG_FILE.open('r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"변환기 설정 로드 실패 — 기본값 사용: {e}")
        return _default_converter_settings()


def save_converter_settings(settings: dict[str, Any]) -> bool:
    """
    이미지 변환기 설정을 JSON 파일로 저장한다.

    Returns:
        저장 성공 여부
    """
    try:
        CONVERTER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with CONVERTER_CONFIG_FILE.open('w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
        return True
    except (OSError, TypeError) as e:
        print(f"변환기 설정 저장 실패: {e}")
        return False
