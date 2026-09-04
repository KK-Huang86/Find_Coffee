from urllib.parse import urlencode

from linebot.v3.messaging import (
    QuickReply,
    QuickReplyItem,
    LocationAction,
    PostbackAction,
)

from line_bot.constants import MenuText, MenuAction, VOTE_OPTIONS
from line_bot.services.search_cache import SearchHistoryService


class QuickReplyBuilder:

    @staticmethod
    def create_search_again_actions():
        """
        搜尋後的通用快速回覆
        適用情境: 單一咖啡店、多間咖啡店(2-4間)
        """
        return QuickReply(
            items=[
                QuickReplyItem(
                    action=PostbackAction(
                        label='🔍 再找一間',
                        data=f'action=menu&type={MenuAction.SEARCH_SHOP_NAME}'
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label='📍 附近搜尋',
                        data=f'action=menu&type={MenuAction.SHARE_LOCATION}'
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label='❤️ 我的收藏',
                        data=f'action=menu&type={MenuAction.FAVORITES}'
                    )
                )
            ]
        )

    @staticmethod
    def create_carousel_pagination_actions(has_more=False):
        """
        搜尋後的通用快速回覆
        適用情境: 搜尋地名、分享位置（5間以上）
        """
        return QuickReply(
            items=[
                QuickReplyItem(
                    action=PostbackAction(
                        label='🔍 再用路名查一次',
                        data=f'action=menu&type={MenuAction.SEARCH_ADDRESS}'
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label='🔍 店名查詢',
                        data=f'action=menu&type={MenuAction.SEARCH_SHOP_NAME}'
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label='📍 附近搜尋',
                        data=f'action=menu&type={MenuAction.SHARE_LOCATION}'
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label='❤️ 我的收藏',
                        data=f'action=menu&type={MenuAction.FAVORITES}'
                    )
                )
            ]
        )

    @staticmethod
    def create_location_request():
        """
        請求使用者分享位置的快速回覆
        """
        return QuickReply(
            items=[
                QuickReplyItem(
                    action=LocationAction(
                        label='📍 分享目前位置',
                        text=MenuText.SHARE_LOCATION
                    )
                )
            ]
        )

    @staticmethod
    def create_recent_search_quick_reply(user_id):
        """產生最近搜尋的 Quick Reply"""
        history = SearchHistoryService.get_search_history(user_id)

        if not history:
            return None

        items = []
        for record in history[:5]:
            keyword = record['keyword']
            search_type = record['type']
            place_id = record.get('place_id')

            icon = '☕️' if search_type == "shop_name" else '📍'

            label = f'{icon} {keyword}'
            label = label[:18]  # 保守限制

            payload = {
                'action': 'recent_search',
                'type': search_type,
                'keyword': keyword,
                'place_id': place_id,
            }

            data = urlencode(payload)

            items.append(
                QuickReplyItem(
                    action=PostbackAction(
                        label=label,
                        data=data,
                        display_text=keyword  # 用戶點擊後顯示的文字
                    )
                )
            )

        return QuickReply(items=items)

    @staticmethod
    def create_more_info_actions():
        """更多功能的選項"""
        return QuickReply(
            items=[
                QuickReplyItem(
                    action=PostbackAction(
                        label='🏙️ 工作友善咖啡',
                        data=f'action=menu&type={MenuAction.DISTRICT_SEARCH}'
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label='🐈 有貓貓狗狗的咖啡廳',
                        data=f'action=menu&type={MenuAction.PET_SEARCH}'
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label='🐕 寵物友善咖啡廳',
                        data=f'action=menu&type={MenuAction.PET_FRIENDLY_SEARCH}'
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label='📝 描述搜尋',
                        data=f'action=menu&type={MenuAction.DESCRIPTION_SEARCH}'
                    )
                ),
            ]
        )

    @staticmethod
    def create_district_search_actions(search_type, keyword, next_offset, has_more=False):
        """
        工作友善 / 寵物搜尋結果後的快速回覆
        - 固定顯示「重新搜尋」
        - has_more=True 時額外顯示「下一頁」
        """
        # 重新搜尋對應的 postback
        re_search_map = {
            'district': f'action=menu&type={MenuAction.DISTRICT_SEARCH}',
            'pet': f'action=menu&type={MenuAction.PET_SEARCH}',
            'pet_friendly': f'action=menu&type={MenuAction.PET_FRIENDLY_SEARCH}',
            'all_pet': 'action=all_pet_search',
            'favorites': f'action=menu&type={MenuAction.FAVORITES}',
        }
        re_search_data = re_search_map.get(search_type, f'action=menu&type={MenuAction.SEARCH_SHOP_NAME}')

        items = [
            QuickReplyItem(
                action=PostbackAction(
                    label='🔍 重新搜尋',
                    data=re_search_data
                )
            )
        ]

        if has_more:
            next_data = urlencode({
                'action': 'next_page',
                'search_type': search_type,
                'keyword': keyword,
                'offset': next_offset,
            })
            items.append(
                QuickReplyItem(
                    action=PostbackAction(
                        label='➡️ 下一頁',
                        data=next_data
                    )
                )
            )

        return QuickReply(items=items)

    @staticmethod
    def create_vote_options(attribute):
        """
        產生投票選項的 Quick Reply
        Args:
            attribute: 屬性名稱 (socket, limited_time, quiet, cheap)
        """

        options = VOTE_OPTIONS.get(attribute, [])
        items = []

        for value, label in options:
            items.append(
                QuickReplyItem(
                    action=PostbackAction(
                        label=label,
                        data=f'action=vote_answer&attr={attribute}&value={value}',
                        display_text=label
                    )
                )
            )

        return QuickReply(items=items)
