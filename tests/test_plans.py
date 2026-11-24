"""Тарифы: бесплатный, пробный период эксперта, Pro; фермер — по тарифу эксперта."""
import time

import plans

NOW = 1_800_000_000


def user(**kw):
    base = {"id": 1, "role": "expert", "plan": "free", "plan_until": 0, "trial_used": 0, "invited_by": None,
            "first_name": "Иван", "last_name": "Петров"}
    base.update(kw)
    return base


def test_free_expert_has_limits_and_trial():
    info = plans.effective(user(), lambda _: None, NOW)
    lim = plans.limits(info)
    assert info["code"] == "free" and info["trial_available"]
    assert lim == {"history_days": 7, "export_days": 7, "farmers": 3, "integrations": False, "analytics": False}


def test_trial_and_expiry():
    active = plans.effective(user(plan="trial", plan_until=NOW + 7 * 86400, trial_used=1), lambda _: None, NOW)
    assert active["code"] == "trial" and active["days_left"] == 7 and not active["trial_available"]
    assert plans.limits(active)["export_days"] == 30 and plans.limits(active)["farmers"] is None
    expired = plans.effective(user(plan="trial", plan_until=NOW - 1, trial_used=1), lambda _: None, NOW)
    assert expired["code"] == "free" and not expired["trial_available"]


def test_pro_without_date_is_unlimited():
    assert plans.effective(user(plan="pro"), lambda _: None, NOW)["code"] == "pro"


def test_farmer_inherits_expert_plan_and_is_not_offered_subscription():
    expert = user(id=5, plan="pro")
    farmer = user(id=9, role="farmer", invited_by=5)
    info = plans.effective(farmer, lambda i: expert if i == 5 else None, NOW)
    assert info["code"] == "pro" and info["source"] == "expert" and not info["trial_available"]
    free = plans.effective(farmer, lambda i: user(id=5), NOW)
    assert "подписк" not in plans.locked_text(free, "Аналитика")
    alone = plans.effective(user(role="farmer"), lambda i: None, NOW)
    assert alone["source"] == "none" and not alone["paid"]


def test_admin_has_everything():
    info = plans.effective(user(role="admin"), lambda _: None, time.time())
    assert info["paid"] and plans.limits(info)["integrations"]
