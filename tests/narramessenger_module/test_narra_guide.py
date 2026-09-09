"""Unit tests for the curated narra_guide reference.

narra_guide serves a STATIC, platform-adapted command reference (not narra's
live runtime.md, whose install/configure/token instructions caused an agent to
try setting up narra-cli itself and fail on sandbox chmod). These tests pin the
load-bearing invariants: it tells the agent narra-cli is platform-provided (no
install/configure/token), points at `--help` for exact flags, and does NOT carry
the harmful setup instructions.
"""
from narranexus_plugins.narramessenger_module import _narra_guide as ncg


def test_guide_returns_curated_reference():
    g = ncg.get_guide()
    assert g and "narra_cli" in g
    # Covers the real command surface.
    for token in ("room list", "im messages", "explore publish", "speech", "status"):
        assert token in g, token


def test_guide_states_platform_provides_narra_cli():
    g = ncg.get_guide().lower()
    # The invariant that stops the agent from setting narra-cli up itself.
    assert "platform" in g
    assert "--help" in g  # live-flag escape hatch is advertised
    # Never pass a token yourself.
    assert "token" in g
    # Since narra-cli 1.2 the endpoint is injected too — the guide must say so
    # and forbid --endpoint, or an agent following narra's own runtime.md will
    # try to pick one (api-cn / api-test bindings, prod 2026-09-09).
    assert "--endpoint" in g
    assert "injects" in g
    # The live --help USAGE line shows `npx ... --endpoint --token`; the guide
    # must inoculate against it, since it points the agent there.
    assert "npx" in g and "ignore" in g
    # Platform-injected credential failures are REPORTED, not diagnosed with
    # certainty; by-design answers are explained, not reported.
    assert "submit_feedback" in g
    assert "official-agent-required" in g and "by-design" in g


def test_guide_is_curated_not_raw_runtime_md():
    # It must be OUR curated doc (strong platform banner), not narra's raw
    # runtime.md. The banner may NAME npm/configure/token to FORBID them; what
    # must be absent is the actual install RECIPE — markers that only appear in a
    # real install command, never in a prohibition.
    g = ncg.get_guide()
    assert "PLATFORM PROVIDES" in g          # our curated banner
    assert "@narra-im" not in g              # the npm package spec (install recipe)
    assert "./node_modules/.bin" not in g    # the run-it-yourself path (recipe)


def test_builtin_fallback_when_resource_missing(monkeypatch, tmp_path):
    # If the resource file is unreadable, the built-in still keeps the invariant.
    monkeypatch.setattr(ncg, "_CURATED_PATH", tmp_path / "does-not-exist.md")
    g = ncg.get_guide()
    assert "narra_cli" in g
    assert "do NOT install" in g or "do not install" in g.lower()
    # The fallback must carry the same 1.2-era invariants as the resource:
    # endpoint is injected, --help's npx USAGE line is to be ignored, failures
    # are reported conservatively, by-design answers are not defects.
    for token in ("--endpoint", "injected", "npx", "submit_feedback",
                  "official-agent-required", "never paste a token"):
        assert token in g, token
