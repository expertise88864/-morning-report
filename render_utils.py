"""渲染格式 helpers(A5-Step2 由 morning_report 抽出)。
Markdown→HTML、分析 HTML 上色、立場包裝、總經單行格式;皆純函式、無網路/狀態、
不依賴 morning_report 其它符號;morning_report 以 re-export 保相容,既有測試零修改。"""
from __future__ import annotations

import re
from typing import Optional

# 世足小組賽完賽判定用:2026 世界盃為 12 組 × 4 隊。ESPN 回傳不完整時保守視為「進行中」。
_WC_EXPECTED_GROUPS = 12
_WC_TEAMS_PER_GROUP = 4


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
    p1 = pf.get("p1") or {}
    p2 = pf.get("p2") or {}
    if p1 or p2:
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
    # 使用者回饋:詳細表格(方向命中/淨報酬/區間涵蓋/樣本)屬內部驗證細節,
    # 不顯示 — 只留一句白話結論;指標仍在後台計算並驅動熔斷與品質警示。
    del rows
    return f"""
        <div style="background:{verdict_bg};border-radius:8px;padding:10px 14px;margin:18px 0 8px;font-size:13px;color:{verdict_c};line-height:1.6;">
          <b>模型狀態：</b>{verdict}
        </div>
        """


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
        + "".join(cards)
        + "<p style='font-size:12px;color:#94a3b8;margin:4px 0;'>"
          "※ 以上為主持人個人觀點之摘要(AI 轉錄,可能有誤),非本報建議;"
          "「對照」為與本報法人/動能資料的對照,不納入股價模型。</p>")


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
    mlb_tw = (sports or {}).get("mlb_tw") or []
    tennis = (sports or {}).get("tennis") or {}
    if not (cpbl or cpbl_scores or nba or nba_fav or nba_offseason or standings or wc_results
            or wc_groups or wc_fixtures or mlb_tw or tennis.get("tournaments")
            or tennis.get("results") or any(news.values())):
        return ""
    blocks = []
    if wc_results or wc_groups or wc_fixtures:
        wc_inner = []
        # 淘汰賽是否已開打:任一賽果/賽程帶「非小組賽」回合標籤(Quarterfinal/Round of 32…)。
        # 用於下方收斂小組積分表;無回合標籤時保守視為未開打(積分表照常顯示,不誤藏)。
        def _is_ko_round(r):
            s = str(r or "")
            return bool(s) and "group" not in s.lower() and "組" not in s
        _knockout_started = (any(_is_ko_round(g.get("round")) for g in wc_results)
                             or any(_is_ko_round(g.get("round")) for g in wc_fixtures))
        if wc_results:
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
        if wc_fixtures:
            lines = "".join(
                f"<div style='font-size:13px;color:#334155;line-height:1.8;'>"
                f"<span style='color:#94a3b8;'>{htmllib.escape(g.get('kickoff', ''))}</span>　"
                f"{htmllib.escape(g['text'])}"
                + (f"　<span style='color:#94a3b8;font-size:11px;'>{htmllib.escape(g['round'])}</span>"
                   if g.get("round") else "")
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
    if nba:
        rows = "".join(
            f"<div style='font-size:13px;color:#334155;line-height:1.9;'>"
            f"{g.get('date', '')}　{g['text']}"
            + (f"　<span style='color:#b91c1c;font-weight:700;'>{htmllib.escape(g['series'])}</span>"
               if g.get("series") else "")
            + (f"<div style='font-size:12px;color:#94a3b8;'>{htmllib.escape(g['note'])}</div>"
               if g.get("note") else "")
            + "</div>"
            for g in nba)
        blocks.append(f"<div style='margin:8px 0;'><b style='color:#0f172a;'>NBA 冠軍賽</b>{rows}</div>")
    if nba_fav:
        rows = "".join(
            f"<div style='font-size:13px;color:#334155;line-height:1.9;'>"
            f"<span style='color:#94a3b8;'>{htmllib.escape(g.get('date', ''))}</span>　{g['text']}"
            + (f"<div style='font-size:12px;color:#94a3b8;'>{htmllib.escape(g['note'])}</div>"
               if g.get("note") else "")
            + "</div>"
            for g in nba_fav)
        blocks.append(f"<div style='margin:8px 0;'><b style='color:#0f172a;'>NBA 關注球隊近況</b>{rows}</div>")
    if nba_offseason and not nba and not nba_fav:
        blocks.append(
            f"<div style='margin:8px 0;'><b style='color:#0f172a;'>NBA</b>"
            f"<div style='font-size:13px;color:#64748b;margin-top:2px;'>"
            f"{htmllib.escape(nba_offseason)}</div></div>")
    if standings:
        seg = "　|　".join(
            f"<b>{lg}</b> " + "、".join(f"{t['team']} {t['record']}" for t in teams)
            for lg, teams in standings.items())
        blocks.append(f"<div style='margin:8px 0;font-size:12px;color:#475569;'>"
                      f"MLB 戰績前三:{seg}</div>")
    if tennis.get("tournaments") or tennis.get("results"):
        t_inner = []
        if tennis.get("tournaments"):
            seg = "　|　".join(
                f"{htmllib.escape(t['name'])}"
                + (f"（{htmllib.escape(t['status'])}）" if t.get("status") else "")
                for t in tennis["tournaments"])
            t_inner.append(f"<div style='font-size:12px;color:#475569;line-height:1.7;'>{seg}</div>")
        if tennis.get("results"):
            seg = "".join(
                f"<div style='font-size:12px;color:#334155;line-height:1.7;'>"
                f"<span style='color:#94a3b8;'>{htmllib.escape(r['tour'])}</span>　"
                + (f"<span style='color:#b45309;'>[{htmllib.escape(r['tier'])}]</span> "
                   if r.get("tier") else "")
                + f"<b>{htmllib.escape(r['winner'])}</b> 勝 {htmllib.escape(r['loser'])}</div>"
                for r in tennis["results"])
            t_inner.append(seg)
        blocks.append(
            "<div style='margin:8px 0;'><b style='color:#0f172a;'>網球 ATP / WTA</b>"
            + "".join(t_inner)
            + "<div style='font-size:11px;color:#94a3b8;'>※ 免費資料源未含逐盤比分</div></div>")
    for label in ("世足", "中華職棒", "網球", "MLB", "NBA"):
        titles = news.get(label) or []
        if not titles:
            continue
        items = "".join(
            f"<li style='margin:3px 0;'>{htmllib.escape(t)}</li>" for t in titles)
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
