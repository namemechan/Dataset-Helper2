"""
ui/tabs/converter_tab.py

이미지 변환 탭 — 이미지 일괄 포맷 변환, 메타데이터 보존, 원본 삭제 기능.
"""

from __future__ import annotations

import os

from PySide6.QtCore    import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QCheckBox, QRadioButton,
    QPushButton, QButtonGroup, QProgressBar, QSpinBox,
    QDoubleSpinBox, QComboBox, QSplitter, QFileDialog, QMessageBox,
    QSizePolicy,
)

from image.converter_engine  import batch_convert_images
from image.file_utils         import scan_directory
from utils.settings           import load_converter_settings, save_converter_settings
from utils.logger             import logger
from utils.common             import format_file_size, RateLimiter
from ui.widgets.log_widget      import LogWidget
from ui.widgets.folder_selector import FolderSelector
from ui.widgets.worker_base     import SafeWorker
from ui.constants import (
    PADDING_DEFAULT, PADDING_SMALL, SPACING_DEFAULT, SPACING_SMALL,
)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _ConvertWorker(SafeWorker):
    progress  = Signal(int, int, str)   # done, total, current_file
    finished  = Signal(dict)

    def __init__(self, file_list: list, settings: dict) -> None:
        super().__init__()
        self._file_list = file_list
        self._settings  = settings
        self._stop  = False
        self._pause = False

    def request_stop(self)  -> None: self._stop  = True
    def request_pause(self) -> None: self._pause = True
    def request_resume(self)-> None: self._pause = False

    def work(self) -> None:
        def on_progress(done, total, path):
            self.progress.emit(done, total, path)

        control = {
            'check_stop':  lambda: self._stop,
            'check_pause': lambda: self._pause,
        }
        results = batch_convert_images(
            self._file_list, self._settings, on_progress, control,
        )
        self.finished.emit(results)


# ---------------------------------------------------------------------------
# 탭 위젯
# ---------------------------------------------------------------------------

class ConverterTab(QWidget):
    """이미지 변환 탭."""

    _INPUT_EXTS = ['JPG', 'PNG', 'WEBP', 'GIF', 'BMP', 'TIFF']
    _OUT_FMTS   = [('PNG', 'png'), ('JPG', 'jpeg'), ('WEBP', 'webp'), ('GIF', 'gif')]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._num_cores   = os.cpu_count() or 1
        self._worker: _ConvertWorker | None = None
        self._last_results: list = []
        self._rate_limiter = RateLimiter(10)
        self._settings = load_converter_settings()

        self._build_ui()
        self._connect_signals()
        self._load_settings_to_ui()

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                PADDING_SMALL, PADDING_SMALL)
        root.setSpacing(SPACING_SMALL)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ── 왼쪽 설정 패널 ────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(SPACING_SMALL)

        # 입출력
        grp_io = QGroupBox('입력 / 출력 설정')
        form_io = QFormLayout(grp_io)
        form_io.setSpacing(SPACING_SMALL)

        self.src_selector = FolderSelector('입력 폴더 선택')
        self.dst_selector = FolderSelector('출력 폴더 선택')

        self.out_to_in_chk       = QCheckBox('입력 폴더에 출력')
        conflict_widget           = QWidget()
        conflict_layout           = QHBoxLayout(conflict_widget)
        conflict_layout.setContentsMargins(0, 0, 0, 0)
        conflict_lbl              = QLabel('이름 충돌 시:')
        self.conflict_skip_rb     = QRadioButton('패스')
        self.conflict_overwrite_rb= QRadioButton('덮어쓰기')
        self.conflict_rename_rb   = QRadioButton('숫자 추가')
        self.conflict_rename_rb.setChecked(True)
        self._conflict_grp = QButtonGroup(self)
        self._conflict_grp.addButton(self.conflict_skip_rb,      0)
        self._conflict_grp.addButton(self.conflict_overwrite_rb, 1)
        self._conflict_grp.addButton(self.conflict_rename_rb,    2)
        for w in (conflict_lbl, self.conflict_skip_rb,
                  self.conflict_overwrite_rb, self.conflict_rename_rb):
            conflict_layout.addWidget(w)
        conflict_layout.addStretch()

        suffix_widget  = QWidget()
        suffix_layout  = QHBoxLayout(suffix_widget)
        suffix_layout.setContentsMargins(0, 0, 0, 0)
        self.suffix_chk  = QCheckBox('파일명 접미사 추가:')
        self.suffix_edit = QLineEdit('_converted')
        self.suffix_edit.setMaximumWidth(120)
        suffix_layout.addWidget(self.suffix_chk)
        suffix_layout.addWidget(self.suffix_edit)
        suffix_layout.addStretch()

        form_io.addRow('입력 폴더:', self.src_selector)
        form_io.addRow('출력 폴더:', self.dst_selector)
        form_io.addRow('',          self.out_to_in_chk)
        form_io.addRow('',          conflict_widget)
        form_io.addRow('',          suffix_widget)
        left_layout.addWidget(grp_io)

        # 입력 파일 필터
        grp_filter = QGroupBox('입력 파일 필터')
        filter_layout = QVBoxLayout(grp_filter)
        self.all_ext_chk = QCheckBox('전체 (All)')
        self.all_ext_chk.setChecked(True)
        filter_layout.addWidget(self.all_ext_chk)

        ext_row = QHBoxLayout()
        self._ext_chks: dict[str, QCheckBox] = {}
        for ext in self._INPUT_EXTS:
            chk = QCheckBox(ext)
            chk.setChecked(True)
            self._ext_chks[ext] = chk
            ext_row.addWidget(chk)
        ext_row.addStretch()
        filter_layout.addLayout(ext_row)
        left_layout.addWidget(grp_filter)

        # 출력 포맷
        grp_fmt = QGroupBox('출력 포맷')
        fmt_layout = QHBoxLayout(grp_fmt)
        self._fmt_rbs: dict[str, QRadioButton] = {}
        self._fmt_grp = QButtonGroup(self)
        for i, (label, value) in enumerate(self._OUT_FMTS):
            rb = QRadioButton(label)
            self._fmt_rbs[value] = rb
            self._fmt_grp.addButton(rb, i)
            fmt_layout.addWidget(rb)
        self._fmt_rbs['png'].setChecked(True)
        fmt_layout.addStretch()
        left_layout.addWidget(grp_fmt)

        # 변환 옵션
        grp_conv = QGroupBox('변환 옵션')
        conv_form = QFormLayout(grp_conv)
        conv_form.setSpacing(SPACING_SMALL)

        quality_row = QHBoxLayout()
        self.quality_chk  = QCheckBox('품질 설정:')
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(1, 100)
        self.quality_spin.setValue(95)
        self.quality_spin.setMaximumWidth(60)
        quality_row.addWidget(self.quality_chk)
        quality_row.addWidget(self.quality_spin)
        quality_row.addStretch()

        resize_row = QHBoxLayout()
        self.resize_chk  = QCheckBox('리사이즈:')
        self.resize_spin = QDoubleSpinBox()
        self.resize_spin.setRange(0.1, 4.0)
        self.resize_spin.setSingleStep(0.1)
        self.resize_spin.setValue(1.0)
        self.resize_spin.setMaximumWidth(70)
        resize_row.addWidget(self.resize_chk)
        resize_row.addWidget(self.resize_spin)
        resize_row.addWidget(QLabel('배율'))
        resize_row.addStretch()

        self.optimize_chk = QCheckBox('최적화 (PNG/WEBP)')

        conv_form.addRow('', quality_row)
        conv_form.addRow('', resize_row)
        conv_form.addRow('', self.optimize_chk)
        left_layout.addWidget(grp_conv)

        # 메타데이터 설정
        # 원본 사양: 보존 여부를 켜고 끄는 단일 체크박스만 존재한다.
        # (EXIF/PNG텍스트/스테가노그래피를 개별로 끄는 기능은 엔진(prepare_save_options)에
        #  애초에 구현되어 있지 않으므로, 세부 체크박스를 추가하면 동작하지 않는 옵션이 되어
        #  사용자에게 잘못된 기대를 줄 수 있다. 원본 그대로 단일 체크박스를 유지한다.)
        grp_meta = QGroupBox('메타데이터 설정')
        meta_layout = QVBoxLayout(grp_meta)
        self.meta_chk = QCheckBox('메타데이터 보존 (EXIF / PNG 텍스트 / 스테가노그래피 정보 자동 보존)')
        meta_layout.addWidget(self.meta_chk)
        left_layout.addWidget(grp_meta)

        # 삭제 옵션
        grp_del = QGroupBox('원본 삭제')
        del_layout = QHBoxLayout(grp_del)
        self.del_orig_chk    = QCheckBox('변환 후 원본 삭제')
        self.del_confirm_chk = QCheckBox('삭제 전 확인 팝업')
        self.del_confirm_chk.setChecked(True)
        del_layout.addWidget(self.del_orig_chk)
        del_layout.addWidget(self.del_confirm_chk)
        del_layout.addStretch()
        left_layout.addWidget(grp_del)

        left_layout.addStretch()

        # ── 오른쪽 패널 ───────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(SPACING_DEFAULT)

        # 진행 상황
        grp_prog = QGroupBox('진행 상황')
        prog_layout = QVBoxLayout(grp_prog)
        self.progress_bar   = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.status_lbl     = QLabel('대기 중')
        self.status_lbl.setProperty('secondary', True)
        prog_layout.addWidget(self.progress_bar)
        prog_layout.addWidget(self.status_lbl)
        right_layout.addWidget(grp_prog)

        # 컨트롤 버튼
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(SPACING_SMALL)
        self.start_btn = QPushButton('변환 시작')
        self.start_btn.setProperty('accent', True)
        self.pause_btn = QPushButton('일시정지')
        self.pause_btn.setEnabled(False)
        self.stop_btn  = QPushButton('중지')
        self.stop_btn.setEnabled(False)
        self.undo_btn  = QPushButton('마지막 작업 취소')
        self.undo_btn.setEnabled(False)
        for w in (self.start_btn, self.pause_btn, self.stop_btn, self.undo_btn):
            btn_bar.addWidget(w)
        # 로그
        self.log_widget = LogWidget(show_controls=False)
        btn_bar.addStretch()
        btn_bar.addWidget(self.log_widget.clear_btn)
        btn_bar.addWidget(self.log_widget.copy_btn)
        right_layout.addLayout(btn_bar)
        right_layout.addWidget(self.log_widget, stretch=1)

        # 로거 GUI 핸들러 연결
        import logging
        class _QtHandler(logging.Handler):
            def __init__(self, widget: LogWidget) -> None:
                super().__init__()
                self._w = widget
            def emit(self, record: logging.LogRecord) -> None:
                self._w.line_appended.emit(self.format(record))

        handler = _QtHandler(self.log_widget)
        handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', '%H:%M:%S'))
        logger.add_gui_handler(handler)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # 시그널 연결
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.out_to_in_chk.toggled.connect(self._on_out_to_in_toggle)
        self.all_ext_chk.toggled.connect(self._on_all_ext_toggle)
        for chk in self._ext_chks.values():
            # 전체 선택이 개별 체크박스를 True로 만드는 과정에서는
            # 전체 선택을 다시 끄지 않는다. 사용자가 개별 항목을
            # 직접 끄거나 켜는 경우에만 전체 선택 상태를 해제한다.
            chk.toggled.connect(
                lambda checked: self.all_ext_chk.setChecked(False)
                if not checked else None
            )
        self.suffix_chk.toggled.connect(self.suffix_edit.setEnabled)
        self.start_btn.clicked.connect(self._on_start)
        self.pause_btn.clicked.connect(self._on_pause)
        self.stop_btn.clicked.connect(self._on_stop)
        self.undo_btn.clicked.connect(self._on_undo)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def set_num_cores(self, n: int) -> None:
        self._num_cores = n

    def get_settings(self) -> dict:
        return {}   # 변환기 설정은 converter_config.json으로 별도 관리

    def load_settings(self, s: dict) -> None:
        pass

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _on_out_to_in_toggle(self, checked: bool) -> None:
        self.dst_selector.setEnabled(not checked)
        self.conflict_skip_rb.setEnabled(checked)
        self.conflict_overwrite_rb.setEnabled(checked)
        self.conflict_rename_rb.setEnabled(checked)

    def _on_all_ext_toggle(self, checked: bool) -> None:
        for chk in self._ext_chks.values():
            chk.setChecked(checked)

    def _selected_format(self) -> str:
        for val, rb in self._fmt_rbs.items():
            if rb.isChecked():
                return val
        return 'png'

    def _conflict_mode(self) -> str:
        if self.conflict_skip_rb.isChecked():      return 'skip'
        if self.conflict_overwrite_rb.isChecked(): return 'overwrite'
        return 'rename'

    def _build_settings(self) -> dict:
        s = load_converter_settings()
        src = self.src_selector.path()
        fmt = self._selected_format()

        use_suffix = self.suffix_chk.isChecked()
        suffix     = self.suffix_edit.text() if use_suffix else ''
        pattern    = f'{{original_name}}{suffix}'

        s['input_settings']['source_folder'] = src
        s['input_settings']['supported_formats'] = [
            ext.lower() for ext, chk in self._ext_chks.items() if chk.isChecked()
        ]
        s['output_settings'].update({
            'target_folder':      self.dst_selector.path(),
            'target_format':      fmt,
            'naming_pattern':     pattern,
            'use_suffix':         use_suffix,
            'suffix_text':        suffix,
            'output_to_input':    self.out_to_in_chk.isChecked(),
            'input_conflict_mode':self._conflict_mode(),
        })
        s['conversion_settings'].update({
            'quality_enabled': self.quality_chk.isChecked(),
            'quality_value':   self.quality_spin.value(),
            'resize_enabled':  self.resize_chk.isChecked(),
            'resize_scale':    self.resize_spin.value(),
            'optimize':        self.optimize_chk.isChecked(),
        })
        s['metadata_settings'].update({
            'preserve_enabled': self.meta_chk.isChecked(),
            # preservation_methods 세부값은 prepare_save_options() 엔진에서 실제로 참조하지
            # 않는다(원본 사양). 보존 여부는 preserve_enabled 단일 값으로만 제어되며,
            # 이 딕셔너리는 설정 파일 스키마 호환을 위해 항상 전체 True로 둔다.
            'preservation_methods': {
                'exif':          True,
                'png_text':      True,
                'steganography': True,
            },
        })
        s['delete_settings'].update({
            'delete_original':      self.del_orig_chk.isChecked(),
            'delete_confirm_popup': self.del_confirm_chk.isChecked(),
        })
        s['processing_settings'].update({
            'multiprocessing_enabled': self._num_cores > 1,
            'max_workers':             self._num_cores,
        })
        return s

    def _load_settings_to_ui(self) -> None:
        s = self._settings
        self.src_selector.set_path(s.get('input_settings', {}).get('source_folder', ''))
        self.dst_selector.set_path(s.get('output_settings', {}).get('target_folder', ''))
        fmt = s.get('output_settings', {}).get('target_format', 'png')
        if fmt in self._fmt_rbs:
            self._fmt_rbs[fmt].setChecked(True)
        use_suffix = s.get('output_settings', {}).get('use_suffix', True)
        self.suffix_chk.setChecked(use_suffix)
        self.suffix_edit.setText(s.get('output_settings', {}).get('suffix_text', '_converted'))
        self.suffix_edit.setEnabled(use_suffix)
        conv = s.get('conversion_settings', {})
        self.quality_chk.setChecked(conv.get('quality_enabled', True))
        self.quality_spin.setValue(conv.get('quality_value', 95))
        self.resize_chk.setChecked(conv.get('resize_enabled', False))
        self.resize_spin.setValue(conv.get('resize_scale', 1.0))
        self.optimize_chk.setChecked(conv.get('optimize', True))
        meta = s.get('metadata_settings', {})
        self.meta_chk.setChecked(meta.get('preserve_enabled', True))
        d = s.get('delete_settings', {})
        self.del_orig_chk.setChecked(d.get('delete_original', False))
        self.del_confirm_chk.setChecked(d.get('delete_confirm_popup', True))

    def _set_busy(self, busy: bool) -> None:
        self.start_btn.setEnabled(not busy)
        self.pause_btn.setEnabled(busy)
        self.stop_btn.setEnabled(busy)
        self.undo_btn.setEnabled(False)

    @staticmethod
    def _fmt_size(size_bytes: int) -> str:
        return format_file_size(size_bytes)

    # ------------------------------------------------------------------
    # 슬롯
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        settings = self._build_settings()
        src = settings['input_settings']['source_folder']

        if not src:
            QMessageBox.warning(self, '경고', '입력 폴더를 선택하세요.')
            return
        if not settings['output_settings'].get('output_to_input') and \
           not settings['output_settings'].get('target_folder'):
            QMessageBox.warning(self, '경고', '출력 폴더를 선택하거나 "입력 폴더에 출력"을 체크하세요.')
            return

        fmts      = settings['input_settings']['supported_formats']
        file_list = scan_directory(src, fmts, include_subdirs=True)
        if not file_list:
            QMessageBox.information(self, '정보', '변환할 파일을 찾을 수 없습니다.')
            return

        save_converter_settings(settings)

        self._last_results = []
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(len(file_list))
        self._set_busy(True)

        self._worker = _ConvertWorker(file_list, settings)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    @Slot(int, int, str)
    def _on_progress(self, done: int, total: int, path: str) -> None:
        if not self._rate_limiter.is_allowed():
            return
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(done)
        self.status_lbl.setText(f'처리 중 ({done}/{total}): {os.path.basename(path)}')

    @Slot(str)
    def _on_worker_error(self, traceback_text: str) -> None:
        """변환 워커에서 예상치 못한 예외가 발생했을 때의 안전망."""
        self._set_busy(False)
        self.status_lbl.setText('오류 발생')
        QMessageBox.critical(
            self, '예상치 못한 오류',
            f'변환 중 오류가 발생했습니다.\n\n{traceback_text[-1500:]}',
        )

    @Slot(dict)
    def _on_finished(self, results: dict) -> None:
        self._set_busy(False)
        self._last_results = results.get('success', [])
        if self._last_results:
            self.undo_btn.setEnabled(True)

        n_ok  = len(results.get('success', []))
        n_err = len(results.get('error', []))
        n_skip= len(results.get('skipped', []))
        self.status_lbl.setText(f'완료 — 성공: {n_ok}, 실패: {n_err}, 건너뜀: {n_skip}')
        self.progress_bar.setValue(self.progress_bar.maximum())

        settings = self._build_settings()
        if settings['delete_settings'].get('delete_original') and results.get('original_paths'):
            orig_paths = results['original_paths']
            orig_size  = sum(os.path.getsize(p) for p in orig_paths if os.path.exists(p))
            conv_size  = sum(os.path.getsize(r['output'])
                             for r in results.get('success', [])
                             if os.path.exists(r.get('output', '')))
            msg = (f'변환 전 원본: {len(orig_paths)}개 / {self._fmt_size(orig_size)}\n'
                   f'변환 완료:   {n_ok}개 / {self._fmt_size(conv_size)}\n\n'
                   f'원본 파일을 삭제하시겠습니까?')

            if settings['delete_settings'].get('delete_confirm_popup', True):
                answer = QMessageBox.question(self, '원본 삭제 확인', msg)
                if answer != QMessageBox.StandardButton.Yes:
                    QMessageBox.information(self, '완료', '이미지 변환이 완료되었습니다.\n원본 파일은 유지됩니다.')
                    return
            self._delete_originals(orig_paths)
        else:
            QMessageBox.information(self, '완료', f'이미지 변환이 완료되었습니다.\n성공: {n_ok}, 실패: {n_err}, 건너뜀: {n_skip}')

    def _delete_originals(self, paths: list) -> None:
        ok = fail = 0
        for p in paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
                    ok += 1
            except OSError as e:
                logger.error(f'원본 삭제 실패: {p} — {e}', module='converter_tab')
                fail += 1
        msg = f'원본 파일 {ok}개를 삭제했습니다.'
        if fail:
            msg += f'\n삭제 실패: {fail}개'
        QMessageBox.information(self, '완료', f'이미지 변환이 완료되었습니다.\n{msg}')

    def _on_pause(self) -> None:
        if self._worker is None:
            return
        if self.pause_btn.text() == '일시정지':
            self._worker.request_pause()
            self.pause_btn.setText('재개')
            logger.info('변환 일시정지')
        else:
            self._worker.request_resume()
            self.pause_btn.setText('일시정지')
            logger.info('변환 재개')

    def _on_stop(self) -> None:
        if self._worker:
            self._worker.request_stop()
            self.stop_btn.setEnabled(False)
            logger.info('변환 중지 요청')

    def _on_undo(self) -> None:
        if not self._last_results:
            QMessageBox.information(self, '정보', '취소할 작업이 없습니다.')
            return
        answer = QMessageBox.question(
            self, '마지막 작업 취소',
            f'{len(self._last_results)}개의 변환된 파일을 삭제하시겠습니까?',
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted = 0
        for r in self._last_results:
            try:
                out = r.get('output', '')
                if out and os.path.exists(out):
                    os.remove(out)
                    deleted += 1
            except OSError as e:
                logger.error(f'파일 삭제 실패: {e}', module='converter_tab')
        self._last_results = []
        self.undo_btn.setEnabled(False)
        QMessageBox.information(self, '완료', f'{deleted}개의 파일을 삭제했습니다.')
