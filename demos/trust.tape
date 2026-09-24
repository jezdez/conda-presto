Source demos/_settings.tape
Output demos/trust.gif

Hide
Set TypingSpeed 1ms
Type `source demos/_setup.sh trust`
Enter
Wait+Line /\$$/
Type "clear"
Enter
Wait+Line /\$$/
Set TypingSpeed 40ms
Show

Type "# Verify a public fixture with staging trust, offline"
Enter
Sleep 500ms
Type@60ms "python verify.py artifact.txt"
Enter
Wait+Line /\$$/
Sleep 3s

Type "# Changed bytes no longer match the signed artifact"
Enter
Type@60ms "cp artifact.txt tampered.txt"
Enter
Wait+Line /\$$/
Type@60ms `printf '\n' >> tampered.txt`
Enter
Wait+Line /\$$/
Type@60ms "python verify.py tampered.txt --expect-error artifact-mismatch"
Enter
Wait+Line /\$$/
Sleep 3s

Type "# A valid signature still needs the expected signer"
Enter
Type@60ms `python verify.py artifact.txt \`
Enter
Type@60ms `  --identity https://example.org/different-workflow \`
Enter
Type@60ms "  --expect-error untrusted-identity"
Enter
Wait+Line /\$$/
Sleep 3s
Type "# Signature and identity checked, provenance claims remain unchecked"
Enter
Sleep 2s
Screenshot demos/trust.png
Sleep 500ms
Hide
Set TypingSpeed 1ms
Type "demo_finish"
Enter
Wait+Line /DEMO_COMPLETE\$$/
