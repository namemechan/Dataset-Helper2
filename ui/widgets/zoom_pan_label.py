"""
ui/widgets/zoom_pan_label.py

마우스 위치를 중심으로 확대/축소되고, 좌클릭 드래그로 이동하는
캔버스 레이블. QScrollArea 안에 넣어 큰 이미지를 자유롭게 탐색할 때 쓴다.

이 위젯은 xy_plot_tab.py의 미리보기와 search_filter_tab.py의 이미지 뷰어가
각각 거의 동일한 코드를 따로 구현하고 있던 것을 하나로 합친 것이다.
새로 줌/팬 캔버스가 필요한 곳에서는 항상 이 위젯을 재사용한다.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore    import Qt, QPoint
from PySide6.QtGui     import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QLabel, QScrollArea, QWidget


class ZoomPanLabel(QLabel):
    """
    QScrollArea.setWidget()에 들어가는 캔버스 레이블.

    줌 배율과 스크롤 보정은 이 위젯이 아니라 부모(보통 QDialog)가
    소유한 QScrollArea를 통해 이뤄지므로, 생성 시 그 QScrollArea를
    scroll_area 인자로 전달받는다. 부모는 다음 두 메서드를 구현해야 한다.

        zoom_by(factor: float, anchor_widget_pos: QPoint | None) -> None
        fit_to_window() -> None  (더블클릭 시 호출)
    """

    def __init__(
        self,
        scroll_area: QScrollArea,
        on_zoom,
        on_fit,
        zoom_step: float = 1.15,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scroll  = scroll_area
        self._on_zoom = on_zoom    # Callable[[float, Optional[QPoint]], None]
        self._on_fit  = on_fit     # Callable[[], None]
        self._zoom_step = zoom_step

        self._dragging     = False
        self._drag_start   = QPoint()
        self._scroll_start = QPoint()
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # ------------------------------------------------------------------
    # 드래그 팬
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging   = True
            self._drag_start = event.globalPosition().toPoint()
            sb_h = self._scroll.horizontalScrollBar()
            sb_v = self._scroll.verticalScrollBar()
            self._scroll_start = QPoint(sb_h.value(), sb_v.value())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            delta = event.globalPosition().toPoint() - self._drag_start
            sb_h = self._scroll.horizontalScrollBar()
            sb_v = self._scroll.verticalScrollBar()
            sb_h.setValue(self._scroll_start.x() - delta.x())
            sb_v.setValue(self._scroll_start.y() - delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self._on_fit()
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # 마우스 위치 중심 줌
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = self._zoom_step if event.angleDelta().y() > 0 else 1 / self._zoom_step
        self._on_zoom(factor, event.position().toPoint())
