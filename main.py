"""
main.py

dataset-helper 진입점.

실행 방법:
    python main.py          # 소스 직접 실행
    dataset-helper.exe      # PyInstaller 패키징 후 실행

exe 패키징 시 데이터(설정, 로그, undo 등)는
exe 옆 data/ 폴더에 자동 생성되므로 소스 실행과 완전히 동일하게 동작한다.
"""

from __future__ import annotations

import multiprocessing
import sys

from PySide6.QtCore       import Qt
from PySide6.QtWidgets    import QApplication

from utils.settings import ensure_data_dirs, LOG_DIR
from utils.logger   import logger
from ui.styles      import build_qss
from ui.themes      import get_theme
from ui.constants    import set_theme, INITIAL_THEME_NAME
from ui.main_window import MainWindow


def main() -> None:
    # PyInstaller 멀티프로세싱 지원 (exe 환경에서 반드시 필요)
    multiprocessing.freeze_support()

    # 런타임 데이터 디렉터리 보장 (없으면 생성)
    ensure_data_dirs()

    # 로거 초기화
    try:
        logger.setup(log_level='INFO', log_dir=LOG_DIR)
    except Exception as e:
        print(f'로거 초기화 실패: {e}')

    # QApplication 생성
    app = QApplication(sys.argv)
    app.setApplicationName('dataset-helper')
    app.setApplicationDisplayName('dataset-helper')

    # 고DPI 대응
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # 전역 QSS 1차 적용 (기본 다크 테마).
    # 저장된 사용자 설정에 다른 테마가 있으면 MainWindow가 로드 시 다시 적용한다.
    set_theme(INITIAL_THEME_NAME)
    app.setStyleSheet(build_qss(get_theme(INITIAL_THEME_NAME)))

    # 메인 윈도우
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
