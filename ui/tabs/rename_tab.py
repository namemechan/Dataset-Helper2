"""
ui/tabs/rename_tab.py

이름 변경 탭 — 이미지-텍스트 파일 쌍 일괄 이름 변경 및 실행 취소.
"""

from __future__ import annotations

from PySide6.QtCore    import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QSpinBox,
    QPushButton, QMessageBox, QSizePolicy,
)

from core.rename_processor import RenameProcessor
from ui.widgets.log_widget  import LogWidget
from ui.widgets.worker_base import SafeWorker
from ui.constants import PADDING_DEFAULT, SPACING_DEFAULT, SPACING_SMALL


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _RenameWorker(SafeWorker):
    finished = Signal(int, int, list)   # success, fail, logs

    def __init__(self, folder: str, base: str, start: int, digits: int) -> None:
        super().__init__()
        self._folder = folder
        self._base   = base
        self._start  = start
        self._digits = digits

    def work(self) -> None:
        success, fail, logs = RenameProcessor.rename_file_pairs(
            self._folder, self._base, self._start, self._digits,
        )
        self.finished.emit(success, fail, logs)


class _UndoWorker(SafeWorker):
    finished = Signal(int, int, list)

    def __init__(self, folder: str) -> None:
        super().__init__()
        self._folder = folder

    def work(self) -> None:
        success, fail, logs = RenameProcessor.undo_rename(self._folder)
        self.finished.emit(success, fail, logs)


# ---------------------------------------------------------------------------
# 탭 위젯
# ---------------------------------------------------------------------------

class RenameTab(QWidget):
    """이름 변경 탭."""


    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._folder: str = ''
        self._worker: QThread | None = None
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PADDING_DEFAULT, PADDING_DEFAULT,
                                PADDING_DEFAULT, PADDING_DEFAULT)
        root.setSpacing(SPACING_DEFAULT)

        # ── 설정 그룹 ─────────────────────────────────────────
        settings_group = QGroupBox('설정')
        form = QFormLayout(settings_group)
        form.setSpacing(SPACING_DEFAULT)

        self.base_edit = QLineEdit('image')
        self.base_edit.setPlaceholderText('변경할 기본 이름')

        self.start_spin = QSpinBox()
        self.start_spin.setRange(0, 9_999_999)
        self.start_spin.setValue(1)

        self.digits_spin = QSpinBox()
        self.digits_spin.setRange(1, 10)
        self.digits_spin.setValue(6)

        form.addRow('기본 이름:', self.base_edit)
        form.addRow('시작 번호:', self.start_spin)
        form.addRow('숫자 자릿수:', self.digits_spin)

        # ── 버튼 바 ───────────────────────────────────────────
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(SPACING_SMALL)

        self.preview_btn = QPushButton('미리보기')
        self.execute_btn = QPushButton('이름 변경 실행')
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

        root.addWidget(settings_group)
        root.addLayout(btn_bar)
        root.addWidget(self.log_widget)

    def _connect_signals(self) -> None:
        self.preview_btn.clicked.connect(self._on_preview)
        self.execute_btn.clicked.connect(self._on_execute)
        self.undo_btn.clicked.connect(self._on_undo)

    # ------------------------------------------------------------------
    # 공개 API (MainWindow 에서 호출)
    # ------------------------------------------------------------------

    def set_folder(self, folder: str) -> None:
        self._folder = folder

    def get_settings(self) -> dict:
        return {
            'rename_base':   self.base_edit.text(),
            'rename_start':  self.start_spin.value(),
            'rename_digits': self.digits_spin.value(),
        }

    def load_settings(self, s: dict) -> None:
        if 'rename_base'   in s: self.base_edit.setText(s['rename_base'])
        if 'rename_start'  in s: self.start_spin.setValue(int(s['rename_start']))
        if 'rename_digits' in s: self.digits_spin.setValue(int(s['rename_digits']))

    # ------------------------------------------------------------------
    # 슬롯
    # ------------------------------------------------------------------

    def _check_folder(self) -> bool:
        if not self._folder:
            QMessageBox.warning(self, '경고', '먼저 작업 폴더를 선택하세요.')
            return False
        return True

    def _check_input(self) -> bool:
        base_name = self.base_edit.text().strip()
        if not base_name:
            QMessageBox.warning(self, '경고', '기본 이름을 입력하세요.')
            return False
        if any(ch in base_name for ch in '<>:"/\\|?*') or base_name.endswith(('.', ' ')):
            QMessageBox.warning(self, '경고', '기본 이름에 사용할 수 없는 문자가 포함되어 있습니다.')
            return False
        return True

    def _set_busy(self, busy: bool) -> None:
        self.preview_btn.setEnabled(not busy)
        self.execute_btn.setEnabled(not busy)
        self.undo_btn.setEnabled(not busy)

    def _on_preview(self) -> None:
        if not self._check_folder() or not self._check_input():
            return
        lines = RenameProcessor.preview_rename(
            self._folder,
            self.base_edit.text().strip(),
            self.start_spin.value(),
            self.digits_spin.value(),
        )
        self.log_widget.clear()
        self.log_widget.append_lines(lines)

    def _on_execute(self) -> None:
        if not self._check_folder() or not self._check_input():
            return
        answer = QMessageBox.question(
            self, '확인',
            '파일 이름을 변경하시겠습니까?\n(실행 취소 버튼으로 되돌릴 수 있습니다)',
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        self._worker = _RenameWorker(
            self._folder,
            self.base_edit.text().strip(),
            self.start_spin.value(),
            self.digits_spin.value(),
        )
        self._worker.finished.connect(self._on_rename_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    @Slot(int, int, list)
    def _on_rename_done(self, success: int, fail: int, logs: list) -> None:
        self._set_busy(False)
        self.log_widget.clear()
        self.log_widget.append_line(f'성공: {success}개, 실패: {fail}개')
        self.log_widget.append_lines(logs)
        QMessageBox.information(self, '완료',
                                f'이름 변경 완료\n성공: {success}개, 실패: {fail}개')

    def _on_undo(self) -> None:
        if not self._check_folder():
            return
        answer = QMessageBox.question(self, '확인', '마지막 이름 변경을 취소하시겠습니까?')
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        self._worker = _UndoWorker(self._folder)
        self._worker.finished.connect(self._on_undo_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    @Slot(int, int, list)
    def _on_undo_done(self, success: int, fail: int, logs: list) -> None:
        self._set_busy(False)
        self.log_widget.clear()
        self.log_widget.append_line(f'복구 성공: {success}개, 실패: {fail}개')
        self.log_widget.append_lines(logs)
        if success > 0:
            QMessageBox.information(self, '완료',
                                    f'실행 취소 완료\n복구: {success}개, 실패: {fail}개')
        else:
            QMessageBox.information(self, '알림', '실행 취소할 내역이 없습니다.')

    @Slot(str)
    def _on_worker_error(self, traceback_text: str) -> None:
        """워커 스레드에서 예상치 못한 예외가 발생했을 때의 안전망."""
        self._set_busy(False)
        QMessageBox.critical(
            self, '예상치 못한 오류',
            f'작업 중 오류가 발생했습니다.\n\n{traceback_text[-1500:]}',
        )
