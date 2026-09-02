import re
from dataclasses import dataclass
from typing import Literal

import jieba
from django.db.models import Q, QuerySet

from cafe.models import Cafe
from integrations.groq.api import GroqAPI, SEARCH_DESCRIPTION_ALLOWED_FIELDS
from integrations.services import ApiUsageService

STOPWORDS = {
    '的', '了', '是', '我', '想', '要', '找', '一個', '一間',
    '有', '家', '附近', '咖啡', '咖啡店', '咖啡廳', '店',
}

# jieba 預設字典對部分中文形容詞/複合詞的斷詞不理想（例如會把「找安靜」黏成一詞、
# 把「咖啡廳」拆成「咖啡」+「廳」），用 suggest_freq 提高這些詞完整切出的機率。
_DOMAIN_WORDS = ('安靜', '咖啡廳', '咖啡店', '插座', '限時', '寵物友善', '適合工作')
for _word in _DOMAIN_WORDS:
    jieba.suggest_freq(_word, True)

_PUNCT_OR_SPACE_RE = re.compile(r'^[\W_]+$', re.UNICODE)


def extract_keywords(description: str) -> list[str]:
    """
    把使用者的自然語言描述斷詞成關鍵字清單，供關鍵字降級搜尋使用（見 design.md 決策 3b）。

    中文描述通常不含空白，需用中文斷詞工具（jieba）而非單純標點/空白切分，
    否則整句會被當成一個詞，icontains 幾乎不可能命中。
    """
    if not description:
        return []

    keywords = []
    seen = set()
    for token in jieba.cut_for_search(description):
        token = token.strip()
        if not token:
            continue
        if _PUNCT_OR_SPACE_RE.match(token):
            continue
        if token in STOPWORDS:
            continue
        if len(token) < 2:
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
    return keywords


@dataclass
class CafeSearchResult:
    """search_by_description 的回傳型別，讓 handler 能明確區分額度不足與查無結果"""
    status: Literal['ok', 'quota_exceeded']
    cafes: QuerySet


class CafeSearchService:
    """依使用者自然語言描述搜尋咖啡店（見 openspec/changes/cafe-description-search）"""

    @staticmethod
    def _keyword_fallback_search(description: str) -> QuerySet:
        keywords = extract_keywords(description)
        if not keywords:
            return Cafe.objects.none()

        query = Q()
        for keyword in keywords:
            query |= Q(ai_summary__icontains=keyword)
        return Cafe.objects.filter(query, ai_summary__isnull=False)

    @classmethod
    def search_by_description(cls, description: str, user_id: int) -> CafeSearchResult:
        if not ApiUsageService.try_increment_ai_calls(user_id):
            return CafeSearchResult(status='quota_exceeded', cafes=Cafe.objects.none())

        parsed = GroqAPI.parse_search_description(description)

        if parsed is None:
            ApiUsageService.revert_ai_call(user_id)
            return CafeSearchResult(status='ok', cafes=cls._keyword_fallback_search(description))

        conditions = {
            field: value
            for field, value in parsed.items()
            if field in SEARCH_DESCRIPTION_ALLOWED_FIELDS and value is not None
        }

        if not conditions:
            return CafeSearchResult(status='ok', cafes=cls._keyword_fallback_search(description))

        cafes = Cafe.objects.filter(
            ai_summary__isnull=False,
            attributes_last_calculated_at__isnull=False,
            **conditions,
        )
        return CafeSearchResult(status='ok', cafes=cafes)
