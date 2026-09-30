# Clip Library

Anki addon that exports video clips and their note/job metadata to a portable JSON + folder library, browses those libraries in a dedicated viewer, and imports clips back into Anki.

## What it does

- Configure one note type, as many clip field sets as needed, optional extra rows on those sets, plus generic and audio fields.
- Export selected browser notes or a whole deck. Files are copied, never moved.
- Optionally exclude clips by any key path on the clip object that export writes, such as `clip -> anki -> fields -> miscinfo -> value`. A preview lists the clips those rules leave out. Hover a discovered key to see a value from this export. Job keys show how many clips in this export contain them. **Use key** puts the selected path into a rule. Updating a library does not delete clips already stored in it.
- The same rules can be edited on the Exclusions tab in settings. That tab has no deck, so it cannot show key paths or example values. Save named presets there, then apply one from the export window.
- Optionally merge clip-encode / mpvacious job records into `clip_library.json`.
- Choose which clip-library keys to store, and which job-record keys to store from a tree built out of a deck's real job records.
- Browse one or more exported libraries, search them, and play clips or linked note-audio files with embedded mpv.
- Import into existing notes only. You build match rules with dropdowns, review the suggested note, and can change that note before writing. Each clip uses the first empty video slot. Sentence, secondary, and miscinfo can be set to Don't import.
- Target decks are shown as a tree (`Parent` above `Parent::Child`). Checking a parent does not include its subdecks.
- Import dropdowns list the best matches, not every note in the deck. Use the … button to search the rest. That stays responsive on collections with tens of thousands of notes.
- The Anki console prints `[clip_library]` timings for building the mapping preview, exporting a collection, importing a collection, and building a job-record export key tree.
- Identity editors show a live example built from the collection being worked with. Hovering a key shows one real value from that collection. If no deck has been checked, note identity uses placeholder text instead of reading other decks.
- The import window is split: mapping, rules, score, note type, and identities on the left; target decks and import field sets on the right.
- A field already chosen in an export or import note type is hidden from the other field dropdowns.

## Menus

- `Tools > Clip Library > Configure` edits a draft. Save writes it. Close discards it, and asks first when something actually changed. The Exclusions tab edits export filter rules and named presets. Paths have to be typed there.
- The Import note type tab remembers field sets and note identity per note type. The import window uses the same setup.
- `Tools > Clip Library > Library Viewer`
- Browser `Edit > Clip Library: Export selected notes...`
- Browser `Edit > Clip Library: Export deck...`

## Export layout

```
<library>/
  clip_library.json
  media/
```

Job-record details are stored inside each clip entry in `clip_library.json`. Settings are written only when you press Save. Job-record linking treats a later note/field-set move as a partial match as long as the output filename still agrees.

Schema 4 stores note-constant Anki fields once under top-level `notes`, keyed by note id. The note object does not repeat that id. Each clip keeps `anki.note_id` as the link, plus clip-specific fields such as sentence, miscinfo, and the clip video under `media`. A clip stores `anki.field_set_index` only. The field set name and its Anki field mapping live in top-level `field_sets`, matched by that same `index` (not by array position). Media paths are `media/<filename>`. Audio filenames are the `media` records on `notes.<id>.audio_fields.<field>`, not a second `files` list. Older libraries are normalized on load. Re-exporting one clip refreshes the shared note record for every clip that points at it, and refreshes `field_sets` from the current collection without renumbering indexes still used by clips that were not re-exported.

## Development

The add-on runtime is under `src/video_clip_library`. The project targets Anki 2.3.10 or newer, as declared by `src/video_clip_library/manifest.json`.

Run the unit tests from the project root:

```text
python -m pytest
```

The tests cover pure export, import, configuration, identity, and path-handling logic. UI and Anki collection workflows still require a manual smoke test inside Anki.

## Add-on packaging

Anki add-on archives must contain the contents of `src/video_clip_library` at the archive root. Do not include the `src` directory, the `video_clip_library` directory wrapper, tests, this README, `meta.json`, `__pycache__`, or the reference material under `user_files`.

Keep `config.json` and `manifest.json` in the archive. `meta.json` is generated and maintained by Anki for an installed add-on; it is not the source of default configuration.
