## Context

現況（見 proposal.md - Why）：

- `Cafe` 已有結構化屬性欄位：`limited_time` / `has_socket` / `pet_friendly` / `has_pet`，值域皆為 `yes` / `maybe` / `no`（`cafe/models.py:15-34`）。這些欄位由 `cafe/services/vote_service.py` 依 `CafeAttributeVote` 投票結果計算寫入。
- Groq 整合現況只有 `GroqAPI.review_cafe`（`integrations/groq/api.py:24`），單店即時生成、Redis 快取 7 天、不落地，由 `handle_ask_ai`（`line_bot/handlers/postback_actions.py:142`）呼叫。
- AI 呼叫額度由 `ApiUsageService.try_increment_ai_calls` / `revert_ai_call`（`integrations/services.py:54-85`）控管，失敗需 revert，避免扣額度但沒有結果。
- 店家建立於 `get_or_create_cafe_info`（`line_bot/handlers/helpers.py:25`），資料 30 天/照片 180 天過期後由 `refresh_cafe_data`（`cafe/tasks.py:147`）背景更新。
- LINE Bot 對話式輸入以 `StateManager` + `UserState`（`line_bot/state.py`）管理多輪流程；目前店名/地址搜尋走 `postback_actions.py` 內的 handler，非本次修改範圍。

## Goals / Non-Goals

**Goals:**
- 讓 `ai_summary` 成為可查詢、可比對的落地資料，不再只存在 Redis。
- 讓使用者用一段自然語言描述，透過 Groq 解析成既有結構化屬性條件，查出符合的咖啡店清單。
- 摘要產生與描述解析共用既有 AI 額度與失敗處理機制，不新增第二套額度系統。
- 描述搜尋在沒有 `ai_summary` 或解析失敗時，仍要有明確、不崩潰的降級行為。

**Non-Goals:**
- 不導入向量資料庫或 embedding 相似度搜尋（使用者已選定「AI 語意分類 + 結構化屬性」，非向量方案）。
- 不重做既有店名/地址關鍵字搜尋（`icontains`）流程。
- 不做「摘要即時重算」——摘要屬於背景/批次資料，不在使用者查詢當下同步呼叫 Groq 產生。
- 不處理多語言描述輸入的翻譯（僅支援繁體中文輸入，與現有 Bot 語言一致）。

## Decisions

### 1. `Cafe.ai_summary` 何時產生
在 `get_or_create_cafe_info` 建立新 `Cafe` 後，以及 `refresh_cafe_data` 更新 `reviews` 後，各自 `.delay()` 一個新的 Celery task `generate_cafe_ai_summary(cafe_id)`（比照 `download_and_upload_cafe_photo` 的既有 async-task 模式），而不是在請求同步路徑呼叫 Groq。

- **理由**：使用者建立/查詢單店時的路徑必須快速回應 LINE webhook（LINE 對 webhook 有時間限制），且 Groq 呼叫可能失敗或緩慢，不應阻塞。
- **替代方案**：同步在 `get_or_create_cafe_info` 內呼叫 Groq——被否決，會拖慢首次查詢且與 webhook 逾時風險衝突。

### 2. 摘要產生的額度與冪等性
`generate_cafe_ai_summary` task：
1. 用 `Cafe.objects.get(id=cafe_id)` 取店家，`reviews` 為空則直接 `return {'status': 'skipped', 'reason': 'no_reviews'}`，不呼叫 Groq、不消耗額度。
2. 呼叫 `ApiUsageService.try_increment_ai_calls`（比照 `handle_ask_ai` 現有模式），額度用盡則 `return {'status': 'skipped', 'reason': 'quota_exceeded'}`。此額度呼叫需要一個系統層級的 user_id（非個別 LINE 使用者），與現有「使用者觸發」的 AI 呼叫分開計數或共用同一個池，需在 tasks.md 落地時決定計數 key（設計傾向：批次生成不綁定單一使用者，另建系統帳號或月額度 key，避免佔用真人使用者額度）。
3. 呼叫 `GroqAPI.summarize_for_search(...)`（新方法，見下）取得摘要文字；失敗（回傳 `None`）則 `revert_ai_call` 並 `return {'status': 'failed', 'reason': 'groq_error'}`，`ai_summary` 保持原值（不覆蓋成 `None`），下次 refresh 會再重試。
4. 成功則 `Cafe.objects.filter(id=cafe_id).update(ai_summary=result)`（比照 `favorite_count` 的 `.update()` 寫法，避免覆蓋掉同時間其他欄位變更；不用 `cafe.save()` 整包存檔）。
5. Task 需可重複執行不出錯（Celery 重試/重複觸發）：同一 `cafe_id` 重跑只會覆寫成最新摘要，不會產生重複資料或副作用，滿足冪等性。

### 3. 描述解析為結構化條件
新增 `GroqAPI.parse_search_description(description: str) -> dict | None`：
- Prompt 要求 Groq 僅回傳 JSON，欄位對應 `limited_time` / `has_socket` / `pet_friendly` / `has_pet`，值域限制在 `yes` / `maybe` / `no` / `null`（未提及的屬性給 `null`，代表不篩選該欄位）。
- 回傳後嚴格驗證：非 JSON、欄位不在白名單、值不在 `{yes, maybe, no, null}` 一律視為解析失敗（`None`），不可讓未驗證字串直接進 ORM filter（避免注入非預期值/防禦式驗證，即使 Django ORM 本身不會有 SQL injection 風險，仍要防止查出不合語意的結果）。
- **理由**：重用既有三態屬性欄位語意一致，且不需要新的比對邏輯層；讓 LLM 的自由文字輸出收斂成可驗證的白名單值，降低不可預期行為風險。
- **替代方案**：讓 Groq 直接生成 SQL 或 ORM 查詢條件字串——被否決，安全風險高且難驗證。

### 4. 查詢邏輯
`CafeSearchService.search_by_description(description: str) -> QuerySet[Cafe]`（新 service，比照 `cafe/services/` 既有結構）：
1. 呼叫 `GroqAPI.parse_search_description`，解析失敗則整體 fallback 為「用原始描述字串比對 `ai_summary` icontains」（降級路徑，見 Risks）。
2. 解析成功則對每個非 `null` 欄位疊加 `Cafe.objects.filter(<field>=value)`，僅查詢已有 `ai_summary`（`ai_summary__isnull=False`）且屬性已計算過（`attributes_last_calculated_at__isnull=False`）的店家，避免把「未知」誤判為「不符合」。
3. 若解析結果全為 `null`（描述沒有可辨識屬性），改用 `ai_summary__icontains` 對描述中的關鍵詞做輔助比對（簡單詞袋，非语意）。
4. 查無結果時回傳空結果，由 LINE handler 呈現「找不到符合描述的店家」，不是例外。

### 5. LINE Bot 對話流程
新增 `UserState` 狀態 `AWAITING_DESCRIPTION_SEARCH`（比照既有搜尋狀態模式）：
1. 使用者由 Rich Menu 或既有選單觸發「描述搜尋」→ 進入該狀態並提示輸入描述。
2. 下一則文字訊息視為描述輸入，呼叫 `CafeSearchService.search_by_description`，額度用盡或 Groq 例外時比照 `handle_ask_ai` 的錯誤訊息模式提示使用者。
3. 結果比照既有搜尋結果 Flex Message carousel 呈現（複用既有 builder，不新增樣式）。

## Risks / Trade-offs

- **[風險] Groq 解析結果格式不穩定（非 JSON / 欄位外洩）** → 嚴格白名單驗證 + 解析失敗 fallback 為關鍵字比對，不讓未驗證輸出進入查詢條件。
- **[風險] 摘要背景生成失敗會讓新店家長期沒有 `ai_summary`，永遠搜不到** → `refresh_cafe_data` 每次刷新都重新觸發生成（若 `ai_summary` 仍為空），提供自我修復機會；同時 `ai_summary__isnull=False` 的過濾條件確保結果只是「暫時搜不到」而非回傳錯誤資料。
- **[風險] 批次生成大量呼叫 Groq，短時間內衝額度，排擠使用者主動觸發「問 AI」的額度** → 決策 2 提到需要獨立的額度 key／池，非與真人使用者共用同一個月額度計數；此配置細節留待 tasks.md／實作階段依 `ApiUsageService` 現有介面決定（見 Open Questions）。
- **[取捨] 用結構化屬性比對而非向量相似度** → 犧牲「語意相近但屬性未提及」的召回率，換取零額外基礎設施、可解釋、與既有屬性系統一致（使用者已在需求澄清階段選定此方案）。

## Migration Plan

1. 新增 migration：`Cafe.ai_summary`（`TextField(null=True, blank=True)`），不需 backfill（欄位預設為空，既有店家由後續 refresh 週期自然補齊）。
2. 部署後，既有店家的 `ai_summary` 皆為 `None`，描述搜尋在補齊完成前召回率偏低；此為預期過渡狀態，不需要额外的一次性 backfill script（可選：若需加速覆蓋率，另開一次性 management command 補跑舊店家，非本次必要範圍，列入 tasks.md 視情況決定）。
3. Rollback：`ai_summary` 欄位與新 task/service/state 皆為新增，不影響既有欄位與流程；migration 可安全 reverse（欄位為 nullable，無資料遺失風險）。

## Open Questions

- 批次生成 `ai_summary` 的 Groq 呼叫要記在哪個額度 key 下（獨立系統額度 vs. 併入現有月額度）？此問題不影響 spec 行為與任務拆解的形狀，可在 tasks.md 實作階段依 `ApiUsageService` 現況決定，但需在合併前有明確結論並寫進實作註記。
