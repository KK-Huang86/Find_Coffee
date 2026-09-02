# Sentinel：代表 get_or_create_cafe_info 因額度不足而回傳，讓呼叫端顯示對應訊息
QUOTA_EXCEEDED = object() # 隨機生產一個物件並存放在某一個記憶體的位置


class UserState:
    NORMAL = '初始'
    WAITING_SHOP_NAME = '等待查詢店名'
    WAITING_ADDRESS = '等待查詢路名'
    WAITING_VOTE = '等待評價'
    WAITING_DISTRICT = '等待查詢行政區'
    WAITING_PET_DISTRICT = '等待查詢有貓貓狗狗的地區'
    WAITING_PET_FRIENDLY_DISTRICT = '等待查詢寵物友善的地區'
    WAITING_DESCRIPTION_SEARCH = '等待描述搜尋'


# 投票屬性順序
VOTE_ATTRIBUTES = ['socket', 'limited_time', 'pet_friendly', 'has_pet', 'quiet', 'cheap']

# 投票屬性對應的問題文字
VOTE_QUESTIONS = {
    'socket': '這間店有插座嗎？🔌',
    'limited_time': '這間店會限時嗎？⏰',
    'quiet': '這間店安靜嗎？🤫',
    'cheap': '這間店價格如何？💰',
    'has_pet': '這間店裡面有貓貓狗狗嗎？🐈',
    'pet_friendly': '這間店歡迎寵物嗎？🐕'

}

# 投票選項對應的值
VOTE_OPTIONS = {
    'socket': [
        ('yes', '有'),
        ('maybe', '部分座位'),
        ('no', '沒有'),
        ('unknown', '不確定'),
    ],
    'limited_time': [
        ('yes', '會限時'),
        ('maybe', '看情況'),
        ('no', '不限時'),
        ('unknown', '不確定'),
    ],
    'quiet': [
        ('yes', '安靜'),
        ('maybe', '普通'),
        ('no', '吵雜'),
        ('unknown', '不確定'),
    ],
    'cheap': [
        ('yes', '便宜'),
        ('maybe', '普通'),
        ('no', '偏貴'),
        ('unknown', '不確定'),
    ],
    'has_pet': [
        ('yes', '有'),
        ('no', '沒有'),
        ('unknown', '不確定'),
    ],
    'pet_friendly': [
        ('yes', '歡迎'),
        ('no', '不歡迎'),
        ('unknown', '不確定'),
    ],

}


class MenuText:
    SHARE_LOCATION = '分享位置查詢'
    SEARCH_SHOP_NAME = '店名查詢'
    SEARCH_ADDRESS = '路名查詢'
    FAVORITES = '收藏的咖啡店'
    RECENT_SEARCH = '最近查詢'
    MORE_INFO = '更多資訊'


class MenuAction:
    SHARE_LOCATION = 'share_location'
    SEARCH_SHOP_NAME = 'search_shop_name'
    SEARCH_ADDRESS = 'search_address'
    FAVORITES = 'favorites'
    RECENT_SEARCH = 'recent_search'
    MORE_INFO = 'more_info'
    DISTRICT_SEARCH = 'district_search'
    PET_SEARCH = 'pet_search'
    PET_FRIENDLY_SEARCH = 'pet_friendly_search'
    DESCRIPTION_SEARCH = 'description_search'
