"""
image/converter_engine.py

이미지 변환 핵심 엔진.

담당:
  - 단일 이미지 변환 (convert_image)
  - 배치 변환 (batch_convert_images) — 순차 / 멀티프로세싱 지원
  - EXIF 방향 보정 (orient_image)
  - 품질·리사이즈 설정 적용
"""

from __future__ import annotations

import multiprocessing
import os
import time
from typing import Callable, Dict, List, Optional

import piexif
from PIL import Image

import image.file_utils as file_utils
import image.metadata as metadata_handler
from utils.logger import logger


# ---------------------------------------------------------------------------
# 멀티프로세싱 워커 (모듈 최상위에 정의해야 pickle 가능)
# ---------------------------------------------------------------------------

def _convert_image_worker(args: tuple) -> dict:
    """multiprocessing.Pool 용 워커 함수."""
    input_path, settings = args
    return convert_image(input_path, settings)


# ---------------------------------------------------------------------------
# 이미지 전처리
# ---------------------------------------------------------------------------

def orient_image(image: Image.Image) -> Image.Image:
    """
    EXIF Orientation 태그에 따라 이미지를 회전/반전 보정한다.
    보정 후 Orientation 태그를 1(정상)로 리셋해 이중 회전을 방지한다.
    """
    exif_bytes = image.info.get('exif')
    if not exif_bytes:
        return image

    try:
        exif_dict = piexif.load(exif_bytes)
        orientation = exif_dict.get('0th', {}).get(piexif.ImageIFD.Orientation)

        _ORI_MAP: dict[int, Callable[[Image.Image], Image.Image]] = {
            2: lambda img: img.transpose(Image.FLIP_LEFT_RIGHT),
            3: lambda img: img.rotate(180),
            4: lambda img: img.rotate(180).transpose(Image.FLIP_LEFT_RIGHT),
            5: lambda img: img.rotate(-90, expand=True).transpose(Image.FLIP_LEFT_RIGHT),
            6: lambda img: img.rotate(-90, expand=True),
            7: lambda img: img.rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT),
            8: lambda img: img.rotate(90, expand=True),
        }

        if orientation in _ORI_MAP:
            image = _ORI_MAP[orientation](image)
            exif_dict['0th'][piexif.ImageIFD.Orientation] = 1

            # CFAPattern(41729) 태그가 있으면 제거 (piexif dump 오류 방지)
            if 'Exif' in exif_dict and 41729 in exif_dict['Exif']:
                del exif_dict['Exif'][41729]

            image.info['exif'] = piexif.dump(exif_dict)

    except Exception as e:
        logger.warning(f"EXIF 방향 보정 중 오류: {e}", module='converter_engine')

    return image


def apply_quality_settings(
    image: Image.Image,
    fmt: str,
    quality: int,
    optimize: bool,
) -> dict:
    """
    PIL Image.save() 에 전달할 품질·최적화 관련 kwargs 를 반환한다.

    Args:
        image:    저장할 이미지 (현재 미사용, 향후 포맷별 분기 확장용)
        fmt:      저장 포맷 문자열 (예: 'PNG', 'WEBP')
        quality:  0–100 품질값
        optimize: 최적화 여부

    Returns:
        save() 에 전달할 kwargs 딕셔너리
    """
    opts: dict = {'quality': quality}
    fmt_upper = fmt.upper()
    if fmt_upper in ('PNG', 'WEBP'):
        opts['optimize'] = optimize
    if fmt_upper == 'WEBP':
        opts['lossless'] = (quality == 100)
    return opts


def apply_resize_settings(image: Image.Image, scale_factor: float) -> Image.Image:
    """
    scale_factor 비율로 이미지를 리사이즈한다.
    1.0 이면 원본을 그대로 반환한다.
    """
    if scale_factor == 1.0:
        return image
    # 아주 작은 이미지에 0.1 같은 축소 비율을 적용해도 0px이 되지 않게 한다.
    new_w = max(1, int(image.width  * scale_factor))
    new_h = max(1, int(image.height * scale_factor))
    return image.resize((new_w, new_h), Image.Resampling.LANCZOS)


def prepare_image_for_format(image: Image.Image, target_format: str) -> Image.Image:
    """저장 포맷이 지원하지 않는 색상 모드를 안전하게 변환한다."""
    fmt = target_format.upper().replace('JPG', 'JPEG')
    if fmt != 'JPEG' or image.mode in ('RGB', 'L'):
        return image

    # JPEG는 알파 채널을 지원하지 않는다. 투명 영역은 흰 배경으로 합성한다.
    if image.mode == 'P' and 'transparency' in image.info:
        image = image.convert('RGBA')
    if image.mode in ('RGBA', 'LA'):
        rgba = image.convert('RGBA')
        background = Image.new('RGB', rgba.size, 'white')
        background.paste(rgba, mask=rgba.getchannel('A'))
        return background
    return image.convert('RGB')


# ---------------------------------------------------------------------------
# 단일 이미지 변환
# ---------------------------------------------------------------------------

def convert_image(input_path: str, settings: dict) -> dict:
    """
    단일 이미지를 변환하고 결과 딕셔너리를 반환한다.

    Returns:
        {'status': 'success'|'skipped'|'error', ...}
    """
    start_time        = time.time()
    output_settings   = settings['output_settings']
    conv_settings     = settings['conversion_settings']
    meta_settings     = settings['metadata_settings']

    # ── 출력 경로 결정 ────────────────────────────────────────────
    if output_settings.get('output_to_input', False):
        output_path = file_utils.generate_output_filename_to_input(
            input_path,
            output_settings['target_format'],
            output_settings['naming_pattern'],
        )
        final_path = file_utils.handle_file_conflicts_for_input(
            output_path,
            output_settings.get('input_conflict_mode', 'rename'),
        )
    else:
        output_path = file_utils.generate_output_filename(
            input_path,
            output_settings['target_folder'],
            output_settings['target_format'],
            output_settings['naming_pattern'],
        )
        final_path = file_utils.handle_file_conflicts(
            output_path,
            output_settings['overwrite_policy'],
        )

    if final_path is None:
        return {
            'status': 'skipped',
            'path':   input_path,
            'reason': 'File exists and overwrite policy is skip',
        }

    # ── 변환 실행 ─────────────────────────────────────────────────
    try:
        logger.log_conversion_start(input_path, output_settings['target_format'])

        source_metadata = None
        if meta_settings.get('preserve_enabled'):
            source_metadata = metadata_handler.extract_all_metadata(input_path)
            if source_metadata:
                detected = [k for k, v in source_metadata.items() if v]
                logger.log_metadata_detection(detected, input_path)

        with Image.open(input_path) as img:
            img = orient_image(img)

            if conv_settings.get('resize_enabled'):
                img = apply_resize_settings(img, conv_settings['resize_scale'])

            img = prepare_image_for_format(img, output_settings['target_format'])

            save_opts: dict = {}
            if conv_settings.get('quality_enabled'):
                save_opts = apply_quality_settings(
                    img,
                    output_settings['target_format'],
                    conv_settings['quality_value'],
                    conv_settings.get('optimize', False),
                )

            if source_metadata:
                meta_opts = metadata_handler.prepare_save_options(
                    source_metadata,
                    output_settings['target_format'],
                    meta_settings,
                )
                save_opts.update(meta_opts)

            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            img.save(final_path, format=output_settings['target_format'].upper(), **save_opts)

        elapsed = time.time() - start_time
        logger.info(f"변환 완료: {final_path} ({elapsed:.2f}초)", module='converter_engine')
        return {'status': 'success', 'input': input_path, 'output': final_path, 'time': elapsed}

    except Exception as e:
        logger.error(f"변환 실패: {input_path} — {e}", module='converter_engine', exc_info=True)
        return {'status': 'error', 'path': input_path, 'reason': str(e)}


# ---------------------------------------------------------------------------
# 배치 변환
# ---------------------------------------------------------------------------

def batch_convert_images(
    file_list: List[str],
    settings: Dict,
    progress_callback: Optional[Callable] = None,
    control_callbacks: Optional[Dict[str, Callable]] = None,
) -> Dict:
    """
    이미지 파일 목록을 배치 변환한다.

    Args:
        file_list:          변환할 파일 경로 목록
        settings:           변환 설정 딕셔너리
        progress_callback:  (current, total, current_file) 시그니처 콜백
        control_callbacks:  {'check_stop': callable, 'check_pause': callable}

    Returns:
        {'success': [...], 'error': [...], 'skipped': [...], 'original_paths': [...]}
    """
    results: Dict = {'success': [], 'error': [], 'skipped': [], 'original_paths': []}
    total = len(file_list)

    use_mp      = settings.get('processing_settings', {}).get('multiprocessing_enabled', False)
    max_workers = settings.get('processing_settings', {}).get('max_workers', 1)

    logger.info(
        f'{total}개 파일 일괄 변환 시작 (멀티코어: {use_mp})',
        module='converter_engine',
    )

    def _is_stop() -> bool:
        return bool(
            control_callbacks
            and control_callbacks.get('check_stop')
            and control_callbacks['check_stop']()
        )

    def _wait_if_paused(pool=None) -> bool:
        """일시정지 동안 대기. 정지 신호가 오면 True 반환."""
        if not (control_callbacks and control_callbacks.get('check_pause')):
            return False
        while control_callbacks['check_pause']():
            time.sleep(0.5)
            if _is_stop():
                if pool:
                    pool.terminate()
                return True
        return False

    def _record(result: dict, idx: int) -> None:
        results[result['status']].append(result)
        if result['status'] == 'success':
            results['original_paths'].append(result['input'])
        if progress_callback:
            progress_callback(idx + 1, total, result.get('input', file_list[idx]))

    # ── 멀티프로세싱 ──────────────────────────────────────────────
    if use_mp and max_workers > 1:
        worker_args = [(f, settings) for f in file_list]
        with multiprocessing.Pool(processes=max_workers) as pool:
            for i, result in enumerate(pool.imap(_convert_image_worker, worker_args)):
                if _is_stop():
                    logger.info("사용자 요청으로 변환 중지 (워커 종료 중...)", module='converter_engine')
                    pool.terminate()
                    break
                if _wait_if_paused(pool):
                    break
                _record(result, i)

    # ── 순차 처리 ─────────────────────────────────────────────────
    else:
        for i, file_path in enumerate(file_list):
            if _is_stop():
                logger.info("사용자 요청으로 변환 중지", module='converter_engine')
                break
            if _wait_if_paused():
                break
            _record(convert_image(file_path, settings), i)

    logger.info("일괄 변환 완료", module='converter_engine')
    return results


# ---------------------------------------------------------------------------
# 플레이스홀더 (향후 구현 예정)
# ---------------------------------------------------------------------------

def estimate_processing_time(file_list: list, settings: dict) -> float:
    """파일 수 × 0.5 초로 단순 추정한다. (향후 정교화 예정)"""
    return len(file_list) * 0.5


def validate_conversion_settings(settings: dict) -> tuple[bool, list]:
    """변환 설정 유효성 검사. (향후 구현 예정)"""
    return True, []
