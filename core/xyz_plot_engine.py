"""
core/xyz_plot_engine.py

XY표 만들기 탭의 핵심 로직 모듈.
(원본 xyz_plot_engine.py 그대로 — 로직 변경 없음, 패키지 경로만 이동)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

# ------------------------------------------------------------------ #
#  상수
# ------------------------------------------------------------------ #

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff"}

CELL_TIGHT        = "tight"
CELL_LONGEST_EDGE = "longest_edge"

RESIZE_LARGEST  = "largest"
RESIZE_SMALLEST = "smallest"
RESIZE_CUSTOM   = "custom"

METHOD_CROP  = "crop"   # 중앙 크롭
METHOD_SCALE = "scale"  # 종횡비 유지 + 레터박스(배경 채움)

SORT_NAME_ASC  = "name_asc"
SORT_NAME_DESC = "name_desc"
SORT_DATE_ASC  = "date_asc"
SORT_DATE_DESC = "date_desc"
SORT_SIZE_ASC  = "size_asc"
SORT_SIZE_DESC = "size_desc"

FONT_AUTO   = "auto"
FONT_FIT    = "fit"
FONT_MANUAL = "manual"

AXIS_ROW = "row"
AXIS_COL = "col"

COLOR_BG       = (255, 255, 255)
COLOR_LABEL_BG = (240, 240, 240)
COLOR_TEXT     = (0, 0, 0)
COLOR_NOIMAGE  = (200, 200, 200)
COLOR_GRID     = (180, 180, 180)

NO_IMAGE_TEXT = "NO IMAGE"


# ------------------------------------------------------------------ #
#  데이터 클래스
# ------------------------------------------------------------------ #

@dataclass
class FolderEntry:
    folder_path: str
    label: str = ""


@dataclass
class XYPlotConfig:
    entries:          list[FolderEntry]  = field(default_factory=list)
    col_labels:       list[str]          = field(default_factory=list)
    row_labels_extra: list[str]          = field(default_factory=list)  # 격자에서 입력한 행 라벨
    grid_cols:        int                = 0   # 격자 지정 열 수 (0=자동)
    grid_rows:        int                = 0   # 격자 지정 행 수 (0=자동)
    fill_mode:        str                = "grid"  # "grid" | "data"
    folder_axis:      str                = AXIS_ROW
    sort_order:       str                = SORT_NAME_ASC
    cell_mode:        str                = CELL_TIGHT
    resize_base:      str                = RESIZE_LARGEST
    resize_custom_wh: tuple[int, int]    = (512, 512)
    resize_method:    str                = METHOD_SCALE
    title_enabled:    bool               = False
    title_text:       str                = ""
    title_fontsize:   Optional[int]      = None
    label_fontsize_mode: str             = FONT_AUTO
    label_fontsize:   Optional[int]      = None
    label_align_h:    str                = "center"
    label_align_v:    str                = "center"
    padding_enabled:  bool               = False
    padding_px:       int                = 4
    downscale_enabled: bool              = False
    downscale_ratio:  float              = 1.0
    save_path:        str                = ""
    save_format:      str                = "png"
    save_lossless:    bool               = True
    save_quality:     int                = 95


@dataclass
class BuildResult:
    success:    bool
    image:      Optional[Image.Image] = None  # 미리보기용 (축소 가능)
    full_image: Optional[Image.Image] = None  # 완성본 원본 (build_preview에서 보존)
    error_msg:  Optional[str]         = None


# ------------------------------------------------------------------ #
#  내부 헬퍼
# ------------------------------------------------------------------ #

def _load_font(size: int):
    candidates = [
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, max(6, size))
            except Exception:
                continue
    return ImageFont.load_default()


def _collect_images(folder: str, sort_order: str) -> list[Path]:
    p = Path(folder)
    if not p.is_dir():
        return []
    files = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
    rev = sort_order.endswith("_desc")
    if "name" in sort_order:
        files.sort(key=lambda f: f.name.lower(), reverse=rev)
    elif "date" in sort_order:
        files.sort(key=lambda f: f.stat().st_mtime, reverse=rev)
    elif "size" in sort_order:
        files.sort(key=lambda f: f.stat().st_size, reverse=rev)
    return files


def _resolve_label(entry: FolderEntry) -> str:
    return entry.label.strip() or Path(entry.folder_path).name


def _determine_cell_size(
    all_images: list[list[Optional[Image.Image]]],
    cell_mode: str,
    resize_base: str,
    resize_custom_wh: tuple[int, int],
) -> tuple[int, int]:
    """
    셀 크기(픽셀) 결정.
    - tight       : resize_base 기준 w×h 그대로
    - longest_edge: 전체 이미지 중 최장변을 한 변으로 하는 정사각형
    """
    flat = [img for row in all_images for img in row if img is not None]
    if not flat:
        return (512, 512)

    if resize_base == RESIZE_LARGEST:
        w = max(img.width  for img in flat)
        h = max(img.height for img in flat)
    elif resize_base == RESIZE_SMALLEST:
        w = min(img.width  for img in flat)
        h = min(img.height for img in flat)
    else:
        w, h = resize_custom_wh

    if cell_mode == CELL_LONGEST_EDGE:
        s = max(w, h)
        return (s, s)

    return (w, h)


def _fit_image(img: Image.Image, target_w: int, target_h: int, method: str) -> Image.Image:
    """
    METHOD_SCALE : 종횡비 유지, 긴 쪽을 target에 맞추고 빈 공간은 배경색으로 채움 (레터박스)
    METHOD_CROP  : 종횡비 유지로 셀을 꽉 채운 뒤 중앙 크롭
    """
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, COLOR_BG)
        bg.paste(img, mask=img.split()[3])
        src = bg
    else:
        src = img.convert("RGB")

    if method == METHOD_SCALE:
        # 종횡비 유지하며 target 안에 최대한 맞춤
        ratio = min(target_w / src.width, target_h / src.height)
        new_w = max(1, int(src.width  * ratio))
        new_h = max(1, int(src.height * ratio))
        resized = src.resize((new_w, new_h), Image.LANCZOS)
        canvas  = Image.new("RGB", (target_w, target_h), COLOR_BG)
        paste_x = (target_w - new_w) // 2
        paste_y = (target_h - new_h) // 2
        canvas.paste(resized, (paste_x, paste_y))
        return canvas

    else:  # METHOD_CROP
        ratio = max(target_w / src.width, target_h / src.height)
        new_w = max(1, int(src.width  * ratio))
        new_h = max(1, int(src.height * ratio))
        resized = src.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - target_w) // 2
        top  = (new_h - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))


def _draw_text_in_box(draw, text, box, font, align_h="center", align_v="center", color=COLOR_TEXT):
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1

    # 초기 폰트 크기 추정: ImageFont는 size 속성이 없으므로 외부에서 추적
    # font가 _load_font로 만들어졌다고 가정하고, 실제 렌더 크기로 이진탐색
    # 먼저 현재 폰트로 시도하고, 넘치면 크기를 줄여 재시도
    def _measure(f):
        bb = draw.textbbox((0, 0), text, font=f)
        return bb[2] - bb[0], bb[3] - bb[1], bb

    tw, th, bbox = _measure(font)
    current_font = font

    if tw > bw - 8 or th > bh - 4:
        # 이진탐색으로 박스에 맞는 최대 폰트 크기 찾기
        lo, hi = 6, 200
        best_size = 6
        while lo <= hi:
            mid = (lo + hi) // 2
            f = _load_font(mid)
            w, h, _ = _measure(f)
            if w <= bw - 8 and h <= bh - 4:
                best_size = mid
                lo = mid + 1
            else:
                hi = mid - 1
        current_font = _load_font(best_size)
        tw, th, bbox = _measure(current_font)

    if align_h == "left":
        tx = x1 + 4 - bbox[0]
    elif align_h == "right":
        tx = x2 - tw - 4 - bbox[0]
    else:
        tx = x1 + (bw - tw) // 2 - bbox[0]

    if align_v == "top":
        ty = y1 + 4 - bbox[1]
    elif align_v == "bottom":
        ty = y2 - th - 4 - bbox[1]
    else:
        ty = y1 + (bh - th) // 2 - bbox[1]

    draw.text((tx, ty), text, font=current_font, fill=color)


def _calc_fit_fontsize(text: str, box_w: int, box_h: int) -> int:
    """박스에 꽉 차는 최대 폰트 크기를 이진탐색으로 계산."""
    lo, hi, best = 6, min(box_h, 300), 6
    tmp_img = Image.new("RGB", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)
    while lo <= hi:
        mid  = (lo + hi) // 2
        font = _load_font(mid)
        bbox = tmp_draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= box_w and (bbox[3] - bbox[1]) <= box_h:
            best = mid
            lo   = mid + 1
        else:
            hi   = mid - 1
    return best


# ------------------------------------------------------------------ #
#  공개 함수
# ------------------------------------------------------------------ #

def collect_images(folder_path: str, sort_order: str) -> list:
    """단일 폴더의 이미지 Path 목록을 반환합니다. (_collect_images 공개 래퍼)"""
    return _collect_images(folder_path, sort_order)


def collect_folder_images(entries, sort_order):
    result = []
    for entry in entries:
        paths = _collect_images(entry.folder_path, sort_order)
        images = []
        for p in paths:
            try:
                img = Image.open(p)
                if img.mode == "RGBA":
                    bg = Image.new("RGB", img.size, COLOR_BG)
                    bg.paste(img, mask=img.split()[3])
                    images.append(bg)
                else:
                    images.append(img.convert("RGB"))
            except Exception:
                images.append(None)
        result.append(images)
    return result


def build_plot(
    config: XYPlotConfig,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> BuildResult:
    try:
        entries    = config.entries
        col_labels = list(config.col_labels)

        if not entries:
            return BuildResult(success=False, error_msg="폴더가 지정되지 않았습니다.")

        row_labels = [_resolve_label(e) for e in entries]
        # 격자에서 입력한 추가 행 라벨로 보완 (폴더 없는 행 포함)
        for i, extra in enumerate(config.row_labels_extra):
            if extra.strip():
                if i < len(row_labels):
                    row_labels[i] = extra.strip()
                else:
                    row_labels.append(extra.strip())

        # ── 이미지 수집 ───────────────────────────────────────────
        raw_images = collect_folder_images(entries, config.sort_order)
        n_img_cols = max((len(imgs) for imgs in raw_images), default=0)
        if n_img_cols == 0:
            return BuildResult(success=False, error_msg="이미지를 찾을 수 없습니다.")

        # ── fill_mode 에 따른 표 크기 결정 ───────────────────────
        # "grid" (격자 우선): grid_rows × grid_cols를 표 크기로 고정.
        #                     부족한 칸은 NO IMAGE로 채움.
        # "data" (데이터 우선): 실제 폴더 수와 최대 이미지 수로 표 크기 결정.
        #                     grid_rows/grid_cols가 0이 아닐 때도 데이터 기준 사용.
        #                     단, 폴더마다 이미지 수가 달라 발생하는 NO IMAGE는 정상.
        if config.fill_mode == "data":
            # 데이터 기준: 폴더 수 × 최대 이미지 수
            data_n_folders = len(entries)
            data_n_images  = n_img_cols
        else:
            # 격자 기준: grid_* > 0이면 고정, 아니면 데이터 수 사용
            data_n_folders = config.grid_rows if config.grid_rows > 0 else len(entries)
            data_n_images  = config.grid_cols if config.grid_cols > 0 else n_img_cols

        # col_labels 길이 맞추기
        fixed_cols = data_n_images
        while len(col_labels) < fixed_cols:
            col_labels.append("")

        # ── folder_axis 반영 ──────────────────────────────────────
        if config.folder_axis == AXIS_COL:
            n_data_rows = fixed_cols
            n_data_cols = data_n_folders
            data_grid = [
                [raw_images[ci][ri] if ci < len(raw_images) and ri < len(raw_images[ci]) else None
                 for ci in range(n_data_cols)]
                for ri in range(n_data_rows)
            ]
            x_labels = (row_labels + [""] * n_data_cols)[:n_data_cols]
            y_labels = col_labels[:n_data_rows]
        else:
            n_data_rows = data_n_folders
            n_data_cols = fixed_cols
            data_grid = [
                [raw_images[ri][ci] if ri < len(raw_images) and ci < len(raw_images[ri]) else None
                 for ci in range(n_data_cols)]
                for ri in range(n_data_rows)
            ]
            x_labels = col_labels[:n_data_cols]
            y_labels = (row_labels + [""] * n_data_rows)[:n_data_rows]

        # ── 셀 크기 결정 ──────────────────────────────────────────
        cell_w, cell_h = _determine_cell_size(
            data_grid,
            config.cell_mode,
            config.resize_base,
            config.resize_custom_wh,
        )

        # ── 스케일조절 ────────────────────────────────────────────
        scale = config.downscale_ratio if config.downscale_enabled else 1.0
        if scale != 1.0:
            cell_w = max(1, int(cell_w * scale))
            cell_h = max(1, int(cell_h * scale))

        pad = config.padding_px if config.padding_enabled else 0

        # ── 치수 계산 ─────────────────────────────────────────────
        label_col_w = cell_w
        label_row_h = max(24, cell_h // 2)
        title_h     = cell_h if config.title_enabled else 0

        n_rows = n_data_rows
        n_cols = n_data_cols

        total_w = pad + label_col_w + pad + (cell_w + pad) * n_cols
        total_h = ((title_h + pad) if config.title_enabled else 0) + \
                  pad + label_row_h + pad + (cell_h + pad) * n_rows

        canvas = Image.new("RGB", (total_w, total_h), COLOR_BG)
        draw   = ImageDraw.Draw(canvas)

        # ── 폰트 크기 결정 ────────────────────────────────────────
        # cell_w/h는 이미 scale이 반영된 값이므로 폰트 계산에 scale을 추가로 곱하지 않음
        if config.label_fontsize_mode == FONT_MANUAL and config.label_fontsize:
            # MANUAL만 원본 pt값에 scale을 곱해서 출력 크기에 맞춤
            lbl_fontsize = max(6, int(config.label_fontsize * scale))
        elif config.label_fontsize_mode == FONT_FIT:
            all_labels = x_labels + y_labels
            longest    = max(all_labels, key=len) if all_labels else "W"
            # 더 작은 공간인 열 라벨 행(label_row_h) 기준으로 계산
            lbl_fontsize = _calc_fit_fontsize(longest, label_col_w - 8, label_row_h - 8)
        else:  # FONT_AUTO
            # label_row_h의 60% 기준, 최소 10
            lbl_fontsize = max(10, int(label_row_h * 0.6))

        lbl_font = _load_font(lbl_fontsize)

        # ── 제목 폰트 ─────────────────────────────────────────────
        title_font = None
        if config.title_enabled:
            if config.title_fontsize:
                # MANUAL: 원본 pt값에 scale 적용
                t_fs = max(6, int(config.title_fontsize * scale))
            else:
                # AUTO: title_h(이미 scale 반영)의 55% 기준
                t_fs = max(10, int(title_h * 0.55))
            title_font = _load_font(t_fs)

        # ── 제목 ──────────────────────────────────────────────────
        y_cursor = 0
        if config.title_enabled and title_font:
            _draw_text_in_box(
                draw, config.title_text,
                (0, y_cursor, total_w, y_cursor + title_h),
                title_font, "center", "center",
            )
            y_cursor += title_h + pad

        # ── 첫 행(열 라벨) ────────────────────────────────────────
        y_cursor += pad
        # 1행1열 빈칸
        draw.rectangle(
            [(pad, y_cursor), (pad + label_col_w - 1, y_cursor + label_row_h - 1)],
            fill=COLOR_LABEL_BG,
        )
        for ci, lbl in enumerate(x_labels):
            x1 = pad + label_col_w + pad + ci * (cell_w + pad)
            x2 = x1 + cell_w
            draw.rectangle([(x1, y_cursor), (x2 - 1, y_cursor + label_row_h - 1)],
                           fill=COLOR_LABEL_BG)
            _draw_text_in_box(
                draw, lbl,
                (x1, y_cursor, x2, y_cursor + label_row_h),
                lbl_font, config.label_align_h, config.label_align_v,
            )
        y_cursor += label_row_h + pad

        # ── 데이터 행 ─────────────────────────────────────────────
        total_cells = n_rows * n_cols
        done_cells  = 0

        for ri in range(n_rows):
            x1 = pad
            x2 = x1 + label_col_w
            draw.rectangle([(x1, y_cursor), (x2 - 1, y_cursor + cell_h - 1)],
                           fill=COLOR_LABEL_BG)
            row_lbl = y_labels[ri] if ri < len(y_labels) else ""
            _draw_text_in_box(
                draw, row_lbl,
                (x1, y_cursor, x2, y_cursor + cell_h),
                lbl_font, config.label_align_h, config.label_align_v,
            )

            for ci in range(n_cols):
                cx1 = pad + label_col_w + pad + ci * (cell_w + pad)
                cx2 = cx1 + cell_w
                cy1 = y_cursor
                cy2 = cy1 + cell_h

                img = (data_grid[ri][ci]
                       if ri < len(data_grid) and ci < len(data_grid[ri])
                       else None)

                if img is None:
                    draw.rectangle([(cx1, cy1), (cx2 - 1, cy2 - 1)], fill=COLOR_NOIMAGE)
                    ni_fs   = _calc_fit_fontsize(NO_IMAGE_TEXT, cell_w - 8, cell_h - 8)
                    ni_font = _load_font(ni_fs)
                    _draw_text_in_box(draw, NO_IMAGE_TEXT,
                                      (cx1, cy1, cx2, cy2), ni_font)
                else:
                    fitted = _fit_image(img, cell_w, cell_h, config.resize_method)
                    canvas.paste(fitted, (cx1, cy1))

                done_cells += 1
                if progress_callback:
                    progress_callback(done_cells, total_cells)

            y_cursor += cell_h + pad

        # ── 격자선 (패딩 없을 때만) ───────────────────────────────
        if not config.padding_enabled:
            _draw_grid(draw, total_w, total_h,
                       label_col_w, label_row_h, cell_w, cell_h,
                       n_rows, n_cols,
                       title_h if config.title_enabled else 0,
                       pad)

        return BuildResult(success=True, image=canvas)

    except Exception as e:
        import traceback
        return BuildResult(success=False, error_msg=traceback.format_exc())


def build_preview(config: XYPlotConfig) -> BuildResult:
    """
    완성본을 먼저 만든 뒤 썸네일로 축소해서 반환.
    이 방식이 레이아웃/폰트 계산을 재사용하므로 실제 결과와 정확히 일치함.
    full_image에 원본을 보존하여 미리보기 창에서 재렌더링 없이 바로 저장 가능.
    """
    result = build_plot(config)
    if not result.success:
        return result

    full_img = result.image
    MAX_PX = 1600  # 미리보기 최대 긴 변
    longest = max(full_img.width, full_img.height)
    if longest > MAX_PX:
        ratio   = MAX_PX / longest
        new_w   = max(1, int(full_img.width  * ratio))
        new_h   = max(1, int(full_img.height * ratio))
        preview = full_img.resize((new_w, new_h), Image.LANCZOS)
    else:
        preview = full_img

    return BuildResult(success=True, image=preview, full_image=full_img)


def save_image(image: Image.Image, config: XYPlotConfig) -> tuple[bool, str]:
    try:
        fmt  = config.save_format.lower()
        path = config.save_path

        if not path:
            return False, "저장 경로가 지정되지 않았습니다."

        save_kwargs: dict = {}

        if fmt in ("jpg", "jpeg"):
            image = image.convert("RGB")
            save_kwargs = {"format": "JPEG", "quality": config.save_quality}
            if not path.lower().endswith((".jpg", ".jpeg")):
                path += ".jpg"
        elif fmt == "webp":
            save_kwargs = {"format": "WEBP", "lossless": config.save_lossless}
            if not config.save_lossless:
                save_kwargs["quality"] = config.save_quality
            if not path.lower().endswith(".webp"):
                path += ".webp"
        else:
            image = image.convert("RGB")
            save_kwargs = {"format": "PNG"}
            if not path.lower().endswith(".png"):
                path += ".png"

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        image.save(path, **save_kwargs)
        return True, f"저장 완료: {path}"

    except Exception as e:
        return False, f"저장 실패: {e}"


def save_preview_image(image: Image.Image, path: str) -> tuple[bool, str]:
    try:
        if not path.lower().endswith((".jpg", ".jpeg")):
            path += ".jpg"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        image.convert("RGB").save(path, format="JPEG", quality=70)
        return True, f"미리보기 저장 완료: {path}"
    except Exception as e:
        return False, f"미리보기 저장 실패: {e}"


def _draw_grid(draw, total_w, total_h, label_col_w, label_row_h,
               cell_w, cell_h, n_rows, n_cols, title_h, pad=0):
    # 실제 드로잉 기준점 계산 (pad 포함)
    origin_x = pad
    origin_y = (title_h + pad if title_h > 0 else 0) + pad

    # 수평선: 라벨행 아래 경계부터 각 데이터행 아래 경계까지
    y = origin_y + label_row_h
    draw.line([(0, y), (total_w, y)], fill=COLOR_GRID, width=1)
    for _ in range(n_rows):
        y += cell_h + pad
        draw.line([(0, y), (total_w, y)], fill=COLOR_GRID, width=1)

    # 수직선: 라벨열 오른쪽 경계부터 각 데이터열 오른쪽 경계까지
    x = origin_x + label_col_w
    draw.line([(x, title_h), (x, total_h)], fill=COLOR_GRID, width=1)
    for _ in range(n_cols):
        x += cell_w + pad
        draw.line([(x, title_h), (x, total_h)], fill=COLOR_GRID, width=1)
