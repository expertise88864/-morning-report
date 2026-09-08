# backtest_data — 離線回測工具

**用途**:驗證計分/預測改動前的回測證據(專案硬規則:任何計分權重變更須先在此跑出 IC/淨報酬證據,通過才上線)。

**這些腳本納入版控;資料檔不納入**(`.gitignore` 只放行 `*.py` / `README.md` / `reports/*.md`)。
多數腳本需要本機的 `../state/model_history.json`(每日快照面板,約 13MB,由晨報 Actions 累積),
因此**在本機跑**;`yfinance` 在台灣 IP 被 geo-block,涉及即時抓價的腳本需在能連 Yahoo 的環境跑。

執行慣例:`PYTHONIOENCODING=utf-8 python backtest_data/<script>.py`(避免 Windows console 中文亂碼)。

## 腳本索引
| 腳本 | 做什麼 |
|---|---|
| `bt_factor_ic.py` | 各因子(技術/基本面/籌碼)對前瞻 1/3/5/20 日報酬的橫斷面 Spearman IC + t 值 |
| `bt_radar_score.py` | 雷達/Top5 計分「方案」的分位數超額 + IC(含「進場時機/買低」對照);附倖存者偏誤/單一 regime 警語 |
| `bt_top5.py` | 依 ranking 選 Top5 的實際淨報酬/勝率回測 |
| `bt_predictions.py` | 模型點預測 vs 實際的方向命中/誤差 |
| `bt_taiex-fixed.py` / `bt_taiex-learned.py` | 加權開盤預測(固定 vs 學習 beta)配對 t 檢定 |
| `bt_m2330-models.py` / `bt_m2330-premium.py` | 2330 / 00662 溢價模型回測 |
| `bt_podcast_calls.py` | Podcast 主持人看多看空事後表現 |
| `bt_strategy.py` | 00662/2330 策略回測 |
| `build_panel.py` | 由 model_history 建構回測面板(panel.csv) |
| `ic_news_score.py` | 新聞分數 IC(當初據此把新聞 tilt 降權) |

## reports/
`reports/YYYY-MM.md` 放每月因子 IC 自動報告(見 OPTIMIZATION_PLAN.md 的 D3);此子目錄的 `*.md` 納入版控。

## Top3 獨立研究（不替換正式公式）

`selection_research.py` 比較儲存排名與固定「避過熱」假說，僅使用本機快照，
不連網、不呼叫模型、不寫 state。先依訊號選股，再檢查未來價格；缺價時整組配對
不計算，不以其他股票補掉缺价者。三個等額資金槽、未投入留現金；持有區間不重疊。

成本必須明確輸入，以下只是研究假設，並非實際券商費率：
```
python backtest_data/selection_research.py --horizon 20 --fee-bps 15 --sell-tax-bps 30 --slippage-bps 10
```
費用與滑價逐邊計入、交易稅在賣出計入；另以 5 日作風險診斷及較高滑價作敏感度分析。
`bt_top5.py` 舊版的「淨」指扣大盤，不是扣交易成本，不能與本工具的 after-cost 混用。

本工具故意固定輸出 `NO_REPLACEMENT`：已觀察的歷史不是未觸碰的樣本外資料，
快照序列不等於已驗證交易日曆，原始價格未完整處理公司行動/退市，開盤價也不代表
漲跌停或停牌時可以成交。各版本歷史排名不是單一固定模型重播。缺少同時點的大盤
開盤基準與連續每日淨值，因此不輸出大盤超額或偽造最大回撤。

真正替換前還需：驗證訊號可用時間、補齊交易日曆/公司行動/可成交價，凍結候選與
時間切分、隔離重疊標籤，最後用未看過的保留資料或前瞻觀察驗證成本後穩定性。
不能因探索期均值較高就調整正式權重或宣稱改善已成立。
