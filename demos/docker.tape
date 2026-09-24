Source demos/_settings.tape
Output demos/docker.gif

Hide
Set TypingSpeed 1ms
Type `source demos/_setup.sh docker`
Enter
Wait+Line /\$$/
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show

Type "# Build the service image from this checkout"
Enter
Type@60ms `docker build --tag conda-presto:docs-demo "$DEMO_REPO" > build.log 2>&1`
Enter
Hide
Set TypingSpeed 1ms
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Sleep 1s

Type "# Publish on a local port and restrict container privileges"
Enter
Type@60ms `DEMO_CONTAINER=$(docker run --detach --publish 127.0.0.1::8000 \`
Enter
Type@60ms `  --cap-drop ALL --security-opt no-new-privileges \`
Enter
Type@60ms "  --env CONDA_PRESTO_PLATFORMS=linux-64 conda-presto:docs-demo)"
Enter
Wait+Line /\$$/
Type@60ms `DEMO_URL="http://$(docker port "$DEMO_CONTAINER" 8000/tcp)"`
Enter
Wait+Line /\$$/
Hide
Set TypingSpeed 1ms
Type `for attempt in {1..180}; do curl --fail --silent "$DEMO_URL/health" > /dev/null && break; sleep 1; done`
Enter
Wait+Line /\$$/
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show

Type "# Check readiness and resolve a Linux environment"
Enter
Type@60ms `curl --fail --silent "$DEMO_URL/health" | jq .`
Enter
Wait+Line /\$$/
Sleep 2s
Type@60ms `curl --fail --silent --get "$DEMO_URL/resolve" \`
Enter
Type@60ms `  --data-urlencode spec=zlib --data-urlencode channel=conda-forge \`
Enter
Type@60ms "  --data-urlencode platform=linux-64 > result.json"
Enter
Wait+Line /\$$/
Type@60ms `jq '[.[] | {platform, error, packages: [.packages[].name]}]' result.json`
Enter
Wait+Line /\$$/
Sleep 3s
Screenshot demos/docker.png
Sleep 500ms
Hide
Set TypingSpeed 1ms
Type `jq -e '.[0].error == null and any(.[0].packages[]; .name == "zlib")' result.json > /dev/null`
Enter
Wait+Line /\$$/
Type "demo_finish"
Enter
Wait+Line /DEMO_COMPLETE\$$/
