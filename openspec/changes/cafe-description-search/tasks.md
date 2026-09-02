## 1. Cafe.ai_summary 欄位與 Migration

- [ ] 1.1 撰寫 migration 測試/檢查：新增 `Cafe.ai_summary`（`TextField`, `null=True`, `blank=True`）後執行 `uv run python manage.py makemigrations --check --dry-run` 確認無漏產生的 migration；先確認目前無此欄位（紅燈：欄位不存在時查詢/序列化不應報錯，`to_dict` 尚未包含此欄位）
- [ ] 1.2 於 `cafe/models.py` 新增 `ai_summary` 欄位，並更新 `Cafe.to_dict()` 納入該欄位；產生 migration，執行 `uv run python manage.py migrate` 驗證套用成功
- [ ] 1.3 補上/更新 `cafe/tests/` 中 model 相關測試（欄位預設為 `None`、`to_dict()` 含 `ai_summary` 鍵、空值與有值兩種情境），確認全綠

## 2. GroqAPI 新方法（摘要落地用 + 描述解析用）

- [ ] 2.1 為 `GroqAPI.summarize_for_search`（產生可落地儲存的摘要文字）撰寫單元測試：成功回傳字串、空 `reviews` 情境、API 例外回傳 `None`、確認呼叫使用專案指定模型 id（模型 id 迴歸測試比照 `integrations/tests/test_groq_api.py` 既有寫法）；先確認方法不存在時測試為紅燈
- [ ] 2.2 實作 `GroqAPI.summarize_for_search`，比照 `review_cafe` 的例外處理（catch 例外回傳 `None`，記錄 log，不吞例外不記錄）；跑測試轉綠
- [ ] 2.3 為 `GroqAPI.parse_search_description` 撰寫單元測試：合法 JSON 且屬性/數值皆在白名單內（成功，回傳 dict）、回傳非 JSON（整體解析失敗回傳 `None`）、回傳含未定義屬性名稱（整體解析失敗回傳 `None`）、回傳屬性數值不在 `{yes, maybe, no, null}` 範圍時**整體解析失敗回傳 `None`**（固定斷言，不得只忽略該屬性；即使其他屬性合法也視為整份失敗，比照 design.md 決策 3 的統一規則）、空字串描述輸入、API 例外回傳 `None`
- [ ] 2.4 實作 `GroqAPI.parse_search_description`，包含 prompt 設計與嚴格白名單驗證邏輯（任一欄位或數值不合法即整份回傳 `None`，不做部分採用）；跑測試轉綠

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

## 6. 關鍵字降級搜尋工具與 `CafeSearchService.search_by_description`

- [ ] 6.1 新增 `jieba` 為專案依賴（`pyproject.toml` + `uv sync`），為關鍵字抽取工具函式（比照 design.md 3b：`jieba.cut_for_search` 斷詞 → 去空白/純標點 → 停用詞過濾 → 長度過濾 → 去重）撰寫單元測試：不含空白的中文長句（例如「我想找安靜適合工作的咖啡廳」需切出「安靜」「工作」「咖啡廳」等有效關鍵詞）、輸入全為停用詞或標點時回傳空清單、含標點與空白混合的描述、空字串輸入
- [ ] 6.2 實作關鍵字抽取工具函式，跑 6.1 測試轉綠
- [ ] 6.3 定義 `CafeSearchResult`（`status: Literal['ok', 'quota_exceeded']`, `cafes: QuerySet[Cafe]`，見 design.md 決策 4），撰寫 `cafe/tests/` 對 `CafeSearchService.search_by_description` 的單元測試，涵蓋 spec 全部情境：解析出明確屬性且有符合店家（`status='ok'`）、解析出屬性但無符合店家（含「有符合屬性但無 `ai_summary`」需被排除）、解析結果為全 `null` 時走 6.2 關鍵字降級搜尋、解析失敗（非 JSON / 未定義欄位 / 屬性數值不在白名單，皆整體視為失敗）時走 6.2 關鍵字降級搜尋、Groq 呼叫例外或逾時時同樣走 6.2 關鍵字降級搜尋（斷言：`status='ok'`，不拋例外，直接回傳降級搜尋結果含空結果情況）、降級後有效關鍵詞為空時直接回傳 `status='ok'` 且 `cafes` 為空、額度用盡時斷言固定為：**不呼叫 Groq**、回傳 `status='quota_exceeded'` 且 `cafes` 為空 QuerySet（不得走降級搜尋，不得回傳一般查無結果訊息）、空字串描述輸入
- [ ] 6.4 於 `cafe/services/` 新增 `search_by_description`，串接額度檢查、`GroqAPI.parse_search_description`、`ai_summary__isnull=False` 過濾、屬性查詢與 6.2 的關鍵字降級查詢，回傳 `CafeSearchResult`；跑 6.3 測試轉綠

## 7. LINE Bot 對話流程與呈現

- [ ] 7.1 撰寫 `line_bot/tests/` 對新對話狀態（`AWAITING_DESCRIPTION_SEARCH`）的測試：觸發入口進入等待狀態、下一則文字訊息被視為描述並呼叫 `search_by_description`、非文字訊息或空白輸入的處理、`CafeSearchResult(status='ok', cafes=<空>)` 時回覆「找不到符合描述的店家」、`CafeSearchResult(status='quota_exceeded')` 時回覆額度不足提示（斷言 handler 是依 `status` 分流，而非用 `cafes` 是否為空來猜測）
- [ ] 7.2 於 `line_bot/state.py` 新增狀態常數與轉換邏輯；跑 7.1 中狀態轉換相關測試轉綠
- [ ] 7.3 於 `line_bot/handlers/` 新增描述搜尋 handler，依 `CafeSearchResult.status` 分流呈現（`ok` 用既有 Flex Message carousel builder 呈現 `cafes` 或查無結果訊息、`quota_exceeded` 顯示額度不足訊息）；跑 7.1 剩餘測試轉綠
- [ ] 7.4 新增入口（Postback button 或 Rich Menu 項目）觸發進入描述搜尋狀態，比照既有入口寫法（`line_bot/builders/postback.py`）

## 8. 整合驗證與 Spec 一致性

- [ ] 8.1 執行 `uv run pytest -q` 與 `uv run pytest --cov --cov-report=term-missing`，確認本次新增/修改測試與既有測試全數通過，且新程式碼有測試覆蓋
- [ ] 8.2 執行 `uv run ruff check .` 確認無新增 lint 問題
- [ ] 8.3 手動或以整合測試驗證端到端流程：LINE 對話輸入描述 → 收到符合條件的咖啡店清單（或明確查無結果訊息）
- [ ] 8.4 開 PR 前執行 `/spectra-verify` 確認實作符合本 change 的 proposal/design/spec，並執行 `/spectra-drift` 確認與目前程式碼現狀無落差；若有落差，回頭修正實作或調整 spec 後再重跑，確認一致後才開 PR
