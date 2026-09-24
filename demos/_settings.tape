Require bash
Require bat
Require conda
Require curl
Require jq
Set Shell "bash"
Set FontFamily "JetBrains Mono"
Set FontSize 20
Set Width 1200
Set Height 600
Set Margin 20
Set MarginFill "#19191b"
Set Padding 20
Set Framerate 15
Set TypingSpeed 40ms
Set WaitTimeout 5m
Set Theme "Dark+"
Env DEMO_PS1 "\[\e[1;38;2;67;176;42m\]$\[\e[0m\] "
Env PROMPT_COMMAND ""
Env BASH_ENV "/dev/null"
Env TERM "xterm-256color"
Env BAT_OPTS "--color=always --paging=never --plain"
Env BAT_THEME "Visual Studio Dark+"
