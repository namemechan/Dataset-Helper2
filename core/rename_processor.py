"""
core/rename_processor.py

이미지-텍스트 파일 쌍 일괄 이름 변경 및 실행 취소 모듈.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.common import get_paired_files, format_number
from utils.settings import UNDO_DIR


class RenameProcessor:

    # ------------------------------------------------------------------
    # 실행 취소 저장 / 복구
    # ------------------------------------------------------------------

    @staticmethod
    def save_undo_info(
        folder_path: str,
        rename_history: List[Tuple[str, str, str, str]],
    ) -> None:
        """
        이름 변경 실행 취소 정보를 JSON 파일로 저장한다.

        Args:
            folder_path:    처리한 폴더 경로
            rename_history: [(원본_이미지명, 원본_텍스트명, 새_이미지명, 새_텍스트명), ...]
        """
        UNDO_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        undo_path = UNDO_DIR / f"undo_rename_{timestamp}.json"

        undo_data = {
            'type':        'rename',
            'folder_path': str(Path(folder_path).absolute()),
            'timestamp':   datetime.now().isoformat(),
            'history':     rename_history,
        }

        try:
            with undo_path.open('w', encoding='utf-8') as f:
                json.dump(undo_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"실행 취소 파일 저장 실패: {e}")

    @staticmethod
    def get_latest_undo_file(
        folder_path: str,
    ) -> Optional[Tuple[Path, Dict]]:
        """
        해당 폴더에 대한 가장 최근 이름 변경 실행 취소 파일을 반환한다.

        Returns:
            (파일 경로, 데이터 딕셔너리) 또는 None
        """
        if not UNDO_DIR.exists():
            return None

        files        = sorted(UNDO_DIR.glob('undo_rename_*.json'), reverse=True)
        current_path = Path(folder_path).absolute()

        for file_path in files:
            try:
                with file_path.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                if Path(data.get('folder_path', '')) == current_path:
                    return file_path, data
            except Exception:
                continue

        return None

    @staticmethod
    def undo_rename(folder_path: str) -> Tuple[int, int, List[str]]:
        """
        이름 변경을 실행 취소한다 (역순으로 원래 이름으로 복구).

        Returns:
            (성공 수, 실패 수, 로그 메시지 리스트)
        """
        undo_info = RenameProcessor.get_latest_undo_file(folder_path)
        if not undo_info:
            return 0, 0, ["현재 폴더에 대한 실행 취소 내역이 없습니다."]

        undo_file_path, data = undo_info
        history = data.get('history', [])
        folder  = Path(folder_path)
        success = 0
        fail    = 0
        logs: List[str] = []

        for orig_img, orig_txt, new_img, new_txt in reversed(history):
            try:
                new_img_path = folder / new_img
                new_txt_path = folder / new_txt
                orig_img_path = folder / orig_img
                orig_txt_path = folder / orig_txt

                if not new_img_path.exists() or not new_txt_path.exists():
                    raise FileNotFoundError('복구할 이미지 또는 텍스트 파일이 없습니다.')
                if orig_img_path.exists() or orig_txt_path.exists():
                    raise FileExistsError('원래 파일명이 이미 존재합니다.')

                new_img_path.rename(orig_img_path)
                try:
                    new_txt_path.rename(orig_txt_path)
                except Exception:
                    # 텍스트 복구가 실패하면 이미지도 원래의 "변경 후" 상태로 되돌린다.
                    if orig_img_path.exists() and not new_img_path.exists():
                        orig_img_path.rename(new_img_path)
                    raise

                logs.append(f"복구 완료: {new_img} → {orig_img}")
                success += 1
            except Exception as e:
                logs.append(f"복구 실패 {new_img}: {e}")
                fail += 1

        if fail == 0:
            try:
                undo_file_path.unlink()
                logs.append(f"실행 취소 파일 삭제됨: {undo_file_path.name}")
            except Exception as e:
                logs.append(f"실행 취소 파일 삭제 실패: {e}")
        else:
            logs.append('복구 실패 항목이 있어 실행 취소 내역을 유지합니다.')

        return success, fail, logs

    # ------------------------------------------------------------------
    # 이름 변경
    # ------------------------------------------------------------------

    @staticmethod
    def rename_file_pairs(
        folder_path: str,
        base_name: str,
        start_number: int,
        digit_count: int,
    ) -> Tuple[int, int, List[str]]:
        """
        폴더 내 이미지-텍스트 파일 쌍을 일괄 이름 변경한다.

        2단계 전략 (충돌 방지):
          1단계: 모든 쌍을 임시 이름(_temp_N)으로 변경
          2단계: 임시 이름을 최종 이름으로 변경

        Args:
            folder_path:  처리할 폴더 경로
            base_name:    새 파일명 기본 접두사
            start_number: 시작 번호
            digit_count:  번호 자릿수 (제로패딩)

        Returns:
            (성공 수, 실패 수, 로그 메시지 리스트)
        """
        folder = Path(folder_path)
        if not folder.exists():
            return 0, 0, ["폴더가 존재하지 않습니다."]

        base_name = base_name.strip()
        if not base_name or any(ch in base_name for ch in '<>:"/\\|?*') or base_name.endswith(('.', ' ')):
            return 0, 0, ["기본 이름에 사용할 수 없는 문자가 포함되어 있습니다."]

        paired_files = get_paired_files(folder)
        if not paired_files:
            return 0, 0, ["이름을 변경할 파일 쌍이 없습니다."]

        plans = []
        token = uuid.uuid4().hex
        for index, (img_path, txt_path) in enumerate(paired_files):
            num_str = format_number(start_number + index, digit_count)
            new_base = f"{base_name}_{num_str}"
            plans.append({
                'orig_img': img_path, 'orig_txt': txt_path,
                'temp_img': img_path.parent / f'.dataset_helper_tmp_{token}_{index}{img_path.suffix}',
                'temp_txt': txt_path.parent / f'.dataset_helper_tmp_{token}_{index}{txt_path.suffix}',
                'final_img': img_path.parent / f'{new_base}{img_path.suffix}',
                'final_txt': txt_path.parent / f'{new_base}{txt_path.suffix}',
            })

        source_paths = {p['orig_img'] for p in plans} | {p['orig_txt'] for p in plans}
        conflicts = [
            target for p in plans for target in (p['final_img'], p['final_txt'])
            if target.exists() and target not in source_paths
        ]
        if conflicts:
            return 0, len(plans), [f'대상 파일이 이미 존재합니다: {conflicts[0].name}']

        logs: List[str] = []

        def rollback() -> None:
            for p in plans:
                for current, original in ((p['final_img'], p['orig_img']), (p['final_txt'], p['orig_txt']),
                                          (p['temp_img'], p['orig_img']), (p['temp_txt'], p['orig_txt'])):
                    try:
                        if current.exists() and not original.exists():
                            current.rename(original)
                    except Exception as e:
                        logs.append(f'복구 실패 {current.name}: {e}')

        try:
            # 1단계: 충돌 가능성이 거의 없는 고유 임시명으로 모든 파일을 이동한다.
            for p in plans:
                p['orig_img'].rename(p['temp_img'])
                p['orig_txt'].rename(p['temp_txt'])

            # 2단계: 사전 검증된 최종명으로 이동한다.
            for p in plans:
                p['temp_img'].rename(p['final_img'])
                p['temp_txt'].rename(p['final_txt'])
        except Exception as e:
            rollback()
            logs.append(f'이름 변경 실패 — 모든 가능한 파일을 원래 이름으로 복구했습니다: {e}')
            return 0, len(plans), logs

        rename_history: List[Tuple[str, str, str, str]] = []
        for p in plans:
            rename_history.append((
                p['orig_img'].name, p['orig_txt'].name,
                p['final_img'].name, p['final_txt'].name,
            ))
            logs.append(f"변경 완료: {p['orig_img'].name} → {p['final_img'].name}")

        if rename_history:
            RenameProcessor.save_undo_info(folder_path, rename_history)

        return len(plans), 0, logs

    # ------------------------------------------------------------------
    # 미리보기
    # ------------------------------------------------------------------

    @staticmethod
    def preview_rename(
        folder_path: str,
        base_name: str,
        start_number: int,
        digit_count: int,
        preview_count: int = 10,
    ) -> List[str]:
        """이름 변경 결과를 실제로 저장하지 않고 미리보기 텍스트를 생성한다."""
        folder = Path(folder_path)
        if not folder.exists():
            return ["폴더가 존재하지 않습니다."]

        paired_files = get_paired_files(folder)
        if not paired_files:
            return ["이름을 변경할 파일 쌍이 없습니다."]

        preview: List[str] = [
            f"총 {len(paired_files)}개의 파일 쌍이 변경됩니다.\n",
            f"미리보기 (처음 {min(preview_count, len(paired_files))}개):",
        ]

        for i, (img_path, txt_path) in enumerate(paired_files[:preview_count]):
            num_str  = format_number(start_number + i, digit_count)
            new_base = f"{base_name}_{num_str}"
            preview.append(f"\n[{i + 1}]")
            preview.append(f"  {img_path.name} → {new_base}{img_path.suffix}")
            preview.append(f"  {txt_path.name} → {new_base}{txt_path.suffix}")

        if len(paired_files) > preview_count:
            preview.append(f"\n... 외 {len(paired_files) - preview_count}개")

        return preview
