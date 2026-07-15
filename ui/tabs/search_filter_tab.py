"""
ui/tabs/search_filter_tab.py

검색 및 분류 탭 — 파일명/크기/해상도/태그 조건 기반 파일 검색 및 삭제/이동/복사.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore    import Qt, Signal, Slot
from PySide6.QtGui     import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QCheckBox, QRadioButton,
    QPushButton, QButtonGroup, QScrollArea, QSplitter,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QSizePolicy, QFrame,
    QDialog, QTextEdit,
)

from core.search_filter import FileEntry, search_files, process_entries, get_orphan_warning
from ui.widgets.folder_selector   import FolderSelector
from ui.widgets.worker_base       import SafeWorker
from ui.widgets.image_viewer      import ImageViewerDialog
from ui.widgets.numeric_tree_item import NumericTreeItem, SORT_KEY_ROLE
from ui.constants import (
    PADDING_DEFAULT, PADDING_SMALL, SPACING_DEFAULT, SPACING_SMALL,
)


# ---------------------------------------------------------------------------
# 처리 결과 상세 로그 창 — 원본 _show_result_log(tkinter)와 동등한 기능
# ---------------------------------------------------------------------------

class _ResultLogDialog(QDialog):
    """삭제/이동/복사 처리 후 항목별 상세 로그를 보여주는 창."""

    def __init__(self, action_name: str, success: int, fail: int,
                 logs: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f'{action_name} 결과')
        self.resize(600, 400)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(PADDING_DEFAULT, PADDING_DEFAULT,
                               PADDING_DEFAULT, PADDING_DEFAULT)
        lay.setSpacing(SPACING_DEFAULT)

        summary_lbl = QLabel(f'성공: {success}건  /  실패: {fail}건')
        summary_lbl.setStyleSheet('font-weight: bold;')
        lay.addWidget(summary_lbl)

        log_text = QTextEdit()
        log_text.setReadOnly(True)
        log_text.setPlainText('\n'.join(logs))
        lay.addWidget(log_text, stretch=1)

        close_btn = QPushButton('닫기')
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _SearchWorker(SafeWorker):
    progress = Signal(int, int)
    finished = Signal(list)

    def __init__(self, folder: str, recursive: bool,
                 conditions: list, num_cores: int) -> None:
        super().__init__()
        self._folder     = folder
        self._recursive  = recursive
        self._conditions = conditions
        self._num_cores  = num_cores
        self._stop       = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def work(self) -> None:
        results = search_files(
            self._folder, self._recursive, self._conditions,
            num_cores=self._num_cores,
            progress_callback=lambda d, t: self.progress.emit(d, t),
            stop_event=self._stop,
        )
        self.finished.emit(results)


# ---------------------------------------------------------------------------
# 모드 라디오 버튼 행 헬퍼
# ---------------------------------------------------------------------------

_MODE_LABELS = [('미사용', 'unused'), ('AND', 'and'), ('OR', 'or'), ('NOT', 'not')]


def _make_mode_row(parent: QWidget) -> tuple[QHBoxLayout, QButtonGroup, dict[str, QRadioButton]]:
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACING_SMALL)
    grp  = QButtonGroup(parent)
    rbs: dict[str, QRadioButton] = {}
    for i, (lbl, val) in enumerate(_MODE_LABELS):
        rb = QRadioButton(lbl)
        if val == 'unused':
            rb.setChecked(True)
        grp.addButton(rb, i)
        layout.addWidget(rb)
        rbs[val] = rb
    layout.addStretch()
    return layout, grp, rbs


# ---------------------------------------------------------------------------
# 탭 위젯
# ---------------------------------------------------------------------------

class SearchFilterTab(QWidget):
    """검색 및 분류 탭."""

    _COLUMNS = ['✓', '파일명', '확장자', '폴더', '용량(KB)', '해상도', '태그 미리보기']
    _COL_W   = [30,   220,     55,       200,    75,         100,      260]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._folder:    str = ''
        self._num_cores: int = 1
        self._results:   list[FileEntry] = []
        self._worker: _SearchWorker | None = None
        self._selected_entry: Optional[FileEntry] = None
        self._sort_col = 1
        self._sort_asc = True
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                PADDING_SMALL, PADDING_SMALL)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 왼쪽: 설정 패널 (스크롤) ─────────────────────────
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # 최대폭을 고정하지 않고 최소폭만 지정한다.
        # (최대폭 고정 시 QSplitter 핸들을 드래그해도 패널이 넓어지지 않는 문제가 있었음)
        left_scroll.setMinimumWidth(200)

        left_content = QWidget()
        left_layout  = QVBoxLayout(left_content)
        left_layout.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                       PADDING_SMALL, PADDING_SMALL)
        left_layout.setSpacing(SPACING_SMALL)
        left_scroll.setWidget(left_content)

        # 경로 설정
        grp_path = QGroupBox('경로 설정')
        path_lay = QVBoxLayout(grp_path)
        self.indep_chk = QCheckBox('독립적인 경로 사용')
        self.indep_sel = FolderSelector('독립 폴더 선택')
        self.indep_sel.setEnabled(False)
        self.recursive_chk = QCheckBox('하위 폴더 포함')
        self.recursive_chk.setChecked(True)
        path_lay.addWidget(self.indep_chk)
        path_lay.addWidget(self.indep_sel)
        path_lay.addWidget(self.recursive_chk)
        left_layout.addWidget(grp_path)

        # 검색 조건
        grp_cond = QGroupBox('검색 조건')
        cond_lay = QVBoxLayout(grp_cond)

        # 파일명
        fn_grp = QGroupBox('파일명')
        fn_lay = QVBoxLayout(fn_grp)
        fn_mode_row, self._fn_grp, self._fn_rbs = _make_mode_row(self)
        self.fn_edit = QLineEdit()
        self.fn_edit.setPlaceholderText('포함 문자열')
        fn_lay.addLayout(fn_mode_row)
        fn_lay.addWidget(self.fn_edit)
        cond_lay.addWidget(fn_grp)

        # 용량
        sz_grp = QGroupBox('용량 (KB)')
        sz_lay = QVBoxLayout(sz_grp)
        sz_mode_row, self._sz_grp, self._sz_rbs = _make_mode_row(self)
        sz_range = QHBoxLayout()
        self.sz_min = QLineEdit(); self.sz_min.setPlaceholderText('최소')
        self.sz_max = QLineEdit(); self.sz_max.setPlaceholderText('최대')
        sz_range.addWidget(QLabel('최소:')); sz_range.addWidget(self.sz_min)
        sz_range.addWidget(QLabel('최대:')); sz_range.addWidget(self.sz_max)
        sz_range.addStretch()
        sz_lay.addLayout(sz_mode_row)
        sz_lay.addLayout(sz_range)
        cond_lay.addWidget(sz_grp)

        # 해상도
        res_grp = QGroupBox('해상도 (px)')
        res_lay = QVBoxLayout(res_grp)
        res_mode_row, self._res_grp, self._res_rbs = _make_mode_row(self)
        res_w = QHBoxLayout()
        self.res_min_w = QLineEdit(); self.res_min_w.setPlaceholderText('너비 최소')
        self.res_max_w = QLineEdit(); self.res_max_w.setPlaceholderText('너비 최대')
        res_w.addWidget(QLabel('너비:')); res_w.addWidget(self.res_min_w)
        res_w.addWidget(QLabel('~'));     res_w.addWidget(self.res_max_w)
        res_h = QHBoxLayout()
        self.res_min_h = QLineEdit(); self.res_min_h.setPlaceholderText('높이 최소')
        self.res_max_h = QLineEdit(); self.res_max_h.setPlaceholderText('높이 최대')
        res_h.addWidget(QLabel('높이:')); res_h.addWidget(self.res_min_h)
        res_h.addWidget(QLabel('~'));     res_h.addWidget(self.res_max_h)
        res_lay.addLayout(res_mode_row)
        res_lay.addLayout(res_w)
        res_lay.addLayout(res_h)
        cond_lay.addWidget(res_grp)

        # 태그
        tag_grp = QGroupBox('태그 (.txt)')
        tag_lay = QVBoxLayout(tag_grp)
        tag_mode_row, self._tag_grp, self._tag_rbs = _make_mode_row(self)
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText('태그1 | 태그2 | ...')
        tag_lay.addLayout(tag_mode_row)
        tag_lay.addWidget(self.tag_edit)
        cond_lay.addWidget(tag_grp)

        # 검색 버튼
        search_bar = QHBoxLayout()
        self.search_btn = QPushButton('🔍  검색')
        self.search_btn.setProperty('accent', True)
        self.stop_btn   = QPushButton('■  중지')
        self.stop_btn.setEnabled(False)
        self.progress_lbl = QLabel('')
        search_bar.addWidget(self.search_btn)
        search_bar.addWidget(self.stop_btn)
        search_bar.addWidget(self.progress_lbl)
        search_bar.addStretch()
        cond_lay.addLayout(search_bar)
        left_layout.addWidget(grp_cond)

        # 처리 설정
        grp_act = QGroupBox('처리 설정')
        act_lay = QVBoxLayout(grp_act)

        tgt_grp = QGroupBox('처리 대상')
        tgt_lay = QVBoxLayout(tgt_grp)
        self.tgt_both_rb  = QRadioButton('이미지 + 태깅(.txt)')
        self.tgt_image_rb = QRadioButton('이미지만')
        self.tgt_txt_rb   = QRadioButton('태깅(.txt)만')
        self.tgt_both_rb.setChecked(True)
        self._tgt_grp = QButtonGroup(self)
        self._tgt_grp.addButton(self.tgt_both_rb,  0)
        self._tgt_grp.addButton(self.tgt_image_rb, 1)
        self._tgt_grp.addButton(self.tgt_txt_rb,   2)
        for rb in (self.tgt_both_rb, self.tgt_image_rb, self.tgt_txt_rb):
            tgt_lay.addWidget(rb)
        act_lay.addWidget(tgt_grp)

        act_btn_bar = QHBoxLayout()
        self.del_btn  = QPushButton('🗑  삭제')
        self.del_btn.setProperty('danger', True)
        self.move_btn = QPushButton('📂  이동')
        self.copy_btn = QPushButton('📋  복사')
        for b in (self.del_btn, self.move_btn, self.copy_btn):
            act_btn_bar.addWidget(b)
        act_btn_bar.addStretch()
        act_lay.addLayout(act_btn_bar)
        left_layout.addWidget(grp_act)
        left_layout.addStretch()

        # ── 오른쪽: 결과 영역 ─────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(SPACING_SMALL)

        # 결과 카운트 + 전체선택 바
        ctrl_bar = QHBoxLayout()
        self.result_count_lbl = QLabel('검색 결과: 0건')
        self.sel_count_lbl    = QLabel('선택: 0건')
        self.sel_all_btn      = QPushButton('전체 선택')
        self.sel_all_btn.setMaximumWidth(80)
        self.desel_all_btn    = QPushButton('전체 선택해제')
        self.desel_all_btn.setMaximumWidth(100)
        ctrl_bar.addWidget(self.result_count_lbl)
        ctrl_bar.addStretch()
        ctrl_bar.addWidget(self.sel_count_lbl)
        ctrl_bar.addWidget(self.sel_all_btn)
        ctrl_bar.addWidget(self.desel_all_btn)
        right_layout.addLayout(ctrl_bar)

        # 결과 트리
        self.tree = QTreeWidget()
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # 검색 결과는 계층형 트리가 아니므로 기본 들여쓰기를 제거한다.
        # 이 공간 때문에 첫 번째 체크박스가 오른쪽으로 밀려 보이지 않는 문제가 있었다.
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setHeaderLabels(self._COLUMNS)
        self.tree.setAlternatingRowColors(True)
        for i, w in enumerate(self._COL_W):
            self.tree.setColumnWidth(i, w)
        self.tree.header().setSortIndicatorShown(True)
        self.tree.setSortingEnabled(True)   # 헤더 클릭 시 실제 정렬 동작 (이전엔 인디케이터만 표시되고 동작 안 했음)
        right_layout.addWidget(self.tree, stretch=1)

        # 미리보기
        preview_splitter = QSplitter(Qt.Orientation.Vertical)
        preview_splitter.setMaximumHeight(200)

        self.preview_lbl = QLabel('파일을 선택하면 미리보기가 표시됩니다.')
        self.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_lbl.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview_lbl.setMinimumHeight(120)

        self.tag_preview = QLabel('')
        self.tag_preview.setWordWrap(True)
        self.tag_preview.setProperty('secondary', True)

        right_layout.addWidget(self.preview_lbl)
        right_layout.addWidget(self.tag_preview)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([300, 700])          # 초기 비율 (이후 자유롭게 드래그 가능)
        splitter.setCollapsible(0, False)       # 왼쪽 패널이 완전히 접히지 않도록
        splitter.setCollapsible(1, False)

        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # 시그널 연결
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.indep_chk.toggled.connect(self.indep_sel.setEnabled)
        self.search_btn.clicked.connect(self._on_search)
        self.stop_btn.clicked.connect(self._on_stop)
        self.sel_all_btn.clicked.connect(self._select_all)
        self.desel_all_btn.clicked.connect(self._deselect_all)
        self.tree.itemSelectionChanged.connect(self._on_tree_select)
        self.tree.itemClicked.connect(self._on_tree_clicked)
        self.tree.itemChanged.connect(self._on_item_check_changed)
        self.del_btn.clicked.connect(lambda: self._on_action('delete'))
        self.move_btn.clicked.connect(lambda: self._on_action('move'))
        self.copy_btn.clicked.connect(lambda: self._on_action('copy'))
        # 인라인 미리보기 클릭 -> 독립 이미지 뷰어 창 (원본 사양)
        self.preview_lbl.mousePressEvent = self._on_preview_clicked  # type: ignore[method-assign]

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def set_folder(self, folder: str) -> None:
        self._folder = folder

    def set_num_cores(self, n: int) -> None:
        self._num_cores = n

    def get_settings(self) -> dict:
        return {
            'sf_use_independent':  self.indep_chk.isChecked(),
            'sf_independent_path': self.indep_sel.path(),
            'sf_recursive':        self.recursive_chk.isChecked(),
            'sf_filename_mode':    self._mode_str(self._fn_rbs),
            'sf_filename_pattern': self.fn_edit.text(),
            'sf_size_mode':        self._mode_str(self._sz_rbs),
            'sf_size_min':         self.sz_min.text(),
            'sf_size_max':         self.sz_max.text(),
            'sf_res_mode':         self._mode_str(self._res_rbs),
            'sf_res_min_w':        self.res_min_w.text(),
            'sf_res_max_w':        self.res_max_w.text(),
            'sf_res_min_h':        self.res_min_h.text(),
            'sf_res_max_h':        self.res_max_h.text(),
            'sf_tag_mode':         self._mode_str(self._tag_rbs),
            'sf_tag_query':        self.tag_edit.text(),
            'sf_target_type':      self._target_type(),
        }

    def load_settings(self, s: dict) -> None:
        if 'sf_use_independent'  in s: self.indep_chk.setChecked(bool(s['sf_use_independent']))
        if 'sf_independent_path' in s: self.indep_sel.set_path(str(s['sf_independent_path']))
        if 'sf_recursive'        in s: self.recursive_chk.setChecked(bool(s['sf_recursive']))
        self._set_mode(self._fn_rbs,  s.get('sf_filename_mode', 'unused'))
        if 'sf_filename_pattern' in s: self.fn_edit.setText(str(s['sf_filename_pattern']))
        self._set_mode(self._sz_rbs,  s.get('sf_size_mode', 'unused'))
        if 'sf_size_min' in s: self.sz_min.setText(str(s['sf_size_min']))
        if 'sf_size_max' in s: self.sz_max.setText(str(s['sf_size_max']))
        self._set_mode(self._res_rbs, s.get('sf_res_mode', 'unused'))
        if 'sf_res_min_w' in s: self.res_min_w.setText(str(s['sf_res_min_w']))
        if 'sf_res_max_w' in s: self.res_max_w.setText(str(s['sf_res_max_w']))
        if 'sf_res_min_h' in s: self.res_min_h.setText(str(s['sf_res_min_h']))
        if 'sf_res_max_h' in s: self.res_max_h.setText(str(s['sf_res_max_h']))
        self._set_mode(self._tag_rbs, s.get('sf_tag_mode', 'unused'))
        if 'sf_tag_query' in s: self.tag_edit.setText(str(s['sf_tag_query']))
        tgt = s.get('sf_target_type', 'both')
        self.tgt_both_rb.setChecked(tgt == 'both')
        self.tgt_image_rb.setChecked(tgt == 'image')
        self.tgt_txt_rb.setChecked(tgt == 'txt')

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    @staticmethod
    def _mode_str(rbs: dict[str, QRadioButton]) -> str:
        for val, rb in rbs.items():
            if rb.isChecked():
                return val
        return 'unused'

    @staticmethod
    def _set_mode(rbs: dict[str, QRadioButton], mode: str) -> None:
        for val, rb in rbs.items():
            rb.setChecked(val == mode)

    def _target_type(self) -> str:
        if self.tgt_image_rb.isChecked(): return 'image'
        if self.tgt_txt_rb.isChecked():   return 'txt'
        return 'both'

    def _safe_float(self, s: str):
        try:
            return float(s.strip()) if s.strip() else None
        except ValueError:
            return None

    def _safe_int(self, s: str):
        try:
            return int(s.strip()) if s.strip() else None
        except ValueError:
            return None

    def _build_conditions(self) -> list:
        return [
            {'mode': self._mode_str(self._fn_rbs),  'type': 'filename',
             'pattern': self.fn_edit.text().strip()},
            {'mode': self._mode_str(self._sz_rbs),  'type': 'size',
             'min_kb': self._safe_float(self.sz_min.text()),
             'max_kb': self._safe_float(self.sz_max.text())},
            {'mode': self._mode_str(self._res_rbs), 'type': 'resolution',
             'min_w': self._safe_int(self.res_min_w.text()),
             'max_w': self._safe_int(self.res_max_w.text()),
             'min_h': self._safe_int(self.res_min_h.text()),
             'max_h': self._safe_int(self.res_max_h.text())},
            {'mode': self._mode_str(self._tag_rbs), 'type': 'tag',
             'query': self.tag_edit.text().strip()},
        ]

    def _active_folder(self) -> str:
        if self.indep_chk.isChecked():
            return self.indep_sel.path()
        return self._folder

    def _set_busy(self, busy: bool) -> None:
        self.search_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def _checked_entries(self) -> list[FileEntry]:
        entries = []
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                idx = item.data(0, Qt.ItemDataRole.UserRole)
                if idx is not None and 0 <= idx < len(self._results):
                    entries.append(self._results[idx])
        return entries

    def _update_sel_count(self) -> None:
        n = len(self._checked_entries())
        self.sel_count_lbl.setText(f'선택: {n}건')

    def _populate_tree(self, results: list[FileEntry]) -> None:
        self.tree.setSortingEnabled(False)
        self.tree.blockSignals(True)
        self.tree.clear()
        for i, entry in enumerate(results):
            res = entry.resolution
            res_str = f'{res[0]}x{res[1]}' if res else '-'
            tags_preview = entry.tag_content[:80]
            item = NumericTreeItem([
                '',
                entry.stem,
                entry.image_ext,
                entry.folder,
                str(entry.file_size_kb),
                res_str,
                tags_preview,
            ])
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            # 용량(KB) — 숫자 그대로 정렬 키로 사용
            item.setData(4, SORT_KEY_ROLE, float(entry.file_size_kb))
            # 해상도 — 원본 사양과 동일하게 가로*세로 면적 기준으로 정렬
            item.setData(5, SORT_KEY_ROLE, float(res[0] * res[1]) if res else 0.0)
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        self.tree.setSortingEnabled(True)

    # ------------------------------------------------------------------
    # 슬롯
    # ------------------------------------------------------------------

    def _on_search(self) -> None:
        folder = self._active_folder()
        if not folder:
            QMessageBox.warning(self, '경고', '먼저 작업 폴더를 선택하세요.')
            return
        conditions = self._build_conditions()
        self._set_busy(True)
        self.progress_lbl.setText('검색 중...')
        self._results = []
        self.tree.clear()

        self._worker = _SearchWorker(
            folder, self.recursive_chk.isChecked(),
            conditions, self._num_cores,
        )
        self._worker.progress.connect(
            lambda d, t: self.progress_lbl.setText(f'{d}/{t}')
        )
        self._worker.finished.connect(self._on_search_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    @Slot(list)
    def _on_search_done(self, results: list) -> None:
        self._set_busy(False)
        self._results = results
        self.result_count_lbl.setText(f'검색 결과: {len(results)}건')
        self.progress_lbl.setText('')
        self._populate_tree(results)

    @Slot(str)
    def _on_worker_error(self, traceback_text: str) -> None:
        """검색 워커에서 예상치 못한 예외가 발생했을 때의 안전망."""
        self._set_busy(False)
        self.progress_lbl.setText('오류 발생')
        QMessageBox.critical(
            self, '예상치 못한 오류',
            f'검색 중 오류가 발생했습니다.\n\n{traceback_text[-1500:]}',
        )

    def _on_stop(self) -> None:
        if self._worker:
            self._worker.stop()

    def _on_tree_select(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        idx = items[0].data(0, Qt.ItemDataRole.UserRole)
        if idx is None or idx >= len(self._results):
            return
        entry = self._results[idx]
        self._selected_entry = entry

        # 이미지 미리보기
        if entry.has_image():
            px = QPixmap(str(entry.image_path))
            if not px.isNull():
                w = max(self.preview_lbl.width() - 10, 10)
                h = max(self.preview_lbl.height()- 10, 10)
                self.preview_lbl.setPixmap(
                    px.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )
                self.preview_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                self.preview_lbl.setToolTip('클릭하면 뷰어 창이 열립니다')
            else:
                self.preview_lbl.setText('미리보기 불가')
                self.preview_lbl.setPixmap(QPixmap())
                self.preview_lbl.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.preview_lbl.setText('이미지 없음')
            self.preview_lbl.setPixmap(QPixmap())
            self.preview_lbl.setCursor(Qt.CursorShape.ArrowCursor)

        # 태그 미리보기
        self.tag_preview.setText(entry.tag_content[:200] or '(태그 없음)')

    def _on_preview_clicked(self, event) -> None:
        """인라인 미리보기 클릭 시 독립 뷰어 창을 연다 (원본 사양)."""
        if self._selected_entry is None or not self._selected_entry.has_image():
            return
        try:
            dlg = ImageViewerDialog.from_path(self._selected_entry.image_path, self)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(self, '오류', f'이미지 뷰어를 열 수 없습니다.\n{e}')

    def _on_item_check_changed(self, item: QTreeWidgetItem, col: int) -> None:
        if col == 0:
            # 체크박스를 눌러도 해당 파일을 현재 행으로 선택한다.
            self.tree.setCurrentItem(item)
            self._update_sel_count()

    def _on_tree_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        """행 클릭은 미리보기 선택만 처리하고 체크 상태는 변경하지 않는다."""
        self.tree.setCurrentItem(item)

    def _select_all(self) -> None:
        self.tree.blockSignals(True)
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            root.child(i).setCheckState(0, Qt.CheckState.Checked)
        self.tree.blockSignals(False)
        self._update_sel_count()

    def _deselect_all(self) -> None:
        self.tree.blockSignals(True)
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            root.child(i).setCheckState(0, Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._update_sel_count()

    def _on_action(self, action: str) -> None:
        entries = self._checked_entries()
        if not entries:
            QMessageBox.warning(self, '경고', '처리할 항목을 선택하세요.')
            return

        target = self._target_type()
        orphans = get_orphan_warning(entries, target)
        if orphans:
            msg = (f'처리 후 {len(orphans)}개의 짝 없는 파일이 남습니다.\n계속하시겠습니까?')
            if QMessageBox.question(self, '경고', msg) != QMessageBox.StandardButton.Yes:
                return

        dest = ''
        if action in ('move', 'copy'):
            dest = QFileDialog.getExistingDirectory(
                self, '대상 폴더 선택',
            )
            if not dest:
                return

        if action == 'delete':
            if QMessageBox.question(
                self, '확인', f'{len(entries)}개의 항목을 삭제하시겠습니까?'
            ) != QMessageBox.StandardButton.Yes:
                return

        success, fail, logs = process_entries(entries, action, target, dest)
        action_label = {'delete': '삭제', 'move': '이동', 'copy': '복사'}[action]
        dlg = _ResultLogDialog(action_label, success, fail, logs, self)
        dlg.exec()
        self._on_search()   # 검색 결과 갱신
