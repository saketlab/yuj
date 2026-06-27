# yuj example: Python word-count

Fetch web pages on borrowed machines and count words per page. The worker is a
single stdlib-only Python script; swap in your own code.

No Python/Conda/root needed on the remote hosts (yuj installs `uv`).

```bash
$EDITOR fleet.csv      # username / IP / password or key
yuj bootstrap          # install uv + venv on each host (once)
yuj deploy             # copy worker + input list
yuj submit             # start the watchdog
yuj status --watch 30
yuj pull --loop 60     # results land in central/<domain>.count
```

## Customising

Edit `main()` in `worker.py`. The item is `sys.argv[1]`; write one file to
`$YUJ_OUT/<item><output_suffix>`. Add packages to `requirements.txt` and re-run
`yuj bootstrap`.
