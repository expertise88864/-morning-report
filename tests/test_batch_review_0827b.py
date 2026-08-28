# -*- coding: utf-8 -*-
"""2026-08-27 r2 全庫外審(baseline 7dc9922)的兩條 CONFIRMED finding。

P1:備援 cron 的同日冪等讀到**排程事件建立當時**的快照,不是執行當下的
    main —— 主班剛寄成功的證據看不到,於是再寄一封。信收不回來。
P2:制裁受詞只認法域與組織,**公司仍然漏**:`sanctions on NVIDIA` 解析
    不出輝達 → 退回主體簽章 → 又是 `sanction:Oil` 那個生產缺陷。
"""
import datetime as dt
import io
import json
from pathlib import Path

import event_identity as eid
import morning_report as mr
import subject_identity as si

_ROOT = Path(mr.__file__).resolve().parent


def _manifest(tmp_path, name, date_str, delivery):
    f = tmp_path / name
    f.write_text(json.dumps({"date": date_str, "delivery": delivery},
                            ensure_ascii=False), encoding="utf-8")
    return f


# ────────────────────────────── P1 ──────────────────────────────
def test_a_queued_backup_sees_the_primarys_fresh_delivery(tmp_path,
                                                          monkeypatch):
    """**本輪最重要的一條。** 22:47 的備援排程事件把 SHA 釘在 S0(主班還在
    跑、還沒 push);它在佇列裡等到 22:53 才拿到 runner,`actions/checkout`
    檢出的**仍是 S0** —— 工作區的 manifest 停在昨天。主班 22:52 已經寄成功
    並 push 了 S1。只讀工作區的話備援班會判定「今天還沒寄」而**再寄一封**。
    """
    now = dt.datetime(2026, 8, 28, 6, 47, tzinfo=mr.TPE)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    # 工作區 = S0 的舊快照(昨天)
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE",
                        _manifest(tmp_path, "stale.json", "2026-08-27 06:13",
                                  {"attempted": True, "success": True}))
    # origin/main = S1(主班今天剛寄成功)
    fresh = _manifest(tmp_path, "fresh.json", "2026-08-28 06:12",
                      {"attempted": True, "success": True,
                       "run_kind": "schedule"})
    monkeypatch.setenv(mr.FRESH_MANIFEST_ENV, str(fresh))
    verdict = mr.already_delivered_today(now)
    assert "已寄出" in verdict and "origin/main" in verdict, verdict


def test_the_fresh_evidence_path_is_wired_into_the_workflow():
    """證據檔是 workflow 產生的 —— 沒有那一步,守衛永遠讀不到新鮮的那份
    (那條 Python 分支就只是一段永遠不執行的宣稱)。fetch 必須在 job 內
    (= 取得 concurrency 名額之後)才有意義。"""
    wf = io.open(_ROOT / ".github" / "workflows" / "morning-report-a.yml",
                 encoding="utf-8").read()
    assert "git fetch --quiet origin main" in wf
    assert "FETCH_HEAD:state/run_manifest.json" in wf
    i = wf.index("FETCH_HEAD:state/run_manifest.json")
    j = wf.index("FRESH_RUN_MANIFEST:")
    assert i < j, "產生證據的步驟必須排在使用它的步驟之前"


def test_unreadable_fresh_evidence_fails_open(tmp_path, monkeypatch):
    """**模稜兩可時要補寄,不是不寄** —— 備援存在的理由。新鮮那份讀不到
    (沒設定/檔不存在/壞檔)一律照跑。"""
    now = dt.datetime(2026, 8, 28, 6, 47, tzinfo=mr.TPE)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE",
                        _manifest(tmp_path, "stale2.json", "2026-08-27 06:13",
                                  {"attempted": True, "success": True}))
    monkeypatch.delenv(mr.FRESH_MANIFEST_ENV, raising=False)
    assert mr.already_delivered_today(now) == ""          # 沒設定
    monkeypatch.setenv(mr.FRESH_MANIFEST_ENV, str(tmp_path / "nope.json"))
    assert mr.already_delivered_today(now) == ""          # 檔不存在
    bad = tmp_path / "bad.json"
    bad.write_text("{壞掉", encoding="utf-8")
    monkeypatch.setenv(mr.FRESH_MANIFEST_ENV, str(bad))
    assert mr.already_delivered_today(now) == ""          # 壞檔
    # 讀取本身炸了也要照跑(不是拋出去把整班弄死)
    assert mr.already_delivered_today(
        now, fresh_loader=lambda: 1 / 0) == ""


def test_the_working_tree_evidence_still_counts(tmp_path, monkeypatch):
    """新鮮那份讀不到、但工作區自己就說今天寄過了 —— 那是**正面證據**,
    照樣要擋(否則新加的來源反而把舊的擋信能力弄丟)。"""
    now = dt.datetime(2026, 8, 28, 6, 47, tzinfo=mr.TPE)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE",
                        _manifest(tmp_path, "today.json", "2026-08-28 06:12",
                                  {"attempted": True, "success": True}))
    monkeypatch.delenv(mr.FRESH_MANIFEST_ENV, raising=False)
    verdict = mr.already_delivered_today(now)
    assert "已寄出" in verdict and "工作區" in verdict, verdict
    # 手動觸發永遠不被擋(使用者的救援管道)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    assert mr.already_delivered_today(now) == ""


# ────────────────────────────── P2 ──────────────────────────────
def test_a_sanctioned_company_is_the_object_not_the_affected_asset():
    """`sanction:Oil` 那個生產缺陷的**公司版**。法域(伊朗)與組織(ICC)
    前兩輪已修,公司這一半還在:受影響的資產又被當成制裁對象。"""
    assert eid.action_object(
        "sanction", "Oil falls after U.S. sanctions on NVIDIA",
        ["Oil"]) == "輝達"
    assert eid.action_object(
        "sanction", "Gold falls after sanctions on TSMC",
        ["Gold"]) == "台積電"
    # 前兩輪修好的不得回退
    assert eid.action_object(
        "sanction", "Oil Falls Despite Fresh U.S. Sanctions on Iran",
        ["Oil"]) == "伊朗"
    assert eid.action_object(
        "sanction", "Oil falls after sanctions on the ICC",
        ["Oil"]) == "國際刑事法院"
    # 不是制裁的新聞照舊走主體簽章(沒有把受詞剖析硬套到所有動作上)
    assert eid.action_object(
        "sanction", "Oil rises on supply worries", ["Oil"]) == "Oil"


def test_the_identity_authority_covers_all_three_families():
    """身分表是**一份**、而且三個家族都在 —— 任何一張表 import 失敗時
    這裡要紅。靜默少一家族的症狀是「制裁受詞解析安靜地失效」,退回主體
    簽章正是缺陷本身,生產上看不出來。"""
    t = si.declared_targets()
    assert t.get("iran") == "伊朗"                     # 法域
    assert t.get("icc") == "國際刑事法院"               # 組織
    assert t.get("nvidia") == "輝達"                   # 公司
    assert t.get("tsmc") == "台積電"
    assert len(t) >= 150, len(t)                       # 防空集合真空通過
    # **股票代號不收**:新聞寫制裁對象不會寫代號,而純數字在英文句子裡
    # 到處都是 —— 收進來只會製造誤判。
    assert "2330" not in t and "2610" not in t


def test_changing_the_identity_formula_bumps_the_schema_version():
    """公式改了要跳版,否則既有 state 的舊鍵不會被遷移 —— 同一件事會裂成
    兩條線(舊鍵 `sanction:Oil` 與新鍵 `sanction:輝達` 各自延燒)。"""
    assert eid.IDENTITY_SCHEMA_VERSION >= 14


def test_the_sanction_resolver_does_not_maintain_its_own_identity_table():
    """外審的架構要求:消費端只消費身分權威,不自維一份。三處各補一次
    的結果就是「補一張漏一張」(法域→組織→公司,連三輪)。"""
    src = io.open(_ROOT / "event_identity.py", encoding="utf-8").read()
    i = src.index("def sanction_target(")
    body = src[i:i + 2500]
    assert "declared_targets" in body
    assert "_ORG_ALIASES" not in body, "又伸手進別的模組的私有表了"


# ───────────────────────── r3 外審 ─────────────────────────
def test_a_delivered_run_whose_state_never_published_still_blocks(
        tmp_path, monkeypatch):
    """r3 外審 P1:**信寄出去了,但 state 沒發佈成功。**

    run_manifest 帶著 delivery 沒錯,可是它跟 history/timeline/ledger 同一批
    commit,要等 state schema 契約通過才 push —— 契約失敗、或 push 撞
    GitHub 5xx 時,origin/main 上**沒有任何證據**,備援班於是再寄一封。
    收據是 SMTP 成功當下就獨立發佈的那一份,只有它答得出來。
    """
    now = dt.datetime(2026, 8, 28, 6, 47, tzinfo=mr.TPE)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    # 工作區與 origin/main 的 manifest 都停在昨天(整批 state 沒發佈)
    stale = _manifest(tmp_path, "stale3.json", "2026-08-27 06:13",
                      {"attempted": True, "success": True})
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", stale)
    monkeypatch.setenv(mr.FRESH_MANIFEST_ENV, str(stale))
    # 收據卻已經在 main 上
    receipt = _manifest(tmp_path, "receipt.json", "2026-08-28 06:12",
                        {"attempted": True, "success": True,
                         "run_kind": "schedule"})
    monkeypatch.setenv(mr.FRESH_RECEIPT_ENV, str(receipt))
    verdict = mr.already_delivered_today(now)
    assert "已寄出" in verdict and "收據" in verdict, verdict


def test_the_receipt_is_published_the_moment_delivery_is_conclusive(
        tmp_path, monkeypatch):
    """收據要在**有結論的當下**寫出來,而且中間狀態不算結論(一班最多推
    一次)。形狀與 run_manifest 相同,好讓守衛用同一個判準讀三個來源。"""
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", tmp_path / "m.json")
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "r.json")
    (tmp_path / "m.json").write_text(
        json.dumps({"date": "2026-08-28 06:12"}), encoding="utf-8")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)   # 本機只寫檔
    mr._mark_delivery_in_manifest(attempted=True)         # 中間狀態
    assert not (tmp_path / "r.json").exists(), "attempted 還不是結論"
    mr._mark_delivery_in_manifest(attempted=True, success=True)
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert data["date"] == "2026-08-28 06:12"
    assert data["delivery"]["success"] is True
    now = dt.datetime(2026, 8, 28, 6, 47, tzinfo=mr.TPE)
    assert "已寄出" in mr._manifest_delivery_verdict(data, now)


def test_the_receipt_is_wired_into_the_workflow():
    wf = io.open(_ROOT / ".github" / "workflows" / "morning-report-a.yml",
                 encoding="utf-8").read()
    assert "FETCH_HEAD:state/delivery_receipt.json" in wf
    assert wf.index("FETCH_HEAD:state/delivery_receipt.json") < wf.index(
        "FRESH_DELIVERY_RECEIPT:")


def test_a_ticker_alone_does_not_name_the_sanctioned_company():
    """r3 外審 P2:**我自己新開的洞。** 為了認得 NVIDIA 而把整張公司別名表
    接進受詞,連股票代號與市場縮寫一起收 —— `MTD` 同時是 month-to-date,
    於是「oil is up 8% MTD」被讀成制裁梅特勒-托利多,而且遷移會用同一個
    判準改寫歷史鍵。"""
    assert eid.action_object(
        "sanction", "New sanctions on exporters; oil is up 8% MTD",
        ["Oil"]) == "Oil"
    assert eid.action_object(
        "sanction", "U.S. sanctions on exporters, AAPL flat",
        ["Oil"]) == "Oil"
    # 公司**名字**照樣認得(判準是宣告,不是「全大寫就丟掉」——
    # 用形狀推導會把 TSMC/AMD/ASE 一起丟掉,那正是這批要修好的能力)
    assert eid.action_object("sanction", "sanctions on TSMC", ["Oil"]) == "台積電"
    assert eid.action_object("sanction", "sanctions on AMD", ["Oil"]) == "超微"


def test_every_ascii_alias_is_classified_as_name_or_market_code():
    """新增一家帶代號的公司時,這條要**紅**在作者面前 —— 否則同一個洞會
    安靜地重開(這正是 r3 P2 的成因:我加表時沒有逐個分類)。"""
    import entity_alias as ea
    ascii_aliases = {a for g in ea.ALIAS_GROUPS for a in g
                     if str(a).isascii() and str(a).isalpha()}
    plain = {a for a in ascii_aliases if not ea.is_market_code(a)
             and a not in ea.CONTEXT_DEPENDENT_ALIASES}
    assert plain == {
        "AMD", "ASE", "ASUS", "FOMC", "Fed", "Foxconn", "MediaTek",
        "Microsoft", "NVIDIA", "Nvidia", "TSMC", "UMC", "Wiwynn",
    }, sorted(plain)
    assert ea.is_market_code("MTD") and ea.is_market_code("2330")
    assert not ea.is_market_code("TSMC")


def test_spelling_alone_never_proves_company_identity():
    """r4→r5 連兩輪同一個病。r3 我把整張公司表接進受詞,`intel` 變成英特爾;
    r4 我加了一層「看形」(要在受詞開頭、且拼寫是公司宣告的那個形),
    r5 外審指出**方向本身就錯**:英文標題本來就寫 Title Case,
    「Sanctions on Intel Sharing With Allies」的 `Intel` 完全合規 ——
    大小寫在這裡不帶任何身分資訊。那一層已整個拆掉,這一族不當受詞。

    兩種錯誤的代價不對稱:誤判在時間軸留下一條假的公司線(正是
    `sanction:Oil` 那個缺陷),漏判只是退回主體簽章 —— 今天的行為。
    """
    for title in ("Sanctions on Intel Sharing With Allies",
                  "New sanctions on military intel chiefs",
                  "U.S. sanctions on Intel",
                  "Sanctions on Apple Exports From Chile",
                  "sanctions on Amazon deforestation loggers",
                  "sanctions on Amazon"):
        assert eid.action_object("sanction", title, ["Oil"]) == "Oil", title
    # **中文形一起排除**(r6 外審):我先前把「美國制裁亞馬遜 → 亞馬遜
    # (公司)」寫進測試當正確答案 —— 中文的亞馬遜一樣是雨林、蘋果一樣是
    # 水果,而中文這半連大小寫都沒得看,CJK 比對也不要求在受詞開頭
    # (「制裁範圍廣達十國」的「廣達」)。只排除英文形等於只修一半。
    for title in ("美國制裁亞馬遜雨林非法伐木業者", "美國制裁蘋果進口",
                  "美國制裁範圍廣達十國", "美國制裁亞馬遜"):
        assert eid.action_object("sanction", title, ["Oil"]) == "Oil", title
    # 純音譯/專名沒有這個問題,照樣指得到那家公司
    assert eid.action_object("sanction", "美國制裁英特爾", ["Oil"]) == "英特爾"
    assert eid.action_object("sanction", "美國制裁台積電", ["Oil"]) == "台積電"
    # 沒有撞名疑慮的公司名照樣認得(整批沒有被一起丟掉)
    assert eid.action_object("sanction", "sanctions on TSMC", ["Oil"]) == "台積電"
    assert eid.action_object(
        "sanction", "sanctions on NVIDIA", ["Oil"]) == "輝達"


def test_every_sibling_of_an_excluded_alias_has_a_recorded_decision():
    """排除某一形時,同組的其他形要**逐個決定**,不是預設沿用 ——
    r6 外審抓到我只排了英文 `Amazon`,中文 `亞馬遜`(一樣是雨林)還留在
    受詞表裡。歧義是**字面**的性質不是實體的(`Micron` 是長度單位、
    `美光` 不是),所以判準不能是「一個排就全排」,而是「每一個都要有
    記錄下來的決定」。少記一個,這裡就紅在作者面前。"""
    import entity_alias as ea
    decided = (ea.CONTEXT_DEPENDENT_ALIASES | ea.SIBLINGS_REVIEWED_AND_KEPT)
    for group in ea.ALIAS_GROUPS:
        if not any(a in ea.CONTEXT_DEPENDENT_ALIASES for a in group):
            continue
        undecided = [a for a in group
                     if a not in decided and not ea.is_market_code(a)]
        assert not undecided, f"{group} 裡的 {undecided} 沒有記錄過決定"
    # 清單本身不得放進沒有同組被排除的形(擺著看的清單會腐爛)
    for a in ea.SIBLINGS_REVIEWED_AND_KEPT:
        grp = [g for g in ea.ALIAS_GROUPS if a in g]
        assert grp and any(x in ea.CONTEXT_DEPENDENT_ALIASES for x in grp[0]), a




# ───────────────────────── r7 外審 ─────────────────────────
def _git(*args, cwd, **kw):
    import subprocess
    # `text=True` 會走 host 的地區編碼(Windows 實測 gbk):git 回顯中文
    # commit 訊息時解碼失敗,stdout 變 None 而 returncode 仍是 0。
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       encoding="utf-8", errors="replace", timeout=60, **kw)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


def test_publishing_the_receipt_cannot_drag_unvetted_state_onto_main(tmp_path):
    """r7 外審 P1:**收據不可以順帶把還沒過契約的 state 推上 main。**

    `STATE_PUSH_DEFERRED=1` 那道閘門的全部意義是「信可以先寄,但壞掉的
    state 不准進 main」。第一版收據走的是 `git add`→`commit`→
    `push_committed_state()` —— 而 `git push` 推的是分支 HEAD,git 不可能
    只推 C 不推它的祖先 B。今天的呼叫順序剛好讓收據先發生,但那是**順序
    的巧合、不是原語的不變量**:誰把 persist 往前挪,保護就無聲消失。

    這條測試把危險序列直接做出來:本機已經有一個「壞掉的 state commit」
    尚未推,然後發佈收據 —— 遠端必須只看得到收據。
    """
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    _git("clone", str(remote), str(work), cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "state").mkdir()
    (work / "state" / "keep.json").write_text("{}", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "base", cwd=work)
    _git("push", "origin", "main", cwd=work)

    # 本機:尚未通過 schema 契約的 state commit(還沒推)
    (work / "state" / "broken_state.json").write_text(
        "{壞掉的 schema}", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "deferred state (契約還沒跑)", cwd=work)

    # 發佈收據
    receipt = work / "state" / "delivery_receipt.json"
    receipt.write_text(json.dumps(
        {"date": "2026-08-28 06:12",
         "delivery": {"success": True, "run_kind": "schedule"}},
        ensure_ascii=False), encoding="utf-8")
    assert mr.publish_receipt_from_remote_base(receipt, cwd=work) is True

    files = _git("ls-tree", "--name-only", "-r", "main", cwd=remote).split()
    assert "state/delivery_receipt.json" in files, files
    assert "state/broken_state.json" not in files, (
        "收據把還沒過契約的 state 一起帶上 main 了", files)
    # 收據內容真的在遠端(不是只有檔名)
    body = json.loads(_git("show", "main:state/delivery_receipt.json",
                           cwd=remote))
    assert body["delivery"]["success"] is True
    # 本機 HEAD 沒有被動到(發佈不碰工作區的歷史)
    assert "deferred state" in _git("log", "-1", "--pretty=%s", cwd=work)
    # 內容沒變就不重複推
    assert mr.publish_receipt_from_remote_base(receipt, cwd=work) is False


def test_a_short_ticker_never_swallows_a_jurisdiction():
    """外審建議永久保留的負向案例。短代號是身分系統很容易再踩到的
    regression class:`sanctions on Iran` 的 `on`、`categories` 的 `cat`。
    這兩個代號**今天不在表裡**,所以這條現在是**柵欄**不是缺陷測試 ——
    將來有人把 ON Semiconductor / Caterpillar 加進來時它會紅。"""
    assert eid.action_object(
        "sanction", "U.S. imposes sanctions on Iran", ["Oil"]) == "伊朗"
    t = si.declared_targets()
    for word in ("on", "cat", "all", "for", "one", "run", "so"):
        assert word not in t, f"{word} 進了受詞表 —— 它是常見英文字"


def test_an_untracked_receipt_does_not_wedge_the_later_state_push(tmp_path):
    """r8 外審:**收據推上遠端之後,本機那份還是 untracked。**

    之後整批 state 要推時是 non-fast-forward → `push_state.sh` 走
    `git pull --rebase --autostash`,而 **autostash 不含 untracked 檔**
    —— git 拒絕用遠端版本蓋掉本機那個未追蹤的同名檔,重試全滅,
    通過了契約的 state 反而發佈不出去、job 變紅。

    這條先證明「危險真的存在」(未追蹤時 rebase 會被拒),再證明修法
    (收據一起 commit)之後整批 state 推得上去。
    """
    def _mk():
        remote = tmp_path / f"r{_mk.n}.git"
        work = tmp_path / f"w{_mk.n}"
        _mk.n += 1
        _git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
        _git("clone", str(remote), str(work), cwd=tmp_path)
        _git("config", "user.email", "t@t", cwd=work)
        _git("config", "user.name", "t", cwd=work)
        (work / "state").mkdir()
        (work / "state" / "keep.json").write_text("{}", encoding="utf-8")
        _git("add", "-A", cwd=work)
        _git("commit", "-m", "base", cwd=work)
        _git("push", "origin", "main", cwd=work)
        receipt = work / "state" / "delivery_receipt.json"
        receipt.write_text(json.dumps({"date": "2026-08-30 07:10",
                                       "delivery": {"success": True}}),
                           encoding="utf-8")
        mr.publish_receipt_from_remote_base(receipt, cwd=work)
        (work / "state" / "run_manifest.json").write_text(
            "{}", encoding="utf-8")          # 週日那批 state
        return work
    _mk.n = 0
    import subprocess

    def _push_after_rebase(work, paths):
        _git("add", *paths, cwd=work)
        _git("commit", "-m", "weekend state", cwd=work)
        r = subprocess.run(["git", "pull", "--rebase", "--autostash"],
                           cwd=work, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=60)
        if r.returncode != 0:
            return r
        return subprocess.run(["git", "push"], cwd=work, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60)

    # ① 收據沒被 commit(修正前的週日路徑)→ rebase 被 untracked 檔擋住
    bad = _push_after_rebase(_mk(), ["state/run_manifest.json"])
    assert bad.returncode != 0, "危險不存在的話這條測試就沒有在量東西"
    assert "untracked" in (bad.stderr + bad.stdout).lower(), bad.stderr

    # ② 收據一起 commit(修正後)→ 整批 state 推得上去
    ok = _push_after_rebase(
        _mk(), ["state/run_manifest.json", "state/delivery_receipt.json"])
    assert ok.returncode == 0, ok.stderr


def test_both_sunday_paths_commit_the_receipt():
    """兩條週日路徑的清單都是**寫死列舉**的(不是 `_state_push_paths()`),
    所以漏一條就等於漏一整條路徑 —— 用原始碼確認兩處都列了。"""
    src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    # **錨點要唯一**:第一版用 `str(EMAIL_ARCHIVE_DIR)`,而 `src.index` 命中的
    # 是更前面 `_state_push_paths()` 裡的那一處 —— 窗口裡剛好也有收據,
    # 於是把第二條週日清單刪掉這條測試照樣綠(突變驗證才發現)。
    for anchor in ('f"chore: weekend no-content manifest "',
                   "§B:週末信件存檔一併 push"):
        assert src.count(anchor) == 1, f"錨點不唯一:{anchor}"
        i = src.index(anchor)
        seg = src[max(0, i - 900):i + 200]
        assert "DELIVERY_RECEIPT_FILE" in seg, anchor
