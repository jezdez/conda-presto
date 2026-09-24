Source demos/_settings.tape
Output demos/workspace.gif
Hide
Set TypingSpeed 1ms
Type `source demos/_setup.sh workspace`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Two environments and two named Linux targets`
Enter
Wait+Line /\$$/
Type@60ms `bat conda.toml`
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
Type@60ms `# Discover without selecting the host platform`
Enter
Wait+Line /\$$/
Type@60ms `conda presto --parse -f conda.toml > discovery.json`
Enter
Wait+Line /\$$/
Type@60ms `jq -c '.environments[] | {name, platforms}' discovery.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '.selected' discovery.json`
Enter
Wait+Line /\$$/
Sleep 2s
Hide
Set TypingSpeed 1ms
Type `jq -e '.selected == [] and ([.environments[].name] | sort) == ["default", "tools"] and all(.environments[]; .platforms == {cpu: "linux-64", gpu: "linux-64"})' discovery.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Select tools/gpu with declared glibc and CUDA`
Enter
Wait+Line /\$$/
Type@60ms `conda presto --parse -f conda.toml -e tools -p gpu > selected.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '.selected[0] | {environment, platform, subdir, system_requirements}' selected.json`
Enter
Wait+Line /\$$/
Sleep 2s
Hide
Set TypingSpeed 1ms
Type `jq -e '.selected | length == 1 and .[0].environment == "tools" and .[0].platform == "gpu" and .[0].subdir == "linux-64" and .[0].system_requirements.glibc == "2.28" and .[0].system_requirements.cuda == "12"' selected.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Solve and validate the complete four-target matrix`
Enter
Wait+Line /\$$/
Type@60ms `conda presto -f conda.toml --format conda-workspaces-lock-v1 > conda.lock`
Enter
Wait+Line /\$$/
Type@60ms `conda presto --validate -f conda.lock --manifest conda.toml > consistency.json`
Enter
Wait+Line /\$$/
Type@60ms `jq -c '.targets[] | {environment, platform, consistent}' consistency.json`
Enter
Wait+Line /\$$/
Sleep 2s
Hide
Set TypingSpeed 1ms
Type `jq -e '.consistent and (.targets | length == 4) and all(.targets[]; .consistent and .subdir == "linux-64" and .reason == null) and ([.targets[] | [.environment, .platform]] | sort) == [["default", "cpu"], ["default", "gpu"], ["tools", "cpu"], ["tools", "gpu"]]' consistency.json > /dev/null`
Enter
Wait+Line /\$$/
Type `mkdir changed`
Enter
Wait+Line /\$$/
Type `check_status=0`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Reject requirements that do not match the saved lock`
Enter
Wait+Line /\$$/
Type@60ms `sed 's/zlib = ">=1.3,<2"/zlib = ">=99"/' conda.toml > changed/conda.toml`
Enter
Wait+Line /\$$/
Type@60ms `conda presto --validate -f conda.lock --manifest changed/conda.toml > mismatch.json || check_status=$?`
Enter
Wait+Line /\$$/
Type@60ms `jq '{consistent, reason: .targets[0].reason}' mismatch.json`
Enter
Wait+Line /\$$/
Sleep 3s
Hide
Set TypingSpeed 1ms
Type `test "$check_status" -eq 1`
Enter
Wait+Line /\$$/
Type `jq -e '.consistent == false and (.targets | length == 4) and all(.targets[]; .consistent == false and (.reason | length > 0))' mismatch.json > /dev/null`
Enter
Wait+Line /\$$/
Type `mkdir updated`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Update tools/cpu and preserve the other three selections`
Enter
Wait+Line /\$$/
Type@60ms `conda presto --update -f conda.lock --manifest conda.toml -e tools -p cpu zstd > updated.lock`
Enter
Wait+Line /\$$/
Type@60ms `python check_update.py conda.lock updated.lock`
Enter
Wait+Line /\$$/
Sleep 2s
Hide
Set TypingSpeed 1ms
Type `cp updated.lock updated/conda.lock`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Validate the complete updated lock`
Enter
Wait+Line /\$$/
Type@60ms `conda presto --validate -f updated/conda.lock --manifest conda.toml > updated-check.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '{consistent, targets: (.targets | length)}' updated-check.json`
Enter
Wait+Line /\$$/
Sleep 2s
Screenshot demos/workspace.png
Sleep 500ms
Hide
Set TypingSpeed 1ms
Type `jq -e '.consistent and (.targets | length == 4)' updated-check.json > /dev/null`
Enter
Wait+Line /\$$/
Type `demo_finish`
Enter
Wait+Line /DEMO_COMPLETE\$$/
