# Daily local reminder

Local HealthLog can install one macOS LaunchAgent for the active profile. It
sends a local notification at a chosen wall-clock time and can optionally open
the private health portal. It does not upload the profile, invoke a cloud model,
or run the Apple Photos export automatically.

## Commands

```bash
# Install or update a daily reminder.
diet reminder set --time 21:30

# Optionally open site/index.html when it fires.
diet reminder set --time 21:30 --open-dashboard

# Preserve the time while changing the message, or change the time while
# preserving an existing custom message.
diet reminder set --time 22:00 --message "Review today's local log."

diet reminder status
diet reminder test
diet reminder remove
```

`HH:MM` is strict 24-hour local time. The schedule follows the Mac's current
timezone. `StartCalendarInterval` is used instead of a polling process; if the
Mac is asleep at the scheduled time, launchd coalesces the missed event and runs
it after wake.

Clone setup can install the same reminder non-interactively:

```bash
./scripts/setup.sh --reminder-time 21:30
./scripts/setup.sh --reminder-time 21:30 --reminder-open-dashboard
```

## Ownership and recovery

The ignored `config/reminder.local.json` owns the user-selected time and
notification preference. The generated LaunchAgent is:

```text
~/Library/LaunchAgents/io.local-healthlog.reminder.<profile_id>.<workspace_id>.plist
```

The plist contains clone-specific absolute paths and the Python executable that
was active when `set` ran. Moving the clone or changing/removing that Python
installation requires rerunning `diet reminder set --time HH:MM`. The private
config remembers the previous workspace-specific label so `set` can unload and
remove the stale task. The workspace suffix also prevents two clones with the
same default profile ID from overwriting each other.

Logs and the latest successful fire timestamp are rebuildable local state under
`runtime/reminders/`. `diet doctor` reports `active` or `disabled` in the normal
states. `configured-not-loaded`, `stale-workspace`, and `orphaned-agent` are
repair states: rerun `set`, or run `remove` before setting the reminder again.

## Notification privacy

The default text is generic because macOS may show notifications on the lock
screen. Custom messages remain local, but users should avoid diagnoses,
medicines, measurements, or medical-record details. Opening the portal is
opt-in because it exposes more private information on screen and may interrupt
the current task.
