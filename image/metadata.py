"""
image/metadata.py

이미지 메타데이터 추출·보존과 LSB 스테가노그래피를 하나로 통합한 모듈.

원본 파일:
  metadata_utils.py  — EXIF / PNG텍스트 추출, AI 생성 도구 감지, 저장 옵션 준비
  stego_utils.py     — LSB 스테가노그래피 임베딩 / 추출 (stealth_pnginfo 방식)

두 파일이 항상 함께 쓰이고, stego_utils 는 metadata_utils 에서만 호출되므로 통합한다.
"""

from __future__ import annotations

import gzip
import struct
from typing import Dict, List, Optional, Tuple

import piexif
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from utils.logger import logger


# ---------------------------------------------------------------------------
# 스테가노그래피 — 시그니처 정의
# ---------------------------------------------------------------------------

#: 지원하는 stealth 시그니처 → 모드/압축 여부 매핑
STEALTH_SIGNATURES: dict[str, dict] = {
    'stealth_pnginfo': {'mode': 'alpha', 'compressed': False},
    'stealth_pngcomp': {'mode': 'alpha', 'compressed': True},
    'stealth_rgbinfo': {'mode': 'rgb',   'compressed': False},
    'stealth_rgbcomp': {'mode': 'rgb',   'compressed': True},
}


# ---------------------------------------------------------------------------
# 스테가노그래피 — 내부 헬퍼
# ---------------------------------------------------------------------------

def _compress(data: str) -> bytes:
    return gzip.compress(data.encode('utf-8'))


def _decompress(data: bytes) -> str:
    return gzip.decompress(data).decode('utf-8')


def _prepare_embed_bits(data: str, signature: str) -> str:
    """임베딩할 데이터를 바이너리 문자열로 변환한다."""
    is_compressed = STEALTH_SIGNATURES[signature]['compressed']
    payload = _compress(data) if is_compressed else data.encode('utf-8')

    # 시그니처를 16바이트로 패딩
    sig_bytes = signature.encode('utf-8').ljust(16, b'\x00')

    binary_sig     = ''.join(format(b, '08b') for b in sig_bytes)
    binary_payload = ''.join(format(b, '08b') for b in payload)
    binary_len     = format(len(binary_payload), '032b')

    return binary_sig + binary_len + binary_payload


# ---------------------------------------------------------------------------
# 스테가노그래피 — 공개 API
# ---------------------------------------------------------------------------

def embed_stealth_pnginfo(
    image: Image.Image,
    data: str,
    mode: str = 'alpha',
    compressed: bool = True,
) -> Image.Image:
    """
    LSB 스테가노그래피로 이미지에 데이터를 숨긴다.

    Args:
        image:      원본 PIL 이미지
        data:       숨길 문자열
        mode:       'alpha' (RGBA 알파 채널) | 'rgb' (RGB 최하위 비트)
        compressed: True 이면 gzip 압축 후 임베딩

    Returns:
        데이터가 삽입된 PIL 이미지
    """
    if mode == 'alpha' and image.mode != 'RGBA':
        image = image.convert('RGBA')
    elif mode == 'rgb' and image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGB')

    sig = f"stealth_{'png' if mode == 'alpha' else 'rgb'}{'comp' if compressed else 'info'}"
    bits = _prepare_embed_bits(data, sig)

    pixels = image.load()
    width, height = image.size
    idx = 0

    for y in range(height):
        for x in range(width):
            if idx >= len(bits):
                return image
            pixel = list(pixels[x, y])
            if mode == 'alpha':
                pixel[3] = (pixel[3] & ~1) | int(bits[idx])
                idx += 1
            else:
                for i in range(3):
                    if idx < len(bits):
                        pixel[i] = (pixel[i] & ~1) | int(bits[idx])
                        idx += 1
            pixels[x, y] = tuple(pixel)

    return image


def extract_stealth_pnginfo(image: Image.Image) -> Optional[dict]:
    """
    이미지에서 LSB 스테가노그래피 데이터를 추출한다.

    Returns:
        추출 성공 시 {'signature', 'mode', 'compressed', 'data'} 딕셔너리,
        데이터가 없거나 실패 시 None.
    """
    if image.mode not in ('RGB', 'RGBA'):
        return None

    width, height = image.size
    pixels = image.load()

    def _bit_gen(mode: str):
        if mode == 'alpha':
            for y in range(height):
                for x in range(width):
                    yield pixels[x, y][3] & 1
        else:
            for y in range(height):
                for x in range(width):
                    p = pixels[x, y]
                    for i in range(3):
                        yield p[i] & 1

    def _read_bytes(gen, n: int) -> bytearray:
        buf = bytearray()
        for _ in range(n):
            byte = 0
            for i in range(8):
                try:
                    byte = (byte << 1) | next(gen)
                except StopIteration:
                    return buf
            buf.append(byte)
        return buf

    def _try(mode: str) -> Optional[dict]:
        gen      = _bit_gen(mode)
        sig_raw  = _read_bytes(gen, 16)
        try:
            sig = sig_raw.decode('utf-8', errors='ignore').rstrip('\x00')
        except Exception:
            return None
        if sig not in STEALTH_SIGNATURES:
            return None
        if STEALTH_SIGNATURES[sig]['mode'] != mode:
            return None
        return _finish_extraction(gen, sig)

    # 알파 채널 우선 시도
    if image.mode == 'RGBA':
        result = _try('alpha')
        if result:
            return result

    return _try('rgb')


def _finish_extraction(bit_gen, signature: str) -> dict:
    """시그니처 이후 길이와 데이터를 읽어 딕셔너리로 반환한다."""
    # 32비트 길이 읽기
    length = 0
    for _ in range(32):
        try:
            length = (length << 1) | next(bit_gen)
        except StopIteration:
            break

    # 데이터 비트 읽기
    data_bits: list[int] = []
    for _ in range(length):
        try:
            data_bits.append(next(bit_gen))
        except StopIteration:
            break

    # 비트 → 바이트
    data_bytes = bytearray()
    for i in range(0, len(data_bits), 8):
        byte = 0
        for j in range(8):
            if i + j < len(data_bits):
                byte = (byte << 1) | data_bits[i + j]
        data_bytes.append(byte)

    is_compressed = STEALTH_SIGNATURES[signature]['compressed']
    try:
        data_str = _decompress(data_bytes) if is_compressed else data_bytes.decode('utf-8', errors='ignore')
    except Exception as e:
        data_str = f"Error decoding data: {e}"

    return {
        'signature':  signature,
        'mode':       STEALTH_SIGNATURES[signature]['mode'],
        'compressed': is_compressed,
        'data':       data_str,
    }


def detect_steganography_methods(image: Image.Image) -> List[str]:
    """이미지에 사용된 스테가노그래피 방식 목록을 반환한다."""
    methods: list[str] = []
    if extract_stealth_pnginfo(image) is not None:
        methods.append('stealth_pnginfo')
    return methods


# ---------------------------------------------------------------------------
# 메타데이터 — 상수
# ---------------------------------------------------------------------------

#: extract_all_metadata 반환 딕셔너리 기본 구조
METADATA_STRUCTURE: dict = {
    'source_info':        {},
    'standard_metadata':  {},
    'steganography_data': {},
    'ai_generation_info': {},
    'preservation_status': {},
}


# ---------------------------------------------------------------------------
# 메타데이터 — 추출
# ---------------------------------------------------------------------------

def extract_exif_data(image: Image.Image) -> Optional[dict]:
    """PIL 이미지 객체에서 EXIF 데이터를 추출한다."""
    try:
        raw = image.info.get('exif')
        if raw:
            return piexif.load(raw)
    except Exception:
        pass
    return None


def extract_png_text_chunks(image: Image.Image) -> dict:
    """PIL 이미지 객체에서 PNG tEXt/iTXt 청크를 추출한다."""
    if not hasattr(image, 'text') or not image.text:
        return {}
    return dict(image.text)


def extract_all_metadata(image_path: str) -> Optional[dict]:
    """
    이미지 파일에서 가능한 모든 메타데이터를 추출한다.

    Returns:
        메타데이터 딕셔너리, 파일 읽기 실패 시 None.
    """
    metadata: dict = {k: v.copy() if isinstance(v, dict) else v
                      for k, v in METADATA_STRUCTURE.items()}
    try:
        with Image.open(image_path) as img:
            metadata['source_info'] = {
                'file_path':  image_path,
                'format':     img.format,
                'dimensions': img.size,
                'color_mode': img.mode,
            }

            metadata['standard_metadata'] = {
                'exif':         extract_exif_data(img),
                'png_text':     extract_png_text_chunks(img),
                'xmp':          None,   # 미구현 (확장 예정)
                'iptc':         {},
                'jpeg_comment': img.info.get('comment', ''),
            }

            metadata['steganography_data'] = extract_stealth_pnginfo(img)

            raw_data, detected_tool = detect_ai_generator_type(metadata)
            metadata['ai_generation_info'] = {
                'detected_tool': detected_tool,
                'raw_data':      raw_data,
                'parameters':    parse_ai_parameters(raw_data, detected_tool),
            }

    except OSError as e:
        logger.error(f"메타데이터 추출 실패: {image_path} — {e}", module='metadata')
        return None

    return metadata


# ---------------------------------------------------------------------------
# 메타데이터 — AI 생성 도구 감지 / 파싱
# ---------------------------------------------------------------------------

def detect_ai_generator_type(metadata: dict) -> Tuple[Optional[str], str]:
    """
    메타데이터를 분석해 AI 생성 도구를 감지한다.

    Returns:
        (raw_data, detected_tool) 튜플.
        감지 실패 시 (None, 'unknown').
    """
    png_text = metadata.get('standard_metadata', {}).get('png_text', {})

    if 'parameters' in png_text:        # Stable Diffusion WebUI (A1111)
        return png_text['parameters'], 'webui'
    if 'prompt' in png_text:            # ComfyUI
        return png_text['prompt'], 'comfyui'

    stealth = metadata.get('steganography_data')
    if stealth and stealth.get('data'):
        return stealth['data'], 'stealth_pnginfo'

    return None, 'unknown'


def parse_ai_parameters(raw_data: Optional[str], generator_type: str) -> dict:
    """
    AI 생성 파라미터 문자열을 파싱한다.
    현재는 raw 문자열을 그대로 보존하며, 향후 포맷별 파서를 추가할 수 있다.
    """
    if not raw_data:
        return {}
    return {'raw': raw_data}


# ---------------------------------------------------------------------------
# 메타데이터 — 저장 옵션 준비
# ---------------------------------------------------------------------------

def prepare_save_options(
    source_metadata: dict,
    target_format: str,
    settings: dict,
) -> dict:
    """
    PIL Image.save() 에 전달할 메타데이터 관련 키워드 인자를 준비한다.

    Args:
        source_metadata: extract_all_metadata() 반환값
        target_format:   저장 포맷 문자열 (예: 'PNG', 'WEBP', 'JPG')
        settings:        converter metadata_settings 딕셔너리

    Returns:
        save() 에 전달할 kwargs 딕셔너리
    """
    save_opts: dict = {}
    if not source_metadata:
        return save_opts

    fmt = target_format.upper().replace('JPEG', 'JPG')
    standard = source_metadata.get('standard_metadata', {})

    # 1. EXIF 보존 (JPEG / WEBP / PNG 공통)
    exif_data = standard.get('exif')
    if exif_data and fmt in ('JPG', 'JPEG', 'WEBP', 'PNG'):
        try:
            save_opts['exif'] = piexif.dump(exif_data)
        except Exception:
            pass

    # 2. PNG 텍스트 청크 보존 (PNG 전용)
    if fmt == 'PNG':
        pnginfo   = PngInfo()
        png_text  = standard.get('png_text', {})
        for key, value in png_text.items():
            pnginfo.add_text(key, str(value))

        # stealth_pnginfo 원본이 있고 parameters 키가 없으면 추가
        ai_info = source_metadata.get('ai_generation_info', {})
        if ai_info.get('detected_tool') == 'stealth_pnginfo' and 'parameters' not in png_text:
            pnginfo.add_text('parameters', ai_info.get('raw_data', ''))

        save_opts['pnginfo'] = pnginfo

    return save_opts


# ---------------------------------------------------------------------------
# 유틸 (하위 호환 / 미래 확장용)
# ---------------------------------------------------------------------------

def calculate_preservation_compatibility(
    source_format: str,
    target_format: str,
) -> dict:
    """소스↔대상 포맷 간 메타데이터 보존 호환성을 반환한다. (확장 예정)"""
    return {'estimated_loss': 0.5}


def merge_metadata_sources(metadata_dict: dict) -> dict:
    """여러 소스에서 추출한 메타데이터를 병합한다. (확장 예정)"""
    return metadata_dict


def preserve_metadata_to_target(
    source_metadata: dict,
    target_image: Image.Image,
    target_format: str,
    settings: dict,
) -> bool:
    """(Deprecated) 대신 prepare_save_options()를 사용한다. 원본 호환성을 위해 유지."""
    return True


# ---------------------------------------------------------------------------
# 스테가노그래피 — 커스텀 방식 플레이스홀더 (원본 미구현, 향후 확장용)
# ---------------------------------------------------------------------------

def extract_custom_steganography(image: Image.Image, method: str) -> bytes:
    """커스텀 스테가노그래피 방식으로 데이터를 추출한다. (미구현 — 향후 확장용)"""
    print(f"Function 'extract_custom_steganography' for method {method} is not implemented yet.")
    return b''


def embed_custom_steganography(image: Image.Image, data: bytes, method: str) -> Image.Image:
    """커스텀 스테가노그래피 방식으로 데이터를 임베딩한다. (미구현 — 향후 확장용)"""
    print(f"Function 'embed_custom_steganography' for method {method} is not implemented yet.")
    return image


def verify_steganography_integrity(image: Image.Image, expected_data: str) -> bool:
    """스테가노그래피 데이터의 무결성을 검증한다. (미구현 — 향후 확장용)"""
    print("Function 'verify_steganography_integrity' is not implemented yet.")
    return False
