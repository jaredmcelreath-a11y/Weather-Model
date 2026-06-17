"""Data source adapters.

Each module exposes a `fetch(...)` returning a dict of
    {series_label: (times, temps_f)}
where `times` is a list of tz-aware datetimes and `temps_f` a parallel list of
floats in Fahrenheit. Ensembles return one entry per member; deterministic and
observation sources return a single entry. This uniform shape lets the model
and settlement code treat every source identically.
"""
