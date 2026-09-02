## Why

使用者目前只能用店名或地址關鍵字搜尋咖啡店，無法用「想要的感覺」（例如「安靜適合工作、有插座」）描述來找店。專案已有 Groq 整合（`integrations/groq/api.py`）與屬性投票資料（`CafeAttributeVote` / `Cafe.has_socket` 等），但 AI 生成的評論摘要目前只在使用者點單店「問 AI」時即時產生、存 Redis 7 天、不落地，因此無法拿來做跨店篩選比對。需要把 AI 摘要落地成可查詢資料，並新增一個描述式搜尋入口。

## What Changes

- 新增 `Cafe.ai_summary` 欄位（TextField，nullable），儲存 Groq 針對該店 reviews 產生的摘要文字。
- 新增摘要產生流程：店家首次建立（`get_or_create_cafe_info`）與既有 `refresh_cafe_data` 資料過期重新整理時，觸發 Celery task 呼叫 Groq 產生/更新 `ai_summary`；沿用 `ApiUsageService` 的 AI 呼叫額度與失敗 revert 機制，失敗不阻斷主流程（reviews 沒有摘要時 fallback 為 `None`，不擋咖啡店建立/顯示）。
- 新增 LINE Bot 描述搜尋入口：使用者輸入自然語言描述 → 呼叫 Groq 將描述解析為結構化條件（插座、寵物友善、限時與否等既有屬性欄位的布林/列舉值）→ 用結構化條件查詢 `Cafe`（`has_socket`、`limited_time`、`pet_friendly` 等既有欄位），並可選擇性用 `ai_summary` 做關鍵字輔助比對 → 回傳符合條件的咖啡店列表（Flex Message，比照既有搜尋結果呈現）。
- 新增/調整 Postback 或 Rich Menu 入口，讓使用者能觸發「描述搜尋」對話狀態（`StateManager`），輸入完成後解析並查詢。

## Capabilities

### New Capabilities
- `cafe-ai-summary`: Cafe 的 AI 摘要（`ai_summary`）產生、儲存、更新時機（建立/refresh）、額度與失敗處理。
- `cafe-description-search`: 使用者以自然語言描述搜尋咖啡店的 LINE Bot 對話流程、描述解析為結構化條件的邏輯、查詢與結果呈現。

### Modified Capabilities
（無現有 spec 定義本次會變更的行為；店名/地址關鍵字搜尋現況未被本次變更修改。）

## Impact

- **Models / Migration**: `cafe/models.py`（新增 `Cafe.ai_summary` 欄位）＋對應 migration。
- **Celery tasks**: `cafe/tasks.py`（新增或擴充 task 產生/更新 `ai_summary`，串接 `GroqAPI`）。
- **LINE Bot**: `line_bot/state.py`（新增描述搜尋對話狀態）、`line_bot/handlers/`（新增描述搜尋 handler）、`line_bot/builders/`（結果呈現、可能新增入口按鈕/Rich Menu 項目）。
- **Integrations**: `integrations/groq/api.py`（新增描述解析用的 prompt/方法，例如 `GroqAPI.parse_search_description`）、`integrations/services.py`（`ApiUsageService` 額度呼叫路徑增加）。
- **Dependencies**: 新增 `jieba`（中文斷詞，供關鍵字降級搜尋使用，見 design.md）。
- **Tests**: 對應新增/擴充 `cafe/tests/`、`line_bot/tests/`、`integrations/tests/`，含邊界案例（空描述、Groq 失敗、無符合結果、額度用盡）。
