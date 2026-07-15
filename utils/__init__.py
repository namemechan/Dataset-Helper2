"""
utils 패키지

하위 모듈:
  common   — 상수, 파일 판별, 파일 쌍 탐색, 포맷 유틸, RateLimiter
  logger   — 앱 전역 로거 싱글톤
  settings — 경로 상수(APP_DIR / DATA_DIR / LOG_DIR / UNDO_DIR),
             디렉터리 보장(ensure_data_dirs),
             앱 설정 및 변환기 설정 저장/로드
"""

from utils.common import (
    IMAGE_EXTENSIONS,
    TEXT_EXTENSION,
    PERSON_COUNT_TAGS,
    is_image_file,
    is_text_file,
    get_paired_files,
    process_with_multicore,
    format_number,
    format_file_size,
    calculate_progress,
    estimate_remaining_time,
    RateLimiter,
)

from utils.logger import logger

from utils.settings import (
    APP_DIR,
    DATA_DIR,
    LOG_DIR,
    UNDO_DIR,
    ensure_data_dirs,
    load_app_settings,
    save_app_settings,
    load_converter_settings,
    save_converter_settings,
)

__all__ = [
    # common
    'IMAGE_EXTENSIONS',
    'TEXT_EXTENSION',
    'PERSON_COUNT_TAGS',
    'is_image_file',
    'is_text_file',
    'get_paired_files',
    'process_with_multicore',
    'format_number',
    'format_file_size',
    'calculate_progress',
    'estimate_remaining_time',
    'RateLimiter',
    # logger
    'logger',
    # settings / paths
    'APP_DIR',
    'DATA_DIR',
    'LOG_DIR',
    'UNDO_DIR',
    'ensure_data_dirs',
    'load_app_settings',
    'save_app_settings',
    'load_converter_settings',
    'save_converter_settings',
]
