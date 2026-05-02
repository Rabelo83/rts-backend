import rts_api


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_call_bustime_falls_back_after_transaction_limit(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["key"])
        if params["key"] == "key-one":
            return _FakeResponse({
                "bustime-response": {
                    "error": [{"msg": "Transaction limit for current day has been exceeded."}]
                }
            })
        return _FakeResponse({
            "bustime-response": {
                "vehicle": [{"vid": "2411", "rt": "7"}]
            }
        })

    monkeypatch.setattr(rts_api, "API_KEYS", ["key-one", "key-two"])
    monkeypatch.setattr(rts_api.requests, "get", fake_get)

    data = rts_api.call_bustime("getvehicles", {"rt": "7"})

    assert calls == ["key-one", "key-two"]
    assert data["vehicle"][0]["vid"] == "2411"
