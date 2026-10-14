"""저수준 이미지 시각 속성 분석 및 캡션 태깅 탭."""
from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import datetime

from PySide6.QtCore import Qt, Signal, Slot, QEvent, QTimer
from PySide6.QtGui import QPixmap, QImageReader
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QCheckBox,
    QSpinBox, QLineEdit, QPushButton, QProgressBar, QTreeWidget,
    QTreeWidgetItem, QMessageBox, QInputDialog, QRadioButton, QFileDialog,
    QSplitter, QSizePolicy, QHeaderView, QFrame,
    QButtonGroup,
)

from core.complexity_tagger import ATTRS, ComplexityTagger, tag_status
from ui.widgets.folder_selector import FolderSelector
from ui.widgets.worker_base import SafeWorker
from ui.constants import PADDING_SMALL, SPACING_SMALL
from utils.settings import APP_DIR


class _AttributeWorker(SafeWorker):
    progress = Signal(int, int, str)
    done = Signal(dict)

    def __init__(self, mode: str, root: str, name: str,
                 workers: int, recursive: bool, create_missing: bool = False,
                 skip_existing: bool = False, workspace_dir: str = '',
                 source_file: str = '', resolution: dict | None = None):
        super().__init__()
        self.mode, self.root, self.name = mode, root, name
        self.workers, self.recursive = workers, recursive
        self.create_missing, self.skip_existing = create_missing, skip_existing
        self.workspace_dir, self.source_file = workspace_dir, source_file
        self.resolution = resolution or {'mode': 'original'}

    def work(self):
        tagger = ComplexityTagger(self.name, self.workspace_dir or None)
        if self.source_file:
            tagger.import_existing(self.source_file)
        cb = lambda done, total, current='': self.progress.emit(done, total, current)
        if self.mode == 'calibrate':
            started = time.perf_counter()
            result = tagger.calibrate(self.root, workers=self.workers,
                                      recursive=self.recursive, progress_cb=cb,
                                      resolution=self.resolution)
            result['_elapsed_seconds'] = time.perf_counter() - started
        elif self.mode == 'tag':
            result = tagger.run_tagging(self.root, workers=self.workers,
                                        recursive=self.recursive,
                                        create_missing=self.create_missing,
                                        on_existing='skip' if self.skip_existing else 'replace',
                                        progress_cb=cb, resolution=self.resolution)
        else:
            result = tagger.remove_tags(self.root, recursive=self.recursive)
        self.done.emit(result)


class ComplexityTab(QWidget):
    """Analyze five image attributes, show hierarchical averages, and tag captions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._folder = ''
        self._num_cores = 1
        self._worker = None
        self._rows = []
        self._thresholds = {}
        self._result_root = ''
        self._current_image = ''
        self._active_workspace = ''
        self._last_generated_name = ''
        self._build_ui()
        self.indep_chk.toggled.connect(self.indep_sel.setEnabled)
        self.calibrate_btn.clicked.connect(lambda: self._start('calibrate'))
        self.tag_btn.clicked.connect(lambda: self._start('tag'))
        self.remove_btn.clicked.connect(self._remove)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(PADDING_SMALL, PADDING_SMALL, PADDING_SMALL, PADDING_SMALL)
        root.setSpacing(SPACING_SMALL)

        path_group = QGroupBox('경로 설정')
        path_layout = QVBoxLayout(path_group)
        row = QHBoxLayout()
        self.recursive_chk = QCheckBox('하위 폴더 포함')
        self.recursive_chk.setChecked(True)
        self.indep_chk = QCheckBox('독립적인 경로 사용')
        self.indep_sel = FolderSelector('독립 폴더 선택')
        self.indep_sel.setEnabled(False)
        row.addWidget(self.recursive_chk)
        row.addWidget(self.indep_chk)
        row.addWidget(self.indep_sel, 1)
        path_layout.addLayout(row)
        self.name_edit = QLineEdit()
        self.name_edit.setText(datetime.now().strftime('%Y-%m-%d'))
        self.name_edit.setMaximumWidth(150)
        self.name_manual_rb = QRadioButton('직접 입력')
        self.name_time_rb = QRadioButton('현재 시간')
        self.name_mode_group = QButtonGroup(self)
        self.name_mode_group.addButton(self.name_manual_rb)
        self.name_mode_group.addButton(self.name_time_rb)
        self.name_manual_rb.setChecked(True)
        self.name_manual_rb.toggled.connect(self._toggle_name_input)
        self.name_time_rb.toggled.connect(self._toggle_name_input)
        self.create_caption_chk = QCheckBox('캡션 없으면 생성')
        self.skip_existing_chk = QCheckBox('이미 완전히 태깅된 캡션 건너뛰기')
        root.addWidget(path_group)

        save_group = QGroupBox('기준선 / 태깅 설정')
        save_layout = QVBoxLayout(save_group)
        control_row = QHBoxLayout()
        control_row.addWidget(QLabel('기준선 이름'))
        control_row.addWidget(self.name_manual_rb)
        control_row.addWidget(self.name_edit)
        control_row.addWidget(self.name_time_rb)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        control_row.addWidget(divider)
        control_row.addWidget(QLabel('결과 저장'))
        self.overwrite_rb = QRadioButton('덮어쓰기')
        self.overwrite_rb.setChecked(True)
        self.other_location_rb = QRadioButton('다른 위치에 저장')
        self.numbered_rb = QRadioButton('숫자 추가')
        self.save_mode_group = QButtonGroup(self)
        for button in (self.overwrite_rb, self.other_location_rb, self.numbered_rb):
            self.save_mode_group.addButton(button)
            control_row.addWidget(button)
        control_row.addStretch()
        save_layout.addLayout(control_row)
        self.save_location = FolderSelector('저장할 상위 폴더 선택 (기준선 이름의 폴더를 생성)')
        self.save_location.setEnabled(False)
        save_layout.addWidget(self.save_location)
        self.other_location_rb.toggled.connect(self.save_location.setEnabled)

        resolution_row = QHBoxLayout()
        resolution_row.addWidget(QLabel('계산 해상도 설정'))
        self.resolution_group = QButtonGroup(self)
        self.resolution_original_rb = QRadioButton('원본 해상도')
        self.resolution_percent_rb = QRadioButton('일괄 비율 설정')
        self.resolution_cutoff_rb = QRadioButton('해상도 컷오프 설정')
        self.resolution_original_rb.setChecked(True)
        for button in (self.resolution_original_rb, self.resolution_percent_rb,
                       self.resolution_cutoff_rb):
            self.resolution_group.addButton(button)
            resolution_row.addWidget(button)
        self.resolution_percent_spin = QSpinBox()
        self.resolution_percent_spin.setRange(10, 100)
        self.resolution_percent_spin.setValue(50)
        self.resolution_percent_spin.setSuffix('%')
        resolution_row.addWidget(self.resolution_percent_spin)
        resolution_row.addSpacing(8)
        resolution_row.addWidget(QLabel('가로'))
        self.resolution_width_spin = QSpinBox()
        self.resolution_width_spin.setRange(16, 100000)
        self.resolution_width_spin.setValue(2048)
        self.resolution_width_spin.setSuffix(' px')
        resolution_row.addWidget(self.resolution_width_spin)
        resolution_row.addWidget(QLabel('세로'))
        self.resolution_height_spin = QSpinBox()
        self.resolution_height_spin.setRange(16, 100000)
        self.resolution_height_spin.setValue(2048)
        self.resolution_height_spin.setSuffix(' px')
        resolution_row.addWidget(self.resolution_height_spin)
        resolution_row.addStretch()
        save_layout.addLayout(resolution_row)
        self.resolution_percent_rb.toggled.connect(self._toggle_resolution_inputs)
        self.resolution_cutoff_rb.toggled.connect(self._toggle_resolution_inputs)
        self._toggle_resolution_inputs()

        reuse_row = QHBoxLayout()
        self.reuse_existing_chk = QCheckBox('기존 기준선/계산 캐시 사용')
        reuse_row.addWidget(self.reuse_existing_chk)
        reuse_row.addWidget(self.skip_existing_chk)
        reuse_row.addWidget(self.create_caption_chk)
        reuse_row.addStretch()
        save_layout.addLayout(reuse_row)

        source_row = QHBoxLayout()
        self.source_file_edit = QLineEdit()
        self.source_file_edit.setPlaceholderText('calibration.json 또는 image_data.csv')
        self.source_file_edit.setEnabled(False)
        self.source_browse_btn = QPushButton('파일 선택...')
        self.source_browse_btn.setEnabled(False)
        self.reuse_existing_chk.toggled.connect(self.source_file_edit.setEnabled)
        self.reuse_existing_chk.toggled.connect(self.source_browse_btn.setEnabled)
        self.source_browse_btn.clicked.connect(self._browse_source_file)
        source_row.addWidget(self.source_file_edit, 1)
        source_row.addWidget(self.source_browse_btn)
        save_layout.addLayout(source_row)
        root.addWidget(save_group)

        actions = QHBoxLayout()
        self.calibrate_btn = QPushButton('이미지 분석 및 기준선 계산')
        self.calibrate_btn.setProperty('accent', True)
        self.tag_btn = QPushButton('캡션에 태그 적용')
        self.remove_btn = QPushButton('복잡도 태깅 삭제')
        self.manual_btn = QPushButton('수동 기준선 설정')
        self.manual_btn.clicked.connect(self._set_manual_threshold)
        for button in (self.calibrate_btn, self.tag_btn, self.manual_btn, self.remove_btn):
            actions.addWidget(button)
        actions.addStretch()
        root.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.status = QLabel('기준선을 계산한 후 태그를 적용하세요.')
        self.status.setWordWrap(False)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status_path = QLabel('')
        self.status_path.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.status_path)
        root.addWidget(self.progress)
        root.addLayout(status_row)

        results = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['폴더 / 이미지', '이미지 수'])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(False)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.tree.installEventFilter(self)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        results.addWidget(self.tree)

        details = QSplitter(Qt.Orientation.Vertical)
        preview_box = QGroupBox('미리보기')
        preview_layout = QVBoxLayout(preview_box)
        self.preview = QLabel('선택 항목 미리보기')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(240, 180)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview.installEventFilter(self)
        preview_layout.addWidget(self.preview)
        details.addWidget(preview_box)

        info_box = QGroupBox('선택 항목 정보')
        info_layout = QVBoxLayout(info_box)
        self.info_tree = QTreeWidget()
        self.info_tree.setHeaderLabels(['항목', '값', '등급 / 상태'])
        self.info_tree.setMinimumHeight(255)
        self.info_tree.setAlternatingRowColors(True)
        self.info_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.info_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.info_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        info_layout.addWidget(self.info_tree)
        details.addWidget(info_box)
        details.setSizes([35, 65])
        results.addWidget(details)
        results.setStretchFactor(0, 1)
        results.setStretchFactor(1, 2)
        results.setSizes([360, 760])
        root.addWidget(results, 1)

    def set_folder(self, folder: str):
        self._folder = folder

    def set_num_cores(self, n: int):
        self._num_cores = max(1, int(n))

    def get_settings(self) -> dict:
        return {'complexity_independent': self.indep_chk.isChecked(),
                'complexity_path': self.indep_sel.path(),
                'complexity_recursive': self.recursive_chk.isChecked(),
                'complexity_name': self.name_edit.text(),
                'complexity_name_mode': 'time' if self.name_time_rb.isChecked() else 'manual',
                'complexity_last_generated_name': self._last_generated_name,
                'complexity_save_mode': ('other' if self.other_location_rb.isChecked() else
                                         ('numbered' if self.numbered_rb.isChecked() else 'overwrite')),
                'complexity_save_location': self.save_location.path(),
                'complexity_reuse_existing': self.reuse_existing_chk.isChecked(),
                'complexity_source_file': self.source_file_edit.text(),
                'complexity_create_caption': self.create_caption_chk.isChecked(),
                'complexity_skip_existing': self.skip_existing_chk.isChecked(),
                'complexity_resolution_mode': self._resolution_settings()['mode'],
                'complexity_resolution_percent': self.resolution_percent_spin.value(),
                'complexity_resolution_width': self.resolution_width_spin.value(),
                'complexity_resolution_height': self.resolution_height_spin.value()}

    def load_settings(self, settings: dict):
        self.indep_chk.setChecked(bool(settings.get('complexity_independent', False)))
        self.indep_sel.set_path(str(settings.get('complexity_path', '')))
        self.recursive_chk.setChecked(bool(settings.get('complexity_recursive', True)))
        name_mode = settings.get('complexity_name_mode',
                                 'time' if settings.get('complexity_add_time', False) else 'manual')
        self.name_time_rb.setChecked(name_mode == 'time')
        self.name_manual_rb.setChecked(name_mode != 'time')
        save_mode = settings.get('complexity_save_mode', 'overwrite')
        self.overwrite_rb.setChecked(save_mode == 'overwrite')
        self.other_location_rb.setChecked(save_mode == 'other')
        self.numbered_rb.setChecked(save_mode == 'numbered')
        self.save_location.set_path(str(settings.get('complexity_save_location', '')))
        self.reuse_existing_chk.setChecked(bool(settings.get('complexity_reuse_existing', False)))
        self.source_file_edit.setText(str(settings.get('complexity_source_file', '')))
        self.create_caption_chk.setChecked(bool(settings.get('complexity_create_caption', False)))
        self.skip_existing_chk.setChecked(bool(settings.get('complexity_skip_existing', False)))
        resolution_mode = settings.get('complexity_resolution_mode', 'original')
        self.resolution_original_rb.setChecked(resolution_mode == 'original')
        self.resolution_percent_rb.setChecked(resolution_mode == 'percent')
        self.resolution_cutoff_rb.setChecked(resolution_mode == 'cutoff')
        self.resolution_percent_spin.setValue(int(settings.get('complexity_resolution_percent', 50)))
        self.resolution_width_spin.setValue(int(settings.get('complexity_resolution_width', 2048)))
        self.resolution_height_spin.setValue(int(settings.get('complexity_resolution_height', 2048)))
        self._toggle_resolution_inputs()
        if settings.get('complexity_name'):
            self.name_edit.setText(str(settings['complexity_name']))
        self._last_generated_name = str(settings.get('complexity_last_generated_name', ''))

    def _active_folder(self):
        return self.indep_sel.path() if self.indep_chk.isChecked() else self._folder

    def _set_busy(self, busy: bool):
        for button in (self.calibrate_btn, self.tag_btn, self.manual_btn, self.remove_btn):
            button.setEnabled(not busy)

    def _toggle_name_input(self, _checked: bool = False):
        self.name_edit.setVisible(self.name_manual_rb.isChecked())

    def _toggle_resolution_inputs(self, _checked: bool = False):
        self.resolution_percent_spin.setEnabled(self.resolution_percent_rb.isChecked())
        cutoff = self.resolution_cutoff_rb.isChecked()
        self.resolution_width_spin.setEnabled(cutoff)
        self.resolution_height_spin.setEnabled(cutoff)

    def _resolution_settings(self) -> dict:
        mode = ('percent' if self.resolution_percent_rb.isChecked() else
                'cutoff' if self.resolution_cutoff_rb.isChecked() else 'original')
        return {'mode': mode, 'percent': self.resolution_percent_spin.value(),
                'width': self.resolution_width_spin.value(),
                'height': self.resolution_height_spin.value()}

    def _browse_source_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '기존 결과 파일 선택', self.source_file_edit.text(),
            '기준선 또는 캐시 (*.json *.csv);;모든 파일 (*.*)',
        )
        if path:
            self.source_file_edit.setText(path)

    def _resolve_workspace(self, mode: str):
        if self.name_time_rb.isChecked():
            name = (datetime.now().strftime('%Y-%m-%d_%H-%M') if mode == 'calibrate'
                    else (self._last_generated_name or datetime.now().strftime('%Y-%m-%d_%H-%M')))
        else:
            name = self.name_edit.text().strip()
        base_dir = self.save_location.path() if self.other_location_rb.isChecked() else os.path.join(str(APP_DIR), 'workspace')
        if self.other_location_rb.isChecked() and not os.path.isdir(base_dir):
            raise ValueError('다른 저장 위치 폴더를 선택해주세요.')
        if mode == 'calibrate' and self.numbered_rb.isChecked():
            candidate = name
            suffix = 2
            while os.path.exists(os.path.join(base_dir, candidate)):
                candidate = f'{name}_{suffix}'
                suffix += 1
            name = candidate
        if mode == 'calibrate' and self.name_time_rb.isChecked():
            self._last_generated_name = name
        workspace = os.path.join(base_dir, name)
        if mode == 'calibrate' and self.overwrite_rb.isChecked() and os.path.isfile(os.path.join(workspace, 'calibration.json')):
            answer = QMessageBox.question(
                self, '기준선 덮어쓰기',
                f'기존 기준선과 캐시를 사용해 갱신합니다.\n{workspace}\n계속할까요?',
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None, None
        return name, workspace

    def _start(self, mode: str):
        folder = self._active_folder()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, '경고', '올바른 경로를 선택해주세요.')
            return
        name = ((self._last_generated_name or datetime.now().strftime('%Y-%m-%d_%H-%M'))
                if self.name_time_rb.isChecked() else self.name_edit.text().strip())
        if not name or any(c in name for c in '/\\<>:\"|?*') or name in ('.', '..'):
            QMessageBox.warning(self, '경고', '기준선 이름을 확인해주세요.')
            return
        source_file = self.source_file_edit.text().strip() if self.reuse_existing_chk.isChecked() else ''
        if self.reuse_existing_chk.isChecked() and (not source_file or not os.path.isfile(source_file)):
            QMessageBox.warning(self, '경고', '사용할 calibration.json 또는 image_data.csv 파일을 선택해주세요.')
            return
        try:
            name, workspace = self._resolve_workspace(mode)
        except ValueError as exc:
            QMessageBox.warning(self, '경고', str(exc))
            return
        if name is None:
            return
        if self.name_manual_rb.isChecked():
            self.name_edit.setText(name)
        if mode == 'tag' and QMessageBox.question(
                self, '확인', '이미지 속성 태그를 캡션 파일에 적용하시겠습니까?') != QMessageBox.StandardButton.Yes:
            return
        self._result_root = folder
        self._active_workspace = workspace
        self._set_busy(True)
        self.progress.setRange(0, 0)
        self.status.setText('작업 중...')
        self._worker = _AttributeWorker(mode, folder, name,
                                        self._num_cores, self.recursive_chk.isChecked(),
                                        self.create_caption_chk.isChecked(),
                                        self.skip_existing_chk.isChecked(), workspace,
                                        source_file, self._resolution_settings())
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(lambda data: self._on_done(mode, data))
        self._worker.error.connect(self._on_error)
        self._worker.start()

    @Slot(int, int, str)
    def _on_progress(self, done, total, current):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.status.setText(f'{done:,} / {total:,} 처리 중  {current}')

    @Slot(dict)
    def _on_done(self, mode: str, result: dict):
        self._set_busy(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if mode == 'remove':
            self.status.setText(f"복잡도 태깅 삭제 완료: {result['removed']:,}개, 복잡도 태그 없음 {result['no_tag']:,}개, 오류 {result['write_error']:,}개")
            self.status_path.clear()
            QMessageBox.information(self, '완료', self.status.text())
            return
        self._rows = result.get('rows', [])
        self._thresholds = {a: x['boundaries'] for a, x in result.get('thresholds', {}).items()}
        self._populate_tree()
        if mode == 'calibrate':
            warning = ('\n경고: ' + ', '.join(result.get('warnings', []))) if result.get('warnings') else ''
            warning = warning.replace('\n', '  ')
            elapsed = max(0, round(result.get('_elapsed_seconds', 0)))
            hours, remainder = divmod(elapsed, 3600)
            minutes, seconds = divmod(remainder, 60)
            duration = (f'{hours}시간 {minutes:02}분 {seconds:02}초' if hours else
                        f'{minutes}분 {seconds:02}초' if minutes else f'{seconds}초')
            self.status.setText(f"기준선 전체 계산 완료: 이미지 {result['images']:,}개, 분석 성공 {result['ok']:,}개, 오류 {result['errors']:,}개, 걸린 시간 {duration}{warning}")
            self.status_path.setText(f'저장: {self._active_workspace}')
            self.status_path.setToolTip(self._active_workspace)
        else:
            self.status.setText('태그 적용 완료: ' + ', '.join(
                f'{k} {result.get(k, 0):,}' for k in ('updated', 'unchanged', 'skipped_tagged', 'skipped_no_caption', 'analysis_error', 'write_error')))
            self.status_path.setText(f'기준선: {os.path.basename(self._active_workspace)}')
            self.status_path.setToolTip(self._active_workspace)
        self.name_edit.setText(self.name_edit.text().strip())

    @Slot(str)
    def _on_error(self, detail: str):
        self._set_busy(False)
        self.progress.setRange(0, 1)
        self.status.setText('작업 실패')
        QMessageBox.critical(self, '예상치 못한 오류', detail[-1500:])

    def _remove(self):
        folder = self._active_folder()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, '경고', '올바른 경로를 선택해주세요.')
            return
        answer = QMessageBox.question(
            self,
            '복잡도 태깅 삭제 확인',
            '기존 캡션의 다른 태그는 유지하고 복잡도 태깅만 삭제합니다.\n계속할까요?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self.progress.setRange(0, 0)
        self._worker = _AttributeWorker('remove', folder, self.name_edit.text().strip(),
                                        100, self._num_cores, self.recursive_chk.isChecked())
        self._worker.done.connect(lambda data: self._on_done('remove', data))
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _set_manual_threshold(self):
        name = self.name_edit.text().strip()
        if not name or any(c in name for c in '/\\<>:\"|?*') or name in ('.', '..'):
            QMessageBox.warning(self, '경고', '기준선 이름을 확인해주세요.')
            return
        try:
            name, workspace = self._resolve_workspace('tag')
        except ValueError as exc:
            QMessageBox.warning(self, '경고', str(exc))
            return
        attr, ok = QInputDialog.getItem(self, '수동 기준선', '속성 선택:', list(ATTRS), 0, False)
        if not ok:
            return
        labels = {'brightness': '밝기', 'saturation': '채도', 'contrast': '대비',
                  'color_temperature': '색조 편향', 'complexity': '복잡도'}
        values, ok = QInputDialog.getText(
            self, '수동 기준선',
            f'{labels[attr]} 경계값 4개를 쉼표로 입력하세요 (낮은 등급부터):',
        )
        if not ok:
            return
        try:
            bounds = [float(v.strip()) for v in values.split(',')]
            ComplexityTagger(name, workspace).set_manual_thresholds(attr, bounds)
            QMessageBox.information(self, '완료', f'{labels[attr]} 기준선을 저장했습니다.')
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, '기준선 오류', str(exc))

    def _populate_tree(self):
        self.tree.clear()
        root_item = QTreeWidgetItem(self.tree, [os.path.basename(self._result_root.rstrip('/\\')) or self._result_root, f'{len(self._rows):,}'])
        folders = {}
        grouped = defaultdict(list)
        for row in self._rows:
            grouped[row['path'].rsplit('/', 1)[0] if '/' in row['path'] else ''].append(row)
        folder_paths = {''}
        for rel in grouped:
            parts = rel.split('/') if rel else []
            folder_paths.update('/'.join(parts[:i]) for i in range(1, len(parts) + 1))
        for rel in sorted(folder_paths, key=lambda p: (p.count('/'), p.casefold())):
            parent_rel = rel.rpartition('/')[0] if rel else None
            parent = folders.get(parent_rel, root_item)
            item = QTreeWidgetItem(parent, [os.path.basename(rel) if rel else '(루트)', ''])
            folders[rel] = item
        for rel, item in folders.items():
            subtree = [r for r in self._rows if not rel or r['path'].startswith(rel + '/') or r['path'].rsplit('/', 1)[0] == rel]
            item.setText(1, f'{len(subtree):,}')
            item.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'folder', 'rows': subtree, 'rel': rel})
        root_item.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'folder', 'rows': self._rows, 'rel': ''})
        for rel, rows in grouped.items():
            folder_item = folders.get(rel, root_item)
            for row in rows:
                child = QTreeWidgetItem(folder_item, [os.path.basename(row['path']), ''])
                child.setToolTip(0, row['path'])
                child.setData(0, Qt.ItemDataRole.UserRole, {'kind': 'image', 'row': row})
        root_item.setExpanded(True)
        self.info_tree.clear()
        self.preview.setText('선택 항목 미리보기')
        self.preview.setPixmap(QPixmap())
        self._resize_tree_columns()

    def _resize_tree_columns(self):
        available = self.tree.viewport().width()
        if available > 0:
            count_width = max(55, int(available * 0.20))
            self.tree.setColumnWidth(1, count_width)
            self.tree.setColumnWidth(0, max(80, available - count_width))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            if watched is self.preview and self._current_image:
                QTimer.singleShot(0, self._show_preview)
            elif watched is self.tree:
                QTimer.singleShot(0, self._resize_tree_columns)
        return super().eventFilter(watched, event)

    def _grade(self, attr, value):
        bounds = self._thresholds.get(attr)
        if not bounds: return ''
        from core.complexity_tagger import grade_label
        return grade_label(attr, value, bounds)

    def _on_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        self.info_tree.clear()
        if data.get('kind') == 'image':
            row = data['row']
            image_path = os.path.join(self._result_root, *row['path'].split('/'))
            self._current_image = image_path
            self._show_preview()
            self._show_image_info(row, image_path)
        else:
            self._current_image = ''
            self.preview.setPixmap(QPixmap())
            self.preview.setText('folder')
            self._show_folder_info(data.get('rows', []), data.get('rel', ''))

    def _show_preview(self):
        if not self._current_image:
            return
        reader = QImageReader(self._current_image)
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid():
            reader.setScaledSize(source_size.scaled(
                self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
            ))
        pixmap = QPixmap.fromImage(reader.read())
        if pixmap.isNull():
            self.preview.setText('이미지를 미리 볼 수 없습니다.')
            self.preview.setPixmap(QPixmap())
            return
        self.preview.setText('')
        self.preview.setPixmap(pixmap.scaled(
            self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._current_image:
            self._show_preview()

    def _add_info(self, label, value, grade=''):
        QTreeWidgetItem(self.info_tree, [str(label), str(value), str(grade)])

    def _show_folder_info(self, rows, rel):
        valid = [r for r in rows if r.get('status') == 'ok']
        failed = sum(1 for r in rows if r.get('status') == 'error')
        pending = sum(1 for r in rows if r.get('status') == 'not_analyzed')
        folder_path = os.path.join(self._result_root, *rel.split('/')) if rel else self._result_root
        folder_name = os.path.basename(folder_path.rstrip('/\\')) or folder_path
        self._add_info('이름', folder_name)
        self._add_info('경로', folder_path)
        self._add_info('이미지 수', f'{len(rows):,}개 중 분석 완료 {len(valid):,}개',
                       f'오류 {failed:,}개 · 미계산 {pending:,}개')
        for attr in ATTRS:
            vals = [float(r[attr]) for r in valid if r.get(attr) not in ('', None)]
            mean = sum(vals) / len(vals) if vals else None
            self._add_info(attr, f'{mean:.6f}' if mean is not None else '계산된 값 없음',
                           f'평균 {self._grade(attr, mean)}' if mean is not None else '')

    def _show_image_info(self, row, image_path):
        self._add_info('이름', os.path.basename(image_path))
        self._add_info('경로', image_path)
        for attr in ATTRS:
            value = row.get(attr, '')
            if row.get('status') == 'ok' and value not in ('', None):
                number = float(value)
                self._add_info(attr, f'{number:.6f}', self._grade(attr, number))
            else:
                self._add_info(attr, '계산 불가', '')
        resolution = f"{row.get('width')} × {row.get('height')}" if row.get('width') else '알 수 없음'
        self._add_info('해상도', resolution)
        state = row.get('error') or row.get('status', '알 수 없음')
        state = {'ok': '완료', 'error': '오류', 'not_analyzed': '미계산'}.get(state, state)
        self._add_info('분석 상태', state)
        caption_path = os.path.splitext(image_path)[0] + '.txt'
        if not os.path.isfile(caption_path):
            tagged = '태깅 없음'
        else:
            try:
                with open(caption_path, 'r', encoding='utf-8', newline='') as f:
                    state = tag_status(f.read())
                tagged = {'complete': '복잡도 태깅 완료', 'partial': '복잡도 일부 태깅',
                          'none': '복잡도 미태깅'}[state]
            except (OSError, UnicodeError):
                tagged = '캡션 읽기 오류'
        self._add_info('태깅 여부', tagged)

