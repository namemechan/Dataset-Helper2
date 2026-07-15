"""
ui/tabs/xy_plot_tab.py

XY표 만들기 탭.

구조 (원본 tkinter 버전과 동일):
  좌측: 설정 패널 (스크롤)
    1. 폴더 입력 방식 (셀프 선택 / 폴더 자동 감지)
    2. 빈 칸 채우기 방식 (격자 우선 / 데이터 우선)
    3. 이미지 정렬 순서 (기준: 이름/날짜/크기, 방향: 오름/내림)
    4. 이미지 배치 방향 (행/열 + 행↔열 스왑 버튼만. 행수/열수는 우측 컨트롤바)
    5. 이미지 셀 크기 (바짝붙이기 / 최장변 기준 정사각형)
    6. 혼합 해상도 처리 (최대/최소/직접 기준 + 처리방식)
    7. 제목 (표시여부+텍스트 / 글자크기자동+크기)
    8. 라벨 글자 (폰트모드+크기 / 가로정렬 / 세로정렬)
    9. 패딩 / 스케일조절
    10. 저장 옵션
    11. 실행 버튼 (미리보기 / 완성본 저장)

  우측: 라벨 입력 격자
    - 행수/열수 입력 + 격자생성/자동격자생성 버튼
    - 스크롤 가능한 Entry 격자 (첫 행=열 라벨, 첫 열=행 라벨, 나머지=[이미지] placeholder)

  미리보기는 별도 팝업(QDialog)으로 띄우며, 줌/팬/창맞춤을 지원한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore    import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QCheckBox, QRadioButton,
    QPushButton, QButtonGroup, QSpinBox, QScrollArea,
    QSplitter, QFileDialog, QMessageBox, QSizePolicy, QFrame,
)

from core.xyz_plot_engine import (
    AXIS_ROW, AXIS_COL,
    CELL_TIGHT, CELL_LONGEST_EDGE,
    FONT_AUTO, FONT_FIT, FONT_MANUAL,
    METHOD_SCALE, METHOD_CROP,
    RESIZE_LARGEST, RESIZE_SMALLEST, RESIZE_CUSTOM,
    FolderEntry, XYPlotConfig, BuildResult,
    build_plot, build_preview, save_image, save_preview_image,
    collect_images,
)
from ui.widgets.worker_base  import SafeWorker
from ui.widgets.image_viewer import ImageViewerDialog, pil_to_qimage
from ui.constants import (
    PADDING_SMALL, SPACING_SMALL, current_colors,
    register_theme_listener, unregister_theme_listener,
)

GRID_CELL = 60
# 원본(GRID_MAX=12)은 격자 위젯 수가 너무 늘면 GUI가 느려지는 것을 막기 위한 제한이었으나,
# 사용자가 큰 표(예: 20x20)를 만들 수 없는 문제가 있어 50으로 상향한다.
# 50x50 = 2500개 셀까지는 QLineEdit/QLabel 위젯으로도 충분히 즉시 반응한다.
GRID_MAX  = 50


# ---------------------------------------------------------------------------
# 폴더 행 위젯 (셀프 선택 모드의 한 줄: 번호 + 경로 + 선택버튼 + 라벨)
# ---------------------------------------------------------------------------

class _FolderRow(QWidget):
    def __init__(self, index: int, path: str = '', label: str = '',
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(SPACING_SMALL)

        self.idx_lbl = QLabel(f'{index + 1}.')
        self.idx_lbl.setFixedWidth(18)

        self.path_edit = QLineEdit(path)
        self.browse_btn = QPushButton('선택')
        self.browse_btn.setFixedWidth(48)

        lbl_caption = QLabel('라벨:')
        self.label_edit = QLineEdit(label)
        self.label_edit.setFixedWidth(90)

        lay.addWidget(self.idx_lbl)
        lay.addWidget(self.path_edit, stretch=1)
        lay.addWidget(self.browse_btn)
        lay.addWidget(lbl_caption)
        lay.addWidget(self.label_edit)

        self.browse_btn.clicked.connect(self._on_browse)

    def _on_browse(self) -> None:
        start = self.path_edit.text() if Path(self.path_edit.text()).is_dir() else ''
        chosen = QFileDialog.getExistingDirectory(self, '폴더 선택', start)
        if chosen:
            self.path_edit.setText(chosen)

    def path(self) -> str:
        return self.path_edit.text().strip()

    def label(self) -> str:
        return self.label_edit.text().strip()


# ---------------------------------------------------------------------------
# 빌드 워커 (백그라운드 스레드)
# ---------------------------------------------------------------------------

class _PreviewWorker(SafeWorker):
    finished = Signal(object)   # BuildResult

    def __init__(self, config: XYPlotConfig) -> None:
        super().__init__()
        self._config = config

    def work(self) -> None:
        result = build_preview(self._config)
        self.finished.emit(result)


class _SaveWorker(SafeWorker):
    finished = Signal(bool, str)   # ok, message

    def __init__(self, config: XYPlotConfig) -> None:
        super().__init__()
        self._config = config

    def work(self) -> None:
        result = build_plot(self._config)
        if result.success:
            ok, msg = save_image(result.image, self._config)
        else:
            ok, msg = False, result.error_msg or '렌더링 실패'
        self.finished.emit(ok, msg)


# ---------------------------------------------------------------------------
# 미리보기 팝업 — 공용 ImageViewerDialog에 저장 버튼 2개만 추가
# ---------------------------------------------------------------------------

class _XYPreviewDialog(ImageViewerDialog):
    """완성된 XY표 이미지를 줌/팬/맞춤으로 살펴보고, 미리보기/완성본을 저장하는 다이얼로그."""

    def __init__(self, result: BuildResult, config: XYPlotConfig,
                 parent: QWidget | None = None) -> None:
        self._result    = result
        self._config     = config
        self._pil_image  = result.image

        self.save_preview_btn = QPushButton('미리보기 저장')
        self.save_final_btn   = QPushButton('완성본 저장')
        self.save_preview_btn.clicked.connect(self._save_preview)
        self.save_final_btn.clicked.connect(self._save_final)

        qimage = pil_to_qimage(result.image)
        super().__init__(
            qimage, title='미리보기', parent=parent,
            extra_toolbar_widgets=[self.save_preview_btn, self.save_final_btn],
        )

    def _save_preview(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, '미리보기 저장', '', 'JPEG (*.jpg);;모든 파일 (*.*)',
        )
        if not path:
            return
        ok, msg = save_preview_image(self._pil_image, path)
        QMessageBox.information(self, '결과', msg)

    def _save_final(self) -> None:
        if not self._config.save_path:
            path, _ = QFileDialog.getSaveFileName(
                self, '완성본 저장', '',
                'PNG (*.png);;WEBP (*.webp);;JPEG (*.jpg);;모든 파일 (*.*)',
            )
            if not path:
                return
            self._config.save_path = path

        full_img = self._result.full_image or self._result.image
        ok, msg = save_image(full_img, self._config)
        if ok:
            QMessageBox.information(self, '결과', msg)
        else:
            QMessageBox.critical(self, '실패', msg)


# ---------------------------------------------------------------------------
# 메인 탭
# ---------------------------------------------------------------------------

class XYPlotTab(QWidget):
    """XY표 만들기 탭."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._num_cores: int = 1
        self._folder_rows: list[_FolderRow] = []
        # 격자 라벨 입력칸: _label_entries[r][c] -> QLineEdit | None
        # r=0 은 열 라벨 행, c=0 은 행 라벨 열. [0][0] 은 항상 None(빈 모서리칸).
        self._label_entries: list[list[Optional[QLineEdit]]] = []
        self._preview_worker: _PreviewWorker | None = None
        self._save_worker:    _SaveWorker | None = None

        self._build_ui()
        self._connect_signals()
        self._on_mode_change()
        self._toggle_custom_resize()
        self._toggle_title()
        self._toggle_lbl_fs()
        self._toggle_pad()
        self._toggle_ds()
        self._toggle_save_opts()
        self._rebuild_grid()

        # 격자 영역은 인라인 setStyleSheet()으로 색을 직접 칠하므로,
        # 전역 QSS 재적용만으로는 테마 전환이 반영되지 않는다.
        # 테마가 바뀔 때마다 _apply_theme_colors()가 자동 호출되도록 등록한다.
        register_theme_listener(self._apply_theme_colors)

    def _apply_theme_colors(self) -> None:
        """테마가 바뀔 때마다 호출되어, 인라인 스타일이 적용된 격자 영역을 다시 칠한다."""
        colors = current_colors()
        self.grid_scroll.setStyleSheet(f"background-color: {colors['bg_input']};")
        self.grid_inner.setStyleSheet(f"background-color: {colors['bg_input']};")
        self._rebuild_grid()   # 셀 위젯들도 새 팔레트로 다시 생성

    def closeEvent(self, event) -> None:
        unregister_theme_listener(self._apply_theme_colors)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 좌측: 설정 패널 (스크롤) ──────────────────────────
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setMinimumWidth(220)

        left_content = QWidget()
        left_layout  = QVBoxLayout(left_content)
        left_layout.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                       PADDING_SMALL, PADDING_SMALL)
        left_layout.setSpacing(SPACING_SMALL)
        left_scroll.setWidget(left_content)

        self._build_folder_group(left_layout)
        self._build_fill_mode_group(left_layout)
        self._build_sort_group(left_layout)
        self._build_axis_group(left_layout)
        self._build_cell_group(left_layout)
        self._build_resize_group(left_layout)
        self._build_title_group(left_layout)
        self._build_label_group(left_layout)
        self._build_padding_group(left_layout)
        self._build_save_group(left_layout)
        self._build_action_group(left_layout)
        left_layout.addStretch()

        # ── 우측: 라벨 입력 격자 ──────────────────────────────
        right = self._build_right_panel()

        splitter.addWidget(left_scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([320, 680])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        root.addWidget(splitter)

    # ── 1. 폴더 입력 방식 ────────────────────────────────────

    def _build_folder_group(self, parent_layout: QVBoxLayout) -> None:
        grp = QGroupBox('폴더 입력 방식')
        lay = QVBoxLayout(grp)
        lay.setSpacing(SPACING_SMALL)

        mode_row = QHBoxLayout()
        self.manual_rb = QRadioButton('셀프 선택')
        self.auto_rb   = QRadioButton('폴더 자동 감지')
        self.auto_rb.setChecked(True)
        self._mode_grp = QButtonGroup(self)
        self._mode_grp.addButton(self.manual_rb, 0)
        self._mode_grp.addButton(self.auto_rb,   1)
        mode_row.addWidget(self.manual_rb)
        mode_row.addWidget(self.auto_rb)
        mode_row.addStretch()
        lay.addLayout(mode_row)

        # 셀프선택 영역
        self.manual_frame = QGroupBox('폴더 목록')
        manual_lay = QVBoxLayout(self.manual_frame)
        manual_lay.setSpacing(2)

        self.folder_list_widget = QWidget()
        self.folder_list_layout = QVBoxLayout(self.folder_list_widget)
        self.folder_list_layout.setContentsMargins(0, 0, 0, 0)
        self.folder_list_layout.setSpacing(1)
        manual_lay.addWidget(self.folder_list_widget)

        btn_row = QHBoxLayout()
        self.add_folder_btn    = QPushButton('+ 폴더 추가')
        self.remove_folder_btn = QPushButton('− 마지막 제거')
        btn_row.addWidget(self.add_folder_btn)
        btn_row.addWidget(self.remove_folder_btn)
        btn_row.addStretch()
        manual_lay.addLayout(btn_row)
        lay.addWidget(self.manual_frame)

        # 자동감지 영역 — entry가 늘어나고 선택버튼은 고정폭으로 우측에
        self.auto_frame = QGroupBox('상위 폴더')
        auto_lay = QHBoxLayout(self.auto_frame)
        self.parent_edit = QLineEdit()
        self.parent_browse_btn = QPushButton('선택')
        self.parent_browse_btn.setFixedWidth(48)
        auto_lay.addWidget(self.parent_edit, stretch=1)
        auto_lay.addWidget(self.parent_browse_btn)
        lay.addWidget(self.auto_frame)

        parent_layout.addWidget(grp)

    # ── 2. 빈 칸 채우기 방식 ──────────────────────────────────

    def _build_fill_mode_group(self, parent_layout: QVBoxLayout) -> None:
        grp = QGroupBox('빈 칸 채우기 방식')
        lay = QVBoxLayout(grp)
        self.fill_grid_rb = QRadioButton('격자 우선  (격자 크기 고정, 부족한 칸은 NO IMAGE)')
        self.fill_data_rb = QRadioButton('데이터 우선  (폴더·이미지 수 기준으로 표 크기 결정)')
        self.fill_grid_rb.setChecked(True)
        self._fill_grp = QButtonGroup(self)
        self._fill_grp.addButton(self.fill_grid_rb, 0)
        self._fill_grp.addButton(self.fill_data_rb, 1)
        lay.addWidget(self.fill_grid_rb)
        lay.addWidget(self.fill_data_rb)
        parent_layout.addWidget(grp)

    # ── 3. 이미지 정렬 순서 ───────────────────────────────────

    def _build_sort_group(self, parent_layout: QVBoxLayout) -> None:
        grp = QGroupBox('이미지 정렬 순서')
        lay = QVBoxLayout(grp)
        lay.setSpacing(SPACING_SMALL)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel('기준:'))
        self.sort_name_rb = QRadioButton('이름')
        self.sort_date_rb = QRadioButton('생성날짜')
        self.sort_size_rb = QRadioButton('크기')
        self.sort_name_rb.setChecked(True)
        self._sort_key_grp = QButtonGroup(self)
        self._sort_key_grp.addButton(self.sort_name_rb, 0)
        self._sort_key_grp.addButton(self.sort_date_rb, 1)
        self._sort_key_grp.addButton(self.sort_size_rb, 2)
        for w in (self.sort_name_rb, self.sort_date_rb, self.sort_size_rb):
            key_row.addWidget(w)
        key_row.addStretch()
        lay.addLayout(key_row)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel('방향:'))
        self.sort_asc_rb  = QRadioButton('오름차순')
        self.sort_desc_rb = QRadioButton('내림차순')
        self.sort_asc_rb.setChecked(True)
        self._sort_dir_grp = QButtonGroup(self)
        self._sort_dir_grp.addButton(self.sort_asc_rb,  0)
        self._sort_dir_grp.addButton(self.sort_desc_rb, 1)
        dir_row.addWidget(self.sort_asc_rb)
        dir_row.addWidget(self.sort_desc_rb)
        dir_row.addStretch()
        lay.addLayout(dir_row)

        parent_layout.addWidget(grp)

    # ── 4. 이미지 배치 방향 (행수/열수는 여기 없음 — 우측 컨트롤바) ──

    def _build_axis_group(self, parent_layout: QVBoxLayout) -> None:
        grp = QGroupBox('이미지 배치 방향  (행=가로로 | 열=세로로)')
        lay = QHBoxLayout(grp)
        self.axis_row_rb = QRadioButton('행')
        self.axis_col_rb = QRadioButton('열')
        self.axis_row_rb.setChecked(True)
        self._axis_grp = QButtonGroup(self)
        self._axis_grp.addButton(self.axis_row_rb, 0)
        self._axis_grp.addButton(self.axis_col_rb, 1)
        self.swap_axis_btn = QPushButton('행↔열 스왑')
        lay.addWidget(self.axis_row_rb)
        lay.addWidget(self.axis_col_rb)
        lay.addWidget(self.swap_axis_btn)
        lay.addStretch()
        parent_layout.addWidget(grp)

    # ── 5. 이미지 셀 크기 (바짝붙이기 / 최장변 기준 정사각형) ────

    def _build_cell_group(self, parent_layout: QVBoxLayout) -> None:
        grp = QGroupBox('이미지 셀 크기')
        lay = QVBoxLayout(grp)
        self.cell_tight_rb  = QRadioButton('바짝붙이기 (이미지 크기 그대로)')
        self.cell_square_rb = QRadioButton('최장변 기준 정사각형 공간 확보')
        self.cell_tight_rb.setChecked(True)
        self._cell_grp = QButtonGroup(self)
        self._cell_grp.addButton(self.cell_tight_rb,  0)
        self._cell_grp.addButton(self.cell_square_rb, 1)
        lay.addWidget(self.cell_tight_rb)
        lay.addWidget(self.cell_square_rb)
        parent_layout.addWidget(grp)

    # ── 6. 혼합 해상도 처리 (셀 크기와 별개 그룹) ────────────

    def _build_resize_group(self, parent_layout: QVBoxLayout) -> None:
        grp = QGroupBox('혼합 해상도 처리')
        lay = QVBoxLayout(grp)
        lay.setSpacing(SPACING_SMALL)

        base_row = QHBoxLayout()
        self.resize_largest_rb  = QRadioButton('최대 기준')
        self.resize_smallest_rb = QRadioButton('최소 기준')
        self.resize_custom_rb   = QRadioButton('직접 지정')
        self.resize_largest_rb.setChecked(True)
        self._resize_grp = QButtonGroup(self)
        self._resize_grp.addButton(self.resize_largest_rb,  0)
        self._resize_grp.addButton(self.resize_smallest_rb, 1)
        self._resize_grp.addButton(self.resize_custom_rb,   2)
        for w in (self.resize_largest_rb, self.resize_smallest_rb, self.resize_custom_rb):
            base_row.addWidget(w)
        base_row.addStretch()
        lay.addLayout(base_row)

        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel('W:'))
        self.resize_w_edit = QLineEdit('512')
        self.resize_w_edit.setFixedWidth(50)
        custom_row.addWidget(self.resize_w_edit)
        custom_row.addWidget(QLabel('H:'))
        self.resize_h_edit = QLineEdit('512')
        self.resize_h_edit.setFixedWidth(50)
        custom_row.addWidget(self.resize_h_edit)
        custom_row.addStretch()
        lay.addLayout(custom_row)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel('처리 방식:'))
        self.method_scale_rb = QRadioButton('스케일 (종횡비 유지+레터박스)')
        self.method_crop_rb  = QRadioButton('크롭 (중앙 기준)')
        self.method_scale_rb.setChecked(True)
        self._method_grp = QButtonGroup(self)
        self._method_grp.addButton(self.method_scale_rb, 0)
        self._method_grp.addButton(self.method_crop_rb,  1)
        method_row.addWidget(self.method_scale_rb)
        method_row.addWidget(self.method_crop_rb)
        method_row.addStretch()
        lay.addLayout(method_row)

        parent_layout.addWidget(grp)

    # ── 7. 제목 ───────────────────────────────────────────────

    def _build_title_group(self, parent_layout: QVBoxLayout) -> None:
        grp = QGroupBox('제목')
        lay = QVBoxLayout(grp)
        lay.setSpacing(SPACING_SMALL)

        row1 = QHBoxLayout()
        self.title_en_chk = QCheckBox('제목 표시')
        self.title_edit    = QLineEdit()
        row1.addWidget(self.title_en_chk)
        row1.addWidget(self.title_edit, stretch=1)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.title_fs_auto_chk = QCheckBox('글자크기 자동')
        self.title_fs_auto_chk.setChecked(True)
        row2.addWidget(self.title_fs_auto_chk)
        row2.addWidget(QLabel('크기:'))
        self.title_fs_edit = QLineEdit('36')
        self.title_fs_edit.setFixedWidth(50)
        row2.addWidget(self.title_fs_edit)
        row2.addStretch()
        lay.addLayout(row2)

        parent_layout.addWidget(grp)

    # ── 8. 라벨 글자 ──────────────────────────────────────────

    def _build_label_group(self, parent_layout: QVBoxLayout) -> None:
        grp = QGroupBox('라벨 글자')
        lay = QVBoxLayout(grp)
        lay.setSpacing(SPACING_SMALL)

        row1 = QHBoxLayout()
        self.lbl_auto_rb   = QRadioButton('자동')
        self.lbl_fit_rb    = QRadioButton('핏')
        self.lbl_manual_rb = QRadioButton('직접 설정')
        self.lbl_auto_rb.setChecked(True)
        self._lbl_fs_grp = QButtonGroup(self)
        self._lbl_fs_grp.addButton(self.lbl_auto_rb,   0)
        self._lbl_fs_grp.addButton(self.lbl_fit_rb,    1)
        self._lbl_fs_grp.addButton(self.lbl_manual_rb, 2)
        for w in (self.lbl_auto_rb, self.lbl_fit_rb, self.lbl_manual_rb):
            row1.addWidget(w)
        row1.addWidget(QLabel('크기:'))
        self.lbl_fs_edit = QLineEdit('18')
        self.lbl_fs_edit.setFixedWidth(50)
        row1.addWidget(self.lbl_fs_edit)
        row1.addStretch()
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel('가로 정렬:'))
        self.align_h_left_rb   = QRadioButton('좌')
        self.align_h_center_rb = QRadioButton('중앙')
        self.align_h_right_rb  = QRadioButton('우')
        self.align_h_center_rb.setChecked(True)
        self._align_h_grp = QButtonGroup(self)
        self._align_h_grp.addButton(self.align_h_left_rb,   0)
        self._align_h_grp.addButton(self.align_h_center_rb, 1)
        self._align_h_grp.addButton(self.align_h_right_rb,  2)
        for w in (self.align_h_left_rb, self.align_h_center_rb, self.align_h_right_rb):
            row2.addWidget(w)
        row2.addStretch()
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel('세로 정렬:'))
        self.align_v_top_rb    = QRadioButton('상')
        self.align_v_center_rb = QRadioButton('중앙')
        self.align_v_bottom_rb = QRadioButton('하')
        self.align_v_center_rb.setChecked(True)
        self._align_v_grp = QButtonGroup(self)
        self._align_v_grp.addButton(self.align_v_top_rb,    0)
        self._align_v_grp.addButton(self.align_v_center_rb, 1)
        self._align_v_grp.addButton(self.align_v_bottom_rb, 2)
        for w in (self.align_v_top_rb, self.align_v_center_rb, self.align_v_bottom_rb):
            row3.addWidget(w)
        row3.addStretch()
        lay.addLayout(row3)

        parent_layout.addWidget(grp)

    # ── 9. 패딩 / 스케일조절 (원본 명칭 그대로) ───────────────

    def _build_padding_group(self, parent_layout: QVBoxLayout) -> None:
        grp = QGroupBox('패딩 / 스케일조절')
        lay = QHBoxLayout(grp)
        lay.setSpacing(SPACING_SMALL)

        self.pad_en_chk = QCheckBox('패딩')
        self.pad_px_edit = QLineEdit('4')
        self.pad_px_edit.setFixedWidth(45)
        lay.addWidget(self.pad_en_chk)
        lay.addWidget(self.pad_px_edit)
        lay.addWidget(QLabel('px'))

        lay.addSpacing(16)
        lay.addWidget(QLabel('스케일조절'))
        self.ds_en_chk = QCheckBox()
        self.ds_pct_edit = QLineEdit('100')
        self.ds_pct_edit.setFixedWidth(45)
        lay.addWidget(self.ds_en_chk)
        lay.addWidget(self.ds_pct_edit)
        lay.addWidget(QLabel('%'))
        lay.addStretch()

        parent_layout.addWidget(grp)

    # ── 10. 저장 옵션 ─────────────────────────────────────────

    def _build_save_group(self, parent_layout: QVBoxLayout) -> None:
        grp = QGroupBox('저장 옵션')
        lay = QVBoxLayout(grp)
        lay.setSpacing(SPACING_SMALL)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel('포맷:'))
        self.save_png_rb  = QRadioButton('PNG')
        self.save_webp_rb = QRadioButton('WEBP')
        self.save_jpg_rb  = QRadioButton('JPG')
        self.save_png_rb.setChecked(True)
        self._save_fmt_grp = QButtonGroup(self)
        self._save_fmt_grp.addButton(self.save_png_rb,  0)
        self._save_fmt_grp.addButton(self.save_webp_rb, 1)
        self._save_fmt_grp.addButton(self.save_jpg_rb,  2)
        for w in (self.save_png_rb, self.save_webp_rb, self.save_jpg_rb):
            fmt_row.addWidget(w)
        fmt_row.addStretch()
        lay.addLayout(fmt_row)

        opt_row = QHBoxLayout()
        self.save_lossless_chk = QCheckBox('무손실')
        self.save_lossless_chk.setChecked(True)
        opt_row.addWidget(self.save_lossless_chk)
        opt_row.addWidget(QLabel('  품질:'))
        self.save_quality_edit = QLineEdit('95')
        self.save_quality_edit.setFixedWidth(45)
        opt_row.addWidget(self.save_quality_edit)
        opt_row.addWidget(QLabel('(0~100)'))
        opt_row.addStretch()
        lay.addLayout(opt_row)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel('저장 경로:'))
        self.save_path_edit = QLineEdit()
        self.save_browse_btn = QPushButton('선택')
        self.save_browse_btn.setFixedWidth(48)
        path_row.addWidget(self.save_path_edit, stretch=1)
        path_row.addWidget(self.save_browse_btn)
        lay.addLayout(path_row)

        parent_layout.addWidget(grp)

    # ── 11. 실행 버튼 ─────────────────────────────────────────

    def _build_action_group(self, parent_layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        self.preview_btn = QPushButton('미리보기')
        self.preview_btn.setProperty('accent', True)
        self.save_btn = QPushButton('완성본 저장')
        row.addWidget(self.preview_btn)
        row.addWidget(self.save_btn)
        row.addStretch()
        parent_layout.addLayout(row)

    # ── 우측: 라벨 입력 격자 ──────────────────────────────────

    def _build_right_panel(self) -> QWidget:
        right = QGroupBox('라벨 입력 격자')
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                        PADDING_SMALL, PADDING_SMALL)
        right_layout.setSpacing(SPACING_SMALL)

        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel('행 수:'))
        self.grid_rows_edit = QLineEdit('3')
        self.grid_rows_edit.setFixedWidth(40)
        ctrl_row.addWidget(self.grid_rows_edit)
        ctrl_row.addWidget(QLabel('열 수:'))
        self.grid_cols_edit = QLineEdit('3')
        self.grid_cols_edit.setFixedWidth(40)
        ctrl_row.addWidget(self.grid_cols_edit)
        self.rebuild_grid_btn = QPushButton('격자 생성')
        self.auto_grid_btn    = QPushButton('자동 격자 생성')
        ctrl_row.addWidget(self.rebuild_grid_btn)
        ctrl_row.addWidget(self.auto_grid_btn)
        ctrl_row.addStretch()
        right_layout.addLayout(ctrl_row)

        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setStyleSheet(f"background-color: {current_colors()['bg_input']};")

        self.grid_inner = QWidget()
        self.grid_layout = QGridLayout(self.grid_inner)
        self.grid_layout.setSpacing(1)
        # 실제로 화면에 보이는 배경은 QScrollArea 자체가 아니라 그 안에 들어가는
        # grid_inner 위젯이다. grid_scroll에만 색을 입히면 안쪽 위젯에 가려져
        # 보이지 않으므로, 두 위젯 모두에 동일한 색을 칠한다.
        self.grid_inner.setStyleSheet(f"background-color: {current_colors()['bg_input']};")
        self.grid_scroll.setWidget(self.grid_inner)

        right_layout.addWidget(self.grid_scroll, stretch=1)
        return right

    # ------------------------------------------------------------------
    # 시그널 연결
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self._mode_grp.idToggled.connect(lambda *_: self._on_mode_change())
        self.add_folder_btn.clicked.connect(self._add_folder_row)
        self.remove_folder_btn.clicked.connect(self._remove_last_folder)
        self.parent_browse_btn.clicked.connect(self._on_browse_parent)

        self._resize_grp.idToggled.connect(lambda *_: self._toggle_custom_resize())

        self.title_en_chk.toggled.connect(lambda *_: self._toggle_title())
        self.title_fs_auto_chk.toggled.connect(lambda *_: self._toggle_title())

        self._lbl_fs_grp.idToggled.connect(lambda *_: self._toggle_lbl_fs())

        self.pad_en_chk.toggled.connect(lambda *_: self._toggle_pad())
        self.ds_en_chk.toggled.connect(lambda *_: self._toggle_ds())

        self._save_fmt_grp.idToggled.connect(lambda *_: self._toggle_save_opts())
        self.save_lossless_chk.toggled.connect(lambda *_: self._toggle_save_opts())
        self.save_browse_btn.clicked.connect(self._on_browse_save_path)

        self.swap_axis_btn.clicked.connect(self._swap_axis)
        self.rebuild_grid_btn.clicked.connect(self._rebuild_grid)
        self.auto_grid_btn.clicked.connect(self._auto_grid)

        self.preview_btn.clicked.connect(self._on_preview)
        self.save_btn.clicked.connect(self._on_save)

    # ------------------------------------------------------------------
    # 공개 API (main_window 에서 호출)
    # ------------------------------------------------------------------

    def set_num_cores(self, n: int) -> None:
        self._num_cores = n

    def get_settings(self) -> dict:
        folders = [{'path': r.path(), 'label': r.label()} for r in self._folder_rows]
        grid_labels = [
            [(e.text() if e else '') for e in row]
            for row in self._label_entries
        ]
        return {
            'xy_mode':           'manual' if self.manual_rb.isChecked() else 'folder',
            'xy_parent_folder':  self.parent_edit.text(),
            'xy_folders':        folders,
            'xy_grid_rows':      self._safe_int(self.grid_rows_edit.text(), 3),
            'xy_grid_cols':      self._safe_int(self.grid_cols_edit.text(), 3),
            'xy_grid_labels':    grid_labels,
            'xy_fill_mode':      'data' if self.fill_data_rb.isChecked() else 'grid',
            'xy_axis':           AXIS_COL if self.axis_col_rb.isChecked() else AXIS_ROW,
            'xy_sort_key':       self._sort_key_str(),
            'xy_sort_dir':       'desc' if self.sort_desc_rb.isChecked() else 'asc',
            'xy_cell_mode':      CELL_LONGEST_EDGE if self.cell_square_rb.isChecked() else CELL_TIGHT,
            'xy_resize_base':    self._resize_base_str(),
            'xy_resize_method':  METHOD_CROP if self.method_crop_rb.isChecked() else METHOD_SCALE,
            'xy_resize_w':       self._safe_int(self.resize_w_edit.text(), 512),
            'xy_resize_h':       self._safe_int(self.resize_h_edit.text(), 512),
            'xy_title_en':       self.title_en_chk.isChecked(),
            'xy_title_text':     self.title_edit.text(),
            'xy_title_fs_auto':  self.title_fs_auto_chk.isChecked(),
            'xy_title_fs':       self._safe_int(self.title_fs_edit.text(), 36),
            'xy_lbl_fs_mode':    self._lbl_fs_mode_str(),
            'xy_lbl_fs':         self._safe_int(self.lbl_fs_edit.text(), 18),
            'xy_lbl_align_h':    self._align_h_str(),
            'xy_lbl_align_v':    self._align_v_str(),
            'xy_pad_en':         self.pad_en_chk.isChecked(),
            'xy_pad_px':         self._safe_int(self.pad_px_edit.text(), 4),
            'xy_ds_en':          self.ds_en_chk.isChecked(),
            'xy_ds_pct':         self._safe_int(self.ds_pct_edit.text(), 100),
            'xy_save_fmt':       self._save_fmt_str(),
            'xy_save_lossless':  self.save_lossless_chk.isChecked(),
            'xy_save_quality':   self._safe_int(self.save_quality_edit.text(), 95),
            'xy_save_path':      self.save_path_edit.text(),
        }

    def load_settings(self, s: dict) -> None:
        if 'xy_mode' in s:
            is_manual = s['xy_mode'] == 'manual'
            self.manual_rb.setChecked(is_manual)
            self.auto_rb.setChecked(not is_manual)
        if 'xy_parent_folder' in s:
            self.parent_edit.setText(str(s['xy_parent_folder']))
        if 'xy_fill_mode' in s:
            self.fill_data_rb.setChecked(s['xy_fill_mode'] == 'data')
            self.fill_grid_rb.setChecked(s['xy_fill_mode'] != 'data')
        if 'xy_axis' in s:
            self.axis_col_rb.setChecked(s['xy_axis'] == AXIS_COL)
            self.axis_row_rb.setChecked(s['xy_axis'] != AXIS_COL)
        sort_key = s.get('xy_sort_key', 'name')
        self.sort_name_rb.setChecked(sort_key == 'name')
        self.sort_date_rb.setChecked(sort_key == 'date')
        self.sort_size_rb.setChecked(sort_key == 'size')
        sort_dir = s.get('xy_sort_dir', 'asc')
        self.sort_asc_rb.setChecked(sort_dir != 'desc')
        self.sort_desc_rb.setChecked(sort_dir == 'desc')
        if 'xy_cell_mode' in s:
            self.cell_square_rb.setChecked(s['xy_cell_mode'] == CELL_LONGEST_EDGE)
            self.cell_tight_rb.setChecked(s['xy_cell_mode'] != CELL_LONGEST_EDGE)
        resize_base = s.get('xy_resize_base', RESIZE_LARGEST)
        self.resize_largest_rb.setChecked(resize_base == RESIZE_LARGEST)
        self.resize_smallest_rb.setChecked(resize_base == RESIZE_SMALLEST)
        self.resize_custom_rb.setChecked(resize_base == RESIZE_CUSTOM)
        if 'xy_resize_method' in s:
            self.method_crop_rb.setChecked(s['xy_resize_method'] == METHOD_CROP)
            self.method_scale_rb.setChecked(s['xy_resize_method'] != METHOD_CROP)
        if 'xy_resize_w' in s: self.resize_w_edit.setText(str(s['xy_resize_w']))
        if 'xy_resize_h' in s: self.resize_h_edit.setText(str(s['xy_resize_h']))
        if 'xy_title_en' in s: self.title_en_chk.setChecked(bool(s['xy_title_en']))
        if 'xy_title_text' in s: self.title_edit.setText(str(s['xy_title_text']))
        if 'xy_title_fs_auto' in s: self.title_fs_auto_chk.setChecked(bool(s['xy_title_fs_auto']))
        if 'xy_title_fs' in s: self.title_fs_edit.setText(str(s['xy_title_fs']))
        lbl_mode = s.get('xy_lbl_fs_mode', FONT_AUTO)
        self.lbl_auto_rb.setChecked(lbl_mode == FONT_AUTO)
        self.lbl_fit_rb.setChecked(lbl_mode == FONT_FIT)
        self.lbl_manual_rb.setChecked(lbl_mode == FONT_MANUAL)
        if 'xy_lbl_fs' in s: self.lbl_fs_edit.setText(str(s['xy_lbl_fs']))
        align_h = s.get('xy_lbl_align_h', 'center')
        self.align_h_left_rb.setChecked(align_h == 'left')
        self.align_h_center_rb.setChecked(align_h == 'center')
        self.align_h_right_rb.setChecked(align_h == 'right')
        align_v = s.get('xy_lbl_align_v', 'center')
        self.align_v_top_rb.setChecked(align_v == 'top')
        self.align_v_center_rb.setChecked(align_v == 'center')
        self.align_v_bottom_rb.setChecked(align_v == 'bottom')
        if 'xy_pad_en' in s: self.pad_en_chk.setChecked(bool(s['xy_pad_en']))
        if 'xy_pad_px' in s: self.pad_px_edit.setText(str(s['xy_pad_px']))
        if 'xy_ds_en' in s: self.ds_en_chk.setChecked(bool(s['xy_ds_en']))
        if 'xy_ds_pct' in s: self.ds_pct_edit.setText(str(s['xy_ds_pct']))
        save_fmt = s.get('xy_save_fmt', 'png')
        self.save_png_rb.setChecked(save_fmt == 'png')
        self.save_webp_rb.setChecked(save_fmt == 'webp')
        self.save_jpg_rb.setChecked(save_fmt == 'jpg')
        if 'xy_save_lossless' in s: self.save_lossless_chk.setChecked(bool(s['xy_save_lossless']))
        if 'xy_save_quality' in s: self.save_quality_edit.setText(str(s['xy_save_quality']))
        if 'xy_save_path' in s: self.save_path_edit.setText(str(s['xy_save_path']))

        # 폴더 목록 복원
        while self._folder_rows:
            self._remove_last_folder()
        for entry in s.get('xy_folders', []):
            self._add_folder_row(entry.get('path', ''), entry.get('label', ''))

        # 격자 복원
        if 'xy_grid_rows' in s: self.grid_rows_edit.setText(str(s['xy_grid_rows']))
        if 'xy_grid_cols' in s: self.grid_cols_edit.setText(str(s['xy_grid_cols']))
        self._rebuild_grid()
        for r, row in enumerate(s.get('xy_grid_labels', [])):
            if r >= len(self._label_entries):
                break
            for c, val in enumerate(row):
                if c >= len(self._label_entries[r]):
                    break
                entry = self._label_entries[r][c]
                if entry is not None and val:
                    entry.setText(val)

        self._on_mode_change()
        self._toggle_custom_resize()
        self._toggle_title()
        self._toggle_lbl_fs()
        self._toggle_pad()
        self._toggle_ds()
        self._toggle_save_opts()

    # ------------------------------------------------------------------
    # 내부 헬퍼 — 문자열 변환
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_int(text: str, default: int) -> int:
        try:
            return int(text.strip())
        except (ValueError, AttributeError):
            return default

    def _sort_key_str(self) -> str:
        if self.sort_date_rb.isChecked(): return 'date'
        if self.sort_size_rb.isChecked(): return 'size'
        return 'name'

    def _resize_base_str(self) -> str:
        if self.resize_smallest_rb.isChecked(): return RESIZE_SMALLEST
        if self.resize_custom_rb.isChecked():   return RESIZE_CUSTOM
        return RESIZE_LARGEST

    def _lbl_fs_mode_str(self) -> str:
        if self.lbl_fit_rb.isChecked():    return FONT_FIT
        if self.lbl_manual_rb.isChecked(): return FONT_MANUAL
        return FONT_AUTO

    def _align_h_str(self) -> str:
        if self.align_h_left_rb.isChecked():  return 'left'
        if self.align_h_right_rb.isChecked(): return 'right'
        return 'center'

    def _align_v_str(self) -> str:
        if self.align_v_top_rb.isChecked():    return 'top'
        if self.align_v_bottom_rb.isChecked(): return 'bottom'
        return 'center'

    def _save_fmt_str(self) -> str:
        if self.save_webp_rb.isChecked(): return 'webp'
        if self.save_jpg_rb.isChecked():  return 'jpg'
        return 'png'

    # ------------------------------------------------------------------
    # 토글 핸들러 (원본 _toggle_* 1:1 대응)
    # ------------------------------------------------------------------

    def _on_mode_change(self) -> None:
        is_manual = self.manual_rb.isChecked()
        self.manual_frame.setVisible(is_manual)
        self.auto_frame.setVisible(not is_manual)

    def _toggle_custom_resize(self) -> None:
        enabled = self.resize_custom_rb.isChecked()
        self.resize_w_edit.setEnabled(enabled)
        self.resize_h_edit.setEnabled(enabled)

    def _toggle_title(self) -> None:
        en   = self.title_en_chk.isChecked()
        auto = self.title_fs_auto_chk.isChecked()
        self.title_edit.setEnabled(en)
        self.title_fs_edit.setEnabled(en and not auto)

    def _toggle_lbl_fs(self) -> None:
        self.lbl_fs_edit.setEnabled(self.lbl_manual_rb.isChecked())

    def _toggle_pad(self) -> None:
        self.pad_px_edit.setEnabled(self.pad_en_chk.isChecked())

    def _toggle_ds(self) -> None:
        self.ds_pct_edit.setEnabled(self.ds_en_chk.isChecked())

    def _toggle_save_opts(self) -> None:
        fmt = self._save_fmt_str()
        if fmt == 'png':
            self.save_lossless_chk.setEnabled(False)
            self.save_quality_edit.setEnabled(False)
        elif fmt == 'webp':
            self.save_lossless_chk.setEnabled(True)
            self.save_quality_edit.setEnabled(not self.save_lossless_chk.isChecked())
        else:  # jpg
            self.save_lossless_chk.setEnabled(False)
            self.save_quality_edit.setEnabled(True)

    # ------------------------------------------------------------------
    # 폴더 관리 (셀프 선택)
    # ------------------------------------------------------------------

    def _add_folder_row(self, path: str = '', label: str = '') -> None:
        row = _FolderRow(len(self._folder_rows), path, label, self)
        self._folder_rows.append(row)
        self.folder_list_layout.addWidget(row)

    def _remove_last_folder(self) -> None:
        if not self._folder_rows:
            return
        row = self._folder_rows.pop()
        self.folder_list_layout.removeWidget(row)
        row.deleteLater()

    def _on_browse_parent(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, '상위 폴더 선택', self.parent_edit.text())
        if chosen:
            self.parent_edit.setText(chosen)

    def _on_browse_save_path(self) -> None:
        fmt = self._save_fmt_str()
        ext_map = {'png': 'PNG (*.png)', 'webp': 'WEBP (*.webp)', 'jpg': 'JPEG (*.jpg)'}
        path, _ = QFileDialog.getSaveFileName(
            self, '저장 경로 지정', '', ext_map.get(fmt, 'PNG (*.png)'),
        )
        if path:
            self.save_path_edit.setText(path)

    # ------------------------------------------------------------------
    # 라벨 입력 격자 (우측 패널)
    # ------------------------------------------------------------------

    def _rebuild_grid(self) -> None:
        # 기존 위젯 제거
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._label_entries.clear()

        n_rows = max(1, min(GRID_MAX, self._safe_int(self.grid_rows_edit.text(), 3)))
        n_cols = max(1, min(GRID_MAX, self._safe_int(self.grid_cols_edit.text(), 3)))

        colors = current_colors()
        corner_style = (
            f"background-color: {colors['border']}; border: 1px solid {colors['separator']};"
        )
        label_cell_style = f"background-color: {colors['bg_panel']};"
        image_cell_style = (
            f"background-color: {colors['bg_dark']}; color: {colors['text_secondary']};"
        )

        for r in range(n_rows + 1):
            row_entries: list[Optional[QLineEdit]] = []
            for c in range(n_cols + 1):
                if r == 0 and c == 0:
                    corner = QLabel('')
                    corner.setFixedSize(64, 28)
                    corner.setStyleSheet(corner_style)
                    self.grid_layout.addWidget(corner, r, c)
                    row_entries.append(None)
                elif r == 0:
                    e = QLineEdit(f'열{c}')
                    e.setFixedSize(64, 28)
                    e.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    e.setStyleSheet(label_cell_style)
                    self.grid_layout.addWidget(e, r, c)
                    row_entries.append(e)
                elif c == 0:
                    e = QLineEdit(f'행{r}')
                    e.setFixedSize(64, 28)
                    e.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    e.setStyleSheet(label_cell_style)
                    self.grid_layout.addWidget(e, r, c)
                    row_entries.append(e)
                else:
                    ph = QLabel('[이미지]')
                    ph.setFixedSize(64, 28)
                    ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    ph.setStyleSheet(image_cell_style)
                    self.grid_layout.addWidget(ph, r, c)
                    row_entries.append(None)
            self._label_entries.append(row_entries)

    def _auto_grid(self) -> None:
        """
        현재 설정된 폴더(셀프/자동)와 각 폴더 안의 이미지 수를 바탕으로
        행×열을 자동 계산해 격자를 생성한다.

        - axis == AXIS_ROW: 폴더 수 → 행, 최대 이미지 수 → 열
        - axis == AXIS_COL: 폴더 수 → 열, 최대 이미지 수 → 행
        결과는 GRID_MAX(50)로 클램프된다.
        """
        sort_order = f'{self._sort_key_str()}_{"desc" if self.sort_desc_rb.isChecked() else "asc"}'

        if self.manual_rb.isChecked():
            folder_paths = [r.path() for r in self._folder_rows if r.path()]
        else:
            parent_p = self.parent_edit.text().strip()
            if not parent_p or not os.path.isdir(parent_p):
                QMessageBox.critical(self, '오류', '유효한 상위 폴더를 지정해주세요.')
                return
            folder_paths = sorted(
                [str(d) for d in Path(parent_p).iterdir() if d.is_dir()],
                key=lambda d: d.lower(),
            )

        if not folder_paths:
            QMessageBox.critical(self, '오류', '폴더가 지정되지 않았습니다.')
            return

        n_folders = len(folder_paths)
        max_images = max(
            (len(collect_images(fp, sort_order)) for fp in folder_paths),
            default=0,
        )
        if max_images == 0:
            QMessageBox.warning(self, '경고',
                                '폴더 안에 이미지가 없습니다.\n행/열을 폴더 수 기준으로만 설정합니다.')
            max_images = 1

        n_folders  = max(1, min(GRID_MAX, n_folders))
        max_images = max(1, min(GRID_MAX, max_images))

        if self.axis_row_rb.isChecked():
            self.grid_rows_edit.setText(str(n_folders))
            self.grid_cols_edit.setText(str(max_images))
        else:
            self.grid_rows_edit.setText(str(max_images))
            self.grid_cols_edit.setText(str(n_folders))

        self._rebuild_grid()

    def _swap_axis(self) -> None:
        """격자의 첫 행/첫 열 라벨을 전치한다. 축 방향 라디오버튼 자체는 건드리지 않는다."""
        if not self._label_entries:
            return

        first_row = self._label_entries[0]
        col_lbls = [
            (first_row[c].text() if first_row[c] else '')
            for c in range(1, len(first_row))
        ]
        row_lbls = [
            (self._label_entries[r][0].text() if self._label_entries[r][0] else '')
            for r in range(1, len(self._label_entries))
        ]

        old_rows = self._safe_int(self.grid_rows_edit.text(), 3)
        old_cols = self._safe_int(self.grid_cols_edit.text(), 3)
        self.grid_rows_edit.setText(str(old_cols))
        self.grid_cols_edit.setText(str(old_rows))
        self._rebuild_grid()

        new_first_row = self._label_entries[0]
        for c in range(1, len(new_first_row)):
            e = new_first_row[c]
            if e is not None:
                e.setText(row_lbls[c - 1] if (c - 1) < len(row_lbls) else '')

        for r in range(1, len(self._label_entries)):
            e = self._label_entries[r][0]
            if e is not None:
                e.setText(col_lbls[r - 1] if (r - 1) < len(col_lbls) else '')

    # ------------------------------------------------------------------
    # 설정 수집 (XYPlotConfig 생성)
    # ------------------------------------------------------------------

    def _collect_config(self) -> Optional[XYPlotConfig]:
        try:
            if self.manual_rb.isChecked():
                entries = [
                    FolderEntry(folder_path=r.path(), label=r.label())
                    for r in self._folder_rows if r.path()
                ]
            else:
                parent_p = self.parent_edit.text().strip()
                if not parent_p or not os.path.isdir(parent_p):
                    QMessageBox.critical(self, '오류', '유효한 상위 폴더를 지정해주세요.')
                    return None
                subs = sorted(
                    [d for d in Path(parent_p).iterdir() if d.is_dir()],
                    key=lambda d: d.name.lower(),
                )
                entries = [FolderEntry(folder_path=str(d), label=d.name) for d in subs]

            if not entries:
                QMessageBox.critical(self, '오류', '폴더가 지정되지 않았습니다.')
                return None

            # 격자 라벨 수집 + 행 라벨을 폴더 엔트리 라벨로 오버라이드
            col_labels: list[str] = []
            row_labels_extra: list[str] = []
            grid_cols = self._safe_int(self.grid_cols_edit.text(), 3)
            grid_rows = self._safe_int(self.grid_rows_edit.text(), 3)

            if self._label_entries:
                first_row = self._label_entries[0]
                for c, e in enumerate(first_row):
                    if c == 0:
                        continue
                    col_labels.append(e.text() if e else '')
                for r, row in enumerate(self._label_entries[1:], 1):
                    e = row[0] if row else None
                    rl = e.text().strip() if e else ''
                    row_labels_extra.append(rl)
                    # 원본 사양: 격자 행 라벨 칸은 항상 '행N' 기본값을 가지고 있으므로
                    # (사용자가 직접 비우지 않는 한) 폴더 엔트리의 라벨을 항상 오버라이드한다.
                    # 즉 셀프 선택에서 입력한 라벨(A, B 등)이 있어도 격자 행 라벨이 최종 우선.
                    if rl and (r - 1) < len(entries):
                        entries[r - 1].label = rl

            title_fs = None if self.title_fs_auto_chk.isChecked() else self._safe_int(self.title_fs_edit.text(), 36)
            lbl_fs   = self._safe_int(self.lbl_fs_edit.text(), 18) if self.lbl_manual_rb.isChecked() else None
            ds_ratio = (self._safe_int(self.ds_pct_edit.text(), 100) / 100.0) if self.ds_en_chk.isChecked() else 1.0

            return XYPlotConfig(
                entries=entries,
                col_labels=col_labels,
                row_labels_extra=row_labels_extra,
                grid_cols=grid_cols,
                grid_rows=grid_rows,
                fill_mode='data' if self.fill_data_rb.isChecked() else 'grid',
                folder_axis=AXIS_COL if self.axis_col_rb.isChecked() else AXIS_ROW,
                sort_order=f'{self._sort_key_str()}_{"desc" if self.sort_desc_rb.isChecked() else "asc"}',
                cell_mode=CELL_LONGEST_EDGE if self.cell_square_rb.isChecked() else CELL_TIGHT,
                resize_base=self._resize_base_str(),
                resize_custom_wh=(
                    self._safe_int(self.resize_w_edit.text(), 512),
                    self._safe_int(self.resize_h_edit.text(), 512),
                ),
                resize_method=METHOD_CROP if self.method_crop_rb.isChecked() else METHOD_SCALE,
                title_enabled=self.title_en_chk.isChecked(),
                title_text=self.title_edit.text(),
                title_fontsize=title_fs,
                label_fontsize_mode=self._lbl_fs_mode_str(),
                label_fontsize=lbl_fs,
                label_align_h=self._align_h_str(),
                label_align_v=self._align_v_str(),
                padding_enabled=self.pad_en_chk.isChecked(),
                padding_px=self._safe_int(self.pad_px_edit.text(), 4),
                downscale_enabled=self.ds_en_chk.isChecked(),
                downscale_ratio=ds_ratio,
                save_path=self.save_path_edit.text(),
                save_format=self._save_fmt_str(),
                save_lossless=self.save_lossless_chk.isChecked(),
                save_quality=self._safe_int(self.save_quality_edit.text(), 95),
            )
        except Exception as e:
            QMessageBox.critical(self, '설정 오류', str(e))
            return None

    # ------------------------------------------------------------------
    # 실행 — 미리보기 / 완성본 저장
    # ------------------------------------------------------------------

    def _on_preview(self) -> None:
        cfg = self._collect_config()
        if cfg is None:
            return
        self.preview_btn.setEnabled(False)
        self._preview_worker = _PreviewWorker(cfg)
        self._preview_worker.finished.connect(self._on_preview_done)
        self._preview_worker.error.connect(self._on_worker_error)
        self._preview_worker.start()

    @Slot(object)
    def _on_preview_done(self, result: BuildResult) -> None:
        self.preview_btn.setEnabled(True)
        if not result.success:
            QMessageBox.critical(self, '미리보기 실패', result.error_msg or '알 수 없는 오류')
            return
        cfg = self._collect_config()
        dlg = _XYPreviewDialog(result, cfg, self)
        dlg.exec()

    def _on_save(self) -> None:
        cfg = self._collect_config()
        if cfg is None:
            return
        if not cfg.save_path:
            QMessageBox.critical(self, '오류', '저장 경로를 지정해주세요.')
            return

        fmt = cfg.save_format.lower()
        ext = {'jpg': '.jpg', 'jpeg': '.jpg', 'webp': '.webp'}.get(fmt, '.png')
        final_path = cfg.save_path if cfg.save_path.lower().endswith(ext) else cfg.save_path + ext
        if os.path.exists(final_path):
            answer = QMessageBox.question(
                self, '덮어쓰기 확인',
                f'이미 파일이 존재합니다.\n{final_path}\n\n덮어쓰시겠습니까?',
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.save_btn.setEnabled(False)
        self._save_worker = _SaveWorker(cfg)
        self._save_worker.finished.connect(self._on_save_done)
        self._save_worker.error.connect(self._on_worker_error)
        self._save_worker.start()

    @Slot(bool, str)
    def _on_save_done(self, ok: bool, msg: str) -> None:
        self.save_btn.setEnabled(True)
        if ok:
            QMessageBox.information(self, '완료', msg)
        else:
            QMessageBox.critical(self, '실패', msg)

    @Slot(str)
    def _on_worker_error(self, traceback_text: str) -> None:
        """워커 스레드에서 예상치 못한 예외가 발생했을 때의 안전망."""
        self.preview_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        QMessageBox.critical(
            self, '예상치 못한 오류',
            f'작업 중 오류가 발생했습니다.\n\n{traceback_text[-1500:]}',
        )
