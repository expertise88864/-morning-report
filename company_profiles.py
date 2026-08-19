# -*- coding: utf-8 -*-
"""**一句話說清楚這家公司在做什麼**(2026-08-18 使用者定案)。

使用者要回舊版信裡那種小標題:

    台積電（2330,全球最大晶圓代工廠,先進製程 N3/N2 與 CoWoS 封裝主導
    AI 晶片供給）:…
    Microsoft（MSFT,全球第二大雲端服務商 Azure 與 AI 助理 Copilot
    營運者）:…

台股那一半**已經有資料**:`tw_universe` 的 `desc` 來自
`morning_report.TW0050_CONSTITUENTS`(五十檔手寫的業務簡介),
渲染層只要把開頭重複的公司名去掉就好(見 `analysis_render_depth._blurb`)。

**外國個股沒有這種資料源**,所以這裡是一張**宣告**,與 `sector_map.EDGES`、
`instrument_registry._KNOWN` 同一種東西:每一條都是人寫的,不是從新聞
猜出來的。少一條只是那家公司的小標題少一句側寫(仍會寫出名字與代號);
寫錯一條會讓讀者以為這家公司在做別的生意 —— 所以寧可短,不收不確定的。

收錄範圍與 `instrument_registry` 一致:這份報告真的會談到的外國個股。
"""
from __future__ import annotations

#: `代號 → (中文/慣用顯示名, 一句側寫)`。顯示名留空就直接用代號。
PROFILES: dict = {
    # ── AI / 半導體
    "NVDA": ("輝達", "AI 加速運算龍頭,GPU 與 CUDA 生態主導資料中心訓練與推論"),
    "AMD": ("超微", "x86 CPU 與資料中心 GPU 的第二供應商,MI 系列對標 NVIDIA"),
    "INTC": ("英特爾", "x86 CPU 老牌大廠,同時經營自有晶圓代工 IFS"),
    "MU": ("美光", "全球前三大記憶體廠,DRAM/NAND 與 HBM 供應商"),
    "AVGO": ("博通", "客製化 ASIC 與網通晶片龍頭,雲端自研晶片主要設計夥伴"),
    "QCOM": ("高通", "全球最大手機 SoC 與 5G 基頻晶片設計商"),
    "TXN": ("德州儀器", "類比與嵌入式晶片龍頭,終端橫跨工控、車用與消費"),
    "ADI": ("亞德諾", "高效能類比與訊號鏈晶片廠,工控與車用比重高"),
    "MRVL": ("邁威爾", "資料中心光通訊與客製化運算晶片設計商"),
    "AMAT": ("應用材料", "全球最大半導體製程設備商"),
    "LRCX": ("科林研發", "蝕刻與薄膜沉積設備龍頭"),
    "KLAC": ("科磊", "製程檢測與量測設備龍頭"),
    "ASML": ("艾司摩爾", "全球唯一 EUV 微影設備供應商,先進製程的咽喉"),
    "ARM": ("安謀", "手機與邊緣運算的 CPU 架構授權商"),
    "SMCI": ("美超微", "AI 伺服器整機與液冷機櫃供應商"),
    "DELL": ("戴爾", "企業伺服器與 AI 機櫃整合商"),
    "HPE": ("慧與", "企業級伺服器與高效能運算供應商"),
    "WDC": ("威騰", "硬碟與儲存解決方案廠"),
    "STX": ("希捷", "近線硬碟龍頭,受惠雲端儲存擴建"),
    "GFS": ("格芯", "成熟製程晶圓代工廠"),
    "UMC": ("聯電 ADR", "台灣第二大晶圓代工廠的美股存託憑證"),
    "ASX": ("日月光 ADR", "全球最大封裝測試廠的美股存託憑證"),
    "TSM": ("台積電 ADR", "全球最大晶圓代工廠的美股存託憑證"),
    "HIMX": ("奇景光電", "顯示驅動 IC 與微投影光學廠"),
    "SK海力士": ("SK 海力士", "全球第二大記憶體廠,HBM 為 AI GPU 關鍵供應商"),
    "三星電子": ("三星電子", "全球最大記憶體廠,同時經營晶圓代工與終端裝置"),
    # ── 雲端 / 軟體 / 平台
    "MSFT": ("Microsoft", "全球第二大雲端服務商 Azure 與 AI 助理 Copilot 營運者"),
    "GOOGL": ("Alphabet", "Google 搜尋與 GCP 雲端營運者,自研 TPU 加速器"),
    "GOOG": ("Alphabet", "Google 搜尋與 GCP 雲端營運者,自研 TPU 加速器"),
    "AMZN": ("Amazon", "全球最大雲端服務商 AWS 與電商平台營運者"),
    "META": ("Meta", "全球最大社群媒體集團,開源 Llama 模型開發者"),
    "AAPL": ("Apple", "全球最大消費電子品牌,自研 M/A 系列晶片"),
    "NFLX": ("Netflix", "全球最大串流影音平台"),
    "ORCL": ("Oracle", "企業資料庫龍頭,近年轉進 AI 雲端算力租賃"),
    "CRM": ("Salesforce", "全球最大 CRM 軟體服務商"),
    "NOW": ("ServiceNow", "企業流程自動化 SaaS 平台"),
    "ADBE": ("Adobe", "創意與文件軟體龍頭,生成式 AI 導入內容工作流"),
    "PLTR": ("Palantir", "政府與企業級資料分析平台"),
    "SNOW": ("Snowflake", "雲端資料倉儲平台"),
    "NET": ("Cloudflare", "全球邊緣網路與資安服務商"),
    "CSCO": ("Cisco", "全球最大網通設備商,資料中心交換器與資安"),
    # ── 其他 NASDAQ-100 權值(**不是科技股**,見 `industry_class.NON_TECH_FOREIGN`)
    "COST": ("Costco", "全球最大會員制倉儲量販"),
    "TMUS": ("T-Mobile", "美國前三大無線電信業者"),
    # ── 電動車 / 加密資產
    "TSLA": ("Tesla", "電動車與儲能龍頭,自研 FSD 與 Dojo 訓練叢集"),
    "COIN": ("Coinbase", "美國最大加密資產交易所"),
    "MSTR": ("Strategy", "以公司資產負債表大量持有比特幣的軟體公司"),
}


def display_name(ticker) -> str:
    """慣用顯示名(查不到就回代號本身)。"""
    row = PROFILES.get(str(ticker or "").strip())
    return (row[0] if row and row[0] else str(ticker or "").strip())


def profile_of(ticker) -> str:
    """一句側寫;**沒有宣告就回空字串**,呼叫端只寫名字與代號,不編造。"""
    row = PROFILES.get(str(ticker or "").strip())
    return row[1] if row else ""


#: **每天一定要去問有沒有新聞的兩組公司**(2026-08-18 使用者定案)。
#: 2026-08-19 從 morning_report 搬到這裡:渲染層要用 NDX 名單推導
#: 「00662 最相關」的標記,而渲染層不能 import 主模組(循環)。
#: 判準只有一份 —— morning_report 以別名引用。
TW0050_TOP10_LABELS: tuple = ("2330", "2317", "2454", "2308", "2382",
                              "2891", "2881", "2882", "3711", "2303")
#: NASDAQ-100 權重前段班(2026-08-18 當時的前段;權重會變,這是宣告)。
NASDAQ_TOP15_LABELS: tuple = ("NVDA", "MSFT", "AAPL", "AMZN", "AVGO", "META",
                              "GOOGL", "TSLA", "NFLX", "COST", "PLTR", "CSCO",
                              "AMD", "TMUS", "ADBE")
