"""
ui/widgets/worker_base.py

모든 탭의 백그라운드 작업(QThread)이 공유하는 안전한 베이스 클래스.

배경
----
QThread.run() 내부에서 예외가 발생하면 Qt는 그 예외를 콘솔에만 출력하고
삼켜버린다. 이때 run() 끝에서 emit 하기로 되어 있던 '완료' 시그널이
발행되지 않으므로, 그 시그널을 기다리는 UI(버튼 비활성화 등)는
영원히 '처리 중' 상태로 멈춘다. 권한 없는 폴더, 손상된 이미지 파일,
디스크 공간 부족 등 실제 사용 환경에서 충분히 발생할 수 있는 상황이다.

해결
----
SafeWorker는 run() 대신 work()를 오버라이드하게 하고, 실제 run()에서
work()를 try/except로 감싼다. 예외가 발생하면 error 시그널을 emit해
최소한 사용자가 무엇이 잘못됐는지 알 수 있게 하고, finally 블록에서
원래 작업이 마치고 발행했어야 할 '완료' 시그널도 보장하고 싶다면
on_error()를 오버라이드해 직접 처리한다.

사용법
------
    class _MyWorker(SafeWorker):
        finished = Signal(int, int, list)

        def __init__(self, folder):
            super().__init__()
            self._folder = folder

        def work(self) -> None:
            success, fail, logs = SomeProcessor.do(self._folder)
            self.finished.emit(success, fail, logs)

work() 안에서 예외가 나면 error 시그널이 자동으로 emit되고, 호출부는
보통 error.connect(lambda msg: QMessageBox.critical(...))로 연결해
사용자에게 알리면서 동시에 _set_busy(False) 류의 UI 잠금 해제도
같은 슬롯에서 처리하면 된다.
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QThread, Signal


class SafeWorker(QThread):
    """
    work()를 오버라이드해서 쓰는 QThread 베이스.

    Signals:
        error(str): work() 내부에서 처리되지 않은 예외가 발생했을 때
                    전체 트레이스백 문자열과 함께 emit된다.
    """

    error = Signal(str)

    def work(self) -> None:
        """하위 클래스가 실제 작업을 구현하는 지점. 반드시 오버라이드해야 한다."""
        raise NotImplementedError

    def run(self) -> None:
        try:
            self.work()
        except Exception:
            self.error.emit(traceback.format_exc())
