"""`as_of` 的归属 —— 一个「看起来板上钉钉」的错值。

事故（2026-09-21，`BIGA-20260921-017`）
----------------------------------------
周一 12:41 出的卡，`market` 那行证据写着::

    [market] 上涨家数 = 4385   as_of 09-18 15:00   src em:push2delay/ulist.np

**4385 是当天此刻的数**，却挂着上周五收盘的时间戳。

两层原因：

1. `as_of` 只从日线的 `trade_date` 算**一次**，然后被所有证据共用。
   而涨跌家数、板块榜这两个端点**连日期字段都没有** ——
   我们能诚实声明的只有「取回它的时刻」。
2. 代码其实知道（它发了 warning），但 `card.render()` **从不渲染 warnings**，
   于是卡面上看起来板上钉钉。

⚠️ 注意这里的不对称：带日期的源（腾讯行情）会被核对、不一致就报
`date_mismatch`；**唯独没有日期的那个源反而被默认对齐** ——
而它恰恰是最可能对不上的。
"""

from __future__ import annotations

import ast
import pathlib
from datetime import timedelta

REPO = pathlib.Path(__file__).resolve().parents[1]

from _contract import (  # noqa: E402
    STANCE_VOCAB,
    AgentVerdict,
    DecisionCard,
    Evidence,
    now_cn,
)
from _roster import absent_registrations  # noqa: E402

#: 无日期字段的实时端点 → 它喂出来的字段必须走 `add_live`。
#:
#: ⚠️ 只列**字面量**字段名。f-string 拼出来的（`add_live(f"{tag}_top"`）
#:    AST 扫不到，由下面 `test_f_string注册的也要走add_live` 单独兜住。
_LIVE_FIELDS = {
    "skills/market-calc/scripts/market_calc.py": {
        "advance_count", "decline_count", "flat_count", "advance_ratio"},
    "skills/sector-calc/scripts/sector_calc.py": {
        "industry_bottom", "main_inflow_top", "main_inflow_total_yi",
        "board_counts"},
}


def _registered(path: pathlib.Path, fn: str) -> set[str]:
    """用 `fn(...)` 登记了哪些字面量字段。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
        if name == fn and node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                out.add(a0.value)
    return out


class TestLiveFieldsUseLiveAsOf:
    """无日期端点来的字段，`as_of` 必须是取回时刻。"""

    def test_它们都走add_live(self):
        bad = {}
        for rel, fields in _LIVE_FIELDS.items():
            live = _registered(REPO / rel, "add_live")
            missed = fields - live
            if missed:
                bad[rel] = sorted(missed)
        assert not bad, (
            f"这些字段来自**无日期**端点，却用了日线推断的 as_of：{bad}\n"
            "  后果：一个今天的数挂着上一个交易日的时间戳，\n"
            "        而卡面上看起来板上钉钉（BIGA-20260921-017 真出过）。\n"
            "  怎么办：改用 add_live()，as_of = 取回时刻。")

    def test_f_string注册的也要走add_live(self):
        """`sector` 的 `{tag}_top` / `{tag}_advance_ratio` 用 f-string 拼名字，
        AST 扫不到字面量 ⇒ 改成断言**源码里没有残留的 `add(f"`**。

        这对 `sector` 成立，因为它所有 f-string 注册的字段都来自板块榜
        （无日期端点）。`market` 不成立 —— 它的 `{key}_close` 来自日线，
        所以这条只管 `sector`。
        """
        src = (REPO / "skills/sector-calc/scripts/sector_calc.py").read_text(
            encoding="utf-8")
        assert 'add(f"' not in src, (
            "sector 里还有用 add(f\"…\") 注册的字段 —— 它们全部来自板块榜"
            "（无日期端点），必须走 add_live")

    def test_日线派生的字段不要误用add_live(self):
        """反向：真正来自日线的字段，as_of 就该是那根 K 的收盘时刻。"""
        live = _registered(REPO / "skills/market-calc/scripts/market_calc.py",
                           "add_live")
        for f in ("trade_date", "sh_close", "sh_pct"):
            assert f not in live, f"{f} 来自日线，as_of 不该是取回时刻"


class TestCardRendersWarnings:
    """warning 与 missing 的分工是「能用但要注意」vs「没有」。

    把前者藏起来，等于只保留了它的名字。
    """

    @staticmethod
    def _card(warnings: list[str]) -> DecisionCard:
        t = now_cn()
        v = AgentVerdict(
            agent="market", task_id="BIGA-20260921-001", status="completed",
            verdict="PASS", stance="分化",
            result={"trade_date": "2026-09-18"},
            evidence=[Evidence(field="trade_date", value="2026-09-18",
                               source="probe", as_of=t, retrieved_at=t)],
            warnings=warnings)
        # 🔴 只有 1 个 agent，另外 5 个天然缺席——F-8 之后 roster 判据按
        #    计数比较，5 条占位才够（本测试类不测 roster）。
        return DecisionCard(decision_id="BIGA-20260921-001", status="WAIT",
                            headline="h", verdicts=[v], synthesis="s", model_ref="m",
                            missing=absent_registrations([v]))

    def test_warning_出现在卡面上(self):
        text = self._card(["涨跌家数接口不返回交易日字段"]).render()
        assert "提请注意" in text
        assert "涨跌家数接口不返回交易日字段" in text

    def test_没有warning时不占版面(self):
        assert "提请注意" not in self._card([]).render()


class TestMixedAsOfIsVisible:
    """同一个 Agent 的证据可以横跨两个时刻 —— 那时它必须看得出来。"""

    def test_两个as_of并存且都能渲染(self):
        now = now_cn()
        old = now - timedelta(days=3)
        v = AgentVerdict(
            agent="market", task_id="BIGA-20260921-001", status="completed",
            verdict="PASS", stance="分化",
            result={"sh_close": 3911.87, "advance_count": 4385},
            evidence=[
                Evidence(field="sh_close", value=3911.87, source="sina:kline",
                         as_of=old, retrieved_at=now, label="上证收盘"),
                Evidence(field="advance_count", value=4385, source="em:ulist",
                         as_of=now, retrieved_at=now, label="上涨家数"),
            ])
        text = DecisionCard(decision_id="BIGA-20260921-001", status="WAIT",
                            headline="h", verdicts=[v], synthesis="s", model_ref="m",
                            missing=absent_registrations([v])).render()
        assert old.strftime("%m-%d %H:%M") in text
        assert now.strftime("%m-%d %H:%M") in text, \
            "两个 as_of 必须都出现在卡上 —— 否则读的人以为它们是同一时刻"


# ══ 外部评审 P1-1：Decision Identity ═════════════════════════════
#
# 评审指出：`synthesize.py` 有「所有 verdict 的 task_id 必须一致」的检查，
# 但**契约层没有** ⇒ 可以构造「卡 001 装着 999 的 verdict」并落库（实测通过）。
#
# 守卫在编排层就只守得住走编排层的那条路。
#
# ⚠️ 评审建议的验收是「Replay 也无法绕过」，但实测已落库 31 张卡里有 20 张
#    的 verdict 写着别的号（Stage 0 占号是后来才加的）。一刀切会让它们永远读不出来。
#    ⇒ 改成三段式：新卡拒绝 / 旧卡可读但显示 / **落库永远拒绝**。
#    这与 stance 检查的取舍同源：能不能重建「当时看到的东西」优先于形式一致。


class TestDecisionIdentity:
    @staticmethod
    def _v(agent: str, task_id: str):
        t = now_cn()
        return AgentVerdict(
            agent=agent, task_id=task_id, status="completed", verdict="PASS",
            stance=STANCE_VOCAB[agent][0], result={"trade_date": "2026-09-18"},
            evidence=[Evidence(field="trade_date", value="2026-09-18",
                               source="probe", as_of=t, retrieved_at=t)])

    def _card(self, did: str, tids: list[str], **kw):
        # ⚠️ 用**真的 agent 名**，不是 a0/a1 —— F8 之后未登记的 agent
        #    在契约层就会被拒，而这组测试要验的是决策身份，不该被那条挡住。
        names = ["market", "emotion", "sector", "technical"]
        # 🔴 最多用到 4 个 agent，STANCE_VOCAB 里另外至少 2 个（news/risk）
        #    天然缺席——roster 判据按计数比较（missing 条数须不少于缺席
        #    agent 数），5 条占位覆盖所有调用点的最坏情况（只传 1 个
        #    tid 时缺席数最多，为 5）。这组测试要验的是决策身份，
        #    不该被 roster 判据挡住。
        kw.setdefault("missing", absent_registrations(kw.get("verdicts") or []))
        return DecisionCard(
            decision_id=did, status="WAIT", headline="h",
            verdicts=[self._v(names[i], t) for i, t in enumerate(tids)],
            synthesis="s", model_ref="m", **kw)

    def test_新造的卡装着别人的判定要被拒(self):
        import pytest
        with pytest.raises(ValueError, match="不属于它的判定"):
            self._card("BIGA-20260921-001", ["BIGA-20260921-999"])

    def test_多个外来判定也要被拒(self):
        import pytest
        with pytest.raises(ValueError, match="不属于它的判定"):
            self._card("BIGA-20260921-001",
                       ["BIGA-20260921-998", "BIGA-20260921-999"])

    def test_一致的卡正常构造(self):
        c = self._card("BIGA-20260921-001",
                       ["BIGA-20260921-001", "BIGA-20260921-001"])
        assert c.identity_warning == ""

    def test_历史卡可读但卡面要显示(self):
        """🔴 这条是与评审建议不同的地方，理由见本节开头。"""
        c = self._card("BIGA-20260921-001", ["BIGA-20260921-999"],
                       from_store=True)
        assert c.identity_warning, "历史卡没有记下身份问题"
        assert "提请注意" in c.render() and "无法核实" in c.render()

    def test_from_dict_读历史卡不抛错(self):
        # contract-exempt: 这里就是要模拟「从库里读回来的 JSON」
        d = {"decision_id": "BIGA-20260921-001", "status": "WAIT",
             "headline": "h", "synthesis": "s", "model_ref": "m",
             "missing": [], "generated_at": "", "elapsed_ms": 0,
             "verdicts": [self._v("market", "BIGA-20260921-999").to_dict()]}
        c = DecisionCard.from_dict(d)
        assert c.identity_warning

    def test_历史卡不许再写回库(self, tmp_path):
        """读可以宽，**写必须严** —— 否则「读一张旧卡再存回去」就洗白了它。"""
        import pytest
        from _store import db
        p = tmp_path / "t.db"
        db.init_schema(p)
        c = self._card("BIGA-20260921-001", ["BIGA-20260921-999"],
                       from_store=True)
        with pytest.raises(ValueError, match="拒绝落库"):
            db.save_card(c, path=p)


# ───────────────────────────────────────── 新源默认纳入，例外自己举手（F16）
#
# 外部评审 F16：上面那份 `_LIVE_FIELDS` 是**按文件路径 + 字面量字段名**
# 枚举的显式字典。未来任何新 skill、或现有 skill 的新字段，只要产出一个
# 无日期端点的事实，而开发者没主动想起改这份白名单，就不会有任何测试提醒。
#
# 🔴 而这不是假想：F4 本身就是「同一次提交里修了 Evidence 层、
#    却在 raw 层重新犯了同一个错」——因为那条路径不在白名单的视野里。
#
# ⇒ 把「这个端点有没有服务端日期」的知识放回**源自己身上**
#   （`server_as_of`），调用方就不必逐处判断。判断一旦分散，
#   必然有某一处判错。
#
# 这条测试守的是那个统一接口：**新加一个源，必须回答这个问题。**


def _source_result_classes() -> list[tuple[str, type]]:
    """`_sources` 里所有「一次抓取的结果」类型 —— 判据是它带 `raw` 字段。"""
    import dataclasses
    import importlib
    import pkgutil

    import _sources

    out = []
    for m in pkgutil.iter_modules(_sources.__path__):
        mod = importlib.import_module(f"_sources.{m.name}")
        for name, obj in vars(mod).items():
            if (isinstance(obj, type) and dataclasses.is_dataclass(obj)
                    and obj.__module__ == mod.__name__
                    and any(f.name == "raw" for f in dataclasses.fields(obj))):
                out.append((f"{m.name}.{name}", obj))
    return sorted(set(out), key=lambda x: x[0])


def test_扫到了源结果类型():
    """判据非空自检 —— 否则下一条会因为「一个都没扫到」而平凡通过。"""
    assert len(_source_result_classes()) >= 5


def test_每个源都要声明自己有没有服务端时刻():
    missing = [n for n, cls in _source_result_classes()
               if not hasattr(cls, "server_as_of")]
    assert not missing, (
        f"这些源没有声明 `server_as_of`：{missing}\n"
        "  每个端点都必须回答「你自己带不带时刻」——\n"
        "  不带就显式写 `server_as_of = None`，**不实现会让人以为是漏了**。\n"
        "  调用方据此统一写 `r.server_as_of or now_cn()`，不必逐处判断。"
    )


def test_无日期端点必须显式声明None():
    """两个已知的无日期端点 —— 它们是 BIGA-20260921-017 那次事故的源头。"""
    import _sources

    for cls in (_sources.BreadthResult, _sources.BoardResult):
        assert cls.server_as_of is None, f"{cls.__name__} 不该声称自己有服务端时刻"


def _no_date_source_names() -> set[str]:
    """无日期端点的结果类型名——这份集合**从源头结构性派生**
    （复用上面的 `_source_result_classes()`），不是手抄的。"""
    return {name.rsplit(".", 1)[-1] for name, cls in _source_result_classes()
            if cls.server_as_of is None}


def test_用到无日期源的calc脚本都登记进了_LIVE_FIELDS():
    """🔴 外部评审 F16 的残留部分：`_LIVE_FIELDS` 仍是手写的
    ``{文件: {字段}}`` 字典。**字段级别**的粒度没法从"这个源有没有日期"
    自动派生——一个 calc 脚本可能同时消费好几个源，也可能把无日期源的值
    拿去做别的用途，而不直接暴露成一个 Evidence 字段。复查因此把这一条
    标成"实例修好、模式还在"：修好的是 F4 炸掉的那两个文件，
    不是这份清单本身会不会漏第三个文件。

    但**文件级别**是可以结构性核对的：只要一个 calc 脚本 import 了
    某个无日期源的结果类型，它就必须在 `_LIVE_FIELDS` 里出现——哪怕
    具体字段还得靠人确认。这堵住的是最严重的那种遗漏：**一整个文件**
    开始消费无日期源，却从未在这份清单里出现过一次，于是它的每一个
    live 字段的 as_of 处理都完全没有测试覆盖。

    对照 F7 的 `_FIELD_OWNER` + AST 核对：同样是"允许手写映射，
    但持续核对映射是否还成立"这个折中，不是假装能全自动派生。
    """
    no_date = _no_date_source_names()
    offenders = []
    for script in sorted(REPO.glob("skills/*-calc/scripts/*.py")):
        rel = str(script.relative_to(REPO))
        src = script.read_text(encoding="utf-8")
        if any(name in src for name in no_date) and rel not in _LIVE_FIELDS:
            offenders.append(rel)
    assert not offenders, (
        f"这些脚本用到了无日期源（{sorted(no_date)}）却没有出现在 "
        f"_LIVE_FIELDS 里：{offenders}\n"
        "  至少加一个条目（哪怕字段集合需要人工确认哪些该走 add_live）——\n"
        "  否则这个文件对无日期字段的 as_of 处理完全没有测试覆盖。")


# ─────────────────────────────── staleness 对共模误差免疫（F15）
#
# 外部评审 F15：`Evidence` 唯一的时间校验是「两者都带 tzinfo」和
# 「as_of <= retrieved_at」，**从不与真实当前时刻比较**。
#
# 构造一对都比现在晚 3 天、但彼此只差 60 秒的时间戳：
#
#     source_lag_sec = 60     # 1 分钟 —— 看起来非常新鲜
#     而 as_of 实际比现在晚了 3 天
#
# 🔴 要害不在「少查了一项」，而在 `source_lag_sec` 是个**差值**：
#    只要上游的两个时刻由同一段错误逻辑派生，它对那个错误免疫。
#    F4 那类错位恰好就是这种形状。


class TestF15ClockAnchor:
    def test_未来的取回时刻被拒绝(self):
        import pytest

        t = now_cn() + timedelta(days=3)
        with pytest.raises(ValueError, match="在未来"):
            Evidence(field="x", source="s", value=1,
                     as_of=t - timedelta(seconds=60), retrieved_at=t)

    def test_差值看起来新鲜也救不了它(self):
        """判的是「有没有被拒」，不是 staleness 算得对不对 ——
        staleness 在这个输入下**本来就是 60**，断言它没有意义。"""
        import pytest

        t = now_cn() + timedelta(days=3)
        try:
            Evidence(field="x", source="s", value=1,
                     as_of=t - timedelta(seconds=60), retrieved_at=t)
        except ValueError as e:
            assert "共模" in str(e) or "差值" in str(e), "报错要指出为什么差值救不了"
        else:
            pytest.fail("未来时刻被放行了")

    def test_正常的过去时刻照常通过(self):
        t = now_cn() - timedelta(hours=2)
        e = Evidence(field="x", source="s", value=1,
                     as_of=t - timedelta(seconds=60), retrieved_at=t)
        assert e.source_lag_sec == 60
        assert e.age_sec > 7000, "age_sec 锚在真实时钟上，不是两个数之差"

    def test_时钟抖动的小幅超前仍然允许(self):
        """容差只为机器间的时钟抖动留口子。定成 0 会在正常运行时偶发报红，
        而偶发报红的检查最后会被当成噪音关掉。"""
        t = now_cn() + timedelta(seconds=30)
        Evidence(field="x", source="s", value=1,
                 as_of=t - timedelta(seconds=1), retrieved_at=t)
