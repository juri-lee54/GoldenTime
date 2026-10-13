# -*- coding: utf-8 -*-
import copy
import logging
from math import radians, cos, sin, asin, sqrt
import networkx as nx
import numpy as np
from scipy.optimize import linear_sum_assignment

from src.constants import DEPT_MAP
from src.config import settings

logger = logging.getLogger(__name__)


class EmergencyRoutingAlgorithms:
    """
    응급환자 최적 이송 경로 및 병원 배정 알고리즘 엔진
    1. 순차적 탐욕 배정 (Sequential Greedy with Triage):
       - 중증도 우선순위(수술 > 진료과) 순 정렬 후 실시간 병상 차감 및 그래프 엣지 동적 갱신
    2. 전역 최적화 배정 (Global Optimal Matching - Hungarian Algorithm):
       - 병상 슬롯 기반 이분 매칭 모델링 및 scipy.optimize.linear_sum_assignment를 통한 전체 환자 총 이동 시간 전역 최소화
    3. 알고리즘 비교 벤치마크 (Comparison Benchmark):
       - 탐욕법 vs 전역 최적화 간 총 소요시간, 평균 시간, 단축률 비교 분석
    """

    def __init__(self, hospitals_df, patients_list):
        self.hospitals_df = hospitals_df.fillna({
            'dgidIdName': '',
            'MKioskTy1': 'N',
            'MKioskTy2': 'N',
            'MKioskTy3': 'N',
            'MKioskTy4': 'N',
            'MKioskTy5': 'N',
            'MKioskTy6': 'N',
            'MKioskTy7': 'N',
            'MKioskTy8': 'N',
            'MKioskTy9': 'N',
            'MKioskTy10': 'N',
            'MKioskTy11': 'N',
            'available_beds': 0
        })
        self.patients_list = patients_list
        self.dept_map = DEPT_MAP

        # 실시간 가용 병상 추적 딕셔너리 (초기 병상 수 복사)
        self.initial_beds = {}
        for _, h in self.hospitals_df.iterrows():
            node_id = f"H_{h['hospital_id']}"
            self.initial_beds[node_id] = max(0, int(h['available_beds']))

        self.current_beds = copy.deepcopy(self.initial_beds)
        self.G = self._build_graph()

    def _haversine(self, lat1, lon1, lat2, lon2):
        """두 위경도 좌표 간의 구면 거리 계산 (단위: km)"""
        R = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        c = 2 * asin(sqrt(a))
        return c * R

    def _estimate_time(self, dist_km):
        """도심 응급차량 평균 주행 속도 기준 소요시간 추정 (단위: 분)"""
        return (dist_km / settings.VEHICLE_AVG_SPEED_KMH) * 60.0

    def _calculate_weight(self, est_time, available_beds):
        """
        비용 함수 (Objective Cost Function)
        C = T_est + Penalty(B)
        - 가용 병상(B) > 0: T_est + (alpha / B)
        - 가용 병상(B) <= 0: T_est + BED_EXHAUSTED_PENALTY (병상 고갈 페널티 부과)
        """
        if available_beds <= 0:
            return est_time + settings.BED_EXHAUSTED_PENALTY
        return est_time + (settings.BED_PENALTY_ALPHA / available_beds)

    def _build_graph(self):
        """환자-병원 간 완전 이분 그래프 구축"""
        G = nx.Graph()

        for _, h in self.hospitals_df.iterrows():
            node_id = f"H_{h['hospital_id']}"
            initial_beds = self.current_beds[node_id]
            G.add_node(
                node_id,
                type='hospital',
                hospital_id=h['hospital_id'],
                lat=h['latitude'],
                lon=h['longitude'],
                beds=initial_beds,
                initial_beds=initial_beds,
                name=h['name']
            )

        for p in self.patients_list:
            p_node_id = f"P_{p['id']}"
            G.add_node(p_node_id, type='patient', lat=p['lat'], lon=p['lon'])

            for _, h in self.hospitals_df.iterrows():
                h_node_id = f"H_{h['hospital_id']}"
                dist = self._haversine(p['lat'], p['lon'], h['latitude'], h['longitude'])
                est_time = self._estimate_time(dist)
                beds = self.current_beds[h_node_id]
                weight = self._calculate_weight(est_time, beds)

                G.add_edge(p_node_id, h_node_id, weight=weight, est_time=est_time)

        return G

    def _update_hospital_edges(self, h_node_id):
        """병상 변동 시 해당 병원과 연결된 모든 환자 엣지의 가중치 갱신"""
        remaining = self.current_beds[h_node_id]
        self.G.nodes[h_node_id]['beds'] = remaining
        for neighbor in self.G.neighbors(h_node_id):
            if self.G.nodes[neighbor].get('type') == 'patient':
                est_time = self.G[neighbor][h_node_id]['est_time']
                self.G[neighbor][h_node_id]['weight'] = self._calculate_weight(est_time, remaining)

    def _decrement_bed(self, h_node_id):
        """환자 배정 시 해당 병원의 잔여 병상을 1 차감하고 가중치를 즉시 동적 갱신"""
        self.current_beds[h_node_id] = max(0, self.current_beds[h_node_id] - 1)
        self._update_hospital_edges(h_node_id)

    def _is_hospital_capable(self, row, request_type, request_code):
        """단일 병원의 환자 진료과목/응급수술 수용 가능 여부 판별"""
        if request_type == 'dept':
            search_keyword = self.dept_map.get(request_code, request_code)
            return search_keyword in str(row['dgidIdName'])
        elif request_type == 'surgery':
            val = row.get(request_code, 'N')
            return bool(val and val != 'N')
        return True

    def _filter_targets(self, request_type, request_code):
        """진료과목 및 응급수술 수용 가능 병원 ID 목록 필터링"""
        filtered_ids = []
        for _, row in self.hospitals_df.iterrows():
            if self._is_hospital_capable(row, request_type, request_code):
                filtered_ids.append(f"H_{row['hospital_id']}")
        return filtered_ids

    # -------------------------------------------------------------
    # 1. 탐욕적 배정 (Sequential Greedy with Triage)
    # -------------------------------------------------------------
    def run_dijkstra(self, patient_id, request_type, request_code, decrement_bed=True):
        source = f"P_{patient_id}"
        targets = self._filter_targets(request_type, request_code)

        if not targets:
            return self.run_dijkstra_nearest(patient_id, decrement_bed=decrement_bed)

        best_hospital = None
        min_weight = float('inf')

        for target in targets:
            try:
                length = nx.dijkstra_path_length(self.G, source, target, weight='weight')
                if length < min_weight:
                    min_weight = length
                    best_hospital = target
            except nx.NetworkXNoPath:
                continue

        if best_hospital:
            result = self._format_result('Sequential Greedy', source, best_hospital, min_weight)
            if decrement_bed:
                self._decrement_bed(best_hospital)
            return result

        return self.run_dijkstra_nearest(patient_id, decrement_bed=decrement_bed)

    def run_dijkstra_nearest(self, patient_id, decrement_bed=True):
        source = f"P_{patient_id}"
        all_hospitals = [n for n in self.G.nodes() if self.G.nodes[n]['type'] == 'hospital']

        if not all_hospitals:
            return {'error': '시스템에 등록된 병원 데이터가 없습니다.'}

        best_hospital = None
        min_weight = float('inf')

        for target in all_hospitals:
            try:
                length = nx.dijkstra_path_length(self.G, source, target, weight='weight')
                if length < min_weight:
                    min_weight = length
                    best_hospital = target
            except nx.NetworkXNoPath:
                continue

        if best_hospital:
            result = self._format_result('Sequential Greedy (Fallback)', source, best_hospital, min_weight)
            result['is_fallback'] = True
            if decrement_bed:
                self._decrement_bed(best_hospital)
            return result

        return {'error': '이동 가능한 병원을 찾을 수 없습니다.'}

    def run_batch_assignment(self):
        """기존 메서드 이름 하위 호환성 유지"""
        return self.run_batch_greedy()

    def run_batch_greedy(self):
        """순차적 탐욕 배정 실행 (중증도 우선순위 Triage + 동적 병상 차감)"""
        # 상태 리셋
        self.current_beds = copy.deepcopy(self.initial_beds)
        self.G = self._build_graph()

        sorted_patients = sorted(
            self.patients_list,
            key=lambda p: 0 if p.get('request_type') == 'surgery' else 1
        )

        results = []
        for p in sorted_patients:
            res = self.run_dijkstra(
                p['id'],
                request_type=p['request_type'],
                request_code=p['request_code'],
                decrement_bed=True
            )
            if res and 'error' not in res:
                res['patient_id'] = p['id']
                res['request'] = p.get('request_name', '미지정')
                results.append(res)
            else:
                error_msg = res.get('error', '배정 실패') if res else '결과 없음'
                logger.error(f"환자 {p['id']} 배정 불가: {error_msg}")

        return sorted(results, key=lambda r: r['patient_id'])

    # -------------------------------------------------------------
    # 2. 전역 최적화 매칭 (Global Optimal Matching - Hungarian Algorithm)
    # -------------------------------------------------------------
    def run_batch_global_optimal(self):
        """
        헝가리안 알고리즘(linear_sum_assignment)을 이용한 다중 환자-병상 전역 최적 매칭
        - 병원의 각 가용 병상을 독립적인 '슬롯(Slot)'으로 전개(Expand)
        - 비용 행렬(N x M) 구축 후 이분 매칭의 총 비용(Total Cost)을 수학적으로 전역 최소화
        """
        if not self.patients_list:
            return []

        # 1. 병상 슬롯(Slots) 목록 생성
        slots = []
        for _, h in self.hospitals_df.iterrows():
            h_node = f"H_{h['hospital_id']}"
            initial_cap = max(0, int(h['available_beds']))
            # 가용 병상이 0개인 병원도 비상용 1개 슬롯으로 최소 포함
            actual_slots = initial_cap if initial_cap > 0 else 1
            for slot_idx in range(actual_slots):
                slots.append({
                    'hospital_node': h_node,
                    'hospital_id': h['hospital_id'],
                    'hospital_name': h['name'],
                    'lat': h['latitude'],
                    'lon': h['longitude'],
                    'is_overflow': (initial_cap <= 0),
                    'initial_cap': initial_cap,
                    'slot_idx': slot_idx,
                    'row_data': h
                })

        num_patients = len(self.patients_list)
        num_slots = len(slots)

        # 환자 수보다 슬롯 수가 적을 경우 가상 대체 슬롯(Dummy Overflow) 추가
        if num_slots < num_patients:
            for d_idx in range(num_patients - num_slots):
                # 가장 가까운 임의 병원을 복제하여 오버플로우 슬롯 추가
                fallback_h = self.hospitals_df.iloc[0]
                slots.append({
                    'hospital_node': f"H_{fallback_h['hospital_id']}",
                    'hospital_id': fallback_h['hospital_id'],
                    'hospital_name': fallback_h['name'],
                    'lat': fallback_h['latitude'],
                    'lon': fallback_h['longitude'],
                    'is_overflow': True,
                    'initial_cap': 0,
                    'slot_idx': 999 + d_idx,
                    'row_data': fallback_h
                })
            num_slots = len(slots)

        # 2. 비용 행렬 (Cost Matrix: num_patients x num_slots) 구축
        cost_matrix = np.zeros((num_patients, num_slots), dtype=float)

        for i, p in enumerate(self.patients_list):
            for j, s in enumerate(slots):
                dist = self._haversine(p['lat'], p['lon'], s['lat'], s['lon'])
                est_time = self._estimate_time(dist)

                # 진료/수술 수용 가능 여부 판별
                capable = self._is_hospital_capable(s['row_data'], p['request_type'], p['request_code'])

                if s['is_overflow']:
                    # 병상 고갈 또는 오버플로우 슬롯
                    cost = est_time + settings.BED_EXHAUSTED_PENALTY
                elif capable:
                    # 조건 부합 병상: 소요시간 + 병상 페널티
                    cap = max(1, s['initial_cap'])
                    cost = est_time + (settings.BED_PENALTY_ALPHA / cap)
                else:
                    # 조건 불일치 병원: 높은 페널티 부여
                    cost = est_time + 5000.0

                cost_matrix[i, j] = cost

        # 3. 헝가리안 알고리즘을 통한 최소 비용 이분 매칭 계산
        row_ind, col_ind = linear_sum_assignment(cost_matrix)

        # 4. 결과 집계 및 병상 차감 계산
        allocated_beds_count = {f"H_{h['hospital_id']}": 0 for _, h in self.hospitals_df.iterrows()}
        results = []

        for p_idx, s_idx in zip(row_ind, col_ind):
            p = self.patients_list[p_idx]
            s = slots[s_idx]
            h_node = s['hospital_node']

            dist = self._haversine(p['lat'], p['lon'], s['lat'], s['lon'])
            est_time = self._estimate_time(dist)
            total_cost = cost_matrix[p_idx, s_idx]

            allocated_beds_count[h_node] += 1
            is_fallback = (total_cost >= 5000.0) or s['is_overflow']

            results.append({
                'algorithm': 'Global Optimal (Hungarian)',
                'patient_id': p['id'],
                'request': p.get('request_name', '미지정'),
                'hospital_node': h_node,
                'hospital_name': s['hospital_name'],
                'available_beds': max(0, s['initial_cap'] - allocated_beds_count[h_node]),
                'total_cost_score': round(float(total_cost), 2),
                'est_time_min': round(float(est_time), 1),
                'lat': s['lat'],
                'lon': s['lon'],
                'is_fallback': is_fallback
            })

        return sorted(results, key=lambda r: r['patient_id'])

    # -------------------------------------------------------------
    # 3. 두 알고리즘 비교 벤치마크 (Comparison Benchmark)
    # -------------------------------------------------------------
    def compare_algorithms(self):
        """탐욕법과 전역 최적화 알고리즘의 종합 성과 지표 비교"""
        res_greedy = self.run_batch_greedy()
        res_global = self.run_batch_global_optimal()

        greedy_total_time = sum(r['est_time_min'] for r in res_greedy)
        global_total_time = sum(r['est_time_min'] for r in res_global)

        greedy_avg_time = (greedy_total_time / len(res_greedy)) if res_greedy else 0.0
        global_avg_time = (global_total_time / len(res_global)) if res_global else 0.0

        greedy_max_time = max((r['est_time_min'] for r in res_greedy), default=0.0)
        global_max_time = max((r['est_time_min'] for r in res_global), default=0.0)

        time_saved_min = round(greedy_total_time - global_total_time, 1)
        time_saved_pct = round((time_saved_min / greedy_total_time * 100), 1) if greedy_total_time > 0 else 0.0

        return {
            'greedy_results': res_greedy,
            'global_results': res_global,
            'metrics': {
                'greedy_total_time': round(greedy_total_time, 1),
                'global_total_time': round(global_total_time, 1),
                'greedy_avg_time': round(greedy_avg_time, 1),
                'global_avg_time': round(global_avg_time, 1),
                'greedy_max_time': round(greedy_max_time, 1),
                'global_max_time': round(global_max_time, 1),
                'time_saved_min': time_saved_min,
                'time_saved_pct': time_saved_pct
            }
        }

    def _format_result(self, algo_name, source, target_node, cost):
        h_data = self.G.nodes[target_node]
        edge_data = self.G.get_edge_data(source, target_node)
        current_beds = self.current_beds[target_node]
        return {
            'algorithm': algo_name,
            'hospital_node': target_node,
            'hospital_name': h_data['name'],
            'available_beds': current_beds,
            'total_cost_score': round(cost, 2),
            'est_time_min': round(edge_data['est_time'], 1),
            'lat': h_data['lat'],
            'lon': h_data['lon'],
            'is_fallback': False
        }
