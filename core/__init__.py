"""
core 패키지

비즈니스 로직 모음 — UI에 의존하지 않는 순수 기능 구현체.

하위 모듈:
  tag_processor    — 태그 치환·삭제·이동·추가·실행취소
  rename_processor — 이미지-텍스트 쌍 일괄 이름 변경·실행취소
  file_manager     — 짝 없는 파일 탐색·삭제·이동
  search_filter    — 조건 기반 파일 검색 및 처리 (FileEntry)
  duplicate_finder — MD5/dHash/태그 기반 중복 탐지
  dataset_analyzer — 버킷 계산, 폴더 분석, 리핏 추천, 스냅샷
  xyz_plot_engine  — XY 플롯 그리드 이미지 합성
"""
