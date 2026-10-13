# -*- coding: utf-8 -*-
"""
시스템 설정 모듈 단위 테스트 (Unit Tests for Settings)
"""

from src.config import settings


def test_settings_loaded():
    assert settings.BASE_DIR.exists()
    assert settings.VEHICLE_AVG_SPEED_KMH > 0
    assert settings.BED_PENALTY_ALPHA > 0
    assert settings.BED_EXHAUSTED_PENALTY > 0
    assert settings.HTTP_TIMEOUT_SECONDS > 0


def test_key_validation_helpers():
    # 기본 예시값 또는 빈값일 때 False를 안전하게 반환하는지
    assert isinstance(settings.has_kakao_key(), bool)
    assert isinstance(settings.has_public_data_key(), bool)

