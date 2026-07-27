import sys
from unittest.mock import MagicMock

for m in ("streamlit", "streamlit.components", "streamlit.components.v1",
          "streamlit_autorefresh"):
    sys.modules.setdefault(m, MagicMock())

import city_view


def test_city_sections_returns_selection_and_codes(monkeypatch):
    monkeypatch.setattr(city_view, "city_control", lambda page_key, arity=3: "Both")
    sel, codes = city_view.city_sections("journal", 3)
    assert sel == "Both"
    assert codes == ["KDFW", "KAUS"]
    monkeypatch.setattr(city_view, "city_control", lambda page_key, arity=3: "Austin")
    sel, codes = city_view.city_sections("journal", 3)
    assert sel == "Austin" and codes == ["KAUS"]
