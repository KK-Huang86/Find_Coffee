import json
import logging

from linebot.v3.messaging import (
    ReplyMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer,
)
from datetime import timedelta

from django.utils import timezone

from cafe.models import Cafe
from cafe.services.attribute_matcher import CafeAttributeMatcher
from cafe.tasks import generate_cafe_ai_summary, refresh_cafe_data
from integrations.google.api import GoogleAPI
from integrations.services import ApiUsageService
from line_bot.constants import QUOTA_EXCEEDED
from line_bot.utils import parse_opening_hours

logger = logging.getLogger(__name__)


def get_or_create_cafe_info(place_id, user_id):
    """
    從 DB 取得咖啡店資訊，若無則從 Google API 取得並存入 DB。

    Args:
        place_id (str): Google Places ID
        user_id (int): 使用者 DB 主鍵，用於 API 額度追蹤

    Returns:
        tuple: (info_dict, cafe_object)
               若失敗或額度不足則回傳 (None, None)
    """

    DATA_REFRESH_DAYS = 30

    # 1. 先查 DB
    cafe = Cafe.objects.filter(place_id=place_id).first()
    if cafe:
        # 檢查該咖啡店的資訊是否需要刷新，走同步，因為有些資訊需即時更新
        last_refreshed = cafe.last_refreshed
        now = timezone.now()
        if last_refreshed is None or now - last_refreshed >= timedelta(days=DATA_REFRESH_DAYS):
            refresh_cafe_data(cafe.id)
            cafe.refresh_from_db()  # 同步更新後，重新從 DB 取得最新資料
        # 是否有插頭 或者 限時的資料，沒有的話去找 CafeNomadCache 是否有資料
        if cafe.has_socket is None or cafe.limited_time is None:
            try:
                if CafeAttributeMatcher.match_and_sync_attributes(cafe):
                    cafe.refresh_from_db()
            except Exception as e:
                logger.warning(f'查詢 CafeNomadCache 失敗 {e}', exc_info=True)
        return cafe.to_dict(), cafe




    # 2. DB 沒有，先確認 API 額度，再呼叫 Google API
    if not ApiUsageService.try_increment_detail_calls(user_id):
        logger.warning(f'User {user_id} 超過 API 額度，無法查詢 {place_id}')
        return QUOTA_EXCEEDED, None

    info_d = GoogleAPI.get_shop_detail(place_id)

    if not info_d:
        return None, None

    if not info_d.get('place_id'):
        logger.error('缺少 place_id，無法建立店家資料')
        return None, None

    # 3. 存入 DB
    try:
        opening_hours_l = info_d.get('opening_hours', [])
        opening_hours_d = parse_opening_hours(opening_hours_l)

        cafe = Cafe.objects.create(
            place_id=info_d['place_id'],
            name=info_d.get('name') or '未提供名稱',
            address=info_d.get('address') or '未提供地址',
            phone=info_d.get('phone') or '未提供電話',
            rating=info_d.get('rating'),
            user_ratings_total=info_d.get('user_ratings_total', 0),
            google_maps=info_d.get('google_maps') or '',
            website=info_d.get('website') or '',
            lat=info_d.get('lat') or 0.0,
            lng=info_d.get('lng') or 0.0,
            opening_hours=opening_hours_d,
            reviews=info_d.get('reviews') or [],
            photo_reference=info_d.get('photo_reference') or '',
            last_refreshed=timezone.now(),
        )
        logger.info(f'建立新咖啡店: {cafe.name} ({cafe.place_id})')

        if cafe.reviews:
            generate_cafe_ai_summary.delay(cafe.id)

        # 從 Google API 查詢的咖啡店，理論上沒有 插頭 跟 是否為不限時的選項，查 CafeNomadCache
        try:
            CafeAttributeMatcher.match_and_sync_attributes(cafe)
        except Exception as e:
            logger.warning(f'查詢 CafeNomadCache 失敗 {e}', exc_info=True)

        return cafe.to_dict(), cafe

    except Exception as e:
        logger.error(f'儲存店家資料時發生錯誤: {e}', exc_info=True)
        return None, None


def reply_text(line_bot_api, reply_token, text):
    """簡單文字回覆"""
    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text)]
        )
    )


def reply_cafe_detail(line_bot_api, reply_token, info_d, is_favorited=False):
    """
    回覆咖啡店詳細資訊 (Flex Message + 操作按鈕)
    """
    # 延遲導入避免循環引用
    from line_bot.builders.flex_builder import FlexMessageBuilder, PostbackBuilder

    flex_data = FlexMessageBuilder.create_shop_flex_message(info_d)
    flex_container = FlexContainer.from_json(json.dumps(flex_data))

    button_message = PostbackBuilder.create_cafe_action_postback(
        info_d=info_d,
        is_favorited=is_favorited
    )

    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[
                FlexMessage(
                    alt_text='咖啡店詳細資訊',
                    contents=flex_container
                ),
                button_message
            ]
        )
    )
