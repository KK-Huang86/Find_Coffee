import pytest

from integrations.models import ApiUsageRecord
from integrations.services import ApiUsageService
from users.models import User


@pytest.mark.django_db
class TestGetSystemAiUserId:
    """測試 ApiUsageService.get_system_ai_user_id"""

    def test_creates_system_user_on_first_call(self):
        """首次呼叫時建立固定 line_user_id 的系統 User"""
        user_id = ApiUsageService.get_system_ai_user_id()

        user = User.objects.get(id=user_id)
        assert user.line_user_id == ApiUsageService.SYSTEM_AI_USER_LINE_ID

    def test_returns_same_id_on_repeated_calls(self):
        """重複呼叫回傳同一個 id，不建立第二筆系統 User（冪等性）"""
        first_id = ApiUsageService.get_system_ai_user_id()
        second_id = ApiUsageService.get_system_ai_user_id()

        assert first_id == second_id
        assert User.objects.filter(line_user_id=ApiUsageService.SYSTEM_AI_USER_LINE_ID).count() == 1

    def test_system_user_quota_independent_from_real_user(self):
        """系統帳號的額度計數與真人使用者的月額度完全分開"""
        from cafe.tests.factories import UserFactory

        real_user = UserFactory()
        system_user_id = ApiUsageService.get_system_ai_user_id()

        ApiUsageService.try_increment_ai_calls(system_user_id)

        real_user_record, _ = ApiUsageRecord.objects.get_or_create(
            user_id=real_user.id, year_month=ApiUsageService._get_year_month()
        )
        assert real_user_record.ai_calls == 0


@pytest.mark.django_db
class TestSystemAiQuotaUsingExistingInterface:
    """驗證系統帳號 id 搭配既有 try_increment_ai_calls / revert_ai_call 的行為（不新增第二套額度系統）"""

    def test_quota_available_can_be_consumed(self):
        """額度充足時可成功佔用"""
        system_user_id = ApiUsageService.get_system_ai_user_id()

        ok = ApiUsageService.try_increment_ai_calls(system_user_id)

        assert ok is True
        record = ApiUsageRecord.objects.get(user_id=system_user_id, year_month=ApiUsageService._get_year_month())
        assert record.ai_calls == 1

    def test_quota_exhausted_returns_false(self):
        """額度用盡時回傳 False，且不再累加"""
        system_user_id = ApiUsageService.get_system_ai_user_id()
        year_month = ApiUsageService._get_year_month()
        ApiUsageRecord.objects.create(
            user_id=system_user_id, year_month=year_month, ai_calls=1, monthly_ai_limit=1,
        )

        ok = ApiUsageService.try_increment_ai_calls(system_user_id)

        assert ok is False
        record = ApiUsageRecord.objects.get(user_id=system_user_id, year_month=year_month)
        assert record.ai_calls == 1

    def test_revert_after_failure_keeps_net_zero(self):
        """呼叫失敗後 revert 額度，淨消耗歸零"""
        system_user_id = ApiUsageService.get_system_ai_user_id()

        ApiUsageService.try_increment_ai_calls(system_user_id)
        ApiUsageService.revert_ai_call(system_user_id)

        record = ApiUsageRecord.objects.get(
            user_id=system_user_id, year_month=ApiUsageService._get_year_month()
        )
        assert record.ai_calls == 0
