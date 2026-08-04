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

### 批#79 `(下一個 commit)` —— Luna 特化路徑的 TypeError 根因
`evidence_packet.canonical_json` 用 `sort_keys=True`,而 **`sort_keys` 在鍵混
型別時會拋 `TypeError: '<' not supported between instances of 'int' and 'str'`**。
`default=str` 保護的是**值**,沒有人保護鍵 —— 而那個函式的 docstring 自己寫著
「寧可得到穩定字串,也不要讓整個 packet 拋例外」。宣稱與實作差一層,
而差的那一層正好是宣稱要解決的問題。

`build()` 只對 news 算 `core_sha` 所以沒事;`build_luna_bundle()` 對整個
packet 算 `evidence_sha` 才炸 —— Luna 特化路徑因此連兩天(08-03、08-04)
落回 legacy,實驗 0/10。

修法:先用型別感知的順序(`(type(k).__name__, str(k))`)重建整棵樹,
再以 `sort_keys=False` 輸出。**全字串鍵時輸出逐位元組相同**(有測試釘住),
混型別時不再拋。另加 `nonstring_key_paths()` 並寫進 manifest,
下次才知道是哪個上游欄位塞了非字串鍵。

**外審應特別看的地方**:
1. 「逐位元組相同」那條測試是否真的涵蓋所有既有形狀(它決定既有 sha 會不會變)
2. `{1: 'a', '1': 'b'}` 撞鍵時資料會少一筆 —— 那是 `json.dumps` 的既有行為,
   我只釘住它沒有改;這樣處理對不對?
3. 診斷欄位寫進 manifest 會不會洩漏內容(目前只寫鍵與型別,不寫值)


### 批#80 `(下一個 commit)` —— 「還是在堆疊數據」的兩側補法
使用者 2026-08-04 第二次反映「很多地方還都只是在呈現數字、堆疊數據,
沒有詳細分析影響」。查下去是**兩個不同的原因**:

**(a) LLM 側**:prompt 有一條「不要重述 EVIDENCE 裡 Python 已經算好的數字」,
用意是避免重複列表,**結果是那些區塊沒有任何人負責解讀**。
改成「不要逐項重列,但要合起來讀」,並新增 R17 指定在「我的明確立場」段的
理由裡用 2–3 句回答:錢往哪裡去、跟今天的立場一致還是矛盾、什麼會讓它反轉。
七之二 60→90 字,要求寫得出傳導路徑而不是四個字的抽象標籤
(「貿易規則再起法律戰」那種)。兩份 profile 版本 4→5。

**(b) Python 側**:Top5 卡片每檔排出約 15 個數字而一句話都沒有,
prompt 改再多也碰不到。新模組 `top5_readout.py` 把已算好的欄位翻成一句話,
**衝突優先講**(外資買但大戶減、漲卻量縮、法人內部不同調)。

**外審應特別看的地方**:
1. `top5_readout` 的門檻(PER<12 偏低 / >25 偏高、殖利率≥5、量比 0.8/1.5、
   大戶 ±0.10)是**我訂的**,對不對?會不會在某些產業說反話?
2. 「只描述不建議」目前靠一份禁用詞清單掃描 —— 那個清單夠不夠?
   有沒有句子在沒有禁用詞的情況下仍然讀起來像建議?
3. R17 要求「矛盾時要明講」,但沒有任何東西驗證模型真的照做
   (grounding 檢查管的是證據引用,不管這個)。這個缺口要不要補?
4. 七之二 放寬到 90 字會不會讓那段從「速覽」變成第二個八段?


## 補審完成後

把上面那一列從清單刪掉;清單空了就**刪掉整個檔案** ——
留一個空的待審清單在 repo 裡,下次有人看到會以為已經審過了。
