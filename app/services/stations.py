# -*- coding: utf-8 -*-
"""Shared Korail station list.

Kept in its own module (no imports) so both korail_service.py and srt_service.py
can build the same merged SRT+Korail station list without importing each other.
"""

KORAIL_STATIONS = [
    "서울", "용산", "광명", "천안아산", "오송", "대전", "김천(구미)", "신경주",
    "울산(통도사)", "부산", "공주", "익산", "정읍", "광주송정", "목포", "전주",
    "남원", "순천", "여천", "여수엑스포", "청량리", "양평", "원주", "제천",
    "단양", "풍기", "영주", "안동", "창원중앙", "창원", "마산", "진주", "홍성",
    "군산", "강릉", "만종", "둔내", "평창", "진부", "포항", "태화강"
]

# A handful of stations are spelled differently in Korail's station database than in
# SRT's - same physical station, different label (Korail spelling -> SRT spelling).
# Used both to de-duplicate the merged station list and to normalize a station name
# before calling SRT's API, which requires an exact match.
SRT_STATION_ALIASES = {
    "여수엑스포": "여수EXPO",
}
