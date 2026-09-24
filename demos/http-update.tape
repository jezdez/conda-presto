Source demos/_settings.tape

Output demos/http-update.gif

Hide
Type `source demos/_setup.sh http-update`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Validate the saved lock against its complete manifest`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `jq -n --rawfile file conda.lock --rawfile manifest conda.toml \`
Enter
Type@60ms `  '{file:$file,filename:"conda.lock",manifest:$manifest,`
Enter
Type@60ms `    manifest_filename:"conda.toml"}' > baseline.json`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS "$DEMO_URL/validate" --json @baseline.json > consistency.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '{consistent, targets:(.targets | length)}' consistency.json`
Enter
Wait+Line /\$$/
Sleep 2s

Hide
Type `jq -e '.consistent and (.targets | length == 4) and all(.targets[]; .consistent and .reason == null)' consistency.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# A changed manifest returns a mismatch with HTTP 200`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `jq '.manifest |= sub(">=1.3,<2"; ">=99")' baseline.json > changed.json`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS "$DEMO_URL/validate" --json @changed.json \`
Enter
Type@60ms `  -w 'HTTP %{http_code}\n' -o mismatch.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '{consistent, reason:.targets[0].reason}' mismatch.json`
Enter
Wait+Line /\$$/
Sleep 3s

Hide
Type `jq -e '.consistent == false and (.targets | length == 4) and all(.targets[]; .consistent == false and (.reason | length > 0))' mismatch.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Update tools/cpu from the original consistent baseline`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `jq '. + {environment:"tools",platform:"cpu",packages:["zstd"]}' \`
Enter
Type@60ms `  baseline.json > update.json`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS "$DEMO_URL/update" --json @update.json > updated.lock`
Enter
Wait+Line /\$$/
Type@60ms `python check_update.py conda.lock updated.lock`
Enter
Wait+Line /\$$/
Sleep 3s

Hide
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Validate all four targets in the returned lock`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `jq --rawfile file updated.lock '.file = $file' baseline.json > updated.json`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS "$DEMO_URL/validate" --json @updated.json > updated-check.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '{consistent, targets:(.targets | length)}' updated-check.json`
Enter
Wait+Line /\$$/
Sleep 3s

Screenshot demos/http-update.png
Sleep 500ms
Hide
Type `jq -e '.consistent and (.targets | length == 4) and all(.targets[]; .consistent)' updated-check.json > /dev/null`
Enter
Wait+Line /\$$/
Type `demo_finish`
Enter
Wait+Line /DEMO_COMPLETE\$$/
