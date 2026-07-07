from collections.abc import Sequence
from html import escape

from yuj.status import DEFAULT_STALL_MIN, HostStatus

_STATE = {
    "producing": ("producing", "#34c759"),
    "stalled": ("stalled", "#ff3b30"),
    "dead": ("dead", "#d70015"),
    "idle": ("idle", "#ff9500"),
    "down": ("down", "#8e8e93"),
    "excluded": ("excluded", "#8e8e93"),
}


def _load_colour(status: HostStatus) -> str:
    if status.load1 is None:
        return "#8e8e93"
    ratio = status.load1 / status.nproc if status.nproc else status.load1
    return "#34c759" if ratio < 0.7 else "#ff9500" if ratio < 1.2 else "#ff3b30"


def _fmt_age(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 90:
        return f"{minutes}m"
    if minutes < 60 * 36:
        return f"{minutes // 60}h"
    return f"{minutes // 1440}d"


def _card(status: HostStatus, stall_threshold_min: int) -> str:
    label, colour = _STATE[status.state(stall_threshold_min)]
    load = "—" if status.load1 is None else f"{status.load1:.2f}"
    ratio = (
        min(status.load1 / status.nproc, 1.0)
        if status.load1 is not None and status.nproc
        else 0.0
    )
    outputs = "—" if status.n_outputs is None else f"{status.n_outputs:,}"
    owner = (
        f'<div class="owner">⚠ {escape(status.console_user)} at console</div>'
        if status.owner_present and status.console_user
        else ""
    )
    gpu = escape(status.gpu) if status.gpu else "no GPU"
    cores = f"{status.nproc}c" if status.nproc is not None else "?c"
    mem = f" · {status.mem_gb}G" if status.mem_gb is not None else ""
    age = _fmt_age(status.newest_age_min)
    load_pct = ratio * 100
    load_colour = _load_colour(status)
    return f"""
    <div class="card">
      <div class="card-head">
        <span class="name">{escape(status.name)}</span>
        <span class="state" style="color:{colour}">●&nbsp;{label}</span>
      </div>
      <div class="ip">{escape(status.ip)}</div>
      <div class="metrics">
        <div><span class="k">gpu</span><span class="v">{gpu}</span></div>
        <div><span class="k">cpu</span><span class="v">{cores}{mem}</span></div>
        <div><span class="k">outputs</span><span class="v">{outputs}</span></div>
        <div><span class="k">age</span><span class="v">{age}</span></div>
      </div>
      <div class="loadrow">
        <span class="k">load</span>
        <div class="bar">
          <div class="fill" style="width:{load_pct:.0f}%;background:{load_colour}">
          </div>
        </div>
        <span class="v">{load}</span>
      </div>
      {owner}
    </div>"""


def _chip(label: str, value: str, colour: str = "#1d1d1f") -> str:
    return (
        '<div class="chip">'
        f'<span class="chip-v" style="color:{colour}">{escape(value)}</span>'
        f'<span class="chip-k">{escape(label)}</span></div>'
    )


def status_html(
    statuses: Sequence[HostStatus],
    *,
    title: str = "yuj fleet",
    stall_threshold_min: int = DEFAULT_STALL_MIN,
    total_items: int | None = None,
    refresh_secs: int | None = None,
    window_label: str | None = None,
    stops_in: str | None = None,
    eta: str | None = None,
    updated: str | None = None,
) -> str:
    """Render the fleet snapshot as one self-contained HTML page."""
    done = sum(s.n_outputs or 0 for s in statuses)
    up = sum(1 for s in statuses if s.reachable)
    producing = sum(1 for s in statuses if s.state(stall_threshold_min) == "producing")
    stalled = sum(1 for s in statuses if s.state(stall_threshold_min) == "stalled")

    pct = int(done / total_items * 100) if total_items else 0
    progress = ""
    if total_items:
        progress = f"""
      <div class="progress">
        <div class="progress-fill" style="width:{pct}%"></div>
      </div>
      <div class="progress-label">
        {done:,} / {total_items:,} &nbsp;·&nbsp; {pct}%
      </div>"""

    chips = [
        _chip("up", f"{up}/{len(statuses)}"),
        _chip("producing", str(producing), "#34c759"),
        _chip("stalled", str(stalled), "#ff3b30" if stalled else "#8e8e93"),
    ]
    if window_label:
        win = window_label + (f" · stops in {stops_in}" if stops_in else "")
        chips.append(_chip("window", win, "#0071e3"))
    if eta:
        chips.append(_chip("eta", eta, "#0071e3"))

    cards = "\n".join(_card(s, stall_threshold_min) for s in statuses)
    meta_refresh = (
        f'<meta http-equiv="refresh" content="{refresh_secs}">' if refresh_secs else ""
    )
    foot = f"updated {escape(updated)}" if updated else ""
    if refresh_secs:
        foot += f" · auto-refresh {refresh_secs}s"

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta_refresh}
<title>{escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 40px 24px; background: #f5f5f7; color: #1d1d1f;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro",
      "SF Pro Display", sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 980px; margin: 0 auto; }}
  h1 {{
    font-size: 28px; font-weight: 600; letter-spacing: -0.02em;
    margin: 0 0 4px;
  }}
  .progress {{
    height: 8px; background: #e3e3e8; border-radius: 4px; overflow: hidden;
    margin: 18px 0 6px;
  }}
  .progress-fill {{
    height: 100%; background: #34c759; border-radius: 4px;
    transition: width .4s ease;
  }}
  .progress-label {{ font-size: 14px; color: #6e6e73; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 22px 0; }}
  .chip {{
    background: #fff; border-radius: 14px; padding: 12px 18px;
    box-shadow: 0 1px 3px rgba(0,0,0,.06); display: flex;
    flex-direction: column; min-width: 90px;
  }}
  .chip-v {{ font-size: 22px; font-weight: 600; letter-spacing: -0.01em; }}
  .chip-k {{
    font-size: 12px; color: #8e8e93; text-transform: uppercase;
    letter-spacing: .04em; margin-top: 2px;
  }}
  .grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 16px;
  }}
  .card {{
    background: #fff; border-radius: 18px; padding: 18px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
  }}
  .card-head {{ display: flex; justify-content: space-between; align-items: baseline; }}
  .name {{ font-size: 17px; font-weight: 600; }}
  .state {{ font-size: 13px; font-weight: 500; }}
  .ip {{
    font-size: 12px; color: #8e8e93; margin: 2px 0 14px;
    font-variant-numeric: tabular-nums;
  }}
  .metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px 16px; }}
  .metrics .k, .loadrow .k {{
    font-size: 11px; color: #8e8e93; text-transform: uppercase;
    letter-spacing: .04em; display: block;
  }}
  .metrics .v {{
    font-size: 15px; font-weight: 500; font-variant-numeric: tabular-nums;
  }}
  .loadrow {{ display: flex; align-items: center; gap: 10px; margin-top: 14px; }}
  .loadrow .v {{
    font-size: 14px; font-variant-numeric: tabular-nums; min-width: 40px;
    text-align: right;
  }}
  .bar {{
    flex: 1; height: 6px; background: #e3e3e8; border-radius: 3px;
    overflow: hidden;
  }}
  .fill {{ height: 100%; border-radius: 3px; transition: width .4s ease; }}
  .owner {{ margin-top: 12px; font-size: 13px; color: #ff3b30; font-weight: 500; }}
  footer {{ margin-top: 28px; font-size: 12px; color: #8e8e93; text-align: center; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #000; color: #f5f5f7; }}
    .chip, .card {{ background: #1c1c1e; box-shadow: none; }}
    .progress, .bar {{ background: #2c2c2e; }}
  }}
</style>
</head><body><div class="wrap">
  <h1>{escape(title)}</h1>{progress}
  <div class="chips">{"".join(chips)}</div>
  <div class="grid">{cards}</div>
  <footer>{foot}</footer>
</div></body></html>"""
