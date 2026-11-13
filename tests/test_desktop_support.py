"""Wrapper regressions: backup preservation, stale runs and CLI isolation."""
from pathlib import Path
import json
import tempfile
import unittest

from desktop_support import accept_result, build_args, digest, load_session, new_session


class DesktopSafetyTests(unittest.TestCase):
    def test_new_project_copies_backup_without_touching_source(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / "original.xml"
            source.write_bytes(b'<config version="11.1.0"><shared/></config>')
            before = digest(source)
            root, state = new_session(source, base / "Projeto com espaço")
            self.assertEqual(before, digest(source))
            self.assertEqual(before, digest(Path(state["input"])))
            self.assertEqual(load_session(root), state)
            Path(state["input"]).write_bytes(b"tampered")
            with self.assertRaises(ValueError): load_session(root)

    def test_failed_rerun_invalidates_all_downstream_results(self):
        state = {"runs": {k: k for k in ("inventory", "analyze", "plan", "convert", "cleanup", "commands", "validate")}}
        accept_result(state, "plan", None, False)
        self.assertEqual(state["runs"], {"inventory": "inventory", "analyze": "analyze"})

    def test_validate_does_not_depend_on_commands_or_bash(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            cleanup = base / "cleanup"
            cleanup.mkdir()
            final = base / "final com espaços.xml"
            (cleanup / "manifest.json").write_text(json.dumps({"output_xml": {"path": str(final)}}))
            state = {"input": "source.xml", "runs": {"plan": "p", "convert": "c", "cleanup": str(cleanup)}}
            args = build_args("validate", base, state)
            self.assertIn(str(final), args)
            self.assertNotIn("commands", args)

    def test_cleanup_keeps_tags_and_uses_original_backup(self):
        state = {"input": "original.xml", "runs": {"plan": "p", "convert": "c"}, "keep_tags": True}
        args = build_args("cleanup", Path("project"), state)
        self.assertEqual(args[args.index("--input") + 1], "original.xml")
        self.assertEqual(args[args.index("--keep-type") + 1], "tag")
        self.assertIn("--from-run", args)
        state["keep_tags"] = False
        self.assertNotIn("--keep-type", build_args("cleanup", Path("project"), state))

    def test_missing_dependencies_block_transformation(self):
        with self.assertRaises(ValueError):
            build_args("convert", Path("project"), {"input": "original.xml", "runs": {}})

    def test_run_paths_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / "source.xml"
            source.write_text("<config/>")
            root, state = new_session(source, base / "project")
            state["runs"]["inventory"] = str(base / "outside")
            (root / "desktop-session.json").write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(ValueError): load_session(root)


if __name__ == "__main__": unittest.main()
