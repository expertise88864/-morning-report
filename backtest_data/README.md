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
