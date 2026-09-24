Source demos/_settings.tape

Output demos/http.gif

Hide
Set TypingSpeed 1ms
Type `source demos/_setup.sh http`
Enter
Wait+Line /\$$/
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show

Type "# Check the local service"
Enter
Sleep 500ms
Type@60ms `curl -fsS "$DEMO_URL/health" | jq .`
Enter
Wait+Line /\$$/
Sleep 1s

Type "# Solve zlib for Linux through the HTTP API"
Enter
Sleep 500ms
Type@60ms `curl -fsS "$DEMO_URL/resolve" \`
Enter
Type@60ms `  -H 'Content-Type: application/json' \`
Enter
Type@60ms `  --data '{"specs":["zlib"],"channels":["conda-forge"],"platforms":["linux-64"]}' \`
Enter
Type@60ms `  > result.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '[.[] | {platform, error, packages: [.packages[].name]}]' result.json`
Enter
Wait+Line /\$$/
Sleep 3s

Hide
Set TypingSpeed 1ms
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show

Type "# Export a Pixi lock and keep the response headers"
Enter
Sleep 500ms
Type@60ms `request="$DEMO_URL/resolve?spec=zlib&channel=conda-forge&platform=linux-64"`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS -D headers.txt "$request&format=pixi-lock-v6" > pixi.lock`
Enter
Wait+Line /\$$/
Type@60ms `head -n 8 pixi.lock`
Enter
Wait+Line /\$$/
Sleep 2s

Type "# Location points to the exact retained bytes"
Enter
Sleep 500ms
Type@60ms `grep -i '^location:' headers.txt`
Enter
Wait+Line /\$$/
Type@60ms `location=$(awk 'tolower($1) == "location:" {print $2}' headers.txt | tr -d '\r')`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS "$DEMO_URL$location" > retained.lock`
Enter
Wait+Line /\$$/
Type@60ms "cmp pixi.lock retained.lock"
Enter
Wait+Line /\$$/
Type@60ms `echo "Retained lock is byte-for-byte identical"`
Enter
Wait+Line /\$$/
Sleep 3s

Screenshot demos/http.png
Sleep 500ms
Hide
Set TypingSpeed 1ms
Type "demo_finish"
Enter
Wait+Line /DEMO_COMPLETE\$$/
