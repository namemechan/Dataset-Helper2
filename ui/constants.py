"""
ui/constants.py

UI 전역 상수 — 폰트, 크기, 간격 (정적 값).
색상은 더 이상 이 파일의 정적 상수가 아니라 ui/themes.py 의 팔레트를 통해
동적으로 제공된다 — 라이트/다크 테마를 실행 중에 전환할 수 있어야 하기
때문이다. 색상이 필요하면 이 파일의 current_colors() 를 호출해 그 시점의
팔레트를 딕셔너리로 받는다.

테마 전환 알림 시스템
----------------------
인라인 setStyleSheet()으로 색을 직접 칠한 위젯(QScrollArea, 격자 셀 등)은
전역 QSS를 다시 적용해도 자기 인라인 스타일을 그대로 유지해버린다.
즉 set_theme()을 호출해도 그런 위젯들은 색이 안 바뀌는 것처럼 보인다.

이 문제를 위젯마다 따로 고치는 대신, 위젯이 자신을 갱신하는 콜백을
register_theme_listener()로 등록해두면 set_theme() 호출 시 자동으로
모든 콜백이 실행되어 갱신된다.

    # 위젯 쪽 (보통 __init__ 끝에서 1회 등록)
    def _apply_theme_colors(self) -> None:
        colors = current_colors()
        self.some_widget.setStyleSheet(f"background-color: {colors['bg_input']};")

    register_theme_listener(self._apply_theme_colors)

    # 위젯이 소멸될 때 (선택, 보통 다이얼로그처럼 일시적인 위젯에서)
    unregister_theme_listener(self._apply_theme_colors)

콜백은 약한 참조가 아니라 강한 참조로 보관되므로, 오래 사는 위젯(탭처럼
프로그램 종료 시까지 유지되는 것)은 해제하지 않아도 무방하지만, 다이얼로그처럼
열고 닫는 위젯은 닫을 때 해제하는 것이 안전하다(닫힌 다이얼로그의 죽은
위젯을 계속 갱신 시도하며 콜백 리스트가 무한히 늘어나는 것을 막기 위함).

규칙:
  - 절대 px 값은 setMinimumSize / setMaximumSize 범위로만 사용한다.
  - 폰트 크기는 pt 단위 (DPI 독립).
  - 색상이 필요하면 색상 리터럴을 직접 쓰지 말고 current_colors()를 호출한다.
  - 인라인 setStyleSheet()으로 동적 색상을 칠하는 위젯은 반드시
    register_theme_listener()로 자신을 등록한다.
"""

from __future__ import annotations

from typing import Callable

from ui.themes import Theme, get_theme, DEFAULT_THEME_NAME

# ---------------------------------------------------------------------------
# 활성 테마 (런타임에 set_theme() 으로 전환된다)
# ---------------------------------------------------------------------------

#: 앱 시작 시 기본으로 적용되는 테마.
#: ui/themes.py 의 DEFAULT_THEME_NAME과는 별개로, "처음 실행했을 때 보여줄 테마"는
#: 여기서 결정한다(저장된 사용자 설정이 없을 때만 사용됨).
INITIAL_THEME_NAME: str = 'light'

_active_theme_name: str = INITIAL_THEME_NAME

#: 테마가 바뀔 때마다 호출되는 콜백 목록 (인라인 스타일을 직접 칠한 위젯들이 등록).
_theme_listeners: list[Callable[[], None]] = []


def set_theme(name: str) -> Theme:
    """
    활성 테마를 변경하고, 등록된 모든 리스너를 호출한 뒤 팔레트를 반환한다.
    """
    global _active_theme_name
    _active_theme_name = name if name in ('light', 'dark') else DEFAULT_THEME_NAME

    for callback in list(_theme_listeners):
        try:
            callback()
        except RuntimeError:
            # PySide6 위젯이 이미 C++ 레벨에서 삭제된 경우 (deleteLater 등).
            # 조용히 무시하고 다음 콜백을 진행한다 — 해제를 깜빡한 위젯이
            # 있어도 테마 전환 자체가 막히면 안 되기 때문.
            pass

    return get_theme(_active_theme_name)


def register_theme_listener(callback: Callable[[], None]) -> None:
    """
    테마가 바뀔 때마다 호출될 콜백을 등록한다.
    인라인 setStyleSheet()으로 색을 직접 칠하는 위젯은 반드시 이걸 호출해야
    set_theme() 으로 전체 테마를 바꿀 때 같이 갱신된다.
    """
    if callback not in _theme_listeners:
        _theme_listeners.append(callback)


def unregister_theme_listener(callback: Callable[[], None]) -> None:
    """더 이상 필요 없는 리스너를 해제한다 (다이얼로그가 닫힐 때 등)."""
    if callback in _theme_listeners:
        _theme_listeners.remove(callback)


def get_theme_name() -> str:
    """현재 활성 테마 이름('light' | 'dark')을 반환한다."""
    return _active_theme_name


def current_colors() -> Theme:
    """현재 활성 테마의 색상 팔레트를 딕셔너리로 반환한다."""
    return get_theme(_active_theme_name)


# ---------------------------------------------------------------------------
# 폰트 (정적)
# ---------------------------------------------------------------------------

FONT_FAMILY_UI   = 'Malgun Gothic'   # Windows 기본 한글 폰트
FONT_FAMILY_MONO = 'Consolas'        # 로그·코드 출력용

FONT_SIZE_SMALL  = 9    # pt — 힌트, 부가 설명
FONT_SIZE_BASE   = 10   # pt — 일반 UI 텍스트
FONT_SIZE_MEDIUM = 11   # pt — 강조 레이블
FONT_SIZE_LARGE  = 13   # pt — 섹션 헤더
FONT_SIZE_TITLE  = 16   # pt — 탭 제목 등

# ---------------------------------------------------------------------------
# 크기 / 간격 (정적)
# ---------------------------------------------------------------------------

WIN_MIN_WIDTH      = 960
WIN_MIN_HEIGHT     = 680
WIN_DEFAULT_WIDTH  = 1100
WIN_DEFAULT_HEIGHT = 760

RADIUS_SMALL   = '4px'
RADIUS_DEFAULT = '6px'
RADIUS_LARGE   = '10px'

PADDING_SMALL   = 4    # px
PADDING_DEFAULT = 8    # px
PADDING_LARGE   = 16   # px

SPACING_SMALL   = 4    # px
SPACING_DEFAULT = 8    # px
SPACING_LARGE   = 16   # px

BTN_MIN_WIDTH    = 60
BTN_MIN_HEIGHT   = 22
INPUT_MIN_HEIGHT = 24
LOG_MIN_HEIGHT   = 200

# ---------------------------------------------------------------------------
# 아이콘 크기 (정적)
# ---------------------------------------------------------------------------

ICON_SIZE_SMALL   = 16
ICON_SIZE_DEFAULT = 20
ICON_SIZE_LARGE   = 24
