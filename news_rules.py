"""新聞分類 / 來源分級 / 降噪規則 + 台灣政策·醫界情報關鍵字(A5-B3 由 morning_report 抽出)。
純規則函式(關鍵字比對、來源評級、去重、重要性打分)與其關鍵字常數;無網路/狀態,
只依 stdlib(各函式內部自行 import re/difflib)。morning_report 以 re-export 保相容,
既有測試零修改。後續 news_events(B5)如需 NEWS_POSITIVE/NEGATIVE_TERMS 由此 import。
"""
import re
from typing import Optional


NEWS_POSITIVE_TERMS = [
    "上修", "優於預期", "創高", "成長", "增加", "擴產", "訂單", "得標",
    "獲利", "轉盈", "調升", "beat", "raise", "raised", "growth", "record",
    "order", "orders", "contract", "contracts", "expand", "expanded",
    "increase", "increased", "upgrade", "upgraded",
]


NEWS_NEGATIVE_TERMS = [
    "下修", "低於預期", "衰退", "減產", "砍單", "虧損", "轉虧", "調降",
    "禁令", "出口管制", "制裁", "召回", "訴訟", "miss", "cut", "lower",
    "decline", "declined", "loss", "losses", "ban", "banned", "sanction",
    "sanctions", "recall", "lawsuit", "downgrade", "downgraded",
]


TECH_NEWS_ANALYST_NOISE = [
    "目標價", "上看", "喊買", "喊到", "看好上", "評等", "重申", "調升評等", "調降評等",
    "投顧", "分析師看", "外資點名", "外資喊", "法人喊", "buy 評等",
    "target price", "price target", "overweight", "outperform", "reiterate", "initiate",
]


TECH_NEWS_CHIPFLOW_NOISE = [
    "買超", "賣超", "三大法人", "外資連", "投信連", "自營商連", "籌碼", "法人動向",
    "土洋對作", "權證", "融資增", "融券增",   # 「認購/認售」太廣(認購私募=實質公司動作)→ 只留權證
]


TECH_GATE_CATALYST = [
    # 訂單/接單/產能
    "訂單", "新訂單", "得標", "接單", "大單", "下單", "投片", "擴產", "產能", "良率",
    # 營運/財報事件(具體,非泛詞;不用裸「上修/下修」——會放行「上修目標價」這類喊價,
    # 真正的財測上修由「財測」涵蓋,另收「上修/下修展望」)
    "法說", "財報", "財測", "上修展望", "下修展望", "轉盈", "轉虧", "beat", "miss",
    # 製造/產品
    "量產", "出貨", "投產", "流片", "tape-out", "tapeout", "認證", "漲價", "報價",
    # 投資/設廠/併購
    "設廠", "建廠", "併購", "收購", "簽約", "簽訂", "合作",
    # 負面具體事件
    "砍單", "減產", "停產", "罷工", "火災", "資遣", "裁員", "召回", "訴訟",
    "出口管制", "禁令", "制裁", "sanction", "sanctions", "ban", "banned", "recall", "lawsuit",
]


TW_POLICY_FINANCE_TERMS = (
    "稅", "關稅", "電價", "能源", "油價", "匯率", "利率", "升息", "降息",
    "房貸", "房市", "信用管制", "新青安", "囤房", "地價",
    "金管會", "央行", "中央銀行", "證交所", "證期", "金融", "保險業", "壽險",
    "半導體", "晶片", "出口", "貿易", "產業園區", "科學園區", "投資",
    "基本工資", "最低工資", "勞保", "勞退", "就業保險", "缺工",
    "健保費", "補助", "振興", "碳費", "碳權", "電動車", "AI", "招商",
    # 批#31(2026-07-25 使用者反映「未來帳戶」整個沒進政策區):白名單原本只認
    # 「補助/津貼」等既有詞,新政策若用新名詞(未來帳戶、兒童帳戶)一律召回=False
    # 被整條剔除。補民生金融語族——**一律用複合詞,不放裸詞**(Codex 批#31 r1 F5:
    # 裸「財產/資產/基金/現金」會讓「公職人員財產申報法」「資產活化」等行政法案
    # 混進政策區,官方來源+重大詞即可衝到 5.9 分進深度解析)。
    "未來帳戶", "兒童帳戶", "儲蓄帳戶", "個人帳戶", "開戶",
    "普發現金", "現金發放", "定期定額", "儲蓄", "存款", "信託",
    "主權基金", "國安基金", "退撫基金", "勞退基金",
    "年金", "退休金", "國民年金",
)


TW_INTELLIGENCE_ENTITY_TERMS = (
    "\u65b0\u9752\u5b89", "\u80b2\u5152\u6d25\u8cbc", "\u5c11\u5b50\u5316",
    "\u623f\u8cb8", "\u5065\u4fdd", "\u4f4f\u9662", "\u6025\u8a3a",
    "\u885b\u798f\u90e8", "\u5065\u4fdd\u7f72", "\u884c\u653f\u9662",
    "\u4e2d\u69ae", "\u53f0\u4e2d\u69ae\u7e3d", "\u81fa\u4e2d\u69ae\u7e3d",
)


TW_INTELLIGENCE_RELEVANCE = {
    "policy": (
        "政策", "補助", "津貼", "新青安", "房貸", "租屋", "社福", "長照",
        "育兒", "托育", "勞保", "稅", "電價", "能源", "產業", "草案",
        "行政院", "立法院", "金管會", "央行", "部會", "鬆綁", "管制",
        "修法", "預告", "上路", "補貼", "少子化", "人口", "住宅",
    ),
    "medical": (
        "醫院", "醫療", "醫界", "住院", "門診", "急診", "停診", "醫師",
        "護理", "健保", "藥價", "藥品", "醫材", "病安", "衛福部", "健保署",
        "疾管署", "食藥署", "疫情", "疫苗", "傳染病", "臨床", "手術",
        "中榮", "台中榮總", "神外", "代刀", "停約", "抵扣停約",
        "裁罰", "健保申報", "停業", "醫療量能",
    ),
}


TW_INTELLIGENCE_BROAD_RECALL = {
    "policy": (
        "台灣", "行政院", "立法院", "部", "署", "會", "政策", "補助",
        "津貼", "草案", "修法", "上路", "預告", "法案", "新制",
    ),
    "medical": (
        "台灣", "醫", "院", "健保", "衛福", "疾管", "食藥", "疫情",
        "藥", "病床", "急診", "門診", "住院", "手術", "護理",
    ),
}


TW_INTELLIGENCE_NOISE = {
    "policy": ("娛樂", "體育", "影劇", "股價", "星座", "食譜",
               "宗教", "毒駕", "酒駕", "性別平等", "性平", "兵役", "替代役",
               "宣導列車", "宣導活動", "揭牌", "剪綵", "頒獎", "表揚",
               "觀光活動", "演習", "招生", "考試", "藝文", "節慶"),
    # 港澳媒體/機構新聞混入過(博愛醫院/醫管局/文匯網)→ 一律剔除,只留台灣醫界
    "medical": ("保健食品", "養生", "星座", "減肥", "美容", "食譜", "偏方",
                "香港", "澳門", "醫管局", "入稟", "文匯", "星島", "singtao",
                "hk01", "東網", "on.cc", "明報", "大公"),
}


# 使用者關注的房市政策詞(命中即政策區加權;2026-07-17 使用者要求新青安 3.0 必見)
TW_POLICY_USER_FOCUS_TERMS = (
    "新青安", "打炒房", "囤房稅", "限貸", "信用管制", "青年安心成家",
    # 批#31:重大民生金融政策(2026-07-24「台灣未來帳戶」召回後僅 2.7 分、擠不進
    # 政策卡前 3)。比照新青安加權——這類「全民適用、金額明確、有上路時程」的
    # 政策是本報固定深度追蹤對象。
    "未來帳戶", "兒童帳戶", "主權基金", "普發現金", "年金改革", "退休金改革",
)

# 重大政策深度解析門檻(批#31):政策卡條目重要性 ≥ 此值 → 進 prompt 供
# 「十一之二、重大政策深度解析」逐項拆解措施與影響。新青安 3.0 實測 6.6、
# 未來帳戶(加權後)約 5.9-6.6,一般行政公告多在 5 分上下。
TW_POLICY_DEEPDIVE_MIN_SCORE = 5.5

TW_INTELLIGENCE_MAJOR_TERMS = {
    "policy": (
        "通過", "核定", "公告", "上路", "修法", "草案", "預告", "補助",
        "津貼", "新青安", "電價", "稅", "勞保", "健保", "少子化",
        "房貸", "信用管制", "行政院", "立法院",
    ),
    "medical": (
        "停約", "停診", "停業", "暫停", "住院", "急診", "病房", "病床",
        "醫療量能", "裁罰", "感染", "疫情", "疫苗", "缺藥", "藥價",
        "健保署", "衛福部", "疾管署", "食藥署", "醫院", "醫學中心",
    ),
}


# 註:任職醫院(彰基/中國醫)的一般/建設消息已改由「在地快訊」卡涵蓋
# (2026-07-15 使用者拍板整合);醫界卡對兩院只收硬新聞(裁罰/感染…),走一般規則。
TW_MEDICAL_HARD_NEWS_TERMS = (
    "停約", "解約", "抵扣停約", "裁罰", "罰鍰", "開罰", "重罰", "處分",
    "懲處", "違規", "違法", "停業", "勒令", "撤照", "廢止", "吊照",
    "糾紛", "醫糾", "疏失", "代刀", "密醫", "弊", "賄", "貪", "詐領", "溢領",
    "起訴", "判刑", "判賠", "判決", "求償", "假扣押",
    "缺藥", "斷藥", "短缺", "回收", "下架",
    "群聚", "爆發", "院內感染", "食物中毒", "中毒", "疫情升溫",
    "罷工", "抗議", "請辭", "出走", "倒閉", "停辦", "示警",
    "致死", "死亡", "事故", "醫療事故", "醫療疏失",
)


TW_MEDICAL_CAPACITY_NEWS_TERMS = (
    "\u6025\u8a3a\u58c5\u585e", "\u6025\u8a3a\u7206\u6eff", "\u6025\u8a3a",
    "\u4f4f\u9662\u696d\u52d9", "\u4f4f\u9662", "\u66ab\u505c\u4f4f\u9662",
    "\u66ab\u505c\u6536\u6cbb", "\u505c\u6536", "\u95dc\u5e8a", "\u7e2e\u5e8a",
    "\u6eff\u5e8a", "\u75c5\u5e8a\u5403\u7dca", "\u5019\u5e8a",
    "\u8b77\u7406\u4eba\u529b", "\u91ab\u5e2b\u8352", "\u4eba\u529b\u4e0d\u8db3",
    "\u91ab\u7642\u91cf\u80fd", "\u91cf\u80fd", "\u6551\u8b77\u8eca",
    "\u91cd\u75c7", "\u5152\u79d1", "\u795e\u5916", "\u91ab\u9662\u670d\u52d9",
)


TW_MEDICAL_ROUTINE_NOISE = (
    "招考", "招募", "錄取", "甄選", "甄試", "約僱", "徵才", "職缺", "報名", "招生",
    "空床", "床數", "住院數", "一覽表", "參考表", "看診時間", "門診表", "代診",
    "衛教", "講座", "課程", "研習", "宣導", "義診", "篩檢", "免費", "活動",
    "保健", "養生", "菜單", "食譜", "祝賀", "得獎", "獲獎", "表揚", "捐贈",
    "揭牌", "啟用", "剪綵", "週年", "感謝", "公益", "志工", "捐血",
)


TW_MEDICAL_ROUTINE_NOISE_EXTRA = (
    "\u4f4f\u9662\u6578", "\u7a7a\u5e8a", "\u4e00\u89bd\u8868",
    "\u62db\u8003", "\u9304\u53d6", "\u514d\u8cbb\u63a1\u6aa2",
    "\u885b\u6559", "\u63d0\u9192",
)


def _tw_intelligence_topic(kind: str, text: str) -> str:
    groups = (
        ("住宅金融", ("新青安", "房貸", "租屋", "房價", "信用管制")),
        # 批#31:新型民生金融政策(未來帳戶/主權基金/普發現金/年金)原本落入
        # 「其他政策」而少 0.7 分,擠不進政策卡前 3
        ("民生金融", ("未來帳戶", "兒童帳戶", "主權基金", "普發", "年金",
                      "退休金", "儲蓄", "開戶", "信託")),
        ("育兒社福", ("育兒", "津貼", "托育", "長照", "勞保", "社福")),
        ("產業能源", ("半導體", "能源", "電價", "AI", "出口", "產業")),
        ("醫院營運", ("醫院", "住院", "急診", "停診", "門診", "人力", "停約", "中榮", "神外")),
        ("健保藥政", ("健保", "藥價", "藥品", "醫材", "食藥署")),
        ("公共衛生", ("疫情", "疫苗", "疾管署", "傳染病", "食安")),
    )
    for topic, tokens in groups:
        if any(token in text for token in tokens):
            return topic
    return "其他政策" if kind == "policy" else "其他醫界"


def _tw_intelligence_recall_hit(kind: str, text: str) -> bool:
    """Broad recall: allow source/category words first, then score importance later."""
    if any(token in text for token in TW_INTELLIGENCE_NOISE[kind]):
        return False
    if kind == "medical" and any(token in text for token in TW_MEDICAL_ROUTINE_NOISE_EXTRA):
        return False
    broad = any(token in text for token in TW_INTELLIGENCE_BROAD_RECALL[kind])
    specific = any(token in text for token in TW_INTELLIGENCE_RELEVANCE[kind])
    major = any(token in text for token in TW_INTELLIGENCE_MAJOR_TERMS[kind])
    if kind == "medical":
        # 醫界區只要「事件性硬新聞」(停約、裁罰、糾紛、缺藥、群聚感染…)。
        # 例行/行政/衛教(招考、空床數、義診、免費篩檢、衛教講座…)若無事件詞,一律剔除。
        hard = any(token in text for token in TW_MEDICAL_HARD_NEWS_TERMS)
        capacity = any(token in text for token in TW_MEDICAL_CAPACITY_NEWS_TERMS)
        if not (hard or capacity):
            return False
        return specific or broad
    if kind == "policy":
        # 政策區必須「與財經/投資相關」(使用者回饋:宗教/毒駕/性平等雜訊太多)
        if not any(token in text for token in TW_POLICY_FINANCE_TERMS):
            return False
        # 焦點政策(新青安/未來帳戶/普發現金…)一律召回:這些是本報固定深度追蹤的
        # 重大民生金融政策,標題常只有政策名+金額而無「行政院/上路」等重大詞
        # (批#31:「普發現金一萬元 8月入帳」原本 broad/major 皆不中而被剔除)
        if any(token in text for token in TW_POLICY_USER_FOCUS_TERMS):
            return True
    return specific or (broad and major)


def _tw_intelligence_entity_key(title: str) -> str:
    text = str(title or "")
    for term in TW_INTELLIGENCE_ENTITY_TERMS:
        if term and term in text:
            return term
    for raw in text.replace("-", " ").split():
        token = "".join(ch for ch in raw if ch.isalnum())
        if 2 <= len(token) <= 18 and any(
            suffix in token for suffix in (
                "\u90e8", "\u7f72", "\u9662", "\u6703", "\u59d4\u54e1\u6703",
                "\u5c40", "\u8655", "\u91ab\u9662", "\u4e2d\u5fc3",
            )
        ):
            return token
    return ""


# 政策錨點別名 → 正名(批#31 r2):同一政策的不同媒體稱呼收斂到同一 timeline。
# 只收「確定是同一項政策」的別名;語意相近但實為不同制度者(如國民年金 vs
# 軍公教退撫)**不可**併,否則會把兩個政策的細節混寫成一段。
_TW_POLICY_ANCHOR_ALIASES: dict[str, str] = {
    "兒童帳戶": "未來帳戶",      # 台灣未來帳戶 / 兒童(未來)帳戶為同一兒少儲蓄政策
}

# 退休/年金類政策的正規化(Codex 批#31 r3/r4):媒體對**同一制度**混用
# 「退休金改革」與「年金改革」(→ 該併),但不同制度(軍公教 / 勞工 / 國民年金)
# **絕不可**併(資格與金額完全不同)。故不以「退休金 vs 年金」字面判斷,而是
# 取「制度對象」為正規識別:軍公教退休金改革 與 軍公教年金改革 → 同一 anchor。
_TW_PENSION_SCHEME_TERMS = (
    ("軍公教", "軍公教年金"), ("公教", "軍公教年金"),
    ("勞工", "勞工退休金"), ("勞退", "勞工退休金"),
    ("國民年金", "國民年金"),
)
_TW_PENSION_GENERIC_ANCHORS = {"年金", "退休金", "國民年金"}


def _tw_intelligence_timeline_key(kind: str, title: str, link: str = "") -> str:
    """Group developing policy/medical stories into stable, human-scale timelines."""
    topic = _tw_intelligence_topic(kind, title)
    anchors = {
        "住宅金融": ("新青安", "房貸", "信用管制", "租屋", "住宅"),
        # 批#31 r1 F3(Codex):新增 民生金融 topic 卻沒給錨點,anchor 會退回
        # topic 本身 → 未來帳戶與普發現金撞成同一 timeline_key、被上游去重丟掉
        # 一個(實測兩者皆為 policy:民生金融:民生金融:)
        "民生金融": ("未來帳戶", "兒童帳戶", "普發現金", "主權基金", "國安基金",
                     "國民年金", "退休金", "年金", "信託", "儲蓄"),
        "育兒社福": ("育兒", "兒少", "成長津貼", "托育", "長照", "少子化"),
        "產業能源": ("電價", "能源", "半導體", "AI", "出口", "產業"),
        "醫院營運": ("中榮", "台中榮總", "神外", "停約", "急診", "住院", "病床"),
        "健保藥政": ("健保", "藥價", "藥品", "醫材", "食藥署"),
        "公共衛生": ("疫情", "疫苗", "疾管署", "傳染病"),
    }.get(topic, ())
    anchor = next((token for token in anchors if token in title), topic)
    # 同一政策的別名收斂到同一錨點(Codex 批#31 r2:媒體對同一兒少儲蓄政策
    # 有「台灣未來帳戶」「兒童帳戶」兩種寫法,錨點不同會拆成兩個政策、
    # 各佔一個深度解析名額且細節無法合併)。比照 _TW_MEDICAL_ORG_ALIASES 作法。
    anchor = _TW_POLICY_ANCHOR_ALIASES.get(anchor, anchor)
    # 退休/年金:改以「制度對象」為正規識別(見 _TW_PENSION_SCHEME_TERMS)。
    # 標題未點明對象者維持泛稱 anchor(年金/退休金),下游不跨 entity 合併——
    # 不知道是哪一套制度時,寧可拆開也不要混寫。
    if anchor in _TW_PENSION_GENERIC_ANCHORS:
        anchor = next((canon for kw, canon in _TW_PENSION_SCHEME_TERMS
                       if kw in title), anchor)
    entity = _tw_intelligence_entity_key(title)
    return f"{kind}:{topic}:{anchor}:{entity}"


def _tw_intelligence_importance(kind: str,
                                title: str,
                                official: bool,
                                scope: str,
                                status: str) -> tuple[float, list[str]]:
    """Score recalled items so keywords expand coverage without flooding the report."""
    reasons = []
    score = 0.0
    if official:
        score += 2.0
        reasons.append("官方/主管機關")
    if scope == "昨日新訊":
        score += 1.5
        reasons.append("昨日新訊")
    if status in ("已公告", "研議中"):
        score += 1.0
        reasons.append(status)
    if kind == "medical":
        # 醫界:事件性硬新聞優先(停約/裁罰/糾紛/缺藥…),例行/行政/衛教重扣。
        if any(token in title for token in TW_MEDICAL_HARD_NEWS_TERMS):
            score += 2.5
            reasons.insert(0, "重大事件")
        if any(token in title for token in TW_MEDICAL_CAPACITY_NEWS_TERMS):
            score += 2.0
            reasons.insert(0, "\u91ab\u7642\u91cf\u80fd/\u670d\u52d9\u4e2d\u65b7")
        if (any(token in title for token in TW_MEDICAL_ROUTINE_NOISE)
                or any(token in title for token in TW_MEDICAL_ROUTINE_NOISE_EXTRA)):
            score -= 3.0
            reasons.append("例行/行政")
    major_hits = [token for token in TW_INTELLIGENCE_MAJOR_TERMS[kind] if token in title]
    if major_hits:
        score += min(2.5, 0.7 * len(major_hits))
        reasons.append("重大詞:" + "、".join(major_hits[:3]))
    if kind == "policy" and any(
            token in title for token in TW_POLICY_USER_FOCUS_TERMS):
        # 使用者關注主題加權(2026-07-17:兩度反映「新青安 3.0 沒看到」——媒體房市
        # 政策條目 2.9 分被官方行政公告 6+ 分壓死,永遠擠不進前 3)。顯示層個人化,
        # 與股價模型無關。
        score += 2.5
        # 顯示文案中性化(Codex 批#15 r3:「使用者關注」會原樣渲染進信件,
        # 違反「不得提及使用者」規範);標籤依實際命中詞給(批#31:未來帳戶等
        # 民生金融政策也走這條加權,不可一律標成「房市政策」)
        _housing = ("新青安", "打炒房", "囤房稅", "限貸", "信用管制", "青年安心成家")
        reasons.insert(0, "本報關注:房市政策"
                       if any(t in title for t in _housing)
                       else "本報關注:重大民生政策")
    topic = _tw_intelligence_topic(kind, title)
    if topic not in ("其他政策", "其他醫界"):
        score += 0.7
        reasons.append(topic)
    if any(token in title for token in TW_INTELLIGENCE_NOISE[kind]):
        score -= 3.0
        reasons.append("疑似雜訊")
    return round(max(0.0, score), 2), reasons[:4]


# 英文 token 一律 word boundary:舊寫法 "sec" in text 是子字串比對,
# "second"/"sector"/"insecure" 都會被誤判成 SEC 官方 A 級(GPT-5.6 三審 P1,
# 實際比 review 指出的更糟)。中文詞無詞界問題,維持 substring。
_A_GRADE_EN = re.compile(r"\b(federal reserve|treasury|sec|mops|twse|taifex)\b")
_A_GRADE_ZH = ("中央銀行", "證交所", "公開資訊觀測站")
_B_GRADE_EN = re.compile(
    r"\b(cnbc|bloomberg|reuters|cnyes|udn|cna|scmp|nikkei|bbc"
    r"|moneydj|technews|digitimes|yahoo)\b")
_B_GRADE_ZH = ("鉅亨", "工商", "經濟日報", "聯合", "中央社", "南華",
               "科技新報")


def _grade_from_text(text: str, allow_a: bool = True) -> str:
    """從單一字串判斷來源等級;無法判斷回空字串。
    allow_a=False 用於「標題」欄位:標題提到 SEC/央行只代表事件主角是官方機構,
    不代表發布者是官方來源——A 級只能由 source/source_name(發布者身分)判定;
    標題僅用於辨識 Google News 尾綴的主流媒體名(B 級)(GPT-5.6 三審 P1)。"""
    text = (text or "").lower()
    if allow_a and (_A_GRADE_EN.search(text)
                    or any(token in text for token in _A_GRADE_ZH)):
        return "A"
    if _B_GRADE_EN.search(text) or any(token in text for token in _B_GRADE_ZH):
        return "B"
    return ""


def _news_source_grade(item: dict) -> str:
    """新聞來源分級：官方 A、主流媒體 B、聚合或未識別來源 C。
    Google News / 類股 feed 的 source 只是聚合器代號(如 Google:NVDA、類股-金融-台股),
    真正的發布媒體在 source_name、或 Google 標題結尾「- 經濟日報」。三者一起看,
    否則正版個股新聞會被誤判為 C → 去重時輸給舊版、且被當低可信度。
    標題只允許升到 B(見 _grade_from_text 的 allow_a 說明)。
    聚合器代號(Google:xxx / 類股-xxx)是內部查詢別名,不是發布者身分——
    「Google:SEC」這類別名不得升 A(四審 P1-2);別名整段跳過,只看
    source_name 與標題尾綴。"""
    source = str(item.get("source") or "")
    is_aggregator = source.lower().startswith("google:") or source.startswith("類股-")
    return ((_grade_from_text(source) if not is_aggregator else "")
            or _grade_from_text(item.get("source_name"))
            or _grade_from_text(item.get("title"), allow_a=False)
            or "C")


def _news_keep_score(item: dict) -> tuple[int, int]:
    """同事件去重時優先保留較可信、內容較完整的版本。"""
    grade_score = {"A": 3, "B": 2, "C": 1}.get(_news_source_grade(item), 0)
    content_len = len(item.get("summary") or "") + len(item.get("fulltext") or "")
    return grade_score, content_len


def _credibility_tag(item: dict) -> str:
    """G6:可信度確定性標記。獨立來源數(dedup 累計的 merged_n)> 1 或含官方來源時,
    回「〔獨立來源 N・含官方來源〕」供 prompt 顯示;否則回 ""。純確定性,不進計分。
    official 欄位由 dedup_news 累計;單筆未去重者退回以來源分級(A=官方)即時判定。"""
    n = item.get("merged_n", 1)
    n = n if isinstance(n, int) and n > 0 else 1
    official = item.get("official")
    if not isinstance(official, bool):
        official = _news_source_grade(item) == "A"
    bits = []
    if n > 1:
        bits.append(f"獨立來源 {n}")
    if official:
        bits.append("含官方來源")
    return f"〔{'・'.join(bits)}〕" if bits else ""


def dedup_news(news: list[dict], similarity: float = 0.85) -> list[dict]:
    """
    去除重複 / 近似重複的新聞（同一事件常被多個 RSS 來源重貼）。
    規則：標題正規化（去空白、去標點、小寫）後完全相同 → 重複；
         或與已保留標題的 difflib 相似度 > similarity → 重複。
    重複時保留來源品質較高、摘要較完整者。
    """
    import difflib
    import re as _re

    def _norm(t: str) -> str:
        t = (t or "").lower().strip()
        t = _re.sub(r"[\s　]+", "", t)
        # 只保留中英數，去掉所有標點符號
        t = _re.sub(r"[^\w一-鿿]", "", t)
        return t

    def _pub_key(item: dict) -> str:
        """發布者身分(用於算「獨立來源數」)。優先用 source_name(真正媒體,如「鉅亨」),
        其次 source(常是聚合器代號)。目的:同一媒體經多條查詢路徑(類股 feed vs 個股 Google)
        重貼,source 不同但 source_name 相同 → 視為同一來源、不灌水 merged_n(Codex review)。"""
        return _norm(str(item.get("source_name") or item.get("source") or ""))

    def _pub_set(item: dict) -> set:
        """該項已知的發布者集合。dedup_news 會被 pipeline 多次呼叫(逐步併入新聞群組),
        故把集合持久化在項目的 _pub_keys 上;後續呼叫從中還原,避免 merged_n 被重置縮水
        (Codex review 第二輪)。無 _pub_keys 者退回單一發布者。"""
        existing = item.get("_pub_keys")
        if isinstance(existing, (list, set)) and existing:
            return {str(x) for x in existing}
        return {_pub_key(item)}

    kept: list[dict] = []
    kept_norms: list[str] = []
    # 每個保留項「已合併的發布者身分集合」,與 kept/kept_norms 同步索引;merged_n = 其基數。
    kept_pubs: list[set] = []
    dropped = 0
    for n in news:
        nt = _norm(n.get("title", ""))
        if not nt:
            # 無標題者不參與比對,但三個平行陣列仍同步 append(空 norm 永不匹配),
            # 維持 kept / kept_norms / kept_pubs 索引一致(否則 dup_index 會錯位)。
            kept.append(n)
            kept_norms.append("")
            kept_pubs.append(_pub_set(n))
            continue
        dup_index = None
        for index, kn in enumerate(kept_norms):
            if not kn:
                continue
            if nt == kn:
                dup_index = index
                break
            # 近似比對：兩者較短長度 >= 8 才比，避免短標題誤殺
            if (min(len(nt), len(kn)) >= 8
                    and difflib.SequenceMatcher(None, nt, kn).ratio() > similarity):
                dup_index = index
                break
        if dup_index is not None:
            # 不論保留哪一版,都把 company_label 補到留下來的那筆,
            # 避免個股新聞因去重而失去標籤、從「科技板塊脈動」消失(rank 5)。
            # world_cat 同理:同一事件常同時出現在一般來源(如 Google-地緣)與世界來源,
            # 若被一般來源那版吃掉、世界標記跟著消失,「世界大事速覽」取材段就漏事件
            # (Codex review)。
            label = n.get("company_label") or kept[dup_index].get("company_label")
            wcat = n.get("world_cat") or kept[dup_index].get("world_cat")
            # 混源重複(一版來自市場來源、一版來自世界來源)→ 標 world_and_market:
            # 該事件同屬兩個版面,市場配額桶與世界取材段都要收,不可因帶 world_cat
            # 就被市場桶排除(Codex review 第二輪:否則跨源大事件從市場桶消失)。
            mixed = bool(n.get("world_cat")) != bool(kept[dup_index].get("world_cat"))
            # G6 可信度確定性欄位:merged_n=去重後「不同發布者」數(非則數),含官方=任一版 grade A。
            # 集合存 kept_pubs(平行陣列,替換保留版本也不遺失累計)並持久化到 _pub_keys
            # (dedup 會被多次呼叫,持久化才不會在下一輪縮水);官方旗標取 OR。
            kept_pubs[dup_index] |= _pub_set(n)
            combined_official = (bool(kept[dup_index].get("official"))
                                 or bool(n.get("official"))
                                 or _news_source_grade(kept[dup_index]) == "A"
                                 or _news_source_grade(n) == "A")
            if _news_keep_score(n) > _news_keep_score(kept[dup_index]):
                kept[dup_index] = n
                kept_norms[dup_index] = nt
            kept[dup_index]["merged_n"] = len(kept_pubs[dup_index])
            kept[dup_index]["_pub_keys"] = sorted(kept_pubs[dup_index])
            kept[dup_index]["official"] = combined_official
            if label and not kept[dup_index].get("company_label"):
                kept[dup_index]["company_label"] = label
            if wcat and not kept[dup_index].get("world_cat"):
                kept[dup_index]["world_cat"] = wcat
            if mixed:
                kept[dup_index]["world_and_market"] = True
            dropped += 1
            continue
        kept.append(n)
        kept_norms.append(nt)
        kept_pubs.append(_pub_set(n))
    print(f"[news] 去重：{len(news)} → {len(kept)} 則（移除 {dropped} 則重複）")
    return kept


FED_OFFICIALS = [
    "Powell", "Williams", "Jefferson", "Bowman", "Cook", "Kugler", "Waller",
    "Barr", "Brainard", "Daly", "Bostic", "Mester", "Kashkari", "Goolsbee",
    "Schmid", "Logan", "Musalem", "Hammack", "鮑爾", "鮑威爾",
    "Warsh",   # 新任聯準會主席
]


FED_EVENTS = [
    "FOMC", "聯準會", "Federal Reserve", "Fed minutes", "Fed Funds",
    "rate decision", "升息", "降息", "利率決議", "點陣圖", "dot plot",
    "Jackson Hole",
]


ECON_DATA = [
    "CPI", "PPI", "PCE", "核心通膨", "core inflation",
    "Nonfarm Payrolls", "非農", "就業數據", "失業率", "Initial Jobless Claims",
    "ADP", "JOLTS",
    "GDP", "ISM", "PMI", "零售銷售", "Retail Sales", "Consumer Confidence",
    "Durable Goods", "Industrial Production",
]


GEOPOLITICAL = [
    "出口管制", "晶片禁令", "對中制裁", "Entity List", "EAR",
    "川習會", "Trump Xi", "貿易戰", "tariff", "關稅",
    "台海", "Taiwan Strait", "封鎖", "demilitarized",
    "伊朗", "以色列", "烏克蘭", "戰爭", "war",
    # 中國政策/對台 深度
    "中共", "中國商務部", "China MOFCOM", "中國國台辦",
    "解放軍", "PLA", "海警", "軍演", "drill",
    "稀土", "rare earth", "中國新晶片", "華為", "SMIC", "Huawei",
    "禁止出口", "ban", "黑名單", "blacklist",
    "晶片補貼", "CHIPS Act",
    "央行降準", "RRR", "China stimulus", "人民幣",
]


GEOPOLITICAL_CRITICAL = [
    "川習會", "川習", "Trump Xi", "拜習", "習拜",
    "台海", "Taiwan Strait", "對台", "台灣問題", "一個中國", "侵台", "封島",
    "軍演", "對台軍售", "解放軍", "PLA", "封鎖", "blockade",
    "出口管制", "晶片禁令", "Entity List", "對中制裁", "EAR",
    "戰爭", "war",
]


TW_POLICY = [
    "金管會", "央行", "升息", "降息", "外資匯入", "外匯存底",
    "產創條例", "新青安", "科專",
    "TSMC", "台積電", "艾司摩爾", "ASML",
]


def _matches_any(text: str, keywords: list[str]) -> Optional[str]:
    """文本是否包含任一關鍵字，回傳命中的那個。"""
    if not text:
        return None
    import re as _re
    lower = text.lower()
    for kw in keywords:
        needle = kw.lower()
        # 英文關鍵字用 word boundary，避免 war 誤中 Warren / software / hardware。
        # 中文與混合中文詞維持 substring，才能命中「台海軍演」等自然語句。
        if _re.fullmatch(r"[a-z0-9][a-z0-9 ._/-]*", needle):
            pattern = rf"(?<![a-z0-9]){_re.escape(needle)}(?![a-z0-9])"
            matched = _re.search(pattern, lower) is not None
        else:
            matched = needle in lower
        if matched:
            return kw
    return None


def _strip_html(html: str) -> str:
    """簡單去 HTML tag，不依賴 BeautifulSoup。"""
    import re as _re
    # 移除 <script>...</script> 與 <style>...</style>
    html = _re.sub(r"<script[^>]*>.*?</script>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
    html = _re.sub(r"<style[^>]*>.*?</style>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
    # 移除其他 tag
    html = _re.sub(r"<[^>]+>", " ", html)
    # HTML entities
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    html = html.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    # 壓縮空白
    html = _re.sub(r"\s+", " ", html).strip()
    return html


def classify_news_importance(news: list[dict]) -> list[dict]:
    """
    對每則新聞自動分類與評重要性：
      importance: "critical" (★★★) / "high" (★★) / "normal"
      category:   "fed" / "econ_data" / "geo" / "tw_policy" / "general"

    Critical 事件會在 prompt 中被特別標記，並可選擇抓全文（Task A）。
    """
    for n in news:
        text = f"{n.get('title','')} {n.get('summary','')}"
        n["source_grade"] = _news_source_grade(n)

        fed_hit = _matches_any(text, FED_OFFICIALS) or _matches_any(text, FED_EVENTS)
        econ_hit = _matches_any(text, ECON_DATA)
        geo_crit_hit = _matches_any(text, GEOPOLITICAL_CRITICAL)
        geo_hit = geo_crit_hit or _matches_any(text, GEOPOLITICAL)
        tw_hit = _matches_any(text, TW_POLICY)

        # 評分邏輯：Fed/數據/重大地緣 → critical；一般地緣/台灣政策 → high
        if fed_hit and econ_hit:
            # Fed + 經濟數據同時出現 = 政策轉向訊號
            n["importance"] = "critical"
            n["category"] = "fed_econ"
            n["keyword"] = f"{fed_hit} + {econ_hit}"
        elif fed_hit:
            n["importance"] = "critical"
            n["category"] = "fed"
            n["keyword"] = fed_hit
        elif econ_hit:
            n["importance"] = "critical"
            n["category"] = "econ_data"
            n["keyword"] = econ_hit
        elif geo_crit_hit:
            # 直接牽動台股的重大地緣事件（川習會、台海、出口管制…）→ critical
            n["importance"] = "critical"
            n["category"] = "geo_critical"
            n["keyword"] = geo_crit_hit
        elif geo_hit:
            n["importance"] = "high"
            n["category"] = "geo"
            n["keyword"] = geo_hit
        elif tw_hit:
            n["importance"] = "high"
            n["category"] = "tw_policy"
            n["keyword"] = tw_hit
        elif n.get("company_label") and (
                _matches_any(text, NEWS_POSITIVE_TERMS)
                or _matches_any(text, NEWS_NEGATIVE_TERMS)):
            # 重點公司 + 具體催化(訂單/上修/財報/砍單/出口管制…)→ 升級為 high。
            # 讓它抓全文並進入高權重區,避免「科技板塊脈動」退化成只報股價+B級低信心(rank 7)。
            n["importance"] = "high"
            n["category"] = "company_catalyst"
            n["keyword"] = (_matches_any(text, NEWS_POSITIVE_TERMS)
                            or _matches_any(text, NEWS_NEGATIVE_TERMS))
        else:
            n["importance"] = "normal"
            n["category"] = "general"
            n["keyword"] = ""

    # 統計
    crit = sum(1 for n in news if n.get("importance") == "critical")
    high = sum(1 for n in news if n.get("importance") == "high")
    print(f"[news] 重要性分類完成：critical={crit}, high={high}, normal={len(news)-crit-high}")
    return news


def _is_low_value_tech_headline(n: dict) -> bool:
    """純分析師喊價或純籌碼流向、且不含具體催化的非 A 級新聞 → 視為科技脈動雜訊。
    僅用於過濾「重點公司新聞」餵 LLM 的取材(這類內容股價表/法人表已涵蓋),
    不更動 importance/ranking 等任何計分。"""
    text = f"{n.get('title', '')} {n.get('summary', '')}"
    grade = n.get("source_grade") or _news_source_grade(n)
    if grade == "A":                       # 官方來源(SEC/MOPS/TWSE…)一律保留
        return False
    if _matches_any(text, TECH_GATE_CATALYST):
        return False
    return bool(_matches_any(text, TECH_NEWS_ANALYST_NOISE)
                or _matches_any(text, TECH_NEWS_CHIPFLOW_NOISE))
