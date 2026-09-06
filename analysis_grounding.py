# -*- coding: utf-8 -*-
"""**根據**的檢查(與 `analysis_schema` 的**形狀**檢查刻意分開)。

## 為什麼分成兩個模組

第十二輪 P1-3 的教訓正是這兩件事被混為一談:strict structured output
保證了形狀 —— 欄位齊全、型別正確、enum 合法 —— 於是很容易以為輸出「驗過了」。
但形狀完美的報告可以完全沒有根據。

實測反例(外審給的,逐字):`materiality=high` 的 `fact`、`evidence_ids=[]`、
`claim_audit=[]`。這份輸出**零問題通過驗證**,而 renderer 會把它排進
「昨夜三大重點」與「我的明確立場」寄出去 —— 讀信的人看到的是一句
語氣肯定的市場判斷,背後什麼都沒有。

缺陷的形狀是本 repo 記過的那一條:**空集合讓迴圈沒跑**。

## 判準

**會進到信裡的段落,都要帶得出根據。** 空物件不算有內容:
「這天沒有國際盤可談」與「有話要說卻說不出根據」是兩回事,只擋後者。
"""
from __future__ import annotations

#: **接受契約的版本**(第十三輪 P1-3)。這個模組決定 Luna 的輸出被不被
#: 採用、要不要修補、要不要落回 legacy —— 也就是決定 primary 成功率、
#: 成本、延遲與**信裡的內容**。它顯然是實驗系統契約的一部分,而先前
#: 它既不在同群鍵裡、也沒有行為快照:改掉 grounding 規則而不升任何版本,
#: 兩種完全不同的接受行為會被當成同一群樣本相加。
#: v2(schema v2):加 `cross_market_synthesis` —— 漏掉它的話,一段完全
#: 沒有根據的橫向綜合會照樣寄出,而它正是最容易被寫成漂亮空話的一段。
#: v3(第十五輪):接受政策多了「合法但淺 → 加深一次」(修補時機變了
#: 就是接受行為變了)。v4(P2-4):`priced_in` 也要帶證據。
#: v5(第十七輪):接受政策加「張力要有逐筆 resolution」與「鏈要走到
#: 財務層」的深度提示 —— 修補時機再次改變。
#: v18(第二十二輪):key_driver 判準合一 —— **同一條 claim 要同時**
#: 同向且共享證據(split-quantifier:方向靠 c1、證據靠 c2,沒有一條
#: 真的支持);標的判準加概念詞黑名單(GPU 永遠不是標的)、ASCII
#: token 邊界(`Ai` 不再借道 `Taiwan`)、中文名也要在證據裡
#: (先前非 ASCII 一律放行,AMD 新聞可以掛「華碩」)。
#: v19(第二十二輪 defer 三項):horizon 改宣告式矩陣 —— 相鄰一階以內
#: 才相容(「這個月看多」推不出「今天會漲」);帶主體的錨點要在該段的
#: 範圍裡(講台積電的鏈不得錨在鴻海的漲跌上);加深選優改用與觸發同一套
#: 判準(先前 `depth_advisories` 在選優裡沒收到 packet)。
#: v20(Commit C):接受政策多了三大重點的事件契約 —— 指到被排除的
#: 價格變化群、指到不存在的群、真事件不到一半、計分最高的靜默略過。
#: v21(Commit D):接受政策多了三條 —— 方向衝突要給淨效果、共用驅動
#: 要說明為什麼不算重複計權、有總經發布時三個情境分支要條件在它上面。
#: v22(第二十三輪):三大重點每一條都要是事件(半數規則移除);
#: 計分前三全部要處理;第二總經發布不得忽略;淨效果衝突偵測用
#: 別名正規化;加深身分補 key_drivers/淨效果/共同驅動/dismissed;
#: request_gate 改量 `response_schema`(32K schema 先前漏算)。
#: v23/v24(第二十四輪 P1-5~P1-9,判準見 `analysis_contracts`):
#: 三大重點的**條數**成為契約(恰好 `min(3, 合格事件群數)`,分母來自 packet);
#: 可駁回集合統一成 必分析 ∪ 計分前三 ∪ 總經發布(先前三套契約互相矛盾);
#: **每一個**未被駁回的總經發布都要條件在三個情境分支上(先前只驗第一個);
#: 結構化引用的**指涉**完整性(淨效果要有 claim 根據、主張要關於那個標的、
#: cluster 引用要指得到真的東西)—— schema 保證形狀,保證不了指涉。
#: v26(第二十六輪 P1-5):淨效果「兩側各一條主張」要**錨回證據側** ——
#: 那一側先前由主張自己的 `direction` 標籤決定,而標籤是輸出自己填的。
#: v27(P1-6):會計期間(`Q2`/`FY25`)不是標的;「永遠不是標的」與
#: 「與這件事無關」拆成兩個問題,訊息才說得出真正的理由。
#: v28(第二十九輪外審 P1-2):標的權威全域化 —— 非 ASCII 實體也走
#: 宣告閘門(「聯準會」不是標的)、撞名英文單字的 ticker 不得靠標題
#: 裸字命中(`NOW`/`NET`/`ARM`/`SNOW`/`COIN`)、台股代號不在證據裡時
#: 不論 universe 在不在都擋(含 `packet is None` 的舊路徑)。
#: v29(2026-08-11 生產驗收):**主角與傳導對象是兩件事** ——
#: 受影響標的可以不是新聞的主角,但要是本報核心標的或宣告過的供應鏈
#: 鄰居,而且要寫得出傳導機制。舊規則與這份報告的用途矛盾:
#: 「油價暴漲 → 通膨 → 估值 → 00662」被判成幽靈標的,連兩天整份作廢。
#: v30(2026-08-11):鏈的連續性改用辨識詞重疊 —— **改寫不是斷鏈**,
#: 這一關要抓的是不相干的片段被接成因果。
#: v31(2026-08-11 CI #492):當日 universe 裡的台股可以是傳導對象
#: (它是真的在交易的股票 —— 這道閘門要擋的假代號不成立);
#: 美股那側的規則不變。判準變了就要進版。
#: v32(2026-08-11 CI #495):鏈的連續性改問「上一步有沒有指名過
#: 這一步的起點」,不再看重疊比例(長句會把它稀釋掉)。
#: v33(2026-08-12 生產):`gap:other:<標籤>` 視同 `gap:other`(標籤更
#: 有資訊,gap ID 不被解參照,守的是回填**宣告過的**缺口那種假揭露)。
#: v34(2026-08-12 CI #502):data_gaps 的 need 集合改讀
#: `packet["required_disclosures"]`(模型看到的那一格)——
#: `gap:payload_omitted:*` 先前被自己的驗證器判成回填。
#: v35(第三十一輪外審 P1-4):鏈交接拆掉兩個 fail-open —— 辨識詞太少
#: 改不接(照抄的短節點由包含判準放行)、單一泛用詞不算指名。
#: v36(v22,repo-wide 外審 2026-08-19 P1-B):RENDERED 涵蓋 v20/v21 六個
#: 新段落;narrative_delta 綁 prior_view_id + evidence_ids、macro 三切面
#: 有內容必有證據(硬性檢查在 analysis_validate)。
#: v38(2026-08-28 生產):共用驅動的駁回訊息改指名**整組** cluster_ids
#: (原本報交集,而下一關要求與整組一致 —— 模型照做就被打回,兩關
#: 合起來讓修補收斂不了);`required_cluster_ids` 保序去重。
#: 驗證行為變了就要升版,樣本才不混群。
GROUNDING_VERSION = 41  # 深入主題不得漏寫；只要求取材覆蓋，不改投資權威。

#: 會被 renderer 排進信裡的段落。
RENDERED = ("executive_summary", "key_drivers", "taiwan_market",
            "global_market", "top_news_analysis", "scenario_tree",
            "contradictions", "portfolio_implications",
            "cross_market_synthesis", "priced_in",
            # 外審 P1-7.4:淨效果會進信(「合起來是利多還是利空」),
            # 卻不在這份清單裡 —— 於是品質指標與孤兒判定都看不到它。
            "asset_net_effects",
            # v22(repo-wide 外審 2026-08-19 P1-B):v20/v21 的六個新段落
            # 全部會進信,卻不在這份清單 —— is_rendered/孤兒判定看不到
            # 它們。narrative_delta/macro 的證據**硬性**檢查在
            # analysis_validate(單一判準,這裡不重複第二份)。
            "world_events", "upcoming_event_scenarios", "narrative_delta",
            "macro_environment", "taiwan_local", "taiwan_policy")

#: schema 裡帶 `evidence_ids` 而且會被寄出去的物件段落
#: (`key_drivers` 與 `claim_audit` 是清單,另外處理)。
#: 第十六輪 P2-4:`priced_in` 已經進 RENDERED 卻不在這裡 —— 於是
#: 「市場已完全反映降息」這種**高推論性**的句子可以完全沒有證據就寄出。
#: 它比一般新聞摘要**更**需要根據,因為它宣稱的是市場的預期狀態。
EVIDENCE_BEARING = ("market_regime", "taiwan_market", "global_market",
                    "cross_market_synthesis", "priced_in")


def has_content(node: dict) -> bool:
    """這個段落**真的有話要說**嗎(`evidence_ids` 不算)。

    r1(Codex,P2):**不能用 dict 的 truthiness 判斷。** strict schema 規定
    所有欄位必填,所以資料不足那天的合法空段落是「欄位都在、值都空」
    —— 它是 truthy 的,於是「有內容卻沒有證據」會誤報,Luna 白白修補一次
    再落回 legacy,而那一段根本沒有任何文字會進信。
    **誤判的代價不是漏擋,是讓 Luna 在資料稀薄的日子看起來比較不可靠**,
    而那正是這個實驗要量的東西。
    (先前的測試用 `{}` 當空段落 —— 那不是 strict 輸出真正的形狀。)

    repo-wide 外審 2026-08-19 P2-2:**巢狀也要遞迴判**。v22 的合法空
    macro 是三個 `{analysis:"", evidence_ids:[]}` —— 內層 dict 本身
    truthy,淺判會把「完全空」當成有內容,稀薄日整份被誤退
    (正是本 docstring 警告的形狀,換了一層深度捲土重來)。
    """
    if not isinstance(node, dict):
        return False
    for k, v in node.items():
        if str(k) == "evidence_ids":
            continue
        if isinstance(v, str):
            if v.strip():
                return True
        elif isinstance(v, dict):
            if has_content(v):
                return True
        elif v:
            return True
    return False


def is_rendered(obj: dict) -> bool:
    """這份輸出有沒有東西真的會被寄出去。"""
    for k in RENDERED:
        v = (obj or {}).get(k)
        if isinstance(v, dict):
            if has_content(v):
                return True
        elif isinstance(v, str):
            if v.strip():
                return True
        elif v:
            return True
    return False


def problems(obj: dict) -> list:
    """回傳「有話說卻說不出根據」的清單(空 = 通過)。"""
    out: list = []
    if not isinstance(obj, dict):
        return out
    for i, d in enumerate(obj.get("key_drivers") or []):
        if isinstance(d, dict) and not (d.get("evidence_ids") or []):
            out.append(f"key_drivers[{i}] 會被排進信裡卻沒有任何證據")
    for sec in EVIDENCE_BEARING:
        node = obj.get(sec)
        if has_content(node) and not (node.get("evidence_ids") or []):
            out.append(f"{sec} 有內容卻沒有任何證據")
    for i, n in enumerate(obj.get("top_news_analysis") or []):
        if isinstance(n, dict) and not str(n.get("source_item_id") or "").strip():
            out.append(f"top_news_analysis[{i}] 沒有指明是哪一則新聞")
    # `claim_audit` 是稽核軌跡本身。它空著時,上面每一條逐項檢查都會因為
    # 「沒有東西可迭代」而通過 —— 那是最安靜的一種假通過,所以它自己要被檢查。
    if is_rendered(obj) and not (obj.get("claim_audit") or []):
        out.append("有內容要寄出,claim_audit 卻是空的(無從稽核)")
    return out
