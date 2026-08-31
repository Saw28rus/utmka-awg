"""Сборка списков split-routing: парсер sapics и зеркала источников."""

from __future__ import annotations

from app.services.split_lists import (
    SAPICS_RU_URLS,
    SOURCES,
    SplitListError,
    _fetch_text,
    _parse_sapics_csv,
    build_direct_cidrs,
)


def test_sapics_urls_are_cdn_not_github_raw() -> None:
    urls = SOURCES["sapics_ru"].urls
    assert urls == SAPICS_RU_URLS
    assert all("raw.githubusercontent.com" not in u for u in urls)
    assert "jsdelivr.net" in urls[0]


def test_parse_sapics_csv_keeps_only_ru() -> None:
    text = (
        "1.0.0.0,1.0.0.255,AU\n"
        "5.45.192.0,5.45.223.255,RU\n"
        "8.8.8.0,8.8.8.255,US\n"
        "77.88.0.0,77.88.63.255,RU\n"
    )
    nets = _parse_sapics_csv(text)
    cidrs = {str(n) for n in nets}
    assert any(c.startswith("5.45.") for c in cidrs)
    assert any(c.startswith("77.88.") for c in cidrs)
    assert not any(c.startswith("1.0.0.") for c in cidrs)
    assert not any(c.startswith("8.8.8.") for c in cidrs)


def test_fetch_text_falls_back_to_next_url(monkeypatch) -> None:
    calls: list[str] = []

    class _Resp:
        def __init__(self, url: str) -> None:
            self.url = url
            self.text = "10.0.0.0/8\n"

        def raise_for_status(self) -> None:
            if "fail.example" in self.url:
                raise __import__("httpx").HTTPStatusError(
                    "404", request=None, response=self  # type: ignore[arg-type]
                )

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, url, headers=None):
            calls.append(url)
            if "fail.example" in url:
                import httpx

                req = httpx.Request("GET", url)
                resp = httpx.Response(404, request=req)
                raise httpx.HTTPStatusError("404", request=req, response=resp)
            return _Resp(url)

    monkeypatch.setattr("app.services.split_lists.httpx.Client", _Client)
    text = _fetch_text(("https://fail.example/a.csv", "https://ok.example/a.csv"))
    assert "10.0.0.0/8" in text
    assert calls == ["https://fail.example/a.csv", "https://ok.example/a.csv"]


def test_build_direct_skips_failed_source(monkeypatch) -> None:
    def boom(source, *, force_refresh, cache):
        if source.id == "sapics_ru":
            raise SplitListError("недоступно")
        from app.services.split_lists import _parse_cidr_lines

        return _parse_cidr_lines("10.0.0.0/8"), None

    monkeypatch.setattr("app.services.split_lists._source_networks", boom)
    monkeypatch.setattr("app.services.split_lists._load_cache", lambda: {})
    monkeypatch.setattr("app.services.split_lists._save_cache", lambda cache: None)
    result = build_direct_cidrs(["sapics_ru", "rfc1918"])
    assert result.total_count > 0
    assert result.per_source["sapics_ru"] == 0
    assert result.warnings
    assert "10.0.0.0/8" in result.cidrs
