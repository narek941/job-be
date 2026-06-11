"""Tests for the pure-data profile helpers (no LLM)."""

from jobfox.profile import Profile, add_skills, remove_skills, render, set_summary, _sanitize


def test_add_skills_dedupes_case_insensitive() -> None:
    p = Profile(skills=["React", "TypeScript"])
    out = add_skills(p, ["react", "Node.js", "TYPESCRIPT"])
    assert out["skills"] == ["React", "TypeScript", "Node.js"]


def test_remove_skills_drops_targets() -> None:
    p = Profile(skills=["React", "TypeScript", "Node.js"])
    out = remove_skills(p, ["TypeScript", "missing"])
    assert out["skills"] == ["React", "Node.js"]


def test_set_summary_trims_long_input() -> None:
    p = Profile(summary="short")
    out = set_summary(p, "x" * 2000)
    assert len(out["summary"]) == 1500


def test_sanitize_caps_skills_at_30() -> None:
    raw = {"skills": [f"S{i}" for i in range(40)]}
    out = _sanitize(raw)
    assert len(out["skills"]) == 30


def test_sanitize_omits_empty_arrays() -> None:
    out = _sanitize({"skills": [], "experience": []})
    assert "skills" not in out
    assert "experience" not in out


def test_sanitize_drops_non_string_skills() -> None:
    out = _sanitize({"skills": ["React", 42, None, "Node"]})
    assert out["skills"] == ["React", "Node"]


def test_render_empty_profile() -> None:
    assert "No structured profile" in render(None)
    assert "No structured profile" in render({})


def test_render_includes_sections() -> None:
    p = Profile(
        headline="Senior FE",
        skills=["React", "TypeScript"],
        experience=[{"role": "Engineer", "company": "Acme", "from": "2022", "to": "2024",
                     "bullets": ["did things"]}],
    )
    text = render(p)
    assert "Senior FE" in text
    assert "React" in text
    assert "Engineer @ Acme" in text
    assert "did things" in text
