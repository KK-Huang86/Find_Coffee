import logging
import requests

from celery import shared_task
from django.utils import timezone
from datetime import timedelta

from cafe.models import Cafe
from integrations.google.api import GoogleAPI
from integrations.groq.api import GroqAPI
from integrations.services import ApiUsageService

from decouple import config
import boto3  # Python SDK
from botocore.config import Config
from io import BytesIO

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # 60 秒後重試
    autoretry_for=(Exception,),  # 自動重試所有例外
)
def download_and_upload_cafe_photo(self, cafe_id):
    """
    異步任務：下載咖啡店照片並上傳到 S3

    Args:
        cafe_id (int): Cafe 的 primary key

    Returns:
        dict: 執行結果
    """
    try:
        # 1. 取得咖啡店資料
        try:
            cafe = Cafe.objects.get(id=cafe_id)
        except Cafe.DoesNotExist:
            logger.error(f' Cafe ID {cafe_id} 不存在')
            return {'status': 'failed', 'reason': 'cafe_not_found'}

        # 2. 檢查是否有photo_reference

        if not cafe.photo_reference:
            logger.info(f'跳過，name：{cafe.name}，place_id：{cafe.place_id}，沒有 photo_reference')
            return {'status': 'skipped', 'reason': 'no_photo_reference'}

        # 3. 檢查該咖啡店的照片是否上傳至 S3

        if cafe.photo_s3_url:
            logger.info(f'跳過，name：{cafe.name}，place_id：{cafe.place_id}，已經上傳照片至s3')
            return {'status': 'skipped', 'reason': 'already_uploaded'}

        logger.info(f'開始下載照片：{cafe.name} (place_id：{cafe.place_id})')

        # 4. 從 Google 下載照片
        photo_url = (
            f'https://maps.googleapis.com/maps/api/place/photo'
            f'?photo_reference={cafe.photo_reference}'
            f'&maxwidth=400'
            f'&key={config("GOOGLE_API_KEY")}'
        )

        response = requests.get(photo_url, timeout=10, stream=True)
        response.raise_for_status()

        # 檢查 content-type
        content_type = response.headers.get('Content-Type', '')
        if 'image' not in content_type:
            logger.warning(f'非圖片類型：{content_type}')
            return {'status': 'failed', 'reason': 'invalid_content_type'}

        # 4. 上傳到 S3
        s3_config = Config(
            retries={'max_attempts': 3, 'mode': 'standard'},  # S3 請求層級重試，task 曾也有自動重跑機制
            connect_timeout=10,
            read_timeout=30
        )
        s3_client = boto3.client(
            's3',
            aws_access_key_id=config('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=config('AWS_SECRET_ACCESS_KEY'),
            region_name=config('AWS_REGION', default='ap-northeast-1'),
            config=s3_config
        )

        bucket_name = config('S3_BUCKET_NAME')
        s3_key = f'cafe-photos/{cafe.place_id}.jpg'

        logger.info(f'上傳到 S3：{s3_key}')

        s3_client.upload_fileobj(
            BytesIO(response.content),
            bucket_name,
            s3_key,
            ExtraArgs={
                'ContentType': 'image/jpeg',
                'CacheControl': 'max-age=31536000',  # 1 年快取
                # Terraform 設定 S3 為拒絕私人連線
            }
        )

        # 5. 生成 URL
        cloudfront_domain = config('CLOUDFRONT_DOMAIN', default='')

        if cloudfront_domain:
            # 使用 CloudFront（快取）
            s3_url = f'https://{cloudfront_domain}/{s3_key}'
        else:
            # 直接用 S3 URL
            region = config('AWS_REGION', default='ap-northeast-1')
            s3_url = f'https://{bucket_name}.s3.{region}.amazonaws.com/{s3_key}'

        # 6. 更新資料庫
        Cafe.objects.filter(id=cafe.id).update(
            photo_s3_url=s3_url,
            photo_updated_at=timezone.now()
        )

        logger.info(f'成功上傳照片至 S3，Cafe name：{cafe.name} → {s3_url}')

        return {
            'status': 'success',
            'cafe_id': cafe.id,
            'cafe_name': cafe.name,
            'place_id': cafe.place_id,
            's3_url': s3_url
        }

    except requests.RequestException as e:
        logger.error(f' 下載照片失敗：{e}')
        # Celery 會自動重試
        raise

    except Exception as e:
        logger.error(f'上傳 S3 失敗：{e}', exc_info=True)
        # Celery 會自動重試
        raise


@shared_task(
    max_retries=3,
    default_retry_delay=60,  # 60 秒後重試
    autoretry_for=(requests.RequestException,)
)
def refresh_cafe_data(cafe_id):
    """
    異步更新咖啡店資料
    (當 last_refreshed 超過 30 天時觸發，該邏輯放在 helpers.py)
    """

    PHOTO_REFRESH_DAYS = 180

    try:
        cafe = Cafe.objects.get(id=cafe_id)
    except Cafe.DoesNotExist:
        logger.error(f'Cafe {cafe_id} 不存在')
        return {'status': 'failed', 'reason': 'cafe_not_found'}

    result = GoogleAPI.get_shop_detail(cafe.place_id)

    if not result:
        logger.warning(f'Google API 無回傳資料: {cafe.place_id}')
        return {'status': 'failed', 'reason': 'no_api_response'}

    old_reviews = cafe.reviews

    # 1. 更新欄位（保留原值如果 API 沒返回）
    # cafe.name = result.get('name') or cafe.name -> 如果拿到 None 會爆掉
    # update_data['name'] = None 暫存，後續 setattr存入
    update_data = {
        'name': result.get('name'),
        'address': result.get('address'),
        'phone': result.get('phone'),
        'rating': result.get('rating'),
        'user_ratings_total': result.get('user_ratings_total'),
        'website': result.get('website'),
        'google_maps': result.get('google_maps'),
        'photo_reference': result.get('photo_reference'),  # 但只有在照片時間超過180天時才會重新拉新的圖片
        'reviews': result.get('reviews'),
    }

    for key, value in update_data.items():
        if value is not None:
            setattr(cafe, key, value)

    cafe.last_refreshed = timezone.now()
    cafe.save()

    # 1b. reviews 有變化，或先前尚未成功產生 ai_summary，重新觸發 AI 摘要產生（重試機會）
    reviews_changed = update_data['reviews'] is not None and update_data['reviews'] != old_reviews
    if reviews_changed or not cafe.ai_summary:
        generate_cafe_ai_summary.delay(cafe.id)

    # 2. 再來決定「是否需要更新照片」 photo_reference 本質是
    if not cafe.photo_s3_url:
        download_and_upload_cafe_photo.delay(cafe.id)


    # 3. 照片超過180天後重新拉資料更新
    elif (
            cafe.photo_updated_at
            and timezone.now() - cafe.photo_updated_at >= timedelta(days=PHOTO_REFRESH_DAYS)
    ):
        # 清除舊照片資訊以強制觸發重新下載
        cafe.photo_s3_url = ""
        cafe.photo_updated_at = None
        cafe.save(update_fields=['photo_s3_url', 'photo_updated_at'])
        download_and_upload_cafe_photo.delay(cafe.id)

    logger.info(f'已更新咖啡店資料: {cafe.name} ({cafe.place_id})')
    return {'status': 'success', 'cafe_id': cafe.id, 'cafe_name': cafe.name}


@shared_task(
    max_retries=3,
    default_retry_delay=60,
)
def generate_cafe_ai_summary(cafe_id):
    """
    異步產生咖啡店的 AI 摘要（ai_summary），供描述搜尋比對用。
    額度記在系統帳號（見 ApiUsageService.get_system_ai_user_id），不佔用真人使用者的月額度。
    """
    try:
        cafe = Cafe.objects.get(id=cafe_id)
    except Cafe.DoesNotExist:
        logger.error(f'Cafe {cafe_id} 不存在，無法產生 AI 摘要')
        return {'status': 'failed', 'reason': 'cafe_not_found'}

    if not cafe.reviews:
        return {'status': 'skipped', 'reason': 'no_reviews'}

    system_user_id = ApiUsageService.get_system_ai_user_id()
    if not ApiUsageService.try_increment_ai_calls(system_user_id):
        logger.warning(f'系統 AI 摘要額度已用盡，跳過 Cafe {cafe_id}')
        return {'status': 'skipped', 'reason': 'quota_exceeded'}

    summary = GroqAPI.summarize_for_search(name=cafe.name, reviews=cafe.reviews)
    if not summary:
        ApiUsageService.revert_ai_call(system_user_id)
        return {'status': 'failed', 'reason': 'groq_error'}

    Cafe.objects.filter(id=cafe_id).update(ai_summary=summary)
    logger.info(f'已產生 AI 摘要: {cafe.name} ({cafe.place_id})')
    return {'status': 'success', 'cafe_id': cafe.id}
