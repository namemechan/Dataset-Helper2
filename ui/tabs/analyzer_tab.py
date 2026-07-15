"""
ui/tabs/analyzer_tab.py

데이터셋 분석 탭 — 폴더별 이미지 수, 버킷 분포, 리핏 추천, 낭비율, 스냅샷 비교.
"""

from __future__ import annotations

import csv
import datetime
import os
from typing import Optional

from PySide6.QtCore    import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QCheckBox, QSpinBox,
    QPushButton, QProgressBar, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QInputDialog, QFileDialog, QMessageBox,
    QSplitter, QDialog, QDialogButtonBox, QTextEdit,
    QComboBox, QSizePolicy, QFrame, QScrollArea, QRadioButton,
    QButtonGroup, QTabWidget,
)

from core.dataset_analyzer import DatasetAnalyzer, DatasetSnapshot, BatchMover, format_size_generic
from ui.widgets.folder_selector   import FolderSelector
from ui.widgets.worker_base       import SafeWorker
from ui.widgets.numeric_tree_item import NumericTreeItem, SORT_KEY_ROLE
from ui.constants import (
    PADDING_DEFAULT, PADDING_SMALL, SPACING_DEFAULT, SPACING_SMALL,
    current_colors,
)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _ScanWorker(SafeWorker):
    finished = Signal(list)

    def __init__(self, path: str, recursive: bool, include_empty: bool,
                 include_untagged: bool, num_cores: int,
                 bucket_settings: Optional[dict]) -> None:
        super().__init__()
        self._path            = path
        self._recursive       = recursive
        self._include_empty   = include_empty
        self._include_untagged= include_untagged
        self._num_cores       = num_cores
        self._bucket_settings = bucket_settings

    def work(self) -> None:
        results = DatasetAnalyzer.scan_directories(
            self._path, self._recursive,
            self._include_empty, self._include_untagged,
            self._num_cores, self._bucket_settings,
        )
        self.finished.emit(results)


# ---------------------------------------------------------------------------
# 스냅샷 저장 다이얼로그
# ---------------------------------------------------------------------------

class _SaveSnapshotDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('스냅샷 저장')
        self.setMinimumWidth(360)
        self.setModal(True)

        lay = QFormLayout(self)
        lay.setSpacing(SPACING_DEFAULT)

        self.name_edit = QLineEdit(datetime.datetime.now().strftime('%Y-%m-%d'))
        self.memo_edit = QTextEdit()
        self.memo_edit.setFixedHeight(80)

        lay.addRow('스냅샷 이름:', self.name_edit)
        lay.addRow('메모 (선택):', self.memo_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)

    def name(self) -> str:
        return self.name_edit.text().strip()

    def memo(self) -> str:
        return self.memo_edit.toPlainText().strip()


# ---------------------------------------------------------------------------
# 스냅샷 선택 다이얼로그
# ---------------------------------------------------------------------------

class _LoadSnapshotDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('스냅샷 불러오기')
        self.setMinimumWidth(420)
        self.setModal(True)
        self._selected_path: str = ''

        lay = QFormLayout(self)
        lay.setSpacing(SPACING_DEFAULT)

        snapshots = DatasetSnapshot.list_snapshots()
        self._path_map = {name: path for name, path in snapshots}

        self.combo = QComboBox()
        self.combo.addItems(list(self._path_map.keys()))
        lay.addRow('저장된 스냅샷:', self.combo)

        browse_row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText('직접 JSON 파일 선택')
        browse_btn = QPushButton('찾아보기')
        browse_btn.setMaximumWidth(80)
        browse_btn.clicked.connect(self._on_browse)
        browse_row.addWidget(self.file_edit)
        browse_row.addWidget(browse_btn)
        lay.addRow('직접 선택:', browse_row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)

    def _on_browse(self) -> None:
        init = str(DatasetSnapshot.get_snapshot_dir())
        path, _ = QFileDialog.getOpenFileName(
            self, '스냅샷 파일 선택', init if os.path.exists(init) else '',
            'JSON 스냅샷 (*.json);;모든 파일 (*.*)',
        )
        if path:
            self.file_edit.setText(path)

    def _on_ok(self) -> None:
        direct = self.file_edit.text().strip()
        if direct and os.path.isfile(direct):
            self._selected_path = direct
            self.accept()
            return
        name = self.combo.currentText()
        path = self._path_map.get(name, '')
        if path and os.path.isfile(path):
            self._selected_path = path
            self.accept()
            return
        QMessageBox.warning(self, '경고', '불러올 스냅샷을 선택해주세요.')

    def selected_path(self) -> str:
        return self._selected_path


# ---------------------------------------------------------------------------
# 데이터셋 일괄이동 — 원본 BatchMoveWindow(tkinter)와 동등한 기능 재구현
#
# 실제 파일시스템 조작(BatchMover)은 core/dataset_analyzer.py 에 있다.
# 이 탭 파일은 그 결과를 화면에 보여주고 사용자 입력을 받는 역할만 한다.
# ---------------------------------------------------------------------------


class _BatchMoveWorker(SafeWorker):
    """백그라운드에서 폴더 일괄 이동/복사를 수행한다."""

    progress = Signal(int, int, str)   # done, total, current_dest_name
    finished = Signal(list)            # error 메시지 리스트

    def __init__(
        self,
        results: list[dict],
        dest: str,
        op: str,
        dup: str,
        apply_repeat: bool,
    ) -> None:
        super().__init__()
        self._results     = results
        self._dest        = dest
        self._op          = op
        self._dup         = dup
        self._apply_repeat = apply_repeat

    def _dest_name(self, folder_name: str, repeat: int) -> str:
        if self._apply_repeat:
            return f"{repeat}_{folder_name}"
        return folder_name

    def work(self) -> None:
        total  = len(self._results)
        errors: list[str] = []

        for idx, r in enumerate(self._results):
            src_path  = r['folder_path']
            src_name  = r['folder_name']
            dest_name = self._dest_name(src_name, r.get('repeat', 1))
            dest_path = os.path.join(self._dest, dest_name)

            try:
                BatchMover.process_one_folder(src_path, dest_path, dest_name, self._dest, self._op, self._dup)
            except Exception as e:
                errors.append(f"{src_name}: {e}")

            self.progress.emit(idx + 1, total, dest_name)

        self.finished.emit(errors)


class _BatchMoveDialog(QDialog):
    """
    데이터셋 폴더를 원하는 위치로 한 번에 이동/복사하는 다이얼로그.

    - 이동 또는 복사 선택
    - 중복 폴더 처리: 건너뛰기 / 숫자 추가 / 합치기
    - 리핏 적용 여부: 테이블에 설정된 리핏값을 폴더명 앞에 "{repeat}_" 형태로 붙임
    - 실행 전 확인 팝업 (총 폴더 수 / 총 용량 표시)
    - 실행은 백그라운드 스레드에서 수행되어 다이얼로그가 멈추지 않는다.
    """

    def __init__(self, results: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('데이터셋 일괄이동')
        self.resize(720, 580)
        self.setMinimumSize(600, 480)

        self._results = results
        self._worker: _BatchMoveWorker | None = None

        self._build_ui()
        self._refresh_preview()

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PADDING_DEFAULT, PADDING_DEFAULT,
                                PADDING_DEFAULT, PADDING_DEFAULT)
        root.setSpacing(SPACING_DEFAULT)

        # ── 목적지 경로 ───────────────────────────────────────
        dest_grp = QGroupBox('이동/복사 목적지')
        dest_lay = QHBoxLayout(dest_grp)
        self.dest_edit = QLineEdit()
        self.dest_browse_btn = QPushButton('폴더 선택')
        dest_lay.addWidget(self.dest_edit, stretch=1)
        dest_lay.addWidget(self.dest_browse_btn)
        root.addWidget(dest_grp)

        # ── 동작 옵션 ─────────────────────────────────────────
        opt_grp = QGroupBox('동작 옵션')
        opt_lay = QVBoxLayout(opt_grp)

        op_row = QHBoxLayout()
        op_row.addWidget(QLabel('동작:'))
        self.op_copy_rb = QRadioButton('복사')
        self.op_move_rb = QRadioButton('이동')
        self.op_copy_rb.setChecked(True)
        self._op_grp = QButtonGroup(self)
        self._op_grp.addButton(self.op_copy_rb, 0)
        self._op_grp.addButton(self.op_move_rb, 1)
        op_row.addWidget(self.op_copy_rb)
        op_row.addWidget(self.op_move_rb)
        op_row.addStretch()
        opt_lay.addLayout(op_row)

        dup_row = QHBoxLayout()
        dup_row.addWidget(QLabel('중복 폴더명:'))
        self.dup_skip_rb   = QRadioButton('건너뛰기')
        self.dup_number_rb = QRadioButton('숫자 추가 (폴더명|N)')
        self.dup_merge_rb  = QRadioButton('합치기')
        self.dup_number_rb.setChecked(True)
        self._dup_grp = QButtonGroup(self)
        self._dup_grp.addButton(self.dup_skip_rb,   0)
        self._dup_grp.addButton(self.dup_number_rb, 1)
        self._dup_grp.addButton(self.dup_merge_rb,  2)
        for w in (self.dup_skip_rb, self.dup_number_rb, self.dup_merge_rb):
            dup_row.addWidget(w)
        dup_row.addStretch()
        opt_lay.addLayout(dup_row)

        repeat_row = QHBoxLayout()
        self.apply_repeat_chk = QCheckBox('설정 리핏 적용  (폴더명 앞에 "{리핏값}_폴더명" 형태로 변경)')
        repeat_row.addWidget(self.apply_repeat_chk)
        repeat_row.addStretch()
        opt_lay.addLayout(repeat_row)

        root.addWidget(opt_grp)

        # ── 미리보기 테이블 ───────────────────────────────────
        prev_grp = QGroupBox('이동/복사 대상 폴더 미리보기')
        prev_lay = QVBoxLayout(prev_grp)
        self.preview_tree = QTreeWidget()
        self.preview_tree.setHeaderLabels(['원본 폴더명', '대상 폴더명 (리핏 적용 시)', '파일 수', '용량'])
        self.preview_tree.setColumnWidth(0, 220)
        self.preview_tree.setColumnWidth(1, 220)
        self.preview_tree.setColumnWidth(2, 80)
        prev_lay.addWidget(self.preview_tree)
        root.addWidget(prev_grp, stretch=1)

        # ── 진행률 바 ─────────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.status_lbl = QLabel('대기 중')
        self.status_lbl.setProperty('secondary', True)
        root.addWidget(self.progress_bar)
        root.addWidget(self.status_lbl)

        # ── 하단 버튼 ─────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.run_btn   = QPushButton('실행')
        self.run_btn.setProperty('accent', True)
        self.close_btn = QPushButton('닫기')
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

        # 시그널
        self.dest_browse_btn.clicked.connect(self._on_browse_dest)
        self.apply_repeat_chk.toggled.connect(self._refresh_preview)
        self.run_btn.clicked.connect(self._on_confirm_and_run)
        self.close_btn.clicked.connect(self.reject)

    # ------------------------------------------------------------------
    # 헬퍼
    # ------------------------------------------------------------------

    def _dest_name(self, folder_name: str, repeat: int) -> str:
        if self.apply_repeat_chk.isChecked():
            return f"{repeat}_{folder_name}"
        return folder_name

    def _selected_op(self) -> str:
        return 'move' if self.op_move_rb.isChecked() else 'copy'

    def _selected_dup(self) -> str:
        if self.dup_skip_rb.isChecked():  return 'skip'
        if self.dup_merge_rb.isChecked(): return 'merge'
        return 'number'

    def _on_browse_dest(self) -> None:
        d = QFileDialog.getExistingDirectory(self, '목적지 폴더 선택')
        if d:
            self.dest_edit.setText(d)

    def _refresh_preview(self) -> None:
        self.preview_tree.clear()
        for r in self._results:
            src_name  = r['folder_name']
            dest_name = self._dest_name(src_name, r.get('repeat', 1))
            path      = r['folder_path']
            try:
                n_files = str(BatchMover.count_files(path))
                sz      = format_size_generic(BatchMover.folder_size(path))
            except Exception:
                n_files = '?'
                sz      = '?'
            QTreeWidgetItem(self.preview_tree, [src_name, dest_name, n_files, sz])

    # ------------------------------------------------------------------
    # 실행
    # ------------------------------------------------------------------

    def _on_confirm_and_run(self) -> None:
        dest = self.dest_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, '경고', '목적지 폴더를 선택해주세요.')
            return
        if not os.path.isdir(dest):
            try:
                os.makedirs(dest, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, '오류', f'목적지 폴더를 생성할 수 없습니다:\n{e}')
                return

        if not self._results:
            QMessageBox.warning(self, '경고', '이동할 폴더가 없습니다.')
            return

        total_folders = len(self._results)
        total_bytes   = 0
        for r in self._results:
            try:
                total_bytes += BatchMover.folder_size(r['folder_path'])
            except Exception:
                pass

        op_label     = '이동' if self._selected_op() == 'move' else '복사'
        dup_label    = {'skip': '건너뛰기', 'number': '숫자 추가', 'merge': '합치기'}[self._selected_dup()]
        repeat_label = '적용' if self.apply_repeat_chk.isChecked() else '미적용'

        msg = (
            f"다음 내용으로 {op_label}을 진행합니다.\n\n"
            f"  총 폴더 수 : {total_folders:,} 개\n"
            f"  총 용량    : {format_size_generic(total_bytes)}\n"
            f"  목적지     : {dest}\n"
            f"  중복 처리  : {dup_label}\n"
            f"  리핏 적용  : {repeat_label}\n\n"
            f"계속하시겠습니까?"
        )
        if QMessageBox.question(self, f'데이터셋 {op_label} 확인', msg) != QMessageBox.StandardButton.Yes:
            return

        self.run_btn.setEnabled(False)
        self.status_lbl.setText('처리 중...')

        self._worker = _BatchMoveWorker(
            self._results, dest,
            self._selected_op(), self._selected_dup(),
            self.apply_repeat_chk.isChecked(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    @Slot(int, int, str)
    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(done)
        self.status_lbl.setText(f'처리 중 ({done}/{total}): {name}')

    @Slot(str)
    def _on_worker_error(self, traceback_text: str) -> None:
        """일괄이동 워커에서 예상치 못한 예외가 발생했을 때의 안전망."""
        self.run_btn.setEnabled(True)
        self.status_lbl.setText('오류 발생')
        QMessageBox.critical(
            self, '예상치 못한 오류',
            f'일괄이동 중 오류가 발생했습니다.\n\n{traceback_text[-1500:]}',
        )

    @Slot(list)
    def _on_finished(self, errors: list) -> None:
        self.run_btn.setEnabled(True)
        self.progress_bar.setValue(self.progress_bar.maximum())

        if errors:
            err_text = '\n'.join(errors[:20])
            if len(errors) > 20:
                err_text += f'\n... 외 {len(errors) - 20}건'
            self.status_lbl.setText(f'완료 (오류 {len(errors)}건)')
            QMessageBox.warning(
                self, '완료 (일부 오류)',
                f'처리가 완료되었으나 아래 항목에서 오류가 발생했습니다:\n\n{err_text}',
            )
        else:
            self.status_lbl.setText('완료!')
            QMessageBox.information(self, '완료', '데이터셋 일괄이동/복사가 완료되었습니다.')


# ---------------------------------------------------------------------------
# 데이터셋 스냅샷 관리 — 원본 SnapshotWindow(tkinter)와 동등한 기능 재구현
#
# 구조: [기본 스냅샷] [비교 스냅샷] [차이점 분석(신규/삭제/변경/이동 4서브탭)]
# ---------------------------------------------------------------------------

class _SnapshotInfoPanel(QWidget):
    """스냅샷 1개의 정보(이름/날짜/경로/통계)와 최하위 폴더별 상세 테이블."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                PADDING_SMALL, PADDING_SMALL)
        root.setSpacing(SPACING_SMALL)

        info_grp = QGroupBox('스냅샷 정보')
        form = QGridLayout(info_grp)
        form.setHorizontalSpacing(SPACING_DEFAULT)
        form.setVerticalSpacing(4)

        self.name_lbl   = QLabel('-')
        self.date_lbl   = QLabel('-')
        self.root_lbl   = QLabel('-')
        self.root_lbl.setProperty('secondary', True)
        self.img_lbl     = QLabel('-')
        self.pair_lbl    = QLabel('-')
        self.size_lbl    = QLabel('-')
        self.folder_lbl  = QLabel('-')
        self.memo_lbl    = QLabel('-')
        self.memo_lbl.setWordWrap(True)

        form.addWidget(QLabel('이름:'),        0, 0); form.addWidget(self.name_lbl,   0, 1)
        form.addWidget(QLabel('날짜:'),        0, 2); form.addWidget(self.date_lbl,   0, 3)
        form.addWidget(QLabel('루트 폴더:'),   1, 0); form.addWidget(self.root_lbl,   1, 1, 1, 3)
        form.addWidget(QLabel('총 이미지:'),   2, 0); form.addWidget(self.img_lbl,    2, 1)
        form.addWidget(QLabel('총 짝(pair):'), 2, 2); form.addWidget(self.pair_lbl,   2, 3)
        form.addWidget(QLabel('총 용량:'),     3, 0); form.addWidget(self.size_lbl,   3, 1)
        form.addWidget(QLabel('폴더 수:'),     3, 2); form.addWidget(self.folder_lbl, 3, 3)
        form.addWidget(QLabel('메모:'),        4, 0); form.addWidget(self.memo_lbl,   4, 1, 1, 3)

        root.addWidget(info_grp)

        table_grp = QGroupBox('최하위 폴더별 상세')
        table_lay = QVBoxLayout(table_grp)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['폴더 경로 (루트 기준 상대)', '이미지', '짝(pair)', '미짝', '용량'])
        self.tree.setColumnWidth(0, 320)
        self.tree.setAlternatingRowColors(True)
        table_lay.addWidget(self.tree)
        root.addWidget(table_grp, stretch=1)

    def display(self, data: dict) -> None:
        dt = data.get('created_at', '')[:19].replace('T', ' ')
        self.name_lbl.setText(data.get('name', '-') or '-')
        self.date_lbl.setText(dt or '-')
        self.root_lbl.setText(data.get('root_path', '-'))
        self.img_lbl.setText(f"{data.get('total_images', 0):,} 장")
        self.pair_lbl.setText(f"{data.get('total_pairs', 0):,} 쌍  (미짝: {data.get('total_unpaired', 0):,})")
        self.size_lbl.setText(DatasetSnapshot.format_size(data.get('total_size_bytes', 0)))
        self.folder_lbl.setText(f"{data.get('leaf_folder_count', 0)} 개")
        self.memo_lbl.setText(data.get('memo', '') or '-')

        self.tree.clear()
        for folder in data.get('leaf_folders', []):
            QTreeWidgetItem(self.tree, [
                folder['rel_path'],
                f"{folder['image_count']:,}",
                f"{folder['pair_count']:,}",
                str(folder['unpaired']),
                DatasetSnapshot.format_size(folder['size_bytes']),
            ])


class _SnapshotDialog(QDialog):
    """데이터셋 스냅샷 저장·불러오기·비교를 관리하는 다이얼로그."""

    def __init__(self, root_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('데이터셋 스냅샷 관리')
        self.resize(1100, 720)
        self.setMinimumSize(900, 600)

        self._root_path    = root_path
        self._base_snapshot: Optional[dict] = None
        self._comp_snapshot: Optional[dict] = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                PADDING_SMALL, PADDING_SMALL)
        root.setSpacing(SPACING_SMALL)

        # ── 상단 액션 버튼 바 ─────────────────────────────────
        top_bar = QHBoxLayout()
        self.save_btn       = QPushButton('현재 상태 저장')
        self.load_base_btn  = QPushButton('기본 스냅샷 불러오기')
        self.load_comp_btn  = QPushButton('비교 스냅샷 불러오기')
        self.compare_btn    = QPushButton('비교하기')
        self.compare_btn.setProperty('accent', True)
        for w in (self.save_btn, self.load_base_btn, self.load_comp_btn, self.compare_btn):
            top_bar.addWidget(w)
        top_bar.addStretch()
        root.addLayout(top_bar)

        # ── 상태 표시 줄 ──────────────────────────────────────
        colors = current_colors()
        status_bar = QHBoxLayout()
        status_bar.addWidget(QLabel('기본:'))
        self.base_status_lbl = QLabel('(없음)')
        self.base_status_lbl.setStyleSheet(f"color: {colors['accent']};")
        status_bar.addWidget(self.base_status_lbl)
        status_bar.addSpacing(20)
        status_bar.addWidget(QLabel('비교:'))
        self.comp_status_lbl = QLabel('(없음)')
        self.comp_status_lbl.setStyleSheet(f"color: {colors['success']};")
        status_bar.addWidget(self.comp_status_lbl)
        status_bar.addStretch()
        root.addLayout(status_bar)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # ── 메인 탭 (기본/비교/차이점) ────────────────────────
        self.tabs = QTabWidget()

        self.base_panel = _SnapshotInfoPanel()
        self.comp_panel = _SnapshotInfoPanel()
        self.tabs.addTab(self.base_panel, '기본 스냅샷')
        self.tabs.addTab(self.comp_panel, '비교 스냅샷')

        self.diff_tab = self._build_diff_tab()
        self.tabs.addTab(self.diff_tab, '차이점 분석')

        root.addWidget(self.tabs, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        self.close_btn = QPushButton('닫기')
        close_row.addWidget(self.close_btn)
        root.addLayout(close_row)

        # 시그널
        self.save_btn.clicked.connect(self._on_save)
        self.load_base_btn.clicked.connect(self._on_load_base)
        self.load_comp_btn.clicked.connect(self._on_load_comp)
        self.compare_btn.clicked.connect(self._on_compare)
        self.close_btn.clicked.connect(self.accept)

    def _build_diff_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                               PADDING_SMALL, PADDING_SMALL)
        lay.setSpacing(SPACING_SMALL)

        summary_grp = QGroupBox('비교 요약')
        summary_lay = QVBoxLayout(summary_grp)
        self.diff_summary_lbl = QLabel(
            "기본 스냅샷과 비교 스냅샷을 모두 불러온 뒤 '비교하기' 버튼을 눌러주세요."
        )
        self.diff_summary_lbl.setWordWrap(True)
        summary_lay.addWidget(self.diff_summary_lbl)
        lay.addWidget(summary_grp)

        self.diff_subtabs = QTabWidget()

        self.diff_added_tree = QTreeWidget()
        self.diff_added_tree.setHeaderLabels(['폴더 경로', '이미지', '짝', '용량'])
        self.diff_subtabs.addTab(self.diff_added_tree, '신규 추가 폴더')

        self.diff_removed_tree = QTreeWidget()
        self.diff_removed_tree.setHeaderLabels(['폴더 경로', '이미지', '짝', '용량'])
        self.diff_subtabs.addTab(self.diff_removed_tree, '삭제된 폴더')

        self.diff_changed_tree = QTreeWidget()
        self.diff_changed_tree.setHeaderLabels(['폴더 경로', '기본(이미지)', '비교(이미지)', '증감(장)', '증감(용량)'])
        self.diff_subtabs.addTab(self.diff_changed_tree, '변경된 폴더')

        self.diff_fuzzy_tree = QTreeWidget()
        self.diff_fuzzy_tree.setHeaderLabels(['기본 경로 (이전)', '비교 경로 (이후/이동)', '증감(장)', '증감(용량)'])
        self.diff_subtabs.addTab(self.diff_fuzzy_tree, '이동 / 재구성')

        for tree in (self.diff_added_tree, self.diff_removed_tree,
                     self.diff_changed_tree, self.diff_fuzzy_tree):
            tree.setAlternatingRowColors(True)
            tree.setColumnWidth(0, 280)

        lay.addWidget(self.diff_subtabs, stretch=1)
        return w

    # ------------------------------------------------------------------
    # 명령 핸들러
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        if not self._root_path or not os.path.exists(self._root_path):
            QMessageBox.warning(self, '경고', '유효한 폴더 경로를 먼저 설정해주세요.')
            return

        data = DatasetSnapshot.collect(self._root_path)
        if data is None:
            QMessageBox.critical(self, '오류', '폴더 정보를 수집할 수 없습니다.')
            return

        sdlg = _SaveSnapshotDialog(self)
        if sdlg.exec() != QDialog.DialogCode.Accepted or not sdlg.name():
            return

        try:
            filepath = DatasetSnapshot.save(data, sdlg.name(), sdlg.memo())
            QMessageBox.information(self, '저장 완료', f'스냅샷이 저장되었습니다.\n\n{filepath}')

            # 저장 후 기본 스냅샷으로 자동 로드
            self._base_snapshot = DatasetSnapshot.load(str(filepath))
            self.base_panel.display(self._base_snapshot)
            self.base_status_lbl.setText(f"{sdlg.name()}  ({data['created_at'][:10]})")
            self.tabs.setCurrentIndex(0)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'스냅샷 저장 중 오류가 발생했습니다:\n{e}')

    def _on_load_base(self) -> None:
        ldlg = _LoadSnapshotDialog(self)
        if ldlg.exec() != QDialog.DialogCode.Accepted or not ldlg.selected_path():
            return
        try:
            self._base_snapshot = DatasetSnapshot.load(ldlg.selected_path())
            self.base_panel.display(self._base_snapshot)
            self.base_status_lbl.setText(
                f"{self._base_snapshot.get('name', '?')}  "
                f"({self._base_snapshot.get('created_at', '')[:10]})"
            )
            self.tabs.setCurrentIndex(0)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'스냅샷 불러오기 실패:\n{e}')

    def _on_load_comp(self) -> None:
        ldlg = _LoadSnapshotDialog(self)
        if ldlg.exec() != QDialog.DialogCode.Accepted or not ldlg.selected_path():
            return
        try:
            self._comp_snapshot = DatasetSnapshot.load(ldlg.selected_path())
            self.comp_panel.display(self._comp_snapshot)
            self.comp_status_lbl.setText(
                f"{self._comp_snapshot.get('name', '?')}  "
                f"({self._comp_snapshot.get('created_at', '')[:10]})"
            )
            self.tabs.setCurrentIndex(1)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'스냅샷 불러오기 실패:\n{e}')

    def _on_compare(self) -> None:
        if not self._base_snapshot or not self._comp_snapshot:
            QMessageBox.warning(self, '경고', '기본 스냅샷과 비교 스냅샷을 모두 불러와야 합니다.')
            return
        try:
            diff = DatasetSnapshot.compare(self._base_snapshot, self._comp_snapshot)
            self._display_diff(diff)
            self.tabs.setCurrentIndex(2)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'비교 중 오류가 발생했습니다:\n{e}')

    # ------------------------------------------------------------------
    # 차이점 표시
    # ------------------------------------------------------------------

    def _display_diff(self, diff: dict) -> None:
        s   = diff['summary']
        fmt = DatasetSnapshot.format_size

        di, dsz = s['delta_images'], s['delta_size']
        si  = '+' if di  >= 0 else ''
        ssz = '+' if dsz >= 0 else ''
        trend_img = '▲' if di  >= 0 else '▼'
        trend_sz  = '▲' if dsz >= 0 else '▼'

        self.diff_summary_lbl.setText(
            f"이미지 증감:  {trend_img} {si}{di:,} 장  ({si}{s['rate_images']:.1f}%)     "
            f"용량 증감:  {trend_sz} {fmt(abs(dsz))} ({ssz}{s['rate_size']:.1f}%)\n"
            f"신규 폴더: {s['added_count']}개   삭제 폴더: {s['removed_count']}개   "
            f"변경된 폴더: {s['changed_count']}개   이동/재구성: {s['fuzzy_count']}개   "
            f"변경 없음: {s['unchanged_count']}개"
        )

        self.diff_subtabs.setTabText(0, f"신규 추가 폴더 ({s['added_count']})")
        self.diff_subtabs.setTabText(1, f"삭제된 폴더 ({s['removed_count']})")
        self.diff_subtabs.setTabText(2, f"변경된 폴더 ({s['changed_count']})")
        self.diff_subtabs.setTabText(3, f"이동/재구성 ({s['fuzzy_count']})")

        self.diff_added_tree.clear()
        for item in diff['added']:
            QTreeWidgetItem(self.diff_added_tree, [
                item['path'], f"{item['image_count']:,}", f"{item['pair_count']:,}",
                fmt(item['size_bytes']),
            ])

        self.diff_removed_tree.clear()
        for item in diff['removed']:
            QTreeWidgetItem(self.diff_removed_tree, [
                item['path'], f"{item['image_count']:,}", f"{item['pair_count']:,}",
                fmt(item['size_bytes']),
            ])

        self.diff_changed_tree.clear()
        for item in diff['changed']:
            d_i, d_sz = item['delta_images'], item['delta_size']
            si2, ssz2 = ('+' if d_i >= 0 else ''), ('+' if d_sz >= 0 else '')
            QTreeWidgetItem(self.diff_changed_tree, [
                item['path'],
                f"{item['base']['image_count']:,}",
                f"{item['comp']['image_count']:,}",
                f"{si2}{d_i:,}",
                f"{ssz2}{fmt(abs(d_sz))} ({'▲' if d_sz >= 0 else '▼'})",
            ])

        self.diff_fuzzy_tree.clear()
        for item in diff['fuzzy_matched']:
            d_i, d_sz = item['delta_images'], item['delta_size']
            si2, ssz2 = ('+' if d_i >= 0 else ''), ('+' if d_sz >= 0 else '')
            QTreeWidgetItem(self.diff_fuzzy_tree, [
                item['base_path'], item['comp_path'],
                f"{si2}{d_i:,}",
                f"{ssz2}{fmt(abs(d_sz))} ({'▲' if d_sz >= 0 else '▼'})",
            ])


# ---------------------------------------------------------------------------
# 탭 위젯
# ---------------------------------------------------------------------------

class AnalyzerTab(QWidget):
    """데이터셋 분석 탭."""

    _COLS     = ('folder', 'count', 'buckets', 'recommend', 'repeat',
                 'total_ops', 'steps', 'waste')
    _COL_HDRS = ('폴더 이름', '원본 수', '버킷(종류)', '추천 리핏',
                 '설정 리핏', '처리량(이론/실제)', '스텝(이론/실제)', '낭비율')
    _COL_W    = (180, 70, 260, 80, 80, 150, 150, 80)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._folder:    str  = ''
        self._num_cores: int  = 1
        self._results:   list = []
        self._avg_data:  float = 0.0
        self._worker: _ScanWorker | None = None
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                PADDING_SMALL, PADDING_SMALL)
        root.setSpacing(SPACING_SMALL)

        # ── 설정 영역 ─────────────────────────────────────────
        grp_settings = QGroupBox('분석 설정')
        settings_lay = QVBoxLayout(grp_settings)
        settings_lay.setSpacing(SPACING_SMALL)

        # 경로
        path_row = QHBoxLayout()
        self.indep_chk = QCheckBox('독립 경로 사용')
        self.indep_sel = FolderSelector('독립 폴더 선택')
        self.indep_sel.setEnabled(False)
        path_row.addWidget(self.indep_chk)
        path_row.addWidget(self.indep_sel)
        settings_lay.addLayout(path_row)

        # 옵션 체크박스
        opts_row = QHBoxLayout()
        self.recursive_chk     = QCheckBox('하위 폴더 포함')
        self.include_empty_chk = QCheckBox('빈 폴더 포함 (최하위 한정)')
        self.include_untagged_chk = QCheckBox('미태깅 파일 포함')
        self.recursive_chk.setChecked(True)
        for w in (self.recursive_chk, self.include_empty_chk, self.include_untagged_chk):
            opts_row.addWidget(w)
        opts_row.addStretch()
        settings_lay.addLayout(opts_row)

        # 버킷 설정
        grp_bucket = QGroupBox('학습 환경 설정')
        bucket_lay = QHBoxLayout(grp_bucket)
        self.custom_bucket_chk = QCheckBox('사용자 정의 버킷')
        self.target_reso_spin  = QSpinBox(); self.target_reso_spin.setRange(256, 4096); self.target_reso_spin.setSingleStep(64);  self.target_reso_spin.setValue(1024); self.target_reso_spin.setMaximumWidth(80)
        self.bucket_steps_spin = QSpinBox(); self.bucket_steps_spin.setRange(8, 1024);  self.bucket_steps_spin.setSingleStep(8);   self.bucket_steps_spin.setValue(64);   self.bucket_steps_spin.setMaximumWidth(70)
        self.min_bucket_spin   = QSpinBox(); self.min_bucket_spin.setRange(64, 4096);   self.min_bucket_spin.setSingleStep(64);   self.min_bucket_spin.setValue(256);    self.min_bucket_spin.setMaximumWidth(80)
        self.max_bucket_spin   = QSpinBox(); self.max_bucket_spin.setRange(64, 8192);   self.max_bucket_spin.setSingleStep(64);   self.max_bucket_spin.setValue(2048);   self.max_bucket_spin.setMaximumWidth(80)
        bucket_lay.addWidget(self.custom_bucket_chk)
        bucket_lay.addWidget(QLabel('기준 해상도:')); bucket_lay.addWidget(self.target_reso_spin)
        bucket_lay.addWidget(QLabel('단위(steps):')); bucket_lay.addWidget(self.bucket_steps_spin)
        bucket_lay.addWidget(QLabel('최소:'));        bucket_lay.addWidget(self.min_bucket_spin)
        bucket_lay.addWidget(QLabel('최대:'));        bucket_lay.addWidget(self.max_bucket_spin)
        bucket_lay.addStretch()
        settings_lay.addWidget(grp_bucket)

        # 학습 파라미터
        grp_params = QGroupBox('학습 파라미터')
        params_lay = QHBoxLayout(grp_params)
        self.batch_spin    = QSpinBox(); self.batch_spin.setRange(1, 1024); self.batch_spin.setValue(1);  self.batch_spin.setMaximumWidth(60)
        self.grad_spin     = QSpinBox(); self.grad_spin.setRange(1, 1024);  self.grad_spin.setValue(1);   self.grad_spin.setMaximumWidth(60)
        self.epochs_spin   = QSpinBox(); self.epochs_spin.setRange(1, 10000); self.epochs_spin.setValue(10); self.epochs_spin.setMaximumWidth(70)
        params_lay.addWidget(QLabel('배치:'));                params_lay.addWidget(self.batch_spin)
        params_lay.addWidget(QLabel('Gradient Acc:'));        params_lay.addWidget(self.grad_spin)
        params_lay.addWidget(QLabel('에포크:'));              params_lay.addWidget(self.epochs_spin)
        params_lay.addStretch()
        settings_lay.addWidget(grp_params)
        root.addWidget(grp_settings)

        # ── 버튼 바 ───────────────────────────────────────────
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(SPACING_SMALL)
        self.search_btn   = QPushButton('검색 (폴더 스캔)')
        self.search_btn.setProperty('accent', True)
        self.analyze_btn  = QPushButton('분석 (계산 갱신)')
        self.analyze_btn.setEnabled(False)
        self.export_btn   = QPushButton('CSV 내보내기')
        self.export_btn.setEnabled(False)
        self.mismatch_btn = QPushButton('버킷 미스매치')
        self.mismatch_btn.setEnabled(False)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)

        self.all_repeat_btn   = QPushButton('리핏 일괄 설정')
        self.rec_repeat_btn   = QPushButton('추천 리핏 설정')
        self.opt_repeat_btn   = QPushButton('최적 리핏 설정')
        self.avg_repeat_btn   = QPushButton('균등 리핏 설정')
        for w in (self.all_repeat_btn, self.rec_repeat_btn,
                  self.opt_repeat_btn, self.avg_repeat_btn):
            w.setEnabled(False)

        for w in (self.search_btn, self.analyze_btn, self.export_btn,
                  self.mismatch_btn, sep,
                  self.all_repeat_btn, self.rec_repeat_btn,
                  self.opt_repeat_btn, self.avg_repeat_btn):
            btn_bar.addWidget(w)
        btn_bar.addStretch()
        root.addLayout(btn_bar)

        # ── 요약 영역 ─────────────────────────────────────────
        grp_summary = QGroupBox('요약 결과')
        summary_lay = QHBoxLayout(grp_summary)
        self.summary_lbl  = QLabel('검색을 진행해주세요.')
        self.summary_lbl.setWordWrap(True)
        self.snapshot_btn = QPushButton('데이터셋 스냅샷')
        self.batch_move_btn = QPushButton('데이터셋 일괄이동')
        self.batch_move_btn.setEnabled(False)
        summary_lay.addWidget(self.summary_lbl, stretch=1)
        summary_lay.addWidget(self.snapshot_btn)
        summary_lay.addWidget(self.batch_move_btn)
        root.addWidget(grp_summary)

        # ── 결과 테이블 ───────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(list(self._COL_HDRS))
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        for i, w in enumerate(self._COL_W):
            self.tree.setColumnWidth(i, w)
        root.addWidget(self.tree, stretch=1)

        # 버킷 스핀박스 초기 비활성화
        self._toggle_bucket_ui(False)

    # ------------------------------------------------------------------
    # 시그널 연결
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.indep_chk.toggled.connect(self.indep_sel.setEnabled)
        self.custom_bucket_chk.toggled.connect(self._toggle_bucket_ui)
        self.search_btn.clicked.connect(self._on_search)
        self.analyze_btn.clicked.connect(self._on_analyze)
        self.export_btn.clicked.connect(self._on_export)
        self.mismatch_btn.clicked.connect(self._on_mismatch)
        self.all_repeat_btn.clicked.connect(self._on_all_repeat)
        self.rec_repeat_btn.clicked.connect(self._on_rec_repeat)
        self.opt_repeat_btn.clicked.connect(self._on_opt_repeat)
        self.avg_repeat_btn.clicked.connect(self._on_avg_repeat)
        self.snapshot_btn.clicked.connect(self._on_snapshot)
        self.batch_move_btn.clicked.connect(self._on_batch_move)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.batch_spin.valueChanged.connect(self._refresh_summary)
        self.grad_spin.valueChanged.connect(self._refresh_summary)
        self.epochs_spin.valueChanged.connect(self._refresh_summary)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def set_folder(self, folder: str) -> None:
        self._folder = folder

    def set_num_cores(self, n: int) -> None:
        self._num_cores = n

    def get_settings(self) -> dict:
        return {
            'az_use_independent':   self.indep_chk.isChecked(),
            'az_independent_path':  self.indep_sel.path(),
            'az_recursive':         self.recursive_chk.isChecked(),
            'az_include_empty':     self.include_empty_chk.isChecked(),
            'az_include_untagged':  self.include_untagged_chk.isChecked(),
            'az_custom_bucket':     self.custom_bucket_chk.isChecked(),
            'az_target_reso':       self.target_reso_spin.value(),
            'az_bucket_steps':      self.bucket_steps_spin.value(),
            'az_min_bucket':        self.min_bucket_spin.value(),
            'az_max_bucket':        self.max_bucket_spin.value(),
            'az_batch':             self.batch_spin.value(),
            'az_grad':              self.grad_spin.value(),
            'az_epochs':            self.epochs_spin.value(),
        }

    def load_settings(self, s: dict) -> None:
        if 'az_use_independent'  in s: self.indep_chk.setChecked(bool(s['az_use_independent']))
        if 'az_independent_path' in s: self.indep_sel.set_path(str(s['az_independent_path']))
        if 'az_recursive'        in s: self.recursive_chk.setChecked(bool(s['az_recursive']))
        if 'az_include_empty'    in s: self.include_empty_chk.setChecked(bool(s['az_include_empty']))
        if 'az_include_untagged' in s: self.include_untagged_chk.setChecked(bool(s['az_include_untagged']))
        if 'az_custom_bucket'    in s: self.custom_bucket_chk.setChecked(bool(s['az_custom_bucket']))
        if 'az_target_reso'      in s: self.target_reso_spin.setValue(int(s['az_target_reso']))
        if 'az_bucket_steps'     in s: self.bucket_steps_spin.setValue(int(s['az_bucket_steps']))
        if 'az_min_bucket'       in s: self.min_bucket_spin.setValue(int(s['az_min_bucket']))
        if 'az_max_bucket'       in s: self.max_bucket_spin.setValue(int(s['az_max_bucket']))
        if 'az_batch'            in s: self.batch_spin.setValue(int(s['az_batch']))
        if 'az_grad'             in s: self.grad_spin.setValue(int(s['az_grad']))
        if 'az_epochs'           in s: self.epochs_spin.setValue(int(s['az_epochs']))

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _toggle_bucket_ui(self, enabled: bool) -> None:
        for w in (self.target_reso_spin, self.bucket_steps_spin,
                  self.min_bucket_spin, self.max_bucket_spin):
            w.setEnabled(enabled)

    def _active_folder(self) -> str:
        return self.indep_sel.path() if self.indep_chk.isChecked() else self._folder

    def _batch_total(self) -> int:
        return self.batch_spin.value() * self.grad_spin.value()

    def _bucket_settings(self) -> Optional[dict]:
        if not self.custom_bucket_chk.isChecked():
            return None
        return {
            'target_res':   self.target_reso_spin.value(),
            'bucket_steps': self.bucket_steps_spin.value(),
            'bucket_min':   self.min_bucket_spin.value(),
            'bucket_max':   self.max_bucket_spin.value(),
        }

    def _set_busy(self, busy: bool) -> None:
        self.search_btn.setEnabled(not busy)

    def _set_results_ready(self, ready: bool) -> None:
        for w in (self.analyze_btn, self.export_btn,
                  self.all_repeat_btn, self.rec_repeat_btn,
                  self.opt_repeat_btn, self.avg_repeat_btn):
            w.setEnabled(ready)

    def _recalculate(self, r: dict) -> None:
        bt = self._batch_total()
        _, waste, steps = DatasetAnalyzer.calculate_waste(r['buckets'], r['repeat'], bt)
        r['waste_rate']        = waste
        r['steps']             = steps
        r['theoretical_steps'] = DatasetAnalyzer.calculate_theoretical_steps(
            r['count'], r['repeat'], bt,
        )

    def _update_recommend(self) -> None:
        if not self._results:
            return
        recs = DatasetAnalyzer.calculate_recommend_repeats(self._results, self._batch_total())
        for r, rec in zip(self._results, recs):
            r['recommend'] = rec
            self._recalculate(r)

    def _populate_table(self) -> None:
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        bt = self._batch_total()
        for r in self._results:
            marker    = '▲' if r['count'] >= self._avg_data else '▼'
            count_str = f"{marker} {r['count']}개"
            b_types   = len(r['buckets'])
            b_str     = f"[{b_types}종] " + ', '.join(
                f"{k}:{v}" for k, v in sorted(r['buckets'].items())
            )
            theo_ops  = r['count'] * r['repeat']
            act_ops   = r['steps'] * bt
            item = NumericTreeItem([
                r['folder_name'],
                count_str,
                b_str,
                str(r.get('recommend', 1)),
                str(r['repeat']),
                f"{theo_ops} / {act_ops}",
                f"{r['theoretical_steps']:.1f} / {r['steps']}",
                f"{r['waste_rate']:.2f}%",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, r['folder_path'])

            # 숫자 의미를 갖는 컬럼에 정렬 키를 별도 저장한다.
            # (컬럼 0 '폴더 이름'은 텍스트 정렬이 자연스러우므로 키를 주지 않는다.)
            item.setData(1, SORT_KEY_ROLE, float(r['count']))
            item.setData(2, SORT_KEY_ROLE, float(b_types))
            item.setData(3, SORT_KEY_ROLE, float(r.get('recommend', 1)))
            item.setData(4, SORT_KEY_ROLE, float(r['repeat']))
            item.setData(5, SORT_KEY_ROLE, float(act_ops))
            item.setData(6, SORT_KEY_ROLE, float(r['steps']))
            item.setData(7, SORT_KEY_ROLE, float(r['waste_rate']))

            self.tree.addTopLevelItem(item)
        self.tree.setSortingEnabled(True)

    def _refresh_summary(self) -> None:
        if not self._results:
            return
        self._update_recommend()
        self._populate_table()
        self._update_summary_label()

    def _update_summary_label(self) -> None:
        if not self._results:
            self.summary_lbl.setText('검색 결과가 없습니다.')
            return
        bt     = self._batch_total()
        epochs = self.epochs_spin.value()
        total_folders   = len(self._results)
        total_data      = sum(r['count']  for r in self._results)
        total_steps_ep  = sum(r['steps']  for r in self._results)
        total_theo_ep   = sum(r['theoretical_steps'] for r in self._results)
        total_waste_slots = total_slots = 0
        for r in self._results:
            w, _, steps = DatasetAnalyzer.calculate_waste(r['buckets'], r['repeat'], bt)
            total_waste_slots += w
            total_slots       += steps * bt
        avg_waste = (total_waste_slots / total_slots * 100) if total_slots else 0.0
        total_mismatches = sum(len(r.get('mismatches', [])) for r in self._results)
        mis_txt = f'⚠ 비율 미스매치 감지: {total_mismatches}건\n' if total_mismatches else ''
        self.summary_lbl.setText(
            f'{mis_txt}'
            f'최종 배치: {bt} | 총 폴더: {total_folders}개 | '
            f'총 데이터셋: {total_data}개 | 폴더 평균: {self._avg_data:.1f}개\n'
            f'이론 스텝 (1에포크): {total_theo_ep:.1f} | '
            f'이론 총 스텝 ({epochs}에포크): {total_theo_ep * epochs:.1f}\n'
            f'예상 스텝 (1에포크): {total_steps_ep} | '
            f'예상 총 스텝 ({epochs}에포크): {total_steps_ep * epochs} | '
            f'평균 낭비율: {avg_waste:.2f}%'
        )

    # ------------------------------------------------------------------
    # 슬롯
    # ------------------------------------------------------------------

    def _on_search(self) -> None:
        folder = self._active_folder()
        if not folder or not os.path.exists(folder):
            QMessageBox.warning(self, '경고', '올바른 경로를 선택해주세요.')
            return
        self._set_busy(True)
        self._set_results_ready(False)
        self._worker = _ScanWorker(
            folder,
            self.recursive_chk.isChecked(),
            self.include_empty_chk.isChecked(),
            self.include_untagged_chk.isChecked(),
            self._num_cores,
            self._bucket_settings(),
        )
        self._worker.finished.connect(self._on_scan_done)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    @Slot(list)
    def _on_scan_done(self, results: list) -> None:
        self._set_busy(False)
        self._results  = results
        total = sum(r['count'] for r in results)
        self._avg_data = total / len(results) if results else 0.0
        for r in self._results:
            r['repeat'] = 1
        self._update_recommend()
        self._populate_table()
        self._update_summary_label()
        self._set_results_ready(bool(results))

        total_mis = sum(len(r.get('mismatches', [])) for r in results)
        self.mismatch_btn.setEnabled(total_mis > 0)
        self.batch_move_btn.setEnabled(bool(results))

    @Slot(str)
    def _on_scan_error(self, traceback_text: str) -> None:
        """폴더 스캔 워커에서 예상치 못한 예외가 발생했을 때의 안전망."""
        self._set_busy(False)
        QMessageBox.critical(
            self, '예상치 못한 오류',
            f'폴더 스캔 중 오류가 발생했습니다.\n\n{traceback_text[-1500:]}',
        )

    def _on_analyze(self) -> None:
        if not self._results:
            return
        bs = self._bucket_settings()
        b_target = bs['target_res']   if bs else 1024
        b_steps  = bs['bucket_steps'] if bs else 64
        b_min    = bs['bucket_min']   if bs else 256
        b_max    = bs['bucket_max']   if bs else 2048
        bt       = self._batch_total()

        bucket_list = DatasetAnalyzer.make_buckets(b_target, b_min, b_max, b_steps)
        bucket_ars  = [bw / bh for bw, bh in bucket_list]
        total_data  = sum(r['count'] for r in self._results)
        self._avg_data = total_data / len(self._results) if self._results else 0.0

        for r in self._results:
            if r.get('image_dims'):
                r['buckets']    = DatasetAnalyzer.rebucketize(
                    r['image_dims'], b_steps, b_min, b_max, b_target,
                )
                r['mismatches'] = []
                for w, h in r['image_dims']:
                    orig_ar  = w / h
                    diffs    = [abs(orig_ar - b) for b in bucket_ars]
                    best_idx = diffs.index(min(diffs))
                    b_ar     = bucket_ars[best_idx]
                    bw, bh   = bucket_list[best_idx]
                    if abs(orig_ar - b_ar) / b_ar > 0.3:
                        r['mismatches'].append({
                            'file_name':  '(계산 갱신됨)',
                            'resolution': f"{w}x{h}",
                            'orig_ar':    round(orig_ar, 3),
                            'bucket_ar':  round(b_ar, 3),
                            'bucket_res': f"{bw}x{bh}",
                            'folder_path': r['folder_path'],
                        })
            self._recalculate(r)

        self._update_recommend()
        self._populate_table()
        self._update_summary_label()
        total_mis = sum(len(r.get('mismatches', [])) for r in self._results)
        self.mismatch_btn.setEnabled(total_mis > 0)

    def _on_export(self) -> None:
        if not self._results:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, 'CSV 저장', '', 'CSV 파일 (*.csv)',
        )
        if not path:
            return
        try:
            bt = self._batch_total()
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                w.writerow(['[데이터셋 분석 요약 결과]'])
                w.writerow(['최종 배치', bt])
                w.writerow(['총 폴더', len(self._results)])
                w.writerow(['총 데이터', sum(r['count'] for r in self._results)])
                w.writerow(['폴더당 평균', f'{self._avg_data:.2f}'])
                bs = self._bucket_settings()
                if bs:
                    w.writerow(['버킷 설정', '사용자 정의'])
                    w.writerow(['기준 해상도', bs['target_res']])
                    w.writerow(['단위', bs['bucket_steps']])
                    w.writerow(['최소', bs['bucket_min']])
                    w.writerow(['최대', bs['bucket_max']])
                else:
                    w.writerow(['버킷 설정', '기본값 (1024 Area / 64 steps)'])
                w.writerow([])
                w.writerow(['폴더 이름','원본 수','추천 리핏','설정 리핏',
                             '처리량(이론)','처리량(실제)','이론 스텝','스텝(실제)',
                             '낭비율(%)','버킷 종류 수','버킷 분포','폴더 경로'])
                for r in self._results:
                    bdet = ', '.join(f"{k}:{v}" for k, v in sorted(r['buckets'].items()))
                    w.writerow([
                        r['folder_name'], r['count'],
                        r.get('recommend', 1), r['repeat'],
                        r['count'] * r['repeat'], r['steps'] * bt,
                        r['theoretical_steps'], r['steps'],
                        f"{r['waste_rate']:.2f}", len(r['buckets']), bdet,
                        r['folder_path'],
                    ])
            QMessageBox.information(self, '완료', f'저장 완료:\n{path}')
        except Exception as e:
            QMessageBox.critical(self, '오류', f'저장 실패: {e}')

    def _on_mismatch(self) -> None:
        mismatches = [m for r in self._results for m in r.get('mismatches', [])]
        if not mismatches:
            QMessageBox.information(self, '알림', '비율 미스매치 이미지가 없습니다.')
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f'버킷 비율 미스매치 ({len(mismatches)}건)')
        dlg.resize(900, 400)
        lay = QVBoxLayout(dlg)
        lbl = QLabel(f'총 {len(mismatches)}개 이미지가 배정된 버킷과 비율 차이가 큽니다. (30% 초과)')
        lbl.setStyleSheet(f"color: {current_colors()['error']};")
        lay.addWidget(lbl)
        tree = QTreeWidget()
        tree.setHeaderLabels(['파일명','원본 해상도','원본 AR','버킷 AR','배정 버킷','폴더 경로'])
        for m in mismatches:
            QTreeWidgetItem(tree, [
                m['file_name'], m['resolution'],
                str(m['orig_ar']), str(m['bucket_ar']),
                m['bucket_res'], m['folder_path'],
            ])
        lay.addWidget(tree)
        btn_row = QHBoxLayout()
        def save_csv():
            p, _ = QFileDialog.getSaveFileName(dlg, 'CSV 저장', '', 'CSV (*.csv)')
            if not p: return
            try:
                with open(p, 'w', encoding='utf-8-sig', newline='') as f:
                    w = csv.writer(f)
                    w.writerow(['파일명','원본 해상도','원본 AR','버킷 AR','배정 버킷','폴더 경로'])
                    for m in mismatches:
                        w.writerow([m['file_name'],m['resolution'],m['orig_ar'],m['bucket_ar'],m['bucket_res'],m['folder_path']])
                QMessageBox.information(dlg, '완료', f'저장됨: {p}')
            except Exception as e:
                QMessageBox.critical(dlg, '오류', str(e))
        csv_btn  = QPushButton('CSV 저장'); csv_btn.clicked.connect(save_csv)
        close_btn = QPushButton('닫기');    close_btn.clicked.connect(dlg.accept)
        btn_row.addStretch(); btn_row.addWidget(csv_btn); btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)
        dlg.exec()

    def _on_all_repeat(self) -> None:
        if not self._results: return
        val, ok = QInputDialog.getInt(self, '리핏 일괄 설정', '모든 폴더에 적용할 리핏 값:', 1, 0, 9999)
        if not ok: return
        for r in self._results:
            r['repeat'] = val
            self._recalculate(r)
        self._populate_table()
        self._update_summary_label()

    def _on_rec_repeat(self) -> None:
        if not self._results: return
        for r in self._results:
            r['repeat'] = r.get('recommend', 1)
            self._recalculate(r)
        self._populate_table()
        self._update_summary_label()

    def _on_opt_repeat(self) -> None:
        if not self._results: return
        bt = self._batch_total()
        for r in self._results:
            r['repeat'] = bt if r['count'] < self._avg_data else r.get('recommend', 1)
            self._recalculate(r)
        self._populate_table()
        self._update_summary_label()

    def _on_avg_repeat(self) -> None:
        if not self._results: return
        bt = self._batch_total()
        base_steps = [DatasetAnalyzer.calculate_waste(r['buckets'], 1, bt)[2] for r in self._results]
        avg_step = sum(base_steps) / len(base_steps) if base_steps else 1
        for r, bs in zip(self._results, base_steps):
            if r['count'] > 0:
                ideal = (avg_step * bt) / r['count']
                base_r = max(1, round(ideal))
                candidates = sorted({max(1, base_r - 1), base_r, base_r + 1, base_r + 2})
                best_r, min_score = base_r, float('inf')
                for cand in candidates:
                    _, wr, steps = DatasetAnalyzer.calculate_waste(r['buckets'], cand, bt)
                    score = abs(steps - avg_step) + (wr / 100) * 0.5
                    if score < min_score:
                        min_score, best_r = score, cand
                    elif abs(score - min_score) < 1e-7 and best_r % 2 != 0 and cand % 2 == 0:
                        best_r = cand
                r['repeat'] = best_r
            else:
                r['repeat'] = 1
            self._recalculate(r)
        self._populate_table()
        self._update_summary_label()

    def _on_double_click(self, item: QTreeWidgetItem, col: int) -> None:
        if col != 4: return   # '설정 리핏' 컬럼만 편집
        folder_path = item.data(0, Qt.ItemDataRole.UserRole)
        current_val = int(item.text(4))
        val, ok = QInputDialog.getInt(
            self, '리핏 수정', f"'{item.text(0)}' 폴더의 리핏 값:", current_val, 0, 9999,
        )
        if not ok: return
        for r in self._results:
            if r['folder_path'] == folder_path:
                r['repeat'] = val
                self._recalculate(r)
                break
        self._populate_table()
        self._update_summary_label()

    def _on_snapshot(self) -> None:
        root_path = self._active_folder()
        if not root_path:
            QMessageBox.warning(self, '경고', '폴더를 먼저 선택하세요.')
            return
        dlg = _SnapshotDialog(root_path, self)
        dlg.exec()

    def _on_batch_move(self) -> None:
        if not self._results:
            QMessageBox.warning(self, '경고', '먼저 폴더 검색을 진행해주세요.')
            return
        dlg = _BatchMoveDialog(self._results, self)
        dlg.exec()
