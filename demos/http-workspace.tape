Source demos/_settings.tape

Output demos/http-workspace.gif

Hide
Type `source demos/_setup.sh http-workspace`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Discover workspace environments without solving`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `jq -n --rawfile file conda.toml \`
Enter
Type@60ms `  '{file:$file,filename:"conda.toml"}' > workspace.json`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS "$DEMO_URL/parse" --json @workspace.json > discovery.json`
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
Type `jq -e '.selected == [] and ([.environments[].name] | sort) == ["default", "tools"] and all(.environments[]; .platforms == {cpu: "linux-64", gpu: "linux-64"})' discovery.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Select tools/gpu with declared glibc and CUDA`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `jq '. + {environments:["tools"],platforms:["gpu"]}' workspace.json > selected.json`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS "$DEMO_URL/parse" --json @selected.json > selected-result.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '.selected[0] | {environment, platform, subdir, system_requirements}' \`
Enter
Type@60ms `  selected-result.json`
Enter
Wait+Line /\$$/
Sleep 2s

Hide
Type `jq -e '.selected | length == 1 and .[0].environment == "tools" and .[0].platform == "gpu" and .[0].subdir == "linux-64" and .[0].system_requirements.glibc == "2.28" and .[0].system_requirements.cuda == "12"' selected-result.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Export selected declarations without solving`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `curl -fsS "$DEMO_URL/export?format=requirements" \`
Enter
Type@60ms `  --json @selected.json -o requirements.txt`
Enter
Wait+Line /\$$/
Type@60ms `bat requirements.txt && echo`
Enter
Wait+Line /\$$/
Sleep 3s

Hide
Type `python -c 'from pathlib import Path; from conda.models.match_spec import MatchSpec; specs = [MatchSpec(line) for line in Path("requirements.txt").read_text().splitlines() if line and not line.startswith("#")]; assert {spec.name: str(spec.version) for spec in specs} == {"zlib": ">=1.3,<2", "zstd": ">=1.5,<2"}'`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Show

Type@60ms `# Solve the complete workspace into one lock`
Enter
Wait+Line /\$$/
Sleep 500ms

Type@60ms `curl -fsS "$DEMO_URL/resolve?format=conda-workspaces-lock-v1" \`
Enter
Type@60ms `  --json @workspace.json -o conda.lock`
Enter
Wait+Line /\$$/
Type@60ms `jq -n --rawfile file conda.lock \`
Enter
Type@60ms `  '{file:$file,filename:"conda.lock"}' > lock.json`
Enter
Wait+Line /\$$/
Type@60ms `curl -fsS "$DEMO_URL/parse" --json @lock.json > saved.json`
Enter
Wait+Line /\$$/
Type@60ms `jq -c '.environments[] | {name, platforms}' saved.json`
Enter
Wait+Line /\$$/
Sleep 2s
Type@60ms `echo "Saved both environments and both named targets"`
Enter
Wait+Line /\$$/
Sleep 2s

Screenshot demos/http-workspace.png
Sleep 500ms
Hide
Type `jq -e '.format == "conda-workspaces-lock-v1" and .selected == [] and ([.environments[].name] | sort) == ["default", "tools"] and all(.environments[]; .platforms == {cpu: "linux-64", gpu: "linux-64"})' saved.json > /dev/null`
Enter
Wait+Line /\$$/
Type `demo_finish`
Enter
Wait+Line /DEMO_COMPLETE\$$/
