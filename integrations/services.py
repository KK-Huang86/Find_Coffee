import logging

from django.db.models import F
from django.utils import timezone

from integrations.models import ApiUsageRecord

logger = logging.getLogger(__name__)


class ApiUsageService:

    SYSTEM_AI_USER_LINE_ID = 'system:ai-summary-batch'

    @staticmethod
    def get_system_ai_user_id():
        """取得（或建立）供批次 AI 摘要生成使用的系統帳號 id，與真人使用者額度分開計數"""
        from users.models import User

        user, _ = User.objects.get_or_create(line_user_id=ApiUsageService.SYSTEM_AI_USER_LINE_ID)
        return user.id

    @staticmethod
    def _get_year_month():
        return timezone.now().strftime('%Y-%m')

    @staticmethod
    def _try_increment_usage(user_id, field_name):
        year_month = ApiUsageService._get_year_month()
        record, _ = ApiUsageRecord.objects.get_or_create(
            user_id=user_id,
            year_month=year_month,
        )

        # 透過 updated_count 判斷是否還可以使用 API 餘額

        updated_count = ApiUsageRecord.objects.filter(
            pk=record.pk,
            can_use=True,
            total_api_calls__lt=F('monthly_limit'), # 使用 lt total_api_calls < monthly_limit，+1 並不會阻礙更新
        ).update(
            **{field_name: F(field_name) + 1},
            total_api_calls=F('total_api_calls') + 1,
        )

        # if updated_count == 0 代表沒有可用 API 餘額

        if updated_count == 0:
            ApiUsageRecord.objects.filter(pk=record.pk, can_use=True).update(can_use=False)
            return False
        logger.info(f'User {user_id} {field_name} +1 ({year_month})')
        return True

    @staticmethod
    def try_increment_detail_calls(user_id):
        """嘗試遞增 detail API 呼叫次數，超過額度回傳 False"""
        return ApiUsageService._try_increment_usage(user_id, 'detail_calls')

    @staticmethod
    def try_increment_search_calls(user_id):
        """嘗試遞增 search API 呼叫次數，超過額度回傳 False"""
        return ApiUsageService._try_increment_usage(user_id, 'search_calls')

    @staticmethod
    def try_increment_ai_calls(user_id):
        """嘗試遞增 AI (Groq) 呼叫次數，超過額度回傳 False"""
        year_month = ApiUsageService._get_year_month()
        record, _ = ApiUsageRecord.objects.get_or_create(
            user_id=user_id,
            year_month=year_month,
        )

        if not record.can_use_ai:
            return False

        updated_count = ApiUsageRecord.objects.filter(
            pk=record.pk,
            can_use_ai=True,
            ai_calls__lt=F('monthly_ai_limit'),
        ).update(ai_calls=F('ai_calls') + 1)

        if updated_count == 0:
            return False
        logger.info(f'User {user_id} ai_calls +1 ({year_month})')
        return True

    @staticmethod
    def revert_ai_call(user_id):
        """當 AI 呼叫失敗時，退回一次額度"""
        year_month = ApiUsageService._get_year_month()
        ApiUsageRecord.objects.filter(
            user_id=user_id,
            year_month=year_month,
            ai_calls__gt=0,
        ).update(ai_calls=F('ai_calls') - 1)
