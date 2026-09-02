import pytest
from unittest.mock import MagicMock, patch

from line_bot.constants import UserState
from line_bot.event_handlers import (
    _parse_postback_data,
    handle_follow,
    handle_message,
    handle_location_message,
    handle_postback,
)
from line_bot.tests.factories import UserFactory, CafeFactory
from integrations.google.api import OutOfServiceAreaError


class TestParsePostbackData:
    """測試 _parse_postback_data"""

    def test_parses_single_param(self):
        """解析單一參數"""
        result = _parse_postback_data('action=favorite')
        assert result == {'action': 'favorite'}

    def test_parses_multiple_params(self):
        """解析多個參數"""
        result = _parse_postback_data('action=favorite&place_id=abc123')
        assert result == {'action': 'favorite', 'place_id': 'abc123'}

    def test_empty_string_returns_empty_dict(self):
        """空字串回傳空 dict"""
        result = _parse_postback_data('')
        assert result == {}


@pytest.mark.django_db
class TestHandleFollow:
    """測試 handle_follow"""

    def test_creates_user_on_first_follow(self):
        """第一次追蹤，建立新使用者"""
        from users.models import User
        event = MagicMock()
        event.source.user_id = 'brand_new_user'

        handle_follow(event)

        assert User.objects.filter(line_user_id='brand_new_user').exists()

    def test_does_not_duplicate_existing_user(self):
        """重複追蹤，不建立重複使用者"""
        from users.models import User
        user = UserFactory()
        event = MagicMock()
        event.source.user_id = user.line_user_id

        handle_follow(event)

        assert User.objects.filter(line_user_id=user.line_user_id).count() == 1


@pytest.mark.django_db
class TestHandleMessage:
    """測試 handle_message"""

    def _make_event(self, user_id, text, reply_token='test_token'):
        event = MagicMock()
        event.source.user_id = user_id
        event.message.text = text
        event.reply_token = reply_token
        return event

    def test_throttled_user_skips_processing(self):
        """LockService 拒絕時，不觸發 loading 動畫也不回覆"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, '星巴克')

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.StateManager.get_state', return_value=UserState.NORMAL),
            patch('line_bot.event_handlers.LockService.acquire', return_value=False),
            patch('line_bot.event_handlers.show_loading') as mock_loading,
        ):
            mock_line_bot_api = MagicMock()
            mock_messaging_cls.return_value = mock_line_bot_api
            handle_message(event)

        mock_loading.assert_not_called()
        mock_line_bot_api.reply_message.assert_not_called()

    def test_waiting_shop_name_hits_db(self):
        """WAITING_SHOP_NAME 時，DB 有資料直接使用，不呼叫 Google API"""
        user = UserFactory()
        cafe = CafeFactory(name='星巴克信義店')
        event = self._make_event(user.line_user_id, '星巴克')

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.StateManager.get_state', return_value=UserState.WAITING_SHOP_NAME),
            patch('line_bot.event_handlers.StateManager.reset_state'),
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.SearchHistoryService.add_search'),
            patch('line_bot.event_handlers.GoogleAPI.search_coffee_shops') as mock_google,
            patch('line_bot.event_handlers.LineMessageBuilder.send_shop_result') as mock_send,
        ):
            mock_messaging_cls.return_value = MagicMock()
            handle_message(event)

        mock_google.assert_not_called()
        mock_send.assert_called_once()
        shops_arg = mock_send.call_args[0][2]
        assert any(s['place_id'] == cafe.place_id for s in shops_arg)

    def test_waiting_shop_name_falls_back_to_google(self):
        """WAITING_SHOP_NAME 時，DB 無資料，呼叫 Google API"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, '不存在的咖啡店xyz')
        google_shops = [{'place_id': 'g_place_id_1', 'name': '測試店'}]

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.StateManager.get_state', return_value=UserState.WAITING_SHOP_NAME),
            patch('line_bot.event_handlers.StateManager.reset_state'),
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.SearchHistoryService.add_search'),
            patch('line_bot.event_handlers.GoogleAPI.search_coffee_shops', return_value=google_shops) as mock_google,
            patch('line_bot.event_handlers.LineMessageBuilder.send_shop_result') as mock_send,
        ):
            mock_messaging_cls.return_value = MagicMock()
            handle_message(event)

        mock_google.assert_called_once_with('不存在的咖啡店xyz')
        mock_send.assert_called_once()

    def test_waiting_shop_name_single_result_saves_place_id(self):
        """單一搜尋結果時，SearchHistoryService 儲存 place_id"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, '星巴克')
        google_shops = [{'place_id': 'single_place_id'}]

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.StateManager.get_state', return_value=UserState.WAITING_SHOP_NAME),
            patch('line_bot.event_handlers.StateManager.reset_state'),
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.SearchHistoryService.add_search') as mock_history,
            patch('line_bot.event_handlers.GoogleAPI.search_coffee_shops', return_value=google_shops),
            patch('line_bot.event_handlers.LineMessageBuilder.send_shop_result'),
        ):
            mock_messaging_cls.return_value = MagicMock()
            handle_message(event)

        mock_history.assert_called_once_with(
            user.line_user_id, '星巴克', search_type='shop_name', place_id='single_place_id'
        )

    def test_waiting_shop_name_multiple_results_saves_none_place_id(self):
        """多筆搜尋結果時，SearchHistoryService 的 place_id 為 None"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, '咖啡')
        google_shops = [{'place_id': 'id1'}, {'place_id': 'id2'}]

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.StateManager.get_state', return_value=UserState.WAITING_SHOP_NAME),
            patch('line_bot.event_handlers.StateManager.reset_state'),
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.SearchHistoryService.add_search') as mock_history,
            patch('line_bot.event_handlers.GoogleAPI.search_coffee_shops', return_value=google_shops),
            patch('line_bot.event_handlers.LineMessageBuilder.send_shop_result'),
        ):
            mock_messaging_cls.return_value = MagicMock()
            handle_message(event)

        mock_history.assert_called_once_with(
            user.line_user_id, '咖啡', search_type='shop_name', place_id=None
        )

    def test_waiting_address_calls_google_nearby(self):
        """WAITING_ADDRESS 時，呼叫 Google 地址搜尋並記錄歷史"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, '信義路')

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.StateManager.get_state', return_value=UserState.WAITING_ADDRESS),
            patch('line_bot.event_handlers.StateManager.reset_state'),
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.SearchHistoryService.add_search') as mock_history,
            patch('line_bot.event_handlers.GoogleAPI.search_nearby_coffee_shops', return_value=[]) as mock_google,
            patch('line_bot.event_handlers.LineMessageBuilder.send_shop_result'),
        ):
            mock_messaging_cls.return_value = MagicMock()
            handle_message(event)

        mock_google.assert_called_once_with(address='信義路')
        mock_history.assert_called_once_with(user.line_user_id, '信義路', search_type='address')

    def test_waiting_address_out_of_service_area_replies_message(self):
        """WAITING_ADDRESS 時輸入範圍外地址，回覆服務範圍提示"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, '台中市中區')

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.StateManager.get_state', return_value=UserState.WAITING_ADDRESS),
            patch('line_bot.event_handlers.StateManager.reset_state'),
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.SearchHistoryService.add_search'),
            patch('line_bot.event_handlers.GoogleAPI.search_nearby_coffee_shops', side_effect=OutOfServiceAreaError),
        ):
            mock_line_bot_api = MagicMock()
            mock_messaging_cls.return_value = mock_line_bot_api
            handle_message(event)

        mock_line_bot_api.reply_message.assert_called_once()
        call_args = mock_line_bot_api.reply_message.call_args[0][0]
        assert '超出服務範圍' in call_args.messages[0].text

    def test_unknown_state_replies_prompt(self):
        """NORMAL 狀態下輸入文字，回覆引導使用選單的提示"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, '隨意文字')

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.StateManager.get_state', return_value=UserState.NORMAL),
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
        ):
            mock_line_bot_api = MagicMock()
            mock_messaging_cls.return_value = mock_line_bot_api
            handle_message(event)

        mock_line_bot_api.reply_message.assert_called_once()
        call_args = mock_line_bot_api.reply_message.call_args[0][0]
        assert '選單' in call_args.messages[0].text


@pytest.mark.django_db
class TestHandleLocationMessage:
    """測試 handle_location_message"""

    def _make_event(self, user_id, lat=25.033, lng=121.564, address='台北市信義區', reply_token='test_token'):
        event = MagicMock()
        event.source.user_id = user_id
        event.message.latitude = lat
        event.message.longitude = lng
        event.message.address = address
        event.reply_token = reply_token
        return event

    def test_user_not_found_replies_error(self):
        """找不到使用者時，回覆錯誤訊息"""
        event = self._make_event('nonexistent_user')

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.reply_text') as mock_reply_text,
        ):
            mock_messaging_cls.return_value = MagicMock()
            handle_location_message(event)

        mock_reply_text.assert_called_once()
        assert '找不到' in mock_reply_text.call_args[0][2]

    def test_throttled_user_skips_processing(self):
        """LockService 拒絕時，不觸發 loading 動畫也不回覆"""
        user = UserFactory()
        event = self._make_event(user.line_user_id)

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.LockService.acquire', return_value=False),
            patch('line_bot.event_handlers.show_loading') as mock_loading,
        ):
            mock_line_bot_api = MagicMock()
            mock_messaging_cls.return_value = mock_line_bot_api
            handle_location_message(event)

        mock_loading.assert_not_called()
        mock_line_bot_api.reply_message.assert_not_called()

    def test_no_shops_found_replies_prompt(self):
        """附近無咖啡店時，回覆找不到的提示"""
        user = UserFactory()
        event = self._make_event(user.line_user_id)

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.GoogleAPI.search_nearby_coffee_shops', return_value=[]),
        ):
            mock_line_bot_api = MagicMock()
            mock_messaging_cls.return_value = mock_line_bot_api
            handle_location_message(event)

        mock_line_bot_api.reply_message.assert_called_once()
        call_args = mock_line_bot_api.reply_message.call_args[0][0]
        assert '找不到咖啡店' in call_args.messages[0].text

    def test_out_of_service_area_replies_service_area_message(self):
        """GPS 位置在服務範圍外時，回覆服務範圍提示"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, lat=24.147, lng=120.673)

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.GoogleAPI.search_nearby_coffee_shops', side_effect=OutOfServiceAreaError),
        ):
            mock_line_bot_api = MagicMock()
            mock_messaging_cls.return_value = mock_line_bot_api
            handle_location_message(event)

        mock_line_bot_api.reply_message.assert_called_once()
        call_args = mock_line_bot_api.reply_message.call_args[0][0]
        assert '超出服務範圍' in call_args.messages[0].text

    def test_shops_found_sends_result(self):
        """找到咖啡店時，呼叫 send_shop_result"""
        user = UserFactory()
        event = self._make_event(user.line_user_id)
        shops = [{'place_id': 'abc', 'name': '測試咖啡'}]

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.GoogleAPI.search_nearby_coffee_shops', return_value=shops),
            patch('line_bot.event_handlers.LineMessageBuilder.send_shop_result') as mock_send,
        ):
            mock_messaging_cls.return_value = MagicMock()
            handle_location_message(event)

        mock_send.assert_called_once()


@pytest.mark.django_db
class TestHandlePostback:
    """測試 handle_postback"""

    def _make_event(self, user_id, data, reply_token='test_token'):
        event = MagicMock()
        event.source.user_id = user_id
        event.postback.data = data
        event.reply_token = reply_token
        return event

    def test_user_not_found_replies_error(self):
        """找不到使用者時，回覆錯誤訊息"""
        event = self._make_event('nonexistent_user', 'action=favorite&place_id=abc')

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.reply_text') as mock_reply_text,
        ):
            mock_messaging_cls.return_value = MagicMock()
            handle_postback(event)

        mock_reply_text.assert_called_once()
        assert '找不到' in mock_reply_text.call_args[0][2]

    def test_throttled_user_skips_processing(self):
        """LockService 拒絕時，不觸發 loading 動畫也不回覆"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, 'action=favorite')

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.LockService.acquire', return_value=False),
            patch('line_bot.event_handlers.show_loading') as mock_loading,
        ):
            mock_line_bot_api = MagicMock()
            mock_messaging_cls.return_value = mock_line_bot_api
            handle_postback(event)

        mock_loading.assert_not_called()
        mock_line_bot_api.reply_message.assert_not_called()

    def test_known_action_dispatches_to_handler(self):
        """已知 action，分派到對應的 handler 並傳入正確參數"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, 'action=favorite&place_id=abc')
        mock_handler = MagicMock()

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.ACTION_HANDLERS', {'favorite': mock_handler}),
        ):
            mock_line_bot_api = MagicMock()
            mock_messaging_cls.return_value = mock_line_bot_api
            handle_postback(event)

        mock_handler.assert_called_once_with(
            mock_line_bot_api, 'test_token', user, {'action': 'favorite', 'place_id': 'abc'}
        )

    def test_unknown_action_does_not_reply(self):
        """未知 action，不回覆任何訊息"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, 'action=unknown_action')

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
        ):
            mock_line_bot_api = MagicMock()
            mock_messaging_cls.return_value = mock_line_bot_api
            handle_postback(event)

        mock_line_bot_api.reply_message.assert_not_called()

    def test_vote_answer_uses_independent_lock_key(self):
        """vote_answer 使用獨立的 lock key（postback:vote_answer），不與其他 postback 共用"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, 'action=vote_answer&attr=socket&value=yes')
        mock_handler = MagicMock()

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.LockService.acquire', return_value=True) as mock_lock,
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.ACTION_HANDLERS', {'vote_answer': mock_handler}),
        ):
            mock_messaging_cls.return_value = MagicMock()
            handle_postback(event)

        mock_lock.assert_called_once_with(user.line_user_id, 'postback:vote_answer', ttl=1)
        mock_handler.assert_called_once()

    def test_non_vote_answer_uses_shared_lock_key(self):
        """一般 postback action 使用共用的 lock key（postback），TTL 為 2 秒"""
        user = UserFactory()
        event = self._make_event(user.line_user_id, 'action=favorite&place_id=abc')

        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_messaging_cls,
            patch('line_bot.event_handlers.LockService.acquire', return_value=True) as mock_lock,
            patch('line_bot.event_handlers.show_loading'),
            patch('line_bot.event_handlers.ACTION_HANDLERS', {'favorite': MagicMock()}),
        ):
            mock_messaging_cls.return_value = MagicMock()
            handle_postback(event)

        mock_lock.assert_called_once_with(user.line_user_id, 'postback', ttl=2)


@pytest.mark.django_db
class TestHandleMessageDistrictSearch:
    """測試 WAITING_DISTRICT / WAITING_PET_DISTRICT / WAITING_PET_FRIENDLY_DISTRICT 狀態"""

    def _make_event(self, user_id, text, reply_token='test_token'):
        event = MagicMock()
        event.source.user_id = user_id
        event.message.text = text
        event.reply_token = reply_token
        return event

    def _run(self, user, text, state):
        event = self._make_event(user.line_user_id, text)
        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_cls,
            patch('line_bot.event_handlers.StateManager.get_state', return_value=state),
            patch('line_bot.event_handlers.StateManager.reset_state'),
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
        ):
            mock_api = MagicMock()
            mock_cls.return_value = mock_api
            handle_message(event)
        return mock_api

    # ── WAITING_DISTRICT ──────────────────────────────────────────────────

    def test_district_no_results_replies_not_found(self):
        """大安區沒有符合條件的店，回覆找不到訊息"""
        user = UserFactory()
        mock_api = self._run(user, '大安區', UserState.WAITING_DISTRICT)

        call_args = mock_api.reply_message.call_args[0][0]
        assert '找不到' in call_args.messages[0].text

    def test_district_limited_time_no_included(self):
        """limited_time='no' 的店納入工作友善搜尋"""
        user = UserFactory()
        CafeFactory(address='台北市大安區仁愛路', limited_time='no', has_socket='yes')

        mock_api = self._run(user, '大安區', UserState.WAITING_DISTRICT)

        mock_api.reply_message.assert_called_once()

    def test_district_limited_time_maybe_included(self):
        """limited_time='maybe' 的店也納入工作友善搜尋（種入資料預設值）"""
        user = UserFactory()
        CafeFactory(address='台北市大安區仁愛路', limited_time='maybe', has_socket='yes')

        mock_api = self._run(user, '大安區', UserState.WAITING_DISTRICT)

        mock_api.reply_message.assert_called_once()

    def test_district_limited_time_yes_excluded(self):
        """limited_time='yes' 的店不納入工作友善搜尋"""
        user = UserFactory()
        CafeFactory(address='台北市大安區仁愛路', limited_time='yes', has_socket='yes')

        mock_api = self._run(user, '大安區', UserState.WAITING_DISTRICT)

        call_args = mock_api.reply_message.call_args[0][0]
        assert '找不到' in call_args.messages[0].text

    def test_district_no_socket_excluded(self):
        """has_socket='no' 的店不納入工作友善搜尋"""
        user = UserFactory()
        CafeFactory(address='台北市大安區仁愛路', limited_time='no', has_socket='no')

        mock_api = self._run(user, '大安區', UserState.WAITING_DISTRICT)

        call_args = mock_api.reply_message.call_args[0][0]
        assert '找不到' in call_args.messages[0].text

    # ── WAITING_PET_DISTRICT ─────────────────────────────────────────────

    def test_pet_district_has_pet_yes_found(self):
        """has_pet='yes' 的店出現在貓貓狗狗搜尋結果"""
        user = UserFactory()
        CafeFactory(address='台北市大安區仁愛路', has_pet='yes')

        mock_api = self._run(user, '大安區', UserState.WAITING_PET_DISTRICT)

        mock_api.reply_message.assert_called_once()

    def test_pet_district_has_pet_no_not_found(self):
        """has_pet='no' 的店不出現在貓貓狗狗搜尋結果"""
        user = UserFactory()
        CafeFactory(address='台北市大安區仁愛路', has_pet='no')

        mock_api = self._run(user, '大安區', UserState.WAITING_PET_DISTRICT)

        call_args = mock_api.reply_message.call_args[0][0]
        assert '找不到' in call_args.messages[0].text

    def test_pet_district_no_results_replies_not_found(self):
        """沒有符合條件的店，回覆找不到訊息"""
        user = UserFactory()
        mock_api = self._run(user, '北投區', UserState.WAITING_PET_DISTRICT)

        call_args = mock_api.reply_message.call_args[0][0]
        assert '找不到' in call_args.messages[0].text

    # ── WAITING_PET_FRIENDLY_DISTRICT ────────────────────────────────────

    def test_pet_friendly_district_yes_found(self):
        """pet_friendly='yes' 的店出現在寵物友善搜尋結果"""
        user = UserFactory()
        CafeFactory(address='台北市大安區仁愛路', pet_friendly='yes')

        mock_api = self._run(user, '大安區', UserState.WAITING_PET_FRIENDLY_DISTRICT)

        mock_api.reply_message.assert_called_once()

    def test_pet_friendly_district_no_not_found(self):
        """pet_friendly='no' 的店不出現在寵物友善搜尋結果"""
        user = UserFactory()
        CafeFactory(address='台北市大安區仁愛路', pet_friendly='no')

        mock_api = self._run(user, '大安區', UserState.WAITING_PET_FRIENDLY_DISTRICT)

        call_args = mock_api.reply_message.call_args[0][0]
        assert '找不到' in call_args.messages[0].text

    def test_pet_friendly_district_no_results_replies_not_found(self):
        """沒有符合條件的店，回覆找不到訊息"""
        user = UserFactory()
        mock_api = self._run(user, '北投區', UserState.WAITING_PET_FRIENDLY_DISTRICT)

        call_args = mock_api.reply_message.call_args[0][0]
        assert '找不到' in call_args.messages[0].text


@pytest.mark.django_db
class TestHandleMessageDescriptionSearch:
    """測試 WAITING_DESCRIPTION_SEARCH 狀態"""

    def _make_event(self, user_id, text, reply_token='test_token'):
        event = MagicMock()
        event.source.user_id = user_id
        event.message.text = text
        event.reply_token = reply_token
        return event

    def _run(self, user, text):
        event = self._make_event(user.line_user_id, text)
        with (
            patch('line_bot.event_handlers.ApiClient'),
            patch('line_bot.event_handlers.MessagingApi') as mock_cls,
            patch('line_bot.event_handlers.StateManager.get_state', return_value=UserState.WAITING_DESCRIPTION_SEARCH),
            patch('line_bot.event_handlers.StateManager.reset_state') as mock_reset,
            patch('line_bot.event_handlers.LockService.acquire', return_value=True),
            patch('line_bot.event_handlers.show_loading'),
        ):
            mock_api = MagicMock()
            mock_cls.return_value = mock_api
            handle_message(event)
        return mock_api, mock_reset

    def test_calls_description_search_handler_and_resets_state(self):
        """觸發描述搜尋 handler，並在完成後重置狀態"""
        user = UserFactory()
        with patch(
            'line_bot.event_handlers.handle_description_search_text'
        ) as mock_handler:
            mock_api, mock_reset = self._run(user, '安靜適合工作、有插座的店')

        mock_handler.assert_called_once()
        args, _ = mock_handler.call_args
        assert args[3] == '安靜適合工作、有插座的店'
        mock_reset.assert_called_once_with(user.line_user_id)

    def test_no_results_replies_not_found(self):
        """查無符合描述的店家時，回覆找不到訊息"""
        user = UserFactory()
        with patch(
            'line_bot.handlers.postback_actions.CafeSearchService.search_by_description'
        ) as mock_search:
            from cafe.services.search_service import CafeSearchResult
            from cafe.models import Cafe
            mock_search.return_value = CafeSearchResult(status='ok', cafes=Cafe.objects.none())
            mock_api, _ = self._run(user, '安靜的店')

        call_args = mock_api.reply_message.call_args[0][0]
        assert '找不到' in call_args.messages[0].text

    def test_blank_input_does_not_crash(self):
        """空白輸入不拋例外，仍完成一次回覆"""
        user = UserFactory()
        with patch(
            'line_bot.handlers.postback_actions.CafeSearchService.search_by_description'
        ) as mock_search:
            from cafe.services.search_service import CafeSearchResult
            from cafe.models import Cafe
            mock_search.return_value = CafeSearchResult(status='ok', cafes=Cafe.objects.none())
            mock_api, _ = self._run(user, '   ')

        mock_api.reply_message.assert_called_once()

    def test_end_to_end_flow_finds_matching_cafe_via_structured_attribute(self):
        """端到端整合驗證：只 mock Groq 邊界，LINE 對話輸入描述 → 解析成屬性 → 查到符合的咖啡店並回覆"""
        from cafe.tests.factories import CafeFactory as CafeFactoryForCafeApp
        from django.utils import timezone

        user = UserFactory()
        matching_cafe = CafeFactoryForCafeApp(
            name='安靜工作咖啡',
            has_socket='yes',
            ai_summary='適合安靜工作的咖啡店，插座充足。',
            attributes_last_calculated_at=timezone.now(),
        )
        CafeFactoryForCafeApp(
            name='熱鬧聚會咖啡',
            has_socket='no',
            ai_summary='適合朋友聚會聊天。',
            attributes_last_calculated_at=timezone.now(),
        )

        with patch(
            'cafe.services.search_service.GroqAPI.parse_search_description',
            return_value={'limited_time': None, 'has_socket': 'yes', 'pet_friendly': None, 'has_pet': None},
        ):
            mock_api, mock_reset = self._run(user, '安靜適合工作、有插座的店')

        mock_api.reply_message.assert_called_once()
        call_args = mock_api.reply_message.call_args[0][0]
        rendered = call_args.messages[0].contents.to_dict()
        rendered_text = str(rendered)
        assert matching_cafe.name in rendered_text
        assert '熱鬧聚會咖啡' not in rendered_text
        mock_reset.assert_called_once_with(user.line_user_id)

    def test_end_to_end_flow_no_match_replies_not_found(self):
        """端到端整合驗證：Groq 解析失敗時走關鍵字降級搜尋，查無結果時明確回覆"""
        from cafe.tests.factories import CafeFactory as CafeFactoryForCafeApp

        user = UserFactory()
        CafeFactoryForCafeApp(name='其他咖啡店', ai_summary='適合朋友聚會聊天。')

        with patch(
            'cafe.services.search_service.GroqAPI.parse_search_description',
            return_value=None,
        ):
            mock_api, mock_reset = self._run(user, '完全不相關的描述文字')

        call_args = mock_api.reply_message.call_args[0][0]
        assert '找不到' in call_args.messages[0].text
        mock_reset.assert_called_once_with(user.line_user_id)
