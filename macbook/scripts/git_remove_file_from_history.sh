# usage:
# run from repo root
#  ./path_to_this_file/git_remove_file_from_history.sh ./file_to_purge.txt

filepath="$1"
origin_url=$(git remote get-url origin 2>/dev/null)

if [ -z "$origin_url" ]; then
  echo "❌ No origin remote found. Aborting."
  exit 1
fi

if [ -z "$filepath" ]; then
  echo "❌ No filepath arg provided. Aborting."
  exit 1
fi

pause_for_enter() {
  printf "Press Enter to continue..."
  read
}

repo_name="$(basename "$PWD")"
backup_path="../backup/$repo_name"
echo "Backing up to: $backup_path ..."
mkdir -p "$backup_path"
cp -r . "$backup_path"
echo "Ensure backup path '$backup_path' is correct and contains a copy of the repo"
pause_for_enter

echo "Filtering repo ..."
git filter-repo --path "$filepath" --invert-paths --force
git remote add origin "$origin_url"

echo "Repo filtered. Ensure things look good and press Enter to push to origin"
pause_for_enter

git push origin --force --all
git push origin --force --tags