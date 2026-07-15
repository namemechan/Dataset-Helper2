"""
ui/themes.py

라이트/다크 두 가지 색상 팔레트를 정의한다.

각 팔레트는 ui/constants.py 에 있던 COLOR_* 상수와 동일한 키 이름을 갖는
딕셔너리(Theme)이다. ui/styles/theme.py 의 build_qss(theme) 가 이 딕셔너리를
받아 QSS 문자열로 조립하고, ui/main_window.py 의 테마 토글 버튼이
LIGHT_THEME <-> DARK_THEME 를 전환해 app.setStyleSheet() 를 다시 호출한다.

팔레트 설계 의도
----------------
- LIGHT_THEME : Claude 데스크톱 앱의 라이트 모드와 비슷한 느낌을 의도했다.
  순백색이 아닌 부드러운 크림/베이지 배경, 차분한 채도의 테라코타 계열
  강조색을 사용해 눈이 편안하면서도 너무 차갑지 않은 분위기를 만든다.
- DARK_THEME : 무채색에 가까운 짙은 차콜(그래파이트) 배경에, 흔히 쓰이는
  Indigo/Purple 계열 대신 채도를 낮춘 딥 틸(청록) 강조색을 사용한다.
  Indigo 계열은 다크 테마에서 가장 흔하게 보이는 조합이라 차별성이
  떨어진다는 평가가 있어, 보다 차분하고 고급스러운 느낌을 주는
  muted teal/petrol 톤으로 교체했다.
"""

from __future__ import annotations

from typing import TypedDict


class Theme(TypedDict):
    name: str
    # 배경
    bg_dark: str
    bg_panel: str
    bg_input: str
    # 강조색
    accent: str
    accent_hover: str
    accent_press: str
    # 상태색
    success: str
    warning: str
    error: str
    # 텍스트
    text_primary: str
    text_secondary: str
    # 선
    border: str
    separator: str
    # 기타
    log_bg: str
    log_text: str
    canvas_bg: str          # 이미지 뷰어/미리보기 캔버스 배경
    hover_row: str          # 리스트/트리 hover 배경


# ---------------------------------------------------------------------------
# 라이트 — Claude 스타일 (부드러운 크림 + 차분한 테라코타)
# ---------------------------------------------------------------------------

LIGHT_THEME: Theme = {
    'name': 'light',

    'bg_dark':  '#f3f2ee',   # 메인 배경 — 눈부심을 낮춘 웜 그레이
    'bg_panel': '#faf9f6',   # 패널·카드 배경 — 부드러운 아이보리
    'bg_input': '#e9e8e2',   # 입력 필드 배경

    'accent':       '#c2682f',   # 차분한 테라코타/번트오렌지
    'accent_hover': '#d97b42',   # hover 시 약간 밝게
    'accent_press': '#a3551f',   # press 시 약간 어둡게

    'success': '#3d8a5f',   # 차분한 포레스트그린
    'warning': '#b8762b',   # 차분한 앰버
    'error':   '#c0432f',   # 차분한 레드 (테라코타와 구분되도록 톤 조정)

    'text_primary':   '#2b2922',   # 거의 검정에 가까운 웜 다크브라운
    'text_secondary': '#86807a',   # 톤 다운된 웜 그레이

    'border':    '#e3ddd1',   # 옅은 베이지 보더
    'separator': '#ece8df',

    'log_bg':   '#f7f6f1',
    'log_text': '#3a372f',
    'canvas_bg': '#dfddd6',
    'hover_row': '#f3efe6',
}

# ---------------------------------------------------------------------------
# 다크 — 차콜(그래파이트) + 무디 틸(청록). 흔한 Indigo 계열을 피해 차별화.
# ---------------------------------------------------------------------------

DARK_THEME: Theme = {
    'name': 'dark',

    'bg_dark':  '#16181a',   # 메인 배경 — 거의 무채색에 가까운 짙은 차콜
    'bg_panel': '#1d2023',   # 패널·카드 배경 — 살짝 밝은 그래파이트
    'bg_input': '#262a2d',   # 입력 필드 배경

    'accent':       '#4d8dff',   # 세련된 딥 블루
    'accent_hover': '#70a6ff',   # hover 시 밝은 블루
    'accent_press': '#376bc7',   # press 시 짙은 블루

    'success': '#7fbf7f',   # 차분한 세이지그린
    'warning': '#d9a256',   # 차분한 앰버
    'error':   '#e2766b',   # 채도를 낮춘 코랄레드 (틸과 보색 계열로 잘 어울림)

    'text_primary':   '#e8e9ea',   # 거의 흰색에 가까운 웜그레이
    'text_secondary': '#8d9396',   # 차분한 쿨그레이

    'border':    '#34383b',   # 그래파이트 보더
    'separator': '#222527',

    'log_bg':   '#0e0f10',
    'log_text': '#c4c9cb',
    'canvas_bg': '#1a1c1e',
    'hover_row': '#2a3032',
}


THEMES: dict[str, Theme] = {
    'light': LIGHT_THEME,
    'dark':  DARK_THEME,
}

#: 잘못된 테마 이름이 들어왔을 때의 안전한 폴백값.
#: "앱을 처음 실행했을 때 보여줄 테마"는 이것과 다른 개념이며,
#: ui/constants.py 의 INITIAL_THEME_NAME 이 그 역할을 담당한다.
DEFAULT_THEME_NAME = 'dark'


def get_theme(name: str) -> Theme:
    """이름으로 팔레트를 가져온다. 모르는 이름이면 기본 테마를 반환한다."""
    return THEMES.get(name, THEMES[DEFAULT_THEME_NAME])
