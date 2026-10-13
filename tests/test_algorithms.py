# -*- coding: utf-8 -*-
"""
배정 알고리즘 엔진 핵심 단위 테스트 (Unit Tests for EmergencyRoutingAlgorithms)
- 구면 거리(Haversine) 및 이동 시간 산출 검증
- 병상 페널티 가중치 및 필터링 검증
- 순차적 탐욕법(Greedy) 시 실시간 병상 차감 검증
- 헝가리안 알고리즘(Hungarian) 전역 최적화 매칭 검증
- 알고리즘 벤치마크 및 Fallback 동작 검증
"""

import pytest
import pandas as pd
from src.algorithms import EmergencyRoutingAlgorithms
from src.config import settings


@pytest.fixture
def sample_hospitals_df():
    """테스트용 가상 응급의료기관 데이터셋"""
    data = [
        {
            "hospital_id": "HOSP_A",
            "name": "병원A (중증외상 전문)",
            "phone": "02-111-1111",
            "available_beds": 2,
            "latitude": 37.5650,
            "longitude": 126.9750,
            "dgidIdName": "내과,외과,신경외과,응급의학과",
            "MKioskTy1": "Y",  # 뇌출혈수술 가능
            "MKioskTy2": "Y",  # 뇌경색재관류 가능
            "MKioskTy3": "N",
        },
        {
            "hospital_id": "HOSP_B",
            "name": "병원B (심혈관 전문)",
            "phone": "02-222-2222",
            "available_beds": 1,
            "latitude": 37.5700,
            "longitude": 126.9800,
            "dgidIdName": "내과,외과,흉부외과,순환기내과",
            "MKioskTy1": "N",
            "MKioskTy2": "N",
            "MKioskTy3": "Y",  # 심근경색재관류 가능
        },
        {
            "hospital_id": "HOSP_C",
            "name": "병원C (일반 종합)",
            "phone": "02-333-3333",
            "available_beds": 5,
            "latitude": 37.5500,
            "longitude": 126.9600,
            "dgidIdName": "내과,소아청소년과,정형외과",
            "MKioskTy1": "N",
            "MKioskTy2": "N",
            "MKioskTy3": "N",
        },
    ]
    return pd.DataFrame(data)


@pytest.fixture
def sample_patients_list():
    """테스트용 가상 환자 목록"""
    return [
        {
            "id": "P1",
            "lat": 37.5640,
            "lon": 126.9740,
            "request_type": "surgery",
            "request_code": "MKioskTy1",
            "request_name": "뇌출혈수술"
        },
        {
            "id": "P2",
            "lat": 37.5660,
            "lon": 126.9760,
            "request_type": "surgery",
            "request_code": "MKioskTy1",
            "request_name": "뇌출혈수술"
        },
        {
            "id": "P3",
            "lat": 37.5690,
            "lon": 126.9790,
            "request_type": "surgery",
            "request_code": "MKioskTy3",
            "request_name": "심근경색의재관류"
        },
        {
            "id": "P4",
            "lat": 37.5510,
            "lon": 126.9610,
            "request_type": "dept",
            "request_code": "D001",
            "request_name": "내과"
        }
    ]


class TestDistanceAndTimeCalculations:
    """거리 및 시간 산출 로직 검증"""

    def test_haversine_same_point(self, sample_hospitals_df):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, [])
        # 동일 좌표 간 거리는 0km
        dist = engine._haversine(37.5665, 126.9780, 37.5665, 126.9780)
        assert pytest.approx(dist, abs=1e-4) == 0.0

    def test_haversine_known_distance(self, sample_hospitals_df):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, [])
        # 서울시청(37.5665, 126.9780) - 강남역(37.4979, 127.0276) 구면 거리 약 8.5km ~ 9.0km
        dist = engine._haversine(37.5665, 126.9780, 37.4979, 127.0276)
        assert 8.0 < dist < 9.5

    def test_estimate_time(self, sample_hospitals_df):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, [])
        # 40km 거리는 시속 40km 기준 정확히 60분
        assert pytest.approx(engine._estimate_time(40.0), abs=1e-4) == 60.0
        # 20km 거리는 30분
        assert pytest.approx(engine._estimate_time(20.0), abs=1e-4) == 30.0


class TestCostWeightCalculation:
    """목적 비용 함수(Objective Cost Function) 계산 검증"""

    def test_calculate_weight_with_positive_beds(self, sample_hospitals_df):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, [])
        est_time = 10.0
        beds = 5
        # weight = 10.0 + (5.0 / 5) = 11.0
        expected = est_time + (settings.BED_PENALTY_ALPHA / beds)
        assert pytest.approx(engine._calculate_weight(est_time, beds), abs=1e-4) == expected

    def test_calculate_weight_with_zero_beds(self, sample_hospitals_df):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, [])
        est_time = 10.0
        # 병상이 0개이면 고갈 페널티 부과
        weight = engine._calculate_weight(est_time, 0)
        assert weight >= settings.BED_EXHAUSTED_PENALTY + est_time


class TestCapabilityFiltering:
    """진료과목 및 응급수술 필터링 검증"""

    def test_surgery_capability_filter(self, sample_hospitals_df):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, [])
        # MKioskTy1(뇌출혈) 가능 병원은 HOSP_A 만 존재
        targets = engine._filter_targets("surgery", "MKioskTy1")
        assert targets == ["H_HOSP_A"]

        # MKioskTy3(심근경색) 가능 병원은 HOSP_B 만 존재
        targets = engine._filter_targets("surgery", "MKioskTy3")
        assert targets == ["H_HOSP_B"]

    def test_department_capability_filter(self, sample_hospitals_df):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, [])
        # D001(내과)는 3개 병원 모두 개설
        targets = engine._filter_targets("dept", "D001")
        assert len(targets) == 3

        # D002(소아청소년과)는 HOSP_C 만 개설
        targets = engine._filter_targets("dept", "D002")
        assert targets == ["H_HOSP_C"]


class TestSequentialGreedyAllocation:
    """순차적 탐욕법 및 동적 병상 차감 검증"""

    def test_capacity_decrement_on_sequential_patients(self, sample_hospitals_df, sample_patients_list):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, sample_patients_list)
        results = engine.run_batch_greedy()

        assert len(results) == len(sample_patients_list)

        # P1, P2 둘 다 뇌출혈 수술 환자이며 HOSP_A로 배정됨
        p1_res = next(r for r in results if r['patient_id'] == 'P1')
        p2_res = next(r for r in results if r['patient_id'] == 'P2')

        assert p1_res['hospital_name'] == "병원A (중증외상 전문)"
        assert p2_res['hospital_name'] == "병원A (중증외상 전문)"

        # 원래 병상 2개에서 2명이 배정되어 잔여 병상이 0개로 차감되어야 함
        hosp_a_beds = engine.current_beds["H_HOSP_A"]
        assert hosp_a_beds == 0


class TestGlobalOptimalHungarianMatching:
    """헝가리안 알고리즘(Hungarian) 전역 최적화 매칭 검증"""

    def test_global_optimal_allocation_completeness(self, sample_hospitals_df, sample_patients_list):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, sample_patients_list)
        results = engine.run_batch_global_optimal()

        assert len(results) == len(sample_patients_list)
        patient_ids = {r['patient_id'] for r in results}
        assert patient_ids == {"P1", "P2", "P3", "P4"}

        # 모든 배정에 예상 시간 및 병원명이 정상 포함
        for r in results:
            assert r['est_time_min'] >= 0.0
            assert r['hospital_name'] != ""
            assert r['algorithm'] == "Global Optimal (Hungarian)"


class TestAlgorithmBenchmarkComparison:
    """알고리즘 벤치마크 비교 검증"""

    def test_compare_algorithms_output_structure(self, sample_hospitals_df, sample_patients_list):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, sample_patients_list)
        comparison = engine.compare_algorithms()

        assert "greedy_results" in comparison
        assert "global_results" in comparison
        assert "metrics" in comparison

        m = comparison["metrics"]
        assert "greedy_total_time" in m
        assert "global_total_time" in m
        assert "time_saved_min" in m
        assert "time_saved_pct" in m

        # 전역 최적화 시간은 0보다 크고 합리적인 범위
        assert m["global_total_time"] > 0
        assert m["greedy_total_time"] > 0


class TestEdgeCasesAndFallbacks:
    """엣지 케이스 및 Fallback 동작 검증"""

    def test_empty_patients_list(self, sample_hospitals_df):
        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, [])
        assert engine.run_batch_greedy() == []
        assert engine.run_batch_global_optimal() == []

    def test_fallback_when_no_hospital_satisfies_condition(self, sample_hospitals_df):
        # 어떤 병원도 지원하지 않는 희귀 수술 요구 환자
        patients = [{
            "id": "UNMET_P",
            "lat": 37.5650,
            "lon": 126.9750,
            "request_type": "surgery",
            "request_code": "MKioskTy11",  # 중증화상(모두 N)
            "request_name": "중증화상"
        }]

        engine = EmergencyRoutingAlgorithms(sample_hospitals_df, patients)
        results = engine.run_batch_greedy()

        assert len(results) == 1
        # 조건에 맞는 병원이 없으므로 is_fallback이 True여야 함
        assert results[0]['is_fallback'] is True
        # 그러나 시스템이 멈추지 않고 가장 가까운 병원으로 대체 배정 완료
        assert results[0]['hospital_name'] != ""

