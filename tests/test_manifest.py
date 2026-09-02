"""Тести звірки зліпка схем служб — захист від підміни описів після схвалення."""
from __future__ import annotations

import json

import mcp_manifest as mf


async def test_snapshot_has_both_servers():
    """Зліпок описує обидві служби."""
    manifest = await mf.build_manifest()
    assert set(manifest) == {"library", "mail"}


async def test_snapshot_records_tool_names_and_hashes():
    """Для кожної дії зберігається ім'я і хеш схеми."""
    manifest = await mf.build_manifest()
    tools = manifest["library"]["tools"]
    assert "place_print_order" in tools
    assert len(tools["place_print_order"]) == 64      # sha256 у hex


async def test_verify_passes_on_matching_manifest(tmp_path):
    """Незмінені служби проходять звірку."""
    path = tmp_path / "m.json"
    path.write_text(json.dumps(await mf.build_manifest(), ensure_ascii=False),
                    encoding="utf-8")
    ok, problems = await mf.verify_manifest(path)
    assert ok is True and problems == []


async def test_verify_detects_changed_description(tmp_path):
    """Змінений опис дії виявляється — це і є підміна після схвалення."""
    manifest = await mf.build_manifest()
    manifest["library"]["tools"]["get_order"] = "0" * 64
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    ok, problems = await mf.verify_manifest(path)
    assert ok is False
    assert any("get_order" in p for p in problems)


async def test_verify_detects_new_tool(tmp_path):
    """Дія, якої не було у зліпку, теж є розходженням."""
    manifest = await mf.build_manifest()
    del manifest["library"]["tools"]["cancel_order"]
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    ok, problems = await mf.verify_manifest(path)
    assert ok is False
    assert any("cancel_order" in p for p in problems)


async def test_verify_detects_resource_list_drift(tmp_path):
    """Розходження переліку довідників виявляється так само, як зміна дії."""
    manifest = await mf.build_manifest()
    manifest["library"]["resources"].append("policy://вигадана")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    ok, problems = await mf.verify_manifest(path)
    assert ok is False
