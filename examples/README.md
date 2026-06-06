# yuj examples

Self-contained project directories. Copy one, edit `fleet.csv`, run the flow.

| Example | What it shows |
|---------|---------------|
| [`python-wordcount/`](python-wordcount/) | Scatter URLs, count words per page. Bootstraps `uv`. |
| [`r-analysis/`](r-analysis/) | Run an R analysis per item. Bootstraps R via `micromamba` (no root). |

```bash
cd python-wordcount    # or r-analysis
$EDITOR fleet.csv      # add your hosts
yuj bootstrap          # install runtime on each host (once)
yuj deploy             # copy code + inputs
yuj submit             # start the self-healing watchdog
yuj status --watch 30  # watch progress
yuj pull --loop 60     # gather results
yuj decommission HOST  # hand machines back clean
```
