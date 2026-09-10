"""Conservative reader projection and explicit incomplete-watch protection."""
import re

WRITING = """
- 今日結論合計約150–250中文字；只留立場、最重要理由及主要風險。操作清單、
  樣板組合曝險、詳細失效條件放後段，不用「計分落在」等內部計算措辭。
- 時間因果必須正確：台北晚間公布數據只能影響夜盤與下一交易日，不能倒過來
  決定當天13:30前台股走勢；公布前只能說預期影響。ECB決策與記者會分開引用日曆。
- 財政部回購須區分流動性支持、現金管理與Fed貨幣政策；公告上限不等於實際成交。
  殖利率上漲不能單獨證明回購失敗、通膨主導或Fed必須升息。沒有估值模型或來源，
  不編造「利率升50bp使估值跌3–5%」等彈性，也不自創數據對升息機率的精確換算。
- 前日跌幅與次日漲幅按複利比對，不能因反彈就說完全收復；1.46+0.59是2.05，
  不把合計寫2.2。歷史來源日期與事件發生月份分開，不能把舊報導日期當新事件日期。
- 複合觀察條件只有部分完成時維持not_triggered並說明已完成/仍待驗證兩部分；
  不得因新品發布就把尚無備貨證據的整條觀察結案。市場機率有當期數字時須引用，
  不同來源的機率不能混稱同一序列；沒有數字要指明缺的是哪個來源。
- 未參與、無直接影響的公司不要硬填affected_assets；用空清單或產業層級敘述。
  散裝BDI、油輪BDTI、貨櫃SCFI分開，不用散裝指數直接證明貨櫃營收或獲利。
- taiwan_local不要再重述top_news_analysis已完整分析的同一來源事件；若有新增事實，
  放回該新聞分析。官方例行採購不等於新擴產決策，金額重大性與新資訊優先於官方標籤。
"""


def watch_status(row: dict) -> str:
    status = str(row.get('status') or '')
    text = str(row.get('what_happened') or '')
    partial = re.search(r'(?<![不非是])(?:只觸發.{0,35}部分|僅觸發.{0,35}部分|只(?:有|完成)部分|'
                        r'僅部分(?:成立|完成|觸發)|尚未全部(?:成立|完成|觸發))', text)
    return 'not_triggered' if status == 'triggered' and partial else status


def conclusion(sections: list[str], obj: dict) -> str:
    """Move supporting paragraphs intact, never truncate risks or alter JSON."""
    from reader_editorial import OUTLOOK_HEADING
    sections = list(sections)
    support = []
    pf_summary = str((obj.get('portfolio_implications') or {}).get('summary') or '').strip()
    for i, section in enumerate(sections):
        if not section.startswith('## 我的明確立場\n'):
            continue
        head, _, body = section.partition('\n')
        body = re.sub(r'系統計分[^。\n]*。', '', body)
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        contract = [line for line in lines if line.startswith(('立場：', '淨分 '))]
        prose = []
        for line in lines:
            if line in contract:
                continue
            if line.startswith(('可考慮的做法:', '風險:', '失效條件:')) or (pf_summary and line == pf_summary):
                support.append(line)
            else:
                prose.append(line)
        sections[i] = head + '\n' + '\n'.join(contract) + '\n\n' + '\n\n'.join(prose) + '\n\n'
    if support:
        heading = '## ' + OUTLOOK_HEADING + '\n'
        index = next((i for i, section in enumerate(sections) if section.startswith(heading)), None)
        extra = '\n\n'.join(support) + '\n\n'
        if index is None:
            sections.insert(next((i for i, s in enumerate(sections)
                                  if s.startswith('## 我的明確立場\n')), len(sections)), heading + extra)
        else:
            sections[index] = sections[index].rstrip() + '\n\n' + extra
    return ''.join(sections)


def watch_reviews(obj: dict) -> dict:
    reviewed = {}
    for row in obj.get('watch_review') or []:
        if isinstance(row, dict) and str(row.get('watch_id') or ''):
            reviewed[str(row['watch_id'])] = watch_status(row)
            if watch_status(row) != row.get('status'):
                print('::warning::watch_partial_retained', flush=True)
    return reviewed
