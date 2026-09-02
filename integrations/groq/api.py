import json
import logging

from groq import Groq
from decouple import config

logger = logging.getLogger(__name__)

CAFE_REVIEW_PROMPT = """你是一位咖啡店評論專家。請根據以下資訊對這間咖啡店進行說明，
包含氛圍、適合族群、是否適合工作或讀書，還有該店推薦的商品。

咖啡店名稱：{name}
地址：{address}
Google 評分：{rating}（{user_ratings_total} 則評論）
用戶評論：
{reviews}

請用繁體中文回答，150 字以內。"""

SUMMARIZE_FOR_SEARCH_PROMPT = """你是一位咖啡店描述專家。請根據以下用戶評論，寫一段這間咖啡店的摘要，
內容需包含氛圍、適合族群、環境特色（例如是否安靜、適合工作或聊天）。這段摘要會被用來比對其他使用者的搜尋描述，
請盡量具體描述明顯的特徵。

咖啡店名稱：{name}
用戶評論：
{reviews}

請用繁體中文回答，100 字以內，只寫摘要內容本身，不要加任何前綴、標題或引號。"""

SEARCH_DESCRIPTION_ALLOWED_FIELDS = ('limited_time', 'has_socket', 'pet_friendly', 'has_pet')
SEARCH_DESCRIPTION_ALLOWED_VALUES = ('yes', 'maybe', 'no', None)

PARSE_SEARCH_DESCRIPTION_PROMPT = """你是一個咖啡店搜尋條件解析器。請閱讀使用者對想去的咖啡店的描述，
判斷描述中是否提到以下屬性，並只用一個 JSON 物件回答，不要有任何其他文字、說明或 Markdown 標記。

JSON 的 key 必須恰好是以下四個（不可新增其他 key）：
- "limited_time"：是否限時
- "has_socket"：是否有插座
- "pet_friendly"：是否歡迎寵物
- "has_pet"：店內是否有貓狗

每個 key 的 value 只能是以下四種之一：
- "yes"：描述中明確提到需要這個屬性
- "no"：描述中明確提到不需要/不要這個屬性
- "maybe"：描述中提到但不明確
- null：描述中完全沒有提到這個屬性

使用者描述：
{description}

請直接輸出 JSON，例如：{{"limited_time": "no", "has_socket": "yes", "pet_friendly": null, "has_pet": null}}"""


class GroqAPI:
    _client = None

    @classmethod
    def review_cafe(cls, name: str, address: str, rating: float, user_ratings_total: int, reviews: list) -> str | None:
        try:
            if cls._client is None:
                cls._client = Groq(api_key=config('GROQ_API_KEY'))
            reviews_text = '\n'.join(f'- {r}' for r in reviews) if reviews else '無用戶評論'
            prompt = CAFE_REVIEW_PROMPT.format(
                name=name,
                address=address,
                rating=rating if rating is not None else '無資料',
                user_ratings_total=user_ratings_total if user_ratings_total is not None else 0,
                reviews=reviews_text,
            )
            response = cls._client.chat.completions.create(
                model='openai/gpt-oss-20b',
                messages=[{'role': 'user', 'content': prompt}],
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f'Groq API 呼叫失敗: {e}', exc_info=True)
            return None

    @classmethod
    def summarize_for_search(cls, name: str, reviews: list) -> str | None:
        """產生可落地儲存的摘要文字，供描述搜尋比對用"""
        if not reviews:
            return None
        try:
            if cls._client is None:
                cls._client = Groq(api_key=config('GROQ_API_KEY'))
            reviews_text = '\n'.join(f'- {r}' for r in reviews)
            prompt = SUMMARIZE_FOR_SEARCH_PROMPT.format(name=name, reviews=reviews_text)
            response = cls._client.chat.completions.create(
                model='openai/gpt-oss-20b',
                messages=[{'role': 'user', 'content': prompt}],
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f'Groq API 呼叫失敗（summarize_for_search）: {e}', exc_info=True)
            return None

    @classmethod
    def parse_search_description(cls, description: str) -> dict | None:
        """把使用者的自然語言描述解析成結構化屬性條件，任一環節不合法即整體回傳 None"""
        if not description or not description.strip():
            return None
        try:
            if cls._client is None:
                cls._client = Groq(api_key=config('GROQ_API_KEY'))
            prompt = PARSE_SEARCH_DESCRIPTION_PROMPT.format(description=description)
            response = cls._client.chat.completions.create(
                model='openai/gpt-oss-20b',
                messages=[{'role': 'user', 'content': prompt}],
            )
            content = response.choices[0].message.content
            parsed = json.loads(content)
        except Exception as e:
            logger.error(f'Groq API 呼叫失敗（parse_search_description）: {e}', exc_info=True)
            return None

        if not isinstance(parsed, dict):
            return None

        if not set(parsed.keys()).issubset(SEARCH_DESCRIPTION_ALLOWED_FIELDS):
            return None

        if any(value not in SEARCH_DESCRIPTION_ALLOWED_VALUES for value in parsed.values()):
            return None

        return {field: parsed.get(field) for field in SEARCH_DESCRIPTION_ALLOWED_FIELDS}
