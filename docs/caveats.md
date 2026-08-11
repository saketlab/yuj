# Caveats

## Use SSH keys when you can

If the host owner can add your public key to `~/.ssh/authorized_keys`, do that instead of using a password. Keys don't trigger fail2ban on failed auths, work without `sshpass`, and aren't logged anywhere.

To use a key, set `key_path` in `fleet.csv`:

```csv
username,ip,name,password,key_path
alice,10.0.0.1,lab-desk-1,,/home/alice/.ssh/id_ed25519
```

## fail2ban bans stick

Repeated failed authentications will get your controller's IP banned from a host for hours, sometimes longer. yuj limits the damage by:

- Defaulting `--max-workers 4` in bootstrap to keep concurrent probes low
- Distinguishing auth failures from other problems (`yuj diagnose`)
- Never retrying on an auth failure without you intervening

Once you're banned, you can't un-ban yourself from outside. Wait it out or ask the host admin.

## Keep fleet.csv out of version control

Passwords go in there. yuj's `.gitignore` excludes `fleet.csv` and `*.creds` by default. Check before pushing, and don't paste `fleet.csv` contents into tickets or Slack.

Passwords are passed to `sshpass` via the `SSHPASS` environment variable, never on the command line (where they'd show up in `ps` output).

## These are someone else's machines

`yuj status` shows `⚠` when someone is at the console. When that happens, stop the job or schedule a decommission:

```bash
yuj decommission lab-desk-1                         # now
yuj decommission lab-desk-1 --at "8am tomorrow"     # polite
```

## Stall detection needs tuning

The default `stall_min: 90` (90 minutes) suits long-running jobs that load big models. For faster workloads, lower it:

```yaml
stall_min: 5   # restart if no new output for 5 minutes
```

The watchdog also ignores stalls for 45 minutes after each (re)launch, so a job that loads a large model on startup won't be killed while warming up. A `stall_min` shorter than your startup time is therefore safe, but one shorter than the gap between two normal outputs will restart a healthy job.

## yuj is not a scheduler

yuj doesn't do job queuing, resource limits, inter-job dependencies, or GPU isolation. It keeps a batch making progress across a set of unreliable machines. For queuing, resource limits, or GPU isolation, use Slurm or Kubernetes.

## Host key verification

yuj defaults to `StrictHostKeyChecking=no` and `UserKnownHostsFile=/dev/null` because borrowed lab desktops get reimaged frequently and their host keys change.

For public internet or less-trusted networks, opt into strict verification per host:

```csv
username,ip,name,key_path,strict_host_key,known_hosts_file
alice,203.0.113.10,cloud-box,/home/alice/.ssh/id_ed25519,true,/home/alice/.ssh/known_hosts
```

In `fleet.yaml`, set `strict_host_key: true` and optionally `known_hosts_file` at the top level or per machine.
