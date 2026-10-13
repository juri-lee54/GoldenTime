# -*- coding: utf-8 -*-
import os
import random
import logging
import requests
import pandas as pd
import folium
from streamlit_folium import st_folium
import streamlit as st

from src.config import settings
from src.constants import REGIONS, SURGERY_MAP, DEPT_MAP
from src.collector import EmergencyDataCollector
from src.algorithms import EmergencyRoutingAlgorithms

# 로깅 설정
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("GoldenTime")


# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------
def get_real_route_vis(start_lat, start_lon, end_lat, end_lon):
    """
    (시각화용) 카카오 모빌리티 길찾기 API로 실제 주행 경로 형상 추출
    - API 키 부재 또는 호출 실패 시 직선 경로 좌표를 Fallback으로 반환
    """
    fallback_path = [[start_lat, start_lon], [end_lat, end_lon]]

    if not settings.has_kakao_key():
        return fallback_path

    headers = {
        "Authorization": f"KakaoAK {settings.KAKAO_API_KEY}",
        "Content-Type": "application/json"
    }
    params = {
        "origin": f"{start_lon},{start_lat}",
        "destination": f"{end_lon},{end_lat}",
        "priority": "RECOMMEND"
    }

    try:
        resp = requests.get(
            settings.KAKAO_NAVI_URL,
            headers=headers,
            params=params,
            timeout=3.0
        )
        if resp.status_code == 200:
            data = resp.json()
            routes = data.get('routes', [])
            if routes and 'sections' in routes[0]:
                path = []
                for sec in routes[0]['sections']:
                    for road in sec.get('roads', []):
                        vertexes = road.get('vertexes', [])
                        for i in range(0, len(vertexes), 2):
                            path.append([vertexes[i + 1], vertexes[i]])
                if path:
                    return path
    except Exception as e:
        logger.warning(f"카카오 경로 API 호출 실패 (직선 경로 대체): {e}")

    return fallback_path


def generate_random_coordinate(center_lat, center_lon, radius_deg=0.08):
    """권역 중심점 기준 임의의 환자 발생 좌표 생성"""
    lat = center_lat + random.uniform(-radius_deg, radius_deg)
    lon = center_lon + random.uniform(-radius_deg, radius_deg)
    return lat, lon


# ---------------------------------------------------------
# Streamlit Application Setup
# ---------------------------------------------------------
st.set_page_config(page_title="Golden Time - 실시간 응급환자 이송 관제 시스템", layout="wide")
st.title("Golden Time: 실시간 응급환자 이송 관제 시스템")


# [1] 데이터 로드 (캐싱)
@st.cache_data
def load_data(city):
    path = os.path.join(settings.PROCESSED_DATA_DIR, f'hospitals_{city}.csv')
    if os.path.exists(path):
        df = pd.read_csv(path)
        required_cols = ['latitude', 'longitude', 'name', 'available_beds']
        if not all(col in df.columns for col in required_cols):
            return pd.DataFrame(columns=required_cols)
        return df
    return pd.DataFrame(columns=['latitude', 'longitude', 'name', 'available_beds'])


# [2] 세션 상태 초기화
if 'patients' not in st.session_state:
    st.session_state.patients = []
if 'assignments' not in st.session_state:
    st.session_state.assignments = []
if 'comparison_metrics' not in st.session_state:
    st.session_state.comparison_metrics = None
if 'current_region' not in st.session_state:
    st.session_state.current_region = None


# [3] 사이드바 - 지역, 알고리즘, 환자 관리
with st.sidebar:
    st.header("지역 설정")
    selected_region_name = st.selectbox("대상 지역 선택", list(REGIONS.keys()))
    region_info = REGIONS[selected_region_name]
    stage1 = region_info['stage1']
    current_center = region_info['coords']

    # 지역 변경 감지 시 환자 및 배정 목록 초기화
    if st.session_state.current_region != selected_region_name:
        st.session_state.current_region = selected_region_name
        st.session_state.patients = []
        st.session_state.assignments = []
        st.session_state.comparison_metrics = None
        st.rerun()

hospitals_df = load_data(stage1)
if hospitals_df.empty:
    st.warning("등록된 병원 데이터가 없습니다. 사이드바에서 데이터를 동기화해주세요.")

with st.sidebar:
    st.divider()
    st.header("데이터 동기화")

    if st.button("해당 지역 데이터 수집 및 갱신"):
        with st.spinner(f"국립중앙의료원 API ({stage1}) 연동 중..."):
            try:
                collector = EmergencyDataCollector()
                collector.run(city=stage1)
                load_data.clear()
                st.success(f"{stage1} 데이터 동기화 완료")
                st.rerun()
            except Exception as e:
                st.error(f"데이터 수집 실패: {e}")

    st.divider()

    # 배정 알고리즘 모드 선택
    st.header("배정 알고리즘 엔진")
    algo_choice = st.radio(
        "엔진 선택",
        [
            "전역 최적화 (Hungarian)",
            "순차적 탐욕법 (Greedy)",
            "알고리즘 벤치마크 비교"
        ],
        index=0
    )

    st.divider()

    # 환자 증상/수요 설정
    st.header("환자 중증도 및 진료 요구조건")
    st.caption("신규 등록 환자에게 적용될 조건입니다.")

    req_type_radio = st.radio("요구 구분", ["응급수술 (긴급)", "진료과목 (일반)"], index=0)
    selected_req_code = None
    selected_req_name = None

    if req_type_radio == "진료과목 (일반)":
        final_req_type = 'dept'
        dept_options = [f"{name} ({code})" for code, name in DEPT_MAP.items()]
        selected_option = st.selectbox("진료과목 선택", dept_options)
        selected_req_code = selected_option.split('(')[-1].strip(')')
        selected_req_name = selected_option.split('(')[0].strip()
    else:
        final_req_type = 'surgery'
        surgery_options = [f"{name} ({code})" for code, name in SURGERY_MAP.items()]
        selected_option = st.selectbox("수술 유형 선택", surgery_options)
        selected_req_code = selected_option.split('(')[-1].strip(')')
        selected_req_name = selected_option.split('(')[0].strip()

    st.write(f"**선택 조건:** {selected_req_name} ({selected_req_code})")

    st.divider()

    # 환자 등록 설정
    st.header("환자 등록 설정")
    mode = st.radio("등록 방식", ["무작위 생성", "지도 직접 선택"])

    if mode == "무작위 생성":
        n_p = st.slider("환자 수", 1, 15, 4)
        if st.button("환자 생성"):
            st.session_state.patients = []
            st.session_state.assignments = []
            st.session_state.comparison_metrics = None
            for i in range(n_p):
                lat, lon = generate_random_coordinate(current_center[0], current_center[1])
                st.session_state.patients.append({
                    'id': f"R{i + 1}",
                    'lat': lat,
                    'lon': lon,
                    'request_type': final_req_type,
                    'request_code': selected_req_code,
                    'request_name': selected_req_name
                })
            st.success(f"[{selected_req_name}] 환자 {n_p}명 등록 완료")
            st.rerun()
    else:
        st.info("지도에서 위치를 클릭하면 설정된 조건의 환자가 추가됩니다.")
        if st.button("환자 목록 초기화"):
            st.session_state.patients = []
            st.session_state.assignments = []
            st.session_state.comparison_metrics = None
            st.rerun()

    st.divider()


# [4] 메인 관제 화면 레이아웃
col1, col2 = st.columns([1.8, 1.2])

with col1:
    st.subheader(f"이송 관제 현황 (대기 환자: {len(st.session_state.patients)}명)")
    m = folium.Map(location=current_center, zoom_start=11, tiles='cartodbpositron')

    # 병원 마커 표시
    for _, h in hospitals_df.iterrows():
        beds = int(h['available_beds'])
        popup_html = folium.Popup(f"<b>{h['name']}</b><br>가용 병상: {beds}", max_width=300)
        color = 'green' if beds >= 5 else 'orange' if beds > 0 else 'red'
        folium.Marker(
            [h['latitude'], h['longitude']],
            popup=popup_html,
            icon=folium.Icon(color=color, icon='plus', prefix='fa'),
            tooltip=h['name']
        ).add_to(m)

    # 환자 마커 표시 (중증도에 따른 색상 분리)
    for p in st.session_state.patients:
        is_surgery = (p.get('request_type') == 'surgery')
        marker_color = 'crimson' if is_surgery else 'blue'
        p_type_label = "응급수술" if is_surgery else "진료과"
        p_info = f"<b>환자 {p['id']}</b> ({p_type_label})<br>{p.get('request_name', '미지정')}"

        folium.CircleMarker(
            [p['lat'], p['lon']],
            radius=8,
            color=marker_color,
            fill=True,
            fill_color=marker_color,
            fill_opacity=0.8,
            popup=folium.Popup(p_info, max_width=200)
        ).add_to(m)

    # 배정 경로 표시
    for asm in st.session_state.assignments:
        if asm.get('path_geometry'):
            popup_txt = folium.Popup(
                f"<b>{asm['hospital_name']}</b><br>예상 소요시간: {asm['est_time']}분",
                max_width=300
            )
            folium.PolyLine(asm['path_geometry'], color='blue', weight=4, opacity=0.7).add_to(m)
            folium.Marker(
                asm['path_geometry'][-1],
                icon=folium.Icon(color='blue', icon='user-md', prefix='fa'),
                popup=popup_txt
            ).add_to(m)

    output = st_folium(m, width="100%", height=620)

    # 지도 클릭 환자 추가
    if mode == "지도 직접 선택" and output and output.get('last_clicked'):
        lat, lon = output['last_clicked']['lat'], output['last_clicked']['lng']
        is_new = True
        if st.session_state.patients:
            last = st.session_state.patients[-1]
            if last['lat'] == lat and last['lon'] == lon:
                is_new = False

        if is_new:
            st.session_state.patients.append({
                'id': f"M{len(st.session_state.patients) + 1}",
                'lat': lat,
                'lon': lon,
                'request_type': final_req_type,
                'request_code': selected_req_code,
                'request_name': selected_req_name
            })
            st.rerun()

with col2:
    st.subheader("최적 이송 병원 배정 결과")

    if st.button("최적 경로 배정 실행", type="primary", disabled=not st.session_state.patients):
        if hospitals_df.empty:
            st.error("병원 데이터가 없습니다. 사이드바에서 데이터를 먼저 동기화해주세요.")
        elif not st.session_state.patients:
            st.warning("대기 중인 환자가 없습니다. 먼저 환자를 등록해주세요.")
        else:
            try:
                with st.spinner("최적 경로 산출 및 병상 제약 연산 중..."):
                    engine = EmergencyRoutingAlgorithms(hospitals_df, st.session_state.patients)

                    # 알고리즘 모드별 연산
                    if algo_choice == "전역 최적화 (Hungarian)":
                        raw_results = engine.run_batch_global_optimal()
                        st.session_state.comparison_metrics = None
                    elif algo_choice == "순차적 탐욕법 (Greedy)":
                        raw_results = engine.run_batch_greedy()
                        st.session_state.comparison_metrics = None
                    else:  # 알고리즘 벤치마크 비교 모드
                        bench = engine.compare_algorithms()
                        raw_results = bench['global_results']
                        st.session_state.comparison_metrics = bench['metrics']

                    # 경로 지오메트리 결합
                    results = []
                    for res in raw_results:
                        p_match = next((p for p in st.session_state.patients if p['id'] == res['patient_id']), None)
                        real_path = get_real_route_vis(
                            p_match['lat'], p_match['lon'],
                            res['lat'], res['lon']
                        ) if p_match else None

                        results.append({
                            'patient_id': res['patient_id'],
                            'request': res.get('request', '미지정'),
                            'hospital_name': res['hospital_name'],
                            'est_time': res['est_time_min'],
                            'remaining_beds': res['available_beds'],
                            'cost_score': res['total_cost_score'],
                            'algorithm': res.get('algorithm', 'Unknown'),
                            'path_geometry': real_path,
                            'is_fallback': res.get('is_fallback', False)
                        })

                    st.session_state.assignments = results
                    logger.info(f"배정 완료 ({algo_choice}): {len(results)}명")
                    st.rerun()

            except Exception as e:
                logger.error(f"배정 중 오류 발생: {e}", exc_info=True)
                import traceback
                st.code(traceback.format_exc())

    # 배정 결과가 있을 때 메트릭 및 상세 렌더링
    if st.session_state.assignments:
        assignments = st.session_state.assignments
        total_patients = len(assignments)
        est_times = [asm['est_time'] for asm in assignments]
        avg_time = sum(est_times) / total_patients if total_patients > 0 else 0.0
        max_time = max(est_times, default=0.0)
        # 골든타임 기준 (30분 이내 도착 환자 비율)
        compliance_count = sum(1 for t in est_times if t <= 30.0)
        compliance_rate = (compliance_count / total_patients * 100.0) if total_patients > 0 else 0.0

        # [2번 고도화] 상단 관제 KPI 메트릭 카드
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)

        with m_col1:
            if st.session_state.comparison_metrics:
                saved = st.session_state.comparison_metrics['time_saved_min']
                pct = st.session_state.comparison_metrics['time_saved_pct']
                delta_str = f"-{saved}분 (-{pct}%)" if saved > 0 else "0.0분"
                st.metric("평균 이송 시간", f"{avg_time:.1f}분", delta=delta_str)
            else:
                st.metric("평균 이송 시간", f"{avg_time:.1f}분")

        with m_col2:
            st.metric("골든타임 준수율", f"{compliance_rate:.1f}%", help="30분 이내 도착 환자 비율")

        with m_col3:
            st.metric("최대 이송 시간", f"{max_time:.1f}분")

        with m_col4:
            st.metric("배정 완료율", f"{total_patients}/{len(st.session_state.patients)}명")

        # 비교 모드일 경우 안내 배너
        if st.session_state.comparison_metrics:
            c = st.session_state.comparison_metrics
            st.info(
                f"**알고리즘 벤치마크 결과:** 탐욕법 총 시간 **{c['greedy_total_time']}분** 대비, "
                f"전역 최적화(Hungarian)로 총 **{c['global_total_time']}분** 달성 "
                f"(총 **{c['time_saved_min']}분** 단축 / **{c['time_saved_pct']}%** 효율 개선)"
            )

        # 결과 테이블
        df_res = pd.DataFrame(assignments)
        st.dataframe(
            df_res[['patient_id', 'request', 'hospital_name', 'est_time', 'remaining_beds', 'cost_score', 'algorithm']],
            hide_index=True,
            column_config={
                "patient_id": "환자 ID",
                "request": "증상 및 수술 구분",
                "hospital_name": "배정 병원",
                "est_time": "소요시간 (분)",
                "remaining_beds": "잔여 병상",
                "cost_score": "비용 가중치",
                "algorithm": "적용 알고리즘"
            }
        )

        # [2번 고도화] 이송 관제 일지 CSV 다운로드 버튼
        csv_export = df_res[[
            'patient_id', 'request', 'hospital_name', 'est_time',
            'remaining_beds', 'cost_score', 'algorithm', 'is_fallback'
        ]].to_csv(index=False, encoding='utf-8-sig')

        st.download_button(
            label="이송 관제 일지 CSV 다운로드",
            data=csv_export,
            file_name=f"dispatch_log_{stage1}_{total_patients}patients.csv",
            mime="text/csv"
        )

        # 개별 환자 배정 세부 내역 Expander
        for asm in assignments:
            with st.expander(f"환자 {asm['patient_id']} 배정 세부 내역"):
                st.write(f"**배정 병원:** {asm['hospital_name']}")
                st.write(f"**진료과목/수술:** {asm['request']}")
                st.write(f"**예상 이동 시간:** {asm['est_time']}분")
                st.write(f"**배정 후 잔여 병상:** {asm['remaining_beds']}개")
                st.write(f"**비용 가중치:** {asm['cost_score']}")
                st.write(f"**배정 방식:** {asm['algorithm']}")

                if asm.get('is_fallback', False):
                    st.warning("조건에 부합하는 병원이 없어 가용 병상 및 거리 기준 대체 병원으로 배정되었습니다.")
                elif asm['remaining_beds'] == 0:
                    st.info("이 병원의 가용 병상이 모두 소진되었습니다. 다음 환자는 인근 다른 병원으로 분산 배정됩니다.")
