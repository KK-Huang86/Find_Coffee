## 1. Cafe.ai_summary 欄位與 Migration

- [ ] 1.1 撰寫 migration 測試/檢查：新增 `Cafe.ai_summary`（`TextField`, `null=True`, `blank=True`）後執行 `uv run python manage.py makemigrations --check --dry-run` 確認無漏產生的 migration；先確認目前無此欄位（紅燈：欄位不存在時查詢/序列化不應報錯，`to_dict` 尚未包含此欄位）
- [ ] 1.2 於 `cafe/models.py` 新增 `ai_summary` 欄位，並更新 `Cafe.to_dict()` 納入該欄位；產生 migration，執行 `uv run python manage.py migrate` 驗證套用成功
- [ ] 1.3 補上/更新 `cafe/tests/` 中 model 相關測試（欄位預設為 `None`、`to_dict()` 含 `ai_summary` 鍵、空值與有值兩種情境），確認全綠

## 2. GroqAPI 新方法（摘要落地用 + 描述解析用）

- [ ] 2.1 為 `GroqAPI.summarize_for_search`（產生可落地儲存的摘要文字）撰寫單元測試：成功回傳字串、空 `reviews` 情境、API 例外回傳 `None`、確認呼叫使用專案指定模型 id（模型 id 迴歸測試比照 `integrations/tests/test_groq_api.py` 既有寫法）；先確認方法不存在時測試為紅燈
- [ ] 2.2 實作 `GroqAPI.summarize_for_search`，比照 `review_cafe` 的例外處理（catch 例外回傳 `None`，記錄 log，不吞例外不記錄）；跑測試轉綠
- [ ] 2.3 為 `GroqAPI.parse_search_description` 撰寫單元測試：合法 JSON 且屬性/數值皆在白名單內（成功）、回傳非 JSON（失敗回傳 `None`）、回傳含未定義屬性名稱（失敗）、回傳屬性數值不在 `{yes, maybe, no, null}` 範圍（該屬性被忽略或整體視為失敗，依此任務實作時定案並寫進測試斷言）、空字串描述輸入、API 例外回傳 `None`
- [ ] 2.4 實作 `GroqAPI.parse_search_description`，包含 prompt 設計與嚴格白名單驗證邏輯；跑測試轉綠

## 3. AI 摘要產生的額度與呼叫服務

- [ ] 3.1 撰寫 `integrations/services.py` 中批次摘要生成用額度呼叫的單元測試（額度充足可佔用、額度用盡回傳 False 且不呼叫 Groq、呼叫失敗後 revert 額度不淨扣），依 design.md Open Questions 決定的額度 key 策略撰寫斷言
- [ ] 3.2 依 3.1 測試結果，於 `ApiUsageService` 補上批次摘要生成所需的額度方法（沿用或擴充 `try_increment_ai_calls` / `revert_ai_call` 介面），跑測試轉綠

## 4. `generate_cafe_ai_summary` Celery Task

- [ ] 4.1 撰寫 `cafe/tests/` 中 task 單元測試（比照 `cafe/tests/` 既有對 `cafe/tasks.py` 的測試風格）：涵蓋（a）`reviews` 為空清單時跳過不呼叫 Groq、不消耗額度；（b）額度用盡時跳過；（c）Groq 呼叫成功時 `ai_summary` 被 `.update()` 寫入且不影響其他欄位；（d）Groq 呼叫失敗/回傳空字串時 revert 額度且 `ai_summary` 維持原值；（e）同一 `cafe_id` 重複執行兩次，最終 `ai_summary` 只反映最後一次成功結果（冪等性）；（f）`Cafe.DoesNotExist` 情境不拋未處理例外
- [ ] 4.2 在 `cafe/tasks.py` 實作 `generate_cafe_ai_summary(cafe_id)` task，串接 2.2 的 `GroqAPI.summarize_for_search` 與 3.2 的額度方法，使用 `.update()` 寫入避免覆蓋其他欄位；跑 4.1 測試轉綠

## 5. 觸發時機：建立與刷新串接

- [ ] 5.1 撰寫 `line_bot/tests/` 對 `get_or_create_cafe_info`（`line_bot/handlers/helpers.py`）的測試：新建店家且 `reviews` 非空時觸發 `generate_cafe_ai_summary.delay`；`reviews` 為空時不觸發；既有店家（非新建）不重複觸發
- [ ] 5.2 於 `get_or_create_cafe_info` 新建店家分支中加入 `generate_cafe_ai_summary.delay(cafe.id)` 呼叫，跑 5.1 測試轉綠
- [ ] 5.3 撰寫 `cafe/tests/` 對 `refresh_cafe_data` 的測試：`reviews` 內容較刷新前有變化時觸發摘要重新產生；`reviews` 未變化不觸發；`ai_summary` 目前為空值時，即使 `reviews` 未變化也觸發重試
- [ ] 5.4 於 `refresh_cafe_data` 加入對應觸發邏輯，跑 5.3 測試轉綠

## 6. `CafeSearchService.search_by_description`

- [ ] 6.1 撰寫 `cafe/tests/` 對新 service 的單元測試，涵蓋 spec 全部情境：解析出明確屬性且有符合店家、解析出屬性但無符合店家（含「有符合屬性但無 `ai_summary`」需被排除）、解析結果為全 `null` 時走摘要關鍵字比對、解析失敗（非 JSON / 未定義欄位）時走降級關鍵字比對、Groq 呼叫例外時走降級路徑、額度用盡時不呼叫 Groq 直接走降級或明確額度不足回應（依 design 決定路徑並寫入斷言）、空字串描述輸入
- [ ] 6.2 於 `cafe/services/` 新增 `search_by_description`，串接 `GroqAPI.parse_search_description`、額度檢查、`ai_summary__isnull=False` 過濾與屬性/關鍵字查詢邏輯；跑 6.1 測試轉綠

## 7. LINE Bot 對話流程與呈現

- [ ] 7.1 撰寫 `line_bot/tests/` 對新對話狀態（`AWAITING_DESCRIPTION_SEARCH`）的測試：觸發入口進入等待狀態、下一則文字訊息被視為描述並呼叫 `search_by_description`、非文字訊息或空白輸入的處理、查無結果時回覆明確提示訊息、額度不足時回覆對應提示
- [ ] 7.2 於 `line_bot/state.py` 新增狀態常數與轉換邏輯；跑 7.1 中狀態轉換相關測試轉綠
- [ ] 7.3 於 `line_bot/handlers/` 新增描述搜尋 handler，串接 `CafeSearchService.search_by_description` 與既有 Flex Message carousel builder 呈現結果；跑 7.1 剩餘測試轉綠
- [ ] 7.4 新增入口（Postback button 或 Rich Menu 項目）觸發進入描述搜尋狀態，比照既有入口寫法（`line_bot/builders/postback.py`）

## 8. 整合驗證與 Spec 一致性

- [ ] 8.1 執行 `uv run pytest -q` 與 `uv run pytest --cov --cov-report=term-missing`，確認本次新增/修改測試與既有測試全數通過，且新程式碼有測試覆蓋
- [ ] 8.2 執行 `uv run ruff check .` 確認無新增 lint 問題
- [ ] 8.3 手動或以整合測試驗證端到端流程：LINE 對話輸入描述 → 收到符合條件的咖啡店清單（或明確查無結果訊息）
- [ ] 8.4 開 PR 前執行 `/spectra-verify` 確認實作符合本 change 的 proposal/design/spec，並執行 `/spectra-drift` 確認與目前程式碼現狀無落差；若有落差，回頭修正實作或調整 spec 後再重跑，確認一致後才開 PR
