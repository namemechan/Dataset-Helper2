"""
ui/widgets/image_viewer.py

QImage 한 장을 확대/축소(마우스 위치 중심)/드래그 팬/창맞춤으로 살펴보는
범용 뷰어. xy_plot_tab의 미리보기 팝업과 search_filter_tab의 독립 이미지
뷰어가 각각 따로 구현하던 중복 로직을 하나로 합친 것이다.

PIL 이미지를 보여줘야 하면 pil_to_qimage()로 변환해 QImage를 만든 뒤
ImageViewerDialog(qimage=...)에 넘긴다. 파일 경로만 있으면
ImageViewerDialog.from_path(path)로 바로 연다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image

from PySide6.QtCore    import Qt, QPoint
from PySide6.QtGui     import QPixmap, QImage, QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea,
)

from ui.widgets.zoom_pan_label import ZoomPanLabel
from ui.constants import (
    PADDING_SMALL, SPACING_SMALL, current_colors,
    register_theme_listener, unregister_theme_listener,
)


def pil_to_qimage(img: Image.Image) -> QImage:
    """PIL Image를 QImage로 변환한다 (RGB 강제 변환 후 버퍼를 복사해 수명을 분리)."""
    rgb = img.convert('RGB')
    data = rgb.tobytes('raw', 'RGB')
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return qimg.copy()


def apply_exif_orientation(img: Image.Image) -> Image.Image:
    """EXIF Orientation 태그에 따라 이미지를 바르게 회전/반전한다. 실패 시 원본을 그대로 반환."""
    try:
        import piexif
        exif_bytes = img.info.get('exif')
        if not exif_bytes:
            return img
        exif = piexif.load(exif_bytes)
        orientation = exif.get('0th', {}).get(piexif.ImageIFD.Orientation, 1)
        ops = {
            2: lambda i: i.transpose(Image.FLIP_LEFT_RIGHT),
            3: lambda i: i.rotate(180),
            4: lambda i: i.transpose(Image.FLIP_TOP_BOTTOM),
            5: lambda i: i.transpose(Image.FLIP_LEFT_RIGHT).rotate(90, expand=True),
            6: lambda i: i.rotate(-90, expand=True),
            7: lambda i: i.transpose(Image.FLIP_LEFT_RIGHT).rotate(-90, expand=True),
            8: lambda i: i.rotate(90, expand=True),
        }
        if orientation in ops:
            img = ops[orientation](img)
    except Exception:
        pass
    return img


class ImageViewerDialog(QDialog):
    """줌/팬/맞춤을 지원하는 범용 이미지 뷰어 다이얼로그."""

    MIN_ZOOM  = 0.05
    MAX_ZOOM  = 20.0
    ZOOM_STEP = 1.15

    def __init__(
        self,
        qimage: QImage,
        title: str = '이미지 뷰어',
        parent: QWidget | None = None,
        extra_toolbar_widgets: Optional[list[QWidget]] = None,
    ) -> None:
        """
        Args:
            qimage: 표시할 QImage (이미 로드·보정된 상태여야 한다)
            title:  창 제목
            extra_toolbar_widgets: 툴바 오른쪽에 추가로 넣을 위젯들
                                    (예: '미리보기 저장'/'완성본 저장' 버튼)
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 700)
        self.setMinimumSize(400, 300)

        self._qimage = qimage
        self._zoom   = 1.0

        self._build_ui(extra_toolbar_widgets or [])
        self._fit_to_window()

        # hint_lbl/scroll/canvas_lbl은 인라인 setStyleSheet()으로 색을 직접
        # 칠하므로 전역 QSS 재적용만으로는 테마 전환이 반영되지 않는다.
        register_theme_listener(self._apply_theme_colors)

    def _apply_theme_colors(self) -> None:
        """테마가 바뀔 때마다 호출되어, 인라인 스타일이 적용된 위젯들을 다시 칠한다."""
        colors = current_colors()
        self.hint_lbl.setStyleSheet(f"color: {colors['text_secondary']};")
        self.scroll.setStyleSheet(f"background-color: {colors['canvas_bg']};")
        self.canvas_lbl.setStyleSheet(f"background-color: {colors['canvas_bg']};")

    def closeEvent(self, event) -> None:
        unregister_theme_listener(self._apply_theme_colors)
        super().closeEvent(event)

    @classmethod
    def from_path(cls, image_path: Path, parent: QWidget | None = None) -> 'ImageViewerDialog':
        """
        파일 경로로부터 바로 뷰어를 생성한다.
        Qt가 직접 못 읽는 포맷이거나 EXIF 회전이 필요한 경우 PIL로 폴백한다.
        """
        qimage = QImage(str(image_path))
        if qimage.isNull():
            pil_img = Image.open(image_path)
            pil_img = apply_exif_orientation(pil_img)
            qimage  = pil_to_qimage(pil_img)
        return cls(qimage, title=f'이미지 뷰어 — {Path(image_path).name}', parent=parent)

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self, extra_widgets: list[QWidget]) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PADDING_SMALL, PADDING_SMALL,
                                PADDING_SMALL, PADDING_SMALL)
        root.setSpacing(SPACING_SMALL)

        bar = QHBoxLayout()
        self.zoom_in_btn  = QPushButton('확대 (+)')
        self.zoom_out_btn = QPushButton('축소 (−)')
        self.fit_btn      = QPushButton('창 크기에 맞춤')
        self.actual_btn   = QPushButton('원본 크기 (1:1)')
        self.zoom_lbl     = QLabel('100%')
        self.zoom_lbl.setFixedWidth(50)
        self.zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_lbl = QLabel('[휠] 확대/축소(마우스 위치 중심)   [드래그] 이동   [더블클릭/R] 맞춤 리셋')
        self.hint_lbl = hint_lbl
        colors = current_colors()
        hint_lbl.setStyleSheet(f"color: {colors['text_secondary']};")

        for w in (self.zoom_in_btn, self.zoom_out_btn, self.fit_btn,
                  self.actual_btn, self.zoom_lbl, hint_lbl):
            bar.addWidget(w)
        bar.addStretch()
        for w in extra_widgets:
            bar.addWidget(w)
        root.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setStyleSheet(f"background-color: {colors['canvas_bg']};")

        self.canvas_lbl = ZoomPanLabel(
            self.scroll,
            on_zoom=self._zoom_by,
            on_fit=self._fit_to_window,
            zoom_step=self.ZOOM_STEP,
        )
        self.canvas_lbl.setStyleSheet(f"background-color: {colors['canvas_bg']};")
        self.scroll.setWidget(self.canvas_lbl)
        root.addWidget(self.scroll, stretch=1)

        self.zoom_in_btn.clicked.connect(lambda: self._zoom_by(self.ZOOM_STEP))
        self.zoom_out_btn.clicked.connect(lambda: self._zoom_by(1 / self.ZOOM_STEP))
        self.fit_btn.clicked.connect(self._fit_to_window)
        self.actual_btn.clicked.connect(self._reset_to_actual)

    # ------------------------------------------------------------------
    # 렌더링 / 줌
    # ------------------------------------------------------------------

    def _render(self) -> None:
        new_w = max(1, int(self._qimage.width()  * self._zoom))
        new_h = max(1, int(self._qimage.height() * self._zoom))
        scaled = self._qimage.scaled(
            new_w, new_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.canvas_lbl.setPixmap(QPixmap.fromImage(scaled))
        self.canvas_lbl.setFixedSize(new_w, new_h)
        self.zoom_lbl.setText(f'{int(self._zoom * 100)}%')

    def _fit_to_window(self) -> None:
        vw = max(1, self.scroll.viewport().width())
        vh = max(1, self.scroll.viewport().height())
        iw, ih = self._qimage.width(), self._qimage.height()
        self._zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, min(vw / iw, vh / ih)))
        self._render()

    def _reset_to_actual(self) -> None:
        self._zoom = 1.0
        self._render()

    def _zoom_by(self, factor: float, anchor_widget_pos: Optional[QPoint] = None) -> None:
        """
        마우스 위치(anchor_widget_pos, 캔버스 좌표)를 중심으로 줌 배율을 factor만큼 곱한다.
        anchor가 없으면(버튼 클릭 등) 뷰포트 중앙을 기준으로 한다.
        """
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._zoom * factor))
        if new_zoom == self._zoom:
            return

        sb_h = self.scroll.horizontalScrollBar()
        sb_v = self.scroll.verticalScrollBar()

        if anchor_widget_pos is not None:
            anchor_x, anchor_y = anchor_widget_pos.x(), anchor_widget_pos.y()
        else:
            vw = self.scroll.viewport().width()
            vh = self.scroll.viewport().height()
            anchor_x = sb_h.value() + vw / 2
            anchor_y = sb_v.value() + vh / 2

        # 줌 전 이미지 좌표계에서의 앵커 위치 (배율 무관 고정점)
        img_x = anchor_x / self._zoom
        img_y = anchor_y / self._zoom

        self._zoom = new_zoom
        self._render()

        # 줌 후에도 같은 이미지 좌표가 같은 화면 위치에 오도록 스크롤 보정
        sb_h.setValue(int(img_x * self._zoom - anchor_x))
        sb_v.setValue(int(img_y * self._zoom - anchor_y))

    # ------------------------------------------------------------------
    # 키보드
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_R:
            self._fit_to_window()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._zoom_by(self.ZOOM_STEP)
        elif key == Qt.Key.Key_Minus:
            self._zoom_by(1 / self.ZOOM_STEP)
        else:
            super().keyPressEvent(event)
