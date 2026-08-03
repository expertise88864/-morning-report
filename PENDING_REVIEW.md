# ⚠ 已上線但**尚未通過外審**的變更

本 repo 的規約是「push 前先過 Codex(GPT-5.6-sol)read-only 外審,APPROVE 才上線」。
下面這些 commit 是**例外**:使用者在 2026-08-03 明確決定先推上去、標記未審,
等額度恢復後一次補審。

## 待補審的範圍

| 項目 | 值 |
| --- | --- |
| 最後一個**已 APPROVE** 的 commit | `7eb60b3`(批#77) |
| 待審的第一個 commit | `6059d59`(批#78) |
| 阻塞原因 | Codex 額度用罄,重置 **2026-08-08 11:39** |
| 使用者決定 | 2026-08-03「都先 push 上去但是標記未審,等額度回復再一次審」 |

## 額度恢復後要跑的指令

```
bash tools/codex_review.sh targeted 7eb60b3 .codex-review/context.md
```

**base 是 `7eb60b3` 而不是 `origin/main`** —— 用 `origin/main` 的話 diff 是空的,
外審會對著沒有變更的樹說 APPROVE,而那是一個看起來通過、實際什麼都沒審的結果。
這正是本 repo 最常見的失效形狀(守衛在空集合上真空通過)。

`.codex-review/` 沒有被 git 追蹤(在 `.git/info/exclude` 裡),所以 context 檔
要重新寫。內容至少要涵蓋下表的每一批。

## 待審清單

### 批#78 `6059d59` —— P1-4 分側成本與延遲進 durable ledger
- 新模組 `side_telemetry.py`:`from_manifest()` 逐側擷取、`side_costs()` 跨帳本彙總
- `build_record(telemetry=)` → `primary/shadow/extractor_telemetry` 三個欄位
  (進 `PROVENANCE_FIELDS`、**不進 `COHORT_FIELDS`**)
- `record_day` 報表加 `side_costs`(傳全部嘗試,不是代表樣本)
- `_material_live` → `blind_review.material_live`(騰位,不調上限)

**外審應特別看的地方**(我自己知道風險在哪):
1. 抽取器標 `attribution="shared"` 不分攤 —— 這個決定對不對?
2. 「確定沒有失敗嘗試 → `0.0`」與「沒量到 → `None`」的界線畫在哪裡才對
3. `_experiment_row` 新增的 `_st.from_manifest()` 會不會弄壞晨報
   (我查到保護在 `experiment_record.record_failure` 的 try/except,
    但那是**間接**的,值得第二雙眼睛)
4. `days_measured` 與 `rows_seen` 差很多時沒有告警,只是兩個數字都報

**在缺外審的情況下我做過的替代驗證**:
`preflight.sh` exit 0、1686 passed、真實 manifest 形狀跑過、四項突變驗證
(其中「拿掉生產呼叫端的 `telemetry=`」一開始沒紅,補測試後才紅)。

## 補審完成後

把上面那一列從清單刪掉;清單空了就**刪掉整個檔案** ——
留一個空的待審清單在 repo 裡,下次有人看到會以為已經審過了。
