"""
core/file_manager.py

파일 관리 모듈 — 짝 없는 단일 파일 탐색, 삭제, 이동 기능.
"""

from __future__ import annotations

import shutil
import re
from pathlib import Path
from typing import List, Tuple

from utils.common import (
    IMAGE_EXTENSIONS,
    TEXT_EXTENSION,
    is_image_file,
    is_text_file,
)


class FileManager:

    @staticmethod
    def _natural_path_key(path: Path) -> tuple:
        parts = re.split(r'(\d+)', path.name.casefold())
        return tuple(int(part) if part.isdigit() else part for part in parts) + (str(path).casefold(),)

    def __init__(self, folder_path: str) -> None:
        self.folder = Path(folder_path)

    # ------------------------------------------------------------------
    # 탐색
    # ------------------------------------------------------------------

    def find_single_images(self, recursive: bool = False) -> List[Path]:
        """
        짝이 없는 이미지 파일 목록을 반환한다 (대응하는 .txt 파일 없음).

        Args:
            recursive: True 이면 하위 폴더도 탐색

        Returns:
            이름 기준 오름차순 정렬된 파일 경로 목록
        """
        if not self.folder.exists():
            return []

        files        = self.folder.rglob('*') if recursive else self.folder.iterdir()
        single: List[Path] = []

        for f in files:
            if f.is_file() and is_image_file(f):
                if not f.with_suffix(TEXT_EXTENSION).exists():
                    single.append(f)

        return sorted(single, key=self._natural_path_key)

    def find_single_texts(self, recursive: bool = False) -> List[Path]:
        """
        짝이 없는 텍스트 파일 목록을 반환한다 (대응하는 이미지 파일 없음).

        Args:
            recursive: True 이면 하위 폴더도 탐색

        Returns:
            이름 기준 오름차순 정렬된 파일 경로 목록
        """
        if not self.folder.exists():
            return []

        files        = self.folder.rglob('*') if recursive else self.folder.iterdir()
        single: List[Path] = []

        for f in files:
            if not f.is_file() or not is_text_file(f):
                continue
            has_pair = any(
                f.with_suffix(ext).exists()
                for ext in IMAGE_EXTENSIONS
            )
            if not has_pair:
                single.append(f)

        return sorted(single, key=self._natural_path_key)

    # ------------------------------------------------------------------
    # 삭제 / 이동
    # ------------------------------------------------------------------

    def delete_files(self, files: List[Path]) -> Tuple[int, int]:
        """
        파일 목록을 삭제한다.

        Returns:
            (성공 수, 실패 수)
        """
        success = fail = 0
        for f in files:
            try:
                if f.exists():
                    f.unlink()
                    success += 1
                else:
                    fail += 1
            except Exception as e:
                print(f"삭제 실패 {f.name}: {e}")
                fail += 1
        return success, fail

    def move_files(self, files: List[Path], dest_folder: str) -> Tuple[int, int]:
        """
        파일 목록을 지정 폴더로 이동한다. 대상 폴더가 없으면 생성한다.

        Returns:
            (성공 수, 실패 수)
        """
        dest = Path(dest_folder)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"폴더 생성 실패: {e}")
            return 0, len(files)

        success = fail = 0
        for f in files:
            try:
                if f.exists():
                    # 같은 이름의 파일을 덮어쓰지 않고 _1, _2 … 를 붙여 보존한다.
                    if f.parent.resolve() == dest.resolve():
                        fail += 1
                        continue
                    target = dest / f.name
                    counter = 1
                    while target.exists():
                        target = dest / f'{f.stem}_{counter}{f.suffix}'
                        counter += 1
                    shutil.move(str(f), str(target))
                    success += 1
                else:
                    fail += 1
            except Exception as e:
                print(f"이동 실패 {f.name}: {e}")
                fail += 1
        return success, fail

    # ------------------------------------------------------------------
    # 유틸
    # ------------------------------------------------------------------

    def get_file_list_text(self, files: List[Path]) -> str:
        """파일 목록을 개행 구분 문자열로 반환한다."""
        if not files:
            return "파일이 없습니다."
        return '\n'.join(f.name for f in files)
