"""
ui/widgets/folder_selector.py

폴더 경로 입력 + 브라우즈 버튼 공용 위젯.

모든 탭에서 동일한 형태로 반복 사용하는 폴더 선택 UI를 한 곳에 정의한다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore    import Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton, QFileDialog,
)

from ui.constants import INPUT_MIN_HEIGHT, BTN_MIN_HEIGHT, SPACING_SMALL


class FolderSelector(QWidget):
    """
    [경로 입력창] [찾아보기] 형태의 공용 폴더 선택 위젯.

    Signals:
        path_changed(str): 경로가 변경될 때 (입력 변경 또는 다이얼로그 선택)
    """

    path_changed = Signal(str)

    def __init__(
        self,
        placeholder: str = '폴더 경로를 입력하거나 선택하세요',
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._placeholder = placeholder
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(self._placeholder)
        self.path_edit.setMinimumHeight(INPUT_MIN_HEIGHT)

        self.browse_btn = QPushButton('찾아보기')
        self.browse_btn.setMinimumHeight(BTN_MIN_HEIGHT)
        self.browse_btn.setFixedWidth(64)

        layout.addWidget(self.path_edit)
        layout.addWidget(self.browse_btn)

    def _connect_signals(self) -> None:
        self.browse_btn.clicked.connect(self._on_browse)
        self.path_edit.textChanged.connect(self.path_changed)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def path(self) -> str:
        """현재 입력된 경로 문자열을 반환한다."""
        return self.path_edit.text().strip()

    def set_path(self, path: str) -> None:
        """경로를 프로그래밍 방식으로 설정한다."""
        self.path_edit.setText(path)

    def is_valid(self) -> bool:
        """입력된 경로가 실제 존재하는 디렉터리인지 확인한다."""
        return Path(self.path()).is_dir()

    # ------------------------------------------------------------------
    # 슬롯
    # ------------------------------------------------------------------

    def _on_browse(self) -> None:
        start = self.path() if self.is_valid() else ''
        chosen = QFileDialog.getExistingDirectory(
            self,
            '폴더 선택',
            start,
        )
        if chosen:
            self.set_path(chosen)
