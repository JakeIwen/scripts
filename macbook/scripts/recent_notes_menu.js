#!/usr/bin/osascript -l JavaScript

/*
 * Return BetterTouchTool Simple JSON for the most recently modified Notes.
 *
 * BTT's floating-menu content script runs this file with osascript. Each menu
 * row calls show_note.sh with the Notes object ID, so duplicate note titles do
 * not make the selected row ambiguous.
 */

const SHOW_NOTE_SCRIPT = "/Users/jacobr/dev/scripts/macbook/scripts/show_note.sh";
const DEFAULT_LIMIT = 10;
const MAX_LIMIT = 30;

function shellQuote(value) {
  return "'" + String(value).replace(/'/g, "'\"'\"'") + "'";
}

function requestedLimit(argv) {
  if (argv.length === 0) {
    return DEFAULT_LIMIT;
  }

  const value = Number(argv[0]);
  if (!Number.isFinite(value) || value < 1) {
    return DEFAULT_LIMIT;
  }
  return Math.min(Math.floor(value), MAX_LIMIT);
}

function timestamp(value) {
  const date = value instanceof Date ? value : new Date(value);
  const milliseconds = date.getTime();
  return Number.isFinite(milliseconds) ? milliseconds : 0;
}

function readNotes(Notes) {
  // Collection property access asks Notes for one array at a time. This is
  // much faster than sending three Apple events for each note in a large
  // library. Fall back to individual reads for older Notes implementations.
  try {
    const titles = Notes.notes.name();
    const ids = Notes.notes.id();
    const modifiedDates = Notes.notes.modificationDate();
    const count = Math.min(titles.length, ids.length, modifiedDates.length);
    const notes = [];

    for (let index = 0; index < count; index += 1) {
      const title = String(titles[index]);
      const id = String(ids[index]);
      if (title.length > 0 && id.length > 0) {
        notes.push({
          title,
          id,
          modified: timestamp(modifiedDates[index])
        });
      }
    }
    return notes;
  } catch (error) {
    const noteRefs = Notes.notes();
    const notes = [];

    for (let index = 0; index < noteRefs.length; index += 1) {
      try {
        const title = String(noteRefs[index].name());
        const id = String(noteRefs[index].id());
        const modified = timestamp(noteRefs[index].modificationDate());

        if (title.length > 0 && id.length > 0) {
          notes.push({ title, id, modified });
        }
      } catch (noteError) {
        // A note can disappear while the list is read. Skip only that row.
      }
    }
    return notes;
  }
}

function run(argv) {
  const limit = requestedLimit(argv);
  const Notes = Application("/System/Applications/Notes.app");
  const notes = readNotes(Notes);

  notes.sort((left, right) => right.modified - left.modified);

  const items = [
    {
      type: "back",
      title: "<<",
      icon: "sfsymbol::chevron.backward"
    }
  ];

  notes.slice(0, limit).forEach((note) => {
    const command = SHOW_NOTE_SCRIPT + " --id " + shellQuote(note.id);
    items.push({
      title: note.title.replace(/[\r\n]+/g, " "),
      icon: "sfsymbol::note.text",
      action: {
        js: "runShellScript({script: " + JSON.stringify(command) + "})"
      }
    });
  });

  if (items.length === 1) {
    items.push({
      title: "No notes found",
      icon: "sfsymbol::note.text"
    });
  }

  return JSON.stringify(items);
}
