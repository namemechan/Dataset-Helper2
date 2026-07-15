"""
ui/styles/theme.py

ui/themes.py 의 팔레트(dict)를 받아 QSS 문자열로 조립해 반환한다.
테마를 전환할 때는 build_qss(new_theme) 를 다시 호출해
app.setStyleSheet(...) 으로 전체 앱에 재적용한다.
"""

from __future__ import annotations

from ui.themes import Theme
from ui.constants import (
    FONT_FAMILY_UI, FONT_FAMILY_MONO,
    FONT_SIZE_BASE, FONT_SIZE_SMALL, FONT_SIZE_MEDIUM,
    RADIUS_DEFAULT, RADIUS_SMALL,
    BTN_MIN_WIDTH, BTN_MIN_HEIGHT, INPUT_MIN_HEIGHT,
)


def build_qss(theme: Theme) -> str:
    """전체 앱에 적용할 QSS 문자열을 반환한다.

    Args:
        theme: ui.themes.LIGHT_THEME 또는 ui.themes.DARK_THEME (또는 동일 스키마의 dict)
    """
    bg_dark        = theme['bg_dark']
    bg_panel       = theme['bg_panel']
    bg_input       = theme['bg_input']
    accent         = theme['accent']
    accent_hover   = theme['accent_hover']
    accent_press   = theme['accent_press']
    success        = theme['success']
    warning        = theme['warning']
    error          = theme['error']
    text_primary   = theme['text_primary']
    text_secondary = theme['text_secondary']
    border         = theme['border']
    separator      = theme['separator']
    log_bg         = theme['log_bg']
    log_text       = theme['log_text']
    hover_row      = theme['hover_row']

    # 강조 버튼 글자색: 라이트 테마의 테라코타는 어두운 편이라 흰 글자가 맞고,
    # 다크 테마의 인디고도 흰 글자가 맞으므로 공통으로 흰색을 사용한다.
    accent_text = '#ffffff'

    return f"""
/* ─────────────────────────────────────────────
   전역 기본값
───────────────────────────────────────────── */
* {{
    font-family: "{FONT_FAMILY_UI}", sans-serif;
    font-size: {FONT_SIZE_BASE}pt;
    color: {text_primary};
    outline: none;
}}

QMainWindow, QDialog {{
    background-color: {bg_dark};
}}

QWidget {{
    background-color: {bg_dark};
}}

/* ─────────────────────────────────────────────
   탭 위젯
───────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {border};
    border-radius: {RADIUS_DEFAULT};
    background-color: {bg_panel};
    top: -1px;
}}

QTabBar::tab {{
    background-color: {bg_dark};
    color: {text_secondary};
    padding: 5px 16px;
    border: 1px solid {border};
    border-bottom: none;
    border-top-left-radius: {RADIUS_SMALL};
    border-top-right-radius: {RADIUS_SMALL};
    min-width: 70px;
    font-size: {FONT_SIZE_SMALL}pt;
}}

QTabBar::tab:selected {{
    background-color: {bg_panel};
    color: {text_primary};
    border-bottom: 2px solid {accent};
    font-size: {FONT_SIZE_BASE}pt;
}}

QTabBar::tab:hover:!selected {{
    background-color: {bg_input};
    color: {text_primary};
}}

/* ─────────────────────────────────────────────
   버튼
───────────────────────────────────────────── */
QPushButton {{
    background-color: {bg_input};
    color: {text_primary};
    border: 1px solid {border};
    border-radius: {RADIUS_SMALL};
    padding: 2px 10px;
    min-width: {BTN_MIN_WIDTH}px;
    min-height: {BTN_MIN_HEIGHT}px;
    font-size: {FONT_SIZE_SMALL}pt;
}}

QPushButton:hover {{
    background-color: {accent};
    border-color: {accent};
    color: {accent_text};
}}

QPushButton:pressed {{
    background-color: {accent_press};
    border-color: {accent_press};
}}

QPushButton:disabled {{
    background-color: {bg_dark};
    color: {text_secondary};
    border-color: {separator};
}}

/* 강조 버튼 */
QPushButton[accent="true"] {{
    background-color: {accent};
    border-color: {accent};
    color: {accent_text};
    font-size: {FONT_SIZE_BASE}pt;
    font-weight: bold;
}}

QPushButton[accent="true"]:hover {{
    background-color: {accent_hover};
    border-color: {accent_hover};
}}

QPushButton[accent="true"]:pressed {{
    background-color: {accent_press};
}}

/* 위험(삭제) 버튼 */
QPushButton[danger="true"] {{
    background-color: transparent;
    border-color: {error};
    color: {error};
}}

QPushButton[danger="true"]:hover {{
    background-color: {error};
    color: #ffffff;
}}

/* ─────────────────────────────────────────────
   입력 위젯
───────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit,
QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {bg_input};
    border: 1px solid {border};
    border-radius: {RADIUS_SMALL};
    padding: 2px 6px;
    min-height: {INPUT_MIN_HEIGHT}px;
    color: {text_primary};
    selection-background-color: {accent};
    selection-color: #ffffff;
    font-size: {FONT_SIZE_SMALL}pt;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {accent};
}}

QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    background-color: {bg_dark};
    color: {text_secondary};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox QAbstractItemView {{
    background-color: {bg_panel};
    border: 1px solid {border};
    selection-background-color: {accent};
    selection-color: #ffffff;
    color: {text_primary};
    font-size: {FONT_SIZE_SMALL}pt;
}}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {bg_panel};
    border: none;
    width: 16px;
}}

/* 로그·모노 출력 */
QPlainTextEdit[mono="true"], QTextEdit[mono="true"] {{
    font-family: "{FONT_FAMILY_MONO}", monospace;
    font-size: {FONT_SIZE_SMALL}pt;
    background-color: {log_bg};
    color: {log_text};
    border: 1px solid {border};
    line-height: 1.4;
}}

/* ─────────────────────────────────────────────
   체크박스 / 라디오버튼
───────────────────────────────────────────── */
QCheckBox, QRadioButton {{
    spacing: 5px;
    color: {text_primary};
    font-size: {FONT_SIZE_SMALL}pt;
}}

QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {border};
    border-radius: 3px;
    background-color: {bg_input};
}}

QCheckBox::indicator:checked {{
    background-color: {accent};
    border-color: {accent};
}}

QRadioButton::indicator {{
    width: 13px;
    height: 13px;
    border: 1px solid {border};
    border-radius: 7px;
    background-color: {bg_input};
}}

QRadioButton::indicator:checked {{
    background-color: {accent};
    border-color: {accent};
}}

/* ─────────────────────────────────────────────
   그룹박스
───────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {border};
    border-radius: {RADIUS_DEFAULT};
    margin-top: 10px;
    padding-top: 6px;
    font-size: {FONT_SIZE_SMALL}pt;
    color: {text_secondary};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 5px;
    left: 10px;
    color: {accent};
    font-size: {FONT_SIZE_SMALL}pt;
    font-weight: bold;
}}

/* ─────────────────────────────────────────────
   스크롤바
───────────────────────────────────────────── */
QScrollBar:vertical {{
    background-color: transparent;
    width: 8px;
}}

QScrollBar::handle:vertical {{
    background-color: {border};
    border-radius: 4px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {accent};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 8px;
}}

QScrollBar::handle:horizontal {{
    background-color: {border};
    border-radius: 4px;
    min-width: 24px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {accent};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}

/* ─────────────────────────────────────────────
   리스트 / 트리 / 테이블
───────────────────────────────────────────── */
QListView, QTreeView, QTableView, QTreeWidget {{
    background-color: {bg_panel};
    border: 1px solid {border};
    border-radius: {RADIUS_SMALL};
    gridline-color: {separator};
    color: {text_primary};
    alternate-background-color: {bg_input};
    font-size: {FONT_SIZE_SMALL}pt;
}}

QListView::item:selected, QTreeView::item:selected,
QTableView::item:selected, QTreeWidget::item:selected {{
    background-color: {accent};
    color: #ffffff;
}}

QListView::item:hover, QTreeView::item:hover,
QTableView::item:hover, QTreeWidget::item:hover {{
    background-color: {hover_row};
}}

QHeaderView::section {{
    background-color: {bg_dark};
    color: {text_secondary};
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
    padding: 3px 6px;
    font-size: {FONT_SIZE_SMALL}pt;
}}

/* ─────────────────────────────────────────────
   레이블
───────────────────────────────────────────── */
QLabel {{
    background-color: transparent;
    color: {text_primary};
    font-size: {FONT_SIZE_SMALL}pt;
}}

QLabel[secondary="true"] {{
    color: {text_secondary};
}}

QLabel[success="true"] {{ color: {success}; }}
QLabel[warning="true"] {{ color: {warning}; }}
QLabel[error="true"]   {{ color: {error};   }}

/* ─────────────────────────────────────────────
   슬라이더
───────────────────────────────────────────── */
QSlider::groove:horizontal {{
    height: 3px;
    background-color: {border};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background-color: {accent};
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 6px;
}}

QSlider::sub-page:horizontal {{
    background-color: {accent};
    border-radius: 2px;
}}

/* ─────────────────────────────────────────────
   프로그레스바
───────────────────────────────────────────── */
QProgressBar {{
    background-color: {bg_input};
    border: 1px solid {border};
    border-radius: {RADIUS_SMALL};
    height: 8px;
    text-align: center;
    color: transparent;
    font-size: {FONT_SIZE_SMALL}pt;
}}

QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {accent}, stop:1 {accent_hover});
    border-radius: {RADIUS_SMALL};
}}

/* ─────────────────────────────────────────────
   구분선
───────────────────────────────────────────── */
QFrame[frameShape="4"],
QFrame[frameShape="5"] {{
    color: {separator};
}}

/* ─────────────────────────────────────────────
   툴팁
───────────────────────────────────────────── */
QToolTip {{
    background-color: {bg_panel};
    color: {text_primary};
    border: 1px solid {accent};
    border-radius: {RADIUS_SMALL};
    padding: 3px 6px;
    font-size: {FONT_SIZE_SMALL}pt;
}}

/* ─────────────────────────────────────────────
   스플리터
───────────────────────────────────────────── */
QSplitter::handle {{
    background-color: {border};
}}

QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle:vertical   {{ height: 2px; }}
"""
