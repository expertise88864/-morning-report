"""世界大事的嚴重性分級(純函式,只看標題;無網路、不改權威欄位)。

使用者 2026-11-02 看了當天晨報:「這邊真的有包含世界大事嗎?足夠嚴重、有代表性的世界大事」——
那天七之二放了阿拉巴馬州球賽外槍擊(1 死 8 傷)與宜蘭外海震度 3 級地震,而同一天的素材裡有
伊朗軍方警告、OPEC+ 產量決議、美加貿易談判、幣安遭駭 63 億美元。舊的取材是「抓到的順序」,
沒有任何嚴重性排序。這裡只決定**先後**,不刪任何一則(取捨仍由寫稿端依規則判斷)。
"""
from __future__ import annotations

import re

#: 影響範圍跨國的事件:戰爭與軍事、大國外交與制裁、能源與航道、貿易政策、政變與大選、重大金融事件。
_SEVERE = re.compile(
    r"戰爭|開戰|戰事|軍方|軍事|飛彈|空襲|轟炸|停火|制裁|核武|政變|大選|峰會|"
    r"OPEC|荷姆茲|原油|關稅|貿易戰|出口管制|遭駭|駭客|違約|"
    r"\b(?:war|missiles?|airstrikes?|ceasefire|sanctions?|coup|tariffs?|hack(?:ed)?)\b", re.I)
#: 地方性的小事件:治安、示威、無災損的有感地震。
_MINOR = re.compile(
    r"槍擊|槍響|搶劫|車禍|示威|抗議|遊行|有感|震度[^0-9]{0,8}[1-4]\s*級|規模\s*[1-5]\.\d|"
    r"\b(?:shootings?|gun violence|protests?)\b", re.I)
#: 死傷規模大到不再是地方小事(死或傷數十人以上;「1死50傷」也算)。
_MASS = re.compile(r"(?:\d{2,}|數十|上百|數百)\s*人?\s*(?:死|傷|罹難|喪生|受傷)"
                   r"|\b\d{2,} (?:dead|killed|injured|wounded)\b", re.I)


def rank(title) -> int:
    """2 = 跨國重大、1 = 一般、0 = 地方小事(大規模死傷除外)。"""
    t = str(title or "")
    if _SEVERE.search(t):
        return 2
    if _MINOR.search(t) and not _MASS.search(t):
        return 0
    return 1
