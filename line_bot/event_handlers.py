import logging
from datetime import timedelta
from decouple import config
from urllib.parse import parse_qs

from django.utils import timezone

from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    MessageEvent,
    FollowEvent,
    TextMessageContent,
    LocationMessageContent,
    PostbackEvent

)

from integrations.google.api import GoogleAPI, OutOfServiceAreaError
from line_bot.constants import UserState
from line_bot.builders.flex_builder import LineMessageBuilder, QuickReplyBuilder
from line_bot.handlers.postback_actions import ACTION_HANDLERS, _reply_cafe_page, handle_description_search_text
from line_bot.handlers.helpers import reply_text
from line_bot.services.search_cache import SearchHistoryService
from line_bot.state import StateManager
from line_bot.utils import show_loading
from users.models import User
from cafe.models import Cafe
from cafe.tasks import refresh_cafe_data
from utils.utils import LockService

logger = logging.getLogger(__name__)
LINE_CHANNEL_ACCESS_TOKEN = config('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = config('LINE_CHANNEL_SECRET')

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    User.objects.get_or_create(line_user_id=user_id)


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        user_id = event.source.user_id
        text = event.message.text
        state = StateManager.get_state(user_id)
        user = User.objects.get(line_user_id=user_id)

        if not LockService.acquire(user_id, 'message'):
            logger.info(f'User {user_id} throttled, skip processing')
            return

        # 顯示打字中動畫
        show_loading(line_bot_api, user_id)

        # 使用者查詢單一咖啡店，回傳結果
        if state == UserState.WAITING_SHOP_NAME:
            # 1. 先查本地 DB
            cached_cafes = Cafe.objects.filter(name__icontains=text)[:10]

            if cached_cafes:
                now = timezone.now()
                for cafe in cached_cafes:
                    if cafe.last_refreshed is None or now - cafe.last_refreshed >= timedelta(days=30):
                        if LockService.acquire(str(cafe.id), 'refresh', ttl=600): # 避免不同人同時觸發更新
                            refresh_cafe_data.delay(cafe.id)
                shops = [{'place_id': cafe.place_id} for cafe in cached_cafes]
            else:
                # 本地沒有，打 Google API
                shops = GoogleAPI.search_coffee_shops(text)

            # 只有 1 筆結果才存 place_id，多筆用 keyword 重搜
            place_id = shops[0]['place_id'] if len(shops) == 1 else None

            # 紀錄搜尋歷史
            SearchHistoryService.add_search(user_id, text, search_type='shop_name', place_id=place_id)

            LineMessageBuilder.send_shop_result(
                line_bot_api,
                event.reply_token,
                shops,
                user,
                quick_reply=QuickReplyBuilder.create_search_again_actions()
            )
            StateManager.reset_state(user_id)
            return

        # 使用者查詢某一路名的咖啡店，回傳結果
        elif state == UserState.WAITING_ADDRESS:

            # 紀錄 Cache
            SearchHistoryService.add_search(user_id, text, search_type='address')

            try:
                shops = GoogleAPI.search_nearby_coffee_shops(address=text)
            except OutOfServiceAreaError:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=(
                            f'「{text}」超出服務範圍 📍\n\n'
                            '本服務目前僅提供台北、新北、基隆地區搜尋，請輸入上述地區的地址。'
                        ))]
                    )
                )
                StateManager.reset_state(user_id)
                return
            LineMessageBuilder.send_shop_result(
                line_bot_api,
                event.reply_token,
                shops,
                user,
                quick_reply=QuickReplyBuilder.create_carousel_pagination_actions()
            )
            StateManager.reset_state(user_id)
            return

        # 使用者輸入行政區查詢工作友善咖啡
        elif state == UserState.WAITING_DISTRICT:
            district = text.strip()
            cafes_qs = Cafe.objects.work_friendly().filter(
                address__icontains=district,
            ).order_by('-favorite_count', '-user_ratings_total')
            _reply_cafe_page(
                line_bot_api, event.reply_token,
                cafes_qs=cafes_qs,
                offset=0,
                search_type='district',
                keyword=district,
                empty_msg=(
                    f'找不到「{district}」不限時且有插座的咖啡店 😢\n\n'
                    '目前 DB 資料有限，建議先用其他方式搜尋該區店家，累積資料後再試試看！'
                ),
                alt_text=f'{district} 工作友善咖啡',
            )
            StateManager.reset_state(user_id)
            return

        elif state == UserState.WAITING_PET_DISTRICT:
            district = text.strip()
            cafes_qs = Cafe.objects.filter(
                address__icontains=district,
                has_pet='yes',
            ).order_by('-favorite_count', '-user_ratings_total')
            _reply_cafe_page(
                line_bot_api, event.reply_token,
                cafes_qs=cafes_qs,
                offset=0,
                search_type='pet',
                keyword=district,
                empty_msg=(
                    f'找不到「{district}」有貓貓狗狗的咖啡店 🐈\n\n'
                    '目前 DB 資料有限，建議先用其他方式搜尋該區店家，累積資料後再試試看！'
                ),
                alt_text=f'{district} 有貓貓狗狗的咖啡廳',
            )
            StateManager.reset_state(user_id)
            return

        elif state == UserState.WAITING_PET_FRIENDLY_DISTRICT:
            district = text.strip()
            cafes_qs = Cafe.objects.filter(
                address__icontains=district,
                pet_friendly='yes',
            ).order_by('-favorite_count', '-user_ratings_total')
            _reply_cafe_page(
                line_bot_api, event.reply_token,
                cafes_qs=cafes_qs,
                offset=0,
                search_type='pet_friendly',
                keyword=district,
                empty_msg=(
                    f'找不到「{district}」寵物友善的咖啡店 🐕\n\n'
                    '目前 DB 資料有限，建議先用其他方式搜尋該區店家，累積資料後再試試看！'
                ),
                alt_text=f'{district} 寵物友善咖啡廳',
            )
            StateManager.reset_state(user_id)
            return

        # 使用者輸入自然語言描述，搜尋符合描述的咖啡店
        elif state == UserState.WAITING_DESCRIPTION_SEARCH:
            handle_description_search_text(line_bot_api, event.reply_token, user, text)
            StateManager.reset_state(user_id)
            return

        # 使用者輸入非預期文字
        else:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='請使用選單功能來查詢咖啡店 ☕️')]
                )
            )


# 分享位置查詢
@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        lat = event.message.latitude
        lng = event.message.longitude
        user_id = event.source.user_id
        user = User.objects.filter(line_user_id=user_id).first()

        if not user:
            logger.warning(f'User not found: {user_id}')
            reply_text(line_bot_api, event.reply_token, '找不到會員資料，請重新操作')
            return

        if not LockService.acquire(user_id, 'location'):
            logger.info(f'User {user_id} throttled, skip processing')
            return

        # 顯示打字中動畫
        show_loading(line_bot_api, user_id)

        try:
            shops = GoogleAPI.search_nearby_coffee_shops(lat=lat, lng=lng)
        except OutOfServiceAreaError:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=(
                        '您目前的位置超出服務範圍 📍\n\n'
                        '本服務目前僅提供台北、新北、基隆地區\n'
                        '請分享上述地區的位置，或輸入地址搜尋。'
                    ))]
                )
            )
            return
        logger.info(shops)

        if not shops:
            logger.warning('️沒有找到任何咖啡店')
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(
                            text=(
                                '附近 500 公尺內找不到咖啡店 😢\n\n'
                                '試試看：\n'
                                '1️⃣ 分享其他位置\n'
                                '2️⃣ 輸入地址搜尋'
                            )
                        )
                    ]
                )
            )
            return

        LineMessageBuilder.send_shop_result(line_bot_api, event.reply_token, shops, user)


def _parse_postback_data(data: str) -> dict:
    parsed = parse_qs(data)
    return {k: v[0] for k, v in parsed.items()}


# postback 處理
@handler.add(PostbackEvent)
def handle_postback(event):
    """
    postback 統一格式為 action=XXX&param1=XXX&param2=XXX
    使用 ACTION_HANDLERS dispatch table 路由到對應的 handler
    """
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        data = event.postback.data

        # 解析 postback data
        params = _parse_postback_data(data)

        user_id = event.source.user_id
        user = User.objects.filter(line_user_id=user_id).first()

        if not user:
            logger.warning(f'User not found: {user_id}')
            reply_text(line_bot_api, event.reply_token, '找不到會員資料，請重新操作')
            return

        action = params.get('action')

        # 使用獨立 lock key 避免不同動作互相阻擋（如 vote 阻擋 vote_answer）
        # vote_answer 縮短 TTL 以提升快速作答體驗，同時防止重複點擊
        lock_category = f'postback:{action}' if action == 'vote_answer' else 'postback'
        lock_ttl = 1 if action == 'vote_answer' else 2
        if not LockService.acquire(user_id, lock_category, ttl=lock_ttl):
            logger.info(f'User {user_id} throttled, skip processing')
            return

        # 顯示打字中動畫
        show_loading(line_bot_api, user_id)

        handler_func = ACTION_HANDLERS.get(action)

        if handler_func:
            handler_func(line_bot_api, event.reply_token, user, params)
        else:
            logger.warning(f'Unknown postback action: {action}')
