"""
ui/widgets/log_widget.py

로그 출력용 공용 위젯.

LogWidget
  - QPlainTextEdit 기반 읽기 전용 로그 뷰어
  - 색상 접두사(성공/실패/경고) 자동 강조
  - Worker 스레드에서 안전하게 호출할 수 있는 Signal 기반 append_line()
  - 최대 라인 수 제한 (메모리 안전)
  - 클리어 / 전체 복사 버튼 내장
"""

from __future__ import annotations

from PySide6.QtCore    import Qt, Signal, Slot
from PySide6.QtGui     import QTextCharFormat, QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QPlainTextEdit, QSizePolicy,
)

from ui.constants import (
    FONT_FAMILY_MONO, FONT_SIZE_SMALL,
    SPACING_SMALL, LOG_MIN_HEIGHT, BTN_MIN_HEIGHT,
    current_colors,
)


class LogWidget(QWidget):
    """
    스레드 안전 로그 출력 위젯.
    Worker -> signal -> slot 경로로 UI를 변경하므로
    QThread / QRunnable 어디서든 line_appended.emit() 으로 출력할 수 있다.
    """

    line_appended = Signal(str)
    MAX_LINES: int = 5000

    def __init__(self, parent: QWidget | None = None,
                 show_controls: bool = True) -> None:
        super().__init__(parent)
        self._show_controls = show_controls
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(SPACING_SMALL)

        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(0, 0, 0, 0)
        btn_bar.setSpacing(SPACING_SMALL)

        self.clear_btn = QPushButton("지우기")
        self.clear_btn.setFixedHeight(BTN_MIN_HEIGHT)
        self.clear_btn.setMinimumWidth(50)

        self.copy_btn = QPushButton("전체 복사")
        self.copy_btn.setFixedHeight(BTN_MIN_HEIGHT)
        self.copy_btn.setMinimumWidth(60)

        if self._show_controls:
            btn_bar.addWidget(self.clear_btn)
            btn_bar.addWidget(self.copy_btn)
            btn_bar.addStretch()

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setProperty('mono', True)
        self.log_edit.setMinimumHeight(LOG_MIN_HEIGHT)
        self.log_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.log_edit.setFont(QFont(FONT_FAMILY_MONO, FONT_SIZE_SMALL))
        self.log_edit.setMaximumBlockCount(self.MAX_LINES)

        self.control_layout = btn_bar
        if self._show_controls:
            root_layout.addLayout(btn_bar)
        root_layout.addWidget(self.log_edit)

    def _connect_signals(self) -> None:
        self.line_appended.connect(self._on_line_appended)
        self.clear_btn.clicked.connect(self.clear)
        self.copy_btn.clicked.connect(self._copy_all)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def append_line(self, text: str) -> None:
        """메인 스레드에서 직접 호출하거나,
        Worker에서 line_appended.emit(text) 로 호출한다."""
        self.line_appended.emit(text)

    def append_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.line_appended.emit(line)

    def clear(self) -> None:
        self.log_edit.clear()

    # ------------------------------------------------------------------
    # 슬롯
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_line_appended(self, text: str) -> None:
        cursor = self.log_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._line_color(text)))
        cursor.setCharFormat(fmt)

        if self.log_edit.document().blockCount() > 1 or self.log_edit.toPlainText():
            cursor.insertText('\n')
        cursor.insertText(text)

        self.log_edit.setTextCursor(cursor)
        self.log_edit.ensureCursorVisible()

    def _copy_all(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.log_edit.toPlainText())

    @staticmethod
    def _line_color(text: str) -> str:
        colors = current_colors()
        s = text.lstrip()
        if s.startswith(('[성공]', '변경 완료', '복구 완료', '변환 완료', '[복사]', '[이동]')):
            return colors['success']
        if s.startswith(('[실패]', '[오류]', 'Error', '오류')):
            return colors['error']
        if s.startswith(('[경고]', '[건너뜀]', '경고')):
            return colors['warning']
        return colors['text_primary']
