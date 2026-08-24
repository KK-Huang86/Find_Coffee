from unittest.mock import MagicMock

import pytest

from integrations.groq.api import GroqAPI


@pytest.fixture(autouse=True)
def reset_groq_client(mocker):
    """GroqAPI._client 是 class 層級快取，測試間需重置避免互相汙染；
    config('GROQ_API_KEY') 改 mock 假值，測試不應依賴本機未提交的 `.env`
    或 CI 是否設有此環境變數（CI 目前只設了 GOOGLE_API_KEY）。"""
    GroqAPI._client = None
    mocker.patch('integrations.groq.api.config', return_value='fake-groq-api-key')
    yield
    GroqAPI._client = None


def _mock_groq_client(mocker, content='這是一間適合工作的咖啡店。'):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=content))]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    mock_groq_cls = mocker.patch('integrations.groq.api.Groq', return_value=mock_client)
    return mock_groq_cls, mock_client


class TestReviewCafe:
    """測試 GroqAPI.review_cafe"""

    def test_success(self, mocker):
        """成功呼叫並回傳 AI 評論內容"""
        _, mock_client = _mock_groq_client(mocker, content='適合工作與讀書的咖啡店。')

        result = GroqAPI.review_cafe(
            name='測試咖啡店',
            address='台北市信義區',
            rating=4.5,
            user_ratings_total=200,
            reviews=['環境安靜', '咖啡好喝'],
        )

        assert result == '適合工作與讀書的咖啡店。'
        mock_client.chat.completions.create.assert_called_once()

    def test_uses_expected_model_id(self, mocker):
        """呼叫時帶入正確的模型代號（回歸測試：曾因模型下架/改名整支功能靜默失效）"""
        _, mock_client = _mock_groq_client(mocker)

        GroqAPI.review_cafe(
            name='測試咖啡店', address='台北市信義區',
            rating=4.5, user_ratings_total=200, reviews=['好喝'],
        )

        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs['model'] == 'openai/gpt-oss-20b'

    def test_prompt_includes_all_fields(self, mocker):
        """prompt 正確帶入店名、地址、評分、評論數與評論內容"""
        _, mock_client = _mock_groq_client(mocker)

        GroqAPI.review_cafe(
            name='測試咖啡店', address='台北市信義區',
            rating=4.5, user_ratings_total=200, reviews=['環境安靜', '咖啡好喝'],
        )

        _, kwargs = mock_client.chat.completions.create.call_args
        prompt = kwargs['messages'][0]['content']
        assert '測試咖啡店' in prompt
        assert '台北市信義區' in prompt
        assert '4.5' in prompt
        assert '200' in prompt
        assert '- 環境安靜' in prompt
        assert '- 咖啡好喝' in prompt

    def test_empty_reviews_uses_placeholder_text(self, mocker):
        """reviews 為空列表時，prompt 顯示「無用戶評論」"""
        _, mock_client = _mock_groq_client(mocker)

        GroqAPI.review_cafe(
            name='測試咖啡店', address='台北市信義區',
            rating=4.5, user_ratings_total=200, reviews=[],
        )

        _, kwargs = mock_client.chat.completions.create.call_args
        assert '無用戶評論' in kwargs['messages'][0]['content']

    def test_none_rating_uses_placeholder_text(self, mocker):
        """rating 為 None 時，prompt 顯示「無資料」"""
        _, mock_client = _mock_groq_client(mocker)

        GroqAPI.review_cafe(
            name='測試咖啡店', address='台北市信義區',
            rating=None, user_ratings_total=200, reviews=['好喝'],
        )

        _, kwargs = mock_client.chat.completions.create.call_args
        assert '無資料' in kwargs['messages'][0]['content']

    def test_none_user_ratings_total_defaults_to_zero(self, mocker):
        """user_ratings_total 為 None 時，prompt 顯示 0"""
        _, mock_client = _mock_groq_client(mocker)

        GroqAPI.review_cafe(
            name='測試咖啡店', address='台北市信義區',
            rating=4.5, user_ratings_total=None, reviews=['好喝'],
        )

        _, kwargs = mock_client.chat.completions.create.call_args
        content = kwargs['messages'][0]['content']
        assert '（0 則評論）' in content

    def test_api_exception_returns_none(self, mocker):
        """Groq API 呼叫拋出例外時回傳 None，不向外拋出"""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception('model_not_found')
        mocker.patch('integrations.groq.api.Groq', return_value=mock_client)

        result = GroqAPI.review_cafe(
            name='測試咖啡店', address='台北市信義區',
            rating=4.5, user_ratings_total=200, reviews=['好喝'],
        )

        assert result is None

    def test_client_created_only_once_across_calls(self, mocker):
        """_client 為 class 層級快取，多次呼叫不重複建立 Groq client"""
        mock_groq_cls, _ = _mock_groq_client(mocker)

        GroqAPI.review_cafe(
            name='店A', address='地址A', rating=4.0, user_ratings_total=10, reviews=[],
        )
        GroqAPI.review_cafe(
            name='店B', address='地址B', rating=4.0, user_ratings_total=10, reviews=[],
        )

        mock_groq_cls.assert_called_once()
