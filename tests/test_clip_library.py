from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from video_clip_library.config import (
    active_job_tree,
    normalize_config,
    normalize_field_sets,
    switch_import_note_type,
    visible_field_choices,
)
from video_clip_library.identity import (
    NOTE_SAMPLE_PLACEHOLDER,
    SAMPLE_PLACEHOLDER,
    clip_identity_choices,
    clip_identity_samples,
    format_note_label,
    note_identity_samples,
    preview_identity,
)
from video_clip_library.exclusions import first_matching_rule, normalize_export_exclusion_presets, resolved_values
from video_clip_library.export import (
    clip_match_document,
    export_clips,
    inspect_export_clips,
    job_key_counts,
    merge_clips,
    write_library,
    load_library,
    copy_media_file,
)
from video_clip_library.export_keys import (
    export_key_examples,
    anki_export_key_tree,
    effective_anki_keys,
    filter_anki_section,
    selected_job_redundancy_pairs,
)
from video_clip_library.import_map import (
    apply_fields_to_note,
    attach_library_notes,
    clip_label,
    field_set_display_name,
    clip_search_fields,
    describe_writes,
    insert_search_term,
    library_audio_fields,
    library_audio_paths,
    plan_imports,
    quote_anki_search_term,
    rank_notes,
    resolve_clip_anki,
    rewrite_video_html,
    score_note,
    source_catalog,
)
from video_clip_library.job_index import (
    JobIndex,
    apply_key_examples,
    apply_record_samples,
    collect_key_examples,
    collect_key_paths,
    effective_record_keys,
    empty_job_tree,
    filter_record,
    prepare_job_payload,
)
from video_clip_library.models import DiscoveredClip, ExtraField, FieldSet, FieldValue, LinkInfo
from video_clip_library.anki_scan import clips_from_note_values, group_deck_names
from video_clip_library.textutil import (
    extract_audio_filenames,
    extract_miscinfo_source,
    extract_video_filename,
    normalize_filename,
    obscure_path,
    obscure_paths_in_data,
)


class VideoParseTests(unittest.TestCase):
    def test_video_tag(self) -> None:
        html = '<video controls="" src="pragmata2026070617461104dvr_00m36s683ms_01m28s332ms.mkv" class="clip-games-general"></video>'
        self.assertEqual(
            extract_video_filename(html),
            "pragmata2026070617461104dvr_00m36s683ms_01m28s332ms.mkv",
        )

    def test_sound_tag_and_plain_name(self) -> None:
        self.assertEqual(extract_video_filename("[sound:clip.mp4]"), "clip.mp4")
        self.assertEqual(extract_video_filename("plain.webm"), "plain.webm")
        self.assertEqual(extract_video_filename("no media here"), "")

    def test_miscinfo_source(self) -> None:
        text = "Source: Pragmata 2026.07.06 - 17.46.11.04.DVR.mp4 | (00m36s-01m28s, length: 0:51)"
        self.assertEqual(extract_miscinfo_source(text), "Pragmata 2026.07.06 - 17.46.11.04.DVR.mp4")

    def test_multiple_audio_references(self) -> None:
        html = '[sound:one.mp3]<audio src="two.ogg"></audio><audio controls><source src="three.wav"></audio>'
        self.assertEqual(extract_audio_filenames(html), ["one.mp3", "two.ogg", "three.wav"])


class FieldSetTests(unittest.TestCase):
    def test_legacy_per_export_job_setting_is_removed(self) -> None:
        config = normalize_config({"include_job_records_on_export": False})
        self.assertNotIn("include_job_records_on_export", config)

    def test_unique_fields_are_kept_first(self) -> None:
        config = normalize_config(
            {
                "field_sets": [
                    {"enabled": True, "video": "picture-subs2srs", "sentence": "example-sentence-subs2srs", "miscinfo": "notes"},
                    {"enabled": True, "video": "picture-subs2srs", "sentence": "example-sentence-2"},
                ]
            }
        )
        self.assertEqual(config["field_sets"][0]["video"], "picture-subs2srs")
        self.assertEqual(config["field_sets"][1]["video"], "")
        self.assertEqual(config["field_sets"][1]["sentence"], "example-sentence-2")

    def test_field_set_index_survives_reorder(self) -> None:
        normalized = normalize_field_sets(
            [
                {"index": 2, "name": "Second", "video": "B"},
                {"name": "First", "video": "A", "index": 1},
                {"name": "New", "video": "C"},
            ]
        )
        self.assertEqual([item["index"] for item in normalized], [2, 1, 3])

    def test_first_set_stays_enabled(self) -> None:
        normalized = normalize_field_sets([{"enabled": False, "video": "", "sentence": ""}])
        self.assertTrue(normalized[0]["enabled"])
        self.assertEqual(len(normalized), 1)

    def test_extra_fields_are_unique(self) -> None:
        config = normalize_config(
            {
                "field_sets": [{"enabled": True, "video": "picture-subs2srs", "sentence": "example-sentence-subs2srs"}],
                "generic_fields": [{"enabled": True, "name": "Field 1", "field": "picture-subs2srs"}],
                "audio_fields": [{"enabled": True, "name": "Audio 1", "field": "example-sentence-subs2srs"}],
            }
        )
        self.assertEqual(config["generic_fields"][0]["field"], "")
        self.assertEqual(config["audio_fields"][0]["field"], "")

    def test_sentence_is_optional_and_extra_roles_sync_across_sets(self) -> None:
        incomplete = FieldSet(enabled=True, video="picture-subs2srs", sentence="")
        self.assertTrue(incomplete.is_complete())
        config = normalize_config(
            {
                "clip_extra_roles": [{"id": "field_5", "name": "Field 5"}],
                "field_sets": [
                    {"enabled": True, "video": "picture-subs2srs", "extras": {"field_5": "word_list"}},
                    {"enabled": True, "video": "picture-2", "sentence": "sentence-2"},
                ],
            }
        )
        self.assertEqual(config["field_sets"][0]["extras"]["field_5"], "word_list")
        self.assertEqual(config["field_sets"][1]["extras"]["field_5"], "")
        self.assertTrue(config["field_sets"][0]["enabled"])
        field_set = FieldSet(enabled=True, name="Field Set 1", index=1, video="picture", extras={"field_5": "word_list"})
        clips = clips_from_note_values(
            note_id=1,
            deck_name="Deck",
            model_name="Yomi",
            sort_field_name="expression",
            sort_field_value="word",
            values={"picture": '<video src="clip.mkv"></video>', "word_list": "alpha"},
            field_sets=[field_set],
            media_directory="",
        )
        self.assertEqual(clips[0].fields["field_5"].value, "alpha")
        self.assertIn("anki.fields.field_5", anki_export_key_tree(config))


class PathPrivacyTests(unittest.TestCase):
    def test_filename_only(self) -> None:
        path = r"E:\Media\backup\Pragmata\Pragmata 2026.07.06 - 17.46.11.04.DVR.mp4"
        self.assertEqual(
            obscure_path(path, "filename_only"),
            r"...\Pragmata 2026.07.06 - 17.46.11.04.DVR.mp4",
        )

    def test_keep_parent(self) -> None:
        path = r"E:\Media\backup\Pragmata\Pragmata 2026.07.06 - 17.46.11.04.DVR.mp4"
        self.assertEqual(
            obscure_path(path, "keep_parents", 1),
            r"...\Pragmata\Pragmata 2026.07.06 - 17.46.11.04.DVR.mp4",
        )

    def test_nested_json(self) -> None:
        data = {"source": {"path": r"C:\Users\test-user\file.mkv"}, "note": "not a path"}
        obscured = obscure_paths_in_data(data, "filename_only")
        self.assertEqual(obscured["source"]["path"], r"...\file.mkv")
        self.assertEqual(obscured["note"], "not a path")


class RecordFilterTests(unittest.TestCase):
    def test_parent_yes_child_no(self) -> None:
        record = {
            "log_type": "mpvacious_videomod",
            "anki": {"deck_name": "jap_words", "note_id": 1, "fields": {"video": {"name": "x"}}},
            "commands": {"encode_command": ["ffmpeg"]},
        }
        selection = effective_record_keys({"anki": True, "anki.deck_name": False, "anki.fields": False, "commands": False})
        filtered = filter_record(record, selection)
        self.assertEqual(filtered["log_type"], "mpvacious_videomod")
        self.assertEqual(filtered["anki"]["note_id"], 1)
        self.assertNotIn("deck_name", filtered["anki"])
        self.assertNotIn("fields", filtered["anki"])
        self.assertNotIn("commands", filtered)


class RedundancySelectionTests(unittest.TestCase):
    def test_equivalent_keys_are_redundant_only_when_both_are_included(self) -> None:
        anki_selection = effective_anki_keys({"anki.deck_name": True})
        job_selection = effective_record_keys({"anki.deck_name": False})

        pairs = [{"clip_key": "anki.deck_name", "job_key": "anki.deck_name"}]
        self.assertNotIn("anki.deck_name", selected_job_redundancy_pairs(anki_selection, job_selection, pairs))

        job_selection["anki.deck_name"] = True

        self.assertEqual(
            selected_job_redundancy_pairs(anki_selection, job_selection, pairs)["anki.deck_name"],
            "anki.deck_name",
        )
        self.assertEqual(selected_job_redundancy_pairs(anki_selection, job_selection), {})


class JobMatchTests(unittest.TestCase):
    def test_filename_match_with_moved_note_is_partial(self) -> None:
        index = JobIndex("")
        summary = {
            "record_id": "abc",
            "created_at": "2026-08-13T22:59:36+02:00",
            "output_path": r"C:\media\clip.mkv",
            "record_path": "records/x.json",
        }
        index.by_filename[normalize_filename("clip.mkv")] = [summary]
        index._record_cache[""] = None
        record = {
            "record_id": "abc",
            "output": {"filename": "clip.mkv"},
            "anki": {"note_id": 111, "fields": {"video": {"name": "picture-subs2srs"}}},
            "job": {"active_field_set_index": 1},
        }

        def fake_load(_summary):
            return record

        index.load_record = fake_load  # type: ignore[method-assign]
        field_set = FieldSet(enabled=True, name="Set 1", index=2, video="picture-2", sentence="s")
        clips = clips_from_note_values(
            note_id=222,
            deck_name="jap_words",
            model_name="Yomi-custom",
            sort_field_name="expression",
            sort_field_value="皮肉",
            values={"picture-2": '<video src="clip.mkv"></video>', "s": "text"},
            field_sets=[field_set],
            media_directory="C:\\media",
        )
        self.assertEqual(len(clips), 1)
        match = index.match_clip(clips[0])
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.link.status, "partial")
        self.assertIn("note_id", match.link.mismatches)
        self.assertIn("field_set", match.link.mismatches)

    def test_newest_record_wins(self) -> None:
        index = JobIndex("")
        older = {"record_id": "old", "created_at": "2026-01-01T00:00:00+00:00", "output_path": "clip.mkv"}
        newer = {"record_id": "new", "created_at": "2026-08-01T00:00:00+00:00", "output_path": "clip.mkv"}
        index.by_filename[normalize_filename("clip.mkv")] = [older, newer]
        index.load_record = lambda summary: {"output": {"filename": "clip.mkv"}, "anki": {}, "job": {}, "record_id": summary["record_id"]}  # type: ignore[method-assign]
        match = index.match_filename("clip.mkv")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.summary["record_id"], "new")


class LibraryMergeTests(unittest.TestCase):
    def test_merge_updates_by_id_and_appends(self) -> None:
        existing = [{"id": "a", "media": {"filename": "one.mkv"}}, {"id": "b", "media": {"filename": "two.mkv"}}]
        incoming = [{"id": "a", "media": {"filename": "one.mkv"}, "extra": 1}, {"id": "c", "media": {"filename": "three.mkv"}}]
        merged = merge_clips(existing, incoming)
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0].get("extra"), 1)
        self.assertEqual(merged[2]["id"], "c")

    def test_copy_does_not_move(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "src" / "clip.mkv"
            dest_dir = Path(folder) / "media"
            source.parent.mkdir()
            source.write_bytes(b"abc")
            used, copied = copy_media_file(str(source), str(dest_dir), "clip.mkv")
            self.assertEqual(used, "clip.mkv")
            self.assertTrue(copied)
            self.assertTrue(source.exists())
            self.assertEqual((dest_dir / "clip.mkv").read_bytes(), b"abc")
            used2, copied2 = copy_media_file(str(source), str(dest_dir), "clip.mkv")
            self.assertEqual(used2, "clip.mkv")
            self.assertFalse(copied2)

    def test_roundtrip_library_json(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "clip_library.json")
            write_library(path, {"format": "clip_library", "clips": []})
            loaded = load_library(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["format"], "clip_library")


class ImportScoreTests(unittest.TestCase):
    def _clip(self, clip_id: str = "c1", sentence: str = "hello", sort_value: str = "皮肉") -> dict:
        return {
            "id": clip_id,
            "media": {"filename": f"{clip_id}.mkv"},
            "anki": {
                "note_id": 10,
                "sort_field_value": sort_value,
                "deck_name": "old deck",
                "field_set_index": 1,
                "fields": {
                    "sentence": {"name": "s", "value": sentence},
                    "secondary": {"name": "sec", "value": "translation"},
                    "video": {"name": "v", "value": f'<video src="{clip_id}.mkv"></video>'},
                },
                "extra_fields": {"Notes": {"name": "Notes", "value": "should-not-auto-copy"}},
            },
        }

    def test_custom_rule_matches_target_field_not_note_id(self) -> None:
        clip = self._clip()
        notes = [
            {"note_id": 10, "deck_name": "target", "sort_field_value": "other", "fields": {"Expression": "hello"}},
            {"note_id": 11, "deck_name": "target", "sort_field_value": "皮肉", "fields": {"Expression": "different"}},
        ]
        config = normalize_config(
            {
                "import_minimum": "medium",
                "import_rules": [
                    {"source": "sentence", "compare": "exact", "target_field": "Expression", "points": "high"},
                ],
                "field_sets": [{"enabled": True, "video": "Video"}],
            }
        )
        ranked = rank_notes(clip, notes, config)
        self.assertEqual(ranked[0].note_id, 10)
        self.assertEqual(score_note(clip, notes[1], config).score, 0)
        self.assertNotIn("note_id", [source_id for source_id, _label in source_catalog([clip])])

    def test_minimum_score_and_tie_stay_unmatched(self) -> None:
        clip = self._clip()
        notes = [
            {"note_id": 1, "deck_name": "target", "fields": {"Expression": "hello"}},
            {"note_id": 2, "deck_name": "target", "fields": {"Expression": "hello"}},
        ]
        config = normalize_config(
            {
                "import_minimum": "high",
                "import_rules": [
                    {"source": "sentence", "compare": "exact", "target_field": "Expression", "points": "low"},
                ],
                "field_sets": [{"enabled": True, "video": "Video"}],
            }
        )
        decisions = plan_imports([clip], notes, config)
        self.assertIsNone(decisions[0].note_id)
        self.assertEqual(decisions[0].conflict_resolution, "no_match")

        config["import_minimum"] = "low"
        config["import_rules"][0]["points"] = "high"
        decisions = plan_imports([clip], notes, config)
        self.assertEqual(decisions[0].conflict_resolution, "tie")
        self.assertEqual(decisions[0].action, "skip")

    def test_first_empty_slot_is_reserved(self) -> None:
        clips = [self._clip("c1", "one"), self._clip("c2", "two")]
        notes = [
            {
                "note_id": 5,
                "deck_name": "target",
                "fields": {"Video1": "", "Video2": "", "Expression": "one"},
            }
        ]
        config = normalize_config(
            {
                "import_minimum": "low",
                "import_rules": [
                    {"source": "sentence", "compare": "contains", "target_field": "Expression", "points": "low"},
                ],
                "field_sets": [
                    {"enabled": True, "name": "Slot 1", "video": "Video1"},
                    {"enabled": True, "name": "Slot 2", "video": "Video2"},
                ],
            }
        )
        notes[0]["fields"]["Expression"] = "one two"
        decisions = plan_imports(clips, notes, config)
        self.assertEqual([item.target_field_set_index for item in decisions], [1, 2])
        self.assertTrue(all(item.action == "import" for item in decisions))

        notes[0]["fields"]["Video1"] = '<video src="already.mkv"></video>'
        notes[0]["fields"]["Video2"] = '<video src="also.mkv"></video>'
        decisions = plan_imports(clips, notes, config)
        self.assertTrue(all(item.conflict_resolution == "no_slot" for item in decisions))

    def test_optional_fields_are_not_written_when_unmapped(self) -> None:
        clip = self._clip()
        note = {"Video": "old", "Sentence": "keep", "Notes": "keep"}
        field_set = FieldSet(video="Video", sentence="", secondary="", miscinfo="")
        updated = apply_fields_to_note(note, field_set, clip, "c1.mkv")
        self.assertEqual(updated, ["Video"])
        self.assertIn("c1.mkv", note["Video"])
        self.assertEqual(note["Sentence"], "keep")
        self.assertEqual(note["Notes"], "keep")
        self.assertEqual(describe_writes(field_set), "Video")

    def test_exact_match_skips_unrelated_notes(self) -> None:
        clip = self._clip()
        notes = [
            {"note_id": index, "deck_name": "target", "sort_field_value": f"other {index}", "fields": {"Expression": f"other {index}"}}
            for index in range(1, 300)
        ]
        notes.append({"note_id": 9000, "deck_name": "target", "sort_field_value": "hello", "fields": {"Expression": "hello"}})
        config = normalize_config(
            {
                "import_minimum": "medium",
                "import_rules": [
                    {"source": "sentence", "compare": "exact", "target_field": "Expression", "points": "high"},
                ],
                "field_sets": [{"enabled": True, "video": "Video"}],
            }
        )
        ranked = rank_notes(clip, notes, config)
        self.assertEqual([item.note_id for item in ranked], [9000])
        self.assertEqual(score_note(clip, notes[-1], config).score, ranked[0].score)
        decisions = plan_imports([clip], notes, config)
        self.assertEqual(decisions[0].note_id, 9000)
        self.assertEqual(decisions[0].suggestion_total, 1)
        self.assertEqual(decisions[0].action, "import")

    def test_tie_includes_matches_outside_the_old_window(self) -> None:
        clip = self._clip()
        notes = [
            {"note_id": index, "deck_name": "target", "fields": {"Expression": "hello"}}
            for index in range(1, 12)
        ]
        config = normalize_config(
            {
                "import_minimum": "low",
                "import_rules": [
                    {"source": "sentence", "compare": "exact", "target_field": "Expression", "points": "high"},
                ],
                "field_sets": [{"enabled": True, "video": "Video"}],
            }
        )
        decisions = plan_imports([clip], notes, config)
        self.assertEqual(decisions[0].conflict_resolution, "tie")
        self.assertIsNone(decisions[0].note_id)
        self.assertEqual(decisions[0].suggestion_total, 11)
        self.assertLessEqual(len(decisions[0].suggestions), 40)

    def test_filename_contains_uses_the_same_score(self) -> None:
        clip = self._clip()
        notes = [
            {"note_id": 1, "deck_name": "target", "fields": {"Video": "nope.mkv"}},
            {"note_id": 2, "deck_name": "target", "fields": {"Video": '<video src="c1.mkv"></video>'}},
        ]
        config = normalize_config(
            {
                "import_minimum": "low",
                "import_rules": [
                    {"source": "video_filename", "compare": "contains", "target_field": "Video", "points": "medium"},
                ],
                "field_sets": [{"enabled": True, "video": "Video"}],
            }
        )
        ranked = rank_notes(clip, notes, config)
        self.assertEqual(ranked[0].note_id, 2)
        self.assertEqual(ranked[0].score, score_note(clip, notes[1], config).score)
        self.assertEqual(score_note(clip, notes[0], config).score, 0)

    def test_deck_names_nest_under_parents(self) -> None:
        tree = group_deck_names(["Japanese::Anime::S1", "Japanese", "Other", "Japanese::Anime", ""])
        self.assertEqual([node["name"] for node in tree], ["Japanese", "Other"])
        japanese = tree[0]
        self.assertEqual(japanese["full_name"], "Japanese")
        self.assertEqual(japanese["children"][0]["name"], "Anime")
        self.assertEqual(japanese["children"][0]["full_name"], "Japanese::Anime")
        self.assertEqual(japanese["children"][0]["children"][0]["full_name"], "Japanese::Anime::S1")

    def test_missing_parent_deck_is_still_shown(self) -> None:
        tree = group_deck_names(["Parent::Child"])
        self.assertEqual(tree[0]["full_name"], "")
        self.assertEqual(tree[0]["children"][0]["full_name"], "Parent::Child")

    def test_manual_note_override(self) -> None:
        clip = self._clip()
        notes = [
            {"note_id": 1, "deck_name": "target", "fields": {"Video": "", "Expression": "hello"}},
            {"note_id": 2, "deck_name": "target", "fields": {"Video": "", "Expression": "other"}},
        ]
        config = normalize_config(
            {
                "import_minimum": "high",
                "import_rules": [],
                "field_sets": [{"enabled": True, "video": "Video", "sentence": "Expression"}],
            }
        )
        decisions = plan_imports([clip], notes, config, overrides={"c1": 2})
        self.assertEqual(decisions[0].note_id, 2)
        self.assertEqual(decisions[0].target_field_set_index, 1)
        self.assertEqual(decisions[0].action, "import")

    def test_rewrite_video_html(self) -> None:
        original = '<video controls="" src="old.mkv" class="x"></video>'
        self.assertIn('src="new.mkv"', rewrite_video_html(original, "new.mkv"))
        self.assertIn("class", rewrite_video_html(original, "new.mkv"))
        self.assertEqual(rewrite_video_html("", "new.mkv"), '<video controls="" src="new.mkv"></video>')

    def test_clip_label(self) -> None:
        clip = {"anki": {"sort_field_value": "皮肉"}, "media": {"filename": "clip.mkv"}}
        self.assertIn("皮肉", clip_label(clip))

    def test_clip_search_fields_include_html_and_extras(self) -> None:
        clip = {
            "anki": {
                "fields": {
                    "sentence": {"name": "Sentence", "value": "hello there"},
                    "secondary": {"name": "Secondary", "value": "   "},
                    "miscinfo": {"name": "Notes", "value": "Source: file.mp4"},
                    "video": {"name": "Video", "value": '<video controls="" src="a_b.mp4"></video>'},
                },
                "extra_fields": {"expression": {"name": "expression", "value": "皮肉"}},
                "audio_fields": {"audio": {"name": "Audio", "value": "[sound:clip.mp3]"}},
            }
        }
        fields = dict(clip_search_fields(clip))
        self.assertEqual(fields["Sentence"], "hello there")
        self.assertEqual(fields["Miscinfo"], "Source: file.mp4")
        self.assertIn("<video", fields["Video"])
        self.assertEqual(fields["Extra: expression"], "皮肉")
        self.assertEqual(fields["Audio: Audio"], "[sound:clip.mp3]")
        self.assertNotIn("Secondary", fields)

    def test_quote_anki_search_term_matches_browser_escaping(self) -> None:
        self.assertEqual(quote_anki_search_term("hello"), "hello")
        self.assertEqual(quote_anki_search_term("hello there"), '"hello there"')
        self.assertEqual(quote_anki_search_term('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(quote_anki_search_term("a_b*c"), r"a\_b\*c")
        self.assertEqual(quote_anki_search_term("Source: x"), r'"Source\: x"')
        self.assertEqual(quote_anki_search_term("a\nb"), '"a b"')
        quoted = quote_anki_search_term('<video controls="" src="a_b.mp4"></video>')
        self.assertTrue(quoted.startswith('"'))
        self.assertIn(r"src=\"a\_b.mp4\"", quoted)

    def test_insert_search_term_keeps_existing_query(self) -> None:
        updated, cursor = insert_search_term("nid:12", '"hello there"', 6)
        self.assertEqual(updated, 'nid:12 "hello there"')
        self.assertEqual(cursor, len(updated))
        replaced, cursor = insert_search_term("keep replace tail", '"term"', 5, 5, 12)
        self.assertEqual(replaced, 'keep "term" tail')
        self.assertEqual(cursor, len('keep "term"'))


class JobTreeTests(unittest.TestCase):
    def test_key_paths_ignore_list_indexes_and_rebuild_keeps_history(self) -> None:
        record = {
            "schema_version": 4,
            "anki": {"note_id": 1},
            "commands": [{"encode_command": "ffmpeg"}],
            "old": {"gone": True},
        }
        self.assertIn("commands.encode_command", collect_key_paths(record))
        self.assertNotIn("commands.0", collect_key_paths(record))
        tree = apply_record_samples(empty_job_tree("Deck", "tree-1"), {"id:one": sorted(collect_key_paths(record))})
        tree["keys"]["old.gone"]["enabled"] = False
        rebuilt = apply_record_samples(
            tree,
            {"id:one": ["schema_version", "anki", "anki.note_id"], "id:two": ["schema_version", "anki", "anki.note_id", "result"]},
        )
        self.assertEqual(rebuilt["keys"]["old.gone"]["count"], 0)
        self.assertFalse(rebuilt["keys"]["old.gone"]["enabled"])
        self.assertTrue(rebuilt["keys"]["result"]["enabled"])
        self.assertEqual(rebuilt["keys"]["schema_version"]["count"], 2)
        self.assertEqual(rebuilt["keys"]["anki.note_id"]["count"], 2)


class PrepareJobTests(unittest.TestCase):
    def test_prepare_applies_filter_and_privacy(self) -> None:
        record = {
            "log_type": "clip_encode",
            "anki": {"note_id": 5, "deck_name": "x"},
            "source": {"path": r"E:\secret\movie.mp4"},
            "commands": {"encode_command": ["ffmpeg"]},
        }
        payload = prepare_job_payload(
            record,
            selection={"commands": False, "anki.deck_name": False},
            privacy_mode="filename_only",
            privacy_parents=1,
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertNotIn("commands", payload)
        self.assertNotIn("deck_name", payload["anki"])
        self.assertEqual(payload["source"]["path"], r"...\movie.mp4")


class ExportIntegrationTests(unittest.TestCase):
    def test_export_links_sample_record_and_copies_media(self) -> None:
        sample = Path(__file__).resolve().parent.parent / "ai_request_samples" / "2026-08-13T22-59-36+02-00_Pragmata_2026.07.06_-_17.46.11.04.DVR.mp4_36683_88333.json"
        if not sample.exists():
            self.skipTest("sample record is not available")
        record = json.loads(sample.read_text(encoding="utf-8"))
        filename = record["output"]["filename"]
        with tempfile.TemporaryDirectory() as folder:
            media_dir = Path(folder) / "anki_media"
            media_dir.mkdir()
            (media_dir / filename).write_bytes(b"clip-bytes")
            index_dir = Path(folder) / "logs"
            record_rel = Path("records") / "2026-08" / sample.name
            (index_dir / record_rel).parent.mkdir(parents=True)
            (index_dir / record_rel).write_text(json.dumps(record), encoding="utf-8")
            index_payload = {
                "schema_version": 4,
                "records": [
                    {
                        "record_id": record["record_id"],
                        "created_at": record["created_at"],
                        "output_path": str(media_dir / filename),
                        "record_path": record_rel.as_posix(),
                    }
                ],
            }
            (index_dir / "index.json").write_text(json.dumps(index_payload), encoding="utf-8")
            field_set = FieldSet(
                enabled=True,
                name="Field Set 1",
                index=1,
                video="picture-subs2srs",
                sentence="example-sentence-subs2srs",
                secondary="example-sentence-translation-subs2srs",
                miscinfo="notes",
            )
            clips = clips_from_note_values(
                note_id=999,
                deck_name="jap_words",
                model_name="Yomi-custom",
                sort_field_name="expression",
                sort_field_value="皮肉",
                values={
                    "picture-subs2srs": record["anki"]["fields"]["video"]["value"],
                    "example-sentence-subs2srs": record["anki"]["fields"]["sentence"]["value"],
                    "example-sentence-translation-subs2srs": "",
                    "notes": record["anki"]["fields"]["miscinfo"]["value"],
                },
                field_sets=[field_set],
                media_directory=str(media_dir),
            )
            dest = Path(folder) / "library"
            tree = apply_record_samples(
                empty_job_tree("jap_words", "tree-1"),
                {"id:sample": sorted(collect_key_paths(record))},
            )
            for path, state in tree["keys"].items():
                if path == "commands" or path.startswith("commands.") or path == "anki.fields" or path.startswith("anki.fields."):
                    state["enabled"] = False
            config = normalize_config(
                {
                    "note_type": "Yomi-custom",
                    "field_sets": [field_set.to_dict()],
                    "job_records_enabled": True,
                    "job_index_path": str(index_dir / "index.json"),
                    "path_privacy_mode": "filename_only",
                    "job_record_trees": [tree],
                    "active_job_record_tree": "tree-1",
                }
            )
            report = export_clips(
                clips,
                config=config,
                destination=str(dest),
                mode="fresh",
                profile_name="test-profile",
            )
            self.assertEqual(report.clips_found, 1)
            self.assertEqual(report.media_copied, 1)
            self.assertEqual(report.records_partial, 1)
            library = load_library(str(dest / "clip_library.json"))
            self.assertIsNotNone(library)
            assert library is not None
            clip = library["clips"][0]
            self.assertEqual(clip["link"]["status"], "partial")
            self.assertIn("note_id", clip["link"]["mismatches"])
            self.assertTrue((dest / "media" / filename).exists())
            self.assertNotIn("commands", clip["job"])
            self.assertNotIn("fields", clip["job"]["anki"])
            self.assertTrue(str(clip["job"]["source"]["path"]).startswith("..."))
            self.assertTrue((media_dir / filename).exists())
            self.assertFalse((dest / "records").exists())
            self.assertEqual(report.job_key_notice, "")

    def test_export_omits_unknown_job_keys_and_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            media_dir = Path(folder) / "anki_media"
            media_dir.mkdir()
            (media_dir / "clip.mkv").write_bytes(b"clip-bytes")
            index_dir = Path(folder) / "logs"
            record_rel = Path("records") / "clip.json"
            (index_dir / record_rel).parent.mkdir(parents=True)
            record = {
                "record_id": "rec-1",
                "output": {"filename": "clip.mkv"},
                "anki": {"note_id": 1},
                "future_key": {"nested": True},
            }
            (index_dir / record_rel).write_text(json.dumps(record), encoding="utf-8")
            (index_dir / "index.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "record_id": "rec-1",
                                "created_at": "2026-08-01T00:00:00+00:00",
                                "output_path": str(media_dir / "clip.mkv"),
                                "record_path": record_rel.as_posix(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            field_set = FieldSet(enabled=True, name="Field Set 1", index=1, video="picture")
            clips = clips_from_note_values(
                note_id=1,
                deck_name="Deck",
                model_name="Yomi",
                sort_field_name="expression",
                sort_field_value="word",
                values={"picture": '<video src="clip.mkv"></video>'},
                field_sets=[field_set],
                media_directory=str(media_dir),
            )
            tree = apply_record_samples(empty_job_tree("Deck", "tree-1"), {"id:old": ["output", "output.filename"]})
            config = normalize_config(
                {
                    "note_type": "Yomi",
                    "field_sets": [field_set.to_dict()],
                    "job_records_enabled": True,
                    "job_index_path": str(index_dir / "index.json"),
                    "job_record_trees": [tree],
                    "active_job_record_tree": "tree-1",
                }
            )
            report = export_clips(clips, config=config, destination=str(Path(folder) / "library"), mode="fresh")
            library = load_library(str(Path(folder) / "library" / "clip_library.json"))
            assert library is not None
            job = library["clips"][0]["job"]
            self.assertIn("filename", job["output"])
            self.assertNotIn("future_key", job)
            self.assertNotIn("anki", job)
            self.assertIn("future_key", report.job_key_notice)
            self.assertIsNotNone(active_job_tree(config))


class ExtraFieldExportTests(unittest.TestCase):
    def test_generic_and_audio_are_exported(self) -> None:
        field_set = FieldSet(enabled=True, name="Field Set 1", index=1, video="picture-subs2srs", sentence="example-sentence-subs2srs")
        with tempfile.TemporaryDirectory() as folder:
            media_dir = Path(folder) / "anki_media"
            media_dir.mkdir()
            (media_dir / "clip.mkv").write_bytes(b"video")
            (media_dir / "word.mp3").write_bytes(b"audio-a")
            (media_dir / "word2.ogg").write_bytes(b"audio-b")
            clips = clips_from_note_values(
                note_id=1,
                deck_name="jap_words",
                model_name="Yomi-custom",
                sort_field_name="expression",
                sort_field_value="皮肉",
                values={
                    "picture-subs2srs": '<video src="clip.mkv"></video>',
                    "example-sentence-subs2srs": "hello",
                    "expression": "皮肉",
                    "audio": "[sound:word.mp3][sound:word2.ogg]",
                },
                field_sets=[field_set],
                media_directory=str(media_dir),
                extra_fields=[ExtraField(enabled=True, name="Field 1", field="expression", kind="generic")],
                audio_fields=[ExtraField(enabled=True, name="Audio 1", field="audio", kind="audio")],
            )
            dest = Path(folder) / "library"
            config = normalize_config(
                {
                    "note_type": "Yomi-custom",
                    "field_sets": [field_set.to_dict()],
                    "generic_fields": [{"enabled": True, "name": "Field 1", "field": "expression"}],
                    "audio_fields": [{"enabled": True, "name": "Audio 1", "field": "audio"}],
                    "anki_export_keys": {"anki.deck_name": False},
                }
            )
            export_clips(clips, config=config, destination=str(dest), mode="fresh")
            library = load_library(str(dest / "clip_library.json"))
            assert library is not None
            clip = library["clips"][0]
            self.assertEqual(library["schema_version"], 4)
            self.assertNotIn("deck_name", clip["anki"])
            self.assertNotIn("extra_fields", clip["anki"])
            self.assertNotIn("audio_fields", clip["anki"])
            self.assertNotIn("model_name", clip["anki"])
            self.assertEqual(clip["anki"]["note_id"], 1)
            note = library["notes"]["1"]
            self.assertNotIn("deck_name", note)
            self.assertEqual(note["model_name"], "Yomi-custom")
            self.assertEqual(note["sort_field_value"], "皮肉")
            self.assertEqual(note["extra_fields"]["expression"]["value"], "皮肉")
            self.assertNotIn("name", note["extra_fields"]["expression"])
            self.assertNotIn("note_id", note)
            self.assertNotIn("field_set_name", clip["anki"])
            self.assertNotIn("enabled", library["field_sets"][0])
            self.assertEqual(clip["anki"]["field_set_index"], library["field_sets"][0]["index"])
            self.assertNotIn("files", note["audio_fields"]["audio"])
            self.assertNotIn("name", note["audio_fields"]["audio"])
            self.assertEqual(
                [item["filename"] for item in note["audio_fields"]["audio"]["media"]],
                ["word.mp3", "word2.ogg"],
            )
            self.assertNotIn("field", note["audio_fields"]["audio"]["media"][0])
            self.assertNotIn("audio", clip.get("media") or {})
            bound = attach_library_notes(clip, library["notes"])
            resolved = resolve_clip_anki(bound)
            self.assertEqual(
                [item["filename"] for item in resolved["audio_fields"]["audio"]["media"]],
                ["word.mp3", "word2.ogg"],
            )
            self.assertEqual(resolved["fields"]["sentence"]["value"], "hello")
            self.assertTrue((dest / "media" / "word.mp3").exists())
            self.assertTrue((dest / "media" / "word2.ogg").exists())
            self.assertEqual(
                library_audio_paths(str(dest), bound),
                [
                    ("word.mp3", str(dest / "media" / "word.mp3")),
                    ("word2.ogg", str(dest / "media" / "word2.ogg")),
                ],
            )
            self.assertEqual(
                library_audio_fields(str(dest), bound),
                [
                    (
                        "audio",
                        [
                            ("word.mp3", str(dest / "media" / "word.mp3")),
                            ("word2.ogg", str(dest / "media" / "word2.ogg")),
                        ],
                    )
                ],
            )
            self.assertTrue((media_dir / "word.mp3").exists())
            self.assertFalse((dest / "records").exists())
            tree = anki_export_key_tree(config)
            self.assertIn("anki.extra_fields.expression", tree)
            self.assertIn("anki.audio_fields.audio", tree)
            filtered = filter_anki_section(clip["anki"], effective_anki_keys({"anki.extra_fields": False}, config))
            self.assertNotIn("extra_fields", filtered)

    def test_audio_paths_fall_back_to_legacy_audio_fields(self) -> None:
        root = Path("C:\\library")
        clip = {
            "media": {},
            "anki": {
                "audio_fields": {
                    "audio": {"files": ["word.mp3", "word2.ogg"]},
                    "pitch-accents-extended": {"files": ["pitch.mp3"]},
                }
            },
        }

        self.assertEqual(
            library_audio_paths(str(root), clip),
            [
                ("word.mp3", str(root / "media" / "word.mp3")),
                ("word2.ogg", str(root / "media" / "word2.ogg")),
                ("pitch.mp3", str(root / "media" / "pitch.mp3")),
            ],
        )
        self.assertEqual(
            library_audio_fields(str(root), clip),
            [
                (
                    "audio",
                    [
                        ("word.mp3", str(root / "media" / "word.mp3")),
                        ("word2.ogg", str(root / "media" / "word2.ogg")),
                    ],
                ),
                ("pitch-accents-extended", [("pitch.mp3", str(root / "media" / "pitch.mp3"))]),
            ],
        )


def _bare_clip(note_id: int, filename: str, **anki: object) -> DiscoveredClip:
    return DiscoveredClip(
        note_id=note_id,
        deck_name=str(anki.get("deck_name") or "Deck"),
        model_name=str(anki.get("model_name") or "Yomi"),
        sort_field_name=str(anki.get("sort_field_name") or "expression"),
        sort_field_value=str(anki.get("sort_field_value") or "word"),
        field_set_index=int(anki.get("field_set_index") or 1),
        field_set_name=str(anki.get("field_set_name") or "Set"),
        filename=filename,
        media_path=filename,
        fields={
            "sentence": FieldValue("sentence", str(anki.get("sentence") or "")),
            "miscinfo": FieldValue("miscinfo", str(anki.get("miscinfo") or "")),
        },
    )


class NoteReferenceTests(unittest.TestCase):
    def test_shared_note_fields_are_stored_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "library"
            clips = [
                _bare_clip(1639572914188, "a.mp4", sentence="one", miscinfo="Source: a", model_name="Yomi-custom", sort_field_value="疾っくに"),
                _bare_clip(
                    1639572914188,
                    "b.mp4",
                    sentence="two",
                    miscinfo="Source: b",
                    model_name="Yomi-custom",
                    sort_field_value="疾っくに",
                    field_set_index=2,
                ),
            ]
            config = normalize_config({"note_type": "Yomi-custom", "field_sets": [FieldSet(video="picture").to_dict()]})
            export_clips(clips, config=config, destination=str(dest), mode="fresh")
            library = load_library(str(dest / "clip_library.json"))
            assert library is not None
            self.assertEqual(list(library["notes"]), ["1639572914188"])
            note = library["notes"]["1639572914188"]
            self.assertEqual(note["model_name"], "Yomi-custom")
            self.assertEqual(note["sort_field_value"], "疾っくに")
            sentences = [clip["anki"]["fields"]["sentence"]["value"] for clip in library["clips"]]
            self.assertEqual(sentences, ["one", "two"])
            for clip in library["clips"]:
                self.assertNotIn("model_name", clip["anki"])
                self.assertNotIn("sort_field_value", clip["anki"])
                self.assertEqual(clip["anki"]["note_id"], 1639572914188)
                self.assertIn("miscinfo", clip["anki"]["fields"])

    def test_schema_1_lifts_duplicated_fields_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "clip_library.json")
            write_library(
                path,
                {
                    "format": "clip_library",
                    "schema_version": 1,
                    "clips": [
                        {
                            "id": "a",
                            "anki": {
                                "note_id": 7,
                                "model_name": "Yomi",
                                "sort_field_value": "x",
                                "fields": {"sentence": {"name": "s", "value": "s1"}},
                            },
                        },
                        {
                            "id": "b",
                            "anki": {
                                "note_id": 7,
                                "model_name": "Yomi",
                                "sort_field_value": "x",
                                "fields": {"sentence": {"name": "s", "value": "s2"}},
                            },
                        },
                    ],
                },
            )
            loaded = load_library(path)
            assert loaded is not None
            self.assertEqual(loaded["schema_version"], 4)
            self.assertEqual(loaded["notes"]["7"]["model_name"], "Yomi")
            self.assertNotIn("note_id", loaded["notes"]["7"])
            self.assertEqual(loaded["notes"]["7"]["sort_field_value"], "x")
            self.assertNotIn("model_name", loaded["clips"][0]["anki"])
            self.assertEqual(loaded["clips"][0]["anki"]["note_id"], 7)
            self.assertEqual(loaded["clips"][1]["anki"]["fields"]["sentence"]["value"], "s2")
            on_disk = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(on_disk["schema_version"], 1)
            self.assertIn("model_name", on_disk["clips"][0]["anki"])

    def test_update_refreshes_shared_note_and_drops_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "library"
            config = normalize_config({"note_type": "Yomi", "field_sets": [FieldSet(video="picture").to_dict()]})
            export_clips(
                [_bare_clip(1, "a.mp4", sort_field_value="old", sentence="keep")],
                config=config,
                destination=str(dest),
                mode="fresh",
            )
            library_path = dest / "clip_library.json"
            stored = json.loads(library_path.read_text(encoding="utf-8"))
            stored["notes"]["999"] = {"note_id": 999, "model_name": "Gone"}
            library_path.write_text(json.dumps(stored), encoding="utf-8")
            report = export_clips(
                [_bare_clip(1, "b.mp4", sort_field_value="new", sentence="added", field_set_index=2)],
                config=config,
                destination=str(dest),
                mode="update",
            )
            library = load_library(str(library_path))
            assert library is not None
            self.assertEqual(library["notes"]["1"]["sort_field_value"], "new")
            self.assertNotIn("999", library["notes"])
            self.assertEqual(len(library["clips"]), 2)
            self.assertFalse(any("differed" in warning for warning in report.warnings))

    def test_note_id_stays_as_link_when_its_export_key_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "library"
            config = normalize_config(
                {
                    "note_type": "Yomi",
                    "field_sets": [FieldSet(video="picture").to_dict()],
                    "anki_export_keys": {"anki.note_id": False},
                }
            )
            export_clips([_bare_clip(4, "a.mp4", model_name="Yomi-custom")], config=config, destination=str(dest), mode="fresh")
            library = load_library(str(dest / "clip_library.json"))
            assert library is not None
            self.assertEqual(library["clips"][0]["anki"]["note_id"], 4)
            self.assertNotIn("note_id", library["notes"]["4"])
            self.assertEqual(library["notes"]["4"]["model_name"], "Yomi-custom")

    def test_conflicting_shared_fields_keep_latest_and_warn(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "library"
            config = normalize_config({"note_type": "Yomi", "field_sets": [FieldSet(video="picture").to_dict()]})
            report = export_clips(
                [
                    _bare_clip(8, "a.mp4", sort_field_value="first"),
                    _bare_clip(8, "b.mp4", sort_field_value="second", field_set_index=2),
                ],
                config=config,
                destination=str(dest),
                mode="fresh",
            )
            library = load_library(str(dest / "clip_library.json"))
            assert library is not None
            self.assertEqual(library["notes"]["8"]["sort_field_value"], "second")
            self.assertTrue(any("sort_field_value" in warning for warning in report.warnings))

    def test_clip_audio_media_is_stored_once_on_the_note(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            media_dir = Path(folder) / "anki_media"
            media_dir.mkdir()
            (media_dir / "a.mp4").write_bytes(b"a")
            (media_dir / "b.mp4").write_bytes(b"b")
            (media_dir / "word.mp3").write_bytes(b"audio")
            clips = [
                _bare_clip(3, "a.mp4", sentence="one"),
                _bare_clip(3, "b.mp4", sentence="two", field_set_index=2),
            ]
            for clip in clips:
                clip.media_path = str(media_dir / clip.filename)
                clip.audio_fields = {"audio": {"name": "audio", "value": "[sound:word.mp3]", "files": ["word.mp3"]}}
            config = normalize_config({"note_type": "Yomi", "field_sets": [FieldSet(video="picture").to_dict()]})
            report = export_clips(clips, config=config, destination=str(Path(folder) / "library"), mode="fresh")
            library = load_library(str(Path(folder) / "library" / "clip_library.json"))
            assert library is not None
            self.assertEqual(report.media_copied, 3)
            for clip in library["clips"]:
                self.assertNotIn("audio", clip["media"])
            media = library["notes"]["3"]["audio_fields"]["audio"]["media"]
            self.assertEqual(media, [
                {
                    "filename": "word.mp3",
                    "size_bytes": 5,
                    "exists": True,
                }
            ])
            self.assertNotIn("relative_path", library["clips"][0]["media"])

    def test_schema_2_audio_lists_move_onto_the_note(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "clip_library.json")
            write_library(
                path,
                {
                    "format": "clip_library",
                    "schema_version": 2,
                    "notes": {
                        "9": {
                            "note_id": 9,
                            "audio_fields": {"audio": {"name": "audio", "value": "[sound:word.mp3]", "files": ["word.mp3"]}},
                        }
                    },
                    "clips": [
                        {
                            "id": "a",
                            "media": {"filename": "a.mp4", "audio": [{"filename": "word.mp3", "relative_path": "media/word.mp3", "size_bytes": 5, "exists": True, "field": "audio"}]},
                            "anki": {"note_id": 9, "fields": {"sentence": {"value": "s1"}}},
                        },
                        {
                            "id": "b",
                            "media": {"filename": "b.mp4", "audio": [{"filename": "word.mp3", "relative_path": "media/word.mp3", "size_bytes": 5, "exists": True, "field": "audio"}]},
                            "anki": {"note_id": 9, "fields": {"sentence": {"value": "s2"}}},
                        },
                    ],
                },
            )
            loaded = load_library(path)
            assert loaded is not None
            self.assertEqual(loaded["schema_version"], 4)
            self.assertNotIn("audio", loaded["clips"][0]["media"])
            self.assertNotIn("audio", loaded["clips"][1]["media"])
            self.assertNotIn("note_id", loaded["notes"]["9"])
            self.assertNotIn("files", loaded["notes"]["9"]["audio_fields"]["audio"])
            self.assertNotIn("name", loaded["notes"]["9"]["audio_fields"]["audio"])
            self.assertEqual(loaded["notes"]["9"]["audio_fields"]["audio"]["media"][0]["filename"], "word.mp3")
            self.assertNotIn("note_id", loaded["notes"]["9"]["audio_fields"]["audio"]["media"][0])


    def test_schema_3_drops_derivable_keys_and_keeps_field_set_index(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "clip_library.json")
            write_library(
                path,
                {
                    "format": "clip_library",
                    "schema_version": 3,
                    "field_sets": [
                        {"enabled": True, "index": 2, "name": "Second", "video": "Video2"},
                    ],
                    "generic_fields": [{"enabled": True, "name": "Field 1", "field": "expression", "kind": "generic"}],
                    "clips": [
                        {
                            "id": "anki:9:2:a.mp4",
                            "media": {"filename": "a.mp4", "relative_path": "media/a.mp4", "exists": True},
                            "anki": {
                                "note_id": 9,
                                "field_set_index": 2,
                                "field_set_name": "Second",
                                "fields": {"sentence": {"name": "Sentence", "value": "hello"}},
                            },
                        }
                    ],
                    "notes": {"9": {"note_id": 9, "extra_fields": {"expression": {"name": "expression", "value": "word"}}}},
                },
            )
            loaded = load_library(path)
            assert loaded is not None
            clip = loaded["clips"][0]
            self.assertEqual(clip["anki"]["field_set_index"], 2)
            self.assertNotIn("field_set_name", clip["anki"])
            self.assertNotIn("relative_path", clip["media"])
            self.assertNotIn("enabled", loaded["field_sets"][0])
            self.assertNotIn("kind", loaded["generic_fields"][0])
            self.assertNotIn("note_id", loaded["notes"]["9"])
            self.assertNotIn("name", loaded["notes"]["9"]["extra_fields"]["expression"])
            attached = dict(clip)
            attached["_library_field_sets"] = loaded["field_sets"]
            self.assertEqual(field_set_display_name(attached), "Second")

    def test_update_keeps_unreferenced_index_used_by_older_clips(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "library"
            media = Path(folder) / "anki_media"
            media.mkdir()
            (media / "a.mp4").write_bytes(b"a")
            (media / "b.mp4").write_bytes(b"b")
            first = FieldSet(enabled=True, name="Keep", index=1, video="Video")
            second = FieldSet(enabled=True, name="Later", index=2, video="Video2")
            config = normalize_config({"note_type": "Yomi", "field_sets": [first.to_dict(), second.to_dict()]})
            clips = [_bare_clip(1, "a.mp4"), _bare_clip(1, "b.mp4", field_set_index=2)]
            for clip in clips:
                clip.media_path = str(media / clip.filename)
            export_clips(clips, config=config, destination=str(dest), mode="fresh")
            renamed = normalize_config(
                {"note_type": "Yomi", "field_sets": [{**first.to_dict(), "name": "Renamed"}]}
            )
            export_clips([], config=renamed, destination=str(dest), mode="update")
            library = load_library(str(dest / "clip_library.json"))
            assert library is not None
            by_index = {item["index"]: item["name"] for item in library["field_sets"]}
            self.assertEqual(by_index[1], "Renamed")
            self.assertEqual(by_index[2], "Later")
            self.assertEqual(
                {clip["anki"]["field_set_index"] for clip in library["clips"]},
                {1, 2},
            )


class IdentityAndImportSplitTests(unittest.TestCase):
    def test_default_clip_label_keeps_sort_field_then_filename(self) -> None:
        clip = {"id": "c1", "anki": {"sort_field_value": "皮肉"}, "media": {"filename": "clip.mkv"}}
        self.assertEqual(clip_label(clip), "皮肉 — clip.mkv")

    def test_clip_identity_uses_requested_order_and_skips_missing_keys(self) -> None:
        clip = {
            "id": "c1",
            "_clip_number": 4,
            "media": {"filename": "clip.mkv"},
            "anki": {
                "fields": {"miscinfo": {"name": "Notes", "value": "from notes"}},
                "extra_fields": {"reading": {"name": "reading", "value": "よみ"}},
            },
        }
        identity = [{"source": "extra:reading"}, {"source": "miscinfo"}, {"source": "sort_field"}, {"source": "clip_number"}]
        self.assertEqual(clip_label(clip, identity), "よみ — from notes — 4")
        choices = [source for source, _label in clip_identity_choices([clip])]
        self.assertIn("extra:reading", choices)
        self.assertIn("miscinfo", choices)
        self.assertIn("clip_number", choices)
        self.assertNotIn("sort_field", choices)

    def test_note_identity_defaults_to_sort_field_and_note_id(self) -> None:
        note = {
            "note_id": 42,
            "deck_name": "Target",
            "sort_field_name": "Expression",
            "sort_field_value": "皮肉",
            "fields": {"Expression": "皮肉", "Meaning": "irony"},
        }
        self.assertEqual(format_note_label(note), "皮肉 — 42")
        identity = [{"source": "sort_field"}, {"source": "field:Meaning"}, {"source": "deck_name"}]
        self.assertEqual(format_note_label(note, identity), "皮肉 — irony — Target")

    def test_import_mapping_is_copied_once_then_left_independent(self) -> None:
        first = normalize_config({"note_type": "Export", "field_sets": [{"enabled": True, "video": "ExportVideo"}]})
        self.assertTrue(first["import_mapping_initialized"])
        self.assertEqual(first["import_note_type"], "Export")
        self.assertEqual(first["import_field_sets"][0]["video"], "ExportVideo")
        second = normalize_config({**first, "note_type": "Other", "field_sets": [{"enabled": True, "video": "OtherVideo"}]})
        self.assertEqual(second["note_type"], "Other")
        self.assertEqual(second["field_sets"][0]["video"], "OtherVideo")
        self.assertEqual(second["import_note_type"], "Export")
        self.assertEqual(second["import_field_sets"][0]["video"], "ExportVideo")

    def test_import_uses_import_field_sets_without_an_export_note_type(self) -> None:
        clip = {
            "id": "c1",
            "media": {"filename": "c1.mkv"},
            "anki": {"fields": {"sentence": {"value": "hello"}}},
        }
        notes = [{"note_id": 7, "deck_name": "target", "fields": {"Expression": "hello", "Video": ""}}]
        config = normalize_config(
            {
                "import_mapping_initialized": True,
                "import_note_type": "Target",
                "import_field_sets": [{"enabled": True, "name": "Imported", "video": "Video"}],
                "import_minimum": "low",
                "import_rules": [
                    {"source": "sentence", "compare": "exact", "target_field": "Expression", "points": "high"},
                ],
                "clip_identity": [{"source": "clip_number"}],
            }
        )
        clip["_clip_number"] = 2
        self.assertEqual(config["note_type"], "")
        self.assertFalse(config["field_sets"][0]["video"])
        decisions = plan_imports([clip], notes, config)
        self.assertEqual(decisions[0].action, "import")
        self.assertEqual(decisions[0].note_id, 7)
        self.assertEqual(decisions[0].target_field_set_index, 1)
        self.assertEqual(decisions[0].label, "2")


class SampleAndProfileTests(unittest.TestCase):
    def test_job_examples_skip_empty_and_replace_with_new_values(self) -> None:
        record = {"source": {"path": None, "name": ""}, "ok": False, "count": 0, "title": "real"}
        examples = collect_key_examples(record)
        self.assertNotIn("source.path", examples)
        self.assertNotIn("source.name", examples)
        self.assertEqual(examples["ok"], "false")
        self.assertEqual(examples["count"], "0")
        self.assertEqual(examples["title"], "real")
        self.assertTrue(examples["source"].startswith("{"))

        tree = apply_key_examples(empty_job_tree("Deck", "tree"), {"source.path": "old.mkv", "title": "old"})
        kept = apply_key_examples(tree, {"source.path": "", "title": None})
        self.assertEqual(kept["examples"]["source.path"], "old.mkv")
        self.assertEqual(kept["examples"]["title"], "old")
        replaced = apply_key_examples(kept, {"source.path": "new.mkv"})
        self.assertEqual(replaced["examples"]["source.path"], "new.mkv")
        self.assertEqual(replaced["examples"]["title"], "old")
        stored = normalize_config(
            {
                "job_record_trees": [replaced],
                "active_job_record_tree": "tree",
            }
        )
        self.assertEqual(active_job_tree(stored)["examples"]["source.path"], "new.mkv")

    def test_identity_preview_uses_samples_or_placeholder(self) -> None:
        parts = [{"source": "sort_field"}, {"source": "note_id"}]
        self.assertEqual(preview_identity(parts, {"sort_field": "皮肉"}), f"皮肉 — {SAMPLE_PLACEHOLDER}")
        blocked = preview_identity(parts, {}, unavailable=NOTE_SAMPLE_PLACEHOLDER)
        self.assertEqual(blocked, f"{NOTE_SAMPLE_PLACEHOLDER} — {NOTE_SAMPLE_PLACEHOLDER}")
        clip = {
            "id": "c1",
            "media": {"filename": "clip.mkv"},
            "anki": {"sort_field_value": "皮肉", "note_id": 9},
        }
        samples = clip_identity_samples([clip])
        self.assertEqual(samples["video_filename"], "clip.mkv")
        self.assertEqual(samples["sort_field"], "皮肉")
        self.assertNotIn("deck_name", samples)
        notes = [{"note_id": 9, "deck_name": "Target", "sort_field_value": "皮肉", "fields": {"Meaning": ""}}]
        note_samples = note_identity_samples(notes, ["Meaning"])
        self.assertEqual(note_samples["sort_field"], "皮肉")
        self.assertEqual(note_samples["note_id"], "9")
        self.assertNotIn("field:Meaning", note_samples)

    def test_import_note_type_profile_is_remembered(self) -> None:
        config = normalize_config(
            {
                "import_mapping_initialized": True,
                "import_note_type": "A",
                "import_field_sets": [{"enabled": True, "video": "VideoA", "sentence": "SentA"}],
                "import_note_identity": [{"source": "field:Meaning"}],
            }
        )
        switch_import_note_type(config, "B")
        self.assertEqual(config["import_note_type"], "B")
        self.assertEqual(config["import_field_sets"][0]["video"], "")
        self.assertEqual(config["import_note_identity"][0]["source"], "sort_field")
        self.assertEqual(config["import_note_profiles"]["A"]["field_sets"][0]["video"], "VideoA")
        self.assertEqual(config["import_note_profiles"]["A"]["note_identity"][0]["source"], "field:Meaning")
        config["import_field_sets"][0]["video"] = "VideoB"
        switch_import_note_type(config, "A")
        self.assertEqual(config["import_field_sets"][0]["video"], "VideoA")
        self.assertEqual(config["import_field_sets"][0]["sentence"], "SentA")
        self.assertEqual(config["import_note_identity"][0]["source"], "field:Meaning")
        self.assertEqual(config["import_note_profiles"]["B"]["field_sets"][0]["video"], "VideoB")
        roundtrip = normalize_config(config)
        self.assertEqual(roundtrip["import_note_profiles"]["A"]["field_sets"][0]["sentence"], "SentA")

    def test_import_field_sets_drop_duplicate_fields(self) -> None:
        config = normalize_config(
            {
                "import_mapping_initialized": True,
                "import_field_sets": [
                    {"enabled": True, "video": "Video", "sentence": "Video"},
                    {"enabled": True, "video": "Other", "sentence": "video"},
                ],
            }
        )
        self.assertEqual(config["import_field_sets"][0]["video"], "Video")
        self.assertEqual(config["import_field_sets"][0]["sentence"], "")
        self.assertEqual(config["import_field_sets"][1]["video"], "Other")
        self.assertEqual(config["import_field_sets"][1]["sentence"], "")
        self.assertEqual(visible_field_choices(["Video", "Other", "Sentence"], {"video"}, "Other"), ["Other", "Sentence"])

    def test_export_key_examples_come_from_notes(self) -> None:
        notes = [
            {
                "note_id": 1,
                "deck_name": "Deck",
                "model_name": "N",
                "sort_field_name": "Expression",
                "sort_field_value": "",
                "fields": {"Expression": "", "Video": ""},
            },
            {
                "note_id": 2,
                "deck_name": "Deck",
                "model_name": "N",
                "sort_field_name": "Expression",
                "sort_field_value": "hi",
                "fields": {"Expression": "hi", "Video": ""},
            },
        ]
        config = {
            "field_sets": [{"enabled": True, "name": "Set", "video": "Video", "sentence": "Expression"}],
            "generic_fields": [],
            "audio_fields": [],
            "clip_extra_roles": [],
        }
        examples = export_key_examples(notes, config, profile_name="test-profile")
        self.assertEqual(examples["anki.profile"], "test-profile")
        self.assertEqual(examples["anki.sort_field_value"], "hi")
        self.assertEqual(examples["anki.fields.sentence"], "hi")
        self.assertNotIn("anki.fields.video", examples)


class ExclusionRuleTests(unittest.TestCase):
    NHK = "ＮＨＫスペシャル　ゲーム×人類　ＰＡＲＴⅠ"

    def _document(self, **kwargs: object) -> dict:
        clip = _bare_clip(
            int(kwargs.get("note_id") or 1),
            str(kwargs.get("filename") or "keep.mp4"),
            miscinfo=str(kwargs.get("miscinfo") or ""),
        )
        record = kwargs.get("record")
        selection = kwargs.get("job_selection")
        return clip_match_document(
            clip,
            record=record if isinstance(record, dict) else None,
            link=LinkInfo(status="linked" if record else "anki_only"),
            anki_keys=effective_anki_keys(None),
            job_selection=selection if isinstance(selection, dict) else {},
        )

    def test_preview_keeps_one_example_per_export_key(self) -> None:
        config = normalize_config({"note_type": "Yomi", "field_sets": [{"enabled": True, "video": "Video"}]})
        preview = inspect_export_clips(
            [
                _bare_clip(1, "keep.mp4", miscinfo="SEKIRO SHADOWS DIE TWICE"),
                _bare_clip(2, "other.mp4", miscinfo="later"),
            ],
            config,
        )
        self.assertEqual(preview.examples["anki.fields.miscinfo.value"], "SEKIRO SHADOWS DIE TWICE")
        self.assertEqual(preview.examples["media.filename"], "keep.mp4")
        self.assertIn("anki.fields.miscinfo.value", preview.key_paths)
        self.assertEqual(preview.job_key_counts, {})

    def test_job_keys_count_clips_that_contain_them(self) -> None:
        selection = {"source": True}
        titled = clip_match_document(
            _bare_clip(1, "a.mp4"),
            record={"source": {"media title": "one"}},
            link=LinkInfo(status="linked"),
            anki_keys=effective_anki_keys(None),
            job_selection=selection,
        )
        other = clip_match_document(
            _bare_clip(2, "b.mp4"),
            record={"source": {"path": r"E:\movie.mkv"}},
            link=LinkInfo(status="linked"),
            anki_keys=effective_anki_keys(None),
            job_selection=selection,
        )
        plain = clip_match_document(
            _bare_clip(3, "c.mp4"),
            record=None,
            link=LinkInfo(),
            anki_keys=effective_anki_keys(None),
            job_selection=selection,
        )
        counts = job_key_counts([titled, other, plain])
        self.assertEqual(counts["job"], 2)
        self.assertEqual(counts["job.source"], 2)
        self.assertEqual(counts["job.source.media title"], 1)
        self.assertNotIn("media.filename", counts)

    def test_presets_keep_unique_names_and_normalized_rules(self) -> None:
        presets = normalize_export_exclusion_presets(
            [
                {
                    "id": "keep",
                    "name": "  Skip titles  ",
                    "enabled": False,
                    "rules": [{"path": "media -> filename", "operator": "nope", "value": "intro", "case_sensitive": 1}],
                },
                {"id": "dup", "name": "skip titles", "rules": [{"path": "other", "value": "x"}]},
                {"name": "", "rules": [{"path": "media.filename", "value": "x"}]},
                "not-a-preset",
                {"name": "Empty", "rules": "bad"},
            ]
        )
        self.assertEqual([preset["name"] for preset in presets], ["Skip titles", "Empty"])
        self.assertEqual(presets[0]["id"], "keep")
        self.assertFalse(presets[0]["enabled"])
        self.assertEqual(presets[0]["rules"][0]["operator"], "contains")
        self.assertTrue(presets[0]["rules"][0]["case_sensitive"])
        self.assertEqual(presets[1]["rules"], [])
        self.assertTrue(presets[1]["id"])

        config = normalize_config({"export_exclusion_presets": presets, "export_exclusions_enabled": True})
        self.assertEqual(config["export_exclusion_presets"], presets)
        self.assertEqual(normalize_config({})["export_exclusion_presets"], [])

    def test_miscinfo_value_contains_title(self) -> None:
        matched = self._document(miscinfo=f"source {self.NHK} more")
        other = self._document(miscinfo="something else")
        rule = {
            "enabled": True,
            "path": "clip -> anki -> fields -> miscinfo -> value",
            "operator": "contains",
            "value": self.NHK,
        }
        self.assertIsNotNone(first_matching_rule(matched, [rule]))
        self.assertIsNone(first_matching_rule(other, [rule]))

    def test_filename_starts_with_respects_case_flag(self) -> None:
        document = self._document(filename="SekiroShadowsDieTwice_01.mp4")
        rule = {
            "enabled": True,
            "path": "media -> filename",
            "operator": "starts_with",
            "value": "sekiroshadowsdietwice",
        }
        self.assertIsNotNone(first_matching_rule(document, [rule]))
        sensitive = {**rule, "case_sensitive": True}
        self.assertIsNone(first_matching_rule(document, [sensitive]))

    def test_job_media_title_equals_only_when_generated(self) -> None:
        title = self.NHK + "　３０億人の熱狂と未来[字]_2025-01-25.mkv"
        record = {"source": {"media title": title, "path": r"E:\secret\movie.mkv"}}
        present = self._document(record=record, job_selection={"source": True})
        rule = {
            "enabled": True,
            "path": "job -> source -> media title",
            "operator": "equals",
            "value": title,
        }
        self.assertEqual(resolved_values(present, "source.media title"), [])
        self.assertIsNotNone(first_matching_rule(present, [rule]))
        self.assertEqual(present["job"]["source"]["path"], r"E:\secret\movie.mkv")
        missing_key = self._document(record=record, job_selection={"source.path": True})
        self.assertIsNone(first_matching_rule(missing_key, [rule]))
        self.assertIsNone(first_matching_rule(self._document(), [rule]))

    def test_absent_path_disabled_rule_and_parent_json(self) -> None:
        document = self._document(miscinfo=self.NHK)
        self.assertIsNone(first_matching_rule(document, [{"enabled": True, "path": "job -> missing", "operator": "contains", "value": "x"}]))
        disabled = {
            "enabled": False,
            "path": "anki -> fields -> miscinfo -> value",
            "operator": "contains",
            "value": self.NHK,
        }
        self.assertIsNone(first_matching_rule(document, [disabled]))
        parent = {
            "enabled": True,
            "path": "anki -> fields -> miscinfo",
            "operator": "contains",
            "value": self.NHK,
        }
        self.assertIsNotNone(first_matching_rule(document, [parent]))

    def test_any_rule_excludes_and_export_skips_matched_clip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "media"
            media.mkdir()
            keep = _bare_clip(1, "keep.mp4", miscinfo="other")
            skip = _bare_clip(2, "sekiroshadowsdietwice_1.mp4", miscinfo="SEKIRO SHADOWS DIE TWICE")
            for clip in (keep, skip):
                clip.media_path = str(media / clip.filename)
                (media / clip.filename).write_bytes(clip.filename.encode("utf-8"))
            config = normalize_config(
                {
                    "note_type": "Yomi",
                    "field_sets": [{"enabled": True, "video": "Video"}],
                    "export_exclusions_enabled": True,
                    "export_exclusions": [
                        {
                            "enabled": True,
                            "path": "clip -> anki -> fields -> miscinfo -> value",
                            "operator": "contains",
                            "value": "SEKIRO SHADOWS DIE TWICE",
                        },
                        {
                            "enabled": True,
                            "path": "media -> filename",
                            "operator": "starts_with",
                            "value": "sekiroshadowsdietwice",
                        },
                    ],
                }
            )
            dest = Path(folder) / "library"
            report = export_clips([keep, skip], config=config, destination=str(dest), mode="fresh")
            self.assertEqual(report.clips_excluded, 1)
            self.assertTrue(report.exclusions_applied)
            library = load_library(str(dest / "clip_library.json"))
            assert library is not None
            self.assertEqual([clip["media"]["filename"] for clip in library["clips"]], ["keep.mp4"])
            self.assertTrue((dest / "media" / "keep.mp4").exists())
            self.assertFalse((dest / "media" / "sekiroshadowsdietwice_1.mp4").exists())

            update = normalize_config({**config, "export_exclusions_enabled": False})
            export_clips([skip], config=update, destination=str(dest), mode="update")
            filtered = normalize_config({**config, "export_exclusions_enabled": True})
            again = export_clips([keep, skip], config=filtered, destination=str(dest), mode="update")
            self.assertEqual(again.clips_excluded, 1)
            self.assertTrue(any("left unchanged" in warning for warning in again.warnings))
            updated = load_library(str(dest / "clip_library.json"))
            assert updated is not None
            self.assertEqual(
                sorted(clip["media"]["filename"] for clip in updated["clips"]),
                ["keep.mp4", "sekiroshadowsdietwice_1.mp4"],
            )


if __name__ == "__main__":
    unittest.main()
