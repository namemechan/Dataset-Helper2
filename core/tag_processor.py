"""
core/tag_processor.py

태그 처리 모듈 — 태그 치환, 삭제, 이동, 추가, 정렬 및 실행 취소 기능.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.common import PERSON_COUNT_TAGS, process_with_multicore
from utils.settings import UNDO_DIR


class TagProcessor:

    # ------------------------------------------------------------------
    # 실행 취소 저장 / 복구
    # ------------------------------------------------------------------

    @staticmethod
    def save_undo_info(folder_path: str, tag_history: List[Dict[str, str]]) -> None:
        """
        태그 처리 실행 취소 정보를 JSON 파일로 저장한다.

        Args:
            folder_path:  처리한 폴더의 절대 경로
            tag_history:  [{"file": "relative_path", "content": "original_content"}, ...]
        """
        if not tag_history:
            return

        UNDO_DIR.mkdir(parents=True, exist_ok=True)

        timestamp     = datetime.now().strftime('%Y%m%d_%H%M%S')
        undo_path     = UNDO_DIR / f"undo_tag_{timestamp}.json"

        undo_data = {
            'type':        'tag',
            'folder_path': str(Path(folder_path).absolute()),
            'timestamp':   datetime.now().isoformat(),
            'history':     tag_history,
        }

        try:
            with undo_path.open('w', encoding='utf-8') as f:
                json.dump(undo_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"태그 실행 취소 파일 저장 실패: {e}")

    @staticmethod
    def undo_last_processing(folder_path: str) -> Tuple[int, int, List[str]]:
        """
        가장 최근 태그 작업을 실행 취소한다.

        Returns:
            (성공 수, 실패 수, 로그 메시지 리스트)
        """
        if not UNDO_DIR.exists():
            return 0, 0, ["실행 취소 폴더가 없습니다."]

        files        = sorted(UNDO_DIR.glob('undo_tag_*.json'), reverse=True)
        current_path = Path(folder_path).absolute()
        target_file: Optional[Path] = None

        for file_path in files:
            try:
                with file_path.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                if Path(data.get('folder_path', '')) == current_path:
                    target_file = file_path
                    break
            except Exception:
                continue

        if not target_file:
            return 0, 0, ["실행 취소할 태그 작업 내역이 없습니다."]

        success = 0
        fail    = 0
        logs: List[str] = []

        try:
            with target_file.open('r', encoding='utf-8') as f:
                data = json.load(f)

            history = data.get('history', [])
            folder  = Path(folder_path)

            for item in history:
                rel_path         = item['file']
                original_content = item['content']
                file_path        = folder / rel_path

                try:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(original_content, encoding='utf-8')
                    success += 1
                    logs.append(f"복구: {rel_path}")
                except Exception as e:
                    logs.append(f"오류 {rel_path}: {e}")
                    fail += 1

            target_file.unlink()
            logs.append(f"실행 취소 파일 삭제됨: {target_file.name}")

        except Exception as e:
            return 0, 0, [f"실행 취소 중 치명적 오류: {e}"]

        return success, fail, logs

    # ------------------------------------------------------------------
    # 태그 파싱 / 직렬화
    # ------------------------------------------------------------------

    @staticmethod
    def parse_tags(tag_string: str) -> List[str]:
        """쉼표 구분 태그 문자열을 리스트로 파싱한다. 빈 태그는 제거한다."""
        if not tag_string:
            return []
        return [t.strip() for t in tag_string.split(',') if t.strip()]

    @staticmethod
    def join_tags(tags: List[str]) -> str:
        """태그 리스트를 ', ' 구분자로 결합한다."""
        return ', '.join(tags)

    # ------------------------------------------------------------------
    # 핵심 처리 로직
    # ------------------------------------------------------------------

    @staticmethod
    def process_tags_logic(
        content: str,
        options: Dict,
    ) -> Tuple[str, List[str]]:
        """
        단일 파일의 태그 문자열에 옵션에 따른 처리를 적용한다.

        Args:
            content: 현재 태그 파일 내용 (쉼표 구분 문자열)
            options: 처리 옵션 딕셔너리

        Returns:
            (new_content, changes) — 변경된 내용과 변경 설명 리스트
        """
        tags    = TagProcessor.parse_tags(content)
        changes: List[str] = []

        # ── 내부 헬퍼 ──────────────────────────────────────────
        def replace_subsequence(
            current: List[str],
            find_seq: List[str],
            replace_seq: List[str],
        ) -> Tuple[List[str], int]:
            if not find_seq:
                return current, 0
            result: List[str] = []
            i, n, m, count = 0, len(current), len(find_seq), 0
            while i < n:
                if i + m <= n and current[i:i + m] == find_seq:
                    result.extend(replace_seq)
                    count += 1
                    i += m
                else:
                    result.append(current[i])
                    i += 1
            return result, count

        def check_condition(current: List[str], condition_str: str) -> bool:
            if not condition_str:
                return False
            return any(
                ct in current
                for ct in [t.strip() for t in condition_str.split('|') if t.strip()]
            )

        # ── 0. 누락된 인원수 태그 주입 ─────────────────────────
        if options.get('use_missing_tag'):
            if not any(t in tags for t in PERSON_COUNT_TAGS):
                gender = options.get('missing_gender', 'girl')
                count  = options.get('missing_count', '1')
                if count == '6+':
                    new_tag = f"6+{gender}s"
                elif count == '1':
                    new_tag = f"1{gender}"
                else:
                    new_tag = f"{count}{gender}s"
                if new_tag not in tags:
                    tags.insert(0, new_tag)
                    changes.append(f"주입: 누락된 인원수 태그 '{new_tag}' 추가")

        # ── 1. 태그 치환 ────────────────────────────────────────
        if options.get('use_replace') and options.get('replace_find'):
            find_str    = options['replace_find'].strip()
            replace_str = options.get('replace_with', '').strip()
            find_seq    = TagProcessor.parse_tags(find_str)
            replace_seq = TagProcessor.parse_tags(replace_str)
            if find_seq:
                tags, cnt = replace_subsequence(tags, find_seq, replace_seq)
                if cnt:
                    changes.append(f"치환: '{find_str}' → '{replace_str}' ({cnt}건)")

        # ── 1.5 인접 태그 수정 ──────────────────────────────────
        if options.get('use_neighbor_modify') and options.get('neighbor_target'):
            target_tag   = options['neighbor_target'].strip()
            neighbor_pos = options.get('neighbor_pos', 'after')   # 'before' | 'after'
            add_pos      = options.get('neighbor_add_pos', 'prefix')  # 'prefix' | 'suffix'
            add_text     = options.get('neighbor_text', '')

            if target_tag and add_text:
                new_tags         = tags[:]
                modified_indices = set()

                for idx, tag in enumerate(tags):
                    if tag == target_tag:
                        n_idx = idx - 1 if neighbor_pos == 'before' else idx + 1
                        if 0 <= n_idx < len(new_tags) and n_idx not in modified_indices:
                            orig = new_tags[n_idx]
                            new_tags[n_idx] = (
                                f"{add_text}{orig}" if add_pos == 'prefix'
                                else f"{orig}{add_text}"
                            )
                            modified_indices.add(n_idx)

                if new_tags != tags:
                    tags = new_tags
                    changes.append(f"인접 수정: '{target_tag}' 기준 {neighbor_pos} 태그에 '{add_text}' {add_pos}")

        # ── 1.7 CSV 기반 특수 처리 ──────────────────────────────
        if options.get('use_csv_process') and options.get('csv_tags_set'):
            csv_tags    = options['csv_tags_set']
            csv_mode    = options.get('csv_mode', 'add')
            csv_input   = options.get('csv_input_text', '')
            csv_add_pos = options.get('csv_add_pos', 'prefix')

            new_tags_list: List[str] = []
            csv_changes_count = 0

            for tag in tags:
                # 비교를 위한 정규화 (소문자화 및 언더바->공백)
                normalized_tag = tag.lower().replace('_', ' ')

                if normalized_tag in csv_tags:
                    csv_changes_count += 1
                    if csv_mode == 'add':
                        processed_tag = (csv_input + tag) if csv_add_pos == 'prefix' else (tag + csv_input)
                        new_tags_list.append(processed_tag)
                    elif csv_mode == 'replace':
                        new_tags_list.append(csv_input)
                    elif csv_mode == 'delete':
                        continue  # 추가하지 않음 (삭제)
                    else:
                        new_tags_list.append(tag)
                else:
                    new_tags_list.append(tag)

            if csv_changes_count > 0:
                tags = new_tags_list
                mode_name = '추가' if csv_mode == 'add' else '치환' if csv_mode == 'replace' else '삭제'
                changes.append(f"CSV처리: {csv_changes_count}개 태그 {mode_name} 완료")

        # ── 2. 태그 삭제 ────────────────────────────────────────
        if options.get('use_delete') and options.get('delete_tags'):
            delete_set = set(options['delete_tags'])

            if options.get('use_conditional_delete'):
                cond = options.get('condition_delete_tags', '')
                if not check_condition(tags, cond):
                    delete_set = set()

            if delete_set:
                before_len = len(tags)
                tags       = [t for t in tags if t not in delete_set]
                removed    = before_len - len(tags)
                if removed:
                    changes.append(f"삭제: {removed}개 태그 제거")

        # ── 3. 태그 이동 + 4. 태그 추가 (이동 활성화 시) ────────
        use_person = options.get('use_move_person', False)
        use_solo   = options.get('use_move_solo', False)
        use_custom = options.get('use_move_custom', False)

        if use_person or use_solo or use_custom:
            custom_targets = set(options.get('move_custom_tags', [])) if use_custom else set()
            person_group, solo_group, custom_group, other_group = [], [], [], []

            for tag in tags:
                if use_person and tag in PERSON_COUNT_TAGS:
                    person_group.append(tag)
                elif use_solo and tag == 'solo':
                    solo_group.append(tag)
                elif use_custom and tag in custom_targets:
                    custom_group.append(tag)
                else:
                    other_group.append(tag)

            person_group.sort()
            front_tags = person_group + solo_group + custom_group

            if options.get('use_add') and options.get('add_tags'):
                should_add = True
                if options.get('use_conditional_add'):
                    all_tags = person_group + solo_group + custom_group + other_group
                    if not check_condition(all_tags, options.get('condition_add_tags', '')):
                        should_add = False
                if should_add:
                    add_str  = options['add_tags']
                    new_adds = TagProcessor.parse_tags(add_str)
                    if new_adds:
                        front_tags.extend(new_adds)
                        changes.append(f"추가: '{add_str}'")

            new_order = front_tags + other_group
            if new_order != tags:
                tags = new_order
                moved_info = (
                    (['인원수'] if person_group else [])
                    + (['solo'] if solo_group else [])
                    + (['지정 태그'] if custom_group else [])
                )
                if moved_info:
                    changes.append(f"이동: {', '.join(moved_info)} 앞으로")

        # ── 4. 태그 추가 (이동 비활성화 시) ─────────────────────
        elif options.get('use_add') and options.get('add_tags'):
            should_add = True
            if options.get('use_conditional_add'):
                if not check_condition(tags, options.get('condition_add_tags', '')):
                    should_add = False
            if should_add:
                add_str  = options['add_tags']
                new_adds = TagProcessor.parse_tags(add_str)
                if new_adds:
                    tags = new_adds + tags
                    changes.append(f"추가: '{add_str}' (맨 앞)")

        return TagProcessor.join_tags(tags), changes

    # ------------------------------------------------------------------
    # 단일 파일 처리
    # ------------------------------------------------------------------

    @staticmethod
    def process_single_file(
        file_path: Path,
        options: Dict,
    ) -> Tuple[bool, str, List[str], str]:
        """
        단일 텍스트 파일에 태그 처리를 적용한다.

        Returns:
            (is_changed, log_message, changes, original_content)
        """
        try:
            content     = file_path.read_text(encoding='utf-8').strip()
            new_content, changes = TagProcessor.process_tags_logic(content, options)

            if new_content != content:
                file_path.write_text(new_content, encoding='utf-8')
                return True, f"변경됨: {file_path.name} | {' / '.join(changes)}", changes, content
            return False, f"변경 없음: {file_path.name}", [], content

        except Exception as e:
            return False, f"오류: {file_path.name} - {e}", [], ''

    # ------------------------------------------------------------------
    # 폴더 일괄 처리
    # ------------------------------------------------------------------

    @staticmethod
    def process_folder(
        text_files: List[Path],
        options: Dict,
        num_cores: int = 1,
        folder_path: str = '',
    ) -> Tuple[int, int, List[str]]:
        """
        텍스트 파일 목록에 태그 처리를 일괄 적용한다.

        Args:
            text_files:  처리할 .txt 파일 경로 목록
            options:     처리 옵션
            num_cores:   사용할 코어 수
            folder_path: 실행 취소용 기준 폴더 경로

        Returns:
            (성공 수, 실패 수, 로그 메시지 리스트)
        """
        if not text_files:
            return 0, 0, ["처리할 파일이 없습니다."]

        worker  = partial(TagProcessor.process_single_file, options=options)
        results = process_with_multicore(worker, text_files, num_cores)

        success       = 0
        fail          = 0
        logs: List[str]         = []
        tag_history: List[Dict] = []
        root_path = Path(folder_path).absolute() if folder_path else None

        for i, (is_changed, log_msg, _, original_content) in enumerate(results):
            logs.append(log_msg)
            if is_changed:
                success += 1
                if root_path:
                    try:
                        rel = str(text_files[i].absolute().relative_to(root_path))
                    except ValueError:
                        rel = text_files[i].name
                    tag_history.append({'file': rel, 'content': original_content})
            elif '오류' in log_msg:
                fail += 1

        if tag_history and folder_path:
            TagProcessor.save_undo_info(folder_path, tag_history)

        return success, fail, logs

    # ------------------------------------------------------------------
    # 미리보기
    # ------------------------------------------------------------------

    @staticmethod
    def preview_tag_processing(
        text_files: List[Path],
        options: Dict,
        preview_count: int = 10,
    ) -> List[str]:
        """처리 결과를 실제로 저장하지 않고 미리보기 텍스트를 생성한다."""
        if not text_files:
            return ["처리할 파일이 없습니다."]

        op_summary: List[str] = []
        if options.get('use_replace'):
            op_summary.append(f"[치환] {options['replace_find']} -> {options['replace_with']}")
        if options.get('use_delete'):
            suffix = ' (조건부)' if options.get('use_conditional_delete') else ''
            op_summary.append(f"[삭제] {len(options['delete_tags'])}개 태그{suffix}")
        if options.get('use_move_person'):
            op_summary.append("[이동] 인원수 태그")
        if options.get('use_move_custom'):
            op_summary.append(f"[이동] 사용자 지정 {len(options['move_custom_tags'])}개 태그")
        if options.get('use_add'):
            suffix = ' (조건부)' if options.get('use_conditional_add') else ''
            op_summary.append(f"[추가] {options['add_tags']}{suffix}")

        preview: List[str] = [f"적용 옵션: {', '.join(op_summary) if op_summary else '없음'}\n", '-' * 50]
        count = processed_count = 0

        for file_path in text_files:
            try:
                content     = file_path.read_text(encoding='utf-8').strip()
                new_content, changes = TagProcessor.process_tags_logic(content, options)

                if changes:
                    processed_count += 1
                    if count < preview_count:
                        preview.append(f"📄 {file_path.name}")
                        for ch in changes:
                            preview.append(f"  └ {ch}")
                        short_orig = (content[:60] + '...') if len(content) > 60 else content
                        short_new  = (new_content[:60] + '...') if len(new_content) > 60 else new_content
                        preview.append(f"  [전] {short_orig}")
                        preview.append(f"  [후] {short_new}")
                        preview.append('')
                        count += 1
            except Exception as e:
                if count < preview_count:
                    preview.append(f"❌ {file_path.name}: {e}")
                    count += 1

        final = [
            f"검색된 전체 파일: {len(text_files)}개",
            f"변경 대상 파일: {processed_count}개",
            '',
        ] + preview

        if count == 0 and processed_count == 0:
            final.append("설정된 옵션으로 변경되는 파일이 없습니다.")
        elif count < processed_count:
            final.append(f"... 외 {processed_count - count}개 파일 변경 예정")

        return final
