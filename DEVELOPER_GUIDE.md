# Dataset-Helper-v2 - 개발자 가이드 (Developer Guide)

본 문서는 **Dataset-Helper-v2** 프로젝트의 아키텍처, 패키지 구조, 모듈 간 연결 관계를 설명하여 개발 및 유지보수를 돕기 위해 작성되었습니다.

---

## 0. 이 문서 사용법 — 무엇을 고치기 전에 먼저 여기를 보세요

파일 수가 많아 한 번에 전체 코드를 보기 어려우므로, **문제·개선·기능 추가 요청이 들어오면 코드를 열기 전에 먼저 이 문서에서 해당 영역을 찾고, 거기 적힌 "함께 확인할 파일" 목록만 열어보는 것**을 권장합니다.

| 요청 유형 | 먼저 볼 절 | 그 다음 열어볼 파일 |
|:---|:---|:---|
| 특정 탭의 버그/동작 이상 | §5 또는 §6.4의 해당 탭 행 | "연관 core/image 모듈" 컬럼에 적힌 파일 |
| "버튼을 눌러도 멈춘다 / 반응이 없다" | §7.4 (워커 안전망) | 그 탭의 `_XxxWorker` 클래스가 `SafeWorker`를 상속하는지 먼저 확인 |
| 화면이 깨지거나 정렬이 안 된다 | §7.1 (숫자 정렬), §6.3 (공용 위젯) | `NumericTreeItem`/`ZoomPanLabel`/`ImageViewerDialog` 사용 여부 |
| 새 탭 추가 | §2.5 (의존성 매트릭스), §6.4 | 기존 탭 중 가장 비슷한 것을 템플릿으로 삼기 |
| 새 설정 항목 추가 | §6.1 (`get_settings`/`load_settings` 계약) | `utils/settings.py`의 스키마도 함께 갱신 필요한지 확인 |
| XY표 만들기 관련 모든 것 | §7.2 — **반드시 먼저 읽기** | 사양을 임의로 바꾼 회귀가 두 번 있었던 영역 |
| exe 패키징/경로 문제 | §7.3 | `utils/settings.py` |
| "원본(v1.x tkinter)과 다르게 동작한다" | §2.5, 해당 모듈 행의 "원본 대응 파일" | 표에 적힌 원본 파일명으로 직접 대조 |
| 색상/팔레트를 바꾸고 싶다 | §6.2 | `ui/themes.py`의 `LIGHT_THEME`/`DARK_THEME` 딕셔너리만 수정 |
| "테마를 바꿔도 특정 영역만 색이 안 바뀐다" | §7.6 — **반드시 먼저 읽기** | 인라인 `setStyleSheet()`을 쓰면서 리스너 등록을 빠뜨린 위젯일 가능성이 높음 |

---

## 1. 아키텍처 개요 (Architecture Overview)

이 프로젝트는 **Python**과 **PySide6(Qt6)** 를 기반으로 한 GUI 애플리케이션입니다.

v1.x의 tkinter 기반 평탄(flat) 파일 구조에서, v2.0.0부터 **역할별 패키지 분리 구조**로 전면 리팩토링되었습니다.

### 핵심 디자인 패턴

- **UI / 로직 완전 분리:** `ui/` 패키지는 화면 표시와 사용자 입력만 담당하고, 실제 파일 처리·알고리즘은 `core/`, `image/` 패키지의 UI 비의존 모듈이 전담합니다. `core`·`image` 패키지는 PySide6를 import하지 않으므로, 향후 CLI나 다른 GUI 프레임워크로도 재사용할 수 있습니다.
  - **이 원칙은 한 번 깨진 적이 있습니다.** `analyzer_tab.py`에 파일시스템 조작 함수 5개가 직접 들어가 있던 회귀가 있었고, `core/dataset_analyzer.py`의 `BatchMover` 클래스로 이동시켜 복구했습니다(§5-F, §7.5 참고). 새 탭을 만들 때 "이 로직에 QWidget/QMessageBox가 전혀 안 보이는데도 탭 파일에 쓰고 있지 않은가"를 항상 점검하십시오.
- **스레드 분리 + 예외 안전망:** 시간이 걸리는 모든 작업은 `ui/widgets/worker_base.py`의 **`SafeWorker`**(`QThread` 서브클래스)로 분리되어 백그라운드에서 실행됩니다. `run()`을 직접 오버라이드하지 않고 `work()`를 오버라이드하면, 내부에서 예외가 나도 `error` 시그널이 자동으로 emit되어 UI가 영구히 멈추지 않습니다. **새 워커를 만들 때는 반드시 `SafeWorker`를 상속**하십시오(§7.4).
- **경로 중앙 관리 (exe 패키징 대응):** `utils/settings.py`의 `APP_DIR`이 "exe가 실행되는 폴더"와 "소스로 직접 실행할 때의 프로젝트 루트"를 동일한 방식으로 계산합니다. 설정·로그·실행취소·스냅샷 등 런타임에 생성되는 모든 파일이 **소스 실행과 exe 실행에서 완전히 동일하게 동작**합니다. 해당 폴더들은 소스에 포함되어 있지 않고, 앱 시작 시 `ensure_data_dirs()`가 없으면 자동으로 생성합니다.
- **테마 중앙화:** 색상·폰트·크기 등 모든 디자인 토큰은 `ui/constants.py` 한 곳에만 정의되어 있고, `ui/styles/theme.py`가 이를 QSS 문자열로 변환해 앱 전체에 한 번에 적용합니다. 개별 위젯에서 `setStyleSheet()`을 산발적으로 호출하지 않습니다.
- **공용 위젯 재사용 (DRY):** 줌/팬 캔버스, 이미지 뷰어, 숫자 정렬 트리 항목처럼 두 곳 이상에서 필요한 UI 패턴은 `ui/widgets/`에 한 번만 구현하고 각 탭에서 import해 씁니다. 이 원칙도 한 번 깨져서(`xy_plot_tab.py`와 `search_filter_tab.py`에 거의 동일한 코드가 복제) §6.3의 4종 위젯으로 통합한 적이 있습니다. **줌/팬, 이미지 뷰어, 정렬 가능한 트리가 필요하면 새로 만들지 말고 먼저 `ui/widgets/`를 확인**하십시오.
- **설정 관리:** 사용자 설정은 JSON으로 영구 저장·로드됩니다. 메인 윈도우 및 8개 탭의 UI 상태는 `data/config/app_settings.json`에, 이미지 변환기 전용 설정은 `data/config/converter_config.json`에 별도로 저장됩니다.

---

## 2. 폴더 구조 (Folder Structure)

```
Dataset-Helper-v2/
├── main.py                    # 진입점 — QApplication 생성, 테마 적용, MainWindow 실행
├── requirements.txt
├── README.md
├── DEVELOPER_GUIDE.md
├── LICENSE
│
├── core/                      # 비즈니스 로직 (PySide6 비의존)
│   ├── tag_processor.py
│   ├── rename_processor.py
│   ├── file_manager.py
│   ├── search_filter.py
│   ├── duplicate_finder.py
│   ├── dataset_analyzer.py    # DatasetAnalyzer, DatasetSnapshot, BatchMover, format_size_generic
│   └── xyz_plot_engine.py
│
├── image/                     # 이미지 변환·메타데이터 전용 로직
│   ├── converter_engine.py
│   ├── file_utils.py
│   └── metadata.py
│
├── ui/                        # PySide6 UI 전체
│   ├── constants.py           # 폰트·크기 등 정적 상수 + 테마 전환 시스템 (set_theme, current_colors, 리스너)
│   ├── themes.py              # LIGHT_THEME / DARK_THEME 색상 팔레트 정의
│   ├── main_window.py         # 메인 윈도우 (탭 컨테이너, 공통 툴바, 테마 토글 버튼)
│   ├── styles/
│   │   └── theme.py           # 팔레트(dict) → QSS 변환 (build_qss(theme))
│   ├── widgets/                # 여러 탭에서 공유하는 커스텀 위젯 (새 UI 만들기 전 항상 먼저 확인)
│   │   ├── folder_selector.py     — FolderSelector
│   │   ├── log_widget.py          — LogWidget
│   │   ├── worker_base.py         — SafeWorker
│   │   ├── zoom_pan_label.py      — ZoomPanLabel
│   │   ├── image_viewer.py        — ImageViewerDialog, pil_to_qimage, apply_exif_orientation
│   │   └── numeric_tree_item.py   — NumericTreeItem, SORT_KEY_ROLE
│   └── tabs/                   # 탭 8개, 각각 하나의 QWidget
│       ├── rename_tab.py
│       ├── single_file_tab.py
│       ├── tag_tab.py
│       ├── converter_tab.py
│       ├── duplicate_tab.py
│       ├── analyzer_tab.py
│       ├── search_filter_tab.py
│       └── xy_plot_tab.py
│
├── utils/                      # 공용 유틸리티
│   ├── common.py               # 상수, 파일 판별, 파일 쌍 탐색, 포맷 유틸
│   ├── logger.py               # 앱 전역 로거 싱글톤
│   └── settings.py             # 경로 관리(APP_DIR 등) + 설정 저장/로드
│
└── data/                       # 실행 중 자동 생성 (소스에는 존재하지 않음)
    ├── config/                 # app_settings.json, converter_config.json
    ├── logs/                   # app_YYYY-MM-DD.log
    ├── undo/                   # 이름변경·태그처리 실행취소 JSON
    └── snapshots/               # 데이터셋 스냅샷 JSON
```

### 패키지 간 의존 방향

```
ui/tabs/*  --depends on-->  core/*, image/*, utils/*, ui/widgets/*
ui/widgets --depends on-->  ui/constants
ui/styles  --depends on-->  ui/constants
core/*     --depends on-->  utils/common, utils/settings   (PySide6 import 없음)
image/*    --depends on-->  utils/logger                   (PySide6 import 없음)
```

`core`와 `image`는 어디서도 `ui`를 import하지 않습니다. 이 방향을 반대로 만드는 변경(예: `core` 모듈에서 `QMessageBox` 호출)은 아키텍처 원칙을 깨므로 지양합니다.

---

## 2.5 파일 종속성 매트릭스 — 무엇을 고치면 무엇을 같이 봐야 하는가

이 표는 "탭 파일 하나"가 실제로 의존하는 모든 파일을 나열합니다. 버그 수정이나 기능 추가 시, 해당 행에 적힌 파일을 전부 열어 영향 범위를 먼저 가늠하십시오. "원본 대응 파일"은 v1.x(tkinter) 시절 파일명으로, "원본과 다르게 동작한다"는 보고가 들어왔을 때 대조용으로 씁니다.

| 탭 (`ui/tabs/`) | 직접 의존하는 `core`/`image` 모듈 | 사용하는 공용 위젯 (`ui/widgets/`) | 원본 대응 파일(v1.x) |
|:---|:---|:---|:---|
| `rename_tab.py` | `core/rename_processor.py` | `LogWidget` | `rename_processor.py` + `main.py`의 `create_rename_tab` |
| `single_file_tab.py` | `core/file_manager.py` | `LogWidget` | `file_manager.py` + `main.py`의 `create_find_single_tab` |
| `tag_tab.py` | `core/tag_processor.py`, `utils/common.py`(`get_paired_files`) | `LogWidget` | `tag_processor.py` + `main.py`의 태그 탭 섹션 |
| `converter_tab.py` | `image/converter_engine.py`, `image/file_utils.py`, `image/metadata.py`(간접, engine 경유), `utils/settings.py`(변환기 설정), `utils/common.py`(`RateLimiter`, `format_file_size`), `utils/logger.py` | `LogWidget`, `FolderSelector` | `image_converter_tab.py`, `image_converter_engine.py`, `image_settings.py`, `metadata_utils.py`, `stego_utils.py` |
| `duplicate_tab.py` | `core/duplicate_finder.py` | `FolderSelector` | `duplicate_finder_tab.py`, `duplicate_finder.py` |
| `analyzer_tab.py` | `core/dataset_analyzer.py`(`DatasetAnalyzer`, `DatasetSnapshot`, `BatchMover`, `format_size_generic`) | `FolderSelector`, `SafeWorker`, `NumericTreeItem` | `dataset_analyzer_tab.py`, `dataset_analyzer.py` (특히 `BatchMoveWindow`, `SnapshotWindow` 클래스) |
| `search_filter_tab.py` | `core/search_filter.py` | `FolderSelector`, `SafeWorker`, `ImageViewerDialog`, `NumericTreeItem` | `search_filter_tab.py`, `search_filter.py` (특히 `ImageViewerWindow`, `_show_result_log`) |
| `xy_plot_tab.py` | `core/xyz_plot_engine.py` (행/열 결정 로직은 임의로 손대지 말 것 — §7.2) | `SafeWorker`, `ImageViewerDialog`(상속해 `_XYPreviewDialog`로 확장) | `xyz_plot_tab.py`, `xyz_plot_engine.py` |

### 공용 위젯 역방향 참조 — 이 위젯을 고치면 영향받는 탭

```
ui/widgets/worker_base.py (SafeWorker)
    사용처: 8개 탭의 워커 11개 전부 (rename_tab x2, single_file_tab, tag_tab,
            converter_tab, duplicate_tab, analyzer_tab x2, search_filter_tab,
            xy_plot_tab x2). 시그니처(work() 오버라이드, error 시그널)를 바꾸면
            11곳 전부 영향을 받는다.

ui/widgets/zoom_pan_label.py (ZoomPanLabel)
    사용처: ui/widgets/image_viewer.py 내부에서만 직접 사용.
            (탭에서 직접 쓰지 않고 항상 ImageViewerDialog를 경유)

ui/widgets/image_viewer.py (ImageViewerDialog)
    사용처: search_filter_tab.py (이미지 클릭 시 뷰어),
            xy_plot_tab.py (_XYPreviewDialog가 이 클래스를 상속해
            '미리보기 저장'/'완성본 저장' 버튼을 추가)
    이 클래스의 _zoom_by()/_render() 좌표 계산을 바꾸면 두 탭 모두 영향받는다.

ui/widgets/numeric_tree_item.py (NumericTreeItem, SORT_KEY_ROLE)
    사용처: analyzer_tab.py (결과 테이블 8개 컬럼 중 7개),
            search_filter_tab.py (용량·해상도 컬럼)
    새 트리 컬럼에 숫자 정렬을 추가하려면:
       1) NumericTreeItem으로 행 생성
       2) item.setData(컬럼인덱스, SORT_KEY_ROLE, float(값))
       3) tree.setSortingEnabled(True) 호출 잊지 말 것
          (이게 빠지면 인디케이터만 보이고 정렬이 동작하지 않는다 — §7.1 참고)
```

### 설정 스키마를 바꿀 때 같이 확인할 파일

탭에 새 옵션을 추가하면 다음 3곳이 항상 같이 바뀌어야 합니다 (하나라도 빠지면 "설정이 저장은 되는데 재시작하면 초기화된다" 류의 버그가 생깁니다).

1. 탭 파일의 `get_settings()` — 새 키 추가
2. 같은 탭 파일의 `load_settings()` — 같은 키를 읽어 위젯에 반영
3. (이미지 변환기 옵션인 경우만) `utils/settings.py`의 `_default_converter_settings()` — 기본값 추가

---

## 3. 공통 유틸리티 (`utils/`)

| 파일 | 역할 |
|:---|:---|
| `common.py` | `IMAGE_EXTENSIONS`, `TEXT_EXTENSION`, `PERSON_COUNT_TAGS` 상수. `is_image_file`/`is_text_file` 판별. `get_paired_files`(이미지-텍스트 쌍 탐색). `process_with_multicore`(멀티프로세싱 래퍼). `format_number`/`format_file_size`/`calculate_progress`/`estimate_remaining_time` 포맷 헬퍼. `RateLimiter`(GUI 업데이트 과다 방지). |
| `logger.py` | `AppLogger` 클래스와 전역 싱글톤 `logger`. 콘솔·파일 핸들러는 `setup()`으로 한 번만 초기화하며, PySide6 GUI 핸들러는 `add_gui_handler()`로 UI 레이어에서 동적으로 붙인다(로거 자체는 Qt를 import하지 않음). |
| `settings.py` | 경로: `APP_DIR`(실행 기준 폴더), `DATA_DIR`, `LOG_DIR`, `UNDO_DIR`과 `ensure_data_dirs()`. 설정: `load_app_settings`/`save_app_settings`(메인 윈도우+8개 탭 통합 설정), `load_converter_settings`/`save_converter_settings`(이미지 변환기 전용, 기본값은 `_default_converter_settings()`). |

`APP_DIR`은 `sys.frozen`(PyInstaller exe 여부)을 확인해 결정되므로, 이 파일을 수정할 때는 항상 두 환경(소스 실행 / exe 실행) 모두에서 같은 동작을 하는지 확인해야 합니다.

---

## 4. 이미지 처리 모듈 (`image/`)

| 파일 | 역할 |
|:---|:---|
| `converter_engine.py` | 단일 이미지 변환(`convert_image`), 배치 변환(`batch_convert_images` — 순차/멀티프로세싱 분기, 일시정지·중지 콜백 지원), EXIF 방향 보정(`orient_image`), 품질·리사이즈 적용. `prepare_image_for_format`은 JPEG가 지원하지 않는 알파 채널을 흰 배경으로 합성하고, 리사이즈 결과가 0px이 되지 않게 보장한다. `batch_convert_images`의 반환값 `original_paths` 키는 변환 후 원본 삭제 기능과 연동된다. |
| `file_utils.py` | 디렉터리 스캔(`scan_directory`), 출력 경로 생성(`generate_output_filename`/`generate_output_filename_to_input`), 파일 충돌 처리(`handle_file_conflicts`/`handle_file_conflicts_for_input` — skip/overwrite/rename 3종), 파일 정보·백업(`get_file_info`/`create_backup`). `scan_directory`는 설정·호출부 호환을 위해 `png`와 `.png` 형식을 모두 허용한다. |
| `metadata.py` | v1.x의 `metadata_utils.py` + `stego_utils.py`를 통합한 모듈. EXIF/PNG텍스트 추출(`extract_exif_data`/`extract_png_text_chunks`/`extract_all_metadata`), AI 생성 도구 감지(`detect_ai_generator_type`), 저장 옵션 준비(`prepare_save_options`), LSB 스테가노그래피 임베딩·추출(`embed_stealth_pnginfo`/`extract_stealth_pnginfo`, `STEALTH_SIGNATURES` 4종: alpha/rgb x 압축유무). |

이미지 변환기 전용 설정 스키마는 더 이상 `image/`에 있지 않고 `utils/settings.py`의 `_default_converter_settings()`로 통합되어 있다.

> 주의 (converter_tab.py와 연동): `prepare_save_options()`는 `settings` 인자를 받지만 EXIF/PNG텍스트/스테가노그래피를 개별로 끄는 기능은 구현되어 있지 않다(원본 v1.x부터 그랬다). `converter_tab.py`의 메타데이터 설정 UI는 이 사실에 맞춰 단일 체크박스(보존 전체 켜기/끄기)만 제공한다. 세부 체크박스 3개를 UI에 추가하면 동작하지 않는 죽은 옵션이 되므로 추가하지 말 것 — 한 번 추가됐다가 제거된 적이 있다.

---

## 5. 비즈니스 로직 모듈 (`core/`)

### A. 이름 변경 — `rename_processor.py`
`RenameProcessor` 클래스(정적 메서드 모음). `rename_file_pairs`는 UUID 기반 고유 임시 이름 → 최종 이름의 2단계 전략으로 충돌을 방지하며, 단계 중 실패하면 가능한 파일을 원래 이름으로 되돌린다. 처리 후 `save_undo_info`로 `data/undo/undo_rename_*.json`을 생성한다. `undo_rename`은 가장 최근 undo 파일을 찾아 역순으로 복구하며, 일부 복구에 실패하면 이력을 지우지 않아 재시도할 수 있다. `preview_rename`은 실제 파일을 건드리지 않고 변경 결과만 미리 계산한다.

### B. 단일 파일 찾기 — `file_manager.py`
`FileManager(folder_path)` 클래스. `find_single_images`/`find_single_texts`로 짝 없는 파일을 자연 숫자 순서로 탐색하고, `delete_files`/`move_files`로 처리한다. 이동 대상에 같은 이름이 있으면 `_1`, `_2`를 붙여 기존 파일을 보존한다.

### C. 태그 처리 — `tag_processor.py`
`TagProcessor` 클래스(정적 메서드 모음). 핵심은 `process_tags_logic(content, options)`이며, 다음 순서로 옵션을 적용한다:

1. 누락 인원수 태그 주입 (`use_missing_tag`)
2. 태그 치환 (`use_replace`)
3. 인접 태그 접두/접미 수정 (`use_neighbor_modify`)
4. CSV 기반 특수 처리 (`use_csv_process` + `csv_tags_set`) — 추가/치환/삭제 3모드
5. 삭제 (`use_delete`, 조건부 포함)
6. 인원수·solo·사용자지정 태그 이동 + 추가 (`use_move_person`/`use_move_solo`/`use_move_custom`/`use_add`, 조건부 포함)

`(new_content, changes)`를 반환한다. `process_folder`는 `process_with_multicore`로 여러 파일을 병렬 처리하며, 변경된 파일의 원본 내용을 모아 `save_undo_info`로 `data/undo/undo_tag_*.json`을 생성한다.

> 주의: 4번(CSV 처리)은 한 번 구현에서 완전히 빠진 적이 있다(`tag_tab.py`는 옵션을 만들어 넘기는데 `process_tags_logic`에 처리 분기 자체가 없었음). `tag_tab.py`의 `_build_options()`가 채우는 키(`use_csv_process`, `csv_tags_set`, `csv_mode`, `csv_add_pos`, `csv_input_text`)와 `process_tags_logic`이 실제로 `options.get(...)`하는 키가 1:1로 일치하는지, 옵션을 추가할 때마다 양쪽을 함께 grep해 확인하는 습관이 필요하다.

### D. 검색 및 분류 — `search_filter.py`
`FileEntry` 데이터 클래스(이미지/텍스트 경로, 크기·해상도·태그를 지연 평가하는 프로퍼티 제공). 조건 평가는 `entry_passes_filter`가 담당하며, 조건 딕셔너리 구조는 `{'mode': 'unused'|'and'|'or'|'not', 'type': 'filename'|'size'|'resolution'|'tag', ...}`이다. AND/NOT은 교집합, OR은 합집합으로 결합된다. `search_files`는 해상도 조건이 있을 때만 `ThreadPoolExecutor`로 병렬 사전 로드를 수행한다(PIL 이미지 열기가 GIL을 해제하므로 스레드풀이 효과적). `process_entries`로 삭제·이동·복사를 수행하며, `get_orphan_warning`으로 짝을 잃을 파일을 사전에 알려준다.

### E. 중복/유사 이미지 찾기 — `duplicate_finder.py`
`DuplicateFinder` 클래스. `scan_files`로 이미지 목록을 수집한 뒤, `find_duplicates`가 종횡비 기준 1차 그룹화 → MD5/dHash/태그 유사도 계산(모두 `ThreadPoolExecutor` 병렬) → `UnionFind` 자료구조로 그룹핑하는 순서로 동작한다. `range_threshold`를 지정하면 단일 임계값이 아닌 범위 전체의 그룹을 한 번에 계산하는 "범위 검색 모드"로 동작해, `{'mode': 'range', 'md5': {...}, 'dhash': {threshold: {...}}}` 형태로 반환한다.

### F. 데이터셋 분석 — `dataset_analyzer.py`
세 부분으로 구성된다.

- `DatasetAnalyzer`: `make_buckets`(면적 보존형 버킷 목록 생성), `get_bucket_size`/`rebucketize`(이미지를 버킷에 배정), `analyze_folder_worker`/`scan_directories`(폴더별 분석, `process_with_multicore`로 병렬화), `calculate_recommend_repeats`(C+B 혼합 방식 리핏 추천), `calculate_waste`(낭비 슬롯·낭비율·총 스텝 계산).
- `DatasetSnapshot`: `collect`(현재 데이터셋 상태 수집), `save`/`load`(JSON 직렬화, 저장 경로는 `utils.settings.DATA_DIR / 'snapshots'`), `list_snapshots`(최신순 목록), `compare`(정확 매칭 → 폴더명 기준 퍼지 매칭 → 추가/삭제 분류 3단계 비교 전략).
- `BatchMover` (UI 레이어에서 분리되어 이곳으로 이동한 클래스): `folder_size`/`count_files`(통계), `process_one_folder`(이동/복사 + 중복 처리 3종: skip/number/merge, merge 시 파일명 충돌은 `_resolve_file_conflict`로 `_000001` 스타일 번호를 붙이고 캡션 `.txt`도 동반 처리). 모듈 레벨 함수 `format_size_generic`도 함께 제공된다(`DatasetSnapshot.format_size`와 별개로 소수점 표기 방식이 달라 분리됨).
  - `ui/tabs/analyzer_tab.py`의 `_BatchMoveDialog`/`_BatchMoveWorker`는 이 클래스를 호출만 하고, 실제 파일시스템 조작은 절대 탭 파일에 직접 작성하지 않는다(§1, §7.5 참고).

### G. XY표 만들기 — `xyz_plot_engine.py`
`FolderEntry`(폴더 경로+라벨), `XYPlotConfig`(dataclass, 모든 렌더링 옵션을 담는 설정 객체), `BuildResult`(성공 여부, 미리보기용 `image`, 저장용 원본 `full_image`, 오류 메시지). 핵심 함수는 `build_plot(config, progress_callback=None)`이며, 행/열 개수 결정 로직이 가장 중요하다:
- `fill_mode == 'grid'`이고 `config.grid_rows`/`grid_cols`가 0보다 크면, 폴더 개수나 이미지 개수와 무관하게 그 값을 그대로 사용한다. 0이면 자동(폴더 수 / 폴더당 최대 이미지 수)으로 대체된다.
- `folder_axis`(`AXIS_ROW`/`AXIS_COL`)에 따라 "폴더축 개수"와 "이미지축 개수"가 화면상 행/열 중 어디에 매핑되는지가 반전된다.

`build_preview(config, progress_callback=None)`은 `build_plot`을 호출한 뒤 결과가 너무 크면(긴 변 1600px 초과) 미리보기용으로 축소하고, `full_image`에는 원본을 그대로 보존한다. 공개 별칭 `collect_images`(내부 `_collect_images`)는 `xy_plot_tab.py`의 "자동 격자 생성" 기능이 폴더별 이미지 수를 세기 위해 외부에서 호출한다. `save_image`/`save_preview_image`로 최종 저장한다.

> v2.0.0 회귀 주의사항 (가장 자주 깨졌던 영역): 이 파일은 리팩토링 과정에서 두 차례 문제가 있었다.
> 1. 행/열 결정 로직이 임의로 재작성되어 `grid_rows`/`grid_cols` 설정이 무시되고 항상 폴더 수·이미지 수로 강제되는 회귀.
> 2. `build_preview()`에 애초에 `progress_callback` 파라미터가 없어서 `xy_plot_tab.py`의 워커가 호출하면 `TypeError`가 나던 버그.
>
> 현재는 원본 알고리즘으로 복원되어 있으므로, 이 파일을 다시 수정할 때는 반드시 위 두 항목이 깨지지 않는지 단위 테스트로 확인해야 한다. 검증 방법: 폴더 3개·이미지 2개씩인 테스트 데이터로 `grid_rows=2, grid_cols=5` (폴더·이미지 수와 다른 값)를 주고 `build_plot()` 결과 이미지 크기가 그 값을 따르는지 픽셀 단위로 확인한다.

---

## 6. UI 레이어 (`ui/`)

### 6.1 메인 윈도우 — `main_window.py`
`MainWindow(QMainWindow)`. 상단 툴바(작업 폴더 선택 `FolderSelector` + 사용 코어 `QSpinBox`, 코어 영역은 `QSizePolicy.Fixed`로 감싸 폴더 입력창이 늘어나도 잘리지 않게 보호됨)와 `QTabWidget`(8개 탭)으로 구성된다. 폴더·코어 값이 바뀌면 `_broadcast_folder`/`_broadcast_cores`로 관련 탭에 즉시 전달한다. `closeEvent`에서 모든 탭의 `get_settings()`를 모아 `data/config/app_settings.json`에 저장한다.

각 탭은 다음 두 메서드를 구현해 `MainWindow`와 연동된다. 이 계약은 모든 탭이 예외 없이 지켜야 하며, 새 탭을 추가할 때 가장 먼저 구현해야 할 부분이다.
```python
def get_settings(self) -> dict: ...   # 종료 시 저장할 현재 UI 상태
def load_settings(self, s: dict) -> None: ...  # 시작 시 복원
```
공통 작업 폴더·코어 수를 사용하는 탭은 추가로 `set_folder(folder: str)` / `set_num_cores(n: int)`를 구현한다. (§2.5의 "설정 스키마를 바꿀 때" 항목도 같이 참고)

### 6.2 디자인 시스템 — `themes.py`, `constants.py`, `styles/theme.py`

색상은 **두 개의 팔레트(라이트/다크)를 실행 중에 전환**할 수 있는 동적 시스템으로 관리된다. 색상 외의 폰트·크기·간격은 여전히 `constants.py`의 정적 상수다.

- **`ui/themes.py`** — `LIGHT_THEME`/`DARK_THEME` 두 딕셔너리(`Theme` TypedDict)에 모든 색상을 정의한다. 라이트는 눈부심을 낮춘 부드러운 크림+테라코타 톤, 다크는 차콜(그래파이트)+딥 블루 톤으로 구성한다. `get_theme(name)`으로 이름을 주면 해당 팔레트를 반환하며, 모르는 이름이면 `DEFAULT_THEME_NAME`('dark', 잘못된 입력에 대한 안전한 폴백일 뿐 시작 테마와는 무관함)으로 대체한다.
- **`ui/constants.py`** — 폰트(`FONT_*`)·크기(`BTN_MIN_*`, `INPUT_MIN_HEIGHT` 등)·간격(`SPACING_*`, `PADDING_*`) 정적 상수와, 테마 전환 시스템 자체를 담당한다.
  - `set_theme(name)` — 활성 테마를 바꾸고 등록된 모든 리스너를 호출한다.
  - `get_theme_name()` — 현재 활성 테마 이름(`'light'`|`'dark'`)을 반환한다.
  - `current_colors()` — 현재 활성 팔레트를 딕셔너리로 반환한다. **색상이 필요한 모든 곳은 이 함수를 호출 시점에 불러와야 한다** — `from ui.constants import COLOR_XXX` 같은 정적 import는 더 이상 존재하지 않는다(값을 한 번 복사해버려서 테마 전환에 반응하지 못하기 때문).
  - `INITIAL_THEME_NAME` — **앱을 처음 실행했을 때(저장된 설정이 없을 때) 보여줄 테마.** 현재 값은 `'light'`. `DEFAULT_THEME_NAME`(themes.py, 폴백용)과 역할이 다르므로 혼동하지 않는다.
  - `register_theme_listener(callback)` / `unregister_theme_listener(callback)` — §7.6 참고.
- **`ui/styles/theme.py`** — `build_qss(theme)`가 팔레트 딕셔너리를 받아 QSS 문자열로 조립한다. `main.py`가 시작 시 한 번, `main_window.py`의 테마 토글 버튼이 클릭될 때마다 `app.setStyleSheet(build_qss(새_팔레트))`를 다시 호출해 전체 앱에 즉시 반영한다.

위젯 코드에서 `#7c6fcd` 같은 색상 리터럴이나 `90px` 같은 크기를 직접 쓰지 않고, 색상은 `current_colors()['키']`로, 크기는 `constants.py`의 정적 상수로 참조한다. 개별 위젯의 `setStyleSheet()` 호출은 가능한 지양하고(꼭 필요한 경우 `setProperty('accent', True)`처럼 QSS 선택자용 속성을 다는 방식을 사용한다 — `QPushButton[accent="true"]` 참고), **QSS 전역 규칙으로 처리할 수 없어 인라인 `setStyleSheet()`을 써야만 하는 경우(QScrollArea 배경, 격자 셀 등)는 반드시 §7.6의 리스너 패턴을 따른다.** 버튼 크기를 한꺼번에 조정하고 싶으면 개별 탭이 아니라 `constants.py`의 `BTN_MIN_WIDTH`/`BTN_MIN_HEIGHT`와 `theme.py`의 padding 값만 고치면 전체에 일괄 반영된다. 색상 톤 자체를 조정하려면 `ui/themes.py`의 두 딕셔너리 값만 고치면 된다(다른 파일을 건드릴 필요 없음).

### 6.3 공용 위젯 — `widgets/`

새로운 UI 패턴이 필요할 때는 먼저 이 목록에 이미 있는지 확인하십시오. 두 곳 이상에서 같은 패턴(줌/팬, 숫자 정렬 등)이 필요해졌는데 매번 새로 작성하면 한쪽만 고치고 다른 쪽을 빠뜨리는 회귀가 발생하기 쉽습니다(실제로 두 번 발생).

| 위젯/클래스 | 파일 | 역할 | 현재 사용처 |
|:---|:---|:---|:---|
| `FolderSelector` | `folder_selector.py` | `[경로 입력창][찾아보기]` 한 줄 위젯. `path_changed` 시그널, `path()`/`set_path()`/`is_valid()` 제공. | 메인 윈도우 툴바 + 4개 탭의 "독립 경로" 입력 |
| `LogWidget` | `log_widget.py` | 스레드 안전 로그 뷰어. `append_line()`이 내부적으로 시그널을 emit하므로 워커 스레드에서 직접 호출해도 안전. 접두사(`[성공]`/`[오류]`/`[경고]`)로 자동 색상(`current_colors()` 호출 시점에 동적 결정). | rename/single_file/tag/converter 탭 |
| `SafeWorker` | `worker_base.py` | `QThread` 베이스. `run()` 대신 `work()`를 오버라이드하면 예외 발생 시 자동으로 `error(str)` 시그널이 emit된다(트레이스백 포함). §7.4 참고. | 8개 탭의 워커 11개 전부 |
| `ZoomPanLabel` | `zoom_pan_label.py` | 마우스 위치 중심 줌 + 좌클릭 드래그 팬을 지원하는 `QLabel`. `QScrollArea`와 줌/맞춤 콜백을 주입받아 동작. | `image_viewer.py` 내부 전용 (직접 쓰지 않음) |
| `ImageViewerDialog` | `image_viewer.py` | 이미지 1장을 줌/팬/맞춤/1:1로 보는 다이얼로그. `from_path()`로 파일 경로에서 바로 생성(EXIF 회전 보정 포함), 또는 `QImage`를 직접 넘겨 생성. `extra_toolbar_widgets`로 툴바에 버튼 추가 가능. 인라인 색상(힌트 텍스트, 캔버스 배경)은 테마 리스너로 자동 갱신된다(§7.6). | `search_filter_tab.py` (직접), `xy_plot_tab.py`의 `_XYPreviewDialog`(상속) |
| `NumericTreeItem`, `SORT_KEY_ROLE` | `numeric_tree_item.py` | `QTreeWidget` 컬럼별 숫자 정렬 키 지원. 사용법은 §2.5의 "역방향 참조" 항목 참고. | `analyzer_tab.py`, `search_filter_tab.py` |

### 6.4 탭 — `tabs/`
각 탭은 `QWidget`을 상속한 독립 클래스이며, 무거운 작업은 그 탭 파일 안에 비공개(`_` 접두사) `SafeWorker` 서브클래스로 분리되어 있다. 어떤 탭이 어떤 `core`/`image` 모듈과 어떤 공용 위젯에 의존하는지는 §2.5 파일 종속성 매트릭스를 참고한다(중복을 피하기 위해 여기서는 모듈명만 빠르게 나열한다).

| 탭 파일 | 클래스 | 주요 워커/다이얼로그 |
|:---|:---|:---|
| `rename_tab.py` | `RenameTab` | `_RenameWorker`, `_UndoWorker` |
| `single_file_tab.py` | `SingleFileTab` | `_FindWorker` |
| `tag_tab.py` | `TagTab` | `_TagWorker`. CSV 기반 처리, 인접 태그 수정 등 모든 옵션을 좌측 스크롤 패널에 배치 |
| `converter_tab.py` | `ConverterTab` | `_ConvertWorker`. 진행률은 `RateLimiter`로 과도한 UI 갱신을 방지 |
| `duplicate_tab.py` | `DuplicateTab` | `_SearchWorker`. 트리에 그룹/항목을 계층 표시, 미리보기 패널 동적 리사이즈 |
| `analyzer_tab.py` | `AnalyzerTab` | `_ScanWorker`, `_BatchMoveDialog`+`_BatchMoveWorker`, `_SnapshotDialog`(`_SaveSnapshotDialog`/`_LoadSnapshotDialog`/`_SnapshotInfoPanel` 포함) |
| `search_filter_tab.py` | `SearchFilterTab` | `_SearchWorker`, `_ResultLogDialog`. 조건 4종(미사용/AND/OR/NOT) 라디오 그룹은 `_make_mode_row` 헬퍼로 생성 |
| `xy_plot_tab.py` | `XYPlotTab` | `_PreviewWorker`, `_SaveWorker`, `_XYPreviewDialog`(별도 팝업), `_FolderRow`. 가장 복잡한 탭이며 §7.2에 상세 기술 |

---

## 7. 까다로운 부분 상세 설명

### 7.1 analyzer_tab / search_filter_tab — 숫자 컬럼 정렬

`QTreeWidget`의 기본 정렬은 모든 컬럼을 문자열로 비교하므로, `"9개"`와 `"10개"`를 비교하면 `"10개" < "9개"`로 잘못 정렬된다(낭비율 `%`, 용량 KB, 해상도 등도 동일 문제). 해결책은 `ui/widgets/numeric_tree_item.py`의 `NumericTreeItem`이다. 실제 표시 텍스트와 별개로 `SORT_KEY_ROLE` 위치에 `float` 정렬 키를 저장해두고, 그 키로 비교한다. 키가 없는 컬럼(폴더 이름)은 자동으로 텍스트 비교로 폴백한다.

새로운 숫자 컬럼을 추가할 때 체크리스트:
1. 그 트리에 행을 추가할 때 `QTreeWidgetItem` 대신 `NumericTreeItem` 사용
2. 해당 컬럼에 `item.setData(컬럼인덱스, SORT_KEY_ROLE, float(값))` 호출
3. `tree.setSortingEnabled(True)`를 호출했는지 반드시 확인 — 이게 빠지면 헤더에 정렬 화살표만 보이고 클릭해도 아무 일도 안 일어나는, 겉보기엔 정상인데 실제로는 죽은 버튼이 된다(실제로 `search_filter_tab.py`에서 이 한 줄이 빠져 있던 적이 있다).

### 7.2 xy_plot_tab — UI 구조는 원본 사양을 그대로 따른다

이 탭은 좌측 11개 설정 그룹과 우측 "라벨 입력 격자"로 구성되며, 완성본 미리보기는 메인 탭 화면에 없고 항상 별도 팝업(`_XYPreviewDialog`, `ImageViewerDialog`를 상속)으로 열린다. 이는 의도적 사양이며, 우측 패널을 미리보기 캔버스로 오해하고 변경하면 안 된다. 이 사양을 잘못 이해하고 전체를 다시 구현해버린 회귀가 실제로 있었다.

주요 사양:
- 이미지 배치 방향 그룹에는 행/열 라디오와 행↔열 스왑 버튼만 있다. 행수·열수 입력은 우측 패널의 컨트롤바(`grid_rows_edit`/`grid_cols_edit`)에 있다.
- 이미지 셀 크기(바짝붙이기/최장변 정사각형)와 혼합 해상도 처리(최대/최소/직접 기준 + 스케일/크롭 처리 방식)는 서로 다른 그룹이다. 합치지 않는다.
- 정렬 순서는 드롭다운이 아니라 "기준(이름/날짜/크기)" 라디오 한 줄 + "방향(오름/내림)" 라디오 한 줄이다.
- 격자 입력칸(`_label_entries`)의 첫 행은 열 라벨, 첫 열은 행 라벨이며 기본값이 `열{c}`/`행{r}` 텍스트로 채워져 있다. `_collect_config`는 이 칸에 값이 있으면(기본값이라도) 항상 대응하는 `FolderEntry.label`을 덮어쓴다 — 즉 셀프 선택에서 입력한 라벨보다 격자 행 라벨이 최종 우선이다. 이는 원본 사양이므로, "폴더에 입력한 라벨이 사라진다"는 보고가 있어도 임의로 격자 기본값을 비워서는 안 된다(한 번 비웠다가 사양 위반으로 되돌린 적이 있다).
- `GRID_MAX`(현재 50, `xy_plot_tab.py` 최상단)는 격자 입력칸 위젯이 너무 많이 생성되어 GUI가 느려지는 것을 막기 위한 안전장치다. 더 큰 표가 필요하면 이 상수만 올리면 된다. (원본 v1.x의 값은 12였는데, 너무 작아 50으로 상향했다.)
- 미리보기/저장 버튼, 줌·팬 동작은 `_XYPreviewDialog`가 `ImageViewerDialog`(§6.3)를 상속해 구현하며, 이 다이얼로그 고유 로직은 `_save_preview`/`_save_final` 두 메서드뿐이다. 줌/팬 자체를 고치고 싶다면 이 탭이 아니라 `ui/widgets/image_viewer.py`를 고쳐야 하며, 그 경우 `search_filter_tab.py`의 이미지 뷰어도 함께 영향을 받는다(§2.5 역방향 참조 참고).

`build_plot`/`build_preview`의 행·열 결정 로직 회귀에 대해서는 §5-G를 반드시 함께 참고한다.

### 7.3 exe 패키징 시 경로 동작

`utils/settings.py`의 `_resolve_app_dir()`이 `sys.frozen` 여부로 분기한다. PyInstaller로 패키징할 때는 `--onefile` 모드에서도 `sys.executable`이 실제 exe 경로를 가리키므로 별도 처리가 필요 없다. 다만 `.spec` 파일에 리소스(폰트 등)를 추가할 경우, 그 리소스의 경로는 `sys._MEIPASS`(코드/리소스 임시 폴더) 기준이어야 하고, 런타임 생성 데이터(`data/`)는 절대 그 안에 쓰면 안 된다 — `_MEIPASS`는 읽기 전용이며 프로세스 종료 시 삭제된다. 현재 이 프로젝트는 외부 리소스 파일(이미지, 아이콘, `.qss` 등)이 전혀 없으므로(QSS는 Python 코드로 생성, 폰트는 OS 시스템 폰트 경로를 직접 탐색) `auto-py-to-exe` 등에서 Additional Files를 추가할 필요가 없다.

### 7.4 워커 안전망 (SafeWorker) — "버튼을 눌렀는데 멈췄다"의 가장 흔한 원인

`QThread.run()` 내부에서 예외가 발생하면 Qt는 예외를 콘솔에만 출력하고 삼켜버린다. `run()` 끝에서 emit하기로 되어 있던 "완료" 시그널이 발행되지 않으므로, 그 시그널을 기다리며 비활성화해둔 버튼은 영원히 눌리지 않는 상태로 남는다. 권한 없는 폴더, 손상된 이미지 파일, 디스크 공간 부족 등 실제 사용 환경에서 충분히 발생하는 상황이다.

`ui/widgets/worker_base.py`의 `SafeWorker`가 이를 해결한다 — `run()`이 아니라 `work()`를 오버라이드하게 하고, `run()` 안에서 `work()`를 `try/except`로 감싼다. 새 워커를 만들 때 체크리스트:

1. `class _MyWorker(SafeWorker):` 로 선언 (`QThread` 직접 상속 금지)
2. `run()` 대신 `def work(self) -> None:` 구현
3. 호출부에서 `worker.error.connect(self._on_worker_error)` 연결 — 이 연결을 빠뜨리면 예외가 조용히 무시되는 것과 똑같은 결과가 된다
4. `_on_worker_error(self, traceback_text: str)` 슬롯에서 최소한 `_set_busy(False)`로 UI 잠금을 풀고 `QMessageBox.critical`로 알릴 것

> 과거에 8개 워커 중 6개가 이 패턴 없이 `QThread.run()`을 직접 구현하고 있었고, `duplicate_tab.py`의 검색 워커는 한술 더 떠 예외를 빈 결과(`{}`)로 바꿔 반환해 사용자가 실패 사실조차 알 수 없게 되어 있었다. 현재는 11개 워커 전부 `SafeWorker`로 통일되어 있다. 새 워커를 추가하면서 이 패턴을 따르지 않으면 회귀다.

### 7.5 UI/로직 분리 위반 사례 — analyzer_tab의 BatchMover

`ui/tabs/analyzer_tab.py`에 `_fmt_size_generic`, `_folder_size`, `_count_files`, `_resolve_file_conflict`, `_process_one_folder` 5개 함수가 PySide6와 전혀 무관한 순수 파일시스템 로직임에도 탭 파일에 직접 작성되어 있던 적이 있다. §1에서 명시한 "core/image는 UI 비의존" 원칙과 정면으로 충돌하는 사례였다. 지금은 `core/dataset_analyzer.py`의 `BatchMover` 클래스(+모듈 함수 `format_size_generic`)로 이동했고, 탭 파일은 `BatchMover.process_one_folder(...)`처럼 호출만 한다.

판별 기준: 새로 작성하는 함수/메서드에 `QWidget`, `QMessageBox`, `Signal`, `self.xxx_btn` 같은 PySide6 관련 식별자가 한 번도 등장하지 않는다면, 그 코드는 `ui/tabs/`가 아니라 `core/` 또는 `image/`에 있어야 한다. 탭 파일에 `import shutil`이나 `import os`가 늘어나면서 파일 조작 로직이 같이 따라 들어오고 있다면 경고 신호로 보고 점검한다.

### 7.6 테마 리스너 패턴 — "테마를 바꿔도 이 영역만 색이 안 바뀐다"

전역 QSS(`build_qss(theme)`)는 `QPushButton`, `QLabel` 같은 위젯 **타입**에 스타일을 적용하므로, `app.setStyleSheet()`을 다시 호출하면 거의 모든 위젯이 자동으로 새 색을 받는다. 그런데 위젯에 직접 `widget.setStyleSheet("background-color: #1e1e1e;")`처럼 **인라인 스타일**을 한 번 줘버리면, 그 위젯은 이후 전역 QSS가 다시 적용되어도 자신의 인라인 스타일을 그대로 유지한다(인라인이 항상 우선순위가 더 높기 때문). 결과적으로 `set_theme()`을 호출해도 그 위젯만 색이 바뀌지 않는 것처럼 보인다.

이런 일이 실제로 있었던 위치: `xy_plot_tab.py`의 라벨 입력 격자(`grid_scroll`/`grid_inner`와 그 안의 셀들), `duplicate_tab.py`의 안내 힌트 라벨, `image_viewer.py`의 안내 힌트와 캔버스 배경. 공통점은 모두 **QSS 선택자로 표현하기 애매한 동적 배경**(스크롤 영역, 동적으로 생성되는 셀, 캔버스)이라 인라인 스타일을 피할 수 없었다는 것이다.

해결책은 `ui/constants.py`의 리스너 시스템이다.

```python
# 위젯 쪽 — 보통 __init__ 끝에서 1회 등록
def _apply_theme_colors(self) -> None:
    colors = current_colors()
    self.some_widget.setStyleSheet(f"background-color: {colors['bg_input']};")

register_theme_listener(self._apply_theme_colors)

# 다이얼로그처럼 열고 닫는 일시적 위젯이면 닫을 때 해제
def closeEvent(self, event) -> None:
    unregister_theme_listener(self._apply_theme_colors)
    super().closeEvent(event)
```

`set_theme(name)`이 호출되면 등록된 모든 콜백이 순서대로 실행된다. 콜백 안에서 예외(`RuntimeError`, 보통 이미 삭제된 C++ 위젯을 건드릴 때 발생)가 나도 조용히 무시하고 나머지 콜백은 계속 실행되므로, 해제를 깜빡한 위젯 하나가 전체 테마 전환을 막는 일은 없다 — 다만 그렇다고 해제를 생략해도 된다는 뜻은 아니며, 탭처럼 프로그램 종료까지 살아있는 위젯은 해제하지 않아도 무방하지만 다이얼로그는 반드시 `closeEvent`에서 해제한다(그러지 않으면 다이얼로그를 열고 닫을 때마다 콜백 리스트가 계속 늘어난다).

새로 인라인 색상이 필요한 위젯을 만들 때 체크리스트:
1. 정말 QSS 전역 선택자(`QWidget[objectName="..."]` 등)로 처리할 수 없는지 먼저 검토한다 — 대부분의 경우는 가능하다.
2. 정말 인라인이 필요하면, 그 위젯을 `self.xxx`로 인스턴스 속성화한다(나중에 콜백에서 다시 칠하려면 참조가 필요하다).
3. `_apply_theme_colors()` 메서드를 만들어 그 안에서 `current_colors()`로 색을 다시 가져와 칠한다.
4. `__init__`에서 `register_theme_listener(self._apply_theme_colors)`로 등록한다.
5. 일시적 위젯(다이얼로그)이면 `closeEvent`에서 `unregister_theme_listener`로 해제한다.

---

## 8. 버전 기록 (Changelog)

### v2.0.0 (2026-06-20) - Major Rewrite

#### 1차 리팩토링 — Tkinter → PySide6 전환 및 구조 분리
- UI 프레임워크 전환: Tkinter → PySide6(Qt6)
  - 모든 탭을 PySide6 위젯으로 전면 재작성. 무거운 작업은 `QThread` + Signal/Slot 패턴으로 분리해 UI 블로킹을 원천 차단.
  - 모던 다크 테마와 색상·폰트·크기 상수를 `ui/constants.py`로 단일화하고 `ui/styles/theme.py`에서 QSS로 일괄 적용.
  - 버튼/입력창 크기와 폰트를 전반적으로 축소해 더 조밀하고 정보 밀도 높은 레이아웃으로 개선.
- 구조 개선: 평탄 구조 → 역할별 패키지 분리
  - `core/`(비즈니스 로직), `image/`(이미지 처리), `ui/`(화면), `utils/`(공용)로 재배치.
  - `metadata_utils.py` + `stego_utils.py` → `image/metadata.py`로 통합. `image_utils.py`는 `utils/common.py`에 흡수. `image_settings.py`는 `utils/settings.py`로 통합.
  - 최상위 폴더에는 `main.py`와 라이선스·문서 파일만 남도록 정리.
- exe 패키징 완전 대응
  - `utils/settings.py`의 `APP_DIR`이 소스 실행과 exe 실행에서 동일한 기준 경로를 계산하도록 통일.
  - 설정(`data/config/`), 로그(`data/logs/`), 실행취소(`data/undo/`), 스냅샷(`data/snapshots/`)이 모두 `data/` 한 폴더 아래로 정리되었고, 앱 시작 시 없으면 자동 생성.

#### 2차 점검 — UI 레이아웃 및 정렬 버그 수정
- 검색 및 분류 / XY표 만들기 탭 — 좌우 패널 리사이즈 가능화: 기존 `setMaximumWidth` 고정으로 스플리터 드래그가 막혀 있던 문제 해결. 중복/유사 이미지 탭도 동일하게 개선.
- 데이터셋 분석 탭 — 숫자 컬럼 정렬 버그 수정: 원본 수·버킷 종류 수·낭비율 등이 문자열 비교로 뒤섞이던 문제를 해결(이후 `NumericTreeItem`으로 공용화됨, §7.1).
- 메인 윈도우 — 상단 툴바 레이아웃 수정: 작업 폴더 입력창이 늘어날 때 "사용 코어" 설정이 잘리던 문제를 `SizePolicy.Fixed` 적용으로 해결.
- XY표 만들기 탭 — `build_preview()` 호출 오류 수정: `progress_callback` 파라미터 부재로 인한 `TypeError` 수정.

#### 3차 점검 — XY표 만들기 사양 전면 복원
- 1차 리팩토링에서 좌측 11개 설정 그룹과 우측 패널의 역할을 임의로 재구성한 회귀를 발견, 원본 v1.x 사양과 1:1로 재정렬(정렬 기준/방향 분리, 배치방향과 격자크기 분리, 셀크기와 혼합해상도 처리 분리, 제목·라벨 글자 옵션 세분화 등). §7.2 참고.
- `build_plot`의 행/열 결정 로직을 원본 알고리즘으로 복원(`grid_rows`/`grid_cols`가 폴더·이미지 개수와 무관하게 그대로 적용). §5-G 참고.
- 마우스 위치 중심 줌, 좌클릭 드래그 패닝을 미리보기 팝업에 추가(원본에 있었으나 1차 포팅 시 누락).
- 격자 입력 가능 범위(`GRID_MAX`)를 12 → 50으로 상향.

#### 4차 점검 — 원본 대비 기능 정밀 감사 (회귀 6건 발견 및 수정)
- `tag_processor.py`: CSV 기반 특수 처리(추가/치환/삭제) 로직이 완전히 빠져 있던 것을 복원(§5-C).
- `analyzer_tab.py` — 데이터셋 일괄이동: `shutil.move` 한 줄로 단순화되어 있던 것을, 이동/복사 선택·중복 처리 3종(건너뛰기/숫자추가/합치기)·리핏 적용·미리보기·진행률·캡션 동반처리를 갖춘 `_BatchMoveDialog`로 복원.
- `analyzer_tab.py` — 데이터셋 스냅샷: 비교 결과가 메시지박스 요약 한 줄로 축소되어 있던 것을, [기본]/[비교]/[차이점 분석(4서브탭)] 구조의 `_SnapshotDialog`로 복원.
- `search_filter_tab.py`: 결과 테이블 헤더 클릭 정렬이 인디케이터만 있고 실제로 동작하지 않던 버그 수정(`setSortingEnabled` 누락, §7.1).
- `search_filter_tab.py`: 독립 이미지 뷰어(줌/팬/맞춤), 처리결과 상세 로그창이 빠져 있던 것을 복원.
- `converter_tab.py`: 엔진이 참조하지 않는 메타데이터 세부 체크박스(EXIF/PNG텍스트/스테가노그래피 개별 토글)가 추가되어 있던 것을 원본 사양인 단일 체크박스로 환원.

#### 5차 점검 — 구조 개선 및 DRY 위반 정리
- `SafeWorker` 도입: 8개 탭 11개 워커 중 6개가 예외 처리 없이 `QThread.run()`을 직접 구현하고 있던 것(예외 시 UI 영구 정지 위험), `duplicate_tab.py`는 예외를 무음으로 삼키던 것을 `ui/widgets/worker_base.py`의 `SafeWorker`로 전체 통일. §7.4 참고.
- `ZoomPanLabel` / `ImageViewerDialog` 공용화: `xy_plot_tab.py`와 `search_filter_tab.py`에 거의 동일한 줌/팬 로직이 중복 구현되어 있던 것을 `ui/widgets/zoom_pan_label.py`, `ui/widgets/image_viewer.py`로 통합. `xy_plot_tab.py`의 `_XYPreviewDialog`는 이제 `ImageViewerDialog`를 상속해 저장 버튼만 추가하는 형태로 단순화.
- `NumericTreeItem` 공용화: `analyzer_tab.py`와 `search_filter_tab.py`에 중복 정의되어 있던 것을 `ui/widgets/numeric_tree_item.py`로 통합.
- UI/로직 분리 원칙 복구: `analyzer_tab.py`에 있던 순수 파일시스템 함수 5개를 `core/dataset_analyzer.py`의 `BatchMover` 클래스로 이동. §7.5 참고.
- 결과적으로 `xy_plot_tab.py` 1404→1216줄, `search_filter_tab.py` 863→654줄로 중복 제거를 통한 감소.

#### 6차 점검 — 라이트/다크 듀얼 테마 도입
- **`ui/themes.py` 신설**: 색상을 정적 상수에서 분리해 `LIGHT_THEME`/`DARK_THEME` 두 팔레트(dict)로 정의. 라이트는 눈부심을 낮춘 부드러운 크림+테라코타 톤, 다크는 차콜(그래파이트)+블루 톤으로 구성.
- **동적 색상 시스템**: `ui/constants.py`의 `COLOR_*` 정적 상수를 전부 제거하고 `current_colors()`(현재 팔레트 호출), `set_theme(name)`(전환), `get_theme_name()`으로 대체. `ui/styles/theme.py`의 `build_qss()` → `build_qss(theme)`로 변경.
- **메인 윈도우 테마 토글 버튼**: 상단 툴바에 버튼을 추가해 클릭 한 번으로 라이트/다크를 전환. 선택한 테마는 `data/config/app_settings.json`의 `theme` 키로 저장되어 재시작 후에도 유지된다. 저장된 설정이 없는 최초 실행 시 기본값은 라이트(`INITIAL_THEME_NAME`).
- **테마 전환 알림(리스너) 시스템 도입**: 인라인 `setStyleSheet()`으로 색을 직접 칠한 위젯(QScrollArea 배경, 동적 생성 셀, 캔버스 등)은 전역 QSS 재적용만으로는 갱신되지 않는 구조적 문제가 있었다(`xy_plot_tab.py`의 라벨 입력 격자에서 실제로 발견됨 — 배경색이 전혀 안 바뀌는 버그). `register_theme_listener()`/`unregister_theme_listener()`로 위젯이 자신의 갱신 콜백을 등록해두면 `set_theme()` 호출 시 자동으로 실행되도록 구조적으로 해결. `xy_plot_tab.py`, `duplicate_tab.py`, `ui/widgets/image_viewer.py` 3곳에 적용. §7.6 참고.

### v1.1.8 이전 (Tkinter 시대)
v1.1.8까지의 변경 이력은 v1.x 계열의 `DEVELOPER_GUIDE.md`(tkinter 기반, 평탄 파일 구조)를 참고하십시오. 주요 마일스톤은 다음과 같습니다.
- v1.1.8: XY표 만들기 탭 최초 추가.
- v1.1.7: 이미지 변환 — 입력 폴더에 출력, 변환 후 원본 삭제 기능 추가.
- v1.1.6: 검색 및 분류 탭 최초 추가.
- v1.1.0 ~ v1.1.5: 데이터셋 분석 탭 추가 및 버킷팅·리핏 추천·스냅샷 기능 고도화.
- v1.0.0: 최초 릴리스 (이름변경, 단일파일찾기, 태그처리, 이미지변환, 중복찾기).
