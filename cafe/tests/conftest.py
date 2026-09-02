import pytest


@pytest.fixture
def mock_google_api(mocker):
    """Mock GoogleAPI.get_shop_detail"""
    return mocker.patch('cafe.tasks.GoogleAPI.get_shop_detail')


@pytest.fixture
def mock_photo_task(mocker):
    """Mock download_and_upload_cafe_photo.delay"""
    return mocker.patch('cafe.tasks.download_and_upload_cafe_photo.delay')


@pytest.fixture(autouse=True)
def mock_summary_task(mocker):
    """Mock generate_cafe_ai_summary.delay（autouse：refresh_cafe_data 每次更新後都會評估是否觸發，
    沒 mock 會真的打 Celery broker，違反 unit test 不得真實呼叫 Redis/Celery 的規則）"""
    return mocker.patch('cafe.tasks.generate_cafe_ai_summary.delay')
