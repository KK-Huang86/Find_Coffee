from unittest.mock import patch

from linebot.v3.messaging import (
    QuickReply,
    LocationAction,
)

from line_bot.builders.quick_reply import QuickReplyBuilder
from line_bot.constants import MenuAction


class TestQuickReplyBuilderCreateSearchAgainActions:
    """測試 QuickReplyBuilder.create_search_again_actions"""

    def test_returns_quick_reply_with_three_items(self):
        """回傳 QuickReply 包含 3 個選項"""
        result = QuickReplyBuilder.create_search_again_actions()
        assert isinstance(result, QuickReply)
        assert len(result.items) == 3

    def test_contains_search_shop_name_action(self):
        """包含『再找一間』選項"""
        result = QuickReplyBuilder.create_search_again_actions()
        datas = [item.action.data for item in result.items]
        assert any(MenuAction.SEARCH_SHOP_NAME in d for d in datas)

    def test_contains_share_location_action(self):
        """包含『附近搜尋』選項"""
        result = QuickReplyBuilder.create_search_again_actions()
        datas = [item.action.data for item in result.items]
        assert any(MenuAction.SHARE_LOCATION in d for d in datas)

    def test_contains_favorites_action(self):
        """包含『我的收藏』選項"""
        result = QuickReplyBuilder.create_search_again_actions()
        datas = [item.action.data for item in result.items]
        assert any(MenuAction.FAVORITES in d for d in datas)


class TestQuickReplyBuilderCreateMoreInfoActions:
    """測試 QuickReplyBuilder.create_more_info_actions"""

    def test_contains_description_search_action(self):
        """包含『描述搜尋』入口，觸發 DESCRIPTION_SEARCH 選單動作"""
        result = QuickReplyBuilder.create_more_info_actions()
        datas = [item.action.data for item in result.items]
        assert any(MenuAction.DESCRIPTION_SEARCH in d for d in datas)


class TestQuickReplyBuilderCreateCarouselPaginationActions:
    """測試 QuickReplyBuilder.create_carousel_pagination_actions"""

    def test_returns_quick_reply_with_four_items(self):
        """回傳 QuickReply 包含 4 個選項"""
        result = QuickReplyBuilder.create_carousel_pagination_actions()
        assert isinstance(result, QuickReply)
        assert len(result.items) == 4

    def test_contains_search_address_action(self):
        """包含路名查詢選項"""
        result = QuickReplyBuilder.create_carousel_pagination_actions()
        datas = [item.action.data for item in result.items]
        assert any(MenuAction.SEARCH_ADDRESS in d for d in datas)


class TestQuickReplyBuilderCreateLocationRequest:
    """測試 QuickReplyBuilder.create_location_request"""

    def test_returns_quick_reply_with_location_action(self):
        """回傳 QuickReply 包含 1 個 LocationAction"""
        result = QuickReplyBuilder.create_location_request()
        assert isinstance(result, QuickReply)
        assert len(result.items) == 1
        assert isinstance(result.items[0].action, LocationAction)

    def test_location_action_label(self):
        """LocationAction 標籤為分享位置"""
        result = QuickReplyBuilder.create_location_request()
        assert '分享' in result.items[0].action.label


class TestQuickReplyBuilderCreateRecentSearchQuickReply:
    """測試 QuickReplyBuilder.create_recent_search_quick_reply"""

    def test_returns_none_when_no_history(self):
        """無搜尋紀錄時回傳 None"""
        with patch('line_bot.builders.quick_reply.SearchHistoryService.get_search_history', return_value=[]):
            result = QuickReplyBuilder.create_recent_search_quick_reply('user123')
        assert result is None

    def test_returns_quick_reply_with_history(self):
        """有搜尋紀錄時回傳 QuickReply"""
        history = [
            {'keyword': '星巴克', 'type': 'shop_name', 'place_id': 'p1'},
            {'keyword': '信義路', 'type': 'address', 'place_id': None},
        ]
        with patch('line_bot.builders.quick_reply.SearchHistoryService.get_search_history', return_value=history):
            result = QuickReplyBuilder.create_recent_search_quick_reply('user123')
        assert isinstance(result, QuickReply)
        assert len(result.items) == 2

    def test_shop_name_uses_coffee_icon(self):
        """shop_name 類型使用 ☕️ 圖示"""
        history = [{'keyword': '星巴克', 'type': 'shop_name', 'place_id': None}]
        with patch('line_bot.builders.quick_reply.SearchHistoryService.get_search_history', return_value=history):
            result = QuickReplyBuilder.create_recent_search_quick_reply('user123')
        assert '☕️' in result.items[0].action.label

    def test_address_type_uses_pin_icon(self):
        """address 類型使用 📍 圖示"""
        history = [{'keyword': '信義路', 'type': 'address', 'place_id': None}]
        with patch('line_bot.builders.quick_reply.SearchHistoryService.get_search_history', return_value=history):
            result = QuickReplyBuilder.create_recent_search_quick_reply('user123')
        assert '📍' in result.items[0].action.label

    def test_label_is_truncated_to_18_chars(self):
        """標籤超過 18 字元時截斷"""
        long_keyword = 'a' * 20
        history = [{'keyword': long_keyword, 'type': 'shop_name', 'place_id': None}]
        with patch('line_bot.builders.quick_reply.SearchHistoryService.get_search_history', return_value=history):
            result = QuickReplyBuilder.create_recent_search_quick_reply('user123')
        assert len(result.items[0].action.label) <= 18

    def test_history_limited_to_five_items(self):
        """最多顯示 5 筆搜尋紀錄"""
        history = [{'keyword': f'店家{i}', 'type': 'shop_name', 'place_id': None} for i in range(8)]
        with patch('line_bot.builders.quick_reply.SearchHistoryService.get_search_history', return_value=history):
            result = QuickReplyBuilder.create_recent_search_quick_reply('user123')
        assert len(result.items) == 5

    def test_display_text_is_keyword(self):
        """PostbackAction 的 display_text 為搜尋關鍵字本身"""
        history = [{'keyword': '星巴克', 'type': 'shop_name', 'place_id': None}]
        with patch('line_bot.builders.quick_reply.SearchHistoryService.get_search_history', return_value=history):
            result = QuickReplyBuilder.create_recent_search_quick_reply('user123')
        assert result.items[0].action.display_text == '星巴克'
