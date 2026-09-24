Source demos/_settings.tape

Output demos/cache.gif

Hide
Set TypingSpeed 1ms
Type `source demos/_setup.sh cache`
Enter
Wait+Line /\$$/
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show

Type "# Configure file storage before starting the service"
Enter
Sleep 500ms
Type@60ms "export CONDA_PRESTO_RESULT_CACHE_BACKEND=file"
Enter
Wait+Line /\$$/
Type@60ms `export CONDA_PRESTO_RESULT_CACHE_DIR="$PWD/cache"`
Enter
Wait+Line /\$$/
Sleep 1s

Type "# Start the service with these settings"
Enter
Sleep 500ms
Hide
Set TypingSpeed 1ms
Type "start_server"
Enter
Wait+Line /\$$/
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `curl -fsS "$DEMO_URL/health" | jq .`
Enter
Wait+Line /\$$/
Sleep 1s

Type "# Solve once and save the retained result location"
Enter
Sleep 500ms
Type@60ms `request="$DEMO_URL/resolve?spec=zlib&channel=conda-forge&platform=linux-64"`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS -D headers.txt "$request" > result.json`
Enter
Wait+Line /\$$/
Type@60ms `location=$(awk 'tolower($1) == "location:" {print $2}' headers.txt | tr -d '\r')`
Enter
Wait+Line /\$$/
Type@60ms `echo "$location"`
Enter
Wait+Line /\$$/
Sleep 2s

Hide
Set TypingSpeed 1ms
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show

Type "# Restart the service with the same cache directory"
Enter
Sleep 1s
Hide
Set TypingSpeed 1ms
Type "stop_server"
Enter
Wait+Line /\$$/
Type "start_server"
Enter
Wait+Line /\$$/
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `curl -fsS "$DEMO_URL/health" | jq .`
Enter
Wait+Line /\$$/
Sleep 1s

Type "# Retrieve the saved result without another solve"
Enter
Sleep 500ms
Type@60ms `curl -fsS "$DEMO_URL$location" > after-restart.json`
Enter
Wait+Line /\$$/
Type@60ms "cmp result.json after-restart.json"
Enter
Wait+Line /\$$/
Type@60ms `echo "Retained bytes survive the restart"`
Enter
Wait+Line /\$$/
Sleep 1s
Type@60ms `jq '[.[] | {platform, error, packages: [.packages[].name]}]' after-restart.json`
Enter
Wait+Line /\$$/
Sleep 2s

Screenshot demos/cache.png
Sleep 500ms
Hide
Set TypingSpeed 1ms
Type "demo_finish"
Enter
Wait+Line /DEMO_COMPLETE\$$/
