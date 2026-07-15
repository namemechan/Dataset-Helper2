"""
ui/tabs/tag_tab.py

태그 처리 탭 — 태그 이동, 추가, 치환, 삭제, CSV 처리 등.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from PySide6.QtCore    import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QCheckBox, QRadioButton,
    QPushButton, QButtonGroup, QComboBox, QSpinBox,
    QFileDialog, QMessageBox, QScrollArea, QSizePolicy, QFrame,
)

from core.tag_processor import TagProcessor
from utils.common import get_paired_files
from ui.widgets.log_widget  import LogWidget
from ui.widgets.worker_base import SafeWorker
from ui.constants import (
    PADDING_DEFAULT, PADDING_SMALL,
    SPACING_DEFAULT, SPACING_SMALL,
)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _TagWorker(SafeWorker):
    finished = Signal(int, int, list)

    def __init__(self, text_files, options, num_cores, folder_path) -> None:
        super().__init__()
        self._files       = text_files
        self._options     = options
        self._num_cores   = num_cores
        self._folder_path = folder_path

    def work(self) -> None:
        success, fail, logs = TagProcessor.process_folder(
            self._files, self._options, self._num_cores, self._folder_path,
        )
        self.finished.emit(success, fail, logs)


# ---------------------------------------------------------------------------
# 탭 위젯
# ---------------------------------------------------------------------------

class TagTab(QWidget):
    """태그 처리 탭."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._folder:    str = ''
        self._num_cores: int = 1
        self._worker: QThread | None = None
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 스크롤 영역 (옵션 패널)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        scroll_layout  = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(PADDING_DEFAULT, PADDING_DEFAULT,
                                         PADDING_DEFAULT, PADDING_SMALL)
        scroll_layout.setSpacing(SPACING_DEFAULT)
        scroll.setWidget(scroll_content)

        # ── 하위 폴더 옵션 ────────────────────────────────────
        top_bar = QHBoxLayout()
        self.subdirs_chk = QCheckBox('하위 폴더 포함 검색')
        top_bar.addWidget(self.subdirs_chk)
        top_bar.addStretch()
        scroll_layout.addLayout(top_bar)

        # ── 1. 인원수 / solo 태그 이동 ────────────────────────
        grp_person = QGroupBox('인원수 · solo 태그 이동')
        lay_person = QHBoxLayout(grp_person)
        self.person_chk = QCheckBox('인원수 태그 맨 앞으로 이동 (1girl, 2boys 등)')
        self.solo_chk   = QCheckBox("'solo' 태그도 함께 이동")
        lay_person.addWidget(self.person_chk)
        lay_person.addWidget(self.solo_chk)
        lay_person.addStretch()
        scroll_layout.addWidget(grp_person)

        # ── 1.1 누락 인원수 태그 자동 추가 ───────────────────
        grp_missing = QGroupBox('인원수 태그 누락 시 자동 추가')
        lay_missing  = QHBoxLayout(grp_missing)

        self.missing_chk = QCheckBox('사용')

        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.VLine)

        gender_label      = QLabel('성별:')
        self.girl_rb      = QRadioButton('Girl')
        self.boy_rb       = QRadioButton('Boy')
        self.girl_rb.setChecked(True)
        self._gender_grp  = QButtonGroup(self)
        self._gender_grp.addButton(self.girl_rb, 0)
        self._gender_grp.addButton(self.boy_rb,  1)

        count_label    = QLabel('인원:')
        self.count_cmb = QComboBox()
        self.count_cmb.addItems(['1', '2', '3', '4', '5', '6+'])

        for w in (self.missing_chk, sep1, gender_label,
                  self.girl_rb, self.boy_rb, count_label, self.count_cmb):
            lay_missing.addWidget(w)
        lay_missing.addStretch()
        scroll_layout.addWidget(grp_missing)

        # ── 1.5 태그 추가 ─────────────────────────────────────
        grp_add = QGroupBox('태그 추가 (인원수/solo 뒤에 자동 삽입)')
        lay_add = QVBoxLayout(grp_add)

        row_add = QHBoxLayout()
        self.add_chk   = QCheckBox('사용')
        add_lbl        = QLabel('추가할 태그:')
        self.add_edit  = QLineEdit()
        self.add_edit.setPlaceholderText('태그1, 태그2, ...')
        row_add.addWidget(self.add_chk)
        row_add.addWidget(add_lbl)
        row_add.addWidget(self.add_edit)

        row_add_cond = QHBoxLayout()
        self.cond_add_chk  = QCheckBox('조건부 추가 사용')
        cond_add_lbl       = QLabel('조건 태그 (|로 구분):')
        self.cond_add_edit = QLineEdit()
        self.cond_add_edit.setEnabled(False)
        row_add_cond.addWidget(self.cond_add_chk)
        row_add_cond.addWidget(cond_add_lbl)
        row_add_cond.addWidget(self.cond_add_edit)

        lay_add.addLayout(row_add)
        lay_add.addLayout(row_add_cond)
        scroll_layout.addWidget(grp_add)

        # ── 2. 추가 이동 태그 ─────────────────────────────────
        grp_move = QGroupBox('추가 이동 태그 (인원수/추가 태그 뒤)')
        lay_move = QHBoxLayout(grp_move)
        self.custom_move_chk  = QCheckBox('사용')
        move_lbl              = QLabel('태그 (|로 구분):')
        self.custom_move_edit = QLineEdit('simple background|white background')
        lay_move.addWidget(self.custom_move_chk)
        lay_move.addWidget(move_lbl)
        lay_move.addWidget(self.custom_move_edit)
        scroll_layout.addWidget(grp_move)

        # ── 3. 태그 치환 ──────────────────────────────────────
        grp_replace = QGroupBox('태그 치환 (찾아서 변경 — 연속 태그 가능)')
        lay_replace = QHBoxLayout(grp_replace)
        self.replace_chk       = QCheckBox('사용')
        find_lbl               = QLabel('찾을 태그:')
        self.replace_find_edit = QLineEdit()
        arrow_lbl              = QLabel('→')
        with_lbl               = QLabel('변경할 태그:')
        self.replace_with_edit = QLineEdit()
        for w in (self.replace_chk, find_lbl, self.replace_find_edit,
                  arrow_lbl, with_lbl, self.replace_with_edit):
            lay_replace.addWidget(w)
        scroll_layout.addWidget(grp_replace)

        # ── 3.5 인접 태그 접두/접미 추가 ─────────────────────
        grp_neighbor = QGroupBox('인접 태그 접두/접미 추가')
        lay_neighbor = QVBoxLayout(grp_neighbor)

        row_nb_top = QHBoxLayout()
        self.neighbor_chk         = QCheckBox('사용')
        nb_target_lbl             = QLabel('기준 태그:')
        self.neighbor_target_edit = QLineEdit()
        self.neighbor_target_edit.setMaximumWidth(150)
        nb_text_lbl               = QLabel('추가할 텍스트:')
        self.neighbor_text_edit   = QLineEdit()
        self.neighbor_text_edit.setMaximumWidth(150)
        for w in (self.neighbor_chk, nb_target_lbl, self.neighbor_target_edit,
                  nb_text_lbl, self.neighbor_text_edit):
            row_nb_top.addWidget(w)
        row_nb_top.addStretch()

        row_nb_bot = QHBoxLayout()
        pos_lbl         = QLabel('대상 위치:')
        self.nb_before  = QRadioButton('앞 태그')
        self.nb_after   = QRadioButton('뒤 태그')
        self.nb_after.setChecked(True)
        self._nb_pos_grp = QButtonGroup(self)
        self._nb_pos_grp.addButton(self.nb_before, 0)
        self._nb_pos_grp.addButton(self.nb_after,  1)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.VLine)

        add_pos_lbl    = QLabel('추가 방식:')
        self.nb_prefix = QRadioButton('접두(앞에)')
        self.nb_suffix = QRadioButton('접미(뒤에)')
        self.nb_prefix.setChecked(True)
        self._nb_add_grp = QButtonGroup(self)
        self._nb_add_grp.addButton(self.nb_prefix, 0)
        self._nb_add_grp.addButton(self.nb_suffix, 1)

        for w in (pos_lbl, self.nb_before, self.nb_after,
                  sep2, add_pos_lbl, self.nb_prefix, self.nb_suffix):
            row_nb_bot.addWidget(w)
        row_nb_bot.addStretch()

        lay_neighbor.addLayout(row_nb_top)
        lay_neighbor.addLayout(row_nb_bot)
        scroll_layout.addWidget(grp_neighbor)

        # ── 3.7 CSV 기반 특수 처리 ────────────────────────────
        grp_csv = QGroupBox('CSV 기반 특수 처리')
        lay_csv = QVBoxLayout(grp_csv)

        row_csv_file = QHBoxLayout()
        self.csv_chk       = QCheckBox('사용')
        csv_path_lbl       = QLabel('CSV 파일:')
        self.csv_path_edit = QLineEdit()
        self.csv_browse_btn = QPushButton('파일 선택')
        self.csv_browse_btn.setMaximumWidth(80)
        for w in (self.csv_chk, csv_path_lbl, self.csv_path_edit, self.csv_browse_btn):
            row_csv_file.addWidget(w)

        row_csv_opts = QHBoxLayout()
        csv_cat_lbl     = QLabel('태그 종류(숫자):')
        self.csv_cat_spin = QSpinBox()
        self.csv_cat_spin.setRange(0, 99)
        self.csv_cat_spin.setMaximumWidth(60)
        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.VLine)
        csv_mode_lbl     = QLabel('작업:')
        self.csv_add_rb  = QRadioButton('추가')
        self.csv_rep_rb  = QRadioButton('치환')
        self.csv_del_rb  = QRadioButton('삭제')
        self.csv_add_rb.setChecked(True)
        self._csv_mode_grp = QButtonGroup(self)
        self._csv_mode_grp.addButton(self.csv_add_rb, 0)
        self._csv_mode_grp.addButton(self.csv_rep_rb, 1)
        self._csv_mode_grp.addButton(self.csv_del_rb, 2)
        for w in (csv_cat_lbl, self.csv_cat_spin, sep3, csv_mode_lbl,
                  self.csv_add_rb, self.csv_rep_rb, self.csv_del_rb):
            row_csv_opts.addWidget(w)
        row_csv_opts.addStretch()

        row_csv_input = QHBoxLayout()
        csv_pos_lbl      = QLabel('추가 위치:')
        self.csv_pre_rb  = QRadioButton('앞(접두)')
        self.csv_suf_rb  = QRadioButton('뒤(접미)')
        self.csv_pre_rb.setChecked(True)
        self._csv_pos_grp = QButtonGroup(self)
        self._csv_pos_grp.addButton(self.csv_pre_rb, 0)
        self._csv_pos_grp.addButton(self.csv_suf_rb, 1)
        csv_text_lbl      = QLabel('입력 문자:')
        self.csv_text_edit = QLineEdit()
        for w in (csv_pos_lbl, self.csv_pre_rb, self.csv_suf_rb,
                  csv_text_lbl, self.csv_text_edit):
            row_csv_input.addWidget(w)

        lay_csv.addLayout(row_csv_file)
        lay_csv.addLayout(row_csv_opts)
        lay_csv.addLayout(row_csv_input)
        scroll_layout.addWidget(grp_csv)

        # ── 4. 태그 삭제 ──────────────────────────────────────
        grp_delete = QGroupBox('태그 삭제 (쉼표 자동 정리 — 연속 태그 가능)')
        lay_delete = QVBoxLayout(grp_delete)

        row_del = QHBoxLayout()
        self.delete_chk       = QCheckBox('사용')
        del_lbl               = QLabel('삭제할 태그 (|로 구분):')
        self.delete_tags_edit = QLineEdit()
        for w in (self.delete_chk, del_lbl, self.delete_tags_edit):
            row_del.addWidget(w)

        row_del_cond = QHBoxLayout()
        self.cond_del_chk  = QCheckBox('조건부 삭제 사용')
        cond_del_lbl       = QLabel('조건 태그 (|로 구분):')
        self.cond_del_edit = QLineEdit()
        self.cond_del_edit.setEnabled(False)
        for w in (self.cond_del_chk, cond_del_lbl, self.cond_del_edit):
            row_del_cond.addWidget(w)

        lay_delete.addLayout(row_del)
        lay_delete.addLayout(row_del_cond)
        scroll_layout.addWidget(grp_delete)

        scroll_layout.addStretch()

        # ── 실행 버튼 바 ──────────────────────────────────────
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(PADDING_DEFAULT, PADDING_SMALL,
                                   PADDING_DEFAULT, PADDING_SMALL)
        btn_bar.setSpacing(SPACING_SMALL)

        self.preview_btn = QPushButton('미리보기')
        self.execute_btn = QPushButton('태그 처리 실행')
        self.execute_btn.setProperty('accent', True)
        self.undo_btn    = QPushButton('실행 취소')

        btn_bar.addWidget(self.preview_btn)
        btn_bar.addWidget(self.execute_btn)
        btn_bar.addWidget(self.undo_btn)
        # ── 로그 ──────────────────────────────────────────────
        self.log_widget = LogWidget(show_controls=False)
        btn_bar.addStretch()
        btn_bar.addWidget(self.log_widget.clear_btn)
        btn_bar.addWidget(self.log_widget.copy_btn)
        log_container   = QWidget()
        log_layout      = QVBoxLayout(log_container)
        log_layout.setContentsMargins(PADDING_DEFAULT, 0,
                                      PADDING_DEFAULT, PADDING_DEFAULT)
        log_layout.addWidget(self.log_widget)

        root.addWidget(scroll, stretch=2)

        sep_line = QFrame()
        sep_line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep_line)

        root.addLayout(btn_bar)
        root.addWidget(log_container, stretch=1)

    # ------------------------------------------------------------------
    # 시그널 연결
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.cond_add_chk.toggled.connect(self.cond_add_edit.setEnabled)
        self.cond_del_chk.toggled.connect(self.cond_del_edit.setEnabled)
        self._csv_mode_grp.idToggled.connect(self._on_csv_mode_change)
        self.csv_browse_btn.clicked.connect(self._on_csv_browse)
        self.preview_btn.clicked.connect(self._on_preview)
        self.execute_btn.clicked.connect(self._on_execute)
        self.undo_btn.clicked.connect(self._on_undo)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def set_folder(self, folder: str) -> None:
        self._folder = folder

    def set_num_cores(self, n: int) -> None:
        self._num_cores = n

    def get_settings(self) -> dict:
        return {
            'tag_subdirs':         self.subdirs_chk.isChecked(),
            'tag_person':          self.person_chk.isChecked(),
            'tag_solo':            self.solo_chk.isChecked(),
            'tag_missing':         self.missing_chk.isChecked(),
            'tag_missing_gender':  'girl' if self.girl_rb.isChecked() else 'boy',
            'tag_missing_count':   self.count_cmb.currentText(),
            'tag_add':             self.add_chk.isChecked(),
            'tag_add_text':        self.add_edit.text(),
            'tag_cond_add':        self.cond_add_chk.isChecked(),
            'tag_cond_add_text':   self.cond_add_edit.text(),
            'tag_custom_move':     self.custom_move_chk.isChecked(),
            'tag_custom_move_text':self.custom_move_edit.text(),
            'tag_replace':         self.replace_chk.isChecked(),
            'tag_replace_find':    self.replace_find_edit.text(),
            'tag_replace_with':    self.replace_with_edit.text(),
            'tag_neighbor':        self.neighbor_chk.isChecked(),
            'tag_neighbor_target': self.neighbor_target_edit.text(),
            'tag_neighbor_text':   self.neighbor_text_edit.text(),
            'tag_neighbor_pos':    'before' if self.nb_before.isChecked() else 'after',
            'tag_neighbor_add':    'prefix' if self.nb_prefix.isChecked() else 'suffix',
            'tag_csv':             self.csv_chk.isChecked(),
            'tag_csv_path':        self.csv_path_edit.text(),
            'tag_csv_cat':         self.csv_cat_spin.value(),
            'tag_csv_mode':        self._csv_mode_str(),
            'tag_csv_pos':         'prefix' if self.csv_pre_rb.isChecked() else 'suffix',
            'tag_csv_text':        self.csv_text_edit.text(),
            'tag_delete':          self.delete_chk.isChecked(),
            'tag_delete_text':     self.delete_tags_edit.text(),
            'tag_cond_del':        self.cond_del_chk.isChecked(),
            'tag_cond_del_text':   self.cond_del_edit.text(),
        }

    def load_settings(self, s: dict) -> None:
        _b = lambda k, d=False: bool(s.get(k, d))
        _s = lambda k, d='':  str(s.get(k, d))
        _i = lambda k, d=0:   int(s.get(k, d))

        self.subdirs_chk.setChecked(_b('tag_subdirs'))
        self.person_chk.setChecked(_b('tag_person'))
        self.solo_chk.setChecked(_b('tag_solo'))
        self.missing_chk.setChecked(_b('tag_missing'))
        gender = _s('tag_missing_gender', 'girl')
        self.girl_rb.setChecked(gender == 'girl')
        self.boy_rb.setChecked(gender == 'boy')
        idx = self.count_cmb.findText(_s('tag_missing_count', '1'))
        if idx >= 0: self.count_cmb.setCurrentIndex(idx)
        self.add_chk.setChecked(_b('tag_add'))
        self.add_edit.setText(_s('tag_add_text'))
        self.cond_add_chk.setChecked(_b('tag_cond_add'))
        self.cond_add_edit.setText(_s('tag_cond_add_text'))
        self.custom_move_chk.setChecked(_b('tag_custom_move'))
        self.custom_move_edit.setText(_s('tag_custom_move_text', 'simple background|white background'))
        self.replace_chk.setChecked(_b('tag_replace'))
        self.replace_find_edit.setText(_s('tag_replace_find'))
        self.replace_with_edit.setText(_s('tag_replace_with'))
        self.neighbor_chk.setChecked(_b('tag_neighbor'))
        self.neighbor_target_edit.setText(_s('tag_neighbor_target'))
        self.neighbor_text_edit.setText(_s('tag_neighbor_text'))
        nb_pos = _s('tag_neighbor_pos', 'after')
        self.nb_before.setChecked(nb_pos == 'before')
        self.nb_after.setChecked(nb_pos == 'after')
        nb_add = _s('tag_neighbor_add', 'prefix')
        self.nb_prefix.setChecked(nb_add == 'prefix')
        self.nb_suffix.setChecked(nb_add == 'suffix')
        self.csv_chk.setChecked(_b('tag_csv'))
        self.csv_path_edit.setText(_s('tag_csv_path'))
        self.csv_cat_spin.setValue(_i('tag_csv_cat'))
        csv_mode = _s('tag_csv_mode', 'add')
        self.csv_add_rb.setChecked(csv_mode == 'add')
        self.csv_rep_rb.setChecked(csv_mode == 'replace')
        self.csv_del_rb.setChecked(csv_mode == 'delete')
        csv_pos = _s('tag_csv_pos', 'prefix')
        self.csv_pre_rb.setChecked(csv_pos == 'prefix')
        self.csv_suf_rb.setChecked(csv_pos == 'suffix')
        self.csv_text_edit.setText(_s('tag_csv_text'))
        self.delete_chk.setChecked(_b('tag_delete'))
        self.delete_tags_edit.setText(_s('tag_delete_text'))
        self.cond_del_chk.setChecked(_b('tag_cond_del'))
        self.cond_del_edit.setText(_s('tag_cond_del_text'))

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _csv_mode_str(self) -> str:
        if self.csv_rep_rb.isChecked(): return 'replace'
        if self.csv_del_rb.isChecked(): return 'delete'
        return 'add'

    def _load_csv_tags(self) -> set:
        path = self.csv_path_edit.text().strip()
        if not path or not os.path.exists(path):
            return set()
        cat = str(self.csv_cat_spin.value())
        tags: set = set()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for row in csv.reader(f):
                    if len(row) >= 2 and row[1].strip() == cat:
                        tags.add(row[0].strip().lower().replace('_', ' '))
        except Exception as e:
            print(f'CSV 로드 오류: {e}')
        return tags

    def _build_options(self) -> dict:
        return {
            'use_move_person':        self.person_chk.isChecked(),
            'use_move_solo':          self.solo_chk.isChecked(),
            'use_missing_tag':        self.missing_chk.isChecked(),
            'missing_gender':         'girl' if self.girl_rb.isChecked() else 'boy',
            'missing_count':          self.count_cmb.currentText(),
            'use_add':                self.add_chk.isChecked(),
            'add_tags':               self.add_edit.text().strip(),
            'use_conditional_add':    self.cond_add_chk.isChecked(),
            'condition_add_tags':     self.cond_add_edit.text().strip(),
            'use_move_custom':        self.custom_move_chk.isChecked(),
            'move_custom_tags':       [t.strip() for t in self.custom_move_edit.text().split('|') if t.strip()],
            'use_replace':            self.replace_chk.isChecked(),
            'replace_find':           self.replace_find_edit.text().strip(),
            'replace_with':           self.replace_with_edit.text().strip(),
            'use_neighbor_modify':    self.neighbor_chk.isChecked(),
            'neighbor_target':        self.neighbor_target_edit.text().strip(),
            'neighbor_pos':           'before' if self.nb_before.isChecked() else 'after',
            'neighbor_add_pos':       'prefix' if self.nb_prefix.isChecked() else 'suffix',
            'neighbor_text':          self.neighbor_text_edit.text().strip(),
            'use_csv_process':        self.csv_chk.isChecked(),
            'csv_file_path':          self.csv_path_edit.text().strip(),
            'csv_category':           str(self.csv_cat_spin.value()),
            'csv_mode':               self._csv_mode_str(),
            'csv_add_pos':            'prefix' if self.csv_pre_rb.isChecked() else 'suffix',
            'csv_input_text':         self.csv_text_edit.text().strip(),
            'csv_tags_set':           self._load_csv_tags() if self.csv_chk.isChecked() else set(),
            'use_delete':             self.delete_chk.isChecked(),
            'delete_tags':            [t.strip() for t in self.delete_tags_edit.text().split('|') if t.strip()],
            'use_conditional_delete': self.cond_del_chk.isChecked(),
            'condition_delete_tags':  self.cond_del_edit.text().strip(),
        }

    def _any_option_active(self, options: dict) -> bool:
        return any([
            options['use_move_person'], options['use_move_solo'],
            options['use_move_custom'], options['use_replace'],
            options['use_delete'],      options['use_add'],
            options['use_missing_tag'], options['use_neighbor_modify'],
            options['use_csv_process'],
        ])

    def _get_text_files(self):
        paired = get_paired_files(self._folder, recursive=self.subdirs_chk.isChecked())
        return [txt for _, txt in paired]

    def _set_busy(self, busy: bool) -> None:
        self.preview_btn.setEnabled(not busy)
        self.execute_btn.setEnabled(not busy)
        self.undo_btn.setEnabled(not busy)

    def _check_folder(self) -> bool:
        if not self._folder:
            QMessageBox.warning(self, '경고', '먼저 작업 폴더를 선택하세요.')
            return False
        return True

    # ------------------------------------------------------------------
    # 슬롯
    # ------------------------------------------------------------------

    def _on_csv_mode_change(self, btn_id: int, checked: bool) -> None:
        if not checked:
            return
        mode = self._csv_mode_str()
        is_delete  = (mode == 'delete')
        is_replace = (mode == 'replace')
        self.csv_pre_rb.setEnabled(not is_delete and not is_replace)
        self.csv_suf_rb.setEnabled(not is_delete and not is_replace)
        self.csv_text_edit.setEnabled(not is_delete)

    def _on_csv_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, 'CSV 파일 선택', '', 'CSV 파일 (*.csv);;모든 파일 (*.*)',
        )
        if path:
            self.csv_path_edit.setText(path)

    def _on_preview(self) -> None:
        if not self._check_folder():
            return
        options = self._build_options()
        if not self._any_option_active(options):
            QMessageBox.warning(self, '경고', '최소한 하나의 기능을 선택하세요.')
            return
        text_files = self._get_text_files()
        if not text_files:
            QMessageBox.information(self, '알림', '처리할 txt 파일이 없습니다.')
            return
        lines = TagProcessor.preview_tag_processing(text_files, options, preview_count=10)
        self.log_widget.clear()
        self.log_widget.append_lines(lines)

    def _on_execute(self) -> None:
        if not self._check_folder():
            return
        options = self._build_options()
        if not self._any_option_active(options):
            QMessageBox.warning(self, '경고', '최소한 하나의 기능을 선택하세요.')
            return
        text_files = self._get_text_files()
        if not text_files:
            QMessageBox.information(self, '알림', '처리할 txt 파일이 없습니다.')
            return

        lines = ['선택한 옵션으로 태그 처리를 진행하시겠습니까?\n']
        if options['use_replace']:        lines.append('- 태그 치환')
        if options['use_neighbor_modify']:lines.append(f"- 인접 태그 수정 (기준: {options['neighbor_target']})")
        if options['use_csv_process']:    lines.append(f"- CSV 기반 처리 (종류: {options['csv_category']})")
        if options['use_delete']:
            s = ' (조건부)' if options['use_conditional_delete'] else ''
            lines.append(f'- 태그 삭제{s}')
        if options['use_missing_tag']:    lines.append(f"- 누락 인원수 태그 추가 ({options['missing_count']}{options['missing_gender']})")
        if options['use_move_person']:    lines.append('- 인원수 태그 이동')
        if options['use_move_solo']:      lines.append("- 'solo' 태그 이동")
        if options['use_add']:
            s = ' (조건부)' if options['use_conditional_add'] else ''
            lines.append(f'- 태그 추가{s}')
        if options['use_move_custom']:    lines.append('- 추가 태그 이동')

        answer = QMessageBox.question(self, '확인', '\n'.join(lines))
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        self._worker = _TagWorker(text_files, options, self._num_cores, self._folder)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    @Slot(int, int, list)
    def _on_done(self, success: int, fail: int, logs: list) -> None:
        self._set_busy(False)
        self.log_widget.clear()
        self.log_widget.append_line(f'성공: {success}개, 실패: {fail}개')
        self.log_widget.append_lines(logs)
        QMessageBox.information(self, '완료', f'태그 처리 완료\n성공: {success}개, 실패: {fail}개')

    @Slot(str)
    def _on_worker_error(self, traceback_text: str) -> None:
        """워커 스레드에서 예상치 못한 예외가 발생했을 때의 안전망."""
        self._set_busy(False)
        QMessageBox.critical(
            self, '예상치 못한 오류',
            f'태그 처리 중 오류가 발생했습니다.\n\n{traceback_text[-1500:]}',
        )

    def _on_undo(self) -> None:
        if not self._check_folder():
            return
        answer = QMessageBox.question(self, '확인', '마지막 태그 처리 작업을 취소하시겠습니까?')
        if answer != QMessageBox.StandardButton.Yes:
            return
        success, fail, logs = TagProcessor.undo_last_processing(self._folder)
        self.log_widget.clear()
        self.log_widget.append_line(f'복구 성공: {success}개, 실패: {fail}개')
        self.log_widget.append_lines(logs)
        if success > 0:
            QMessageBox.information(self, '완료', f'실행 취소 완료\n복구: {success}개, 실패: {fail}개')
        else:
            QMessageBox.information(self, '알림', '실행 취소할 내역이 없습니다.')
