"""
Postback Action Handlers - 每個 action 獨立處理
"""
import logging
from urllib.parse import urlencode, quote

from linebot.v3.messaging import ReplyMessageRequest, TextMessage, FlexMessage, FlexContainer, QuickReply, QuickReplyItem, PostbackAction, TemplateMessage

from cafe.models import Cafe, CafeAttributeVote
from integrations.groq.api import GroqAPI
from cafe.services.search_service import CafeSearchService
from cafe.services.vote_service import VoteService
from integrations.google.api import GoogleAPI
from integrations.services import ApiUsageService
from line_bot.builders.flex_builder import LineMessageBuilder, QuickReplyBuilder, FlexMessageBuilder,FavoritesPageBuilder
from line_bot.constants import UserState, MenuAction, VOTE_ATTRIBUTES, VOTE_QUESTIONS, VOTE_OPTIONS, QUOTA_EXCEEDED
from line_bot.handlers.helpers import get_or_create_cafe_info, reply_text, reply_cafe_detail
from line_bot.state import StateManager
from users.views import FavoritesManager

logger = logging.getLogger(__name__)

PAGE_SIZE = 5
FAVORITES_PAGE_SIZE = 15


def _reply_cafe_page(line_bot_api, reply_token, cafes_qs, offset, search_type, keyword, empty_msg, alt_text):
    """
    分頁輔助：從 queryset 取指定頁的資料並回覆 carousel + quick_reply。

    Args:
        cafes_qs: 已過濾 + 排序好的 QuerySet（不含 slice）
        offset:   本頁起始位置
        search_type: 'district' / 'pet' / 'pet_friendly' / 'all_pet'
        keyword:  搜尋關鍵字（用於「下一頁」postback）
        empty_msg: 無資料時的提示文字
        alt_text: carousel alt text
    """
    cafes = list(cafes_qs[offset:offset + PAGE_SIZE + 1])
    has_more = len(cafes) > PAGE_SIZE
    cafes = cafes[:PAGE_SIZE]

    if not cafes:
        reply_text(line_bot_api, reply_token, empty_msg)
        return

    flex_messages = [
        FlexMessageBuilder.create_shop_flex_message(cafe.to_dict(), is_multiple=True)
        for cafe in cafes
    ]
    carousel = {'type': 'carousel', 'contents': flex_messages}

    quick_reply = QuickReplyBuilder.create_district_search_actions(
        search_type, keyword, offset + PAGE_SIZE, has_more
    )

    flex_msg = FlexMessage(
        alt_text=alt_text,
        contents=FlexContainer.from_dict(carousel),
    )
    flex_msg.quick_reply = quick_reply

    line_bot_api.reply_message(
        ReplyMessageRequest(reply_token=reply_token, messages=[flex_msg])
    )


def handle_favorite(line_bot_api, reply_token, user, params):
    """處理收藏動作"""
    place_id = params.get('place_id')

    info_d, _ = get_or_create_cafe_info(place_id, user.id)
    if info_d is QUOTA_EXCEEDED:
        reply_text(line_bot_api, reply_token, '本月 API 查詢額度已達上限，無法取得店家資訊 😢')
        return
    if not info_d:
        reply_text(line_bot_api, reply_token, '找不到該咖啡店')
        return

    ok, msg = FavoritesManager.add_favorite(user, info_d)
    reply_text(line_bot_api, reply_token, msg)


def handle_unfavorite(line_bot_api, reply_token, user, params):
    """處理取消收藏動作"""
    place_id = params.get('place_id')

    cafe = Cafe.objects.filter(place_id=place_id).first()
    if not cafe:
        reply_text(line_bot_api, reply_token, '找不到該咖啡店，無法取消收藏')
        return

    info_d = cafe.to_dict()
    ok, msg = FavoritesManager.remove_favorite(user, info_d)
    reply_text(line_bot_api, reply_token, msg)


def handle_share(line_bot_api, reply_token, user, params):
    """處理分享動作：回傳 Google Maps 連結 + LINE 分享按鈕"""
    place_id = params.get('place_id')
    if not place_id:
        reply_text(line_bot_api, reply_token, '找不到該咖啡店')
        return

    info_d, _ = get_or_create_cafe_info(place_id, user.id)
    if not info_d or info_d is QUOTA_EXCEEDED:
        reply_text(line_bot_api, reply_token, '找不到該咖啡店' if info_d is not QUOTA_EXCEEDED else '本月 API 查詢額度已達上限 😢')
        return

    cafe_name = info_d.get('name', '咖啡店')
    if len(cafe_name) > 100:
        cafe_name = cafe_name[:97] + '...'
    maps_url = f'https://www.google.com/maps/place/?q=place_id:{place_id}'
    line_share_url = f'https://social-plugins.line.me/lineit/share?url={quote(maps_url)}'

    share_template = {
        'type': 'template',
        'altText': f'分享「{cafe_name}」',
        'template': {
            'type': 'buttons',
            'text': f'分享「{cafe_name}」',
            'actions': [
                {
                    'type': 'uri',
                    'label': '分享到 LINE',
                    'uri': line_share_url
                }
            ]
        }
    }

    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[
                TextMessage(text=f'📍 {cafe_name}\n\n長按以下連結即可複製：\n{maps_url}'),
                TemplateMessage.from_dict(share_template)
            ]
        )
    )


def handle_ask_ai(line_bot_api, reply_token, user, params):
    """呼叫 Groq 對咖啡店進行 AI 評價，結果以 Redis 快取避免重複呼叫"""
    from django.core.cache import cache

    place_id = params.get('place_id')
    if not place_id:
        reply_text(line_bot_api, reply_token, '找不到該咖啡店')
        return

    info_d, _ = get_or_create_cafe_info(place_id, user.id)
    if not info_d or info_d is QUOTA_EXCEEDED:
        reply_text(line_bot_api, reply_token, '找不到該咖啡店' if info_d is not QUOTA_EXCEEDED else '本月 API 查詢額度已達上限 😢')
        return

    cache_key = f'ai_review:{place_id}'
    result = cache.get(cache_key)

    if not result:
        if not ApiUsageService.try_increment_ai_calls(user.id):
            reply_text(line_bot_api, reply_token, '本月 AI 評價額度已達上限 😢')
            return

        result = GroqAPI.review_cafe(
            name=info_d.get('name', ''),
            address=info_d.get('address', ''),
            rating=info_d.get('rating'),
            user_ratings_total=info_d.get('user_ratings_total', 0),
            reviews=info_d.get('reviews', []),
        )
        if not result:
            ApiUsageService.revert_ai_call(user.id)
            reply_text(line_bot_api, reply_token, 'AI 評價暫時無法使用，請稍後再試')
            return
        cache.set(cache_key, result, timeout=60 * 60 * 24 * 7)  # 快取 7 天

    reply_text(line_bot_api, reply_token, f'🤖 AI 評價｜{info_d.get("name", "")}\n\n{result}')


def handle_view_detail(line_bot_api, reply_token, user, params):
    """處理查看詳情動作"""
    place_id = params.get('place_id')

    info_d, cafe = get_or_create_cafe_info(place_id, user.id)
    if info_d is QUOTA_EXCEEDED:
        reply_text(line_bot_api, reply_token, '本月 API 查詢額度已達上限，無法取得店家資訊 😢')
        return
    if not info_d:
        reply_text(line_bot_api, reply_token, '找不到此咖啡店')
        return

    is_favorited = False
    if cafe:
        is_favorited = user.favorites.filter(cafe=cafe).exists()

    reply_cafe_detail(line_bot_api, reply_token, info_d, is_favorited)


def handle_recent_search(line_bot_api, reply_token, user, params):
    """處理最近搜尋動作"""
    search_type = params.get('type')
    keyword = params.get('keyword')
    place_id = params.get('place_id')
    user_id = user.line_user_id

    if not keyword:
        logger.warning(f'Recent search without keyword: {params}')
        return

    if search_type == 'shop_name':
        _handle_shop_name_search(line_bot_api, reply_token, user, user_id, keyword, place_id)

    elif search_type == 'address':
        _handle_address_search(line_bot_api, reply_token, user, user_id, keyword)


def handle_vote(line_bot_api, reply_token, user, params):
    """會員對於該咖啡店評價"""

    place_id = params.get('place_id')

    info_d, cafe = get_or_create_cafe_info(place_id, user.id)
    if info_d is QUOTA_EXCEEDED:
        reply_text(line_bot_api, reply_token, '本月 API 查詢額度已達上限，無法取得店家資訊 😢')
        return
    if not info_d:
        reply_text(line_bot_api, reply_token, '找不到此咖啡店')
        return

    # 避免重複評價
    committed = CafeAttributeVote.objects.filter(user_id=user.id, cafe_id=cafe.id).exists()
    if committed:
        reply_text(line_bot_api, reply_token, '您已經對此咖啡店評價過了，感謝您的貢獻！')
        return

    user_id = user.line_user_id

    # 設定狀態和 context
    StateManager.set_state(user_id, UserState.WAITING_VOTE)
    StateManager.set_context(user_id, {
        'place_id': place_id,
        'cafe_id': cafe.id,
        'cafe_name': info_d.get('name', ''),
        'current_index': 0,
        'answers': {}
    })

    # 發送第一個問題
    first_attr = VOTE_ATTRIBUTES[0]
    question = VOTE_QUESTIONS[first_attr]
    quick_reply = QuickReplyBuilder.create_vote_options(first_attr)

    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[
                TextMessage(
                    text=f'📝 開始評價「{info_d.get("name", "")}」\n\n{question}',
                    quick_reply=quick_reply
                )
            ]
        )
    )


def handle_vote_answer(line_bot_api, reply_token, user, params):
    """處理投票回答"""

    user_id = user.line_user_id
    state = StateManager.get_state(user_id)

    # 檢查狀態
    if state != UserState.WAITING_VOTE:
        reply_text(line_bot_api, reply_token, '評價流程已過期，請重新開始')
        return

    context = StateManager.get_context(user_id)
    if not context:
        reply_text(line_bot_api, reply_token, '評價流程已過期，請重新開始')
        StateManager.reset_all(user_id)
        return

    # 記錄本次回答
    attr = params.get('attr')
    value = params.get('value')

    # 驗證輸入值，防止惡意請求
    valid_values_for_attr = [opt[0] for opt in VOTE_OPTIONS.get(attr, [])]
    if attr not in VOTE_ATTRIBUTES or value not in valid_values_for_attr:
        logger.warning(f'來自使用者 {user_id} 的無效投票回答: attr={attr}, value={value}')
        reply_text(line_bot_api, reply_token, '您選擇的評價選項無效，請重新開始。')
        StateManager.reset_all(user_id)
        return

    context['answers'][attr] = value

    # 移動到下一題
    current_index = context.get('current_index', 0) + 1
    context['current_index'] = current_index

    # 檢查是否還有下一題
    if current_index < len(VOTE_ATTRIBUTES):
        # 還有題目，發送下一題
        next_attr = VOTE_ATTRIBUTES[current_index]
        question = VOTE_QUESTIONS[next_attr]
        quick_reply = QuickReplyBuilder.create_vote_options(next_attr)

        StateManager.set_context(user_id, context)

        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(text=question, quick_reply=quick_reply)
                ]
            )
        )
    else:
        # 所有題目回答完畢，儲存投票
        cafe_id = context.get('cafe_id')
        answers = context.get('answers')
        cafe_name = context.get('cafe_name', '')

        VoteService.create_user_votes(cafe_id, user.id, answers)

        # 清除狀態
        StateManager.reset_all(user_id)

        reply_text(
            line_bot_api,
            reply_token,
            f'感謝您對「{cafe_name}」的評價！\n您的回饋將幫助其他使用者 ☕️'
        )


def _handle_shop_name_search(line_bot_api, reply_token, user, user_id, keyword, place_id=None):
    """處理店名搜尋"""

    # 1. 若有 place_id 直接取用。近期查詢該資料只有一筆時，才會存 place_id，否則為 None
    if place_id:
        cafe = Cafe.objects.filter(place_id=place_id).first()
        if cafe:
            info_d = cafe.to_dict()
            is_favorited = user.favorites.filter(cafe=cafe).exists()
            reply_cafe_detail(line_bot_api, reply_token, info_d, is_favorited)
            return

    # 2. 若沒有 place_id，檢查 DB 是否有此店家，搜尋的時候可能不只有一間
    cafes = Cafe.objects.filter(name__icontains=keyword)[:5]

    if cafes.exists():
        # DB 有資料，顯示 carousel（理論上這裡抓到的都是兩筆以上的資料，因為一筆的話會有 place_id）
        shops = [{'place_id': cafe.place_id, 'name': cafe.name} for cafe in cafes]

    # 3. 都沒有的話直接打 Google API 進行查詢
    else:
        if not ApiUsageService.try_increment_search_calls(user.id):
            reply_text(line_bot_api, reply_token, '本月搜尋次數已達上限，無法繼續查詢 😢')
            return
        shops = GoogleAPI.search_coffee_shops(keyword)

    LineMessageBuilder.send_shop_result(
        line_bot_api,
        reply_token,
        shops,
        user,
        quick_reply=QuickReplyBuilder.create_search_again_actions()
    )


def _handle_address_search(line_bot_api, reply_token, user, user_id, keyword):
    """處理地址搜尋"""
    if not ApiUsageService.try_increment_search_calls(user.id):
        reply_text(line_bot_api, reply_token, '本月搜尋次數已達上限，無法繼續查詢 😢')
        return
    shops = GoogleAPI.search_nearby_coffee_shops(address=keyword)

    LineMessageBuilder.send_shop_result(
        line_bot_api,
        reply_token,
        shops,
        user,
        quick_reply=QuickReplyBuilder.create_carousel_pagination_actions()
    )


def _menu_search_shop_name(line_bot_api, reply_token, user):
    """處理店名查詢"""
    StateManager.set_state(user.line_user_id, UserState.WAITING_SHOP_NAME)
    reply_text(line_bot_api, reply_token, '請輸入咖啡店名稱 ☕️')


def _menu_search_address(line_bot_api, reply_token, user):
    """處理路名查詢"""
    StateManager.set_state(user.line_user_id, UserState.WAITING_ADDRESS)
    reply_text(line_bot_api, reply_token, '請輸入路名️（例如：台北市信義區松信路）')


def _menu_share_location(line_bot_api, reply_token, user):
    """處理分享位置"""
    quick_reply = QuickReplyBuilder.create_location_request()
    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[
                TextMessage(
                    text='請點擊下方按鈕分享您的位置\n我來幫您找附近的咖啡店 ☕️',
                    quick_reply=quick_reply
                )
            ]
        )
    )


def _reply_favorites_page(line_bot_api, reply_token, user, offset):
    """列表 bubble 分頁，每頁 FAVORITES_PAGE_SIZE 筆，依收藏時間倒序"""
    favorites = list(
        user.favorites.select_related('cafe').order_by('-created_at')[offset:offset + FAVORITES_PAGE_SIZE + 1]
    )
    has_more = len(favorites) > FAVORITES_PAGE_SIZE
    favorites = favorites[:FAVORITES_PAGE_SIZE]

    if not favorites:
        reply_text(line_bot_api, reply_token, '沒有更多收藏了 ❤️')
        return

    page_num = offset // FAVORITES_PAGE_SIZE + 1
    flex_msg = FavoritesPageBuilder.build_page_message(favorites, page_num)

    if has_more:
        next_data = urlencode({
            'action': 'next_page',
            'search_type': 'favorites',
            'offset': offset + FAVORITES_PAGE_SIZE,
        })
        flex_msg.quick_reply = QuickReply(items=[
            QuickReplyItem(action=PostbackAction(label='➡️ 下一頁', data=next_data))
        ])

    line_bot_api.reply_message(
        ReplyMessageRequest(reply_token=reply_token, messages=[flex_msg])
    )


def _menu_favorites(line_bot_api, reply_token, user):
    """處理收藏清單"""
    if not user.favorites.exists():
        reply_text(line_bot_api, reply_token, '您還沒有收藏任何咖啡店喔～\n快去探索喜歡的店家吧！❤️')
        return

    _reply_favorites_page(line_bot_api, reply_token, user, offset=0)


def _menu_recent_search(line_bot_api, reply_token, user):
    """處理最近查詢"""
    user_id = user.line_user_id
    quick_reply = QuickReplyBuilder.create_recent_search_quick_reply(user_id)

    if quick_reply:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(text='請選擇最近搜尋的關鍵字：', quick_reply=quick_reply)
                ]
            )
        )
    else:
        reply_text(line_bot_api, reply_token, '目前還沒有搜尋紀錄喔～')


def _menu_more_info(line_bot_api, reply_token, user):
    """更多功能選單"""
    quick_reply = QuickReplyBuilder.create_more_info_actions()
    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[
                TextMessage(
                    text='請選擇功能 ☕️',
                    quick_reply=quick_reply
                )
            ]
        )
    )


def _menu_district_search(line_bot_api, reply_token, user):
    """處理工作友善咖啡查詢：設定狀態，等待使用者輸入行政區"""
    StateManager.set_state(user.line_user_id, UserState.WAITING_DISTRICT)
    reply_text(line_bot_api, reply_token, '請輸入行政區名稱\n（例如：大安區、信義區）')

def _menu_pet_search(line_bot_api, reply_token, user):
    """查詢有貓貓狗狗的咖啡廳：設定狀態，等待使用者輸入行政區（附全部查詢快捷）"""
    StateManager.set_state(user.line_user_id, UserState.WAITING_PET_DISTRICT)
    quick_reply = QuickReply(items=[
        QuickReplyItem(action=PostbackAction(
            label='🐈 查詢全部有貓貓狗狗的店',
            data='action=all_pet_search'
        ))
    ])
    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(
                text='請輸入行政區名稱，我幫你找有貓貓狗狗的咖啡廳 🐈\n（例如：大安區、信義區）\n\n或點下方按鈕查詢全部店家',
                quick_reply=quick_reply
            )]
        )
    )


def _menu_pet_friendly_search(line_bot_api, reply_token, user):
    """查詢寵物友善咖啡廳：設定狀態，等待使用者輸入行政區"""
    StateManager.set_state(user.line_user_id, UserState.WAITING_PET_FRIENDLY_DISTRICT)
    reply_text(line_bot_api, reply_token, '請輸入行政區名稱，我幫你找寵物友善的咖啡廳 🐕\n（例如：大安區、信義區）')


def _menu_description_search(line_bot_api, reply_token, user):
    """處理描述搜尋：設定狀態，等待使用者輸入描述"""
    StateManager.set_state(user.line_user_id, UserState.WAITING_DESCRIPTION_SEARCH)
    reply_text(
        line_bot_api, reply_token,
        '請描述你想找的咖啡店 ☕️\n（例如：安靜適合工作、有插座、歡迎寵物）'
    )


def handle_description_search_text(line_bot_api, reply_token, user, text):
    """處理使用者輸入的描述搜尋文字，依 CafeSearchResult.status 分流呈現"""
    result = CafeSearchService.search_by_description(text, user.id)

    if result.status == 'quota_exceeded':
        reply_text(line_bot_api, reply_token, '本月 AI 搜尋額度已達上限 😢')
        return

    cafes = list(result.cafes[:PAGE_SIZE])

    if not cafes:
        reply_text(
            line_bot_api, reply_token,
            '找不到符合描述的店家 😢\n\n試試看換個描述方式，或用其他方式搜尋！'
        )
        return

    flex_messages = [
        FlexMessageBuilder.create_shop_flex_message(cafe.to_dict(), is_multiple=True)
        for cafe in cafes
    ]
    carousel = {'type': 'carousel', 'contents': flex_messages}
    flex_msg = FlexMessage(
        alt_text='符合描述的咖啡店',
        contents=FlexContainer.from_dict(carousel),
    )
    line_bot_api.reply_message(
        ReplyMessageRequest(reply_token=reply_token, messages=[flex_msg])
    )


# Menu Dispatch Table
MENU_HANDLERS = {
    MenuAction.SEARCH_SHOP_NAME: _menu_search_shop_name,
    MenuAction.SEARCH_ADDRESS: _menu_search_address,
    MenuAction.SHARE_LOCATION: _menu_share_location,
    MenuAction.FAVORITES: _menu_favorites,
    MenuAction.RECENT_SEARCH: _menu_recent_search,
    MenuAction.MORE_INFO: _menu_more_info,
    MenuAction.DISTRICT_SEARCH: _menu_district_search,
    MenuAction.PET_SEARCH: _menu_pet_search,
    MenuAction.PET_FRIENDLY_SEARCH: _menu_pet_friendly_search,
    MenuAction.DESCRIPTION_SEARCH: _menu_description_search,
}


def handle_all_pet_search(line_bot_api, reply_token, user, params):
    """查詢全部有貓貓狗狗的咖啡店（不限地區），支援分頁"""
    try:
        offset = int(params.get('offset', 0))
    except (ValueError, TypeError):
        offset = 0
    cafes_qs = Cafe.objects.filter(has_pet='yes').order_by('-favorite_count', '-user_ratings_total')

    _reply_cafe_page(
        line_bot_api, reply_token,
        cafes_qs=cafes_qs,
        offset=offset,
        search_type='all_pet',
        keyword='',
        empty_msg='目前資料庫還沒有有貓貓狗狗的咖啡店資料 😢\n歡迎投票回報你知道的店家！',
        alt_text='有貓貓狗狗的咖啡廳',
    )


def handle_next_page(line_bot_api, reply_token, user, params):
    """處理翻頁動作"""
    search_type = params.get('search_type')
    keyword = params.get('keyword', '')
    try:
        offset = int(params.get('offset', 0))
    except (ValueError, TypeError):
        offset = 0

    if search_type == 'favorites':
        _reply_favorites_page(line_bot_api, reply_token, user, offset)
        return

    search_configs = {
        'district': {
            'queryset': Cafe.objects.work_friendly().filter(address__icontains=keyword),
            'empty_msg': f'「{keyword}」沒有更多工作友善咖啡店了 ☕️',
            'alt_text': f'{keyword} 工作友善咖啡',
        },
        'pet': {
            'queryset': Cafe.objects.filter(address__icontains=keyword, has_pet='yes'),
            'empty_msg': f'「{keyword}」沒有更多有貓貓狗狗的咖啡店了 🐈',
            'alt_text': f'{keyword} 有貓貓狗狗的咖啡廳',
        },
        'pet_friendly': {
            'queryset': Cafe.objects.filter(address__icontains=keyword, pet_friendly='yes'),
            'empty_msg': f'「{keyword}」沒有更多寵物友善的咖啡店了 🐕',
            'alt_text': f'{keyword} 寵物友善咖啡廳',
        },
        'all_pet': {
            'queryset': Cafe.objects.filter(has_pet='yes'),
            'empty_msg': '沒有更多有貓貓狗狗的咖啡店了 🐈',
            'alt_text': '有貓貓狗狗的咖啡廳',
        },
    }

    config = search_configs.get(search_type)
    if not config:
        logger.warning(f'Unknown search_type for next_page: {search_type}')
        return

    cafes_qs = config['queryset'].order_by('-favorite_count', '-user_ratings_total')

    _reply_cafe_page(
        line_bot_api, reply_token,
        cafes_qs=cafes_qs,
        offset=offset,
        search_type=search_type,
        keyword=keyword,
        empty_msg=config['empty_msg'],
        alt_text=config['alt_text'],
    )


def handle_menu(line_bot_api, reply_token, user, params):
    """處理 Rich Menu 選單動作（使用字典分派）"""
    menu_type = params.get('type')
    handler = MENU_HANDLERS.get(menu_type)

    if handler:
        handler(line_bot_api, reply_token, user)
    else:
        logger.warning(f'Unknown menu type: {menu_type}')


# Action Dispatch Table
ACTION_HANDLERS = {
    'favorite': handle_favorite,
    'unfavorite': handle_unfavorite,
    'share': handle_share,
    'ask_ai': handle_ask_ai,
    'view_detail': handle_view_detail,
    'recent_search': handle_recent_search,
    'vote': handle_vote,
    'vote_answer': handle_vote_answer,
    'menu': handle_menu,
    'all_pet_search': handle_all_pet_search,
    'next_page': handle_next_page,
}
