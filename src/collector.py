# -*- coding: utf-8 -*-
import os
import logging
import requests
import pandas as pd
import xml.etree.ElementTree as ET
from tqdm import tqdm

from src.config import settings

logger = logging.getLogger(__name__)


class EmergencyDataCollector:
    """
    국립중앙의료원 응급의료기관 정보 조회 API 및 카카오 로컬 API 연동 수집기
    - 실시간 병상 현황 (hvec)
    - 의료기관 기본정보 (진료과목, 응급수술/장비 코드 MKioskTy1~11, 자체 WGS84 좌표)
    - 2중 좌표 안전망: 카카오 지오코딩 우선 -> 실패/키 부재 시 공공데이터 자체 WGS84 좌표 자동 Fallback
    """

    def __init__(self, output_dir=None):
        self.output_dir = str(output_dir or settings.PROCESSED_DATA_DIR)
        os.makedirs(self.output_dir, exist_ok=True)
        self.public_data_key = settings.PUBLIC_DATA_KEY
        self.kakao_api_key = settings.KAKAO_API_KEY

    def fetch_real_er_status(self, city="서울특별시") -> pd.DataFrame:
        """1. 실시간 응급실 가용 병상 정보 조회"""
        if not settings.has_public_data_key():
            logger.warning("공공데이터포털 API 키(PUBLIC_DATA_KEY)가 설정되지 않았습니다.")
            return pd.DataFrame()

        logger.info(f"[실시간 데이터] {city} 지역 응급실 병상 현황 수집 시작")
        params = {
            "serviceKey": self.public_data_key,
            "STAGE1": city,
            "numOfRows": "400",
            "pageNo": "1"
        }

        try:
            response = requests.get(
                settings.NMC_REALTIME_ER_URL,
                params=params,
                timeout=settings.HTTP_TIMEOUT_SECONDS
            )
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                items = root.findall('.//item')
                er_data = []
                for item in items:
                    try:
                        hvec = item.find('hvec')
                        beds = int(hvec.text) if hvec is not None and hvec.text else 0
                        beds = max(0, beds)
                    except ValueError:
                        beds = 0

                    er_data.append({
                        'hospital_id': item.findtext('hpid'),
                        'name': item.findtext('dutyName'),
                        'phone': item.findtext('dutyTel3'),
                        'available_beds': beds
                    })
                return pd.DataFrame(er_data)
        except Exception as e:
            logger.error(f"실시간 병상 정보 수집 실패: {e}")
        return pd.DataFrame()

    def fetch_basic_info(self, city="서울특별시") -> pd.DataFrame:
        """
        2. 응급의료기관 기본정보 조회
        - dgidIdName (진료과목 목록)
        - MKioskTy1 ~ MKioskTy11 (응급수술 가능 여부)
        - wgs84Lat, wgs84Lon (공공데이터 자체 위경도 좌표)
        """
        if not settings.has_public_data_key():
            return pd.DataFrame()

        logger.info(f"[기본 정보] {city} 지역 병원 상세 정보(진료과목/수술/자체좌표) 수집 시작")
        params = {
            "serviceKey": self.public_data_key,
            "STAGE1": city,
            "numOfRows": "400",
            "pageNo": "1"
        }

        try:
            response = requests.get(
                settings.NMC_BASIC_INFO_URL,
                params=params,
                timeout=settings.HTTP_TIMEOUT_SECONDS
            )
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                items = root.findall('.//item')
                basic_data = []

                for item in items:
                    row = {
                        'hospital_id': item.findtext('hpid'),
                        'dgidIdName': item.findtext('dgidIdName')
                    }
                    for i in range(1, 12):
                        tag_name = f'MKioskTy{i}'
                        row[tag_name] = item.findtext(tag_name)

                    # 공공데이터 자체 WGS84 위경도 좌표 추출
                    lat_text = item.findtext('wgs84Lat')
                    lon_text = item.findtext('wgs84Lon')
                    try:
                        row['wgs84Lat'] = float(lat_text) if lat_text else None
                        row['wgs84Lon'] = float(lon_text) if lon_text else None
                    except (ValueError, TypeError):
                        row['wgs84Lat'] = None
                        row['wgs84Lon'] = None

                    basic_data.append(row)
                return pd.DataFrame(basic_data)
        except Exception as e:
            logger.error(f"병원 기본 정보 수집 실패: {e}")
        return pd.DataFrame()

    def get_coordinates(self, query):
        """카카오 키워드 장소 검색 API를 통한 위경도 좌표 조회"""
        if not settings.has_kakao_key():
            return None, None

        headers = {"Authorization": f"KakaoAK {self.kakao_api_key}"}
        try:
            resp = requests.get(
                settings.KAKAO_LOCAL_SEARCH_URL,
                headers=headers,
                params={"query": query},
                timeout=3
            ).json()
            docs = resp.get('documents', [])
            if docs:
                return float(docs[0]['y']), float(docs[0]['x'])
        except Exception as e:
            logger.debug(f"카카오 좌표 검색 실패 ({query}): {e}")
        return None, None

    def augment_location(self, df):
        """
        2중 좌표 안전망 (Fallback) 적용:
        1차: 카카오 로컬 지오코딩 API
        2차: 카카오 키 부재/실패 시 공공데이터 기본정보 자체 위경도(wgs84Lat, wgs84Lon) 자동 채택
        """
        logger.info(f"[좌표 변환] {len(df)}개 병원 좌표 확인 (카카오 API + 공공 WGS84 Fallback)")
        lats, lons, valid_idx = [], [], []

        use_kakao = settings.has_kakao_key()

        for i, row in tqdm(df.iterrows(), total=len(df), desc="좌표 안전망 변환"):
            lat, lon = None, None

            # 1차: 카카오 API 시도
            if use_kakao:
                lat, lon = self.get_coordinates(row['name'])

            # 2차 Fallback: 카카오 좌표가 없거나 키가 없으면 공공데이터 자체 좌표 채택
            if lat is None or lon is None:
                orig_lat = row.get('wgs84Lat')
                orig_lon = row.get('wgs84Lon')
                if pd.notna(orig_lat) and pd.notna(orig_lon):
                    lat = float(orig_lat)
                    lon = float(orig_lon)

            if lat is not None and lon is not None:
                lats.append(lat)
                lons.append(lon)
                valid_idx.append(i)

        valid_df = df.loc[valid_idx].copy()
        valid_df['latitude'] = lats
        valid_df['longitude'] = lons

        # 보조 컬럼 정리
        cols_to_drop = [c for c in ['wgs84Lat', 'wgs84Lon'] if c in valid_df.columns]
        if cols_to_drop:
            valid_df.drop(columns=cols_to_drop, inplace=True)

        return valid_df.reset_index(drop=True)

    def generate_seed_data(self, city="서울특별시"):
        """
        오프라인 시연 및 데모용 시드(Seed) 병원 데이터 생성
        - API 키가 없거나 네트워크 연결이 불가능한 경우에도 즉시 관제 시스템 시연 가능
        """
        logger.info(f"[데모 시드] {city} 권역 가상 응급의료기관 시드 데이터셋 생성 중...")

        seed_hospitals = [
            {"hospital_id": "SEED_01", "name": "서울대학교병원 응급의료센터", "phone": "02-2072-2474", "available_beds": 8, "latitude": 37.5796, "longitude": 126.9990, "dgidIdName": "내과,외과,신경외과,흉부외과,응급의학과,정형외과", "MKioskTy1": "Y", "MKioskTy2": "Y", "MKioskTy3": "Y", "MKioskTy4": "Y", "MKioskTy11": "Y"},
            {"hospital_id": "SEED_02", "name": "연세대학교 세브란스병원", "phone": "02-2227-7777", "available_beds": 6, "latitude": 37.5623, "longitude": 126.9408, "dgidIdName": "내과,외과,신경외과,소아청소년과,응급의학과", "MKioskTy1": "Y", "MKioskTy2": "Y", "MKioskTy3": "Y", "MKioskTy6": "Y", "MKioskTy7": "Y"},
            {"hospital_id": "SEED_03", "name": "강북삼성병원 응급실", "phone": "02-2001-1114", "available_beds": 5, "latitude": 37.5684, "longitude": 126.9678, "dgidIdName": "내과,외과,신경과,정형외과,응급의학과", "MKioskTy1": "Y", "MKioskTy2": "Y", "MKioskTy3": "N", "MKioskTy4": "Y"},
            {"hospital_id": "SEED_04", "name": "서울적십자병원 응급실", "phone": "02-2002-8000", "available_beds": 5, "latitude": 37.5678, "longitude": 126.9664, "dgidIdName": "내과,외과,정형외과,가정의학과", "MKioskTy1": "N", "MKioskTy2": "N", "MKioskTy3": "N", "MKioskTy6": "Y"},
            {"hospital_id": "SEED_05", "name": "가톨릭대학교 서울성모병원", "phone": "02-2258-2005", "available_beds": 7, "latitude": 37.5019, "longitude": 127.0048, "dgidIdName": "내과,외과,신경외과,흉부외과,정신건강의학과,응급의학과", "MKioskTy1": "Y", "MKioskTy2": "Y", "MKioskTy3": "Y", "MKioskTy8": "Y", "MKioskTy10": "Y"},
            {"hospital_id": "SEED_06", "name": "한양대학교병원 응급의료센터", "phone": "02-2290-8282", "available_beds": 4, "latitude": 37.5597, "longitude": 127.0438, "dgidIdName": "내과,외과,정형외과,신경외과,성형외과", "MKioskTy1": "Y", "MKioskTy3": "Y", "MKioskTy4": "Y", "MKioskTy5": "Y"},
            {"hospital_id": "SEED_07", "name": "중앙대학교병원 응급의료센터", "phone": "02-6299-1114", "available_beds": 3, "latitude": 37.5067, "longitude": 126.9602, "dgidIdName": "내과,외과,소아청소년과,이비인후과", "MKioskTy1": "N", "MKioskTy3": "Y", "MKioskTy6": "Y"},
            {"hospital_id": "SEED_08", "name": "고려대학교 안암병원", "phone": "02-920-5114", "available_beds": 6, "latitude": 37.5871, "longitude": 127.0333, "dgidIdName": "내과,외과,신경외과,흉부외과,응급의학과", "MKioskTy1": "Y", "MKioskTy2": "Y", "MKioskTy3": "Y", "MKioskTy7": "Y"},
        ]

        df = pd.DataFrame(seed_hospitals)
        for i in range(1, 12):
            tag = f'MKioskTy{i}'
            if tag not in df.columns:
                df[tag] = 'N'

        save_path = os.path.join(self.output_dir, f"hospitals_{city}.csv")
        df.to_csv(save_path, index=False, encoding='utf-8-sig')
        logger.info(f"[데모 시드 완료] {save_path} ({len(df)}개 병원)")
        return df

    def run(self, city="서울특별시"):
        """데이터 수집, 병합, 좌표 변환 및 CSV 저장 파이프라인 실행"""
        df_real = self.fetch_real_er_status(city)
        if df_real.empty:
            logger.warning(f"{city} 지역 실시간 데이터를 가져오지 못했습니다. (API 키 부재/네트워크 오류 시 시드 데이터 생성)")
            save_path = os.path.join(self.output_dir, f"hospitals_{city}.csv")
            if not os.path.exists(save_path):
                self.generate_seed_data(city)
            return

        df_basic = self.fetch_basic_info(city)

        if not df_basic.empty:
            logger.info(f"[병합] 실시간({len(df_real)}건) + 상세정보({len(df_basic)}건) 병합")
            df_merged = pd.merge(df_real, df_basic, on='hospital_id', how='left')
        else:
            df_merged = df_real

        final_df = self.augment_location(df_merged)

        save_path = os.path.join(self.output_dir, f"hospitals_{city}.csv")
        final_df.to_csv(save_path, index=False, encoding='utf-8-sig')
        logger.info(f"데이터셋 갱신 완료: {save_path} ({len(final_df)}개 기관)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    collector = EmergencyDataCollector()
    collector.run("서울특별시")