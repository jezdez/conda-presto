Source demos/_settings.tape

Output demos/operations.gif

Hide
Set TypingSpeed 1ms
Type `source demos/_setup.sh operations`
Enter
Wait+Line /\$$/
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show

Type "# Check readiness and installed versions"
Enter
Sleep 500ms
Type@60ms `curl -fsS "$DEMO_URL/health" | jq .`
Enter
Wait+Line /\$$/
Sleep 1s
Type@60ms `curl -fsS "$DEMO_URL/version" | jq .`
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

Type "# Inspect the service's configured capabilities"
Enter
Sleep 500ms
Type@60ms `curl -fsS "$DEMO_URL/capabilities" | jq .`
Enter
Wait+Line /\$$/
Sleep 3s

Type "# An unknown output format returns HTTP 400"
Enter
Sleep 500ms
Type@60ms `curl -sS -o error.json -w 'HTTP %{http_code}\n' \`
Enter
Type@60ms `  "$DEMO_URL/resolve?spec=zlib&platform=linux-64&format=unknown-demo-format"`
Enter
Wait+Line /\$$/
Type@60ms `jq '{error, detail}' error.json`
Enter
Wait+Line /\$$/
Sleep 3s

Hide
Set TypingSpeed 1ms
Type `test "$(curl -sS -o /dev/null -w '%{http_code}' "$DEMO_URL/resolve?spec=zlib&platform=linux-64&format=unknown-demo-format")" = 400`
Enter
Wait+Line /\$$/
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show

Type "# Measure one solve and its retained result retrieval"
Enter
Sleep 500ms
Type@60ms `request="$DEMO_URL/resolve?spec=zlib&channel=conda-forge&platform=linux-64"`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS -D headers.txt -o result.json \`
Enter
Type@60ms `  -w 'Resolve: %{time_total}s\n' "$request"`
Enter
Wait+Line /\$$/
Sleep 1s
Type@60ms `location=$(awk 'tolower($1) == "location:" {print $2}' headers.txt | tr -d '\r')`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS -o retained.json \`
Enter
Type@60ms `  -w 'Retained retrieval: %{time_total}s\n' "$DEMO_URL$location"`
Enter
Wait+Line /\$$/
Sleep 2s
Type@60ms "cmp result.json retained.json"
Enter
Wait+Line /\$$/
Type@60ms `echo "Both responses contain identical bytes"`
Enter
Wait+Line /\$$/
Sleep 1s
Type "# Timings are local observations, not a service benchmark"
Enter
Wait+Line /\$$/
Sleep 3s

Screenshot demos/operations.png
Sleep 500ms
Hide
Set TypingSpeed 1ms
Type "demo_finish"
Enter
Wait+Line /DEMO_COMPLETE\$$/
