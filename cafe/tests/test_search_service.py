import pytest
from django.utils import timezone

from cafe.services.search_service import CafeSearchService, extract_keywords
from cafe.tests.factories import CafeFactory


class TestExtractKeywords:
    """測試關鍵字降級搜尋用的斷詞工具（見 design.md 決策 3b）"""

    def test_extracts_keywords_from_sentence_without_spaces(self):
        """不含空白的中文長句能切出有效關鍵詞"""
        keywords = extract_keywords('我想找安靜適合工作的咖啡廳')

        assert '安靜' in keywords
        assert '工作' in keywords

    def test_stopwords_and_generic_nouns_removed(self):
        """輸入全為停用詞或標點時回傳空清單"""
        keywords = extract_keywords('我想找一間咖啡店')

        assert keywords == []

    def test_mixed_punctuation_and_spaces(self):
        """含標點與空白混合的描述能正確斷詞，標點不會混入關鍵詞"""
        keywords = extract_keywords('安靜、有插座, 適合工作！')

        assert '安靜' in keywords
        assert '插座' in keywords
        assert '工作' in keywords
        for kw in keywords:
            assert '、' not in kw
            assert ',' not in kw
            assert '！' not in kw

    def test_empty_string_returns_empty_list(self):
        """空字串輸入回傳空清單"""
        assert extract_keywords('') == []

    def test_none_returns_empty_list(self):
        """None 輸入回傳空清單，不拋例外"""
        assert extract_keywords(None) == []

    def test_deduplicates_keywords(self):
        """重複出現的關鍵詞去重"""
        keywords = extract_keywords('安靜安靜的環境，安靜的咖啡廳')

        assert keywords.count('安靜') <= 1


@pytest.mark.django_db
class TestSearchByDescription:
    """測試 CafeSearchService.search_by_description"""

    USER_ID = 1

    def _mock_quota_ok(self, mocker):
        return mocker.patch(
            'cafe.services.search_service.ApiUsageService.try_increment_ai_calls', return_value=True
        )

    def test_returns_ok_with_matching_cafes_for_structured_attributes(self, mocker):
        """解析出明確屬性且有符合店家時，回傳 status='ok' 且包含該店"""
        self._mock_quota_ok(mocker)
        mocker.patch(
            'cafe.services.search_service.GroqAPI.parse_search_description',
            return_value={'limited_time': None, 'has_socket': 'yes', 'pet_friendly': None, 'has_pet': None},
        )
        matching_cafe = CafeFactory(
            has_socket='yes', ai_summary='適合工作', attributes_last_calculated_at=timezone.now(),
        )
        CafeFactory(has_socket='no', ai_summary='不適合', attributes_last_calculated_at=timezone.now())

        result = CafeSearchService.search_by_description('有插座的店', self.USER_ID)

        assert result.status == 'ok'
        assert list(result.cafes) == [matching_cafe]

    def test_excludes_cafe_matching_attribute_but_missing_ai_summary(self, mocker):
        """符合屬性但尚未有 ai_summary 的店家會被排除"""
        self._mock_quota_ok(mocker)
        mocker.patch(
            'cafe.services.search_service.GroqAPI.parse_search_description',
            return_value={'limited_time': None, 'has_socket': 'yes', 'pet_friendly': None, 'has_pet': None},
        )
        CafeFactory(has_socket='yes', ai_summary=None, attributes_last_calculated_at=timezone.now())

        result = CafeSearchService.search_by_description('有插座的店', self.USER_ID)

        assert result.status == 'ok'
        assert list(result.cafes) == []

    def test_falls_back_to_keyword_search_when_all_fields_null(self, mocker):
        """解析結果為全 null 時，走關鍵字降級搜尋比對 ai_summary"""
        self._mock_quota_ok(mocker)
        mocker.patch(
            'cafe.services.search_service.GroqAPI.parse_search_description',
            return_value={'limited_time': None, 'has_socket': None, 'pet_friendly': None, 'has_pet': None},
        )
        matching_cafe = CafeFactory(ai_summary='適合安靜工作的環境')
        CafeFactory(ai_summary='適合聚會聊天')

        result = CafeSearchService.search_by_description('安靜適合工作的店', self.USER_ID)

        assert result.status == 'ok'
        assert list(result.cafes) == [matching_cafe]

    def test_falls_back_to_keyword_search_when_parsing_fails(self, mocker):
        """解析失敗（非 JSON / 未定義欄位 / 數值不合法皆回傳 None）時走關鍵字降級搜尋，並歸還額度"""
        self._mock_quota_ok(mocker)
        mocker.patch('cafe.services.search_service.GroqAPI.parse_search_description', return_value=None)
        mock_revert = mocker.patch('cafe.services.search_service.ApiUsageService.revert_ai_call')
        matching_cafe = CafeFactory(ai_summary='適合安靜工作的環境')

        result = CafeSearchService.search_by_description('安靜的店', self.USER_ID)

        assert result.status == 'ok'
        assert list(result.cafes) == [matching_cafe]
        mock_revert.assert_called_once_with(self.USER_ID)

    def test_no_effective_keywords_returns_ok_with_empty_result(self, mocker):
        """降級後有效關鍵詞為空時，回傳 status='ok' 且 cafes 為空，不拋例外"""
        self._mock_quota_ok(mocker)
        mocker.patch('cafe.services.search_service.GroqAPI.parse_search_description', return_value=None)
        CafeFactory(ai_summary='適合安靜工作的環境')

        result = CafeSearchService.search_by_description('我想找一間店', self.USER_ID)

        assert result.status == 'ok'
        assert list(result.cafes) == []

    def test_quota_exceeded_does_not_call_groq_and_returns_quota_exceeded(self, mocker):
        """額度用盡時不呼叫 Groq，回傳 status='quota_exceeded' 且 cafes 為空 QuerySet"""
        mocker.patch(
            'cafe.services.search_service.ApiUsageService.try_increment_ai_calls', return_value=False
        )
        mock_parse = mocker.patch('cafe.services.search_service.GroqAPI.parse_search_description')

        result = CafeSearchService.search_by_description('安靜的店', self.USER_ID)

        assert result.status == 'quota_exceeded'
        assert list(result.cafes) == []
        mock_parse.assert_not_called()

    def test_empty_string_description_returns_ok_with_empty_result(self, mocker):
        """空字串描述輸入，最終回傳 status='ok' 且查無結果，不拋例外"""
        self._mock_quota_ok(mocker)
        mocker.patch('cafe.services.search_service.GroqAPI.parse_search_description', return_value=None)

        result = CafeSearchService.search_by_description('', self.USER_ID)

        assert result.status == 'ok'
        assert list(result.cafes) == []
