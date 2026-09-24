Source demos/_settings.tape
Output demos/cli.gif
Hide
Set TypingSpeed 1ms
Type `source demos/_setup.sh cli`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Solve inline requirements for Linux`
Enter
Wait+Line /\$$/
Type@60ms `conda-presto -c conda-forge -p linux-64 zlib > specs.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '.[0] | {platform, packages: [.packages[].name], error}' specs.json`
Enter
Wait+Line /\$$/
Sleep 2s
Hide
Set TypingSpeed 1ms
Type `jq -e 'length == 1 and .[0].platform == "linux-64" and .[0].error == null and any(.[0].packages[]; .name == "zlib")' specs.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Solve an environment file through the conda subcommand`
Enter
Wait+Line /\$$/
Type@60ms `bat environment.yml`
Enter
Wait+Line /\$$/
Sleep 1s
Type@60ms `conda presto -f environment.yml -p linux-64 > environment.json`
Enter
Wait+Line /\$$/
Type@60ms `jq '.[0] | {platform, packages: [.packages[].name], error}' environment.json`
Enter
Wait+Line /\$$/
Sleep 2s
Hide
Set TypingSpeed 1ms
Type `jq -e 'length == 1 and .[0].error == null and ([.[0].packages[].name] | contains(["zlib", "zstd"]))' environment.json > /dev/null`
Enter
Wait+Line /\$$/
Type `clear`
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show
Type@60ms `# Export declarations without solving`
Enter
Wait+Line /\$$/
Type@60ms `conda-presto --export -f environment.yml --format requirements > requirements.txt`
Enter
Wait+Line /\$$/
Type@60ms `bat requirements.txt && echo`
Enter
Wait+Line /\$$/
Sleep 2s
Screenshot demos/cli.png
Sleep 500ms
Hide
Set TypingSpeed 1ms
Type `python -c 'from pathlib import Path; from conda.models.match_spec import MatchSpec; specs = [MatchSpec(line) for line in Path("requirements.txt").read_text().splitlines() if line and not line.startswith("#")]; assert {spec.name: str(spec.version) for spec in specs} == {"zlib": ">=1.3,<2", "zstd": ">=1.5,<2"}'`
Enter
Wait+Line /\$$/
Type `demo_finish`
Enter
Wait+Line /DEMO_COMPLETE\$$/
