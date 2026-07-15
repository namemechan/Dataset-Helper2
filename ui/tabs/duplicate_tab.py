"""
ui/tabs/duplicate_tab.py

중복/유사 이미지 탐지 탭.
"""

from __future__ import annotations

import os
import shutil
import time

from PySide6.QtCore    import Qt, Signal, Slot, QSize
from PySide6.QtGui     import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QCheckBox, QRadioButton, QSlider, QSpinBox,
    QPushButton, QProgressBar, QSplitter, QTreeWidget, QTreeWidgetItem,
    QFileDialog, QMessageBox, QSizePolicy, QFrame,
)

from core.duplicate_finder import DuplicateFinder, ImageInfo
from ui.widgets.folder_selector import FolderSelector
from ui.widgets.worker_base     import SafeWorker
from ui.constants import (
    PADDING_DEFAULT, PADDING_SMALL, SPACING_DEFAULT, SPACING_SMALL,
    current_colors, register_theme_listener, unregister_theme_listener,
)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _SearchWorker(SafeWorker):
    progress = Signal(int, int, str)
    finished = Signal(dict)

    def __init__(self, folder: str, kwargs: dict) -> None:
        super().__init__()
        self._folder = folder
        self._kwargs = kwargs
        self._finder = DuplicateFinder()

    def stop(self) -> None:
        self._finder.stop()

    def work(self) -> None:
        def on_progress(cur, total, msg):
            self.progress.emit(cur, total, msg)

        results = self._finder.find_duplicates(
            self._folder,
            progress_callback=on_progress,
            **self._kwargs,
        )
        self.finished.emit(results)


# ---------------------------------------------------------------------------
# 탭 위젯
# ---------------------------------------------------------------------------

class DuplicateTab(QWidget):
    """중복/유사 이미지 탭."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._folder:    str  = ''
        self._num_cores: int  = 1
        self._worker: _SearchWorker | None = None
        self._selected_path: str = ''
        self._start_time: float  = 0.0
        self._build_ui()
        self._connect_signals()

        # match_res_hint_lbl은 인라인 setStyleSheet()으로 색을 직접 칠하므로
        # 전역 QSS만으로는 테마 전환이 반영되지 않는다. 자동 갱신을 등록한다.
        register_theme_listener(self._apply_theme_colors)

    def _apply_theme_colors(self) -> None:
        """테마가 바뀔 때마다 호출되어, 인라인 스타일이 적용된 위젯을 다시 칠한다."""
        colors = current_colors()
        self.match_res_hint_lbl.setStyleSheet(
            f"color: {colors['text_secondary']}; font-size: 8pt;"
        )

    def closeEvent(self, event) -> None:
        unregister_theme_listener(self._apply_theme_colors)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                PADDING_SMALL, PADDING_SMALL)
        root.setSpacing(SPACING_SMALL)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 왼쪽: 설정 패널 ───────────────────────────────────
        left = QWidget()
        left.setMinimumWidth(180)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(SPACING_SMALL)

        # 경로
        grp_path = QGroupBox('경로 설정')
        path_layout = QVBoxLayout(grp_path)
        self.indep_chk  = QCheckBox('독립적인 경로 사용')
        self.indep_sel  = FolderSelector('독립 폴더 선택')
        self.indep_sel.setEnabled(False)
        path_layout.addWidget(self.indep_chk)
        path_layout.addWidget(self.indep_sel)
        left_layout.addWidget(grp_path)

        # 검색 옵션
        grp_opt = QGroupBox('검색 옵션 (중복 선택 가능)')
        opt_layout = QVBoxLayout(grp_opt)

        self.md5_chk = QCheckBox('완전 중복 (MD5 해시)')
        self.md5_chk.setChecked(True)
        opt_layout.addWidget(self.md5_chk)

        # 태그
        self.tag_chk  = QCheckBox('태그 내용 기반 검색 (.txt)')
        opt_layout.addWidget(self.tag_chk)
        tag_inner = QWidget()
        tag_inner_lay = QVBoxLayout(tag_inner)
        tag_inner_lay.setContentsMargins(20, 0, 0, 0)
        tag_lbl         = QLabel('태그 일치율 (%):')
        self.tag_slider = QSlider(Qt.Orientation.Horizontal)
        self.tag_slider.setRange(0, 100)
        self.tag_slider.setValue(100)
        self.tag_val_lbl = QLabel('100')
        self.tag_val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag_inner_lay.addWidget(tag_lbl)
        tag_inner_lay.addWidget(self.tag_slider)
        tag_inner_lay.addWidget(self.tag_val_lbl)
        self.tag_widget = tag_inner
        self.tag_widget.setEnabled(False)
        opt_layout.addWidget(self.tag_widget)

        # dHash
        self.dhash_chk = QCheckBox('유사 이미지 (dHash)')
        opt_layout.addWidget(self.dhash_chk)

        dhash_inner = QWidget()
        dhash_inner_lay = QVBoxLayout(dhash_inner)
        dhash_inner_lay.setContentsMargins(0, 0, 0, 0)

        single_lbl         = QLabel('유사도 허용 오차 (0-20):')
        self.sim_slider    = QSlider(Qt.Orientation.Horizontal)
        self.sim_slider.setRange(0, 20)
        self.sim_slider.setValue(5)
        self.sim_val_lbl   = QLabel('5')
        self.sim_val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dhash_inner_lay.addWidget(single_lbl)
        dhash_inner_lay.addWidget(self.sim_slider)
        dhash_inner_lay.addWidget(self.sim_val_lbl)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        dhash_inner_lay.addWidget(sep)

        self.range_chk   = QCheckBox('유사도 그룹 검색 (범위)')
        range_row        = QHBoxLayout()
        self.range_start = QSpinBox(); self.range_start.setRange(0, 20); self.range_start.setValue(0)
        self.range_end   = QSpinBox(); self.range_end.setRange(0, 20);   self.range_end.setValue(3)
        range_row.addWidget(QLabel('시작:')); range_row.addWidget(self.range_start)
        range_row.addWidget(QLabel('종료:')); range_row.addWidget(self.range_end)
        range_row.addStretch()
        dhash_inner_lay.addWidget(self.range_chk)
        dhash_inner_lay.addLayout(range_row)
        self.dhash_widget = dhash_inner
        self.dhash_widget.setEnabled(False)
        opt_layout.addWidget(self.dhash_widget)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        opt_layout.addWidget(sep2)
        self.match_res_chk = QCheckBox('종횡비가 같은 것끼리만 비교')
        self.match_res_chk.setChecked(True)
        self.match_res_hint_lbl = QLabel('(크기가 달라도 비율이 같으면 비교)')
        self.match_res_hint_lbl.setStyleSheet(f"color: {current_colors()['text_secondary']}; font-size: 8pt;")
        opt_layout.addWidget(self.match_res_chk)
        opt_layout.addWidget(self.match_res_hint_lbl)
        left_layout.addWidget(grp_opt)

        # 검색 버튼
        self.search_btn = QPushButton('중복 이미지 찾기 시작')
        self.search_btn.setProperty('accent', True)
        self.stop_btn   = QPushButton('검색 중지')
        self.stop_btn.setEnabled(False)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.status_lbl = QLabel('대기 중')
        self.status_lbl.setWordWrap(True)

        left_layout.addWidget(self.search_btn)
        left_layout.addWidget(self.stop_btn)
        left_layout.addWidget(self.progress_bar)
        left_layout.addWidget(self.status_lbl)
        left_layout.addStretch()

        # ── 중앙: 결과 트리 ───────────────────────────────────
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)

        center_layout.addWidget(QLabel('검색 결과'))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['그룹/파일', '해상도', '크기', '경로'])
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 80)
        self.tree.setAlternatingRowColors(True)
        center_layout.addWidget(self.tree)

        # ── 오른쪽: 미리보기 + 액션 ──────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(SPACING_SMALL)

        self.preview_lbl = QLabel('이미지를 선택하세요')
        self.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
        )
        self.preview_lbl.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview_lbl.setMinimumSize(200, 200)

        self.info_lbl = QLabel('')
        self.info_lbl.setWordWrap(True)
        self.info_lbl.setProperty('secondary', True)

        self.pair_txt_chk = QCheckBox('동일명의 태깅파일(.txt)도 같이 처리')
        self.del_btn      = QPushButton('선택한 파일 삭제')
        self.del_btn.setProperty('danger', True)
        self.move_btn     = QPushButton('선택한 파일 이동...')
        self.open_btn     = QPushButton('파일이 속한 폴더 열기')

        right_layout.addWidget(self.preview_lbl, stretch=1)
        right_layout.addWidget(self.info_lbl)
        right_layout.addWidget(self.pair_txt_chk)
        right_layout.addWidget(self.del_btn)
        right_layout.addWidget(self.move_btn)
        right_layout.addWidget(self.open_btn)

        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([220, 440, 440])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setCollapsible(2, False)

        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # 시그널 연결
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.indep_chk.toggled.connect(self.indep_sel.setEnabled)
        self.tag_chk.toggled.connect(self.tag_widget.setEnabled)
        self.dhash_chk.toggled.connect(self.dhash_widget.setEnabled)
        self.tag_slider.valueChanged.connect(lambda v: self.tag_val_lbl.setText(str(v)))
        self.sim_slider.valueChanged.connect(lambda v: self.sim_val_lbl.setText(str(v)))
        self.search_btn.clicked.connect(self._on_search)
        self.stop_btn.clicked.connect(self._on_stop)
        self.tree.itemSelectionChanged.connect(self._on_tree_select)
        self.del_btn.clicked.connect(self._on_delete)
        self.move_btn.clicked.connect(self._on_move)
        self.open_btn.clicked.connect(self._on_open_folder)
        self.preview_lbl.resizeEvent = self._on_preview_resize  # type: ignore[method-assign]

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def set_folder(self, folder: str) -> None:
        self._folder = folder

    def set_num_cores(self, n: int) -> None:
        self._num_cores = n

    def get_settings(self) -> dict:
        return {
            'dup_use_independent':  self.indep_chk.isChecked(),
            'dup_independent_path': self.indep_sel.path(),
        }

    def load_settings(self, s: dict) -> None:
        if 'dup_use_independent'  in s: self.indep_chk.setChecked(bool(s['dup_use_independent']))
        if 'dup_independent_path' in s: self.indep_sel.set_path(str(s['dup_independent_path']))

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _active_folder(self) -> str:
        if self.indep_chk.isChecked():
            return self.indep_sel.path()
        return self._folder

    def _set_busy(self, busy: bool) -> None:
        self.search_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def _show_preview(self, path: str) -> None:
        self._selected_path = path
        try:
            px = QPixmap(path)
            if px.isNull():
                raise ValueError('이미지 로드 실패')
            w = max(self.preview_lbl.width()  - 10, 10)
            h = max(self.preview_lbl.height() - 10, 10)
            self.preview_lbl.setPixmap(
                px.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
            stat = os.stat(path)
            self.info_lbl.setText(
                f'파일명: {os.path.basename(path)}\n'
                f'경로:   {os.path.dirname(path)}\n'
                f'크기:   {stat.st_size / 1024:.1f} KB'
            )
        except Exception as e:
            self.preview_lbl.setText(f'이미지를 불러올 수 없습니다.\n{e}')
            self.preview_lbl.setPixmap(QPixmap())

    def _on_preview_resize(self, event) -> None:
        if self._selected_path:
            self._show_preview(self._selected_path)

    def _insert_groups(self, parent_item: QTreeWidgetItem | None, groups: dict) -> None:
        for group_id, data in groups.items():
            items: list[ImageInfo] = data['items']
            gtype = data['type']
            term  = '쌍' if len(items) == 2 else '그룹'
            label = f'[완전 중복] {term}' if gtype == 'exact' else f'[유사] {term}'
            rep   = items[0]
            res   = f'{rep.resolution[0]}x{rep.resolution[1]}' if rep.resolution != (0, 0) else '-'

            group_node = QTreeWidgetItem(
                [f'{label} ({len(items)}개)', res, '', '']
            )
            if parent_item:
                parent_item.addChild(group_node)
            else:
                self.tree.addTopLevelItem(group_node)
            group_node.setExpanded(True)

            for info in items:
                sz  = f'{info.size / 1024:.1f} KB'
                res2 = f'{info.resolution[0]}x{info.resolution[1]}' if info.resolution != (0, 0) else '-'
                child = QTreeWidgetItem(
                    [os.path.basename(info.path), res2, sz, info.path]
                )
                group_node.addChild(child)

    # ------------------------------------------------------------------
    # 슬롯
    # ------------------------------------------------------------------

    def _on_search(self) -> None:
        folder = self._active_folder()
        if not folder or not os.path.exists(folder):
            QMessageBox.warning(self, '경고', '올바른 폴더를 선택하세요.')
            return
        if not any([self.md5_chk.isChecked(),
                    self.dhash_chk.isChecked(),
                    self.tag_chk.isChecked()]):
            QMessageBox.warning(self, '경고', '최소한 하나의 검색 옵션을 선택하세요.')
            return

        range_threshold = None
        if self.dhash_chk.isChecked() and self.range_chk.isChecked():
            s, e = self.range_start.value(), self.range_end.value()
            if s > e:
                s, e = e, s
                self.range_start.setValue(s)
                self.range_end.setValue(e)
            range_threshold = (s, e)

        self.tree.clear()
        self._selected_path = ''
        self.preview_lbl.setText('이미지를 선택하세요')
        self.preview_lbl.setPixmap(QPixmap())
        self.info_lbl.setText('')
        self._start_time = time.time()
        self._set_busy(True)
        self.progress_bar.setValue(0)
        self.status_lbl.setText('검색 중...')

        kwargs = dict(
            check_md5               = self.md5_chk.isChecked(),
            check_dhash             = self.dhash_chk.isChecked(),
            check_tag               = self.tag_chk.isChecked(),
            match_resolution        = self.match_res_chk.isChecked(),
            similarity_threshold    = self.sim_slider.value(),
            tag_similarity_threshold= self.tag_slider.value(),
            max_workers             = self._num_cores,
            range_threshold         = range_threshold,
        )
        self._worker = _SearchWorker(folder, kwargs)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    @Slot(int, int, str)
    def _on_progress(self, cur: int, total: int, msg: str) -> None:
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(cur)
        self.status_lbl.setText(msg)

    @Slot(str)
    def _on_worker_error(self, traceback_text: str) -> None:
        """검색 워커에서 예상치 못한 예외가 발생했을 때의 안전망.
        (이전에는 예외 발생 시 빈 결과만 반환하고 아무 알림도 없이 조용히 실패했었다.)"""
        self._set_busy(False)
        self.status_lbl.setText('오류 발생')
        QMessageBox.critical(
            self, '예상치 못한 오류',
            f'검색 중 오류가 발생했습니다.\n\n{traceback_text[-1500:]}',
        )

    @Slot(dict)
    def _on_finished(self, results: dict) -> None:
        self._set_busy(False)
        elapsed = time.time() - self._start_time

        if isinstance(results, dict) and results.get('mode') == 'range':
            md5_groups   = results.get('md5', {})
            dhash_groups = results.get('dhash', {})
            total = len(md5_groups) + sum(len(g) for g in dhash_groups.values())
            self.status_lbl.setText(f'완료: {total}개 그룹 발견 ({elapsed:.1f}초)')

            if md5_groups:
                root_node = QTreeWidgetItem([f'완전 중복 (MD5) — {len(md5_groups)}그룹'])
                self.tree.addTopLevelItem(root_node)
                root_node.setExpanded(True)
                self._insert_groups(root_node, md5_groups)

            for th in sorted(dhash_groups.keys()):
                groups = dhash_groups[th]
                if not groups:
                    continue
                th_node = QTreeWidgetItem([f'유사도_{th} — {len(groups)}그룹'])
                self.tree.addTopLevelItem(th_node)
                self._insert_groups(th_node, groups)
        else:
            self.status_lbl.setText(f'완료: {len(results)}개 그룹 발견 ({elapsed:.1f}초)')
            self._insert_groups(None, results)

    def _on_stop(self) -> None:
        if self._worker:
            self._worker.stop()
            self.status_lbl.setText('중지 요청됨...')

    def _on_tree_select(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        path = items[0].text(3)
        if path and os.path.exists(path):
            self._show_preview(path)

    def _on_delete(self) -> None:
        if not self._selected_path:
            return
        path     = self._selected_path
        txt_path = os.path.splitext(path)[0] + '.txt'
        has_txt  = self.pair_txt_chk.isChecked() and os.path.exists(txt_path)

        msg = f'정말 삭제하시겠습니까?\n이미지: {os.path.basename(path)}'
        if has_txt:
            msg += f'\n캡션: {os.path.basename(txt_path)}'
        if QMessageBox.question(self, '삭제 확인', msg) != QMessageBox.StandardButton.Yes:
            return

        try:
            os.remove(path)
            if has_txt:
                os.remove(txt_path)
            sel = self.tree.selectedItems()
            if sel:
                (sel[0].parent() or self.tree.invisibleRootItem()).removeChild(sel[0])
            self._selected_path = ''
            self.preview_lbl.setText('삭제됨')
            self.preview_lbl.setPixmap(QPixmap())
            self.info_lbl.setText('')
            QMessageBox.information(self, '완료', '삭제되었습니다.')
        except Exception as e:
            QMessageBox.critical(self, '오류', f'삭제 실패: {e}')

    def _on_move(self) -> None:
        if not self._selected_path:
            return
        dest = QFileDialog.getExistingDirectory(self, '이동할 폴더 선택')
        if not dest:
            return
        path     = self._selected_path
        txt_path = os.path.splitext(path)[0] + '.txt'
        try:
            shutil.move(path, os.path.join(dest, os.path.basename(path)))
            if self.pair_txt_chk.isChecked() and os.path.exists(txt_path):
                shutil.move(txt_path, os.path.join(dest, os.path.basename(txt_path)))
            sel = self.tree.selectedItems()
            if sel:
                (sel[0].parent() or self.tree.invisibleRootItem()).removeChild(sel[0])
            self._selected_path = ''
            self.preview_lbl.setText('이동됨')
            self.preview_lbl.setPixmap(QPixmap())
            QMessageBox.information(self, '완료', '이동되었습니다.')
        except Exception as e:
            QMessageBox.critical(self, '오류', f'이동 실패: {e}')

    def _on_open_folder(self) -> None:
        if self._selected_path:
            folder = os.path.dirname(self._selected_path)
            try:
                os.startfile(folder)
            except Exception:
                QMessageBox.information(self, '경로', folder)
