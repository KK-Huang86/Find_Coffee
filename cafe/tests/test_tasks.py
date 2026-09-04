import pytest
from datetime import timedelta
from django.utils import timezone

from cafe.tasks import generate_cafe_ai_summary, refresh_cafe_data
from cafe.tests.factories import CafeFactory


@pytest.mark.django_db
class TestRefreshCafeData:
    """測試 refresh_cafe_data task"""

    def test_cafe_not_found(self, mock_google_api):
        """cafe_id 不存在時，返回 cafe_not_found"""
        result = refresh_cafe_data(cafe_id=99999)

        assert result == {'status': 'failed', 'reason': 'cafe_not_found'}
        mock_google_api.assert_not_called()

    def test_api_no_response(self, mock_google_api):
        """GoogleAPI 返回空 dict 時，返回 no_api_response"""
        cafe = CafeFactory()
        mock_google_api.return_value = {}

        result = refresh_cafe_data(cafe_id=cafe.id)

        assert result == {'status': 'failed', 'reason': 'no_api_response'}

    def test_update_fields_success(self, mock_google_api, mock_photo_task):
        """API 返回完整資料時，cafe 各欄位正確更新"""
        cafe = CafeFactory(
            name='舊名稱',
            rating=None,
            photo_s3_url='https://example.com/photo.jpg',  # 有照片，不觸發更新
            photo_updated_at=timezone.now(),
        )

        # 從 google map 拉到的新資料
        mock_google_api.return_value = {
            'name': '新名稱',
            'address': '新地址',
            'phone': '02-1234-5678',
            'rating': 4.8,
            'user_ratings_total': 200,
            'website': 'https://example.com',
            'google_maps': 'https://maps.google.com/xxx',
        }

        result = refresh_cafe_data(cafe_id=cafe.id)

        cafe.refresh_from_db()
        assert result['status'] == 'success'
        assert cafe.name == '新名稱'
        assert cafe.address == '新地址'
        assert cafe.phone == '02-1234-5678'
        assert float(cafe.rating) == 4.8
        assert cafe.user_ratings_total == 200

    def test_partial_update_none_ignored(self, mock_google_api, mock_photo_task):
        """API 部分欄位為 None 時，只更新非 None 欄位，原值保留"""
        cafe = CafeFactory(
            name='原始名稱',
            phone='原始電話',
            photo_s3_url='https://example.com/photo.jpg',
            photo_updated_at=timezone.now(),
        )
        mock_google_api.return_value = {
            'name': '新名稱',
            'phone': None,  # None 應該被忽略
            'rating': None,
        }

        refresh_cafe_data(cafe_id=cafe.id)

        cafe.refresh_from_db()
        assert cafe.name == '新名稱'
        assert cafe.phone == '原始電話'  # 保留原值

    def test_trigger_photo_upload_no_s3_url(self, mock_google_api, mock_photo_task):
        """無 photo_s3_url 時，觸發 download_and_upload_cafe_photo"""
        cafe = CafeFactory(photo_s3_url='')  # 沒有 S3 照片
        mock_google_api.return_value = {'name': '咖啡店'}

        refresh_cafe_data(cafe_id=cafe.id)

        mock_photo_task.assert_called_once_with(cafe.id)

    def test_trigger_photo_upload_expired(self, mock_google_api, mock_photo_task):
        """photo_updated_at 超過 180 天時，觸發照片更新"""
        cafe = CafeFactory(
            photo_s3_url='https://example.com/old.jpg',
            photo_updated_at=timezone.now() - timedelta(days=181),  # 超過 180 天
        )
        mock_google_api.return_value = {'name': '咖啡店'}

        refresh_cafe_data(cafe_id=cafe.id)

        mock_photo_task.assert_called_once_with(cafe.id)
        cafe.refresh_from_db()
        assert cafe.photo_s3_url == ""
        assert cafe.photo_updated_at is None

    def test_no_photo_upload_recent(self, mock_google_api, mock_photo_task):
        """photo_updated_at 未超過 180 天時，不觸發照片更新"""
        cafe = CafeFactory(
            photo_s3_url='https://example.com/photo.jpg',
            photo_updated_at=timezone.now() - timedelta(days=30),  # 30 天前，未過期
        )
        mock_google_api.return_value = {'name': '咖啡店'}

        refresh_cafe_data(cafe_id=cafe.id)

        mock_photo_task.assert_not_called()

    def test_triggers_ai_summary_regeneration_when_reviews_changed(
        self, mock_google_api, mock_photo_task, mock_summary_task
    ):
        """reviews 內容較刷新前有變化時，觸發摘要重新產生"""
        cafe = CafeFactory(
            reviews=['舊評論'],
            ai_summary='舊摘要',
            photo_s3_url='https://example.com/photo.jpg',
            photo_updated_at=timezone.now(),
        )
        mock_google_api.return_value = {'name': '咖啡店', 'reviews': ['新評論']}

        refresh_cafe_data(cafe_id=cafe.id)

        mock_summary_task.assert_called_once_with(cafe.id)

    def test_no_ai_summary_regeneration_when_reviews_unchanged_and_summary_exists(
        self, mock_google_api, mock_photo_task, mock_summary_task
    ):
        """reviews 未變化且已有 ai_summary 時，不觸發摘要重新產生"""
        cafe = CafeFactory(
            reviews=['相同評論'],
            ai_summary='已有摘要',
            photo_s3_url='https://example.com/photo.jpg',
            photo_updated_at=timezone.now(),
        )
        mock_google_api.return_value = {'name': '咖啡店', 'reviews': ['相同評論']}

        refresh_cafe_data(cafe_id=cafe.id)

        mock_summary_task.assert_not_called()

    def test_triggers_ai_summary_retry_when_summary_missing_even_if_reviews_unchanged(
        self, mock_google_api, mock_photo_task, mock_summary_task
    ):
        """ai_summary 目前為空值時，即使 reviews 未變化也觸發重試"""
        cafe = CafeFactory(
            reviews=['相同評論'],
            ai_summary=None,
            photo_s3_url='https://example.com/photo.jpg',
            photo_updated_at=timezone.now(),
        )
        mock_google_api.return_value = {'name': '咖啡店', 'reviews': ['相同評論']}

        refresh_cafe_data(cafe_id=cafe.id)

        mock_summary_task.assert_called_once_with(cafe.id)


@pytest.mark.django_db
class TestGenerateCafeAiSummary:
    """測試 generate_cafe_ai_summary task"""

    SYSTEM_USER_ID = 999

    def _mock_quota(self, mocker, can_increment=True):
        mocker.patch('cafe.tasks.ApiUsageService.get_system_ai_user_id', return_value=self.SYSTEM_USER_ID)
        mock_increment = mocker.patch(
            'cafe.tasks.ApiUsageService.try_increment_ai_calls', return_value=can_increment
        )
        mock_revert = mocker.patch('cafe.tasks.ApiUsageService.revert_ai_call')
        return mock_increment, mock_revert

    def test_cafe_not_found(self, mocker):
        """cafe_id 不存在時，回傳 cafe_not_found，不拋未處理例外"""
        mock_summarize = mocker.patch('cafe.tasks.GroqAPI.summarize_for_search')

        result = generate_cafe_ai_summary(cafe_id=99999)

        assert result == {'status': 'failed', 'reason': 'cafe_not_found'}
        mock_summarize.assert_not_called()

    def test_no_reviews_skips_without_calling_groq_or_quota(self, mocker):
        """reviews 為空清單時跳過，不呼叫 Groq、不消耗額度"""
        cafe = CafeFactory(reviews=[])
        mock_increment, _ = self._mock_quota(mocker)
        mock_summarize = mocker.patch('cafe.tasks.GroqAPI.summarize_for_search')

        result = generate_cafe_ai_summary(cafe_id=cafe.id)

        assert result == {'status': 'skipped', 'reason': 'no_reviews'}
        mock_increment.assert_not_called()
        mock_summarize.assert_not_called()

    def test_quota_exhausted_skips_without_calling_groq(self, mocker):
        """額度用盡時跳過，不呼叫 Groq"""
        cafe = CafeFactory(reviews=['環境安靜'])
        self._mock_quota(mocker, can_increment=False)
        mock_summarize = mocker.patch('cafe.tasks.GroqAPI.summarize_for_search')

        result = generate_cafe_ai_summary(cafe_id=cafe.id)

        assert result == {'status': 'skipped', 'reason': 'quota_exceeded'}
        mock_summarize.assert_not_called()

    def test_success_updates_ai_summary_without_touching_other_fields(self, mocker):
        """Groq 呼叫成功時，ai_summary 被寫入，且不影響其他欄位"""
        cafe = CafeFactory(reviews=['環境安靜'], name='原始名稱', favorite_count=3)
        self._mock_quota(mocker)
        mocker.patch('cafe.tasks.GroqAPI.summarize_for_search', return_value='適合安靜工作的咖啡店。')

        result = generate_cafe_ai_summary(cafe_id=cafe.id)

        cafe.refresh_from_db()
        assert result == {'status': 'success', 'cafe_id': cafe.id}
        assert cafe.ai_summary == '適合安靜工作的咖啡店。'
        assert cafe.name == '原始名稱'
        assert cafe.favorite_count == 3

    def test_groq_failure_reverts_quota_and_keeps_original_ai_summary(self, mocker):
        """Groq 呼叫失敗（回傳 None）時，revert 額度且 ai_summary 維持原值"""
        cafe = CafeFactory(reviews=['環境安靜'], ai_summary='舊摘要')
        _, mock_revert = self._mock_quota(mocker)
        mocker.patch('cafe.tasks.GroqAPI.summarize_for_search', return_value=None)

        result = generate_cafe_ai_summary(cafe_id=cafe.id)

        cafe.refresh_from_db()
        assert result == {'status': 'failed', 'reason': 'groq_error'}
        mock_revert.assert_called_once_with(self.SYSTEM_USER_ID)
        assert cafe.ai_summary == '舊摘要'

    def test_repeated_execution_is_idempotent(self, mocker):
        """同一 cafe_id 重複執行兩次，最終 ai_summary 只反映最後一次成功結果"""
        cafe = CafeFactory(reviews=['環境安靜'])
        self._mock_quota(mocker)
        mock_summarize = mocker.patch('cafe.tasks.GroqAPI.summarize_for_search')

        mock_summarize.return_value = '第一次摘要'
        generate_cafe_ai_summary(cafe_id=cafe.id)

        mock_summarize.return_value = '第二次摘要'
        generate_cafe_ai_summary(cafe_id=cafe.id)

        cafe.refresh_from_db()
        assert cafe.ai_summary == '第二次摘要'
