async function retrieveRecentNotes() {
  return await runShellScript({
    script: "/usr/bin/osascript -l JavaScript /Users/jacobr/dev/scripts/macbook/scripts/recent_notes_menu.js 10"
  });
}
