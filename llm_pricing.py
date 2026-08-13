# -*- coding: utf-8 -*-
"""**單價與成本估算**(從 `llm_telemetry` 抽出,第十三輪 P1-1)。

計價自成一塊:它有自己的 schema 版本、自己的出處、自己的失效方式
(「查不到單價」與「量不到 usage」是兩件事),而且**這個數字會直接被拿去
做「換不換模型」的決定** —— 錯的成本數字比沒有成本數字更糟。

第十三輪 P1-1 的外審宣稱 Luna/Terra 單價各低估 5 倍與 20%。
**2026-08-02 逐頁查證後不成立**:官方頁面兩處各自確認
luna `$0.2 / $0.02 / $1.2`、terra `$2.00 / $0.20 / $12.00`,與本表一致。
但同一份文件證實它的另一半:`>272K input tokens` 會讓整個 request 換一段
費率,而先前完全沒有這一層 —— 那部分已在本批補上。
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------- 成本估算

#: 每 100 萬 token 的官方牌價(USD)。**只收錄有出處的。**
#:
#: 查不到單價的一律不估,而不是拿別處的數字近似 —— 這個數字會直接被拿來做
#: 「換不換模型」的決定,而我在 2026-08-01 已經用 OpenRouter 的價格估過一次
#: GPT-5.6,結果比官方低 2.5 倍。錯的成本數字比沒有成本數字更糟。
#:
#: 出處:OpenAI pricing 頁與 DeepSeek pricing 頁,兩者皆於 2026-08-01 逐頁查證。
#: **DeepSeek 原本刻意留空**(當時我手上沒有官方單價,寧可回報「未收錄」);
#: 十天 Luna 對比實驗要比成本,「未收錄」在那個情境等於整半邊沒有數字,
#: 所以去查了官方頁面後補上 —— 而不是拿第三方轉載的數字近似。
#: DeepSeek 的欄位對照(官方 pricing 頁,2026-08-01 查證):
#:   cache miss input → `input`;cache hit input → `cached_input`;output → `output`
#: **DeepSeek 沒有 cache write 費用** —— 那是 GPT-5.6+ 才有的 1.25× 收費。
#: 這裡不需要特判:DeepSeek 的回應根本不含 `cache_write_tokens`,
#: 所以 `cache_write_tokens_of` 回 None,乘數不會被套用。由測試盯住。
MODEL_PRICING = {
    "gpt-5.6-sol": {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gpt-5.6-terra": {"input": 2.00, "cached_input": 0.20, "output": 12.00},
    "gpt-5.6-luna": {"input": 0.20, "cached_input": 0.02, "output": 1.20},
    # DeepSeek 官方 pricing 頁(2026-08-01 查證)。**先前刻意留空**,理由是
    # 「我手上沒有官方單價,寧可回報未收錄」—— 現在查到了,所以補上。
    # 十天實驗要比成本,而「未收錄」在那個情境等於整半邊沒有數字。
    # ⚠ DeepSeek 已公告 2026-08-16 16:00 UTC 起改峰谷計價(離峰半價:
    # pro 離峰 $0.66/$0.022/$1.98、尖峰 $1.32/$0.044/$3.96)——
    # 生效後本表要換 schema(峰谷是**時間函數**,單一費率表不成立)。
    # 本報排程 06:00 台北 = 22:00 UTC,屬離峰。
    "deepseek-v4-pro": {"input": 0.435, "cached_input": 0.003625, "output": 0.87},
    "deepseek-v4-flash": {"input": 0.14, "cached_input": 0.0028, "output": 0.28},
}

#: **DeepSeek 的峰谷計價**(官方頁 2026-08-14 查證,中英文兩版一致)。
#:
#: 北京時間 2026-08-17 00:00(= UTC 2026-08-16 16:00)起生效,
#: 尖峰時段 **北京 09:00–12:00 與 14:00–18:00**,其餘為離峰。
#:
#: ⚠ **這不是「離峰打對折」而已 —— 離峰價本身就比現價貴**:
#: pro 輸入 $0.435→$0.66(1.5×)、輸出 $0.87→$1.98(2.3×)、
#: 快取命中 $0.003625→$0.022(6.1×)。把它讀成「調價後有便宜時段」
#: 會低估帳單。
#:
#: 判準用**北京時間的小時**比對(官方就是這樣寫的)—— 換算成 UTC 再比
#: 多一層可能出錯的翻譯。半開區間 `[9,12)`/`[14,18)`:邊界的讀法是假設,
#: 而本報排程在北京 06:00,離兩個邊界都很遠。
DEEPSEEK_PEAK_PRICING = {
    # (UTC) 生效時刻 —— 之前一律用上面的單一費率表
    "effective_from_utc": (2026, 8, 16, 16, 0),
    "peak_hours_beijing": ((9, 12), (14, 18)),
    "rates": {
        "deepseek-v4-pro": {
            "offpeak": {"input": 0.66, "cached_input": 0.022, "output": 1.98},
            "peak": {"input": 1.32, "cached_input": 0.044, "output": 3.96},
        },
        "deepseek-v4-flash": {
            "offpeak": {"input": 0.22, "cached_input": 0.007, "output": 0.66},
            "peak": {"input": 0.44, "cached_input": 0.014, "output": 1.32},
        },
    },
}


def deepseek_window(at) -> str:
    """這個時刻落在 DeepSeek 的哪個計價時段:`peak` / `offpeak`。

    `at` 是 timezone-aware 的 UTC 時間。北京 = UTC+8(無日光節約),
    所以直接加八小時再比小時數 —— 與官方公告的寫法逐字對應。
    """
    import datetime as _dt
    beijing = at.astimezone(_dt.timezone.utc) + _dt.timedelta(hours=8)
    h = beijing.hour
    for lo, hi in DEEPSEEK_PEAK_PRICING["peak_hours_beijing"]:
        if lo <= h < hi:
            return "peak"
    return "offpeak"


#: 寫入快取的計價倍率。官方明文:
#: "Cache writes are billed at 1.25x the uncached input token rate."
#: (developers.openai.com,2026-08-01 逐頁查證)
CACHE_WRITE_MULTIPLIER = 1.25

#: 價格表的版本與出處。第十輪外審宣稱 Luna 是 $1.00/$6.00、Terra 是 $2.50/$15,
#: 據此推得「低估約六倍」—— 我逐頁查證三個 model 頁面後**駁回**該推論:
#: 官方為 Sol $5/$0.5/$30、Terra $2/$0.2/$12、Luna $0.2/$0.02/$1.2,與本表一致。
#: 但同一條裡有兩件是對的,而且本表原本都沒有:cached input 有**獨立費率**,
#: 而 cache write 以 1.25 倍計。實測重算 2026-08-01 10:11 那班:
#: 記錄 $0.027369 → 正確約 $0.0325,**低估 19%**(不是 594%)。
#: **每個模型各自的出處。** 單一字串在收錄兩家之後就是錯的宣稱:
#: manifest 會說 DeepSeek 的價格來自 openai.com。這條被 r1 外審在
#: 別的地方抓過同型問題(「宣稱要對得上實作」)。
PRICING_SOURCE_BY_PREFIX = {
    "gpt-": "developers.openai.com/api/docs/pricing",
    "deepseek-": "api-docs.deepseek.com/quick_start/pricing",
}
#: **超長輸入的另一段費率**(第十三輪 P1-1,2026-08-02 官方頁查證)。
#: `>272K input tokens` 讓**整個 request** 換價:in ×2、cached ×2、out ×1.5。
#: 三個 GPT-5.6 的 long-context 牌價都符合這組倍率(luna 0.2→0.4/1.2→1.8、
#: terra 2→4/12→18、sol 5→10/30→45),所以記倍率不再抄一張表 ——
#: 兩張表就有兩張要同步。DeepSeek 沒有這層,不收錄就不會被套用。
LONG_CONTEXT_TIERS = {"gpt-": {"threshold": 272_000,
                               "input": 2.0, "cached_input": 2.0,
                               "output": 1.5}}
PRICING_SOURCE = "developers.openai.com + api-docs.deepseek.com"
#: 2026-08-14 重查:峰谷費率(2026-08-17 生效)取自同兩頁的中英文版,
#: 兩版數字一致(¥/$ 換算約 6.8-7.1)。
PRICING_AS_OF = "2026-08-14"
#: schema 4:加入 `>272K` long-context 費率層,每筆多帶 `pricing_tier`
#: 與生效費率 —— 舊 schema 的資料**不可與新的相加**。
#: schema 5(2026-08-14):DeepSeek 改**峰谷計價**(2026-08-17 生效)——
#: 單價從此是時間的函數,而且**離峰價本身就比舊價貴**(pro 輸出 2.3×)。
#: 舊 schema 的成本資料不可與新的相加。
PRICING_SCHEMA = 5


def pricing_source_for(model: str) -> str:
    """這個模型的價格是**從哪一頁查來的**。查不到就明說,不要編一個。"""
    m = (model or "").strip().lower()
    for prefix, src in PRICING_SOURCE_BY_PREFIX.items():
        if m.startswith(prefix):
            return src
    return "未收錄"


def cache_write_tokens_of(usage: Optional[dict]) -> Optional[int]:
    """寫入快取的 token 數(批#100)。

    2026-08-01 實測回應:`prompt_tokens_details: {cached_tokens: 0,
    cache_write_tokens: 93191}` —— 我原本只記 `cached_tokens`(當天是 0),
    於是「快取」這件事在 manifest 裡看起來完全沒發生,而實際上有 93,191 個
    token 被寫進快取。寫入通常另有費率,**沒收錄費率就不能假裝成本是精確的**。
    """
    det = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    if isinstance(det, dict) and isinstance(det.get("cache_write_tokens"), int):
        return det["cache_write_tokens"]
    return None


def cached_tokens_of(usage: Optional[dict]) -> Optional[int]:
    """快取命中的輸入 token 數。兩家的欄位名不同,**擇一不相加**。

    OpenAI:`prompt_tokens_details.cached_tokens`;
    DeepSeek:`prompt_cache_hit_tokens`。
    """
    if not isinstance(usage, dict):
        return None
    det = usage.get("prompt_tokens_details")
    if isinstance(det, dict) and isinstance(det.get("cached_tokens"), int):
        return det["cached_tokens"]
    hit = usage.get("prompt_cache_hit_tokens")
    return hit if isinstance(hit, int) else None


def price_of(model: str, at=None) -> Optional[dict]:
    """查單價。先精確比對,再前綴比對(容納 `-2026-02-16` 這類日期後綴)。

    `at`(timezone-aware UTC)給定且落在 DeepSeek 峰谷計價生效之後時,
    回**那個時段**的費率 —— 單價從此是**時間的函數**,不再是一張表。
    """
    m = (model or "").strip().lower()
    peaked = _deepseek_price_at(m, at)
    if peaked:
        return peaked
    if m in MODEL_PRICING:
        return MODEL_PRICING[m]
    for name, price in MODEL_PRICING.items():
        if m.startswith(name):
            return price
    return None


def _deepseek_price_at(model: str, at) -> Optional[dict]:
    """峰谷生效後的 DeepSeek 費率;不適用(舊時刻/沒給時間/非 DeepSeek)回 None。"""
    if at is None:
        return None
    import datetime as _dt
    eff = _dt.datetime(*DEEPSEEK_PEAK_PRICING["effective_from_utc"],
                       tzinfo=_dt.timezone.utc)
    try:
        if at.astimezone(_dt.timezone.utc) < eff:
            return None
    except (AttributeError, TypeError, ValueError):
        return None                 # 時間形狀不對 → 退回單一費率表,不猜
    rates = DEEPSEEK_PEAK_PRICING["rates"]
    for name, byw in rates.items():
        if model == name or model.startswith(name):
            return byw[deepseek_window(at)]
    return None


def long_context_tier(model: str, prompt_tokens: int) -> Optional[dict]:
    """這次呼叫要不要套用長 context 費率。不適用回 `None`。

    判準是輸入 token 數,超過就套用到**整個 request**(不是只有超出那段)
    —— 官方原文:"…priced at 2x input and 1.5x output for the full request."
    """
    m = (model or "").strip().lower()
    for prefix, tier in LONG_CONTEXT_TIERS.items():
        if m.startswith(prefix) and int(prompt_tokens or 0) > tier["threshold"]:
            return tier
    return None


def estimate_cost(model: str, usage: Optional[dict], at=None) -> dict:
    """估這一次呼叫的成本。**估不出來就說估不出來。**

    回 `{"usd": float|None, "basis": str}`。`basis` 一定要寫,因為這個數字
    帶著兩個會被忘記的假設:(a) 推理 token 以 output 計價(這是推理模型的
    定價方式,而它通常是帳單的主要來源);(b) 快取命中的輸入**以全價計** ——
    折扣比例我手上沒有官方數字,所以這是**上界**而不是實際金額。
    """
    # **單價是時間的函數**(DeepSeek 峰谷,2026-08-17 起):沒給時刻就用
    # 現在 —— 這次呼叫剛剛發生,那就是它的計價時刻。測試傳 `at` 取得確定性。
    if at is None:
        import datetime as _dt
        at = _dt.datetime.now(_dt.timezone.utc)
    price = price_of(model, at)
    if not price:
        return {"usd": None, "basis": f"未收錄 {model or '(未知模型)'} 的單價,不估"}
    if not isinstance(usage, dict):
        return {"usd": None, "basis": "沒有 usage,不估"}
    pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if not (isinstance(pt, int) and isinstance(ct, int)):
        return {"usd": None, "basis": "usage 缺 token 數,不估"}
    # 輸入分三種費率(第十輪 P1-1):**快取命中有獨立費率、寫入以 1.25 倍計。**
    # 原本一律用 uncached 費率、且完全不計 cache write —— 前者高估、後者低估,
    # 而 2026-08-01 的實際情形是後者主導(92,259 tok 全部是 cache write)。
    cached = cached_tokens_of(usage) or 0
    written = cache_write_tokens_of(usage) or 0
    plain = max(0, pt - cached - written)
    # 長 context 那一段:倍率套在**整個 request**,不是只有超出的部分。
    tier = long_context_tier(model, pt)
    rate_in = price["input"] * (tier["input"] if tier else 1.0)
    rate_cached = (price.get("cached_input", price["input"])
                   * (tier["cached_input"] if tier else 1.0))
    rate_out = price["output"] * (tier["output"] if tier else 1.0)
    input_cost = (plain * rate_in + cached * rate_cached
                  + written * rate_in * CACHE_WRITE_MULTIPLIER)
    usd = (input_cost + ct * rate_out) / 1_000_000
    bits = [f"官方牌價({PRICING_SOURCE} {PRICING_AS_OF});推理以 output 計價"]
    # **時段要記下來**:同一個模型同一天可以有兩種單價,只記總額的話
    # 事後對不上帳單時分不出是「跑在尖峰」還是「漏算呼叫」。
    _win = (deepseek_window(at)
            if _deepseek_price_at((model or "").strip().lower(), at) else "")
    if _win:
        bits.append(f"DeepSeek 峰谷計價:{_win}"
                    "(尖峰為北京 09-12、14-18)")
    if tier:
        bits.append(f"輸入 {pt} tok > {tier['threshold']},整筆改用長 context "
                    f"費率(input ×{tier['input']}、output ×{tier['output']})")
    if cached:
        bits.append(f"快取命中 {cached} tok 以 ${price.get('cached_input')}/M 計")
    if written:
        bits.append(f"寫入快取 {written} tok 以 {CACHE_WRITE_MULTIPLIER}× 輸入費率計")
    # **生效費率要記下來** —— 只記總額的話,事後對不上帳單時分不出是
    # 「用錯費率」還是「漏了呼叫」。
    return {"usd": round(usd, 6), "basis": ";".join(bits),
            "pricing_tier": ("long_context" if tier
                             else (f"deepseek_{_win}" if _win else "standard")),
            "pricing_schema": PRICING_SCHEMA,
            "effective_input_rate": round(rate_in, 6),
            "effective_cached_rate": round(rate_cached, 6),
            "effective_output_rate": round(rate_out, 6),
            "pricing_source": pricing_source_for(model)}


#: 角色槽位(`attempts` 是失敗紀錄的清單,不是角色)。
