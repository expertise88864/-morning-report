"""Reporting qualification only; never alter a registered selection formula."""


def qualify(result: dict) -> dict:
    qualified = dict(result)
    missing = bool(result.get('skipped', {}).get('missing_selected_price_paired_cohort'))
    qualified['comparison_status'] = ('invalid_selected_price_coverage' if missing
                                      else 'selected_price_coverage_complete')
    if missing:
        qualified['summary'] = None
        qualified['folds'] = {}
        qualified['replacement_blockers'] = list(result.get('replacement_blockers') or []) + [
            'Selected entry/exit prices are missing: aggregate comparisons withheld '
            'because dropping paired cohorts can introduce survivorship bias.']
    return qualified
