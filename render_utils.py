"""渲染格式 helpers(A5-Step2 由 morning_report 抽出)。
Markdown→HTML、分析 HTML 上色、立場包裝、總經單行格式;皆純函式、無網路/狀態、
不依賴 morning_report 其它符號;morning_report 以 re-export 保相容,既有測試零修改。"""
from __future__ import annotations

import html as _h
import re
from typing import Optional

# 世足小組賽完賽判定用:2026 世界盃為 12 組 × 4 隊。ESPN 回傳不完整時保守視為「進行中」。
_WC_EXPECTED_GROUPS = 12
_WC_TEAMS_PER_GROUP = 4

# MLB 隊名縮寫 → 中文(城市+隊名;使用者要求 2026-07-15)。涵蓋 ESPN 30 隊縮寫。
_MLB_TEAM_ZH = {
    "NYY": "紐約洋基", "BOS": "波士頓紅襪", "TB": "坦帕灣光芒", "TOR": "多倫多藍鳥",
    "BAL": "巴爾的摩金鶯", "CLE": "克里夫蘭守護者", "MIN": "明尼蘇達雙城",
    "CHW": "芝加哥白襪", "DET": "底特律老虎", "KC": "堪薩斯皇家",
    "HOU": "休士頓太空人", "SEA": "西雅圖水手", "TEX": "德州遊騎兵",
    "LAA": "洛杉磯天使", "ATH": "運動家", "OAK": "奧克蘭運動家",
    "LAD": "洛杉磯道奇", "SF": "舊金山巨人", "SD": "聖地牙哥教士",
    "ARI": "亞利桑那響尾蛇", "COL": "科羅拉多落磯", "MIL": "密爾瓦基釀酒人",
    "CHC": "芝加哥小熊", "STL": "聖路易紅雀", "CIN": "辛辛那提紅人",
    "PIT": "匹茲堡海盜", "ATL": "亞特蘭大勇士", "NYM": "紐約大都會",
    "PHI": "費城費城人", "MIA": "邁阿密馬林魚", "WSH": "華盛頓國民",
}


def _mlb_zh(text: str) -> str:
    """把字串中的 MLB 隊名縮寫換成中文隊名(整詞比對,未知縮寫原樣保留)。"""
    return re.sub(r"\b([A-Z]{2,3})\b",
                  lambda m: _MLB_TEAM_ZH.get(m.group(1), m.group(1)), str(text or ""))


# NBA 隊名縮寫 → 繁中(ESPN scoreboard 縮寫;含常見別體,未知縮寫原樣保留。
# 使用者 2026-07-16:英文隊名之外也要有繁中名稱)
_NBA_TEAM_ZH = {
    "ATL": "亞特蘭大老鷹", "BOS": "波士頓塞爾提克", "BKN": "布魯克林籃網",
    "CHA": "夏洛特黃蜂", "CHI": "芝加哥公牛", "CLE": "克里夫蘭騎士",
    "DAL": "達拉斯獨行俠", "DEN": "丹佛金塊", "DET": "底特律活塞",
    "GS": "金州勇士", "GSW": "金州勇士", "HOU": "休士頓火箭", "IND": "印第安納溜馬",
    "LAC": "洛杉磯快艇", "LAL": "洛杉磯湖人", "MEM": "曼菲斯灰熊",
    "MIA": "邁阿密熱火", "MIL": "密爾瓦基公鹿", "MIN": "明尼蘇達灰狼",
    "NO": "紐奧良鵜鶘", "NOP": "紐奧良鵜鶘", "NY": "紐約尼克", "NYK": "紐約尼克",
    "OKC": "奧克拉荷馬雷霆", "ORL": "奧蘭多魔術", "PHI": "費城76人",
    "PHX": "鳳凰城太陽", "POR": "波特蘭拓荒者", "SAC": "沙加緬度國王",
    "SA": "聖安東尼奧馬刺", "SAS": "聖安東尼奧馬刺", "TOR": "多倫多暴龍",
    "UTAH": "猶他爵士", "UTA": "猶他爵士", "WSH": "華盛頓巫師", "WAS": "華盛頓巫師",
}


def _nba_zh(text: str) -> str:
    """把字串中的 NBA 隊名縮寫換成中文隊名(整詞比對;UTAH 為 4 字母故 {2,4})。"""
    return re.sub(r"\b([A-Z]{2,4})\b",
                  lambda m: _NBA_TEAM_ZH.get(m.group(1), m.group(1)), str(text or ""))


# 網球:賽事/球星中文對照(常見者;查無對照維持英文,不硬翻小賽事與新秀)
_TENNIS_EVENT_ZH = {
    "Wimbledon": "溫網", "US Open": "美網", "Australian Open": "澳網",
    "Roland Garros": "法網", "French Open": "法網",
}
_TENNIS_PLAYER_ZH = {   # 以「姓氏」比對(賽果常見 "J. Sinner" 縮寫格式)
    "Sinner": "辛納", "Alcaraz": "艾卡拉茲", "Djokovic": "喬科維奇",
    "Zverev": "茲維列夫", "Medvedev": "梅德維傑夫", "Fritz": "弗里茨",
    "Rune": "魯內", "Musetti": "穆塞提", "Draper": "德雷珀", "Fonseca": "馮塞卡",
    "Sabalenka": "莎巴倫卡", "Swiatek": "斯瓦泰克", "Gauff": "高芙",
    "Rybakina": "雷巴金娜", "Pegula": "佩古拉", "Keys": "凱斯",
    "Osaka": "大坂直美", "Paolini": "保里尼", "Andreeva": "安德蕾娃",
}


def _tennis_zh(name: str) -> str:
    """球員名補中文:姓氏在對照表 → 「J. Sinner(辛納)」;否則原樣。"""
    name = str(name or "").strip()
    surname = name.split()[-1] if name else ""
    zh = _TENNIS_PLAYER_ZH.get(surname)
    return f"{name}({zh})" if zh else name


def _tennis_round_zh(name: str) -> str:
    """輪次中文(批#30):Final→決賽、Semifinal→準決賽、Quarterfinal→8強、
    Round N→第N輪;未知/缺回空字串(顯示端略過)。"""
    import re as _re
    m = {"Final": "決賽", "Semifinal": "準決賽", "Quarterfinal": "8強"}
    name = str(name or "").strip()
    if name in m:
        return m[name]
    mm = _re.match(r"Round (\d+)$", name)
    return f"第{mm.group(1)}輪" if mm else ""


def _tennis_event_zh(event: str) -> str:
    """賽事名補中文:大滿貫等常見賽事 → 「Wimbledon(溫網)」;否則原樣。"""
    event = str(event or "").strip()
    for en, zh in _TENNIS_EVENT_ZH.items():
        if en.lower() in event.lower():
            return f"{event}({zh})"
    return event


def _format_macro_line(name: str, m: dict) -> str:
    """總經指標餵 LLM 的單行格式。明確帶「前值」避免 LLM 回推前值而編造數字
    (曾出現「VIX 從 22.2 跳水」幻覺);僅在 change_pct 確為數字時才反推前值。"""
    if not isinstance(m, dict) or "error" in m or not m.get("close"):
        return f"{name}=資料缺失"
    rank = m.get("pct_rank_252d")
    rank_str = f", 1Y百分位 {rank:.0f}%" if rank is not None else ""
    cp = m.get("change_pct")
    prev = m.get("prev_close")
    if prev is None and isinstance(cp, (int, float)) and (1 + cp / 100) != 0:
        try:
            prev = m["close"] / (1 + cp / 100)
        except (TypeError, ZeroDivisionError):
            prev = None
    prev_str = f"前值 {prev:.2f}, " if isinstance(prev, (int, float)) else ""
    cp_str = f"{cp:+.2f}%" if isinstance(cp, (int, float)) else "漲跌不明"
    return f"{name}={m['close']} ({prev_str}{cp_str}{rank_str})"


#: 章節標題的形狀:中文數字 + 頓號(「七、」「七之二、」「十一、」)。
#: 既有路徑偶爾會把章節標題寫成 `**…**` 而不是 `##`(2026-08-12 實信
#: 十一個章節全部如此),而**內文裡的粗體標籤不是這個形狀** ——
#: 那正是這條規則要分開的兩件事。
_SECTION_NUMBER = re.compile(
    r"^[〇零一二三四五六七八九十百]+(?:之[〇零一二三四五六七八九十百]+)?、")


def _md_to_html(text: str) -> str:
    """
    自製 minimal Markdown → HTML 轉譯器，只用 stdlib `re`，不依賴第三方套件。
    支援：H1-H4 標題、**粗體**、*斜體*、- 與 * 列表、> 引用、空行分段。
    """
    import html as html_lib

    # 1. HTML escape（避免 LLM 輸出的 < > & 變成標籤）
    text = html_lib.escape(text)

    # 2. 一次處理一行
    lines = text.split("\n")
    out: list[str] = []
    in_ul = False
    in_blockquote = False
    para_buffer: list[str] = []

    def flush_para():
        nonlocal para_buffer
        if para_buffer:
            joined = " ".join(para_buffer).strip()
            if joined:
                out.append(f"<p>{joined}</p>")
            para_buffer = []

    def close_lists():
        nonlocal in_ul, in_blockquote
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_blockquote:
            out.append("</blockquote>")
            in_blockquote = False

    for raw in lines:
        line = raw.rstrip()
        # 空行 → 段落結束
        if not line.strip():
            flush_para()
            close_lists()
            continue

        # 標題 #### / ### / ## / #
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            flush_para()
            close_lists()
            level = len(m.group(1))
            content = m.group(2).strip()
            # **編號章節一律 h2**(2026-08-14 實信:模型這次寫 `# 七、…`
            # 單井號 → h1,藍色卡片樣式與 `_wrap_stance` 的段落邊界都
            # 錨在 `<h2>`,整段卡片又只剩文字。prompt 用 `##`、上一班用
            # `**粗體**`、這一班用 `#` —— 模型在三種寫法之間擺盪,
            # 修在渲染器(每天都吃得到):章節的**身分**是「中文數字+頓號」
            # 的形狀,不是它今天恰好用哪一號井號。非編號標題不動。
            if _SECTION_NUMBER.match(content):
                level = 2
            out.append(f"<h{level}>{content}</h{level}>")
            continue

        # **整行只有粗體 = 模型把它當標題在用。**
        #
        # 2026-08-12 實信:既有路徑那一班把「七、昨夜三大重點」到
        # 「十二、一句話總結」**全部**寫成 `**…**` 而不是 `##` ——
        # 於是十一個段落標題變成普通段落,藍色卡片、`_wrap_stance` 的
        # 立場 callout 一起消失,讀者看到的是一大片沒有分段的文字。
        # (prompt 自己就是用 `##` 寫的,前一天同一條路徑也照做了 ——
        # 這是模型的擺盪,不是規格不清楚。)
        #
        # 判準取自那封信本身:**整行粗體的 11 行全部是標題**,
        # 而**粗體開頭後面還有內容**的 21 行(「台積電（2330…）:…」)
        # 全部是內文。所以只認「整行」。
        #
        # **而且要是編號章節**(外審 r1,P2):只看「整行粗體」的話,
        # 段落內一個 `**風險與不確定**` 也會變成藍色標題卡 —— 更糟的是
        # 它若落在立場段裡,`_wrap_stance` 會把它當成下一個 `<h2>`,
        # callout 就提前收掉。本報的章節一律是「中文數字 + 、」
        # (七、/ 七之二、/ 十一、),而內文標籤不是這個形狀。
        # 用形狀而不是寫死名單:週一才出現的「七之六、近期預測檢討」
        # 不會因為沒被列進名單而漏掉。
        m = re.match(r"^\s*\*\*(.+)\*\*\s*$", line)
        if (m and "**" not in m.group(1)
                and _SECTION_NUMBER.match(m.group(1).lstrip())):
            flush_para()
            close_lists()
            out.append(f"<h2>{m.group(1).strip()}</h2>")
            continue

        # 引用 >
        if line.lstrip().startswith("&gt;") or line.lstrip().startswith(">"):
            flush_para()
            if not in_blockquote:
                out.append("<blockquote>")
                in_blockquote = True
            content = re.sub(r"^\s*(?:&gt;|>)\s?", "", line)
            out.append(f"{content}<br>")
            continue
        elif in_blockquote:
            out.append("</blockquote>")
            in_blockquote = False

        # 列表 - 或 *
        m = re.match(r"^\s*[-*]\s+(.+)$", line)
        if m:
            flush_para()
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{m.group(1)}</li>")
            continue
        elif in_ul:
            out.append("</ul>")
            in_ul = False

        # 一般段落內容（累積）
        para_buffer.append(line)

    flush_para()
    close_lists()
    html = "\n".join(out)

    # 3. 行內樣式：**粗體** 與 *斜體*（粗體優先）
    html = re.sub(r"\*\*([^*\n]+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", html)

    # Convert escaped Markdown links before source-label dimming. Keep long RSS
    # destinations in href, not in visible text that expands narrow email tables.
    def link(m):
        clean_url = safe_href(html_lib.unescape(m.group(2)), max_chars=2048)
        if not clean_url:
            return m.group(0)
        return (f'<a href="{html_lib.escape(clean_url, quote=True)}" '
                'style="color:#94a3b8;font-size:12px;font-weight:400;'
                f'font-variant-numeric:normal;">{m.group(1)}</a>')
    return re.sub(r"\[([^\[\]<>\n]{1,120})\]\((https?://[^\s()<>]{1,4096})\)", link, html)


def _style_analysis_html(html: str) -> str:
    """為 markdown 轉出的 HTML 加 inline style（email client 不支援 <style>）。"""
    replacements = [
        # H2（章節標題：四、五、六、七、八、九、十）— 同首頁三大區塊風格
        ("<h2>", "<h2 style=\"color:#0f172a;font-size:21px;font-weight:700;margin:36px 0 14px;padding:10px 16px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;letter-spacing:0.5px;\">"),
        # H3（個股代號 + 公司名）— 大字 + 漸層背景
        ("<h3>", "<h3 style=\"color:#92400e;font-size:19px;font-weight:700;margin:22px 0 12px;padding:10px 14px;background:linear-gradient(90deg,#fef3c7,#fde68a);border-radius:6px;border-left:4px solid #f59e0b;\">"),
        # H4
        ("<h4>", "<h4 style=\"color:#0c4a6e;font-size:16px;font-weight:700;margin:20px 0 8px;\">"),
        ("<h1>", "<h1 style=\"color:#0f172a;font-size:24px;margin:24px 0 12px;\">"),
        # 段落
        ("<p>", "<p style=\"margin:14px 0;line-height:1.95;color:#1f2937;font-size:15px;\">"),
        # 列表
        ("<ul>", "<ul style=\"margin:14px 0 18px;padding-left:24px;line-height:1.95;color:#1f2937;font-size:15px;\">"),
        ("<ol>", "<ol style=\"margin:14px 0 18px;padding-left:24px;line-height:1.95;color:#1f2937;font-size:15px;\">"),
        ("<li>", "<li style=\"margin:8px 0;padding-left:4px;\">"),
        # 強調
        ("<strong>", "<strong style=\"color:#0c4a6e;font-weight:700;\">"),
        ("<em>", "<em style=\"color:#475569;\">"),
        # 引用塊（用於「我的明確立場」）
        ("<blockquote>",
         "<blockquote style=\"border-left:5px solid #0284c7;background:#f0f9ff;margin:14px 0;padding:14px 18px;border-radius:4px;color:#0c4a6e;\">"),
        # 水平線
        ("<hr>", "<hr style=\"border:none;border-top:1px solid #e2e8f0;margin:24px 0;\">"),
        ("<hr />", "<hr style=\"border:none;border-top:1px solid #e2e8f0;margin:24px 0;\">"),
    ]
    for old, new in replacements:
        html = html.replace(old, new)
    return html


def _dim_source_citations(html: str) -> str:
    """新聞來源標註淡化(使用者批#27:來源保留但淡化,不干擾閱讀)。
    把敘述中的來源引用括號「[Bloomberg／鉅亨網]」縮小、轉灰、去粗;
    **信心標「[A 級・信心:高]」保留原樣**(以括號內是否含「信心」二字辨識)。
    在 _md_to_html(已 HTML-escape)之後執行,故方括號為字面值、可安全比對。"""
    import re as _re

    def _repl(m: "_re.Match") -> str:
        inner = m.group(1)
        # 保留(不淡化):①信心標——一律含「信心」二字([X 級・信心:…]/[行情觀察・
        # 信心:…]),故只認「信心」即足(Codex 批#27 r6:不可加「級」豁免,否則
        # [惠譽評級]/[標普全球評級] 等評級機構來源會被誤當信心標而不淡化);
        # ②明確列舉的內部/語義標籤 [stale]、[geo_critical](R13 休市要求輸出醒目
        # [stale];r5:不用「全小寫 ASCII」豁免,否則 [cnbc]/[reuters] 小寫媒體名誤中)。
        if ("信心" in inner
                or inner.strip().lower() in {"stale", "geo_critical"}):
            return m.group(0)
        # 其餘方括號視為來源引用 → 條末小灰字
        return ('<span style="color:#94a3b8;font-size:12px;font-weight:400;'
                'font-variant-numeric:normal;">（' + inner + "）</span>")

    # ===== 全形括號來源 fallback(批#29:2026-07-22 實信驗收發現 LLM 無視 R10b,
    # 全信來源仍寫「（CNBC）（日經 / 巴隆周刊）」→ 淡化完全沒生效。prompt 管不住,
    # render 端補保守辨識:**只有括號內「每個 token 都像媒體名」才淡化**,其餘
    # 全形括號(公司簡介/價位/計價註記)原樣保留。)=====
    # 媒體 token:純拉丁短詞(CNBC/Bloomberg/UDN/news.cnyes.com/CME FedWatch)
    # 或 已知媒體名 或 帶媒體後綴(××報/新聞/周刊/雜誌/通訊社/日報/時報/網)
    _media_known = {"日經", "日經亞洲", "路透", "彭博", "鉅亨", "中央社", "惠譽",
                    "巴隆周刊", "金融時報", "南華早報", "大紀元", "住展", "非凡新聞",
                    "豐雲學堂", "工商時報", "自由財經", "自由時報", "蕃新聞",
                    "好房網", "富房網", "鉅亨網", "經理人", "商周",
                    "今日新聞", "NOWnews"}
    # 泛稱/語義詞不是媒體名(Codex 批#29 r5:精確比對擋不住「最新財報/法說簡報/
    # 無重大新聞」等複合詞 → 改樣式拒絕:①語義後綴(財報/簡報/年報…——「報」
    # 後綴規則的既知誤區);②泛稱修飾開頭(無/最新/重大/相關…——「××新聞」
    # 泛稱片語,非媒體名)。媒體名(經濟日報/非凡新聞/Yahoo 新聞)不受影響)
    _not_media = {"本報", "官網", "法說會"}
    _not_media_pat = _re.compile(
        r"(財報|簡報|年報|季報|月報|週報|周報|半年報|晨報"
        r"|情報|警報|速報|申報|通報|預報|回報|舉報)$"          # Codex r10:快速情報/風險警報等
        r"|^(無|最新|重大|相關|昨日|今日|利多|利空|負面|正面)")
    # 拉丁媒體白名單(Codex 批#29 r1:不可「純拉丁=媒體」——（backwardation）
    # （AVGO）（contango）等術語/代號會被誤淡化;改明確列舉,未知拉丁詞不動)
    _latin_media = {"cnbc", "bloomberg", "reuters", "udn", "yahoo", "cmoney",
                    "moneydj", "rfi", "marketwatch", "wsj", "ft", "nikkei",
                    "ap", "afp", "bbc", "sec 8-k", "sec", "cme fedwatch",
                    "fedwatch", "barron's", "barrons", "newtalk", "ettoday",
                    "msn", "line today", "dazn"}
    # 網域:開頭字母+至少兩個點+字母 TLD(Codex 批#29 r2:泛化「有點就算網域」會
    # 誤吃（2330.TW）代號與（1.029）小數;news.cnyes.com ✓、單點 cnyes.com 寧漏)
    _domain_tok = _re.compile(
        r"^[A-Za-z][A-Za-z0-9-]*(\.[A-Za-z0-9-]+)+\.[A-Za-z]{2,4}$")
    # 裸「網」後綴不成立(Codex 批#29 r4:「（全球最大社群網）」等短簡介會誤中);
    # ××網媒體(好房網/富房網)改列 _media_known,「新聞網」由「網News/新聞」涵蓋
    _suffix_tok = _re.compile(r"(報|新聞|周刊|週刊|雜誌|通訊社|日報|時報|新聞網)$")

    def _is_media_group(inner: str) -> bool:
        s = inner.strip()
        # 快篩:過長、含全形逗號/冒號(公司簡介/價位敘述特徵)一律不是來源
        if not s or len(s) > 30 or _re.search(r"[，：:]", s):
            return False
        toks = [t.strip() for t in _re.split(r"[／/、]", s) if t.strip()]
        if not toks:
            return False
        def _tok_ok(t: str) -> bool:
            # 已知媒體白名單**優先於拒絕樣式**(Codex 批#29 r6:「今日新聞」是真
            # 媒體(NOWnews),不可被 ^今日 泛稱開頭誤拒)
            if t in _media_known or t.lower() in _latin_media:
                return True
            if t in _not_media or _not_media_pat.search(t):
                return False
            if _re.match(r"(僅|如|依|含|詳見|參見)", t):   # 指示性開頭≠媒體名
                return False
            return bool(_domain_tok.match(t)
                        or (_suffix_tok.search(t) and not _re.search(r"[0-9%％]", t)))
        return all(_tok_ok(t) for t in toks)

    def _repl_paren(m: "_re.Match") -> str:
        inner = m.group(1)
        if not _is_media_group(inner):
            return m.group(0)
        return ('<span style="color:#94a3b8;font-size:12px;font-weight:400;'
                'font-variant-numeric:normal;">（' + inner.strip() + "）</span>")
    # 順序:先跑全形括號 pass,再跑方括號 pass——方括號 pass 產出的 span 內含
    # （…）,若順序顛倒會被全形 pass 重複包一層(nested span)
    html = _re.sub(r"（([^（）]{1,30})）", _repl_paren, html)
    # 括號內不再含方括號、長度上限 60(避免誤吞跨句內容)
    return _re.sub(r"\[([^\[\]]{1,60})\]", _repl, html)


#: 引用媒體名 → 語料端別名(來源連結比對用;一律小寫比對)
_CITE_MEDIA_ALIASES: dict = {
    "鉅亨": ("鉅亨", "anue", "cnyes"), "鉅亨網": ("鉅亨", "anue", "cnyes"),
    "cnbc": ("cnbc",), "路透": ("路透", "reuters"), "彭博": ("彭博", "bloomberg"),
    "bloomberg": ("彭博", "bloomberg"), "reuters": ("路透", "reuters"),
    "日經": ("日經", "nikkei"), "日經亞洲": ("日經", "nikkei"),
    "中央社": ("中央社", "cna"), "工商時報": ("工商", "ctee"),
    "自由財經": ("自由", "ltn"), "自由時報": ("自由", "ltn"),
    "經濟日報": ("經濟日報", "udn"), "udn": ("udn",),
    "moneydj": ("moneydj",), "yahoo": ("yahoo",),
    "金融時報": ("金融時報", "ft"),
}


def _cite_sig_tokens(text: str) -> set:
    """內容比對用的特徵 token:CJK 二元組 + 拉丁詞(≥2)+ 數字串。"""
    import re as _re
    toks: set = set()
    for run in _re.findall("[\u4e00-\u9fff]{2,}", text):
        toks.update(run[i:i + 2] for i in range(len(run) - 1))
    toks.update(w.lower() for w in _re.findall("[A-Za-z]{2,}", text))
    toks.update(_re.findall("[0-9]+(?:[.][0-9]+)?", text))
    return toks


def build_news_link_index(news: list) -> list:
    """新聞語料 → 來源連結索引 [{"t"(token 集), "u"(URL), "m"(媒體小寫)}]。

    (2026-08-20 使用者)分析文的來源引用(鉅亨/CNBC…)要能點回原文。
    媒體優先序(外審 2026-08-20):`source_name` 是真正媒體,`source` 常是
    聚合器代號(Google:2330)只當最後退路;標題的「 - 尾碼」要「像媒體」
    (等於/包含 source_name,或在別名表)才剝 —— 真副標題要留在內容 token,
    媒體尾碼則要剝掉,免得媒體名自己變成內容特徵。
    """
    out = []
    for it in news or []:
        try:
            title = str(it.get("title") or "")
            url = str(it.get("link") or "")
            if not title or not url.startswith("http"):
                continue
            src = str(it.get("source") or "").strip()
            media = str(it.get("source_name") or "").strip()
            # 外審 R2:媒體先從欄位確立 —— source_name,否則非聚合器的
            # source(直接 RSS 的 feed 名,如 CNBC Top News)。標題尾碼只在
            # 「像媒體」(對得上已確立媒體/在別名表)或「聚合器條目且欄位
            # 都沒給」(Google News 的「標題 - 媒體」慣例)時才當媒體剝掉;
            # 否則是真副標題,要留在內容 token 裡。
            is_agg = src.lower().startswith(("google:", "類股-", "世界-"))
            if not media and src and not is_agg:
                media = src
            if " - " in title:
                base, tail = title.rsplit(" - ", 1)
                tail = tail.strip()
                tl, ml = tail.lower(), media.lower()
                if ((media and (tl in ml or ml in tl))
                        or tl in _CITE_MEDIA_ALIASES
                        or (not media and is_agg)):
                    title = base
                    media = media or tail
            out.append({"t": _cite_sig_tokens(title),
                        "u": url, "m": media.strip().lower()})
        except Exception:
            continue
    return out[:600]


def _link_source_citations(html: str, sources: list) -> str:
    """把 `_dim_source_citations` 產出的來源 span 升級成超連結(保守比對)。

    ★錯連比不連糟★:連到不相干的文章等於替內容背書一個假出處。三個條件
    全過才上連結 —— ①引用媒體與語料條目的媒體對得上(任一端缺媒體才
    放寬)、②該行內容與標題的特徵 token 重合 ≥3、③最佳候選與次佳分得
    出高下(同分=歧義)。比不出來 → 原樣保留淡化文字。
    sources 缺席(舊測試/降級運行)= no-op。
    """
    import html as _h
    import re as _re
    if not sources:
        return html

    def _media_ok(cited: str, item_media: str) -> bool:
        c = cited.strip().lower()
        aliases = (_CITE_MEDIA_ALIASES.get(c) or (c,)) if c else ()
        if not aliases or not item_media:
            return True     # 缺媒體 → 交給內容重合門檻
        return any(a in item_media or item_media in a for a in aliases)

    _span_pat = _re.compile(
        '<span style="color:#94a3b8;font-size:12px;font-weight:400;'
        'font-variant-numeric:normal;">（([^<）]{1,60})）</span>')

    def _repl(m: "_re.Match") -> str:
        inner = m.group(1)
        # 該行內容:回頭找最近的區塊邊界,剝標籤、反轉義後取特徵 token
        pre = m.string[:m.start()]
        _nl = chr(10)
        cut = max(pre.rfind(t) for t in ("<br", "<li", "</p", "</h",
                                         "</td", "</div", _nl))
        line = _re.sub("<[^>]+>", " ", pre[max(cut, 0):])
        toks = _cite_sig_tokens(_h.unescape(line)[-200:])
        cited_medias = [t.strip() for t in _re.split("[／/、]", inner)
                        if t.strip()]
        if len(cited_medias) > 1:
            # 群組引用(鉅亨/CNBC)不連:單一 <a> 蓋住整組,點到另一家的
            # 名字也會進同一篇 —— 錯誤歸屬,錯連比不連糟(外審 2026-08-20)。
            return m.group(0)
        best_u, best, second = "", 0, 0
        for it in sources:
            if cited_medias and not any(_media_ok(c, it["m"])
                                        for c in cited_medias):
                continue
            score = len(toks & it["t"])
            if score > best:
                best_u, second, best = it["u"], best, score
            elif score > second:
                second = score
        if best >= 3 and best > second and best_u:
            clean = safe_href(best_u)
            if not clean:
                return m.group(0)
            return (f'<a href="{_h.escape(clean, quote=True)}" target="_blank" '
                    f'style="color:#64748b;text-decoration:underline;">'
                    f"{m.group(0)}</a>")
        return m.group(0)

    return _span_pat.sub(_repl, html)


def _wrap_stance(html: str) -> str:
    """把『我的明確立場』段做更醒目的藍色 callout box。"""
    marker = "我的明確立場"
    if marker not in html:
        return html
    idx = html.find(marker)
    h2_start = html.rfind("<h2", 0, idx)
    # 找下一個 h2 即立場段結束
    h2_end = html.find("<h2", idx)
    # **兩端都要找得到。** 只擋 `h2_end == -1` 的話,立場段出現在第一個 `<h2>`
    # 之前時 `h2_start` 是 -1 —— `html[:-1]` 會砍掉最後一個字元、
    # `html[h2_end:]` 又把後半段整個接第二次:讀者看到的是**重複的內文**。
    # 找不到就原樣返回(沒有 callout 是小事,信裡出現兩份內容不是)。
    if h2_start == -1 or h2_end == -1:
        return html
    pre  = html[:h2_start]
    mid  = html[h2_start:h2_end]
    post = html[h2_end:]

    box = ("<div style=\"background:linear-gradient(135deg,#dbeafe,#e0f2fe);"
           "border:2px solid #0284c7;border-radius:14px;"
           "padding:22px 26px;margin:28px 0;box-shadow:0 2px 8px rgba(2,132,199,0.10);\">"
           + mid + "</div>")
    return pre + box + post


# ===== A5-B2:自 morning_report.py 抽出的區塊渲染函式(本體逐字未改;morning_report re-export 保相容)=====
def _render_kpi_strip(quotes: dict, fair: dict, predictions: dict, stance: dict) -> str:
    """頂部 KPI 一覽條（dark bg，緊接 HERO 下方）。
    內容：立場 / 2330 / 00662 / 0050 / 加權，2 秒掃完今天重點。
    若有設定個人持股,第二行顯示 持倉1/持倉2 昨日帳上(未實現)市值變動 + 金額(僅彙總,不揭露明細)。
    (VIX 移到「總經指標」表內，騰出 KPI 位置給 0050。)"""
    import html as _htmllib_kpi   # 持倉名稱可能是 user 自訂字串,需 escape
    # === 立場 ===
    # 批#26 使用者要求:立場只顯示標籤(偏多/偏空/中性),**不顯示淨分數字**
    # ——分數計算仍在後台(STANCE_PY)決定顏色與標籤,只是不外露。
    score = stance.get("score")
    label = stance.get("label") or "—"
    score_str = ""
    if score is None:
        stance_color = "#94a3b8"
    elif score >= 4:
        stance_color = "#fb7185"   # 偏多 → 暖紅（TW 慣例）
    elif score <= -4:
        stance_color = "#86efac"   # 偏空 → 綠
    else:
        stance_color = "#fcd34d"   # 中性 → 黃

    # === 2330 ===
    mid_2330 = predictions.get("mid") if isinstance(predictions, dict) else None
    last_2330 = predictions.get("last_2330") if isinstance(predictions, dict) else None
    pct_2330 = ((mid_2330 / last_2330 - 1) * 100) if (mid_2330 and last_2330) else None

    # === 00662 ===
    fair_price = fair.get("fair_price") if isinstance(fair, dict) else None
    last_00662 = fair.get("last_00662_price") if isinstance(fair, dict) else None
    pct_00662 = ((fair_price / last_00662 - 1) * 100) if (fair_price and last_00662) else None

    # === 加權 ===
    taiex = quotes.get("TAIEX_PRED", {}) or {}
    pred_taiex = taiex.get("pred_open")
    last_taiex = taiex.get("last_close")
    pct_taiex = ((pred_taiex / last_taiex - 1) * 100) if (pred_taiex and last_taiex) else None

    # === 0050 ===
    tw0050p = quotes.get("TW0050_PRED", {}) or {}
    pred_0050 = tw0050p.get("pred_open")
    last_0050 = tw0050p.get("last")
    pct_0050 = ((pred_0050 / last_0050 - 1) * 100) if (pred_0050 and last_0050) else None

    def fmt(v, dec=2):
        return f"{v:.{dec}f}" if v is not None else "—"

    def fmt_int(v):
        return f"{v:,.0f}" if v is not None else "—"

    def color_pct(p):
        if p is None:
            return "rgba(255,255,255,0.55)"
        return "#fb7185" if p >= 0 else "#86efac"   # TW: 紅漲綠跌（在 dark bg 上用較柔的色)

    def fmt_pct(p):
        if p is None:
            return ""
        sign = "+" if p >= 0 else ""
        return f"{sign}{p:.2f}%"

    cell = ("text-align:center;padding:12px 6px 14px;vertical-align:middle;"
            "border-right:1px solid rgba(255,255,255,0.10);")
    cell_last = "text-align:center;padding:12px 6px 14px;vertical-align:middle;"
    lbl = ("font-size:12px;letter-spacing:2px;color:rgba(255,255,255,0.60);"
           "text-transform:uppercase;font-weight:600;line-height:1.2;")
    val = ("font-size:18px;font-weight:700;color:#ffffff;line-height:1.2;"
           "margin-top:6px;font-variant-numeric:tabular-nums;")
    delta = ("font-size:12px;font-weight:500;line-height:1.2;margin-top:3px;"
             "font-variant-numeric:tabular-nums;")

    def _kpi_tile_numeric(label_txt: str, value_str: str, pct: float | None,
                          is_last: bool = False) -> str:
        c = cell_last if is_last else cell
        if pct is None:
            delta_line = ""
        else:
            delta_line = (f'<div style="{delta};color:{color_pct(pct)};">'
                          f'{fmt_pct(pct)}</div>')
        return (f'<td style="{c}">'
                f'<div style="{lbl}">{label_txt}</div>'
                f'<div style="{val}">{value_str}</div>'
                f'{delta_line}'
                f'</td>')

    stance_tile = (f'<td style="{cell}">'
                   f'<div style="{lbl}">立場</div>'
                   f'<div style="{val};color:{stance_color};">{label}{score_str}</div>'
                   f'</td>')

    # === 個人持股列(第二行,僅在有設定時顯示;只秀彙總「昨日帳上漲跌」+ 金額,不揭露明細)===
    pf = quotes.get("PORTFOLIO_ACTUAL", {}) or {}

    def _fmt_amount(amt):
        if amt is None:
            return ""
        sign = "+" if amt >= 0 else "−"
        a = abs(amt)
        if a >= 10000:
            return f"{sign}NT${a/10000:.1f}萬"
        return f"{sign}NT${a:,.0f}"

    def _portfolio_tile(name, data, is_last):
        c = cell_last if is_last else cell
        if not data or data.get("gain_pct") is None:
            return (f'<td style="{c}">'
                    f'<div style="{lbl}">{_htmllib_kpi.escape(name)}</div>'
                    f'<div style="{val};color:rgba(255,255,255,0.55);">—</div>'
                    f'<div style="{delta};color:rgba(255,255,255,0.45);">未設定</div>'
                    f'</td>')
        # 長抱者語意:這是「昨日帳上(未實現)市值變動」= 前天收盤→昨天收盤的部位漲跌,
        # 非賣出已實現損益(系統無持有成本,無法算累計未實現)。不揭露總市值。
        p = data["gain_pct"]
        amt = data.get("gain_amount")
        return (f'<td style="{c}">'
                f'<div style="{lbl}">{_htmllib_kpi.escape(name)} 昨日帳上</div>'
                f'<div style="{val};color:{color_pct(p)};">{fmt_pct(p)}</div>'
                f'<div style="{delta};color:{color_pct(p)};">{_fmt_amount(amt)}</div>'
                f'</td>')

    portfolio_row = ""
    # 批#15(2026-07-18 使用者):持倉1/持倉2 兩格直接隱藏,不再顯示於信件。
    # 計算與去識別存檔邏輯保留(未來要恢復只需翻回 True)。
    _SHOW_PORTFOLIO_ROW = False
    p1 = pf.get("p1") or {}
    p2 = pf.get("p2") or {}
    if _SHOW_PORTFOLIO_ROW and (p1 or p2):
        p1_name = pf.get("p1_name", "持倉1")
        p2_name = pf.get("p2_name", "持倉2")
        # 兩格各佔一半;若只設一個,另一格顯示「未設定」佔位以維持版面
        # <!--PF_ROW_START/END--> 標記供 archive_report_html 去識別(存檔時整列移除),於信件中不可見
        portfolio_row = f"""
          <!--PF_ROW_START--><tr>
            <td style="background:#0a3f5e;padding:0;border-top:1px solid rgba(255,255,255,0.12);">
              <table role="presentation" style="width:100%;border-collapse:collapse;">
                <tr>
                  {_portfolio_tile(p1_name, p1, is_last=False)}
                  {_portfolio_tile(p2_name, p2, is_last=True)}
                </tr>
              </table>
            </td>
          </tr><!--PF_ROW_END-->"""

    # 手機版 3+2 兩列:5 格橫排在 iPhone(~390px)每格僅 78px,數字會擠爆
    return f"""
          <tr>
            <td style="background:#0c4a6e;padding:0;">
              <table role="presentation" style="width:100%;border-collapse:collapse;">
                <tr>
                  {stance_tile}
                  {_kpi_tile_numeric("2330 預測", fmt(mid_2330), pct_2330)}
                  {_kpi_tile_numeric("00662 公允價", fmt(fair_price), pct_00662, is_last=True)}
                </tr>
              </table>
              <table role="presentation" style="width:100%;border-collapse:collapse;border-top:1px solid rgba(255,255,255,0.12);">
                <tr>
                  {_kpi_tile_numeric("0050 預測", fmt(pred_0050), pct_0050)}
                  {_kpi_tile_numeric("加權預測", fmt_int(pred_taiex), pct_taiex, is_last=True)}
                </tr>
              </table>
            </td>
          </tr>{portfolio_row}"""


def _render_model_evidence_html(quotes: dict) -> str:
    """
    顯示「五檔 ML 模型實證(walk-forward)」——讓使用者知道何時該信 ML 排序、何時只信啟發式。
    指標:方向命中率、Top5 平均淨報酬/超額、區間涵蓋、樣本數。無資料則不顯示。
    """
    wf = quotes.get("MODEL_WALK_FORWARD", {}) or {}
    mon = quotes.get("MODEL_MONITORING", {}) or {}
    rows = []
    have_data = False
    for key, label in (("3d", "3 日"), ("5d", "5 日")):
        m = wf.get(key) or {}
        dh = m.get("direction_hit_pct")
        net = m.get("top5_avg_net_return_pct")
        exc = m.get("top5_avg_excess_pct")
        cov = m.get("interval_coverage_pct")
        n = m.get("samples") or 0
        if dh is not None or n:
            have_data = True
        def _c(v, good_hi=None):
            if v is None:
                return "#94a3b8"
            if good_hi is not None:
                return "#16a34a" if v >= good_hi else "#dc2626"
            return "#dc2626" if v >= 0 else "#16a34a"
        dh_s = f"{dh:.1f}%" if dh is not None else "—"
        net_s = f"{net:+.2f}%" if net is not None else "—"
        exc_s = f"{exc:+.2f}%" if exc is not None else "—"
        cov_s = f"{cov:.0f}%" if cov is not None else "—"
        rows.append(
            f"<tr>"
            f"<td style='padding:7px 10px;border-bottom:1px solid #e2e8f0;font-weight:700;color:#0f172a;'>{label}</td>"
            f"<td style='padding:7px 10px;border-bottom:1px solid #e2e8f0;text-align:right;color:{_c(dh,52)};font-weight:700;'>{dh_s}</td>"
            f"<td style='padding:7px 10px;border-bottom:1px solid #e2e8f0;text-align:right;color:{_c(net)};'>{net_s}</td>"
            f"<td style='padding:7px 10px;border-bottom:1px solid #e2e8f0;text-align:right;color:{_c(exc)};'>{exc_s}</td>"
            f"<td style='padding:7px 10px;border-bottom:1px solid #e2e8f0;text-align:right;color:#475569;'>{cov_s}</td>"
            f"<td style='padding:7px 10px;border-bottom:1px solid #e2e8f0;text-align:right;color:#94a3b8;'>{n}</td>"
            f"</tr>")

    # 白話判決(用 3 日為主)
    m3 = wf.get("3d") or {}
    dh3 = m3.get("direction_hit_pct")
    net3 = m3.get("top5_avg_net_return_pct")
    n3 = m3.get("samples") or 0
    status = mon.get("status", "ok")
    if not have_data or n3 < 30 or dh3 is None:
        verdict_bg, verdict_c = "#f1f5f9", "#475569"
        verdict = ("模型實證樣本累積中（live 紀錄需時間累積）。目前五檔以「籌碼+動能+營收」啟發式為主，"
                   "ML 加權自動調低——數字夠了才會證明它是否真的贏過基準。")
    elif dh3 >= 53 and (net3 is None or net3 > 0) and status != "error":
        verdict_bg, verdict_c = "#dcfce7", "#15803d"
        verdict = (f"模型已展現邊際優勢（3 日方向命中 {dh3:.1f}%、Top5 淨報酬 "
                   f"{(f'{net3:+.2f}%' if net3 is not None else 'n/a')}）。五檔 ML 排序可參考。")
    else:
        verdict_bg, verdict_c = "#fef9c3", "#a16207"
        verdict = (f"模型尚未穩定贏過基準（3 日方向命中 {dh3:.1f}%）。建議五檔以籌碼/基本面為主、"
                   f"ML 僅作輔助。")
    # 使用者要求(2026-07-14):「模型狀態」白話結論也不顯示 → 整卡收掉。
    # 指標仍在後台計算並驅動熔斷與品質警示(walk-forward/監測不受影響);
    # verdict 邏輯保留(上方)供日後想恢復顯示或寫 log 時取用。
    del rows, verdict_bg, verdict_c, verdict
    return ""


def _render_event_calendar_html(events: list[dict]) -> str:
    """未來 7 天風險事件。

    2026-08-05 使用者第四次反映信裡有英文:「什麼是 AVERAGE HOURLY
    EARNINGS M/M、non-farm employment change、unemployment rate」。
    這些字串**不是模型寫的** —— ForexFactory 的日曆由 Python 直接排進
    HTML,prompt 改再多也碰不到。一律「中文（英文原名）」:英文保留是
    刻意的,使用者要對得上外電與看盤軟體。
    """
    import econ_terms as _et
    if not events:
        return ""
    def _note(e):
        # 解說接在既有 note 後面(2026-08-27 使用者:事件「最好附上這是
        # 什麼數據/什麼目的」)。認不得的事件回空字串,不硬編。
        base = _et.annotate(str(e.get("note") or ""))
        why = _et.explain(str(e.get("title") or ""))
        if not why:
            return base
        return f"{base}　※{why}" if base else f"※{why}"

    # **外部文字進 HTML 一律 escape**(全案審查 2026-09-03 FR-2):ForexFactory
    # 的 title / note / time 是外部 JSON,先前是全信唯一沒過 `html.escape` /
    # `_external_text` 就進 HTML 的欄位(`_md_to_html`、`safe_href`、CPBL/NBA
    # 都做了)。順序是先 annotate 再 escape:`econ_terms` 只吐純文字,而術語表
    # 裡有「S&P」這種含 `&` 的鍵,先 escape 會讓它對不上。
    events = [dict(e, note=_h.escape(_note(e)),
                   title=_h.escape(_et.annotate(str(e.get("title") or ""))),
                   time=_h.escape(str(e.get("time") or "")))
              for e in events]
    rows = "".join(
        f"<tr><td style='padding:7px 12px;border-bottom:1px solid #e2e8f0;color:#0f172a;"
        f"font-weight:700;white-space:nowrap;font-size:13px;'>"
        f"{e['date'].strftime('%m/%d')}（{'一二三四五六日'[e['date'].weekday()]}）{e.get('time', '')}</td>"
        f"<td style='padding:7px 12px;border-bottom:1px solid #e2e8f0;font-size:13px;"
        f"color:{'#b91c1c' if e.get('impact') == 'high' else '#475569'};'>{e['title']}"
        + (f"<span style='color:#94a3b8;font-size:12px;'>　{e['note']}</span>" if e.get("note") else "")
        + "</td></tr>"
        for e in events[:12])
    return (
        '<div style="border:1px solid #fca5a5;border-radius:10px;overflow:hidden;margin:14px 0;">'
        '<div style="background:#fef2f2;color:#991b1b;padding:8px 14px;font-weight:700;font-size:14px;">'
        '未來 7 天風險事件（時間均為台北時間）</div>'
        '<table style="width:100%;border-collapse:collapse;background:#ffffff;">'
        + rows + '</table></div>')


def _podcast_ticker_crosscheck(t: dict, snapshot: list[dict]) -> str:
    """主持人觀點 vs 本報法人/動能資料的規則式對照(純 Python 查表,零幻覺)。"""
    code = str(t.get("code") or "").strip()
    direction = t.get("direction")
    if t.get("market") == "US" or not code:
        return ""
    row = next((s for s in snapshot or [] if str(s.get("code")) == code), None)
    if row is None:
        # 2026-07-29 使用者要求:不得透露有「追蹤池」這種東西存在。
        # 只說「本報無對照資料」——那是事實陳述,不揭露清單。
        return "本報無對照資料"
    f30 = row.get("foreign_30d_lot")
    p5 = row.get("pct_5d")
    facts = []
    if isinstance(f30, (int, float)) and f30:
        facts.append(f"外資30日{'買超' if f30 > 0 else '賣超'} {abs(f30):,.0f} 張")
    if isinstance(p5, (int, float)):
        facts.append(f"5日 {p5:+.1f}%")
    if not facts:
        return ""
    aligned = None
    if isinstance(f30, (int, float)) and f30 and direction in ("bullish", "bearish"):
        aligned = (f30 > 0) == (direction == "bullish")
    tag = ("與法人方向一致" if aligned
           else "與法人方向分歧" if aligned is not None else "本報資料")
    return f"{tag}({'、'.join(facts)})"


def _episode_age_tag(ep: dict) -> str:
    """節目發布日 + 過舊提示。

    2026-07-27 實信:財經M平方 EP.208 講「台股創單日最大漲點」「高檔震盪」,
    而當天實際是普跌(上漲佔比 30.7%)——讀者會以為那是對今天盤勢的判讀。
    這不是 bug(podcast 本來就有時間差,收錄取決於節目排程),但**沒標日期
    就看不出它在講哪一天**。標出發布日;超過一週再加一句提示。
    """
    import datetime as _dt
    raw = str(ep.get("published") or "").strip()
    if not raw:
        return ""
    try:
        d = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if d.tzinfo is not None:
        d = d.astimezone(_dt.timezone(_dt.timedelta(hours=8)))
    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    days = (now.date() - d.date()).days
    stamp = d.strftime("%m/%d")
    if days >= 7:
        return f" ・{stamp} 錄製（約 {days} 天前，內容可能已非當前盤勢）"
    return f" ・{stamp}"


def _render_podcast_html(episodes: list[dict], snapshot: list[dict], htmllib,
                         max_episodes: int = 14, compact_points: Optional[int] = None) -> str:
    """「Podcast 重點」卡片:每集重點摘要 + 個股觀點與本報資料對照。
    max_episodes / compact_points 供 102KB 超標時「局部縮減」(先減集數/條數)使用,
    預設維持原行為(14 集、台系 15 條)。"""
    if not episodes:
        return ""
    dir_label = {"bullish": ("看多", "#dc2626"), "bearish": ("看空", "#16a34a"),
                 "neutral": ("中性", "#64748b")}
    # 國際快訊壓到 6 條,把版面留給台股(iPhone Gmail 102KB);其餘(含未知/新增節目)維持 15 條
    intl_shows = {"FT News Briefing", "WSJ What's News", "Wall Street Breakfast",
                  "Unhedged (FT)", "Odd Lots", "Money Talks (Economist)",
                  "Sharp Tech (Ben Thompson)", "All-In Podcast",
                  "Animal Spirits", "Invest Like the Best"}
    cards = []
    for ep in episodes[:max(1, max_episodes)]:
        d = ep.get("digest") or {}
        max_pts = 6 if ep.get("show", "") in intl_shows else 15
        if compact_points is not None:               # 局部縮減:進一步壓低每集條數
            max_pts = min(max_pts, compact_points)
        points = "".join(
            f"<li style='margin:4px 0;'>{htmllib.escape(str(p))}</li>"
            for p in (d.get("summary_points") or [])[:max_pts])
        ticker_rows = ""
        for t in (d.get("tickers") or [])[:8]:
            label, color = dir_label.get(str(t.get("direction")), ("—", "#64748b"))
            name = htmllib.escape(str(t.get("name", "")))
            code = htmllib.escape(str(t.get("code", "")).strip())
            disp = f"{name}（{code}）" if code else name
            check = _podcast_ticker_crosscheck(t, snapshot)
            check_html = (f"<div style='font-size:12px;color:#0369a1;margin-top:2px;'>"
                          f"對照:{htmllib.escape(check)}</div>") if check else ""
            ticker_rows += (
                f"<div style='padding:6px 0;border-bottom:1px dashed #e2e8f0;'>"
                f"<b style='color:{color};'>[{label}]</b> "
                f"<b>{disp}</b>"
                f"<span style='color:#475569;font-size:13px;'>"
                f" — {htmllib.escape(str(t.get('reason', '')))}</span>"
                f"{check_html}</div>")
        extras = ""
        for key, label in (("market_view", "大盤觀點"), ("action_view", "操作思路")):
            val = str(d.get(key) or "").strip()
            if val:
                extras += (f"<div style='font-size:13px;color:#334155;margin-top:6px;'>"
                           f"<b>{label}：</b>{htmllib.escape(val)}</div>")
        quote = str(d.get("notable_quote") or "").strip()
        if quote:
            extras += (f"<div style='font-size:12px;color:#64748b;margin-top:6px;"
                       f"font-style:italic;'>「{htmllib.escape(quote)}」</div>")
        cards.append(
            f"<div style='border:1px solid #e2e8f0;border-radius:10px;padding:14px;"
            f"margin:10px 0;background:#ffffff;'>"
            f"<div style='font-size:14px;font-weight:700;color:#0f172a;'>"
            f"{htmllib.escape(str(ep.get('show', '')))}"
            f"<span style='font-weight:400;color:#64748b;font-size:12px;'> ・ "
            f"{htmllib.escape(str(ep.get('title', ''))[:60])}"
            f"{_episode_age_tag(ep)}</span></div>"
            f"<ul style='margin:8px 0;padding-left:20px;font-size:13px;color:#1f2937;"
            f"line-height:1.7;'>{points}</ul>"
            f"{ticker_rows}{extras}</div>")
    return (
        '<h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;'
        'background:#faf5ff;border-left:5px solid #9333ea;border-radius:4px;">'
        'Podcast 重點（台灣節目在前・國際在後）</h2>'
        + "".join(cards))


def _render_sector_rotation_table(rot: dict, heat: dict) -> str:
    """近 5 日資金輪動 —— **一張完整的表**,不是四顆膠囊。

    2026-09-04 使用者:「關於信件內資金輪動的地方我希望能夠清楚、詳細看到資金
    輪動狀況」。先前只印前 4 強 / 後 2 弱的膠囊,類股之間的相對位置、有多少檔
    跟著漲、當天的錢有沒有真的進去,全部看不到。

    每一列一個類股,依「相對大盤」由強到弱:
      近 5 日中位 / 相對大盤 / 5 日上漲檔數÷成分檔數(晨報 universe 口徑,
      `_sector_rotation` 算的)+ 今日成交占比・法人淨買賣(估)・領漲股
      (全市場口徑,與「類股熱度表」同源;類股名稱對不上時該欄留「—」)。
    兩個口徑不同,表頭與註腳都寫明;紅漲綠跌是台股慣例。
    """
    rows = list((rot or {}).get("table") or [])
    if not rows:
        return ""
    sectors = (heat or {}).get("sectors") or {}
    rows.sort(key=lambda r: (r.get("relative") or 0), reverse=True)
    strong = {r[0] for r in (rot.get("strong") or [])}
    weak = {r[0] for r in (rot.get("weak") or [])}

    def _col(v) -> str:
        return "#dc2626" if (v or 0) >= 0 else "#16a34a"

    def _cell(txt, *, color="", bold=False, align="right", muted=False) -> str:
        style = ("padding:5px 6px;border-bottom:1px solid #f1e6d2;font-size:12px;"
                 f"text-align:{align};white-space:nowrap;"
                 + (f"color:{color};" if color else ("color:#94a3b8;" if muted else ""))
                 + ("font-weight:700;" if bold else ""))
        return f"<td style='{style}'>{txt}</td>"

    trs = []
    for r in rows:
        ind = str(r.get("industry") or "")
        med, rel = float(r.get("median_5d") or 0), float(r.get("relative") or 0)
        s = sectors.get(ind) if isinstance(sectors.get(ind), dict) else None
        tag = ("<span style='color:#dc2626;'>▲</span> " if ind in strong
               else "<span style='color:#16a34a;'>▼</span> " if ind in weak else "")
        lead = ""
        if s and s.get("leaders"):
            m = s["leaders"][0]
            try:
                lead = (f"{_h.escape(str(m.get('code') or ''))} "
                        f"{_h.escape(str(m.get('name') or ''))} {float(m.get('pct') or 0):+.1f}%")
            except (TypeError, ValueError):
                lead = ""
        name_cell = (f"{tag}{_h.escape(ind)}"
                     + (f"<div style='font-size:11px;color:#94a3b8;font-weight:400;'>領漲 {lead}</div>"
                        if lead else ""))
        if s:
            share = s.get("value_share_pct")
            inst = s.get("inst_net_yi")
            today = (f"{float(share):.1f}%" if isinstance(share, (int, float)) else "—")
            today += (f"・法人 {float(inst):+,.0f} 億" if isinstance(inst, (int, float)) else "")
        else:
            today = "—"
        trs.append(
            "<tr>" + _cell(name_cell, align="left", bold=True)
            + _cell(f"{med:+.1f}%", color=_col(med), bold=True)
            + _cell(f"{rel:+.1f}", color=_col(rel))
            + _cell(f"{int(r.get('up_5d') or 0)}/{int(r.get('members') or 0)}")
            + _cell(today, muted=not s) + "</tr>")
    head = "".join(
        f"<th style='padding:5px 6px;font-size:11px;color:#92400e;text-align:{a};"
        f"border-bottom:1px solid #fcd9b6;white-space:nowrap;'>{t}</th>"
        for t, a in (("類股", "left"), ("5 日中位", "right"), ("相對大盤", "right"),
                     ("5 日上漲/檔", "right"), ("今日成交占比・法人", "right")))
    mm = float((rot or {}).get("market_median") or 0)
    return (
        "<div style='margin:4px 0 14px;padding:10px 12px;background:#fffbeb;border-radius:8px;'>"
        "<div style='font-size:13px;font-weight:600;color:#92400e;margin-bottom:6px;'>"
        f"近 5 日資金輪動（各類股中位漲幅 vs 大盤中位 {mm:+.1f}%；▲ 強勢 / ▼ 轉弱）</div>"
        "<div style='overflow-x:auto;'><table style='width:100%;border-collapse:collapse;'>"
        f"<tr>{head}</tr>{''.join(trs)}</table></div>"
        "<div style='font-size:11px;color:#94a3b8;margin-top:6px;'>"
        "※ 5 日中位 / 相對大盤 / 上漲檔數＝晨報 universe 成分股口徑（相對 &gt;0＝資金相對流入）；"
        "今日成交占比・法人淨買賣（估）・領漲＝全市場口徑，與上方類股熱度表同源。"
        "純參考、非買賣訊號。</div></div>")


def _mlb_series_odds_div(s: dict, htmllib) -> str:
    """MLB 賭盤:**一行**,與中職那一行同一個樣子(2026-09-03 使用者:
    「直接 賭盤:樂天 46%・味全 54%(Polymarket) 這樣即可」)。
    單場:「賭盤:道奇 52%・洋基 48%(Polymarket)」;
    連戰:「賭盤:07/18:光芒 46%・紅襪 54%;07/19:光芒 48%・紅襪 52%(Polymarket)」
    —— 每場的勝率都在(帶日期),但不再各自佔一行、也不再另起一個標題行。"""
    odds_list = s.get("odds_list") or []
    if not odds_list:
        return ""

    def _strip(o: str) -> str:
        return (str(o).replace("賭盤:", "").replace("(Polymarket)", "")
                .replace("・", " ・ ").strip())

    if len(odds_list) == 1 and s.get("n", 1) == 1:
        body = htmllib.escape(_strip(odds_list[0][1]))
    else:
        body = ";".join(
            (f"{htmllib.escape(day)}:" if day else "") + htmllib.escape(_strip(o))
            for day, o in odds_list)
    return (f"<div style='font-size:11px;color:#b45309;margin-left:2px;"
            f"line-height:1.8;'>賭盤:{body}"
            f"<span style='color:#94a3b8;'>　(Polymarket)</span></div>")


def _render_sports_html(sports: dict, htmllib) -> str:
    """體育快訊卡:CPBL 戰績表 + NBA 冠軍賽 + MLB 戰績榜 + 新聞標題。無資料回空。"""
    news = (sports or {}).get("news") or {}
    cpbl = (sports or {}).get("cpbl") or []
    cpbl_source = (sports or {}).get("cpbl_source")
    cpbl_label = (sports or {}).get("cpbl_label") or ""
    cpbl_full_year = (sports or {}).get("cpbl_full_year") or []
    cpbl_full_year_label = (sports or {}).get("cpbl_full_year_label") or ""
    cpbl_scores = (sports or {}).get("cpbl_scores") or []
    nba = (sports or {}).get("nba") or []
    nba_fav = (sports or {}).get("nba_fav") or []
    nba_offseason = (sports or {}).get("nba_offseason") or ""
    standings = (sports or {}).get("standings") or {}
    worldcup = (sports or {}).get("worldcup") or {}
    wc_results = worldcup.get("results") or []
    wc_groups = worldcup.get("groups") or []
    wc_fixtures = worldcup.get("fixtures") or []
    wc_knockout = worldcup.get("knockout") or []
    mlb_tw = (sports or {}).get("mlb_tw") or []
    tennis = (sports or {}).get("tennis") or {}
    cpbl_fixtures = (sports or {}).get("cpbl_fixtures") or []
    poly = (sports or {}).get("poly") or {}   # Polymarket 賭盤(2026-07-16)

    def _poly_delta_sfx(r) -> str:
        # 變化(↑↓pp);基準非昨日時標實際間隔天數(地基批#4)。
        # 量低標記移到行級聚合(批#14:逐名⚠塞滿整行難讀)
        d = r.get("delta")
        if isinstance(d, (int, float)) and abs(d) >= 1:
            days = r.get("delta_days", 1)
            span = f"/{days}日" if isinstance(days, int) and days > 1 else ""
            arrow = f"↑{d:.0f}" if d > 0 else f"↓{-d:.0f}"
            return f"({arrow}pp{span})"
        return ""

    def _poly_line(rows) -> str:
        # 批#15 排版:條目間用「 ・ 」(前後留空),名稱與機率間以空格相連,
        # 量低註記前加全形空——原本全部黏成一長串難讀(使用者三度反映)
        body = " ・ ".join(
            f"{htmllib.escape(str(r.get('name', '')))} {r.get('prob', 0)}%{_poly_delta_sfx(r)}"
            for r in rows or [])
        if any(r.get("low_vol") for r in rows or []):
            body += "　(部分量低)"
        if any(r.get("wide") for r in rows or []):   # 批#17:價差寬=顯示價不可盡信
            body += "　(部分價差寬)"
        return body

    def _poly_odds_block(label: str, content_lines: list, note: str = "Polymarket") -> str:
        """賭盤小卡通用版式(批#15):標籤獨立一行(深色粗體+淡色註記),
        內容逐行縮排列在下方,行距 1.9——取代舊「label:A 26%・B 18%…(note)」
        全擠一行的寫法。"""
        rows_html = "".join(
            f"<div style='color:#b45309;padding-left:10px;'>{line}</div>"
            for line in content_lines if line)
        if not rows_html:
            return ""
        return (f"<div style='font-size:12px;line-height:1.9;margin:6px 0 4px;'>"
                f"<div style='color:#0f172a;font-weight:700;'>{label}"
                f"<span style='color:#94a3b8;font-weight:400;font-size:11px;'>"
                f"　({note})</span></div>{rows_html}</div>")

    def _poly_champ_div(label: str, rows, note: str = "Polymarket") -> str:
        return _poly_odds_block(label, [_poly_line(rows)], note)

    def _two_league_div(label: str, a_rows, b_rows, pa: str = "AL", pb: str = "NL") -> str:
        """雙聯盟/雙分區:標籤一行,AL/NL(或東/西)各自獨立一行。兩邊皆空回空。"""
        lines = []
        if a_rows:
            lines.append(f"{pa}:{_poly_line(a_rows)}")
        if b_rows:
            lines.append(f"{pb}:{_poly_line(b_rows)}")
        return _poly_odds_block(label, lines)

    def _mlb_poly_lines() -> str:
        # 世界大賽冠軍 + 年度 MVP + 賽揚(批#11,2026-07-16)
        return ((_poly_champ_div("世界大賽冠軍盤", poly["mlb_ws"])
                 if poly.get("mlb_ws") else "")
                + _two_league_div("年度 MVP 盤", poly.get("mlb_al_mvp"), poly.get("mlb_nl_mvp"))
                + _two_league_div("賽揚獎盤", poly.get("mlb_al_cy"), poly.get("mlb_nl_cy")))

    def _nba_poly_lines() -> str:
        # 總冠軍 + 東西區冠軍(批#11,2026-07-16)
        return ((_poly_champ_div("2026-27 冠軍盤", poly["nba_champ"])
                 if poly.get("nba_champ") else "")
                + _two_league_div("東西區冠軍盤", poly.get("nba_east"), poly.get("nba_west"),
                                  "東", "西"))

    def _tennis_poly_div(p, line_fn) -> str:
        # 下一個大滿貫(美網)冠軍 futures;球星名以中文為主(批#14:
        # 「EN(中文)」逐名並列讓整行過長難讀),查無對照才保留英文
        def _zh_rows(rows):
            def _short(name):
                surname = str(name or "").split()[-1] if name else ""
                return _TENNIS_PLAYER_ZH.get(surname, str(name or ""))
            return [{**r, "name": _short(r.get("name"))} for r in rows or []]
        lines = []
        if p.get("tennis_m"):
            lines.append(f"男:{line_fn(_zh_rows(p['tennis_m']))}")
        if p.get("tennis_w"):
            lines.append(f"女:{line_fn(_zh_rows(p['tennis_w']))}")
        return _poly_odds_block("美網冠軍盤", lines)
    # Polymarket 冠軍盤本身也是可渲染內容:傳統來源全掛時不可讓整張體育卡消失
    # (Codex review 批#9;cpbl_games 不算——沒賽程行可掛就無處顯示)
    _poly_renderable = any(poly.get(k) for k in (
        "wc_champion", "mlb_ws", "nba_champ", "tennis_m", "tennis_w",
        "mlb_al_mvp", "mlb_nl_mvp", "mlb_al_cy", "mlb_nl_cy", "nba_east", "nba_west"))
    if not (cpbl or cpbl_full_year or cpbl_scores or cpbl_fixtures
            or nba or nba_fav or nba_offseason
            or standings or wc_results or wc_groups or wc_fixtures or wc_knockout
            or mlb_tw or tennis.get("tournaments") or tennis.get("results")
            or (sports or {}).get("mlb_fixtures") or (sports or {}).get("nba_fixtures")
            or _poly_renderable or any(news.values())):
        return ""
    blocks = []
    # 區塊身分:在 append 的當下記下,標題再由它推出。
    # 掃 HTML 找關鍵字永遠會有假陽性(中職新聞標題提到 NBA → 標題冒出 NBA)。
    sections: set = set()

    def _mark(section: str) -> None:
        if section:
            sections.add(section)

    if wc_results or wc_groups or wc_fixtures or wc_knockout:
        wc_inner = []
        # 淘汰賽對戰表(各回合完整賽果+未賽場次台北開球時間):存在時為世足主視圖,
        # 「近期戰績/今日賽程」(其內容是對戰表的子集)不再另列,只保留小組表收斂註記。
        if wc_knockout:
            # 早期回合收斂:某回合「全部打完」且「後面已有回合開打」→ 它是舊聞(32 強足足
            # 16 行 ≈ 1.2KB/日),收成一行;最新開打的回合與未來回合仍完整顯示。
            # 102KB 天花板下,這些空間直接還給 Podcast(2026-07-14 使用者反映 podcast 變少)。
            _round_done = [all(g.get("done") for g in (rd.get("games") or [])) and
                           bool(rd.get("games")) for rd in wc_knockout]
            _latest_active = -1
            for _i, rd in enumerate(wc_knockout):
                if any(g.get("done") for g in (rd.get("games") or [])):
                    _latest_active = _i
            ko_parts = []
            for _i, rd in enumerate(wc_knockout):
                games = rd.get("games") or []
                if _round_done[_i] and _i < _latest_active:
                    ko_parts.append(
                        f"<div style='margin:4px 0;font-size:12px;color:#94a3b8;'>"
                        f"<b style='color:#475569;'>{htmllib.escape(rd.get('name', ''))}</b>"
                        f"　已完賽 {len(games)} 場(賽果見先前信件)</div>")
                    continue
                glines = "".join(
                    "<div style='font-size:13px;color:#334155;line-height:1.85;'>"
                    f"<span style='color:#94a3b8;'>{htmllib.escape(str(g.get('when', '')))}</span>　"
                    + (f"{htmllib.escape(g['text'])}"
                       if g.get("done")
                       else f"<span style='color:#64748b;'>{htmllib.escape(g['text'])}</span>")
                    + (f"<div style='font-size:11px;color:#b45309;margin-left:2px;'>"
                       f"{htmllib.escape(g['odds'])}</div>"
                       if g.get("odds") and not g.get("done") else "")
                    + "</div>"
                    for g in games)
                ko_parts.append(
                    f"<div style='margin:4px 0;'><b style='color:#0f172a;font-size:13px;'>"
                    f"{htmllib.escape(rd.get('name', ''))}</b>{glines}</div>")
            wc_inner.append(
                "<div style='margin:6px 0;'><b style='color:#0f172a;'>淘汰賽對戰表(台北時間;灰字=未開賽)</b>"
                + "".join(ko_parts) + "</div>")
        if poly.get("wc_champion"):
            # 「冠軍」機率=整屆奪冠(含延長/PK),與單場「賭盤(90分鐘)」語意不同,
            # 標題必須寫「冠軍機率」——不可與 DraftKings 90 分鐘市場混用措辭。
            wc_inner.append(
                f"<div style='margin:4px 0;font-size:12px;color:#b45309;'>"
                f"<b>冠軍機率</b>:{_poly_line(poly['wc_champion'])}"
                f"<span style='color:#94a3b8;'>(Polymarket 預測市場)</span></div>")
        # 淘汰賽是否已開打:對戰表中「有已完賽場次」、或任一賽果/賽程帶「非小組賽」
        # 回合標籤。刻意不以「對戰表存在」為準——淘汰賽首日早上對戰表全是未賽場次,
        # 一場未打就收掉小組最終積分表、吞掉末日小組賽賽果都太早(Codex review)。
        # 偵測不到時保守視為未開打(積分表/近期戰績照常顯示,不誤藏)。
        def _is_ko_round(r):
            s = str(r or "")
            return bool(s) and "group" not in s.lower() and "組" not in s
        # _bracket_live=對戰表含已完賽場次(可完整取代近期戰績);對戰表缺席或全未賽時,
        # 近期戰績照常顯示(bracket 抓取失敗要能降級,末日小組賽賽果也不能被吞)。
        _bracket_live = any(g.get("done") for rd in wc_knockout
                            for g in (rd.get("games") or []))
        _knockout_started = (_bracket_live
                             or any(_is_ko_round(g.get("round")) for g in wc_results)
                             or any(_is_ko_round(g.get("round")) for g in wc_fixtures))
        if wc_results and not _bracket_live:
            lines = "".join(
                f"<div style='font-size:13px;color:#334155;line-height:1.85;'>"
                f"<span style='color:#94a3b8;'>{g.get('date', '')}</span>　{htmllib.escape(g['text'])}"
                + (f"　<span style='color:#94a3b8;font-size:11px;'>{htmllib.escape(g['round'])}</span>"
                   if _is_ko_round(g.get("round")) else "")
                + f"　<span style='color:#16a34a;font-size:11px;'>{htmllib.escape(g.get('status', ''))}</span>"
                f"</div>"
                for g in wc_results)
            wc_inner.append(
                f"<div style='margin:6px 0;'><b style='color:#0f172a;'>近期戰績</b>{lines}</div>")
        # 賽程是對戰表未賽場次的嚴格子集:對戰表存在(含全未賽)即不重複列
        if wc_fixtures and not wc_knockout:
            lines = "".join(
                f"<div style='font-size:13px;color:#334155;line-height:1.8;'>"
                f"<span style='color:#94a3b8;'>{htmllib.escape(g.get('kickoff', ''))}</span>　"
                f"{htmllib.escape(g['text'])}"
                + (f"　<span style='color:#94a3b8;font-size:11px;'>{htmllib.escape(g['round'])}</span>"
                   if g.get("round") else "")
                + (f"<div style='font-size:11px;color:#b45309;margin-left:2px;'>"
                   f"{htmllib.escape(g['odds'])}</div>"
                   if g.get("odds") else "")
                + "</div>"
                for g in wc_fixtures)
            wc_inner.append(
                f"<div style='margin:6px 0;'><b style='color:#0f172a;'>今日/近日賽程（台北時間）</b>{lines}</div>")
        if wc_groups and _knockout_started:
            # 淘汰賽已開打:12 組積分表已是舊聞(最終積分已在小組賽結束當天完整顯示過),
            # 每日重複佔 ~3KB;收斂成一行。偵測不到淘汰賽回合標籤時走下方分支照常顯示(不誤藏)。
            wc_inner.append(
                "<div style='margin:6px 0;font-size:12px;color:#94a3b8;'>"
                "小組賽已結束(最終積分表已於先前信件完整刊出);淘汰賽戰績與賽程如上。</div>")
        elif wc_groups:
            # 收合:每組一行(隊名 積分(勝-和-敗)),iPhone 上比 12 張表省 3/4 高度。
            # 各組前 2 名(暫居晉級線內)以綠色粗體標示;小組賽結束後即代表晉級者。
            # 小組賽是否全部踢完 → 只用來切換標題/圖例措辭(不影響顯示哪些隊,一律列全隊)。
            # 完整性防護(Codex review):ESPN 可能只回部分分組/部分隊伍(缺列的組不會出現在
            # wc_groups),若只檢查「已回傳的列 gp≥3」,單一完賽分組就會誤判整個小組賽結束。
            # 故要求「分組數達預期 12 組、每組 4 隊皆有、且每隊 gp≥3」才算完賽;任一條件不符
            # → 保守視為進行中(顯示全隊+「累計」標籤),不會誤藏隊伍。
            # 用「唯一」組名/隊名計數,避免重複組或重複隊被當成完整 payload(Codex review)。
            _grp_done = (
                len({grp["name"] for grp in wc_groups}) >= _WC_EXPECTED_GROUPS
                and all(len({t["team"] for t in grp["rows"]}) >= _WC_TEAMS_PER_GROUP
                        for grp in wc_groups)
                and all(t.get("gp", 0) >= 3 for grp in wc_groups for t in grp["rows"])
            )
            _grp_title = "小組賽最終積分" if _grp_done else "分組累計戰績"
            # 2026 為 48 隊制:各組前 2「直接晉級」,另有 8 個成績最佳第 3 名亦晉級,
            # 故措辭不可寫成「只有前 2 晉級」。
            _grp_note = "小組前 2(直接晉級)" if _grp_done else "暫居小組前 2(晉級區)"

            def _team_cell(idx, t):
                cell = f"{htmllib.escape(t['team'])} {t['pts']}({t['w']}-{t['d']}-{t['l']})"
                if idx < 2:   # rows 已於 fetch_worldcup 端依積分/淨勝分排序,前兩名為晉級區
                    return f"<b style='color:#16a34a;'>{cell}</b>"
                return f"<span style='color:#94a3b8;'>{cell}</span>"
            # 一律列全隊。刻意不在小組賽結束後只留前 2 名:2026 世界盃 48 隊制,除各組前 2 外
            # 另有「8 個成績最佳的第 3 名」晉級,隱藏第 3 名會藏掉真正晉級的隊伍(Codex review)。
            grp_lines = "".join(
                "<div style='font-size:12px;color:#334155;line-height:1.8;margin:1px 0;'>"
                f"<b style='color:#0f172a;'>{htmllib.escape(grp['name'])}</b>　"
                + " ・ ".join(_team_cell(i, t) for i, t in enumerate(grp["rows"]))
                + "</div>"
                for grp in wc_groups)
            wc_inner.append(
                f"<div style='margin:6px 0;'><b style='color:#0f172a;'>{_grp_title}</b>"
                "<div style='font-size:11px;color:#94a3b8;'>隊名 積分(勝-和-敗);"
                f"<span style='color:#16a34a;'>綠字</span>={_grp_note}</div>"
                + grp_lines + "</div>")
        _mark("世足")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;font-size:14px;'>世界盃足球賽</b>"
            + "".join(wc_inner) + "</div>")
    elif poly.get("wc_champion"):
        # ESPN 世足資料全掛但 Polymarket 活著 → 冠軍機率仍要出現(Codex review 批#9)
        _mark("世足")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;font-size:14px;'>世界盃足球賽</b>"
            + _poly_champ_div("冠軍機率", poly["wc_champion"], "Polymarket 預測市場")
            + "</div>")
    if mlb_tw:
        rows = "".join(
            f"<div style='font-size:13px;color:#334155;line-height:1.85;'>"
            f"<span style='color:#94a3b8;'>{htmllib.escape(p.get('date', ''))}</span>　"
            f"<b>{htmllib.escape(p['name'])}</b>"
            f"<span style='color:#64748b;font-size:11px;'>（{htmllib.escape(p.get('role', ''))}）</span>　"
            f"{htmllib.escape(p.get('summary', ''))}</div>"
            for p in mlb_tw)
        _mark("MLB")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>MLB 台灣旅外球員（近期出賽）</b>"
            + rows + "</div>")
    if cpbl_scores:
        def _side(name, score, is_win):
            cell = f"{htmllib.escape(name)} {score}"
            return f"<b style='color:#b91c1c;'>{cell}</b>" if is_win else cell
        rows = "".join(
            f"<div style='font-size:13px;color:#334155;line-height:1.9;'>"
            f"<span style='color:#94a3b8;'>{htmllib.escape(s.get('date', ''))}</span>　"
            f"{_side(s['away'], s['away_score'], s.get('winner') == 'away')}"
            f"　:　{_side(s['home'], s['home_score'], s.get('winner') == 'home')}</div>"
            for s in cpbl_scores)
        _mark("中職")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>中華職棒 最新賽果</b>"
            + rows + "</div>")
    if cpbl_fixtures:
        rows = "".join(
            f"<div style='font-size:13px;color:#334155;line-height:1.85;'>"
            f"<span style='color:#94a3b8;'>"
            f"{htmllib.escape((str(f.get('date', '')) + ' ' + str(f.get('start', ''))).strip())}"
            # 場地緊跟在時間後面(2026-08-24 使用者:「08/25 18:35@斗六
            # 味全 vs 統一」)—— 先前排在隊伍後面,一眼看過去是「幾點、
            # 誰打誰」,地點被擠到句尾。
            + (f"<span style='color:#0f766e;'>@"
               f"{htmllib.escape(str(f['venue']))}</span>"
               if f.get("venue") else "")
            + "</span>　"
            f"{htmllib.escape(f['away'])} vs {htmllib.escape(f['home'])}"
            + (f"<div style='font-size:11px;color:#b45309;margin-left:2px;'>"
               f"{htmllib.escape(str(f['odds']))}</div>" if f.get("odds") else "")
            + "</div>"
            for f in cpbl_fixtures)
        _mark("中職")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>中華職棒 未來一週賽程（台北時間）</b>"
            + rows + "</div>")
    def _cpbl_table(teams: list, title: str, note: str = "") -> str:
        rows = "".join(
            f"<tr><td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;"
            f"font-size:13px;color:#0f172a;'>{t['rank']}. <b>{htmllib.escape(t['team'])}</b></td>"
            f"<td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;text-align:right;"
            f"font-size:13px;'>{t['wdl']}</td>"
            f"<td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;text-align:right;"
            f"font-size:13px;'>{t['pct']}</td>"
            f"<td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;text-align:right;"
            f"font-size:13px;color:#64748b;'>{htmllib.escape(t['gb'] or '-')}</td></tr>"
            for t in teams)
        return ("<div style='margin:8px 0;'><b style='color:#0f172a;'>"
                + htmllib.escape(title) + "</b>"
                '<table data-mobile-layout="table" '
                "style='width:100%;border-collapse:collapse;margin-top:4px;'>"
                "<tr style='background:#f8fafc;'><th style='padding:4px 10px;text-align:left;"
                "font-size:12px;color:#64748b;'>排名</th><th style='padding:4px 10px;"
                "text-align:right;font-size:12px;color:#64748b;'>勝-和-敗</th>"
                "<th style='padding:4px 10px;text-align:right;font-size:12px;color:#64748b;'>勝率</th>"
                "<th style='padding:4px 10px;text-align:right;font-size:12px;color:#64748b;'>勝差</th></tr>"
                + rows + "</table>" + note + "</div>")

    if cpbl or cpbl_full_year:
        src_note = ""
        if cpbl_source == "Wikipedia 備援":
            src_note = ("<div style='font-size:11px;color:#94a3b8;margin-top:2px;'>"
                        "※ 中職官網海外連線受限,本表為 Wikipedia 備援(社群更新,可能稍有遲滯)</div>")
        _mark("中職")
        # 分段名沒拿到就不標 —— 中職季後賽資格同時看半季冠軍與全年勝率,
        # 標錯段比不標更糟(讀者會拿下半季的勝差去想全年的門票)。
        if cpbl:
            blocks.append(_cpbl_table(
                cpbl, f"中華職棒戰績（{cpbl_label}）" if cpbl_label else "中華職棒戰績",
                "" if cpbl_full_year else src_note))
        if cpbl_full_year:
            blocks.append(_cpbl_table(
                cpbl_full_year,
                f"中華職棒戰績（{cpbl_full_year_label}）" if cpbl_full_year_label
                else "中華職棒全年戰績", src_note))
    _nba_champ_shown = False   # 冠軍盤是否已嵌進某個 NBA 區塊(否則最後獨立渲染)
    if nba:
        rows = "".join(
            f"<div style='font-size:13px;color:#334155;line-height:1.9;'>"
            f"{g.get('date', '')}　{_nba_zh(g['text'])}"
            + (f"　<span style='color:#b91c1c;font-weight:700;'>{htmllib.escape(_nba_zh(g['series']))}</span>"
               if g.get("series") else "")
            + (f"<div style='font-size:12px;color:#94a3b8;'>{htmllib.escape(g['note'])}</div>"
               if g.get("note") else "")
            + "</div>"
            for g in nba)
        _mark("NBA")
        blocks.append(
            f"<div style='margin:8px 0;'><b style='color:#0f172a;'>NBA 冠軍賽</b>{rows}"
            + _nba_poly_lines() + "</div>")
        _nba_champ_shown = True
    if nba_fav:
        rows = "".join(
            f"<div style='font-size:13px;color:#334155;line-height:1.9;'>"
            f"<span style='color:#94a3b8;'>{htmllib.escape(g.get('date', ''))}</span>　{_nba_zh(g['text'])}"
            + (f"<div style='font-size:12px;color:#94a3b8;'>{htmllib.escape(g['note'])}</div>"
               if g.get("note") else "")
            + "</div>"
            for g in nba_fav)
        _mark("NBA")
        blocks.append(f"<div style='margin:8px 0;'><b style='color:#0f172a;'>NBA 關注球隊近況</b>{rows}</div>")
    if nba_offseason and not nba and not nba_fav:
        _mark("NBA")
        blocks.append(
            f"<div style='margin:8px 0;'><b style='color:#0f172a;'>NBA</b>"
            f"<div style='font-size:13px;color:#64748b;margin-top:2px;'>"
            f"{htmllib.escape(nba_offseason)}</div>"
            + _nba_poly_lines() + "</div>")
        _nba_champ_shown = True
    nba_fixtures = (sports or {}).get("nba_fixtures") or []
    if nba_fixtures:
        rows = "".join(
            f"<div style='padding:6px 0;border-bottom:1px dashed #f1f5f9;"
            f"font-size:13px;color:#334155;line-height:1.7;'>"
            f"<span style='color:#94a3b8;'>{htmllib.escape(str(g.get('when', '')))}</span>　"
            f"<b>{htmllib.escape(_nba_zh(str(g.get('text', ''))))}</b>"
            + (f"<div style='font-size:11px;color:#b45309;margin-left:2px;'>"
               f"{htmllib.escape(_nba_zh(str(g.get('odds', ''))))}</div>" if g.get("odds") else "")
            + "</div>"
            for g in nba_fixtures)
        _mark("NBA")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>NBA 未來一週賽程（台北時間）</b>"
            + rows + "</div>")
    if (poly.get("nba_champ") or poly.get("nba_east") or poly.get("nba_west"))             and not _nba_champ_shown:
        # 冠軍賽/休賽季說明都缺席(如 ESPN 掛掉)→ 各盤獨立渲染(Codex review 批#9)
        _mark("NBA")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>NBA</b>"
            + _nba_poly_lines() + "</div>")
    if standings:
        # MLB 戰績:改表格排版(2026-07-16 使用者反映「、」串接一長行難讀)——
        # 聯盟分節列 + 每隊一列(排名/中文隊名/勝-敗/勝率),與中職戰績表同款式
        seg_rows = []
        for lg, teams in standings.items():
            seg_rows.append(
                f"<tr><td colspan='3' style='padding:6px 10px;background:#f8fafc;"
                f"font-size:12px;font-weight:700;color:#475569;'>{htmllib.escape(lg)}</td></tr>")
            for i, t in enumerate(teams, 1):
                pct = f"{t['pct']:.3f}" if t.get("pct") else "-"
                seg_rows.append(
                    f"<tr><td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;"
                    f"font-size:13px;color:#0f172a;'>{i}. <b>{htmllib.escape(_mlb_zh(t['team']))}</b></td>"
                    f"<td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;"
                    f"text-align:right;font-size:13px;'>{htmllib.escape(str(t['record']))}</td>"
                    f"<td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;"
                    f"text-align:right;font-size:13px;color:#64748b;'>{pct}</td></tr>")
        _mark("MLB")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>MLB 戰績（兩聯盟勝率前 5）</b>"
            "<table style='width:100%;border-collapse:collapse;margin-top:4px;'>"
            "<tr style='background:#f8fafc;'><th style='padding:4px 10px;text-align:left;"
            "font-size:12px;color:#64748b;'>排名 / 球隊</th><th style='padding:4px 10px;"
            "text-align:right;font-size:12px;color:#64748b;'>勝-敗</th>"
            "<th style='padding:4px 10px;text-align:right;font-size:12px;color:#64748b;'>勝率</th></tr>"
            + "".join(seg_rows) + "</table>"
            + _mlb_poly_lines() + "</div>")
    elif poly.get("mlb_ws") or poly.get("mlb_al_mvp") or poly.get("mlb_nl_mvp")             or poly.get("mlb_al_cy") or poly.get("mlb_nl_cy"):
        # ESPN 戰績掛掉但 Polymarket 活著 → 各盤獨立渲染(Codex review 批#9)
        _mark("MLB")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>MLB</b>"
            + _mlb_poly_lines() + "</div>")
    mlb_fixtures = (sports or {}).get("mlb_fixtures") or []
    if mlb_fixtures:
        # 同一組對戰的系列賽合併成一行(使用者反映 07/18 TB@BOS 連列 3 行太混亂):
        # 顯示首戰時間 + 對戰 + 系列場數與日期。保持首戰時間排序。
        series: dict[str, dict] = {}
        for g in mlb_fixtures:
            key = _mlb_zh(g.get("text", ""))   # 中文隊名(使用者要求 2026-07-15)
            s = series.setdefault(key, {"first": str(g.get("when", "")),
                                        "dates": [], "special": False,
                                        "odds_list": []})
            when = str(g.get("when", ""))
            day = when.split(" ")[0] if when else ""
            if day and day not in s["dates"]:
                s["dates"].append(day)
            if g.get("odds"):
                # 每場賭盤各自保留(帶日期);合併列只留首戰賭盤會吞掉第 2、3 戰
                # 各自的勝率(Codex review 批#10)。同日雙重賽兩場的賭盤若相同
                # (Polymarket 常只開一場或兩場同價)→ 去重,不重印同一行
                # (2026-07-17 信件:光芒@紅襪同日兩行一模一樣)
                entry = (day, _mlb_zh(g.get("odds", "")))
                if entry not in s["odds_list"]:
                    s["odds_list"].append(entry)
            s["special"] = s["special"] or bool(g.get("special"))
            s["n"] = s.get("n", 0) + 1
        rows = "".join(
            f"<div style='padding:6px 0;border-bottom:1px dashed #f1f5f9;"
            f"font-size:13px;color:#334155;line-height:1.7;'>"
            f"<span style='color:#94a3b8;'>{htmllib.escape(s['first'])}</span>　"
            f"<b>{htmllib.escape(text)}</b>"
            + (f"　<span style='color:#94a3b8;font-size:11px;'>"
               f"{s['n']} 連戰:{htmllib.escape('、'.join(s['dates']))}</span>"
               if s.get("n", 1) > 1 else "")
            + ("　<span style='color:#b45309;font-size:11px;'>特別賽事</span>"
               if s["special"] else "")
            + _mlb_series_odds_div(s, htmllib)
            + "</div>"
            for text, s in sorted(series.items(), key=lambda kv: kv[1]["first"]))
        _mark("MLB")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>MLB 未來一週焦點賽程（台北時間;強隊對戰）</b>"
            + rows + "</div>")
    if tennis.get("tournaments") or tennis.get("results"):
        t_inner = []
        results = tennis.get("results") or []
        if results:
            # 比照世足收斂(使用者 2026-07-15):已結束的賽事不再逐場列,收斂成「冠軍行」。
            # 批#30:冠軍判定改**輪次**——只有 round=="Final" 的場次才是決賽→冠軍行;
            # 無 Final 賽果=賽事未打完→逐場列。舊消去法(不在進行中列表=已結束)誤判:
            # ESPN 對進行中賽事當日打完也標 post,三天出了三個「冠軍」(07/21-23 實信,
            # Palermo/Estoril 每天換冠軍——其實是把當日場次當決賽)。
            by_event: dict[tuple, list] = {}
            for r in results:
                key = str(r.get("event_key") or r.get("event") or "—")
                by_event.setdefault((key, str(r.get("tour") or "")), []).append(r)
            done_lines, live_lines = [], []
            for (event, tour), rs in by_event.items():
                rs.sort(key=lambda r: str(r.get("date", "")))
                fin = next((r for r in reversed(rs)
                            if str(r.get("round") or "") == "Final"), None)
                if fin is None:
                    live_lines.extend(rs[-4:])          # 未見決賽:列最近 4 場
                    continue
                tier = (f"<span style='color:#b45309;'>[{htmllib.escape(fin['tier'])}]</span> "
                        if fin.get("tier") else "")
                shown_event = str(fin.get("event") or event)   # 顯示用截斷名;比對用未截斷 key
                done_lines.append(
                    f"<div style='padding:4px 0;border-bottom:1px dashed #f1f5f9;"
                    f"font-size:12px;color:#334155;line-height:1.7;'>"
                    f"{tier}<b>{htmllib.escape(_tennis_event_zh(shown_event))}</b>"
                    f"　{htmllib.escape(tour)} 冠軍:"
                    f"<b>{htmllib.escape(_tennis_zh(fin['winner']))}</b>"
                    f"<span style='color:#94a3b8;font-size:11px;'>"
                    f"　（決賽勝 {htmllib.escape(_tennis_zh(fin['loser']))}"
                    f"・{htmllib.escape(str(fin.get('date', '')))}）</span></div>")
            live_parts = []
            for r in live_lines:
                rd = _tennis_round_zh(str(r.get("round") or ""))   # 批#30:標輪次
                ev_note = ""
                if r.get("event"):
                    ev_note = (f"<span style='color:#94a3b8;font-size:11px;'>"
                               f"（{htmllib.escape(_tennis_event_zh(r['event']))}"
                               + (f"・{rd}" if rd else "") + "）</span>")
                elif rd:
                    ev_note = (f"<span style='color:#94a3b8;font-size:11px;'>"
                               f"（{rd}）</span>")
                live_parts.append(
                    f"<div style='padding:4px 0;border-bottom:1px dashed #f1f5f9;"
                    f"font-size:12px;color:#334155;line-height:1.7;'>"
                    f"<span style='color:#94a3b8;'>{htmllib.escape(str(r.get('date', '')))} "
                    f"{htmllib.escape(r['tour'])}</span>　"
                    + (f"<span style='color:#b45309;'>[{htmllib.escape(r['tier'])}]</span> "
                       if r.get("tier") else "")
                    + f"<b>{htmllib.escape(_tennis_zh(r['winner']))}</b> 勝 "
                      f"{htmllib.escape(_tennis_zh(r['loser']))}"
                    + (f"　<span style='color:#0369a1;'>"
                       f"{htmllib.escape(str(r['score']))}</span>"
                       if r.get("score") else "")
                    + ev_note + "</div>")
            live_seg = "".join(live_parts)
            t_inner.append("".join(done_lines) + live_seg)
        if tennis.get("tournaments"):
            # 進行中/即將開打的賽事:逐行列(原「|」串接一長行難讀,2026-07-16)
            seg = "".join(
                f"<div style='font-size:12px;color:#475569;line-height:1.7;'>"
                f"<span style='color:#94a3b8;'>・</span>{htmllib.escape(_tennis_event_zh(t['name']))}"
                + (f"<span style='color:#94a3b8;'>（{htmllib.escape(t['status'])}）</span>"
                   if t.get("status") else "")
                + "</div>"
                for t in tennis["tournaments"][:5])
            t_inner.append(
                f"<div style='font-size:12px;color:#475569;line-height:1.7;margin-top:4px;'>"
                f"<b>進行中/即將</b>{seg}</div>")
        if poly.get("tennis_m") or poly.get("tennis_w"):
            t_inner.append(_tennis_poly_div(poly, _poly_line))
        _mark("網球")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>網球 ATP / WTA</b>"
            + "".join(t_inner) + "</div>")
    elif poly.get("tennis_m") or poly.get("tennis_w"):
        # ESPN 網球資料掛掉但 Polymarket 活著 → 冠軍盤獨立渲染(Codex review 批#9)
        _mark("網球")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>網球 ATP / WTA</b>"
            + _tennis_poly_div(poly, _poly_line) + "</div>")
    for label in ("世足", "中華職棒", "網球", "MLB", "NBA"):
        titles = news.get(label) or []
        if not titles:
            continue

        def _sports_item(t) -> str:
            # 新格式 dict {"title","link"} → 可見超連結(使用者要求 2026-07-14);
            # 舊格式純字串(state 殘留/降級)→ 純文字,不崩
            if isinstance(t, dict):
                title = htmllib.escape(str(t.get("title", "")))
                link = htmllib.escape(safe_href(t.get("link")), quote=True)
                if link:
                    return (f"<li style='margin:3px 0;'><a href='{link}' "
                            f"style='color:#0f172a;text-decoration:none;'>{title}</a></li>")
                return f"<li style='margin:3px 0;'>{title}</li>"
            return f"<li style='margin:3px 0;'>{htmllib.escape(str(t))}</li>"

        items = "".join(_sports_item(t) for t in titles)
        _mark(_NEWS_LABEL_TO_SECTION.get(label, label))
        blocks.append(
            f"<div style='margin:8px 0;'><b style='color:#0f172a;'>{label} 消息</b>"
            f"<ul style='margin:4px 0;padding-left:20px;font-size:12px;color:#475569;"
            f"line-height:1.6;'>{items}</ul></div>")
    # 標題由**實際有內容的項目**推出,不再寫死。
    # 2026-07-27 實信:世足賽期已於 7/19 結束、整個區塊不出現,標題卻仍寫著
    # 「世足 / MLB / NBA / 中職 / 網球」——讀者會去找一個不存在的區塊。
    # 賽季性項目本來就會輪流缺席(NBA 休賽季、世足四年一次),寫死必然對不上。
    # r1(Codex,P2)**確認**:我上一批用「資料存不存在」當判斷,但那與 blocks
    # 實際渲染的條件不同——tennis 正常回的是 {"tournaments":[],"results":[]},
    # `bool(tennis)` 為真卻不會渲染任何網球區塊;MLB 戰績表的鍵是「美聯」不是
    # 「mlb」,只有戰績可用時反而不會列進標題。**而我的測試用
    # {"tennis":{"atp":[...]}} 這種不會產生區塊的形狀,等於把缺陷釘成規格。**
    # 正解:標題直接由**已經渲染出來的區塊**推出,兩者不可能再分歧。
    present = [s for s in _SPORTS_SECTION_ORDER if s in sections]
    title = "體育快訊" + (f"（{' / '.join(present)}）" if present else "")
    return (
        '<h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;'
        'background:#f0fdf4;border-left:5px solid #16a34a;border-radius:4px;">'
        f'{title}</h2>'
        '<div style="border:1px solid #e2e8f0;border-radius:10px;padding:6px 16px;'
        'background:#ffffff;">' + "".join(blocks) + "</div>")


#: 體育各項目在**已渲染區塊**裡的辨識字串。標題由區塊推出,兩者不可能分歧
#: (r1 Codex,P2:先前用「資料存不存在」判斷,與實際渲染條件不同)。
#: 順序即標題顯示順序。
#: (標題顯示名, 該項目在已渲染區塊裡的辨識字串)
#: r2(Codex,P2):世足區塊寫的是「世界盃足球賽」而我找的是「世足」——
#: 只有世足資料時區塊會出現、標題卻漏掉它,正好是這條修正要防的反向落差。
#: **辨識字串必須逐字對應區塊實際輸出的標題**,不能憑印象寫簡稱。
#: (標題顯示名, 結構化區塊的辨識字串, 新聞區塊的字典鍵)
#: r3(Codex,P2):只掃結構化區塊的字串不夠——世足在**沒有結構化賽果、只有新聞**
#: 時會渲染出「世足 消息」區塊,而辨識字串是「世界盃足球賽」→ 區塊出現、標題卻
#: 漏掉它。新聞區塊的標籤直接來自 news 字典的鍵,拿鍵去判斷比掃 HTML 可靠。
#: 標題顯示順序。
#: r5(Codex,P2,**同一件事他講了三次**):前兩版我都在掃區塊的 HTML 找關鍵字,
#: 於是「中職新聞的標題裡剛好提到 NBA」就會讓 NBA 出現在標題裡,而根本沒有
#: NBA 區塊。掃內容永遠會有這種假陽性——正解是**在 append 的當下記下身分**,
#: 標題與區塊因此不可能再分歧。我兩次都選了比較省事的做法,這次照做。
_SPORTS_SECTION_ORDER = ("世足", "MLB", "NBA", "中職", "網球")
#: 新聞區塊用的字典鍵 → 標題顯示名
_NEWS_LABEL_TO_SECTION = {"世足": "世足", "中華職棒": "中職", "網球": "網球",
                          "MLB": "MLB", "NBA": "NBA"}


def safe_href(raw, *, max_chars: int = 500) -> str:
    """外部連結 → **可以放進 `href` 的乾淨 URL**;不合格回空字串。

    **全 repo 只有這一份判準**(2026-08-28 外審 P2)。先前是:
      * `morning_report._safe_source_url` 只用在**一個**寫入點(新聞進 state);
      * `render_utils._is_web_url` 定義了但**零呼叫端**;
      * 而 `_safe_source_url` 的 docstring 宣稱「渲染端另有第二道(縱深
        防禦)」—— 那句話是假的。
    於是 CWA 警特報、停班停課公告、體育賽事這些**外部來源**的連結,
    渲染時只做了 `html.escape`。escape 擋得住屬性逃逸(`" onclick=`),
    但擋不住 scheme:`javascript:alert(1)` escape 完還是
    `javascript:alert(1)`,照樣是一個可點的 href。
    信件用戶端多半會擋,但那是別人的邊界,不是我們的。

    判準:scheme 必須**完全等於** http/https(不是 startswith ——
    `httpx://`、`httpjavascript:` 都會通過 startswith)、要有 netloc、
    長度有上限、不含控制字元(換行可以把屬性拆開)。
    """
    from urllib.parse import urlsplit
    url = str(raw or "").strip()
    if not url or len(url) > max_chars:
        return ""
    if any(ord(c) < 32 or ord(c) == 127 for c in url):
        return ""                       # 控制字元(換行可以把屬性拆開)
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return ""
    return url


def _fmt_fact(raw) -> str:
    """數字事實 → 可讀字串。與 story_ledger.format_fact 同一份實作(避免分歧)。"""
    try:
        from story_ledger import format_fact
        return format_fact(raw)
    except Exception:
        return str(raw)


# ── 信件體積:重複的 inline style 收斂成 class ────────────────────────────
#: 一個 style 字串要出現幾次才值得換成 class。
#:
#: **門檻本身就是安全網。** 只出現一兩次的樣式(KPI 卡、立場卡那些一次性的
#: 版面)會留在 inline —— 萬一某個客戶端剝掉 `<style>`,信裡最關鍵、最獨特的
#: 部分仍然有樣式;被 class 化的是重複幾十次的內文與表格,那些即使失去樣式
#: 也只是變樸素,不會讀不懂。
_STYLE_CLASS_MIN_USES = 4

_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_ATTR_RE = re.compile(r"""\sstyle=(['"])(.*?)\1""", re.S)


def compact_inline_styles(html: str, min_uses: int = _STYLE_CLASS_MIN_USES) -> str:
    """把重複的 inline style 收斂成 `<style>` 裡的 class(批#103)。

    2026-08-01 實測:一封信 106.8 KB,其中 **55.3 KB(52%)是 inline style
    屬性**,而可見文字只有 31.8 KB。完全相同的 style 字串就重複了 41 KB ——
    `color:#94a3b8;` 出現 45 次、`color:#0c4a6e;font-weight:700;` 出現 41 次。

    信超過 Gmail 的 102 KB 門檻就會被摺疊(收件端看到「•••」,中間整段收起來),
    而這在 2026-07-28 之後每天都在發生。壓體積是**不刪任何內容**的解法。

    刻意做成**組裝完成後的單一轉換**,而不是去改幾十個 HTML 產生器:
    它是純字串重寫、可以逐元素驗證等價,而散在各處的改動沒辦法。

    只重寫標籤內的 `style=` 屬性;可見文字裡剛好出現同樣字元的地方不會被碰到。
    """
    if not html or "<head>" not in html:
        return html
    counts: dict = {}
    for tag in _TAG_RE.findall(html):
        for _q, value in _STYLE_ATTR_RE.findall(tag):
            counts[value] = counts.get(value, 0) + 1
    # 依「省下的位元組」排序才會先處理長而重複的;同分時用字串排序保證輸出穩定
    # (輸出穩定 = 兩封信的 diff 有意義,而不是每天 class 編號都在跳)。
    worth = sorted((v for v, n in counts.items() if n >= min_uses),
                   key=lambda v: (-len(v) * counts[v], v))
    if not worth:
        return html
    names = {value: f"s{i}" for i, value in enumerate(worth)}

    def _rewrite(m):
        tag = m.group(0)

        def _sub(sm):
            value = sm.group(2)
            cls = names.get(value)
            return f' class="{cls}"' if cls else sm.group(0)

        return _STYLE_ATTR_RE.sub(_sub, tag)

    out = _TAG_RE.sub(_rewrite, html)
    sheet = "".join(f".{names[v]}{{{v}}}" for v in worth)
    return out.replace("</head>", f"<style>{sheet}</style></head>", 1)
