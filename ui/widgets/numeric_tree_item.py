"""
ui/widgets/numeric_tree_item.py

QTreeWidget의 기본 정렬은 모든 컬럼을 문자열로 비교하므로 '9개'와 '10개',
'9.5%'와 '10.2%', 해상도(가로x세로) 같은 숫자 의미를 가진 컬럼이 잘못된
순서로 정렬된다. NumericTreeItem은 컬럼별로 SORT_KEY_ROLE에 저장된
float 값이 있으면 그 값으로, 없으면 텍스트로 비교한다.

사용법
------
    item = NumericTreeItem(['폴더A', '12개', '34.5%'])
    item.setData(1, SORT_KEY_ROLE, float(12))
    item.setData(2, SORT_KEY_ROLE, float(34.5))
    tree.addTopLevelItem(item)
    tree.setSortingEnabled(True)   # 반드시 켜야 헤더 클릭 정렬이 동작한다

정렬 키를 주지 않은 컬럼(보통 이름 같은 텍스트 컬럼)은 자동으로
일반 문자열 비교로 폴백된다.
"""

from __future__ import annotations

from PySide6.QtCore    import Qt
from PySide6.QtWidgets import QTreeWidgetItem

#: 정렬 키 저장용 커스텀 role.
#: UserRole 자체는 다른 용도(예: 원본 데이터 인덱스, 폴더 경로)로 흔히 쓰이므로
#: 충돌을 피하기 위해 한 칸 띈 UserRole + 1을 사용한다.
SORT_KEY_ROLE = Qt.ItemDataRole.UserRole + 1


class NumericTreeItem(QTreeWidgetItem):
    """숫자 정렬 키(SORT_KEY_ROLE)가 있는 컬럼은 그 값으로, 없으면 텍스트로 비교한다."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:  # noqa: D105
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        key_self  = self.data(col, SORT_KEY_ROLE)
        key_other = other.data(col, SORT_KEY_ROLE)
        if key_self is not None and key_other is not None:
            return float(key_self) < float(key_other)
        return self.text(col) < other.text(col)
