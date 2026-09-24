Source demos/_settings.tape
Output demos/locks.gif
Hide
Set TypingSpeed 1ms
Type `source demos/_setup.sh locks`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Inspect exact records from a saved workspace lock`
Enter
Wait+Line /\$$/
Type@60ms `conda-presto --parse -f conda.lock > saved.json`
Enter
Wait+Line /\$$/
Type@60ms `jq -c '.environments[]' saved.json`
Enter
Wait+Line /\$$/
Sleep 2s
Hide
Set TypingSpeed 1ms
Type `mkdir extracted`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Extract only tools/cpu without solving again`
Enter
Wait+Line /\$$/
Type@60ms `conda-presto --export -f conda.lock -e tools -p cpu --format conda-workspaces-lock-v1 > extracted/conda.lock`
Enter
Wait+Line /\$$/
Type@60ms `conda-presto --parse -f extracted/conda.lock > extracted.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '.environments' extracted.json`
Enter
Wait+Line /\$$/
Sleep 2s
Hide
Set TypingSpeed 1ms
Type `jq -e '.environments == [{name: "tools", platforms: {cpu: "linux-64"}}]' extracted.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Render exact package URLs`
Enter
Wait+Line /\$$/
Type@60ms `conda-presto --export -f conda.lock -e tools -p cpu --format explicit > explicit.txt`
Enter
Wait+Line /\$$/
Type@60ms `cat explicit.txt`
Enter
Wait+Line /\$$/
Sleep 3s
Hide
Set TypingSpeed 1ms
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Render a normalized manifest from saved records`
Enter
Wait+Line /\$$/
Type@60ms `conda-presto --export -f conda.lock -e tools -p cpu --format conda-toml > normalized.toml`
Enter
Wait+Line /\$$/
Type@60ms `head -n 6 normalized.toml`
Enter
Wait+Line /\$$/
Sleep 2s
Hide
Set TypingSpeed 1ms
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Convert a generic lock without solving or downloading`
Enter
Wait+Line /\$$/
Type@60ms `conda-presto --export -f conda-lock.yml -p linux-64 --format pixi-lock-v6 > pixi.lock`
Enter
Wait+Line /\$$/
Type@60ms `head -n 8 pixi.lock`
Enter
Wait+Line /\$$/
Sleep 2s
Hide
Set TypingSpeed 1ms
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Render a saved-lock SBOM with declared package roots`
Enter
Wait+Line /\$$/
Type@60ms `conda-presto --export -f conda.lock -e tools -p cpu --manifest conda.toml --format cyclonedx-json-v1.7 > sbom.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '{bomFormat, specVersion, packages: [.components[].name]}' sbom.json`
Enter
Wait+Line /\$$/
Sleep 2s
Type@60ms `python check_locks.py`
Enter
Wait+Line /\$$/
Sleep 2s
Screenshot demos/locks.png
Sleep 500ms
Hide
Set TypingSpeed 1ms
Type `demo_finish`
Enter
Wait+Line /DEMO_COMPLETE\$$/
