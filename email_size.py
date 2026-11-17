"""信件超過約 100KB 那天才精簡體育快訊(使用者 2026-11-17 選 b)。

11/17 實信 104.7KB,超過 Gmail ~102KB 剪信線,信末被摺疊。使用者不要每天都縮短,只要超量那天自動精簡:
NBA 賽程 8 → 4 場、各項體育新聞 3 → 2 條。不移除任何區塊;精簡後仍超量就照寄(與原本 full 模式相同)。
大小量的是寄出去的那份(`display_pass` 之後),與 Gmail 量的一致。純函式,不做網路呼叫。
"""
from __future__ import annotations

LIMIT_KB = 100.0
NBA_FIXTURES = 4
NEWS_PER_LABEL = 2


def over(kb: float) -> bool:
    return kb > LIMIT_KB


def compact_sports(sports: dict) -> dict:
    """回傳精簡後的副本(原資料不動):NBA 賽程只留前 4 場要顯示的、每項新聞只留前 2 條。"""
    if not isinstance(sports, dict):
        return sports
    out = dict(sports)
    fixtures = sports.get("nba_fixtures")
    if isinstance(fixtures, list):
        shown, kept = 0, []
        for game in fixtures:
            hidden = isinstance(game, dict) and game.get("phase") == 1   # 渲染端本來就不顯示
            if hidden or shown < NBA_FIXTURES:
                kept.append(game)
                shown += 0 if hidden else 1
        out["nba_fixtures"] = kept
    news = sports.get("news")
    if isinstance(news, dict):
        out["news"] = {k: (v[:NEWS_PER_LABEL] if isinstance(v, list) else v) for k, v in news.items()}
    return out
