"""渲染格式 helpers(A5-Step2 由 morning_report 抽出)。
Markdown→HTML、分析 HTML 上色、立場包裝、總經單行格式;皆純函式、無網路/狀態、
不依賴 morning_report 其它符號;morning_report 以 re-export 保相容,既有測試零修改。"""
from __future__ import annotations

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
            out.append(f"<h{level}>{content}</h{level}>")
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

    return html


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


def _wrap_stance(html: str) -> str:
    """把『我的明確立場』段做更醒目的藍色 callout box。"""
    marker = "我的明確立場"
    if marker not in html:
        return html
    idx = html.find(marker)
    h2_start = html.rfind("<h2", 0, idx)
    # 找下一個 h2 即立場段結束
    h2_end = html.find("<h2", idx)
    if h2_end == -1:
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
    score = stance.get("score")
    label = stance.get("label") or "—"
    if score is None:
        stance_color = "#94a3b8"
        score_str = ""
    elif score >= 4:
        stance_color = "#fb7185"   # 偏多 → 暖紅（TW 慣例）
        score_str = f" {score:+d}"
    elif score <= -4:
        stance_color = "#86efac"   # 偏空 → 綠
        score_str = f" {score:+d}"
    else:
        stance_color = "#fcd34d"   # 中性 → 黃
        score_str = f" {score:+d}"

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
    if not events:
        return ""
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
        return "不在本報追蹤池"
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
            f"{htmllib.escape(str(ep.get('title', ''))[:60])}</span></div>"
            f"<ul style='margin:8px 0;padding-left:20px;font-size:13px;color:#1f2937;"
            f"line-height:1.7;'>{points}</ul>"
            f"{ticker_rows}{extras}</div>")
    return (
        '<h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;'
        'background:#faf5ff;border-left:5px solid #9333ea;border-radius:4px;">'
        'Podcast 重點（台灣節目在前・國際在後）</h2>'
        + "".join(cards))


def _mlb_series_odds_div(s: dict, htmllib) -> str:
    """MLB 系列賽賭盤:合併成單一小字行(批#14 使用者反映多行「賭盤:…」重複難讀)。
    單場:「賭盤:道奇 52%・洋基 48%(Polymarket)」照舊;
    連戰:「賭盤(Polymarket):07/18 光芒 46%・紅襪 54%;07/19 光芒 48%・紅襪 52%」。"""
    odds_list = s.get("odds_list") or []
    if not odds_list:
        return ""

    def _strip(o: str) -> str:
        # 批#15:條目分隔「・」前後補空,不再整串黏在一起
        return (str(o).replace("賭盤:", "").replace("(Polymarket)", "")
                .replace("・", " ・ ").strip())

    if len(odds_list) == 1 and s.get("n", 1) == 1:
        return (f"<div style='font-size:11px;color:#b45309;margin-left:2px;"
                f"line-height:1.8;'>賭盤:{htmllib.escape(_strip(odds_list[0][1]))}"
                f"<span style='color:#94a3b8;'>　(Polymarket)</span></div>")
    # 連戰:每個比賽日各自一行(舊版以「;」串成一長行難讀,批#15)
    rows = "".join(
        "<div style='color:#b45309;padding-left:10px;'>"
        + (f"{htmllib.escape(day)}:" if day else "")
        + htmllib.escape(_strip(o)) + "</div>"
        for day, o in odds_list)
    return (f"<div style='font-size:11px;line-height:1.8;margin-left:2px;'>"
            f"<span style='color:#0f172a;font-weight:700;'>賭盤"
            f"<span style='color:#94a3b8;font-weight:400;'>　(Polymarket)</span></span>"
            f"{rows}</div>")


def _render_sports_html(sports: dict, htmllib) -> str:
    """體育快訊卡:CPBL 戰績表 + NBA 冠軍賽 + MLB 戰績榜 + 新聞標題。無資料回空。"""
    news = (sports or {}).get("news") or {}
    cpbl = (sports or {}).get("cpbl") or []
    cpbl_source = (sports or {}).get("cpbl_source")
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
            body += "　(部分量低⚠)"
        if any(r.get("wide") for r in rows or []):   # 批#17:價差寬=顯示價不可盡信
            body += "　(部分價差寬⚠)"
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
    if not (cpbl or cpbl_scores or cpbl_fixtures or nba or nba_fav or nba_offseason
            or standings or wc_results or wc_groups or wc_fixtures or wc_knockout
            or mlb_tw or tennis.get("tournaments") or tennis.get("results")
            or (sports or {}).get("mlb_fixtures") or (sports or {}).get("nba_fixtures")
            or _poly_renderable or any(news.values())):
        return ""
    blocks = []
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
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;font-size:14px;'>世界盃足球賽</b>"
            + "".join(wc_inner) + "</div>")
    elif poly.get("wc_champion"):
        # ESPN 世足資料全掛但 Polymarket 活著 → 冠軍機率仍要出現(Codex review 批#9)
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
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>中華職棒 最新賽果</b>"
            + rows + "</div>")
    if cpbl_fixtures:
        rows = "".join(
            f"<div style='font-size:13px;color:#334155;line-height:1.85;'>"
            f"<span style='color:#94a3b8;'>"
            f"{htmllib.escape((str(f.get('date', '')) + ' ' + str(f.get('start', ''))).strip())}</span>　"
            f"{htmllib.escape(f['away'])} vs {htmllib.escape(f['home'])}"
            + (f"<div style='font-size:11px;color:#b45309;margin-left:2px;'>"
               f"{htmllib.escape(str(f['odds']))}</div>" if f.get("odds") else "")
            + "</div>"
            for f in cpbl_fixtures)
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>中華職棒 未來一週賽程（台北時間）</b>"
            + rows + "</div>")
    if cpbl:
        rows = "".join(
            f"<tr><td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;"
            f"font-size:13px;color:#0f172a;'>{t['rank']}. <b>{htmllib.escape(t['team'])}</b></td>"
            f"<td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;text-align:right;"
            f"font-size:13px;'>{t['wdl']}</td>"
            f"<td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;text-align:right;"
            f"font-size:13px;'>{t['pct']}</td>"
            f"<td style='padding:4px 10px;border-bottom:1px solid #f1f5f9;text-align:right;"
            f"font-size:13px;color:#64748b;'>{htmllib.escape(t['gb'] or '-')}</td></tr>"
            for t in cpbl)
        src_note = ""
        if cpbl_source == "Wikipedia 備援":
            src_note = ("<div style='font-size:11px;color:#94a3b8;margin-top:2px;'>"
                        "※ 中職官網海外連線受限,本表為 Wikipedia 備援(社群更新,可能稍有遲滯)</div>")
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>中華職棒戰績</b>"
            "<table style='width:100%;border-collapse:collapse;margin-top:4px;'>"
            "<tr style='background:#f8fafc;'><th style='padding:4px 10px;text-align:left;"
            "font-size:12px;color:#64748b;'>排名</th><th style='padding:4px 10px;"
            "text-align:right;font-size:12px;color:#64748b;'>勝-和-敗</th>"
            "<th style='padding:4px 10px;text-align:right;font-size:12px;color:#64748b;'>勝率</th>"
            "<th style='padding:4px 10px;text-align:right;font-size:12px;color:#64748b;'>勝差</th></tr>"
            + rows + "</table>" + src_note + "</div>")
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
        blocks.append(f"<div style='margin:8px 0;'><b style='color:#0f172a;'>NBA 關注球隊近況</b>{rows}</div>")
    if nba_offseason and not nba and not nba_fav:
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
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>NBA 未來一週賽程（台北時間）</b>"
            + rows + "</div>")
    if (poly.get("nba_champ") or poly.get("nba_east") or poly.get("nba_west"))             and not _nba_champ_shown:
        # 冠軍賽/休賽季說明都缺席(如 ESPN 掛掉)→ 各盤獨立渲染(Codex review 批#9)
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
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>MLB 未來一週焦點賽程（台北時間;強隊對戰）</b>"
            + rows + "</div>")
    if tennis.get("tournaments") or tennis.get("results"):
        t_inner = []
        results = tennis.get("results") or []
        # 比對用 event_key(未截斷原名;顯示名 40/30 字截斷不一致,長名賽事會誤判已結束
        # —— Codex review);舊 state 無 event_key 時退回顯示名。
        ongoing_names = {str(t.get("event_key") or t.get("name") or "")
                         for t in (tennis.get("tournaments") or [])}
        if results:
            # 比照世足收斂(使用者 2026-07-15):已結束的賽事不再逐場列(溫網 6 行舊賽果=雜訊),
            # 收斂成「冠軍行」——各巡迴(ATP/WTA)取該賽事最後一場=決賽;進行中的賽事才逐場列。
            by_event: dict[tuple, list] = {}
            for r in results:
                key = str(r.get("event_key") or r.get("event") or "—")
                by_event.setdefault((key, str(r.get("tour") or "")), []).append(r)
            done_lines, live_lines = [], []
            for (event, tour), rs in by_event.items():
                rs.sort(key=lambda r: str(r.get("date", "")))
                if event and event in ongoing_names:
                    live_lines.extend(rs[-4:])          # 進行中:列最近 4 場
                    continue
                fin = rs[-1]                            # 已結束:最後一場=決賽 → 冠軍行
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
            live_seg = "".join(
                f"<div style='padding:4px 0;border-bottom:1px dashed #f1f5f9;"
                f"font-size:12px;color:#334155;line-height:1.7;'>"
                f"<span style='color:#94a3b8;'>{htmllib.escape(str(r.get('date', '')))} "
                f"{htmllib.escape(r['tour'])}</span>　"
                + (f"<span style='color:#b45309;'>[{htmllib.escape(r['tier'])}]</span> "
                   if r.get("tier") else "")
                + f"<b>{htmllib.escape(_tennis_zh(r['winner']))}</b> 勝 {htmllib.escape(_tennis_zh(r['loser']))}"
                + (f"<span style='color:#94a3b8;font-size:11px;'>（{htmllib.escape(_tennis_event_zh(r['event']))}）</span>"
                   if r.get("event") else "")
                + "</div>"
                for r in live_lines)
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
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>網球 ATP / WTA</b>"
            + "".join(t_inner) + "</div>")
    elif poly.get("tennis_m") or poly.get("tennis_w"):
        # ESPN 網球資料掛掉但 Polymarket 活著 → 冠軍盤獨立渲染(Codex review 批#9)
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
                link = htmllib.escape(str(t.get("link", "")))
                if link:
                    return (f"<li style='margin:3px 0;'><a href='{link}' "
                            f"style='color:#0f172a;text-decoration:none;'>{title}</a></li>")
                return f"<li style='margin:3px 0;'>{title}</li>"
            return f"<li style='margin:3px 0;'>{htmllib.escape(str(t))}</li>"

        items = "".join(_sports_item(t) for t in titles)
        blocks.append(
            f"<div style='margin:8px 0;'><b style='color:#0f172a;'>{label} 消息</b>"
            f"<ul style='margin:4px 0;padding-left:20px;font-size:12px;color:#475569;"
            f"line-height:1.6;'>{items}</ul></div>")
    return (
        '<h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;'
        'background:#f0fdf4;border-left:5px solid #16a34a;border-radius:4px;">'
        '體育快訊（世足 / MLB / NBA / 中職 / 網球）</h2>'
        '<div style="border:1px solid #e2e8f0;border-radius:10px;padding:6px 16px;'
        'background:#ffffff;">' + "".join(blocks) + "</div>")
