"""Both generation paths receive the same factual boundaries, offline only."""
import prompt_profiles as pp
from tests.test_deepseek_legacy_golden import legacy_prompt_inputs
import morning_report as mr
import copy


def test_primary_and_fallback_share_fact_boundaries():
    legacy = mr._build_prompt(*legacy_prompt_inputs())
    for phrase in ('開盤預測不是停損價', '核心排除食品能源', '零票息不等於零融資成本',
                   '藥證核准、專利與法規獨占不同', '月增營收不能單獨證明產能滿載'):
        assert phrase in legacy
        assert phrase in pp.LUNA_DEVELOPER_INSTRUCTIONS
    assert 'β≈1.1' not in legacy
    assert '由負轉正回升則為景氣回溫訊號' not in legacy


def test_populated_macro_data_does_not_reintroduce_recovery_claim():
    args = copy.deepcopy(legacy_prompt_inputs())
    args[0]['MACRO'] = {'10Y': {'close': 4.94}, '13W': {'close': 3.85}}
    prompt = mr._build_prompt(*args)
    assert '利差 = +1.09' in prompt
    assert '轉正回升=景氣回溫訊號' not in prompt
    assert '僅描述曲線形狀' in prompt
