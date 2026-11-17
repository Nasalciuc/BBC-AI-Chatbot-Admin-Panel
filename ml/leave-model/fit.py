"""Leave model v0 — "will this visitor leave the site in the next few seconds?"

Discrete-time hazard model (logistic regression on person-periods), fit on aggregated cells from
PostHog (buybusinessclass.com, 13 Nov 14:00 → 16 Nov 16:20 UTC, before Alex's cards existed; no bots,
no visits from MD/RO/IN/UA; phones and computers). One person-period = one visitor still on the site at
the start of an interval; the event = they left during it. Features are known at the interval's start:
traffic source, device, in-app browser (Facebook/Instagram/Google app), and whether they had tapped or
moved the pointer by then (PostHog heatmap flushes, which come after the touch: no peeking ahead).

Split by session (cityHash64(session) % 5 == 0 → test, ~20%), so no visitor is in both halves.

    python3 fit.py person_period.csv   → prints the metrics, the coefficients and example predictions
"""
import json
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score

INTERVALS = [(0, 2), (2, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 45), (45, 60), (60, 90), (90, 120)]


def design(df: pd.DataFrame, model: str) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    for k in range(len(INTERVALS)):
        x[f"k{k}"] = (df.k == k).astype(float)
    if model in ("M1", "M2"):
        x["kayak"] = (df.source == "kayak").astype(float)
        x["search_ad"] = (df.source == "search_ad").astype(float)
        x["phone"] = (df.device == "phone").astype(float)
        x["in_app"] = df.in_app.astype(float)
        x["kayak_phone"] = x.kayak * x.phone
    if model == "M2":
        x["touched"] = df.touched.astype(float)
        x["touched_phone"] = x.touched * (df.device == "phone").astype(float)
        x["touched_early"] = x.touched * (df.k <= 4).astype(float)
    return x


def expand(df: pd.DataFrame, x: pd.DataFrame):
    """Each cell → two weighted rows: left (y=1, weight=left) and stayed (y=0, weight=at_risk-left)."""
    xs = pd.concat([x, x], ignore_index=True)
    y = np.r_[np.ones(len(df)), np.zeros(len(df))]
    w = np.r_[df.left.values, (df.at_risk - df.left).values].astype(float)
    keep = w > 0
    return xs[keep].values, y[keep], w[keep]


def fit(df_train, model):
    x = design(df_train, model)
    X, y, w = expand(df_train, x)
    m = LogisticRegression(C=100.0, fit_intercept=False, max_iter=5000)
    m.fit(X, y, sample_weight=w)
    return m, list(x.columns)


def evaluate(m, df_test, model):
    x = design(df_test, model)
    X, y, w = expand(df_test, x)
    p = m.predict_proba(X)[:, 1]
    return {"log_loss": round(log_loss(y, p, sample_weight=w), 4), "auc": round(roc_auc_score(y, p, sample_weight=w), 3)}


def calibration(m, df_test, model, bins=5):
    x = design(df_test, model)
    p = m.predict_proba(x.values)[:, 1]
    t = df_test.assign(p=p)
    t["bin"] = pd.qcut(t.p.rank(method="first"), bins, labels=False)
    out = []
    for b, g in t.groupby("bin"):
        out.append({"predicted": round(float((g.p * g.at_risk).sum() / g.at_risk.sum()), 3),
                    "observed": round(float(g.left.sum() / g.at_risk.sum()), 3),
                    "person_periods": int(g.at_risk.sum())})
    return out


def hazard(m, cols, **cell):
    row = pd.DataFrame([cell])
    x = design(row, "M2")[cols]
    return float(m.predict_proba(x.values)[:, 1][0])


def main(path):
    df = pd.read_csv(path)
    train, test = df[df.split == "train"].reset_index(drop=True), df[df.split == "test"].reset_index(drop=True)
    report = {"sessions": {"train": int(train[train.k == 0].at_risk.sum()), "test": int(test[test.k == 0].at_risk.sum())}}
    models = {}
    for name in ("M0", "M1", "M2"):
        m, cols = fit(train, name)
        models[name] = (m, cols)
        report[name] = evaluate(m, test, name)
    m, cols = models["M2"]
    report["calibration_M2_test"] = calibration(m, test, "M2")
    report["coefficients_M2"] = {c: round(float(v), 3) for c, v in zip(cols, m.coef_[0])}

    # Examples: a visitor still here at 10 s (interval 10–15 s, k=3) and at 15 s (k=4): the chance to leave
    # within the next 10 s = 1 - (1 - h3)(1 - h4).
    ex = {}
    for source in ("kayak", "search_ad"):
        for device in ("phone", "computer"):
            for in_app in ((0, 1) if device == "phone" else (0,)):
                for touched in (0, 1):
                    cell = dict(source=source, device=device, in_app=in_app, touched=touched, form=0)
                    h3 = hazard(m, cols, k=3, **cell)
                    h4 = hazard(m, cols, k=4, **cell)
                    key = f"{source}/{device}/{'in-app' if in_app else 'browser'}/{'touched' if touched else 'no touch'}"
                    ex[key] = round(1 - (1 - h3) * (1 - h4), 3)
    report["p_leave_in_next_10s_at_10s"] = ex
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "person_period.csv")
