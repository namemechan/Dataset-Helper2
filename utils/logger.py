"""
utils/logger.py

앱 전역 로거.

설계 원칙:
  - 이 파일 자체는 UI(tkinter / PySide6) 에 의존하지 않는다.
  - GUI 로그 출력이 필요한 경우, UI 쪽에서 add_gui_handler()를 호출해
    핸들러를 붙이는 방식으로 결합도를 낮춘다.
  - 로그 디렉터리는 utils/settings.py 의 LOG_DIR 을 참조해
    setup() 호출 시 외부에서 주입받는다.
"""

from __future__ import annotations

import datetime
import logging
import logging.handlers
import time
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 내부 필터 — %(mod)s 포맷 필드 주입
# ---------------------------------------------------------------------------

class _ModuleFilter(logging.Filter):
    """로그 레코드에 'mod' 필드를 추가해 포맷터에서 %(mod)s 로 쓸 수 있게 한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.mod = getattr(record, 'custom_module', 'main')  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# 앱 로거 클래스
# ---------------------------------------------------------------------------

class AppLogger:
    """
    앱 전역에서 공유하는 로거 래퍼.

    사용법:
        from utils.logger import logger
        logger.info("메시지", module="converter_engine")
    """

    def __init__(self, name: str = 'DatasetHelper') -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(logging.DEBUG)
        self._logger.addFilter(_ModuleFilter())

        self._file_handler: Optional[logging.Handler] = None
        self._gui_handler:  Optional[logging.Handler] = None

        self.statistics: dict = self._empty_stats()

    # ------------------------------------------------------------------
    # 초기화
    # ------------------------------------------------------------------

    def setup(self, log_level: str = 'INFO', log_dir: Optional[Path] = None) -> None:
        """
        콘솔 핸들러와 날짜별 파일 핸들러를 설정한다.

        Args:
            log_level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
            log_dir:   로그 파일을 저장할 디렉터리 (None 이면 파일 핸들러 생략)
        """
        # 기존 핸들러 제거 (중복 방지)
        for h in self._logger.handlers[:]:
            self._logger.removeHandler(h)

        fmt_file    = '[%(asctime)s] [%(levelname)s] [%(mod)s] %(message)s'
        fmt_console = '[%(asctime)s] [%(levelname)s] [%(mod)s] %(message)s'
        date_fmt    = '%Y-%m-%d %H:%M:%S'

        # 콘솔 핸들러
        ch = logging.StreamHandler()
        ch.setLevel(log_level)
        ch.setFormatter(logging.Formatter(fmt_console, date_fmt))
        self._logger.addHandler(ch)

        # 파일 핸들러 (날짜별 로테이션)
        if log_dir is not None:
            log_dir = Path(log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)

            today      = datetime.datetime.now().strftime('%Y-%m-%d')
            log_file   = log_dir / f"app_{today}.log"

            fh = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
                encoding='utf-8',
            )
            fh.setLevel(log_level)
            fh.setFormatter(logging.Formatter(fmt_file, date_fmt))
            self._logger.addHandler(fh)
            self._file_handler = fh

    # ------------------------------------------------------------------
    # GUI 핸들러 (PySide6 쪽에서 호출)
    # ------------------------------------------------------------------

    def add_gui_handler(self, handler: logging.Handler) -> None:
        """
        외부(UI 레이어)에서 만든 핸들러를 로거에 붙인다.
        PySide6 위젯에 로그를 출력할 때 사용한다.
        """
        if self._gui_handler is not None:
            self._logger.removeHandler(self._gui_handler)
        self._gui_handler = handler
        self._logger.addHandler(handler)

    def remove_gui_handler(self) -> None:
        """GUI 핸들러를 제거한다 (탭 소멸 시 등)."""
        if self._gui_handler is not None:
            self._logger.removeHandler(self._gui_handler)
            self._gui_handler = None

    # ------------------------------------------------------------------
    # 로깅 메서드
    # ------------------------------------------------------------------

    def debug(self, message: str, module: str = 'main') -> None:
        self._logger.debug(message, extra={'custom_module': module})

    def info(self, message: str, module: str = 'main') -> None:
        self._logger.info(message, extra={'custom_module': module})

    def warning(self, message: str, module: str = 'main') -> None:
        self._logger.warning(message, extra={'custom_module': module})

    def error(self, message: str, module: str = 'main', exc_info: bool = False) -> None:
        self._logger.error(message, extra={'custom_module': module}, exc_info=exc_info)

    def critical(self, message: str, module: str = 'main', exc_info: bool = False) -> None:
        self._logger.critical(message, extra={'custom_module': module}, exc_info=exc_info)

    # ------------------------------------------------------------------
    # 도메인 특화 로그 헬퍼
    # ------------------------------------------------------------------

    def log_conversion_start(self, source_file: str, target_format: str) -> None:
        self.info(f'이미지 변환 시작: {source_file} -> .{target_format}', module='converter_engine')

    def log_metadata_detection(self, metadata_types: list[str], file_path: str) -> None:
        if metadata_types:
            self.debug(
                f'{file_path}에서 메타데이터 발견: {", ".join(metadata_types)}',
                module='metadata_handler',
            )
        else:
            self.debug(f'{file_path}에서 메타데이터 없음', module='metadata_handler')

    def log_performance_stats(self, processing_time: float, memory_bytes: int) -> None:
        self.debug(
            f'처리 시간: {processing_time:.2f}초, '
            f'메모리: {memory_bytes / 1024 / 1024:.2f} MB',
            module='performance',
        )

    def log_error(self, error_type: str, error_msg: str, file_path: str = '') -> None:
        msg = f'[{error_type}] {error_msg}'
        if file_path:
            msg += f' (파일: {file_path})'
            self.statistics['failed_files'] += 1
        self.error(msg, module='error_handler')

    # ------------------------------------------------------------------
    # 통계
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_stats() -> dict:
        return {
            'start_time':           None,
            'end_time':             None,
            'total_files':          0,
            'converted_files':      0,
            'failed_files':         0,
            'skipped_files':        0,
            'total_processing_time': 0,
            'metadata_preserved':   0,
            'metadata_failed':      0,
        }

    def reset_statistics(self) -> None:
        self.statistics = self._empty_stats()
        self.statistics['start_time'] = time.time()

    def get_statistics(self) -> dict:
        if self.statistics['start_time']:
            self.statistics['end_time'] = time.time()
            self.statistics['total_processing_time'] = (
                self.statistics['end_time'] - self.statistics['start_time']
            )
        return self.statistics


# ---------------------------------------------------------------------------
# 전역 싱글톤
# ---------------------------------------------------------------------------

#: 앱 전역에서 공유하는 로거 인스턴스.
#: 다른 모듈에서는 ``from utils.logger import logger`` 로 가져다 쓴다.
logger = AppLogger()
