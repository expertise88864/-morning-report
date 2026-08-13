#!/usr/bin/env bash
# **state push 的唯一重試政策**(2026-08-13 Podcast Digest #235)。
#
# 那一班的 commit 做好了,push 收到 GitHub 的 500:
#
#   remote: Internal Server Error
#   ! [remote rejected] main -> main (Internal Server Error)
#
# 這是 GitHub 端的暫時性故障,不是我們的程式碼 —— 而**沒有重試**的後果
# 是那一天的 state 整個掉了(podcast digest 得整批重轉、晨報的去重基準
# 消失)。這個 repo 記過同一形狀:「本機與當班都寫成功,但 state 沒被
# push,隔天 runner 看不到」。
#
# 兩種失敗要同一套處理,因為 push 當下分不出是哪一種:
#   * **競寫**(另一個 workflow 剛推了東西)→ rebase 之後就會過;
#   * **暫時性 5xx**(這次)→ 隔幾秒再試就會過。
# 所以每一輪都做「fetch + rebase + 等一下 + 再推」。
#
# 政策寫在這裡而不是各 workflow 各寫一份:先前四個呼叫點有四種寫法
# (兩個完全沒有重試),而「改了一份、另外三份還留著舊的」是這個 repo
# 反覆記過的失效形狀。
set -euo pipefail

ATTEMPTS="${PUSH_STATE_ATTEMPTS:-3}"
# 退避秒數:第 1 次失敗等 5 秒、第 2 次等 15 秒。**有界**(總共 +20 秒)
# —— 這些 workflow 都有 job timeout,重試不得把它撞掉。
# 可由環境變數覆寫(job timeout 更緊的呼叫端、以及**測試**要跑得動
# 這個迴圈本身:只驗文字的合約測試證明不了它會重試)。
read -r -a SLEEPS <<< "${PUSH_STATE_SLEEPS:-5 15 30}"

for ((i = 1; i <= ATTEMPTS; i++)); do
  if git push; then
    echo "[push_state] pushed (attempt ${i}/${ATTEMPTS})"
    exit 0
  fi
  if [[ "${i}" -ge "${ATTEMPTS}" ]]; then
    break
  fi
  nap="${SLEEPS[$((i - 1))]:-30}"
  echo "[push_state] push 失敗(第 ${i} 次)—— fetch/rebase 後等 ${nap}s 再試" >&2
  # rebase 失敗不要當場死:可能只是這一輪沒東西可 rebase(5xx 的情況),
  # 下一輪的 push 仍然值得試。真的推不上去由迴圈結束時的 exit 1 說話。
  git fetch origin || true
  git pull --rebase --autostash || true
  sleep "${nap}"
done

# **推不上去要紅**(不得吞掉保持綠燈):state 沒發佈,而下一班會用舊的。
echo "::error::state push 連續 ${ATTEMPTS} 次失敗 —— 這一班的 state 沒有發佈" >&2
exit 1
