Source demos/_settings.tape
Output demos/action.gif

Hide
Set TypingSpeed 1ms
Type `source demos/_setup.sh action`
Enter
Wait+Line /\$$/
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show

Type "# Solve a workspace in a GitHub Actions workflow"
Enter
Sleep 500ms
Type@60ms "bat --line-range 15:27 workflow.yml"
Enter
Wait+Line /\$$/
Sleep 3s

Type "# This example selects two platforms from conda.toml"
Enter
Type@60ms "bat conda.toml"
Enter
Wait+Line /\$$/
Sleep 3s

Type "# Run the composite Action steps locally against Presto"
Enter
Type@60ms `python action-client.py "$DEMO_URL"`
Enter
Wait+Line /\$$/
Sleep 3s

Type "# Inspect the reported outputs"
Enter
Type@60ms `grep "^solved=" action-output.txt`
Enter
Wait+Line /\$$/
Sleep 3s
Screenshot demos/action.png
Sleep 500ms
Hide
Set TypingSpeed 1ms
Type "demo_finish"
Enter
Wait+Line /DEMO_COMPLETE\$$/
