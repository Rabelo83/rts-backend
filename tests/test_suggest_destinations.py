from routes.agent_claude import _build_buttons
from routes.agent_tools import _tool_suggest_destinations


def test_poi_match_returns_candidates(monkeypatch):
    monkeypatch.setattr(
        "routes.agent_tools.get_common_destinations",
        lambda: {
            "landmarks": {},
            "pois": {
                "library": [
                    {
                        "name": "Alachua County Library HQ",
                        "address": "401 E University Ave, Gainesville, FL",
                    }
                ]
            },
        },
    )

    result = _tool_suggest_destinations("library")

    assert result["status"] == "ok"
    assert len(result["candidates"]) >= 1


def test_substring_match_works(monkeypatch):
    monkeypatch.setattr(
        "routes.agent_tools.get_common_destinations",
        lambda: {
            "landmarks": {},
            "pois": {
                "library": [
                    {
                        "name": "Alachua County Library HQ",
                        "address": "401 E University Ave, Gainesville, FL",
                    }
                ]
            },
        },
    )

    result = _tool_suggest_destinations("the library")

    assert result["status"] == "ok"


def test_not_found_path(monkeypatch):
    monkeypatch.setattr(
        "routes.agent_tools.get_common_destinations",
        lambda: {"landmarks": {}, "pois": {}},
    )

    result = _tool_suggest_destinations("unicorn pizza")

    assert result["status"] == "not_found"


def test_buttons_render_from_destination_tool_result():
    buttons = _build_buttons(
        [
            {
                "tool": "suggest_destinations",
                "result": {
                    "status": "ok",
                    "candidates": [
                        {"name": "Place A", "address": "123 Main St"},
                        {"name": "Place B", "address": "456 Main St"},
                    ],
                },
            }
        ],
        "en",
    )

    assert len(buttons) == 2
    assert all(button.get("label") for button in buttons)
    assert all(button.get("action", "").startswith("plan trip to ") for button in buttons)
