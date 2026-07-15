"""
ui/main_window.py

메인 윈도우 — 탭 컨테이너, 공용 폴더 선택, 코어 수 설정, 테마 토글, 설정 저장/로드.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore    import Qt, Slot
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QSpinBox, QPushButton, QSizePolicy,
    QApplication,
)

from ui.constants import (
    WIN_MIN_WIDTH, WIN_MIN_HEIGHT,
    WIN_DEFAULT_WIDTH, WIN_DEFAULT_HEIGHT,
    PADDING_SMALL, SPACING_SMALL,
    set_theme, get_theme_name, INITIAL_THEME_NAME,
)
from ui.themes import get_theme
from ui.styles  import build_qss
from ui.widgets.folder_selector import FolderSelector
from ui.tabs.rename_tab         import RenameTab
from ui.tabs.single_file_tab    import SingleFileTab
from ui.tabs.tag_tab            import TagTab
from ui.tabs.converter_tab      import ConverterTab
from ui.tabs.duplicate_tab      import DuplicateTab
from ui.tabs.analyzer_tab       import AnalyzerTab
from ui.tabs.search_filter_tab  import SearchFilterTab
from ui.tabs.xy_plot_tab        import XYPlotTab

from utils.settings import load_app_settings, save_app_settings
from utils.logger   import logger


class MainWindow(QMainWindow):
    """데이터셋 헬퍼 메인 윈도우."""

    def __init__(self) -> None:
        super().__init__()
        self._folder: str = ''
        self._build_ui()
        self._connect_signals()
        self._load_settings()
        # 저장된 설정이 없는 첫 실행에도 상단 기본 코어 수를 모든 탭에 전달한다.
        # QSpinBox의 초기 setValue()는 시그널 연결 전이므로, 이 호출이 없으면
        # 일부 탭이 내부 기본값(1코어)으로 남을 수 있다.
        self._broadcast_cores(self.core_spin.value())

    def _build_ui(self) -> None:
        self.setWindowTitle('dataset-helper')
        self.setMinimumSize(WIN_MIN_WIDTH, WIN_MIN_HEIGHT)
        self.resize(WIN_DEFAULT_WIDTH, WIN_DEFAULT_HEIGHT)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                PADDING_SMALL, PADDING_SMALL)
        root.setSpacing(SPACING_SMALL)

        # 상단 툴바
        toolbar = QHBoxLayout()
        toolbar.setSpacing(SPACING_SMALL)

        folder_lbl = QLabel('작업 폴더:')
        folder_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.folder_selector = FolderSelector('폴더를 선택하세요')

        # '사용 코어' 영역은 창 너비에 관계없이 항상 보여야 하므로
        # 별도 위젯으로 감싸 Fixed 정책을 줘서 폴더 입력창에 밀려 잘리지 않게 한다.
        core_widget = QWidget()
        core_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        core_layout = QHBoxLayout(core_widget)
        core_layout.setContentsMargins(0, 0, 0, 0)
        core_layout.setSpacing(SPACING_SMALL)

        core_lbl = QLabel('사용 코어:')
        self.core_spin = QSpinBox()
        available_cores = os.cpu_count() or 1
        self.core_spin.setRange(1, available_cores)
        # 첫 실행 기본값은 4코어로 두되, 4코어 미만 환경에서는 가능한 최대값을 사용한다.
        # 이후 사용자가 바꾼 값은 app_settings.json에서 다시 불러온다.
        self.core_spin.setValue(min(4, available_cores))
        self.core_spin.setFixedWidth(55)

        core_layout.addWidget(core_lbl)
        core_layout.addWidget(self.core_spin)

        # 테마 토글 버튼 (클릭 한 번에 라이트<->다크 전환)
        theme_widget = QWidget()
        theme_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        theme_layout = QHBoxLayout(theme_widget)
        theme_layout.setContentsMargins(0, 0, 0, 0)

        self.theme_btn = QPushButton()
        self.theme_btn.setFixedWidth(72)
        self.theme_btn.setToolTip('라이트 / 다크 테마 전환')
        theme_layout.addWidget(self.theme_btn)

        toolbar.addWidget(folder_lbl)
        toolbar.addWidget(self.folder_selector, stretch=1)
        toolbar.addWidget(core_widget)
        toolbar.addWidget(theme_widget)
        root.addLayout(toolbar)

        # 탭 위젯
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.rename_tab    = RenameTab()
        self.single_tab    = SingleFileTab()
        self.tag_tab       = TagTab()
        self.converter_tab = ConverterTab()
        self.duplicate_tab = DuplicateTab()
        self.analyzer_tab  = AnalyzerTab()
        self.sf_tab        = SearchFilterTab()
        self.xy_tab        = XYPlotTab()

        self.tabs.addTab(self.rename_tab,    '이름 변경')
        self.tabs.addTab(self.single_tab,    '단일 파일 찾기')
        self.tabs.addTab(self.tag_tab,       '태그 처리')
        self.tabs.addTab(self.converter_tab, '이미지 변환')
        self.tabs.addTab(self.duplicate_tab, '중복/유사 이미지')
        self.tabs.addTab(self.analyzer_tab,  '데이터셋 분석')
        self.tabs.addTab(self.sf_tab,        '검색 및 분류')
        self.tabs.addTab(self.xy_tab,        'XY표 만들기')

        root.addWidget(self.tabs, stretch=1)

        self._refresh_theme_btn_label()

    def _connect_signals(self) -> None:
        self.folder_selector.path_changed.connect(self._on_folder_changed)
        self.core_spin.valueChanged.connect(self._on_core_changed)
        self.theme_btn.clicked.connect(self._on_toggle_theme)

    def _broadcast_folder(self, folder: str) -> None:
        for tab in (self.rename_tab, self.single_tab, self.tag_tab,
                    self.duplicate_tab, self.analyzer_tab, self.sf_tab):
            tab.set_folder(folder)

    def _broadcast_cores(self, n: int) -> None:
        for tab in (self.tag_tab, self.converter_tab,
                    self.duplicate_tab, self.analyzer_tab,
                    self.sf_tab, self.xy_tab):
            tab.set_num_cores(n)

    @Slot(str)
    def _on_folder_changed(self, folder: str) -> None:
        if folder and Path(folder).is_dir():
            self._folder = folder
            self._broadcast_folder(folder)

    @Slot(int)
    def _on_core_changed(self, n: int) -> None:
        self._broadcast_cores(n)

    # ------------------------------------------------------------------
    # 테마 전환
    # ------------------------------------------------------------------

    def _refresh_theme_btn_label(self) -> None:
        """현재 테마와 반대되는, '눌렀을 때 바뀔 테마'를 버튼 라벨로 보여준다."""
        if get_theme_name() == 'dark':
            self.theme_btn.setText('☀ 라이트')
        else:
            self.theme_btn.setText('🌙 다크')

    @Slot()
    def _on_toggle_theme(self) -> None:
        new_name = 'light' if get_theme_name() == 'dark' else 'dark'
        self._apply_theme(new_name)

    def _apply_theme(self, name: str) -> None:
        """테마를 전환하고 즉시 전체 앱에 QSS를 재적용한다."""
        theme = set_theme(name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(theme))
        self._refresh_theme_btn_label()

    # ------------------------------------------------------------------
    # 설정 저장 / 로드
    # ------------------------------------------------------------------

    def _collect_settings(self) -> dict:
        s: dict = {
            'folder_path': self._folder,
            'core_count':  self.core_spin.value(),
            'theme':       get_theme_name(),
        }
        for tab in (self.rename_tab, self.single_tab, self.tag_tab,
                    self.converter_tab, self.duplicate_tab, self.analyzer_tab,
                    self.sf_tab, self.xy_tab):
            s.update(tab.get_settings())
        return s

    def _apply_settings(self, s: dict) -> None:
        folder = s.get('folder_path', '')
        if folder and Path(folder).is_dir():
            self.folder_selector.set_path(folder)
            self._folder = folder
            self._broadcast_folder(folder)

        cores = int(s.get('core_count', os.cpu_count() or 1))
        self.core_spin.setValue(cores)
        self._broadcast_cores(cores)

        # 설정 파일에 theme 키가 없는 경우(구버전 설정 등)에는
        # "처음 실행했을 때 보여줄 테마"와 동일하게 라이트를 기본값으로 한다.
        theme_name = s.get('theme', INITIAL_THEME_NAME)
        self._apply_theme(theme_name)

        for tab in (self.rename_tab, self.single_tab, self.tag_tab,
                    self.converter_tab, self.duplicate_tab, self.analyzer_tab,
                    self.sf_tab, self.xy_tab):
            tab.load_settings(s)

    def _load_settings(self) -> None:
        try:
            s = load_app_settings()
            if s:
                self._apply_settings(s)
        except Exception as e:
            logger.warning(f'설정 로드 실패: {e}', module='main_window')

    def _save_settings(self) -> None:
        try:
            save_app_settings(self._collect_settings())
        except Exception as e:
            logger.warning(f'설정 저장 실패: {e}', module='main_window')

    def closeEvent(self, event) -> None:
        self._save_settings()
        logger.remove_gui_handler()
        super().closeEvent(event)
