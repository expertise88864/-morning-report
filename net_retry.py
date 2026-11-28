"""逾時的同一天再試一次。期交所偶發讀取逾時,原本直接退回前一天的舊值
(11-28 實信:外資台指期 -106,275 口連兩天都是 11/26 的數字)。"""
import requests


def retry_timeout(call, timeouts=(15, 45)):
    """`call(timeout)` 逾時或連線錯誤時,以較長的逾時再試;最後一次的例外照常拋出。"""
    for i, t in enumerate(timeouts):
        try:
            return call(t)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if i == len(timeouts) - 1:
                raise
            print(f"[retry] {t}s 逾時,以 {timeouts[i + 1]}s 再試一次: {type(exc).__name__}", flush=True)
    return None
