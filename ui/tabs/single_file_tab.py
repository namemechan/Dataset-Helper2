"""
ui/tabs/single_file_tab.py

단일 파일 찾기 탭 — 짝 없는 이미지/텍스트 파일 탐색, 삭제, 이동.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore    import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QCheckBox, QMessageBox, QFileDialog,
)

from core.file_manager import FileManager
from ui.widgets.log_widget  import LogWidget
from ui.widgets.worker_base import SafeWorker
from ui.constants import PADDING_DEFAULT, SPACING_DEFAULT, SPACING_SMALL


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _FindWorker(SafeWorker):
    finished = Signal(list, str)   # [Path, ...], mode

    def __init__(self, folder: str, mode: str, recursive: bool) -> None:
        super().__init__()
        self._folder    = folder
        self._mode      = mode       # 'image' | 'text'
        self._recursive = recursive

    def work(self) -> None:
        fm = FileManager(self._folder)
        if self._mode == 'image':
            files = fm.find_single_images(recursive=self._recursive)
        else:
            files = fm.find_single_texts(recursive=self._recursive)
        self.finished.emit(files, self._mode)


# ---------------------------------------------------------------------------
# 탭 위젯
# ---------------------------------------------------------------------------

class SingleFileTab(QWidget):
    """단일 파일 찾기 탭."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._folder: str        = ''
        self._found:  list[Path] = []
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

        # ── 상단 버튼 바 ──────────────────────────────────────
        top_bar = QHBoxLayout()
        top_bar.setSpacing(SPACING_SMALL)

        self.find_image_btn = QPushButton('단일 이미지 찾기')
        self.find_text_btn  = QPushButton('단일 텍스트 찾기')
        self.recursive_chk  = QCheckBox('하위 폴더 포함 검색')

        top_bar.addWidget(self.find_image_btn)
        top_bar.addWidget(self.find_text_btn)
        top_bar.addWidget(self.recursive_chk)

        # ── 로그 ──────────────────────────────────────────────
        self.log_widget = LogWidget(show_controls=False)

        # ── 하단 액션 버튼 ────────────────────────────────────
        self.delete_btn = QPushButton('삭제')
        self.delete_btn.setProperty('danger', True)
        self.move_btn   = QPushButton('이동')
        self.delete_btn.setEnabled(False)
        self.move_btn.setEnabled(False)

        top_bar.addWidget(self.delete_btn)
        top_bar.addWidget(self.move_btn)
        top_bar.addStretch()
        top_bar.addWidget(self.log_widget.clear_btn)
        top_bar.addWidget(self.log_widget.copy_btn)

        root.addLayout(top_bar)
        root.addWidget(self.log_widget)

    def _connect_signals(self) -> None:
        self.find_image_btn.clicked.connect(lambda: self._on_find('image'))
        self.find_text_btn.clicked.connect(lambda: self._on_find('text'))
        self.delete_btn.clicked.connect(self._on_delete)
        self.move_btn.clicked.connect(self._on_move)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def set_folder(self, folder: str) -> None:
        self._folder = folder

    def get_settings(self) -> dict:
        return {'find_subdirs': self.recursive_chk.isChecked()}

    def load_settings(self, s: dict) -> None:
        if 'find_subdirs' in s:
            self.recursive_chk.setChecked(bool(s['find_subdirs']))

    # ------------------------------------------------------------------
    # 슬롯
    # ------------------------------------------------------------------

    def _check_folder(self) -> bool:
        if not self._folder:
            QMessageBox.warning(self, '경고', '먼저 작업 폴더를 선택하세요.')
            return False
        return True

    def _set_busy(self, busy: bool) -> None:
        self.find_image_btn.setEnabled(not busy)
        self.find_text_btn.setEnabled(not busy)

    def _on_find(self, mode: str) -> None:
        if not self._check_folder():
            return
        self._found = []
        self.delete_btn.setEnabled(False)
        self.move_btn.setEnabled(False)
        self._set_busy(True)

        self._worker = _FindWorker(self._folder, mode, self.recursive_chk.isChecked())
        self._worker.finished.connect(self._on_find_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    @Slot(list, str)
    def _on_find_done(self, files: list, mode: str) -> None:
        self._set_busy(False)
        self._found = files
        self.log_widget.clear()

        kind = '이미지' if mode == 'image' else '텍스트'
        if files:
            self.log_widget.append_line(f'총 {len(files)}개의 단일 {kind} 파일을 찾았습니다.')
            self.log_widget.append_line('')
            self.log_widget.append_lines([str(f) for f in files])
            self.delete_btn.setEnabled(True)
            self.move_btn.setEnabled(True)
        else:
            self.log_widget.append_line(f'단일 {kind} 파일이 없습니다.')

    @Slot(str)
    def _on_worker_error(self, traceback_text: str) -> None:
        """워커 스레드에서 예상치 못한 예외가 발생했을 때의 안전망."""
        self._set_busy(False)
        QMessageBox.critical(
            self, '예상치 못한 오류',
            f'작업 중 오류가 발생했습니다.\n\n{traceback_text[-1500:]}',
        )

    def _on_delete(self) -> None:
        if not self._found:
            QMessageBox.warning(self, '경고', '먼저 단일 파일을 찾아주세요.')
            return
        answer = QMessageBox.question(
            self, '확인', f'{len(self._found)}개의 파일을 삭제하시겠습니까?'
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        fm = FileManager(self._folder)
        success, fail = fm.delete_files(self._found)
        self._found = []
        self.delete_btn.setEnabled(False)
        self.move_btn.setEnabled(False)
        self.log_widget.clear()
        self.log_widget.append_line(f'삭제 완료 — 성공: {success}개, 실패: {fail}개')
        QMessageBox.information(self, '완료', f'삭제 완료\n성공: {success}개, 실패: {fail}개')

    def _on_move(self) -> None:
        if not self._found:
            QMessageBox.warning(self, '경고', '먼저 단일 파일을 찾아주세요.')
            return
        dest = QFileDialog.getExistingDirectory(self, '이동할 폴더 선택')
        if not dest:
            return

        fm = FileManager(self._folder)
        success, fail = fm.move_files(self._found, dest)
        self._found = []
        self.delete_btn.setEnabled(False)
        self.move_btn.setEnabled(False)
        self.log_widget.clear()
        self.log_widget.append_line(f'이동 완료 — 성공: {success}개, 실패: {fail}개')
        self.log_widget.append_line(f'대상 폴더: {dest}')
        QMessageBox.information(self, '완료', f'이동 완료\n성공: {success}개, 실패: {fail}개')
