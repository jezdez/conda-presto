Source demos/_settings.tape

Output demos/http.gif

Hide
Type `source demos/_setup.sh http`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Discover the installed Pixi output formats`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `curl -fsS "$DEMO_URL/formats" > formats.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '.formats | map(select(startswith("pixi")))' formats.json`
Enter
Wait+Line /\$$/
Sleep 2s

Hide
Type `jq -e '.formats | index("pixi-lock-v6") != null' formats.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Solve inline requirements through the HTTP API`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `curl -fsS "$DEMO_URL/resolve" --json \`
Enter
Type@60ms `  '{"specs":["zlib"],"channels":["conda-forge"],"platforms":["linux-64"]}' \`
Enter
Type@60ms `  > result.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '[.[] | {platform, error, packages: [.packages[].name]}]' result.json`
Enter
Wait+Line /\$$/
Sleep 2s

Hide
Type `jq -e 'length == 1 and .[0].error == null and any(.[0].packages[]; .name == "zlib")' result.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Upload an environment file and save a Pixi lock`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `bat environment.yml`
Enter
Wait+Line /\$$/
Sleep 1s
Type@60ms `request="$DEMO_URL/resolve?filename=environment.yml&platform=linux-64"`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS -D headers.txt "$request&format=pixi-lock-v6" \`
Enter
Type@60ms `  -H 'Content-Type: application/yaml' --data-binary @environment.yml \`
Enter
Type@60ms `  -o pixi.lock`
Enter
Wait+Line /\$$/
Type@60ms `head -n 8 pixi.lock`
Enter
Wait+Line /\$$/
Sleep 2s

Hide
Type `test -s pixi.lock`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Location points to the exact retained bytes`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `grep -i '^location:' headers.txt`
Enter
Wait+Line /\$$/
Sleep 1s
Type@60ms `location=$(awk 'tolower($1) == "location:" {print $2}' headers.txt | tr -d '\r')`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS "$DEMO_URL$location" > retained.lock`
Enter
Wait+Line /\$$/
Type@60ms `cmp pixi.lock retained.lock`
Enter
Wait+Line /\$$/
Type@60ms `echo "Retained lock is byte-for-byte identical"`
Enter
Wait+Line /\$$/
Sleep 3s

Screenshot demos/http.png
Sleep 500ms
Hide
Type `test -n "$location"`
Enter
Wait+Line /\$$/
Type `demo_finish`
Enter
Wait+Line /DEMO_COMPLETE\$$/
