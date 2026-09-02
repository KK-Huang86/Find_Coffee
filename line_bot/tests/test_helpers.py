import pytest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone
from linebot.v3.messaging import ReplyMessageRequest, TextMessage

from cafe.models import Cafe
from line_bot.constants import QUOTA_EXCEEDED
from line_bot.handlers.helpers import get_or_create_cafe_info, reply_text, reply_cafe_detail
from line_bot.tests.factories import CafeFactory, UserFactory


@pytest.mark.django_db
class TestGetOrCreateCafeInfo:
    """測試 get_or_create_cafe_info"""

    def test_returns_existing_cafe_from_db(self):
        """DB 有資料且未過期，直接回傳"""
        cafe = CafeFactory(last_refreshed=timezone.now())

        with patch('line_bot.handlers.helpers.CafeAttributeMatcher.match_and_sync_attributes') as mock_match:
            mock_match.return_value = False
            info_d, returned_cafe = get_or_create_cafe_info(cafe.place_id, user_id=1)

        assert info_d is not None
        assert returned_cafe == cafe
        assert info_d['place_id'] == cafe.place_id

    def test_refreshes_stale_cafe_data(self):
        """last_refreshed 超過 30 天，觸發 refresh"""
        cafe = CafeFactory(last_refreshed=timezone.now() - timedelta(days=31))

        with (
            patch('line_bot.handlers.helpers.refresh_cafe_data') as mock_refresh,
            patch('line_bot.handlers.helpers.CafeAttributeMatcher.match_and_sync_attributes') as mock_match,
        ):
            mock_refresh.return_value = None
            mock_match.return_value = False
            get_or_create_cafe_info(cafe.place_id, user_id=1)

        mock_refresh.assert_called_once_with(cafe.id)

    def test_refreshes_when_last_refreshed_is_none(self):
        """last_refreshed 為 None，觸發 refresh"""
        cafe = CafeFactory(last_refreshed=None)

        with (
            patch('line_bot.handlers.helpers.refresh_cafe_data') as mock_refresh,
            patch('line_bot.handlers.helpers.CafeAttributeMatcher.match_and_sync_attributes') as mock_match,
        ):
            mock_refresh.return_value = None
            mock_match.return_value = False
            get_or_create_cafe_info(cafe.place_id, user_id=1)

        mock_refresh.assert_called_once_with(cafe.id)

    def test_returns_quota_exceeded_when_quota_exceeded(self):
        """API 額度不足，回傳 (QUOTA_EXCEEDED, None) 且不呼叫 Google API"""
        with (
            patch('line_bot.handlers.helpers.ApiUsageService.try_increment_detail_calls') as mock_check,
            patch('line_bot.handlers.helpers.GoogleAPI.get_shop_detail') as mock_google,
        ):
            mock_check.return_value = False
            info_d, cafe = get_or_create_cafe_info('some_place_id', user_id=1)

        assert info_d is QUOTA_EXCEEDED
        assert cafe is None
        mock_google.assert_not_called()

    def test_calls_google_api_when_not_in_db(self):
        """DB 無資料，呼叫 Google API 並存入 DB"""
        user = UserFactory()
        place_id = 'test_google_place_id'
        google_data = {
            'place_id': place_id,
            'name': '測試咖啡店',
            'address': '台北市信義區',
            'lat': 25.033,
            'lng': 121.564,
        }

        with (
            patch('line_bot.handlers.helpers.ApiUsageService.try_increment_detail_calls', return_value=True) as mock_increment,
            patch('line_bot.handlers.helpers.GoogleAPI.get_shop_detail') as mock_google,
            patch('line_bot.handlers.helpers.CafeAttributeMatcher.match_and_sync_attributes') as mock_match,
        ):
            mock_google.return_value = google_data
            mock_match.return_value = False
            info_d, cafe = get_or_create_cafe_info(place_id, user.id)

        mock_google.assert_called_once_with(place_id)
        mock_increment.assert_called_once_with(user.id)
        assert info_d is not None
        assert Cafe.objects.filter(place_id=place_id).exists()

    def test_returns_none_when_google_api_fails(self):
        """Google API 回傳 None，回傳 (None, None)"""
        with (
            patch('line_bot.handlers.helpers.ApiUsageService.try_increment_detail_calls', return_value=True),
            patch('line_bot.handlers.helpers.GoogleAPI.get_shop_detail') as mock_google,
        ):
            mock_google.return_value = None
            info_d, cafe = get_or_create_cafe_info('nonexistent_place_id', user_id=1)

        assert info_d is None
        assert cafe is None

    def test_returns_none_when_google_data_missing_place_id(self):
        """Google API 回傳資料缺少 place_id，回傳 (None, None)"""
        with (
            patch('line_bot.handlers.helpers.ApiUsageService.try_increment_detail_calls', return_value=True),
            patch('line_bot.handlers.helpers.GoogleAPI.get_shop_detail') as mock_google,
        ):
            mock_google.return_value = {'name': '咖啡店'}
            info_d, cafe = get_or_create_cafe_info('some_place_id', user_id=1)

        assert info_d is None
        assert cafe is None

    def test_triggers_ai_summary_generation_when_new_cafe_has_reviews(self):
        """新建店家且 reviews 非空時，觸發 generate_cafe_ai_summary.delay"""
        user = UserFactory()
        place_id = 'test_place_id_with_reviews'
        google_data = {
            'place_id': place_id,
            'name': '測試咖啡店',
            'address': '台北市信義區',
            'lat': 25.033,
            'lng': 121.564,
            'reviews': ['環境安靜', '咖啡好喝'],
        }

        with (
            patch('line_bot.handlers.helpers.ApiUsageService.try_increment_detail_calls', return_value=True),
            patch('line_bot.handlers.helpers.GoogleAPI.get_shop_detail', return_value=google_data),
            patch('line_bot.handlers.helpers.CafeAttributeMatcher.match_and_sync_attributes', return_value=False),
            patch('line_bot.handlers.helpers.generate_cafe_ai_summary') as mock_summary_task,
        ):
            info_d, cafe = get_or_create_cafe_info(place_id, user.id)

        mock_summary_task.delay.assert_called_once_with(cafe.id)

    def test_does_not_trigger_ai_summary_generation_when_new_cafe_has_no_reviews(self):
        """新建店家且 reviews 為空時，不觸發 generate_cafe_ai_summary.delay"""
        user = UserFactory()
        place_id = 'test_place_id_without_reviews'
        google_data = {
            'place_id': place_id,
            'name': '測試咖啡店',
            'address': '台北市信義區',
            'lat': 25.033,
            'lng': 121.564,
            'reviews': [],
        }

        with (
            patch('line_bot.handlers.helpers.ApiUsageService.try_increment_detail_calls', return_value=True),
            patch('line_bot.handlers.helpers.GoogleAPI.get_shop_detail', return_value=google_data),
            patch('line_bot.handlers.helpers.CafeAttributeMatcher.match_and_sync_attributes', return_value=False),
            patch('line_bot.handlers.helpers.generate_cafe_ai_summary') as mock_summary_task,
        ):
            get_or_create_cafe_info(place_id, user.id)

        mock_summary_task.delay.assert_not_called()

    def test_does_not_trigger_ai_summary_generation_for_existing_cafe(self):
        """既有店家（非新建，直接從 DB 取得）不重複觸發 generate_cafe_ai_summary.delay"""
        cafe = CafeFactory(last_refreshed=timezone.now(), reviews=['環境安靜'])

        with (
            patch('line_bot.handlers.helpers.CafeAttributeMatcher.match_and_sync_attributes', return_value=False),
            patch('line_bot.handlers.helpers.generate_cafe_ai_summary') as mock_summary_task,
        ):
            get_or_create_cafe_info(cafe.place_id, user_id=1)

        mock_summary_task.delay.assert_not_called()


class TestReplyText:
    """測試 reply_text"""

    def test_calls_api_with_correct_message(self):
        """正確呼叫 LINE API 傳送文字訊息"""
        mock_api = MagicMock()
        reply_text(mock_api, 'test_token', '測試訊息')

        mock_api.reply_message.assert_called_once()
        call_args = mock_api.reply_message.call_args[0][0]
        assert isinstance(call_args, ReplyMessageRequest)
        assert call_args.reply_token == 'test_token'
        assert isinstance(call_args.messages[0], TextMessage)
        assert call_args.messages[0].text == '測試訊息'


class TestReplyCafeDetail:
    """測試 reply_cafe_detail"""

    def test_sends_two_messages(self):
        """傳送 FlexMessage + 按鈕訊息共 2 則"""
        mock_api = MagicMock()
        info_d = {
            'place_id': 'test_place_id',
            'name': '測試咖啡店',
            'address': '台北市',
            'lat': 25.033,
            'lng': 121.564,
            'rating': 4.5,
        }

        with (
            patch('line_bot.builders.flex_builder.FlexMessageBuilder.create_shop_flex_message', return_value={}),
            patch('line_bot.builders.flex_builder.PostbackBuilder.create_cafe_action_postback') as mock_button,
            patch('line_bot.handlers.helpers.FlexContainer.from_json'),
            patch('line_bot.handlers.helpers.FlexMessage') as mock_flex_msg,
            patch('line_bot.handlers.helpers.ReplyMessageRequest') as mock_req,
        ):
            mock_button.return_value = MagicMock()
            mock_flex_msg.return_value = MagicMock()
            mock_req.return_value = MagicMock()
            reply_cafe_detail(mock_api, 'test_token', info_d)

        mock_api.reply_message.assert_called_once()
        messages = mock_req.call_args.kwargs['messages']
        assert len(messages) == 2

    def test_passes_is_favorited_to_postback_builder(self):
        """正確傳遞 is_favorited=True 給 PostbackBuilder"""
        mock_api = MagicMock()
        info_d = {'place_id': 'test_place_id', 'name': '測試咖啡店'}

        with (
            patch('line_bot.builders.flex_builder.FlexMessageBuilder.create_shop_flex_message', return_value={}),
            patch('line_bot.builders.flex_builder.PostbackBuilder.create_cafe_action_postback') as mock_button,
            patch('line_bot.handlers.helpers.FlexContainer.from_json'),
            patch('line_bot.handlers.helpers.FlexMessage'),
            patch('line_bot.handlers.helpers.ReplyMessageRequest'),
        ):
            mock_button.return_value = MagicMock()
            reply_cafe_detail(mock_api, 'test_token', info_d, is_favorited=True)

        mock_button.assert_called_once_with(info_d=info_d, is_favorited=True)
