# -*- coding: utf-8 -*-
"""
시스템 전역 환경변수 및 설정 관리 모듈 (Configuration Management)
- .env 파일 자동 로드 및 중앙 집중식 관리
- API 키, 경로, 알고리즘 파라미터, 외부 API 엔드포인트 단일 진실 공급원(SSOT)
"""

import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# 프로젝트 루트 경로 (GoldenTime/)
BASE_DIR = Path(__file__).resolve().parent.parent

# .env 파일 로드 (프로젝트 루트 기준)
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    # -------------------------------------------------------------
    # 1. 외부 API 인증키 (External API Credentials)
    # -------------------------------------------------------------
    KAKAO_API_KEY: str = os.getenv("KAKAO_API_KEY", "").strip()
    PUBLIC_DATA_KEY: str = os.getenv("PUBLIC_DATA_KEY", "").strip()

    # -------------------------------------------------------------
    # 2. 프로젝트 디렉토리 경로 (Filesystem Paths)
    # -------------------------------------------------------------
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = BASE_DIR / "data"
    PROCESSED_DATA_DIR: Path = BASE_DIR / "data" / "processed"

    # -------------------------------------------------------------
    # 3. 배정 알고리즘 하이퍼파라미터 (Optimization Parameters)
    # -------------------------------------------------------------
    # 도심지 응급차량 평균 주행 속도 (km/h)
    VEHICLE_AVG_SPEED_KMH: float = float(os.getenv("VEHICLE_AVG_SPEED_KMH", "40.0"))
    # 가용 병상 역수 페널티 가중치 계수
    BED_PENALTY_ALPHA: float = float(os.getenv("BED_PENALTY_ALPHA", "5.0"))
    # 병상 고갈 시 부과되는 페널티 상수
    BED_EXHAUSTED_PENALTY: float = float(os.getenv("BED_EXHAUSTED_PENALTY", "99999.0"))

    # -------------------------------------------------------------
    # 4. 외부 연동 API 엔드포인트 및 네트워크 옵션 (Network & Endpoints)
    # -------------------------------------------------------------
    # 카카오 모빌리티 길찾기 (Directions)
    KAKAO_NAVI_URL: str = "https://apis-navi.kakaomobility.com/v1/directions"
    # 카카오 로컬 키워드 장소 검색
    KAKAO_LOCAL_SEARCH_URL: str = "https://dapi.kakao.com/v2/local/search/keyword.json"
    # 국립중앙의료원 실시간 응급실 가용병상 정보 조회
    NMC_REALTIME_ER_URL: str = "http://apis.data.go.kr/B552657/ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire"
    # 국립중앙의료원 응급의료기관 기본정보 조회
    NMC_BASIC_INFO_URL: str = "http://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytBassInfoInqire"

    # HTTP 요청 타임아웃 (초)
    HTTP_TIMEOUT_SECONDS: int = int(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))

    # -------------------------------------------------------------
    # 5. 로깅 및 애플리케이션 설정
    # -------------------------------------------------------------
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    def has_kakao_key(self) -> bool:
        """카카오 API 키 유효성 확인"""
        return bool(self.KAKAO_API_KEY and self.KAKAO_API_KEY != "your_kakao_key")

    def has_public_data_key(self) -> bool:
        """공공데이터 API 키 유효성 확인"""
        return bool(self.PUBLIC_DATA_KEY and self.PUBLIC_DATA_KEY != "your_public_key")


# 전역 싱글톤 설정 인스턴스
settings = Settings()

