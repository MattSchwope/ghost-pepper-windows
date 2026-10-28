# Eval clips

Put test clips in `eval/clips/` (git-ignored). Each clip needs a matching reference transcript:

```
eval/clips/
  dictation-car-01.m4a
  dictation-car-01.ref.txt      what was actually said, typed by hand
  talk-panel-01.m4a
  talk-panel-01.ref.txt
  call-01.m4a
  call-01.ref.txt
```

Name prefixes set the source type: `dictation-`, `talk-`, `call-`.
Target: 5 of each, 30 to 60 seconds long.
